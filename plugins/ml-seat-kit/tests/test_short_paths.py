"""One folder, two spellings. Windows can hand the kit a folder in its 8.3 short form (a long user name with a dot
gives a TEMP like ABCDEF~1.XYZ) or in its long form. The kit must never decide by comparing the two as strings: the
send gate must not miss a send, adopt's refusals must not be dodged, and undo must find what adopt did.

Every test here uses a REAL short spelling, asked of Windows (GetShortPathNameW) for a temp folder with a long,
dotted name. On a volume that makes no short names the call gives the long form back, and the test skips saying so.
"""
import builtins
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

from tests import _helpers
from tests.test_adopt import HER_LAUNCHER, HER_MD, HER_SETTINGS, listing
import seat_setup as s
import send_gate as g

GATE = str(_helpers.SCRIPTS / "send_gate.py")
SEND = "mcp__claude_ai_Gmail__send_message"
CHILD_FLAG = "SEAT_KIT_SHORT_TEMP_RUN"
load_tests = _helpers.load_tests     # build-only tests are not loaded outside a release build


class ShortBase(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="Long.Name ")
        self.tmp = _helpers.real(self.td)
        self.short_tmp = _helpers.short_form(self.tmp)
        if not self.short_tmp:
            self.td.cleanup()
            self.skipTest(_helpers.NO_SHORT_NAMES)
        self.kit = self.tmp / "Kit.Home1"
        (self.kit / "current").mkdir(parents=True)
        self.folder = self.tmp / "Her.Folder1"
        self.folder.mkdir()
        (self.folder / "CLAUDE.md").write_bytes(HER_MD.encode("utf-8"))
        (self.folder / ".claude").mkdir()
        (self.folder / ".claude" / "settings.json").write_text(json.dumps(HER_SETTINGS, indent=2), encoding="utf-8")
        (self.folder / "assistant.cmd").write_bytes(HER_LAUNCHER)
        self.claude = self.tmp / "bin" / "claude.exe"
        self.claude.parent.mkdir()
        self.claude.write_bytes(b"")
        self.calls = []
        self.saved = (s._claude, s.run_self_tests, s.make_shortcut, s.desktop_folder, builtins.input)
        s._claude = lambda claude, *a, timeout=180: (self.calls.append(" ".join(a)), (0, "[]" if "list" in a else "ok"))[1]
        s.run_self_tests = lambda *a: (True, "OK")

        def refuse(*a, **k):
            raise AssertionError("not in adopt")
        s.make_shortcut = s.desktop_folder = builtins.input = refuse

    def tearDown(self):
        s._claude, s.run_self_tests, s.make_shortcut, s.desktop_folder, builtins.input = self.saved
        self.td.cleanup()

    def short(self, path):
        got = _helpers.short_form(path)
        self.assertTrue(got and got != str(path), "no short spelling for %s" % path)
        return got

    def run_adopt(self, folder, *extra, kit=None):
        args = ["adopt"] + list(extra) + ([] if "--undo" in extra else ["--name", "Helper"])
        args += [str(folder), "--claude", str(self.claude), "--kit-home", str(kit or self.kit),
                 "--seat-home", str(self.tmp / "seats")]
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = s.main(args)
        return rc, buf.getvalue()


class Spellings(ShortBase):
    def test_same_and_inside_ask_the_file_system(self):
        short = self.short(self.folder)
        self.assertNotEqual(short.lower(), str(self.folder).lower())
        for a, b in ((short, self.folder), (str(self.folder).upper(), self.folder), (str(self.folder) + "\\", short)):
            self.assertTrue(s._same(a, b), (a, b))
            self.assertTrue(s._inside(a, b), (a, b))
        self.assertTrue(s._inside(os.path.join(short, "state", "not made yet"), self.folder))
        self.assertTrue(s._inside(self.folder / "state", self.short_tmp))
        self.assertFalse(s._inside(self.tmp, short))
        self.assertFalse(s._same(self.kit, short))

    def test_the_long_spelling_is_what_is_printed(self):
        self.assertEqual(s.long_path(self.short(self.folder)), str(self.folder))
        self.assertEqual(s.long_path(self.folder / "not there"), str(self.folder / "not there"))

    def test_a_store_python_is_seen_through_its_short_spelling(self):
        store = self.tmp / "Microsoft" / "WindowsApps"
        store.mkdir(parents=True)
        (store / "python.exe").write_bytes(b"")
        short = self.short(store / "python.exe")
        self.assertNotIn("windowsapps", short.lower())
        self.assertTrue(s.is_store_python(short))
        self.assertFalse(s.is_store_python(str(self.claude)))


class Refusals(ShortBase):
    def assert_refused(self, folder, words, kit=None):
        before = listing(self.tmp / "Her.Folder1")
        rc, out = self.run_adopt(folder, kit=kit)
        self.assertEqual(rc, 1, out)
        self.assertIn(words, out)
        self.assertEqual(listing(self.tmp / "Her.Folder1"), before)
        self.assertEqual(self.calls, [])

    def test_the_short_spelling_of_the_profile_root_is_refused(self):
        home = self.tmp / "Profile.Of1"
        home.mkdir()
        old = os.environ.get("USERPROFILE")
        for profile, typed in ((str(home), self.short(home)), (self.short(home), str(home))):
            with self.subTest(profile=profile):
                os.environ["USERPROFILE"] = profile
                try:
                    self.assert_refused(typed, "whole user folder")
                finally:
                    os.environ["USERPROFILE"] = old

    def test_the_short_spelling_of_the_kit_home_is_refused(self):
        self.assert_refused(self.short(self.kit), "kit's own folder")
        self.assert_refused(self.short(self.kit / "current"), "kit's own folder")
        self.assert_refused(self.kit, "kit's own folder", kit=self.short(self.kit))

    def test_the_short_spelling_of_a_folder_inside_a_seat_is_refused(self):
        rc, out = self.run_adopt(self.folder)
        self.assertEqual(rc, 0, out)
        inner = self.folder / "Inner.Fold1"
        inner.mkdir()
        self.calls.clear()
        self.assert_refused(self.short(inner), "inside another assistant seat")


class AdoptUndo(ShortBase):
    def test_adopt_short_then_undo_long_and_the_reverse_come_back_byte_for_byte(self):
        before = listing(self.folder)
        for first, second in ((self.short(self.folder), str(self.folder)), (str(self.folder), self.short(self.folder))):
            with self.subTest(adopt=first):
                rc, out = self.run_adopt(first)
                self.assertEqual(rc, 0, out)
                self.assertIn(str(self.folder), out)                       # the long form is printed
                self.assertNotIn(self.short(self.folder), out)
                rc, out = self.run_adopt(second, "--undo")
                self.assertEqual(rc, 0, out)
                after = {k: v for k, v in listing(self.folder).items() if not k.startswith(".seat")}
                self.assertEqual(after, before)

    def test_a_second_adopt_through_the_other_spelling_is_already_adopted(self):
        for first, second in ((self.short(self.folder), str(self.folder)), (str(self.folder), self.short(self.folder))):
            with self.subTest(adopt=first):
                self.assertEqual(self.run_adopt(first)[0], 0)
                done = listing(self.folder)
                rc, out = self.run_adopt(second)
                self.assertEqual(rc, 0, out)
                self.assertIn("already adopted, nothing changed", out)
                self.assertEqual(listing(self.folder), done)
                rc, out = self.run_adopt(second, "--check")
                self.assertIn("would change nothing", out)
                self.assertEqual(self.run_adopt(second, "--undo")[0], 0)


class Gate(ShortBase):
    def setUp(self):
        ShortBase.setUp(self)
        rc, out = self.run_adopt(self.folder)
        self.assertEqual(rc, 0, out)
        self.outside = self.tmp / "Not.A.Seat1"
        self.outside.mkdir()

    def event(self, cwd):
        return json.dumps({"tool_name": SEND, "tool_input": {"to": ["a@example.invalid"]}, "cwd": cwd})

    def spawn(self, project, cwd):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=project)
        return subprocess.run([sys.executable, "-I", GATE], input=self.event(cwd), capture_output=True, text=True,
                              timeout=60, cwd=cwd, env=env)

    def test_a_send_is_blocked_when_the_session_names_the_folder_in_short_form(self):
        short = self.short(self.folder)
        r = self.spawn(short, short)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertTrue(r.stderr.startswith("Drafts only:"), r.stderr)
        sub = self.folder / "state"
        r = self.spawn(self.short(sub), self.short(sub))                   # deeper, still inside the seat
        self.assertEqual(r.returncode, 2, r.stderr)
        r = self.spawn(str(self.outside), str(self.outside))
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_a_send_is_blocked_in_another_case_and_with_a_trailing_slash(self):
        for spelling in (str(self.folder).upper(), str(self.folder).lower() + "\\", self.short(self.folder) + "\\"):
            with self.subTest(spelling):
                self.assertTrue(gate_blocks(spelling))

    def test_a_send_is_blocked_through_a_junction(self):
        try:
            import _winapi
            link = self.outside / "Link.State1"
            _winapi.CreateJunction(str(self.folder / "state"), str(link))
        except (ImportError, AttributeError, OSError) as e:
            self.skipTest("no junction could be made here (%s)" % e)
        try:
            r = self.spawn(str(link), str(link))
            self.assertEqual(r.returncode, 2, r.stderr)
        finally:
            os.rmdir(str(link))                                             # removes the link only


def gate_blocks(spelling):
    """The gate in this process, with the project folder and the event cwd both spelled this way."""
    old = os.environ.get("CLAUDE_PROJECT_DIR")
    os.environ["CLAUDE_PROJECT_DIR"] = spelling
    try:
        return bool(g.in_seat({"cwd": spelling})) and g.main(io.StringIO(json.dumps(
            {"tool_name": SEND, "cwd": spelling}))) == 2
    finally:
        if old is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = old


# 0.1.7: the inner whole-suite run goes through the kit's own runner (run_suite), with the same limits per step as any
# run and a total of its own, INNER_TOTAL. It runs what a colleague's PC runs: the build-only tests are left out
# (whole_suite drops SEAT_KIT_RELEASE_BUILD; the outer release run has already run them). Measured on the build PC
# (2026-11-24, last 0.1.7 round): the suite in that shape, 260 tests, took 255 s (plain unittest; the round before,
# with the build-only tests still inside, the inner run took 339 s). INNER_TOTAL = 2700 s is more than ten times
# 255 s, and make_kit refuses a build whose inner run, times 4, is over INNER_TOTAL. This test's own limit is
# INNER_TOTAL + 60 = 2760 s, and the outer run's TOTAL_SECONDS is 10800: inner 2700 < this test's 2760 < outer 10800.
# Why the INNER run is the one that names a hung test: a hang inside it is stopped by the inner run's limit for the
# step it is in (300 s for most tests, a test's own sum for the few that ask for more), and in any case by its total,
# 2700 s after it started; stopping takes at most 30 s (end_process_tree waits up to 30 s for the runner to end) plus
# 5 s for the pipes (PIPE_GRACE), so the inner run returns its stop line, which names the test that was running, by
# about 2735 s, before this test's own 2760 s limit. Not named by the inner run only if the inner run itself does
# not return within those 25 s of slack.
INNER_TOTAL = 2700
FORCE_FLAG = "SEAT_KIT_FORCE_SHORT_TEMP_RUN"   # "1": run the whole suite again even here (make_kit.py sets it)
NOT_SHORT = ("this PC's TEMP is not a short-form path, so the second full run is not needed here (%s=1 runs it "
             "anyway)" % FORCE_FLAG)


def temp_is_short(environ=None):
    """True when TEMP or TMP, as set, is really a short-form (8.3) spelling: an existing folder whose value differs
    from its long spelling (GetLongPathNameW, seat_setup.long_path) other than in letter case or a trailing
    backslash. A junction or a link is not followed by that call, so it never counts as short."""
    environ = os.environ if environ is None else environ
    for var in ("TEMP", "TMP"):
        value = (environ.get(var) or "").strip()
        if not value or not os.path.isdir(value):
            continue
        given = os.path.normcase(os.path.abspath(value)).rstrip("\\/")
        if given != os.path.normcase(s.long_path(value)).rstrip("\\/"):
            return True
    return False


def whole_suite(temp, kit=None, **limits):
    """The kit's whole suite in a child Python whose TEMP and TMP are `temp`, through run_suite, silent (no progress
    on the person's console). Returns (run_suite's result, why it failed or None)."""
    env = dict(os.environ, TEMP=temp, TMP=temp)
    env[CHILD_FLAG] = "1"
    env.pop(_helpers.RELEASE_BUILD, None)    # the build-only tests ran in the outer run; never again in here
    limits.setdefault("total", INNER_TOTAL)
    res = s.run_suite(None, kit=kit or _helpers.KIT, env=env, progress=None, **limits)
    out = res["output"]
    if res["stopped"]:
        return res, "the inner run was stopped: %s\n%s" % (res["stopped"], out[-3000:])
    bad = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
    if (res["returncode"], bad) != (0, []) or "\nOK" not in out:
        return res, "the inner run did not pass (exit code %s, %s)\n%s" % (res["returncode"], bad, out[-3000:])
    return res, None


class WholeSuiteUnderShortTemp(unittest.TestCase):
    """The regression for the real failure: the whole suite, in a child Python whose TEMP and TMP are the short
    spelling of a folder with a long, dotted name, must pass."""

    def test_the_whole_suite_passes_with_a_short_form_temp(self):
        if os.environ.get(CHILD_FLAG) == "1":
            self.skipTest("this run is already the short-TEMP run")
        if not temp_is_short() and os.environ.get(FORCE_FLAG) != "1":    # 0.1.7: only where it can matter
            self.skipTest(NOT_SHORT)
        with tempfile.TemporaryDirectory(prefix="Long.Temp ") as td:
            short = _helpers.short_form(_helpers.real(td))
            if not short:
                self.skipTest(_helpers.NO_SHORT_NAMES)
            res, why = whole_suite(short)
        self.assertIsNone(why, why)
        self.assertTrue(res["ok"])
    test_the_whole_suite_passes_with_a_short_form_temp.selftest_seconds = INNER_TOTAL + 60

    def test_only_a_really_short_form_temp_counts(self):
        with tempfile.TemporaryDirectory(prefix="Long.Temp ") as td:
            long_ = str(_helpers.real(td))
            short = _helpers.short_form(long_)
            self.assertFalse(temp_is_short({"TEMP": long_, "TMP": long_}))
            self.assertFalse(temp_is_short({"TEMP": long_.upper() + "\\", "TMP": long_.lower()}))
            self.assertFalse(temp_is_short({"TEMP": long_ + "\\missing"}))
            self.assertFalse(temp_is_short({}))
            if not short:
                self.skipTest(_helpers.NO_SHORT_NAMES)
            self.assertTrue(temp_is_short({"TEMP": short, "TMP": long_}))
            self.assertTrue(temp_is_short({"TEMP": long_, "TMP": short}))
        self.assertIn("TEMP is not a short-form path, so the second full run is not needed here", NOT_SHORT)

    def test_a_run_under_a_short_form_temp_passes_it_through_intact(self):
        """What the second full run checked of the runner itself, kept where it is skipped: a suite run through
        run_suite with a short-form TEMP sees exactly that TEMP, and its temporary folders are made under it."""
        from tests.test_selftest import fake_kit
        with tempfile.TemporaryDirectory(prefix="Long.Temp ") as td:
            short = _helpers.short_form(_helpers.real(td))
            if not short:
                self.skipTest(_helpers.NO_SHORT_NAMES)
            probe = ("import os\nimport tempfile\nimport unittest\n\n\nclass Temp(unittest.TestCase):\n"
                     "    def test_temp(self):\n        self.assertEqual(os.environ['TEMP'], %r)\n"
                     "        self.assertEqual(os.environ['TMP'], %r)\n"
                     "        with tempfile.TemporaryDirectory() as d:\n"
                     "            self.assertTrue(os.path.normcase(d).startswith(os.path.normcase(%r)), d)\n"
                     % (short, short, short))
            res, why = whole_suite(short, kit=fake_kit(_helpers.real(td), probe))
        self.assertIsNone(why, why)
        self.assertEqual(res["ran"], 1)

    @_helpers.build_only
    def test_the_inner_run_names_a_hung_test(self):
        """A made-up suite stands in for the kit's (a test that hangs), with a per-test limit of seconds."""
        from tests.test_selftest import fake_kit
        hung = ("import time\nimport unittest\n\n\nclass Fine(unittest.TestCase):\n    def test_fine(self):\n"
                "        pass\n\n\nclass Stuck(unittest.TestCase):\n    def test_stuck(self):\n        time.sleep(120)\n")
        with tempfile.TemporaryDirectory(prefix="Long.Temp ") as td:
            res, why = whole_suite(str(_helpers.real(td)), kit=fake_kit(_helpers.real(td), hung), per_test=10)
        self.assertEqual(res["stopped"], "The self tests were stopped: tests.test_fake.Stuck.test_stuck ran longer "
                                         "than 10 seconds (test 2 of 2). 1 finished before it.")
        self.assertTrue(why.startswith("the inner run was stopped: The self tests were stopped: "
                                       "tests.test_fake.Stuck.test_stuck ran longer than 10 seconds"), why)


if __name__ == "__main__":
    unittest.main()
