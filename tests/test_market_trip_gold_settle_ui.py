"""Supplier gold settlement from market custody (046, «تسوية ذهب من العهدة») on the real Main.qml, with real mouse clicks at the three target
sizes. The services are the real ones over the in-memory database (tests/market_trip_fakes.py); the supplier page is a counting stub, so the
post-commit refresh of the supplier views is observable. NOT real-MySQL tests."""
from decimal import Decimal as D
from unittest.mock import patch
import unittest

from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from tests import test_invoice_page_qml as harness
from tests import test_market_trip_ui as base
from tests.market_trip_fakes import OWNER, SettlementRepo

TODAY = "2026-10-10"


def lkey(n):
    return "55555555-5555-4555-8555-%012d" % n


class GoldSettlePageTests(base.MarketTripHeatPageTests):
    def setUp(self):
        super().setUp()
        started = patch("repositories.supplier_gold_settlement_repository.SupplierGoldSettlementRepository", SettlementRepo)
        started.start()
        self.addCleanup(started.stop)
        self.supplier_vm = self.controller.operational_viewmodels["supplierVM"]
        self.stub = base.SupplierViewStub()
        self.supplier_vm._service = self.stub
        self.grant("SUPPLIER_VIEW", "SUPPLIER_GOLD_SETTLE", "SUPPLIER_GOLD_SETTLE_REVERSE", "TRANSACTION_CORRECT")
        self.fakes = SettlementRepo(self.db)

    # ------------------------------------------------------------ helpers
    def open_settle(self):
        self.reveal(self.item("custodyGoldSettleButton"))
        self.click("custodyGoldSettleButton")
        self.wait_until(lambda: self.item("tripGoldSettleDialog").property("opened"), "gold settlement dialog")
        self.wait_until(lambda: len(self.tvm.context.get("goldSuppliers", [])) == 2, "suppliers loaded")
        QTest.qWait(120)

    def fill_settle(self, supplier_index=2, weight="4"):
        """The supplier combo lists the active supplier first (index 1: 21K), then the inactive one (index 2: 18K)."""
        self.pick("custodyGoldSettleSupplierCombo", supplier_index)
        self.pick("custodyGoldSettleSourceCombo", 1)
        self.type_in("custodyGoldSettleWeightField", weight)

    def type_replace(self, name, text):
        self.click(name)
        QTest.keyClick(self.root, Qt.Key_A, Qt.ControlModifier)
        self.type_text(text)
        QTest.qWait(80)

    def plain(self, text):
        return text.replace("⁦", "").replace("⁩", "")

    def settled_logo(self):
        """The effective outstanding LOGO grams as the SERVICE derives them (the view model only formats it)."""
        custody = self.tvm._service.trip_detail(self.tvm.selectedTripId)["custody"]
        return next(g for g in custody["gold"] if g["designation"] == "LOGO")["outstanding"]

    def reverse_buttons(self):
        self.open_archive()
        found = []

        def collect(node):
            if node.objectName() == "reverseEntryButton" and node.property("visible"):
                found.append(node)
            for child in self.child_items(node):                      # an unexpected non-item child FAILS with its type, index and ancestors
                collect(child)
        collect(self.root.contentItem())
        return found

    # ------------------------------------------------------------ tests
    def test_delivering_logo_gold_with_real_clicks_at_1080_posts_no_scrap_and_refreshes_the_supplier_view(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.logo_trip()                                                                          # 10 g of 21K LOGO in a new custody
        scrap, cash, strips, reloads = len(self.db.scrap), self.db.cash(), self.counts(), self.stub.reloads
        self.assertTrue(self.item("custodyGoldSettleButton").property("enabled"))
        self.open_settle()
        for name in ("custodyGoldSettleSupplierCombo", "custodyGoldSettleSourceCombo", "custodyGoldSettleWeightField", "custodyGoldSettleDateField",
                     "confirmCustodyGoldSettleButton", "cancelCustodyGoldSettleButton"):
            self.assert_inside_window(self.item(name))
        self.assertFalse(self.item("confirmCustodyGoldSettleButton").property("enabled"))        # nothing chosen yet
        self.fill_settle(2, "4")
        self.assertIn("10.000 جرام عيار 21", self.plain(self.text("custodyGoldSettleAvailable")))     # the source availability
        self.assertIn("عيار 18", self.text("custodyGoldSettleSupplierKarat"))                          # the supplier's dedicated karat
        self.assertEqual(self.plain(self.text("custodyGoldSettleDelivered")), "المُسلَّم: 4.000 جرام عيار 21 (لوجو)")
        self.assertEqual(self.text("custodyGoldSettleFormula"), "الحساب: 4.000 × 21 ÷ 18")
        self.assertEqual(self.text("custodyGoldSettleCredited"), "يُحتسب للمورد: 4.667 جرام عيار 18")
        self.assertIn("4.666667", self.text("custodyGoldSettleAnalytical"))
        self.assertTrue(self.item("confirmCustodyGoldSettleButton").property("enabled"))        # no extra confirmation checkbox is needed
        self.click("confirmCustodyGoldSettleButton")
        self.wait_until(lambda: len(self.db.settlements) == 1, "settlement posted")
        self.closed("tripGoldSettleDialog")
        self.wait_idle()
        self.assertEqual((len(self.db.scrap), self.db.cash()), (scrap, cash))                    # NO second Scrap deduction, no cash movement
        self.assertEqual(self.counts(), strips)                                                   # no shop balance moved, so none reloaded
        self.assertEqual(self.settled_logo(), D("6.000"))                                         # the custody gold left the custody
        header = next(iter(self.db.settlements.values()))
        self.assertEqual((header["source_kind"], header["delivered_weight_grams"], header["credited_weight_grams"]), ("CUSTODY_GOLD", D("4.000"), D("4.667")))
        what = self.plain(" ".join(self.visible_texts("entryWhat")))
        self.assertIn("SG000001", what)
        self.assertIn("سُلِّم 4.000 جرام عيار 21 (لوجو) ← احتُسب 4.667 جرام عيار 18", what)       # delivered beside credited, in the custody history
        self.wait_until(lambda: self.stub.reloads > reloads, "supplier view reloaded")            # the open supplier view re-reads the database
        self.assertIn("SG000001", self.tvm.message)
        self.assertEqual(self.warnings, [])

    def test_a_weight_above_the_source_or_an_invalid_weight_is_explained_and_cannot_be_submitted(self):
        self.logo_trip()
        self.open_settle()
        self.fill_settle(2, "10.001")                                                             # one milligram more than the custody holds
        self.assertIsNotNone(self.visible_item("custodyGoldSettleShort"))
        self.assertFalse(self.item("confirmCustodyGoldSettleButton").property("enabled"))
        self.type_replace("custodyGoldSettleWeightField", "10")                                   # exactly everything is allowed
        self.assertIsNone(self.visible_item("custodyGoldSettleShort"))
        self.assertTrue(self.item("confirmCustodyGoldSettleButton").property("enabled"))
        for bad in ("abc", "0", "1.2345"):
            self.type_replace("custodyGoldSettleWeightField", bad)
            self.assertFalse(self.item("confirmCustodyGoldSettleButton").property("enabled"), bad)
            self.assertIsNotNone(self.visible_item("custodyGoldSettlePreviewError"), bad)
            self.assertIsNone(self.visible_item("custodyGoldSettleCredited"), bad)
        self.assertEqual(self.db.settlements, {})

    def test_a_refusal_keeps_every_field_and_a_corrected_retry_posts_once(self):
        self.logo_trip()
        self.open_settle()
        self.fill_settle(2, "3")
        self.type_in("custodyGoldSettleReferenceField", "REF-9")
        self.type_in("custodyGoldSettleNotesField", "ملاحظة")
        self.authorization.permissions = OWNER - {"SUPPLIER_GOLD_SETTLE"}                          # revoked in the database after the dialog opened
        self.click("confirmCustodyGoldSettleButton")
        self.wait_until(lambda: self.tvm.errorMessage != "", "refusal")
        QTest.qWait(100)
        self.assertTrue(self.item("tripGoldSettleDialog").property("visible"))                     # still open
        self.assertTrue(self.text("custodyGoldSettleError"))
        self.assertEqual((self.text("custodyGoldSettleWeightField"), self.text("custodyGoldSettleReferenceField"), self.text("custodyGoldSettleNotesField")),
                         ("3", "REF-9", "ملاحظة"))
        self.assertEqual((self.item("custodyGoldSettleSupplierCombo").property("currentIndex"), self.item("custodyGoldSettleSourceCombo").property("currentIndex")), (2, 1))
        self.assertEqual((self.db.settlements, self.db.gold_settle_links), ({}, {}))
        self.authorization.permissions = OWNER
        self.click("confirmCustodyGoldSettleButton")
        self.wait_until(lambda: len(self.db.settlements) == 1, "settlement posted")
        self.closed("tripGoldSettleDialog")
        self.assertEqual(next(iter(self.db.settlements.values()))["reference"], "REF-9")

    def test_a_double_click_posts_exactly_once(self):
        self.logo_trip()
        self.open_settle()
        self.fill_settle(1, "2")
        button = self.item("confirmCustodyGoldSettleButton")
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)                          # a double click is one request
        self.wait_until(lambda: len(self.db.settlements) >= 1, "settlement posted")
        self.closed("tripGoldSettleDialog")
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual((len(self.db.settlements), len(self.db.sup_gold), self.settled_logo()), (1, 1, D("8.000")))

    def test_the_reversal_from_the_custody_history_explains_the_effect_and_refreshes_the_supplier_view(self):
        self.logo_trip()
        self.open_settle()
        self.fill_settle(2, "4")
        self.click("confirmCustodyGoldSettleButton")
        self.wait_until(lambda: len(self.db.settlements) == 1, "settlement posted")
        self.closed("tripGoldSettleDialog")
        self.wait_idle()
        reloads, scrap = self.stub.reloads, len(self.db.scrap)
        buttons = self.reverse_buttons()
        self.assertEqual(len(buttons), 2)                                                         # the issue and the settlement
        self.reveal(buttons[-1])
        self.assert_inside_window(buttons[-1])
        self.click(buttons[-1])
        self.wait_until(lambda: self.item("tripReasonDialog").property("opened"), "reversal dialog")
        self.assertIn("عاد فعلًا إلى عهدة السوق", self.text("tripReasonNote"))                      # the gold was actually recovered into custody
        self.assertFalse(self.item("confirmTripReasonButton").property("enabled"))                # a reason is mandatory
        self.type_in("tripReasonField", "تسوية بالخطأ")
        self.click("confirmTripReasonButton")
        self.wait_until(lambda: len(self.db.corrections) == 1, "reversed")
        self.closed("tripReasonDialog")
        self.wait_idle()
        self.assertEqual(self.settled_logo(), D("10.000"))                                        # the stored delivered grams are back in custody
        self.assertEqual(sum((m["weight_delta"] for m in self.db.sup_gold), D("0.000")), D("0.000"))   # and the stored credit left the supplier account
        self.assertEqual(len(self.db.scrap), scrap)                                               # still no Scrap movement
        self.assertIn("معكوسة بالعملية RV000001", " ".join(self.visible_texts("entryState")))
        self.wait_until(lambda: self.stub.reloads > reloads, "supplier view reloaded after the reversal")
        self.assertEqual(self.warnings, [])

    def test_the_dialog_fits_every_target_size(self):
        self.logo_trip()
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(150)
                self.open_settle()
                self.fill_settle(2, "4")
                for name in ("custodyGoldSettleSupplierCombo", "custodyGoldSettleSourceCombo", "custodyGoldSettleWeightField", "custodyGoldSettleDateField",
                             "custodyGoldSettleCredited", "confirmCustodyGoldSettleButton", "cancelCustodyGoldSettleButton"):
                    self.assert_inside_window(self.item(name))
                dialog = self.item("tripGoldSettleDialog")
                self.assertLessEqual(dialog.property("height"), height)
                self.assertLessEqual(dialog.property("width"), width)
                dialog.close()
                self.closed("tripGoldSettleDialog")
        self.assertEqual(self.warnings, [])

    def test_melt_designated_gold_cannot_be_settled_so_the_action_is_not_available(self):
        self.open_page()
        self.new_trip("", [(2, "30.000")])                                                        # 21K, MELT
        self.wait_until(lambda: self.tvm.detail.get("meltAvailable"), "melting available")
        self.assertFalse(self.tvm.detail["goldSettleAvailable"])
        self.reveal(self.item("custodyGoldSettleButton"))
        self.assertFalse(self.item("custodyGoldSettleButton").property("enabled"))

    def test_a_settled_custody_shows_the_delivered_grams_and_closes_only_at_exactly_zero(self):
        self.logo_trip()
        self.tvm._service.settle_supplier_gold("1", "11", "1", "LOGO", "10", TODAY, "", "", lkey(1))
        self.tvm.refresh()
        self.wait_idle()
        self.assertEqual(self.tvm.detail["gold"][0]["settled"], "10.000")
        row = self.item("custodyGoldRow_21_LOGO")                                                  # the custody figure row shows the delivered grams
        self.assertIn("سُلِّم لمورد 10.000 جرام", " ".join(str(child.property("text")) for child in row.childItems()))
        self.assertEqual(self.settled_logo(), D("0.000"))
        self.assertTrue(self.item("closeTripButton").property("enabled"))                          # exactly zero outstanding: it closes
        self.assertFalse(self.item("custodyGoldSettleButton").property("enabled"))                 # nothing left to deliver


# The base classes carry their own tests: only this module's tests are collected here.
for _inherited in [n for n in dir(harness.InvoicePageTests) if n.startswith("test_")] + \
                  [n for n in dir(base.MarketTripHeatPageTests) if n.startswith("test_") and n not in vars(GoldSettlePageTests)]:
    setattr(GoldSettlePageTests, _inherited, None)


if __name__ == "__main__":
    unittest.main()
