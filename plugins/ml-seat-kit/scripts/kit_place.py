r"""Step 3 of the assistant launcher: put a new kit in place WITHOUT renaming, overwriting or deleting anything
that may be in use, and without tar.exe (Python's own zipfile unpacks it).

The kit zip runs this itself (make_kit.py puts a small __main__.py at the root of the zip):

    python -I <zip> --kit-home <folder>
    python -I kit_place.py --kit-home <folder> --folder <a kit folder holding .claude-plugin\marketplace.json>

The kit home holds:

    kits\<version>\     one folder per kit version, unpacked there once and never renamed or changed after
    kit.txt             one line: the name of the folder under kits\ that is in use (the pointer)
    send_gate.py        the send gate's fixed address: seat settings name this file, and it runs the send gate of
                        the kit named in kit.txt, or refuses when it cannot (see gate_shim.py)
    market.txt          the kit folder last registered with Claude Code as the plugin source "seat-kit"
    current\            the folder kit 0.1.3 and older used. Left exactly as it is: an older seat's send gate
                        or plugin registration may still point into it.

A new version is unpacked into a folder that did not exist a moment before, so nothing can be holding it; then
kit.txt is switched. A version already unpacked from the very same zip is used again as it is (nothing written).
A refused step (a file held for a moment by a scanner) is tried again a few times, a few seconds in all, each
unpack into a fresh folder. Afterward, older kit folders and the leftovers of kit 0.1.3 and older (incoming,
previous, kit.zip) and of earlier downloads are removed, best effort: one that is in use stays until a later run,
and that is never an error. A kit folder is removed only when it is neither in kit.txt nor in market.txt.

Prints the rest of the launcher's "Step 3 of 6" line: "done" (exit 0), or "not done" and then, each on a line of
its own, what could not be done and what Windows said (exit 3). Standard library only; nothing else from the kit
is imported, because this runs from inside the zip.
"""
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import time
import zipfile

PLUGIN = "ml-seat-kit"
POINTER = "kit.txt"
MARKET = "market.txt"
SHIM = "send_gate.py"
KITS = "kits"
READY = ".kit-ready"
PLUGIN_JSON = "plugins/%s/.claude-plugin/plugin.json" % PLUGIN
SETUP = "plugins/%s/scripts/seat_setup.py" % PLUGIN
SHIM_SRC = "plugins/%s/scripts/gate_shim.py" % PLUGIN
NAME_OK = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,39}$")
LEGACY_LEFTOVERS = ("incoming", "previous", "kit.zip")
BACKOFF = (0.5, 1.0, 1.5)          # waits between tries of a refused step: three more tries, three seconds in all


class NotPlaced(Exception):
    def __init__(self, what, detail=None):
        Exception.__init__(self, what)
        self.what, self.detail = what, detail


def said(e):
    """What Windows said, in its own words, with the file it was about."""
    text = getattr(e, "strerror", None) or str(e) or type(e).__name__
    name = getattr(e, "filename", None)
    return "%s: %s" % (text, name) if name else text


def _retry(fn, tries=20, wait=0.1):
    """A file another program has open for a moment (a scanner, the send gate reading kit.txt) refuses a replace;
    try again for about two seconds before giving up."""
    for i in range(tries):
        try:
            return fn()
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(wait)


def write_file(path, data):
    """Write through a new temporary name, then replace. Never needs to remove a leftover temporary file."""
    path = pathlib.Path(path)
    tmp = path.with_name("%s.new-%d-%d" % (path.name, os.getpid(), int(time.time() * 1000) % 100000))
    tmp.write_bytes(data)
    try:
        _retry(lambda: os.replace(str(tmp), str(path)))
    except OSError:
        try:
            os.remove(str(tmp))
        except OSError:
            pass
        raise


def valid_name(name):
    return bool(name) and bool(NAME_OK.match(name)) and name not in (".", "..")


def read_pointer(kit_home):
    """(folder name, kit folder Path). Raises NotPlaced with a plain reason when kit.txt is missing, unreadable or
    names a folder that is not a whole kit."""
    p = pathlib.Path(kit_home) / POINTER
    try:
        name = p.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        raise NotPlaced("kit.txt is missing")
    except (OSError, UnicodeDecodeError) as e:
        raise NotPlaced("kit.txt could not be read", said(e))
    if not valid_name(name):
        raise NotPlaced("kit.txt does not name a kit folder")
    folder = pathlib.Path(kit_home) / KITS / name
    if not (folder / SETUP).is_file():
        raise NotPlaced("the kit folder named in kit.txt (%s) is not there" % folder)
    return name, folder


def version_of_folder(folder):
    try:
        v = json.loads((pathlib.Path(folder) / PLUGIN_JSON).read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return v if isinstance(v, str) and valid_name(v) else None


def _digest_zip(zip_path):
    h = hashlib.sha256()
    with open(zip_path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def _skip(rel):
    parts = pathlib.PurePosixPath(rel).parts
    return any(p in ("__pycache__", ".git", ".pytest_cache") for p in parts) or rel.endswith((".pyc", ".pyo"))


def _digest_folder(folder):
    h = hashlib.sha256()
    for p in sorted(pathlib.Path(folder).rglob("*")):
        rel = p.relative_to(folder).as_posix()
        if p.is_file() and not _skip(rel):
            h.update(rel.encode("utf-8") + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def _tries(step, what):
    """Run step(); on a refusal (OSError) wait and try again, BACKOFF long. Raises NotPlaced(what, Windows' words)."""
    last = None
    for wait in (0,) + BACKOFF:
        if wait:
            time.sleep(wait)
        try:
            return step()
        except NotPlaced:
            raise
        except (OSError, zipfile.BadZipFile, shutil.Error, ValueError, KeyError) as e:
            last = e
            if isinstance(e, (zipfile.BadZipFile, ValueError, KeyError)):
                break                   # a damaged file does not mend by waiting
    raise NotPlaced(what, said(last))


class Source:
    """The new kit: a zip or a folder. version, digest, and unpack(into)."""

    def __init__(self, zip_path=None, folder=None):
        self.zip, self.folder = zip_path, folder
        meta = _tries(self._read, "the downloaded kit could not be read")
        v = meta.get("version") if isinstance(meta, dict) else None
        if not isinstance(v, str) or not valid_name(v):
            raise NotPlaced("the downloaded kit has no version this launcher can use")
        self.version = v

    def _read(self):
        if self.zip:
            with zipfile.ZipFile(self.zip) as z:
                if SETUP not in set(z.namelist()):
                    raise NotPlaced("the downloaded file is not a whole kit")
                meta = json.loads(z.read(PLUGIN_JSON).decode("utf-8"))
            self.digest = _digest_zip(self.zip)
        else:
            if not (pathlib.Path(self.folder) / SETUP).is_file():
                raise NotPlaced("the kit folder given is not a whole kit")
            meta = json.loads((pathlib.Path(self.folder) / PLUGIN_JSON).read_text(encoding="utf-8"))
            self.digest = _digest_folder(self.folder)
        return meta

    def unpack(self, into):
        into = pathlib.Path(into)
        if self.zip:
            with zipfile.ZipFile(self.zip) as z:
                z.extractall(str(into))
        else:
            shutil.copytree(str(self.folder), str(into), ignore=lambda d, names: [
                n for n in names if n in ("__pycache__", ".git", ".pytest_cache") or n.endswith((".pyc", ".pyo"))])


def _ready(folder, digest):
    try:
        return (folder / READY).read_text(encoding="ascii").strip() == digest and (folder / SETUP).is_file()
    except (OSError, UnicodeDecodeError):
        return False


def _free_or_ready(kits, src):
    """(name, True) for a folder already holding this very zip, else (name, False) for the first free name."""
    for n in range(1, 41):
        name = src.version if n == 1 else "%s-%d" % (src.version, n)
        folder = kits / name
        if folder.exists():
            if _ready(folder, src.digest):
                return name, True
            continue                    # another kit, or a part left by a stopped run: never touched here
        return name, False
    raise NotPlaced("forty folders for kit %s are already there and none is whole" % src.version)


def place(kit_home, src):
    """Put src in place. Returns (folder name, how) where how is 'new' or 'already here'. Raises NotPlaced."""
    kits = pathlib.Path(kit_home) / KITS
    _tries(lambda: kits.mkdir(parents=True, exist_ok=True), "the folder %s could not be made" % kits)
    state = {}

    def unpack_once():
        name, ready = _free_or_ready(kits, src)       # a fresh name on every try: a failed try is never reused
        state["name"], state["how"] = name, "already here" if ready else "new"
        if ready:
            return
        folder = kits / name
        state["folder"] = folder
        src.unpack(folder)
        if not (folder / SETUP).is_file():
            raise NotPlaced("the unpacked kit in %s is not whole" % folder)
        write_file(folder / READY, (src.digest + "\n").encode("ascii"))

    _tries(unpack_once, "the new kit could not be unpacked under %s" % kits)
    chosen = state["name"]
    shim = pathlib.Path(kit_home) / SHIM
    shim_src = kits / chosen / SHIM_SRC
    if shim_src.is_file():
        data = shim_src.read_bytes()
        try:
            if not shim.is_file() or shim.read_bytes() != data:
                _tries(lambda: write_file(shim, data), "the send gate file %s could not be written" % shim)
        except NotPlaced:
            if not shim.is_file():
                raise
    _tries(lambda: write_file(pathlib.Path(kit_home) / POINTER, (chosen + "\r\n").encode("ascii")),   # CRLF: set /p
           "kit.txt could not be switched to the new kit")
    try:
        if read_pointer(kit_home)[0] != chosen:
            raise NotPlaced("kit.txt did not keep the new kit's name")
    except NotPlaced as e:
        raise NotPlaced("after switching, %s" % e.what, e.detail)
    return chosen, state["how"]


def _named_in(path):
    try:
        return pathlib.Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def cleanup(kit_home, keep_zip=None):
    """Best effort, silent: older kit folders and leftovers. Returns the names removed (for tests)."""
    kh = pathlib.Path(kit_home)
    gone = []
    try:
        active = read_pointer(kh)[0]
    except NotPlaced:
        return gone                     # no good pointer: remove nothing
    keep = {active.lower()}
    market = _named_in(kh / MARKET)
    if market:
        keep.add(pathlib.PureWindowsPath(market).name.lower())
    try:
        entries = list((kh / KITS).iterdir())
    except OSError:
        entries = []
    for d in entries:
        if d.name.lower() in keep or not d.is_dir():
            continue
        try:
            if (d / READY).exists():
                os.remove(str(d / READY))   # first, so a half removed folder is never taken as whole
        except OSError:
            continue                    # in use: leave all of it for a later run
        shutil.rmtree(str(d), ignore_errors=True)
        if not d.exists():
            gone.append(d.name)
    for name in LEGACY_LEFTOVERS:
        p = kh / name
        try:
            if p.is_dir():
                shutil.rmtree(str(p), ignore_errors=True)
            elif p.is_file():
                os.chmod(str(p), 0o666)  # a read-only leftover
                p.unlink()
        except OSError:
            pass
    for p in list(kh.glob("dl-*.zip")) + list(kh.glob("dl-*.txt")) + list(kh.glob("*.new-*")):
        if keep_zip and os.path.normcase(str(p)) == os.path.normcase(str(keep_zip)):
            continue
        try:
            p.unlink()
        except OSError:
            pass
    return gone


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    kit_home = zip_path = folder = None
    i = 0
    while i < len(argv):
        if argv[i] in ("--kit-home", "--zip", "--folder") and i + 1 < len(argv):
            if argv[i] == "--kit-home":
                kit_home = argv[i + 1]
            elif argv[i] == "--zip":
                zip_path = argv[i + 1]
            else:
                folder = argv[i + 1]
            i += 2
            continue
        i += 1
    try:
        if not kit_home or not (zip_path or folder):
            raise NotPlaced("the launcher did not say where the kit goes")
        name, how = place(kit_home, Source(zip_path=zip_path, folder=folder))
    except NotPlaced as e:
        print("not done")
        print("   %s%s." % (e.what[:1].upper(), e.what[1:]))
        if e.detail:
            print("   Windows said: %s" % e.detail)
        return 3
    except Exception as e:  # noqa: BLE001
        print("not done")
        print("   Something unexpected went wrong (%s: %s)." % (type(e).__name__, str(e)[:160]))
        return 3
    try:
        cleanup(kit_home, keep_zip=zip_path)
    except Exception:  # noqa: BLE001
        pass                            # removing old folders never matters for this run
    print("done" if how == "new" else "done (this version was already here)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
