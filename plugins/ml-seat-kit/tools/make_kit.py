"""Build the kit zip that the assistant launcher downloads: a one-plugin local marketplace.

    python -I make_kit.py --out <kit.zip> [--source <https URL where this zip will be served>]

Zip layout (expanded by the launcher into %LOCALAPPDATA%\\seat-kit\\current, then registered as a local folder
marketplace named "seat-kit"):

    .claude-plugin/marketplace.json     lists the one plugin, ml-seat-kit, at ./plugins/ml-seat-kit
    plugins/ml-seat-kit/...             the plugin folder, verbatim (no caches)
    Assistant.cmd, Assistant.ps1        the launchers, with KIT_SOURCE filled in when --source is given

Bump the version in plugin.json (and the company marketplace entry) for EVERY zip you hand out: an installed copy
with the same version is not refreshed. The zip is checked before it is written: every file must pass the kit's
privacy scan (tests/test_privacy.py), or nothing is written.
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


def build(out, source=None):
    sys.path.insert(0, str(PLUGIN / "tests"))
    import test_privacy   # noqa: E402  (the same scan the tests run)
    version = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    entries = {".claude-plugin/marketplace.json": (json.dumps(marketplace(version), indent=2) + "\n").encode("utf-8")}
    for p in plugin_files():
        entries["plugins/ml-seat-kit/" + p.relative_to(PLUGIN).as_posix()] = p.read_bytes()
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
    args = ap.parse_args(argv)
    version, n = build(args.out, args.source)
    print("kit %s: %d files -> %s" % (version, n, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
