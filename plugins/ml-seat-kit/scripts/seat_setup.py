r"""Steps 4 and 5 of the assistant launcher: add the kit to Claude, then set up (or check) the seat folder.

Run by Assistant.cmd and Assistant.ps1 every time; running it again is safe (it is how the person starts each day).

    python -I seat_setup.py --claude <claude.exe> --kit-home <folder> [--seat-home <folder>] [--desktop <folder>]
                            [--ask] [--no-plugin] [--no-tests]

  --kit-home   the folder holding current\ (the expanded kit, registered as a local marketplace) and last-seat.txt
  --seat-home  where seat folders live (default %USERPROFILE%\Assistant); the seat is <seat-home>\<assistant name>
  --desktop    where the shortcut goes (default: the Windows Desktop folder, OneDrive or not)
  --ask        ask the three questions again (names and leadership); nothing else is reset
  SEAT_ANSWERS="<name>|<assistant name>|<leadership, comma separated>" answers the questions without typing (tests)

What it writes, and only there: the seat folder, <kit-home>\last-seat.txt, one desktop shortcut, and (through the
`claude plugin` commands only) the person's own Claude config. It never writes an allow rule or any permission
grant. It never overwrites the ledger, open items, corrections, senders, decisions or wakes.

Prints one plain line per step. On a failure it prints "Stopped at step N: ..." with what to do, and exits 1.
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import seat_common  # noqa: E402

KIT = seat_common.KIT_ROOT
MARKETPLACE = "seat-kit"
PLUGIN = "ml-seat-kit"
PLUGIN_ID = "%s@%s" % (PLUGIN, MARKETPLACE)
STATE_FILES = {   # seat path -> template name; written only when absent, never overwritten
    "state/open-items.md": "open-items.md",
    "state/corrections.md": "corrections.md",
    "state/senders.md": "senders.md",
    "state/decisions.md": "decisions.md",
}
EMPTY_FILES = ["state/ledger.jsonl", "state/wakes.jsonl"]
DENY = [   # second layer behind the send gate: the known connector send tools, refused even if the hook cannot run
    "mcp__claude_ai_Gmail__send_message",
    "mcp__claude_ai_Gmail__send_draft",
    "mcp__claude_ai_Gmail__reply",
    "mcp__claude_ai_Gmail__reply_all",
    "mcp__claude_ai_Gmail__forward",
    "mcp__claude_ai_Google_Calendar__respond_to_event",
    "mcp__claude_ai_Google_Calendar__update_event",
    "mcp__claude_ai_Google_Calendar__delete_event",
]
FIRST_PROMPT = "Start my assistant: run the seat-start skill."
# The send gate lives in the seat's own settings, not the plugin's hooks.json: only here can the hook name the absolute
# Python the launcher found, so a session started without the launcher (no Python on PATH) still runs it. Exec form
# (command + args) has no shell, so cmd, PowerShell and Git Bash all see the same argv, spaces included. onFailure
# "block": if that Python is gone, the tool call is blocked, not let through. Only seat sessions carry this hook.
GATE_MATCHER = ("mcp__.*([Ss]end|SEND|[Rr]eply|REPLY|[Ff]orward|FORWARD|[Rr]espond|RESPOND|[Rr]svp|RSVP|[Ee]vent|EVENT"
                "|[Ii]nvit|INVIT|[Mm]eeting|MEETING|[Aa]ppointment|APPOINTMENT).*")


def gate_hook(python, kit_home):
    script = pathlib.Path(kit_home).resolve() / "current" / "plugins" / PLUGIN / "scripts" / "send_gate.py"
    return {"matcher": GATE_MATCHER, "hooks": [{"type": "command", "command": os.path.abspath(python),
                                                "args": ["-I", str(script)], "timeout": 30, "onFailure": "block"}]}


def _is_gate(entry):
    return isinstance(entry, dict) and any(
        isinstance(h, dict) and isinstance(h.get("args"), list) and any(str(a).endswith("send_gate.py") for a in h["args"])
        for h in (entry.get("hooks") if isinstance(entry.get("hooks"), list) else []))


class Stop(Exception):
    def __init__(self, step, what, todo):
        Exception.__init__(self, "Stopped at step %d: %s. What to do: %s" % (step, what, todo))


def say(text, end="\n"):
    sys.stdout.write(text + end)
    sys.stdout.flush()


def kit_version():
    try:
        return json.loads((KIT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version", "?")
    except (OSError, ValueError):
        return "?"


# ---------------------------------------------------------------- step 4: the plugin

def _claude(claude, *args, timeout=180):
    try:
        r = subprocess.run([claude] + list(args), capture_output=True, text=True, errors="replace",
                           stdin=subprocess.DEVNULL, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timed out after %d s (it may be waiting for a sign-in or a question)" % timeout
    except OSError as e:
        return 127, str(e)


def _json_list(text):
    for line in reversed(text.strip().splitlines() or [""]):
        line = line.strip()
        if line.startswith("["):
            try:
                return json.loads(line)
            except ValueError:
                break
    try:
        start = text.index("[")
        return json.loads(text[start:])
    except ValueError:
        return None


def ensure_plugin(claude, kit_home):
    folder = str(pathlib.Path(kit_home) / "current")
    say("Step 4 of 6: adding the kit to Claude ... ", end="")
    rc, out = _claude(claude, "plugin", "marketplace", "list", "--json")
    known = _json_list(out) if rc == 0 else None
    if known is None:
        raise Stop(4, "Claude could not list its plugin sources (%s)" % _last(out),
                   "close this window, open it again, and if it stops here again, send this line to whoever set up your assistant")
    names = {m.get("name"): m for m in known if isinstance(m, dict)}
    if MARKETPLACE in names:
        rc, out = _claude(claude, "plugin", "marketplace", "update", MARKETPLACE)
    else:
        rc, out = _claude(claude, "plugin", "marketplace", "add", folder, "--scope", "user")
    if rc != 0:
        raise Stop(4, "Claude did not accept the kit folder (%s)" % _last(out),
                   "send this line to whoever set up your assistant")
    rc, out = _claude(claude, "plugin", "list", "--json")
    installed = _json_list(out) if rc == 0 else None
    ids = {p.get("id") for p in (installed or []) if isinstance(p, dict)}
    if PLUGIN_ID in ids:
        rc, out = _claude(claude, "plugin", "update", PLUGIN_ID)
        verb = "updated"
    else:
        rc, out = _claude(claude, "plugin", "install", PLUGIN_ID, "--scope", "user")
        verb = "installed"
    if rc != 0:
        raise Stop(4, "Claude did not install the assistant kit (%s)" % _last(out),
                   "send this line to whoever set up your assistant")
    say("done (%s, kit %s)" % (verb, kit_version()))


def _last(text):
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    return (lines[-1] if lines else "no message")[:200]


# ---------------------------------------------------------------- step 5: the seat folder

def clean_name(s):
    s = re.sub(r"[^A-Za-z0-9 \-]", "", (s or "")).strip()
    return re.sub(r"\s+", " ", s)[:40]


def find_seats(seat_home):
    home = pathlib.Path(seat_home)
    out = []
    try:
        for d in sorted(home.iterdir()):
            if d.is_dir() and (d / seat_common.MARKER).is_file():
                out.append(d)
    except OSError:
        pass
    return out


def pick_seat(seat_home, kit_home):
    """The existing seat to use, or None for a first run."""
    seats = find_seats(seat_home)
    if len(seats) == 1:
        return seats[0]
    if not seats:
        return None
    try:   # several: the one used last
        last = (pathlib.Path(kit_home) / "last-seat.txt").read_text(encoding="ascii", errors="replace").strip().split("|", 1)[1]
        for s in seats:
            if os.path.normcase(s.name) == os.path.normcase(last):
                return s
    except (OSError, IndexError):
        pass
    return seats[0]


def ask_questions():
    """(person, seat name, [leadership]). Every answer is optional."""
    canned = os.environ.get("SEAT_ANSWERS")
    if canned is not None:
        parts = (canned.split("|") + ["", "", ""])[:3]
    else:
        say("")
        say("   Three quick questions. Press Enter to skip any of them.")
        parts = []
        for q in ("   Your first name: ",
                  "   A name for your assistant (Enter for \"Assistant\"): ",
                  "   Your leadership, whose mail must always reach you (names, commas between): "):
            try:
                parts.append(input(q))
            except EOFError:
                parts.append("")
        say("   Thank you. Setting up ... ", end="")
    person = clean_name(parts[0])
    seat = clean_name(parts[1])
    leaders = [clean_name(x) for x in parts[2].split(",") if clean_name(x)]
    return person, seat, leaders


def render(template, cfg):
    person = cfg.get("person_name") or ""
    seat = cfg.get("seat_name") or "Assistant"
    leaders = cfg.get("leadership") or []
    subs = {
        "{{SEAT}}": seat,
        "{{PERSON_OR_YOUR}}": (person + "'s") if person else "your",
        "{{PERSON_OR_YOU}}": person or "you",
        "{{PERSON_SHORT}}": person or "You",
        "{{PERSON_LINE}}": person or "(not given)",
        "{{LEADERSHIP_LINE}}": ", ".join(leaders) if leaders else "(not given yet; ask the person once)",
    }
    for k, v in subs.items():
        template = template.replace(k, v)
    return template


ABOUT = re.compile(r"<!-- seat-kit:about start.*?<!-- seat-kit:about end -->", re.S)


def write_claude_md(seat, cfg, refresh):
    path = seat / "CLAUDE.md"
    fresh = render((KIT / "templates" / "CLAUDE.md").read_text(encoding="utf-8"), cfg)
    if not path.exists():
        path.write_text(fresh, encoding="utf-8", newline="\n")
        return "written"
    if refresh:
        old = path.read_text(encoding="utf-8")
        block = ABOUT.search(fresh)
        if block and ABOUT.search(old):
            path.write_text(ABOUT.sub(lambda _m: block.group(0), old, count=1), encoding="utf-8", newline="\n")
            return "updated"
    return "kept"


def write_settings(seat, gate):
    """The seat's project settings: deny rules and the one send gate hook. Adds missing deny entries and replaces only
    the kit's own gate entry; never removes her entries or allows anything. A file it cannot read stops setup: a seat
    without its gate must not open."""
    path = seat / ".claude" / "settings.json"
    path.parent.mkdir(exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        perms = data.setdefault("permissions", {})
        deny = perms.setdefault("deny", [])
        pre = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
        if not isinstance(deny, list) or not isinstance(pre, list):
            raise TypeError
    except (ValueError, TypeError, AttributeError):
        raise Stop(5, "the settings file in your assistant's folder (.claude\\settings.json) is not in a shape the kit "
                      "can read, so the send gate could not be put in place", "send this line to whoever set up your assistant")
    added = [d for d in DENY if d not in deny]
    hooks = [e for e in pre if not _is_gate(e)] + [gate]
    if not added and hooks == pre and path.exists():
        return "kept"
    deny.extend(added)
    pre[:] = hooks
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    return "written"


def seed_state(seat):
    made = []
    (seat / "state").mkdir(parents=True, exist_ok=True)
    (seat / ".cache").mkdir(exist_ok=True)
    for rel, tpl in STATE_FILES.items():
        p = seat / rel
        if not p.exists():
            p.write_text((KIT / "templates" / tpl).read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
            made.append(rel)
    for rel in EMPTY_FILES:
        p = seat / rel
        if not p.exists():
            p.write_text("", encoding="utf-8")
            made.append(rel)
    return made


def desktop_folder(override=None):
    """The person's Desktop (OneDrive-redirected or not); an empty override means the real one."""
    if override and override.strip():
        return pathlib.Path(override.strip())
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "[Environment]::GetFolderPath('Desktop')"],
                           capture_output=True, text=True, timeout=30)
        p = r.stdout.strip()
        return pathlib.Path(p) if p else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def make_shortcut(desktop, target, seat_name, workdir):
    """One .lnk to the launcher copy in the kit folder. Best effort: (made, message)."""
    if desktop is None or not desktop.is_dir():
        return False, "not made (no desktop folder found)"
    if not pathlib.Path(target).is_file():
        return False, "not made (no launcher copy in the kit folder yet)"
    lnk = desktop / ("%s.lnk" % seat_name)
    if lnk.exists():
        return True, "already there"
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:SEAT_LNK); "
          "$s.TargetPath = $env:SEAT_TARGET; $s.WorkingDirectory = $env:SEAT_WORKDIR; "
          "$s.Description = 'Start your assistant'; $s.Save()")
    env = dict(os.environ, SEAT_LNK=str(lnk), SEAT_TARGET=str(target), SEAT_WORKDIR=str(workdir))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=60, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, "not made (%s)" % type(e).__name__
    if r.returncode != 0 or not lnk.exists():
        return False, "not made (%s)" % _last(r.stderr)
    return True, "made"


def run_self_tests():
    tests = KIT / "tests"
    try:
        r = subprocess.run([sys.executable, "-I", "-m", "unittest", "discover", "-s", str(tests), "-t", str(KIT)],
                           capture_output=True, text=True, errors="replace", timeout=300, cwd=str(KIT))
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, type(e).__name__
    tail = _last(r.stderr)
    return r.returncode == 0, tail


def dry_tick(seat):
    try:
        r = subprocess.run([sys.executable, "-I", str(KIT / "scripts" / "tick.py"), "--seat", str(seat), "check"],
                           capture_output=True, text=True, errors="replace", timeout=180)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, type(e).__name__
    out = r.stdout.strip()
    return out.startswith(("FREE", "HELD", "UNKNOWN")), out


CHECKLIST = """
   What only you can do (your assistant walks you through each one when it opens):
     1. Sign in to Claude when the window asks (your own account).
     2. Say yes to trusting your assistant's folder.
     3. Connect Gmail, Google Calendar and Google Drive when your assistant shows you where.
     4. Say yes or no when your assistant asks to create its five mail labels.
     5. Pick a folder for the daily backup (a Google Drive folder is best).
     6. Answer each permission question Claude shows you. Never allow sending.
"""


def setup_seat(args):
    say("Step 5 of 6: setting up your assistant's folder ... ", end="")
    seat_home = pathlib.Path(args.seat_home)
    seat = pick_seat(seat_home, args.kit_home)
    first = seat is None
    cfg = seat_common.load_config(seat) if seat else {}
    refresh = False
    if first or args.ask:
        person, name, leaders = ask_questions()
        refresh = True
        name = name or cfg.get("seat_name") or "Assistant"
        if first:
            seat = seat_home / name
            n = 2
            while (seat / seat_common.MARKER).exists() or (seat.exists() and any(seat.iterdir())):
                seat, n = seat_home / ("%s %d" % (name, n)), n + 1
        cfg.update({"person_name": person or cfg.get("person_name") or "", "seat_name": name,
                    "leadership": leaders or cfg.get("leadership") or []})
    seat.mkdir(parents=True, exist_ok=True)
    cfg.setdefault("kit", PLUGIN)
    cfg.setdefault("created", datetime.date.today().isoformat())
    cfg.setdefault("backup_dir", None)
    cfg.setdefault("labels", {})
    cfg["python"] = sys.executable
    cfg["kit_version"] = kit_version()
    seat_common.save_config(seat, cfg)                 # the marker: from here on this folder is a seat
    made = seed_state(seat)
    claude_md = write_claude_md(seat, cfg, refresh)
    settings = write_settings(seat, gate_hook(sys.executable, args.kit_home))
    notes = []
    if cfg.get("tests_passed_for") != kit_version() and not args.no_tests:
        ok, tail = run_self_tests()
        if not ok:
            raise Stop(5, "the kit's self tests failed (%s)" % tail,
                       "do not use the assistant yet; send this line to whoever set up your assistant")
        cfg["tests_passed_for"] = kit_version()
        seat_common.save_config(seat, cfg)
        notes.append("self tests passed")
    ok, line = dry_tick(seat)
    if not ok:
        raise Stop(5, "the check loop did not answer (%s)" % (line or "no output"),
                   "send this line to whoever set up your assistant")
    made_lnk, lnk_msg = make_shortcut(desktop_folder(args.desktop), pathlib.Path(args.kit_home) / "Assistant.cmd",
                                      cfg["seat_name"], pathlib.Path(args.kit_home))
    # ASCII only (name and folder leaf), so cmd's code page cannot garble it; the launcher joins the leaf to seat-home.
    (pathlib.Path(args.kit_home) / "last-seat.txt").write_text("%s|%s\n" % (cfg["seat_name"], seat.name),
                                                               encoding="ascii", errors="replace")
    notes.append("desktop shortcut %s" % lnk_msg)
    if made:
        notes.append("%d new files" % len(made))
    notes += ["%s %s" % (k, v) for k, v in (("CLAUDE.md", claude_md), ("settings", settings)) if v != "kept"]
    say("done (%s; %s)" % (seat, "; ".join(notes)))
    if not cfg.get("first_run_done"):
        say(CHECKLIST.rstrip("\n"))
    return seat, cfg


def main(argv=None):
    seat_common.utf8_stdout()
    ap = argparse.ArgumentParser(prog="seat_setup.py")
    ap.add_argument("--claude", required=True)
    ap.add_argument("--kit-home", required=True)
    ap.add_argument("--seat-home", default=str(pathlib.Path.home() / "Assistant"))
    ap.add_argument("--desktop", nargs="?", const="", default=None)
    ap.add_argument("--ask", action="store_true")
    ap.add_argument("--no-plugin", action="store_true")
    ap.add_argument("--no-tests", action="store_true")
    args = ap.parse_args(argv)
    try:
        if not args.no_plugin:
            ensure_plugin(args.claude, args.kit_home)
        setup_seat(args)
    except Stop as e:
        say("")
        say(str(e))
        return 1
    except Exception as e:  # noqa: BLE001
        say("")
        say("Stopped at step 5: something unexpected went wrong (%s: %s). What to do: send this line to whoever "
            "set up your assistant." % (type(e).__name__, str(e)[:160]))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
