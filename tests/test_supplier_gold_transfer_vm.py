"""Supplier-to-supplier gold transfer in the supplier page (SupplierViewModel): the source appears in «تسوية ذهب», the preview, posting, the ledger rows and the reversal.
The transfer service runs over the in-memory ledger of tests/test_supplier_gold_transfer.py; the supplier READ service is a stub. The worker is replaced by a synchronous call."""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal as D
from types import SimpleNamespace
import unittest

from app.viewmodels.supplier_viewmodel import SupplierViewModel
from services.supplier_gold_transfer_service import SupplierGoldTransferService
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import session_for
from tests.test_supplier_gold_transfer import FakeRepo, FakeTrips, MANAGER, OWNER, Store, key

TODAY = "2026-11-24"


class DetailStub:
    """list_suppliers() and supplier_detail() for the recipient 2 «مكرم» (21K); the ledger rows are the ones a real detail would give."""
    def __init__(self, ledger=()):
        self.ledger = list(ledger)

    def list_suppliers(self, search="", active=False, _expected_actor=None, **kw):
        return [{"id": "2", "name": "مكرم"}]

    def supplier_detail(self, supplier_id, _expected_actor=None):
        return {"supplier": {"id": "2", "code": "S000002", "name": "مكرم", "phone": "", "notes": "", "is_active": True, "karat_id": 1, "karat_value": 21, "karat_locked": False,
                             "is_supplier": True, "is_intermediary": False, "role_text": "مورد"},
                "balances": {"gold_status": "RESOLVED", "gold_karat_value": 21, "gold_by_karat": [], "gold_direction": "SETTLED", "gold_balance": D("0.000"),
                             "making_direction": "SETTLED", "making": D("0.00")},
                "gold_ledger": self.ledger, "making_ledger": []}


def ledger_row(**over):
    row = {"id": "9", "occurred_at": datetime(2026, 11, 24, 12, 0), "movement": "تسوية ذهب من حساب مورد", "karat_value": 21, "weight_delta": D("-1.000"),
           "weight_balance_after": D("91.740"), "reference": "GT000001", "source_id": "1", "source_type": "SUPPLIER_GOLD_TRANSFER", "actor": "المالك",
           "transfer_id": "1", "transfer_reversed": False, "transfer_reverses": False, "transfer_state": ""}
    row.update(over)
    return row


class Case(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        FakeRepo.store = self.store
        FakeTrips.store = self.store

    def view_model(self, permissions=OWNER, ledger=()):
        session = session_for(permissions)

        @contextmanager
        def transaction():
            yield SimpleNamespace()
        vm = SupplierViewModel(session, DetailStub(ledger))
        vm._transfers = SupplierGoldTransferService(session, transaction_factory=transaction, repository=FakeRepo, trips=FakeTrips, authorization=FakeAuthorization(session))
        actor = session.current_actor()

        def start(operation, call):                         # run the worker's job synchronously, as the worker would
            try:
                result, error = call(_expected_actor=actor), ""
            except (ValueError, PermissionError) as exc:
                result, error = None, str(exc)
            getattr(vm, f"_on_{operation}")(result, error)
            vm.changed.emit()
        vm._start = start
        vm._selected = "2"
        return vm


class SettleFromASupplierTests(Case):
    def test_the_supplier_that_holds_gold_for_the_shop_is_listed_as_a_source(self):
        vm = self.view_model()
        vm.loadSupplierGoldSources("2")
        self.assertEqual([(s["key"], s["source_type"]) for s in vm.supplierGoldSources], [("supplier:5", "SUPPLIER")])
        self.assertIn("المتحدة", vm.supplierGoldSources[0]["text"])
        self.assertEqual(vm.errorMessage, "")

    def test_a_user_without_the_settlement_right_sees_no_such_source(self):
        vm = self.view_model({"APP_LOGIN", "SUPPLIER_VIEW"})
        vm.loadSupplierGoldSources("2")
        self.assertEqual(vm.supplierGoldSources, [])

    def test_picking_the_source_shows_what_it_holds_and_the_preview_converts_the_karat(self):
        vm = self.view_model()
        vm._detail = DetailStub().supplier_detail("2")                                                  # the open supplier page: the recipient's karat for the preview
        vm.loadSupplierGoldSources("2")
        vm.loadSupplierGoldSource("supplier:5")
        context = vm.supplierGoldSourceContext
        self.assertEqual((context["type"], context["availableText"]), ("SUPPLIER", "265.100 جرام"))
        preview = vm.previewSupplierGoldSettlement("2", "1", "", False)
        self.assertEqual((preview["valid"], preview["creditedText"]), (True, "1.000 جرام عيار 21"))
        too_much = vm.previewSupplierGoldSettlement("2", "265.101", "", False)
        self.assertEqual((too_much["short"], too_much["valid"]), (True, False))                       # more than it holds

    def test_posting_takes_one_gram_from_almuttahida_and_reduces_what_is_owed_to_makram(self):
        vm = self.view_model()
        posted = []
        vm.supplierGoldSettlementPosted.connect(posted.append)
        vm.loadSupplierGoldSources("2")
        vm.loadSupplierGoldSource("supplier:5")
        vm.postSupplierGoldSettlement("supplier:5", "1", "", False, TODAY, "", "", key())
        self.assertEqual(vm.errorMessage, "")
        self.assertEqual(posted, ["GT000001"])
        self.assertIn("GT000001", vm.message)
        self.assertEqual((self.store.balance(5, 1), self.store.balance(2, 1)), (D("-264.100"), D("91.740")))

    def test_a_refusal_shows_the_reason_and_posts_nothing(self):
        vm = self.view_model()
        vm.loadSupplierGoldSources("2")
        vm.loadSupplierGoldSource("supplier:5")
        vm.postSupplierGoldSettlement("supplier:5", "999", "", False, TODAY, "", "", key())
        self.assertIn("المتاح", vm.errorMessage)
        self.assertEqual(self.store.transfers, [])


class LedgerAndReverseTests(Case):
    def test_a_transfer_row_offers_the_reverse_button_only_while_it_is_open(self):
        vm = self.view_model()
        vm._detail = DetailStub([ledger_row(), ledger_row(id="10", transfer_reversed=True, transfer_state="معكوس بتسوية عكس"),
                                 ledger_row(id="11", transfer_reverses=True, transfer_state="عكس تسوية ذهب بين موردين", transfer_id="2")]).supplier_detail("2")
        rows = vm.detail["goldLedger"]
        self.assertEqual([(r["transferId"], r["canReverseTransfer"], r["state"], r["reversed"]) for r in rows],
                         [("1", True, "", False), ("1", False, "معكوس بتسوية عكس", True), ("2", False, "عكس تسوية ذهب بين موردين", False)])

    def test_the_owner_reverses_a_transfer_and_the_balances_come_back(self):
        vm = self.view_model()
        done = []
        vm.transferReversed.connect(done.append)
        vm.loadSupplierGoldSources("2")
        vm.loadSupplierGoldSource("supplier:5")
        vm.postSupplierGoldSettlement("supplier:5", "5", "", False, TODAY, "", "", key())
        vm.reverseSupplierGoldTransfer("1", "سُجِّل بالخطأ")
        self.assertEqual(vm.errorMessage, "")
        self.assertEqual(done, ["GT000002"])
        self.assertEqual((self.store.balance(5, 1), self.store.balance(2, 1)), (D("-265.100"), D("92.740")))

    def test_a_manager_may_post_but_the_reverse_is_refused(self):
        vm = self.view_model(MANAGER)
        self.assertEqual(vm.canReverseSettlement, False)
        vm.loadSupplierGoldSources("2")
        vm.loadSupplierGoldSource("supplier:5")
        vm.postSupplierGoldSettlement("supplier:5", "1", "", False, TODAY, "", "", key())
        vm.reverseSupplierGoldTransfer("1", "x")
        self.assertTrue(vm.errorMessage)
        self.assertEqual(len(self.store.transfers), 1)


if __name__ == "__main__":
    unittest.main()
