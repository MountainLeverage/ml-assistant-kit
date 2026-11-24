r"""Remove mode: takes back a FIRST install (one the launcher made), never an adopted folder. Run by the launchers:

    python -I seat_setup.py remove [--check] [--delete-my-notes] --claude <claude.exe> --kit-home <folder>
                                   [--seat-home <folder>] [--desktop <folder>] [--launcher <file>]

--check prints what remove would take away and what it would leave, each on a line of its own with its full
location, and changes nothing: no folder, no file, no shortcut, and no call to Claude Code at all.

Without --check, in this order, each on a line saying done, not there, or could not (with Windows' own words):
  1. the plugin: claude plugin uninstall ml-seat-kit@seat-kit, then claude plugin marketplace remove seat-kit.
     Claude Code not found: said, the two lines printed for later, and remove carries on.
  2. each desktop shortcut whose TARGET is the kit's kept launcher (<kit home>\Assistant.cmd). One with the kit's
     name or description that points anywhere else is left and named.
  3. each assistant folder the launcher made under the seat home. The person's notes are in it, so it is MOVED
     beside itself to <name>.removed-<yyyymmdd-hhmmss>, never deleted, unless --delete-my-notes is given and the
     person types the folder's name (asked before anything changes; a wrong name changes nothing).
  4. the kit's own files and folders in the kit home, then the kit home itself when nothing else is in it.

Everything is checked first: the folders it was given (never a drive root, the profile, the Desktop or Documents
folder itself, Downloads or anything in it, the Windows folder, or a kit home without the kit in it), whether
anything is in use (a file open, or a window sitting in the folder: remove stops BEFORE changing anything and says
to close the assistant's window), whether the launcher running is the copy kept inside the kit home (a running
file cannot take away its own folder: remove stops and says to run the downloaded copy), and what is adopted. Only
when the whole plan can run does anything change. A step that fails then stops there, says what was already taken
back and what is still there, and running remove again finishes the job ("not there" is a normal line).

An adopted folder is never touched. Adopted folders are found under the seat home (their marker says "adopted")
and in <kit home>\adopted.txt (written by adopt since 0.1.5). While one exists, the plugin and the kit home stay
(its send gate runs <kit home>\send_gate.py), and only the first install's own things go: its folder, its
shortcut, the kept launcher copy and last-seat.txt.

Never touched: anything outside the seat home and the kit home (apart from the kit's .lnk files on the desktop),
any file named assistant.cmd or assistant.ps1 that is not the kit's kept copy, Claude Code, Python, the person's
Claude sign-in and connectors, and Downloads. Exit code 0 when done or nothing was there, 1 on a stop.
"""
import argparse
import builtins
import datetime
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import seat_common  # noqa: E402
import kit_place  # noqa: E402
import seat_setup as S  # noqa: E402

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

PLUGIN, MARKETPLACE, PLUGIN_ID = S.PLUGIN, S.MARKETPLACE, S.PLUGIN_ID
REMOVED = S.REMOVED_NAME
KEPT_LAUNCHER = "Assistant.cmd"
KIT_FILES = {"kit.txt", "market.txt", "send_gate.py", "python.txt", "last-seat.txt", "selftest-last.txt",
             "install-claude.cmd", S.ADOPTED_LIST, "kit.zip"}
KIT_DIRS = {"kits", "current", "incoming", "previous", "__pycache__"}
KIT_PATTERNS = (re.compile(r"^dl-\d+\.(zip|txt)$", re.I), re.compile(r"\.new-\d+-\d+$", re.I))
LAST_IN_KIT_HOME = ("kit.txt", "send_gate.py")              # deleted last: the gate's pointer and its fixed address
FIRST_INSTALL_ONLY = {"assistant.cmd", "last-seat.txt"}     # taken even while an adopted folder keeps the rest
SHORTCUT_DESCRIPTION = "Start your assistant"
NEVER = ("Claude Code itself, Python, your Claude sign-in and connectors, and everything in your Downloads folder")
OLD_ADOPT = ("A folder of your own that was set up with adopt before kit 0.1.5 is not known to the kit. If you have "
             "one, take it back first with adopt --undo on that folder, then run remove.")
TODO_SEND = "send this window to whoever set up your assistant"
TODO_AGAIN = "run remove again; it picks up where it stopped"
CLOSE = ("close your assistant's window, and any other window or program using that folder (a Command Prompt "
         "sitting in it counts), then run remove again")


def say(text="", end="\n"):
    S.say(text, end=end)


class Halt(Exception):
    """A stop before anything was changed."""

    def __init__(self, what, todo, more=()):
        Exception.__init__(self, what)
        self.what, self.todo, self.more = what, todo, list(more)

    def show(self, step=4):
        say("")
        say("Stopped at step %d: %s." % (step, self.what))
        say("What to do: %s" % self.todo)
        for m in self.more:
            say("   " + m)


class Failed(Exception):
    """A step that could not finish after others had already changed things."""

    def __init__(self, line, todo):
        Exception.__init__(self, line)
        self.line, self.todo = line, todo


class _Args(argparse.ArgumentParser):
    def error(self, message):
        raise Halt("the remove line was not understood (%s), so nothing was changed" % message,
                   "type it as: Assistant.cmd remove --check, then Assistant.cmd remove")


def said(e):
    return kit_place.said(e)


# ---------------------------------------------------------------- folders remove must never take anything from

def _profiles():
    out = []
    for home in (os.environ.get("USERPROFILE") or "", str(pathlib.Path.home())):
        if home and not any(S._same(home, h) for h in out):
            out.append(home)
    return out


def _shell_folder(guid):
    """A known folder as Windows names it (a Desktop or Documents moved into OneDrive included), or None."""
    if os.name != "nt":
        return None
    try:
        class GUID(ctypes.Structure):
            _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]
        g, p = GUID(), ctypes.c_wchar_p()
        if ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(guid), ctypes.byref(g)) != 0:
            return None
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) != 0:
            return None
        value = p.value
        ctypes.windll.ole32.CoTaskMemFree(p)
        return value
    except Exception:  # noqa: BLE001
        return None


KNOWN = (("Desktop", "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"), ("Documents", "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"),
         ("Downloads", "{374DE290-123F-4565-9164-39C4925E467B}"))


def known_folders(desktop=None):
    out = {name: [pathlib.Path(h) / name for h in _profiles()] for name, _ in KNOWN}
    for name, guid in KNOWN:
        p = _shell_folder(guid)
        if p:
            out[name].append(pathlib.Path(p))
    if desktop:
        out["Desktop"].append(pathlib.Path(desktop))
    return out


def odd_folder(path, desktop=None):
    """None, or why remove must never take anything away at this folder. Reads only."""
    r = S._resolved(path)
    if r.parent == r or str(r) == r.anchor:
        return "is the top of a drive"
    for home in _profiles():
        if S._same(r, home):
            return "is your whole user folder"
        if S._inside(home, r):
            return "holds your whole user folder"
    for win in {os.environ.get("SystemRoot") or "", os.environ.get("windir") or ""}:
        if win and (S._inside(r, win) or S._inside(win, r)):
            return "is or holds the Windows folder"
    known = known_folders(desktop)
    for name in ("Desktop", "Documents"):
        if any(S._same(r, p) for p in known[name]):
            return "is your %s folder itself" % name
    if any(S._inside(r, p) or S._inside(p, r) for p in known["Downloads"]):
        return "is, holds or is inside your Downloads folder"
    return None


def is_kit_home(kh):
    """True when the folder holds the kit: kit.txt, a kit under kits\\, or the old current\\ kit."""
    kh = pathlib.Path(kh)
    if (kh / kit_place.POINTER).is_file() or (kh / "current" / "plugins" / PLUGIN).is_dir():
        return True
    try:
        return any((d / "plugins" / PLUGIN).is_dir() for d in (kh / kit_place.KITS).iterdir())
    except OSError:
        return False


# ---------------------------------------------------------------- what is in use (reads only)

if os.name == "nt":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.HANDLE]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
_DELETE, _SHARE_ALL, _OPEN_EXISTING, _BACKUP, _NO_FOLLOW = 0x00010000, 0x7, 3, 0x02000000, 0x00200000
_IN_USE = (32, 33)   # a sharing or lock violation: another program holds it


def _busy(path):
    """True when another program has this file or folder open in a way that stops it being moved or deleted (an open
    file, or a window or program whose working folder it is). Asks Windows to open it for deleting, with every kind of
    sharing allowed, and closes it again at once: nothing is moved, deleted or written."""
    if os.name != "nt":
        return False
    h = _k32.CreateFileW(str(path), _DELETE, _SHARE_ALL, None, _OPEN_EXISTING, _BACKUP | _NO_FOLLOW, None)
    if h is None or h == wintypes.HANDLE(-1).value:
        return ctypes.get_last_error() in _IN_USE
    _k32.CloseHandle(h)
    return False


def _reparse(path):
    try:
        st = os.lstat(str(path))
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400)


def _walk(top):
    """Every file and folder under top, never through a link or junction."""
    stack = [str(top)]
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            yield e.path
            if not _reparse(e.path) and e.is_dir(follow_symlinks=False):
                stack.append(e.path)


def held(top):
    """None, or the first file or folder at or under top that another program holds (see _busy)."""
    if _busy(top):
        return pathlib.Path(top)
    if _reparse(top) or not os.path.isdir(str(top)):
        return None
    for p in _walk(top):
        if _busy(p):
            return pathlib.Path(p)
    return None


# ---------------------------------------------------------------- what the first install left (reads only)

def scan_seat_home(seat_home):
    """(folders the launcher made, adopted folders, folders an earlier remove moved aside, folders with a marker that
    is not the kit's) directly under the seat home."""
    seats, adopted, earlier, other = [], [], [], []
    try:
        entries = sorted(pathlib.Path(seat_home).iterdir())
    except OSError:
        entries = []
    for d in entries:
        if not d.is_dir():
            continue
        if REMOVED.search(d.name):
            earlier.append(d)
            continue
        if not (d / seat_common.MARKER).is_file():
            continue
        cfg = seat_common.load_config(d)
        if cfg.get("adopted"):
            adopted.append(d)
        elif cfg.get("kit") == PLUGIN:
            seats.append(d)
        else:
            other.append(d)
    return seats, adopted, earlier, other


def recorded_adopted(kit_home):
    """The adopted folders named in <kit home>\\adopted.txt that are still adopted (marker there, saying so)."""
    try:
        lines = (pathlib.Path(kit_home) / S.ADOPTED_LIST).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    out = []
    for ln in lines:
        p = ln.strip()
        if p and pathlib.Path(p).is_dir() and (pathlib.Path(p) / seat_common.MARKER).is_file() \
                and seat_common.load_config(p).get("adopted"):
            out.append(pathlib.Path(S.long_path(p)))
    return out


def own_launchers(folder):
    """Files named assistant.cmd or assistant.ps1 anywhere in the folder (a launcher of the person's own)."""
    return sorted(pathlib.Path(p) for p in _walk(folder)
                  if os.path.basename(p).lower() in S.PROTECTED and os.path.isfile(p))


def is_kit_launcher(path):
    """The kit's own launcher copy, told by what is in it (every kit launcher runs seat_setup.py from seat-kit)."""
    try:
        data = pathlib.Path(path).read_bytes()
    except OSError:
        return False
    return b"seat_setup.py" in data and b"seat-kit" in data


def kit_entries(kh):
    """(the kit's own files and folders directly in the kit home, everything else there)."""
    own, other = [], []
    try:
        entries = sorted(pathlib.Path(kh).iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return own, other
    for p in entries:
        n = p.name.lower()
        if n == KEPT_LAUNCHER.lower():
            mine = p.is_file() and is_kit_launcher(p)
        else:
            mine = ((n in KIT_FILES and p.is_file()) or (n in KIT_DIRS and p.is_dir() and not _reparse(p))
                    or (p.is_file() and any(rx.search(p.name) for rx in KIT_PATTERNS)))
        (own if mine else other).append(p)
    own.sort(key=lambda p: (p.name.lower() in LAST_IN_KIT_HOME, LAST_IN_KIT_HOME.index(p.name.lower())
                            if p.name.lower() in LAST_IN_KIT_HOME else 0))
    return own, other


SHORTCUT_PS = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; $sh = New-Object -ComObject WScript.Shell; "
               "Get-ChildItem -LiteralPath $env:SEAT_DESK -Filter *.lnk -File -ErrorAction SilentlyContinue | "
               "ForEach-Object { $l = $sh.CreateShortcut($_.FullName); "
               "[Console]::Out.WriteLine($_.FullName + [char]9 + $l.TargetPath + [char]9 + $l.Description) }")


def read_shortcuts(desktop):
    """([(lnk, target, description)] for each .lnk directly on the desktop, None) or (None, why). Read only: a
    shortcut is opened to read where it points, never saved."""
    if desktop is None:
        return None, "Windows did not say where your desktop is"
    if not pathlib.Path(desktop).is_dir():
        return [], None
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", SHORTCUT_PS],
                           capture_output=True, timeout=120, stdin=subprocess.DEVNULL,
                           env=dict(os.environ, SEAT_DESK=str(desktop)))
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, "the desktop could not be read (%s)" % type(e).__name__
    if r.returncode != 0:
        return None, "the desktop could not be read (%s)" % S._last(r.stderr.decode("utf-8", "replace"))
    out = []
    for line in r.stdout.decode("utf-8", "replace").lstrip("﻿").splitlines():
        parts = line.rstrip("\r").split("\t")
        if len(parts) == 3 and parts[0].lower().endswith(".lnk"):
            out.append((pathlib.Path(parts[0]), parts[1], parts[2]))
    return out, None


def classify_shortcuts(found, kit_home, names):
    """(the kit's shortcuts: their target is the kept launcher, ones that look like the kit's but point elsewhere)."""
    target_kit = pathlib.Path(kit_home) / KEPT_LAUNCHER
    take, left = [], []
    lowered = {n.lower() for n in names if n}
    for lnk, target, desc in found or []:
        if target and S._same(target, target_kit):
            take.append((lnk, target))
        elif lnk.stem.lower() in lowered or desc == SHORTCUT_DESCRIPTION:
            left.append((lnk, target))
    return take, left


def plugin_state(claude):
    """(installed, registered), asked of Claude Code. Halt when it does not answer: nothing is changed then."""
    found = []
    for what, args in (("which plugins it has", ("plugin", "list", "--json")),
                       ("which plugin sources it has", ("plugin", "marketplace", "list", "--json"))):
        rc, out = S._claude(claude, *args)
        known = S._json_list(out) if rc == 0 else None
        if known is None:
            raise Halt("Claude Code did not answer when asked %s (%s), so nothing was changed" % (what, S._last(out)),
                       "close every Claude Code window and run remove again. If it stops here twice, " + TODO_SEND)
        found.append(known)
    ids = {p.get("id") for p in found[0] if isinstance(p, dict)}
    names = {m.get("name") for m in found[1] if isinstance(m, dict)}
    return PLUGIN_ID in ids, MARKETPLACE in names


# ---------------------------------------------------------------- the plan

class Plan:
    pass


def run_line(launcher, *words):
    return S._launcher_line(launcher, *words)


def copy_out_lines(launcher, words):
    tail = " ".join(words)
    return ['copy "%s" "%%TEMP%%\\Assistant.cmd"' % launcher, '"%%TEMP%%\\Assistant.cmd" %s' % tail]


def make_plan(args, check):
    """Everything remove would do, worked out without changing anything. Halt on anything that must stop the whole
    run (in --check, the reasons are collected in p.blockers and printed instead)."""
    p = Plan()
    p.stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    p.kit_home = pathlib.Path(S.long_path(args.kit_home))
    p.seat_home = pathlib.Path(S.long_path(args.seat_home))
    p.desktop = S.desktop_folder(args.desktop)
    p.desktop = pathlib.Path(S.long_path(p.desktop)) if p.desktop is not None else None
    p.claude = args.claude if os.path.isfile(args.claude or "") else None
    p.launcher = S.long_path(args.launcher) if args.launcher else ""
    p.words = ["remove"] + (["--delete-my-notes"] if args.delete_my_notes else [])
    p.blockers = []

    def block(halt):
        if not check:
            raise halt
        p.blockers.append(halt)

    folder = S.clean_folder_arg(args.folder)
    if folder:
        f = pathlib.Path(folder)
        if (f / seat_common.MARKER).is_file() and seat_common.load_config(f).get("adopted"):
            raise Halt("%s was set up with adopt, and remove never touches an adopted folder, so nothing was changed"
                       % S._resolved(f), "to take that folder back, run: %s"
                       % run_line(args.launcher, "adopt", "--undo", '"%s"' % S._resolved(f)))
        raise Halt("remove takes no folder name (it takes back the whole first install), so nothing was changed",
                   "run %s first to see what it would do, then %s"
                   % (run_line(args.launcher, "remove", "--check"), run_line(args.launcher, "remove")))
    for label, where in (("the kit folder", p.kit_home), ("the folder for assistants", p.seat_home)):
        why = odd_folder(where, p.desktop)
        if why:
            raise Halt("%s this launcher was given (%s) %s, so remove will not take anything from it, and nothing "
                       "was changed" % (label, where, why), TODO_SEND)
    if S._inside(p.kit_home, p.seat_home) or S._inside(p.seat_home, p.kit_home):
        raise Halt("the kit folder (%s) and the folder for assistants (%s) overlap, so remove will not take anything "
                   "from them, and nothing was changed" % (p.kit_home, p.seat_home), TODO_SEND)
    p.kit_there = p.kit_home.is_dir()
    if p.kit_there and not is_kit_home(p.kit_home):
        raise Halt("%s does not look like the kit's own folder (it has no kit.txt and no kit in it), so remove will "
                   "not take anything from it, and nothing was changed" % p.kit_home, TODO_SEND)
    p.launcher_inside = bool(p.launcher) and p.kit_there and S._inside(p.launcher, p.kit_home)
    if p.launcher_inside:
        block(Halt("this launcher is the copy kept inside the kit folder (%s), and a file that is running cannot "
                   "take away the folder it is in, so nothing was changed" % p.launcher,
                   "run remove from the Assistant.cmd you downloaded, or copy this one out first and run the copy, "
                   "with these two lines in a Command Prompt:", copy_out_lines(p.launcher, p.words)))

    p.seats, here, p.earlier, p.other_markers = scan_seat_home(p.seat_home)
    p.adopted = []
    for a in here + recorded_adopted(p.kit_home):
        if not any(S._same(a, b) for b in p.adopted):
            p.adopted.append(a)
    p.keep_shared = bool(p.adopted)
    for seat in p.seats:
        for own in own_launchers(seat):
            block(Halt("%s is a launcher of your own inside your assistant's folder, and remove never moves or "
                       "deletes one, so nothing was changed" % own, "move that file out of the folder, then run "
                       "remove again"))
    for seat in p.seats:
        h = held(seat)
        if h is not None:
            block(Halt("your assistant's folder %s is in use (%s is open in another program, or a window is sitting "
                       "in it), so nothing was changed" % (seat, h), CLOSE))
    p.kit_own, p.kit_other = kit_entries(p.kit_home) if p.kit_there else ([], [])
    p.kit_take = [e for e in p.kit_own if not p.keep_shared or e.name.lower() in FIRST_INSTALL_ONLY]
    p.kit_folder_goes = p.kit_there and not p.keep_shared and not p.kit_other
    for e in p.kit_take + ([p.kit_home] if p.kit_folder_goes else []):
        h = held(e) if e != p.kit_home else (p.kit_home if _busy(p.kit_home) else None)
        if h is not None:
            block(Halt("the kit folder %s is in use (%s is open in another program, or a window is sitting in it), "
                       "so nothing was changed" % (p.kit_home, h), CLOSE))
            break

    names = [seat_common.load_config(s).get("seat_name") or s.name for s in p.seats] + [s.name for s in p.seats]
    try:
        last = (p.kit_home / "last-seat.txt").read_text(encoding="ascii", errors="replace").strip().split("|")
        names += last
    except OSError:
        pass
    found, p.lnk_error = read_shortcuts(p.desktop)
    p.lnk_take, p.lnk_left = classify_shortcuts(found, p.kit_home, names)
    p.installed = p.registered = None
    if not check and p.claude and not p.keep_shared:
        p.installed, p.registered = plugin_state(p.claude)
    return p


def confirm_names(p, args):
    """--delete-my-notes: the person types each folder's name before anything changes. A wrong name stops."""
    if not args.delete_my_notes:
        return
    for seat in p.seats:
        try:
            typed = builtins.input("   To delete %s and everything in it, type its name (%s) and press Enter: "
                                   % (seat, seat.name))
        except EOFError:
            say("")
            typed = ""
        if typed.strip().lower() != seat.name.lower():
            raise Halt("the name typed (%s) is not %s, so nothing was deleted or changed" % (typed.strip() or "nothing",
                                                                                               seat.name),
                       "to delete it, run %s again and type the name exactly as shown; to keep your notes in a moved "
                       "folder instead, run %s" % (run_line(args.launcher, *p.words), run_line(args.launcher, "remove")))


# ---------------------------------------------------------------- --check

def left_lines(p):
    """What remove leaves, and why: one plain line each, with its full location."""
    out = ["%s was set up with adopt, and remove never touches an adopted folder. The kit plugin and the kit folder "
           "stay because it uses them (its send gate runs %s). To take it back as well: %s, then run remove again."
           % (a, p.kit_home / kit_place.SHIM, run_line(p.launcher, "adopt", "--undo", '"%s"' % a)) for a in p.adopted]
    out += ["the desktop shortcut %s points at %s, not at the kit's launcher, so it is left as it is."
            % (lnk, target or "nothing") for lnk, target in p.lnk_left]
    out += ["%s is not one of the kit's own files, so it is left, and so is the kit folder that holds it." % e
            for e in p.kit_other]
    out += ["%s holds your notes from an earlier remove; deleting it is up to you." % e for e in p.earlier]
    out += ["%s has an assistant marker that is not the kit's own, so it is left as it is." % e
            for e in p.other_markers]
    out.append(NEVER + ".")
    return out


def print_check(p, args):
    say("")
    say("Check only: this is what remove would do. Nothing was changed: no folder, file or shortcut was touched, "
        "and Claude Code was not called.")
    if p.blockers:
        say("")
        say("Right now remove would stop before changing anything, because:")
        for h in p.blockers:
            say("   %s%s." % (h.what[:1].upper(), h.what[1:]))
            say("   What to do: %s" % h.todo)
            for m in h.more:
                say("      " + m)
    say("")
    say("Remove would take away, in this order:")
    if p.keep_shared:
        say("  1. The kit plugin in Claude Code: kept, because an adopted folder uses it (named below)")
    elif p.claude:
        say("  1. The kit plugin in Claude Code, if it is there: claude plugin uninstall %s, then claude plugin "
            "marketplace remove %s" % (PLUGIN_ID, MARKETPLACE))
    else:
        say("  1. The kit plugin in Claude Code: cannot be taken out, because Claude Code was not found where the kit "
            "looks (%s); remove would print the two lines to run later" % (args.claude or "nothing given"))
    if p.lnk_error:
        say("  2. The desktop shortcut: %s, so no shortcut would be touched" % p.lnk_error)
    elif not p.lnk_take:
        say("  2. A desktop shortcut of the kit's: none on your desktop (%s)" % p.desktop)
    for lnk, target in p.lnk_take:
        say("  2. The desktop shortcut %s (it opens %s)" % (lnk, target))
    if not p.seats:
        say("  3. An assistant's folder made by the kit: none in %s" % p.seat_home)
    for seat in p.seats:
        if args.delete_my_notes:
            say("  3. Your assistant's folder %s: DELETED with everything in it, after you type its name (%s) to "
                "confirm" % (seat, seat.name))
        else:
            say("  3. Your assistant's folder %s: moved, not deleted, to %s. Your notes stay in it; deleting that "
                "folder later is up to you." % (seat, seat.with_name(seat.name + ".removed-<date>-<time>")))
    if not p.kit_there:
        say("  4. The kit folder %s: not there" % p.kit_home)
    elif not p.kit_take:
        say("  4. From the kit folder %s: nothing" % p.kit_home)
    else:
        say("  4. %s:" % ("The kit folder %s, and the kit's own things in it" % p.kit_home if p.kit_folder_goes else
                          "From the kit folder %s, only these (the folder itself stays)" % p.kit_home))
        for e in p.kit_take:
            say("       %s" % e)
    say("")
    say("Remove would leave:")
    for line in left_lines(p):
        say("   %s%s" % (line[:1].upper(), line[1:]))
    say("   " + OLD_ADOPT)
    say("")
    if p.launcher_inside:
        say("To do it: run %s from the Assistant.cmd you downloaded (this copy is inside the kit folder)."
            % " ".join(p.words))
    else:
        say("To do it:   %s" % run_line(args.launcher, *p.words))


# ---------------------------------------------------------------- taking it back

def _guard(path, parent, what):
    if not S._inside(path, parent) or S._same(path, parent):
        raise Failed("  The kit was about to touch %s, which is not inside %s, so it stopped." % (path, parent),
                     TODO_SEND)


def _delete(path):
    """Delete one file, or one folder with everything in it (a link or junction inside is removed, never followed).
    A read-only file is made writable first. Tried three times over about two seconds. Raises OSError."""
    path = pathlib.Path(path)
    last = None
    for wait in (0, 0.5, 1.5):
        if wait:
            time.sleep(wait)
        try:
            if path.is_dir() and not _reparse(path):
                for p in list(_walk(path)):
                    try:
                        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
                    except OSError:
                        pass
                shutil.rmtree(str(path))
            elif _reparse(path) and path.is_dir():
                os.rmdir(str(path))
            elif os.path.lexists(str(path)):
                try:
                    os.chmod(str(path), stat.S_IWRITE | stat.S_IREAD)
                except OSError:
                    pass
                os.remove(str(path))
            return
        except FileNotFoundError:
            return
        except OSError as e:
            last = e
    raise last


def dated(seat, stamp):
    dest, n = seat.with_name("%s.removed-%s" % (seat.name, stamp)), 2
    while os.path.lexists(str(dest)):
        dest, n = seat.with_name("%s.removed-%s-%d" % (seat.name, stamp, n)), n + 1
    return dest


def step_plugin(p, args, done):
    if p.keep_shared:
        say("  1. The kit plugin: kept, because an adopted folder uses it (named below)")
        return
    if not p.claude:
        p.plugin_left = True
        say("  1. The kit plugin: not taken out, because Claude Code was not found where the kit looks (%s). Nothing "
            "registered can be removed without it. When Claude Code is back, run these two lines in a Command Prompt:"
            % (args.claude or "nothing given"))
        say("       claude plugin uninstall %s" % PLUGIN_ID)
        say("       claude plugin marketplace remove %s" % MARKETPLACE)
        return
    todo = "close every Claude Code window, then " + TODO_AGAIN + ". If it stops here twice, " + TODO_SEND
    if p.installed:
        rc, out = S._claude(p.claude, "plugin", "uninstall", PLUGIN_ID)
        if rc != 0:
            p.plugin_left = True
            raise Failed("  1. The kit plugin: could not be uninstalled (Claude Code said: %s)" % S._last(out), todo)
        done.append("the kit plugin (uninstalled from Claude Code)")
    if p.registered:
        rc, out = S._claude(p.claude, "plugin", "marketplace", "remove", MARKETPLACE)
        if rc != 0:
            p.plugin_left = True
            raise Failed("  1. The kit plugin: %sits source %s could not be removed from Claude Code (Claude Code "
                         "said: %s)" % ("uninstalled, but " if p.installed else "", MARKETPLACE, S._last(out)), todo)
        done.append("the kit's plugin source in Claude Code (%s)" % MARKETPLACE)
    if p.installed and p.registered:
        say("  1. The kit plugin: done (uninstalled from Claude Code, and its source %s removed)" % MARKETPLACE)
    elif p.installed:
        say("  1. The kit plugin: done (uninstalled from Claude Code; its source %s was not there)" % MARKETPLACE)
    elif p.registered:
        say("  1. The kit plugin: done (its source %s removed from Claude Code; the plugin itself was not there)"
            % MARKETPLACE)
    else:
        say("  1. The kit plugin: not there (Claude Code has neither the plugin nor its source)")


def step_shortcuts(p, done):
    if p.lnk_error:
        say("  2. The desktop shortcut: %s, so no shortcut was touched" % p.lnk_error)
        return
    if not p.lnk_take:
        say("  2. The desktop shortcut: not there")
    for lnk, _ in p.lnk_take:
        if not lnk.name.lower().endswith(".lnk") or not S._same(lnk.parent, p.desktop):
            raise Failed("  The kit was about to touch %s, which is not a shortcut on your desktop, so it stopped."
                         % lnk, TODO_SEND)
        try:
            os.remove(str(lnk))
        except FileNotFoundError:
            say("  2. The desktop shortcut %s: not there" % lnk)
            continue
        except OSError as e:
            raise Failed("  2. The desktop shortcut %s: could not be deleted (Windows said: %s)" % (lnk, said(e)),
                         "close any window showing your desktop's files, then " + TODO_AGAIN)
        done.append("the desktop shortcut %s" % lnk)
        say("  2. The desktop shortcut %s: deleted" % lnk)


def step_folders(p, args, done):
    if not p.seats:
        say("  3. Your assistant's folder: not there (no folder made by the kit in %s)" % p.seat_home)
    for seat in p.seats:
        _guard(seat, p.seat_home, "folder")
        dest = dated(seat, p.stamp)
        try:
            os.rename(str(seat), str(dest))
        except OSError as e:
            raise Failed("  3. Your assistant's folder %s: could not be moved (Windows said: %s)" % (seat, said(e)),
                         CLOSE.replace("run remove again", TODO_AGAIN))
        if not args.delete_my_notes:
            done.append("your assistant's folder %s (moved to %s)" % (seat, dest))
            say("  3. Your assistant's folder %s: moved to %s. All your notes are in it; deleting that folder later is "
                "up to you." % (seat, dest))
            continue
        try:
            _delete(dest)
        except OSError as e:
            raise Failed("  3. Your assistant's folder %s: could not be deleted completely (Windows said: %s). What is "
                         "left of it is in %s" % (seat, said(e), dest),
                         "close any program using that folder and delete %s by hand, then %s" % (dest, TODO_AGAIN))
        done.append("your assistant's folder %s (deleted)" % seat)
        say("  3. Your assistant's folder %s: deleted, with everything in it" % seat)
    if args.delete_my_notes and p.seats:
        try:
            p.seat_home.rmdir()          # only when nothing else is in it
        except OSError:
            pass


def step_kit_home(p, done):
    if not p.kit_there:
        say("  4. The kit folder %s: not there" % p.kit_home)
        return
    for e in p.kit_take:
        _guard(e, p.kit_home, "kit")
        try:
            _delete(e)
        except OSError as err:
            raise Failed("  4. The kit folder %s: could not delete %s (Windows said: %s)" % (p.kit_home, e, said(err)),
                         CLOSE.replace("run remove again", TODO_AGAIN))
    if p.kit_take:
        done.append("the kit's own things in %s" % p.kit_home)
    if p.kit_folder_goes:
        try:
            p.kit_home.rmdir()
        except FileNotFoundError:
            pass
        except OSError as err:
            raise Failed("  4. The kit folder %s: its contents were deleted, but the folder itself could not be "
                         "(Windows said: %s)" % (p.kit_home, said(err)), CLOSE.replace("run remove again", TODO_AGAIN))
        done.append("the kit folder %s" % p.kit_home)
        say("  4. The kit folder %s: deleted" % p.kit_home)
    elif p.keep_shared:
        say("  4. The kit folder %s: kept, because an adopted folder uses its send gate (named below); %s"
            % (p.kit_home, "only the kit's copy of Assistant.cmd and last-seat.txt were deleted from it" if p.kit_take
               else "nothing of the first install was in it"))
    else:
        say("  4. The kit folder %s: the kit's own things deleted; the folder itself stays, because it also holds "
            "things that are not the kit's (named below)" % p.kit_home)


def still_there(p):
    """What of the first install is still on the PC, looked at again (the plugin from what this run saw)."""
    out = []
    if getattr(p, "plugin_left", False):
        out.append("the kit plugin in Claude Code")
    seats = scan_seat_home(p.seat_home)[0]
    out += ["your assistant's folder %s" % s for s in seats]
    found, err = read_shortcuts(p.desktop)
    out += ["the desktop shortcut %s" % lnk for lnk, _ in classify_shortcuts(found, p.kit_home, [])[0]]
    if p.kit_folder_goes and p.kit_home.exists():
        out.append("the kit folder %s" % p.kit_home)
    elif p.kit_there:
        out += ["%s" % e for e in p.kit_take if os.path.lexists(str(e))]
    return out


def take_back(p, args):
    done = []
    p.plugin_left = False
    say("Step 5 of 6: taking it back ...")
    try:
        step_plugin(p, args, done)
        step_shortcuts(p, done)
        step_folders(p, args, done)
        step_kit_home(p, done)
    except Failed as f:
        say(f.line)
        say("")
        say("Stopped at step 5: remove could not finish taking the first install back.")
        say("Already taken back:%s" % ("" if done else " nothing"))
        for d in done:
            say("   " + d)
        left = still_there(p)
        say("Still there:%s" % ("" if left else " nothing"))
        for x in left:
            say("   " + x)
        say("What to do: %s" % f.todo)
        return 1
    say("Step 6 of 6: checking what is left ... ", end="")
    left = [x for x in still_there(p) if x != "the kit plugin in Claude Code"]
    if left:
        say("not everything is gone")
        for x in left:
            say("   Still there: " + x)
        say("")
        say("Stopped at step 6: some of the first install is still there.")
        say("What to do: " + CLOSE.replace("run remove again", TODO_AGAIN))
        return 1
    say("nothing of the first install is left%s" % (", apart from the plugin (see step 1)" if p.plugin_left else ""))
    say("")
    for line in left_lines(p):
        say("Not touched: " + line)
    say("")
    say("Done: the first install is taken back." if done else "Nothing of a first install was there to take back.")
    return 0


def main(argv):
    seat_common.utf8_stdout()
    ap = _Args(prog="seat_setup.py remove")
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--delete-my-notes", action="store_true")
    ap.add_argument("--claude", default="")
    ap.add_argument("--kit-home", required=True)
    ap.add_argument("--seat-home", default=str(pathlib.Path.home() / "Assistant"))
    ap.add_argument("--desktop", nargs="?", const="", default=None)
    ap.add_argument("--launcher", default="")
    try:
        args = ap.parse_args(argv)
    except Halt as h:
        h.show()
        return 1
    try:
        os.chdir(os.path.dirname(os.path.abspath(sys.executable)))   # this process never holds a folder it takes
    except OSError:
        pass
    say("Step 4 of 6: looking at what the first install put on this PC ... ", end="")
    try:
        p = make_plan(args, args.check)
    except Halt as h:
        say("stopped")
        h.show()
        return 1
    except Exception as e:  # noqa: BLE001
        say("stopped")
        say("")
        say("Stopped at step 4: something unexpected went wrong (%s: %s), and nothing was changed." % (
            type(e).__name__, str(e)[:160]))
        say("What to do: " + TODO_SEND)
        return 1
    say("done")
    if args.check:
        print_check(p, args)
        return 0
    try:
        confirm_names(p, args)
    except Halt as h:
        h.show()
        return 1
    try:
        return take_back(p, args)
    except Exception as e:  # noqa: BLE001
        say("")
        say("Stopped at step 5: something unexpected went wrong (%s: %s)." % (type(e).__name__, str(e)[:160]))
        say("What to do: run remove --check to see what is still there, then " + TODO_SEND)
        return 1
