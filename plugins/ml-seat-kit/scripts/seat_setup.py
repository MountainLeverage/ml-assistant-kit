r"""Steps 4 and 5 of the assistant launcher: add the kit to Claude, then set up (or check) the seat folder.

Run by Assistant.cmd and Assistant.ps1 every time; running it again is safe (it is how the person starts each day).

    python -I seat_setup.py --claude <claude.exe> --kit-home <folder> [--seat-home <folder>] [--desktop <folder>]
                            [--ask] [--no-plugin] [--no-tests]

  --kit-home   the folder holding kits\<version>\ (each expanded kit; kit.txt names the one in use, see
               kit_place.py), send_gate.py (the gate's fixed address), market.txt and last-seat.txt
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

Self test mode only runs the kit's self tests and says what it found; it changes nothing but one report file:

    python -I seat_setup.py selftest --kit-home <folder>

Every self test run keeps unittest's full output in <kit-home>\\selftest-last.txt (overwritten each run), and a
failure names the tests that did not pass and that file. Since 0.1.6 the summary also names the folders the tests'
before-and-after comparisons ignored, as the tests themselves reported them (parse_noise). Since 0.1.7 the tests run
through the kit's own runner (selftest_runner.py): a progress line about every 15 seconds, each test's seconds in
the file with the slowest tests and the total by test file at its end, and a limit per step (PER_TEST_SECONDS, or
what a test asks for) with a three-hour backstop in all (TOTAL_SECONDS); a run that passes one is stopped and the
stop line names the step. The limits are there to catch a hang, not to judge speed; after ten minutes the console
says once that a slow run is expected and that a stuck check stops the run by itself (SLOW_NOTE). The kit's tests of
its own runner that use real waits run only when the kit is built; the summary counts them as "Not run here".

Remove mode takes back a first install (never an adopted folder); see seat_remove.py:

    python -I seat_setup.py remove [--check] [--delete-my-notes] --claude <claude.exe> --kit-home <folder>
                                   [--seat-home <folder>] [--desktop <folder>] [--launcher <file>]
"""
import argparse
import ast
import codecs
import datetime
import hashlib
import json
import os
import pathlib
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import seat_common  # noqa: E402
import kit_place  # noqa: E402

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


# ---------------------------------------------------------------- one spelling per folder
# A folder can arrive in its 8.3 short form (a profile folder shown as ABCDEF~1.XYZ: Windows gives TEMP like that
# for a long user name with a dot), in its long form, in another case, with a trailing slash, or through a junction.
# The kit PRINTS the long form (what the person sees in Explorer) and never compares two spellings as strings: it
# asks the file system (samefile) or compares canon() forms.

def long_path(p):
    """The long spelling of an existing path (each 8.3 short part expanded, as Explorer names it; junctions are NOT
    followed), or the absolute path as given when it does not exist or this is not Windows."""
    s = os.path.abspath(str(p))
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(32768)
            n = ctypes.windll.kernel32.GetLongPathNameW(s, buf, 32768)
            if 0 < n < 32768:
                return buf.value
        except Exception:  # noqa: BLE001
            pass
    return s


def canon(p):
    """The one spelling used to compare folders: realpath (short names expanded, junctions followed, also for the
    part that exists of a path that does not) in normal case, with no trailing slash."""
    try:
        return os.path.normcase(os.path.realpath(str(p)))
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(str(p)))


def versioned(kit_home, kit=None):
    """True when this code runs from <kit home>\\kits\\<version> (the layout of 0.1.4 and later). False when it runs
    from the old <kit home>\\current (an older launcher brought it) or from anywhere else (tests, a checkout)."""
    root = pathlib.Path(kit or KIT).parent.parent
    try:
        return _same(root.parent, pathlib.Path(kit_home) / kit_place.KITS)
    except OSError:
        return False


def gate_script(kit_home, kit=None):
    """The file a seat's gate hook runs. In the versioned layout: <kit home>\\send_gate.py, a fixed address that
    runs the gate of the kit named in kit.txt and refuses when it cannot. Otherwise the old address inside
    <kit home>\\current, exactly as 0.1.3 and older wrote it."""
    kh = pathlib.Path(kit_home).resolve()
    if versioned(kit_home, kit):
        return kh / kit_place.SHIM
    return kh / "current" / "plugins" / PLUGIN / "scripts" / "send_gate.py"


def gate_hook(python, kit_home, kit=None):
    script = gate_script(kit_home, kit)
    return {"matcher": GATE_MATCHER, "hooks": [{"type": "command", "command": long_path(python),
                                                "args": ["-I", str(script)], "timeout": 30, "onFailure": "block"}]}


def ensure_gate_file(kit_home, step, kit=None):
    """In the versioned layout, make sure <kit home>\\send_gate.py is there and is this kit's gate_shim.py before a
    hook is written that names it. Replaced only when different; one in use is kept (any copy reads kit.txt)."""
    if not versioned(kit_home, kit):
        return
    shim = pathlib.Path(kit_home) / kit_place.SHIM
    data = (pathlib.Path(kit or KIT) / "scripts" / "gate_shim.py").read_bytes()
    try:
        if not shim.is_file() or shim.read_bytes() != data:
            kit_place.write_file(shim, data)
    except OSError as e:
        if not shim.is_file():
            raise Stop(step, "the send gate file %s could not be written (%s); nothing was changed"
                       % (shim, e.strerror or type(e).__name__), "send this line to whoever set up your assistant")


def _is_gate(entry):
    return isinstance(entry, dict) and any(
        isinstance(h, dict) and isinstance(h.get("args"), list) and any(str(a).endswith("send_gate.py") for a in h["args"])
        for h in (entry.get("hooks") if isinstance(entry.get("hooks"), list) else []))


class Stop(Exception):
    def __init__(self, step, what, todo):
        Exception.__init__(self, "Stopped at step %d: %s. What to do: %s" % (step, what, todo))


_LINE = {"open": "", "broken": ""}   # the step line still open (said with end=""), and the head of one progress broke


def say(text, end="\n"):
    """Print and flush. When progress lines broke an open step line ("Step 4 of 6: ... " then progress), the words
    that would have ended that line get its step head again, so the result is on its own clear line:
    "Step 4 of 6: all passed"."""
    if _LINE["broken"]:
        if text:
            text = _LINE["broken"] + text
        _LINE["broken"] = ""
    out = text + end
    sys.stdout.write(out)
    sys.stdout.flush()
    _LINE["open"] = out.rsplit("\n", 1)[1] if "\n" in out else _LINE["open"] + out


def say_progress(line):
    """One progress line of the self tests, on a line of its own: an open step line is ended first."""
    if _LINE["open"]:
        m = re.match(r"Step \d+ of \d+: ", _LINE["open"])
        _LINE["broken"] = m.group(0) if m else "   "
        sys.stdout.write("\n")
        _LINE["open"] = ""
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def kit_version(kit=None):
    try:
        return json.loads((pathlib.Path(kit or KIT) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version", "?")
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


def market_folder(kit_home, kit=None):
    """The folder registered with Claude Code as the plugin source "seat-kit": the kit folder this code runs from
    in the versioned layout, else <kit home>\\current as before."""
    if versioned(kit_home, kit):
        return str(long_path(pathlib.Path(kit or KIT).parent.parent))
    return str(pathlib.Path(kit_home) / "current")


def registered_folder(kit_home):
    """The folder "seat-kit" was last registered at: market.txt, written after each registration; without it, the
    old <kit home>\\current (every kit before 0.1.4 registered that folder)."""
    try:
        text = (pathlib.Path(kit_home) / kit_place.MARKET).read_text(encoding="utf-8").strip()
        if text:
            return text
    except (OSError, UnicodeDecodeError):
        pass
    return str(pathlib.Path(kit_home) / "current")


def ensure_plugin(claude, kit_home, step=4, kit=None):
    folder = market_folder(kit_home, kit)
    say("Step %d of 6: adding the kit to Claude ... " % step, end="")
    rc, out = _claude(claude, "plugin", "marketplace", "list", "--json")
    known = _json_list(out) if rc == 0 else None
    if known is None:
        raise Stop(step, "Claude could not list its plugin sources (%s)" % _last(out),
                   "close this window, open it again, and if it stops here again, send this line to whoever set up your assistant")
    names = {m.get("name"): m for m in known if isinstance(m, dict)}
    if MARKETPLACE in names and _same(registered_folder(kit_home), folder):
        rc, out = _claude(claude, "plugin", "marketplace", "update", MARKETPLACE)
    else:
        if MARKETPLACE in names:    # registered at another kit folder (an older version, or the old current)
            rc, out = _claude(claude, "plugin", "marketplace", "remove", MARKETPLACE)
            if rc != 0:
                raise Stop(step, "Claude did not let go of the older kit folder (%s)" % _last(out),
                           "send this line to whoever set up your assistant")
        rc, out = _claude(claude, "plugin", "marketplace", "add", folder, "--scope", "user")
    if rc != 0:
        raise Stop(step, "Claude did not accept the kit folder (%s)" % _last(out),
                   "send this line to whoever set up your assistant")
    try:
        kit_place.write_file(pathlib.Path(kit_home) / kit_place.MARKET, (folder + "\n").encode("utf-8"))
    except OSError:
        pass        # without the record the next run registers the folder again; nothing breaks
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


REMOVED_NAME = re.compile(r"\.removed-\d{8}-\d{6}(-\d+)?$", re.I)   # a folder remove moved aside: never a seat again


def find_seats(seat_home):
    home = pathlib.Path(seat_home)
    out = []
    try:
        for d in sorted(home.iterdir()):
            if d.is_dir() and not REMOVED_NAME.search(d.name) and (d / seat_common.MARKER).is_file():
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


# ---------------------------------------------------------------- the self tests

SELFTEST_FILE = "selftest-last.txt"
_RULE_EQ, _RULE_DASH = "=" * 20, "-" * 20
_TEST_ID = re.compile(r"^(\S+) \(([^()]*)\)(.*)$")


def short_test_name(head):
    """'test_x (tests.test_mod.Klass.test_x) [cmd]' -> 'Klass.test_x [cmd]'. Python 3.9 and 3.10 leave the method off
    the part in brackets; a module that did not load shows as unittest's _FailedTest."""
    m = _TEST_ID.match(head.strip())
    if not m:
        return head.strip()[:120]
    method, where, rest = m.groups()
    parts = [p for p in where.split(".") if p]
    if parts and parts[-1] == method:
        parts = parts[:-1]
    if "_FailedTest" in parts:
        return "%s (did not load)" % method
    owner = parts[-1] if parts else ""
    return ("%s.%s" % (owner, method) if owner and owner != method else method) + rest.rstrip()


def parse_unittest(text):
    """What unittest itself printed: the FAIL and ERROR blocks (name and the one line saying why), the skipped tests
    (from -v lines), 'Ran N tests' and the last OK or FAILED line. Nothing is guessed: a count unittest did not print
    stays None."""
    lines = (text or "").splitlines()
    failed, skips = [], []
    i = 0
    while i < len(lines):
        head = re.match(r"(FAIL|ERROR): (.*)$", lines[i + 1]) if lines[i].startswith(_RULE_EQ) and i + 1 < len(lines) else None
        if not head:
            i += 1
            continue
        j = i + 2
        while j < len(lines) and not lines[j].startswith(_RULE_DASH):   # a docstring line may follow the name
            j += 1
        k, reason = j + 1, ""
        while k < len(lines) and not lines[k].startswith((_RULE_EQ, _RULE_DASH)):
            ln = lines[k]
            if not reason and ln.strip() and not ln[0].isspace() and not ln.startswith("Traceback"):
                reason = ln.strip()[:200]
            k += 1
        failed.append((head.group(1), short_test_name(head.group(2)), reason))
        i = k
    for n, ln in enumerate(lines):
        # unittest prints the reason's repr: in double quotes when it holds an apostrophe ("this PC's ...", 0.1.7),
        # in single quotes with \' when it holds both kinds; read back exactly with literal_eval
        m = re.search(r" \.\.\. skipped ('.*'|\".*\")$", ln)
        if not m:
            continue
        own = ln[:m.start()].strip()   # a sub-case's line is indented (Python 3.11 and newer)
        if not _TEST_ID.match(own) and n and _TEST_ID.match(lines[n - 1].strip()):   # a docstring line sat between
            own = lines[n - 1].strip()
        try:
            reason = ast.literal_eval(m.group(1))
            reason = reason if isinstance(reason, str) else m.group(1)[1:-1]
        except (ValueError, SyntaxError):
            reason = m.group(1)[1:-1]
        skips.append((short_test_name(own), reason))
    ran = re.findall(r"^Ran (\d+) tests? in ", text or "", re.M)
    summary = [ln.strip() for ln in lines if re.match(r"^(OK|FAILED)( \(.*\))?\s*$", ln) or ln.strip() == "NO TESTS RAN"]
    counts = dict(re.findall(r"(\w+)=(\d+)", summary[-1])) if summary else {}
    return {"failed": failed, "skips": skips, "ran": int(ran[-1]) if ran else None,
            "summary": summary[-1] if summary else None,
            "failures": int(counts.get("failures", 0)), "errors": int(counts.get("errors", 0)),
            "skipped": int(counts.get("skipped", 0)), "noise": parse_noise(text)}


# The one line the kit's tests print (tests/_helpers.py powershell_noise): the AppData folders the tests' PowerShell
# probe made in a scratch profile on this PC, which their before-and-after folder comparisons leave out (0.1.6).
NOISE_MARKER = "SEAT-KIT-TEST-NOISE:"
_NOISE_PATH = re.compile(r'^AppData/(Local|Roaming)/([^/\\:*?"<>|]+)$')
POWERSHELL_OWN = "AppData/Local/Microsoft"


def parse_noise(text):
    """The folders the tests' comparisons ignored, from their marker line: a list (empty when nothing was found or
    PowerShell could not run), or None when the tests printed no such line (an older kit, or a run cut short). Only
    top-level AppData\\Local and AppData\\Roaming names are taken, never the kit's own seat-kit. The FIRST valid
    marker line is the main run's; a later one (a child suite's, quoted in a failure's text) never replaces it."""
    for ln in (text or "").splitlines():
        if not ln.startswith(NOISE_MARKER):
            continue
        try:
            names = json.loads(ln[len(NOISE_MARKER):])
        except ValueError:
            continue
        if isinstance(names, list):
            return [n for n in names if isinstance(n, str) and _NOISE_PATH.match(n)
                    and _NOISE_PATH.match(n).group(2).lower() != "seat-kit"]
    return None


def noise_lines(noise):
    """The self test summary's lines on what the comparisons ignored, printed every run, pass or fail."""
    if noise is None:
        return ["Ignored:      not reported (the tests printed no list of ignored folders)"]
    if not noise:
        return ["Ignored:      nothing"]
    lines = ["Ignored:      %s (folders other programs make whenever PowerShell starts)"
             % ", ".join(n.replace("/", "\\") for n in noise)]
    others = []
    for n in noise:
        name = n.rsplit("/", 1)[1]
        if n != POWERSHELL_OWN and name not in others:
            others.append(name)
    if others:
        lines.append("Also seen and ignored: %s" % ", ".join(others))
    return lines


FIXTURES = ("setUpClass", "tearDownClass", "setUpModule", "tearDownModule")


def _test_of(name):
    """'Klass.test_x [cmd]' -> 'Klass.test_x' (a test with sub-cases is one test)."""
    return name.split(" ")[0]


def _is_fixture(name):
    """True for unittest's class and module set up and tear down, which are not tests and are not in 'Ran N'."""
    return _test_of(name).rsplit(".", 1)[-1] in FIXTURES


def tests_line(res):
    """'Tests: N ran, P passed, F failed, S skipped', where F and S count distinct tests and P is the rest, so
    N = P + F + S. A failure or error in a class or module set up or tear down is not a test: it is not taken off
    'passed' and is named in words after the counts (the run still fails). When sub-cases make unittest's own counts
    larger, those are added in words. When the counts still cannot add up, unittest's own counts are printed as
    unittest printed them, and the line says so."""
    if res["ran"] is None:
        return "Tests:        no count from unittest (%s)" % (res["summary"] or "no output")
    names = failed_names(res)
    fixture_bad = [n for n in names if _is_fixture(n)]
    failed_tests = []
    for n in names:
        if not _is_fixture(n) and _test_of(n) not in failed_tests:
            failed_tests.append(_test_of(n))
    skip_names = [n for n, _ in res.get("skips", [])]
    fixture_skips = []
    for n in skip_names:
        if _is_fixture(n) and n not in fixture_skips:
            fixture_skips.append(n)
    skipped_tests = {_test_of(n) for n in skip_names if not _is_fixture(n)} - set(failed_tests)
    sub_skips = len([n for n in skip_names if not _is_fixture(n) and _test_of(n) in skipped_tests])
    skipped = len(skipped_tests)
    if not skip_names and res["skipped"]:   # no -v lines to name them: unittest's own count
        skipped = sub_skips = res["skipped"]
    failed = len(failed_tests)
    passed = res["ran"] - failed - skipped
    if passed < 0:
        return ("Tests:        %d ran; unittest's own counts: %s (these do not add up per test, so they are shown as "
                "unittest printed them)" % (res["ran"], res["summary"] or "none printed"))
    bad_raw = res["failures"] + res["errors"] - len(fixture_bad)
    extra_failed = " (%d failures counting sub-cases)" % bad_raw if bad_raw > failed else ""
    extra_skipped = " (%d skips counting sub-cases)" % sub_skips if sub_skips > skipped else ""
    line = "Tests:        %d ran, %d passed, %d failed%s, %d skipped%s" % (res["ran"], passed, failed, extra_failed,
                                                                         skipped, extra_skipped)
    if fixture_bad:
        line += "; %d %s outside any test (%s)" % (len(fixture_bad), "error" if len(fixture_bad) == 1 else "errors",
                                                   ", ".join(fixture_bad))
    if fixture_skips:
        line += "; %d %s outside any test (%s)" % (len(fixture_skips), "skip" if len(fixture_skips) == 1 else "skips",
                                                   ", ".join(fixture_skips))
    return line


def failed_names(res):
    out = []
    for _, name, _ in res.get("failed", []):
        if name not in out:
            out.append(name)
    return out


def windows_version():
    try:
        parts = [platform.system(), platform.release(), platform.version()]
        edition = platform.win32_edition() if hasattr(platform, "win32_edition") else None
        return " ".join(p for p in parts if p) + (" (%s)" % edition if edition else "")
    except Exception:  # noqa: BLE001
        return "unknown"


# The self tests run through the kit's own runner (scripts/selftest_runner.py, 0.1.7), read as they come, so the
# console shows progress, each test's seconds are kept, and a single stuck test is stopped and named.
RUNNER = pathlib.Path(__file__).resolve().parent / "selftest_runner.py"
# The limits exist to catch a HANG, not to judge speed: the progress lines already tell the person the run is alive.
PER_TEST_SECONDS = 300       # one test, from its START line; a class tear-down and set-up, from its SETUP line. A
                             # test method that starts child processes asks for more with selftest_seconds = N, at
                             # least the sum of the subprocess time limits it uses; the release build measures
                             # that sum per test (selftest_runner.py WAITS lines) and tools/make_kit.py refuses a
                             # build in which any test's sum is over its limit
TOTAL_SECONDS = 10800        # the whole run, three hours: a BACKSTOP only. Every hang is caught first by the limit
                             # of the step it is in (a test, a set-up or tear-down, finding the tests, the end of
                             # the run); this one is for a run that keeps moving and never ends. make_kit refuses a
                             # build whose whole run, times 4, would not fit in it
BEFORE_FIRST_SECONDS = 300   # from the start until the runner has found and imported every test
PROGRESS_EVERY = 15          # seconds between progress lines on the console
SLOW_TEST = 30               # a test running longer than this is named on the next progress line
SLOW_NOTE_SECONDS = 600      # after ten minutes, once per run, a line that says a slow run is expected (SLOW_NOTE)
SLOW_NOTE = ("   this PC is running the checks slowly, which is expected; if one check is stuck, the run stops by "
             "itself and names it")
PIPE_GRACE = 5.0             # seconds to keep reading the pipes after the runner has ended (or was stopped)
_POLL = 0.25                 # seconds between looks at the clock while no line comes
_MARK = re.compile(r"^SEAT-KIT-TEST-(JOB|NOTRUN|BUILDONLY|FOUND|SETUP|START|DONE|WAITS|TEARDOWN|END)(?: (.*))?$")
_WAITS = re.compile(r"^(\d+) (\d+) (\d+)$")
NOT_RUN_HERE = "%d checks of the kit's own test runner, which are run when the kit is built"
_AT = re.compile(r"^(\d+|\?)/(\d+)(?: limit=(\d+))? (\S+)(?: after(?: limit=(\d+))? (\S+))?$")
_DONE = re.compile(r"^(\d+|\?)/(\d+) (ok|FAIL|ERROR|skipped|expected failure|unexpected success) "
                   r"(\d+(?:\.\d+)?)s (\S+)$")


def duration(seconds):
    """'45 s', '3 min 10 s'."""
    s = int(max(0, seconds))
    return "%d s" % s if s < 60 else "%d min %d s" % (s // 60, s % 60)


def _total_words(seconds):
    return "%d minutes" % (seconds // 60) if seconds >= 120 and seconds % 60 == 0 else "%d seconds" % seconds


def _short_id(test_id):
    """'tests.test_x.Klass.test_y' -> 'Klass.test_y' (a class: its own name)."""
    parts = test_id.split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 else test_id


class SuiteWatch:
    """Reads the runner's SEAT-KIT-TEST lines (with the time each came) and, on each tick, says whether a progress
    line is due and whether a limit was passed. No clock of its own: every call is given `now`."""

    def __init__(self, now, total=TOTAL_SECONDS, per_test=PER_TEST_SECONDS, before_first=BEFORE_FIRST_SECONDS,
                 every=PROGRESS_EVERY, slow=SLOW_TEST, slow_note=SLOW_NOTE_SECONDS):
        self.t0, self.total, self.per_test, self.every, self.slow = now, total, per_test, every, slow
        self.slow_note, self.noted = slow_note, False
        self.count = None          # tests found
        self.times = []            # (id, seconds, outcome) from DONE lines, in order
        self.limits = {}           # id -> the limit that test ran under (from its START line)
        self.job = None
        self.ended = False
        self.not_run = []          # ids the runner left out here (build-only tests, NOTRUN lines)
        self.build_only = []       # ids of the build-only tests that run in a release build (BUILDONLY lines)
        self.waits = []            # (what, seconds of subprocess time limits used, its limit, calls without one)
        self.phases = []           # (kind, name, seconds, limit) of each finished step that is not a test
        self.phase = {"kind": "find", "since": now, "limit": before_first, "n": None, "name": None}
        self.next_progress = now + every
        self.stopped = None

    def _set(self, phase, now):
        """Move to a new step; the one that ends is kept in `phases` when it is not a test (a test's time is its
        DONE line's)."""
        p = self.phase
        if p["kind"] != "test":
            what = p.get("name") or ""
            if p.get("prev"):
                what = "%s after %s" % (what, p["prev"])
            self.phases.append((p["kind"], what, now - p["since"], p["limit"]))
        self.phase = phase

    def finish(self, now):
        """The runner has ended: the last step (normally the end of the run) is over."""
        if not self.stopped:
            self._set({"kind": "over", "since": now, "limit": self.per_test, "n": None, "name": None}, now)

    def line(self, text, now):
        """One line of the runner's stderr. Lines after END (unittest's summary, which may quote a child suite's own
        marker lines in a failure's text) are never read as marks."""
        m = _MARK.match(text.rstrip("\r\n"))
        if not m or self.ended:
            return
        kind, rest = m.group(1), m.group(2) or ""
        if kind == "JOB":
            self.job = rest
        elif kind == "NOTRUN":
            self.not_run.append(rest)
        elif kind == "BUILDONLY":
            self.build_only.append(rest)
        elif kind == "WAITS":
            w = _WAITS.match(rest)
            p = self.phase
            if w and p["kind"] == "after" and self.times:      # right after a test's DONE line: that test's
                tid = self.times[-1][0]
                self.waits.append((tid, int(w.group(1)), self.limits.get(tid, self.per_test), int(w.group(3))))
            elif w and p["kind"] in ("setup", "teardown"):
                what = ("the tear-down of %s and the set-up of %s" % (p["prev"], p["name"]) if p.get("prev") else
                        "the %s of %s" % ("set-up" if p["kind"] == "setup" else "tear-down", p["name"]))
                self.waits.append((what, int(w.group(1)), p["limit"], int(w.group(3))))
        elif kind == "FOUND":
            self.count = int(rest) if rest.isdigit() else None
            self._set({"kind": "after", "since": now, "limit": self.per_test, "n": 0, "name": None}, now)
        elif kind in ("SETUP", "START", "TEARDOWN"):
            a = _AT.match(rest)
            if a:
                n = int(a.group(1)) if a.group(1).isdigit() else None
                self.count = self.count or int(a.group(2))
                limit = int(a.group(3)) if a.group(3) else self.per_test
                prev = a.group(6)
                if prev:     # A's tear-down, then B's set-up, back to back: the larger of the two classes' limits
                    limit = max(limit, int(a.group(5)) if a.group(5) else self.per_test)
                self._set({"kind": {"START": "test", "SETUP": "setup", "TEARDOWN": "teardown"}[kind], "since": now,
                           "n": n, "name": a.group(4), "limit": limit, "prev": prev}, now)
                if kind == "START":
                    self.limits[a.group(4)] = limit
        elif kind == "DONE":
            d = _DONE.match(rest)
            if d:
                self.times.append((d.group(5), float(d.group(4)), d.group(3)))
                n = int(d.group(1)) if d.group(1).isdigit() else len(self.times)
                self._set({"kind": "after", "since": now, "limit": self.per_test, "n": n, "name": None}, now)
        elif kind == "END":
            self.ended = True
            self._set({"kind": "end", "since": now, "limit": self.per_test, "n": None, "name": None}, now)

    def _of(self, n):
        return "test %s of %s" % (n if n is not None else "?", self.count if self.count is not None else "?")

    def _where(self):
        """(what is running, in words for a progress line, and how long the current part has run is known)."""
        p = self.phase
        if p["kind"] == "find":
            return "finding the tests", None
        if p["kind"] == "end":
            return "finishing after the last test", None
        if p["kind"] == "test":
            return self._of(p["n"]), _short_id(p["name"])
        if p["kind"] == "setup":
            if p.get("prev"):
                return ("the tear-down of %s and the set-up before %s" % (p["prev"].rsplit(".", 1)[-1], self._of(p["n"])),
                        p["name"].rsplit(".", 1)[-1])
            return "the set-up before " + self._of(p["n"]), p["name"].rsplit(".", 1)[-1]
        if p["kind"] == "teardown":
            return "the tear-down after " + self._of(p["n"]), p["name"].rsplit(".", 1)[-1]
        nxt = (p["n"] or 0) + 1
        return self._of(nxt if self.count is None or nxt <= self.count else self.count), None

    def progress(self, now):
        where, name = self._where()
        took = now - self.phase["since"]
        if name and took > self.slow:
            return "   still running: %s (%s) has taken %s; %s so far" % (where, name, duration(took),
                                                                         duration(now - self.t0))
        return "   still running: %s, %s so far" % (where, duration(now - self.t0))

    def _stop_line(self, now):
        p, done = self.phase, len(self.times)
        if now - self.t0 > self.total:
            head = "The self tests were stopped after %s: " % _total_words(self.total)
            if p["kind"] == "find":
                return head + "they were still being found and loaded; no test had started."
            of = "%d of %s had finished" % (done, self.count if self.count is not None else "?")
            if p["kind"] == "test":
                return head + "%s; the one running was %s (%s)." % (of, p["name"], self._of(p["n"]))
            if p["kind"] == "setup" and p.get("prev"):
                return head + "%s; running was the tear-down of class %s, then the set-up before %s (class %s)." % (
                    of, p["prev"], self._of(p["n"]), p["name"])
            if p["kind"] == "setup":
                return head + "%s; running was the set-up before %s (class %s)." % (of, self._of(p["n"]), p["name"])
            if p["kind"] == "teardown":
                return head + "%s; running was the tear-down of class %s, after its last test." % (of, p["name"])
            if p["kind"] == "after":
                return head + "%s; the next was to start." % of
            return head + "%s; the run was ending." % of
        if now - p["since"] <= p["limit"]:
            return None
        lim = "%d seconds" % p["limit"]
        before = "%d finished before it." % done
        if p["kind"] == "find":
            return ("The self tests were stopped: finding and loading the tests ran longer than %s, before any test "
                    "started." % lim)
        if p["kind"] == "test":
            return "The self tests were stopped: %s ran longer than %s (%s). %s" % (p["name"], lim, self._of(p["n"]),
                                                                                  before)
        if p["kind"] == "setup" and p.get("prev"):
            return ("The self tests were stopped: the tear-down of class %s, then the set-up before %s (class %s), "
                    "ran longer than %s. %s" % (p["prev"], self._of(p["n"]), p["name"], lim, before))
        if p["kind"] == "setup":
            return ("The self tests were stopped: the set-up before %s (class %s) ran longer than %s. %s"
                    % (self._of(p["n"]), p["name"], lim, before))
        if p["kind"] == "teardown":
            return ("The self tests were stopped: the tear-down of class %s, after its last test (%s), ran longer "
                    "than %s. %d of %s had finished." % (p["name"], self._of(p["n"]), lim, done,
                                                         self.count if self.count is not None else "?"))
        if p["kind"] == "end":
            return ("The self tests were stopped: the end of the run, after its last test, ran longer than %s. "
                    "%d of %s had finished." % (lim, done, self.count if self.count is not None else "?"))
        return ("The self tests were stopped: the time after %s ran longer than %s. %d had finished."
                % (self._of(p["n"]), lim, done))

    def tick(self, now):
        """(a progress line or None, a stop line or None)."""
        self.stopped = self.stopped or self._stop_line(now)
        if self.stopped:
            return None, self.stopped
        if now >= self.next_progress:
            while self.next_progress <= now:
                self.next_progress += self.every
            return self.progress(now), None
        return None, None

    def note(self, now):
        """SLOW_NOTE once per run, when the run has gone on for slow_note seconds (asked right after a progress line
        was printed), else None."""
        if self.noted or now - self.t0 < self.slow_note:
            return None
        self.noted = True
        return SLOW_NOTE

    def failed_before(self):
        """Short names of the tests whose DONE line said FAIL or ERROR, in order."""
        return [_short_id(tid) for tid, _, outcome in self.times if outcome in ("FAIL", "ERROR")]

    def running(self, now):
        """The test running at a stop, as (id, seconds), or None."""
        if self.phase["kind"] in ("test", "setup", "teardown") and self.phase["name"]:
            return self.phase["name"], now - self.phase["since"]
        return None


def timing_tables(times, running=None, wall=None):
    """The end of selftest-last.txt: the ten slowest tests and the total by test file, from the DONE lines."""
    out = ["", "-" * 70, "Time per test (from the SEAT-KIT-TEST-DONE lines above): %d %s timed, %.1f s in tests%s."
           % (len(times), "test" if len(times) == 1 else "tests", sum(t for _, t, _ in times),
              ", %.0f s in all" % wall if wall is not None else "")]
    if running:
        out.append("Still running when the run was stopped: %s, %.0f s" % running)
    out.append("Slowest tests:")
    for tid, took, outcome in sorted(times, key=lambda x: -x[1])[:10]:
        out.append("  %8.2f s  %-18s %s" % (took, outcome, tid))
    if not times:
        out.append("  none finished")
    files = {}
    for tid, took, _ in times:
        mod = tid.rsplit(".", 2)[0] if tid.count(".") >= 2 else tid
        n, t = files.get(mod, (0, 0.0))
        files[mod] = (n + 1, t + took)
    out.append("Total by test file:")
    for mod, (n, t) in sorted(files.items(), key=lambda x: -x[1][1]):
        out.append("  %8.2f s  %4d %s  %s" % (t, n, "test " if n == 1 else "tests", mod))
    if not files:
        out.append("  none finished")
    return "\n".join(out) + "\n"


def end_process_tree(proc):
    """Stop the runner the way subprocess.run's timeout does (TerminateProcess on Windows). When the runner's job
    object is armed (its SEAT-KIT-TEST-JOB line says "armed"), that job ends every process in it when the runner's
    handle closes (selftest_runner._own_job), so this also ends what the tests started and left running, and nothing
    else. When it is not armed, or a process broke away from the job, such a process keeps running."""
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=30)
    except Exception:  # noqa: BLE001  (a process that will not end is left; its output so far is kept)
        pass


def watch_suite(proc, watch, clock, progress=None, poll=_POLL, grace=PIPE_GRACE, wall=None):
    """Read the runner's stdout and stderr as they come (one daemon thread each), feed stderr's lines to the watch,
    print its progress lines, and stop the runner when a limit is passed. Returns (stdout text, stderr text, return
    code or None when stopped).

    The run is over when the RUNNER has ended, not when its pipes close: a process a test left running (where the
    job object is not armed, or that broke away from it) may hold the pipes for as long as it lives. Once the runner
    has ended, the pipes get `grace` seconds (on the real clock, `wall`) to deliver what is still in them, and then
    the runner's real return code is returned with what was read. A limit that passes after the runner has already
    ended is not a stop: its return code is taken. Each pipe is closed by its own reader thread once it reaches its
    end, never from here (a close here would wait for a reader blocked on a pipe that a leftover holds); such a
    reader is a daemon thread and ends, closing its pipe, when the leftover lets go, or with this process."""
    wall = wall or time.monotonic
    q = queue.Queue()
    got = {"out": [], "err": []}

    def reader(stream, key):
        at_end = False
        try:
            for ln in iter(stream.readline, ""):
                got[key].append(ln)
                q.put((key, ln))
            at_end = True
        except (OSError, ValueError):
            pass
        finally:
            if at_end:        # closed by its own reader, once it has reached its end: a close cannot block here
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass
            q.put((key, None))
    threads = [threading.Thread(target=reader, args=(s, k), daemon=True) for s, k in ((proc.stdout, "out"),
                                                                                      (proc.stderr, "err"))]
    for t in threads:
        t.start()
    open_pipes, rc, exited, feed = 2, None, None, [True]

    def drain(wait):
        nonlocal open_pipes
        try:
            item = q.get(timeout=wait) if wait else q.get_nowait()
            while item is not None:              # every line that has come, before the clock is looked at
                key, ln = item
                if ln is None:
                    open_pipes -= 1
                elif key == "err" and feed[0]:   # after a stop, lines are kept but no longer change what was said
                    watch.line(ln, clock())
                try:
                    item = q.get_nowait()
                except queue.Empty:
                    item = None
        except queue.Empty:
            pass
    while True:
        drain(poll)
        code = proc.poll()
        if code is not None:
            exited = exited if exited is not None else wall()
            if open_pipes == 0 or wall() - exited >= grace:
                rc = code                        # the runner has ended: its own return code, whatever holds a pipe
                break
        say_line, stop = watch.tick(clock())
        if stop:
            code = proc.poll()
            if code is not None:                 # it had already ended at the limit's instant: not a stop
                rc, watch.stopped = code, None
                until = (exited if exited is not None else wall()) + grace
                while open_pipes and wall() < until:
                    drain(min(poll, max(0.0, until - wall())))
                break
            feed[0] = False                      # the stop line's counts stand; the file still gets every line
            end_process_tree(proc)
            until = wall() + grace               # what was written just before the stop is still in the pipes
            while open_pipes and wall() < until:
                drain(min(poll, max(0.0, until - wall())))
            if watch.ended and proc.returncode == 0:
                # it ended by itself between the look above and the kill (a kill leaves exit code 1): finished
                rc, watch.stopped = 0, None
            break
        if say_line and progress:
            progress(say_line)
            note = watch.note(clock())
            if note:
                progress(note)
    drain(0)
    for t in threads:
        t.join(timeout=1)        # a reader still blocked on a pipe a leftover holds closes it itself when it ends
    return "".join(list(got["out"])), "".join(list(got["err"])), rc


def job_words(job):
    """The selftest-last.txt header's words for the runner's SEAT-KIT-TEST-JOB line."""
    if job == "armed":
        return ("armed (when the test runner's own process ends, Windows ends every program the tests started and "
                "left running, unless one broke away from the job)")
    if job:
        return "%s (programs the tests started and left running are NOT ended with the run)" % job
    return "not reported (the runner said nothing about it)"


def run_suite(kit_home=None, kit=None, total=TOTAL_SECONDS, per_test=PER_TEST_SECONDS,
              before_first=BEFORE_FIRST_SECONDS, progress=None, env=None, clock=None, popen=None,
              every=PROGRESS_EVERY, slow=SLOW_TEST, slow_note=SLOW_NOTE_SECONDS, grace=PIPE_GRACE):
    """Run the kit's tests through the kit's own runner in a fresh python -I. Returns parse_unittest's dict plus ok,
    output, returncode, stopped (the stop line, or None), times ([(id, seconds, outcome)]), limits ({id: the limit
    it ran under}), failed_before (a stopped run's tests whose DONE line said FAIL or ERROR), job and, when
    kit_home is given, file (the report path) and file_error (None when it was written). `progress`, when given, is
    called with each progress line (say_progress for a person's console) and, once after ten minutes, SLOW_NOTE;
    without it nothing is printed."""
    kit = pathlib.Path(kit or KIT)
    clock = clock or time.monotonic
    cmd = [sys.executable, "-I", str(RUNNER), "-s", str(kit / "tests"), "-t", str(kit)]
    rc, stopped = None, None
    started = clock()
    watch = SuiteWatch(started, total, per_test, before_first, every, slow, slow_note)
    try:
        proc = (popen or subprocess.Popen)(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           stdin=subprocess.DEVNULL, text=True, errors="replace", cwd=str(kit),
                                           env=env)
    except OSError as e:
        output = "The self tests could not start (%s: %s).\n" % (type(e).__name__, e)
    else:
        out, err, rc = watch_suite(proc, watch, clock, progress, grace=grace)
        output = out + err   # as subprocess.run gave it before 0.1.7: all of stdout, then all of stderr
        if rc is None:
            stopped = watch.stopped or "The self tests were stopped."
            output += "\n%s\n" % stopped
        else:
            watch.finish(clock())
    ended = clock()
    res = parse_unittest(output)
    if stopped:
        res["summary"] = stopped
    elif rc is None and not res["summary"]:
        res["summary"] = output.strip().splitlines()[-1] if output.strip() else "no output"
    res.update(ok=(rc == 0 and bool(res["ran"]) and not res["failures"] and not res["errors"]),
               output=output, returncode=rc, stopped=stopped, times=list(watch.times), limits=dict(watch.limits),
               failed_before=watch.failed_before() if stopped else [], job=watch.job,
               not_run=list(watch.not_run), build_only=list(watch.build_only), waits=list(watch.waits),
               phases=list(watch.phases), wall=ended - started,
               timing=timing_tables(watch.times, watch.running(ended) if stopped else None, ended - started),
               file=None, file_error=None)
    if kit_home:
        res["file"] = pathlib.Path(long_path(kit_home)) / SELFTEST_FILE
        head = ["Seat kit self tests, %s" % datetime.datetime.now().isoformat(timespec="seconds"),
                "Kit version: %s" % kit_version(kit),
                "Python: %s (%s)" % (platform.python_version(), sys.executable),
                "Windows: %s" % windows_version(),
                "TEMP: %s" % os.environ.get("TEMP", ""),
                "Command: %s" % subprocess.list2cmdline(cmd),
                "Limits: %d s per test (a test may ask for more), %d s to find the tests, %d s in all (a backstop)"
                % (per_test, before_first, total),
                "Job object: %s" % (job_words(watch.job))]
        if watch.not_run:
            head.append("Not run here: %s (named once, on the SEAT-KIT-TEST-NOTRUN lines below)"
                        % (NOT_RUN_HERE % len(watch.not_run)))
        if watch.build_only:
            head.append("Build-only checks: %d, run in this release build (SEAT-KIT-TEST-BUILDONLY lines below)"
                        % len(watch.build_only))
        head += ["Exit code: %s" % ("none (did not finish)" if rc is None else rc), "-" * 70, ""]
        try:
            res["file"].write_text("\n".join(head) + output + res["timing"], encoding="utf-8")
        except OSError as e:
            res["file_error"] = e.strerror or type(e).__name__
    return res


def self_test_detail(res):
    """The part of the stop line in brackets: which tests did not pass (first 3, then 'and N more'), and where the
    full output is, or that it could not be written."""
    names = failed_names(res)
    if res.get("stopped"):   # the stop always reaches the line, with what had already failed before it
        bad = names + [n for n in res.get("failed_before") or [] if n not in names]
        what = res["stopped"].rstrip(".")
        if bad:
            what += "; before the stop, %d did not pass: %s%s" % (
                len(bad), ", ".join(bad[:3]), " and %d more" % (len(bad) - 3) if len(bad) > 3 else "")
    elif names:
        what = "%d did not pass: %s%s" % (len(names), ", ".join(names[:3]),
                                          " and %d more" % (len(names) - 3) if len(names) > 3 else "")
    else:
        what = (res.get("summary") or "no test result").rstrip(".")
    if res.get("file") is None:
        return what
    if res.get("file_error"):
        return "%s; the full output could not be written to %s (%s)" % (what, res["file"], res["file_error"])
    return "%s; full output in %s" % (what, res["file"])


def run_self_tests(kit_home=None):
    """(passed, the detail for a stop line). Progress lines go to the console while the tests run."""
    res = run_suite(kit_home, progress=say_progress)
    return res["ok"], self_test_detail(res)


SELFTEST_TODO = "send this line, and the file it names, to whoever set up your assistant"
SELFTEST_TAKES = "this takes several minutes; longer on a PC where security software checks each step"


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
    ensure_gate_file(args.kit_home, 5)
    settings = write_settings(seat, gate_hook(sys.executable, args.kit_home))
    notes = []
    if cfg.get("tests_passed_for") != kit_version() and not args.no_tests:
        ok, tail = run_self_tests(args.kit_home)
        if not ok:
            raise Stop(5, "the kit's self tests failed (%s)" % tail, "do not use the assistant yet; " + SELFTEST_TODO)
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
    say("done (%s; %s)" % (long_path(seat), "; ".join(notes)))
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
    """One folder, however each is spelled: the file system decides when both exist."""
    try:
        if os.path.exists(str(a)) and os.path.exists(str(b)):
            return os.path.samefile(str(a), str(b))
    except OSError:
        pass
    return canon(a) == canon(b)


def _inside(child, parent):
    if _same(child, parent):
        return True
    c, p = canon(child), canon(parent)
    return c.startswith(p.rstrip("\\/") + os.sep)


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
    """A Microsoft Store Python lives under ...\\WindowsApps\\, which a short spelling hides as WINDOW~1."""
    return "\\windowsapps\\" in os.path.normcase(long_path(exe) if exe else "").replace("/", "\\")


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
                if versioned(args.kit_home):
                    say("         That file stays at the same place for every kit version: it runs the send gate of "
                        "the kit in use, and refuses the tool if it cannot.")
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
    kit_now = pathlib.Path(long_path(KIT.parent.parent)) if versioned(args.kit_home) else \
        pathlib.Path(long_path(args.kit_home)) / "current"
    if kit_now.is_dir():
        say("Outside your folder: to run this check, the launcher put the newest kit into %s (the kit's own folder; "
            "every run of the launcher does this). Nothing else was written: no plugin call, no Claude settings, "
            "no shortcut." % kit_now)
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
        ok, tail = run_self_tests(args.kit_home)
        if not ok:
            raise Stop(4, "the kit's self tests failed (%s); nothing was changed" % tail, SELFTEST_TODO)
        say("   The kit's self tests passed.")
    say("Step 5 of 6: putting the kit onto your folder ... ", end="")
    ensure_gate_file(args.kit_home, 5)
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
    record_adopted(args.kit_home, folder)
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


ADOPTED_LIST = "adopted.txt"


def record_adopted(kit_home, folder):
    """Remember an adopted folder in <kit home>\\adopted.txt (one folder a line, since kit 0.1.5), so remove knows the
    kit's send gate file is in use there and leaves it. Best effort: an adopt never fails over this record."""
    path = pathlib.Path(kit_home) / ADOPTED_LIST
    try:
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except (OSError, UnicodeDecodeError):
        lines = []
    if any(_same(ln, folder) for ln in lines):
        return
    try:
        kit_place.write_file(path, ("\n".join(lines + [str(folder)]) + "\n").encode("utf-8"))
    except OSError:
        pass


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


class _SelftestArgs(argparse.ArgumentParser):
    def error(self, message):
        raise Stop(4, "the selftest line was not understood (%s)" % message, "type it as: Assistant.cmd selftest")


def selftest_main(argv):
    """Read only: run the self tests and print what was found. Writes only <kit-home>\\selftest-last.txt. No plugin
    call, no seat, no shortcut, no session, no folder of the person's. 0 when every test passed, else 1."""
    ap = _SelftestArgs(prog="seat_setup.py selftest")
    ap.add_argument("--kit-home", required=True)
    try:
        args = ap.parse_args(argv)
    except Stop as e:
        say(str(e))
        return 1
    say("Step 4 of 6: running the kit's self tests only (%s) ... " % SELFTEST_TAKES, end="")
    res = run_suite(args.kit_home, progress=say_progress)
    names = failed_names(res)
    # after progress lines, say() puts "Step 4 of 6: " back in front, so the result is on its own clear line
    say("all passed" if res["ok"] else "some did not pass")
    say("")
    say("Self test only: nothing was installed or set up, no plugin call was made, and no folder of yours was touched.")
    say("Kit version:  %s" % kit_version())
    say("Python:       %s (%s)" % (platform.python_version(), sys.executable))
    say("Windows:      %s" % windows_version())
    say(tests_line(res))
    if res.get("not_run"):   # build-only checks: never loaded here, so never counted as ran or skipped
        say("Not run here: " + NOT_RUN_HERE % len(res["not_run"]))
    if res.get("stopped") and res.get("ran") is not None:   # the Tests line has counts, so it does not say why
        say("Stopped:      %s" % res["stopped"])
    for line in noise_lines(res.get("noise")):
        say(line)
    earlier = [n for n in res.get("failed_before") or [] if n not in names]
    if names or earlier:
        say("Did not pass:")
        seen = set()
        for kind, name, reason in res["failed"]:
            if name not in seen:
                seen.add(name)
                say("  %s: %s" % (name, reason or kind))
        outcome = {_short_id(t): o for t, _, o in res.get("times") or []}
        for name in earlier:
            say("  %s: %s (the run was stopped before the reason was written; see the file)"
                % (name, outcome.get(name, "FAIL")))
    if res["file_error"]:
        say("Full output:  could not be written to %s (%s)" % (res["file"], res["file_error"]))
    else:
        say("Full output:  %s" % res["file"])
    say("")
    if res["ok"]:
        say("All self tests passed.")
        return 0
    say("Some self tests did not pass. Send everything in this window, and the file named above, to whoever set up "
        "your assistant.")
    return 1


def main(argv=None):
    seat_common.utf8_stdout()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "adopt":
        return adopt_main(argv[1:])
    if argv and argv[0] == "selftest":
        return selftest_main(argv[1:])
    if argv and argv[0] == "remove":
        import seat_remove   # imported before anything is taken away; it never imports later
        return seat_remove.main(argv[1:])
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
