"""The automatic backup tool: naming, retention (never the newest files, never foreign files) and the verification of a finished dump. No database and no mysqldump are used."""
import gzip
import os
import tempfile
from datetime import datetime
from pathlib import Path
import unittest

from tools import backup_database as backup


class BackupRulesTests(unittest.TestCase):
    def test_the_name_round_trips_and_foreign_files_are_ignored(self):
        now = datetime(2026, 10, 23, 2, 30, 5)
        name = backup.backup_name("masgold_prod", now)
        self.assertEqual(name, "masgold_prod_20261023_023005.sql.gz")
        with tempfile.TemporaryDirectory() as folder:
            for file in (name, "masgold_prod_20260101_000000.sql.gz", "masgold_v2_20260101_000000.sql.gz", "notes.txt", "masgold_prod_20260102_000000.sql.gz.partial"):
                (Path(folder) / file).write_bytes(b"x")
            found = backup.files_for(folder, "masgold_prod")
            self.assertEqual([p.name for p, _ in found], ["masgold_prod_20260101_000000.sql.gz", name])

    def test_retention_deletes_old_files_but_keeps_the_newest_ones(self):
        now = datetime(2026, 10, 23, 3, 0, 0)
        files = [(Path(f"f{i}"), datetime(2026, 8, 1 + i, 3, 0, 0)) for i in range(5)] + [(Path("recent"), datetime(2026, 10, 22, 3, 0, 0))]
        self.assertEqual([p.name for p in backup.to_delete(files, now, keep_days=30, keep_min=0)], ["f0", "f1", "f2", "f3", "f4"])
        self.assertEqual([p.name for p in backup.to_delete(files, now, keep_days=30, keep_min=3)], ["f0", "f1", "f2"])             # the newest three are protected
        self.assertEqual(backup.to_delete(files, now, keep_days=30, keep_min=10), [])

    def test_a_dump_is_accepted_only_when_it_ends_with_the_trailer(self):
        with tempfile.TemporaryDirectory() as folder:
            good, cut = Path(folder) / "good.sql.gz", Path(folder) / "cut.sql.gz"
            body = b"-- MySQL dump\n" + b"".join(b"INSERT INTO t VALUES ('" + os.urandom(20).hex().encode() + b"');\n" for _ in range(200))
            with gzip.open(good, "wb") as handle:
                handle.write(body + b"-- Dump completed on 2026-10-23 03:00:00\n")
            with gzip.open(cut, "wb") as handle:
                handle.write(body)
            self.assertGreater(backup.verify(good), 100)
            with self.assertRaises(backup.BackupError):
                backup.verify(cut)


if __name__ == "__main__":
    unittest.main()
