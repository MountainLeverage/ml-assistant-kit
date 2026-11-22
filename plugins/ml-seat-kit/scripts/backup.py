"""Back up the seat's own state to a folder the person picked (a synced Drive folder is the usual choice).

    python -I backup.py [--seat <folder>]              -> writes <dest>/<seat name> backup/seat-state.zip and LAST_BACKUP.txt
    python -I backup.py [--seat <folder>] --set "<dest>" -> records the folder the person picked (it must exist), then backs up

What goes: state/ (ledger, open items, corrections, senders, decisions, wakes, loop state), .seat/seat.json, the
seat's CLAUDE.md and its .claude/settings.json and settings.local.json (the person's own allow rules, no secrets). What never
goes: login files, keys, chat transcripts, caches. The zip is written beside the old one and swapped in, so a failed
copy never leaves the folder with no backup.

Restore: unzip seat-state.zip into a new seat folder, then double-click the assistant launcher and sign in again.
Origin: a working seat's tools/backup.py, cut down to the seat's own files (no repo bundle, no config folder copy).
"""
import datetime
import os
import pathlib
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import seat_common  # noqa: E402

INCLUDE_DIRS = ["state"]
INCLUDE_FILES = [".seat/seat.json", "CLAUDE.md", ".claude/settings.json", ".claude/settings.local.json"]
NEVER = (".credentials.json", ".claude.json", ".env")


def files_to_back_up(root):
    root = pathlib.Path(root)
    out = []
    for d in INCLUDE_DIRS:
        for p in sorted((root / d).rglob("*")):
            if p.is_file() and not p.name.endswith(".tmp") and p.name not in NEVER:
                out.append(p)
    for rel in INCLUDE_FILES:
        p = root / rel
        if p.is_file():
            out.append(p)
    return out


def backup(root, dest, now=None):
    """(ok, message). Never raises for an unmounted or missing folder."""
    root = pathlib.Path(root)
    dest = pathlib.Path(dest)
    if not dest.is_dir():
        return False, "FAILED: %s not found (is Google Drive running, or was the folder moved?)" % dest
    cfg = seat_common.load_config(root)
    target = dest / ("%s backup" % (cfg.get("seat_name") or root.name))
    target.mkdir(exist_ok=True)
    fresh, final = target / "seat-state.zip.new", target / "seat-state.zip"
    files = files_to_back_up(root)
    with zipfile.ZipFile(fresh, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.relative_to(root).as_posix())
    os.replace(fresh, final)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    line = "%s | %d files | %s\n" % (ts, len(files), final.name)
    (target / "LAST_BACKUP.txt").write_text(line, encoding="utf-8")
    (dest / "LAST_BACKUP.txt").write_text(line, encoding="utf-8")   # the file tick.py checks
    return True, "OK %s %d files to %s" % (ts, len(files), final)


def main(argv=None):
    seat_common.utf8_stdout()
    try:
        root, rest = seat_common.resolve_seat(argv)
    except seat_common.NotASeat as e:
        print(e)
        return 2
    cfg = seat_common.load_config(root)
    if rest[:1] == ["--set"]:
        if len(rest) < 2 or not pathlib.Path(rest[1]).is_dir():
            print("FAILED: pick a folder that already exists (for example a folder in your Google Drive)")
            return 2
        cfg["backup_dir"] = str(pathlib.Path(rest[1]).resolve())
        seat_common.save_config(root, cfg)
        print("backup folder set: %s" % cfg["backup_dir"])
    if not cfg.get("backup_dir"):
        print("FAILED: no backup folder chosen yet; run with --set \"<folder>\"")
        return 2
    ok, msg = backup(root, cfg["backup_dir"])
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
