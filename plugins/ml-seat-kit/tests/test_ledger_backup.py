"""Checks for scripts/ledger.py and scripts/backup.py."""
import json
import pathlib
import tempfile
import unittest
import zipfile

from tests import _helpers
import backup
import ledger
import seat_common


class Ledger(unittest.TestCase):
    def test_append_lines(self):
        td, seat = _helpers.make_seat()
        with td:
            self.assertEqual(ledger.main(["--seat", str(seat), "t1", "vendor@", "Quote", "drafted", "reply owed"]), 0)
            self.assertEqual(ledger.main(["--seat", str(seat), "t2", "x@", "Lunch", "waiting", "asked", "2026-12-01"]), 0)
            rows = [json.loads(l) for l in (seat / "state" / "ledger.jsonl").read_text().splitlines()]
            self.assertEqual([r["thread"] for r in rows], ["t1", "t2"])
            self.assertEqual(rows[1]["due"], "2026-12-01")
            self.assertTrue(rows[0]["ts"].endswith("Z"))
            self.assertNotIn(b"\r\n", (seat / "state" / "ledger.jsonl").read_bytes())

    def test_refuses_bad_input(self):
        td, seat = _helpers.make_seat()
        with td:
            s = ["--seat", str(seat)]
            self.assertEqual(ledger.main(s + ["t1", "a", "b", "maybe", "r"]), 2)        # unknown outcome
            self.assertEqual(ledger.main(s + ["t1", "a", "b", "done", "r", "soon"]), 2)  # bad due date
            self.assertEqual(ledger.main(s + ["t1", "a"]), 2)
            self.assertFalse((seat / "state" / "ledger.jsonl").exists())

    def test_seat_found_from_a_subfolder(self):
        td, seat = _helpers.make_seat()
        with td:
            sub = seat / "state"
            root, rest = seat_common.resolve_seat(["--seat", str(sub), "x"])
            self.assertEqual(root, seat.resolve())
            self.assertEqual(rest, ["x"])


class Backup(unittest.TestCase):
    def test_backup_and_set(self):
        td, seat = _helpers.make_seat(name="Juniper")
        with td, tempfile.TemporaryDirectory() as dest:
            (seat / "state" / "open-items.md").write_text("# Open items\n")
            (seat / "CLAUDE.md").write_text("rules")
            (seat / ".cache").mkdir()
            (seat / ".cache" / "noise.json").write_text("{}")
            self.assertEqual(backup.main(["--seat", str(seat)]), 2)                    # no folder chosen yet
            self.assertEqual(backup.main(["--seat", str(seat), "--set", dest + "-missing"]), 2)
            self.assertEqual(backup.main(["--seat", str(seat), "--set", dest]), 0)
            cfg = seat_common.load_config(seat)
            self.assertEqual(pathlib.Path(cfg["backup_dir"]), pathlib.Path(dest).resolve())
            z = pathlib.Path(dest) / "Juniper backup" / "seat-state.zip"
            names = set(zipfile.ZipFile(z).namelist())
            self.assertIn("state/open-items.md", names)
            self.assertIn(".seat/seat.json", names)
            self.assertIn("CLAUDE.md", names)
            self.assertFalse([n for n in names if n.startswith(".cache")])
            self.assertTrue((pathlib.Path(dest) / "LAST_BACKUP.txt").is_file())
            self.assertEqual(backup.main(["--seat", str(seat)]), 0)                    # again: replaces in place
            self.assertFalse((z.parent / "seat-state.zip.new").exists())

    def test_unmounted_drive_fails_plainly(self):
        td, seat = _helpers.make_seat()
        with td:
            ok, msg = backup.backup(seat, pathlib.Path(td.name) / "no-such-drive")
            self.assertFalse(ok)
            self.assertTrue(msg.startswith("FAILED"))


if __name__ == "__main__":
    unittest.main()
