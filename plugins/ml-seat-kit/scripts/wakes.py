"""Promises for later: a promise to act later is ONE dated row, fired by the sleeper, closed with a line.

Ledger: state/wakes.jsonl in the seat folder, append-only events; a row's state is a pure fold (fold()).
  {"op":"add","id","at","text","context","grace_min","owner_line","created"}
  {"op":"snooze","id","until","reason","at"}
  {"op":"done"|"fail"|"cancel","id","line","at"}
Bookkeeping (fired, late, owner) lives in .cache/wakes_state.json, never in loop_state.json, whose time schedules
the next tick.

Lines:
  SEAT WAKE: <id> due HH:MM[ (late: no sleeper was running at HH:MM)] | <text> | context: <ctx> | then: ...
  SEAT WAKE: <id> LATE | <text> | due HH:MM, <n>m late[ (snoozed N times)] | context: <ctx> | then: ...
  SEAT WAKE: <id> OWNER | <owner_line or text> | late <n>h
due fires once at the row's time. LATE = fired, then the grace (default 30 min) passed with no done/fail/cancel/
snooze; said once, then again at each 07:00 morning pass until closed. Three snoozes = LATE regardless. OWNER = LATE
24 h, or at the morning pass: one line to the person in the chat ("I promised X and have not done it").

CLI (times are the laptop's own clock):
  python -I wakes.py [--seat <folder>] add --at "<when>" --text "..." [--context "..."] [--grace 30] [--owner-line "..."]
  python -I wakes.py [--seat <folder>] park --text "..." [--context "..."]   (next 9:00 AM / 7:30 PM, >= 30 min out)
  python -I wakes.py [--seat <folder>] list [--all]
  python -I wakes.py [--seat <folder>] snooze <id> --until "<when>" --reason "..."
  python -I wakes.py [--seat <folder>] done|fail <id> "<line>"        cancel <id> "<reason>"
  python -I wakes.py [--seat <folder>] due [--restate]                 (tick.py begin and sleep call this themselves)
<when>: HH:MM | tomorrow HH:MM | YYYY-MM-DD HH:MM | YYYY-MM-DD (09:00) | +2h / +30m / +1d | mon 09:00

Origin: copied from a working seat's tools/wakes.py. Changes: the seat folder is found at run time, the clock is
the laptop's own (a ZONE object that tests can swap), lines start "SEAT WAKE:".
"""
import argparse
import datetime as dt
import io
import json
import os
import pathlib
import random
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import seat_common  # noqa: E402

PREFIX = "SEAT WAKE:"
DEFAULT_GRACE_MIN = 30
LATE_FIRE_S = 15 * 60            # fired later than this after its time = no sleeper was running then
OWNER_AFTER_S = 24 * 3600
MORNING_HM = (7, 0)
SNOOZE_LATE_AT = 3
PARK_SLOTS = [(9, 0), (19, 30)]
PARK_MIN_OUT_S = 30 * 60
_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

ROOT = None
LEDGER = None
STATE = None


def set_root(root):
    global ROOT, LEDGER, STATE
    ROOT = pathlib.Path(root)
    LEDGER = ROOT / "state" / "wakes.jsonl"
    STATE = ROOT / ".cache" / "wakes_state.json"


# ---------------------------------------------------------------- the clock

class LocalZone:
    """The laptop's own clock and its daylight saving rule (from Windows, so no tzdata is needed)."""

    def to_dt(self, ts):
        return dt.datetime.fromtimestamp(ts).astimezone()

    def to_ts(self, day, h, m):
        return time.mktime((day.year, day.month, day.day, h, m, 0, 0, 0, -1))


ZONE = LocalZone()


def _dt(ts):
    return ZONE.to_dt(ts)


def _ts(day, h, m):
    return ZONE.to_ts(day, h, m)


# ---------------------------------------------------------------- pure core

def _hm(s):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s.strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError("bad time %r (want HH:MM, 24-hour)" % s)
    return int(m.group(1)), int(m.group(2))


def parse_when(s, now):
    """A human <when> to epoch seconds. Raises ValueError when it cannot."""
    s = (s or "").strip().lower()
    today = _dt(now).date()
    m = re.fullmatch(r"\+(\d+)\s*([mhd])", s)
    if m:
        return now + int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400}[m.group(2)]
    if re.fullmatch(r"\d{1,2}:\d{2}", s):
        h, mi = _hm(s)
        t = _ts(today, h, mi)
        return t if t > now else _ts(today + dt.timedelta(days=1), h, mi)
    m = re.fullmatch(r"tomorrow\s+(\d{1,2}:\d{2})", s)
    if m:
        h, mi = _hm(m.group(1))
        return _ts(today + dt.timedelta(days=1), h, mi)
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{1,2}:\d{2}))?", s)
    if m:
        h, mi = _hm(m.group(4)) if m.group(4) else (9, 0)
        return _ts(dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))), h, mi)
    m = re.fullmatch(r"([a-z]{3})[a-z]*\s+(\d{1,2}:\d{2})", s)
    if m and m.group(1) in _WEEKDAYS:
        h, mi = _hm(m.group(2))
        ahead = (_WEEKDAYS.index(m.group(1)) - today.weekday()) % 7
        t = _ts(today + dt.timedelta(days=ahead), h, mi)
        return t if t > now else _ts(today + dt.timedelta(days=ahead + 7), h, mi)
    raise ValueError("cannot read when %r" % s)


def park_time(now):
    """Next quiet slot (9:00 AM or 7:30 PM), at least 30 min out."""
    today = _dt(now).date()
    for add_days in (0, 1):
        for h, m in PARK_SLOTS:
            t = _ts(today + dt.timedelta(days=add_days), h, m)
            if t >= now + PARK_MIN_OUT_S:
                return t
    return _ts(today + dt.timedelta(days=2), 9, 0)


def fold(events, errors=None):
    """{id: row} from the event list. A bad event is skipped (noted in `errors` when given), never fatal."""
    rows = {}
    for n, e in enumerate(events, 1):
        try:
            _apply(rows, e)
        except Exception as exc:                                  # noqa: BLE001
            if errors is not None:
                key = (e.get("id") if isinstance(e, dict) else None) or "event %d" % n
                errors.append((str(key), "%s: %s" % (type(exc).__name__, str(exc)[:80])))
    return rows


def _apply(rows, e):
    op, rid = e.get("op"), e.get("id")
    if op == "add" and rid and rid not in rows:
        rows[rid] = {"id": rid, "at": float(e["at"]), "text": e.get("text", ""), "context": e.get("context", ""),
                     "grace_min": int(e.get("grace_min", DEFAULT_GRACE_MIN)), "owner_line": e.get("owner_line") or "",
                     "created": e.get("created", e.get("at")), "status": "open", "snoozes": 0,
                     "last_snooze_at": None, "line": "", "closed_at": None, "orig_at": float(e["at"])}
        return
    r = rows.get(rid)
    if r is None or r["status"] != "open":
        return
    if op == "snooze":
        r["at"] = float(e["until"])
        r["snoozes"] += 1
        r["last_snooze_at"] = e.get("at")
        r["line"] = e.get("reason", "")
    elif op in ("done", "fail", "cancel"):
        r["status"] = op
        r["line"] = e.get("line", "")
        r["closed_at"] = e.get("at")
        r["learned"] = e.get("learned", "")


def _fmt_hm(ts):
    return _dt(ts).strftime("%H:%M")


def _then(rid):
    w = seat_common.py_cmd("wakes.py")
    return ("then: %s done %s \"<line>\" (or snooze %s --until \"<when>\" --reason \"...\" / cancel %s \"<reason>\")"
            % (w, rid, rid, rid))


def compute_lines(rows, st, now, first_beat=False):
    """The SEAT WAKE lines for this beat. Mutates st: wake_fired {id: {at, f}}, wake_late {id: at},
    wake_owner {id: at}, wake_morning day. Pure otherwise. first_beat = restate open LATE rows."""
    fired = dict(st.get("wake_fired") or {})
    late_seen = dict(st.get("wake_late") or {})
    owner_seen = dict(st.get("wake_owner") or {})
    day = _dt(now).strftime("%Y-%m-%d")
    morning = now >= _ts(_dt(now).date(), *MORNING_HM) and st.get("wake_morning") != day
    restate = first_beat or morning
    open_rows = sorted((r for r in rows.values() if r["status"] == "open"), key=lambda r: (r["at"], r["id"]))
    open_ids = {r["id"] for r in open_rows}
    lines = []
    for r in open_rows:
        rid, eff = r["id"], r["at"]
        fi = fired.get(rid)
        if (not fi or fi["at"] != eff) and now >= eff:
            late_by = now - eff
            fired[rid] = fi = {"at": eff, "f": now if late_by > LATE_FIRE_S else eff}
            note = (" (late: no sleeper was running at %s)" % _fmt_hm(eff)) if late_by > LATE_FIRE_S else ""
            lines.append("%s %s due %s%s | %s | context: %s | %s"
                         % (PREFIX, rid, _fmt_hm(eff), note, r["text"], r["context"] or "(none)", _then(rid)))
            continue                              # a fresh firing starts its own grace
        late_since = None
        snooze_late = r["snoozes"] >= SNOOZE_LATE_AT
        if fi and fi["at"] == eff:
            late_since = fi["f"] if snooze_late else fi["f"] + r["grace_min"] * 60
        elif snooze_late:
            late_since = r["last_snooze_at"] or now
        if late_since is None or now < late_since:
            continue
        emitted = False
        if late_seen.get(rid) != eff:
            late_seen[rid] = eff
            emitted = True
        if emitted or restate:
            why = " (snoozed %d times)" % r["snoozes"] if snooze_late else ""
            lines.append("%s %s LATE | %s | due %s, %dm late%s | context: %s | %s"
                         % (PREFIX, rid, r["text"], _fmt_hm(eff), max(0, int((now - eff) // 60)), why,
                            r["context"] or "(none)", _then(rid)))
        hours = (now - late_since) / 3600.0
        if (hours >= OWNER_AFTER_S / 3600.0 and owner_seen.get(rid) != eff) or morning:
            owner_seen[rid] = eff
            lines.append("%s %s OWNER | %s | late %dh"
                         % (PREFIX, rid, r["owner_line"] or r["text"], max(1, int((now - eff) // 3600))))
    st["wake_fired"] = {k: v for k, v in fired.items() if k in open_ids}
    st["wake_late"] = {k: v for k, v in late_seen.items() if k in open_ids}
    st["wake_owner"] = {k: v for k, v in owner_seen.items() if k in open_ids}
    if morning:
        st["wake_morning"] = day
    return lines


# ---------------------------------------------------------------- I/O

def read_events(path=None, errors=None):
    out = []
    try:
        with io.open(path or LEDGER, encoding="utf-8") as fh:
            for n, raw in enumerate(fh, 1):
                if not raw.strip():
                    continue
                try:
                    e = json.loads(raw)
                except ValueError as exc:
                    if errors is not None:
                        errors.append(("line %d" % n, "%s: %s" % (type(exc).__name__, str(exc)[:80])))
                    continue
                if isinstance(e, dict):
                    out.append(e)
                elif errors is not None:
                    errors.append(("line %d" % n, "not an object"))
    except OSError:
        pass
    return out


def append_event(event, path=None):
    path = pathlib.Path(path or LEDGER)
    path.parent.mkdir(parents=True, exist_ok=True)
    with io.open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def _load_state(path=None):
    try:
        return json.loads(pathlib.Path(path or STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(st, path=None):
    path = pathlib.Path(path or STATE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def bad_row_lines(st, errors):
    """One `SEAT WAKE ERROR: bad row ...` per distinct bad row, once ever; the rest of the ledger keeps firing."""
    seen = set(st.get("wake_bad_reported") or [])
    lines = []
    for key, err in errors:
        tag = "%s|%s" % (key, err)
        if tag not in seen:
            seen.add(tag)
            lines.append("SEAT WAKE ERROR: bad row %s in state/wakes.jsonl: %s" % (key, err))
    st["wake_bad_reported"] = sorted(seen)[-200:]
    return lines


def due_lines(st, now=None, restate=False, ledger=None):
    now = time.time() if now is None else now
    errors = []
    rows = fold(read_events(ledger, errors), errors)
    return bad_row_lines(st, errors) + compute_lines(rows, st, now, first_beat=restate)


def run_due(now=None, restate=False, ledger=None, state=None):
    """Lines due now, with the bookkeeping saved: what tick.py begin and the sleeper call."""
    st = _load_state(state)
    lines = due_lines(st, now, restate, ledger)
    _save_state(st, state)
    return lines


def pending(now=None, ledger=None, state=None):
    """True when an open row is due and not yet fired. Read-only: the sleeper polls this, then run_due prints."""
    now = time.time() if now is None else now
    fired = (_load_state(state).get("wake_fired") or {})
    for r in fold(read_events(ledger)).values():
        if r["status"] == "open" and now >= r["at"] and (fired.get(r["id"]) or {}).get("at") != r["at"]:
            return True
    return False


def new_id(now, existing):
    day = _dt(now).strftime("%Y%m%d")
    while True:
        rid = "w-%s-%04x" % (day, random.randrange(0x10000))
        if rid not in existing:
            return rid


def add(at, text, context="", grace_min=DEFAULT_GRACE_MIN, owner_line="", now=None, path=None):
    now = time.time() if now is None else now
    rid = new_id(now, fold(read_events(path)))
    append_event({"op": "add", "id": rid, "at": at, "text": text, "context": context, "grace_min": int(grace_min),
                  "owner_line": owner_line, "created": int(now)}, path)
    return fold(read_events(path))[rid]


def _open_row(rid, path):
    row = fold(read_events(path)).get(rid)
    if row is None:
        raise KeyError("no wake %s" % rid)
    if row["status"] != "open":
        raise ValueError("%s is already %s" % (rid, row["status"]))
    return row


def close(rid, op, line, now=None, path=None, learned=""):
    """done | fail | cancel. Raises KeyError/ValueError on unknown or closed ids. learned: what the close taught
    that outlives the promise (a standing preference, a fact about a person, a trap); most closes teach nothing."""
    now = time.time() if now is None else now
    _open_row(rid, path)
    e = {"op": op, "id": rid, "line": line, "at": int(now)}
    if learned:
        e["learned"] = learned
    append_event(e, path)
    return fold(read_events(path))[rid]


def snooze(rid, until, reason, now=None, path=None):
    now = time.time() if now is None else now
    _open_row(rid, path)
    if until <= now:
        raise ValueError("snooze time is not in the future")
    append_event({"op": "snooze", "id": rid, "until": until, "reason": reason, "at": int(now)}, path)
    return fold(read_events(path))[rid]


def show(r):
    at = _dt(r["at"]).strftime("%a %Y-%m-%d %H:%M")
    extra = " snoozed x%d" % r["snoozes"] if r["snoozes"] else ""
    tail = " | %s: %s" % (r["status"], r["line"]) if r["status"] != "open" else ""
    return "%s  %s  [%s%s]  %s  (grace %dm)%s%s" % (
        r["id"], at, r["status"], extra, r["text"], r["grace_min"],
        (" | context: " + r["context"]) if r["context"] else "", tail)


def main(argv=None):
    seat_common.utf8_stdout()
    try:
        root, rest = seat_common.resolve_seat(argv)
    except seat_common.NotASeat as e:
        print(e)
        return 2
    set_root(root)
    ap = argparse.ArgumentParser(prog="python -I wakes.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("add", "park"):
        a = sub.add_parser(name)
        if name == "add":
            a.add_argument("--at", required=True)
        a.add_argument("--text", required=True)
        a.add_argument("--context", default="")
        a.add_argument("--grace", type=int, default=DEFAULT_GRACE_MIN)
        a.add_argument("--owner-line", default="")
    sub.add_parser("list").add_argument("--all", action="store_true")
    sn = sub.add_parser("snooze")
    sn.add_argument("id")
    sn.add_argument("--until", required=True)
    sn.add_argument("--reason", required=True)
    for name in ("done", "fail", "cancel"):
        c = sub.add_parser(name)
        c.add_argument("id")
        c.add_argument("line")
        c.add_argument("--learned", default="", help="what this close taught that outlives the promise")
    sub.add_parser("due").add_argument("--restate", action="store_true")
    args = ap.parse_args(rest)
    now = time.time()
    try:
        if args.cmd == "add":
            r = add(parse_when(args.at, now), args.text, args.context, args.grace, args.owner_line, now)
        elif args.cmd == "park":
            r = add(park_time(now), args.text, args.context, args.grace, args.owner_line, now)
        elif args.cmd == "snooze":
            r = snooze(args.id, parse_when(args.until, now), args.reason, now)
        elif args.cmd in ("done", "fail", "cancel"):
            r = close(args.id, args.cmd, args.line, now, learned=args.learned)
        elif args.cmd == "due":
            print("\n".join(run_due(now, restate=args.restate)) or "(no wakes due)")
            return 0
        else:
            rows = sorted(fold(read_events()).values(), key=lambda x: x["at"])
            shown = [x for x in rows if args.all or x["status"] == "open"]
            print("\n".join(show(x) for x in shown) or "(no %swakes)" % ("" if args.all else "open "))
            return 0
    except (KeyError, ValueError) as exc:
        print("error: %s" % exc)
        return 2
    print(show(r))
    if r.get("learned"):
        print("LEARNED: write this lesson to state/corrections.md this turn (it outlives the promise): %s" % r["learned"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
