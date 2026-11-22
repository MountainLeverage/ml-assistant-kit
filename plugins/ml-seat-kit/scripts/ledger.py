"""Append one line to the seat's ledger, state/ledger.jsonl (UTC timestamp from the clock, LF endings).

usage: python -I ledger.py [--seat <folder>] <thread> <from> <subject> <outcome> <reason> [due YYYY-MM-DD]

outcome is one of: done, drafted, waiting, needs_you, skip. One line per thread, written as you go, so a crash
loses nothing. Origin: a working seat's tools/ledger.py, with the seat folder found at run time.
"""
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import seat_common  # noqa: E402

OUTCOMES = ("done", "drafted", "waiting", "needs_you", "skip")


def record(root, thread, sender, subject, outcome, reason, due=None, now=None):
    if outcome not in OUTCOMES:
        raise ValueError("outcome %r is not one of %s" % (outcome, ", ".join(OUTCOMES)))
    if due is not None:
        datetime.date.fromisoformat(due)   # raises ValueError on a bad date
    now = now or datetime.datetime.now(datetime.timezone.utc)
    rec = {"ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "thread": thread, "from": sender, "subject": subject,
           "outcome": outcome, "reason": reason, "due": due}
    path = pathlib.Path(root) / "state" / "ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def main(argv=None):
    seat_common.utf8_stdout()
    try:
        root, a = seat_common.resolve_seat(argv)
    except seat_common.NotASeat as e:
        print(e)
        return 2
    if len(a) < 5:
        print(__doc__)
        return 2
    try:
        rec = record(root, a[0], a[1], a[2], a[3], a[4], a[5] if len(a) > 5 else None)
    except ValueError as e:
        print("error: %s" % e)
        return 2
    print(rec["thread"], rec["outcome"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
