"""BOOK Phase 2 (058) on the real Main.qml with real mouse clicks: stock reissue, supplier delivery (credit preview in the supplier's own karat), marketer delivery
and receipt, a refused receipt that is corrected in the same dialog, and the visibility of the new actions at 1080x680 (plus a check at the two larger sizes).
Real services over the in-memory database (tests/market_trip_fakes.py + tests/marketer_fakes.py); BOOK trips are built by the test. NOT real-MySQL tests."""
from decimal import Decimal as D
import unittest
from unittest.mock import patch

from PySide6.QtTest import QTest

from tests.market_trip_fakes import SettlementRepo, TripRepo
from tests.marketer_fakes import MarketerRepo, prepare
from tests.test_market_trip_book_ui import BookPageTests, TODAY


class BookCyclePageTests(BookPageTests):
    def setUp(self):
        super().setUp()
        prepare(self.db)
        for target, value in (("repositories.marketer_custody_repository.MarketerCustodyRepository", MarketerRepo),
                              ("repositories.supplier_gold_settlement_repository.SupplierGoldSettlementRepository", SettlementRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)
        self.resize(1080, 680)

    def choice(self, key, wanted_id):
        """The index of a dialog choice (index 0 is the placeholder) as the employee sees it in the combo."""
        rows = self.tvm.detail["book"][key]
        return 1 + next(i for i, r in enumerate(rows) if r["id"] == str(wanted_id))

    def submit(self):
        self.click("confirmBookButton")
        self.wait_until(lambda: not self.item("tripBookDialog").property("visible") or self.tvm.errorMessage, "book dialog answered")
        self.wait_idle()
        self.assertFalse(self.item("tripBookDialog").property("visible"), f"still open: {self.tvm.errorMessage!r}")

    def open_action(self, button, dialog):
        self.reveal(self.item(button))
        self.assertTrue(self.item(button).property("visible"), button)
        self.click(button)
        self.wait_until(lambda: self.item(dialog).property("opened"), dialog)
        self.wait_idle()                                                                         # the dialog's refresh reloads the detail before anything is picked
        QTest.qWait(150)

    def karat21(self):
        return 1 + [str(k["text"]).replace("عيار", "").strip() for k in self.tvm.context["karats"]].index("21")

    def test_supplier_delivery_by_clicks_shows_the_credit_in_the_suppliers_karat(self):
        trip = self.make_book_trip(weight="20")
        self.show(trip)
        self.open_action("bookDeliverSupplierButton", "tripBookDialog")
        self.assertEqual(self.text("bookDialogTitle"), "تسليم ذهب لمورد (دفتري)")
        self.pick("bookTargetCombo", self.choice("suppliers", 21))                             # the 18K supplier
        self.book_line(0, 0, self.karat21(), "", "7.924")
        QTest.qWait(200)
        self.assertIn("9.245", self.text("bookPreviewCredit"), (self.item("bookTargetCombo").property("currentIndex"), self.tvm.detail["book"].get("suppliers"), self.tvm.context.get("suppliers")))
        self.assertIn("بعيار حسابه 18", self.text("bookPreviewCredit"))
        self.assertFalse(self.item("addBookLineButton").property("visible"))                    # one line per delivery
        self.submit()
        self.assertEqual([(m["supplier_id"], m["weight_delta"]) for m in self.db.sup_gold], [(21, D("-9.245"))])
        self.assertIn("دفتري", self.tvm.message)

    def test_marketer_delivery_and_a_refused_receipt_corrected_in_place(self):
        trip = self.make_book_trip(weight="10")
        self.show(trip)
        self.open_action("bookDeliverMarketerButton", "tripBookDialog")
        self.pick("bookTargetCombo", self.choice("marketers", 20))
        self.book_line(0, 0, self.karat21(), "", "5")
        self.submit()
        self.assertEqual(MarketerRepo(self.db).gold_balance(20), D("5.000"))
        self.open_action("bookReceiveMarketerButton", "tripBookDialog")
        self.pick("bookTargetCombo", self.choice("marketers", 20))
        self.book_line(0, 0, self.karat21(), "", "6")
        self.click("confirmBookButton")
        self.wait_until(lambda: self.tvm.errorMessage, "refusal shown")
        self.wait_idle()
        self.assertTrue(self.item("tripBookDialog").property("opened"))                          # refused: the dialog stays with every field kept
        self.assertIn("5.000", self.text("bookDialogError"))
        self.type_in("bookWeight_0", "")
        self.item("bookWeight_0").setProperty("text", "")
        self.type_in("bookWeight_0", "2")
        self.submit()
        self.assertEqual(MarketerRepo(self.db).gold_balance(20), D("3.000"))

    def test_stock_reissue_by_clicks_takes_the_whole_remaining_item(self):
        self.owner()
        origin = self.tvm._service.book_issue("", [{"basis": "MASTER_KARAT", "karat_id": "1", "weight": "20"}], TODAY, "", self.k())["trip_id"]
        back = self.tvm._service.book_return(origin, [{"basis": "FINENESS", "fineness": "866.7", "weight": "1.8"}], TODAY, "", self.k())
        target = self.make_book_trip(weight="5")
        self.show(target)
        self.open_action("bookIssueStockButton", "tripStockDialog")
        self.pick("stockItem_0", 1)
        self.click("stockAll_0")
        QTest.qWait(80)
        self.assertEqual(self.item("stockGrams_0").property("text"), "1.800")
        self.click("confirmStockButton")
        self.wait_until(lambda: not self.item("tripStockDialog").property("visible") or self.tvm.errorMessage, "stock dialog answered")
        self.wait_idle()
        self.assertFalse(self.item("tripStockDialog").property("visible"), f"still open: {self.tvm.errorMessage!r}")
        self.wait_idle()
        self.assertEqual(TripRepo(self.db).stock_balance(back["entry_id"], 1), (D("0.000"), D("0.000")))

    def test_an_evidenced_gain_by_clicks_needs_the_explanation_the_evidence_and_the_confirmation(self):
        trip = self.make_book_trip(weight="5")
        self.show(trip)
        self.open_action("bookGainButton", "tripBookDialog")
        self.assertEqual(self.text("bookDialogTitle"), "تسجيل زيادة مثبتة (دفتري)")
        self.assertFalse(self.item("addBookLineButton").property("visible"))
        self.assertFalse(self.item("bookTargetCombo").property("visible"))                         # a gain has no counterparty
        self.book_line(0, 0, self.karat21(), "", "0.5")
        self.assertFalse(self.item("confirmBookButton").property("enabled"))                       # nothing else is filled
        self.type_in("gainExplanationField", "وُجدت قطعة غير مسجلة")
        self.type_in("bookReferenceField", "إيصال وزن 77")
        self.assertFalse(self.item("confirmBookButton").property("enabled"))                       # the explicit confirmation is still missing
        self.click("gainConfirmedCheck")
        QTest.qWait(80)
        self.assertTrue(self.item("confirmBookButton").property("enabled"))
        self.submit()
        gains = list(self.db.gains.values())
        self.assertEqual([(g["explanation"], g["evidence_reference"]) for g in gains], [("وُجدت قطعة غير مسجلة", "إيصال وزن 77")])
        self.assertEqual(self.tvm.detail["book"]["balanceText"].split(" ")[0], "5.500")
        self.assertIn("زيادة مثبتة", " ".join(e["what"] for e in self.tvm.detail["entries"]))

    def test_a_reconciled_trip_is_closed_by_a_real_click_and_reopened(self):
        trip = self.make_book_trip(weight="10")
        service = self.tvm._service
        service._reconciliation_enabled = True
        service.book_return(str(trip), [{"basis": "MASTER_KARAT", "karat_id": "1", "weight": "4"}], TODAY, "", self.k())
        self.show(trip)
        self.assertFalse(self.item("closeTripButton").property("enabled"))
        self.assertIn("اعتماد تسوية", self.text("closeHint"))
        from services import book_settlement as bs
        from tests.market_trip_fakes import TripRepo
        digest = bs.build(TripRepo(self.db).reconciliation_source_current(trip))[3]
        service.reconcile(str(trip), digest, None, "OPERATING_LOSS", None, {k: True for k in bs.CONFIRMATION_KEYS}, dict(bs.CONTRACT_VERSIONS), self.k())
        self.tvm.refresh()
        self.wait_idle()
        self.show(trip)
        self.assertTrue(self.item("closeTripButton").property("enabled"))
        self.assertIn("التسوية معتمدة", self.text("closeHint"))
        self.reveal(self.item("closeTripButton"))
        self.click("closeTripButton")
        self.wait_until(lambda: self.db.trips[trip]["status"] == "CLOSED", "closed")
        self.wait_idle()
        self.assertIsNotNone(self.visible_item("reopenTripButton"))

    def test_the_gain_and_loss_report_opens_and_lists_documents_with_provisional_totals(self):
        trip = self.make_book_trip(weight="10")
        service = self.tvm._service
        service._reconciliation_enabled = True
        service.book_gain(str(trip), {"basis": "MASTER_KARAT", "karat_id": "1", "weight": "1"}, "شرح", "دليل", True, TODAY, "", self.k())
        from services import book_settlement as bs
        from tests.market_trip_fakes import TripRepo
        digest = bs.build(TripRepo(self.db).reconciliation_source_current(trip))[3]
        service.reconcile(str(trip), digest, TODAY, "OPERATING_LOSS", None, {k: True for k in bs.CONFIRMATION_KEYS}, bs.versions_for(
            bs.build(TripRepo(self.db).reconciliation_source_current(trip))[1]), self.k())
        self.show(trip)
        self.click("bookReportButton")
        self.wait_until(lambda: self.item("bookReportDialog").property("opened"), "report dialog")
        self.type_in("reportFromField", "2026-10-01")
        self.type_in("reportToField", "2026-10-31")
        self.click("reportApplyButton")
        self.wait_until(lambda: self.tvm.report.get("reconciliations") is not None and not self.tvm.report.get("loading"), "report loaded")
        QTest.qWait(150)
        self.assertIn("غير نهائي", self.text("reportTotals"))
        self.assertIsNotNone(self.visible_item("reportReconciliationRow"))
        self.assertIsNotNone(self.visible_item("reportGainRow"))
        self.assertEqual(self.tvm.report["reconciliations"][0]["reversed"], False)

    def test_the_new_actions_are_reachable_at_every_target_size(self):
        trip = self.make_book_trip(weight="10")
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            with self.subTest(size=f"{width}x{height}"):
                self.resize(width, height)
                self.show(trip)
                for button in ("bookDeliverSupplierButton", "bookDeliverMarketerButton", "bookReceiveMarketerButton", "bookIssueStockButton", "bookGainButton"):
                    self.reveal(self.item(button))
                    item = self.item(button)
                    self.assertTrue(item.property("visible"), button)
                    corner = item.mapToScene(item.boundingRect().bottomRight())
                    self.assertLessEqual(corner.x(), width + 1, button)
                    self.assertLessEqual(corner.y(), height + 1, button)


if __name__ == "__main__":
    unittest.main()
