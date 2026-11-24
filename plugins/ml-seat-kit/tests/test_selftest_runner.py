"""0.1.7: the self tests run through the kit's own runner (scripts/selftest_runner.py). It prints a START and a DONE
line around every test and otherwise exactly what `unittest discover -v` prints; run_suite reads it as it comes,
prints a progress line about every 15 seconds, keeps each test's seconds in the file, and stops a run that passes a
limit (per test, in all, before the first test) and names what was running. Made-up suites stand in for the kit's
own, with limits of a few seconds passed as parameters, so nothing here waits two minutes."""
import argparse
import importlib
import io
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout

from tests import _helpers
from tests.test_adopt import AdoptBase
from tests.test_selftest import (FAKE_SUITE, FIXTURE_SUITE, PASSING_SUITE, SUBSKIP_SUITE, fake_kit, with_noise)
import seat_setup as s

MARK = re.compile(r"^SEAT-KIT-TEST-")
load_tests = _helpers.load_tests     # the build-only classes and tests below are not loaded outside a release build

CLASSES_SUITE = '''import unittest


class A_Broken(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raise RuntimeError("class set-up broke")

    def test_never(self):
        pass


class B_Skipped(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raise unittest.SkipTest("not on this machine")

    def test_never_either(self):
        pass


class C_Kinds(unittest.TestCase):
    @unittest.expectedFailure
    def test_expected(self):
        self.assertEqual(1, 2)

    @unittest.expectedFailure
    def test_unexpected(self):
        pass

    def test_sub_error(self):
        for k in ("x", "y"):
            with self.subTest(k):
                if k == "y":
                    raise ValueError("bad y")

    def test_sub_skip(self):
        with self.subTest("z"):
            self.skipTest("nor here")

    def test_ok(self):
        pass
'''

SECOND_FILE = '''import time
import unittest


class Slow(unittest.TestCase):
    def test_slower(self):
        time.sleep(0.6)

    def test_slow(self):
        time.sleep(0.3)
'''

FIRST_FILE = '''import time
import unittest


class Quick(unittest.TestCase):
    def test_quick(self):
        time.sleep(0.1)

    def test_quicker(self):
        pass
'''

# a test that starts a child (it keeps the runner's own stdout and stderr, as a careless test's child would), writes
# the child's pid, and then hangs; the child writes a heartbeat until it is ended (or for one minute at most: run_suite
# never waits for it, and the tests that use it end it themselves when it is still running)
BEAT = "import sys, time\nfor _ in range(300):\n    open(sys.argv[1], 'a').write('x')\n    time.sleep(0.2)\n"
HUNG_SUITE = '''import os
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BEAT = %r


class A_Before(unittest.TestCase):
    def test_1(self):
        pass

    def test_2(self):
        pass


class B_Hung(unittest.TestCase):
    def test_hangs(self):
        p = subprocess.Popen([sys.executable, "-I", "-c", BEAT, os.path.join(HERE, "beat.txt")])
        with open(os.path.join(HERE, "child.pid"), "w") as f:
            f.write(str(p.pid))
        time.sleep(120)


class C_After(unittest.TestCase):
    def test_3(self):
        pass
''' % BEAT

LONGER_SUITE = '''import time
import unittest


class Long(unittest.TestCase):
    def test_takes_its_time(self):
        time.sleep(12)
    test_takes_its_time.selftest_seconds = 60


class LongClass(unittest.TestCase):
    selftest_seconds = 60

    def test_also(self):
        time.sleep(12)
'''

MANY_SUITE = "import time\nimport unittest\n\n\nclass Many(unittest.TestCase):\n" + "".join(
    "    def test_%02d(self):\n        time.sleep(0.5)\n\n" % i for i in range(60))

IMPORT_HANG = "import time\nimport unittest\n\ntime.sleep(120)\n\n\nclass Never(unittest.TestCase):\n" \
              "    def test_x(self):\n        pass\n"

SETUP_HANG = '''import time
import unittest


class Fine(unittest.TestCase):
    def test_fine(self):
        pass


class SlowSetUp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        time.sleep(120)

    def test_after_set_up(self):
        pass
'''

# a passing test that leaves a child holding the runner's stdout and stderr for LEFTOVER seconds, writing its pid first
LEFTOVER_SUITE = '''import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class Leaves(unittest.TestCase):
    def test_leaves_a_child(self):
        p = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(%d)"], stdout=sys.stdout,
                             stderr=sys.stderr, stdin=subprocess.DEVNULL)
        with open(os.path.join(HERE, "child.pid"), "w") as f:
            f.write(str(p.pid))
'''

# A's tear-down is slow (the stop must name it, under the larger of A's and B's limits); the last class's too
SLOW_TEARDOWN = '''import time
import unittest


class A(unittest.TestCase):
    %s
    @classmethod
    def tearDownClass(cls):
        time.sleep(%d)

    def test_a(self):
        pass


class B(unittest.TestCase):
    def test_b(self):
        pass
'''

SLOW_LAST_TEARDOWN = '''import time
import unittest


class Only(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        time.sleep(60)

    def test_only(self):
        pass
'''

# a tear-down that leaves a line open on stderr, and a test that does the same: no mark may be merged into it
PARTIAL_LINES = '''import sys
import unittest


class A(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        sys.stderr.write("partial")

    def test_a(self):
        pass


class B(unittest.TestCase):
    def test_b(self):
        sys.stderr.write("half")
'''

# every test passes, and then the runner never ends: something hangs at its exit, after unittest's summary
HANG_AT_EXIT = '''import atexit
import time
import unittest

atexit.register(time.sleep, 120)


class Fine(unittest.TestCase):
    def test_fine(self):
        pass
'''


def plain(kit):
    """`python -I -m unittest discover -v`, exactly as run_suite ran the tests before 0.1.7."""
    r = subprocess.run([sys.executable, "-I", "-m", "unittest", "discover", "-v", "-s", str(kit / "tests"), "-t",
                        str(kit)], capture_output=True, text=True, errors="replace", cwd=str(kit),
                       stdin=subprocess.DEVNULL, timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def comparable(text):
    """Output without the runner's own lines, and without the time unittest measured."""
    lines = [ln for ln in text.splitlines() if not MARK.match(ln)]
    return re.sub(r"^(Ran \d+ tests?) in [\d.]+s$", r"\1", "\n".join(lines), flags=re.M)


def marks(text, kind):
    return [ln for ln in text.splitlines() if ln.startswith("SEAT-KIT-TEST-%s " % kind)]


def alive(pid, wait_ms=5000):
    """True while the process with this pid runs, still after wait_ms (it is not opened for anything but waiting)."""
    if os.name != "nt":
        return False
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x00100000, False, int(pid))   # SYNCHRONIZE
    if not h:
        return False
    try:
        return k32.WaitForSingleObject(ctypes.c_void_p(h), int(wait_ms)) != 0
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = _helpers.real(self.td) / "with space"
        self.kit_home = self.base / "kit home"
        self.kit_home.mkdir(parents=True)
        self.n = 0
        self.pids = []        # children a test's made-up suite left running: ended before the folder is removed
        s._LINE.update(open="", broken="")

    def tearDown(self):
        s._LINE.update(open="", broken="")
        for pid in self.pids:
            end_pid(pid)
        self.td.cleanup()

    def kit(self, source, **more):
        self.n += 1
        kit = fake_kit(self.base / ("suite %d" % self.n), source)
        for name, text in more.items():
            (kit / "tests" / ("%s.py" % name)).write_text(text, encoding="utf-8")
        return kit


def end_pid(pid):
    """End one process a test here started itself (never a tree), when it still runs."""
    if pid and alive(pid, 0):
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=60)
        alive(pid, 10000)     # wait (up to 10 s) until it is gone, so its working folder can be removed


NO_JOB = {"SEAT_KIT_TEST_NO_JOB": "1"}   # the runner leaves its job object out: what a PC without one does


class Runner(Base):
    """The runner's own lines, and that everything else is what unittest -v prints."""

    def test_start_and_done_lines_name_every_test_and_its_outcome(self):
        res = s.run_suite(None, kit=self.kit(FAKE_SUITE))
        out = res["output"]
        names = ["test_a_fails", "test_b_errors", "test_c_fails_docstring", "test_d_fails", "test_e_passes",
                 "test_f_skipped"]
        self.assertEqual(marks(out, "START"), ["SEAT-KIT-TEST-START %d/6 tests.test_fake.Fake.%s" % (i + 1, n)
                                               for i, n in enumerate(names)])
        done = [re.sub(r" \d+\.\d\ds ", " T ", ln) for ln in marks(out, "DONE")]
        self.assertEqual(done, ["SEAT-KIT-TEST-DONE %d/6 %s T tests.test_fake.Fake.%s" % (i + 1, o, n) for i, (o, n)
                                in enumerate(zip(("FAIL", "ERROR", "FAIL", "FAIL", "ok", "skipped"), names))])
        self.assertEqual(marks(out, "FOUND"), ["SEAT-KIT-TEST-FOUND 6"])
        self.assertIn("SEAT-KIT-TEST-END", out.splitlines())
        self.assertEqual([t[0] for t in res["times"]], ["tests.test_fake.Fake.%s" % n for n in names])
        self.assertIsNone(res["stopped"])
        self.assertEqual(res["returncode"], 1)

    def test_class_set_up_errors_and_skips_and_the_other_outcomes(self):
        res = s.run_suite(None, kit=self.kit(CLASSES_SUITE))
        out = res["output"]
        done = [s._DONE.match(ln[len("SEAT-KIT-TEST-DONE "):]) for ln in marks(out, "DONE")]
        got = {d.group(5).rsplit(".", 1)[1]: d.group(3) for d in done}
        self.assertEqual(got, {"test_expected": "expected failure", "test_unexpected": "unexpected success",
                               "test_sub_error": "ERROR", "test_sub_skip": "skipped", "test_ok": "ok"})
        # the two classes whose set-up failed or skipped start no test; each next class's set-up is announced, with no
        # "after": unittest never runs the tear-down of a class whose set-up failed or skipped
        self.assertEqual([ln[len("SEAT-KIT-TEST-SETUP "):] for ln in marks(out, "SETUP")],
                         ["1/7 tests.test_fake.A_Broken", "2/7 tests.test_fake.B_Skipped", "3/7 tests.test_fake.C_Kinds"])
        self.assertEqual(marks(out, "TEARDOWN"), ["SEAT-KIT-TEST-TEARDOWN 7/7 tests.test_fake.C_Kinds"])
        self.assertIn("ERROR: setUpClass (tests.test_fake.A_Broken)", out)
        self.assertFalse(res["ok"])

    def test_the_parse_is_the_same_as_plain_unittest_on_the_same_suite(self):
        mw = ["AppData/Local/Microsoft", "AppData/Local/Malwarebytes"]
        for name, source in (("fake", FAKE_SUITE), ("passing", PASSING_SUITE), ("fixture", FIXTURE_SUITE),
                             ("subskip", SUBSKIP_SUITE), ("classes", CLASSES_SUITE),
                             ("noise", with_noise(FAKE_SUITE, mw)), ("noise passing", with_noise(PASSING_SUITE, []))):
            with self.subTest(name):
                kit = self.kit(source)
                rc, before = plain(kit)
                res = s.run_suite(None, kit=kit)
                self.assertEqual(res["returncode"], rc)
                want = s.parse_unittest(before)
                got = s.parse_unittest(res["output"])
                self.assertEqual(got, want)
                self.assertIsNotNone(want["ran"])
                self.assertEqual(s.tests_line(got), s.tests_line(want))
                self.assertEqual(s.failed_names(got), s.failed_names(want))
                # everything plain unittest printed is there, in the same order: only the runner's lines are added
                self.assertEqual(comparable(res["output"]), comparable(before))
                self.assertEqual(len(marks(res["output"], "START")), sum(1 for ln in before.splitlines()
                                                                         if re.match(r"^test\w* \(.*\)", ln)))
        self.assertEqual(s.parse_noise(s.run_suite(None, kit=self.kit(with_noise(PASSING_SUITE, mw)))["output"]), mw)
    # seven plain unittest runs x 120 s = 840 s (its run_suite calls have only run_suite's own limits)
    test_the_parse_is_the_same_as_plain_unittest_on_the_same_suite.selftest_seconds = 840

    def test_a_skip_reason_with_an_apostrophe_is_counted_and_named(self):
        """unittest prints such a reason in double quotes (the short-TEMP test's "this PC's TEMP ..."); before 0.1.7
        the parse missed it, and next to a single-quoted skip the Tests line counted it as passed."""
        source = ("import unittest\n\n\nclass Two(unittest.TestCase):\n    def test_a(self):\n"
                  "        self.skipTest(\"this PC's TEMP is not short\")\n\n    def test_b(self):\n"
                  "        self.skipTest('not here')\n\n    def test_c(self):\n        pass\n")
        res = s.run_suite(None, kit=self.kit(source))
        self.assertEqual(res["skips"], [("Two.test_a", "this PC's TEMP is not short"), ("Two.test_b", "not here")])
        self.assertEqual(s.tests_line(res), "Tests:        3 ran, 1 passed, 0 failed, 2 skipped")

    def test_a_skip_reason_with_both_quote_kinds_and_a_backslash_is_read_back_exactly(self):
        """unittest prints such a reason in single quotes with \\' inside; it is read back as the test gave it."""
        reasons = ["it's \"both\"", "a\\b it's", "plain"]
        source = "import unittest\n\n\nclass Q(unittest.TestCase):\n" + "".join(
            "    def test_%d(self):\n        self.skipTest(%r)\n\n" % (i, r) for i, r in enumerate(reasons))
        res = s.run_suite(None, kit=self.kit(source))
        self.assertEqual(res["skips"], [("Q.test_%d" % i, r) for i, r in enumerate(reasons)])
        self.assertEqual(s.tests_line(res), "Tests:        3 ran, 0 passed, 0 failed, 3 skipped")
        text = "test_x (t.K.test_x) ... skipped 'it\\'s \"both\"'\n\nRan 1 test in 0.0s\n\nOK (skipped=1)\n"
        self.assertEqual(s.parse_unittest(text)["skips"], [("K.test_x", "it's \"both\"")])

    def test_a_mark_never_merges_into_a_line_a_test_left_open(self):
        res = s.run_suite(None, kit=self.kit(PARTIAL_LINES))
        lines = res["output"].splitlines()
        self.assertIn("partial", lines)                     # the open line was ended before the next mark
        self.assertIn("SEAT-KIT-TEST-SETUP 2/2 tests.test_fake.B after tests.test_fake.A", lines)
        self.assertEqual([t[0] for t in res["times"]], ["tests.test_fake.A.test_a", "tests.test_fake.B.test_b"])
        self.assertFalse([ln for ln in lines if "SEAT-KIT-TEST-" in ln and not ln.startswith("SEAT-KIT-TEST-")], lines)
        self.assertTrue(res["ok"], res["output"])
        self.assertIn("SEAT-KIT-TEST-END", lines)

    def test_order_and_count_equal_unittest_discover_over_two_files(self):
        kit = self.kit(FIRST_FILE, test_more=SECOND_FILE)
        want = []
        for ln in plain(kit)[1].splitlines():           # "test_x (tests.m.K.test_x) ... ok"; before 3.11 "(tests.m.K)"
            m = re.match(r"^(test\w*) \(([\w.]+)\)", ln)
            if m:
                want.append(m.group(2) if m.group(2).endswith("." + m.group(1)) else "%s.%s" % (m.group(2), m.group(1)))
        res = s.run_suite(None, kit=kit)
        self.assertEqual(len(want), 4)
        self.assertEqual([ln.split()[-1] for ln in marks(res["output"], "START")], want)
        self.assertEqual([t[0] for t in res["times"]], want)
        self.assertEqual(res["ran"], len(want))


class Watch(unittest.TestCase):
    """Progress lines from the runner's lines and an injected clock: no real waiting."""

    def run_watch(self, events, until, **limits):
        """events: [(seconds, line)]; ticks once a second like the real loop. Returns [(second, progress line)] and
        the stop line."""
        w = s.SuiteWatch(1000.0, **limits)
        said, stop = [], None
        events = sorted(events, key=lambda e: e[0])    # stable: lines at the same second keep their order
        for t in range(until + 1):
            while events and events[0][0] <= t:
                w.line(events.pop(0)[1] + "\n", 1000.0 + t)
            line, stop = w.tick(1000.0 + t)
            if stop:
                return said, stop, w
            if line:
                said.append((t, line))
        return said, stop, w

    def steady(self, n, each):
        """n tests of `each` seconds, one class."""
        ev = [(0, "SEAT-KIT-TEST-JOB armed"), (1, "SEAT-KIT-TEST-FOUND %d" % n), (1, "SEAT-KIT-TEST-SETUP 1/%d K" % n)]
        t = 1
        for i in range(n):
            ev.append((t, "SEAT-KIT-TEST-START %d/%d tests.test_m.K.test_%d" % (i + 1, n, i)))
            t += each
            ev.append((t, "SEAT-KIT-TEST-DONE %d/%d ok %.2fs tests.test_m.K.test_%d" % (i + 1, n, each, i)))
        ev.append((t, "SEAT-KIT-TEST-END"))
        return ev, t

    def test_a_line_about_every_15_seconds_and_none_for_a_quick_suite(self):
        ev, end = self.steady(224, 1)
        said, stop, w = self.run_watch(ev, 200)
        self.assertIsNone(stop)
        self.assertEqual([t for t, _ in said], list(range(15, 201, 15)))
        self.assertEqual(said[0][1], "   still running: test 15 of 224, 15 s so far")
        self.assertEqual(said[-1][1], "   still running: test 195 of 224, 3 min 15 s so far")
        self.assertEqual(len(w.times), 199)
        quick, _ = self.steady(20, 0)
        said, stop, _ = self.run_watch(quick, 14)
        self.assertEqual((said, stop), ([], None))

    def test_a_test_running_over_30_seconds_is_named(self):
        ev = [(1, "SEAT-KIT-TEST-FOUND 224"), (1, "SEAT-KIT-TEST-START 1/224 tests.test_m.K.test_a"),
              (3, "SEAT-KIT-TEST-DONE 1/224 ok 2.00s tests.test_m.K.test_a"),
              (3, "SEAT-KIT-TEST-START 2/224 tests.test_m.K.test_b"),
              (5, "SEAT-KIT-TEST-DONE 2/224 ok 2.00s tests.test_m.K.test_b"),
              (5, "SEAT-KIT-TEST-SETUP 3/224 limit=900 tests.test_short_paths.WholeSuiteUnderShortTemp"),
              (5, "SEAT-KIT-TEST-START 3/224 limit=900 tests.test_short_paths.WholeSuiteUnderShortTemp."
                  "test_the_whole_suite_passes_with_a_short_form_temp")]
        said, stop, _ = self.run_watch(ev, 250)
        self.assertIsNone(stop)                                    # its own limit is 900 seconds
        lines = dict(said)
        self.assertEqual(lines[30], "   still running: test 3 of 224, 30 s so far")      # 25 s: not named yet
        self.assertEqual(lines[45], "   still running: test 3 of 224 (WholeSuiteUnderShortTemp.test_the_whole_suite_"
                                    "passes_with_a_short_form_temp) has taken 40 s; 45 s so far")
        self.assertEqual(lines[240], "   still running: test 3 of 224 (WholeSuiteUnderShortTemp.test_the_whole_suite_"
                                     "passes_with_a_short_form_temp) has taken 3 min 55 s; 4 min 0 s so far")
        for _, ln in said:
            self.assertNotIn("\r", ln)

    def test_before_the_first_test_and_in_a_class_set_up(self):
        said, stop, _ = self.run_watch([(0, "SEAT-KIT-TEST-JOB armed")], 31)
        self.assertEqual([ln for _, ln in said], ["   still running: finding the tests, 15 s so far",
                                                   "   still running: finding the tests, 30 s so far"])
        said, stop, _ = self.run_watch([(1, "SEAT-KIT-TEST-FOUND 9"), (2, "SEAT-KIT-TEST-SETUP 4/9 tests.test_m.Slow")],
                                       46)
        self.assertEqual(dict(said)[30], "   still running: the set-up before test 4 of 9, 30 s so far")
        self.assertEqual(dict(said)[45], "   still running: the set-up before test 4 of 9 (Slow) has taken 43 s; "
                                         "45 s so far")

    def test_each_limit_and_its_line(self):
        start = "SEAT-KIT-TEST-START 37/224 tests.test_x.Klass.test_y"
        ev = [(1, "SEAT-KIT-TEST-FOUND 224")] + [(1, "SEAT-KIT-TEST-DONE %d/224 ok 0.01s tests.test_x.K.t%d" % (i, i))
                                                  for i in range(1, 37)] + [(2, start)]
        _, stop, w = self.run_watch(ev, 400)
        self.assertEqual(stop, "The self tests were stopped: tests.test_x.Klass.test_y ran longer than 300 seconds "
                               "(test 37 of 224). 36 finished before it.")
        self.assertEqual(w.limits["tests.test_x.Klass.test_y"], 300)
        _, stop, w = self.run_watch(ev[:-1] + [(2, "SEAT-KIT-TEST-START 37/224 limit=5000 tests.test_x.Klass.test_y")],
                                    3700, total=3600)
        self.assertEqual(stop, "The self tests were stopped after 60 minutes: 36 of 224 had finished; the one running "
                               "was tests.test_x.Klass.test_y (test 37 of 224).")
        self.assertEqual(w.limits["tests.test_x.Klass.test_y"], 5000)
        _, stop, _ = self.run_watch([(0, "SEAT-KIT-TEST-JOB armed")], 400)
        self.assertEqual(stop, "The self tests were stopped: finding and loading the tests ran longer than 300 "
                               "seconds, before any test started.")
        _, stop, _ = self.run_watch([(0, "SEAT-KIT-TEST-JOB armed")], 400, total=60)
        self.assertEqual(stop, "The self tests were stopped after 60 seconds: they were still being found and loaded; "
                               "no test had started.")
        _, stop, _ = self.run_watch([(1, "SEAT-KIT-TEST-FOUND 9"), (2, "SEAT-KIT-TEST-SETUP 4/9 tests.test_m.Slow")],
                                    400, per_test=7)
        self.assertEqual(stop, "The self tests were stopped: the set-up before test 4 of 9 (class tests.test_m.Slow) "
                               "ran longer than 7 seconds. 0 finished before it.")
        _, stop, _ = self.run_watch([(1, "SEAT-KIT-TEST-FOUND 1"), (1, "SEAT-KIT-TEST-START 1/1 t.m.K.a"),
                                     (2, "SEAT-KIT-TEST-DONE 1/1 ok 1.00s t.m.K.a"), (2, "SEAT-KIT-TEST-END")],
                                    400, per_test=7)
        self.assertEqual(stop, "The self tests were stopped: the end of the run, after its last test, ran longer than "
                               "7 seconds. 1 of 1 had finished.")

    def test_a_tear_down_then_set_up_has_its_own_words_and_the_larger_limit(self):
        head = [(1, "SEAT-KIT-TEST-FOUND 9"), (1, "SEAT-KIT-TEST-DONE 3/9 ok 1.00s tests.test_m.A.test_c")]
        _, stop, _ = self.run_watch(head + [(2, "SEAT-KIT-TEST-SETUP 4/9 tests.test_m.B after tests.test_m.A")], 400,
                                    per_test=7)
        self.assertEqual(stop, "The self tests were stopped: the tear-down of class tests.test_m.A, then the set-up "
                               "before test 4 of 9 (class tests.test_m.B), ran longer than 7 seconds. 1 finished before "
                               "it.")
        for line in ("SEAT-KIT-TEST-SETUP 4/9 tests.test_m.B after limit=60 tests.test_m.A",
                     "SEAT-KIT-TEST-SETUP 4/9 limit=60 tests.test_m.B after tests.test_m.A"):
            said, stop, _ = self.run_watch(head + [(2, line)], 400, per_test=7)
            self.assertEqual(stop, "The self tests were stopped: the tear-down of class tests.test_m.A, then the set-up "
                                   "before test 4 of 9 (class tests.test_m.B), ran longer than 60 seconds. 1 finished "
                                   "before it.")
            self.assertEqual(dict(said)[45], "   still running: the tear-down of A and the set-up before test 4 of 9 "
                                             "(B) has taken 43 s; 45 s so far")
        _, stop, _ = self.run_watch(head + [(2, "SEAT-KIT-TEST-SETUP 4/9 limit=5 tests.test_m.B after limit=6 "
                                                "tests.test_m.A")], 400, per_test=7)
        self.assertIn("ran longer than 6 seconds", stop)        # the larger of the two classes' own limits
        _, stop, _ = self.run_watch([(1, "SEAT-KIT-TEST-FOUND 9"),
                                     (1, "SEAT-KIT-TEST-DONE 9/9 ok 1.00s tests.test_m.Z.test_z"),
                                     (2, "SEAT-KIT-TEST-TEARDOWN 9/9 tests.test_m.Z")], 400, per_test=7)
        self.assertEqual(stop, "The self tests were stopped: the tear-down of class tests.test_m.Z, after its last test "
                               "(test 9 of 9), ran longer than 7 seconds. 1 of 9 had finished.")

    def test_the_slow_note_once_after_ten_minutes_never_before_never_twice(self):
        w = s.SuiteWatch(1000.0)
        self.assertEqual(s.SLOW_NOTE_SECONDS, 600)
        self.assertEqual(s.SLOW_NOTE, "   this PC is running the checks slowly, which is expected; if one check is "
                                      "stuck, the run stops by itself and names it")
        self.assertIsNone(w.note(1000.0 + 599))
        self.assertEqual(w.note(1000.0 + 600), s.SLOW_NOTE)
        self.assertIsNone(w.note(1000.0 + 615))
        self.assertIsNone(w.note(1000.0 + 5000))

    def test_lines_after_the_end_are_never_read_as_marks(self):
        w = s.SuiteWatch(0.0)
        for ln in ("SEAT-KIT-TEST-FOUND 1", "SEAT-KIT-TEST-START 1/1 a.b.C.d", "SEAT-KIT-TEST-DONE 1/1 FAIL 2.00s a.b.C.d",
                   "SEAT-KIT-TEST-END", "SEAT-KIT-TEST-START 1/9 inner.quoted.K.t",
                   "SEAT-KIT-TEST-DONE 1/9 ok 9.00s inner.quoted.K.t"):
            w.line(ln + "\n", 1.0)
        self.assertEqual(w.times, [("a.b.C.d", 2.0, "FAIL")])
        self.assertEqual(w.phase["kind"], "end")


class FakeChild:
    """A child that feeds the runner's lines slowly (real short sleeps; the injected clock runs 20 times faster)."""

    def __init__(self, lines, pause, hold=None, ends=True):
        """hold: a threading.Event; when given, stderr stays open after the last line until it is set (a leftover
        holding the pipe). ends: the child ends (exit code 0) after its last line; when False it never ends by
        itself, and kill() is then what ends it."""
        self.lines, self.pause, self.returncode, self.drained = lines, pause, None, False
        me = self

        class Err:
            def __init__(self):
                self.left = list(lines)

            def readline(self):
                if not self.left:
                    me.drained = True
                    if ends:
                        me.returncode = 0
                    if hold is not None:
                        hold.wait(120)
                    return ""
                time.sleep(me.pause(self.left[0]))
                return self.left.pop(0) + "\n"

            def close(self):
                pass

        class Out:
            def readline(self):
                return ""

            def close(self):
                pass
        self.stderr, self.stdout = Err(), Out()

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = 1

    def wait(self, timeout=None):
        return self.returncode


class ProgressOnTheConsole(Base):
    def fake_run(self, lines, pause, speed=20, **k):
        t0 = time.monotonic()
        said = []
        res = s.run_suite(None, kit=self.base, progress=said.append, clock=lambda: (time.monotonic() - t0) * speed,
                          popen=lambda *a, **kw: FakeChild(lines, pause), **k)
        return res, said

    @_helpers.build_only     # real waits (3.5 s) on a clock 20 times faster
    def test_a_slow_child_gets_progress_lines_and_its_long_test_is_named(self):
        lines = ["SEAT-KIT-TEST-FOUND 3", "SEAT-KIT-TEST-SETUP 1/3 tests.test_m.K",
                 "SEAT-KIT-TEST-START 1/3 tests.test_m.K.test_a", "SEAT-KIT-TEST-DONE 1/3 ok 1.00s tests.test_m.K.test_a",
                 "SEAT-KIT-TEST-START 2/3 tests.test_m.K.test_long",
                 "SEAT-KIT-TEST-DONE 2/3 ok 60.00s tests.test_m.K.test_long", "SEAT-KIT-TEST-END", "Ran 3 tests in 1s",
                 "", "OK"]
        # 3.5 real seconds before test_long's DONE line: 70 seconds on the injected clock
        res, said = self.fake_run(lines, lambda ln: 3.5 if "DONE 2/3" in ln else 0.0)
        self.assertIsNone(res["stopped"], res["output"])
        self.assertTrue(res["ok"], res["output"])
        self.assertGreaterEqual(len(said), 3, said)
        for ln in said:
            self.assertTrue(ln.startswith("   still running: test 2 of 3"), said)
            self.assertNotIn("\r", ln)
        self.assertTrue(any(ln.startswith("   still running: test 2 of 3 (K.test_long) has taken ") for ln in said),
                        said)

    def test_a_quick_child_prints_nothing(self):
        res, said = self.fake_run(["SEAT-KIT-TEST-FOUND 1", "SEAT-KIT-TEST-START 1/1 t.m.K.a",
                                   "SEAT-KIT-TEST-DONE 1/1 ok 0.01s t.m.K.a", "SEAT-KIT-TEST-END", "Ran 1 test in 0s",
                                   "", "OK"], lambda ln: 0.0, speed=1)     # the real clock: a slow PC is still quick
        self.assertEqual(said, [])
        self.assertTrue(res["ok"])

    LONG_RUN = ["SEAT-KIT-TEST-FOUND 2", "SEAT-KIT-TEST-SETUP 1/2 tests.test_m.K",
                "SEAT-KIT-TEST-START 1/2 limit=5000 tests.test_m.K.test_long",
                "SEAT-KIT-TEST-DONE 1/2 ok 1500.00s tests.test_m.K.test_long",
                "SEAT-KIT-TEST-START 2/2 limit=5000 tests.test_m.K.test_longer",
                "SEAT-KIT-TEST-DONE 2/2 ok 1500.00s tests.test_m.K.test_longer", "SEAT-KIT-TEST-TEARDOWN 2/2 tests.test_m.K",
                "SEAT-KIT-TEST-END", "Ran 2 tests in 3000s", "", "OK"]

    @_helpers.build_only     # about 7.5 real seconds of a fake 25 minute run
    def test_the_slow_note_comes_once_right_after_a_progress_line_past_ten_minutes(self):
        """A fake run of about 25 minutes on a clock 200 times faster (about 7.5 real seconds)."""
        t0 = time.monotonic()
        said = []
        res = s.run_suite(None, kit=self.base, progress=said.append, clock=lambda: (time.monotonic() - t0) * 200,
                          popen=lambda *a, **kw: FakeChild(self.LONG_RUN, lambda ln: 3.75 if "-DONE " in ln else 0.0))
        self.assertTrue(res["ok"], res["output"])
        self.assertEqual(said.count(s.SLOW_NOTE), 1, said)
        i = said.index(s.SLOW_NOTE)
        self.assertTrue(said[i - 1].startswith("   still running: "), said)
        before = re.search(r"[;,] (?:(\d+) min )?(\d+) s so far$", said[i - 1])
        self.assertGreaterEqual(int(before.group(1) or 0) * 60 + int(before.group(2)), 600, said)
        for ln in said[:i - 1]:                              # every earlier line was before the ten minutes
            m = re.search(r"[;,] (?:(\d+) min )?(\d+) s so far$", ln)
            self.assertLess(int(m.group(1) or 0) * 60 + int(m.group(2)), 600, said)
        self.assertGreater(len(said) - i - 1, 0, "progress lines go on after the note")
        # with no progress callable (the silent inner run, the tests' own runs) nothing is printed, the note neither
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = s.run_suite(None, kit=self.base, progress=None, clock=lambda: (time.monotonic() - t0) * 200 + 5000,
                              popen=lambda *a, **kw: FakeChild(self.LONG_RUN, lambda ln: 0.0))
        self.assertTrue(res["ok"], res["output"])
        self.assertEqual(buf.getvalue(), "")

    def test_the_slow_note_keeps_the_step_line_and_the_parse(self):
        kit, real = self.kit(PASSING_SUITE), s.run_suite

        def slow(kit_home=None, **k):
            k["progress"]("   still running: test 2 of 3, 10 min 0 s so far")
            k["progress"](s.SLOW_NOTE)
            k["progress"]("   still running: test 2 of 3, 10 min 15 s so far")
            return real(kit_home, kit=kit)
        s.run_suite = slow
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = s.main(["selftest", "--kit-home", str(self.kit_home)])
        finally:
            s.run_suite = real
        lines = buf.getvalue().splitlines()
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertEqual(lines[:5], ["Step 4 of 6: running the kit's self tests only (this takes several minutes; longer "
                                     "on a PC where security software checks each step) ... ",
                                     "   still running: test 2 of 3, 10 min 0 s so far", s.SLOW_NOTE,
                                     "   still running: test 2 of 3, 10 min 15 s so far", "Step 4 of 6: all passed"])
        self.assertIn("Tests:        3 ran, 2 passed, 0 failed, 1 skipped", lines)

    def test_the_step_line_is_closed_and_the_result_has_its_own_clear_line(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            s.say("Step 4 of 6: running the kit's self tests only (%s) ... " % s.SELFTEST_TAKES, end="")
            s.say_progress("   still running: test 120 of 224, 3 min 10 s so far")
            s.say_progress("   still running: test 140 of 224, 3 min 25 s so far")
            s.say("all passed")
            s.say("")
            s.say("Self test only: ...")
        self.assertEqual(buf.getvalue().splitlines(), [
            "Step 4 of 6: running the kit's self tests only (this takes several minutes; longer on a PC where security "
            "software checks each step) ... ",
            "   still running: test 120 of 224, 3 min 10 s so far", "   still running: test 140 of 224, 3 min 25 s so far",
            "Step 4 of 6: all passed", "", "Self test only: ..."])
        buf = io.StringIO()
        with redirect_stdout(buf):                       # no progress: one line, as before 0.1.7
            s.say("Step 4 of 6: running ... ", end="")
            s.say("all passed")
        self.assertEqual(buf.getvalue(), "Step 4 of 6: running ... all passed\n")

    def test_selftest_prints_the_progress_between_its_step_line_and_the_result(self):
        kit, real = self.kit(PASSING_SUITE), s.run_suite

        def slow(kit_home=None, **k):
            k["progress"]("   still running: test 2 of 3, 15 s so far")
            return real(kit_home, kit=kit)
        s.run_suite = slow
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = s.main(["selftest", "--kit-home", str(self.kit_home)])
        finally:
            s.run_suite = real
        lines = buf.getvalue().splitlines()
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertEqual(lines[:3], ["Step 4 of 6: running the kit's self tests only (this takes several minutes; longer "
                                     "on a PC where security software checks each step) ... ",
                                     "   still running: test 2 of 3, 15 s so far", "Step 4 of 6: all passed"])
        self.assertIn("Tests:        3 ran, 2 passed, 0 failed, 1 skipped", lines)


@_helpers.build_only     # real waits for the pipes (PIPE_GRACE) after the fake runner ends
class AtTheLimitsInstant(Base):
    """The race between a limit passing and the runner ending, made deterministic with fakes: the clock jumps past
    every limit only once the fake runner has written its last line AND the watch has read every line it wrote (the
    watch is wrapped to count them), so the watch's state at the jump is always the same."""

    PASSED = ["SEAT-KIT-TEST-FOUND 1", "SEAT-KIT-TEST-SETUP 1/1 t.m.K", "SEAT-KIT-TEST-START 1/1 t.m.K.a",
              "test_a (t.m.K.a) ... ok", "SEAT-KIT-TEST-DONE 1/1 ok 0.01s t.m.K.a", "SEAT-KIT-TEST-TEARDOWN 1/1 t.m.K",
              "SEAT-KIT-TEST-END", "", "-" * 70, "Ran 1 test in 0.010s", "", "OK"]

    def run_fake(self, child, grace):
        hold = child.hold
        self.addCleanup(hold.set)
        fed = []
        real = s.SuiteWatch

        class Counted(real):
            def line(self, text, now):
                fed.append(text)
                return real.line(self, text, now)
        s.SuiteWatch = Counted
        self.addCleanup(setattr, s, "SuiteWatch", real)
        t0 = time.monotonic()
        res = s.run_suite(None, kit=self.base, popen=lambda *a, **kw: child, grace=grace,
                          clock=lambda: 10.0 ** 6 if child.drained and len(fed) == len(child.lines) else 0.0)
        return res, time.monotonic() - t0

    def test_a_runner_that_had_ended_when_the_limit_passed_is_not_a_stop(self):
        import threading
        hold = threading.Event()
        child = FakeChild(self.PASSED, lambda ln: 0.0, hold=hold)      # ended (0); a leftover keeps stderr open
        child.hold = hold
        # the first look after it ended comes long before the 5 s grace, and the clock is then past every limit
        res, took = self.run_fake(child, grace=5)
        self.assertIsNone(res["stopped"], res["output"])
        self.assertEqual(res["returncode"], 0)
        self.assertTrue(res["ok"], res["output"])
        self.assertLess(took, 30)
        self.assertNotIn("stopped", res["output"])

    def test_a_kill_that_came_just_after_the_runner_ended_by_itself_is_not_a_stop(self):
        import threading
        hold = threading.Event()

        class EndedFirst(FakeChild):
            def kill(self):      # TerminateProcess found it already ended, with its own exit code 0
                self.returncode = 0
        child = EndedFirst(self.PASSED, lambda ln: 0.0, hold=hold, ends=False)
        child.hold = hold
        res, took = self.run_fake(child, grace=1)
        self.assertIsNone(res["stopped"], res["output"])
        self.assertEqual(res["returncode"], 0)
        self.assertTrue(res["ok"], res["output"])
        self.assertLess(took, 30)

    def test_a_runner_still_running_when_the_limit_passed_is_stopped(self):
        import threading
        hold = threading.Event()
        child = FakeChild(self.PASSED[:3], lambda ln: 0.0, hold=hold, ends=False)
        child.hold = hold
        res, took = self.run_fake(child, grace=1)
        self.assertIsNone(res["returncode"])
        self.assertEqual(res["stopped"], "The self tests were stopped after 180 minutes: 0 of 1 had finished; the one "
                                         "running was t.m.K.a (test 1 of 1).")
        self.assertEqual(s.TOTAL_SECONDS, 10800)
        self.assertFalse(res["ok"])


@_helpers.build_only     # real hung tests and leftover processes, limits of seconds
class Limits(Base):
    """Real made-up suites with limits of seconds."""

    def hung(self, env=None):
        """HUNG_SUITE with a 10 s per-test limit: (result, seconds run_suite took, the hung test's child's pid). The
        child is ended here when it still runs (it would end by itself within a minute), whatever the result."""
        kit = self.kit(HUNG_SUITE)
        t0 = time.monotonic()
        res = s.run_suite(self.kit_home, kit=kit, per_test=10, env=env)
        took = time.monotonic() - t0
        pid_file = kit / "tests" / "child.pid"
        pid = int(pid_file.read_text()) if pid_file.is_file() else None
        self.pids.append(pid)
        self.assertEqual(res["stopped"], "The self tests were stopped: tests.test_fake.B_Hung.test_hangs ran longer "
                                         "than 10 seconds (test 3 of 4). 2 finished before it.")
        # 10 s limit + at most 30 s for the runner to end + 5 s for the pipes; never the child's minute
        self.assertLess(took, 50)
        self.assertIsNone(res["returncode"])
        self.assertFalse(res["ok"])
        self.assertEqual(res["summary"], res["stopped"])
        self.assertEqual(s.tests_line(res), "Tests:        no count from unittest (%s)" % res["stopped"])
        self.assertEqual([t[0] for t in res["times"]], ["tests.test_fake.A_Before.test_1",
                                                        "tests.test_fake.A_Before.test_2"])
        self.assertIsNotNone(pid, "the hung test wrote no pid file")
        text = (self.kit_home / "selftest-last.txt").read_text(encoding="utf-8")
        self.assertTrue(text.endswith(res["output"] + res["timing"]))
        self.assertIn("Exit code: none (did not finish)", text)
        self.assertIn("SEAT-KIT-TEST-START 3/4 tests.test_fake.B_Hung.test_hangs", text)
        self.assertIn("\n%s\n" % res["stopped"], text)
        self.assertIn("Still running when the run was stopped: tests.test_fake.B_Hung.test_hangs, ", text)
        return res, took, pid, kit / "tests" / "beat.txt", text

    def test_a_hung_test_is_stopped_named_and_leaves_no_child_running(self):
        res, took, pid, beat, text = self.hung()
        if res["job"] != "armed":
            self.skipTest("no job object on this PC (%s): what the tests started is not ended" % res["job"])
        self.assertIn("Job object: armed (when the test runner's own process ends, Windows ends every program the tests "
                      "started and left running, unless one broke away from the job)", text)
        self.assertFalse(alive(pid), "the hung test's child %d is still running" % pid)
        size = beat.stat().st_size
        time.sleep(1.0)
        self.assertEqual(beat.stat().st_size, size, "the child's heartbeat still grows")

    def test_without_the_job_a_hung_test_is_stopped_and_its_child_holding_the_pipes_is_not_waited_for(self):
        res, took, pid, beat, text = self.hung(env=dict(os.environ, **NO_JOB))
        self.assertEqual(res["job"], "not armed (switched off by SEAT_KIT_TEST_NO_JOB=1)")
        self.assertIn("Job object: not armed (switched off by SEAT_KIT_TEST_NO_JOB=1) (programs the tests started and "
                      "left running are NOT ended with the run)", text)
        if os.name == "nt":
            self.assertTrue(alive(pid, 0), "the child should still run (and hold the pipes) without a job")

    def test_a_passing_run_whose_leftover_holds_the_pipes_is_passed_at_once(self):
        """Without the job (switched off here; a PC where it cannot be made), a test leaves a child holding the
        runner's stdout and stderr for 120 s. The runner itself ends after printing OK: run_suite returns passed,
        with exit code 0, a few seconds after that (PIPE_GRACE), not when the child lets go (within 60 s, room for a
        slow PC, and half of the leftover's time)."""
        kit = self.kit(LEFTOVER_SUITE % 120)
        t0 = time.monotonic()
        res = s.run_suite(self.kit_home, kit=kit, env=dict(os.environ, **NO_JOB))
        took = time.monotonic() - t0
        pid = int((kit / "tests" / "child.pid").read_text())
        self.pids.append(pid)
        self.assertIsNone(res["stopped"], res["output"])
        self.assertEqual(res["returncode"], 0, res["output"])
        self.assertTrue(res["ok"], res["output"])
        self.assertEqual(s.tests_line(res), "Tests:        1 ran, 1 passed, 0 failed, 0 skipped")
        self.assertLess(took, 60, "run_suite waited for the leftover")
        if os.name == "nt":
            self.assertTrue(alive(pid, 0), "the leftover had already ended: this proves nothing")
        self.assertIn("Exit code: 0", (self.kit_home / "selftest-last.txt").read_text(encoding="utf-8"))

    def test_a_leftover_that_never_ends_does_not_hang_run_suite_or_its_process(self):
        """The same with a leftover that would hold the pipes for ten minutes: run_suite returns, and a Python that
        called it ends too (nothing closes a pipe a reader thread is blocked on)."""
        kit = self.kit(LEFTOVER_SUITE % 600)
        probe = ("import sys\nsys.path.insert(0, sys.argv[1])\nimport seat_setup as s\n"
                 "res = s.run_suite(None, kit=sys.argv[2])\nprint('RESULT', res['returncode'], res['ok'], res['stopped'])\n")
        t0 = time.monotonic()
        r = subprocess.run([sys.executable, "-I", "-c", probe, str(_helpers.SCRIPTS), str(kit)], capture_output=True,
                           text=True, errors="replace", timeout=120, env=dict(os.environ, **NO_JOB),
                           stdin=subprocess.DEVNULL)
        took = time.monotonic() - t0
        pid_file = kit / "tests" / "child.pid"
        pid = int(pid_file.read_text()) if pid_file.is_file() else None
        self.pids.append(pid)
        self.assertIn("RESULT 0 True None", r.stdout, r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertLess(took, 60)
        if os.name == "nt":
            self.assertTrue(alive(pid, 0), "the leftover had already ended: this proves nothing")

    def test_a_slow_class_tear_down_is_named_as_such_under_the_larger_limit(self):
        res = s.run_suite(None, kit=self.kit(SLOW_TEARDOWN % ("", 60)), per_test=10)
        self.assertEqual(res["stopped"], "The self tests were stopped: the tear-down of class tests.test_fake.A, then "
                                         "the set-up before test 2 of 2 (class tests.test_fake.B), ran longer than 10 "
                                         "seconds. 1 finished before it.")
        # A asks for 60 s: its 12 s tear-down passes, although B and the run ask for only 10
        res = s.run_suite(None, kit=self.kit(SLOW_TEARDOWN % ("selftest_seconds = 60", 12)), per_test=10)
        self.assertIsNone(res["stopped"], res["output"])
        self.assertTrue(res["ok"], res["output"])
        self.assertIn("SEAT-KIT-TEST-SETUP 2/2 tests.test_fake.B after limit=60 tests.test_fake.A", res["output"])
        res = s.run_suite(None, kit=self.kit(SLOW_LAST_TEARDOWN), per_test=10)
        self.assertEqual(res["stopped"], "The self tests were stopped: the tear-down of class tests.test_fake.Only, "
                                         "after its last test (test 1 of 1), ran longer than 10 seconds. 1 of 1 had "
                                         "finished.")

    def test_a_test_with_its_own_longer_limit_is_allowed(self):
        res = s.run_suite(None, kit=self.kit(LONGER_SUITE), per_test=10)
        self.assertIsNone(res["stopped"], res["output"])
        self.assertTrue(res["ok"], res["output"])
        self.assertIn("SEAT-KIT-TEST-START 1/2 limit=60 tests.test_fake.Long.test_takes_its_time", res["output"])
        self.assertIn("SEAT-KIT-TEST-START 2/2 limit=60 tests.test_fake.LongClass.test_also", res["output"])

    def test_the_total_limit_stops_many_quick_tests_and_says_how_many_finished(self):
        res = s.run_suite(None, kit=self.kit(MANY_SUITE), total=20)     # 60 tests of 0.5 s: 30 s, more than 20
        m = re.match(r"^The self tests were stopped after 20 seconds: (\d+) of 60 had finished; the one running was "
                     r"tests\.test_fake\.Many\.test_(\d\d) \(test (\d+) of 60\)\.$", res["stopped"] or "")
        if m is None:      # stopped in the instant between one test's DONE line and the next one's START line
            m = re.match(r"^The self tests were stopped after 20 seconds: (\d+) of 60 had finished; the next was to "
                         r"start\.$", res["stopped"] or "")
            self.assertTrue(m, res["stopped"])
        else:
            self.assertEqual(int(m.group(3)), len(res["times"]) + 1)
            self.assertEqual(int(m.group(2)), len(res["times"]))
        self.assertEqual(int(m.group(1)), len(res["times"]))
        self.assertTrue(0 < len(res["times"]) < 60)
        self.assertIsNone(res["returncode"])

    def test_a_hang_before_the_first_test_is_caught(self):
        res = s.run_suite(None, kit=self.kit(IMPORT_HANG), before_first=4)
        self.assertEqual(res["stopped"], "The self tests were stopped: finding and loading the tests ran longer than "
                                         "4 seconds, before any test started.")
        res = s.run_suite(None, kit=self.kit(SETUP_HANG), per_test=10)
        self.assertEqual(res["stopped"], "The self tests were stopped: the tear-down of class tests.test_fake.Fine, then "
                                         "the set-up before test 2 of 2 (class tests.test_fake.SlowSetUp), ran longer "
                                         "than 10 seconds. 1 finished before it.")

    def tiny(self, kit, **limits):
        real = s.run_suite
        return real, (lambda kit_home=None, **k: real(kit_home, kit=kit, progress=k.get("progress"), **limits))

    def test_selftest_exits_1_names_the_test_and_still_says_what_was_ignored(self):
        kit = self.kit(with_noise(HUNG_SUITE, ["AppData/Local/Microsoft", "AppData/Local/Malwarebytes"]))
        real, s.run_suite = self.tiny(kit, per_test=10)
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = s.main(["selftest", "--kit-home", str(self.kit_home)])
        finally:
            s.run_suite = real
        out = buf.getvalue()
        lines = out.splitlines()
        self.assertEqual(rc, 1, out)
        self.assertIn("Step 4 of 6: running the kit's self tests only (this takes several minutes; longer on a PC where "
                      "security software checks each step) ... some did not pass", lines)
        stop = ("The self tests were stopped: tests.test_fake.B_Hung.test_hangs ran longer than 10 seconds (test 3 of 4). "
                "2 finished before it.")
        self.assertIn("Tests:        no count from unittest (%s)" % stop, lines)
        self.assertIn("Ignored:      AppData\\Local\\Microsoft, AppData\\Local\\Malwarebytes (folders other programs "
                      "make whenever PowerShell starts)", lines)
        self.assertIn("Also seen and ignored: Malwarebytes", lines)
        self.assertIn("Full output:  %s" % (self.kit_home / "selftest-last.txt"), lines)
        self.assertIn("Some self tests did not pass.", out)
        self.assertIn(stop, (self.kit_home / "selftest-last.txt").read_text(encoding="utf-8"))
        self._end_child(kit)

    def selftest_console(self, kit, **limits):
        real, s.run_suite = self.tiny(kit, **limits)
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = s.main(["selftest", "--kit-home", str(self.kit_home)])
        finally:
            s.run_suite = real
        return rc, buf.getvalue()

    def test_a_stop_after_failures_names_the_tests_that_had_failed(self):
        source = HUNG_SUITE.replace("def test_1(self):\n        pass", "def test_1(self):\n        self.fail('first')")
        source = source.replace("def test_2(self):\n        pass", "def test_2(self):\n        raise ValueError('x')")
        kit = self.kit(source)
        rc, out = self.selftest_console(kit, per_test=10)
        self._end_child(kit)
        lines = out.splitlines()
        self.assertEqual(rc, 1, out)
        stop = ("The self tests were stopped: tests.test_fake.B_Hung.test_hangs ran longer than 10 seconds (test 3 of 4). "
                "2 finished before it.")
        self.assertIn("Tests:        no count from unittest (%s)" % stop, lines)
        i = lines.index("Did not pass:")
        self.assertEqual(lines[i + 1:i + 3], [
            "  A_Before.test_1: FAIL (the run was stopped before the reason was written; see the file)",
            "  A_Before.test_2: ERROR (the run was stopped before the reason was written; see the file)"])
        res = s.run_suite(None, kit=kit, per_test=10)
        self._end_child(kit)
        self.assertEqual(res["failed_before"], ["A_Before.test_1", "A_Before.test_2"])
        self.assertEqual(s.self_test_detail(res), stop.rstrip(".") + "; before the stop, 2 did not pass: "
                                                  "A_Before.test_1, A_Before.test_2")

    def test_a_run_stopped_after_its_summary_still_says_why_on_the_console(self):
        """Every test passed and unittest printed "Ran 1 test" and OK, and then the runner hung at its exit: the
        Tests line has counts, so a line of its own says the run was stopped and why."""
        rc, out = self.selftest_console(self.kit(HANG_AT_EXIT), per_test=10)
        lines = out.splitlines()
        self.assertEqual(rc, 1, out)
        self.assertIn("Tests:        1 ran, 1 passed, 0 failed, 0 skipped", lines)
        self.assertIn("Stopped:      The self tests were stopped: the end of the run, after its last test, ran longer "
                      "than 10 seconds. 1 of 1 had finished.", lines)

    def _end_child(self, kit):
        pid_file = kit / "tests" / "child.pid"
        if pid_file.is_file():
            end_pid(int(pid_file.read_text().strip()))


@_helpers.build_only     # a real hung test under a 10 s limit
class StoppedStartAndAdopt(AdoptBase):
    """A stopped run stops adopt before any write ("nothing was changed") and a first start at step 5."""

    def setUp(self):
        AdoptBase.setUp(self)
        s.run_self_tests = self.saved[1]
        self.hung = fake_kit(self.tmp / "suite", HUNG_SUITE)
        self.real = s.run_suite
        hung = self.hung
        s.run_suite = lambda kit_home=None, **k: self.real(kit_home, kit=hung, progress=k.get("progress"), per_test=10)
        s._LINE.update(open="", broken="")

    def tearDown(self):
        s.run_suite = self.real
        s._LINE.update(open="", broken="")
        Limits._end_child(self, self.hung)
        AdoptBase.tearDown(self)

    STOP = ("the kit's self tests failed (The self tests were stopped: tests.test_fake.B_Hung.test_hangs ran longer "
            "than 10 seconds (test 3 of 4). 2 finished before it; full output in %s)")

    def test_adopt_refuses_and_changes_nothing(self):
        self.her_folder()
        rc, out = self.run_adopt()
        self.assertEqual(rc, 1, out)
        (stop,) = [ln for ln in out.splitlines() if ln.startswith("Stopped at step")]
        self.assertTrue(stop.startswith("Stopped at step 4: " + self.STOP % (self.kit / "selftest-last.txt")
                                        + "; nothing was changed. What to do: "), stop)
        self.assertEqual(self.calls, [])

    def test_a_first_start_stops_at_step_5(self):
        a = argparse.Namespace(claude="claude", kit_home=str(self.kit), seat_home=str(self.tmp / "seats"),
                               desktop=str(self.tmp / "no-desktop"), ask=False, no_plugin=True, no_tests=False)
        os.environ["SEAT_ANSWERS"] = "||"
        try:
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(s.Stop) as cm:
                    s.setup_seat(a)
        finally:
            os.environ.pop("SEAT_ANSWERS", None)
        self.assertEqual(str(cm.exception).split(". What to do:")[0] + ".",
                         "Stopped at step 5: " + self.STOP % (self.kit / "selftest-last.txt") + ".")


class TheFile(Base):
    def test_per_test_seconds_and_the_two_tables(self):
        kit = self.kit(FIRST_FILE, test_more=SECOND_FILE)
        res = s.run_suite(self.kit_home, kit=kit)
        self.assertTrue(res["ok"], res["output"])
        text = (self.kit_home / "selftest-last.txt").read_text(encoding="utf-8")
        self.assertTrue(text.endswith(res["output"] + res["timing"]))
        self.assertIn("Limits: 300 s per test (a test may ask for more), 300 s to find the tests, 10800 s in all (a "
                      "backstop)", text)
        self.assertIn("\nJob object: %s\n" % s.job_words(res["job"]), text)
        self.assertTrue(res["job"] == "armed" or res["job"].startswith("not armed ("), res["job"])
        done = {}
        for ln in marks(text, "DONE"):
            parts = ln.split()
            done[parts[-1]] = float(parts[3].rstrip("s"))
        self.assertEqual(sorted(done), ["tests.test_fake.Quick.test_quick", "tests.test_fake.Quick.test_quicker",
                                        "tests.test_more.Slow.test_slow", "tests.test_more.Slow.test_slower"])
        tail = res["timing"].splitlines()
        self.assertEqual(tail[2], "Time per test (from the SEAT-KIT-TEST-DONE lines above): 4 tests timed, "
                                  "%.1f s in tests, %s" % (sum(done.values()), tail[2].split(", ")[-1]))
        i = tail.index("Slowest tests:")
        slowest = [ln.split() for ln in tail[i + 1:i + 5]]
        self.assertEqual([r[-1] for r in slowest], sorted(done, key=lambda k: -done[k]))
        self.assertEqual([float(r[0]) for r in slowest], sorted(done.values(), reverse=True))
        self.assertEqual(slowest[0][-1], "tests.test_more.Slow.test_slower")
        j = tail.index("Total by test file:")
        files = [ln.split() for ln in tail[j + 1:]]
        self.assertEqual([(r[-1], int(r[2])) for r in files], [("tests.test_more", 2), ("tests.test_fake", 2)])
        self.assertAlmostEqual(float(files[0][0]), done["tests.test_more.Slow.test_slow"]
                               + done["tests.test_more.Slow.test_slower"], places=1)
        self.assertGreaterEqual(float(files[0][0]), 0.85)

    def test_the_slowest_table_keeps_ten(self):
        times = [("tests.test_m.K.test_%02d" % i, float(i), "ok") for i in range(14)]
        table = s.timing_tables(times).splitlines()
        i = table.index("Slowest tests:")
        self.assertEqual([ln.split()[-1] for ln in table[i + 1:i + 11]], ["tests.test_m.K.test_%02d" % i
                                                                         for i in range(13, 3, -1)])
        self.assertEqual(table[i + 11], "Total by test file:")
        self.assertEqual(table[i + 12].split(), ["91.00", "s", "14", "tests", "tests.test_m"])


# a made-up suite with one build-only test (marked on the method) and one build-only class, and no load_tests hook:
# the runner itself leaves them out outside a release build
BUILD_ONLY_SUITE = '''import subprocess
import sys
import unittest


class Mixed(unittest.TestCase):
    def test_everywhere(self):
        pass

    def test_build_only(self):
        pass
    test_build_only.selftest_build_only = True


class Waits(unittest.TestCase):
    def test_two_programs(self):
        for _ in range(2):
            subprocess.run([sys.executable, "-I", "-c", "pass"], capture_output=True, timeout=7)

    def test_one_wait(self):
        p = subprocess.Popen([sys.executable, "-I", "-c", "pass"])
        p.wait(timeout=11)


class OnlyInTheBuild(unittest.TestCase):
    selftest_build_only = True

    def test_one(self):
        pass

    def test_two(self):
        pass
'''
NOT_RUN = ["tests.test_fake.Mixed.test_build_only", "tests.test_fake.OnlyInTheBuild.test_one",
           "tests.test_fake.OnlyInTheBuild.test_two"]
RELEASE = {_helpers.RELEASE_BUILD: "1"}


class BuildOnly(Base):
    """0.1.7, last round: the tests of the kit's own runner that use real waits run only in the release build. On a
    colleague's PC they are not loaded at all: "Ran N" counts what really ran, nothing is called skipped, and the
    summary says how many were not run here."""

    def colleague_env(self):
        return {k: v for k, v in os.environ.items() if k != _helpers.RELEASE_BUILD}

    def test_outside_a_release_build_they_are_left_out_and_named_once(self):
        res = s.run_suite(self.kit_home, kit=self.kit(BUILD_ONLY_SUITE), env=self.colleague_env())
        self.assertTrue(res["ok"], res["output"])
        self.assertEqual(res["not_run"], NOT_RUN)
        self.assertEqual(res["build_only"], [])
        self.assertEqual(res["ran"], 3)
        self.assertEqual(s.tests_line(res), "Tests:        3 ran, 3 passed, 0 failed, 0 skipped")
        self.assertEqual(marks(res["output"], "NOTRUN"), ["SEAT-KIT-TEST-NOTRUN " + i for i in NOT_RUN])
        self.assertEqual(marks(res["output"], "FOUND"), ["SEAT-KIT-TEST-FOUND 3"])
        self.assertEqual(res["waits"], [])          # measured only in a release build
        text = (self.kit_home / "selftest-last.txt").read_text(encoding="utf-8")
        self.assertIn("\nNot run here: 3 checks of the kit's own test runner, which are run when the kit is built "
                      "(named once, on the SEAT-KIT-TEST-NOTRUN lines below)\n", text)
        for i in NOT_RUN:
            self.assertEqual(text.count(i), 1, i)

    def test_a_release_build_runs_them_says_which_and_measures_each_tests_time_limits(self):
        res = s.run_suite(None, kit=self.kit(BUILD_ONLY_SUITE), env=dict(os.environ, **RELEASE))
        self.assertTrue(res["ok"], res["output"])
        self.assertEqual(res["not_run"], [])
        self.assertEqual(res["build_only"], NOT_RUN)
        self.assertEqual(res["ran"], 6)
        self.assertEqual(s.tests_line(res), "Tests:        6 ran, 6 passed, 0 failed, 0 skipped")
        waits = {w[0].rsplit(".", 1)[1]: w[1:] for w in res["waits"]}
        self.assertEqual(sorted(waits), ["test_build_only", "test_everywhere", "test_one", "test_one_wait", "test_two",
                                         "test_two_programs"])
        # (seconds of time limits used, the test's own limit, calls without a time limit)
        self.assertEqual(waits["test_two_programs"], (14, 300, 0))   # 2 x subprocess.run(timeout=7)
        self.assertEqual(waits["test_one_wait"], (11, 300, 0))       # Popen.wait(timeout=11)
        self.assertEqual(waits["test_everywhere"], (0, 300, 0))

    def test_the_colleague_summary_says_how_many_were_not_run_here(self):
        kit, real = self.kit(BUILD_ONLY_SUITE), s.run_suite
        env = self.colleague_env()
        s.run_suite = lambda kit_home=None, **k: real(kit_home, kit=kit, env=env)
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = s.main(["selftest", "--kit-home", str(self.kit_home)])
        finally:
            s.run_suite = real
        lines = buf.getvalue().splitlines()
        self.assertEqual(rc, 0, buf.getvalue())
        i = lines.index("Tests:        3 ran, 3 passed, 0 failed, 0 skipped")
        self.assertEqual(lines[i + 1], "Not run here: 3 checks of the kit's own test runner, which are run when the kit "
                                       "is built")
        self.assertIn("All self tests passed.", lines)

    def test_the_load_tests_hook_leaves_them_out_under_plain_unittest_too(self):
        class K(unittest.TestCase):
            def test_a(self):
                pass

            @_helpers.build_only
            def test_b(self):
                pass

        @_helpers.build_only
        class L(unittest.TestCase):
            def test_c(self):
                pass
        loader = unittest.TestLoader()
        both = unittest.TestSuite([loader.loadTestsFromTestCase(K), loader.loadTestsFromTestCase(L)])
        saved = os.environ.pop(_helpers.RELEASE_BUILD, None)
        try:
            got = [t.id().rsplit(".", 1)[1] for t in flat(_helpers.load_tests(loader, both, None))]
            self.assertEqual(got, ["test_a"])
            self.assertIn(K("test_b").id(), _helpers.SELFTEST_NOT_RUN_HERE)
            os.environ[_helpers.RELEASE_BUILD] = "1"
            got = [t.id().rsplit(".", 1)[1] for t in flat(_helpers.load_tests(loader, both, None))]
            self.assertEqual(got, ["test_a", "test_b", "test_c"])
        finally:
            _helpers.SELFTEST_NOT_RUN_HERE[:] = [i for i in _helpers.SELFTEST_NOT_RUN_HERE if ".K." not in i
                                                 and ".L." not in i]
            if saved is None:
                os.environ.pop(_helpers.RELEASE_BUILD, None)
            else:
                os.environ[_helpers.RELEASE_BUILD] = saved
        import selftest_runner
        self.assertEqual((selftest_runner.RELEASE_BUILD, selftest_runner.BUILD_ONLY_ATTR),
                         (_helpers.RELEASE_BUILD, _helpers.BUILD_ONLY_ATTR))

    def test_the_kits_build_only_tests_are_in_modules_with_the_hook(self):
        """Every test module holding a build-only test leaves it out at discovery (load_tests), so plain unittest
        and the runner count the same tests. Read straight from each module's classes, past any hook."""
        import selftest_runner
        here = pathlib.Path(__file__).resolve().parent
        loader = unittest.TestLoader()
        holders = set()
        for path in sorted(here.glob("test*.py")):
            module = importlib.import_module("tests." + path.stem)
            for obj in vars(module).values():
                if isinstance(obj, type) and issubclass(obj, unittest.TestCase) and obj.__module__ == module.__name__:
                    if any(selftest_runner.is_build_only(t) for t in loader.loadTestsFromTestCase(obj)):
                        holders.add(module)
        self.assertEqual(sorted(m.__name__ for m in holders), ["tests.test_selftest_runner", "tests.test_short_paths"])
        for module in holders:
            self.assertIs(getattr(module, "load_tests", None), _helpers.load_tests, module.__name__)


def flat(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from flat(t)
        else:
            yield t


if __name__ == "__main__":
    unittest.main()
