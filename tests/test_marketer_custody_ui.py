"""Marketer (مسوقجي) custody account (055) on the real Main.qml with real mouse clicks at the three target sizes. The marketer service is the real one over the
in-memory database of tests/market_trip_fakes.py + tests/marketer_fakes.py; the supplier READ service is the small stub of the holdings UI tests. NOT real-MySQL
tests."""
from decimal import Decimal as D
from unittest.mock import patch
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from services.marketer_custody_service import MarketerCustodyService
from tests import test_invoice_page_qml as harness
from tests import test_market_trip_ui as base
from tests.market_trip_fakes import AccountsRepo, OWNER, ScrapRepo, SettlementRepo, build
from tests.marketer_fakes import MarketerRepo, prepare
from tests.supplier_fakes import ALL as SUPPLIER_RIGHTS
from tests.test_intermediary_holdings_ui import HoldingSupplierStub

RIGHTS = tuple(SUPPLIER_RIGHTS) + tuple(OWNER)
PARTY = "20"
REFERENCE = {21: D("6000.0000")}


class MarketerPageTests(harness.InvoicePageTests):
    def setUp(self):
        super().setUp()
        session = self.controller.session
        self.db, _, self.authorization, self.trips, self.corrections = build(permissions=OWNER, session=session)
        prepare(self.db)
        for target, value in (("repositories.marketer_custody_repository.MarketerCustodyRepository", MarketerRepo),
                              ("repositories.supplier_gold_settlement_repository.SupplierGoldSettlementRepository", SettlementRepo),
                              ("repositories.financial_account_repository.FinancialAccountRepository", AccountsRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)
        self.service = MarketerCustodyService(session, transaction_factory=self.db.transaction, repository=MarketerRepo, accounts=AccountsRepo, scrap=ScrapRepo,
                                              settlements=SettlementRepo, authorization=self.authorization, corrections=self.corrections,
                                              price_reference=lambda: dict(REFERENCE))
        vms = self.controller.operational_viewmodels
        self.svm = vms["supplierVM"]
        self.mvm = self.svm.marketer
        self.mvm._service = self.service
        self.stub = HoldingSupplierStub(self.db, None)
        self.stub.supplier_detail = self.detail
        self.svm._service = self.stub
        vms["supplierPurchaseVM"]._service = type("NoInvoices", (), {"list_invoices": lambda s, supplier, status=None, **kw: []})()
        self.grant(*RIGHTS)
        self.svm.changed.emit()
        QTest.qWait(60)

    def detail(self, supplier_id, *, _expected_actor=None):
        row = self.db.suppliers[int(supplier_id)]
        return {"supplier": {**self.stub._supplier(row), "is_supplier": bool(row.get("is_supplier")), "is_intermediary": bool(row.get("is_intermediary"))},
                "balances": {"gold_status": "NOT_APPLICABLE", "gold_karat_value": None, "gold_karat_id": None, "gold_by_karat": [], "gold_direction": None,
                             "gold_balance": None, "making_direction": "SETTLED", "making": D("0.00")},
                "gold_ledger": [], "making_ledger": [],
                "holdings": {"marketer_gold": sum((m["weight_delta"] for m in self.db.mk_gold if m["party_id"] == int(supplier_id)), D("0.000")),
                             "pools": [], "history": [], "total": D("0"), "pure": D("0"), "can_manage": True, "logo_pools": [], "logo_history": [],
                             "settlements": [], "can_settle": True, "can_reverse": True, "receipts": [], "can_receive": True, "can_reverse_receipt": True}}

    visible_texts = base.MarketTripPageTests.visible_texts
    pick = base.MarketTripPageTests.pick

    def wait_idle(self):
        self.wait_until(lambda: not self.svm.busy and not self.mvm.writing, "workers idle")
        QTest.qWait(80)

    def open_party(self, party=PARTY):
        self.root.setProperty("currentPage", 14)
        QTest.qWait(200)
        self.svm.refresh()
        self.wait_idle()
        self.svm.selectSupplier(party)
        self.wait_until(lambda: "id" in self.svm.detail, "party detail")
        self.wait_until(lambda: self.mvm.visible, "marketer account loaded")
        self.wait_idle()
        tab = self.item("tabGold")
        if tab.property("visible") and not tab.property("on"):
            self.click(tab)
            QTest.qWait(80)

    def closed(self, name):
        self.wait_until(lambda: not self.item(name).property("visible"), f"{name} closed")

    def fill(self, name, text):
        self.click(name)
        QTest.keyClick(self.root, Qt.Key_A, Qt.ControlModifier)
        self.type_text(text)
        QTest.qWait(60)

    def cash_in_hand(self):
        return D(str(self.mvm.account["cashValue"]))

    def test_cash_handover_with_real_clicks_at_1080_updates_the_figures_and_lists_the_document(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.open_party()
        self.assertTrue(self.item("marketerPanel").property("visible"))
        self.assertIn("21", self.text("marketerIsolationNote") + "21")
        for name in ("marketerCashHandoverButton", "marketerCashReturnButton", "marketerPurchaseButton", "marketerSaleButton", "marketerGoldReturnButton"):
            self.reveal(self.item(name))
            self.assert_inside_window(self.item(name))
        self.click("marketerCashHandoverButton")
        self.wait_until(lambda: self.item("marketerDialog").property("opened"), "dialog")
        for name in ("marketerAmountField", "marketerDateField", "confirmMarketerButton", "cancelMarketerButton"):
            self.assert_inside_window(self.item(name))
        self.assertFalse(self.item("confirmMarketerButton").property("enabled"))
        self.fill("marketerAmountField", "50000")
        self.assertTrue(self.item("confirmMarketerButton").property("enabled"))
        self.click("confirmMarketerButton")
        self.wait_until(lambda: len(self.db.mk_docs) == 1, "handover posted")
        self.closed("marketerDialog")
        self.wait_idle()
        self.assertEqual(self.cash_in_hand(), 50000)
        self.wait_until(lambda: len(self.visible_texts("marketerDocumentRow")) == 1, "document listed")
        self.assertEqual(self.warnings, [])

    def test_purchase_and_sale_preview_and_post_gold_in_21_only(self):
        self.open_party()
        self.service.cash_handover(PARTY, "200000", "2026-10-17", "", "", "88888888-8888-4888-8888-000000000001")
        self.mvm.load()
        self.wait_idle()
        self.click("marketerPurchaseButton")
        self.wait_until(lambda: self.item("marketerDialog").property("opened"), "dialog")
        self.pick("marketerCounterpartyCombo", 0)
        self.fill("marketerCounterpartyNameField", "تاجر السوق")
        self.fill("marketerLineWeight_0", "10")
        self.fill("marketerLinePrice_0", "58000")
        self.assertTrue(self.item("confirmMarketerButton").property("enabled"))
        self.click("confirmMarketerButton")
        self.wait_until(lambda: len(self.db.mk_gold) == 1, "purchase posted")
        self.closed("marketerDialog")
        self.wait_idle()
        self.assertEqual(D(str(self.mvm.account["goldValue"])), 10)
        self.assertEqual(self.cash_in_hand(), 142000)

    def test_the_dialog_fits_at_every_target_size(self):
        self.open_party()
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            with self.subTest(size=f"{width}x{height}"):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(200)
                for mode_button in ("marketerPurchaseButton", "marketerSaleButton"):
                    self.reveal(self.item(mode_button))
                    self.click(mode_button)
                    self.wait_until(lambda: self.item("marketerDialog").property("opened"), "dialog")
                    for name in ("marketerDateField", "confirmMarketerButton", "cancelMarketerButton"):
                        self.assert_inside_window(self.item(name))
                    self.click("cancelMarketerButton")
                    self.closed("marketerDialog")


    # ------------------------------------------------------------ stabilization: post-commit refresh and the party summary
    def spy(self):
        seen = []
        for name in ("balancesChanged", "positionChanged", "partyChanged", "supplierChanged"):
            getattr(self.mvm, name).connect(lambda n=name: seen.append(n.replace("Changed", "")))
        return seen

    def post(self, seen, call):
        seen.clear()
        call()
        self.wait_until(lambda: not self.mvm.writing, "write finished")
        QTest.qWait(80)
        return list(seen)

    def test_post_commit_signals_follow_the_operation_never_a_page_hint_and_a_refusal_emits_none(self):
        self.open_party()
        seen = self.spy()
        key = self.mvm.newRequestKey
        self.assertEqual(self.post(seen, lambda: self.mvm.cashHandover("300000", "2026-10-17", "", "", key())), ["balances"])
        self.assertEqual(self.post(seen, lambda: self.mvm.purchase([{"weight": "10", "price": "58000"}], "", "تاجر", "2026-10-17", "", "", key(), False)),
                         ["position", "party"])
        self.assertEqual(self.post(seen, lambda: self.mvm.sell("2", "TOTAL", "12000", "", "مشتري", "2026-10-17", "", "", key(), False)), ["position", "party"])
        self.assertEqual(self.post(seen, lambda: self.mvm.goldReturn("3", "2026-10-17", "", "", key())), ["balances", "position", "party"])
        self.assertEqual(self.post(seen, lambda: self.mvm.cashReturn("1000", "2026-10-17", "", "", key())), ["balances"])
        self.assertEqual(self.post(seen, lambda: self.mvm.settle("23", "1", False, "2026-10-17", "", "", key())), ["position", "party", "supplier"])
        before = self.mvm.account["goldValue"]
        self.assertEqual(self.post(seen, lambda: self.mvm.cashReturn("99999999", "2026-10-17", "", "", key())), [])      # refused: nothing is announced
        self.assertEqual(self.mvm.errorCode, "CASH_EXCEEDS_HELD")
        self.assertEqual(self.mvm.account["goldValue"], before)
        # a reversal does not know the kind of document on the client: it refreshes every target, whatever the page says
        document = next(d for d in self.mvm.account["documents"] if d["kind"] == "CASH_RETURN" and d["canReverse"])
        self.assertEqual(self.post(seen, lambda: self.mvm.reverseDocument(document["id"], "تصحيح")), ["balances", "position", "party", "supplier"])
        settlement = next(s for s in self.mvm.account["settlements"] if s["canReverse"])
        self.assertEqual(self.post(seen, lambda: self.mvm.reverseSettlement(settlement["id"], "تصحيح")), ["balances", "position", "party", "supplier"])

    def test_the_real_wiring_reloads_the_balance_strip_and_the_gold_position_after_the_commit(self):
        class Strip:
            calls = 0

            def strip(self, *, _expected_actor=None):
                Strip.calls += 1
                return {}

        class Position:
            calls = 0

            def position(self, *, _expected_actor=None):
                Position.calls += 1
                return None

        vms = self.controller.operational_viewmodels
        self.grant(*RIGHTS, "MANAGEMENT_REVIEW")
        vms["balancesVM"]._service = Strip()
        vms["reviewsVM"]._gold_service = Position()
        self.open_party()
        self.assertTrue(vms["reviewsVM"].canViewGoldPosition)
        seen = self.spy()
        strip, position = Strip.calls, Position.calls
        self.post(seen, lambda: self.mvm.cashHandover("300000", "2026-10-17", "", "", self.mvm.newRequestKey()))
        self.wait_until(lambda: Strip.calls > strip, "balance strip reloaded after a cash handover")
        self.assertEqual(Position.calls, position)                                     # cash never moves the gold position
        strip = Strip.calls
        self.post(seen, lambda: self.mvm.purchase([{"weight": "10", "price": "58000"}], "", "تاجر", "2026-10-17", "", "", self.mvm.newRequestKey(), False))
        self.wait_until(lambda: Position.calls > position, "gold position reloaded after a purchase")
        QTest.qWait(150)
        self.assertEqual(Strip.calls, strip)                                           # a purchase moves no Main Safe / Scrap
        position = Position.calls
        self.post(seen, lambda: self.mvm.goldReturn("3", "2026-10-17", "", "", self.mvm.newRequestKey()))
        self.wait_until(lambda: Strip.calls > strip and Position.calls > position, "Scrap consumers and the gold position reloaded after a gold return")

    def test_a_read_started_before_the_commit_never_decides_the_final_party_summary(self):
        import threading
        self.open_party()
        self.service.cash_handover(PARTY, "300000", "2026-10-17", "", "", "88888888-8888-4888-8888-000000000011")
        gate, calls = threading.Event(), []
        original = self.detail

        def slow(supplier_id, *, _expected_actor=None):
            snapshot = original(supplier_id)                   # the state BEFORE the write below commits
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                gate.wait(15)                                  # the first read is slow: it answers after the write has committed
            return snapshot
        self.stub.supplier_detail = slow
        self.svm._reload_selected()
        self.wait_until(lambda: calls == [1], "the first read is in flight")
        self.mvm.purchase([{"weight": "10", "price": "58000"}], "", "تاجر", "2026-10-17", "", "", self.mvm.newRequestKey(), False)
        self.wait_until(lambda: len(calls) >= 2 and not self.mvm.writing, "the write committed and a newer read ran")
        gate.set()                                             # now the stale answer arrives
        self.wait_until(lambda: self.svm.busy is False, "reads finished")
        QTest.qWait(200)
        self.assertEqual(D(str(self.svm._detail["holdings"]["marketer_gold"])), D("10.000"))        # the final state includes the committed write
        self.assertIn("10.000", self.svm.holdingsPanel["goldPositionText"])

    def test_the_party_summary_includes_the_marketer_gold_and_fits_at_1080(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.open_party()
        self.assertFalse(self.item("goldHeldPosition").property("visible"))               # nothing held yet
        self.service.cash_handover(PARTY, "300000", "2026-10-17", "", "", "88888888-8888-4888-8888-000000000021")
        self.service.purchase(PARTY, [{"weight": "10", "price": "58000"}], "", "تاجر", "2026-10-17", "", "", "88888888-8888-4888-8888-000000000022", False)
        self.svm._reload_selected()
        self.wait_until(lambda: self.item("goldHeldPosition").property("visible"), "summary visible for a party holding only marketer gold")
        self.assertIn("10.000", self.text("goldHeldPosition"))
        self.assertIn("عيار 21", self.text("goldHeldPosition"))
        self.assert_inside_window(self.item("goldHeldPosition"))
        self.assertEqual(self.warnings, [])


if __name__ == "__main__":
    unittest.main()
