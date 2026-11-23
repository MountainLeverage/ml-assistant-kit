"""Checks for tools/make_kit.py: a release build never silently goes without the private name list."""
import contextlib
import io
import sys
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


if __name__ == "__main__":
    unittest.main()
