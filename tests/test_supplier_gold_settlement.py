"""Direct supplier gold settlement from available shop Scrap (045, SGxxxxxx). Real SupplierGoldSettlementService and the real CorrectionService over the
in-memory transactional database of tests/market_trip_fakes.py (a failed transaction restores every table; the 045 CHECK / unique constraints are
mirrored as asserts). Scrap, holds, the supplier gold ledger and the corrections are tables of that one fake database.

NOT real-MySQL tests: the SQL, the constraints and the rollback were exercised on the masgold_rehearsal sandbox, single-threaded. Concurrency is not
exercised anywhere (the supplier-then-Scrap lock order is inferred from the code, not proven)."""
import copy
from decimal import Decimal as D
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
        return copy.deepcopy({k: v for k, v in vars(self.db).items() if k not in ("fail_at", "transactions")})

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


class PostingTests(SettlementCase):
    def test_the_example_credits_the_suppliers_karat_and_deducts_the_delivered_grams_once(self):
        cash, making = copy.deepcopy(self.db.cash_moves), copy.deepcopy(self.db.sm_making)
        scrap = self.db.physical(K21)
        done = self.settle(S18_INACTIVE, K21, "10.000", reference="ت-1")
        self.assertEqual((done["delivered_weight"], done["credited_weight"], done["analytical"], done["rounding_difference"], done["duplicate"]),
                         ("10.000", "11.667", "11.666667", "0.000333", False))
        self.assertEqual(self.db.physical(K21), scrap - D("10.000"))                         # the ACTUAL delivered grams, once
        self.assertEqual(self.payable(11, K18), D("-11.667"))                                # the supplier's own karat, never converted back
        self.assertEqual((self.db.cash_moves, self.db.sm_making), (cash, making))             # no cash and no making effect
        row = self.db.sup_gold[0]
        self.assertEqual((row["karat_id"], row["weight_delta"], row["source_type"]), (K18, D("-11.667"), "SUPPLIER_GOLD_SETTLEMENT"))
        self.assertLess(row["eq21"], 0)                                                       # the Eq21 compatibility column keeps the sign
        self.assertEqual(self.db.scrap[-1]["type_code"], "SUPPLIER_GOLD_SETTLEMENT_OUT")

    def test_the_document_keeps_every_original_and_derived_figure(self):
        done = self.settle(S18_INACTIVE, K21, "10", reference="ت-1", notes="ملاحظة")
        document = self.service.settlement_detail(done["id"])
        self.assertEqual((document["code"], document["supplier_name"], document["delivered_karat"], document["target_karat"], document["delivered_weight"],
                          document["credited_weight"], document["analytical"], document["rounding_difference"], document["reference"], document["notes"]),
                         ("SG000001", "مورد متوقف", 21, 18, D("10.000"), D("11.667"), D("11.666667"), D("0.000333"), "ت-1", "ملاحظة"))
        stored = self.db.settlements[int(done["id"])]
        self.assertEqual(stored["rounding_difference_grams"], stored["credited_weight_grams"] - stored["credited_analytical_grams"])   # from the two stored figures
        self.assertEqual(stored["source_kind"], "SHOP_SCRAP")
        self.assertEqual([l["leg_kind"] for l in self.db.settlement_legs], ["SCRAP", "GOLD"])

    def test_the_same_karat_credits_the_delivered_grams(self):
        done = self.settle(S21, K21, "7.250")
        self.assertEqual((done["credited_weight"], done["analytical"], done["rounding_difference"]), ("7.250", "7.250000", "0.000000"))
        self.assertEqual(self.payable(10, K21), D("-7.250"))

    def test_the_supplier_credit_may_cross_zero_and_an_inactive_supplier_and_a_dual_role_party_receive_it(self):
        self.assertEqual(self.payable(10, K21), D("0.000"))
        self.settle(S21, K21, "3")
        self.assertEqual(self.payable(10, K21), D("-3.000"))                                  # crossed zero: the supplier owes the shop
        self.db.suppliers[10]["is_active"] = 0
        self.settle(S21, K21, "1")                                                           # an inactive supplier still receives it
        self.assertEqual(self.payable(10, K21), D("-4.000"))
        done = self.settle(S24_BOTH, K21, "2.100")                                           # a supplier + intermediary party
        self.assertEqual(done["credited_weight"], "1.838")                                   # 2.1 * 21 / 24 = 1.8375 -> half up

    def test_held_manager_review_scrap_is_not_available(self):
        self.assertEqual((self.db.physical(K21), self.db.available(K21)), (D("100.000"), D("95.000")))      # 5 g are held
        self.refused("SCRAP_SHORTAGE", lambda: self.settle(S21, K21, "95.001"))
        self.settle(S21, K21, "95.000")                                                       # exactly the available grams
        self.assertEqual((self.db.physical(K21), self.db.available(K21)), (D("5.000"), D("0.000")))
        self.refused("SCRAP_SHORTAGE", lambda: self.settle(S21, K21, "0.001"))

    def test_refusals_leave_no_trace(self):
        self.refused("SUPPLIER_ROLE_REQUIRED", lambda: self.settle(INTERMEDIARY, K21, "1"))
        self.refused("SUPPLIER_KARAT_MISSING", lambda: self.settle(NO_KARAT, K21, "1"))
        self.refused("SUPPLIER_NOT_FOUND", lambda: self.settle("999", K21, "1"))
        self.refused("SUPPLIER_NOT_FOUND", lambda: self.settle("x", K21, "1"))
        self.refused("KARAT_NOT_FOUND", lambda: self.settle(S21, 999, "1"))
        self.refused("KARAT_NOT_FOUND", lambda: self.settle(S21, K22, "1"))                   # an inactive master karat
        self.refused("KARAT_REQUIRED", lambda: self.settle(S21, "", "1"))
        for weight in ("", "0", "-1", "abc", "1.0005", "0.0004"):
            with self.subTest(weight=weight):
                self.refused("WEIGHT_INVALID", lambda w=weight: self.settle(S21, K21, w))
        self.refused("DATE_INVALID", lambda: self.settle(S21, K21, "1", when="2026-13-45"))
        self.refused("DATE_REQUIRED", lambda: self.settle(S21, K21, "1", when=""))
        self.refused("REQUEST_INVALID", lambda: self.settle(S21, K21, "1", request="nope"))
        self.assertEqual(self.db.settlements, {})

    def test_permissions_are_checked_inside_the_transaction(self):
        self.denied(lambda: self.settle(service=self.as_user(CASHIER)))
        done = self.settle(service=self.as_user(MANAGER_SG))                                  # a MANAGER may post
        self.assertEqual(done["credited_weight"], "10.000")
        revoked = self.as_user(MANAGER_SG, user_id=11)
        revoked._authorization.permissions = frozenset({"APP_LOGIN"})                          # revoked in the database, stale session
        self.denied(lambda: self.settle(service=revoked))

    def test_a_failure_at_any_posting_step_persists_nothing(self):
        before = self.state()
        for step in ("sg_header", "sg_scrap", "sg_gold", "sg_leg", "sg_audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.settle(S18_INACTIVE, K21, "10")
            self.db.fail_at = None
            self.assertEqual(self.state(), before, f"a failure at {step} left a trace")
        self.settle(S18_INACTIVE, K21, "10")
        self.assertEqual(len(self.db.settlements), 1)

    def test_replay_returns_the_original_and_any_other_payload_is_refused(self):
        request = key(900)
        first = self.settle(S18_INACTIVE, K21, "10", request=request, reference="a")
        after = self.state()
        again = self.settle(S18_INACTIVE, K21, "10", request=request, reference="a")
        self.assertEqual((again["duplicate"], again["id"], again["code"], again["credited_weight"]), (True, first["id"], first["code"], first["credited_weight"]))
        self.assertEqual(self.state(), after)
        for call in (lambda: self.settle(S18_INACTIVE, K21, "10.001", request=request, reference="a"),
                     lambda: self.settle(S21, K21, "10", request=request, reference="a"),
                     lambda: self.settle(S18_INACTIVE, K18, "10", request=request, reference="a"),
                     lambda: self.settle(S18_INACTIVE, K21, "10", request=request, reference="b"),
                     lambda: self.settle(S18_INACTIVE, K21, "10", request=request, reference="a", service=self.as_user(OWNER, user_id=99))):
            self.refused("REQUEST_CONFLICT", call)

    def test_a_twin_that_committed_first_is_decided_from_a_fresh_read(self):
        request = key(950)
        first = self.settle(request=request)
        digest = self.db.settlements[int(first["id"])]["request_hash"]
        original = IntegrityError(msg="Duplicate entry for key 'uq_sgs_request'", errno=1062)
        replay = self.service._replay(self.session.require_authenticated(), request, digest, original)
        self.assertEqual((replay["id"], replay["duplicate"]), (first["id"], True))

    def test_the_context_lists_the_available_scrap_per_active_karat_and_the_recipient(self):
        context = self.service.settlement_context(S18_INACTIVE)
        self.assertEqual((context["supplier"]["karat_value"], context["supplier"]["is_active"], context["supplier"]["code"]), (18, False, "S000011"))
        by_karat = {k["karat_value"]: (k["available"], k["held"]) for k in context["scrap"]}
        self.assertEqual(by_karat[21], (D("95.000"), D("5.000")))
        self.assertEqual(by_karat[18], (D("50.000"), D("0.000")))
        self.assertNotIn(22, by_karat)                                                       # an inactive karat is never offered
        self.denied(lambda: self.as_user(CASHIER).settlement_context(S21))


class ReversalTests(SettlementCase):
    def reverse(self, result, reason="تصحيح", service=None):
        return (service or self.service).reverse(result["id"], reason)

    def test_a_reversal_restores_the_stored_grams_and_the_stored_credit_never_a_recalculation(self):
        done = self.settle(S18_INACTIVE, K21, "10")
        scrap_after = self.db.physical(K21)
        self.db.suppliers[11]["karat_id"] = K24                                                # the supplier's karat changes afterwards...
        cash = copy.deepcopy(self.db.cash_moves)
        reversal = self.reverse(done, "تسليم خاطئ")
        self.assertTrue(reversal["code"].startswith("RV"))
        self.assertEqual(self.db.physical(K21), scrap_after + D("10.000"))                     # the stored 10.000 g return to Scrap
        self.assertEqual((self.payable(11, K18), self.payable(11, K24)), (D("0.000"), D("0.000")))     # ...the STORED 11.667 of the STORED karat is restored
        self.assertEqual(self.db.cash_moves, cash)
        back = self.db.sup_gold[-1]
        self.assertEqual((back["karat_id"], back["weight_delta"], back["source_type"]), (K18, D("11.667"), "TRANSACTION_CORRECTION"))
        self.assertEqual(self.db.scrap[-1]["type_code"], "REVERSAL_SUPPLIER_GOLD_SETTLEMENT_IN")

    def test_one_correction_record_drives_the_reversed_state(self):
        done = self.settle()
        self.assertFalse(self.service.settlement_detail(done["id"])["reversed"])
        reversal = self.reverse(done)
        records = [c for c in self.db.corrections if c["correction_type"] == "FULL_REVERSAL"]
        self.assertEqual([(c["source_type"], c["source_id"]) for c in records], [("SUPPLIER_GOLD_SETTLEMENT", done["id"])])
        document = self.service.settlement_detail(done["id"])
        self.assertEqual((document["reversed"], document["reversal_code"], document["reversal_reason"]), (True, reversal["code"], "تصحيح"))
        self.assertEqual(self.db.settlements[int(done["id"])]["credited_weight_grams"], D("10.000"))      # the original is never rewritten

    def test_the_rights_the_reason_and_a_second_reversal(self):
        done = self.settle()
        self.denied(lambda: self.reverse(done, service=self.as_user(MANAGER_SG)))             # OWNER only
        self.denied(lambda: self.reverse(done, service=self.as_user(OWNER - {"TRANSACTION_CORRECT"}, user_id=12)))
        self.denied(lambda: self.reverse(done, service=self.as_user(OWNER - {"SUPPLIER_GOLD_SETTLE_REVERSE"}, user_id=13)))
        self.refused("REASON_REQUIRED", lambda: self.reverse(done, reason="  "), error=CorrectionError)
        self.reverse(done)
        self.refused("ALREADY_REVERSED", lambda: self.reverse(done), error=CorrectionError)

    def test_a_failure_inside_the_reversal_changes_nothing(self):
        done = self.settle()
        before = self.state()
        for step in ("correction", "reversal_scrap", "sg_gold", "audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.reverse(done)
            self.db.fail_at = None
            self.assertEqual(self.state(), before, step)

    def test_a_reversed_settlement_can_be_settled_again_with_a_new_document(self):
        done = self.settle()
        self.reverse(done)
        again = self.settle()
        self.assertNotEqual(again["id"], done["id"])
        self.assertEqual(self.payable(10, K21), D("-10.000"))


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
