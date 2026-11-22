"""Checks for scripts/wakes.py (no network). Carried from the origin seat's test_wakes.py with a pinned clock zone.

    python -I -m unittest discover -s tests -t .
"""
import datetime as dt
import pathlib
import unittest

from tests import _helpers
import wakes


def et(y, mo, d, h=0, mi=0):
    return wakes._ts(dt.date(y, mo, d), h, mi)


NOW = None   # set in setUpModule, once the zone is pinned


def setUpModule():
    global NOW
    wakes.ZONE = _helpers.EasternZone()
    NOW = et(2026, 11, 12, 10, 0)   # a Thursday; DST ended 2026-11-01; spring-forward is 2027-03-14


def tearDownModule():
    wakes.ZONE = wakes.LocalZone()


def ev_add(rid="w-1", at=None, **kw):
    at = NOW if at is None else at
    e = {"op": "add", "id": rid, "at": at, "text": "check the export", "context": "thread X",
         "grace_min": 30, "owner_line": "", "created": at - 100}
    e.update(kw)
    return e


def run(rows, st, now, first=False):
    return wakes.compute_lines(wakes.fold(rows), st, now, first_beat=first)


class Clock(unittest.TestCase):
    def test_zone_is_pinned(self):
        self.assertEqual(NOW, dt.datetime(2026, 11, 12, 15, 0, tzinfo=dt.timezone.utc).timestamp())
        self.assertEqual(et(2027, 7, 4, 9, 0), dt.datetime(2027, 7, 4, 13, 0, tzinfo=dt.timezone.utc).timestamp())

    def test_local_zone_round_trips(self):
        z = wakes.LocalZone()
        t = z.to_ts(dt.date(2026, 6, 15), 9, 30)
        self.assertEqual(z.to_dt(t).strftime("%Y-%m-%d %H:%M"), "2026-06-15 09:30")
        t = z.to_ts(dt.date(2026, 12, 15), 17, 5)
        self.assertEqual(z.to_dt(t).strftime("%Y-%m-%d %H:%M"), "2026-12-15 17:05")


class ParseWhen(unittest.TestCase):
    def test_forms(self):
        p = wakes.parse_when
        self.assertEqual(p("14:30", NOW), et(2026, 11, 12, 14, 30))
        self.assertEqual(p("09:00", NOW), et(2026, 11, 13, 9, 0))          # past today -> tomorrow
        self.assertEqual(p("tomorrow 08:15", NOW), et(2026, 11, 13, 8, 15))
        self.assertEqual(p("2026-12-01 17:00", NOW), et(2026, 12, 1, 17, 0))
        self.assertEqual(p("2026-12-01", NOW), et(2026, 12, 1, 9, 0))
        self.assertEqual(p("+2h", NOW), NOW + 7200)
        self.assertEqual(p("+30m", NOW), NOW + 1800)
        self.assertEqual(p("mon 09:00", NOW), et(2026, 11, 16, 9, 0))
        self.assertEqual(p("thu 09:00", NOW), et(2026, 11, 19, 9, 0))      # past today -> next week
        self.assertEqual(p("thu 11:00", NOW), et(2026, 11, 12, 11, 0))
        for bad in ("whenever", "25:00"):
            with self.assertRaises(ValueError):
                p(bad, NOW)

    def test_dst_safe(self):
        t = wakes.parse_when("tomorrow 09:00", et(2027, 3, 13, 8, 0))
        self.assertEqual(wakes._dt(t).strftime("%Y-%m-%d %H:%M %Z"), "2027-03-14 09:00 EDT")
        self.assertEqual(wakes._dt(et(2026, 11, 1, 9, 0)).strftime("%H:%M %Z"), "09:00 EST")

    def test_park_slots(self):
        self.assertEqual(wakes.park_time(et(2026, 11, 12, 6, 0)), et(2026, 11, 12, 9, 0))
        self.assertEqual(wakes.park_time(et(2026, 11, 12, 8, 45)), et(2026, 11, 12, 19, 30))
        self.assertEqual(wakes.park_time(et(2026, 11, 12, 20, 0)), et(2026, 11, 13, 9, 0))


class Lines(unittest.TestCase):
    def test_fold(self):
        rows = wakes.fold([ev_add("w-1"), ev_add("w-2"), ev_add("w-3"),
                           {"op": "snooze", "id": "w-1", "until": NOW + 600, "reason": "busy", "at": NOW},
                           {"op": "done", "id": "w-2", "line": "did it", "at": NOW},
                           {"op": "cancel", "id": "w-3", "line": "moot", "at": NOW},
                           {"op": "snooze", "id": "w-2", "until": NOW + 9, "reason": "ignored", "at": NOW}])
        self.assertEqual((rows["w-1"]["status"], rows["w-1"]["at"], rows["w-1"]["snoozes"]), ("open", NOW + 600, 1))
        self.assertEqual((rows["w-2"]["status"], rows["w-2"]["at"]), ("done", NOW))
        self.assertEqual(rows["w-3"]["status"], "cancel")

    def test_due_once_then_late_once(self):
        ev, st = [ev_add()], {"wake_morning": "2026-11-12"}
        self.assertEqual(run(ev, st, NOW - 60), [])
        out = run(ev, st, NOW)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("SEAT WAKE: w-1 due 10:00 | check the export | context: thread X |"))
        self.assertNotIn("late:", out[0])
        self.assertIn("wakes.py\" done w-1", out[0])
        self.assertEqual(run(ev, st, NOW + 29 * 60), [])
        late = run(ev, st, NOW + 31 * 60)
        self.assertEqual(len(late), 1)
        self.assertTrue(late[0].startswith("SEAT WAKE: w-1 LATE | check the export | due 10:00, 31m late"))
        self.assertEqual(run(ev, st, NOW + 5 * 3600), [])

    def test_restate_and_late_firing(self):
        ev, st = [ev_add()], {"wake_morning": "2026-11-12"}
        out = run(ev, st, NOW + 3 * 3600)
        self.assertIn("(late: no sleeper was running at 10:00)", out[0])
        self.assertEqual(run(ev, st, NOW + 3 * 3600 + 20 * 60), [])
        self.assertIn("LATE", run(ev, st, NOW + 3 * 3600 + 31 * 60)[0])
        again = run(ev, st, NOW + 4 * 3600, first=True)
        self.assertEqual(len(again), 1)
        self.assertIn("LATE", again[0])

    def test_snooze_and_close(self):
        ev, st = [ev_add()], {"wake_morning": "2026-11-12"}
        run(ev, st, NOW)
        ev.append({"op": "snooze", "id": "w-1", "until": NOW + 3600, "reason": "busy", "at": NOW + 60})
        self.assertEqual(run(ev, st, NOW + 31 * 60), [])
        self.assertIn("w-1 due 11:00", run(ev, st, NOW + 3600)[0])
        ev.append({"op": "done", "id": "w-1", "line": "ok", "at": NOW + 3700})
        self.assertEqual(run(ev, st, NOW + 9000), [])
        self.assertEqual(st["wake_fired"], {})

    def test_owner_at_morning(self):
        ev, st = [ev_add(owner_line="Promised the export check; not done")], {"wake_morning": "2026-11-12"}
        run(ev, st, NOW)
        run(ev, st, NOW + 31 * 60)
        out = run(ev, st, et(2026, 11, 13, 7, 0))
        self.assertEqual([o.split("|")[0].strip() for o in out], ["SEAT WAKE: w-1 LATE", "SEAT WAKE: w-1 OWNER"])
        self.assertEqual(out[1], "SEAT WAKE: w-1 OWNER | Promised the export check; not done | late 21h")


class Files(unittest.TestCase):
    def test_files_pending_and_cli(self):
        td, seat = _helpers.make_seat()
        with td:
            wakes.set_root(seat)
            r = wakes.add(NOW - 60, "ping the vendor", "thread A", now=NOW - 120)
            self.assertTrue(wakes.pending(NOW))
            lines = wakes.run_due(NOW)
            self.assertTrue(any(l.startswith("SEAT WAKE: %s due" % r["id"]) for l in lines))
            self.assertFalse(wakes.pending(NOW + 5))                 # fired once; the sleeper stays asleep
            s = ["--seat", str(seat)]
            self.assertEqual(wakes.main(s + ["done", r["id"], "pinged"]), 0)
            self.assertEqual(wakes.main(s + ["done", r["id"], "again"]), 2)
            led = seat / "state" / "wakes.jsonl"
            led.write_text(led.read_text() + "{not json\n")
            bad = wakes.run_due(NOW + 10)
            self.assertEqual(len([l for l in bad if "bad row" in l]), 1)
            self.assertFalse([l for l in wakes.run_due(NOW + 20) if "bad row" in l])
            self.assertEqual(wakes.main(s + ["add", "--at", "+1h", "--text", "t"]), 0)
            self.assertEqual(wakes.main(s + ["park", "--text", "later"]), 0)
            self.assertEqual(wakes.main(s + ["list"]), 0)
            r2 = wakes.add(NOW + 60, "lesson row")
            self.assertEqual(wakes.main(s + ["done", r2["id"], "kept", "--learned", "wants X on Mondays"]), 0)
            f = wakes.fold(wakes.read_events())
            self.assertEqual(f[r2["id"]]["learned"], "wants X on Mondays")
            self.assertEqual(f[r["id"]]["learned"], "")

    def test_not_a_seat(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(wakes.main(["--seat", d, "list"]), 2)
            self.assertFalse((pathlib.Path(d) / "state").exists())


if __name__ == "__main__":
    unittest.main()
