"""The barcode stocktake count survives closing the program (owner 2026-11-08): scope + scanned ids are saved to a small local file and re-applied on the CURRENT stock."""
import tempfile
import unittest
from pathlib import Path

from app.viewmodels.stocktake_viewmodel import StocktakeViewModel
from tests.test_inventory_stock import K21, Store, piece, service_for


def make(path, n=5, drop=()):
    store = Store([piece(i, K21, "خاتم", "2.000", barcode=f"{i:08d}") for i in range(1, n + 1) if i not in drop])
    return StocktakeViewModel(service=service_for(store), session_path=path)


class PersistTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "s.json"
        self.addCleanup(self.dir.cleanup)

    def test_a_new_program_run_gets_the_count_back(self):
        a = make(self.path)
        a.start()
        a.scan("00000001")
        a.scan("00000003")
        b = make(self.path)
        b.resume()
        self.assertEqual((b.scannedCount, b.remainingCount), (2, 3))
        self.assertEqual([r["barcode"] for r in b.scannedRows], ["00000003", "00000001"])

    def test_a_piece_gone_meanwhile_is_dropped_and_said(self):
        a = make(self.path)
        a.start()
        a.scan("00000001")
        a.scan("00000002")
        b = make(self.path, drop=(2,))
        b.resume()
        self.assertEqual(b.scannedCount, 1)
        self.assertIn("استبعاد 1", b.message)

    def test_reset_removes_the_saved_file(self):
        a = make(self.path)
        a.start()
        a.scan("00000001")
        self.assertTrue(self.path.exists())
        a.reset(True)
        self.assertFalse(self.path.exists())

    def test_a_broken_file_starts_a_fresh_session(self):
        self.path.write_text("not json", encoding="utf-8")
        b = make(self.path)
        b.resume()
        self.assertEqual((b.scannedCount, b.remainingCount), (0, 5))


if __name__ == "__main__":
    unittest.main()
