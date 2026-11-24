r"""The kit's own test runner (0.1.7): `unittest discover -v`, plus one line before and one line after every test, so
the self test can show progress, record how long each test took, and stop a single stuck test by name.

    python -I selftest_runner.py -s <tests folder> -t <top folder>

It discovers the same tests in the same order as `python -m unittest discover -v -s <tests> -t <top>` and prints
exactly what that prints (the verbose line per test, the FAIL and ERROR blocks, "Ran N tests in X s", OK or FAILED),
with these lines added on the same stream (stderr), each flushed as it is written:

    SEAT-KIT-TEST-JOB armed                          (or "not armed (why)"): see _own_job below
    SEAT-KIT-TEST-FOUND 224                          discovery and imports are done
    SEAT-KIT-TEST-SETUP 1/224 tests.test_a.First     the first class's (and module's) set-up runs next
    SEAT-KIT-TEST-SETUP 37/224 tests.test_x.B after tests.test_x.A
                                                     the tear-down of class A (and of its module, when the module
                                                     changes) runs next, then the set-up of class B, before test 37
    SEAT-KIT-TEST-START 37/224 tests.test_x.Klass.test_y
    SEAT-KIT-TEST-DONE 37/224 ok 2.41s tests.test_x.Klass.test_y
    SEAT-KIT-TEST-TEARDOWN 224/224 tests.test_z.Last the last class's (and module's) tear-down runs next
    SEAT-KIT-TEST-END                                every test has run; unittest's own summary follows

A START line carries " limit=900" after the numbers when the test asks for more time than the usual per-test limit:
an attribute `selftest_seconds = 900` on the test method, or on its class. A SETUP or TEARDOWN line carries the
limit its CLASS asks for (the class attribute only, as a class set-up or tear-down is not one test), and after
"after" the limit the previous class asks for, so the reader can apply the larger of the two: unittest runs A's
tear-down and B's set-up back to back, with no line between them. seat_setup.run_suite reads these lines as they
come and owns every limit. Outcomes on a DONE line: ok, FAIL, ERROR, skipped, expected failure, unexpected success
(a failing or erroring sub-case makes the test FAIL or ERROR, as unittest counts it).

A mark starts on a line of its own after anything written through sys.stderr: sys.stderr is wrapped so the runner
knows whether the last thing written through it ended a line, and when not (a test that wrote "partial" with no
newline), a newline is written before the mark. Output that bypasses sys.stderr (sys.__stderr__, a write to file
descriptor 2, a child process sharing the stream) is not seen, and a mark can then follow it on the same line.

Build-only tests (0.1.7): a test method or class with `selftest_build_only = True` (tests/_helpers.build_only) tests
the kit's own runner and watcher with real waits and real hung or leftover processes. It runs only when
SEAT_KIT_RELEASE_BUILD=1 (tools/make_kit.py sets it for its own suite run); anywhere else it is not loaded at all
(tests/_helpers.load_tests leaves it out at discovery, and this runner leaves out any marked test still found), so
"Ran N" counts what really ran and nothing is reported as skipped. The runner names each one:

    SEAT-KIT-TEST-NOTRUN tests.test_x.K.test_y       left out here (before FOUND), one line each
    SEAT-KIT-TEST-BUILDONLY tests.test_x.K.test_y    in a release build: this build-only test will run

In a release build the runner also adds up, per test, the subprocess time limits the test actually used
(Popen.communicate and Popen.wait with a timeout, counted once per call; a wait inside communicate is part of that
call), and says so after each test's DONE line, and after a class set-up or tear-down that used any:

    SEAT-KIT-TEST-WAITS 360 3 0                      seconds in all, calls with a time limit, calls without one

so tools/make_kit.py can refuse a test whose limit is below the sum of its own programs' time limits.

SEAT_KIT_TEST_NO_JOB=1 in the environment leaves the job object out ("not armed (switched off ...)"), so the kit's
own tests can run the path a PC without a job object takes. It is for the kit's tests only.

Exit code as `python -m unittest`: 0 when the run was successful, 1 when not, 5 when no test ran (Python 3.12+).
Python 3.9 or newer, standard library only.
"""
import os
import sys
import time
import unittest

PREFIX = "SEAT-KIT-TEST-"
LIMIT_ATTR = "selftest_seconds"
NO_JOB = "SEAT_KIT_TEST_NO_JOB"
RELEASE_BUILD = "SEAT_KIT_RELEASE_BUILD"     # "1": run the build-only tests too (tools/make_kit.py)
BUILD_ONLY_ATTR = "selftest_build_only"
NOT_RUN_ATTR = "SELFTEST_NOT_RUN_HERE"       # a module's list of the test ids its load_tests left out
OUTCOMES = ("ok", "FAIL", "ERROR", "skipped", "expected failure", "unexpected success")


def _own_job():
    """Put this process into a new Windows job object that ends every process in it when its last handle closes.
    Only this process holds that handle (it is not inheritable), so when run_suite stops a run by ending this
    process, Windows also ends every process the tests started and left running (cmd, PowerShell, a child Python),
    and nothing else. A program that asks to break away from a job may still do so, as without the job.
    Returns 'armed' or 'not armed (why)'. Never raises.
    What it does NOT do: a process that breaks away from the job (it is allowed to), or one started while the job
    is not armed, is not ended; it keeps running, and may hold the runner's output pipes after the runner ends
    (seat_setup.watch_suite then stops waiting for the pipes a few seconds after the runner has ended)."""
    if os.environ.get(NO_JOB) == "1":
        return "not armed (switched off by %s=1)" % NO_JOB
    if os.name != "nt":
        return "not armed (not Windows)"
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        k32.GetCurrentProcess.restype = wintypes.HANDLE

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount",
                                                       "OtherOperationCount", "ReadTransferCount",
                                                       "WriteTransferCount", "OtherTransferCount")]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        job = k32.CreateJobObjectW(None, None)
        if not job:
            return "not armed (CreateJobObject error %d)" % ctypes.get_last_error()
        info = Extended()
        # 0x2000 KILL_ON_JOB_CLOSE, 0x800 BREAKAWAY_OK; 9 = JobObjectExtendedLimitInformation
        info.BasicLimitInformation.LimitFlags = 0x2000 | 0x800
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return "not armed (SetInformationJobObject error %d)" % ctypes.get_last_error()
        if not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
            return "not armed (AssignProcessToJobObject error %d)" % ctypes.get_last_error()
        _JOB.append(job)    # held, never closed, for the life of this process
        return "armed"
    except Exception as e:  # noqa: BLE001  (no job is today's behavior, never a reason not to run the tests)
        return "not armed (%s)" % type(e).__name__


_JOB = []


def flat(suite):
    """The tests of a suite in the order unittest runs them."""
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            for x in flat(t):
                yield x
        else:
            yield t


def _seconds(owner):
    v = getattr(owner, LIMIT_ATTR, None)
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
        return int(v)
    return None


def limit_of(test):
    """The seconds a test asks for (selftest_seconds on its method, else on its class), or None."""
    method = getattr(type(test), getattr(test, "_testMethodName", "") or "", None)
    for owner in (method, type(test)):
        v = _seconds(owner)
        if v:
            return v
    return None


def class_limit_of(test):
    """The seconds a test's CLASS asks for (its set-up and tear-down), or None."""
    return _seconds(type(test))


def _limit_text(limit):
    return " limit=%d" % limit if limit else ""


def is_build_only(test):
    """True when the test's method or class carries selftest_build_only = True."""
    method = getattr(type(test), getattr(test, "_testMethodName", "") or "", None)
    return any(getattr(owner, BUILD_ONLY_ATTR, False) is True for owner in (method, type(test)))


def not_run_by_hooks():
    """The test ids the test modules' load_tests hooks left out (each module's SELFTEST_NOT_RUN_HERE list), in order,
    once each."""
    out = []
    for mod in list(sys.modules.values()):
        try:
            ids = vars(mod).get(NOT_RUN_ATTR)
        except TypeError:
            continue
        if isinstance(ids, list):
            out += [i for i in ids if isinstance(i, str) and i not in out]
    return out


class _Waits:
    """In a release build: the subprocess time limits used since the last take(), counted once per call."""

    def __init__(self):
        self.seconds, self.timed, self.untimed = 0.0, 0, 0

    def count(self, timeout):
        if timeout is None:
            self.untimed += 1
        else:
            self.seconds += float(timeout)
            self.timed += 1

    def take(self):
        got = (self.seconds, self.timed, self.untimed)
        self.seconds, self.timed, self.untimed = 0.0, 0, 0
        return got

    def install(self):
        import subprocess
        real_communicate, real_wait = subprocess.Popen.communicate, subprocess.Popen.wait
        me = self

        def communicate(proc, input=None, timeout=None):
            if getattr(proc, "_seat_kit_in_call", False):
                return real_communicate(proc, input, timeout)
            proc._seat_kit_in_call = True
            try:
                me.count(timeout)
                return real_communicate(proc, input, timeout)
            finally:
                proc._seat_kit_in_call = False

        def wait(proc, timeout=None):
            # a wait with no time limit is what `with Popen(...)` and subprocess.run do after the program has
            # ended: not counted; a wait inside communicate is part of that call
            if timeout is not None and not getattr(proc, "_seat_kit_in_call", False):
                me.count(timeout)
            return real_wait(proc, timeout)
        subprocess.Popen.communicate, subprocess.Popen.wait = communicate, wait
        return self


class _Tracked:
    """stderr, remembering whether the last thing written to it ended a line."""

    def __init__(self, raw):
        self._raw = raw
        self.fresh = True

    def write(self, text):
        n = self._raw.write(text)
        if text:
            self.fresh = text.endswith("\n")
        return n

    def __getattr__(self, name):
        return getattr(self._raw, name)


class TimedResult(unittest.TextTestResult):
    """unittest's verbose result, with the SEAT-KIT-TEST lines around every test."""

    plan = []   # [(id, class, limit, class limit)] in run order, set by main before the run
    waits = None   # a _Waits in a release build

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.where = {}
        for n, (tid, _, _, _) in enumerate(self.plan):
            self.where.setdefault(tid, n)
        self.now = None
        self.started = None
        self.marks = set()

    def mark(self, text):
        if getattr(self.stream, "fresh", True) is False:    # a test left a line open: never merge a mark into it
            self.stream.write("\n")
        self.stream.write("%s%s\n" % (PREFIX, text))
        self.stream.flush()

    def setup_line(self, n, after=None):
        """Announce what runs before plan[n]: the class set-up, and when `after` (the index of the previous class's
        last test) is given, that class's tear-down before it."""
        if 0 <= n < len(self.plan):
            _, klass, _, limit = self.plan[n]
            tail = ""
            if after is not None and 0 <= after < len(self.plan):
                _, prev, _, prev_limit = self.plan[after]
                tail = " after%s %s" % (_limit_text(prev_limit), prev)
            self.mark("SETUP %d/%d%s %s%s" % (n + 1, len(self.plan), _limit_text(limit), klass, tail))

    def startTestRun(self):
        super().startTestRun()
        self.setup_line(0)

    def waits_line(self, always=False):
        """In a release build: the subprocess time limits used since the last such line (a test, or the set-up and
        tear-down before it)."""
        if self.waits is None:
            return
        seconds, timed, untimed = self.waits.take()
        if always or timed or untimed:
            self.mark("WAITS %d %d %d" % (round(seconds), timed, untimed))

    def stopTestRun(self):
        self.waits_line()
        self.mark("END")
        super().stopTestRun()

    def startTest(self, test):
        self.waits_line()
        n = self.where.get(test.id())
        self.now = (test, n)
        self.marks = set()
        limit = self.plan[n][2] if n is not None else limit_of(test)
        self.mark("START %s/%d%s %s" % (n + 1 if n is not None else "?", len(self.plan), _limit_text(limit),
                                         test.id()))
        self.started = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        super().stopTest(test)
        took = time.perf_counter() - (self.started or time.perf_counter())
        n = self.now[1] if self.now and self.now[0] is test else self.where.get(test.id())
        outcome = "ok"
        for o in ("ERROR", "FAIL", "unexpected success", "expected failure", "skipped"):
            if o in self.marks:
                outcome = o
                break
        self.mark("DONE %s/%d %s %.2fs %s" % (n + 1 if n is not None else "?", len(self.plan), outcome, took,
                                             test.id()))
        self.waits_line(always=True)
        self.now = None
        if n is not None and n + 1 < len(self.plan) and self.plan[n + 1][1] != self.plan[n][1]:
            self.setup_line(n + 1, after=n)
        elif n is not None and n + 1 == len(self.plan):
            _, klass, _, limit = self.plan[n]
            self.mark("TEARDOWN %d/%d%s %s" % (n + 1, len(self.plan), _limit_text(limit), klass))

    def _mine(self, test):
        return self.now is not None and (test is self.now[0] or getattr(test, "test_case", None) is self.now[0])

    def _fixture(self, test, what):
        """A class or module set-up that failed or skipped (none of its tests will start): announce the next class's
        set-up, so its time is not counted to this one. Without "after": unittest never runs the tear-down of a
        class (or module) whose set-up failed or skipped, so no tear-down is named for it."""
        desc = str(test)
        for kind in ("setUpClass (", "setUpModule ("):
            if desc.startswith(kind):
                owner = desc[len(kind):].rstrip(")")
                break
        else:
            return
        if kind == "setUpClass (":
            last = [n for n, (_, k, _, _) in enumerate(self.plan) if k == owner]
        else:
            last = [n for n, (_, k, _, _) in enumerate(self.plan) if k.startswith(owner + ".")]
        if last:
            self.setup_line(last[-1] + 1)

    def addError(self, test, err):
        super().addError(test, err)
        if self._mine(test):
            self.marks.add("ERROR")
        else:
            self._fixture(test, "ERROR")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        if self._mine(test):
            self.marks.add("FAIL")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None and self._mine(test):
            self.marks.add("FAIL" if issubclass(err[0], test.failureException) else "ERROR")

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        if self._mine(test):
            self.marks.add("skipped")
        else:
            self._fixture(test, "skipped")

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        if self._mine(test):
            self.marks.add("expected failure")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        if self._mine(test):
            self.marks.add("unexpected success")


def _args(argv):
    start, top = ".", None
    i = 0
    while i < len(argv):
        if argv[i] in ("-s", "--start-directory") and i + 1 < len(argv):
            start, i = argv[i + 1], i + 2
        elif argv[i] in ("-t", "--top-level-directory") and i + 1 < len(argv):
            top, i = argv[i + 1], i + 2
        else:
            raise SystemExit("selftest_runner.py: not understood: %s (use -s <tests> -t <top>)" % argv[i])
    return start, top


def main(argv=None):
    start, top = _args(list(sys.argv[1:] if argv is None else argv))
    here = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))
    if sys.path and os.path.normcase(os.path.abspath(sys.path[0] or os.curdir)) == here:
        # A guard only: -m unittest never has this script's folder on sys.path. Under -I, Python 3.11.4 and 3.12.3
        # were seen NOT to add it (checked on the build PC); 3.9 and 3.10 were not verified, so it is removed if there.
        del sys.path[0]
    sys.stderr = stream = _Tracked(sys.stderr)   # a test's print(..., file=sys.stderr) goes through it too
    stream.write("%sJOB %s\n" % (PREFIX, _own_job()))
    stream.flush()
    suite = unittest.TestLoader().discover(start, pattern="test*.py", top_level_dir=top)
    tests = list(flat(suite))
    if os.environ.get(RELEASE_BUILD) == "1":
        for t in tests:
            if is_build_only(t):
                stream.write("%sBUILDONLY %s\n" % (PREFIX, t.id()))
        TimedResult.waits = _Waits().install()
    else:
        not_run = not_run_by_hooks()
        keep = [t for t in tests if not is_build_only(t)]
        if len(keep) != len(tests):     # a marked test whose module has no load_tests hook: left out here
            not_run += [t.id() for t in tests if is_build_only(t) and t.id() not in not_run]
            tests, suite = keep, unittest.TestSuite(keep)
        for tid in not_run:
            stream.write("%sNOTRUN %s\n" % (PREFIX, tid))
    TimedResult.plan = [(t.id(), "%s.%s" % (type(t).__module__, type(t).__qualname__), limit_of(t),
                         class_limit_of(t)) for t in tests]
    stream.write("%sFOUND %d\n" % (PREFIX, len(TimedResult.plan)))
    stream.flush()
    # as `python -m unittest`: verbosity 2, and warnings shown once each unless -W was given
    runner = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=TimedResult,
                                     warnings=None if sys.warnoptions else "default")
    result = runner.run(suite)
    if sys.version_info >= (3, 12) and result.testsRun == 0 and not result.skipped:
        return 5
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
