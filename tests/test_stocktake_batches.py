"""الجرد بالباركود in batches (owner 2026-11-04): the left table (تم الجرد) shows only the current batch of N scanned pieces; the next scan after a
full batch starts a new one. The right table and every total keep covering the whole count, and the approval payload is unchanged."""
import unittest

from app.viewmodels.stocktake_viewmodel import StocktakeViewModel
from tests.test_inventory_stock import K21, Store, piece, service_for


def make(n=7):
    store = Store([piece(i, K21, "خاتم", "2.000", barcode=f"{i:08d}") for i in range(1, n + 1)])
    vm = StocktakeViewModel(service=service_for(store))
    vm.start()
    return vm


def scanned(vm):
    return [r["barcode"] for r in vm.scannedRows]


class BatchTests(unittest.TestCase):
    def test_off_by_default_every_scan_stays_in_the_left_table(self):
        vm = make()
        for i in (1, 2, 3):
            vm.scan(f"{i:08d}")
        self.assertFalse(vm.batchMode)
        self.assertEqual(len(vm.scannedRows), 3)

    def test_the_right_table_keeps_every_unscanned_piece_and_numbers_the_rows(self):
        vm = make()
        vm.setBatchSize(2)
        self.assertEqual([r["number"] for r in vm.remainingRows], list(range(1, 8)))
        vm.scan("00000003")
        self.assertEqual(len(vm.remainingRows), 6)

    def test_a_full_batch_stays_visible_until_the_next_scan_then_the_table_resets(self):
        vm = make()
        vm.setBatchSize(2)
        vm.scan("00000001")
        vm.scan("00000002")
        self.assertEqual(scanned(vm), ["00000002", "00000001"])           # the full batch is still on screen to be checked
        self.assertIn("اكتملت الدفعة 1", vm.message)
        vm.scan("00000003")
        self.assertEqual(scanned(vm), ["00000003"])                       # the next scan opened a new batch
        self.assertEqual(vm.scannedCount, 3)                              # the totals still cover everything
        self.assertEqual(vm.scannedWeightText, "6.00")
        self.assertEqual(vm.remainingCount, 4)
        self.assertIn("دفعات منتهية 1 (2 قطعة)", vm.batchText)

    def test_rows_are_numbered_in_scan_order_inside_the_batch(self):
        vm = make()
        vm.setBatchSize(3)
        vm.scan("00000005")
        vm.scan("00000001")
        self.assertEqual([(r["barcode"], r["number"]) for r in vm.scannedRows], [("00000001", 2), ("00000005", 1)])

    def test_a_duplicate_of_an_archived_piece_is_still_refused(self):
        vm = make()
        vm.setBatchSize(1)
        vm.scan("00000001")
        vm.scan("00000002")
        self.assertEqual(vm.scan("00000001"), "DUPLICATE")
        self.assertEqual(vm.scannedCount, 2)

    def test_start_next_batch_closes_the_current_one_early(self):
        vm = make()
        vm.setBatchSize(5)
        vm.scan("00000001")
        vm.startNextBatch()
        self.assertEqual(scanned(vm), [])
        self.assertEqual(vm.scannedCount, 1)
        vm.scan("00000002")
        self.assertEqual(scanned(vm), ["00000002"])

    def test_the_approval_payload_still_covers_the_whole_count(self):
        vm = make(5)
        vm.setBatchSize(2)
        for i in (1, 2, 3):
            vm.scan(f"{i:08d}")
        payload = vm.approvalPayload()
        self.assertEqual((len(payload["scanned"]), len(payload["remaining"])), (3, 2))

    def test_changing_the_size_does_not_lose_scans(self):
        vm = make()
        vm.scan("00000001")
        vm.scan("00000002")
        vm.setBatchSize(5)
        self.assertEqual((vm.scannedCount, len(vm.scannedRows)), (2, 0))
        vm.setBatchSize(0)
        self.assertEqual(len(vm.scannedRows), 2)


if __name__ == "__main__":
    unittest.main()
