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

Adopt mode puts the kit onto a folder the person ALREADY uses for an assistant, in place:

    python -I seat_setup.py adopt [--check | --undo] [--name <assistant name>] "<folder>"
                                  --claude <claude.exe> --kit-home <folder> [--seat-home <folder>] [--launcher <file>]

  --check  prints, file by file, what the real run would do; writes nothing in the folder and makes no plugin call
  --undo   puts back the files adopt changed (from its backup) and removes the files it made, if still unchanged

It keeps her folder, her name, her CLAUDE.md (one fenced hard-limits block appended) and her own launcher (a file
named assistant.cmd or assistant.ps1 is never created, replaced, renamed or deleted). Before any write it copies
every file it will change into <folder>\\.seat\\backup\\<time>\\ with a manifest of what it creates. No desktop
shortcut, no session at the end.
"""
import argparse
import codecs
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
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


def ensure_plugin(claude, kit_home, step=4):
    folder = str(pathlib.Path(kit_home) / "current")
    say("Step %d of 6: adding the kit to Claude ... " % step, end="")
    rc, out = _claude(claude, "plugin", "marketplace", "list", "--json")
    known = _json_list(out) if rc == 0 else None
    if known is None:
        raise Stop(step, "Claude could not list its plugin sources (%s)" % _last(out),
                   "close this window, open it again, and if it stops here again, send this line to whoever set up your assistant")
    names = {m.get("name"): m for m in known if isinstance(m, dict)}
    if MARKETPLACE in names:
        rc, out = _claude(claude, "plugin", "marketplace", "update", MARKETPLACE)
    else:
        rc, out = _claude(claude, "plugin", "marketplace", "add", folder, "--scope", "user")
    if rc != 0:
        raise Stop(step, "Claude did not accept the kit folder (%s)" % _last(out),
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
        raise Stop(step, "Claude did not install the assistant kit (%s)" % _last(out),
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


# ---------------------------------------------------------------- adopt: the kit onto a folder she already has

HARD_LIMITS = re.compile(r"<!-- seat-kit:hard-limits start.*?<!-- seat-kit:hard-limits end -->", re.S)
HARD_LIMITS_BEGIN = "<!-- seat-kit:hard-limits start"
PROTECTED = {"assistant.cmd", "assistant.ps1"}   # her own launcher: never created, replaced, renamed or deleted
BACKUPS = pathlib.PurePosixPath(".seat/backup")
MANIFEST = "manifest.json"
UNDONE = "undone.json"
MANIFEST_KIND = "seat-kit adopt"
KIT_DIRS = [".seat", ".seat/backup", ".claude", "state", ".cache"]   # made by adopt only when absent
MARKER_REL = seat_common.MARKER.as_posix()
SETTINGS_REL = ".claude/settings.json"


class _AdoptArgs(argparse.ArgumentParser):
    def error(self, message):
        raise Stop(4, "the adopt line was not understood (%s)" % message,
                   'type it as: Assistant.cmd adopt --check "<your folder>", then without --check to do it')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _file_sha(path):
    try:
        return _sha(pathlib.Path(path).read_bytes())
    except OSError:
        return None


def _win(rel):
    return str(pathlib.PureWindowsPath(rel))


def _guard(folder, rel):
    """Every adopt write and delete goes through here: a path inside the folder, never her launcher."""
    pure = pathlib.PurePosixPath(str(rel).replace("\\", "/"))
    if pure.is_absolute() or not pure.parts or ".." in pure.parts or ":" in str(pure):
        raise Stop(5, "the kit was about to touch a path outside your folder (%s), so it stopped" % rel,
                   "send this line to whoever set up your assistant")
    if any(part.lower() in PROTECTED for part in pure.parts):
        raise Stop(5, "the kit was about to touch your own launcher (%s), so it stopped" % rel,
                   "send this line to whoever set up your assistant")
    return pathlib.Path(folder).joinpath(*pure.parts)


def _write(folder, rel, data):
    path = _guard(folder, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".seat-kit-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _same(a, b):
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def _inside(child, parent):
    c, p = os.path.normcase(os.path.abspath(str(child))), os.path.normcase(os.path.abspath(str(parent)))
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def _resolved(p):
    try:
        return pathlib.Path(p).resolve()
    except (OSError, RuntimeError, ValueError):
        return pathlib.Path(os.path.abspath(str(p)))


def clean_folder_arg(folder):
    """A folder typed as "C:\\My Folder\\" reaches Python with a trailing quote; drop stray quotes and spaces."""
    return (folder or "").strip().strip('"').strip()


def adopt_refusal(folder, kit_home, seat_home):
    """None, or the plain reason this folder must not be adopted. Reads only. An adopted folder is not refused."""
    raw = pathlib.Path(folder)
    if not raw.is_dir():
        return "the folder %s does not exist" % folder if not raw.exists() else "%s is a file, not a folder" % folder
    r = _resolved(raw)
    if r.parent == r or str(r) == r.anchor:
        return "%s is the top of a drive, not an assistant's folder" % r
    for home in {os.environ.get("USERPROFILE") or "", str(pathlib.Path.home())}:
        if home and _same(r, _resolved(home)):
            return "%s is your whole user folder, not an assistant's folder" % r
    for win in {os.environ.get("SystemRoot") or "", os.environ.get("windir") or ""}:
        if win and _inside(r, _resolved(win)):
            return "%s is the Windows folder" % r
    kh = _resolved(kit_home)
    if _inside(r, kh) or _inside(kh, r):
        return "%s is the assistant kit's own folder" % r if _inside(r, kh) else \
            "%s holds the assistant kit's own folder" % r
    if seat_home and _same(r, _resolved(seat_home)):
        return "%s is the folder where the kit's launcher keeps its own assistants" % r
    if (r / seat_common.MARKER).is_file():
        if seat_common.load_config(r).get("adopted"):
            return None
        return "%s is already an assistant seat made by the kit's launcher, so there is nothing to adopt" % r
    outer = seat_common.find_seat(r.parent)
    if outer is not None:
        return "%s is inside another assistant seat (%s)" % (r, outer)
    return None


def is_store_python(exe):
    return "\\windowsapps\\" in os.path.normcase(str(exe or "")).replace("/", "\\")


def hard_limits_block():
    m = HARD_LIMITS.search((KIT / "templates" / "CLAUDE.md").read_text(encoding="utf-8"))
    if not m:
        raise Stop(4, "the kit's CLAUDE.md template has no hard-limits block", "send this line to whoever set up your assistant")
    return m.group(0)


def plan_claude_md(folder):
    """(action, new bytes or None, appended bytes). action: 'create', 'append' or 'already there'. Her bytes stay
    exactly as they are, byte-order mark and line ends included; the block takes her line ends."""
    path = pathlib.Path(folder) / "CLAUDE.md"
    block = hard_limits_block()
    if not path.exists():
        data = (block + "\n").encode("utf-8")
        return "create", data, data
    old = path.read_bytes()
    body = old[len(codecs.BOM_UTF8):] if old.startswith(codecs.BOM_UTF8) else old
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise Stop(4, "your CLAUDE.md is not saved as UTF-8 text, so the kit cannot add its block without risking "
                      "your file; nothing was changed", "send this line to whoever set up your assistant")
    if HARD_LIMITS_BEGIN in text:
        return "already there", None, b""
    nl = "\r\n" if "\r\n" in text else "\n"
    lead = "" if not text else (nl if text.endswith("\n") else nl + nl)
    add = (lead + block.replace("\n", nl) + nl).encode("utf-8")
    return "append", old + add, add


def read_folder_settings(folder):
    """(data, raw bytes or None). Stops on a file the kit cannot read, and on settings that switch hooks off."""
    path = pathlib.Path(folder) / ".claude" / "settings.json"
    raw = path.read_bytes() if path.exists() else None
    data = {}
    if raw is not None:
        try:
            data = json.loads(raw[len(codecs.BOM_UTF8):] if raw.startswith(codecs.BOM_UTF8) else raw)
            if not isinstance(data, dict):
                raise TypeError
            merge_settings(data, {})
        except (ValueError, TypeError, AttributeError):
            raise Stop(4, "the settings file in your folder (.claude\\settings.json) is not in a shape the kit can "
                          "read, so the send gate could not be put in place; nothing was changed",
                       "send this line to whoever set up your assistant")
    local = {}
    try:
        local = json.loads((pathlib.Path(folder) / ".claude" / "settings.local.json").read_bytes())
    except (OSError, ValueError):
        pass
    for name, d in ((".claude\\settings.json", data), (".claude\\settings.local.json", local)):
        if isinstance(d, dict) and d.get("disableAllHooks") is True:
            raise Stop(4, "%s in your folder switches all hooks off (disableAllHooks), so the send gate could not "
                          "run; nothing was changed" % name, "send this line to whoever set up your assistant")
    return data, raw


def merge_settings(data, gate):
    """(new settings, deny rules added, gate action 'add'|'replace'|'keep'). Her keys, rules and hooks stay as they
    are: deny rules are only added, and only the kit's own gate entry is replaced."""
    new = json.loads(json.dumps(data))
    perms = new.setdefault("permissions", {})
    if not isinstance(perms, dict):
        raise TypeError
    deny = perms.setdefault("deny", [])
    hooks = new.setdefault("hooks", {})
    if not isinstance(deny, list) or not isinstance(hooks, dict):
        raise TypeError
    pre = hooks.setdefault("PreToolUse", [])
    if not isinstance(pre, list):
        raise TypeError
    added = [d for d in DENY if d not in deny]
    gates = [e for e in pre if _is_gate(e)]
    action = "keep" if gates == [gate] else ("replace" if gates else "add")
    deny.extend(added)
    pre[:] = [e for e in pre if not _is_gate(e)] + [gate]
    return new, added, action


def settings_bytes(new, raw):
    text = json.dumps(new, indent=2, ensure_ascii=False) + "\n"
    if raw is not None and b"\r\n" in raw:
        text = text.replace("\n", "\r\n")
    bom = codecs.BOM_UTF8 if raw is not None and raw.startswith(codecs.BOM_UTF8) else b""
    return bom + text.encode("utf-8")


def gate_in_place(folder, gate):
    """True only when the folder's settings, read back from disk, hold the gate entry and every deny rule."""
    try:
        raw = (pathlib.Path(folder) / ".claude" / "settings.json").read_bytes()
        data = json.loads(raw[len(codecs.BOM_UTF8):] if raw.startswith(codecs.BOM_UTF8) else raw)
        deny = data["permissions"]["deny"]
        pre = data["hooks"]["PreToolUse"]
        return all(d in deny for d in DENY) and [e for e in pre if _is_gate(e)] == [gate]
    except (OSError, ValueError, KeyError, TypeError):
        return False


def her_launchers(folder):
    try:
        return sorted(p for p in pathlib.Path(folder).iterdir() if p.name.lower() in PROTECTED and p.is_file())
    except OSError:
        return []


def _launcher_line(launcher, *words):
    tail = " ".join(words)
    if launcher and launcher.lower().endswith(".ps1"):
        return '& "%s" %s' % (launcher, tail)
    if launcher:
        return '"%s" %s' % (launcher, tail)
    return "Assistant.cmd %s" % tail


def _new_backup_dir(folder):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    rel, n = BACKUPS / stamp, 2
    while (pathlib.Path(folder) / rel).exists():
        rel, n = BACKUPS / ("%s-%02d" % (stamp, n)), n + 1
    return rel


def adopt_name(args, folder, cfg):
    """The assistant's name: the marker's on a second adopt, --name, else one question (none in --check)."""
    if cfg.get("seat_name"):
        return cfg["seat_name"]
    default = clean_name(pathlib.Path(folder).name) or "Assistant"
    if args.name is not None:
        return clean_name(args.name) or default
    if args.check:
        return None
    try:
        answer = input('   A name for your assistant (Enter for "%s"): ' % default)
    except EOFError:
        say("")
        answer = ""
    return clean_name(answer) or default


GATE_WORDS = "send, reply, forward, respond, RSVP, event, invite, meeting or appointment"
PLUGIN_EFFECT = ("In your other Claude sessions this makes the kit's start skill (seat-start) visible. Nothing is gated "
                 "or blocked outside an adopted folder, and no allow rule or permission grant is written anywhere.")


def plan_adopt(folder, args):
    """Everything adopt would do, computed without writing anything."""
    cfg = seat_common.load_config(folder) if (folder / seat_common.MARKER).is_file() else {}
    again = bool(cfg.get("adopted"))
    gate = gate_hook(sys.executable, args.kit_home)
    data, raw = read_folder_settings(folder)
    new, added, gate_action = merge_settings(data, gate)
    settings_change = raw is None or bool(added) or gate_action != "keep"
    plan = {"again": again, "cfg": cfg, "gate": gate, "added": added, "gate_action": gate_action,
            "settings_raw": raw, "settings_new": settings_bytes(new, raw) if settings_change else None,
            "claude_md": ("skip", None, b""), "state": [], "dirs": [], "name": None}
    if again:                                     # a second adopt changes only an out-of-date gate or deny list
        return plan
    plan["claude_md"] = plan_claude_md(folder)
    plan["state"] = [(rel, (KIT / "templates" / tpl).read_bytes().replace(b"\r\n", b"\n"))
                     for rel, tpl in STATE_FILES.items() if not (folder / rel).exists()]
    plan["state"] += [(rel, b"") for rel in EMPTY_FILES if not (folder / rel).exists()]
    plan["dirs"] = [d for d in KIT_DIRS if not (folder / d).exists()]
    return plan


def _modified_and_created(folder, plan):
    """([(rel, new bytes)] for files that exist and change, [(rel, bytes)] for files made new)."""
    modified, created = [], []
    action, data, _ = plan["claude_md"]
    if action == "append":
        modified.append(("CLAUDE.md", data))
    elif action == "create":
        created.append(("CLAUDE.md", data))
    if plan["settings_new"] is not None:
        (modified if plan["settings_raw"] is not None else created).append((SETTINGS_REL, plan["settings_new"]))
    created += plan["state"]
    return modified, created


def print_check(folder, args, plan):
    w = lambda rel: str(folder / _win(rel))   # noqa: E731
    say("Check only: this is what adopting your folder would do. Nothing in your folder was written, and no plugin "
        "call was made.")
    say("")
    say("Your folder: %s" % folder)
    if plan["again"]:
        say("This folder is already adopted (its marker says so).")
        if plan["settings_new"] is None:
            say("A real run would change nothing in it: the send gate and every deny rule are already in place.")
        else:
            say("A real run would only bring the send gate up to date in %s, after copying it to %s\\<date-time>\\:"
                % (w(SETTINGS_REL), w(str(BACKUPS))))
            for d in plan["added"]:
                say("    deny rule: %s" % d)
            if plan["gate_action"] != "keep":
                say("    send gate hook: run with %s (the Python or the kit folder changed)" % sys.executable)
    else:
        modified, created = _modified_and_created(folder, plan)
        n = 1
        say("Assistant's name: %s (written only to the marker; nothing is renamed)" % (
            plan["name"] if plan["name"] else "the real run asks one question (or give --name), "
                                              "Enter keeps \"%s\"" % (clean_name(folder.name) or "Assistant")))
        say("")
        say("The real run would, in this order:")
        say("  %d. Make a backup folder first: %s\\<date-time>\\" % (n, w(str(BACKUPS))))
        if modified:
            say("     and copy into it, before changing them:")
            for rel, _ in modified:
                say("       %s" % w(rel))
        say("     It also holds a list (manifest.json) of every file the run creates, so undo knows what to remove.")
        n += 1
        say("  %d. Create these files:" % n)
        for rel, _ in created:
            say("       %s" % w(rel))
        say("       %s   (the marker: it makes this folder an assistant seat, so the send gate applies here)"
            % w(MARKER_REL))
        shown = [w(d) for d in plan["dirs"] if d != ".seat/backup"]
        if shown:
            say("     and these folders: %s" % ", ".join(shown))
        n += 1
        if plan["settings_new"] is None:
            say("  %d. %s: nothing to add, the send gate and every deny rule are already there." % (n, w(SETTINGS_REL)))
        else:
            say("  %d. Add to %s (nothing of yours is removed or changed):" % (n, w(SETTINGS_REL)))
            for d in plan["added"]:
                say("       deny rule: %s" % d)
            if plan["gate_action"] != "keep":
                say("       send gate hook (PreToolUse): for any tool whose name has a %s word, run" % GATE_WORDS)
                say('         "%s" -I "%s"' % (plan["gate"]["hooks"][0]["command"], plan["gate"]["hooks"][0]["args"][1]))
                say("         and if it cannot start, block the tool. It refuses sending only inside this folder.")
        n += 1
        action = plan["claude_md"][0]
        if action == "already there":
            say("  %d. %s: the kit's hard-limits block is already there, nothing added." % (n, w("CLAUDE.md")))
        else:
            say("  %d. %s: %s one block with the kit's hard limits, between these two lines:" % (
                n, w("CLAUDE.md"), "append" if action == "append" else "create it with"))
            block = hard_limits_block().splitlines()
            say("       %s" % block[0])
            say("       %s" % block[-1])
            if action == "append":
                say("     The rest of your CLAUDE.md stays byte for byte as it is.")
        n += 1
        say("  %d. Run the kit's self tests (they use temporary folders only)." % n)
    n = (n + 1) if not plan["again"] else 1
    say("%sRegister the kit plugin for your Windows user (claude plugin marketplace add, then claude plugin install "
        "%s, both --scope user; on a later run, update)." % ("  %d. " % n if not plan["again"] else "A real run would also ",
                                                              PLUGIN_ID))
    say("     " + PLUGIN_EFFECT)
    if not os.path.isfile(args.claude or ""):
        say("     Claude Code was not found where the kit looks (%s), so the real run would stop before changing "
            "anything." % (args.claude or "nothing given"))
    say("")
    launchers = her_launchers(folder)
    if launchers:
        say("Your own launcher stays exactly as it is: %s" % ", ".join(str(p) for p in launchers))
    say("Never done in adopt mode: a file named assistant.cmd or assistant.ps1 in your folder is never created, "
        "replaced, renamed or deleted; no desktop shortcut; no session opened; nothing renamed; nothing installed.")
    say("")
    kit_current = pathlib.Path(args.kit_home) / "current"
    if kit_current.is_dir():
        say("Outside your folder: to run this check, the launcher put the newest kit into %s (the kit's own folder; "
            "every run of the launcher does this). Nothing else was written: no plugin call, no Claude settings, "
            "no shortcut." % kit_current)
    say("")
    say("To do it:   %s" % _launcher_line(args.launcher, "adopt", '"%s"' % folder))
    say("To undo it: %s" % _launcher_line(args.launcher, "adopt", "--undo", '"%s"' % folder))


def how_to_start(folder, args):
    launchers = her_launchers(folder)
    if launchers:
        say("How to start your assistant: the same way as before, with your own launcher (%s)." % launchers[0])
    else:
        say("How to start your assistant: the same way as before, by opening Claude Code in %s." % folder)
    say("The send gate applies however the session is started, because it is in this folder's own settings "
        "(.claude\\settings.json) with the full path of Python (%s)." % sys.executable)
    say('In the session, say "Start my assistant" to run the kit\'s start skill.')
    say('Do not run Assistant.cmd without "adopt" for this folder: on its own it makes a separate, new assistant folder.')
    say("To undo: %s" % _launcher_line(args.launcher, "adopt", "--undo", '"%s"' % folder))


def adopt(args):
    """The real run, or --check. Returns the folder."""
    folder_arg = clean_folder_arg(args.folder)
    if not folder_arg:
        raise Stop(4, "no folder was given", 'type it as: Assistant.cmd adopt --check "<your folder>"')
    say("Step 4 of 6: checking your folder ... ", end="")
    why = adopt_refusal(folder_arg, args.kit_home, args.seat_home)
    if why:
        raise Stop(4, why + "; nothing was changed", "check the folder name, or send this line to whoever set up your assistant")
    folder = _resolved(folder_arg)
    if is_store_python(sys.executable):
        raise Stop(4, "the only Python found is the Microsoft Store one (%s); nothing was installed or changed"
                   % sys.executable, "ask IT to install Python 3.12 for your user from python.org, then run this again")
    plan = plan_adopt(folder, args)
    say("fine (%s)" % folder)
    if not plan["again"]:
        plan["name"] = adopt_name(args, folder, plan["cfg"])
    if args.check:
        print_check(folder, args, plan)
        return folder
    if not args.no_plugin and not os.path.isfile(args.claude or ""):
        raise Stop(4, "Claude Code was not found where the kit looks (%s); nothing was changed" % (args.claude or "-"),
                   "send this line to whoever set up your assistant")
    version = kit_version()
    if plan["cfg"].get("tests_passed_for") != version and not args.no_tests:
        ok, tail = run_self_tests()
        if not ok:
            raise Stop(4, "the kit's self tests failed (%s); nothing was changed" % tail,
                       "send this line to whoever set up your assistant")
        say("   The kit's self tests passed.")
    say("Step 5 of 6: putting the kit onto your folder ... ", end="")
    modified, created = _modified_and_created(folder, plan)
    if plan["again"] and not modified and not created:
        say("already adopted, nothing changed")
    else:
        bdir = _new_backup_dir(folder)
        marker = None
        if not plan["again"]:
            cfg = {"seat_name": plan["name"], "person_name": "", "leadership": [], "kit": PLUGIN,
                   "created": datetime.date.today().isoformat(), "backup_dir": None, "labels": {},
                   "python": sys.executable, "kit_version": version,
                   "adopted": {"date": datetime.date.today().isoformat(), "backup": str(bdir)}}
            if not args.no_tests:
                cfg["tests_passed_for"] = version
            marker = (json.dumps(cfg, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
        made_dirs = [d for d in plan["dirs"]]
        manifest = {
            "kind": MANIFEST_KIND, "kit_version": version, "made": datetime.datetime.now().isoformat(timespec="seconds"),
            "folder": str(folder), "complete": False,
            "modified": [{"path": rel, "backup": rel, "before_sha256": _file_sha(folder / rel), "written_sha256": _sha(data),
                          "appended": (plan["claude_md"][2].decode("utf-8") if rel == "CLAUDE.md" else None)}
                         for rel, data in modified],
            "created": [{"path": rel, "sha256": _sha(data)} for rel, data in created]
                       + ([{"path": MARKER_REL, "sha256": _sha(marker)}] if marker else []),
            "created_dirs": made_dirs,
        }
        for rel, _ in modified:                                   # 1. the backup, before any other write
            _guard(folder, rel)
            dest = _guard(folder, "%s/%s" % (bdir, rel))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(folder / rel), str(dest))
            if _file_sha(dest) != _file_sha(folder / rel):
                raise Stop(5, "the backup copy of %s did not match, so nothing was changed" % _win(rel),
                           "send this line to whoever set up your assistant")
        _write(folder, "%s/%s" % (bdir, MANIFEST), (json.dumps(manifest, indent=1) + "\n").encode("utf-8"))
        for d in made_dirs:                                       # 2. her files: added to, or made new
            _guard(folder, d).mkdir(parents=True, exist_ok=True)
        for rel, data in modified + created:
            _write(folder, rel, data)
        if marker:
            _write(folder, MARKER_REL, marker)                     # 3. the marker last: from here on it is a seat
        manifest["complete"] = True
        _write(folder, "%s/%s" % (bdir, MANIFEST), (json.dumps(manifest, indent=1) + "\n").encode("utf-8"))
        parts = []
        if plan["again"]:
            parts.append("already adopted; the send gate brought up to date")
        else:
            parts.append("%d new files" % len(manifest["created"]))
            parts.append({"append": "CLAUDE.md: hard-limits block appended", "create": "CLAUDE.md made",
                          "already there": "CLAUDE.md: hard-limits block already there"}[plan["claude_md"][0]])
        if plan["added"]:
            parts.append("%d deny rules added" % len(plan["added"]))
        if plan["gate_action"] != "keep":
            parts.append("send gate %s" % ("added" if plan["gate_action"] == "add" else "updated"))
        say("done (%s)" % "; ".join(parts))
        say("   Backup of what changed, with the list of what was made: %s" % (folder / _win(str(bdir))))
        for rel, _ in modified:
            say("     %s" % _win(rel))
    if not gate_in_place(folder, plan["gate"]) or not seat_common.load_config(folder).get("adopted"):
        raise Stop(5, "after writing, the send gate or a deny rule is not in your folder's settings, so your folder is "
                      "NOT adopted", "do not use it as an adopted assistant; run %s and send this line to whoever set "
                                     "up your assistant" % _launcher_line(args.launcher, "adopt", "--undo", '"%s"' % folder))
    say("   Checked: the send gate and all %d deny rules are in .claude\\settings.json" % len(DENY))
    if not args.no_plugin:
        try:
            ensure_plugin(args.claude, args.kit_home, step=6)
        except Stop as e:
            raise Stop(6, "%s. Your folder already has the send gate; only the start skill is missing"
                       % str(e).split(": ", 1)[1].split(". What to do", 1)[0],
                       "run the same adopt line again later; to take it all back: %s"
                       % _launcher_line(args.launcher, "adopt", "--undo", '"%s"' % folder))
    say("")
    say("Your folder is adopted. No session was opened.")
    how_to_start(folder, args)
    return folder


def _open_backups(folder):
    """[(backup folder Path, manifest)] of adopt runs not yet undone, newest first."""
    out = []
    try:
        dirs = sorted((pathlib.Path(folder) / BACKUPS).iterdir(), key=lambda p: p.name, reverse=True)
    except OSError:
        return out
    for d in dirs:
        try:
            man = json.loads((d / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if man.get("kind") == MANIFEST_KIND and not (d / UNDONE).exists():
            out.append((d, man))
    return out


def undo_adopt(args):
    folder_arg = clean_folder_arg(args.folder)
    if not folder_arg or not pathlib.Path(folder_arg).is_dir():
        raise Stop(4, "the folder %s does not exist" % (folder_arg or "(none given)"), "check the folder name")
    folder = _resolved(folder_arg)
    say("Undoing adopt in %s ..." % folder)
    backups = _open_backups(folder)
    cfg = seat_common.load_config(folder) if (folder / seat_common.MARKER).is_file() else {}
    if not backups:
        if cfg and not cfg.get("adopted"):
            raise Stop(4, "this folder is an assistant seat made by the kit's launcher, not by adopt, so there is "
                          "nothing to undo", "nothing; it was left as it is")
        say("Nothing to undo: this folder has no adopt backup that is still open. Nothing was changed.")
        return folder
    done, left = [], []
    for bdir, man in backups:
        for m in man.get("modified", []):
            rel = m["path"]
            path, saved = _guard(folder, rel), _guard(folder, "%s/%s" % (pathlib.PurePath(bdir).relative_to(folder).as_posix(), m["backup"]))
            now = _file_sha(path)
            back = "copy /y \"%s\" \"%s\"" % (saved, path)
            if now is not None and now == m.get("written_sha256") and _file_sha(saved) == m.get("before_sha256"):
                _write(folder, rel, saved.read_bytes())
                done.append("put back your %s from the backup" % _win(rel))
                continue
            add = (m.get("appended") or "").encode("utf-8")
            cur = path.read_bytes() if now is not None else b""
            if rel == "CLAUDE.md" and add and cur.count(add) == 1:
                _write(folder, rel, cur.replace(add, b"", 1))
                done.append("removed the kit's hard-limits block from your CLAUDE.md (your later edits kept)")
                continue
            left.append("%s changed since adopt, so it was left as it is. To put back the copy from before adopt: %s"
                        % (path, back) if now is not None else
                        "%s is gone, so nothing was put back. The copy from before adopt is %s" % (path, saved))
        for c in man.get("created", []):
            rel = c["path"]
            if rel == MARKER_REL:
                continue
            path = _guard(folder, rel)
            now = _file_sha(path)
            if now is None:
                continue
            if now == c.get("sha256"):
                path.unlink()
                done.append("removed %s (made by adopt, unchanged)" % _win(rel))
            else:
                left.append("%s has changed since adopt (you or your assistant added to it), so it was left in place"
                            % path)
    marker = folder / seat_common.MARKER
    if marker.is_file():
        if seat_common.load_config(folder).get("adopted"):
            newest = backups[0][0]
            shutil.copyfile(str(marker), str(newest / "seat.json.at-undo"))
            _guard(folder, MARKER_REL).unlink()
            done.append("removed the marker %s (a copy is kept in %s)" % (_win(MARKER_REL), newest))
        else:
            left.append("%s is not an adopt marker, so it was left in place" % marker)
    for bdir, man in backups:
        for d in sorted(man.get("created_dirs", []), key=len, reverse=True):
            if d in (".seat", ".seat/backup"):
                continue
            p = _guard(folder, d)
            try:
                p.rmdir()
                done.append("removed the empty folder %s" % _win(d))
            except OSError:
                if p.exists():
                    left.append("%s still holds files made while the assistant was used, so it was left in place" % p)
        (bdir / UNDONE).write_text(json.dumps({"undone": datetime.datetime.now().isoformat(timespec="seconds"),
                                               "done": done, "left": left}, indent=1) + "\n", encoding="utf-8")
    for line in done:
        say("   " + line)
    for line in left:
        say("   Left: " + line)
    say("Undo finished. Nothing else in your folder was touched; your own launcher was not touched.")
    say("The backups stay in %s, so nothing is lost; delete that folder by hand when you no longer want it."
        % (folder / _win(str(BACKUPS))))
    claude = args.claude if os.path.isfile(args.claude or "") else "claude"
    say("The kit plugin is still registered for your Windows user (it only adds the start skill and gates nothing "
        "outside an adopted folder). To remove it as well, run these two lines:")
    say('   "%s" plugin uninstall %s' % (claude, PLUGIN_ID))
    say('   "%s" plugin marketplace remove %s' % (claude, MARKETPLACE))
    return folder


def adopt_main(argv):
    ap = _AdoptArgs(prog="seat_setup.py adopt")
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--name", default=None)
    ap.add_argument("--claude", default="")
    ap.add_argument("--kit-home", required=True)
    ap.add_argument("--seat-home", default=str(pathlib.Path.home() / "Assistant"))
    ap.add_argument("--launcher", default="")
    ap.add_argument("--no-plugin", action="store_true")
    ap.add_argument("--no-tests", action="store_true")
    try:
        args = ap.parse_args(argv)
        if args.check and args.undo:
            raise Stop(4, "--check and --undo cannot be used together", "use one of them")
        if args.undo:
            undo_adopt(args)
        else:
            adopt(args)
    except Stop as e:
        say("")
        say(str(e))
        return 1
    except Exception as e:  # noqa: BLE001
        say("")
        say("Stopped: something unexpected went wrong (%s: %s). What to do: send this line to whoever set up your "
            "assistant; to take back what adopt did, run it again with --undo." % (type(e).__name__, str(e)[:160]))
        return 1
    return 0


def main(argv=None):
    seat_common.utf8_stdout()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "adopt":
        return adopt_main(argv[1:])
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
