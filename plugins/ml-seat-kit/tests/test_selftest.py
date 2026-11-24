"""The self tests' own reporting: the stop line names what failed and where the full output is, and
`seat_setup.py selftest` is read only. A small made-up suite stands in for the kit's tests (run for real, in a fresh
python -I, from a temp folder), so nothing here runs the kit's own suite again."""
import argparse
import builtins
import hashlib
import io
import json
import os
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout

from tests import _helpers
from tests.test_adopt import AdoptBase, listing
import seat_setup as s

FAKE_SUITE = '''import unittest


class Fake(unittest.TestCase):
    def test_a_fails(self):
        self.assertEqual(1, 2)

    def test_b_errors(self):
        raise KeyError("no such key")

    def test_c_fails_docstring(self):
        """A docstring line that unittest prints under the name."""
        self.assertTrue(False, "plainly false")

    def test_d_fails(self):
        for kind in ("cmd", "ps1"):
            with self.subTest(kind):
                self.fail("this one failed in %s" % kind)

    def test_e_passes(self):
        pass

    @unittest.skip("not on this machine")
    def test_f_skipped(self):
        pass
'''

PASSING_SUITE = '''import unittest


class Good(unittest.TestCase):
    def test_one(self):
        pass

    def test_two(self):
        """Has a docstring."""
        pass

    @unittest.skip("not on this machine")
    def test_skipped(self):
        pass
'''


FIXTURE_SUITE = '''import unittest


class Tear(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        raise RuntimeError("tear down broke")

    def test_one(self):
        pass

    def test_two(self):
        pass
'''

SUBSKIP_SUITE = '''import unittest


class Sub(unittest.TestCase):
    def test_a(self):
        for k in ("x", "y"):
            with self.subTest(k):
                self.skipTest("not here %s" % k)

    def test_b(self):
        """Has a docstring."""
        with self.subTest("z"):
            self.skipTest("nor here")

    def test_c(self):
        pass
'''


def fake_kit(base, source):
    kit = pathlib.Path(base) / "fake kit"
    (kit / ".claude-plugin").mkdir(parents=True)
    (kit / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": "9.9.9"}), encoding="utf-8")
    (kit / "tests").mkdir()
    (kit / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (kit / "tests" / "test_fake.py").write_text(source, encoding="utf-8")
    return kit


def with_noise(source, noise):
    """A made-up suite that prints the noise marker line as the kit's own tests do (tests/_helpers.powershell_noise),
    or, with None, prints none (an older kit)."""
    if noise is None:
        return source
    return source.replace("import unittest\n", "import json\nimport sys\nimport unittest\n\nsys.__stdout__.write("
                          "'\\n%s %%s\\n' %% json.dumps(%r))\n" % (_helpers.NOISE_MARKER, list(noise)), 1)


class Patched:
    """s.run_suite runs the made-up suite instead of the kit's own."""

    def __init__(self, kit):
        self.kit, self.real = kit, s.run_suite

    def __enter__(self):
        real, kit = self.real, self.kit
        s.run_suite = lambda kit_home=None, **_: real(kit_home, kit=kit)
        return self

    def __exit__(self, *exc):
        s.run_suite = self.real


class Parse(unittest.TestCase):
    def test_names_from_both_python_formats_and_a_module_that_did_not_load(self):
        self.assertEqual(s.short_test_name("test_x (tests.test_m.Klass.test_x)"), "Klass.test_x")
        self.assertEqual(s.short_test_name("test_x (tests.test_m.Klass)"), "Klass.test_x")
        self.assertEqual(s.short_test_name("test_x (tests.test_m.Klass.test_x) [cmd]"), "Klass.test_x [cmd]")
        self.assertEqual(s.short_test_name("test_m (unittest.loader._FailedTest.test_m)"), "test_m (did not load)")
        self.assertEqual(s.short_test_name("setUpClass (tests.test_m.Klass)"), "Klass.setUpClass")

    def test_the_noise_line_is_read_bounded_and_may_be_absent(self):
        m = s.NOISE_MARKER
        self.assertEqual(m, _helpers.NOISE_MARKER)                          # the tests print what selftest reads
        self.assertIsNone(s.parse_noise("Ran 3 tests in 0.1s\n\nOK\n"))
        self.assertIsNone(s.parse_noise("%s not json\n" % m))
        self.assertEqual(s.parse_noise("x\n%s []\nRan 1 test\n" % m), [])
        self.assertEqual(s.parse_noise('%s ["AppData/Local/Microsoft", "AppData/Local/seat-kit", "AppData/Local/SEAT-KIT", '
                                       '"AppData/Local/Microsoft/Windows", "Desktop", "AppData/Roaming/Thing", 7]\n' % m),
                         ["AppData/Local/Microsoft", "AppData/Roaming/Thing"])
        # two marker lines (a child suite's, quoted in a failure's text, comes later): the first valid one is kept
        self.assertEqual(s.parse_noise('%s ["AppData/Local/Microsoft"]\nFAIL: test_short_temp\n  %s x\n'
                                       '%s ["AppData/Local/Child"]\n' % (m, m, m)), ["AppData/Local/Microsoft"])
        self.assertEqual(s.parse_noise('%s not json\n%s ["AppData/Local/Microsoft"]\n%s []\n' % (m, m, m)),
                         ["AppData/Local/Microsoft"])
        self.assertEqual(s.noise_lines(["AppData/Local/Microsoft", "AppData/Roaming/Thing"]),
                         ["Ignored:      AppData\\Local\\Microsoft, AppData\\Roaming\\Thing (folders other programs make "
                          "whenever PowerShell starts)", "Also seen and ignored: Thing"])

    def test_the_tests_line_adds_up(self):
        def res(ran, skipped, failed, failures=0, errors=0, skips=None, summary="x"):
            out = {"ran": ran, "skipped": skipped, "failures": failures, "errors": errors, "summary": summary,
                   "failed": [("FAIL", n, "why") for n in failed]}
            if skips is not None:
                out["skips"] = [(n, "why") for n in skips]
            return out
        # the colleague's run: 9 tests failed, 12 failures counting sub-cases
        nine = ["K.t%d" % i for i in range(9)] + ["K.t0 [a]", "K.t0 [b]", "K.t1 [cmd]"]
        self.assertEqual(s.tests_line(res(198, 0, nine, failures=12)),
                         "Tests:        198 ran, 189 passed, 9 failed (12 failures counting sub-cases), 0 skipped")
        self.assertEqual(s.tests_line(res(10, 2, ["K.a", "K.b"], failures=1, errors=1)),
                         "Tests:        10 ran, 6 passed, 2 failed, 2 skipped")
        self.assertEqual(s.tests_line(res(None, 0, [])), "Tests:        no count from unittest (x)")
        # a tearDownClass error with every test passing: not taken off passed, named after the counts
        self.assertEqual(s.tests_line(res(10, 0, ["K.tearDownClass"], errors=1)),
                         "Tests:        10 ran, 10 passed, 0 failed, 0 skipped; 1 error outside any test "
                         "(K.tearDownClass)")
        self.assertEqual(s.tests_line(res(4, 0, ["K.setUpClass", "test_m.setUpModule", "J.t"], failures=1, errors=2)),
                         "Tests:        4 ran, 3 passed, 1 failed, 0 skipped; 2 errors outside any test "
                         "(K.setUpClass, test_m.setUpModule)")
        # three sub-case skips in two tests: skipped counts tests, so ran = passed + failed + skipped
        self.assertEqual(s.tests_line(res(3, 3, [], skips=["K.a [x]", "K.a [y]", "K.b [z]"])),
                         "Tests:        3 ran, 1 passed, 0 failed, 2 skipped (3 skips counting sub-cases)")
        # a whole class skipped in setUpClass is not one of the tests that ran
        self.assertEqual(s.tests_line(res(2, 1, [], skips=["S.setUpClass"])),
                         "Tests:        2 ran, 2 passed, 0 failed, 0 skipped; 1 skip outside any test (S.setUpClass)")
        # a test with a failing and a skipped sub-case is failed, not also skipped
        self.assertEqual(s.tests_line(res(2, 1, ["K.a [x]"], failures=1, skips=["K.a [y]"])),
                         "Tests:        2 ran, 1 passed, 1 failed, 0 skipped")
        # counts that cannot add up are shown as unittest printed them, and the line says so
        self.assertEqual(s.tests_line(res(1, 0, ["K.a", "K.b"], failures=2, summary="FAILED (failures=2)")),
                         "Tests:        1 ran; unittest's own counts: FAILED (failures=2) (these do not add up per "
                         "test, so they are shown as unittest printed them)")


class Report(unittest.TestCase):
    """Item 1: the names come from unittest's own output, and the full output is kept in the kit home."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = _helpers.real(self.td) / "with space"
        self.kit_home = self.base / "kit home"
        self.kit_home.mkdir(parents=True)
        self.kit = fake_kit(self.base, FAKE_SUITE)

    def tearDown(self):
        self.td.cleanup()

    def test_a_failing_suite_names_the_tests_and_keeps_the_full_output(self):
        res = s.run_suite(self.kit_home, kit=self.kit)
        self.assertFalse(res["ok"])
        self.assertEqual(s.failed_names(res), ["Fake.test_b_errors", "Fake.test_a_fails", "Fake.test_c_fails_docstring",
                                               "Fake.test_d_fails [cmd]", "Fake.test_d_fails [ps1]"])
        reasons = {name: why for _, name, why in res["failed"]}
        self.assertEqual(reasons["Fake.test_a_fails"], "AssertionError: 1 != 2")
        self.assertEqual(reasons["Fake.test_b_errors"], "KeyError: 'no such key'")
        self.assertEqual(reasons["Fake.test_c_fails_docstring"], "AssertionError: False is not true : plainly false")
        self.assertEqual((res["ran"], res["failures"], res["errors"], res["skipped"]), (6, 4, 1, 1))
        self.assertEqual(res["skips"], [("Fake.test_f_skipped", "not on this machine")])
        detail = s.self_test_detail(res)
        self.assertEqual(detail, "5 did not pass: Fake.test_b_errors, Fake.test_a_fails, Fake.test_c_fails_docstring "
                                 "and 2 more; full output in %s" % (self.kit_home / "selftest-last.txt"))
        text = (self.kit_home / "selftest-last.txt").read_text(encoding="utf-8")
        self.assertTrue(text.endswith(res["output"]))
        for want in ("Kit version: 9.9.9", "Python: ", "Windows: ", "TEMP: ",
                     "FAIL: test_a_fails", "ERROR: test_b_errors", "Traceback", "Ran 6 tests", "FAILED ("):
            self.assertIn(want, text)

    def test_the_file_is_overwritten_each_run(self):
        (self.kit_home / "selftest-last.txt").write_text("an old run\n" * 5000, encoding="utf-8")
        s.run_suite(self.kit_home, kit=self.kit)
        self.assertNotIn("an old run", (self.kit_home / "selftest-last.txt").read_text(encoding="utf-8"))

    def test_a_file_that_cannot_be_written_still_names_the_tests(self):
        (self.kit_home / "selftest-last.txt").mkdir()          # a folder where the file should go: the write fails
        res = s.run_suite(self.kit_home, kit=self.kit)
        self.assertTrue(res["file_error"])
        detail = s.self_test_detail(res)
        self.assertTrue(detail.startswith("5 did not pass: Fake.test_b_errors, Fake.test_a_fails, "), detail)
        self.assertIn("the full output could not be written to %s (" % (self.kit_home / "selftest-last.txt"), detail)

    def test_a_passing_suite_is_ok_and_still_keeps_the_file(self):
        with Patched(fake_kit(self.base / "good", PASSING_SUITE)):
            ok, detail = s.run_self_tests(self.kit_home)
        self.assertTrue(ok, detail)
        self.assertIn("Ran 3 tests", (self.kit_home / "selftest-last.txt").read_text(encoding="utf-8"))



class StopLines(AdoptBase):
    """Item 1 and item 5: both paths stop with the names and the file; adopt still writes nothing first."""

    def setUp(self):
        AdoptBase.setUp(self)
        s.run_self_tests = self.saved[1]                      # the real one, over the made-up suite
        self.fake = fake_kit(self.tmp / "suite", FAKE_SUITE)

    def test_adopt_stops_before_any_write_and_names_the_tests(self):
        self.her_folder()
        before = listing(self.folder)
        with Patched(self.fake):
            rc, out = self.run_adopt()
        self.assertEqual(rc, 1, out)
        (stop,) = [ln for ln in out.splitlines() if ln.startswith("Stopped at step")]
        self.assertTrue(stop.startswith("Stopped at step 4: the kit's self tests failed (5 did not pass: "
                                        "Fake.test_b_errors, Fake.test_a_fails, Fake.test_c_fails_docstring and 2 more; "
                                        "full output in %s); nothing was changed. What to do: " % (self.kit / "selftest-last.txt")), stop)
        self.assertIn("the file it names", stop)
        self.assertEqual(listing(self.folder), before)
        self.assertEqual(self.calls, [])
        self.assertIn("FAIL: test_a_fails", (self.kit / "selftest-last.txt").read_text(encoding="utf-8"))

    def test_adopt_runs_the_tests_once_per_kit_version(self):
        self.her_folder()
        ran = []
        s.run_self_tests = lambda *a: (ran.append(a), (True, "OK"))[1]
        self.assertEqual(self.run_adopt()[0], 0)
        self.assertEqual(ran, [(str(self.kit),)])
        self.assertEqual(self.run_adopt()[0], 0)               # adopted for this version: not run again
        self.assertEqual(len(ran), 1)

    def test_the_normal_path_names_the_tests_too(self):
        a = argparse.Namespace(claude="claude", kit_home=str(self.kit), seat_home=str(self.tmp / "seats"),
                               desktop=str(self.tmp / "no-desktop"), ask=False, no_plugin=True, no_tests=False)
        os.environ["SEAT_ANSWERS"] = "||"
        orig_tick = s.dry_tick
        s.dry_tick = lambda seat: (True, "FREE: test")
        try:
            with Patched(self.fake), redirect_stdout(io.StringIO()):
                with self.assertRaises(s.Stop) as cm:
                    s.setup_seat(a)
        finally:
            s.dry_tick = orig_tick
            os.environ.pop("SEAT_ANSWERS", None)
        said = str(cm.exception)
        self.assertTrue(said.startswith("Stopped at step 5: the kit's self tests failed (5 did not pass: "
                                        "Fake.test_b_errors, "), said)
        self.assertIn("full output in %s)" % (self.kit / "selftest-last.txt"), said)


def tree(folder):
    """{relative path: sha256 or None for a folder} under folder."""
    out = {}
    for p in sorted(pathlib.Path(folder).rglob("*")):
        out[p.relative_to(folder).as_posix()] = None if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class SelftestMode(unittest.TestCase):
    """Item 2: `seat_setup.py selftest` prints what it found, writes only the report in the kit home, makes no plugin
    call, and exits 0 or 1."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = _helpers.real(self.td) / "with space"
        self.profile = self.base / "profile"
        self.kit_home = self.profile / "AppData" / "Local" / "seat-kit"
        for rel, text in (("Desktop/notes.txt", "mine"), ("Assistant/Helper/CLAUDE.md", "# my helper\n"),
                          ("Assistant/Helper/.seat/seat.json", '{"seat_name": "Helper"}'),
                          ("AppData/Roaming/Claude/settings.json", "{}"), ("Her Folder/CLAUDE.md", "# hers\n"),
                          ("AppData/Local/seat-kit/python.txt", "python.exe\n")):
            (self.profile / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.profile / rel).write_text(text, encoding="utf-8")
        self.env = {k: os.environ.get(k) for k in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOME")}
        os.environ.update(USERPROFILE=str(self.profile), APPDATA=str(self.profile / "AppData" / "Roaming"),
                          LOCALAPPDATA=str(self.profile / "AppData" / "Local"), HOME=str(self.profile))
        self.saved = (s._claude, s.ensure_plugin, s.setup_seat, s.adopt, s.make_shortcut, s.desktop_folder,
                      builtins.input)

        def refuse(*a, **k):
            raise AssertionError("selftest must not do this")
        s._claude = s.ensure_plugin = s.setup_seat = s.adopt = s.make_shortcut = s.desktop_folder = refuse
        builtins.input = refuse

    def tearDown(self):
        (s._claude, s.ensure_plugin, s.setup_seat, s.adopt, s.make_shortcut, s.desktop_folder,
         builtins.input) = self.saved
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.td.cleanup()

    def run_mode(self, source, *extra):
        kit = fake_kit(self.base / ("suite %d" % len(list(self.base.iterdir()))), source)
        before = tree(self.profile)
        buf = io.StringIO()
        with Patched(kit), redirect_stdout(buf):
            rc = s.main(["selftest", "--kit-home", str(self.kit_home)] + list(extra))
        after = tree(self.profile)
        made = {k: v for k, v in after.items() if k not in before or before[k] != v}
        self.assertEqual({k: v for k, v in before.items() if k not in after}, {})
        self.assertEqual(sorted(made), ["AppData/Local/seat-kit/selftest-last.txt"])   # nothing else, anywhere
        return rc, buf.getvalue()

    def test_all_passing_exits_0_and_says_what_it_found(self):
        rc, out = self.run_mode(PASSING_SUITE)
        self.assertEqual(rc, 0, out)
        for want in ("Self test only: nothing was installed or set up, no plugin call was made",
                     "Kit version:  %s" % s.kit_version(), "Python:       ", "Windows:      ",
                     "Tests:        3 ran, 2 passed, 0 failed, 1 skipped",
                     "Full output:  %s" % (self.kit_home / "selftest-last.txt"), "All self tests passed."):
            self.assertIn(want, out)
        self.assertNotIn("Did not pass", out)

    def test_a_failure_exits_1_and_names_each_test_with_its_reason(self):
        rc, out = self.run_mode(FAKE_SUITE)
        self.assertEqual(rc, 1, out)
        # 0.1.6: failed counts tests (6 = 1 + 4 + 1); test_d_fails's two failing sub-cases make unittest's count 5
        self.assertIn("Tests:        6 ran, 1 passed, 4 failed (5 failures counting sub-cases), 1 skipped", out)
        for want in ("  Fake.test_a_fails: AssertionError: 1 != 2", "  Fake.test_b_errors: KeyError: 'no such key'",
                     "  Fake.test_d_fails [cmd]: AssertionError: this one failed in cmd",
                     "  Fake.test_d_fails [ps1]: AssertionError: this one failed in ps1"):
            self.assertIn(want, out)
        self.assertIn("Some self tests did not pass", out)

    def test_the_summary_says_what_the_comparisons_ignored_every_run(self):
        mw = ["AppData/Local/Microsoft", "AppData/Local/Malwarebytes"]
        for source, noise, want, also in (
                (PASSING_SUITE, None, "Ignored:      not reported (the tests printed no list of ignored folders)", None),
                (PASSING_SUITE, [], "Ignored:      nothing", None),
                (PASSING_SUITE, mw[:1], "Ignored:      AppData\\Local\\Microsoft (folders other programs make "
                                        "whenever PowerShell starts)", None),
                (PASSING_SUITE, mw, "Ignored:      AppData\\Local\\Microsoft, AppData\\Local\\Malwarebytes (folders "
                                    "other programs make whenever PowerShell starts)", "Malwarebytes"),
                (FAKE_SUITE, mw, "Ignored:      AppData\\Local\\Microsoft, AppData\\Local\\Malwarebytes (folders "
                                 "other programs make whenever PowerShell starts)", "Malwarebytes")):
            with self.subTest(noise=noise, failing=source is FAKE_SUITE):
                rc, out = self.run_mode(with_noise(source, noise))
                self.assertEqual(rc, 1 if source is FAKE_SUITE else 0, out)
                lines = out.splitlines()
                tests = [n for n, ln in enumerate(lines) if ln.startswith("Tests:        ")]
                self.assertEqual(len(tests), 1, out)
                self.assertEqual(lines[tests[0] + 1], want, out)                # right after the Tests line
                if also:
                    self.assertEqual(lines[tests[0] + 2], "Also seen and ignored: %s" % also, out)
                self.assertEqual(len([ln for ln in lines if ln.startswith("Also seen and ignored:")]), 1 if also else 0,
                                 out)
                self.assertEqual(len([ln for ln in lines if ln.startswith("Ignored:")]), 1, out)
                self.assertNotIn(s.NOISE_MARKER, out)

    def test_a_tear_down_error_with_every_test_passing_still_fails_and_is_named(self):
        rc, out = self.run_mode(FIXTURE_SUITE)
        self.assertEqual(rc, 1, out)
        self.assertIn("Tests:        2 ran, 2 passed, 0 failed, 0 skipped; 1 error outside any test "
                      "(Tear.tearDownClass)", out.splitlines())
        self.assertIn("  Tear.tearDownClass: RuntimeError: tear down broke", out.splitlines())
        self.assertIn("Some self tests did not pass", out)

    def test_three_sub_case_skips_in_two_tests_count_two_skipped(self):
        rc, out = self.run_mode(SUBSKIP_SUITE)
        self.assertEqual(rc, 0, out)
        (line,) = [ln for ln in out.splitlines() if ln.startswith("Tests:")]
        # 3 = 1 + 0 + 2; Python 3.11 and newer name each skipped sub-case, so the line adds the count of three
        self.assertTrue(line.startswith("Tests:        3 ran, 1 passed, 0 failed, 2 skipped"), line)
        self.assertIn(line[len("Tests:        3 ran, 1 passed, 0 failed, 2 skipped"):],
                      ("", " (3 skips counting sub-cases)"))
        self.assertIn("All self tests passed.", out)

    def test_a_bad_flag_is_a_plain_line_and_exit_1(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = s.main(["selftest", "--kit-home", str(self.kit_home), "--nope"])
        self.assertEqual(rc, 1)
        self.assertIn("Stopped at step 4: the selftest line was not understood", buf.getvalue())


class Launchers(unittest.TestCase):
    """Item 2 in the launchers' own text (read, not run): selftest takes the no-install road and calls only
    seat_setup.py selftest."""

    def test_both_launchers_route_selftest_without_installing(self):
        cmd = (_helpers.KIT / "launcher" / "Assistant.cmd").read_bytes().decode("utf-8").replace("\r\n", "\n")
        self.assertIn('if /i "%~1"=="selftest"', cmd)
        section = cmd.split("\n:selftest\n", 1)[1].split("\n:", 1)[0]
        self.assertIn('seat_setup.py" selftest --kit-home "%KIT_HOME%"', section)
        for never in ("plugin install", "plugin marketplace", "-n ", "copy ", "--claude", "--seat-home"):
            self.assertNotIn(never, section)
        ps1 = (_helpers.KIT / "launcher" / "Assistant.ps1").read_bytes().decode("utf-8").replace("\r\n", "\n")
        self.assertIn("$args[0] -eq 'selftest'", ps1)
        block = ps1.split("if ($SelfTest) {\n", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("'selftest', '--kit-home', $KitHome", block)
        for never in ("plugin install", "plugin marketplace", "-n ", "Copy-Item", "--claude", "--seat-home"):
            self.assertNotIn(never, block)


if __name__ == "__main__":
    unittest.main()
