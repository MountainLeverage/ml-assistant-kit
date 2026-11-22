"""Shared by the kit's scripts: where the seat folder is, and the seat's own settings.

The code runs from the plugin folder; the seat folder holds only the person's state. A script finds
its seat in ONE way, in this order:
  1. `--seat <folder>` on the command line (removed from argv before the script parses it),
  2. CLAUDE_PROJECT_DIR (set by Claude Code for hooks and, in recent versions, for tool commands),
  3. the current folder.
Each candidate is walked upward to the first folder holding the marker `.seat/seat.json`. No marker,
no seat: the script says so in one line and stops, so it never writes state into a random folder.

Standard library only; Python 3.9 or newer.
"""
import json
import os
import pathlib
import sys

MARKER = pathlib.Path(".seat") / "seat.json"
KIT_SCRIPTS = pathlib.Path(__file__).resolve().parent
KIT_ROOT = KIT_SCRIPTS.parent


class NotASeat(Exception):
    pass


def find_seat(start):
    """The nearest folder at or above `start` that holds the seat marker, or None."""
    try:
        p = pathlib.Path(start).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    for cand in [p] + list(p.parents):
        try:
            if (cand / MARKER).is_file():
                return cand
        except OSError:
            continue
    return None


def pop_seat_arg(argv):
    """(seat folder given with --seat or None, argv without it)."""
    out, seat, i = [], None, 0
    while i < len(argv):
        a = argv[i]
        if a == "--seat" and i + 1 < len(argv):
            seat, i = argv[i + 1], i + 2
            continue
        if a.startswith("--seat="):
            seat, i = a.split("=", 1)[1], i + 1
            continue
        out.append(a)
        i += 1
    return seat, out


def resolve_seat(argv=None):
    """(seat root Path, remaining argv). Raises NotASeat with a plain message."""
    argv = list(sys.argv[1:] if argv is None else argv)
    given, rest = pop_seat_arg(argv)
    candidates = [given] if given else [os.environ.get("CLAUDE_PROJECT_DIR"), os.getcwd()]
    for c in candidates:
        if c:
            root = find_seat(c)
            if root is not None:
                return root, rest
    where = given or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    raise NotASeat("NOT A SEAT FOLDER: %s has no %s; run this from your assistant's folder or pass --seat"
                   % (where, MARKER.as_posix()))


def load_config(root):
    try:
        return json.loads((pathlib.Path(root) / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(root, cfg):
    path = pathlib.Path(root) / MARKER
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def utf8_stdout():
    """A wake row or subject with an emoji must not crash print on a cp1252 console."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def py_cmd(script, *args):
    """The exact command line the assistant should run for a kit script (shown in tick output)."""
    parts = ['python', '-I', '"%s"' % (KIT_SCRIPTS / script)] + list(args)
    return " ".join(parts)
