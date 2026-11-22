"""Checks for scripts/tick.py (no network, no real sleeper, no process list)."""
import datetime
import io
import json
import os
import pathlib
import time
import unittest
from contextlib import redirect_stdout

from tests import _helpers
import tick


def out_of(fn, *a, **kw):
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*a, **kw)
    return buf.getvalue()


class Cadence(unittest.TestCase):
    def test_day_night_weekend(self):
        thu = datetime.datetime(2026, 11, 12)
        self.assertEqual(tick.cadence(thu.replace(hour=10)), 1800)
        self.assertEqual(tick.cadence(thu.replace(hour=7)), 1800)
        self.assertEqual(tick.cadence(thu.replace(hour=21, minute=59)), 1800)
        self.assertEqual(tick.cadence(thu.replace(hour=22)), 3600)
        self.assertEqual(tick.cadence(thu.replace(hour=6, minute=59)), 3600)
        self.assertEqual(tick.cadence(datetime.datetime(2026, 11, 14, 12)), 3600)   # Saturday
        self.assertEqual(tick.cadence(datetime.datetime(2026, 11, 15, 12)), 3600)   # Sunday

    def test_sleep_pattern_matches_with_and_without_seat(self):
        for cmd in ('python -I "C:\\kit\\scripts\\tick.py" sleep',
                    'python -I tick.py --seat "C:\\Some Seat" sleep',
                    'python -I tick.py --seat=C:\\seat sleep'):
            self.assertTrue(tick.SLEEP_RE.search(cmd), cmd)
        self.assertFalse(tick.SLEEP_RE.search('python -I tick.py check'))


class Ticks(unittest.TestCase):
    def setUp(self):
        self.td, self.seat = _helpers.make_seat()
        tick.set_root(self.seat)

    def tearDown(self):
        self.td.cleanup()

    def test_begin_end_round_trip(self):
        out = out_of(tick.begin).splitlines()
        first = int(out[0])
        self.assertLessEqual(abs(first - (time.time() - 24 * 3600)), 5)       # no state yet: last 24 hours
        self.assertTrue(any(l.startswith("BACKUP OFF") for l in out))
        self.assertTrue(out[-1].startswith("KIT: loop steps are in "))
        self.assertTrue(pathlib.Path(out[-1].split(" in ", 1)[1]).is_file())
        self.assertFalse(any(l.startswith("BACKUP OFF") for l in out_of(tick.begin).splitlines()))  # once a day
        self.assertEqual(out_of(tick.end, "3 done, 1 drafted").strip(), "ok")
        s = json.loads((self.seat / "state" / "loop_state.json").read_text())
        self.assertNotIn("tick_started", s)
        self.assertLessEqual(s["last_pass_epoch"], time.time() - tick.MARGIN + 1)
        self.assertEqual(s["last_note"], "3 done, 1 drafted")
        self.assertEqual(int(out_of(tick.begin).splitlines()[0]), s["last_pass_epoch"])

    def test_stop_file(self):
        (self.seat / "state" / "STOP").write_text("")
        self.assertEqual(out_of(tick.begin).strip(), "STOP")
        self.assertFalse((self.seat / "state" / "loop_state.json").exists())

    def test_unreadable_state_reads_last_day(self):
        (self.seat / "state" / "loop_state.json").write_text("{half")
        out = out_of(tick.begin).splitlines()
        self.assertTrue(out[0].startswith("STATE UNREADABLE"))
        self.assertLessEqual(abs(int(out[1]) - (time.time() - 24 * 3600)), 5)

    def test_backup_line(self):
        self.assertIn("BACKUP OFF", tick._backup_line({}))
        with _helpers.tempfile.TemporaryDirectory() as d:
            self.assertIn("BACKUP DUE: no backup", tick._backup_line({"backup_dir": d}))
            marker = pathlib.Path(d) / "LAST_BACKUP.txt"
            marker.write_text("x")
            self.assertIsNone(tick._backup_line({"backup_dir": d}))
            old = time.time() - 30 * 3600
            os.utime(marker, (old, old))
            self.assertIn("30 h old", tick._backup_line({"backup_dir": d}))

    def test_sleep_refuses_second_arm(self):
        orig = tick._loop_holders
        tick._loop_holders = lambda: ([(1234, "session 'other'", time.time() + 900)], 99)
        try:
            out = out_of(tick.sleep)
        finally:
            tick._loop_holders = orig
        self.assertTrue(out.startswith("REFUSED: the check loop is already running in session 'other' (pid 1234)"))

    def test_check_lines(self):
        orig = tick._loop_holders
        try:
            tick._loop_holders = lambda: (None, None)
            self.assertTrue(out_of(tick.check).startswith("UNKNOWN"))
            tick._loop_holders = lambda: ([], 5)
            self.assertTrue(out_of(tick.check).startswith("FREE"))
            tick._loop_holders = lambda: ([(7, "this session", time.time())], 7)
            self.assertTrue(out_of(tick.check).startswith("HELD"))
        finally:
            tick._loop_holders = orig

    def _fake_sleep(self, start, wake_at=None):
        """Run sleep() on a fake clock; optionally a wake row comes due at `wake_at`."""
        clock = [start]
        orig = tick._loop_holders
        tick._loop_holders = lambda: ([], None)
        if wake_at is not None:
            import wakes
            wakes.set_root(self.seat)
            wakes.add(wake_at, "call back the vendor", now=start)
        try:
            return out_of(tick.sleep, poll_s=30, now_fn=lambda: clock[0],
                          sleep_fn=lambda s: clock.__setitem__(0, clock[0] + s)), clock[0] - start
        finally:
            tick._loop_holders = orig

    def test_sleep_wakes_on_time(self):
        out, slept = self._fake_sleep(time.time())
        self.assertTrue(out.startswith("WAKE: check loop tick. Read "))
        self.assertIn(slept, (1800, 3600))

    def test_sleep_wakes_early_for_a_promise(self):
        now = time.time()
        out, slept = self._fake_sleep(now, wake_at=now + 300)
        self.assertTrue(out.startswith("WAKE: early"))
        self.assertIn("SEAT WAKE:", out)
        self.assertIn("call back the vendor", out)
        self.assertLessEqual(slept, 330)

    def test_main_refuses_outside_a_seat(self):
        with _helpers.tempfile.TemporaryDirectory() as d:
            self.assertEqual(tick.main(["--seat", d, "begin"]), 2)
            self.assertFalse((pathlib.Path(d) / "state").exists())


if __name__ == "__main__":
    unittest.main()
