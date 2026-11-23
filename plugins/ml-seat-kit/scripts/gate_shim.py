"""The send gate's fixed address. Step 3 of the launcher copies this file to <kit home>\\send_gate.py.

A seat's .claude/settings.json names that copy, not a file inside a kit version, so a new kit version never
leaves a seat's gate pointing at a folder that is gone. It runs the send gate of the kit named in kit.txt next to
it. When it cannot (kit.txt missing, unreadable, or naming a folder that is not there), it REFUSES the tool call:
exit 2 with a plain reason, the same as the gate itself refusing a send. A gate that cannot run never lets a send
through.

Standard library only. Changing this file changes every seat's gate, so keep it this small.
"""
import os
import re
import runpy
import sys


def _refuse(why):
    sys.stderr.write("Drafts only: the send gate could not run (%s), so it refused. Run \"Assistant.cmd selftest\" "
                     "once (it puts the kit back in place), then try again.\n" % why)
    return 2


def _gate():
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(here, "kit.txt"), encoding="ascii") as f:
            name = f.read().strip()
    except OSError:
        return None, "kit.txt is missing or could not be read"
    except ValueError:
        return None, "kit.txt could not be read"
    if not re.match(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,39}$", name) or name in (".", ".."):
        return None, "kit.txt does not name a kit folder"
    gate = os.path.join(here, "kits", name, "plugins", "ml-seat-kit", "scripts", "send_gate.py")
    if not os.path.isfile(gate):
        return None, "the kit named in kit.txt is not there"
    return gate, None


def main():
    try:
        gate, why = _gate()
    except BaseException as e:  # noqa: BLE001
        return _refuse(type(e).__name__)
    if not gate:
        return _refuse(why)
    sys.argv[0] = gate
    sys.path.insert(0, os.path.dirname(gate))
    try:
        runpy.run_path(gate, run_name="__main__")
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        sys.stderr.write("%s\n" % code)
        return 2
    except BaseException as e:  # noqa: BLE001
        return _refuse("the gate hit an error, %s" % type(e).__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
