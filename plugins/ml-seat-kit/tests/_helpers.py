"""Test helpers: put the kit's scripts on sys.path, make throwaway seat folders, pin a clock zone, build a fake
profile's environment, and probe what a PowerShell start leaves in a profile (0.1.6)."""
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

KIT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = KIT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


# ---------------------------------------------------------------- build-only tests (0.1.7)
# Tests of the kit's own runner and watcher that use real waits and real hung or leftover processes run only in the
# release build (tools/make_kit.py sets RELEASE_BUILD=1 for its suite run, and refuses a build in which they did not
# all run). A colleague's self test is there to test the colleague's PC; these are not loaded there at all, so the
# Tests line counts what really ran and they are named once, as "not run here", never as skipped.
RELEASE_BUILD = "SEAT_KIT_RELEASE_BUILD"
BUILD_ONLY_ATTR = "selftest_build_only"     # scripts/selftest_runner.py reads the same name
SELFTEST_NOT_RUN_HERE = []                  # ids load_tests left out in this process (read by the runner)


def build_only(obj):
    """Decorator for a test method or a TestCase class: run it only in the release build."""
    setattr(obj, BUILD_ONLY_ATTR, True)
    return obj


def allow_seconds(n):
    """Decorator for a test method that starts programs: its limit in the self test (selftest_seconds), at least the
    sum of the subprocess time limits it uses. The release build measures that sum and refuses a build in which it is
    over the limit (scripts/selftest_runner.py WAITS lines, tools/make_kit.py require_waits)."""
    def mark(f):
        f.selftest_seconds = n
        return f
    return mark


def release_build(environ=None):
    return (os.environ if environ is None else environ).get(RELEASE_BUILD) == "1"


def load_tests(loader, standard_tests, pattern):
    """unittest's load_tests hook for a test module with build-only tests (`load_tests = _helpers.load_tests`):
    outside a release build those tests are not loaded, here or under plain `python -m unittest`, and their ids are
    kept in SELFTEST_NOT_RUN_HERE."""
    if release_build():
        return standard_tests
    keep = unittest.TestSuite()
    for t in _flat(standard_tests):
        method = getattr(type(t), getattr(t, "_testMethodName", "") or "", None)
        if any(getattr(owner, BUILD_ONLY_ATTR, False) is True for owner in (method, type(t))):
            if t.id() not in SELFTEST_NOT_RUN_HERE:
                SELFTEST_NOT_RUN_HERE.append(t.id())
        else:
            keep.addTest(t)
    return keep


def _flat(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from _flat(t)
        else:
            yield t


def real(td):
    """The long spelling of a TemporaryDirectory. TEMP can be in 8.3 short form (a long user name with a dot), while
    the kit prints long paths; every expected path in a test is built from this one spelling."""
    return pathlib.Path(os.path.realpath(td.name if hasattr(td, "name") else td))


def norm(path):
    """For comparing a path a launcher printed with one a test built: the file system's long form, normal case."""
    return os.path.normcase(os.path.realpath(str(path).strip()))


def short_form(path):
    """The 8.3 short spelling of an existing path, or None when this volume makes no short names (then a test that
    needs one skips and says so)."""
    if os.name != "nt":
        return None
    import ctypes
    buf = ctypes.create_unicode_buffer(32768)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 32768)
    if not 0 < n < 32768 or os.path.normcase(buf.value) == os.path.normcase(str(path)):
        return None
    return buf.value


NO_SHORT_NAMES = "this PC's temp volume makes no 8.3 short names, so there is no short spelling to test with"


# ---------------------------------------------------------------- a fake profile, and what PowerShell itself leaves

ROOT = os.environ.get("SystemRoot", r"C:\Windows")
POWERSHELL = os.path.join(ROOT, r"System32\WindowsPowerShell\v1.0\powershell.exe")
NOISE_MARKER = "SEAT-KIT-TEST-NOISE:"   # the one line the tests print for `selftest` (seat_setup.parse_noise)
# Names the kit itself uses under AppData (tests/test_privacy.py AppDataNames pins the launchers' list): never noise,
# whatever the probe saw. seat-kit is the kit's own folder; Programs (Python lookup) and npm (claude lookup) are only
# read, and a test makes npm itself. Microsoft is NOT here: the kit only looks for winget.exe in
# Microsoft\WindowsApps, and it is the folder PowerShell writes its own caches to.
KIT_APPDATA = ("seat-kit", "Programs", "npm")
# Names of things the kit makes. Inside an ignored folder these still count, so a kit write there is still seen.
KIT_NAMES = ("seat-kit", "kits", "kit.txt", "market.txt", "python.txt", "send_gate.py", "selftest-last.txt",
             "last-seat.txt", "adopted.txt", "install-claude.cmd", "assistant.cmd", "assistant.ps1", ".seat",
             "seat.json", "assistant")


def profile_env(home, temp=None, **extra):
    """The environment for a child started under a fake profile: every profile variable points into `home`, never at
    the person's own; only what Windows, cmd and PowerShell need is kept, and PATH is Windows' own folders."""
    home = pathlib.Path(home)
    env = {k: v for k, v in os.environ.items() if k.upper() in ("COMSPEC", "PATHEXT", "PSMODULEPATH")}
    temp = str(temp or os.environ.get("TEMP") or tempfile.gettempdir())
    env.update(SystemRoot=ROOT, windir=ROOT, USERPROFILE=str(home), HOME=str(home),
               LOCALAPPDATA=str(home / "AppData" / "Local"), APPDATA=str(home / "AppData" / "Roaming"),
               TEMP=temp, TMP=temp,
               PATH=os.pathsep.join([os.path.join(ROOT, "System32"), os.path.dirname(POWERSHELL)]))
    env.update(extra)
    return env


PROFILE_VARS = ("USERPROFILE", "LOCALAPPDATA", "APPDATA", "HOME")


def fake_profile(home):
    """Point this process's profile variables at `home` (so what a test starts inherits the fake, never the real one);
    returns the old values for restore_profile."""
    home = pathlib.Path(home)
    old = {k: os.environ.get(k) for k in PROFILE_VARS}
    os.environ.update(USERPROFILE=str(home), HOME=str(home), LOCALAPPDATA=str(home / "AppData" / "Local"),
                      APPDATA=str(home / "AppData" / "Roaming"))
    return old


def restore_profile(old):
    """Put back what fake_profile changed. Safe to call twice."""
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def noise_list(found):
    """'Local/Name' and 'Roaming/Name' entries the probe saw -> sorted 'AppData/<where>/<Name>' paths, top level only,
    never a name the kit itself uses there."""
    out = set()
    for item in found:
        parts = str(item).replace("\\", "/").strip("/").split("/")
        if len(parts) != 2 or parts[0] not in ("Local", "Roaming") or not parts[1] or parts[1] in (".", ".."):
            continue
        if parts[1].lower() in {n.lower() for n in KIT_APPDATA}:
            continue
        out.add("AppData/%s/%s" % tuple(parts))
    return sorted(out)


PROBE_TIMEOUT = 60     # seconds, each start
PROBE_SETTLE = 1.0     # seconds to wait after the last start before listing again (a scanner may write just after)


def shortcut_script():
    """(seat_remove.SHORTCUT_PS, the variable it reads the desktop folder from), taken from the kit's own module when
    asked, so the probe runs exactly the script seat_remove.read_shortcuts runs. None when it cannot be imported."""
    try:
        import seat_remove
        ps = seat_remove.SHORTCUT_PS
        var = re.search(r"\$env:(\w+)", ps).group(1)
    except Exception:  # noqa: BLE001  (no kit module here: the probe keeps its bare start only)
        return None
    return ps, var


def probe_starts(scratch):
    """The PowerShell starts the probe makes, as (argv, extra environment) in order. 1: a bare start. 2: the kit's own
    READ-ONLY desktop script as seat_remove.read_shortcuts runs it (-Command SHORTCUT_PS), with its desktop variable on
    an empty folder in the scratch folder, outside the fake home. 3: the .ps1 launcher's shape (-ExecutionPolicy
    Bypass -File) on a script that only exits, written in the scratch folder, outside the fake home. Only start 1
    when the kit's module cannot be imported."""
    starts = [([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", "exit"], {})]
    found = shortcut_script()
    if found is None:
        return starts
    ps, var = found
    desk = pathlib.Path(scratch) / "empty desktop"
    desk.mkdir()
    script = pathlib.Path(scratch) / "probe.ps1"
    script.write_bytes(b"exit 0\r\n")
    starts.append(([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", ps], {var: str(desk)}))
    starts.append(([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                   {}))
    return starts


def probe_noise(run=None, sleep=None, look=None):
    """Start PowerShell the ways the kit and its tests do (probe_starts) against a scratch fake profile and return the
    top-level folders that appeared under AppData\\Local and AppData\\Roaming (noise_list form): listed after the last
    start and again PROBE_SETTLE seconds later, the union of both. Exit codes are not checked (a locked-down PC may
    refuse a script; the list is still taken), and a start that cannot run is passed over. [] when PowerShell is not
    here. The probe itself writes nothing in the fake home but its two empty AppData folders; `look`, when given, is
    called with the fake home just before the scratch folder is removed. A scratch folder that cannot be removed (a
    file still held by a scanner) is left, and the list stands."""
    run = run or subprocess.run
    sleep = sleep or time.sleep
    if os.name != "nt" or not os.path.isfile(POWERSHELL):
        return []
    scratch = tempfile.mkdtemp(prefix="seat-kit-probe-")
    try:
        td = real(scratch)
        home, temp = td / "probe home", td / "temp"
        for d in (home / "AppData" / "Local", home / "AppData" / "Roaming", temp):
            d.mkdir(parents=True)
        found = set()

        def take():
            for where in ("Local", "Roaming"):
                try:
                    entries = list((home / "AppData" / where).iterdir())
                except OSError:
                    continue
                found.update("%s/%s" % (where, p.name) for p in entries if p.is_dir())
        for argv, extra in probe_starts(td):   # each in its own process
            try:
                run(argv, capture_output=True, timeout=PROBE_TIMEOUT, env=profile_env(home, temp, **extra),
                    stdin=subprocess.DEVNULL, cwd=str(td))
            except (OSError, subprocess.SubprocessError):
                continue
        take()
        sleep(PROBE_SETTLE)
        take()
        if look is not None:
            look(home)
        result = noise_list(sorted(found))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return result


_NOISE = []


def powershell_noise():
    """probe_noise, run once per test process. The first call prints one marker line on the real stdout, which
    `selftest` reads out of the suite's output to say what the tree comparisons ignored."""
    if not _NOISE:
        try:
            found = probe_noise()
        except Exception:  # noqa: BLE001  (a probe that breaks is a probe that found nothing)
            found = []
        _NOISE.append(found)
        out = sys.__stdout__
        if out is not None:
            try:
                out.write("\n%s %s\n" % (NOISE_MARKER, json.dumps(found)))
                out.flush()
            except (OSError, ValueError):
                pass
    return list(_NOISE[0])


def kit_named(rel):
    """True when any part of a relative path is a name the kit makes (a .lnk counts too)."""
    names = {n.lower() for n in KIT_NAMES}
    return any(p.lower() in names or p.lower().endswith(".lnk") for p in rel.split("/"))


def without_noise(tree, noise):
    """`tree` ({posix relative path: hash or None}, taken at a profile's top) minus the named noise folders and what is
    under them; an entry under one that carries a kit name is kept, so a kit write there still shows."""
    def ignored(k):
        return any((k == n or k.startswith(n + "/")) and not kit_named(k[len(n):]) for n in noise)
    return {k: v for k, v in tree.items() if not ignored(k)}


def ignored_note(noise):
    """For a failure message: what the comparison left out."""
    return "(tree compared without %s: folders other programs make whenever PowerShell starts)" % (
        ", ".join(noise) if noise else "nothing")


def make_seat(base=None, name="Seat"):
    """A temp folder holding a seat marker; returns (TemporaryDirectory, seat Path)."""
    td = tempfile.TemporaryDirectory()
    seat = real(td) / name
    (seat / ".seat").mkdir(parents=True)
    (seat / ".seat" / "seat.json").write_text(json.dumps({"seat_name": name}), encoding="utf-8")
    (seat / "state").mkdir()
    return td, seat


class EasternZone:
    """US Eastern by the 2007 rule, computed by hand (no tzdata needed), so the wake tests give the same answers on
    any laptop clock. The kit itself uses the laptop's own zone."""

    @staticmethod
    def offset(utc):
        y = utc.year
        mar1 = datetime.datetime(y, 3, 1, 7, tzinfo=datetime.timezone.utc)
        start = mar1 + datetime.timedelta(days=(6 - mar1.weekday()) % 7 + 7)
        nov1 = datetime.datetime(y, 11, 1, 6, tzinfo=datetime.timezone.utc)
        end = nov1 + datetime.timedelta(days=(6 - nov1.weekday()) % 7)
        return -4 if start <= utc < end else -5

    def to_dt(self, ts):
        u = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
        off = self.offset(u)
        return u.astimezone(datetime.timezone(datetime.timedelta(hours=off), "EDT" if off == -4 else "EST"))

    def to_ts(self, day, h, m):
        for off in (-4, -5):
            u = datetime.datetime(day.year, day.month, day.day, h, m, tzinfo=datetime.timezone.utc) - datetime.timedelta(hours=off)
            if self.offset(u) == off:
                return u.timestamp()
        return (datetime.datetime(day.year, day.month, day.day, h, m, tzinfo=datetime.timezone.utc)
                + datetime.timedelta(hours=5)).timestamp()
