"""Checks for tools/make_kit.py: a release build never silently goes without the private name list."""
import contextlib
import io
import os
import pathlib
import sys
import tempfile
import unittest

from tests import _helpers

sys.path.insert(0, str(_helpers.KIT / "tools"))
import make_kit as mk   # noqa: E402

ADDRESS = "https://downloads.example.invalid/seat-kit.zip"
WORDS = [("seat name", "juni" + "per")]


class PrivateListGuard(unittest.TestCase):
    def test_a_release_build_without_the_list_is_refused_loudly(self):
        with self.assertRaises(SystemExit) as caught:
            mk.require_private_list([], ADDRESS)
        said = str(caught.exception)
        self.assertIn("REFUSED", said)
        self.assertIn("private_words.txt", said)
        self.assertIn("--without-private-list", said)

    def test_a_release_build_with_the_list_goes_ahead(self):
        self.assertIsNone(mk.require_private_list(WORDS, ADDRESS))

    def test_going_without_must_be_said_and_is_printed(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mk.require_private_list([], ADDRESS, without=True)
        self.assertIn("WITHOUT the private name list", out.getvalue())

    def test_a_build_with_no_address_is_not_a_release(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertIsNone(mk.require_private_list([], None))
        self.assertEqual(out.getvalue(), "")

    def test_the_list_is_never_packed(self):
        self.assertIn("private_words.txt", mk.SKIP_NAMES)
        self.assertFalse([p for p in mk.plugin_files() if p.name == "private_words.txt"])


class TestsFirst(unittest.TestCase):
    """make_kit runs the whole suite (a fake result here: the real run would be this suite again) and refuses."""

    FAILED = {"ok": False, "ran": 120, "summary": "FAILED (failures=1)", "skipped": 0,
              "failed": [("FAIL", "Klass.test_one", "AssertionError: 1 != 2")], "output": "FAIL: test_one\nFAILED"}
    # what a release build's run_suite returns for a good run: the build-only test ran, every test's subprocess time
    # limits were measured, and the inner short-TEMP run passed
    BUILD_ONLY = "tests.test_x.Limits.test_hung"
    PASSED = {"ok": True, "ran": 120, "summary": "OK", "skipped": 0, "failed": [], "output": "OK",
              "times": [(BUILD_ONLY, 10.0, "ok"), (mk.INNER_TEST, 100.0, "ok")],
              "limits": {mk.INNER_TEST: 1860}, "build_only": [BUILD_ONLY], "not_run": [],
              "waits": [(BUILD_ONLY, 30, 300, 0), (mk.INNER_TEST, 0, 1860, 0)],
              "phases": [("find", "", 2.0, 300), ("setup", "tests.test_x.Limits", 1.0, 300), ("end", "", 0.5, 300)],
              "wall": 400.0}

    def passed(self, **change):
        return dict(self.PASSED, **change)

    def test_a_failing_suite_refuses_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "seat-kit.zip"
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    mk.build(out, tests=lambda: self.FAILED)
            self.assertFalse(out.exists())
        self.assertIn("REFUSED", str(caught.exception))
        self.assertIn("Klass.test_one", str(caught.exception))

    def test_a_passing_suite_builds(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "seat-kit.zip"
            with contextlib.redirect_stdout(io.StringIO()) as said:
                mk.build(out, tests=lambda: self.PASSED)
            self.assertTrue(out.is_file())
        self.assertIn("120 tests ran, all passed", said.getvalue())

    def refused(self, res):
        """build() on a fake result: the refusal's text; nothing may be written."""
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "seat-kit.zip"
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    mk.build(out, tests=lambda: res)
            self.assertFalse(out.exists())
        return str(caught.exception)

    def test_a_test_using_more_than_a_quarter_of_its_limit_refuses_the_build(self):
        """0.1.7: a 4x margin on the build PC, from the DONE times and the limit each test ran under."""
        timed = self.passed(times=self.PASSED["times"] + [
            ("tests.test_a.K.test_ok", 75.0, "ok"),           # 75 * 4 = 300: allowed
            ("tests.test_a.K.test_slow", 80.5, "ok"),         # 80.5 * 4 > 300
            ("tests.test_b.Big.test_own", 300.0, "ok"),       # 300 * 4 < 1560: allowed
            ("tests.test_b.Big.test_over", 400.0, "ok")],     # 400 * 4 > 1560
            limits={mk.INNER_TEST: 1860, "tests.test_a.K.test_ok": 300, "tests.test_b.Big.test_own": 1560,
                    "tests.test_b.Big.test_over": 1560})                                    # test_slow: the default
        timed["waits"] = timed["waits"] + [(t, 0, 300, 0) for t, _, _ in timed["times"][2:]]
        said = self.refused(timed)
        self.assertTrue(said.startswith("REFUSED: 2 steps took more than a quarter of their limit on this PC"), said)
        self.assertIn("  80.5 s of a 300 s limit: tests.test_a.K.test_slow", said)
        self.assertIn("  400.0 s of a 1560 s limit: tests.test_b.Big.test_over", said)
        self.assertNotIn("test_ok", said)
        self.assertNotIn("test_own", said)
        fine = dict(timed, times=timed["times"][:3] + timed["times"][4:5])
        self.assertEqual(mk.require_margin(fine, inner=1500), "every step finished within a quarter of its limit "
                                                              "(closest: 75.0 s of 300 s, tests.test_a.K.test_ok)")
        self.assertEqual(mk.MARGIN, 4)

    def test_a_slow_step_the_whole_run_or_the_inner_run_past_a_quarter_refuses_the_build(self):
        """0.1.7 last round: set-up, tear-down, finding the tests and the end of the run are checked as tests are; the
        whole run against TOTAL_SECONDS; the inner short-TEMP run against INNER_TOTAL, and it must have run."""
        import seat_setup
        said = self.refused(self.passed(phases=[("setup", "tests.test_x.Slow after tests.test_x.Before", 80.0, 300),
                                                ("find", "", 76.0, 300), ("end", "", 1.0, 300)]))
        self.assertTrue(said.startswith("REFUSED: 2 steps took more than a quarter"), said)
        self.assertIn("  80.0 s of a 300 s limit: set-up (tests.test_x.Slow after tests.test_x.Before)", said)
        self.assertIn("  76.0 s of a 300 s limit: finding the tests", said)
        said = self.refused(self.passed(wall=seat_setup.TOTAL_SECONDS / 4 + 1))
        self.assertIn("of a %d s limit: the whole run" % seat_setup.TOTAL_SECONDS, said)
        self.assertEqual(seat_setup.TOTAL_SECONDS, 10800)
        inner = mk.inner_total()
        said = self.refused(self.passed(times=[(self.BUILD_ONLY, 10.0, "ok"), (mk.INNER_TEST, inner / 4 + 1, "ok")]))
        self.assertIn("of a %d s limit: the inner short-TEMP run, against INNER_TOTAL" % inner, said)
        said = self.refused(self.passed(times=[(self.BUILD_ONLY, 10.0, "ok"), (mk.INNER_TEST, 0.0, "skipped")]))
        self.assertIn("REFUSED: the second full run under a short-form TEMP", said)
        self.assertIn("did not run and pass", said)
        from tests import test_short_paths
        self.assertEqual(inner, test_short_paths.INNER_TOTAL)

    def test_the_build_only_tests_must_all_have_run(self):
        """0.1.7 last round: a build refuses when the build-only tests of the kit's runner did not all run."""
        for res, words in ((self.passed(build_only=[]), "0 found, 0 ran"),
                           (self.passed(not_run=[self.BUILD_ONLY], build_only=[]), "0 found, 0 ran, 1 left out"),
                           (self.passed(build_only=[self.BUILD_ONLY, "tests.test_x.Limits.test_other"]),
                            "2 found, 1 ran"),
                           (self.passed(times=[(self.BUILD_ONLY, 0.0, "skipped"), (mk.INNER_TEST, 100.0, "ok")]),
                            "1 found, 0 ran")):
            with self.subTest(words):
                said = self.refused(res)
                self.assertTrue(said.startswith("REFUSED: the build-only tests of the kit's own runner did not all "
                                                "run (%s" % words), said)
        self.assertEqual(mk.require_build_only(self.PASSED), "1 build-only tests ran, all of them")
        self.assertEqual(mk.RELEASE_SUITE["SEAT_KIT_RELEASE_BUILD"], "1")
        self.assertEqual(_helpers.RELEASE_BUILD, "SEAT_KIT_RELEASE_BUILD")

    def test_a_test_whose_programs_time_limits_add_up_past_its_own_limit_refuses_the_build(self):
        """0.1.7 last round (review minor 1): measured in the build's own run, per test, by the runner."""
        said = self.refused(self.passed(waits=[(self.BUILD_ONLY, 360, 300, 0), (mk.INNER_TEST, 0, 1860, 0)]))
        self.assertIn("REFUSED: 1 step used subprocess time limits adding up to more than its own limit", said)
        self.assertIn("  360 s of time limits under a 300 s limit: %s" % self.BUILD_ONLY, said)
        said = self.refused(self.passed(waits=[(self.BUILD_ONLY, 30, 300, 0)]))
        self.assertIn("did not measure every test's subprocess time limits (1 tests without a WAITS line)", said)
        self.assertEqual(mk.require_waits(self.PASSED).splitlines(), [
            "every test's subprocess time limits fit in its own limit (2 measured; closest: 30 s of 300 s, %s)"
            % self.BUILD_ONLY,
            "      0 s of time limits under its 1860 s limit: %s" % mk.INNER_TEST])   # each test with its own limit

    def test_the_real_runner_is_the_default(self):
        self.assertIs(mk.build.__defaults__[-1], mk.run_tests)

    def test_a_release_build_forces_the_second_full_run_under_a_short_temp(self):
        """0.1.7: that run happens only where TEMP really is short form, so make_kit's own suite run forces it; it
        also runs the build-only tests (SEAT_KIT_RELEASE_BUILD=1)."""
        from tests import test_short_paths
        import seat_setup
        self.assertEqual(mk.FORCE_SHORT_TEMP_RUN, {test_short_paths.FORCE_FLAG: "1"})
        self.assertEqual(mk.RELEASE_SUITE, {test_short_paths.FORCE_FLAG: "1", _helpers.RELEASE_BUILD: "1"})
        got, real = [], seat_setup.run_suite
        seat_setup.run_suite = lambda kit_home=None, **k: (got.append((kit_home, k)), self.PASSED)[1]
        try:
            self.assertIs(mk.run_tests(), self.PASSED)
        finally:
            seat_setup.run_suite = real
        ((kit_home, k),) = got
        self.assertIsNone(kit_home)
        self.assertEqual(k["env"][test_short_paths.FORCE_FLAG], "1")
        self.assertEqual(k["env"][_helpers.RELEASE_BUILD], "1")
        ours = (test_short_paths.FORCE_FLAG, _helpers.RELEASE_BUILD)
        self.assertEqual({x: k["env"][x] for x in k["env"] if x not in ours},
                         {x: v for x, v in os.environ.items() if x not in ours})
        self.assertIs(k["progress"], seat_setup.say_progress)


if __name__ == "__main__":
    unittest.main()
