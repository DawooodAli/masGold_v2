"""The encrypted offsite backup: AES-256-GCM chunks, a wrong passphrase / a changed, cut-off or extended file is refused, and the offsite step of the backup tool (no database, no mysqldump,
no keyring: the passphrase is passed in)."""
import gzip
import os
import tempfile
from datetime import datetime
from pathlib import Path
import unittest
from unittest.mock import patch

from services import backup_crypto as crypto
from tools import backup_database as backup

PASSPHRASE = "correct horse battery staple"


class BackupCryptoTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.dir = Path(self.folder.name)

    def encrypted(self, data, chunk=1024):
        source, target = self.dir / "plain.bin", self.dir / "plain.bin.enc"
        source.write_bytes(data)
        crypto.encrypt_file(source, target, PASSPHRASE, chunk_bytes=chunk)
        return target

    def test_round_trip_for_empty_small_exact_multiple_and_large_files(self):
        for size in (0, 1, 1023, 1024, 1025, 4096, 10_000):
            with self.subTest(size=size):
                data = os.urandom(size)
                target = self.encrypted(data)
                out = self.dir / f"out{size}.bin"
                self.assertEqual(crypto.decrypt_file(target, out, PASSPHRASE), size)
                self.assertEqual(out.read_bytes(), data)
                self.assertEqual(crypto.verify_file(target, PASSPHRASE), size)

    def test_the_file_hides_the_content_and_each_copy_differs(self):
        data = b"-- Dump completed " * 200
        first, second = self.encrypted(data).read_bytes(), self.encrypted(data).read_bytes()
        self.assertNotIn(b"Dump completed", first)
        self.assertNotEqual(first, second)                                            # fresh salt and nonce prefix every time

    def test_wrong_or_short_passphrase_is_refused(self):
        target = self.encrypted(os.urandom(3000))
        with self.assertRaises(crypto.BackupCryptoError):
            crypto.verify_file(target, "another passphrase entirely")
        with self.assertRaises(crypto.BackupCryptoError):
            crypto.encrypt_file(self.dir / "plain.bin", self.dir / "x.enc", "short")

    def test_a_changed_cut_off_or_extended_file_is_refused(self):
        target = self.encrypted(os.urandom(5000))
        raw = target.read_bytes()
        cases = {"flipped": raw[:200] + bytes([raw[200] ^ 1]) + raw[201:],
                 "cut at a record boundary": raw[:crypto.HEADER.size + crypto.LENGTH.size + 1024 + 16],
                 "cut inside": raw[:-10], "extended": raw + b"\x00",
                 "not a backup": b"hello", "bad magic": b"XXXXXX" + raw[6:]}
        for label, damaged in cases.items():
            with self.subTest(label):
                path = self.dir / "damaged.enc"
                path.write_bytes(damaged)
                with self.assertRaises(crypto.BackupCryptoError):
                    crypto.verify_file(path, PASSPHRASE)

    def test_swapped_chunks_are_refused(self):
        target = self.encrypted(os.urandom(4096), chunk=1024)
        raw = target.read_bytes()
        size = crypto.LENGTH.size + 1024 + 16
        body = raw[crypto.HEADER.size:]
        swapped = raw[:crypto.HEADER.size] + body[size:2 * size] + body[:size] + body[2 * size:]
        path = self.dir / "swapped.enc"
        path.write_bytes(swapped)
        with self.assertRaises(crypto.BackupCryptoError):
            crypto.verify_file(path, PASSPHRASE)


class OffsiteCopyTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.dir = Path(self.folder.name)
        self.local = self.dir / "local" / "masgold_prod_20261031_010000.sql.gz"
        self.local.parent.mkdir()
        body = b"-- MySQL dump\n" + b"".join(b"INSERT INTO t VALUES ('" + os.urandom(20).hex().encode() + b"');\n" for _ in range(300))
        with gzip.open(self.local, "wb") as handle:
            handle.write(body + b"-- Dump completed on 2026-10-31 01:00:00\n")

    def test_the_offsite_copy_is_encrypted_verified_and_restorable(self):
        offsite = self.dir / "drive"
        text = backup.offsite_copy(self.local, offsite, "masgold_prod", datetime(2026, 10, 31, 1, 0), 30, 7, passphrase=PASSPHRASE)
        copy = offsite / (self.local.name + ".enc")
        self.assertTrue(copy.exists() and copy.name in text)
        self.assertFalse(list(offsite.glob("*.partial")))
        restored = self.dir / "restored.sql.gz"
        crypto.decrypt_file(copy, restored, PASSPHRASE)
        self.assertEqual(restored.read_bytes(), self.local.read_bytes())
        self.assertGreater(backup.verify(restored), 100)                              # the restored file is a valid dump again

    def test_without_a_stored_passphrase_the_step_fails_loudly(self):
        with patch("services.secret_store.SecretStore.get_secret", return_value=None):
            with self.assertRaisesRegex(backup.BackupError, "passphrase"):
                backup.offsite_copy(self.local, self.dir / "drive", "masgold_prod", datetime(2026, 10, 31, 1, 0), 30, 7)
        self.assertFalse((self.dir / "drive" / (self.local.name + ".enc")).exists())

    def test_retention_applies_only_to_this_databases_encrypted_files(self):
        offsite = self.dir / "drive"
        offsite.mkdir()
        for name in ("masgold_prod_20260101_000000.sql.gz.enc", "masgold_prod_20260102_000000.sql.gz.enc", "masgold_v2_20260101_000000.sql.gz.enc", "notes.txt",
                     "masgold_prod_20260103_000000.sql.gz"):
            (offsite / name).write_bytes(b"x")
        backup.offsite_copy(self.local, offsite, "masgold_prod", datetime(2026, 10, 31, 1, 0), 30, 1, passphrase=PASSPHRASE)
        names = sorted(p.name for p in offsite.iterdir())
        self.assertIn("masgold_v2_20260101_000000.sql.gz.enc", names)                  # another database: untouched
        self.assertIn("masgold_prod_20260103_000000.sql.gz", names)                    # a plain local-style file: untouched
        self.assertIn("notes.txt", names)
        self.assertNotIn("masgold_prod_20260101_000000.sql.gz.enc", names)
        self.assertIn("masgold_prod_20261031_010000.sql.gz.enc", names)


if __name__ == "__main__":
    unittest.main()
