"""Loop tick bookkeeping for one assistant seat, so each tick is three short commands, not inline scripts.

    python -I tick.py [--seat <folder>] check        -> FREE, HELD (with the seat that runs the loop), or UNKNOWN
    python -I tick.py [--seat <folder>] begin        -> STOP, or the epoch to search mail after, then any due lines
    python -I tick.py [--seat <folder>] end "<note>" -> rewrites state/loop_state.json (its time is the liveness signal)
    python -I tick.py [--seat <folder>] sleep        -> the sleeper: run it in the background; it exits when the next
                                                        tick is due (30 min by day, 60 at night and on weekends) or a
                                                        promise comes due; prints REFUSED at once if a loop is armed

Only one session may run the loop. A loop is "armed" when a `tick.py sleep` process runs under a live Claude
session of this seat folder. There is no lock file to go stale: the process list and `claude agents --json` are the
record, and a sleeper whose session died does not count.

Origin: copied from a working seat's tools/tick.py and cut down for a laptop seat (no git commit, no phone, no
peer rails). Times are the laptop's own clock: day is 07:00 to 22:00 on weekdays.
"""
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # -I leaves the script's folder off sys.path
import seat_common  # noqa: E402

CADENCE = 1800
QUIET_CADENCE = 3600
MARGIN = 300
POLL_S = 30
BACKUP_MAX_AGE_H = 26
DAY_START_H, DAY_END_H = 7, 22

ROOT = None
STATE = None


def set_root(root):
    global ROOT, STATE
    ROOT = pathlib.Path(root)
    STATE = ROOT / "state" / "loop_state.json"


def _read_state():
    """loop_state.json, or a stand-in that re-reads the last 24 hours when it is missing or unreadable."""
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        if STATE.exists():
            print("STATE UNREADABLE (%s): searching from 24 h ago; end rewrites it" % type(e).__name__)
        return {"last_pass_epoch": int(time.time()) - 24 * 3600}


def _write_state(s):
    """Temp file then os.replace, three tries: never a half-written loop_state.json."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    for i in range(3):
        try:
            tmp.write_text(json.dumps(s, indent=1) + "\n", encoding="utf-8")
            os.replace(tmp, STATE)
            return
        except OSError:
            if i == 2:
                raise
            time.sleep(0.5)


def is_quiet(now_local):
    """Night (22:00 to 07:00) or a weekend, on the laptop's own clock."""
    return now_local.weekday() >= 5 or now_local.hour >= DAY_END_H or now_local.hour < DAY_START_H


def cadence(now_local=None):
    return QUIET_CADENCE if is_quiet(now_local or datetime.datetime.now()) else CADENCE


def _backup_line(cfg, now=None):
    """One line when a backup is due or broken; nothing when it is fresh."""
    dest = cfg.get("backup_dir")
    if not dest:
        return "BACKUP OFF: no backup folder chosen yet (the seat-start skill asks for one)"
    marker = pathlib.Path(dest) / "LAST_BACKUP.txt"
    now = time.time() if now is None else now
    try:
        age = (now - marker.stat().st_mtime) / 3600
    except OSError:
        return "BACKUP DUE: no backup in %s yet; run %s" % (dest, seat_common.py_cmd("backup.py"))
    if age > BACKUP_MAX_AGE_H:
        return "BACKUP DUE: last backup is %.0f h old; run %s" % (age, seat_common.py_cmd("backup.py"))
    return None


def _wake_lines(restate=False, now=None):
    try:
        import wakes
        wakes.set_root(ROOT)
        return wakes.run_due(now=now, restate=restate)
    except Exception as e:  # noqa: BLE001
        return ["WAKES UNREADABLE: %s %s" % (type(e).__name__, str(e)[:120])]


def begin():
    if (ROOT / "state" / "STOP").exists():
        print("STOP")
        return
    s = _read_state()
    print(s["last_pass_epoch"])
    # end() stamps the next search from when this tick began, not when it ended: a tick that waits on a
    # permission prompt must not skip the mail that arrived meanwhile.
    s["tick_started"] = int(time.time())
    today = datetime.date.today().isoformat()
    line = _backup_line(seat_common.load_config(ROOT))
    if line and s.get("backup_said") != today:
        print(line)
        s["backup_said"] = today
    _write_state(s)
    for w in _wake_lines():
        print(w)
    print("KIT: loop steps are in %s" % (seat_common.KIT_ROOT / "skills" / "seat-start" / "loop.md"))


def end(note):
    s = _read_state()
    epoch = min(int(time.time()), int(s.pop("tick_started", time.time()))) - MARGIN
    s["last_pass_epoch"] = epoch
    s["last_pass_utc"] = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s["last_note"] = (note or "")[:300]
    _write_state(s)
    print("ok")


# ---------------------------------------------------------------- one loop per seat

def _processes():
    """{pid: (ppid, exe name, command line, start epoch)} for every process, or None."""
    ps = ("Get-CimInstance Win32_Process | ForEach-Object { [pscustomobject]@{"
          "p=$_.ProcessId; pp=$_.ParentProcessId; n=$_.Name; c=$_.CommandLine; "
          "s=$(if ($_.CreationDate) { ([DateTimeOffset]($_.CreationDate)).ToUnixTimeSeconds() } else { 0 })"
          "} } | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, text=True, errors="replace", timeout=90)
        rows = json.loads(out.stdout)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(rows, dict):
        rows = [rows]
    return {r["p"]: (r["pp"], (r["n"] or "").lower(), r["c"] or "", r["s"] or 0) for r in rows}


def claude_exe():
    return shutil.which("claude") or str(pathlib.Path.home() / ".local" / "bin" / "claude.exe")


def _seats():
    """{pid: name} of this seat folder's live Claude sessions, from `claude agents --json`, or None."""
    try:
        out = subprocess.run([claude_exe(), "agents", "--json", "--cwd", str(ROOT)],
                             capture_output=True, text=True, errors="replace", timeout=60)
        return {int(a["pid"]): a.get("name") or a.get("id") or "?" for a in json.loads(out.stdout)}
    except Exception:  # noqa: BLE001
        return None


def _seat_of(pid, procs, seats):
    """The nearest live Claude session above pid, or None when the chain breaks (a parent died)."""
    child_started = procs[pid][3]
    cur = procs[pid][0]
    for _ in range(25):
        p = procs.get(cur)
        if p is None or (p[3] and child_started and p[3] > child_started + 2):
            return None  # parent gone, or its pid was reused by a younger process
        if p[1] == "claude.exe" and (seats is None or cur in seats):
            return cur
        child_started, cur = p[3], p[0]
    return None


SLEEP_RE = re.compile(r"tick\.py\"?\s+(?:--seat(?:=|\s+)(?:\"[^\"]*\"|\S+)\s+)?sleep\b")


def _loop_holders():
    """([(session pid, label, wake epoch)] per armed sleeper of this seat, own session pid); (None, None) if unknown."""
    procs = _processes()
    if not procs or os.getpid() not in procs:
        return None, None
    seats = _seats()
    mine = _seat_of(os.getpid(), procs, seats)
    held = []
    for pid, (_, name, cmd, started) in procs.items():
        if pid == os.getpid() or not name.startswith("python") or not SLEEP_RE.search(cmd):
            continue
        sess = _seat_of(pid, procs, seats)
        if sess is not None:
            label = "this session" if sess == mine else "session '%s'" % (seats or {}).get(sess, "claude pid %d" % sess)
            held.append((sess, label, started + CADENCE))
    return held, mine


def _describe(held):
    parts = ["%s (pid %d), next check about %s" % (label, sess, time.strftime("%I:%M %p", time.localtime(wake)).lstrip("0"))
             for sess, label, wake in held]
    return "the check loop is already running in " + "; ".join(parts)


def check():
    held, _ = _loop_holders()
    if held is None:
        print("UNKNOWN: could not read the process list; treat as FREE")
    elif held:
        print("HELD: " + _describe(held) + ". Do not start a second loop.")
    else:
        print("FREE: no check loop is running; this session may start it")


def sleep(poll_s=POLL_S, now_fn=time.time, sleep_fn=time.sleep):
    held, _ = _loop_holders()
    if held:
        print("REFUSED: " + _describe(held) + ". Do not re-arm; say so in one line.")
        return
    cad = cadence()
    now = now_fn()
    try:
        due = STATE.stat().st_mtime + cad   # counted from the last full tick
    except OSError:
        due = now + cad
    deadline = min(max(due, now + 120), now + cad)
    import wakes
    wakes.set_root(ROOT)
    while True:
        left = deadline - now_fn()
        if left <= 0:
            break
        try:
            if wakes.pending(now_fn()):
                print("WAKE: early (a promise came due). Act on each SEAT WAKE line below, close it with the command "
                      "it names, then re-arm the sleeper; the full check keeps its time.")
                for w in _wake_lines(now=now_fn()):
                    print(w)
                return
        except Exception as e:  # noqa: BLE001
            print("WAKES UNREADABLE this sleep: %s %s" % (type(e).__name__, str(e)[:120]))
            poll_s = max(left, 1)
        sleep_fn(min(poll_s, max(left, 0.01)))
    print("WAKE: check loop tick. Read %s and run one tick." % (seat_common.KIT_ROOT / "skills" / "seat-start" / "loop.md"))


def main(argv=None):
    seat_common.utf8_stdout()
    try:
        root, rest = seat_common.resolve_seat(argv)
    except seat_common.NotASeat as e:
        print(e)
        return 2
    set_root(root)
    cmd = rest[0] if rest else ""
    if cmd == "begin":
        begin()
    elif cmd == "end":
        end(rest[1] if len(rest) > 1 else "")
    elif cmd == "sleep":
        sleep()
    elif cmd == "check":
        check()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
