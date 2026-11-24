"""Step 1 of both launchers: which claude they pick. Runs only the lookup (cut out of each launcher) against empty fake
files in a temp profile, under a PATH of System32 plus one fake folder. Nothing is installed, downloaded or started."""
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

from tests import _helpers

LAUNCHER = _helpers.KIT / "launcher"
ROOT = os.environ.get("SystemRoot", r"C:\Windows")


def cmd_lookup():
    text = (LAUNCHER / "Assistant.cmd").read_text(encoding="utf-8")
    return re.search(r"^:findclaude\r?\n.*?(?=^:findpy)", text, re.S | re.M).group(0)


def ps1_lookup():
    text = (LAUNCHER / "Assistant.ps1").read_text(encoding="utf-8")
    return re.search(r"^function Find-Claude \{\r?\n.*?^\}\r?\n", text, re.S | re.M).group(0)


@unittest.skipUnless(sys.platform == "win32", "Windows launchers")
class ClaudeLookup(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = _helpers.real(self.td) / "with space"
        self.home = self.base / "home"
        self.appdata = self.home / "AppData" / "Roaming"
        self.onpath = self.base / "elsewhere"
        for d in (self.home, self.appdata, self.onpath):
            d.mkdir(parents=True)

    def tearDown(self):
        self.td.cleanup()

    def touch(self, p):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
        return str(p)

    def env(self):
        keep = {k: v for k, v in os.environ.items() if k.upper() in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
                                                                       "TEMP", "TMP", "PSMODULEPATH")}
        keep.update(USERPROFILE=str(self.home), APPDATA=str(self.appdata), HOME=str(self.home),
                    LOCALAPPDATA=str(self.home / "AppData" / "Local"),   # never the person's own (0.1.6)
                    PATH=os.pathsep.join([os.path.join(ROOT, "System32"), ROOT,
                                          os.path.join(ROOT, r"System32\WindowsPowerShell\v1.0"), str(self.onpath)]))
        return keep

    def run_cmd(self):
        bat = self.base / "probe.cmd"
        bat.write_text("@echo off\r\nsetlocal\r\nset \"NATIVE=%USERPROFILE%\\.local\\bin\\claude.exe\"\r\n"
                       "set \"CLAUDE=%NATIVE%\"\r\ncall :findclaude\r\necho(%CFROM%^|%CLAUDE%\r\nexit /b 0\r\n"
                       + cmd_lookup().replace("\r\n", "\n").replace("\n", "\r\n"), encoding="utf-8")
        r = subprocess.run([os.path.join(ROOT, "System32", "cmd.exe"), "/d", "/c", str(bat)], capture_output=True,
                           text=True, timeout=60, env=self.env(), stdin=subprocess.DEVNULL)
        return r.stdout.strip()

    def run_ps1(self):
        ps = self.base / "probe.ps1"
        ps.write_text("$Native = Join-Path $env:USERPROFILE '.local\\bin\\claude.exe'\n" + ps1_lookup()
                      + "$r = Find-Claude\nif ($r) { Write-Output \"$($r[1])|$($r[0])\" } else { Write-Output '|' }\n",
                      encoding="utf-8")
        r = subprocess.run([os.path.join(ROOT, r"System32\WindowsPowerShell\v1.0\powershell.exe"), "-NoProfile",
                            "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(ps)], capture_output=True,
                           text=True, timeout=120, env=self.env(), stdin=subprocess.DEVNULL)
        return r.stdout.strip()

    def both(self):
        return self.run_cmd(), self.run_ps1()

    def assertFound(self, out, label, path):
        """'label|path', the path compared after normalizing both sides: `where` and Get-Command give the long
        spelling, an existence check gives the spelling it was handed."""
        got_label, _, got_path = out.partition("|")
        self.assertEqual(got_label, label, out)
        self.assertEqual(_helpers.norm(got_path), _helpers.norm(path), out)

    def test_native_spot_wins(self):
        native = self.touch(self.home / ".local" / "bin" / "claude.exe")
        self.touch(self.appdata / "npm" / "claude.cmd")
        self.touch(self.onpath / "claude.exe")
        for out in self.both():
            self.assertFound(out, "native install", native)

    def test_npm_shim_before_path(self):
        shim = self.touch(self.appdata / "npm" / "claude.cmd")
        self.touch(self.onpath / "claude.exe")
        for out in self.both():
            self.assertFound(out, "npm install", shim)

    def test_path_lookup_last(self):
        exe = self.touch(self.onpath / "claude.exe")
        self.touch(self.onpath / "claude")              # npm's extensionless shell shim is never picked
        for out in self.both():
            self.assertFound(out, "on PATH", exe)

    def test_none_found(self):
        cmd_out, ps_out = self.both()
        self.assertTrue(cmd_out.startswith("|") and cmd_out.endswith(r"\.local\bin\claude.exe"), cmd_out)
        self.assertEqual(ps_out, "|")


if __name__ == "__main__":
    unittest.main()
