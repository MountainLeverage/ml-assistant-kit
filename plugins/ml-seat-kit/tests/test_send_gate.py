"""Checks for scripts/send_gate.py and its matcher in hooks/hooks.json. Nothing here can deliver mail: the gate has
no send path at all, and these tests only feed it fake events."""
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

from tests import _helpers
import send_gate as g

GATE = str(_helpers.SCRIPTS / "send_gate.py")
SENDS = ["mcp__claude_ai_Gmail__send_message", "mcp__claude_ai_Gmail__reply", "mcp__claude_ai_Gmail__forward",
         "mcp__claude_ai_Gmail__send_draft", "mcp__claude_ai_Google_Calendar__respond_to_event",
         "mcp__gmail__gmail_send_message", "mcp__mail__sendMessage", "mcp__outlook__reply_all",
         "mcp__claude_ai_Brain__agent_send", "mcp__x__create_and_send", "mcp__cal__update_rsvp"]
PASSES = ["mcp__claude_ai_Gmail__create_draft", "mcp__claude_ai_Gmail__update_draft",
          "mcp__claude_ai_Gmail__create_reply_draft", "mcp__claude_ai_Gmail__list_replies",
          "mcp__claude_ai_Google_Calendar__get_event_responses"]
NEVER_MATCHED = ["mcp__claude_ai_Gmail__create_draft", "mcp__claude_ai_Gmail__search_threads",
                 "mcp__claude_ai_Gmail__label_thread", "Bash", "Write"]


def ev(tool, cwd):
    return {"session_id": "s", "hook_event_name": "PreToolUse", "cwd": cwd, "tool_name": tool,
            "tool_input": {"to": ["someone@example.invalid"], "subject": "x", "body": "y"}}


class Env:
    """Run with CLAUDE_PROJECT_DIR and the process cwd pointing where we say."""

    def __init__(self, project, cwd):
        self.project, self.cwd = project, cwd

    def __enter__(self):
        self.old_env, self.old_cwd = os.environ.get("CLAUDE_PROJECT_DIR"), os.getcwd()
        if self.project is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = self.project
        os.chdir(self.cwd)

    def __exit__(self, *a):
        os.chdir(self.old_cwd)
        if self.old_env is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = self.old_env


def run_main(stdin_text):
    err = io.StringIO()
    with redirect_stderr(err):
        code = g.main(io.StringIO(stdin_text))
    return code, err.getvalue()


class Decide(unittest.TestCase):
    def test_sends_are_denied(self):
        for t in SENDS:
            deny, why = g.decide({"tool_name": t})
            self.assertTrue(deny, t)
            self.assertTrue(why.startswith("Drafts only:"), t)

    def test_reads_and_drafts_pass(self):
        for t in PASSES:
            self.assertEqual(g.decide({"tool_name": t}), (False, ""), t)

    def test_unknown_shapes_are_denied(self):
        for e in (None, [], "text", {}, {"tool_name": ""}, {"tool_name": 7}, {"tool_name": "mcp__x__frobnicate"}):
            self.assertTrue(g.decide(e)[0], repr(e))

    def test_an_event_with_guests_is_denied(self):
        cal = "mcp__claude_ai_Google_Calendar__create_event"
        for inp in ({"summary": "x", "attendees": ["a@example.invalid"]},
                    {"summary": "x", "attendees": [{"email": "a@example.invalid"}]},
                    {"event": {"guests": "a@example.invalid"}}, {"invitees": ["a@example.invalid"]},
                    {"body": {"participants": [{"email": "a@example.invalid"}]}}):
            deny, why = g.decide({"tool_name": cal, "tool_input": inp})
            self.assertTrue(deny, inp)
            self.assertEqual(why, g.NO_INVITE)
        for bad in (None, "text", ["a"]):                   # an input the gate cannot read: refuse
            self.assertTrue(g.decide({"tool_name": cal, "tool_input": bad})[0], repr(bad))
        self.assertTrue(g.decide({"tool_name": cal})[0])

    def test_an_event_with_no_guests_passes(self):
        for t in ("mcp__claude_ai_Google_Calendar__create_event", "mcp__cal__createEvent", "mcp__cal__quick_add_event"):
            for inp in ({"summary": "Focus block", "start": "2026-11-23T09:00"},
                        {"summary": "x", "attendees": []}, {"summary": "x", "attendees": None}):
                self.assertEqual(g.decide({"tool_name": t, "tool_input": inp}), (False, ""), (t, inp))

    def test_changing_or_removing_an_event_is_denied(self):
        for t in ("mcp__claude_ai_Google_Calendar__update_event", "mcp__claude_ai_Google_Calendar__delete_event",
                  "mcp__cal__move_event", "mcp__cal__patchEvent", "mcp__cal__cancel_meeting",
                  "mcp__cal__invite_to_event", "mcp__cal__add_invitees", "mcp__cal__frobnicate_event"):
            deny, why = g.decide({"tool_name": t, "tool_input": {"event_id": "e1", "summary": "x"}})
            self.assertTrue(deny, t)
            self.assertEqual(why, g.NO_INVITE)

    def test_reading_events_passes(self):
        for t in ("mcp__claude_ai_Google_Calendar__list_events", "mcp__claude_ai_Google_Calendar__get_event",
                  "mcp__cal__search_events", "mcp__cal__find_meeting_times"):
            self.assertEqual(g.decide({"tool_name": t, "tool_input": {"q": "x"}}), (False, ""), t)

    def test_plain_reasons(self):
        self.assertEqual(g.decide({"tool_name": SENDS[0]})[1],
                         "Drafts only: I wrote the draft, you send it yourself from Gmail.")
        self.assertIn("Google Calendar", g.decide({"tool_name": SENDS[4]})[1])


class Hook(unittest.TestCase):
    def setUp(self):
        self.td, self.seat = _helpers.make_seat()
        self.out = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.td.cleanup()
        self.out.cleanup()

    def test_outside_a_seat_does_nothing(self):
        with Env(None, self.out.name):
            for t in SENDS:
                self.assertEqual(run_main(json.dumps(ev(t, self.out.name))), (0, ""))
            self.assertEqual(run_main("not json"), (0, ""))
            self.assertEqual(run_main(""), (0, ""))

    def test_in_a_seat_denies_with_exit_2(self):
        with Env(str(self.seat), str(self.seat)):
            code, err = run_main(json.dumps(ev(SENDS[0], str(self.seat))))
        self.assertEqual(code, 2)
        self.assertIn("Drafts only", err)
        self.assertIn("DENY mcp__claude_ai_Gmail__send_message",
                      (self.seat / ".seat" / "logs" / "send_gate.log").read_text())

    def test_in_a_seat_fails_closed(self):
        with Env(str(self.seat), str(self.seat)):
            for bad in ("not json", "", "[1, 2]", json.dumps({"cwd": str(self.seat)})):
                code, err = run_main(bad)
                self.assertEqual(code, 2, bad)
                self.assertIn("Drafts only", err)

    def test_any_seat_place_counts(self):
        sub = self.seat / "state"
        with Env(None, self.out.name):                     # only the event's cwd is a seat (a subfolder of one)
            self.assertEqual(run_main(json.dumps(ev(SENDS[0], str(sub))))[0], 2)
        with Env(str(self.seat), self.out.name):           # only the project dir is a seat
            self.assertEqual(run_main(json.dumps(ev(SENDS[0], self.out.name)))[0], 2)

    def test_byte_order_mark_is_read(self):
        body = json.dumps(ev(SENDS[0], str(self.seat))).encode("utf-8")
        for raw in (b"\xef\xbb\xbf" + body, body):
            self.assertEqual(json.loads(g.read_event_text(io.BytesIO(raw)))["tool_name"], SENDS[0])
        draft = json.dumps(ev(PASSES[0], str(self.seat))).encode("utf-8")
        with Env(str(self.seat), str(self.seat)):
            self.assertEqual(g.main(io.BytesIO(b"\xef\xbb\xbf" + draft)), 0)

    def test_drafts_pass_in_a_seat(self):
        with Env(str(self.seat), str(self.seat)):
            self.assertEqual(run_main(json.dumps(ev(PASSES[0], str(self.seat)))), (0, ""))

    def test_real_process(self):
        """The hook as Claude Code runs it: a fresh python -I process, JSON on stdin."""
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.seat))
        r = subprocess.run([sys.executable, "-I", GATE], input=json.dumps(ev(SENDS[0], str(self.seat))),
                           capture_output=True, text=True, timeout=60, cwd=str(self.seat), env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("Drafts only", r.stderr)
        env["CLAUDE_PROJECT_DIR"] = self.out.name
        r = subprocess.run([sys.executable, "-I", GATE], input=json.dumps(ev(SENDS[0], self.out.name)),
                           capture_output=True, text=True, timeout=60, cwd=self.out.name, env=env)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_probe_sends_nothing(self):
        r = subprocess.run([sys.executable, "-I", GATE, "--probe"], capture_output=True, text=True, timeout=60)
        self.assertTrue(r.stdout.startswith("DENY Drafts only"))


class Matcher(unittest.TestCase):
    def test_hooks_json_matcher(self):
        hooks = json.loads((_helpers.KIT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        entry = hooks["hooks"]["PreToolUse"][0]
        rx = re.compile(entry["matcher"])
        for t in SENDS + ["mcp__claude_ai_Google_Calendar__create_event", "mcp__cal__updateEvent",
                          "mcp__claude_ai_Google_Calendar__delete_event", "mcp__cal__add_invitees",
                          "mcp__cal__cancel_meeting"]:
            self.assertTrue(rx.fullmatch(t) and rx.search(t), t)
        for t in NEVER_MATCHED:
            self.assertFalse(rx.search(t), t)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/send_gate.py", entry["hooks"][0]["command"])


if __name__ == "__main__":
    unittest.main()
