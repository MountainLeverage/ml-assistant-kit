"""Privacy scan: no kit file may name a company, a person, an internal host, a path from one PC, or an address.

The kit must be able to live in a public repo, so this fails on any hit. The deny words are assembled from pieces
so this file passes its own scan. make_kit.py runs the same scan over every file it puts in the zip.

Two lists. The GENERIC one below (paths from one PC, addresses, an internal host shape) ships with the kit. The
PRIVATE one (the names of real people, seats and the company) must not be published, because a list of the names
to keep out is itself those names. It lives in tests/private_words.txt, one "label<TAB>regular expression" per
line, only on the machine that cuts releases (git ignores it). make_kit.py never puts that file in a zip, refuses
a release build when it is missing, and a public copy of the tests runs without it.
"""
import pathlib
import re
import tempfile
import unittest

KIT = pathlib.Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = {".py", ".md", ".json", ".cmd", ".ps1", ".txt", ""}
PRIVATE_WORDS = pathlib.Path(__file__).resolve().parent / "private_words.txt"

_W = [
    ("internal host", r"git" + r"lab\."),
    ("profile path", r"c:[\\/]+" + "users"),
    ("repo path", r"c:[\\/]+" + "repos"),
    ("database", "fire" + "store"),
    ("email address", r"[A-Za-z0-9._%+\-]+" + "@" + r"[A-Za-z0-9\-]+\.[A-Za-z0-9.\-]*[A-Za-z]"),
]


def private_words(path=PRIVATE_WORDS):
    """[(label, regular expression)] from the private list, or [] where the file is absent (a public copy)."""
    out = []
    try:
        lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for ln in lines:
        if not ln.strip() or ln.lstrip().startswith("#") or "\t" not in ln:
            continue
        label, rx = ln.split("\t", 1)
        out.append((label.strip(), rx.strip()))
    return out


DENY = [(label, re.compile(rx, re.I)) for label, rx in _W + private_words()]
ALLOWED_ADDRESSES = {"someone@example.invalid", "a@example.invalid"}   # obviously fake test addresses


def scan_text(text):
    """['<label>: <match> (line N)', ...] for every deny-list hit in text."""
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for label, rx in DENY:
            for m in rx.finditer(line):
                if label == "email address" and m.group(0).lower() in ALLOWED_ADDRESSES:
                    continue
                hits.append("%s: %r (line %d)" % (label, m.group(0), n))
    return hits


def kit_files():
    for p in sorted(KIT.rglob("*")):
        if (p.is_file() and "__pycache__" not in p.parts and p.suffix.lower() in TEXT_SUFFIXES
                and p != PRIVATE_WORDS):
            yield p


class Privacy(unittest.TestCase):
    def test_no_kit_file_names_anything_private(self):
        hits = []
        files = list(kit_files())
        self.assertGreater(len(files), 15)
        for p in files:
            for h in scan_text(p.read_text(encoding="utf-8", errors="replace")):
                hits.append("%s: %s" % (p.relative_to(KIT).as_posix(), h))
        self.assertEqual(hits, [], "\n".join(hits))

    def test_scan_catches_each_generic_kind(self):
        samples = ["git" + "lab.example", "C:\\" + "Users\\x", "C:/" + "repos/x", "fire" + "store",
                   "pat" + "@" + "example.com"]
        for s in samples:
            self.assertTrue(scan_text(s), s)
        self.assertEqual(scan_text("ml-seat-kit@seat-kit and brooklyn and danger"), [])

    def test_the_private_list_is_read_when_present_and_is_never_a_kit_file(self):
        with tempfile.TemporaryDirectory() as td:
            f = pathlib.Path(td) / "private_words.txt"
            f.write_text("# a comment\nseat name\t\\bjuni" + "per\\b\nno tab on this line\n", encoding="utf-8")
            self.assertEqual(private_words(f), [("seat name", "\\bjuni" + "per\\b")])
            self.assertEqual(private_words(pathlib.Path(td) / "absent.txt"), [])
        self.assertNotIn(PRIVATE_WORDS, list(kit_files()))
        if PRIVATE_WORDS.exists():                       # the private home: the listed words are live
            self.assertGreaterEqual(len(DENY), len(_W) + 5)


# 0.1.6: every place under AppData the kit's code names, and why. The tests' folder comparisons ignore what a bare
# PowerShell leaves under AppData (tests/_helpers.py powershell_noise), so the kit itself must never write under
# AppData anywhere but its own folder; this pins that it does not even name another place.
KIT_APPDATA = (
    ("LOCALAPPDATA", "seat-kit"),                               # the kit's own folder (KIT_HOME): the only write
    ("LOCALAPPDATA", "Programs\\Python\\Python3"),              # read: a python.org per-user Python (step 2)
    ("LOCALAPPDATA", "Microsoft\\WindowsApps\\winget.exe"),     # read: winget when it is not on PATH (step 2)
    ("APPDATA", "npm\\"),                                       # read: an npm install of Claude Code (step 1)
)
# What each known place may be, as the whole path reference (up to a quote, whitespace or the end of the line):
# the folder itself or something under it after a separator; Python3 only with the * the lookup uses; winget.exe
# exactly. A reference containing .. is never known.
_KNOWN_REF = {
    KIT_APPDATA[0]: re.compile(r"seat-kit(?:[\\/].*)?", re.I),
    KIT_APPDATA[1]: re.compile(r"Programs\\Python\\Python3\*(?:[\\/].*)?", re.I),
    KIT_APPDATA[2]: re.compile(r"Microsoft\\WindowsApps\\winget\.exe", re.I),
    KIT_APPDATA[3]: re.compile(r"npm\\.+", re.I),
}


def known_appdata(var, rest):
    """The KIT_APPDATA entry a reference is, or None. `rest` is what follows the variable (appdata_refs); the path
    reference is the part of it before the first quote or whitespace."""
    ref = re.split(r"[\"'\s]", rest, maxsplit=1)[0]
    if ".." in ref:
        return None
    for entry, rx in _KNOWN_REF.items():
        if entry[0] == var and rx.fullmatch(ref):
            return entry
    return None
BARE_APPDATA = ("if ($env:APPDATA) {",)                         # a test that the variable is set, naming no path
_APPDATA_REF = re.compile(r"(?:%|\$env:)(LOCALAPPDATA|APPDATA)%?", re.I)
_APPDATA_ANY = re.compile(r"(?:LOCAL)?APPDATA|ApplicationData|AppData[\\/]", re.I)


def appdata_refs(text):
    """[(variable, what follows it)] for each %VAR% or $env:VAR reference to (LOCAL)APPDATA in text; what follows has
    quotes, spaces and leading backslashes taken off ('' for a bare test). Plus the count of every other spelling."""
    refs, others = [], 0
    for line in text.splitlines():
        spans = []
        for m in _APPDATA_REF.finditer(line):
            rest = line[m.end():].replace("\\\\", "\\").lstrip(" '\"\\")
            refs.append((m.group(1).upper(), rest if not line.strip() in BARE_APPDATA else ""))
            spans.append((m.start(), m.end()))
        others += sum(1 for m in _APPDATA_ANY.finditer(line) if not any(a <= m.start() < b for a, b in spans))
    return refs, others


def kit_code():
    for p in kit_files():
        rel = p.relative_to(KIT).as_posix()
        if p.suffix.lower() in (".py", ".cmd", ".ps1", ".json") and not rel.startswith("tests/"):
            yield p, rel


# seat_setup.py names AppData folders in one block only: it reads the tests' marker line and prints which folders the
# comparisons ignored. That block writes nothing (pinned below), so it is left out of the scan.
NOISE_BLOCK = ("scripts/seat_setup.py", "# The one line the kit's tests print", "\nFIXTURES = (")
WRITES = (".write", "open(", "mkdir", "unlink", "rename", "rmtree", "subprocess", "os.remove", "shutil")


def without_noise_block(rel, text):
    name, start, end = NOISE_BLOCK
    if rel != name:
        return text, ""
    a = text.index(start)
    b = text.index(end, a)
    return text[:a] + text[b:], text[a:b]


class AppDataNames(unittest.TestCase):
    def test_the_kit_names_no_appdata_place_but_its_own_known_ones(self):
        seen, bad = set(), []
        for p, rel in kit_code():
            text, block = without_noise_block(rel, p.read_text(encoding="utf-8", errors="replace"))
            for w in WRITES:
                if w in block:
                    bad.append("%s: the block that prints the ignored folders has %r" % (rel, w))
            refs, others = appdata_refs(text)
            if others:
                bad.append("%s: %d other spelling(s) of an AppData place" % (rel, others))
            for var, rest in refs:
                hit = known_appdata(var, rest)
                if hit:
                    seen.add(hit)
                elif rest:
                    bad.append("%s: %%%s%%\\%s" % (rel, var, rest[:60]))
        self.assertEqual(bad, [], "\n".join(bad))
        self.assertEqual(seen, set(KIT_APPDATA))                        # each one is still really used

    def test_the_check_sees_a_new_place_and_another_spelling(self):
        self.assertEqual(appdata_refs('set "X=%LOCALAPPDATA%\\Other\\x"')[0], [("LOCALAPPDATA", 'Other\\x"')])
        self.assertEqual(appdata_refs("Join-Path $env:APPDATA 'Thing'")[0], [("APPDATA", "Thing'")])
        self.assertEqual(appdata_refs('os.environ["LOCALAPPDATA"]')[1], 1)
        self.assertEqual(appdata_refs("[Environment]::GetFolderPath('LocalApplicationData')")[1], 1)
        self.assertEqual(appdata_refs("p = home / 'AppData/Local/x'")[1], 1)
        self.assertEqual(appdata_refs("  if ($env:APPDATA) {"), ([("APPDATA", "")], 0))

    def test_the_matcher_takes_only_the_known_places_themselves(self):
        def known(line):
            (ref,) = appdata_refs(line)[0]
            return known_appdata(*ref)
        for line in ('set "W=%LOCALAPPDATA%\\Microsoft\\WindowsApps\\winget.exe.log"',   # only starts like winget.exe
                     'set "K=%LOCALAPPDATA%\\seat-kit-old\\x"',                         # only starts like seat-kit
                     'set "N=%APPDATA%\\npm\\..\\Microsoft\\x"',                        # climbs out of npm
                     'set "P=%LOCALAPPDATA%\\Programs\\Python\\Python312\\x"',          # not the lookup's pattern
                     'set "S=%LOCALAPPDATA%\\seat-kit\\..\\Other"'):                    # climbs out of seat-kit
            with self.subTest(line):
                self.assertIsNone(known(line))
        for line, entry in (('set "KIT_HOME=%LOCALAPPDATA%\\seat-kit"', KIT_APPDATA[0]),
                            ("Join-Path $env:LOCALAPPDATA 'seat-kit' }", KIT_APPDATA[0]),
                            ('if exist "%APPDATA%\\npm\\claude.cmd" set "CLAUDE=x"', KIT_APPDATA[3]),
                            ('(Test-Path "$env:LOCALAPPDATA\\Microsoft\\WindowsApps\\winget.exe")) {', KIT_APPDATA[2]),
                            ('for /d %%d in ("%LOCALAPPDATA%\\Programs\\Python\\Python3*") do', KIT_APPDATA[1])):
            with self.subTest(line):
                self.assertEqual(known(line), entry)


if __name__ == "__main__":
    unittest.main()
