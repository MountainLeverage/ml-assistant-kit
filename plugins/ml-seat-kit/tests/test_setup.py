"""Checks for scripts/seat_setup.py. Everything happens in temp folders; `claude` is faked, no shortcut is made."""
import argparse
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

from tests import _helpers
import seat_common
import seat_setup as s


def args(tmp, ask=False):
    return argparse.Namespace(claude="claude", kit_home=str(tmp / "kit"), seat_home=str(tmp / "seats"),
                              desktop=str(tmp / "no-desktop"), ask=ask, no_plugin=True, no_tests=True)


class Setup(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self.td.name)
        (self.tmp / "kit").mkdir()
        self.orig_tick = s.dry_tick
        s.dry_tick = lambda seat: (True, "FREE: test")
        os.environ["SEAT_ANSWERS"] = "Pat|Juniper|Lee Park, Sam Ortiz"

    def tearDown(self):
        s.dry_tick = self.orig_tick
        os.environ.pop("SEAT_ANSWERS", None)
        self.td.cleanup()

    def run_setup(self, ask=False):
        buf = io.StringIO()
        with redirect_stdout(buf):
            seat, cfg = s.setup_seat(args(self.tmp, ask))
        return seat, cfg, buf.getvalue()

    def test_first_run_makes_the_seat(self):
        seat, cfg, out = self.run_setup()
        self.assertEqual(seat, self.tmp / "seats" / "Juniper")
        self.assertTrue((seat / ".seat" / "seat.json").is_file())
        self.assertEqual((cfg["person_name"], cfg["seat_name"], cfg["leadership"]), ("Pat", "Juniper", ["Lee Park", "Sam Ortiz"]))
        for rel in ("state/open-items.md", "state/corrections.md", "state/senders.md", "state/decisions.md",
                    "state/ledger.jsonl", "state/wakes.jsonl", "CLAUDE.md", ".claude/settings.json"):
            self.assertTrue((seat / rel).is_file(), rel)
        md = (seat / "CLAUDE.md").read_text()
        self.assertIn("# Juniper, Pat's assistant", md)
        self.assertIn("Lee Park, Sam Ortiz", md)
        self.assertNotIn("{{", md)
        self.assertEqual((self.tmp / "kit" / "last-seat.txt").read_text().strip(), "Juniper|Juniper")
        self.assertIn("Step 5 of 6: setting up your assistant's folder ... ", out)
        self.assertIn("What only you can do", out)

    def test_settings_hold_deny_rules_and_the_gate_only(self):
        seat, _, _ = self.run_setup()
        data = json.loads((seat / ".claude" / "settings.json").read_text())
        self.assertEqual(set(data), {"permissions", "hooks"})
        self.assertEqual(set(data["permissions"]), {"deny"})
        self.assertIn("mcp__claude_ai_Gmail__send_message", data["permissions"]["deny"])
        self.assertFalse((seat / ".claude" / "settings.local.json").exists())
        (entry,) = data["hooks"]["PreToolUse"]
        hook = entry["hooks"][0]
        self.assertEqual(hook["command"], os.path.abspath(sys.executable))   # the Python setup ran with
        self.assertEqual(hook["onFailure"], "block")
        self.assertEqual(pathlib.Path(hook["args"][1]),
                         (self.tmp / "kit").resolve() / "current/plugins/ml-seat-kit/scripts/send_gate.py")

    def test_gate_is_replaced_not_doubled_and_her_hooks_stay(self):
        seat, _, _ = self.run_setup()
        p = seat / ".claude" / "settings.json"
        data = json.loads(p.read_text())
        old = data["hooks"]["PreToolUse"][0]
        old["hooks"][0]["command"] = "C:\\old\\python.exe"                    # a Python that has since moved
        hers = {"matcher": "Bash", "hooks": [{"type": "command", "command": "her-check"}]}
        data["hooks"]["PreToolUse"].insert(0, hers)
        p.write_text(json.dumps(data))
        self.run_setup()
        pre = json.loads(p.read_text())["hooks"]["PreToolUse"]
        self.assertEqual(pre[0], hers)
        self.assertEqual(len(pre), 2)
        self.assertEqual(pre[1]["hooks"][0]["command"], os.path.abspath(sys.executable))
        _, _, out = self.run_setup()
        self.assertNotIn("settings", out.split("done (", 1)[1])                 # third run: nothing to change

    def test_unreadable_settings_stop_setup(self):
        seat, _, _ = self.run_setup()
        (seat / ".claude" / "settings.json").write_text("{ not json")
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(s.Stop) as cm:
                s.setup_seat(args(self.tmp))
        self.assertIn("send gate could not be put in place", str(cm.exception))

    def test_existing_settings_keep_her_entries(self):
        seat, _, _ = self.run_setup()
        p = seat / ".claude" / "settings.json"
        p.write_text(json.dumps({"permissions": {"allow": ["mcp__her__own_rule"], "deny": ["x"]}, "model": "m"}))
        self.run_setup()
        data = json.loads(p.read_text())
        self.assertEqual(data["permissions"]["allow"], ["mcp__her__own_rule"])   # hers, untouched
        self.assertEqual(data["model"], "m")
        self.assertIn("x", data["permissions"]["deny"])
        self.assertIn("mcp__claude_ai_Google_Calendar__respond_to_event", data["permissions"]["deny"])

    def test_second_run_overwrites_nothing_of_hers(self):
        seat, _, _ = self.run_setup()
        mine = {"state/ledger.jsonl": '{"thread":"t1"}\n', "state/open-items.md": "# mine\n- 1. keep me\n",
                "state/corrections.md": "my rules", "state/senders.md": "my senders",
                "state/decisions.md": "my decisions", "state/wakes.jsonl": '{"op":"add"}\n',
                "CLAUDE.md": "my own CLAUDE.md, edited"}
        for rel, text in mine.items():
            (seat / rel).write_text(text)
        os.environ["SEAT_ANSWERS"] = "Someone|Else|Nobody"     # must NOT be asked again
        seat2, cfg, out = self.run_setup()
        self.assertEqual(seat2, seat)
        self.assertEqual(cfg["seat_name"], "Juniper")
        for rel, text in mine.items():
            self.assertEqual((seat / rel).read_text(), text, rel)
        self.assertNotIn("Three quick questions", out)
        self.assertEqual(len(s.find_seats(self.tmp / "seats")), 1)

    def test_ask_again_updates_only_the_about_block(self):
        seat, _, _ = self.run_setup()
        md = seat / "CLAUDE.md"
        md.write_text(md.read_text() + "\n## Her own section\nkeep this\n")
        os.environ["SEAT_ANSWERS"] = "|| Ana Ruiz"           # name skipped: keep it; new leadership
        seat2, cfg, _ = self.run_setup(ask=True)
        self.assertEqual(seat2, seat)
        self.assertEqual((cfg["person_name"], cfg["seat_name"], cfg["leadership"]), ("Pat", "Juniper", ["Ana Ruiz"]))
        text = md.read_text()
        self.assertIn("Ana Ruiz", text)
        self.assertNotIn("Lee Park", text)
        self.assertIn("keep this", text)

    def test_skipped_answers_give_a_working_seat(self):
        os.environ["SEAT_ANSWERS"] = "||"
        seat, cfg, _ = self.run_setup()
        self.assertEqual(seat.name, "Assistant")
        md = (seat / "CLAUDE.md").read_text()
        self.assertIn("# Assistant, your assistant", md)
        self.assertNotIn("{{", md)

    def test_names_are_cleaned(self):
        os.environ["SEAT_ANSWERS"] = 'Pat|..\\Evil/"Name"|'
        seat, cfg, _ = self.run_setup()
        self.assertEqual(seat.parent, self.tmp / "seats")
        self.assertEqual(cfg["seat_name"], "EvilName")

    def test_failed_self_tests_stop_setup(self):
        orig = s.run_self_tests
        s.run_self_tests = lambda: (False, "FAILED (failures=1)")
        a = args(self.tmp)
        a.no_tests = False
        try:
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(s.Stop) as cm:
                    s.setup_seat(a)
        finally:
            s.run_self_tests = orig
        self.assertIn("Stopped at step 5", str(cm.exception))


class Plugin(unittest.TestCase):
    def fake(self, responses):
        calls = []

        def _claude(claude, *a, timeout=180):
            calls.append(" ".join(a))
            for key, val in responses.items():
                if " ".join(a).startswith(key):
                    return val
            return 0, ""
        return calls, _claude

    def test_first_time_adds_and_installs(self):
        calls, f = self.fake({"plugin marketplace list": (0, "[]"), "plugin list": (0, "[]")})
        orig, s._claude = s._claude, f
        try:
            with redirect_stdout(io.StringIO()) as out:
                s.ensure_plugin("claude", "C:\\kit-home")
        finally:
            s._claude = orig
        self.assertTrue(calls[1].startswith("plugin marketplace add ") and calls[1].endswith("current --scope user"))
        self.assertEqual(calls[3], "plugin install ml-seat-kit@seat-kit --scope user")
        self.assertIn("done (installed", out.getvalue())

    def test_later_runs_update(self):
        calls, f = self.fake({"plugin marketplace list": (0, '[{"name": "seat-kit"}]'),
                              "plugin list": (0, '[{"id": "ml-seat-kit@seat-kit"}]')})
        orig, s._claude = s._claude, f
        try:
            with redirect_stdout(io.StringIO()):
                s.ensure_plugin("claude", "kit")
        finally:
            s._claude = orig
        self.assertEqual(calls, ["plugin marketplace list --json", "plugin marketplace update seat-kit",
                                 "plugin list --json", "plugin update ml-seat-kit@seat-kit"])

    def test_a_failure_stops_with_a_plain_line(self):
        calls, f = self.fake({"plugin marketplace list": (124, "timed out after 180 s")})
        orig, s._claude = s._claude, f
        try:
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(s.Stop) as cm:
                    s.ensure_plugin("claude", "kit")
        finally:
            s._claude = orig
        self.assertTrue(str(cm.exception).startswith("Stopped at step 4: "))
        self.assertIn("What to do:", str(cm.exception))


class Marker(unittest.TestCase):
    def test_marker_is_what_the_gate_reads(self):
        import send_gate
        self.assertEqual(pathlib.Path(send_gate.MARKER), seat_common.MARKER)


if __name__ == "__main__":
    unittest.main()
