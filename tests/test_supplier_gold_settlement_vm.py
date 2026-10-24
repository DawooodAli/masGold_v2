"""Direct supplier gold settlement (045) — SupplierViewModel: the dialog context, the preview, posting, reversal and the ledger rows.

The settlement service and the real CorrectionService run over the in-memory transactional database of tests/market_trip_fakes.py. The supplier
READ service (list / detail) is a small stub, because only the view model's formatting of its result is under test here; the SQL behind the real
supplier detail was exercised on the masgold_rehearsal sandbox. The worker thread is replaced by a synchronous call, as in the payment tests."""
from datetime import datetime
from decimal import Decimal as D
import unittest
from unittest.mock import patch

from app.viewmodels.supplier_viewmodel import SupplierViewModel
from services.correction_service import CorrectionError, CorrectionService
from services.supplier_gold_settlement_service import SupplierGoldSettlementService
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import (Corrections, Customers, Invoices, MANAGER, OWNER, ScrapRepo, SettlementRepo, build, session_for)

TODAY = "2026-10-10"
K21, K18 = "1", "2"
MANAGER_SG = frozenset({"APP_LOGIN", "TRANSACTION_CORRECT", "SUPPLIER_GOLD_SETTLE"})
CASHIER = frozenset({"APP_LOGIN"})


def gold_row(**over):
    row = {"id": "1", "occurred_at": datetime(2026, 10, 10, 10, 0), "movement": "تسوية ذهب", "karat_value": 18, "weight_delta": D("-11.667"),
           "weight_balance_after": D("-11.667"), "reference": "SG000001", "source_id": "1", "actor": "المالك", "settlement_id": "1",
           "delivered_weight": D("10.000"), "delivered_karat": 21, "reversed_by": None, "reverses_reference": None}
    row.update(over)
    return row


class DetailStub:
    """Only what SupplierViewModel reads: list_suppliers() and supplier_detail()."""

    def __init__(self, ledger=()):
        self.ledger = list(ledger)

    def list_suppliers(self, search="", active=False, _expected_actor=None, **kw):
        return [{"id": "11", "name": "مورد متوقف"}]

    def supplier_detail(self, supplier_id, _expected_actor=None):
        return {"supplier": {"id": "11", "code": "S000011", "name": "مورد متوقف", "phone": "", "notes": "", "is_active": False, "karat_id": 2,
                             "karat_value": 18, "karat_locked": False, "is_supplier": True, "is_intermediary": False, "role_text": "مورد"},
                "balances": {"gold_status": "RESOLVED", "gold_karat_value": 18, "gold_by_karat": [], "gold_direction": "SETTLED",
                             "gold_balance": D("0.000"), "making_direction": "SETTLED", "making": D("0.00")},
                "gold_ledger": self.ledger, "making_ledger": []}


class SettlementVmCase(unittest.TestCase):
    def setUp(self):
        self.db, self.session, self.authorization, _, self.corrections = build(permissions=OWNER)
        started = patch("repositories.supplier_gold_settlement_repository.SupplierGoldSettlementRepository", SettlementRepo)
        started.start()
        self.addCleanup(started.stop)
        self.vm = self.view_model(self.session, self.authorization, self.corrections)
        self.posted, self.reversed = [], []
        self.vm.settlementPosted.connect(self.posted.append)
        self.vm.settlementReversed.connect(self.reversed.append)

    def view_model(self, session, authorization, corrections, ledger=()):
        service = SupplierGoldSettlementService(session, transaction_factory=self.db.transaction, repository=SettlementRepo, scrap=ScrapRepo,
                                                authorization=authorization, corrections=corrections)
        vm = SupplierViewModel(session, DetailStub(ledger))
        vm._settlements = service
        actor = session.current_actor()

        def start(operation, call):                         # run the worker's job synchronously, as the worker would
            try:
                result, error = call(_expected_actor=actor), ""
            except (ValueError, PermissionError, CorrectionError) as exc:
                result, error = None, str(exc)
            getattr(vm, f"_on_{operation}")(result, error)
            vm.changed.emit()
        vm._start = start
        vm._selected = "11"
        return vm

    def user_vm(self, permissions, user_id=9):
        session = session_for(permissions, user_id)
        authorization = FakeAuthorization(session)
        corrections = CorrectionService(session, authorization=authorization, transaction_factory=self.db.transaction, invoice_repository=Invoices,
                                        correction_repository=Corrections, customer_repository=Customers)
        return self.view_model(session, authorization, corrections)


class RightsAndContextTests(SettlementVmCase):
    def test_ui_hints_follow_the_session_rights_only(self):
        self.assertEqual((self.vm.canSettleGold, self.vm.canReverseSettlement), (True, True))
        manager = self.user_vm(MANAGER_SG | {"INTERMEDIARY_HOLDING_MANAGE"})
        self.assertEqual((manager.canSettleGold, manager.canReverseSettlement), (True, False))
        manager_without_source_right = self.user_vm(MANAGER_SG)
        self.assertEqual(manager_without_source_right.canSettleGold, False)
        cashier = self.user_vm(CASHIER)
        self.assertEqual((cashier.canSettleGold, cashier.canReverseSettlement), (False, False))

    def test_the_dialog_context_names_the_supplier_its_karat_and_the_available_scrap_apart_from_the_held(self):
        self.assertEqual(self.vm.settlementContext, {})
        self.vm.loadSettlementContext("11")
        context = self.vm.settlementContext
        self.assertEqual((context["supplierName"], context["supplierCode"], context["karatText"], context["karatResolved"], context["isSupplier"],
                          context["supplierActive"]), ("مورد متوقف", "S000011", "عيار 18", True, True, False))
        by_id = {row["karatId"]: row["text"] for row in context["scrap"]}
        self.assertEqual(by_id[K21], "عيار 21 — المتاح 95.000 جرام • محجوز لمراجعة المدير 5.000")            # held Scrap is not available
        self.assertEqual(by_id[K18], "عيار 18 — المتاح 50.000 جرام")                                           # no hold, no hold text
        self.assertTrue(all(isinstance(row["karatId"], str) for row in context["scrap"]))                      # ids cross as strings

    def test_a_refused_context_shows_the_reason_and_leaves_no_context(self):
        manager_without_right = self.user_vm(CASHIER)
        manager_without_right.loadSettlementContext("11")
        self.assertEqual(manager_without_right.settlementContext, {})
        self.assertTrue(manager_without_right.errorMessage)
        self.vm.loadSettlementContext("999")
        self.assertEqual(self.vm.settlementContext, {})
        self.assertTrue(self.vm.errorMessage)


class PreviewTests(SettlementVmCase):
    def setUp(self):
        super().setUp()
        self.vm.loadSettlementContext("11")

    def test_the_preview_shows_delivered_credited_analytical_and_the_signed_difference_from_the_rules(self):
        preview = self.vm.previewSettlement(K21, "10")
        self.assertEqual((preview["valid"], preview["short"], preview["error"]), (True, False, ""))
        self.assertEqual(preview["deliveredText"], "10.000 جرام عيار 21")
        self.assertEqual(preview["creditedText"], "11.667 جرام عيار 18")
        self.assertEqual(preview["formulaText"], "10.000 × 21 ÷ 18")
        self.assertEqual(preview["availableText"], "95.000 جرام عيار 21")                                      # the available, not the physical
        self.assertEqual(preview["analyticalText"], "11.666667")
        self.assertIn("0.000333", preview["differenceText"])

    def test_the_rounding_boundary_and_a_negative_difference_are_shown_exactly(self):
        half = self.vm.previewSettlement(K21, "0.012")                                                          # 0.012 x 21 / 18 = 0.014 exactly
        self.assertEqual(half["creditedText"], "0.014 جرام عيار 18")
        down = self.vm.previewSettlement(K21, "0.001")                                                          # 0.001166.. -> 0.001
        self.assertEqual((down["creditedText"], down["analyticalText"]), ("0.001 جرام عيار 18", "0.001167"))
        self.assertIn("0.000167", down["differenceText"])
        self.assertNotEqual(down["differenceText"], half["differenceText"])

    def test_more_than_the_available_grams_is_flagged_short_and_not_valid_even_though_physical_would_cover_it(self):
        self.assertEqual(self.db.physical(1), D("100.000"))
        preview = self.vm.previewSettlement(K21, "95.001")
        self.assertEqual((preview["short"], preview["valid"]), (True, False))
        exact = self.vm.previewSettlement(K21, "95.000")
        self.assertEqual((exact["short"], exact["valid"]), (False, True))

    def test_incomplete_or_invalid_input_gives_an_empty_or_explained_preview_never_a_figure(self):
        for karat, weight in ((K21, ""), (K21, "   "), ("", "5")):
            with self.subTest(karat=karat, weight=weight):
                preview = self.vm.previewSettlement(karat, weight)
                self.assertEqual((preview["valid"], preview["deliveredText"], preview["creditedText"], preview["error"]), (False, "", "", ""))
        for bad in ("abc", "0", "-1", "1.0005"):
            with self.subTest(weight=bad):
                preview = self.vm.previewSettlement(K21, bad)
                self.assertFalse(preview["valid"])
                self.assertTrue(preview["error"])
                self.assertEqual(preview["creditedText"], "")
        self.assertTrue(self.vm.previewSettlement("999", "5")["error"])                                         # an unknown karat is explained

    def test_a_supplier_without_a_resolved_karat_gets_the_explanation(self):
        self.vm.loadSettlementContext("13")
        self.assertEqual(self.vm.settlementContext, {})                                                         # (13 is absent from this fake: refused)
        self.db.suppliers[13] = {"id": 13, "name": "بلا عيار", "is_active": 1, "is_supplier": True, "is_intermediary": False, "karat_id": None}
        self.vm.loadSettlementContext("13")
        self.assertFalse(self.vm.settlementContext["karatResolved"])
        preview = self.vm.previewSettlement(K21, "5")
        self.assertFalse(preview["valid"])
        self.assertIn("عيار هذا المورد غير محدد", preview["error"])


class PostingAndReversalTests(SettlementVmCase):
    def source(self, source_type="CUSTODY_GOLD"):
        if source_type == "HOLDING":
            return {"key": "source-1", "source_type": source_type, "pool_kind": "LOT", "pool_id": "1",
                    "text": "مسوقجي • سبكة"}
        return {"key": "source-1", "source_type": source_type, "trip_id": "1", "trip_code": "MT000001",
                "karat_id": K21, "designation": "LOGO", "text": "MT000001 • لوجو عيار 21"}

    def prepare_source(self, source):
        self.vm._gold_sources = [source]
        self.vm._gold_sources_supplier_id = "11"
        if source["source_type"] == "HOLDING":
            self.vm._gold_source_context = {"source": source, "holding": {} , "supplier_id": "11"}
        else:
            self.vm._gold_source_context = {"source": source, "available": D("25.000"), "karat_value": 21,
                                            "supplier_id": "11"}
        self.vm._gold_source_context_supplier_id = "11"

    def test_the_legacy_scrap_viewmodel_entry_point_cannot_post_a_new_settlement(self):
        self.vm.postSettlement("11", K21, "10", TODAY, "", "", self.vm.newRequestKey())
        self.assertEqual(self.posted, [])
        self.assertEqual((self.db.settlements, self.db.physical(1)), ({}, D("100.000")))
        self.assertIn("كسر المحل متوقفة", self.vm.errorMessage)

    def test_direct_duplicate_source_submission_is_ignored_while_the_operation_is_busy(self):
        source = self.source()
        self.prepare_source(source)
        calls = []
        self.vm._market_trip_settlements = type(
            "TripService", (), {"settle_supplier_gold": lambda service, *args, **kwargs: calls.append(args)})()
        self.vm._busy.add("suppliergoldsettle")
        self.vm.postSupplierGoldSettlement("source-1", "10", "", False, TODAY, "", "", self.vm.newRequestKey())
        self.assertEqual(calls, [])
        self.assertTrue(self.vm.settlingSupplierGold)

    def test_custody_source_submission_uses_the_market_trip_settlement_service(self):
        source = self.source()
        self.prepare_source(source)
        calls = []
        service = type("TripService", (), {
            "settle_supplier_gold": lambda instance, *args, **kwargs: (
                calls.append((args, kwargs)) or {"code": "SG000001", "delivered_weight": "10.000",
                                                "credited_weight": "11.667", "target_karat": 18})
        })()
        self.vm._market_trip_settlements = service
        completed, custody = [], []
        self.vm.supplierGoldSettlementPosted.connect(completed.append)
        self.vm.custodySettlementPosted.connect(custody.append)
        self.vm.postSupplierGoldSettlement("source-1", "10", "", False, TODAY, "R", "N", self.vm.newRequestKey())
        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(args[:5], ("1", "11", K21, "LOGO", "10"))
        self.assertEqual(kwargs["_expected_actor"].user_id, self.session.current_actor().user_id)
        self.assertEqual((completed, custody), (["SG000001"], ["SG000001"]))
        self.assertIn("من عهدة السوق", self.vm.message)

    def test_holding_source_submission_uses_holding_settlement_service(self):
        source = self.source("HOLDING")
        self.prepare_source(source)
        calls = []
        service = type("HoldingService", (), {
            "settle": lambda instance, *args, **kwargs: (
                calls.append((args, kwargs)) or {"code": "SG000002", "delivered_weight": "2.000",
                                                "credited_weight": "2.333", "target_karat": 18})
        })()
        self.vm._holding_settlements = service
        completed = []
        self.vm.supplierGoldSettlementPosted.connect(completed.append)
        self.vm.postSupplierGoldSettlement("source-1", "2", "GRAM_ENTITLEMENT", False,
                                           TODAY, "", "", self.vm.newRequestKey())
        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(args[:6], ("LOT", "1", "11", "2", "GRAM_ENTITLEMENT", False))
        self.assertEqual(kwargs["_expected_actor"].user_id, self.session.current_actor().user_id)
        self.assertEqual(completed, ["SG000002"])
        self.assertIn("من رصيد المسوقجي", self.vm.message)

    def test_source_context_is_bound_to_the_supplier_selection(self):
        self.prepare_source(self.source())
        self.vm.selectSupplier("12")
        self.assertEqual((self.vm.supplierGoldSources, self.vm.supplierGoldSourceContext), ([], {}))
        self.vm.postSupplierGoldSettlement("source-1", "1", "", False, TODAY, "", "", self.vm.newRequestKey())
        self.assertIn("أعد تحميل بياناته", self.vm.errorMessage)


class LedgerRowTests(SettlementVmCase):
    def rows(self, *ledger):
        vm = self.view_model(self.session, self.authorization, self.corrections, ledger)
        vm._detail = vm._service_instance().supplier_detail("11")
        return vm.detail["goldLedger"]

    def test_a_settlement_row_shows_delivered_beside_credited_with_its_sg_reference_and_offers_the_reversal(self):
        (row,) = self.rows(gold_row())
        self.assertEqual(row["detail"], "سُلِّم 10.000 جرام عيار 21 ← احتُسب 11.667 جرام عيار 18")
        self.assertEqual((row["reference"], row["weight"], row["karat"], row["state"], row["reversed"], row["canReverse"], row["settlementId"]),
                         ("SG000001", "11.667", 18, "", False, True, "1"))

    def test_a_reversed_settlement_and_its_reversal_row_are_marked_by_the_one_correction(self):
        original, undo = self.rows(
            gold_row(reversed_by="RV000001", weight_balance_after=D("0.000")),
            gold_row(id="2", movement="عكس تسوية ذهب", weight_delta=D("11.667"), reference="RV000001", settlement_id=None, delivered_weight=None,
                     delivered_karat=None, reverses_reference="SG000001", weight_balance_after=D("0.000")))
        self.assertEqual((original["state"], original["reversed"], original["canReverse"]), ("معكوسة بالعملية RV000001", True, False))
        self.assertEqual((undo["state"], undo["reference"], undo["detail"], undo["canReverse"]), ("عكس التسوية SG000001", "RV000001", "", False))

    def test_a_settlement_delivered_from_market_custody_names_its_logo_source_and_custody_entry_and_cannot_be_reversed_here(self):
        """046: the reversal of a custody-sourced document belongs to its custody entry, so the supplier page offers no reverse action."""
        (row,) = self.rows(gold_row(custody_reference="MT000001-2", custody_designation="LOGO"))
        self.assertEqual(row["detail"], "سُلِّم 10.000 جرام عيار 21 (لوجو) ← احتُسب 11.667 جرام عيار 18 • من عهدة السوق MT000001-2 (تُعكس من صفحة العهدة)")
        self.assertEqual((row["custodyReference"], row["canReverse"], row["reversed"], row["state"]), ("MT000001-2", False, False, ""))

    def test_the_reversed_state_of_a_custody_settlement_and_its_reversal_row_come_from_the_one_custody_correction(self):
        original, undo = self.rows(
            gold_row(custody_reference="MT000001-2", custody_designation="LOGO", reversed_by="RV000007", weight_balance_after=D("0.000")),
            gold_row(id="2", movement="عكس تسوية ذهب مورد", weight_delta=D("11.667"), reference="RV000007", settlement_id=None, delivered_weight=None,
                     delivered_karat=None, reverses_reference="SG000001 • MT000001-2", weight_balance_after=D("0.000")))
        self.assertEqual((original["state"], original["reversed"], original["canReverse"]), ("معكوسة بالعملية RV000007", True, False))
        self.assertEqual((undo["state"], undo["reference"], undo["canReverse"]), ("عكس التسوية SG000001 • MT000001-2", "RV000007", False))

    def test_an_ordinary_gold_row_is_unchanged(self):
        (row,) = self.rows(gold_row(settlement_id=None, delivered_weight=None, delivered_karat=None, movement="فاتورة شراء", reference="SP000001"))
        self.assertEqual((row["detail"], row["state"], row["canReverse"], row["settlementId"]), ("", "", False, ""))


if __name__ == "__main__":
    unittest.main()
