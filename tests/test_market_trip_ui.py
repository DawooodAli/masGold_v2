"""Market trips on the real Main.qml: the OWNER-only navigation, the issue / return / close / reopen / reverse flow with real mouse
clicks at the three target sizes, retained input on refusal, no double submission, and the post-commit refresh of the money views
(top-bar balances, wallet / bank balances, الحسابات المالية, the Scrap consumers) without the periodic timer. The services are the
real ones over the in-memory database; the balance services are counting stand-ins that re-read the same ledgers."""
import time
from decimal import Decimal as D
from unittest.mock import patch
import unittest

from PySide6.QtCore import QMetaObject, QObject, QPointF, Qt
from PySide6.QtTest import QTest

from app.formatting import money
from tests import test_invoice_page_qml as harness
from tests.market_trip_fakes import AccountsRepo, Corrections, Customers, Invoices, OWNER, ScrapRepo, TripRepo, build

TODAY = "2026-10-10"


class Ledgers:
    """Balance services reading the fake database's ledgers; every read is counted."""

    def __init__(self, db):
        self.db = db
        self.strip_calls = self.account_calls = self.view_calls = self.scrap_calls = 0
        self.slow_first_strip = 0.0

    def can_view(self):
        return True

    def strip(self, **kwargs):
        self.strip_calls += 1
        value, first = self.db.cash(), self.strip_calls == 1
        if first and self.slow_first_strip:
            time.sleep(self.slow_first_strip)
        return {"cash_balance": str(value), "old_gold": []}

    def financial_accounts(self, **kwargs):
        self.account_calls += 1
        return []

    def cash_view(self, start="", end="", label="", **kwargs):
        self.view_calls += 1
        return {"account": {"name": "Main Safe", "balance": str(self.db.cash())}}

    def get_balances(self):
        self.scrap_calls += 1
        return []


class MarketTripPageTests(harness.InvoicePageTests):
    def setUp(self):
        super().setUp()
        vms = self.controller.operational_viewmodels
        self.tvm, self.balances, self.reviews, self.accounts = vms["marketTripVM"], vms["balancesVM"], vms["reviewsVM"], vms["financialAccountsVM"]
        self.conversion = vms["oldGoldConversionVM"]
        session = self.controller.session
        self.db, _, self.authorization, service, _ = build(session=session)
        self.tvm._service = service
        for target, value in (("repositories.market_trip_repository.MarketTripRepository", TripRepo),
                              ("repositories.financial_account_repository.FinancialAccountRepository", AccountsRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)
        self.ledgers = Ledgers(self.db)
        self.balances._timer.stop()                                              # the periodic refresh must play no part
        self.balances._service = self.ledgers
        self.reviews._balance_service = self.ledgers
        self.accounts._service = self.ledgers
        self.conversion._service = self.ledgers

    # ------------------------------------------------------------ helpers
    def owner(self):
        self.grant(*OWNER)
        self.tvm.changed.emit()
        QTest.qWait(60)

    def open_page(self):
        self.owner()
        self.root.setProperty("currentPage", 15)
        QTest.qWait(250)
        self.wait_idle()

    def wait_idle(self):
        self.wait_until(lambda: not self.tvm.busy, "trip view model idle")
        self.wait_until(lambda: not self.balances._workers and not self.reviews._workers and not self.accounts._workers, "refreshes done")
        QTest.qWait(80)

    def type_in(self, name, text):
        self.click(name)
        self.type_text(text)
        QTest.qWait(50)

    def pick(self, name, index):
        combo = self.item(name)
        self.click(combo)
        QTest.qWait(80)
        for child in combo.findChildren(QObject):
            if child.metaObject().indexOfMethod("close()") >= 0 and child.property("visible"):
                QMetaObject.invokeMethod(child, "close")
                QTest.qWait(150)
        combo.setProperty("currentIndex", index)
        combo.activated[int].emit(index)
        QTest.qWait(150)

    def closed(self, name):
        self.wait_until(lambda: not self.item(name).property("visible"), f"{name} closed")

    def counts(self):
        return (self.ledgers.strip_calls, self.ledgers.account_calls, self.ledgers.scrap_calls)

    def new_trip(self, cash="10000", gold=None):
        """Create a trip through the dialog with real clicks; gold is [(combo index, weight)]."""
        self.click("newTripButton")
        self.wait_until(lambda: self.item("tripEntryDialog").property("opened"), "entry dialog")
        self.wait_until(lambda: self.tvm.context.get("karats"), "context")
        self.type_in("tripCashField", cash)
        for i, (index, weight) in enumerate(gold or []):
            if i:
                self.click("addGoldRowButton")
            self.pick(f"tripGoldKarat_{i}", index)
            self.pick(f"tripGoldDesignation_{i}", 1)                                    # للسبك: every gold line needs its designation
            self.type_in(f"tripGoldWeight_{i}", weight)
        self.click("confirmTripEntryButton")
        self.wait_until(lambda: len(self.db.trips) >= 1, "trip created")
        self.closed("tripEntryDialog")
        self.wait_idle()

    def visible_texts(self, name, parent=None):
        parent = parent or self.root.contentItem()
        found = [str(parent.property("text"))] if parent.objectName() == name and parent.property("visible") else []
        for child in parent.childItems():
            found += self.visible_texts(name, child)
        return found

    # ------------------------------------------------------------ tests
    def test_only_the_owner_sees_the_navigation_and_the_page(self):
        self.assertIsNone(self.visible_item("marketTripsNavButton"))
        self.owner()
        self.assertIsNotNone(self.visible_item("marketTripsNavButton"))
        self.grant(revoke=("MARKET_TRIP_MANAGE",))
        self.tvm.changed.emit()
        QTest.qWait(80)
        self.assertIsNone(self.visible_item("marketTripsNavButton"))
        self.assertEqual(self.warnings, [])

    def test_the_whole_flow_with_real_clicks_at_1080(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.open_page()
        self.assert_inside_window(self.item("newTripButton"))
        before = self.counts()
        self.new_trip("10000", [(2, "20.000"), (3, "5.500")])                      # entry 1: 21K and 18K (entries: placeholder, 24K, 21K, 18K)
        self.assertEqual(self.counts()[0], before[0] + 1)                           # the money views reloaded once, no timer
        self.assertEqual(self.balances.cashText, money(D("40000.00")))
        self.assertEqual((self.db.cash(), self.db.physical(1), self.db.physical(2)), (D("40000.00"), D("80.000"), D("44.500")))
        self.wait_until(lambda: self.tvm.detail.get("id"), "trip selected")
        self.assertEqual(self.text("tripTitle").replace("⁦", "").replace("⁩", ""), "عهدة MT000001")
        self.assertIn("إرجاع كل النقدية وكل الذهب", self.text("tripLimitation"))                  # the limitation is explained
        outstanding = " | ".join(self.visible_texts("figureOutstanding"))
        for part in ("10,000.00 ج", "20.000 جرام", "5.500 جرام"):                                 # custody per karat, apart from the shop
            self.assertIn(part, outstanding)
        self.assertFalse(self.item("closeTripButton").property("enabled"))                        # outstanding custody
        self.assertIn("لا يمكن الإغلاق", self.text("closeHint"))
        self.assertIn("10,000.00", self.text("closeHint"))
        self.assertTrue(self.text("shopCash").startswith("الخزنة الرئيسية: "))                    # shop balance apart from custody
        self.assertEqual(self.text("tripMessage"), "تم إخراج العهدة MT000001-1")
        # ---- a partial return
        self.click("returnButton")
        self.wait_until(lambda: self.item("tripEntryDialog").property("opened"), "return dialog")
        self.type_in("tripCashField", "3000")
        for name in ("confirmTripEntryButton", "cancelTripEntryButton", "tripCashField", "tripDateField", "tripNotesField", "addGoldRowButton"):
            self.assert_inside_window(self.item(name))
        self.click("confirmTripEntryButton")
        self.wait_until(lambda: len(self.db.entries) == 2, "return posted")
        self.closed("tripEntryDialog")
        self.wait_idle()
        self.assertEqual(self.db.cash(), D("43000.00"))
        self.assertEqual(self.balances.cashText, money(D("43000.00")))
        self.assertEqual(self.warnings, [])

    def test_a_refusal_keeps_every_field_and_a_double_click_posts_once(self):
        self.open_page()
        self.click("newTripButton")
        self.wait_until(lambda: self.item("tripEntryDialog").property("opened"), "entry dialog")
        self.wait_until(lambda: self.tvm.context.get("karats"), "context")
        self.pick("tripGoldKarat_0", 1)                                                    # 24K: no Scrap at all
        self.type_in("tripGoldWeight_0", "5")
        self.click("confirmTripEntryButton")                                              # no designation chosen yet
        self.wait_until(lambda: self.tvm.errorMessage != "", "designation refusal")
        self.assertIn("وجهة الذهب", self.text("tripEntryError"))
        self.pick("tripGoldDesignation_0", 2)                                              # لوجو
        self.type_in("tripCashField", "100")
        self.type_in("tripNotesField", "ملاحظة")
        self.click("confirmTripEntryButton")
        self.wait_until(lambda: self.tvm.errorMessage != "", "refusal")
        QTest.qWait(100)
        self.assertTrue(self.item("tripEntryDialog").property("visible"))
        self.assertIn("غير كافٍ", self.text("tripEntryError"))
        self.assertEqual((self.text("tripCashField"), self.text("tripGoldWeight_0"), self.text("tripNotesField")), ("100", "5", "ملاحظة"))
        self.assertEqual(self.item("tripGoldDesignation_0").property("currentIndex"), 2)       # the choice is kept too
        self.assertEqual((self.db.trips, self.db.entries), ({}, {}))
        self.pick("tripGoldKarat_0", 2)                                                    # 21K has grams: the corrected retry
        button = self.item("confirmTripEntryButton")
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
        self.wait_until(lambda: len(self.db.entries) >= 1, "entry posted")
        self.closed("tripEntryDialog")
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual((len(self.db.trips), len(self.db.entries), self.db.cash()), (1, 1, D("49900.00")))

    def test_close_reopen_and_reversal_through_the_dialogs(self):
        self.open_page()
        self.new_trip("500", [])
        self.click("returnButton")
        self.wait_until(lambda: self.item("tripEntryDialog").property("opened"), "return dialog")
        self.type_in("tripCashField", "500")
        self.click("confirmTripEntryButton")
        self.wait_until(lambda: len(self.db.entries) == 2, "full return")
        self.closed("tripEntryDialog")
        self.wait_idle()
        self.assertTrue(self.item("closeTripButton").property("enabled"))
        self.assertIn("يمكن إغلاق", self.text("closeHint"))
        self.click("closeTripButton")
        self.wait_until(lambda: self.db.trips[1]["status"] == "CLOSED", "closed")
        self.wait_idle()
        self.assertEqual(self.text("tripStatus"), "مغلقة")
        self.assertIsNone(self.visible_item("issueMoreButton"))                              # a closed trip offers no entry action
        self.assertIsNone(self.visible_item("reverseEntryButton"))
        self.click("reopenTripButton")
        self.wait_until(lambda: self.item("tripReasonDialog").property("opened"), "reason dialog")
        self.assertFalse(self.item("confirmTripReasonButton").property("enabled"))           # a reason is mandatory
        self.type_in("tripReasonField", "مراجعة")
        self.click("confirmTripReasonButton")
        self.wait_until(lambda: self.db.trips[1]["status"] == "OPEN", "reopened")
        self.closed("tripReasonDialog")
        self.wait_idle()
        self.assertIsNotNone(self.visible_item("reverseEntryButton"))
        before = self.counts()
        buttons = []

        def collect(node):
            if node.objectName() == "reverseEntryButton" and node.property("visible"):
                buttons.append(node)
            for child in node.childItems():
                collect(child)
        collect(self.root.contentItem())
        self.assertEqual(len(buttons), 2)
        self.click(buttons[-1])                                                           # the return (newest entry)
        self.wait_until(lambda: self.item("tripReasonDialog").property("opened"), "reversal dialog")
        self.type_in("tripReasonField", "مرتجع بالخطأ")
        self.click("confirmTripReasonButton")
        self.wait_until(lambda: len(self.db.corrections) == 1, "reversed")
        self.closed("tripReasonDialog")
        self.wait_idle()
        self.assertEqual(self.db.cash(), D("49500.00"))
        self.assertGreater(self.counts()[0], before[0])                                     # the money views reloaded
        self.assertIn("معكوسة بالعملية RV000001", " ".join(self.visible_texts("entryState")))
        self.assertEqual(self.warnings, [])

    def test_every_money_view_reloads_once_after_a_commit_and_never_after_a_refusal(self):
        self.accounts.activate()
        self.wait_idle()
        self.open_page()
        before = (self.ledgers.strip_calls, self.ledgers.account_calls, self.ledgers.view_calls, self.ledgers.scrap_calls)
        self.tvm.issue("", "700", [], TODAY, "", self.tvm.newRequestKey())
        self.wait_until(lambda: len(self.db.entries) == 1, "posted")
        self.wait_idle()
        after = (self.ledgers.strip_calls, self.ledgers.account_calls, self.ledgers.view_calls, self.ledgers.scrap_calls)
        self.assertEqual(tuple(a - b for a, b in zip(after, before)), (1, 1, 1, 1))
        self.tvm.issue("", "", [{"karatId": "3", "weight": "1", "designation": "MELT"}], TODAY, "", self.tvm.newRequestKey())   # refused: no 24K
        self.wait_idle()
        self.assertEqual((self.ledgers.strip_calls, self.ledgers.account_calls, self.ledgers.view_calls, self.ledgers.scrap_calls), after)

    def test_a_refresh_requested_during_a_running_load_ends_with_a_fresh_read(self):
        self.open_page()
        self.ledgers.slow_first_strip = 0.7
        self.ledgers.strip_calls = 0
        self.balances.refresh()                                                          # reads the OLD value, slowly
        QTest.qWait(80)
        self.tvm.issue("", "1000", [], TODAY, "", self.tvm.newRequestKey())
        self.wait_until(lambda: self.ledgers.strip_calls >= 2, "second read")
        QTest.qWait(1000)
        self.assertEqual(self.balances.cashText, money(D("49000.00")))                     # the stale result did not win

    def test_a_failing_balance_refresh_is_not_a_failed_trip_entry(self):
        self.open_page()

        def broken(**kwargs):
            raise RuntimeError("balance read failed")
        self.ledgers.strip = broken
        self.tvm.issue("", "100", [], TODAY, "", self.tvm.newRequestKey())
        self.wait_until(lambda: len(self.db.entries) == 1, "posted")
        self.wait_idle()
        self.assertEqual(self.tvm.errorMessage, "")
        self.assertIn("MT000001-1", self.tvm.message)
        self.assertEqual(len(self.db.entries), 1)

    def test_dialogs_fit_at_every_target_size(self):
        self.open_page()
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(150)
                self.assert_inside_window(self.item("newTripButton"))
                self.click("newTripButton")
                self.wait_until(lambda: self.item("tripEntryDialog").property("opened"), "entry dialog")
                self.wait_until(lambda: self.tvm.context.get("karats"), "context")
                for name in ("tripCashField", "tripDateField", "tripGoldKarat_0", "tripGoldDesignation_0", "tripGoldWeight_0", "addGoldRowButton", "tripNotesField",
                             "confirmTripEntryButton", "cancelTripEntryButton"):
                    self.assert_inside_window(self.item(name))
                self.item("tripEntryDialog").close()
                self.closed("tripEntryDialog")


class MarketTripMeltingPageTests(MarketTripPageTests):
    """Melting on the real page: designation, mixed inputs, fineness, residues, refusals that keep every field, permanent analysis,
    reversal - with real mouse clicks at the three target sizes."""

    def start_trip(self):
        self.open_page()
        self.new_trip("", [(2, "30.000")])                                              # 21K 30 g, MELT
        self.wait_until(lambda: self.tvm.detail.get("meltAvailable"), "melting available")

    def reveal(self, item):
        """Scroll the detail Flickable so that `item` is on screen (the employee's mouse wheel); clicking stays real."""
        node = item.parentItem()
        while node is not None and node.property("contentY") is None:
            node = node.parentItem()
        if node is not None:
            node.setProperty("contentY", max(0, item.mapToItem(node.property("contentItem"), QPointF(0, 0)).y() - 40))
            QTest.qWait(100)

    def open_melt(self):
        self.click("meltButton")
        self.wait_until(lambda: self.item("tripMeltDialog").property("opened"), "melt dialog")

    def fill_melt(self, weight="10", output="9.5", fineness="875.5", residue=("0.4", ""), workshop="ورشة النور"):
        self.type_in("meltWorkshopField", workshop)
        self.pick("meltInputKarat_0", 1)
        self.type_in("meltInputWeight_0", weight)
        self.type_in("meltOutputWeight", output)
        self.type_in("meltOutputFineness", fineness)
        if residue:
            self.click("addResidueButton")
            self.type_in("meltResidueWeight_0", residue[0])
            if residue[1]:
                self.type_in("meltResidueFineness_0", residue[1])
            self.type_in("meltResidueLabel_0", "خبث")

    def test_melting_with_real_clicks_shows_the_permanent_analysis_and_the_incomplete_marker(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.start_trip()
        self.assertTrue(self.item("meltButton").property("enabled"))
        self.open_melt()
        for name in ("meltDateField", "meltWorkshopField", "meltInputKarat_0", "meltInputWeight_0", "addMeltInputButton",
                     "meltOutputWeight", "meltOutputFineness", "addResidueButton", "meltGainField", "meltAssayField", "meltNotesField",
                     "confirmMeltButton", "cancelMeltButton"):
            with self.subTest(control=name):
                self.item(name)                                                          # reachable controls (scrolled form + fixed buttons)
        for name in ("confirmMeltButton", "cancelMeltButton"):
            self.assert_inside_window(self.item(name))
        self.assertIn("عيار 21", self.text("meltStockText"))
        self.fill_melt()
        self.snapshot("melt-dialog")
        self.click("confirmMeltButton")
        self.wait_until(lambda: len(self.db.melts) == 1, "melt posted")
        self.closed("tripMeltDialog")
        self.wait_idle()
        self.snapshot("trip-after-melt")
        melt = next(iter(self.db.melts.values()))
        self.assertEqual((melt["workshop"], melt["input_weight"], melt["output_weight"], melt["pure_complete"]),
                         ("ورشة النور", D("10.000"), D("9.900"), False))
        self.assertEqual(self.db.cash(), D("50000.00"))                                  # no cash, shop Scrap or supplier movement
        self.assertEqual(self.db.physical(1), D("70.000"))
        values = " | ".join(self.visible_texts("meltAnalysisValue"))
        self.assertIn("غير مكتمل", values)                                                # no definitive loss when a residue is unknown
        self.assertIn("-0.100", values.replace("⁦", "").replace("⁩", "").replace("−", "-"))
        self.assertIn("الناتج الرئيسي", " ".join(self.visible_texts("lotTitle")))
        self.assertIn("رجوع ناتج السبك", " ".join(self.visible_texts("lotNote")))
        self.assertFalse(self.item("closeTripButton").property("enabled"))               # 20 g of 21K custody and the lot are outstanding
        self.assertEqual(self.warnings, [])

    def test_a_refused_melt_keeps_every_field_and_the_corrected_retry_posts_once(self):
        self.start_trip()
        self.open_melt()
        self.fill_melt(fineness="870.55")                                                # 0.1 per-mille precision only
        self.click("confirmMeltButton")
        self.wait_until(lambda: self.tvm.errorMessage != "", "refusal")
        QTest.qWait(100)
        self.assertTrue(self.item("tripMeltDialog").property("visible"))
        self.assertIn("خانة عشرية واحدة", self.text("meltEntryError"))
        self.assertEqual((self.text("meltWorkshopField"), self.text("meltInputWeight_0"), self.text("meltOutputWeight"),
                          self.text("meltOutputFineness"), self.text("meltResidueWeight_0")), ("ورشة النور", "10", "9.5", "870.55", "0.4"))
        self.assertEqual(len(self.db.melts), 0)
        self.click("meltOutputFineness")
        QTest.keyClick(self.root, Qt.Key_A, Qt.ControlModifier)
        self.type_text("870.5")
        button = self.item("confirmMeltButton")
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)                # a double click is one request
        self.wait_until(lambda: len(self.db.melts) >= 1, "melt posted")
        self.closed("tripMeltDialog")
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual((len(self.db.melts), len(self.db.lots)), (1, 2))

    def test_weight_gain_needs_its_explanation_and_the_form_fits_every_size(self):
        self.start_trip()
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(150)
                self.open_melt()
                for name in ("confirmMeltButton", "cancelMeltButton", "meltDateField", "meltWorkshopField"):
                    self.assert_inside_window(self.item(name))
                self.item("tripMeltDialog").close()
                self.closed("tripMeltDialog")
        self.open_melt()
        self.fill_melt(weight="5", output="5.5", residue=None)
        self.click("confirmMeltButton")
        self.wait_until(lambda: self.tvm.errorMessage != "", "refusal")
        self.assertIn("سبب زيادة الوزن", self.text("meltEntryError"))
        self.type_in("meltGainField", "شوائب أخف")
        self.click("confirmMeltButton")
        self.wait_until(lambda: len(self.db.melts) == 1, "melt posted")
        self.closed("tripMeltDialog")
        self.wait_idle()
        self.assertEqual(next(iter(self.db.melts.values()))["gain_explanation"], "شوائب أخف")

    def test_a_melt_is_reversed_from_the_log_and_its_analysis_stays_visible(self):
        self.start_trip()
        self.open_melt()
        self.fill_melt(residue=None)
        self.click("confirmMeltButton")
        self.wait_until(lambda: len(self.db.melts) == 1, "melt posted")
        self.closed("tripMeltDialog")
        self.wait_idle()
        buttons = []

        def collect(node):
            if node.objectName() == "reverseEntryButton" and node.property("visible"):
                buttons.append(node)
            for child in node.childItems():
                collect(child)
        collect(self.root.contentItem())
        self.assertEqual(len(buttons), 2)
        self.reveal(buttons[-1])
        self.click(buttons[-1])                                                           # the melt (newest entry)
        self.wait_until(lambda: self.item("tripReasonDialog").property("opened"), "reversal dialog")
        self.type_in("tripReasonField", "وزن خاطئ")
        self.click("confirmTripReasonButton")
        self.wait_until(lambda: len(self.db.corrections) == 1, "reversed")
        self.closed("tripReasonDialog")
        self.wait_idle()
        self.assertIn("معكوسة بالعملية RV000001", " ".join(self.visible_texts("meltState")))
        self.assertIn("عملية معكوسة — الأرقام للتاريخ فقط ولا تؤثر على الرصيد الحالي", self.visible_texts("meltReversedNotice"))
        self.assertTrue(self.visible_texts("meltAnalysisValue"))                           # the analysis is kept after the reversal
        self.snapshot("reversed-melt")
        self.assertEqual(self.tvm.detail["gold"][0]["outstanding"], "30.000")             # the input custody is back
        self.assertEqual(self.tvm.detail["lots"], [])


class MarketTripLotReturnPageTests(MarketTripMeltingPageTests):
    """M2 on the real page, real clicks: whole melt lots return to the shop (a melt bar is indivisible), the separate shop lot stock,
    refusals that keep the fields and reversal. The 1080x680 viewport is the binding case."""

    def melted_trip(self):
        self.start_trip()
        self.open_melt()
        self.fill_melt(weight="10", output="9.5", fineness="930", residue=("0.4", ""))
        self.click("confirmMeltButton")
        self.wait_until(lambda: len(self.db.melts) == 1, "melt posted")
        self.closed("tripMeltDialog")
        self.wait_idle()

    def open_lot_return(self):
        self.click("lotReturnButton")
        self.wait_until(lambda: self.item("tripLotReturnDialog").property("opened"), "lot return dialog")

    def test_returning_whole_lots_with_real_clicks_updates_custody_and_the_separate_shop_stock(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.melted_trip()
        self.assertTrue(self.item("lotReturnButton").property("enabled"))
        self.assertEqual(self.tvm.shopLots, [])                                         # nothing in the shop yet
        before_cash, before_scrap = self.db.cash(), self.db.physical(1)
        self.open_lot_return()
        self.assertEqual(self.text("lotReturnRule"), "ترجع السبكة كاملة ولا يمكن إرجاع جزء منها")
        for name in ("lotReturnDateField", "lotReturnSelect_0", "lotReturnSelect_1", "lotReturnNotesField", "confirmLotReturnButton", "cancelLotReturnButton"):
            with self.subTest(control=name):
                self.item(name)
        for name in ("confirmLotReturnButton", "cancelLotReturnButton"):
            self.assert_inside_window(self.item(name))
        self.assertIsNone(self.visible_item("lotReturnQty_0"))                          # there is no quantity to type
        self.assertIn("930", self.text("lotReturnInfo_0"))
        self.assertIn("الوزن الكامل: 9.500", self.text("lotReturnInfo_0"))
        self.assertIn("يتبقى في العهدة بعد الرجوع: 0.000", self.text("lotReturnInfo_0"))
        self.assertIn("غير معلومة", self.text("lotReturnInfo_1"))                     # the unassayed residue
        self.assertIn("الوزن الكامل: 0.400", self.text("lotReturnInfo_1"))
        self.assertFalse(self.item("confirmLotReturnButton").property("enabled"))     # nothing selected yet
        self.click("lotReturnSelect_0")
        self.click("lotReturnSelect_1")
        self.assertTrue(self.item("confirmLotReturnButton").property("enabled"))
        self.snapshot("lot-return-dialog")
        self.click("confirmLotReturnButton")
        self.wait_until(lambda: len(self.db.lot_returns) == 2, "lots returned")
        self.closed("tripLotReturnDialog")
        self.wait_idle()
        self.snapshot("trip-after-lot-return")
        self.assertEqual((self.db.cash(), self.db.physical(1)), (before_cash, before_scrap))        # no cash / Scrap movement
        self.assertEqual(sorted(m["weight_delta"] for m in self.db.shop_moves), [D("0.400"), D("9.500")])     # the full weights
        self.assertIn("MT000001-2", " | ".join(self.visible_texts("shopLotRef")))
        figures = " | ".join(self.visible_texts("shopLotFigures"))
        self.assertIn("9.500 جرام", figures)
        self.assertIn("غير معلومة", figures)                                            # the residue purity stays unknown
        self.assertIn("8.835000", figures)                                              # 9.5 g * 930 / 1000, known assay only
        self.assertEqual(self.tvm.detail["lots"], [])                                   # nothing of the melt is left in custody
        # the shop stock is readable with no trip selected
        self.tvm.selectTrip("")
        QTest.qWait(150)
        self.assertIn("9.500 جرام", " | ".join(self.visible_texts("shopLotFigures")))
        self.assertEqual(self.warnings, [])

    def test_a_stale_selection_is_refused_keeps_the_fields_and_a_double_click_posts_once(self):
        self.melted_trip()
        self.open_lot_return()
        self.click("lotReturnSelect_0")
        self.type_in("lotReturnNotesField", "ملاحظة")
        main_lot = next(l["id"] for l in self.db.lots.values() if l["lot_kind"] == "MAIN")
        self.tvm._service.return_lots("1", [{"lot_id": str(main_lot)}], TODAY, "", "11111111-1111-4111-8111-111111111111")   # returned elsewhere meanwhile
        self.click("confirmLotReturnButton")
        self.wait_until(lambda: self.tvm.errorMessage != "", "refusal")
        QTest.qWait(100)
        self.assertTrue(self.item("tripLotReturnDialog").property("visible"))
        self.assertIn("ليست في العهدة", self.text("lotReturnError"))
        self.assertEqual(self.text("lotReturnNotesField"), "ملاحظة")
        self.assertTrue(self.item("lotReturnSelect_0").property("checked"))              # the choice is kept
        self.assertEqual(len(self.db.lot_returns), 1)                                    # only the direct service call
        self.click("lotReturnSelect_0")                                                  # unselect the stale lot, select the residue
        self.click("lotReturnSelect_1")
        button = self.item("confirmLotReturnButton")
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
        self.wait_until(lambda: len(self.db.lot_returns) >= 2, "residue returned")
        self.closed("tripLotReturnDialog")
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual(len(self.db.lot_returns), 2)

    def test_a_partially_returned_legacy_lot_cannot_be_selected_and_says_why(self):
        self.melted_trip()
        from datetime import date
        from types import SimpleNamespace
        repo = TripRepo(self.db)
        main_lot = next(l["id"] for l in self.db.lots.values() if l["lot_kind"] == "MAIN")
        entry = repo.insert_entry(trip_id=1, entry_no=repo.next_entry_no(1), kind="LOT_RETURN", entry_date=date(2026, 10, 10), notes=None,
                                  key="22222222-2222-4222-8222-222222222222", digest="x" * 64,
                                  actor=SimpleNamespace(user_id=7, employee_id=None), now=self.db.now)
        custody = repo.insert_lot_movement(lot_id=main_lot, entry_id=entry, movement_type="SHOP_RETURN", weight_delta=D("-1.000"))
        shop = repo.insert_shop_lot_movement(lot_id=main_lot, entry_id=entry, movement_type="TRIP_RETURN", weight_delta=D("1.000"))
        repo.insert_lot_return(entry_id=entry, lot_id=main_lot, weight=D("1.000"), custody_movement_id=custody, shop_movement_id=shop)
        self.tvm.refresh()
        self.wait_idle()
        self.open_lot_return()
        self.assertFalse(self.item("lotReturnSelect_0").property("enabled"))
        self.assertIn("اعكس الرجوع الجزئي", self.text("lotReturnReason_0"))
        self.assertTrue(self.item("lotReturnSelect_1").property("enabled"))               # the residue is still a whole lot
        self.assertEqual(len(self.db.lot_returns), 1)                                      # nothing was rewritten

    def test_the_dialog_fits_every_size_and_a_whole_return_is_reversed_from_the_history(self):
        self.melted_trip()
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(150)
                self.assert_inside_window(self.item("lotReturnButton"))
                self.open_lot_return()
                for name in ("confirmLotReturnButton", "cancelLotReturnButton", "lotReturnDateField"):
                    self.assert_inside_window(self.item(name))
                self.item("tripLotReturnDialog").close()
                self.closed("tripLotReturnDialog")
        self.open_lot_return()
        self.click("lotReturnSelect_0")
        self.click("confirmLotReturnButton")
        self.wait_until(lambda: len(self.db.lot_returns) == 1, "lot returned")
        self.closed("tripLotReturnDialog")
        self.wait_idle()
        buttons = []

        def collect(node):
            if node.objectName() == "reverseEntryButton" and node.property("visible"):
                buttons.append(node)
            for child in node.childItems():
                collect(child)
        collect(self.root.contentItem())
        self.assertEqual(len(buttons), 3)                                                # issue, melt, lot return
        self.reveal(buttons[-1])
        self.click(buttons[-1])                                                           # the lot return (newest)
        self.wait_until(lambda: self.item("tripReasonDialog").property("opened"), "reversal dialog")
        self.type_in("tripReasonField", "رجوع بالخطأ")
        self.click("confirmTripReasonButton")
        self.wait_until(lambda: len(self.db.corrections) == 1, "reversed")
        self.closed("tripReasonDialog")
        self.wait_idle()
        self.assertIn("معكوسة بالعملية RV000001", " ".join(self.visible_texts("entryState")))
        self.assertEqual(self.tvm.shopLots, [])                                           # the shop stock is gone again
        self.assertEqual([l["outstanding"] for l in self.tvm.detail["lots"]], ["9.500", "0.400"])   # and the whole lot is back in custody


# The base class carries the invoice-page tests: only this module's own tests are collected here.
for _inherited in [n for n in dir(harness.InvoicePageTests) if n.startswith("test_")]:
    setattr(MarketTripPageTests, _inherited, None)
    setattr(MarketTripMeltingPageTests, _inherited, None)
for _own in [n for n in dir(MarketTripPageTests) if n.startswith("test_") and getattr(MarketTripPageTests, n) is not None]:
    setattr(MarketTripMeltingPageTests, _own, None)                                    # the subclass runs only its own melting tests
for _melting in [n for n in dir(MarketTripMeltingPageTests) if n.startswith("test_") and getattr(MarketTripMeltingPageTests, n) is not None]:
    setattr(MarketTripLotReturnPageTests, _melting, None)                              # the lot-return class runs only its own tests


if __name__ == "__main__":
    unittest.main()
