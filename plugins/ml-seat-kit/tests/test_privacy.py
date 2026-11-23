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


if __name__ == "__main__":
    unittest.main()
