"""Party-page simplification (supplier / intermediary page): ONE scroll, supplier accounts and intermediary holdings kept separate, summaries grouped by source kind and karat / fineness, received shop pieces
apart from gold held with the intermediary, codes and history under Details, «المزيد» for secondary actions, «تصحيح تسجيل» with the exact balances. Real Main.qml, real mouse
clicks at the three target sizes; services are the in-memory fakes (NOT real-MySQL tests)."""
from decimal import Decimal as D, localcontext
import os
from pathlib import Path
import unittest

from app.formatting import grams
from app.viewmodels.supplier_viewmodel import held_gold_21k_equivalent
from services import supplier_rules as rules
from tests import test_holding_receipt_ui as base
from tests import test_intermediary_holdings_ui as ui

SIZES = ((1440, 900), (1280, 800), (1080, 680))
DUAL = "21"


class DualRoleStub(ui.HoldingSupplierStub):
    """Party 21 is a 21K supplier AND an intermediary; its supplier GOLD position is a CREDIT (the supplier owes the shop) and its MAKING position is owed to it."""

    def _supplier(self, row):
        if row["id"] == 21:
            return {"id": "21", "code": "S000021", "name": row["name"], "phone": "", "notes": "", "is_active": True, "karat_id": 1, "karat_value": 21,
                    "karat_locked": False, "is_supplier": True, "is_intermediary": True, "role_text": "مورد • مسوقجي", "history_karats": []}
        return super()._supplier(row)

    def supplier_detail(self, supplier_id, *, _expected_actor=None):
        detail = super().supplier_detail(supplier_id, _expected_actor=_expected_actor)
        if int(supplier_id) == 21:
            detail["balances"] = {"gold_status": "RESOLVED", "gold_karat_value": 21, "gold_karat_id": 1,
                                  "gold_by_karat": [{"karat_value": 21, "direction": rules.SUPPLIER_OWES, "weight_balance": D("-4.000")}],
                                  "gold_direction": rules.SUPPLIER_OWES, "gold_balance": D("-4.000"), "making_direction": rules.SHOP_OWES, "making": D("3200.00")}
        return detail


class SupplierOnlyStub(DualRoleStub):
    """Party 30 is a plain 21K SUPPLIER (no intermediary role): gold owed TO the supplier and making owed BY the supplier."""

    def _supplier(self, row):
        if row["id"] == 30:
            return {"id": "30", "code": "S000030", "name": row["name"], "phone": "", "notes": "", "is_active": True, "karat_id": 1, "karat_value": 21,
                    "karat_locked": False, "is_supplier": True, "is_intermediary": False, "role_text": "مورد", "history_karats": []}
        return super()._supplier(row)

    def supplier_detail(self, supplier_id, *, _expected_actor=None):
        detail = super().supplier_detail(supplier_id, _expected_actor=_expected_actor)
        if int(supplier_id) == 30:
            detail["balances"] = {"gold_status": "RESOLVED", "gold_karat_value": 21, "gold_karat_id": 1,
                                  "gold_by_karat": [{"karat_value": 21, "direction": rules.SHOP_OWES, "weight_balance": D("12.500")}],
                                  "gold_direction": rules.SHOP_OWES, "gold_balance": D("12.500"), "making_direction": rules.SUPPLIER_OWES, "making": D("1500.00")}
        return detail


class PartyPageLayoutTests(base.ReceiptPageTests):
    def setUp(self):
        super().setUp()
        self.stub = DualRoleStub(self.db, self.holding)
        self.svm._service = self.stub
        self.db.suppliers[21]["name"] = "مورد ومسوقجي"

    def mixed_party(self, party=ui.PARTY):
        """An assayed bar with allocation history, one live/reversed receipt, and a separate LOGO source."""
        trip, lot, done = self.hand_over(party=party)
        pool = str(max(self.db.holding_pools))
        first = self.settle_service.settle("LOT", pool, "10", "1", "GRAM_ENTITLEMENT", False, "2026-10-10", "", "", self.key())
        self.settle_service.reverse(first["id"], "تصحيح")
        keys = self.key
        self.settle_service.settle("LOT", pool, "10", "2", "GRAM_ENTITLEMENT", False, "2026-10-10", "", "", keys())
        live = self.svm._holding_receipts.receive(pool, "1", "1", "2026-10-10", "", "", keys())
        gone = self.svm._holding_receipts.receive(pool, "0.5", "0.5", "2026-10-10", "", "", keys())
        self.svm._holding_receipts.reverse(gone["receipt_id"], "تصحيح")
        self.logo_hand_over("8", party=party)
        return pool, live, gone

    def y_of(self, name):
        item = self.item(name)
        return item.mapToItem(self.item("partyScroll").property("contentItem"), self.point(0, 0)).y()

    def point(self, x, y):
        from PySide6.QtCore import QPointF
        return QPointF(x, y)

    def capture_review_view(self, name, width, height, collapse_details=False):
        review_dir = os.getenv("MASGOLD_UI_REVIEW_DIR")
        if not review_dir:
            return
        if collapse_details and self.item("holdingDetailsCard").property("visible"):
            self.click("holdingDetailsToggle")
        self.root.setWidth(width)
        self.root.setHeight(height)
        QTest_wait(200)
        self.item("partyScroll").setProperty("contentY", 0)
        screenshot_dir = Path(review_dir)
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        screenshot = screenshot_dir / f"{name}-{width}x{height}.png"
        self.assertTrue(self.root.grabWindow().save(str(screenshot)), str(screenshot))
        if collapse_details:
            self.click("holdingDetailsToggle")

    # ------------------------------------------------------------ tests
    def test_21k_equivalent_uses_assay_and_each_logo_karat_without_partial_unknown_totals(self):
        sample = held_gold_21k_equivalent(
            [{"balance": D("2.950"), "fineness": D("930.0")}],
            [{"balance": D("1.000"), "karat_value": 21}],
        )
        with localcontext() as context:
            context.prec = 50
            expected_sample = D("2.950") * D("930.0") / D("875") + D("1.000")
        self.assertEqual(sample, expected_sample)
        self.assertEqual(grams(sample), "4.135")

        other_karats = held_gold_21k_equivalent(
            [{"balance": D("1.250"), "fineness": D("875.0")}],
            [{"balance": D("2.000"), "karat_value": 18},
             {"balance": D("1.000"), "karat_value": 22},
             {"balance": D("0.500"), "karat_value": 24}],
        )
        with localcontext() as context:
            context.prec = 50
            expected_other = (D("1.250") + D("2.000") * D(18) / D(21)
                              + D("1.000") * D(22) / D(21) + D("0.500") * D(24) / D(21))
        self.assertEqual(other_karats, expected_other)
        self.assertIsNone(held_gold_21k_equivalent(
            [{"balance": D("2.000"), "fineness": D("875.0")},
             {"balance": D("1.000"), "fineness": None}],
            [{"balance": D("1.000"), "karat_value": 21}],
        ))

    def test_an_intermediary_only_party_has_gold_tab_content_and_no_account_sections_at_the_three_sizes(self):
        self.mixed_party()
        self.open_party("20")                                                                   # the helper opens «سجل الذهب»
        for width, height in SIZES:
            with self.subTest(size=f"{width}x{height}"):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest_wait(200)
                self.assertGreater(self.item("partyScroll").property("contentHeight"), 0)
                self.assertFalse(self.item("supplierInvoiceList").property("interactive"))     # no nested scrolling in the lists
                self.assertTrue(self.item("accountPositions").property("visible"))             # the gold card summarizes shop-owned holdings
                self.assertTrue(self.item("goldHeldPosition").property("visible"))
                self.assertFalse(self.item("makingPositionCard").property("visible"))           # no supplier-making account for intermediary-only parties
                self.assertFalse(self.item("tabMaking").property("visible"))
                self.assertFalse(self.item("goldAccountSection").property("visible"))
                self.assertTrue(self.item("tabGold").property("visible"))                      # available under the holding-view right alone
                self.assertTrue(self.item("holdingsGroup").property("visible"))
                for name in ("holdingReceiveButton", "holdingMoreButton", "logoReturnButton", "logoMoreButton", "holdingDetailsToggle"):
                    self.assert_inside_window(self.item(name))                                  # reachable by the one scroll, nothing clipped
        self.assertEqual(self.warnings, [])

    def test_an_intermediary_only_overview_is_concise_and_has_no_purchase_invoices(self):
        self.mixed_party()
        self.open_party("20")
        self.click("tabInvoices")
        self.assertTrue(self.item("overviewHolding").property("visible"))
        self.assertEqual(self.text("intermediaryOnlyNote"), "هذا الطرف مسوقجي فقط؛ لا توجد فواتير شراء.")
        self.assertFalse(self.item("supplierInvoiceList").property("visible"))                 # purchase invoices are supplier-only
        self.assertEqual(self.visible_texts("overviewGroupRow"), [])                            # all intermediary gold stays in «سجل الذهب»
        self.assertEqual(self.visible_texts("overviewReceivedRow"), [])

    def test_a_supplier_only_party_has_the_account_the_making_tab_and_no_holdings(self):
        self.stub = SupplierOnlyStub(self.db, self.holding)
        self.svm._service = self.stub
        self.db.suppliers[30] = {"id": 30, "name": "مورد فقط", "is_active": 1, "is_supplier": True, "is_intermediary": False, "karat_id": 1}
        self.open_party("30")
        self.assertTrue(self.item("accountPositions").property("visible"))
        self.assertTrue(self.item("tabMaking").property("visible"))
        self.assertIn("علينا للمورد", self.text("goldPosition"))
        self.assertEqual(self.item("makingPositionCard").property("title"), "موقف مصنعيات المورد")
        self.click("tabGold")
        self.assertTrue(self.item("goldAccountSection").property("visible"))
        self.assertFalse(self.item("goldAccountTitle").property("visible"))                    # a single section needs no heading
        self.assertFalse(self.item("holdingsGroup").property("visible"))                       # no intermediary role: no holdings section
        self.assertFalse(self.item("goldHeldPosition").property("visible"))
        self.assertEqual(self.text("goldAccountBalanceLabel"), "حساب المورد")
        self.assertIn("علينا للمورد", self.text("goldPosition"))
        self.assertIn("لنا عند المورد", self.text("makingPosition"))
        self.assertEqual(self.item("makingPositionCard").property("title"), "موقف مصنعيات المورد")
        review_dir = os.getenv("MASGOLD_UI_REVIEW_DIR")
        if review_dir:
            for width, height in ((1440, 900), (1080, 680)):
                self.capture_review_view("supplier-only-gold", width, height)
        self.click("tabInvoices")
        self.assertTrue(self.item("supplierInvoiceList").property("visible"))

    def test_dual_role_keeps_payable_grams_and_shop_owned_gold_in_separate_gold_tab_sections(self):
        self.mixed_party("21")
        self.open_party(DUAL)
        for width, height in SIZES:
            with self.subTest(size=f"{width}x{height}"):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest_wait(200)
                gold, making = self.text("goldPosition"), self.text("makingPosition")
                self.assertEqual(gold, "لنا عند المورد 4.000 جرام عيار 21")                    # supplier credit is not netted with held shop gold
                self.assertIn("علينا للمورد", making)
                self.assertEqual(self.item("makingPositionCard").property("title"), "موقف مصنعيات المورد")
                self.assertNotIn("علينا للمورد", gold)
                self.assertEqual(self.text("goldHeldPosition"), "ذهب المحل عنده: يعادل 15.000 جرام عيار 21")
                self.assertEqual(self.text("goldAccountBalanceLabel"), "حساب المورد")
                self.assertTrue(self.item("goldAccountSection").property("visible"))
                self.assertTrue(self.item("goldAccountTitle").property("visible"))             # both sections are labelled
                self.assertEqual(self.text("goldAccountTitle"), "حساب المورد")
                self.assertLessEqual(self.item("supplierGoldLedger").height(), 28)              # compact empty supplier ledger before holdings
                self.assertTrue(self.item("holdingsGroup").property("visible"))
                self.assertEqual(self.item("holdingsPanel").property("title"), "ذهب المحل عند المسوقجي")
                self.assertIn("لوجو عيار 21", self.text("logoSource"))
                self.assertNotIn("غير معلوم", self.text("logoSource"))
                self.assertTrue(self.item("receivedPiecesCard").property("visible"))
                self.assertEqual(len(self.visible_texts("holdingGroupRow")), 2)
                self.assertEqual(len(self.visible_texts("receiptRow")), 1)
                self.assertLess(self.y_of("goldAccountSection"), self.y_of("holdingsGroup"))   # the account ledger, then the holdings: never mixed in one list
                self.assertEqual(self.visual_item("logoReturnButton").height(), self.item("logoMoreButton").height())
                self.assertEqual(self.item("logoMoreButton").height(), 30)
                for name in ("settleGoldButton", "payMakingButton", "tabGold", "logoMoreButton"):
                    self.assert_inside_window(self.item(name))
                self.assert_inside_window(self.visual_item("logoReturnButton"))
                if (width, height) in ((1440, 900), (1080, 680)) and os.getenv("MASGOLD_UI_REVIEW_DIR"):
                    self.capture_review_view("mixed-party-gold", width, height, collapse_details=True)
        self.assertEqual(self.warnings, [])

    def test_effective_holding_balance_updates_for_partial_settlement_return_and_reversal_without_counting_receipts_twice(self):
        pool, _, _ = self.mixed_party(DUAL)
        self.open_party(DUAL)
        self.assertEqual(self.text("goldHeldPosition"), "ذهب المحل عنده: يعادل 15.000 جرام عيار 21")
        self.assertIn("المخزون 1.000", self.text("receiptRow"))

        posted = self.settle_service.settle("LOT", pool, "10", "1", "GRAM_ENTITLEMENT", False,
                                            "2026-10-10", "", "", self.key())
        self.svm.selectSupplier(DUAL)
        self.wait_until(lambda: self.svm.detail.get("id") == DUAL, "party detail after partial settlement")
        self.assertEqual(self.text("goldHeldPosition"), "ذهب المحل عنده: يعادل 14.000 جرام عيار 21")
        self.settle_service.reverse(posted["id"], "تصحيح")
        self.svm.selectSupplier(DUAL)
        self.wait_until(lambda: self.svm.detail.get("id") == DUAL, "party detail after settlement reversal")
        self.assertEqual(self.text("goldHeldPosition"), "ذهب المحل عنده: يعادل 15.000 جرام عيار 21")

        logo_pool = str(max(self.db.logo_pools))
        returned = self.holding.return_logo(logo_pool, "2", "2026-10-10", "", "", self.key())
        self.svm.selectSupplier(DUAL)
        self.wait_until(lambda: self.svm.detail.get("id") == DUAL, "party detail after partial return")
        self.assertEqual(self.text("goldHeldPosition"), "ذهب المحل عنده: يعادل 13.000 جرام عيار 21")
        self.corrections.reverse("MARKET_TRIP_ENTRY", returned["entry_id"], "تصحيح")
        self.svm.selectSupplier(DUAL)
        self.wait_until(lambda: self.svm.detail.get("id") == DUAL, "party detail after return reversal")
        self.assertEqual(self.text("goldHeldPosition"), "ذهب المحل عنده: يعادل 15.000 جرام عيار 21")
        self.assertIn("لنا عند المورد", self.text("goldPosition"))                              # held gold never nets against supplier-account credit

    def test_summaries_are_grouped_by_source_and_never_labelled_unknown_because_of_received_pieces(self):
        self.mixed_party()
        self.open_party("20")
        groups = self.visible_texts("holdingGroupRow")
        self.assertEqual(len(groups), 2)                                                         # bars and LOGO are different sources: never merged
        self.assertTrue(any("سبائك" in g and "875.0" in g and "7.000" in g for g in groups), groups)
        self.assertTrue(any("لوجو عيار 21" in g and "8.000" in g for g in groups), groups)
        self.assertFalse(any("غير معلوم" in g for g in groups), groups)                          # holdings are NOT unknown because receipts are
        received = " ".join(self.visible_texts("receivedGroupRow"))
        self.assertIn("1.000", received)
        self.assertIn("العيار غير معلوم", received)                                              # only the received pieces are UNKNOWN
        self.assertIn("غير متاحة", " ".join(self.visible_texts("receivedRestriction")))        # usage restrictions stay visible
        self.assertEqual(len(self.visible_texts("receiptRow")), 1)                               # the live piece with its current stock stays on the page
        self.assertIn("المخزون 1.000", self.text("receiptRow"))
        self.assertEqual(self.text("holdingReceiveButton"), "استلام ذهب بالمحل")
        self.assertEqual(self.text("holdingReturnButton"), "إرجاع السبكة كاملة للعهدة")
        self.assertEqual(self.text("receiptReverseButton"), "تصحيح تسجيل")

    def test_details_are_collapsed_by_selection_and_hold_codes_history_and_the_reversed_receipt(self):
        self.mixed_party()
        self.open_party("20")
        self.click("holdingDetailsToggle")                                                       # close what the helper opened
        self.assertFalse(self.item("holdingDetailsCard").property("visible"))
        main_text = " ".join(self.visible_texts("holdingSource") + self.visible_texts("holdingFigures") + self.visible_texts("receiptRow"))
        for code in ("IH0", "HR0", "RP0", "MT0", "SG0"):
            self.assertNotIn(code, main_text)                                                   # no codes on the main page
        self.click("holdingDetailsToggle")
        self.assertTrue(self.item("holdingDetailsCard").property("visible"))
        details = " ".join(self.visible_texts("holdingRefs") + self.visible_texts("receiptHistoryRow") + self.visible_texts("holdingSettlementRow"))
        for code in ("IH0", "HR0", "RP0", "MT0", "SG0"):
            self.assertIn(code, details)
        self.assertEqual(len(self.visible_texts("receiptHistoryRow")), 2)                       # the live AND the reversed receipt
        self.assertTrue(any("معكوس" in t for t in self.visible_texts("receiptHistoryRow")))
        self.assertIn("الذهب الصافي", " ".join(self.visible_texts("holdingRefs")))              # pure grams belong to Details

    def test_more_reveals_the_secondary_actions_without_preselecting_anything(self):
        self.mixed_party()
        self.open_party("20")
        self.assertFalse(self.item("receiptMoreRow").property("visible"))
        self.click("receiptMoreButton")
        self.assertTrue(self.item("receiptMoreRow").property("visible"))
        self.assertTrue(self.item("receiptReverseButton").property("visible"))
        self.click("receiptMoreButton")
        self.assertFalse(self.item("receiptMoreRow").property("visible"))
        self.assertFalse(self.item("holdingMoreRow").property("visible"))
        self.click("holdingMoreButton")
        self.assertTrue(self.item("holdingMoreRow").property("visible"))
        self.assertEqual(self.text("holdingSettleButton"), "تسوية لمورد")
        self.click("holdingSettleButton")                                                        # opens the dialog: its declarations stay unselected there
        self.wait_until(lambda: self.item("holdingSettleDialog").property("opened"), "settlement dialog")
        self.assertFalse(self.item("confirmHoldingSettleButton").property("enabled"))

    def test_an_intact_bar_makes_the_custody_return_the_primary_action(self):
        self.hand_over()
        self.open_party("20")
        self.assertTrue(self.item("holdingReturnButton").property("primary"))
        self.assertFalse(self.item("holdingReceiveButton").property("visible"))                  # no receipt without allocation history

    def test_allocation_history_makes_the_shop_receipt_primary_with_plain_guidance(self):
        self.mixed_party()
        self.open_party("20")
        self.assertFalse(self.item("holdingReturnButton").property("primary"))                   # the return waits under More
        self.assertTrue(self.item("holdingReceiveButton").property("primary"))
        self.assertEqual(self.text("holdingReceiveButton"), "استلام ذهب بالمحل")
        self.assertEqual(self.text("holdingReason"), "الرجوع كسبكة كاملة غير متاح؛ استخدم استلام ذهب بالمحل")
        self.assertNotIn("SG0", self.text("holdingReason"))
        self.assertIn("SG0", self.text("holdingRefs"))                                          # its reference lives under Details
        details = self.text("holdingRefs")
        self.assertIn("لا يُسجَّل رجوعها كاملة ولا يُعكس تسليمها", details)
        self.assertIn("قبل عملية استلام فعلي مستقبلية", details)

    # ------------------------------------------------------------ refresh versus selecting another party
    def mark_rows(self):
        """Tag the live holding row with a dynamic property: it survives only while the SAME row object lives, so it proves whether the rows were rebuilt."""
        self.item("holdingMoreRow").setProperty("rowMark", 1)

    def rows_were_rebuilt(self):
        return self.item("holdingMoreRow").property("rowMark") is None

    def reload_party(self):
        before = self.stub.reloads
        self.svm.refresh()
        self.wait_until(lambda: self.stub.reloads > before, "party reloaded")
        self.wait_idle()
        QTest_wait(150)

    def test_refreshing_the_same_party_with_unchanged_data_keeps_the_open_rows(self):
        self.mixed_party()
        self.open_party("20")
        self.click("holdingMoreButton")
        self.mark_rows()
        self.reload_party()
        self.assertFalse(self.rows_were_rebuilt())                                              # nothing changed: the rows are the same objects
        self.assertTrue(self.item("holdingMoreRow").property("visible"))
        self.assertTrue(self.item("holdingDetailsCard").property("visible"))

    def test_a_data_change_rebuilds_the_rows_but_keeps_more_and_details_open(self):
        pool, live, gone = self.mixed_party()
        self.open_party("20")
        self.click("holdingMoreButton")
        self.mark_rows()
        figures = self.text("holdingFigures")
        self.svm._holding_receipts.receive(pool, "0.5", "0.5", "2026-10-10", "", "", self.key())      # a committed receipt changes the pool balance and the receipts
        self.reload_party()
        self.assertTrue(self.rows_were_rebuilt())                                               # the underlying rows were really rebuilt ...
        self.wait_until(lambda: self.text("holdingFigures") != figures, "new balance shown")
        self.assertTrue(self.item("holdingMoreRow").property("visible"))                        # ... and the employee's open row and Details stayed open
        self.assertTrue(self.item("holdingDetailsCard").property("visible"))

    def test_selecting_another_party_rebuilds_and_collapses_more_and_details(self):
        self.mixed_party()
        self.logo_hand_over("4", party=21)
        self.open_party("20")
        self.click("holdingMoreButton")
        self.mark_rows()
        self.assertTrue(self.item("holdingDetailsCard").property("visible"))
        self.svm.selectSupplier("21")
        self.wait_idle()
        QTest_wait(200)
        self.svm.selectSupplier("20")
        self.wait_idle()
        QTest_wait(250)
        self.assertTrue(self.rows_were_rebuilt())
        self.assertFalse(self.item("holdingMoreRow").property("visible"))                      # a different selection starts collapsed (also coming back)
        self.assertFalse(self.item("holdingDetailsCard").property("visible"))

    def test_the_correction_dialog_states_the_exact_balances_and_is_inside_the_window(self):
        self.mixed_party()
        self.open_party("20")
        for width, height in SIZES:
            with self.subTest(size=f"{width}x{height}"):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest_wait(150)
                self.click("holdingSettlementReverseButton")
                self.wait_until(lambda: self.item("reverseSettlementDialog").property("opened"), "correction dialog")
                note = self.text("reverseSettlementNote")
                self.assertIn("أثر التصحيح على الأرصدة", note)
                self.assertIn("2.000 جرام إلى رصيد المسوقجي", note)                          # the grams restored to the intermediary
                self.assertIn("حساب المورد", note)                                             # and the supplier balance it removes
                self.assertNotIn("لا يؤثر", note)                                              # never implies "no accounting effect"
                for name in ("reverseSettlementReason", "confirmReverseSettlementButton"):
                    self.assert_inside_window(self.item(name))
                self.click_cancel()

    def click_cancel(self):
        QTest_wait(50)
        from PySide6.QtCore import QMetaObject, Qt
        QMetaObject.invokeMethod(self.item("reverseSettlementDialog"), "close", Qt.DirectConnection)
        self.wait_until(lambda: not self.item("reverseSettlementDialog").property("visible"), "dialog fully closed")      # the modal overlay is gone, not just fading


def QTest_wait(ms):
    from PySide6.QtTest import QTest
    QTest.qWait(ms)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(PartyPageLayoutTests(name) for name in loader.getTestCaseNames(PartyPageLayoutTests) if name in PartyPageLayoutTests.__dict__)
    return suite


if __name__ == "__main__":
    unittest.main()
