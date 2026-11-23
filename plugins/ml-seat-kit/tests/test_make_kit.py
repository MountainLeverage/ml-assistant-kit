"""Checks for tools/make_kit.py: a release build never silently goes without the private name list."""
import contextlib
import io
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
    PASSED = {"ok": True, "ran": 120, "summary": "OK", "skipped": 0, "failed": [], "output": "OK"}

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

    def test_the_real_runner_is_the_default(self):
        self.assertIs(mk.build.__defaults__[-1], mk.run_tests)


if __name__ == "__main__":
    unittest.main()
