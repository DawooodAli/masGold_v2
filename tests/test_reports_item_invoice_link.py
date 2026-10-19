"""Stock report -> invoice -> the piece (packet27). The available-stock report offers the LAST linked invoice (a line of a non-draft invoice carrying the piece), the existing
invoice screen opens exactly that invoice and the piece is shown inside its details - also for a legacy invoice whose split amounts are unknown. «Last linked invoice» is NOT claimed
to be the invoice that acquired the piece. Real ReportService + the real InvoiceHistoryService over their fixtures; no database."""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal as D
import unittest

from services.report_service import ReportService
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import session_for
from tests.test_invoice_history import FINALIZED_AT, invoice, make, summary_row
from tests.test_report_service import ALL, FakeRepo, TODAY


class ItemInvoiceLinkTests(unittest.TestCase):
    def report(self, items):
        class Repo(FakeRepo):
            def stock_items(self, statuses):
                return [r for r in items if r["inventory_status"] in statuses]
        session = session_for(ALL)

        @contextmanager
        def snapshot():
            yield object()
        return ReportService(session, transaction_factory=snapshot, repository=Repo, authorization=FakeAuthorization(session), today=lambda: TODAY)

    @staticmethod
    def item(barcode, invoice_id, number, line_type="SELL_INVENTORY"):
        return {"item_id": 100, "weight_grams": D("10.000"), "size": "18", "stock_classification": "NEW_STOCK", "inventory_status": "IN_STOCK", "acquisition_source": "MARKET_PURCHASE",
                "acquired_at": datetime(2026, 9, 1), "karat_id": 1, "karat_value": 21, "item_type": "خاتم", "category": "خواتم", "factory": "لوكس", "barcode": barcode,
                "invoice_id": invoice_id, "invoice_number": number, "invoice_line_type": line_type}

    def test_the_report_row_opens_the_invoice_and_the_invoice_shows_the_piece(self):
        row = self.report([self.item("01260925", 21, "26092701")]).run("stock", {})["tables"][1]["rows"][0]
        self.assertEqual((row["barcode"], row["invoice"], row["ref"]), ("01260925", "26092701 (بيع قطعة)", {"type": "INVOICE", "id": "21"}))
        service, state, _ = make(rows=[summary_row("26092701", FINALIZED_AT, invoice_id="21")])
        detail = service.detail(row["ref"]["id"])                                                   # the existing invoice screen's own service call
        piece = next(l for l in detail["sale_lines"] if l.get("barcode") == "01260925" or l.get("barcode_snapshot") == "01260925")
        self.assertIn(str(piece.get("inventory_item_id", "100")), ("100",))
        self.assertEqual(detail["invoice_number"], "26092701")

    def test_a_legacy_invoice_with_unknown_split_amounts_still_opens_and_shows_the_piece(self):
        service, state, _ = make(rows=[])
        state["invoices"]["21"] = invoice(record_origin="LEGACY", sell_total=None, buy_total=None, net_amount=D("90000.00"), legacy_completeness="PARTIAL",
                                          payments=[], settlement_status="UNKNOWN")
        detail = service.detail("21")
        self.assertTrue(any((l.get("barcode") or l.get("barcode_snapshot")) == "01260925" for l in detail["sale_lines"]))
        self.assertEqual(detail["invoice_number"], "26092701")                                      # the unknown amounts do not stop the invoice or its lines from being shown

    def test_a_piece_with_no_invoice_says_so_and_offers_no_link(self):
        row = self.report([self.item("0999", None, None)]).run("stock", {})["tables"][1]["rows"][0]
        self.assertEqual((row["invoice"], row["ref"]), ("غير مرتبطة بفاتورة", None))

    def test_the_meaning_says_what_the_linked_invoice_is_and_is_not(self):
        meaning = " ".join(self.report([self.item("0999", None, None)]).run("stock", {})["meaning"])
        self.assertIn("آخر فاتورة غير مسودة", meaning)
        self.assertIn("ليست بالضرورة فاتورة أصل اقتناء القطعة", meaning)


if __name__ == "__main__":
    unittest.main()
