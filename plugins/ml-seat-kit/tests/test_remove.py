"""Remove mode (0.1.5): takes back a first install, never an adopted folder.

Every profile here is made up inside a temp folder whose name holds a space: a kit home, a seat home, a desktop with
REAL Windows shortcuts (made through PowerShell, as the kit makes them), a Downloads folder and stand-ins for Claude
Code's own files. `claude` is a fake that records each call. Folders are held open by REAL child processes that exit
by themselves within 120 seconds. Nothing outside the temp folder is read for a decision or changed.
"""
import argparse
import builtins
import inspect
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout

from tests import _helpers
from tests.test_adopt import AdoptBase, listing
from tests.test_kit_place import Held, LAUNCHER, NEED, ROOT, deep, make_tree, make_zip
from tests import test_selftest
import kit_place as kp
import seat_remove as r
import seat_setup as s

sys.path.insert(0, str(_helpers.KIT / "tools"))
import make_kit  # noqa: E402

WIN = sys.platform == "win32"
SEND = "mcp__claude_ai_Gmail__send_message"
LIST, MLIST = "plugin list --json", "plugin marketplace list --json"
UNINSTALL, MREMOVE = "plugin uninstall ml-seat-kit@seat-kit", "plugin marketplace remove seat-kit"
DATED = re.compile(r"^Juniper\.removed-\d{8}-\d{6}$")
def tree(folder, noise=None):
    """{relative path: sha256, or None for a folder} under folder, leaving out only the top-level AppData folders the
    probe's three PowerShell starts made in a scratch profile on this PC (_helpers.powershell_noise: on the build PC
    AppData/Local/Microsoft, PowerShell's own caches; on a PC with Malwarebytes expected also
    AppData/Local/Malwarebytes, its script scanner's log, not yet run on such a PC). PowerShell
    runs in these tests whenever the kit makes or reads a shortcut. Anything inside those folders that carries one of
    the kit's own names still counts, and so does everything else."""
    return _helpers.without_noise(test_selftest.tree(folder), _helpers.powershell_noise() if noise is None else noise)


def ignored(*more):
    """A failure message for a tree comparison: what else was said, and which folders the comparison left out."""
    return "\n".join([str(m) for m in more] + [_helpers.ignored_note(_helpers.powershell_noise())])


def make_lnk(lnk, target, home, desc="Start your assistant"):
    """A real Windows shortcut, made the way the kit makes one, by a PowerShell that sees only the fake profile."""
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:SEAT_LNK); $s.TargetPath = $env:SEAT_TARGET; "
          "$s.Description = $env:SEAT_DESC; $s.Save()")
    subprocess.run([_helpers.POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                   timeout=120, check=True, stdin=subprocess.DEVNULL,
                   env=_helpers.profile_env(home, SEAT_LNK=str(lnk), SEAT_TARGET=str(target), SEAT_DESC=desc))


class FakeClaude:
    """Claude Code's plugin list as remove sees it: installed and registered until taken out."""

    def __init__(self):
        self.installed = self.registered = True
        self.calls, self.fail = [], set()

    def __call__(self, claude, *a, timeout=180):
        line = " ".join(a)
        self.calls.append(line)
        if line in self.fail:
            return 1, "refused for the test"
        if line == LIST:
            return 0, json.dumps([{"id": s.PLUGIN_ID}] if self.installed else [])
        if line == MLIST:
            return 0, json.dumps([{"name": s.MARKETPLACE}] if self.registered else [])
        if line == UNINSTALL:
            self.installed = False
            return 0, "ok"
        if line == MREMOVE:
            self.registered = False
            return 0, "ok"
        return 1, "unknown command"


def adopt_by_hand(folder, kh):
    """An adopted folder as adopt leaves it in the 0.1.4 layout: the marker says adopted, and its settings' send gate
    runs <kit home>\\send_gate.py. Recorded in adopted.txt as adopt does since 0.1.5."""
    (folder / ".seat").mkdir(parents=True)
    (folder / ".seat" / "seat.json").write_text(json.dumps(
        {"seat_name": "Helper", "kit": "ml-seat-kit", "adopted": {"date": "2026-11-23", "backup": ".seat/backup/x"}}),
        encoding="utf-8")
    (folder / "CLAUDE.md").write_text("# My helper\n", encoding="utf-8")
    (folder / "assistant.cmd").write_bytes(b"@echo off\r\nclaude %*\r\n")
    hook = s.gate_hook(sys.executable, kh, kit=deep(kh / "kits" / NEED))
    (folder / ".claude").mkdir()
    (folder / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [hook]}}), encoding="utf-8")
    s.record_adopted(kh, folder)
    return hook["hooks"][0]


@unittest.skipUnless(WIN, "Windows shortcuts and folders")
class Base(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        _helpers.powershell_noise()   # probed once, between tests, so its marker line stands on its own

    def setUp(self):
        self.cwd = os.getcwd()
        self.td = tempfile.TemporaryDirectory()
        self.base = _helpers.real(self.td) / "with space"
        self.home = self.base / "home"
        self.kh = self.home / "AppData" / "Local" / "seat-kit"
        self.seats = self.home / "Assistant"
        self.desk = self.home / "Desktop"
        self.dl = self.home / "Downloads"
        self.claude = self.home / ".local" / "bin" / "claude.exe"
        for d in (self.kh, self.desk, self.dl, self.claude.parent, self.home / "AppData" / "Roaming" / "Claude",
                  self.home / ".claude", self.base / "Elsewhere"):
            d.mkdir(parents=True, exist_ok=True)
        self.claude.write_bytes(b"")
        (self.home / "AppData" / "Roaming" / "Claude" / "config.json").write_text('{"signed_in": true}', encoding="utf-8")
        (self.home / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
        (self.dl / "Assistant.cmd").write_bytes((LAUNCHER / "Assistant.cmd").read_bytes())
        (self.dl / "notes.txt").write_text("mine", encoding="utf-8")
        (self.base / "Elsewhere" / "assistant.cmd").write_bytes(b"@echo off\r\necho mine\r\n")
        # every profile variable is the fake one, so PowerShell started by the kit (shortcuts) never sees the real one;
        # put back by a cleanup, which runs even when the rest of setUp fails (tearDown does not)
        self.env = {"SEAT_ANSWERS": os.environ.get("SEAT_ANSWERS")}
        self.real_profile = _helpers.fake_profile(self.home)
        self.addCleanup(_helpers.restore_profile, self.real_profile)
        self.fake = FakeClaude()
        self.held = Held(self.base)
        self.answers, self.asked = [], []
        self.saved = (s._claude, s.dry_tick, builtins.input, r._delete)
        s._claude = self.fake
        s.dry_tick = lambda seat: (True, "FREE: test")
        builtins.input = self.answer

    def tearDown(self):
        self.assertTrue(self.held.release(), "a holder did not exit")
        s._claude, s.dry_tick, builtins.input, r._delete = self.saved
        os.chdir(self.cwd)
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.td.cleanup()

    def answer(self, prompt=""):
        self.asked.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def kit_home(self, version=NEED, launcher=True):
        make_tree(self.kh / "kits" / version, version)
        (self.kh / "kits" / version / kp.READY).write_text("x\n", encoding="ascii")
        (self.kh / "kit.txt").write_bytes(version.encode("ascii") + b"\r\n")
        (self.kh / "send_gate.py").write_bytes((_helpers.SCRIPTS / "gate_shim.py").read_bytes())
        (self.kh / "market.txt").write_text(str(self.kh / "kits" / version) + "\n", encoding="utf-8")
        (self.kh / "selftest-last.txt").write_text("Ran 1 test\nOK\n", encoding="utf-8")
        if launcher:
            (self.kh / "python.txt").write_text(sys.executable + "\n", encoding="ascii")
            (self.kh / "Assistant.cmd").write_bytes((LAUNCHER / "Assistant.cmd").read_bytes())

    def first_install(self, name="Juniper"):
        """The kit home and one seat made by the real setup step, with its real desktop shortcut."""
        self.kit_home()
        os.environ["SEAT_ANSWERS"] = "Pat|%s|Lee Park" % name
        a = argparse.Namespace(claude="claude", kit_home=str(self.kh), seat_home=str(self.seats),
                               desktop=str(self.desk), ask=False, no_plugin=True, no_tests=True)
        with redirect_stdout(io.StringIO()):
            seat, _ = s.setup_seat(a)
        (seat / "state" / "open-items.md").write_text("- call the bank back\n", encoding="utf-8")
        (seat / ".seat" / "logs").mkdir(parents=True, exist_ok=True)
        (seat / ".seat" / "logs" / "tick.log").write_text("a tick\n", encoding="utf-8")
        self.assertTrue((self.desk / ("%s.lnk" % name)).is_file(), "setup did not make the shortcut")
        return seat

    def run_remove(self, *extra, launcher=None, claude=None, kit_home=None, seat_home=None):
        argv = ["remove"] + list(extra) + [
            "--claude", str(claude or self.claude), "--kit-home", str(kit_home or self.kh),
            "--seat-home", str(seat_home or self.seats), "--desktop", str(self.desk),
            "--launcher", str(launcher or self.dl / "Assistant.cmd")]
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = s.main(argv)
        return rc, buf.getvalue()

    def outside(self, *skip):
        """Everything in the profile except the kit home, the seat home and the named desktop files."""
        skip = ("AppData/Local/seat-kit", "Assistant") + skip
        return {k: v for k, v in tree(self.home).items() if not any(k == x or k.startswith(x + "/") for x in skip)}


class Check(Base):
    def test_check_changes_nothing_and_names_each_thing(self):
        seat = self.first_install()
        before = tree(self.home)
        for extra in ((), ("--delete-my-notes",)):
            with self.subTest(extra=extra):
                rc, out = self.run_remove("--check", *extra)
                self.assertEqual(rc, 0, out)
                self.assertEqual(tree(self.home), before, ignored(out))
                self.assertEqual((self.fake.calls, self.asked), ([], []))
                lines = out.splitlines()
                self.assertIn("Step 4 of 6: looking at what the first install put on this PC ... done", lines)
                self.assertIn("  1. The kit plugin in Claude Code, if it is there: claude plugin uninstall "
                              "ml-seat-kit@seat-kit, then claude plugin marketplace remove seat-kit", lines)
                self.assertIn("  2. The desktop shortcut %s (it opens %s)" % (self.desk / "Juniper.lnk",
                                                                            self.kh / "Assistant.cmd"), lines)
                if extra:
                    self.assertIn("  3. Your assistant's folder %s: DELETED with everything in it, after you type its "
                                  "name (Juniper) to confirm" % seat, lines)
                else:
                    self.assertIn("  3. Your assistant's folder %s: moved, not deleted, to %s. Your notes stay in it; "
                                  "deleting that folder later is up to you."
                                  % (seat, self.seats / "Juniper.removed-<date>-<time>"), lines)
                self.assertIn("  4. The kit folder %s, and the kit's own things in it:" % self.kh, lines)
                for name in ("Assistant.cmd", "kits", "last-seat.txt", "market.txt", "python.txt",
                             "selftest-last.txt", "kit.txt", "send_gate.py"):
                    self.assertIn("       %s" % (self.kh / name), lines)
                self.assertIn("   Claude Code itself, Python, your Claude sign-in and connectors, and everything in "
                              "your Downloads folder.", lines)
                self.assertIn('To do it:   "%s" %s' % (self.dl / "Assistant.cmd", " ".join(("remove",) + extra)), lines)


class Remove(Base):
    def test_remove_takes_back_a_first_install_and_again_finds_nothing(self):
        seat = self.first_install()
        notes, outside = listing(seat), self.outside("Desktop/Juniper.lnk")
        rc, out = self.run_remove()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.fake.calls, [LIST, MLIST, UNINSTALL, MREMOVE])
        self.assertFalse((self.desk / "Juniper.lnk").exists())
        self.assertFalse(seat.exists())
        (moved,) = list(self.seats.iterdir())
        self.assertRegex(moved.name, DATED)
        self.assertEqual(listing(moved), notes)                             # every file, byte for byte
        self.assertFalse(self.kh.exists())
        self.assertEqual(self.outside("Desktop/Juniper.lnk"), outside, ignored(out))   # nothing else in the profile changed
        lines = out.splitlines()
        for want in ("Step 5 of 6: taking it back ...",
                     "  1. The kit plugin: done (uninstalled from Claude Code, and its source seat-kit removed)",
                     "  2. The desktop shortcut %s: deleted" % (self.desk / "Juniper.lnk"),
                     "  3. Your assistant's folder %s: moved to %s. All your notes are in it; deleting that folder "
                     "later is up to you." % (seat, moved),
                     "  4. The kit folder %s: deleted" % self.kh,
                     "Step 6 of 6: checking what is left ... nothing of the first install is left",
                     "Done: the first install is taken back."):
            self.assertIn(want, lines)
        self.assertEqual(s.find_seats(self.seats), [])                      # the moved folder is never a seat again

        self.fake.calls = []
        rc, out = self.run_remove()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.fake.calls, [LIST, MLIST])
        lines = out.splitlines()
        for want in ("  1. The kit plugin: not there (Claude Code has neither the plugin nor its source)",
                     "  2. The desktop shortcut: not there",
                     "  3. Your assistant's folder: not there (no folder made by the kit in %s)" % self.seats,
                     "  4. The kit folder %s: not there" % self.kh,
                     "Not touched: %s holds your notes from an earlier remove; deleting it is up to you." % moved,
                     "Nothing of a first install was there to take back."):
            self.assertIn(want, lines)
        self.assertEqual(listing(moved), notes)

    def test_delete_my_notes_deletes_only_after_the_typed_name_matches(self):
        seat = self.first_install()
        before = tree(self.home)
        for typed in (["Junipr"], []):
            with self.subTest(typed=typed):
                self.fake.calls, self.answers = [], list(typed)
                rc, out = self.run_remove("--delete-my-notes")
                self.assertEqual(rc, 1, out)
                self.assertIn("Stopped at step 4: the name typed (%s) is not Juniper, so nothing was deleted or "
                              "changed." % (typed[0] if typed else "nothing"), out.splitlines())
                self.assertEqual(tree(self.home), before, ignored(out))
                self.assertNotIn(UNINSTALL, self.fake.calls)
        self.answers = ["Juniper"]
        rc, out = self.run_remove("--delete-my-notes")
        self.assertEqual(rc, 0, out)
        self.assertIn("  3. Your assistant's folder %s: deleted, with everything in it" % seat, out.splitlines())
        self.assertFalse(seat.exists())
        self.assertFalse(self.seats.exists())                               # it was the kit's, and now empty
        self.assertFalse(self.kh.exists())

    def test_claude_code_not_found_carries_on_and_prints_the_two_lines(self):
        seat = self.first_install()
        rc, out = self.run_remove(claude=self.base / "no claude here" / "claude.exe")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.fake.calls, [])
        lines = out.splitlines()
        self.assertIn("       claude plugin uninstall ml-seat-kit@seat-kit", lines)
        self.assertIn("       claude plugin marketplace remove seat-kit", lines)
        self.assertTrue(any(ln.startswith("  1. The kit plugin: not taken out, because Claude Code was not found")
                            for ln in lines), out)
        self.assertFalse(seat.exists())
        self.assertFalse(self.kh.exists())

    def test_a_failure_midway_says_what_is_done_and_a_second_run_finishes(self):
        seat = self.first_install()
        self.fake.fail = {MREMOVE}
        rc, out = self.run_remove()
        self.assertEqual(rc, 1, out)
        lines = out.splitlines()
        self.assertIn("  1. The kit plugin: uninstalled, but its source seat-kit could not be removed from Claude Code "
                      "(Claude Code said: refused for the test)", lines)
        self.assertIn("Stopped at step 5: remove could not finish taking the first install back.", lines)
        done = lines[lines.index("Already taken back:"):lines.index("Still there:")]
        self.assertEqual(done, ["Already taken back:", "   the kit plugin (uninstalled from Claude Code)"])
        for left in ("   the kit plugin in Claude Code", "   your assistant's folder %s" % seat,
                     "   the desktop shortcut %s" % (self.desk / "Juniper.lnk"), "   the kit folder %s" % self.kh):
            self.assertIn(left, lines)
        self.assertTrue(seat.exists() and self.kh.exists() and (self.desk / "Juniper.lnk").exists())

        self.fake.fail, self.fake.calls = set(), []
        real_delete = r._delete

        def refuse_kits(path):
            if pathlib.Path(path).name == "kits":
                raise PermissionError(13, "Access is denied", str(path))
            return real_delete(path)
        r._delete = refuse_kits
        rc, out = self.run_remove()
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.fake.calls, [LIST, MLIST, MREMOVE])
        lines = out.splitlines()
        self.assertIn("  4. The kit folder %s: could not delete %s (Windows said: Access is denied: %s)"
                      % (self.kh, self.kh / "kits", self.kh / "kits"), lines)
        self.assertIn("   the desktop shortcut %s" % (self.desk / "Juniper.lnk"), lines)
        self.assertIn("   the kit folder %s" % self.kh, lines[lines.index("Still there:"):])
        self.assertTrue((self.kh / "kits").is_dir())
        self.assertFalse(seat.exists())

        r._delete = real_delete
        rc, out = self.run_remove()
        self.assertEqual(rc, 0, out)
        self.assertFalse(self.kh.exists())
        self.assertIn("Done: the first install is taken back.", out.splitlines())

    def test_a_folder_in_use_stops_before_anything_changes(self):
        seat = self.first_install()
        before = tree(self.home)
        for n, (mode, path, words) in enumerate((
                ("file", seat / "state" / "ledger.jsonl", "your assistant's folder"),
                ("cwd", seat / "state", "your assistant's folder"),
                ("cwd", seat, "your assistant's folder"),
                ("file", deep(self.kh / "kits" / NEED) / "scripts" / "send_gate.py", "the kit folder"),
                ("cwd", self.kh, "the kit folder"))):
            with self.subTest(mode=mode, path=path.name):
                self.fake.calls = []
                holder = Held(self.base / ("holder %d" % n))                  # its own ready files each time
                holder.base.mkdir()
                holder.hold(mode, path)
                try:
                    rc, out = self.run_remove()
                finally:
                    self.assertTrue(holder.release(), "a holder did not exit")
                self.assertEqual(rc, 1, out)
                stop = [ln for ln in out.splitlines() if ln.startswith("Stopped at step 4: ")]
                self.assertTrue(stop and stop[0].startswith("Stopped at step 4: %s " % words) and "is in use" in stop[0],
                                out)
                self.assertIn("What to do: close your assistant's window", out)
                self.assertEqual(tree(self.home), before, ignored(out))
                self.assertEqual(self.fake.calls, [])

    def test_shortcuts_are_known_by_their_target_never_by_name(self):
        self.first_install()
        (self.desk / "Juniper.lnk").unlink()
        mine = self.base / "Elsewhere" / "assistant.cmd"
        make_lnk(self.desk / "Juniper.lnk", mine, self.home)            # the kit's name, changed to point elsewhere
        make_lnk(self.desk / "Other.lnk", mine, self.home, desc="mine")
        make_lnk(self.desk / "Kit Copy.lnk", self.kh / "Assistant.cmd", self.home, desc="something else")
        kept = {n: (self.desk / n).read_bytes() for n in ("Juniper.lnk", "Other.lnk")}
        rc, out = self.run_remove()
        self.assertEqual(rc, 0, out)
        self.assertEqual({n: (self.desk / n).read_bytes() for n in kept}, kept)
        self.assertFalse((self.desk / "Kit Copy.lnk").exists())
        self.assertIn("Not touched: the desktop shortcut %s points at %s, not at the kit's launcher, so it is left as "
                      "it is." % (self.desk / "Juniper.lnk", mine), out.splitlines())
        self.assertNotIn("Other.lnk", out)

    def test_files_that_are_not_the_kits_are_left_and_named(self):
        seat = self.first_install()
        (self.kh / "Assistant.cmd").write_bytes(b"@echo off\r\necho a launcher of her own\r\n")
        (self.kh / "assistant.ps1").write_bytes(b"Write-Host mine\r\n")
        (self.seats / "assistant.cmd").write_bytes(b"@echo off\r\n")
        theirs = {p: p.read_bytes() for p in (self.kh / "Assistant.cmd", self.kh / "assistant.ps1",
                                              self.seats / "assistant.cmd", self.base / "Elsewhere" / "assistant.cmd")}
        rc, out = self.run_remove()
        self.assertEqual(rc, 0, out)
        self.assertEqual({p: p.read_bytes() for p in theirs}, theirs)
        self.assertEqual(sorted(p.name for p in self.kh.iterdir()), ["Assistant.cmd", "assistant.ps1"])
        for p in (self.kh / "Assistant.cmd", self.kh / "assistant.ps1"):
            self.assertIn("Not touched: %s is not one of the kit's own files, so it is left, and so is the kit folder "
                          "that holds it." % p, out.splitlines())
        self.assertFalse(seat.exists())

    def test_a_launcher_of_her_own_inside_the_seat_folder_stops(self):
        seat = self.first_install()
        (seat / "tools").mkdir()
        (seat / "tools" / "Assistant.ps1").write_bytes(b"Write-Host mine\r\n")
        before = tree(self.home)
        rc, out = self.run_remove()
        self.assertEqual(rc, 1, out)
        self.assertIn("Stopped at step 4: %s is a launcher of your own inside your assistant's folder, and remove never "
                      "moves or deletes one, so nothing was changed." % (seat / "tools" / "Assistant.ps1"),
                      out.splitlines())
        self.assertEqual((tree(self.home), self.fake.calls), (before, []), ignored(out))

    def test_the_kept_copy_in_the_kit_folder_cannot_take_its_own_folder(self):
        self.first_install()
        before = tree(self.home)
        kept = self.kh / "Assistant.cmd"
        rc, out = self.run_remove(launcher=kept)
        self.assertEqual(rc, 1, out)
        lines = out.splitlines()
        self.assertIn("Stopped at step 4: this launcher is the copy kept inside the kit folder (%s), and a file that is "
                      "running cannot take away the folder it is in, so nothing was changed." % kept, lines)
        self.assertIn('   copy "%s" "%%TEMP%%\\Assistant.cmd"' % kept, lines)
        self.assertIn('   "%TEMP%\\Assistant.cmd" remove', lines)
        self.assertEqual((tree(self.home), self.fake.calls), (before, []), ignored(out))
        rc, out = self.run_remove("--check", launcher=kept)                 # the check still shows the whole plan
        self.assertEqual(rc, 0, out)
        self.assertIn("Right now remove would stop before changing anything, because:", out.splitlines())
        self.assertEqual(tree(self.home), before, ignored(out))

    def test_odd_folders_are_refused_before_anything(self):
        self.first_install()
        (self.home / "Documents").mkdir()
        inside_dl = self.dl / "kit"
        inside_dl.mkdir()
        (inside_dl / "kit.txt").write_text("0.1.5\r\n", encoding="ascii")
        not_kit = self.base / "Not A Kit"
        not_kit.mkdir()
        (not_kit / "letter.txt").write_text("mine", encoding="utf-8")
        before = tree(self.base)
        for kw, words in (({"kit_home": self.home}, "is your whole user folder"),
                          ({"kit_home": self.desk}, "is your Desktop folder itself"),
                          ({"kit_home": self.home / "Documents"}, "is your Documents folder itself"),
                          ({"kit_home": inside_dl}, "is, holds or is inside your Downloads folder"),
                          ({"kit_home": self.base}, "holds your whole user folder"),
                          ({"seat_home": self.home}, "is your whole user folder"),
                          ({"seat_home": self.dl}, "is, holds or is inside your Downloads folder"),
                          ({"kit_home": not_kit}, "does not look like the kit's own folder")):
            for extra in ((), ("--check",)):
                with self.subTest(kw=kw, extra=extra):
                    rc, out = self.run_remove(*extra, **kw)
                    self.assertEqual(rc, 1, out)
                    self.assertIn(words, out)
                    self.assertTrue(any(ln.startswith("Stopped at step 4: ") for ln in out.splitlines()), out)
                    self.assertEqual(tree(self.base), before, ignored(out))
                    self.assertEqual(self.fake.calls, [])
        self.assertEqual(r.odd_folder(pathlib.Path(self.base.anchor)), "is the top of a drive")
        self.assertIsNone(r.odd_folder(self.kh))
        self.assertIsNone(r.odd_folder(self.seats))

    def test_a_folder_named_on_the_line_is_refused(self):
        self.first_install()
        her = self.base / "Her Folder"
        adopt_by_hand(her, self.kh)
        before = tree(self.base)
        rc, out = self.run_remove(str(her))
        self.assertEqual(rc, 1, out)
        self.assertIn("Stopped at step 4: %s was set up with adopt, and remove never touches an adopted folder, so "
                      "nothing was changed." % her, out.splitlines())
        self.assertIn('What to do: to take that folder back, run: "%s" adopt --undo "%s"' % (self.dl / "Assistant.cmd",
                                                                                           her), out.splitlines())
        rc, out = self.run_remove(str(self.seats / "Juniper"))
        self.assertEqual(rc, 1, out)
        self.assertIn("remove takes no folder name", out)
        self.assertEqual((tree(self.base), self.fake.calls), (before, []), ignored(out))


class Adopted(Base):
    def spawn(self, hook, folder):
        env = {k: v for k, v in os.environ.items() if k.upper() in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")}
        env.update(PATH=os.pathsep.join([os.path.join(ROOT, "System32"), ROOT]), CLAUDE_PROJECT_DIR=str(folder))
        return subprocess.run([hook["command"]] + hook["args"], capture_output=True, text=True, timeout=60,
                              input=json.dumps({"tool_name": SEND, "cwd": str(folder), "tool_input": {}}),
                              cwd=str(folder), env=env)

    def test_an_adopted_folder_alone_is_never_touched(self):
        self.kit_home(launcher=False)                                       # adopt never keeps a launcher copy
        her = self.base / "Her Folder"
        adopt_by_hand(her, self.kh)
        before = (tree(self.home), listing(her))
        for extra in (("--check",), ()):
            with self.subTest(extra=extra):
                rc, out = self.run_remove(*extra)
                self.assertEqual(rc, 0, out)
                self.assertEqual((tree(self.home), listing(her)), before, ignored(out))
                self.assertEqual(self.fake.calls, [])
                self.assertIn("%s was set up with adopt, and remove never touches an adopted folder" % her, out)
                self.assertIn('adopt --undo "%s"' % her, out)
        self.assertIn("Nothing of a first install was there to take back.", out.splitlines())   # the real run's

    def test_with_both_only_the_first_install_goes_and_the_gate_keeps_working(self):
        seat = self.first_install()
        her = self.base / "Her Folder"
        hook = adopt_by_hand(her, self.kh)
        under = self.seats / "Also Hers"                                    # adopted, but under the seat home
        (under / ".seat").mkdir(parents=True)
        (under / ".seat" / "seat.json").write_text(json.dumps({"kit": "ml-seat-kit", "adopted": {"date": "x"}}),
                                                   encoding="utf-8")
        kit_before, hers, also = tree(self.kh), listing(her), listing(under)
        rc, out = self.run_remove()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.fake.calls, [])                               # the plugin stays: the adopted folder uses it
        self.assertFalse(seat.exists())
        self.assertFalse((self.desk / "Juniper.lnk").exists())
        self.assertEqual((listing(her), listing(under)), (hers, also))
        self.assertEqual(tree(self.kh), {k: v for k, v in kit_before.items() if k not in ("Assistant.cmd", "last-seat.txt")},
                         ignored(out))
        lines = out.splitlines()
        self.assertIn("  1. The kit plugin: kept, because an adopted folder uses it (named below)", lines)
        self.assertIn("  4. The kit folder %s: kept, because an adopted folder uses its send gate (named below); only "
                      "the kit's copy of Assistant.cmd and last-seat.txt were deleted from it" % self.kh, lines)
        for folder in (her, under):
            self.assertIn("Not touched: %s was set up with adopt, and remove never touches an adopted folder. The kit "
                          "plugin and the kit folder stay because it uses them (its send gate runs %s). To take it back "
                          'as well: "%s" adopt --undo "%s", then run remove again.'
                          % (folder, self.kh / "send_gate.py", self.dl / "Assistant.cmd", folder), lines)
        g = self.spawn(hook, her)                                           # the gate still refuses a send
        self.assertEqual(g.returncode, 2, g.stderr)
        self.assertTrue(g.stderr.startswith("Drafts only:"), g.stderr)
        self.assertNotIn("could not run", g.stderr)
        (self.kh / "kit.txt").unlink()                                      # and fails closed without its pointer
        g = self.spawn(hook, her)
        self.assertEqual(g.returncode, 2, g.stderr)
        self.assertIn("the send gate could not run", g.stderr)
        (self.kh / "send_gate.py").unlink()                                 # or without its file
        self.assertEqual(self.spawn(hook, her).returncode, 2)


class AdoptRecord(AdoptBase):
    def test_adopt_records_the_folder_once_and_check_records_nothing(self):
        self.her_folder()
        rc, out = self.run_adopt("--check")
        self.assertEqual(rc, 0, out)
        self.assertFalse((self.kit / "adopted.txt").exists())
        for _ in range(2):
            rc, out = self.run_adopt()
            self.assertEqual(rc, 0, out)
            lines = (self.kit / "adopted.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual([_helpers.norm(x) for x in lines], [_helpers.norm(self.folder)])
        self.assertEqual([_helpers.norm(p) for p in r.recorded_adopted(self.kit)], [_helpers.norm(self.folder)])


CLAUDE_STUB = r'''import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
state_file = os.path.join(here, "claude-state.json")
st = json.load(open(state_file))
a = " ".join(sys.argv[1:])
with open(os.path.join(here, "claude-calls.log"), "a") as f:
    f.write(a + "\n")
if a == "plugin list --json":
    print(json.dumps([{"id": "ml-seat-kit@seat-kit"}] if st["installed"] else []))
elif a == "plugin marketplace list --json":
    print(json.dumps([{"name": "seat-kit"}] if st["registered"] else []))
elif a == "plugin uninstall ml-seat-kit@seat-kit":
    st["installed"] = False
elif a == "plugin marketplace remove seat-kit":
    st["registered"] = False
else:
    sys.exit(1)
json.dump(st, open(state_file, "w"))
'''


def real_zip(path, version=NEED):
    """A kit zip with every file of this kit, as make_kit lays it out (no tests run, no address filled in)."""
    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(".claude-plugin/marketplace.json", json.dumps(make_kit.marketplace(version)))
        z.writestr("__main__.py", make_kit.ZIP_MAIN)
        for p in make_kit.plugin_files():
            z.writestr("plugins/ml-seat-kit/" + p.relative_to(_helpers.KIT).as_posix(), p.read_bytes())
    return path


@unittest.skipUnless(WIN, "Windows launchers")
class RemoveLaunchers(unittest.TestCase):
    """Both launchers end to end, each on a profile of its own, with a stand-in claude.cmd (npm's place) that records
    every call. The printed lines of the two must be the same."""
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        _helpers.powershell_noise()

    def setUp(self):
        if os.environ.get("SEAT_KIT_SHORT_TEMP_RUN") == "1":
            self.skipTest("the launcher runs set their own TEMP; they run once, in the main run")
        self.td = tempfile.TemporaryDirectory()
        self.base = _helpers.real(self.td) / "with space"
        self.base.mkdir()
        self.zip = real_zip(self.base / "seat-kit.zip")

    def tearDown(self):
        self.td.cleanup()

    def profile(self, name, kit=NEED, placed=True):
        P = argparse.Namespace(root=self.base / name)
        P.home = P.root / "home"
        P.kh = P.home / "AppData" / "Local" / "seat-kit"
        P.seats, P.desk, P.dl, P.temp = P.home / "Assistant", P.home / "Desktop", P.home / "Downloads", P.root / "temp"
        P.stub = P.root / "stub"
        npm = P.home / "AppData" / "Roaming" / "npm"
        for d in (P.kh, P.desk, P.dl, P.temp, P.stub, npm):
            d.mkdir(parents=True)
        (P.stub / "claude_stub.py").write_text(CLAUDE_STUB, encoding="utf-8")
        (P.stub / "claude-state.json").write_text('{"installed": true, "registered": true}', encoding="utf-8")
        (npm / "claude.cmd").write_bytes(('@echo off\r\n"%s" -I "%s" %%*\r\n' % (sys.executable, P.stub / "claude_stub.py"))
                                         .encode("utf-8"))
        for n in ("Assistant.cmd", "Assistant.ps1"):
            (P.dl / n).write_bytes((LAUNCHER / n).read_bytes())
        if placed:
            r0 = subprocess.run([sys.executable, "-I", str(self.zip), "--kit-home", str(P.kh)], capture_output=True,
                                text=True, timeout=120, stdin=subprocess.DEVNULL, cwd=str(P.root),
                                env=_helpers.profile_env(P.home, P.temp))
            self.assertEqual(r0.returncode, 0, r0.stdout + r0.stderr)
        else:
            make_tree(P.kh / "kits" / kit, kit)
            (P.kh / "kit.txt").write_bytes(kit.encode("ascii") + b"\r\n")
            (P.kh / "send_gate.py").write_bytes((_helpers.SCRIPTS / "gate_shim.py").read_bytes())
        (P.kh / "python.txt").write_text(sys.executable + "\n", encoding="ascii")
        (P.kh / "Assistant.cmd").write_bytes((LAUNCHER / "Assistant.cmd").read_bytes())
        (P.kh / "last-seat.txt").write_text("Juniper|Juniper\n", encoding="ascii")
        P.seat = P.seats / "Juniper"
        (P.seat / ".seat").mkdir(parents=True)
        (P.seat / ".seat" / "seat.json").write_text('{"seat_name": "Juniper", "kit": "ml-seat-kit"}', encoding="utf-8")
        (P.seat / "state").mkdir()
        (P.seat / "state" / "open-items.md").write_text("- call the bank back\n", encoding="utf-8")
        make_lnk(P.desk / "Juniper.lnk", P.kh / "Assistant.cmd", P.home)
        return P

    def launch(self, P, kind, *args, source=None, launcher=None, stdin=None):
        env = _helpers.profile_env(
            P.home, P.temp, SEAT_HOME=str(P.seats), SEAT_DESKTOP=str(P.desk), SEAT_KIT_SOURCE=str(source or self.zip),
            # Python's own folder, as a python.org install puts it on PATH: after a remove, python.txt is gone
            PATH=os.pathsep.join([os.path.join(ROOT, "System32"), os.path.join(ROOT, r"System32\WindowsPowerShell\v1.0"),
                                  os.path.dirname(sys.executable)]))
        target = launcher or P.dl / ("Assistant.%s" % kind)
        if kind == "cmd":
            argv = '"%s" /d /s /c ""%s" %s"' % (os.path.join(ROOT, "System32", "cmd.exe"), target,
                                                " ".join('"%s"' % a for a in args))
        else:
            argv = [os.path.join(ROOT, r"System32\WindowsPowerShell\v1.0\powershell.exe"), "-NoProfile",
                    "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(target)] + list(args)
        res = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=300, env=env,
                             input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL, cwd=str(P.root))
        lines = [ln.rstrip() for ln in res.stdout.splitlines() if ln.strip() and not ln.startswith("Press any key")]
        return res.returncode, lines, res.stdout + res.stderr

    def calls(self, P):
        f = P.stub / "claude-calls.log"
        return f.read_text(encoding="utf-8").splitlines() if f.exists() else []

    def masked(self, P, lines):
        out = []
        for ln in lines:
            ln = ln.replace(str(P.root), "<P>")
            ln = re.sub(r"\d{8}-\d{6}", "<T>", ln)
            ln = ln.replace('& "<P>\\home\\Downloads\\Assistant.ps1"', "<launcher>")
            ln = ln.replace('"<P>\\home\\Downloads\\Assistant.cmd"', "<launcher>")
            out.append(ln.replace("<P>\\home\\Downloads\\Assistant.ps1", "<P>\\home\\Downloads\\Assistant.cmd"))
        return out

    def test_check_then_remove_then_remove_again_with_both_launchers(self):
        seen = {}
        for kind in ("cmd", "ps1"):
            P = self.profile(kind)
            before = tree(P.home)
            rc, check, raw = self.launch(P, kind, "remove", "--check")
            self.assertEqual(rc, 0, raw)
            self.assertEqual(tree(P.home), before, ignored(raw))
            self.assertEqual(self.calls(P), [])
            self.assertIn("Step 3 of 6: getting the assistant kit ... using the one already on this PC", check)
            self.assertIn("Kit in use: %s (%s)" % (NEED, P.kh / "kits" / NEED), check)
            self.assertIn("  2. The desktop shortcut %s (it opens %s)" % (P.desk / "Juniper.lnk", P.kh / "Assistant.cmd"),
                          check)

            rc, first, raw = self.launch(P, kind, "remove")
            self.assertEqual(rc, 0, raw)
            self.assertEqual(self.calls(P), [LIST, MLIST, UNINSTALL, MREMOVE])
            self.assertFalse(P.kh.exists() or P.seat.exists() or (P.desk / "Juniper.lnk").exists(), raw)
            (moved,) = list(P.seats.iterdir())
            self.assertRegex(moved.name, DATED)
            self.assertEqual((moved / "state" / "open-items.md").read_text(encoding="utf-8"), "- call the bank back\n")
            self.assertIn("Done: the first install is taken back.", first)

            rc, again, raw = self.launch(P, kind, "remove")
            self.assertEqual(rc, 0, raw)
            self.assertIn("Step 3 of 6: getting the assistant kit ... done", again)
            self.assertIn("Kit in use: %s (fetched from %s, not put in place)" % (NEED, self.zip), again)
            self.assertIn("Nothing of a first install was there to take back.", again)
            self.assertFalse(P.kh.exists(), raw)                            # remove never writes into the kit folder
            self.assertEqual([p.name for p in P.temp.iterdir() if p.name.startswith("seat-kit-")], [])
            seen[kind] = [self.masked(P, x) for x in (check, first, again)]
        self.assertEqual(seen["cmd"], seen["ps1"])

    def test_delete_my_notes_through_the_launcher_asks_for_the_name(self):
        P = self.profile("cmd")
        rc, lines, raw = self.launch(P, "cmd", "remove", "--delete-my-notes", stdin="Juniper\n")
        self.assertEqual(rc, 0, raw)
        self.assertFalse(P.seats.exists(), raw)
        self.assertIn("type its name (Juniper)", raw)

    def test_the_kept_copy_stops_and_says_to_run_the_downloaded_one(self):
        P = self.profile("cmd")
        before = tree(P.home)
        rc, lines, raw = self.launch(P, "cmd", "remove", launcher=P.kh / "Assistant.cmd")
        self.assertEqual(rc, 1, raw)
        self.assertIn("What to do: run remove from the Assistant.cmd you downloaded, or copy this one out first and run "
                      "the copy, with these two lines in a Command Prompt:", lines)
        self.assertEqual((tree(P.home), self.calls(P)), (before, []), ignored(raw))

    def test_an_older_kit_and_no_newer_one_stops_with_the_hand_steps(self):
        older = make_zip(self.base / "older.zip", "0.1.4")
        seen = {}
        for kind in ("cmd", "ps1"):
            for source, why in ((self.base / "not-there.zip", "   The kit file %s is not there." % (self.base / "not-there.zip")),
                                (older, "   The kit that was fetched is 0.1.4, and remove needs %s or newer." % NEED)):
                # 0.1.5 on the PC: it has remove, but it is older than this launcher needs (0.1.6 wording fix)
                P = self.profile("%s-%s" % (kind, source.stem), kit="0.1.5", placed=False)
                before = tree(P.home)
                rc, lines, raw = self.launch(P, kind, "remove", source=source)
                self.assertEqual(rc, 1, raw)
                self.assertFalse(any("has no remove" in ln for ln in lines), raw)
                for want in ("Step 3 of 6: getting the assistant kit ... not done", why,
                             "Stopped at step 3: could not get kit %s, which remove needs, and the kit on this PC (0.1.5) "
                             "is older than %s, the version this launcher needs for remove, so nothing was changed."
                             % (NEED, NEED),
                             "What to do: check that you are online and run this again. Or take the first install back "
                             "by hand, in this order:",
                             "   1. Close your assistant's window.",
                             "   2. In a Command Prompt, run: claude plugin uninstall ml-seat-kit@seat-kit",
                             "      and then: claude plugin marketplace remove seat-kit",
                             "   3. Delete your assistant's shortcut from your desktop.",
                             "   4. Your assistant's folder in %s holds your notes: move it somewhere you keep your "
                             "files, or delete it if you do not want them." % P.seats,
                             "   5. Delete the folder %s" % P.kh):
                    self.assertIn(want, lines, raw)
                self.assertEqual((tree(P.home), self.calls(P)), (before, []), ignored(raw))
                self.assertEqual([p.name for p in P.temp.iterdir() if p.name.startswith("seat-kit-")], [])
                seen.setdefault(kind, []).append([ln.replace(str(P.root), "<P>") for ln in lines])
        self.assertEqual(seen["cmd"], seen["ps1"])

    def test_a_first_install_made_by_0_1_4_is_taken_back_by_the_newer_kit(self):
        for kind in ("cmd", "ps1"):
            P = self.profile("up-" + kind, kit="0.1.4", placed=False)
            rc, lines, raw = self.launch(P, kind, "remove")
            self.assertEqual(rc, 0, raw)
            self.assertIn("Kit in use: %s (fetched from %s, not put in place)" % (NEED, self.zip), lines)
            self.assertFalse(P.kh.exists() or P.seat.exists() or (P.desk / "Juniper.lnk").exists(), raw)
            self.assertEqual(self.calls(P), [LIST, MLIST, UNINSTALL, MREMOVE])


SYNTHETIC = ["AppData/Local/Malwarebytes", "AppData/Local/Microsoft"]


def plant(home, rel, text="x"):
    p = pathlib.Path(home) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class Noise(unittest.TestCase):
    """0.1.6: the tree comparisons ignore only the top-level AppData folders the probe's PowerShell starts left in a
    scratch profile, never the kit's own, and a real stray write is still seen."""

    @classmethod
    def setUpClass(cls):
        _helpers.powershell_noise()

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.home = _helpers.real(self.td) / "with space" / "home"
        for rel in ("AppData/Local/seat-kit/kit.txt", "AppData/Local/Microsoft/Windows/cache.dat",
                    "AppData/Local/Malwarebytes/Logs/MBAMSI.log", "AppData/Roaming/Claude/config.json",
                    "Desktop/notes.txt", "Assistant/Juniper/.seat/seat.json"):
            plant(self.home, rel)

    def tearDown(self):
        self.td.cleanup()

    def test_noise_names_only_top_level_appdata_folders_and_never_the_kits(self):
        found = ["Local/Microsoft", "Local/Malwarebytes", "Roaming/Thing", "Local/seat-kit", "Local/SEAT-KIT",
                 "Local/Programs", "Roaming/npm", "Local/Microsoft/Windows", "Other/x", "Local/", "Local/..", "Desktop"]
        self.assertEqual(_helpers.noise_list(found),
                         ["AppData/Local/Malwarebytes", "AppData/Local/Microsoft", "AppData/Roaming/Thing"])

    @unittest.skipUnless(WIN and os.path.isfile(_helpers.POWERSHELL), "PowerShell")
    def test_the_probe_starts_powershell_three_ways_on_a_fake_profile_and_drops_the_kits_folder(self):
        seen, slept = [], []

        def run(argv, **k):
            env = k["env"]
            seen.append({"argv": argv, "env": env, "kw": k,
                         "desk": sorted(os.listdir(env["SEAT_DESK"])) if "SEAT_DESK" in env else None,
                         "script": pathlib.Path(argv[-1]).read_bytes() if "-File" in argv else None})
            local, roaming = pathlib.Path(env["LOCALAPPDATA"]), pathlib.Path(env["APPDATA"])
            for p in (local / "seat-kit" / "kit.txt", local / "Malwarebytes" / "Logs" / "MBAMSI.log",
                      local / "Microsoft" / "Windows" / "x", roaming / "Thing" / "y"):
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(str(len(seen)), encoding="utf-8")
            (local / "stray.txt").write_text("a file, not a folder", encoding="utf-8")
        self.assertEqual(_helpers.probe_noise(run, sleep=slept.append),
                         ["AppData/Local/Malwarebytes", "AppData/Local/Microsoft", "AppData/Roaming/Thing"])
        self.assertEqual(slept, [1.0])                                      # one pause after the last start
        self.assertEqual(len(seen), 3)
        ps = _helpers.POWERSHELL
        one, two, three = seen
        self.assertEqual(one["argv"], [ps, "-NoProfile", "-NonInteractive", "-Command", "exit"])
        self.assertEqual(two["argv"], [ps, "-NoProfile", "-NonInteractive", "-Command", r.SHORTCUT_PS])
        self.assertIs(two["argv"][4], r.SHORTCUT_PS)                        # the kit's own read-only script object
        self.assertEqual(three["argv"][:6], [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"])
        self.assertEqual((len(three["argv"]), pathlib.Path(three["argv"][6]).name, three["script"]),
                         (7, "probe.ps1", b"exit 0\r\n"))
        home = pathlib.Path(one["env"]["USERPROFILE"])
        scratch = home.parent
        self.assertEqual(home.name, "probe home")
        # the variable SHORTCUT_PS reads, passed as read_shortcuts passes it, on an empty folder outside the fake home
        self.assertIn("$env:SEAT_DESK", r.SHORTCUT_PS)
        self.assertIn("SEAT_DESK=str(desktop)", inspect.getsource(r.read_shortcuts))
        desk = pathlib.Path(two["env"]["SEAT_DESK"])
        self.assertEqual((desk.parent, two["desk"]), (scratch, []))
        self.assertEqual(pathlib.Path(three["argv"][6]).parent, scratch)
        for start, extra in ((one, {}), (two, {"SEAT_DESK": str(desk)}), (three, {})):
            self.assertEqual(start["env"], _helpers.profile_env(home, scratch / "temp", **extra))   # fake profile only
            self.assertEqual((start["kw"]["timeout"], start["kw"]["stdin"], start["kw"]["cwd"]),
                             (60, subprocess.DEVNULL, str(scratch)))
        self.assertFalse(scratch.exists())                                  # the scratch folder is removed after

    @unittest.skipUnless(WIN and os.path.isfile(_helpers.POWERSHELL), "PowerShell")
    def test_without_the_kits_module_or_powershell_the_probe_falls_back(self):
        argvs = []

        def run(argv, **k):
            argvs.append(argv)
            plant(k["env"]["USERPROFILE"], "AppData/Local/One/x")
        saved = sys.modules.get("seat_remove")
        sys.modules["seat_remove"] = None                                   # import seat_remove now fails
        try:
            self.assertEqual(_helpers.probe_noise(run, sleep=lambda s: None), ["AppData/Local/One"])
        finally:
            sys.modules["seat_remove"] = saved
        self.assertEqual(argvs, [[_helpers.POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", "exit"]])

        def broken(argv, **k):
            raise OSError(2, "not there")
        self.assertEqual(_helpers.probe_noise(broken, sleep=lambda s: None), [])

        argvs.clear()

        def refuse_two(argv, **k):                                          # a start that times out is passed over
            argvs.append(argv)
            if len(argvs) == 2:
                raise subprocess.TimeoutExpired(argv, 60)
            plant(k["env"]["USERPROFILE"], "AppData/%s/x" % ("Local/One" if len(argvs) == 1 else "Roaming/Three"))
        self.assertEqual(_helpers.probe_noise(refuse_two, sleep=lambda s: None),
                         ["AppData/Local/One", "AppData/Roaming/Three"])
        self.assertEqual(len(argvs), 3)

    @unittest.skipUnless(WIN and os.path.isfile(_helpers.POWERSHELL), "PowerShell")
    def test_the_probe_lists_again_after_a_pause_and_keeps_both(self):
        homes = []

        def run(argv, **k):
            homes.append(pathlib.Path(k["env"]["USERPROFILE"]))
            plant(homes[-1], "AppData/Local/Early/x")

        def late(seconds):                                                  # a scanner writing just after the exit
            self.assertEqual(seconds, _helpers.PROBE_SETTLE)
            shutil.rmtree(str(homes[-1] / "AppData" / "Local" / "Early"))
            plant(homes[-1], "AppData/Roaming/Late/MBAMSI.log")
        self.assertEqual(_helpers.probe_noise(run, sleep=late), ["AppData/Local/Early", "AppData/Roaming/Late"])

    @unittest.skipUnless(WIN and os.path.isfile(_helpers.POWERSHELL), "PowerShell")
    def test_a_scratch_folder_that_cannot_be_removed_keeps_the_list(self):
        scratch, held, calls = [], [], []

        def run(argv, **k):
            home = pathlib.Path(k["env"]["USERPROFILE"])
            scratch.append(home.parent)
            plant(home, "AppData/Local/Malwarebytes/Logs/MBAMSI.log")
            if not held:                                                    # a scanner keeps its log open
                held.append(open(str(home / "AppData" / "Local" / "Malwarebytes" / "Logs" / "MBAMSI.log"), "rb"))
        try:
            self.assertEqual(_helpers.probe_noise(run, sleep=lambda s: None), ["AppData/Local/Malwarebytes"])
            self.assertTrue(scratch[0].exists())                            # it could not be removed, and that is all
        finally:
            for h in held:
                h.close()
        shutil.rmtree(str(scratch[0]), ignore_errors=True)
        real_rmtree = shutil.rmtree

        def no_op(path, *a, **k):                                           # removal that does nothing at all
            calls.append((path, k))
        shutil.rmtree = no_op
        try:
            self.assertEqual(_helpers.probe_noise(run, sleep=lambda s: None), ["AppData/Local/Malwarebytes"])
        finally:
            shutil.rmtree = real_rmtree
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], {"ignore_errors": True})
        self.assertTrue(scratch[-1].exists())
        real_rmtree(calls[0][0])

    @unittest.skipUnless(WIN and os.path.isfile(_helpers.POWERSHELL), "PowerShell")
    def test_a_real_probe_here_writes_nothing_in_its_fake_home_outside_appdata(self):
        tops = []

        def look(home):
            tops.append(sorted(p.name for p in home.iterdir()))
            tops.append(sorted(p.name for p in (home / "AppData").iterdir()))
        found = _helpers.probe_noise(look=look)
        self.assertEqual(tops, [["AppData"], ["Local", "Roaming"]], found)
        for n in found:
            self.assertRegex(n, r"^AppData/(Local|Roaming)/[^/]+$")

    def test_a_stray_write_is_still_seen_and_only_the_named_folders_are_ignored(self):
        before = tree(self.home, SYNTHETIC)
        self.assertNotIn("AppData/Local/Malwarebytes", before)
        self.assertIn("AppData/Local/seat-kit/kit.txt", before)
        plant(self.home, "AppData/Local/Malwarebytes/Logs/MBAMSI.log", "changed")       # the noise itself
        plant(self.home, "AppData/Local/Microsoft/Windows/new.dat")
        self.assertEqual(tree(self.home, SYNTHETIC), before)
        for rel, change in (("AppData/Local/Other/x", "a new folder of someone else's"),
                            ("Desktop/stray.txt", "a desktop file"),
                            ("Assistant/stray.txt", "an entry in the seat home"),
                            ("AppData/Local/seat-kit/kit.txt", "a change inside the kit's own folder"),
                            ("AppData/Roaming/Microsoft/x", "the same name, but not the folder that was named"),
                            ("AppData/Local/MicrosoftX/x", "a name that only starts like a named one"),
                            ("AppData/Local/Malwarebytes/Logs/kit.txt", "a kit file name inside a named folder"),
                            ("AppData/Local/Microsoft/Juniper.lnk", "a shortcut inside a named folder")):
            with self.subTest(change):
                old = (self.home / rel).read_bytes() if (self.home / rel).exists() else None
                plant(self.home, rel, "planted")
                try:
                    self.assertNotEqual(tree(self.home, SYNTHETIC), before, rel)
                finally:
                    if old is None:
                        (self.home / rel).unlink()
                        if rel.startswith("AppData/Local/Other/"):
                            (self.home / "AppData" / "Local" / "Other").rmdir()
                        if rel.startswith(("AppData/Roaming/Microsoft/", "AppData/Local/MicrosoftX/")):
                            (self.home / rel).parent.rmdir()
                    else:
                        (self.home / rel).write_bytes(old)
        self.assertEqual(tree(self.home, SYNTHETIC), before)
        self.assertEqual(tree(self.home, []), test_selftest.tree(self.home))

    def test_this_pcs_noise_is_bounded(self):
        noise = _helpers.powershell_noise()
        self.assertEqual(noise, _helpers.powershell_noise())                # computed once, the same every call
        for n in noise:
            self.assertRegex(n, r"^AppData/(Local|Roaming)/[^/]+$")
            self.assertNotIn(n.rsplit("/", 1)[1].lower(), [k.lower() for k in _helpers.KIT_APPDATA])
        self.assertIn(_helpers.ignored_note(noise), ignored())
        self.assertIn("(tree compared without nothing", _helpers.ignored_note([]))


def tree_015(folder):
    """The comparison as 0.1.5 had it: only AppData/Local/Microsoft left out."""
    own = "AppData/Local/Microsoft"
    return {k: v for k, v in test_selftest.tree(folder).items() if k != own and not k.startswith(own + "/")}


def carries_script(argv):
    """True when a PowerShell command line runs script content: -File, or -Command with anything but a bare exit."""
    a = [str(x) for x in argv]
    low = [x.lower() for x in a]
    if "-file" in low:
        return True
    if "-command" in low:
        return " ".join(a[low.index("-command") + 1:]).strip().lower() != "exit"
    return False


def _bare_exit_probe():
    """0.1.6's first draft of the probe, kept here only to show what it would have missed: two bare
    `-Command exit` starts on a scratch fake profile, then the folders under AppData/Local and AppData/Roaming."""
    scratch = tempfile.mkdtemp(prefix="seat-kit-probe-")
    try:
        td = _helpers.real(scratch)
        home, temp = td / "probe home", td / "temp"
        for d in (home / "AppData" / "Local", home / "AppData" / "Roaming", temp):
            d.mkdir(parents=True)
        for _ in range(2):
            subprocess.run([_helpers.POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", "exit"],
                           capture_output=True, timeout=60, env=_helpers.profile_env(home, temp),
                           stdin=subprocess.DEVNULL, cwd=str(td))
        found = ["%s/%s" % (w, p.name) for w in ("Local", "Roaming") for p in (home / "AppData" / w).iterdir()
                 if p.is_dir()]
        return _helpers.noise_list(found)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


class ColleaguePC(Base):
    """Kit 0.1.5's self test failed on a colleague's PC with Malwarebytes: its script scanner, inside the PowerShell
    the tests start, wrote AppData/Local/Malwarebytes/Logs/MBAMSI.log into the fake profile. Simulated here, in this
    class only: every PowerShell start that carries script content (-File, or -Command with more than a bare exit)
    appends to that log in the profile it was given. The kit's own starts all carry script content.

    The three *_fails_015_and_passes_016 tests patch the probe's result to report Malwarebytes, so they prove the
    comparison logic only: the 0.1.5 comparison fails as it did there and the 0.1.6 one passes. The test of the real
    probe does not patch the probe: it proves the probe reports Malwarebytes from such a scanner, through its
    script-carrying starts (2 and 3), and that the bare-exit-only first draft would not have. None of these runs the
    self test summary; the summary lines are tested in test_selftest."""

    def setUp(self):
        super().setUp()
        self.starts, self.planted = [], []
        real_run, self.saved_run = subprocess.run, subprocess.run
        self.saved_noise = _helpers.powershell_noise

        def run(argv, *a, **k):
            res = real_run(argv, *a, **k)
            exe = argv[0] if isinstance(argv, (list, tuple)) else str(argv)
            if os.path.basename(str(exe)).lower() in ("powershell", "powershell.exe"):
                env = k.get("env") or os.environ
                self.starts.append({v: env.get(v) for v in _helpers.PROFILE_VARS})
                if carries_script(argv):
                    self.planted.append(list(argv))
                    log = pathlib.Path(env["USERPROFILE"]) / "AppData/Local/Malwarebytes/Logs/MBAMSI.log"
                    log.parent.mkdir(parents=True, exist_ok=True)
                    with open(str(log), "a", encoding="utf-8") as f:
                        f.write("scan %d\n" % len(self.starts))
            return res
        subprocess.run = run                                                # the kit calls subprocess.run
        self.addCleanup(setattr, subprocess, "run", self.saved_run)
        _helpers.powershell_noise = lambda: list(SYNTHETIC)
        self.addCleanup(setattr, _helpers, "powershell_noise", self.saved_noise)

    def test_the_simulation_acts_only_on_script_content(self):
        ps = _helpers.POWERSHELL
        self.assertFalse(carries_script([ps, "-NoProfile", "-NonInteractive", "-Command", "exit"]))
        self.assertFalse(carries_script([ps, "-NoProfile", "-NonInteractive", "-Command", " EXIT "]))
        self.assertTrue(carries_script([ps, "-NoProfile", "-NonInteractive", "-Command", r.SHORTCUT_PS]))
        self.assertTrue(carries_script(["powershell", "-NoProfile", "-Command", "exit 0"]))
        self.assertTrue(carries_script([ps, "-ExecutionPolicy", "Bypass", "-File", "x.ps1"]))

    @unittest.skipUnless(os.path.isfile(_helpers.POWERSHELL), "PowerShell")
    def test_the_real_probe_reports_a_scanner_that_acts_only_on_script_content(self):
        found = _helpers.probe_noise()                                      # NOT patched: real PowerShell starts
        self.assertIn("AppData/Local/Malwarebytes", found)
        self.assertEqual(len(self.starts), 3)
        self.assertEqual([a[3:] if a[3] == "-Command" else a[3:6] for a in self.planted],
                         [["-Command", r.SHORTCUT_PS], ["-ExecutionPolicy", "Bypass", "-File"]])   # runs 2 and 3
        for env in self.starts:                                             # each on the probe's fake profile
            home = env["USERPROFILE"]
            self.assertEqual(pathlib.Path(home).name, "probe home")
            self.assertNotEqual(_helpers.norm(home), _helpers.norm(self.real_profile["USERPROFILE"] or "x"))
            for k in _helpers.PROFILE_VARS:
                self.assertEqual(os.path.commonpath([env[k], home]), home, k)

        self.starts, self.planted = [], []
        self.assertNotIn("AppData/Local/Malwarebytes", _bare_exit_probe())  # the first draft would have missed it
        self.assertEqual((len(self.starts), self.planted), (2, []))

    def assert_only_fake_profiles(self, least=1):
        self.assertGreaterEqual(len(self.starts), least)
        for env in self.starts:                                             # no PowerShell saw the real profile
            for k, v in env.items():
                self.assertIsNotNone(v, k)
                self.assertEqual(os.path.commonpath([v, str(self.home)]), str(self.home), k)

    def test_adopted_style_a_new_folder_fails_015_and_passes_016(self):
        her = self.base / "Her Folder"
        self.kit_home(launcher=False)
        adopt_by_hand(her, self.kh)
        old, new = tree_015(self.home), tree(self.home)
        self.assertNotIn("AppData/Local/Malwarebytes", old)                 # no PowerShell has run yet
        for extra in (("--check",), ()):
            with self.subTest(extra=extra):
                rc, out = self.run_remove(*extra)                           # reads the desktop through PowerShell
                self.assertEqual(rc, 0, out)
                self.assertNotEqual(tree_015(self.home), old)               # 0.1.5: "Tuples differ" (a new folder)
                self.assertEqual(tree(self.home), new, ignored(out))
                self.assertIn("AppData/Local/Malwarebytes", ignored(out))
        self.assert_only_fake_profiles(2)

    def test_check_style_a_changed_log_fails_015_and_passes_016(self):
        self.first_install()                                                # its shortcut is made through PowerShell
        self.assertTrue((self.home / "AppData/Local/Malwarebytes/Logs/MBAMSI.log").is_file())
        old, new = tree_015(self.home), tree(self.home)
        rc, out = self.run_remove("--check")
        self.assertEqual(rc, 0, out)
        self.assertNotEqual(tree_015(self.home), old)                       # 0.1.5: the log's hash changed
        self.assertEqual(tree(self.home), new, ignored(out))
        self.assert_only_fake_profiles(2)

    def test_remove_style_everything_outside_fails_015_and_passes_016(self):
        seat = self.first_install()
        skip = ("AppData/Local/seat-kit", "Assistant", "Desktop/Juniper.lnk")

        def outside_015():
            return {k: v for k, v in tree_015(self.home).items() if not any(k == x or k.startswith(x + "/") for x in skip)}
        old, new = outside_015(), self.outside("Desktop/Juniper.lnk")
        rc, out = self.run_remove()
        self.assertEqual(rc, 0, out)
        self.assertFalse(seat.exists() or self.kh.exists())
        self.assertNotEqual(outside_015(), old)
        self.assertEqual(self.outside("Desktop/Juniper.lnk"), new, ignored(out))
        self.assert_only_fake_profiles(2)


@unittest.skipUnless(WIN, "Windows shortcuts and folders")
class SetUpFails(unittest.TestCase):
    def test_a_setup_that_fails_after_the_fake_profile_leaves_the_environment_as_it_was(self):
        before = {k: os.environ.get(k) for k in _helpers.PROFILE_VARS}
        during = []

        def boom():                                                         # Base.setUp's next line after the fake
            during.append(os.environ.get("USERPROFILE"))
            raise RuntimeError("setUp broke")

        class Broken(Base):
            def test_nothing(self):
                pass
        saved = globals()["FakeClaude"]
        globals()["FakeClaude"] = boom
        try:
            case = Broken("test_nothing")
            result = unittest.TestResult()
            case.run(result)
        finally:
            globals()["FakeClaude"] = saved
            case.td.cleanup()
        self.assertEqual(len(result.errors), 1, result.errors)
        self.assertIn("setUp broke", result.errors[0][1])
        self.assertEqual(during, [str(case.home)])                          # the fake was in place when it broke
        self.assertEqual({k: os.environ.get(k) for k in _helpers.PROFILE_VARS}, before)


class Docs(unittest.TestCase):
    def test_readme_and_hand_test_say_how_to_take_it_back(self):
        for name in ("README.md", "HAND_TEST.md"):
            text = (_helpers.KIT / name).read_text(encoding="utf-8")
            for words in ("remove --check", "adopt --undo", "--delete-my-notes"):
                self.assertIn(words, text, name)


if __name__ == "__main__":
    unittest.main()
