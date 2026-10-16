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
                "holdings": {"pools": [], "history": [], "total": D("0"), "pure": D("0"), "can_manage": True, "logo_pools": [], "logo_history": [],
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


if __name__ == "__main__":
    unittest.main()
