"""One line per thread from a saved search_threads JSON result.

usage: python -I summarize_search.py [--seat <folder>] <search.json> [more.json ...] [--all]
Skips threads already in state/ledger.jsonl unless --all is given.
Columns: id | msgs | last date | last 3 senders | subject | assistant labels | snippet
Label names come from .seat/seat.json "labels" ({"_ai_done": "Label_123", ...}), written when the labels are made.
Origin: a working seat's tools/summarize_search.py, with the label ids read from the seat instead of written in.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import seat_common  # noqa: E402


def main(argv=None):
    seat_common.utf8_stdout()
    try:
        root, rest = seat_common.resolve_seat(argv)
    except seat_common.NotASeat as e:
        print(e)
        return 2
    labels = {v: k for k, v in (seat_common.load_config(root).get("labels") or {}).items()}
    files = [a for a in rest if a != "--all"]
    if not files:
        print(__doc__)
        return 2
    seen = set()
    if "--all" not in rest:
        led = root / "state" / "ledger.jsonl"
        if led.exists():
            for line in led.read_text(encoding="utf-8").splitlines():
                try:
                    seen.add(json.loads(line)["thread"])
                except (ValueError, KeyError, TypeError):
                    continue
    for f in files:
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        print("# %s: %d threads, next=%s" % (f, len(d.get("threads", [])), d.get("nextPageToken")))
        for t in d.get("threads", []):
            if t.get("id") in seen or not t.get("messages"):
                continue
            m = t["messages"]
            last = m[-1]
            ls = set()
            for x in m:
                ls |= set(x.get("labelIds", []))
            tags = " ".join(sorted(labels[i] for i in ls if i in labels))
            snd = ",".join((x.get("sender") or "?").split("@")[0] for x in m[-3:])
            print("%s|%d|%s|%s|%s|%s|%s" % (t["id"], len(m), (last.get("date") or "")[:16], snd,
                                            (last.get("subject") or "")[:70], tags, (last.get("snippet") or "")[:100]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
