"""Step 3 since 0.1.4: a new kit goes into kits\\<version> and kit.txt is switched; nothing in use is renamed,
overwritten or deleted. Also the send gate's fixed address (fails closed), the plugin source moving with the kit,
and both launchers end to end (selftest and adopt never run an older kit; every mode prints the kit version).

Folders are held open by REAL child processes (a working folder inside, or a file kept open). Each child exits by
itself within 120 seconds and is stopped and waited for in tearDown. The launchers run against kits whose
seat_setup.py is a stand-in that only records that it ran: nothing is installed, downloaded or started.
"""
import io
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from contextlib import redirect_stdout

from tests import _helpers
import kit_place as kp
import seat_setup as s

sys.path.insert(0, str(_helpers.KIT / "tools"))
import make_kit  # noqa: E402

ROOT = os.environ.get("SystemRoot", r"C:\Windows")
LAUNCHER = _helpers.KIT / "launcher"
NEED = json.loads((_helpers.KIT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
SEND = "mcp__claude_ai_Gmail__send_message"
READ_EVENTS = "mcp__claude_ai_Google_Calendar__list_events"
STUB = '''import json, pathlib, sys
kit = pathlib.Path(__file__).resolve().parent.parent
v = json.loads((kit / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
a = sys.argv[1:]
mode = a[0] if a and not a[0].startswith("--") else "start"
kh = pathlib.Path(a[a.index("--kit-home") + 1])
print("STUB kit %s ran: %s" % (v, mode))
with open(kh / "stub-ran.txt", "a", encoding="utf-8") as f:
    f.write("%s %s\\n" % (v, mode))
if mode == "start":
    (pathlib.Path(a[a.index("--seat-home") + 1]) / "Stub").mkdir(parents=True, exist_ok=True)
    (kh / "last-seat.txt").write_text("Stub|Stub\\n", encoding="ascii")
'''


def kit_files(version, stub=True, placer=True):
    """{zip member: bytes} of a small kit: the real placer, gate shim and send gate, a stand-in seat_setup."""
    p = "plugins/ml-seat-kit/"
    files = {".claude-plugin/marketplace.json": json.dumps(make_kit.marketplace(version)).encode("utf-8"),
             p + ".claude-plugin/plugin.json": json.dumps({"name": "ml-seat-kit", "version": version}).encode("utf-8"),
             p + "scripts/seat_setup.py": STUB.encode("utf-8") if stub else
             (_helpers.SCRIPTS / "seat_setup.py").read_bytes(),
             p + "scripts/send_gate.py": (_helpers.SCRIPTS / "send_gate.py").read_bytes(),
             p + "scripts/gate_shim.py": (_helpers.SCRIPTS / "gate_shim.py").read_bytes()}
    if placer:
        files["__main__.py"] = make_kit.ZIP_MAIN
        files[p + "scripts/kit_place.py"] = (_helpers.SCRIPTS / "kit_place.py").read_bytes()
    return files


def make_zip(path, version, **kw):
    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(kit_files(version, **kw).items()):
            z.writestr(name, data)
    return path


def make_tree(folder, version, **kw):
    for name, data in kit_files(version, **kw).items():
        f = pathlib.Path(folder) / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    return pathlib.Path(folder)


HOLDER = ("import os,sys,time\n"
          "mode,path,ready=sys.argv[1:4]\n"
          "h=None\n"
          "if mode=='cwd': os.chdir(path)\n"
          "else: h=open(path,'rb')\n"
          "open(ready,'w').write(str(os.getpid()))\n"
          "end=time.time()+120\n"
          "while time.time()<end and not os.path.exists(ready+'.stop'): time.sleep(0.1)\n")


class Held:
    """Real child processes holding folders: 'cwd' (working folder inside) or 'file' (a file kept open)."""

    def __init__(self, base):
        self.base, self.kids = pathlib.Path(base), []

    def hold(self, mode, path):
        ready = self.base / ("held-%d.ready" % len(self.kids))
        p = subprocess.Popen([sys.executable, "-I", "-c", HOLDER, mode, str(path), str(ready)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             cwd=str(self.base))
        self.kids.append((p, ready))
        for _ in range(200):
            if ready.exists() and ready.read_text().strip():
                return p
            if p.poll() is not None:
                break
            time.sleep(0.05)
        raise AssertionError("the holder did not start")

    def release(self):
        for p, ready in self.kids:
            try:
                pathlib.Path(str(ready) + ".stop").write_text("x")
            except OSError:
                pass
        for p, _ in self.kids:
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=30)
        gone = all(p.poll() is not None for p, _ in self.kids)
        self.kids = []
        return gone


def deep(kit):
    return pathlib.Path(kit) / "plugins" / "ml-seat-kit"


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.tmp = _helpers.real(self.td) / "with space"
        self.kh = self.tmp / "kit home"
        self.kh.mkdir(parents=True)
        self.held = Held(self.tmp)

    def tearDown(self):
        self.assertTrue(self.held.release(), "a holder did not exit")
        for p in self.tmp.rglob("*"):                 # read-only leftovers made by a test
            try:
                os.chmod(str(p), stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
        self.td.cleanup()

    def place_zip(self, zpath):
        """Run the zip exactly as the launcher does: python -I <zip> --kit-home <kit home>."""
        r = subprocess.run([sys.executable, "-I", str(zpath), "--kit-home", str(self.kh)], capture_output=True,
                           text=True, timeout=120, cwd=str(self.tmp), stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout + r.stderr

    def pointer(self):
        return (self.kh / "kit.txt").read_text(encoding="ascii").strip()


class Placing(Base):
    def test_a_fresh_kit_home(self):
        rc, out = self.place_zip(make_zip(self.tmp / "a.zip", "0.1.4"))
        self.assertEqual((rc, out.strip()), (0, "done"))
        self.assertEqual(self.pointer(), "0.1.4")
        self.assertEqual((self.kh / "kit.txt").read_bytes(), b"0.1.4\r\n")      # CRLF for cmd's set /p
        self.assertTrue((self.kh / "kits" / "0.1.4" / kp.SETUP).is_file())
        self.assertEqual((self.kh / "send_gate.py").read_bytes(), (_helpers.SCRIPTS / "gate_shim.py").read_bytes())

    def test_the_same_zip_again_is_used_as_it_is(self):
        z = make_zip(self.tmp / "a.zip", "0.1.4")
        self.place_zip(z)
        stamp = (self.kh / "kits" / "0.1.4" / kp.SETUP).stat().st_mtime_ns
        rc, out = self.place_zip(z)
        self.assertEqual((rc, out.strip()), (0, "done (this version was already here)"))
        self.assertEqual(sorted(p.name for p in (self.kh / "kits").iterdir()), ["0.1.4"])
        self.assertEqual((self.kh / "kits" / "0.1.4" / kp.SETUP).stat().st_mtime_ns, stamp)

    def test_a_different_zip_with_the_same_version_gets_its_own_folder(self):
        self.place_zip(make_zip(self.tmp / "a.zip", "0.1.4"))
        with zipfile.ZipFile(str(make_zip(self.tmp / "b.zip", "0.1.4")), "a") as z:
            z.writestr("extra.txt", "changed")
        rc, _ = self.place_zip(self.tmp / "b.zip")
        self.assertEqual((rc, self.pointer()), (0, "0.1.4-2"))

    def test_held_open_versioned_kit_working_folder_and_open_file(self):
        """The kit in use (kits\\0.1.3) is held by a process working inside it AND by an open file in it."""
        old = make_tree(self.kh / "kits" / "0.1.3", "0.1.3")
        (old / kp.READY).write_text("x\n", encoding="ascii")
        (self.kh / "kit.txt").write_bytes(b"0.1.3\r\n")
        self.held.hold("cwd", deep(old))
        self.held.hold("file", deep(old) / "scripts" / "send_gate.py")
        rc, out = self.place_zip(make_zip(self.tmp / "a.zip", "0.1.4"))
        self.assertEqual((rc, out.strip(), self.pointer()), (0, "done", "0.1.4"))
        self.assertTrue((deep(old) / "scripts" / "send_gate.py").is_file())    # in use: left, and no error
        self.assertTrue(self.held.release())
        self.assertEqual(kp.cleanup(self.kh), ["0.1.3"])                         # a later run removes it

    def test_held_open_old_current_is_never_touched(self):
        cur = make_tree(self.kh / "current", "0.1.2", placer=False)
        before = {p.relative_to(cur).as_posix(): p.read_bytes() for p in cur.rglob("*") if p.is_file()}
        self.held.hold("cwd", deep(cur))
        self.held.hold("file", deep(cur) / "scripts" / "seat_setup.py")
        rc, out = self.place_zip(make_zip(self.tmp / "a.zip", "0.1.4"))
        self.assertEqual((rc, out.strip(), self.pointer()), (0, "done", "0.1.4"))
        self.assertEqual({p.relative_to(cur).as_posix(): p.read_bytes() for p in cur.rglob("*") if p.is_file()},
                         before)

    def test_her_leftovers_do_not_stop_it_and_are_cleaned_where_possible(self):
        """incoming with a file held open, a read-only kit.zip and previous, as stopped 0.1.3 runs leave them."""
        make_tree(self.kh / "current", "0.1.2", placer=False)
        make_tree(self.kh / "incoming", "0.1.3", placer=False)
        make_tree(self.kh / "previous", "0.1.1", placer=False)
        (self.kh / "kit.zip").write_bytes(b"part of an old download")
        os.chmod(str(self.kh / "kit.zip"), stat.S_IREAD)
        self.held.hold("file", deep(self.kh / "incoming") / "scripts" / "seat_setup.py")
        rc, out = self.place_zip(make_zip(self.tmp / "a.zip", "0.1.4"))
        self.assertEqual((rc, out.strip(), self.pointer()), (0, "done", "0.1.4"))
        self.assertFalse((self.kh / "kit.zip").exists())
        self.assertFalse((self.kh / "previous").exists())
        self.assertTrue((self.kh / "current").is_dir())

    def test_no_new_folder_can_be_made_stops_with_windows_words(self):
        (self.kh / "kits").write_text("a file where the kits folder goes", encoding="ascii")
        (self.kh / "kit.txt").write_bytes(b"0.1.3\r\n")
        old = kp.BACKOFF
        kp.BACKOFF = (0.01,)
        try:
            with redirect_stdout(io.StringIO()) as out:
                rc = kp.main(["--kit-home", str(self.kh), "--zip", str(make_zip(self.tmp / "a.zip", "0.1.4"))])
        finally:
            kp.BACKOFF = old
        lines = out.getvalue().splitlines()
        self.assertEqual(rc, 3)
        self.assertEqual(lines[0], "not done")
        self.assertTrue(lines[1].startswith("   The folder ") and lines[1].endswith(" could not be made."), lines)
        self.assertTrue(lines[2].startswith("   Windows said: "), lines)
        self.assertEqual(self.pointer(), "0.1.3")

    def test_a_refused_unpack_is_tried_again_in_a_fresh_folder(self):
        z = make_zip(self.tmp / "a.zip", "0.1.4")
        src = kp.Source(zip_path=str(z))
        real, calls = src.unpack, []

        def flaky(into):
            calls.append(pathlib.Path(into).name)
            if len(calls) == 1:
                pathlib.Path(into).mkdir(parents=True)
                raise PermissionError(13, "Access is denied", str(into))
            return real(into)
        src.unpack = flaky
        old = kp.BACKOFF
        kp.BACKOFF = (0.01, 0.01)
        try:
            name, how = kp.place(self.kh, src)
        finally:
            kp.BACKOFF = old
        self.assertEqual(calls, ["0.1.4", "0.1.4-2"])
        self.assertEqual((name, how, self.pointer()), ("0.1.4-2", "new", "0.1.4-2"))

    def test_a_damaged_zip_is_not_done(self):
        (self.tmp / "bad.zip").write_bytes(b"not a zip")
        with redirect_stdout(io.StringIO()) as out:
            rc = kp.main(["--kit-home", str(self.kh), "--zip", str(self.tmp / "bad.zip")])
        self.assertEqual(rc, 3)
        self.assertIn("The downloaded kit could not be read.", out.getvalue())
        self.assertFalse((self.kh / "kit.txt").exists())

    def test_cleanup_keeps_the_kit_in_use_and_the_registered_one(self):
        for v in ("0.1.2", "0.1.3", "0.1.4"):
            make_tree(self.kh / "kits" / v, v)
            (self.kh / "kits" / v / kp.READY).write_text("x\n", encoding="ascii")
        (self.kh / "kit.txt").write_bytes(b"0.1.4\r\n")
        (self.kh / "market.txt").write_text(str(self.kh / "kits" / "0.1.3") + "\n", encoding="utf-8")
        self.assertEqual(kp.cleanup(self.kh), ["0.1.2"])
        self.assertEqual(sorted(p.name for p in (self.kh / "kits").iterdir()), ["0.1.3", "0.1.4"])

    def test_cleanup_removes_nothing_without_a_good_pointer(self):
        make_tree(self.kh / "kits" / "0.1.3", "0.1.3")
        (self.kh / "kit.txt").write_bytes(b"0.1.9\r\n")
        self.assertEqual(kp.cleanup(self.kh), [])
        self.assertTrue((self.kh / "kits" / "0.1.3").is_dir())

    def test_pointer_problems_are_plain_reasons(self):
        with self.assertRaises(kp.NotPlaced) as c:
            kp.read_pointer(self.kh)
        self.assertEqual(c.exception.what, "kit.txt is missing")
        (self.kh / "kit.txt").write_text("..\\..\\elsewhere\n", encoding="ascii")
        with self.assertRaises(kp.NotPlaced) as c:
            kp.read_pointer(self.kh)
        self.assertEqual(c.exception.what, "kit.txt does not name a kit folder")
        (self.kh / "kit.txt").write_text("0.1.9\n", encoding="ascii")
        with self.assertRaises(kp.NotPlaced) as c:
            kp.read_pointer(self.kh)
        self.assertIn("is not there", c.exception.what)


class GateAddress(Base):
    """The hook a seat's settings hold, spawned with no shell, as Claude Code runs it."""

    def setUp(self):
        Base.setUp(self)
        self.seat = self.tmp / "My Seat"
        (self.seat / ".seat").mkdir(parents=True)
        (self.seat / ".seat" / "seat.json").write_text("{}", encoding="utf-8")
        self.rc, _ = self.place_zip(make_zip(self.tmp / "a.zip", "0.1.4"))
        self.kit = deep(self.kh / "kits" / "0.1.4")
        self.hook = s.gate_hook(sys.executable, self.kh, kit=self.kit)["hooks"][0]
        self.env = {k: v for k, v in os.environ.items() if k.upper() in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")}
        self.env.update(PATH=os.pathsep.join([os.path.join(ROOT, "System32"), ROOT]),
                        CLAUDE_PROJECT_DIR=str(self.seat))

    def spawn(self, tool, hook=None):
        hook = hook or self.hook
        return subprocess.run([hook["command"]] + hook["args"], capture_output=True, text=True, timeout=60,
                              input=json.dumps({"tool_name": tool, "cwd": str(self.seat), "tool_input": {}}),
                              cwd=str(self.seat), env=self.env)

    def test_the_hook_names_the_fixed_address(self):
        self.assertEqual(self.rc, 0)
        self.assertTrue(s.versioned(self.kh, self.kit))
        self.assertEqual(_helpers.norm(self.hook["args"][1]), _helpers.norm(self.kh / "send_gate.py"))
        self.assertEqual(self.hook["onFailure"], "block")

    def test_a_good_pointer_runs_the_real_gate(self):
        r = self.spawn(SEND)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertTrue(r.stderr.startswith("Drafts only:"), r.stderr)
        self.assertNotIn("could not run", r.stderr)
        self.assertIn("DENY " + SEND, (self.seat / ".seat" / "logs" / "send_gate.log").read_text(encoding="utf-8"))
        self.assertEqual(self.spawn(READ_EVENTS).returncode, 0)

    def test_a_broken_pointer_fails_closed(self):
        for content in (None, b"0.1.9\r\n", b"..\\..\\x\r\n", b"\xff\xfe"):
            with self.subTest(content=content):
                p = self.kh / "kit.txt"
                if p.exists():
                    p.unlink()
                if content is not None:
                    p.write_bytes(content)
                for tool in (SEND, READ_EVENTS):
                    r = self.spawn(tool)
                    self.assertEqual(r.returncode, 2, (tool, r.stderr))
                    self.assertIn("the send gate could not run", r.stderr)

    def test_a_hook_into_the_old_current_keeps_working_after_the_upgrade(self):
        """A seat adopted with 0.1.3 or older names <kit home>\\current\\...\\send_gate.py; 0.1.4 leaves it there."""
        make_tree(self.kh / "current", "0.1.3", placer=False)
        old_hook = s.gate_hook(sys.executable, self.kh)["hooks"][0]           # not run from kits: the old address
        self.assertEqual(_helpers.norm(old_hook["args"][1]),
                         _helpers.norm(deep(self.kh / "current") / "scripts" / "send_gate.py"))
        self.assertEqual(self.place_zip(make_zip(self.tmp / "b.zip", "0.1.5"))[0], 0)
        r = self.spawn(SEND, old_hook)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertTrue(r.stderr.startswith("Drafts only:"), r.stderr)

    def test_a_hook_into_a_folder_that_is_gone_still_blocks(self):
        """Even the old address, were its folder ever gone: python cannot open the script, exit 2, a block."""
        gone = dict(self.hook, args=["-I", str(self.kh / "current" / "nothing" / "send_gate.py")])
        self.assertEqual(self.spawn(SEND, gone).returncode, 2)

    def test_setup_puts_the_gate_file_in_place_before_naming_it(self):
        (self.kh / "send_gate.py").unlink()
        s.ensure_gate_file(self.kh, 5, kit=self.kit)
        self.assertEqual((self.kh / "send_gate.py").read_bytes(), (self.kit / "scripts" / "gate_shim.py").read_bytes())


class PluginSourceMoves(Base):
    def setUp(self):
        Base.setUp(self)
        self.kit = deep(make_tree(self.kh / "kits" / "0.1.4", "0.1.4"))
        self.calls = []

    def fake(self, registered=True, remove_rc=0):
        def _claude(claude, *a, timeout=180):
            line = " ".join(a)
            self.calls.append(line)
            if line.startswith("plugin marketplace list"):
                return 0, '[{"name": "seat-kit"}]' if registered else "[]"
            if line.startswith("plugin list"):
                return 0, '[{"id": "ml-seat-kit@seat-kit"}]'
            if line.startswith("plugin marketplace remove"):
                return remove_rc, "no"
            return 0, "ok"
        return _claude

    def run_ensure(self, **kw):
        orig, s._claude = s._claude, self.fake(**kw)
        try:
            with redirect_stdout(io.StringIO()):
                s.ensure_plugin("claude", str(self.kh), kit=self.kit)
        finally:
            s._claude = orig

    def test_a_registration_at_the_old_current_moves_to_the_new_folder(self):
        self.run_ensure()
        folder = s.market_folder(self.kh, self.kit)
        self.assertEqual(_helpers.norm(folder), _helpers.norm(self.kh / "kits" / "0.1.4"))
        self.assertEqual(self.calls, ["plugin marketplace list --json", "plugin marketplace remove seat-kit",
                                      "plugin marketplace add %s --scope user" % folder, "plugin list --json",
                                      "plugin update ml-seat-kit@seat-kit"])
        self.assertEqual((self.kh / "market.txt").read_text(encoding="utf-8").strip(), folder)
        self.calls = []
        self.run_ensure()                                         # the next start: the same folder, just update
        self.assertEqual(self.calls[1], "plugin marketplace update seat-kit")

    def test_a_refused_remove_stops(self):
        with self.assertRaises(s.Stop) as c:
            self.run_ensure(remove_rc=1)
        self.assertIn("did not let go of the older kit folder", str(c.exception))

    def test_first_time_adds_the_new_folder(self):
        self.run_ensure(registered=False)
        self.assertTrue(self.calls[1].startswith("plugin marketplace add ") and "0.1.4" in self.calls[1])


@unittest.skipUnless(sys.platform == "win32", "Windows launchers")
class Launchers(unittest.TestCase):
    """Both launchers, end to end, in a made-up profile. Every printed line of the two must be the same."""

    def setUp(self):
        if os.environ.get("SEAT_KIT_SHORT_TEMP_RUN") == "1":
            # these runs set their own TEMP, so the short-TEMP repeat of the suite would only run them twice
            self.skipTest("the launcher runs set their own TEMP; they run once, in the main run")
        self.td = tempfile.TemporaryDirectory()
        self.base = _helpers.real(self.td) / "with space"
        self.home = self.base / "home"
        self.kh = self.home / "AppData" / "Local" / "seat-kit"
        self.kh.mkdir(parents=True)
        (self.home / "AppData" / "Roaming").mkdir(parents=True)
        (self.base / "temp").mkdir()
        claude = self.home / ".local" / "bin" / "claude.exe"   # an empty stand-in: found, never run
        claude.parent.mkdir(parents=True)
        claude.write_bytes(b"")
        self.zip = make_zip(self.base / "seat-kit.zip", NEED)
        self.folder = self.base / "Her Folder"
        self.folder.mkdir()
        self.held = Held(self.base)

    def tearDown(self):
        self.assertTrue(self.held.release(), "a holder did not exit")
        self.td.cleanup()

    def env(self, source=None):
        keep = {k: v for k, v in os.environ.items() if k.upper() in ("COMSPEC", "PATHEXT", "PSMODULEPATH")}
        keep.update(SystemRoot=ROOT, windir=ROOT, USERPROFILE=str(self.home),
                    LOCALAPPDATA=str(self.home / "AppData" / "Local"), APPDATA=str(self.home / "AppData" / "Roaming"),
                    TEMP=str(self.base / "temp"), TMP=str(self.base / "temp"), SEAT_HOME=str(self.base / "seats"),
                    SEAT_KIT_SOURCE=str(source or self.zip), SEAT_KIT_DRY_OPEN="1",
                    PATH=os.pathsep.join([os.path.join(ROOT, "System32"),
                                          os.path.join(ROOT, r"System32\WindowsPowerShell\v1.0")]))
        return keep

    def launch(self, kind, *args, source=None):
        (self.kh / "python.txt").write_text(sys.executable + "\n", encoding="ascii")
        if kind == "cmd":
            argv = '"%s" /d /s /c ""%s" %s"' % (os.path.join(ROOT, "System32", "cmd.exe"), LAUNCHER / "Assistant.cmd",
                                                " ".join('"%s"' % a for a in args))
        else:
            argv = [os.path.join(ROOT, r"System32\WindowsPowerShell\v1.0\powershell.exe"), "-NoProfile",
                    "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(LAUNCHER / "Assistant.ps1")] + list(args)
        r = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=180,
                           env=self.env(source), stdin=subprocess.DEVNULL, cwd=str(self.base))
        lines = [ln.rstrip() for ln in r.stdout.splitlines() if ln.strip() and not ln.startswith("Press any key")]
        return r.returncode, lines, r.stdout + r.stderr

    def both(self, *args, source=None, prepare=None):
        """Run the .cmd and the .ps1 on identical kit homes; (exit codes, lines) after checking the lines agree."""
        out = {}
        for kind in ("cmd", "ps1"):
            if self.kh.exists():
                shutil.rmtree(str(self.kh))
            self.kh.mkdir(parents=True)
            if prepare:
                prepare()
            out[kind] = self.launch(kind, *args, source=source)
            self.assertTrue(self.held.release())
            out[kind] = out[kind] + (self.stub_runs(),)
        self.assertEqual(out["cmd"][1], out["ps1"][1], "the two launchers printed different lines:\n%s\n%s" % (
            out["cmd"][2], out["ps1"][2]))
        return out

    def stub_runs(self):
        f = self.kh / "stub-ran.txt"
        return f.read_text(encoding="utf-8").split("\n")[:-1] if f.exists() else []

    def assertVersionBeforeRun(self, lines, version, mode):
        kit_line = [i for i, ln in enumerate(lines) if ln.startswith("Kit in use: %s (" % version)]
        ran = [i for i, ln in enumerate(lines) if ln == "STUB kit %s ran: %s" % (version, mode)]
        self.assertTrue(kit_line and ran and kit_line[0] < ran[0], lines)

    def old_current(self, version="0.1.2"):
        make_tree(self.kh / "current", version, placer=False)

    def old_versioned(self, version="0.1.3"):
        make_tree(self.kh / "kits" / version, version)
        (self.kh / "kits" / version / kp.READY).write_text("x\n", encoding="ascii")
        (self.kh / "kit.txt").write_bytes(version.encode("ascii") + b"\r\n")

    def test_every_mode_prints_the_kit_version_before_running_it(self):
        for args, mode in ((("selftest",), "selftest"), (("adopt", "--check", str(self.folder)), "adopt"),
                           (("adopt", str(self.folder)), "adopt"), ((), "start")):
            with self.subTest(args=args):
                out = self.both(*args)
                for kind in ("cmd", "ps1"):
                    rc, lines, raw, ran = out[kind]
                    self.assertEqual(rc, 0, raw)
                    self.assertVersionBeforeRun(lines, NEED, mode)
                    self.assertEqual(ran, ["%s %s" % (NEED, mode)])
                    self.assertIn("Step 3 of 6: getting the assistant kit ... done", lines)

    def test_held_open_old_current_and_her_leftovers_selftest_runs_the_new_kit(self):
        def prepare():
            self.old_current()
            make_tree(self.kh / "incoming", "0.1.3", placer=False)
            (self.kh / "kit.zip").write_bytes(b"part of an old download")
            self.held.hold("cwd", deep(self.kh / "current"))
            self.held.hold("file", deep(self.kh / "current") / "scripts" / "seat_setup.py")
            self.held.hold("file", deep(self.kh / "incoming") / "scripts" / "seat_setup.py")
        out = self.both("selftest", prepare=prepare)
        for kind in ("cmd", "ps1"):
            rc, lines, raw, ran = out[kind]
            self.assertEqual((rc, ran), (0, ["%s selftest" % NEED]), raw)
            self.assertVersionBeforeRun(lines, NEED, "selftest")

    def test_selftest_and_adopt_never_run_an_older_kit_when_the_download_fails(self):
        missing = self.base / "not-there.zip"
        for prepare, old, args in ((self.old_current, "0.1.2", ("selftest",)),
                                   (self.old_current, "0.1.2", ("adopt", str(self.folder))),
                                   (self.old_versioned, "0.1.3", ("adopt", "--check", str(self.folder))),
                                   (self.old_versioned, "0.1.3", ("selftest",))):
            if True:
                with self.subTest(old=old, args=args):
                    out = self.both(*args, source=missing, prepare=prepare)
                    for kind in ("cmd", "ps1"):
                        rc, lines, raw, ran = out[kind]
                        self.assertNotEqual(rc, 0, raw)
                        self.assertEqual(ran, [], raw)                    # the older kit was NOT run
                        self.assertIn("Stopped at step 3: could not get kit %s, and the kit already on this PC (%s) "
                                      "is never used for %s, so nothing was run." % (NEED, old, args[0]), lines)
                        self.assertIn("   The kit file %s is not there." % missing, lines)
                        self.assertIn("   The kit folder is %s" % self.kh, lines)

    def test_a_failed_https_download_says_what_curl_said(self):
        """An https address, as the real launcher has, refused on this PC itself (port 9 on 127.0.0.1: nothing
        leaves the PC). curl's own words come on a line of their own; nothing older is run."""
        out = {}
        for kind in ("cmd", "ps1"):
            if self.kh.exists():
                shutil.rmtree(str(self.kh))
            self.kh.mkdir(parents=True)
            self.old_current()
            rc, lines, raw = self.launch(kind, "selftest", source="https://127.0.0.1:9/seat-kit.zip")
            out[kind] = [ln for ln in lines if not ln.startswith("   curl: ")]   # its timing differs run to run
            self.assertNotEqual(rc, 0, raw)
            self.assertEqual(self.stub_runs(), [], raw)
            self.assertIn("   The download did not work.", lines)
            self.assertTrue(any(ln.startswith("   curl: (7) ") for ln in lines), raw)
            self.assertNotIn("Join-Path", raw)
            self.assertIn("Stopped at step 3: could not get kit %s, and the kit already on this PC (0.1.2) is never "
                          "used for selftest, so nothing was run." % NEED, lines)
        self.assertEqual(out["cmd"], out["ps1"])

    def test_a_normal_start_runs_the_kit_it_has_and_says_which(self):
        out = self.both(source=self.base / "not-there.zip", prepare=self.old_current)
        for kind in ("cmd", "ps1"):
            rc, lines, raw, ran = out[kind]
            self.assertEqual((rc, ran), (0, ["0.1.2 start"]), raw)
            self.assertIn("   Using the kit already on this laptop.", lines)
            self.assertVersionBeforeRun(lines, "0.1.2", "start")

    def test_no_new_folder_possible_stops_selftest(self):
        def prepare():
            self.old_current("0.1.3")
            (self.kh / "kits").write_text("a file", encoding="ascii")
        out = self.both("selftest", prepare=prepare)
        for kind in ("cmd", "ps1"):
            rc, lines, raw, ran = out[kind]
            self.assertEqual((rc != 0, ran), (True, []), raw)
            self.assertIn("Step 3 of 6: getting the assistant kit ... not done", lines)
            self.assertTrue(any(ln.startswith("   Windows said: ") for ln in lines), lines)
            self.assertIn("Stopped at step 3: could not get kit %s, and the kit already on this PC (0.1.3) is never "
                          "used for selftest, so nothing was run." % NEED, lines)

    def test_a_served_kit_older_than_the_launcher_needs(self):
        older = make_zip(self.base / "older.zip", "0.1.3")
        out = self.both("adopt", "--check", str(self.folder), source=older)
        for kind in ("cmd", "ps1"):
            rc, lines, raw, ran = out[kind]
            self.assertEqual((rc != 0, ran), (True, []), raw)
            self.assertIn("Stopped at step 3: the kit that was put in place is 0.1.3, and adopt needs kit %s or newer, "
                          "so nothing was run." % NEED, lines)
        out = self.both(source=older)                                          # a normal start runs it
        self.assertEqual(out["cmd"][3], ["0.1.3 start"])

    def test_a_kit_from_before_0_1_4_cannot_place_itself(self):
        old_zip = make_zip(self.base / "old-style.zip", "0.1.3", placer=False)
        out = self.both("selftest", source=old_zip, prepare=self.old_current)
        for kind in ("cmd", "ps1"):
            rc, lines, raw, ran = out[kind]
            self.assertEqual((rc != 0, ran), (True, []), raw)
            self.assertIn("   The kit that was fetched could not put itself in place (a kit older than 0.1.4 cannot).",
                          lines)

    def test_a_pointer_to_a_missing_folder_stops_a_normal_start(self):
        def prepare():
            self.old_current()
            (self.kh / "kit.txt").write_bytes(b"0.1.9\r\n")
        out = self.both(source=self.base / "not-there.zip", prepare=prepare)
        for kind in ("cmd", "ps1"):
            rc, lines, raw, ran = out[kind]
            self.assertEqual((rc != 0, ran), (True, []), raw)
            self.assertIn("Stopped at step 3: kit.txt names a kit folder that is not there.", lines)

    def test_the_launchers_name_this_kits_version_as_the_oldest_they_run(self):
        for name, line in make_kit.kit_need_lines(NEED).items():
            self.assertIn(line, (LAUNCHER / name).read_bytes(), name)


if __name__ == "__main__":
    unittest.main()
