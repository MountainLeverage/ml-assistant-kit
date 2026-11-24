"""Checks for adopt mode (seat_setup.py adopt): the kit put onto a folder the person already uses, in place.

Everything happens in temp folders whose names hold a space. `claude` is a fake (an empty file plus a recorder in
place of the plugin calls); no plugin call reaches anything, no shortcut is made, no session is opened.
"""
import builtins
import codecs
import hashlib
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
import seat_common
import seat_setup as s

HER_MD = "# My helper\n\nKeep replies short.\nSign as me.\n"
HER_SETTINGS = {
    "permissions": {"allow": ["mcp__claude_ai_Gmail__search_threads"],
                    "deny": ["Bash(rm:*)", "mcp__claude_ai_Gmail__forward"]},
    "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "her-check.cmd"}]}],
              "Stop": [{"hooks": [{"type": "command", "command": "her-stop.cmd"}]}]},
    "model": "her-model",
    "env": {"HER_SETTING": "1"},
}
HER_LAUNCHER = b'@echo off\r\n"D:\\Tools\\claude.exe" %*\r\n'
LAUNCHER_NAMES = ["assistant.cmd", "Assistant.cmd", "ASSISTANT.CMD", "assistant.ps1", "Assistant.ps1", "ASSISTANT.PS1"]


def listing(folder):
    """{relative path: (size, sha256)} for every file under folder, and None for every folder."""
    out = {}
    for p in sorted(pathlib.Path(folder).rglob("*")):
        rel = p.relative_to(folder).as_posix()
        out[rel] = None if p.is_dir() else (p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest())
    return out


class AdoptBase(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.tmp = _helpers.real(self.td) / "with space"
        self.kit = self.tmp / "kit home"
        (self.kit / "current").mkdir(parents=True)
        self.folder = self.tmp / "Her Folder"
        self.folder.mkdir()
        self.claude = self.tmp / "bin" / "claude.exe"
        self.claude.parent.mkdir()
        self.claude.write_bytes(b"")
        self.calls, self.tests_ran, self.asked = [], [], []
        self.saved = (s._claude, s.run_self_tests, s.make_shortcut, s.desktop_folder, builtins.input)

        def fake_claude(claude, *a, timeout=180):
            self.calls.append(" ".join(a))
            return 0, "[]" if "list" in a else "ok"

        def no_shortcut(*a, **k):
            raise AssertionError("adopt must not make a desktop shortcut")

        def no_question(prompt=""):
            raise AssertionError("adopt asked a question: %s" % prompt)

        s._claude = fake_claude
        s.run_self_tests = lambda *a: (self.tests_ran.append(1), (True, "OK"))[1]
        s.make_shortcut = s.desktop_folder = no_shortcut
        builtins.input = no_question

    def tearDown(self):
        s._claude, s.run_self_tests, s.make_shortcut, s.desktop_folder, builtins.input = self.saved
        self.td.cleanup()

    def her_folder(self, md=HER_MD.encode("utf-8"), settings=HER_SETTINGS, launcher="assistant.cmd"):
        if md is not None:
            (self.folder / "CLAUDE.md").write_bytes(md)
        if settings is not None:
            (self.folder / ".claude").mkdir(exist_ok=True)
            (self.folder / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
        if launcher:
            (self.folder / launcher).write_bytes(HER_LAUNCHER)

    def run_adopt(self, *argv, folder=None, name="Helper"):
        args = ["adopt"] + list(argv)
        if name is not None and "--undo" not in argv:
            args += ["--name", name]
        args += [str(folder or self.folder), "--claude", str(self.claude), "--kit-home", str(self.kit),
                 "--seat-home", str(self.tmp / "seats"), "--launcher", str(self.tmp / "Downloads" / "Assistant.cmd")]
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = s.main(args)
        return rc, buf.getvalue()

    def settings(self):
        return json.loads((self.folder / ".claude" / "settings.json").read_text(encoding="utf-8"))

    def backups(self):
        return sorted((self.folder / ".seat" / "backup").iterdir())


class Check(AdoptBase):
    """Item 1: --check prints what the real run would do and writes nothing."""

    def test_check_writes_nothing_and_makes_no_plugin_call(self):
        self.her_folder()
        before = listing(self.folder)
        rc, out = self.run_adopt("--check")
        self.assertEqual(rc, 0, out)
        self.assertEqual(listing(self.folder), before)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.tests_ran, [])
        for rel in ("state\\open-items.md", "state\\ledger.jsonl", "state\\wakes.jsonl", ".seat\\seat.json"):
            self.assertIn(str(self.folder / rel), out)
        self.assertIn(str(self.folder / ".seat" / "backup"), out)
        self.assertIn(str(self.folder / "CLAUDE.md"), out)
        self.assertIn("<!-- seat-kit:hard-limits start", out)
        self.assertIn("send gate hook (PreToolUse)", out)
        self.assertIn(sys.executable, out)
        self.assertIn("Register the kit plugin for your Windows user", out)
        self.assertIn(s.PLUGIN_EFFECT, out)
        self.assertIn("Helper", out)
        self.assertIn("Outside your folder", out)

    def test_check_lists_only_the_missing_deny_rules(self):
        self.her_folder()
        rc, out = self.run_adopt("--check")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("deny rule: mcp__claude_ai_Gmail__forward", out)       # she has it already
        self.assertIn("deny rule: mcp__claude_ai_Gmail__send_message", out)

    def test_check_asks_no_question(self):
        self.her_folder()
        rc, out = self.run_adopt("--check", name=None)
        self.assertEqual(rc, 0, out)
        self.assertIn("asks one question", out)

    def test_check_on_an_adopted_folder_says_so_and_writes_nothing(self):
        self.her_folder()
        self.run_adopt()
        before = listing(self.folder)
        self.calls.clear()
        rc, out = self.run_adopt("--check")
        self.assertEqual(rc, 0, out)
        self.assertIn("already adopted", out)
        self.assertIn("would change nothing", out)
        self.assertEqual(listing(self.folder), before)
        self.assertEqual(self.calls, [])

    def test_check_says_when_the_block_is_already_there(self):
        self.her_folder(md=(HER_MD + "\n" + s.hard_limits_block() + "\n").encode("utf-8"))
        rc, out = self.run_adopt("--check")
        self.assertIn("already there", out)


class Adopt(AdoptBase):
    def test_backup_holds_byte_identical_copies_and_a_manifest(self):
        """Item 2."""
        self.her_folder()
        md0 = (self.folder / "CLAUDE.md").read_bytes()
        st0 = (self.folder / ".claude" / "settings.json").read_bytes()
        rc, out = self.run_adopt()
        self.assertEqual(rc, 0, out)
        (bdir,) = self.backups()
        self.assertIn(str(bdir), out)
        self.assertEqual((bdir / "CLAUDE.md").read_bytes(), md0)
        self.assertEqual((bdir / ".claude" / "settings.json").read_bytes(), st0)
        man = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(man["complete"])
        self.assertEqual({m["path"] for m in man["modified"]}, {"CLAUDE.md", ".claude/settings.json"})
        created = {c["path"] for c in man["created"]}
        self.assertIn(".seat/seat.json", created)
        self.assertIn("state/open-items.md", created)
        for c in man["created"]:
            self.assertEqual(s._file_sha(self.folder / c["path"]), c["sha256"], c["path"])

    def test_her_settings_survive_the_merge(self):
        """Her deny rules, allow rules, hooks and other keys stay exactly as they were; the kit only adds."""
        self.her_folder()
        rc, out = self.run_adopt()
        self.assertEqual(rc, 0, out)
        data = self.settings()
        self.assertEqual(data["permissions"]["allow"], HER_SETTINGS["permissions"]["allow"])
        self.assertEqual(data["permissions"]["deny"][:2], HER_SETTINGS["permissions"]["deny"])
        self.assertEqual(sorted(set(data["permissions"]["deny"])), sorted(set(HER_SETTINGS["permissions"]["deny"] + s.DENY)))
        self.assertEqual(data["hooks"]["PreToolUse"][0], HER_SETTINGS["hooks"]["PreToolUse"][0])
        self.assertEqual(data["hooks"]["Stop"], HER_SETTINGS["hooks"]["Stop"])
        self.assertEqual((data["model"], data["env"]), (HER_SETTINGS["model"], HER_SETTINGS["env"]))
        self.assertEqual(len(data["hooks"]["PreToolUse"]), 2)
        gate = data["hooks"]["PreToolUse"][1]["hooks"][0]
        self.assertEqual((gate["command"], gate["onFailure"]), (s.long_path(sys.executable), "block"))
        self.assertTrue(gate["args"][1].endswith("send_gate.py"))

    def test_claude_md_gets_one_block_and_hers_stays_byte_for_byte(self):
        """Item 7."""
        self.her_folder()
        rc, out = self.run_adopt()
        md = (self.folder / "CLAUDE.md").read_bytes()
        self.assertTrue(md.startswith(HER_MD.encode("utf-8")))
        tail = md[len(HER_MD.encode("utf-8")):].decode("utf-8")
        self.assertEqual(tail.count("<!-- seat-kit:hard-limits start"), 1)
        self.assertEqual(tail.count("<!-- seat-kit:hard-limits end -->"), 1)
        self.assertIn("**Never send.**", tail)
        self.assertNotIn("About the person", tail)
        self.assertNotIn("{{", tail)
        self.assertNotIn("The five outcomes", tail)

    def test_crlf_and_bom_are_kept(self):
        for label, md in (("crlf", HER_MD.replace("\n", "\r\n").encode("utf-8")),
                          ("bom", codecs.BOM_UTF8 + HER_MD.encode("utf-8")),
                          ("bom crlf", codecs.BOM_UTF8 + HER_MD.replace("\n", "\r\n").encode("utf-8")),
                          ("no final newline", b"# Mine\nlast line")):
            with self.subTest(label):
                self.tearDown()
                self.setUp()
                self.her_folder(md=md)
                rc, out = self.run_adopt()
                self.assertEqual(rc, 0, out)
                new = (self.folder / "CLAUDE.md").read_bytes()
                self.assertTrue(new.startswith(md))
                added = new[len(md):]
                self.assertFalse(added.startswith(codecs.BOM_UTF8))
                if b"\r\n" in md:
                    self.assertEqual(added.count(b"\n"), added.count(b"\r\n"))
                else:
                    self.assertNotIn(b"\r", added)
                rc, out = self.run_adopt("--undo")
                self.assertEqual((self.folder / "CLAUDE.md").read_bytes(), md)

    def test_block_already_there_is_not_appended_again(self):
        md = (HER_MD + "\n" + s.hard_limits_block() + "\n").encode("utf-8")
        self.her_folder(md=md)
        rc, out = self.run_adopt()
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.folder / "CLAUDE.md").read_bytes(), md)
        (bdir,) = self.backups()
        self.assertFalse((bdir / "CLAUDE.md").exists())

    def test_no_claude_md_gets_one_with_the_block_only(self):
        self.her_folder(md=None)
        rc, out = self.run_adopt()
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.folder / "CLAUDE.md").read_text(encoding="utf-8"), s.hard_limits_block() + "\n")

    def test_name_goes_only_to_the_marker_and_no_shortcut(self):
        """Item 5: --name means no question; nothing renamed; make_shortcut raises if called."""
        self.her_folder()
        names = sorted(os.listdir(self.folder))
        rc, out = self.run_adopt(name="Helper Two")
        self.assertEqual(rc, 0, out)
        self.assertEqual(seat_common.load_config(self.folder)["seat_name"], "Helper Two")
        self.assertTrue(seat_common.load_config(self.folder)["adopted"])
        self.assertTrue(self.folder.is_dir() and self.folder.name == "Her Folder")
        self.assertTrue(set(names) <= set(os.listdir(self.folder)))
        self.assertNotIn("desktop", out.lower().replace("no desktop shortcut", ""))

    def test_without_name_one_question_is_asked(self):
        self.her_folder()
        builtins.input = lambda prompt="": (self.asked.append(prompt), "Helper Three")[1]
        rc, out = self.run_adopt(name=None)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(seat_common.load_config(self.folder)["seat_name"], "Helper Three")

    def test_real_run_registers_the_plugin_and_runs_self_tests(self):
        """Item 9 (self tests) and item 1's plugin note: the calls happen in the real run only."""
        self.her_folder()
        rc, out = self.run_adopt()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.tests_ran, [1])
        self.assertEqual(self.calls[0], "plugin marketplace list --json")
        self.assertIn("plugin install ml-seat-kit@seat-kit --scope user", self.calls)
        self.assertIn("Checked: the send gate and all %d deny rules" % len(s.DENY), out)

    def test_failed_self_tests_change_nothing(self):
        self.her_folder()
        before = listing(self.folder)
        s.run_self_tests = lambda *a: (False, "FAILED (failures=1)")
        rc, out = self.run_adopt()
        self.assertEqual(rc, 1)
        self.assertIn("self tests failed", out)
        self.assertEqual(listing(self.folder), before)
        self.assertEqual(self.calls, [])

    def test_a_missing_gate_after_writing_is_never_reported_as_adopted(self):
        """Item 9: settings are read back; no gate means a stop, not a success line."""
        self.her_folder()
        orig = s.gate_in_place
        s.gate_in_place = lambda folder, gate: False
        try:
            rc, out = self.run_adopt()
        finally:
            s.gate_in_place = orig
        self.assertEqual(rc, 1)
        self.assertIn("NOT adopted", out)
        self.assertNotIn("Your folder is adopted", out)
        self.assertEqual(self.calls, [])                          # no plugin either

    def test_output_says_how_to_start_and_opens_no_session(self):
        """Items 4 and 10."""
        self.her_folder()
        rc, out = self.run_adopt()
        self.assertIn("No session was opened", out)
        self.assertIn("with your own launcher (%s)" % (self.folder / "assistant.cmd"), out)
        self.assertIn("The send gate applies however the session is started", out)
        self.assertIn(sys.executable, out)
        self.assertNotIn("-n ", " ".join(self.calls))

    def test_store_python_stops_and_installs_nothing(self):
        """Item 6 (the Python side; the launchers' side is LauncherAdopt below)."""
        self.her_folder()
        before = listing(self.folder)
        orig = sys.executable
        sys.executable = "D:\\Programs\\WindowsApps\\PythonSoftwareFoundation.Python.3.13_x\\python.exe"
        try:
            rc, out = self.run_adopt()
        finally:
            sys.executable = orig
        self.assertEqual(rc, 1)
        self.assertIn("Microsoft Store", out)
        self.assertIn("python.org", out)
        self.assertEqual(listing(self.folder), before)
        self.assertEqual(self.calls, [])


class Again(AdoptBase):
    """Item 8, last part: a second adopt is a no-op, except an out-of-date gate (with a backup first)."""

    def test_second_adopt_changes_nothing(self):
        self.her_folder()
        self.run_adopt()
        first = listing(self.folder)
        rc, out = self.run_adopt(name="Other")
        self.assertEqual(rc, 0, out)
        self.assertIn("already adopted, nothing changed", out)
        self.assertEqual(listing(self.folder), first)
        self.assertEqual(seat_common.load_config(self.folder)["seat_name"], "Helper")

    def test_second_adopt_updates_a_moved_python_with_a_backup(self):
        self.her_folder()
        self.run_adopt()
        p = self.folder / ".claude" / "settings.json"
        data = self.settings()
        data["hooks"]["PreToolUse"][1]["hooks"][0]["command"] = "D:\\old\\python.exe"
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        stale = p.read_bytes()
        md = (self.folder / "CLAUDE.md").read_bytes()
        rc, out = self.run_adopt()
        self.assertEqual(rc, 0, out)
        self.assertIn("send gate updated", out)
        self.assertEqual(self.settings()["hooks"]["PreToolUse"][1]["hooks"][0]["command"], s.long_path(sys.executable))
        newest = self.backups()[-1]
        self.assertEqual((newest / ".claude" / "settings.json").read_bytes(), stale)
        self.assertEqual((self.folder / "CLAUDE.md").read_bytes(), md)
        self.assertEqual(len(self.settings()["hooks"]["PreToolUse"]), 2)


class Undo(AdoptBase):
    """Item 3."""

    def test_undo_restores_her_files_and_removes_only_what_adopt_made_unchanged(self):
        self.her_folder()
        before = listing(self.folder)
        md0 = (self.folder / "CLAUDE.md").read_bytes()
        st0 = (self.folder / ".claude" / "settings.json").read_bytes()
        rc, out = self.run_adopt()
        self.assertEqual(rc, 0, out)
        with open(self.folder / "state" / "open-items.md", "a", encoding="utf-8") as f:
            f.write("- 1. something she added\n")
        (self.folder / "state" / "her-notes.txt").write_text("hers", encoding="utf-8")
        rc, out = self.run_adopt("--undo")
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.folder / "CLAUDE.md").read_bytes(), md0)
        self.assertEqual((self.folder / ".claude" / "settings.json").read_bytes(), st0)
        self.assertFalse((self.folder / ".seat" / "seat.json").exists())
        self.assertTrue((self.folder / "state" / "open-items.md").exists())
        self.assertIn(str(self.folder / "state" / "open-items.md"), out)
        self.assertEqual((self.folder / "state" / "her-notes.txt").read_text(encoding="utf-8"), "hers")
        for rel in ("state/corrections.md", "state/ledger.jsonl", "state/wakes.jsonl"):
            self.assertFalse((self.folder / rel).exists(), rel)
        self.assertFalse((self.folder / ".cache").exists())
        after = {k: v for k, v in listing(self.folder).items() if not k.startswith(".seat")}
        expected = dict(before, **{"state": None, "state/open-items.md": after["state/open-items.md"],
                                   "state/her-notes.txt": after["state/her-notes.txt"]})
        self.assertEqual(after, expected)
        self.assertEqual((self.folder / "assistant.cmd").read_bytes(), HER_LAUNCHER)

    def test_undo_keeps_her_later_claude_md_edits(self):
        self.her_folder()
        self.run_adopt()
        p = self.folder / "CLAUDE.md"
        p.write_bytes(b"# A line she added later\n" + p.read_bytes())
        rc, out = self.run_adopt("--undo")
        self.assertEqual(p.read_bytes(), b"# A line she added later\n" + HER_MD.encode("utf-8"))
        self.assertIn("your later edits kept", out)

    def test_undo_leaves_settings_she_changed_and_says_how_to_put_them_back(self):
        self.her_folder()
        self.run_adopt()
        data = self.settings()
        data["permissions"]["allow"].append("mcp__claude_ai_Gmail__label_thread")
        (self.folder / ".claude" / "settings.json").write_text(json.dumps(data), encoding="utf-8")
        changed = (self.folder / ".claude" / "settings.json").read_bytes()
        rc, out = self.run_adopt("--undo")
        self.assertEqual((self.folder / ".claude" / "settings.json").read_bytes(), changed)
        self.assertIn("changed since adopt, so it was left as it is", out)
        self.assertIn("copy /y", out)

    def test_adopt_undo_adopt_undo_comes_back_to_the_start(self):
        self.her_folder()
        before = listing(self.folder)
        for _ in range(2):
            self.assertEqual(self.run_adopt()[0], 0)
            self.assertEqual(self.run_adopt("--undo")[0], 0)
        after = {k: v for k, v in listing(self.folder).items() if not k.startswith(".seat")}
        self.assertEqual(after, before)
        rc, out = self.run_adopt("--undo")
        self.assertIn("Nothing to undo", out)

    def test_undo_after_a_gate_update_still_comes_back_to_the_start(self):
        self.her_folder()
        st0 = (self.folder / ".claude" / "settings.json").read_bytes()
        self.run_adopt()
        orig = sys.executable
        sys.executable = "D:\\moved\\python.exe"                 # the Python moved since the first adopt
        try:
            rc, out = self.run_adopt()
        finally:
            sys.executable = orig
        self.assertIn("send gate updated", out)
        self.assertEqual(len(self.backups()), 2)
        self.run_adopt("--undo")
        self.assertEqual((self.folder / ".claude" / "settings.json").read_bytes(), st0)

    def test_undo_does_not_undo_a_launcher_seat(self):
        self.her_folder()
        (self.folder / ".seat").mkdir()
        (self.folder / ".seat" / "seat.json").write_text('{"seat_name": "X"}', encoding="utf-8")
        before = listing(self.folder)
        rc, out = self.run_adopt("--undo")
        self.assertEqual(rc, 1)
        self.assertIn("made by the kit's launcher", out)
        self.assertEqual(listing(self.folder), before)


class HerLauncher(AdoptBase):
    """Item 4: a file named assistant.cmd or assistant.ps1, any case, is never created, replaced, renamed or deleted."""

    def test_her_launcher_survives_check_adopt_again_and_undo(self):
        for name in LAUNCHER_NAMES:
            with self.subTest(name):
                self.tearDown()
                self.setUp()
                self.her_folder(launcher=name)
                for step in (("--check",), (), (), ("--undo",)):
                    rc, out = self.run_adopt(*step)
                    self.assertEqual(rc, 0, out)
                    self.assertIn(name, os.listdir(self.folder))
                    self.assertEqual([n for n in os.listdir(self.folder) if n.lower() in s.PROTECTED], [name])
                    self.assertEqual((self.folder / name).read_bytes(), HER_LAUNCHER)

    def test_no_launcher_is_made_when_she_has_none(self):
        self.her_folder(launcher=None)
        self.run_adopt()
        self.assertEqual([n for n in os.listdir(self.folder) if n.lower() in s.PROTECTED], [])

    def test_the_write_door_refuses_her_launcher_and_outside_paths(self):
        for rel in ("assistant.cmd", "Assistant.CMD", "ASSISTANT.PS1", "sub/assistant.ps1", "../x.txt", "D:/x.txt"):
            with self.subTest(rel):
                with self.assertRaises(s.Stop):
                    s._guard(self.folder, rel)
        self.assertEqual(s._guard(self.folder, "state/ledger.jsonl"), self.folder / "state" / "ledger.jsonl")


class Refusals(AdoptBase):
    """Item 8: one plain line, nothing written, no plugin call."""

    def assert_refused(self, folder, words):
        before = listing(folder) if pathlib.Path(folder).is_dir() else None
        rc, out = self.run_adopt(folder=folder)
        self.assertEqual(rc, 1, out)
        stops = [ln for ln in out.splitlines() if ln.startswith("Stopped at step")]
        self.assertEqual(len(stops), 1, out)
        self.assertIn(words, stops[0])
        self.assertIn("What to do:", stops[0])
        if before is not None:
            self.assertEqual(listing(folder), before)
        self.assertEqual(self.calls, [])

    def test_missing_folder(self):
        self.assert_refused(self.tmp / "Not There", "does not exist")

    def test_drive_root(self):
        self.assertIn("top of a drive", s.adopt_refusal(self.tmp.anchor, self.kit, self.tmp / "seats"))

    def test_profile_root(self):
        home = self.tmp / "profile"
        home.mkdir()
        old = os.environ.get("USERPROFILE")
        os.environ["USERPROFILE"] = str(home)
        try:
            self.assert_refused(home, "whole user folder")
        finally:
            os.environ["USERPROFILE"] = old

    def test_windows_folder(self):
        win = self.tmp / "Win"
        (win / "System32").mkdir(parents=True)
        old = {k: os.environ.get(k) for k in ("SystemRoot", "windir")}
        os.environ["SystemRoot"] = os.environ["windir"] = str(win)
        try:
            self.assert_refused(win, "Windows folder")
            self.assert_refused(win / "System32", "Windows folder")
        finally:
            for k, v in old.items():
                os.environ[k] = v

    def test_kit_home(self):
        self.assert_refused(self.kit, "kit's own folder")
        self.assert_refused(self.kit / "current", "kit's own folder")

    def test_inside_another_seat(self):
        td, outer = _helpers.make_seat(name="Outer Seat")
        try:
            (outer / "Inner").mkdir()
            self.assert_refused(outer / "Inner", "inside another assistant seat")
        finally:
            td.cleanup()

    def test_a_launcher_seat_has_nothing_to_adopt(self):
        self.her_folder()
        (self.folder / ".seat").mkdir()
        (self.folder / ".seat" / "seat.json").write_text('{"seat_name": "X"}', encoding="utf-8")
        self.assert_refused(self.folder, "already an assistant seat made by the kit's launcher")

    def test_unreadable_settings(self):
        self.her_folder()
        (self.folder / ".claude" / "settings.json").write_text("{ not json", encoding="utf-8")
        self.assert_refused(self.folder, "not in a shape the kit can read")

    def test_hooks_switched_off(self):
        self.her_folder(settings=dict(HER_SETTINGS, disableAllHooks=True))
        self.assert_refused(self.folder, "switches all hooks off")

    def test_claude_md_not_utf8(self):
        self.her_folder(md=codecs.BOM_UTF16_LE + HER_MD.encode("utf-16-le"))
        self.assert_refused(self.folder, "not saved as UTF-8")

    def test_check_is_refused_the_same_way(self):
        rc, out = self.run_adopt("--check", folder=self.kit)
        self.assertEqual(rc, 1)
        self.assertIn("kit's own folder", out)


class Versions(unittest.TestCase):
    """Item 11: the version is the same everywhere it lives."""

    def test_plugin_and_marketplace_agree(self):
        v = json.loads((_helpers.KIT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
        market = _helpers.KIT.parent.parent / ".claude-plugin" / "marketplace.json"
        if not market.exists():
            self.skipTest("no marketplace list next to this copy")
        entries = [p for p in json.loads(market.read_text(encoding="utf-8"))["plugins"] if p["name"] == "ml-seat-kit"]
        self.assertEqual([p["version"] for p in entries], [v])

    def test_readme_and_hand_test_say_how_to_adopt(self):
        for name in ("README.md", "HAND_TEST.md"):
            text = (_helpers.KIT / name).read_text(encoding="utf-8")
            self.assertIn("adopt --check", text, name)
            self.assertIn("adopt --undo", text, name)


LAUNCHER = _helpers.KIT / "launcher"
ROOT = os.environ.get("SystemRoot", r"C:\Windows")


@unittest.skipUnless(sys.platform == "win32", "Windows launchers")
class LauncherAdopt(unittest.TestCase):
    """Items 6 and 10 in the launchers themselves: run under a fake profile with a stripped PATH. Each run stops at
    step 2, before any download, and a fake winget records whether anything tried to install."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = _helpers.real(self.td) / "with space"
        self.home = self.base / "home"
        self.store = self.base / "Microsoft" / "WindowsApps"
        self.tools = self.base / "tools"
        self.folder = self.base / "Her Folder"
        for d in (self.home / "AppData" / "Local", self.home / "AppData" / "Roaming", self.store, self.tools,
                  self.folder, self.base / "temp"):
            d.mkdir(parents=True)
        (self.folder / "assistant.cmd").write_bytes(HER_LAUNCHER)
        (self.folder / "CLAUDE.md").write_bytes(HER_MD.encode("utf-8"))
        native = self.home / ".local" / "bin" / "claude.exe"   # an empty stand-in: step 1 finds it, never runs it
        native.parent.mkdir(parents=True)
        native.write_bytes(b"")
        for name, marker in ((self.store / "python.cmd", "python-ran"), (self.tools / "winget.cmd", "winget-ran")):
            name.write_bytes(('@echo off\r\necho ran> "%s"\r\nexit /b 0\r\n' % (self.base / marker)).encode("utf-8"))

    def tearDown(self):
        self.td.cleanup()

    def env(self, system_root=ROOT):
        keep = {k: v for k, v in os.environ.items() if k.upper() in ("COMSPEC", "PATHEXT", "PSMODULEPATH")}
        keep.update(SystemRoot=system_root, windir=system_root, USERPROFILE=str(self.home),
                    LOCALAPPDATA=str(self.home / "AppData" / "Local"), APPDATA=str(self.home / "AppData" / "Roaming"),
                    TEMP=str(self.base / "temp"), TMP=str(self.base / "temp"), SEAT_KIT_HOME=str(self.base / "kit home"),
                    SEAT_HOME=str(self.base / "seats"),
                    # no Windows folder itself on PATH: the py launcher lives there and would find a real Python
                    PATH=os.pathsep.join([os.path.join(ROOT, "System32"),
                                          os.path.join(ROOT, r"System32\WindowsPowerShell\v1.0"),
                                          str(self.store), str(self.tools)]))
        return keep

    def run_launcher(self, kind, *args, env=None):
        if kind == "cmd":   # /s and one outer pair of quotes: cmd keeps every inner quote, spaces in paths included
            argv = '"%s" /d /s /c ""%s" %s"' % (os.path.join(ROOT, "System32", "cmd.exe"), LAUNCHER / "Assistant.cmd",
                                                " ".join('"%s"' % a for a in args))
        else:
            argv = [os.path.join(ROOT, r"System32\WindowsPowerShell\v1.0\powershell.exe"), "-NoProfile",
                    "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(LAUNCHER / "Assistant.ps1")] + list(args)
        r = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=180,
                           env=env or self.env(), stdin=subprocess.DEVNULL, cwd=str(self.base))
        self.last = r
        return r.returncode, r.stdout + r.stderr

    def test_store_only_python_stops_and_installs_nothing(self):
        before = listing(self.folder)
        for kind in ("cmd", "ps1"):
            with self.subTest(kind):
                rc, out = self.run_launcher(kind, "adopt", "--check", str(self.folder))
                self.assertIn("Stopped at step 2", out)
                seen = [ln.split("found, not used: ", 1)[1] for ln in out.splitlines() if "found, not used: " in ln]
                self.assertIn(_helpers.norm(self.store / "python.cmd"), [_helpers.norm(p) for p in seen], out)
                self.assertIn("python.org", out)
                self.assertFalse((self.base / "winget-ran").exists())
                self.assertFalse((self.base / "python-ran").exists())
                self.assertFalse((self.base / "kit home" / "python.txt").exists())
                self.assertEqual(listing(self.folder), before)

    def test_adopt_never_installs_claude_code(self):
        """No claude anywhere, and SystemRoot pointed at an empty folder, so even a mistake could not reach the
        real curl. Adopt must say it does not install Claude Code and go on to step 2."""
        (self.home / ".local" / "bin" / "claude.exe").unlink()
        fake_root = self.base / "no windows"
        (fake_root / "System32").mkdir(parents=True)
        rc, out = self.run_launcher("cmd", "adopt", "--check", str(self.folder), env=self.env(str(fake_root)))
        self.assertFalse((self.base / "kit home" / "install-claude.cmd").exists())
        if not self.last.stdout.strip() and not self.last.stderr.strip():
            # 0.1.6: on a colleague's PC this run printed nothing at all, not even "Step 1 of 6" (the same launcher
            # with the real SystemRoot passed there). Security software stopping a cmd.exe whose SystemRoot points
            # elsewhere is the likely cause. Skip only when a control run of the same launcher, same fake profile and
            # mode, with the REAL SystemRoot and windir, does print "Step 1 of 6": a launcher that dies silently
            # everywhere fails here. The control run has the empty claude.exe stand-in back, so step 1 finds it and
            # the control can never reach the installer download.
            (self.home / ".local" / "bin" / "claude.exe").write_bytes(b"")
            control_rc, control = self.run_launcher("cmd", "adopt", "--check", str(self.folder), env=self.env())
            self.assertFalse((self.base / "kit home" / "install-claude.cmd").exists())
            if "Step 1 of 6" not in control:
                self.fail("the launcher printed nothing with SystemRoot redirected (exit code %s), and nothing with "
                          "the real SystemRoot either (exit code %s):\n%s" % (rc, control_rc, control))
            self.skipTest("the launcher printed nothing with SystemRoot redirected (exit code %s) but did run with the "
                          "real SystemRoot (exit code %s); security software may have stopped it; the branch order is "
                          "checked by test_not_found_branch_comes_before_the_installer_and_jumps_past_it"
                          % (rc, control_rc))
        said = "exit code %s\nstdout:\n%s\nstderr:\n%s" % (rc, self.last.stdout, self.last.stderr)
        self.assertIn("not found where the kit looks; adopt does not install it", out, said)
        self.assertIn("Step 2 of 6", out, said)

    def test_adopt_branches_open_no_session(self):
        cmd = (LAUNCHER / "Assistant.cmd").read_bytes().decode("utf-8").replace("\r\n", "\n")
        section = cmd.split("\n:adopt\n", 1)[1].split("\n:adoptnopy", 1)[0]
        self.assertIn("seat_setup.py\" adopt", section)
        self.assertIn("exit /b %RC%", section)
        self.assertNotIn("-n ", section)
        ps1 = (LAUNCHER / "Assistant.ps1").read_bytes().decode("utf-8").replace("\r\n", "\n")
        block = ps1.split("if ($Adopt) {\n  # Adopt never opens", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("'adopt'", block)
        self.assertIn("return", block)
        self.assertNotIn("-n ", block)


@unittest.skipUnless(sys.platform == "win32", "Windows launchers")
class SilentLauncher(unittest.TestCase):
    """0.1.6 review: the empty-output skip in test_adopt_never_installs_claude_code fires only when the control run
    with the real SystemRoot printed "Step 1 of 6"; a launcher silent in both runs fails. The two runs are scripted
    here (nothing is started), since a silent launcher cannot be produced on demand."""

    def outcome(self, *outputs):
        said = list(outputs)

        class Scripted(LauncherAdopt):
            def run_launcher(self, kind, *args, env=None):
                text = said.pop(0)
                self.last = subprocess.CompletedProcess([kind], 7 if not text else 0, text, "")
                return self.last.returncode, text
        result = unittest.TestResult()
        Scripted("test_adopt_never_installs_claude_code").run(result)
        return result, said

    def test_silent_with_a_redirected_root_only_skips(self):
        result, left = self.outcome("", "Step 1 of 6: checking for Claude Code ... found\n")
        self.assertEqual((len(result.skipped), result.failures, result.errors, left), (1, [], [], []))
        self.assertIn("did run with the real SystemRoot", result.skipped[0][1])

    def test_silent_with_the_real_root_too_fails_with_both_exit_codes(self):
        result, left = self.outcome("", "")
        self.assertEqual((len(result.failures), result.skipped, left), (1, [], []))
        self.assertIn("(exit code 7), and nothing with the real SystemRoot either (exit code 7)", result.failures[0][1])

    def test_output_with_a_redirected_root_runs_no_control(self):
        result, left = self.outcome("Step 1 of 6: checking for Claude Code ... not found where the kit looks; adopt "
                                    "does not install it\nStep 2 of 6\n", "never used")
        self.assertEqual((result.skipped, result.failures, result.errors, left), ([], [], [], ["never used"]))


class LauncherBranches(unittest.TestCase):
    """0.1.6: the launchers' own text, read on any PC: the no-install branch for Claude Code."""

    def test_not_found_branch_comes_before_the_installer_and_jumps_past_it(self):
        """Read, not run (so it holds on every PC): in both launchers, adopt, selftest and remove say Claude Code was
        not found and go on to step 2 BEFORE the line that downloads the Claude Code installer."""
        cmd = (LAUNCHER / "Assistant.cmd").read_bytes().decode("utf-8").replace("\r\n", "\n").split("\n")
        for mode in ("adopt", "selftest", "remove"):
            self.assertIn('if /i "%%~1"=="%s" set "%s=1" & set "NOINST=%s"' % (mode, mode.upper(), mode),
                          "\n".join(cmd))
        branch = cmd.index("if defined NOINST echo not found where the kit looks; %NOINST% does not install it& "
                           "goto step2")
        curl = [n for n, ln in enumerate(cmd) if "curl.exe" in ln and "claude.ai/install.cmd" in ln]
        self.assertEqual(len(curl), 1, "one installer download line")
        self.assertLess(branch, curl[0])
        self.assertLess(curl[0], cmd.index(":step2"))                       # the goto jumps past the download
        self.assertFalse(any("goto" in ln and "step2" not in ln for ln in cmd[branch:curl[0]]))
        self.assertEqual(cmd[branch + 1:curl[0]], [])                       # nothing, no label or goto, in between
        ps1 = (LAUNCHER / "Assistant.ps1").read_bytes().decode("utf-8").replace("\r\n", "\n").split("\n")
        self.assertIn("$NoInst = if ($Adopt) { 'adopt' } elseif ($SelfTest) { 'selftest' } elseif ($Remove) "
                      "{ 'remove' } else { '' }", ps1)
        branch = ps1.index('elseif ($NoInst) { Write-Host "not found where the kit looks; $NoInst does not install it" }')
        curl = [n for n, ln in enumerate(ps1) if "$Curl" in ln and "claude.ai/install.cmd" in ln]
        self.assertEqual(len(curl), 1, "one installer download line")
        self.assertLess(branch, curl[0])
        self.assertEqual(ps1[branch + 1], "else {")                         # the download is only in the else
        self.assertEqual(ps1[branch + 1:curl[0]], ["else {"])               # and nothing else lies in between
        self.assertLess(curl[0], ps1.index("Write-Host -NoNewline 'Step 2 of 6: checking for Python ... '"))
        closing = ps1.index("}", branch + 1)
        self.assertLess(curl[0], closing)                                   # inside that else block


if __name__ == "__main__":
    unittest.main()
