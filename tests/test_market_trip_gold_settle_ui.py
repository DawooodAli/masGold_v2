"""Supplier gold settlement from market custody (046, «تسوية ذهب من العهدة») on the real Main.qml, with real mouse clicks at the three target
sizes. The services are the real ones over the in-memory database (tests/market_trip_fakes.py); the supplier page is a counting stub, so the
post-commit refresh of the supplier views is observable. NOT real-MySQL tests."""
from decimal import Decimal as D
from unittest.mock import patch
import unittest

from PySide6.QtCore import QObject, QPointF, Qt
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
        self.reveal(buttons[0])
        self.assert_inside_window(buttons[0])
        self.click(buttons[0])
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
        self.new_trip("", [(2, "30.000")], designation="MELT")                                 # 21K, MELT (the issue dialog has no designation: created through the view model)
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


class DistributionPageTests(GoldSettlePageTests):
    """Settlement step 2b on the real page (owner, 2026-10-20): the SAME «تسوية ذهب من العهدة» dialog also offers the unmelted gold (its OWN karat) and the melted bar (21K
    equivalent) as sources. Real clicks at 1080x680; the services are the real ones over the in-memory database."""

    def melted_trip(self):
        self.open_page()
        self.new_trip("", [(2, "50.000"), (3, "10.000")])                                          # 21K 50 g and 18K 10 g, NO designation (the stage 1 dialog)
        trip = self.tvm.selectedTripId
        self.tvm._service.settle_melt(trip, [{"karat_id": "1", "weight": "40"}, {"karat_id": "2", "weight": "5"}], "44.8", "850", "ورشة", TODAY, "",
                                      self.tvm.newRequestKey())                                  # bar 43.520 (21K eq); raw left 21K 10 g, 18K 5 g
        self.tvm.refresh()
        self.wait_until(lambda: self.tvm.detail.get("barVisible"), "bar pool shown")
        self.wait_until(lambda: self.tvm.detail.get("goldSettleAvailable"), "delivery available")

    def test_the_bar_and_the_unmelted_gold_are_delivered_with_real_clicks_at_1080(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.melted_trip()
        scrap, cash = len(self.db.scrap), self.db.cash()
        self.open_settle()
        for name in ("custodyGoldSettleSupplierCombo", "custodyGoldSettleSourceCombo", "custodyGoldSettleWeightField", "confirmCustodyGoldSettleButton"):
            self.assert_inside_window(self.item(name))
        sources = [s["text"] for s in self.tvm.detail["goldSettleSources"]]
        self.assertEqual(len(sources), 3)                                                          # 21K raw, 18K raw, the bar
        self.assertIn("السبيكة", sources[2])
        self.fill_settle(supplier_index=1, weight="18.8")                                          # supplier 10 (21K); source index 1 = 21K raw: change to the BAR
        self.pick("custodyGoldSettleSourceCombo", 3)
        QTest.qWait(150)
        self.click("confirmCustodyGoldSettleButton")
        self.wait_until(lambda: len(self.db.settlements) == 1, "bar delivered")
        self.closed("tripGoldSettleDialog")
        self.wait_idle()
        header = next(iter(self.db.settlements.values()))
        self.assertEqual((header["source_kind"], header["delivered_weight_grams"], header["credited_weight_grams"]), ("TRIP_BAR", D("18.800"), D("18.800")))
        self.wait_until(lambda: "24.720" in self.tvm.detail["bar"], "bar pool reduced")
        self.assertEqual((len(self.db.scrap), self.db.cash()), (scrap, cash))                       # no Scrap and no cash movement
        self.open_settle()                                                                         # the unmelted 21K gold to the 18K supplier, in its own karat
        self.pick("custodyGoldSettleSupplierCombo", 2)
        self.pick("custodyGoldSettleSourceCombo", 1)
        self.type_in("custodyGoldSettleWeightField", "10")
        QTest.qWait(150)
        self.click("confirmCustodyGoldSettleButton")
        self.wait_until(lambda: len(self.db.settlements) == 2, "unmelted gold delivered")
        self.closed("tripGoldSettleDialog")
        second = sorted(self.db.settlements.values(), key=lambda h: h["id"])[1]
        self.assertEqual((second["source_kind"], second["delivered_karat_value"], second["credited_weight_grams"]), ("CUSTODY_GOLD", 21, D("11.667")))
        self.assertEqual(self.warnings, [])


    def test_the_bar_is_handed_to_a_marketer_with_real_clicks_at_1080(self):
        from tests.marketer_fakes import MarketerRepo, prepare
        prepare(self.db)
        started = patch("repositories.marketer_custody_repository.MarketerCustodyRepository", MarketerRepo)
        started.start()
        self.addCleanup(started.stop)
        self.grant("INTERMEDIARY_HOLDING_VIEW", "INTERMEDIARY_HOLDING_MANAGE", "INTERMEDIARY_HOLDING_REVERSE")
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.melted_trip()
        self.tvm.refresh()
        self.wait_until(lambda: self.tvm.detail.get("barMarketerAvailable"), "bar hand-over available")
        scrap, cash, settlements = len(self.db.scrap), self.db.cash(), len(self.db.settlements)
        self.reveal(self.item("barMarketerButton"))
        self.click("barMarketerButton")
        self.wait_until(lambda: self.item("tripBarMarketerDialog").property("opened"), "bar marketer dialog")
        self.wait_until(lambda: len(self.tvm.context.get("holdParties", [])) >= 1, "marketers loaded")
        QTest.qWait(120)
        for name in ("barMarketerPartyCombo", "barMarketerWeightField", "barMarketerDateField", "confirmBarMarketerButton", "cancelBarMarketerButton"):
            self.assert_inside_window(self.item(name))
        self.assertFalse(self.item("confirmBarMarketerButton").property("enabled"))                  # nothing chosen yet
        self.pick("barMarketerPartyCombo", 1)
        self.type_in("barMarketerWeightField", "20")
        QTest.qWait(150)
        self.assertIn("43.520", self.plain(self.text("barMarketerFacts")))                  # the pool shown before the hand-over
        self.click("confirmBarMarketerButton")
        self.wait_until(lambda: len(self.db.mk_gold) == 1, "handed over")
        self.closed("tripBarMarketerDialog")
        self.wait_idle()
        leg = self.db.mk_gold[0]
        self.assertEqual((leg["movement_type"], leg["trip_entry_kind"], leg["weight_delta"]), ("TRIP_BOOK_IN", "ST_BAR_MKT", D("20.000")))
        self.wait_until(lambda: "23.520" in self.tvm.detail["bar"], "bar pool reduced")
        self.assertEqual((len(self.db.scrap), self.db.cash(), len(self.db.settlements)), (scrap, cash, settlements))        # no Scrap, cash or supplier movement
        self.assertEqual(self.warnings, [])


    def test_the_remainder_is_classified_as_logo_with_real_clicks_and_the_logo_flows_open(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.melted_trip()
        self.wait_until(lambda: self.tvm.detail.get("classifyLogoAvailable"), "classification available")
        self.reveal(self.item("classifyLogoButton"))
        self.click("classifyLogoButton")
        self.wait_until(lambda: self.item("tripClassifyLogoDialog").property("opened"), "classify dialog")
        QTest.qWait(120)
        for name in ("classifyLogoDateField", "classifyLogoNotesField", "confirmClassifyLogoButton", "cancelClassifyLogoButton"):
            self.assert_inside_window(self.item(name))
        self.assertIn("عيار 21: 10.000", self.plain(self.text("classifyLogoLines")))
        scrap, cash, settlements = len(self.db.scrap), self.db.cash(), len(self.db.settlements)
        self.click("confirmClassifyLogoButton")
        self.wait_until(lambda: any(e["entry_kind"] == "ST_LOGO" for e in self.db.entries.values()), "classified")
        self.closed("tripClassifyLogoDialog")
        self.wait_idle()
        gold = {(g["karat"], g["designation"]): g for g in self.tvm.detail["gold"]}
        self.assertEqual((gold[(21, "LOGO")]["outstanding"], gold[(21, "UNCLASSIFIED")]["outstanding"]), ("10.000", "0.000"))
        self.assertFalse(self.tvm.detail["classifyLogoAvailable"])                                   # nothing left to classify
        self.assertEqual((len(self.db.scrap), self.db.cash(), len(self.db.settlements)), (scrap, cash, settlements))
        self.assertEqual(self.warnings, [])


    def test_the_bar_is_sold_with_real_clicks_and_the_proceeds_enter_the_custody_cash(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.melted_trip()
        self.tvm._service._price_reference = lambda: {21: D("6000.0000")}
        scrap, cash_moves = len(self.db.scrap), len(self.db.cash_moves)
        self.reveal(self.item("custodySaleButton"))
        self.click("custodySaleButton")
        self.wait_until(lambda: self.item("tripGoldSaleDialog").property("opened"), "sale dialog")
        self.wait_until(lambda: "buyers" in self.tvm._context and self.tvm._reference_prices, "context and price reference loaded")
        QTest.qWait(120)
        sources = [s["text"] for s in self.tvm.detail["saleSources"]]
        self.assertTrue(any("سبيكة السبك" in t for t in sources))
        self.pick("custodyGoldSaleSourceCombo", len(sources))                                      # the bar is the last gold source (index 0 is the placeholder)
        self.type_in("custodyGoldSaleWeightField", "10")
        self.type_in("custodyGoldSalePriceField", "60000")
        self.type_in("custodyGoldSaleNameField", "تاجر الصاغة")
        QTest.qWait(150)
        for name in ("custodyGoldSaleSourceCombo", "custodyGoldSaleWeightField", "custodyGoldSalePriceField", "confirmCustodyGoldSaleButton"):
            self.assert_inside_window(self.item(name))
        self.click("confirmCustodyGoldSaleButton")
        self.wait_until(lambda: len(self.db.gold_sales) == 1, "bar sale posted")
        self.closed("tripGoldSaleDialog")
        self.wait_idle()
        sale = next(iter(self.db.gold_sales.values()))
        self.assertEqual((sale["source_kind"], sale["weight_grams"], sale["total_price"]), ("TRIP_BAR", D("10.000"), D("60000.00")))
        self.wait_until(lambda: "33.520" in self.tvm.detail["bar"], "bar pool reduced")
        self.assertEqual((len(self.db.scrap), len(self.db.cash_moves)), (scrap, cash_moves))       # nothing in Scrap, Main Safe or a bank
        self.assertEqual(self.warnings, [])


    def open_cash_settle(self):
        self.reveal(self.item("custodyCashSettleButton"))
        self.click("custodyCashSettleButton")
        self.wait_until(lambda: self.item("tripCashSettleDialog").property("opened"), "cash settlement dialog")
        QTest.qWait(120)

    def test_the_custody_cash_is_settled_with_real_clicks_expense_bank_and_write_off(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.melted_trip()
        self.tvm._service.issue(self.tvm.selectedTripId, "10000", [], TODAY, "", self.tvm.newRequestKey())
        self.tvm.refresh()
        self.wait_until(lambda: self.tvm.detail.get("cashSettleAvailable"), "cash settlement available")
        safe = self.db.cash()
        # 1) a free-text expense
        self.open_cash_settle()
        for name in ("cashSettleKindCombo", "cashSettleAmountField", "cashSettleTextField", "confirmCashSettleButton"):
            self.assert_inside_window(self.item(name))
        self.type_in("cashSettleAmountField", "1500")
        self.type_in("cashSettleTextField", "أجرة نقل")
        QTest.qWait(100)
        self.click("confirmCashSettleButton")
        self.wait_until(lambda: len(self.db.settle_cash) == 1, "expense posted")
        self.closed("tripCashSettleDialog")
        self.wait_idle()
        # 2) a deposit into the bank account
        self.open_cash_settle()
        self.pick("cashSettleKindCombo", 1)
        QTest.qWait(100)
        self.assertIsNone(self.visible_item("cashSettleTextField"))                                # a deposit has no free text
        self.type_in("cashSettleAmountField", "5000")
        self.pick("cashSettleBankCombo", 1)
        QTest.qWait(100)
        self.click("confirmCashSettleButton")
        self.wait_until(lambda: len(self.db.settle_cash) == 2, "deposit posted")
        self.closed("tripCashSettleDialog")
        self.wait_idle()
        self.assertEqual([(m["account_id"], m["amount_delta"]) for m in self.db.bank_moves], [(1, D("5000.00"))])
        # 3) the write-off of the rest needs a reason
        self.open_cash_settle()
        self.pick("cashSettleKindCombo", 2)
        self.type_in("cashSettleAmountField", "3500")
        QTest.qWait(100)
        self.assertFalse(self.item("confirmCashSettleButton").property("enabled"))                  # no reason yet
        self.type_in("cashSettleTextField", "فرق عد")
        QTest.qWait(100)
        self.click("confirmCashSettleButton")
        self.wait_until(lambda: len(self.db.settle_cash) == 3, "write-off posted")
        self.closed("tripCashSettleDialog")
        self.wait_idle()
        self.assertEqual(self.db.cash(), safe)                                                     # Main Safe never moved
        self.wait_until(lambda: self.tvm.detail["cash"]["zero"], "custody cash settled to zero")
        self.assertEqual(self.warnings, [])


    def test_the_remainder_is_returned_lost_and_the_issue_weight_corrected_with_real_clicks(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.melted_trip()
        s21 = self.db.physical(1)
        # 1) market return of part of the bar: it enters Scrap as 21K
        self.reveal(self.item("custodyDisposeButton"))
        self.click("custodyDisposeButton")
        self.wait_until(lambda: self.item("tripDisposeDialog").property("opened"), "dispose dialog")
        QTest.qWait(120)
        sources = [x["text"] for x in self.tvm.detail["disposeSources"]]
        self.assertEqual(len(sources), 3)                                                          # 21K raw, 18K raw, the bar
        for name in ("disposeActionCombo", "disposeSourceCombo", "disposeWeightField", "confirmDisposeButton"):
            self.assert_inside_window(self.item(name))
        self.pick("disposeSourceCombo", 3)
        self.type_in("disposeWeightField", "20")
        QTest.qWait(100)
        self.click("confirmDisposeButton")
        self.wait_until(lambda: len(self.db.settle_lines) == 1, "market return posted")
        self.closed("tripDisposeDialog")
        self.wait_idle()
        self.assertEqual(self.db.physical(1), s21 + D("20.000"))
        self.wait_until(lambda: "23.520" in self.tvm.detail["bar"], "bar pool reduced")
        # 2) a crucible loss from the bar
        self.click("custodyDisposeButton")
        self.wait_until(lambda: self.item("tripDisposeDialog").property("opened"), "dispose dialog again")
        QTest.qWait(120)
        self.pick("disposeActionCombo", 1)
        self.pick("disposeSourceCombo", 3)
        self.type_in("disposeWeightField", "0.5")
        QTest.qWait(100)
        self.click("confirmDisposeButton")
        self.wait_until(lambda: len(self.db.settle_lines) == 2, "loss posted")
        self.closed("tripDisposeDialog")
        self.wait_idle()
        self.assertEqual([l["classification"] for l in self.db.settle_lines], [None, "CRUCIBLE"])
        # 3) the issue weight was higher than recorded: Scrap pays the difference; the reason is mandatory
        self.reveal(self.item("custodyAdjustButton"))
        self.click("custodyAdjustButton")
        self.wait_until(lambda: self.item("tripAdjustDialog").property("opened"), "adjust dialog")
        QTest.qWait(120)
        for name in ("adjustKaratCombo", "adjustDirectionCombo", "adjustWeightField", "adjustReasonField", "confirmAdjustButton"):
            self.assert_inside_window(self.item(name))
        s21 = self.db.physical(1)
        self.pick("adjustKaratCombo", 1)
        self.type_in("adjustWeightField", "2")
        QTest.qWait(100)
        self.assertFalse(self.item("confirmAdjustButton").property("enabled"))                     # no reason yet
        self.type_in("adjustReasonField", "وزن غلط")
        QTest.qWait(100)
        self.click("confirmAdjustButton")
        self.wait_until(lambda: len(self.db.settle_adjusts) == 1, "correction posted")
        self.closed("tripAdjustDialog")
        self.wait_idle()
        self.assertEqual(self.db.physical(1), s21 - D("2.000"))
        self.assertEqual(self.warnings, [])


    def test_the_older_actions_are_hidden_by_default_and_the_new_workflow_stays_visible(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.melted_trip()
        page = self.root.findChild(QObject, "marketTripsPage")
        page.setProperty("advancedActions", False)                                                  # the production default
        QTest.qWait(150)
        for name in ("lotIssueButton", "lotReturnButton", "meltButton", "heatButton", "classifyLogoButton", "barMarketerButton", "custodyLotSettleButton",
                     "custodyLogoHoldButton", "custodyLotHoldButton"):
            self.assertIsNone(self.visible_item(name), name)
        for name in ("settleMeltButton", "custodyDisposeButton", "custodySaleButton", "custodyGoldBuyButton", "custodyAdjustButton", "custodyGoldSettleButton",
                     "custodyCashSettleButton", "custodyPayButton", "issueMoreButton", "returnButton", "closeTripButton"):
            self.assertIsNotNone(self.visible_item(name), name)
        self.assertIsNotNone(self.visible_item("goldActions"))
        self.assertIsNotNone(self.visible_item("cashActions"))
        page.setProperty("advancedActions", True)
        QTest.qWait(150)
        self.assertIsNotNone(self.visible_item("meltButton"))                                       # one switch brings them back


# The base classes carry their own tests: only this module's tests are collected here.
for _inherited in [n for n in dir(harness.InvoicePageTests) if n.startswith("test_")] + \
                  [n for n in dir(base.MarketTripHeatPageTests) if n.startswith("test_") and n not in vars(GoldSettlePageTests)]:
    setattr(GoldSettlePageTests, _inherited, None)
for _gold in [n for n in dir(GoldSettlePageTests) if n.startswith("test_") and getattr(GoldSettlePageTests, n) is not None]:
    setattr(DistributionPageTests, _gold, None)                                          # the distribution class runs only its own tests


if __name__ == "__main__":
    unittest.main()
