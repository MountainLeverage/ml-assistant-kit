"""Build the kit zip that the assistant launcher downloads: a one-plugin local marketplace.

    python -I make_kit.py --out <kit.zip> [--source <https URL where this zip will be served>]

Zip layout (the launcher runs the zip itself, `python -I <zip> --kit-home <folder>`, which unpacks it into
%LOCALAPPDATA%\\seat-kit\\kits\\<version> and switches kit.txt to it; see scripts/kit_place.py. That folder is then
registered as a local folder marketplace named "seat-kit"):

    __main__.py                         runs plugins/ml-seat-kit/scripts/kit_place.py from inside the zip
    .claude-plugin/marketplace.json     lists the one plugin, ml-seat-kit, at ./plugins/ml-seat-kit
    plugins/ml-seat-kit/...             the plugin folder, verbatim (no caches)
    Assistant.cmd, Assistant.ps1        the launchers, with KIT_SOURCE filled in when --source is given

Bump the version in plugin.json (and the company marketplace entry) for EVERY zip you hand out: an installed copy
with the same version is not refreshed. The zip is checked before it is written: the kit's whole test suite must
pass and every file must pass the kit's privacy scan (tests/test_privacy.py), or nothing is written.
"""
import argparse
import io
import json
import pathlib
import sys
import zipfile

PLUGIN = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
SKIP_SUFFIX = (".pyc", ".pyo", ".tmp")
SKIP_NAMES = {"private_words.txt"}   # the private deny list (tests/test_privacy.py): never in a zip
PLACEHOLDER = b"PUT-KIT-ZIP-ADDRESS-HERE"
ZIP_MAIN = b'''"""Run by the assistant launcher: python -I <this zip> --kit-home <folder>. Puts this kit in place."""
import os
import sys

sys.path.insert(0, os.path.join(sys.argv[0], "plugins", "ml-seat-kit", "scripts"))
import kit_place  # noqa: E402

raise SystemExit(kit_place.main(sys.argv[1:] + ["--zip", os.path.abspath(sys.argv[0])]))
'''


def plugin_files():
    for p in sorted(PLUGIN.rglob("*")):
        if (p.is_file() and not (set(p.relative_to(PLUGIN).parts) & SKIP_DIRS)
                and not p.name.endswith(SKIP_SUFFIX)
                and p.name not in SKIP_NAMES):
            yield p


def marketplace(version):
    return {
        "name": "seat-kit",
        "owner": {"name": "Seat kit maintainers"},
        "description": "One plugin: the assistant seat kit, served from a local folder so no git is needed.",
        "plugins": [{
            "name": "ml-seat-kit",
            "source": "./plugins/ml-seat-kit",
            "version": version,
            "category": "productivity",
            "description": "A drafts-only personal email assistant seat for one person.",
        }],
    }


def crlf(data):
    """Batch files with bare LF line ends can break goto labels; whatever the checkout did, ship CRLF."""
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def stamp(data, source):
    data = crlf(data)
    if not source:
        return data
    if PLACEHOLDER not in data:
        raise SystemExit("launcher has no %s placeholder to fill" % PLACEHOLDER.decode())
    return data.replace(PLACEHOLDER, source.encode("utf-8"))


NO_PRIVATE_LIST = " ".join((
    "REFUSED: this is a release build (--source is set) and the private name list is missing",
    "(tests/private_words.txt). Without it the zip is only checked for generic things (paths,",
    "addresses), not for the names of real people, seats or the company.",
    "Put the list in place and run again. Only if you have checked the kit for names by hand,",
    "run again with --without-private-list."))


def require_private_list(words, source, without=False):
    """A release build (one with a download address) needs the private name list, unless the person
    cutting it says, in so many words, that they are going without. Never a silent skip."""
    if source and not words and not without:
        raise SystemExit(NO_PRIVATE_LIST)
    if source and not words:
        print("WARNING: release build WITHOUT the private name list; names were not scanned for.")


def kit_need_lines(version):
    """The line in each launcher that names the oldest kit self test and adopt may run: the kit's own version."""
    return {"Assistant.cmd": ('set "KIT_NEED=%s"' % version).encode("ascii"),
            "Assistant.ps1": ("$KitNeed = '%s'" % version).encode("ascii")}


def require_kit_need(version):
    """Refuse a zip whose launchers would accept an older kit than the one they ship with."""
    for name, line in kit_need_lines(version).items():
        if line not in (PLUGIN / "launcher" / name).read_bytes():
            raise SystemExit("REFUSED: launcher/%s does not say %s (it must name this kit's version, %s)"
                             % (name, line.decode("ascii"), version))


def run_tests():
    """The kit's whole suite in a fresh python -I, exactly as the self tests run it (seat_setup.run_suite)."""
    if str(PLUGIN / "scripts") not in sys.path:
        sys.path.insert(0, str(PLUGIN / "scripts"))
    import seat_setup   # noqa: E402
    return seat_setup.run_suite(None, timeout=900)


def require_passing(res):
    """Refuse the build unless every test passed. The names come from unittest's own FAIL and ERROR lines."""
    if res.get("ok"):
        return "%s tests ran, all passed (%d skipped)" % (res.get("ran"), res.get("skipped", 0))
    names = []
    for _, name, _ in res.get("failed", []):
        if name not in names:
            names.append(name)
    tail = "\n".join((res.get("output") or "").splitlines()[-40:])
    raise SystemExit("REFUSED: the kit's tests did not all pass, so nothing was written (%s%s).\n%s" % (
        res.get("summary") or "no result", ": " + ", ".join(names) if names else "", tail))


def build(out, source=None, without_private_list=False, tests=run_tests):
    sys.path.insert(0, str(PLUGIN / "tests"))
    import test_privacy   # noqa: E402  (the same scan the tests run)
    require_private_list(test_privacy.private_words(), source, without_private_list)
    print("running the kit's tests first ...")
    print(require_passing(tests()))
    version = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    entries = {".claude-plugin/marketplace.json": (json.dumps(marketplace(version), indent=2) + "\n").encode("utf-8"),
               "__main__.py": ZIP_MAIN}
    for p in plugin_files():
        entries["plugins/ml-seat-kit/" + p.relative_to(PLUGIN).as_posix()] = p.read_bytes()
    require_kit_need(version)
    for name in ("Assistant.cmd", "Assistant.ps1"):
        entries[name] = stamp((PLUGIN / "launcher" / name).read_bytes(), source)
    hits = []
    for name, data in entries.items():
        # the download address is the one thing allowed to name its host: it is public by definition
        seen = data.replace(source.encode("utf-8"), PLACEHOLDER) if source else data
        hits += ["%s: %s" % (name, h) for h in test_privacy.scan_text(seen.decode("utf-8", "replace"))]
    if hits:
        raise SystemExit("privacy scan failed, nothing written:\n  " + "\n  ".join(hits[:20]))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(entries):
            z.writestr(name, entries[name])
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(buf.getvalue())
    return version, len(entries)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="make_kit.py")
    ap.add_argument("--out", required=True)
    ap.add_argument("--source", default=None, help="the https address the zip will be served from")
    ap.add_argument("--without-private-list", action="store_true",
                    help="cut a release with no private name list present (you checked for names by hand)")
    args = ap.parse_args(argv)
    version, n = build(args.out, args.source, args.without_private_list)
    print("kit %s: %d files -> %s" % (version, n, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
