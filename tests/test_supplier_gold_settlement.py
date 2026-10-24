"""Legacy direct supplier gold settlement from shop Scrap (045, SGxxxxxx).

New SHOP_SCRAP posting is disabled; tests cover that policy alongside exact replay
and canonical reversal of historical records in the in-memory transactional database.

NOT real-MySQL tests: the SQL, the constraints and the rollback were exercised on the masgold_rehearsal sandbox, single-threaded. Concurrency is not
exercised anywhere (the supplier-then-Scrap lock order is inferred from the code, not proven)."""
import copy
from decimal import Decimal as D
import hashlib
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from mysql.connector import IntegrityError

from services import supplier_rules as rules
from services.correction_service import CorrectionError
from services.supplier_gold_settlement_service import SupplierGoldSettlementService
from services.supplier_rules import SupplierRuleError
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import OWNER, ScrapRepo, SettlementRepo, build, session_for

TODAY = "2026-10-10"
K21, K18, K24, K22 = 1, 2, 3, 4                  # fake karat ids: 21K, 18K, 24K, 22K (inactive)
S21, S18_INACTIVE, INTERMEDIARY, NO_KARAT, S24_BOTH = "10", "11", "12", "13", "14"
MANAGER_SG = frozenset({"APP_LOGIN", "TRANSACTION_CORRECT", "SUPPLIER_GOLD_SETTLE"})
CASHIER = frozenset({"APP_LOGIN"})


def key(n):
    return "55555555-5555-4555-8555-%012d" % n


class SettlementCase(unittest.TestCase):
    def setUp(self):
        self.db, self.session, self.authorization, _, self.corrections = build(permissions=OWNER)
        self.db.suppliers[13] = {"id": 13, "name": "بلا عيار", "is_active": 1, "is_supplier": True, "is_intermediary": False, "karat_id": None}
        self.db.suppliers[14] = {"id": 14, "name": "مورد 24", "is_active": 1, "is_supplier": True, "is_intermediary": True, "karat_id": K24}
        self.service = self.make(self.session, self.authorization, self.corrections)
        self.n = 0
        started = patch("repositories.supplier_gold_settlement_repository.SupplierGoldSettlementRepository", SettlementRepo)
        started.start()
        self.addCleanup(started.stop)

    def make(self, session, authorization, corrections):
        return SupplierGoldSettlementService(session, transaction_factory=self.db.transaction, repository=SettlementRepo, scrap=ScrapRepo,
                                             authorization=authorization, corrections=corrections)

    def as_user(self, permissions, user_id=9):
        from services.correction_service import CorrectionService
        from tests.market_trip_fakes import Corrections, Customers, Invoices
        session = session_for(permissions, user_id)
        authorization = FakeAuthorization(session)
        corrections = CorrectionService(session, authorization=authorization, transaction_factory=self.db.transaction, invoice_repository=Invoices,
                                        correction_repository=Corrections, customer_repository=Customers)
        return self.make(session, authorization, corrections)

    def settle(self, supplier=S21, karat=K21, weight="10", request=None, service=None, reference="", notes="", when=TODAY):
        self.n += 1
        return (service or self.service).settle(supplier, karat, weight, when, reference, notes, request or key(self.n))

    def state(self):
        return copy.deepcopy({k: v for k, v in vars(self.db).items() if k not in ("fail_at", "transactions", "lock_log")})

    def refused(self, code, call, error=SupplierRuleError):
        before = self.state()
        with self.assertRaises(error) as context:
            call()
        self.assertEqual(getattr(context.exception, "code", None), code)
        self.assertEqual(self.state(), before, "a refused request left a trace")
        return context.exception

    def denied(self, call):
        before = self.state()
        with self.assertRaises(PermissionError):
            call()
        self.assertEqual(self.state(), before, "a denied request left a trace")

    def payable(self, supplier, karat):
        return SettlementRepo(self.db).balance(supplier, karat)


class PostingPolicyTests(SettlementCase):
    def test_new_shop_scrap_settlements_are_refused_without_any_ledger_effect(self):
        before = self.state()
        with self.assertRaises(SupplierRuleError) as refusal:
            self.settle(S18_INACTIVE, K21, "10")
        self.assertEqual(refusal.exception.code, "SHOP_SCRAP_SETTLEMENT_DISABLED")
        self.assertEqual(self.state(), before)

    def test_a_manager_with_the_settlement_right_is_still_blocked_from_shop_scrap(self):
        with self.assertRaises(SupplierRuleError) as refusal:
            self.settle(service=self.as_user(MANAGER_SG))
        self.assertEqual(refusal.exception.code, "SHOP_SCRAP_SETTLEMENT_DISABLED")
        self.denied(lambda: self.settle(service=self.as_user(CASHIER)))

    def test_input_syntax_is_validated_before_the_disabled_source_is_rejected(self):
        for weight in ("", "0", "-1", "abc", "1.0005", "0.0004"):
            with self.subTest(weight=weight):
                self.refused("WEIGHT_INVALID", lambda w=weight: self.settle(S21, K21, w))
        self.refused("DATE_INVALID", lambda: self.settle(S21, K21, "1", when="2026-13-45"))
        self.refused("DATE_REQUIRED", lambda: self.settle(S21, K21, "1", when=""))
        self.assertEqual(self.db.settlements, {})

    def test_authorization_is_still_checked_before_the_source_policy(self):
        revoked = self.as_user(MANAGER_SG, user_id=11)
        revoked._authorization.permissions = frozenset({"APP_LOGIN"})
        self.denied(lambda: self.settle(service=revoked))

    def test_request_key_owned_by_another_flow_is_refused_before_the_source_policy(self):
        request = key(900)
        self.db.entries[1] = {"request_key": request}
        self.refused("REQUEST_CONFLICT", lambda: self.settle(request=request))

    def test_read_only_scrap_context_remains_available_for_legacy_history(self):
        context = self.service.settlement_context(S18_INACTIVE)
        self.assertEqual((context["supplier"]["karat_value"], context["supplier"]["is_active"], context["supplier"]["code"]),
                         (18, False, "S000011"))
        by_karat = {k["karat_value"]: (k["available"], k["held"]) for k in context["scrap"]}
        self.assertEqual(by_karat[21], (D("95.000"), D("5.000")))
        self.assertNotIn(22, by_karat)
        self.denied(lambda: self.as_user(CASHIER).settlement_context(S21))


class LegacyReplayAndReversalTests(SettlementCase):
    def seed_legacy_settlement(self, supplier=S18_INACTIVE, karat=K21, weight="10", request=None, reference="legacy", notes=""):
        weight = rules.parse_delivered_weight(weight)
        request = request or key(999)
        supplier_id, karat_id = int(supplier), int(karat)
        repo = SettlementRepo(self.db)
        delivered_karat = repo.get_karat(karat_id)
        target_karat = repo.get_supplier(supplier_id)
        credited, analytical, difference = rules.settlement_credit(weight, delivered_karat["karat_value"], target_karat["karat_value"])
        notes = notes or None
        digest = hashlib.sha256(json.dumps(
            [supplier_id, karat_id, str(weight), TODAY, reference, notes], ensure_ascii=False).encode()).hexdigest()
        actor = self.session.require_authenticated()
        settlement_id = repo.insert_header(
            supplier_id=supplier_id, delivered_karat=delivered_karat,
            target_karat=repo.get_karat(target_karat["karat_id"]), delivered_weight=weight,
            credited=credited, analytical=analytical, difference=difference, settlement_date=TODAY,
            reference=reference, notes=notes, key=request, digest=digest, actor=actor, now=self.db.now)
        scrap_id = repo.post_scrap_out(settlement_id=settlement_id, karat_id=karat_id, weight=weight, code="SG000001",
                                       notes=notes, actor=actor, now=self.db.now)
        gold_type = repo.gold_type_id("SETTLEMENT")
        gold_id = repo.insert_gold_movement(
            supplier_id=supplier_id, type_id=gold_type, karat_id=target_karat["karat_id"], weight_delta=-credited,
            eq21_delta=-(credited * D(target_karat["karat_value"]) / D("21")), source_type="SUPPLIER_GOLD_SETTLEMENT",
            source_id=settlement_id, reference="SG000001", description="", now=self.db.now, user_id=actor.user_id)
        repo.insert_leg(settlement_id=settlement_id, kind="SCRAP", source_kind="SHOP_SCRAP", old_gold_movement_id=scrap_id)
        repo.insert_leg(settlement_id=settlement_id, kind="GOLD", source_kind="SHOP_SCRAP", supplier_gold_movement_id=gold_id)
        return {"id": str(settlement_id), "code": f"SG{settlement_id:06d}", "request": request, "digest": digest,
                "credited": credited, "weight": weight, "karat_id": target_karat["karat_id"]}

    def test_exact_replay_of_an_existing_shop_scrap_request_remains_readable(self):
        legacy = self.seed_legacy_settlement()
        before = self.state()
        replay = self.settle(S18_INACTIVE, K21, "10", request=legacy["request"], reference="legacy")
        self.assertEqual((replay["duplicate"], replay["id"], replay["code"]), (True, legacy["id"], legacy["code"]))
        self.assertEqual(self.state(), before)

    def test_replay_conflict_is_not_misreported_as_a_disabled_source(self):
        legacy = self.seed_legacy_settlement()
        self.refused("REQUEST_CONFLICT", lambda: self.settle(S18_INACTIVE, K21, "10.001",
                                                              request=legacy["request"], reference="legacy"))

    def test_a_valid_legacy_reversal_still_uses_stored_values_and_the_canonical_correction(self):
        legacy = self.seed_legacy_settlement()
        scrap_after = self.db.physical(K21)
        self.db.suppliers[11]["karat_id"] = K24
        reversal = self.service.reverse(legacy["id"], "تسليم خاطئ")
        self.assertTrue(reversal["code"].startswith("RV"))
        self.assertEqual(self.db.physical(K21), scrap_after + legacy["weight"])
        self.assertEqual((self.payable(11, K18), self.payable(11, K24)), (D("0.000"), D("0.000")))
        self.assertEqual(self.db.sup_gold[-1]["karat_id"], legacy["karat_id"])
        self.assertEqual(self.db.sup_gold[-1]["weight_delta"], legacy["credited"])
        document = self.service.settlement_detail(legacy["id"])
        self.assertTrue(document["reversed"])
        self.assertEqual(self.db.settlements[int(legacy["id"])]["credited_weight_grams"], legacy["credited"])

    def test_legacy_reversal_requires_owner_right_reason_and_remains_single_use(self):
        legacy = self.seed_legacy_settlement()
        manager = self.as_user(MANAGER_SG)
        self.denied(lambda: manager.reverse(legacy["id"], "تصحيح"))
        self.refused("REASON_REQUIRED", lambda: self.service.reverse(legacy["id"], "  "), error=CorrectionError)
        self.service.reverse(legacy["id"], "تصحيح")
        self.refused("ALREADY_REVERSED", lambda: self.service.reverse(legacy["id"], "مرة أخرى"), error=CorrectionError)

    def test_a_reversed_legacy_settlement_does_not_reenable_new_shop_scrap_posting(self):
        legacy = self.seed_legacy_settlement()
        self.service.reverse(legacy["id"], "تصحيح")
        self.refused("SHOP_SCRAP_SETTLEMENT_DISABLED", lambda: self.settle(S18_INACTIVE, K21, "1"))


class RulesTests(unittest.TestCase):
    def test_the_posted_credit_comes_from_the_high_precision_value_not_the_rounded_analytical_one(self):
        posted, analytical, difference = rules.settlement_credit(D("1.2344996"), 1, 1)
        self.assertEqual((posted, analytical), (D("1.234"), D("1.234500")))                 # rounding the analytical value would have posted 1.235
        self.assertEqual(difference, posted - analytical)

    def test_half_up_boundaries_and_the_difference_from_the_two_figures(self):
        self.assertEqual(rules.settlement_credit(D("0.500"), 21, 24)[:2], (D("0.438"), D("0.437500")))       # 0.4375 exactly: half up
        self.assertEqual(rules.settlement_credit(D("0.001"), 18, 24)[:2], (D("0.001"), D("0.000750")))       # 0.00075 -> 0.001
        posted, analytical, difference = rules.settlement_credit(D("10.000"), 21, 18)
        self.assertEqual((posted, analytical, difference), (D("11.667"), D("11.666667"), D("0.000333")))
        posted, analytical, difference = rules.settlement_credit(D("2.950"), 24, 21)
        self.assertEqual((posted, analytical, difference), (D("3.371"), D("3.371429"), D("-0.000429")))      # a negative difference is kept
        for value in (posted, analytical, difference):
            self.assertIsInstance(value, D)

    def test_a_positive_delivery_that_rounds_to_zero_credit_is_refused(self):
        with self.assertRaises(SupplierRuleError) as refusal:
            rules.settlement_credit(D("0.0001"), 18, 24)
        self.assertEqual(refusal.exception.code, "CREDIT_ROUNDS_TO_ZERO")

    def test_the_delivered_weight_is_never_silently_rounded(self):
        self.assertEqual(rules.parse_delivered_weight("1.250"), D("1.250"))
        self.assertEqual(rules.parse_delivered_weight("٣٫٥".replace("٫", ".")), D("3.500"))
        for bad in ("1.0004", "0", "-2", "", "x"):
            with self.assertRaises(SupplierRuleError):
                rules.parse_delivered_weight(bad)


if __name__ == "__main__":
    unittest.main()
