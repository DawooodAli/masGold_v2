"""Market-trip custody core (037): issue, return, close, reopen and reversal.

Real MarketTripService and the real CorrectionService over the in-memory transactional database of tests/market_trip_fakes.py:
a failed transaction restores every table. These are NOT real-MySQL concurrency tests: the over-return / over-issue races and the
locking were run separately on the rehearsal sandbox (see the task report)."""
import copy
import inspect
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
import unittest

from mysql.connector import IntegrityError

from services.correction_service import CorrectionError
from services.market_trip_service import MarketTripService, TripError, parse_cash, parse_weight
from tests.market_trip_fakes import (AccountsRepo, HoldingRepo, MANAGER, OWNER, OWNER_NO_CORRECT, SupplierPayRepo, TripRepo, build, session_for)
from tests.auth_fakes import FakeAuthorization

ROOT = Path(__file__).resolve().parents[1]
TODAY = "2026-10-10"
K21, K18, K24 = "1", "2", "3"


def key(n):
    return "33333333-3333-4333-8333-%012d" % n


def gold(*rows):
    """(karat, grams[, designation]) lines; the designation defaults to MELT (للسبك), as 039 requires one on every issued line."""
    return [{"karat_id": r[0], "weight": r[1], "designation": r[2] if len(r) > 2 else "MELT"} for r in rows]


class TripCase(unittest.TestCase):
    def setUp(self):
        self.db, self.session, self.authorization, self.service, self.corrections = build()
        self.n = 0
        for target, value in (("repositories.market_trip_repository.MarketTripRepository", TripRepo),
                              ("repositories.supplier_payment_repository.SupplierPaymentRepository", SupplierPayRepo),
                              ("repositories.financial_account_repository.FinancialAccountRepository", AccountsRepo),
                              ("repositories.intermediary_holding_repository.IntermediaryHoldingRepository", HoldingRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)

    # ------------------------------------------------------------ helpers
    def issue(self, cash="", rows=(), trip="", when=TODAY, notes="", request=None, service=None):
        self.n += 1
        return (service or self.service).issue(trip, cash, gold(*rows), when, notes, request or key(self.n))

    def ret(self, trip, cash="", rows=(), when=TODAY, notes="", request=None, service=None):
        self.n += 1
        return (service or self.service).return_custody(trip, cash, gold(*rows), when, notes, request or key(self.n))

    def state(self):
        return copy.deepcopy({k: v for k, v in vars(self.db).items() if k not in ("fail_at", "transactions", "lock_log")})

    def refused(self, code, call, error=TripError):
        before = self.state()
        with self.assertRaises(error) as context:
            call()
        self.assertEqual(getattr(context.exception, "code", None), code)
        self.assertEqual(self.state(), before, "a refused request left a trace")
        return context.exception

    def custody(self, trip="1"):
        return self.service.trip_detail(trip)["custody"]

    def owner_like(self, permissions, user_id=9):
        session = session_for(permissions, user_id)
        authorization = FakeAuthorization(session)
        from services.correction_service import CorrectionService
        from tests.market_trip_fakes import Corrections, Customers, Invoices
        corrections = CorrectionService(session, authorization=authorization, transaction_factory=self.db.transaction,
                                        invoice_repository=Invoices, correction_repository=Corrections, customer_repository=Customers)
        return MarketTripService(session, transaction_factory=self.db.transaction, repository=TripRepo, accounts=AccountsRepo, supplier_payments=SupplierPayRepo,
                                 scrap=__import__("tests.market_trip_fakes", fromlist=["ScrapRepo"]).ScrapRepo,
                                 authorization=authorization, corrections=corrections), authorization


# --------------------------------------------------------------------------- issue
class IssueTests(TripCase):
    def test_cash_only_issue_reduces_the_main_safe_once_and_creates_custody(self):
        result = self.issue("10000")
        self.assertEqual((result["trip_code"], result["code"], result["created"], result["duplicate"]), ("MT000001", "MT000001-1", True, False))
        self.assertEqual(self.db.cash(), D("40000.00"))
        (move,) = [m for m in self.db.cash_moves if m["reference_type"] == "MARKET_TRIP_ENTRY"]
        self.assertEqual((move["account_id"], move["amount_delta"], move["type_code"], move["reference_code"]),
                         (1, D("-10000.00"), "TRIP_CASH_ISSUE", "MT000001-1"))
        custody = self.custody()
        self.assertEqual((custody["cash"]["issued"], custody["cash"]["outstanding"], custody["gold"], custody["settled"]),
                         (D("10000.00"), D("10000.00"), [], False))
        self.assertEqual([m for m in self.db.scrap if m["reference_type"] == "MARKET_TRIP_ENTRY"], [])      # no Scrap touched
        self.assertEqual([e["event_type"] for e in self.db.events], ["OPENED"])

    def test_gold_only_issue_writes_a_counted_scrap_out_of_the_same_karat_and_keeps_physical_grams(self):
        self.issue("", [(K21, "12.345"), (K18, "7.000")])
        outs = {m["karat_id"]: m for m in self.db.scrap if m["reference_type"] == "MARKET_TRIP_ENTRY"}
        self.assertEqual((outs[1]["weight_delta"], outs[1]["type_code"], outs[2]["weight_delta"]), (D("-12.345"), "TRIP_CUSTODY_OUT", D("-7.000")))
        self.assertEqual((self.db.physical(1), self.db.physical(2)), (D("87.655"), D("43.000")))
        self.assertEqual(self.db.cash(), D("50000.00"))                                       # no cash touched
        gold_rows = {g["karat_value"]: g["outstanding"] for g in self.custody()["gold"]}
        self.assertEqual(gold_rows, {21: D("12.345"), 18: D("7.000")})                          # per karat, never one Eq21 total

    def test_a_combined_issue_and_a_second_issue_on_the_same_trip(self):
        first = self.issue("1000", [(K21, "10")])
        second = self.issue("500.50", [(K21, "5"), (K24, "0")], trip=first["trip_id"])          # a zero line is ignored
        self.assertEqual((second["code"], second["created"]), ("MT000001-2", False))
        custody = self.custody()
        self.assertEqual((custody["cash"]["outstanding"], [(g["karat_value"], g["outstanding"]) for g in custody["gold"]]),
                         (D("1500.50"), [(21, D("15.000"))]))
        self.assertEqual(self.db.cash(), D("48499.50"))

    def test_the_same_karat_twice_is_summed_into_one_line(self):
        self.issue("", [(K21, "1.500"), (K21, "2.250")])
        self.assertEqual([(g["karat_id"], g["weight_grams"]) for g in self.db.gold_legs], [(1, D("3.750"))])

    def test_only_available_scrap_can_be_issued_manager_review_holds_are_excluded(self):
        self.assertEqual((self.db.physical(1), self.db.available(1)), (D("100.000"), D("95.000")))
        self.refused("SCRAP_SHORTAGE", lambda: self.issue("", [(K21, "95.001")]))
        message = self.refused("SCRAP_SHORTAGE", lambda: self.issue("", [(K21, "98")])).args[0]
        self.assertIn("محجوز لمراجعة المدير", message)                                            # the held grams are explained
        self.refused("SCRAP_SHORTAGE", lambda: self.issue("", [(K24, "0.001")]))                  # no 24K at all
        self.issue("", [(K21, "95.000")])                                                        # exactly the available grams
        self.assertEqual((self.db.physical(1), self.db.available(1)), (D("5.000"), D("0.000")))
        self.refused("SCRAP_SHORTAGE", lambda: self.issue("", [(K21, "0.001")]))

    def test_the_whole_issue_is_refused_when_any_karat_is_short(self):
        self.refused("SCRAP_SHORTAGE", lambda: self.issue("100", [(K18, "10"), (K21, "500")]))
        self.assertEqual((self.db.cash(), self.db.trips, self.db.entries), (D("50000.00"), {}, {}))

    def test_quantities_are_exact_and_validated_by_the_service(self):
        self.refused("NOTHING_TO_RECORD", lambda: self.issue("", []))
        self.refused("NOTHING_TO_RECORD", lambda: self.issue("0", [(K21, "0")]))
        for bad in ("-1", "abc", "10.005", "1e30", "NaN"):
            with self.subTest(cash=bad):
                self.refused("CASH_INVALID", lambda c=bad: self.issue(c))
        for bad in ("-1", "x", "1.2345", "1e30"):
            with self.subTest(weight=bad):
                self.refused("WEIGHT_INVALID", lambda w=bad: self.issue("", [(K21, w)]))
        self.refused("KARAT_REQUIRED", lambda: self.issue("", [("", "5")]))
        self.refused("KARAT_NOT_FOUND", lambda: self.issue("", [("99", "5")]))
        self.refused("KARAT_NOT_FOUND", lambda: self.issue("", [("4", "5")]))                      # inactive master karat
        self.refused("DATE_REQUIRED", lambda: self.issue("1", when=""))
        self.refused("DATE_INVALID", lambda: self.issue("1", when="10/10/2026"))
        self.refused("TEXT_LONG", lambda: self.issue("1", notes="x" * 1001))
        self.refused("REQUEST_INVALID", lambda: self.issue("1", request="nope"))
        self.refused("TRIP_NOT_FOUND", lambda: self.issue("1", trip="77"))
        self.issue("١٠٠٠٫٥".replace("٫", "."), [(K21, "٢٫٥".replace("٫", "."))], when="٢٠٢٦-١٠-١٠")   # Arabic digits are normalised
        self.assertEqual(self.custody()["cash"]["issued"], D("1000.50"))

    def test_an_inactive_or_missing_main_safe_is_refused(self):
        self.db.cash_accounts[1]["is_active"] = 0
        self.refused("ACCOUNT_INACTIVE", lambda: self.issue("100"))
        self.issue("", [(K21, "1")])                                                              # gold alone needs no cash account
        self.db.cash_accounts.clear()
        self.refused("ACCOUNT_NOT_FOUND", lambda: self.issue("100"))

    def test_funds_policy_is_unchanged_the_safe_may_go_negative(self):
        self.issue("60000")
        self.assertEqual(self.db.cash(), D("-10000.00"))

    def test_no_supplier_or_other_business_effect_exists_in_this_slice(self):
        source = "".join((ROOT / p).read_text(encoding="utf-8") for p in ("services/market_trip_service.py", "repositories/market_trip_repository.py"))
        # 043: only the making payment; 046: gold goes to a supplier only THROUGH the settlement repository, so the trip modules never touch the
        # supplier gold ledger tables themselves (and the Eq21 compatibility column is filled by the settlement code, never read here)
        # 058 (BOOK Phase 2): `entry_book_supplier` READS the stored supplier gold movement of a TRIP_BOOK delivery (the exact figure a reversal restores); no trip module ever
        # WRITES the ledger, so the table name is forbidden only in a write statement (the same read also selects the stored Eq21 compatibility column, which no trip module fills or uses)
        for write in ("INSERT INTO supplier_gold_movements", "UPDATE supplier_gold_movements", "DELETE FROM supplier_gold_movements"):
            self.assertNotIn(write, source, write)
        for forbidden in ("supplier_gold_movement_types", "supplier_purchase", "supplier_invoice",
                          "financial_operations", "golddb", "float("):
            self.assertNotIn(forbidden, source, forbidden)
        self.assertEqual([m for m in self.db.cash_moves if m["type_code"].startswith("SUPPLIER")], [])
        self.issue("1", [(K21, "1")])
        self.assertFalse(hasattr(self.db, "making"))                                          # no supplier ledger is touched


# --------------------------------------------------------------------------- return
class ReturnTests(TripCase):
    def setUp(self):
        super().setUp()
        self.trip = self.issue("10000", [(K21, "20.000"), (K18, "5.500")])["trip_id"]

    def test_a_partial_return_restores_the_safe_and_the_same_karat_and_reduces_custody(self):
        result = self.ret(self.trip, "3000", [(K21, "8.250")])
        self.assertEqual((result["code"], result["kind"]), ("MT000001-2", "RETURN"))
        self.assertEqual(self.db.cash(), D("43000.00"))
        back = [m for m in self.db.scrap if m["type_code"] == "TRIP_CUSTODY_RETURN_IN"]
        self.assertEqual([(m["karat_id"], m["weight_delta"]) for m in back], [(1, D("8.250"))])
        custody = self.custody()
        self.assertEqual(custody["cash"]["outstanding"], D("7000.00"))
        self.assertEqual({g["karat_value"]: g["outstanding"] for g in custody["gold"]}, {21: D("11.750"), 18: D("5.500")})
        self.assertFalse(custody["settled"])

    def test_the_example_take_10000_use_7000_return_3000_never_deducts_the_safe_again(self):
        self.ret(self.trip, "3000")
        self.assertEqual(self.db.cash(), D("43000.00"))                                           # 50,000 - 10,000 + 3,000
        self.assertEqual(len([m for m in self.db.cash_moves if m["reference_type"] == "MARKET_TRIP_ENTRY"]), 2)

    def test_a_full_return_brings_every_quantity_back_exactly(self):
        self.ret(self.trip, "10000", [(K21, "20.000"), (K18, "5.500")])
        custody = self.custody()
        self.assertEqual((custody["cash"]["outstanding"], [g["outstanding"] for g in custody["gold"]], custody["settled"]),
                         (D("0.00"), [D("0.000"), D("0.000")], True))
        self.assertEqual((self.db.cash(), self.db.physical(1), self.db.physical(2)), (D("50000.00"), D("100.000"), D("50.000")))

    def test_returns_are_limited_per_karat_and_for_cash_independently(self):
        self.refused("RETURN_EXCEEDS_CUSTODY", lambda: self.ret(self.trip, "10000.01"))
        self.refused("RETURN_EXCEEDS_CUSTODY", lambda: self.ret(self.trip, "", [(K21, "20.001")]))
        self.refused("RETURN_EXCEEDS_CUSTODY", lambda: self.ret(self.trip, "", [(K24, "0.001")]))      # a karat never issued
        self.ret(self.trip, "", [(K18, "5.500")])
        self.refused("RETURN_EXCEEDS_CUSTODY", lambda: self.ret(self.trip, "", [(K18, "0.001")]))      # already fully back
        self.ret(self.trip, "", [(K21, "20.000")])                                                      # 21K is independent of 18K
        message = self.refused("RETURN_EXCEEDS_CUSTODY", lambda: self.ret(self.trip, "10000.01")).args[0]
        self.assertIn("10,000.00", message)
        self.assertGreaterEqual(min(g["outstanding"] for g in self.custody()["gold"]), 0)

    def test_a_return_needs_an_existing_open_trip_and_at_least_one_quantity(self):
        self.refused("TRIP_NOT_FOUND", lambda: self.ret("", "1"))
        self.refused("TRIP_NOT_FOUND", lambda: self.ret("99", "1"))
        self.refused("NOTHING_TO_RECORD", lambda: self.ret(self.trip, "", []))

    def test_custody_is_never_mixed_with_available_scrap(self):
        self.assertEqual((self.db.physical(1), self.db.available(1)), (D("80.000"), D("75.000")))     # shop side, without the 20 g out
        context = self.service.context()
        row = next(s for s in context["shop_scrap"] if s["karat_value"] == 21)
        self.assertEqual((row["physical"], row["held"], row["available"]), (D("80.000"), D("5.000"), D("75.000")))
        self.assertEqual({g["karat_value"]: g["outstanding"] for g in self.custody()["gold"]}[21], D("20.000"))


# --------------------------------------------------------------------------- idempotency, atomicity, permissions
class GuardTests(TripCase):
    def test_same_key_same_payload_returns_the_original_and_posts_nothing_twice(self):
        first = self.issue("500", [(K21, "1")], request=key(1))
        before = self.state()
        again = self.issue("500", [(K21, "1")], request=key(1))
        self.assertEqual((again["entry_id"], again["code"], again["duplicate"]), (first["entry_id"], first["code"], True))
        self.assertEqual(self.state(), before)
        trip = first["trip_id"]
        ret = self.ret(trip, "100", request=key(2))
        before = self.state()
        self.assertTrue(self.ret(trip, "100", request=key(2))["duplicate"])
        self.assertEqual(self.state(), before)
        self.assertEqual(ret["entry_id"], self.ret(trip, "100", request=key(2))["entry_id"])

    def test_same_key_with_a_different_payload_user_or_trip_is_a_request_conflict(self):
        first = self.issue("500", request=key(1))
        for call in (lambda: self.issue("501", request=key(1)),
                     lambda: self.issue("500", [(K21, "1")], request=key(1)),
                     lambda: self.issue("500", notes="n", request=key(1)),
                     lambda: self.issue("500", trip=first["trip_id"], request=key(1))):
            self.refused("REQUEST_CONFLICT", call)
        other, _ = self.owner_like(OWNER, 21)
        self.refused("REQUEST_CONFLICT", lambda: self.issue("500", request=key(1), service=other))

    def test_a_concurrent_twin_that_meets_the_unique_key_is_resolved_from_a_fresh_read(self):
        # Simulated: the first read misses the committed winner, so the insert meets uq_mte_request. NOT a real race.
        winner = self.issue("700", request=key(5))
        original, calls = TripRepo.by_request, []

        def stale(repo, request_key):
            calls.append(request_key)
            return None if len(calls) == 1 else original(repo, request_key)
        with patch.object(TripRepo, "by_request", stale):
            before = self.state()
            twin = self.issue("700", request=key(5))
            self.assertEqual((twin["entry_id"], twin["duplicate"]), (winner["entry_id"], True))
            self.assertEqual(self.state(), before)
        calls.clear()
        with patch.object(TripRepo, "by_request", stale):
            before = self.state()
            with self.assertRaises(TripError) as context:
                self.issue("701", request=key(5))
            self.assertEqual(context.exception.code, "REQUEST_CONFLICT")
            self.assertEqual(self.state(), before)

    def test_only_the_request_key_violation_is_handled_other_integrity_errors_surface(self):
        def other(repo, **kw):
            raise IntegrityError("1062 (23000): Duplicate entry '1' for key 'market_trip_entry_gold.uq_mteg_entry_karat'", errno=1062)
        with patch.object(TripRepo, "insert_gold_leg", other):
            before = self.state()
            with self.assertRaises(IntegrityError):
                self.issue("", [(K21, "1")])
            self.assertEqual(self.state(), before)

    def test_a_failure_at_any_step_leaves_no_trip_entry_movement_or_audit(self):
        for step in ("trip", "entry", "cash", "scrap", "leg", "event", "audit"):
            with self.subTest(step=step):
                self.db.fail_at = step
                before = self.state()
                with self.assertRaises(RuntimeError):
                    self.issue("900", [(K21, "3")])
                self.db.fail_at = None
                self.assertEqual(self.state(), before)
        self.assertEqual(self.issue("900", [(K21, "3")])["code"], "MT000001-1")                      # numbering was not consumed

    def test_a_failing_return_is_atomic_too(self):
        trip = self.issue("900", [(K21, "3")])["trip_id"]
        for step in ("entry", "cash", "scrap", "leg", "audit"):
            self.db.fail_at = step
            before = self.state()
            with self.assertRaises(RuntimeError):
                self.ret(trip, "100", [(K21, "1")])
            self.assertEqual(self.state(), before)
        self.db.fail_at = None

    def test_only_the_owner_right_works_and_it_is_rechecked_against_the_database(self):
        trip = self.issue("900")["trip_id"]
        outsider, _ = self.owner_like(MANAGER, 31)
        self.assertFalse(outsider.can_manage())
        for call in (lambda: outsider.issue("", "1", [], TODAY, "", key(70)), lambda: outsider.return_custody(trip, "1", [], TODAY, "", key(71)),
                     lambda: outsider.close(trip), lambda: outsider.reopen(trip, "x"), lambda: outsider.reverse("1", "x"),
                     lambda: outsider.trips(), lambda: outsider.trip_detail(trip), lambda: outsider.context()):
            before = self.state()
            with self.assertRaises(PermissionError):
                call()
            self.assertEqual(self.state(), before)
        service, authorization = self.owner_like(OWNER, 32)
        authorization.permissions = OWNER - {"MARKET_TRIP_MANAGE"}                                # revoked after login
        self.assertTrue(service.can_manage())                                                      # the UI hint is stale ...
        for call in (lambda: service.issue("", "1", [], TODAY, "", key(72)), lambda: service.close(trip)):
            with self.assertRaises(PermissionError):
                call()                                                                             # ... the transaction refuses
        self.assertIn(("WRITE", "MARKET_TRIP_MANAGE"), authorization.checked)

    def test_the_migration_grants_the_right_to_the_owner_only(self):
        forward = (ROOT / "database" / "schema" / "037_market_trip_custody.sql").read_text(encoding="utf-8")
        self.assertIn("p.code='MARKET_TRIP_MANAGE'\n WHERE r.code = 'OWNER'", forward)
        for role in ("MANAGER", "CASHIER", "SALESPERSON"):
            self.assertNotIn(role, forward)

    def test_the_actor_is_the_session_user_and_no_identity_is_accepted(self):
        self.issue("1")
        self.assertEqual(next(iter(self.db.entries.values()))["performed_by_user_id"], 7)
        for method in (MarketTripService.issue, MarketTripService.return_custody, MarketTripService.close,
                       MarketTripService.reopen, MarketTripService.reverse):
            self.assertFalse(set(inspect.signature(method).parameters) & {"user_id", "employee_id", "actor", "permissions"})


# --------------------------------------------------------------------------- close and reopen
class CloseTests(TripCase):
    def setUp(self):
        super().setUp()
        self.trip = self.issue("1000", [(K21, "10")])["trip_id"]

    def test_closure_is_refused_with_the_exact_outstanding_quantities(self):
        error = self.refused("OUTSTANDING_CUSTODY", lambda: self.service.close(self.trip))
        self.assertIn("1,000.00 ج", error.args[0])
        self.assertIn("عيار 21 (للسبك): 10.000 جرام", error.args[0])
        self.ret(self.trip, "1000")
        self.assertIn("عيار 21", self.refused("OUTSTANDING_CUSTODY", lambda: self.service.close(self.trip)).args[0])      # gold still out
        self.ret(self.trip, "", [(K21, "9.999")])
        self.assertIn("0.001", self.refused("OUTSTANDING_CUSTODY", lambda: self.service.close(self.trip)).args[0])        # no rounding write-off
        self.assertEqual(self.db.trips[1]["status"], "OPEN")

    def test_closure_needs_exactly_zero_everywhere_and_adjusts_nothing(self):
        self.ret(self.trip, "1000", [(K21, "10")])
        before_money = (self.db.cash(), self.db.physical(1), len(self.db.cash_moves), len(self.db.scrap))
        result = self.service.close(self.trip)
        self.assertEqual((result["status"], self.db.trips[1]["status"]), ("CLOSED", "CLOSED"))
        self.assertEqual((self.db.cash(), self.db.physical(1), len(self.db.cash_moves), len(self.db.scrap)), before_money)
        self.assertEqual([e["event_type"] for e in self.db.events], ["OPENED", "CLOSED"])
        self.refused("TRIP_ALREADY_CLOSED", lambda: self.service.close(self.trip))

    def test_a_closed_trip_accepts_nothing_until_it_is_reopened(self):
        self.ret(self.trip, "1000", [(K21, "10")])
        self.service.close(self.trip)
        self.refused("TRIP_CLOSED", lambda: self.issue("1", trip=self.trip))
        self.refused("TRIP_CLOSED", lambda: self.ret(self.trip, "1"))
        self.refused("TRIP_CLOSED", lambda: self.corrections.reverse("MARKET_TRIP_ENTRY", "2", "x"), CorrectionError)
        self.refused("REASON_REQUIRED", lambda: self.service.reopen(self.trip, "  "))
        self.refused("TRIP_NOT_FOUND", lambda: self.service.reopen("99", "x"))
        self.service.reopen(self.trip, "تصحيح")
        self.refused("TRIP_NOT_CLOSED", lambda: self.service.reopen(self.trip, "again"))
        self.assertEqual([(e["event_type"], e["reason"]) for e in self.db.events][-1], ("REOPENED", "تصحيح"))
        self.assertEqual(self.issue("5", trip=self.trip)["code"], "MT000001-3")
        self.assertEqual(self.db.audit_rows[-1]["event_type"], "MARKET_TRIP_ISSUE")

    def test_reopening_is_audited_and_changes_no_balance(self):
        self.ret(self.trip, "1000", [(K21, "10")])
        self.service.close(self.trip)
        before = (self.db.cash(), self.db.physical(1), self.db.physical(2))
        self.service.reopen(self.trip, "سبب")
        self.assertEqual((self.db.cash(), self.db.physical(1), self.db.physical(2)), before)
        self.assertIn("MARKET_TRIP_REOPENED", [a["event_type"] for a in self.db.audit_rows])

    def test_the_detail_explains_closability_and_lists_the_history(self):
        detail = self.service.trip_detail(self.trip)
        self.assertEqual((detail["trip"]["status"], detail["custody"]["settled"]), ("OPEN", False))
        self.assertEqual([e["code"] for e in detail["entries"]], ["MT000001-1"])
        self.assertEqual(self.service.trips()[0]["custody"]["cash"]["outstanding"], D("1000.00"))


# --------------------------------------------------------------------------- reversal
class ReversalTests(TripCase):
    REASON = "إدخال بالخطأ"

    def setUp(self):
        super().setUp()
        self.trip = self.issue("10000", [(K21, "20")])["trip_id"]              # entry 1 -> id 1

    def reverse(self, entry="1", reason=None, service=None):
        return (service or self.service).reverse(entry, self.REASON if reason is None else reason)

    def test_reversing_an_issue_returns_cash_and_scrap_and_removes_it_from_custody(self):
        result = self.reverse("1")
        self.assertEqual((result["code"], result["source_reference"]), ("RV000001", "MT000001-1"))
        self.assertEqual((self.db.cash(), self.db.physical(1)), (D("50000.00"), D("100.000")))
        self.assertEqual(self.db.cash_moves[-1]["type_code"], "REVERSAL_PAYMENT")
        self.assertEqual(self.db.scrap[-1]["type_code"], "REVERSAL_TRIP_ISSUE_IN")
        custody = self.custody()
        self.assertEqual((custody["cash"]["outstanding"], custody["gold"], custody["settled"]), (D("0.00"), [], True))
        (correction,) = self.db.corrections
        self.assertEqual((correction["source_type"], correction["source_id"], correction["invoice_id"], correction["reason"]),
                         ("MARKET_TRIP_ENTRY", "1", None, self.REASON))
        originals = [m for m in self.db.scrap if m["reference_type"] == "MARKET_TRIP_ENTRY"]
        self.assertEqual([(m["weight_delta"], m["type_code"]) for m in originals], [(D("-20.000"), "TRIP_CUSTODY_OUT")])   # not rewritten

    def test_reversing_a_return_puts_it_back_into_custody_and_out_of_the_shop(self):
        self.ret(self.trip, "4000", [(K21, "8")])                               # entry 2
        self.reverse("2")
        self.assertEqual((self.db.cash(), self.db.physical(1)), (D("40000.00"), D("80.000")))
        self.assertEqual(self.db.scrap[-1]["type_code"], "REVERSAL_TRIP_RETURN_OUT")
        custody = self.custody()
        self.assertEqual((custody["cash"]["outstanding"], custody["gold"][0]["outstanding"]), (D("10000.00"), D("20.000")))

    def test_an_issue_with_a_dependent_unreversed_return_is_blocked_newest_first(self):
        self.ret(self.trip, "4000", [(K21, "8")])
        self.refused("DEPENDENT_RETURN_EXISTS", lambda: self.reverse("1"), CorrectionError)
        self.reverse("2")                                                        # the return first ...
        self.reverse("1")                                                        # ... then the issue
        self.assertEqual((self.db.cash(), self.db.physical(1)), (D("50000.00"), D("100.000")))

    def test_a_partial_dependency_per_karat_or_cash_also_blocks(self):
        self.issue("1000", [(K18, "5")], trip=self.trip)                          # entry 2
        self.ret(self.trip, "", [(K18, "5")])                                     # entry 3 returns the 18K
        self.refused("DEPENDENT_RETURN_EXISTS", lambda: self.reverse("2"), CorrectionError)
        self.reverse("1")                                                         # entry 1 (10,000 + 20 g 21K) has no dependent return

    def test_reversing_a_return_is_refused_when_the_shop_has_since_used_that_scrap(self):
        self.ret(self.trip, "", [(K21, "8")])
        self.db.scrap.append({"id": 900, "karat_id": 1, "weight_delta": D("-80.000"), "reference_type": "BILLING", "reference_id": "9",
                              "type_code": "SOLD"})                               # the shop consumed the returned grams
        error = self.refused("OLD_GOLD_DEPENDENCY_EXISTS", lambda: self.reverse("2"), CorrectionError)
        self.assertIn("المتاح", error.args[0])

    def test_a_double_reversal_a_missing_entry_and_a_blank_reason_are_refused(self):
        self.refused("REASON_REQUIRED", lambda: self.reverse("1", "  "), CorrectionError)
        self.refused("SOURCE_NOT_FOUND", lambda: self.reverse("77"), CorrectionError)
        self.reverse("1")
        self.refused("ALREADY_REVERSED", lambda: self.reverse("1"), CorrectionError)
        self.assertEqual(len(self.db.corrections), 1)

    def test_a_closed_trip_cannot_be_reversed_into(self):
        self.ret(self.trip, "10000", [(K21, "20")])
        self.service.close(self.trip)
        self.refused("TRIP_CLOSED", lambda: self.reverse("2"), CorrectionError)
        self.service.reopen(self.trip, "x")
        self.reverse("2")

    def test_reversal_needs_the_owner_right_and_the_correction_right_never_weakened(self):
        no_correct, _ = self.owner_like(OWNER_NO_CORRECT, 41)
        manager, _ = self.owner_like(MANAGER, 42)
        before = self.state()
        for service in (no_correct, manager):
            with self.assertRaises(PermissionError):
                self.reverse(service=service)
        self.assertEqual(self.state(), before)
        self.reverse()

    def test_the_owner_right_is_rechecked_inside_the_reversal_transaction(self):
        service, authorization = self.owner_like(OWNER, 43)
        authorization.permissions = OWNER - {"MARKET_TRIP_MANAGE"}
        before = self.state()
        with self.assertRaises(PermissionError):
            self.reverse(service=service)
        self.assertEqual(self.state(), before)

    def test_a_failure_at_any_reversal_step_changes_nothing(self):
        for step in ("correction", "reversal_cash", "reversal_scrap", "audit"):
            with self.subTest(step=step):
                self.db.fail_at = step
                before = self.state()
                with self.assertRaises(RuntimeError):
                    self.reverse()
                self.db.fail_at = None
                self.assertEqual(self.state(), before)
        self.reverse()

    def test_the_preview_states_the_exact_effects_and_the_blockers(self):
        preview = self.corrections.preview_reversal("MARKET_TRIP_ENTRY", "1")
        self.assertEqual((preview["can_reverse"], preview["money"], preview["old_gold"], preview["source_text"]),
                         (True, [{"account": "النقدية", "ledger": "CASH", "delta": "10000.00"}],
                          [{"karat": 21, "grams": "20.000"}], "حركة عهدة السوق"))
        self.ret(self.trip, "1", [])
        self.assertFalse(self.corrections.preview_reversal("MARKET_TRIP_ENTRY", "1")["can_reverse"])

    def test_a_trip_entry_is_never_settlement_corrected(self):
        for call in (lambda: self.corrections.current_settlement("MARKET_TRIP_ENTRY", "1"),):
            with self.assertRaises(CorrectionError) as context:
                call()
            self.assertEqual(context.exception.code, "SETTLEMENT_NOT_SUPPORTED")

    def test_after_a_reversal_the_trip_can_be_issued_again_and_numbers_continue(self):
        self.reverse("1")
        again = self.issue("200", trip=self.trip)
        self.assertEqual(again["code"], "MT000001-2")
        self.assertEqual(self.custody()["cash"]["outstanding"], D("200.00"))


class ViewModelTests(TripCase):
    """MarketTripViewModel over the same services, with the worker run synchronously."""

    def setUp(self):
        super().setUp()
        from app.viewmodels.market_trip_viewmodel import MarketTripViewModel
        self.vm = MarketTripViewModel(self.session, self.service)
        actor = self.session.current_actor()

        def start(operation, call):
            try:
                result, error = call(_expected_actor=actor), ""
            except (ValueError, PermissionError) as exc:
                result, error = None, str(exc)
            getattr(self.vm, f"_on_{operation}")(result, error)
            self.vm.changed.emit()
        self.vm._start = start
        self.signals = {"custody": 0, "entry": [], "action": []}
        self.vm.custodyChanged.connect(lambda: self.signals.__setitem__("custody", self.signals["custody"] + 1))
        self.vm.entryPosted.connect(self.signals["entry"].append)
        self.vm.actionDone.connect(self.signals["action"].append)

    def test_the_right_hint_and_the_limitation_text(self):
        self.assertTrue(self.vm.canManage)
        self.assertFalse(type(self.vm)(session_for(MANAGER), self.service).canManage)
        self.assertIn("إرجاع كل النقدية وكل الذهب", self.vm.limitation)

    def test_notification_follows_only_a_committed_money_change(self):
        self.vm.issue("", "700", [{"karatId": K21, "weight": "10", "designation": "MELT"}], TODAY, "", self.vm.newRequestKey())
        self.assertEqual((self.signals["custody"], self.signals["entry"]), (1, ["MT000001-1"]))
        self.assertEqual(self.vm.message, "تم إخراج العهدة MT000001-1")
        self.vm.issue("", "", [{"karatId": K24, "weight": "1", "designation": "MELT"}], TODAY, "", self.vm.newRequestKey())              # refused: no 24K
        self.assertEqual((self.signals["custody"], len(self.signals["entry"])), (1, 1))
        self.assertIn("غير كافٍ", self.vm.errorMessage)
        self.vm.returnCustody(self.vm.selectedTripId, "700", [{"karatId": K21, "weight": "10", "designation": "MELT"}], TODAY, "", self.vm.newRequestKey())
        self.assertEqual(self.signals["custody"], 2)
        self.vm.closeTrip(self.vm.selectedTripId)
        self.assertEqual((self.signals["custody"], self.signals["action"]), (2, ["close"]))                       # no balance moved
        self.vm.reopenTrip(self.vm.selectedTripId, "سبب")
        self.assertEqual((self.signals["custody"], self.signals["action"]), (2, ["close", "reopen"]))
        self.vm.reverseEntry("2", "غلط")
        self.assertEqual((self.signals["custody"], self.signals["action"][-1]), (3, "reverse"))

    def test_the_same_request_key_is_reported_as_already_recorded_and_a_second_submit_is_ignored(self):
        request = self.vm.newRequestKey()
        self.vm.issue("", "5", [], TODAY, "", request)
        self.vm.issue("", "5", [], TODAY, "", request)
        self.assertIn("مسجلة من قبل", self.vm.message)
        self.assertEqual(len(self.db.entries), 1)
        calls = []
        self.vm._service = type("S", (), {"issue": lambda s, *a, **k: calls.append(a)})()
        self.vm._busy.add("write")
        self.vm.issue("", "5", [], TODAY, "", self.vm.newRequestKey())
        self.assertEqual(calls, [])

    def test_the_detail_separates_custody_from_the_shop_and_explains_what_blocks_closing(self):
        self.vm.refresh()
        self.vm.issue("", "10000", [{"karatId": K21, "weight": "20", "designation": "MELT"}], TODAY, "", self.vm.newRequestKey())
        detail = self.vm.detail
        self.assertEqual((detail["cash"]["outstanding"], detail["gold"][0]["karat"], detail["gold"][0]["outstanding"], detail["closable"]),
                         ("10,000.00 ج", 21, "20.000", False))
        self.assertIn("نقدية 10,000.00 ج • عيار 21 (للسبك): 20.000 جرام", detail["closeHint"])
        context = self.vm.context
        self.assertEqual(context["safeBalanceText"], "40,000.00 ج")                                              # the SHOP side
        self.assertIn("عيار 21: المتاح 75.000 جرام • محجوز لمراجعة المدير 5.000", " ".join(r["text"] for r in context["shop"]))
        self.assertEqual([k["text"] for k in context["karats"]], ["عيار 24", "عيار 21", "عيار 18"])
        row = detail["entries"][0]
        self.assertEqual((row["code"], row["kindText"], row["canReverse"]), ("MT000001-1", "إخراج عهدة", True))
        self.assertEqual(self.vm.trips[0]["outstandingText"], "نقدية 10,000.00 ج • عيار 21 (للسبك): 20.000 جرام")


class RulesTests(unittest.TestCase):
    def test_parsers_are_exact(self):
        self.assertEqual((parse_cash("1,250.5"), parse_cash(""), parse_cash("0")), (D("1250.50"), D("0.00"), D("0.00")))
        self.assertEqual((parse_weight("12.345"), parse_weight(None)), (D("12.345"), D("0.000")))
        for bad in ("-1", "1.001"):
            with self.assertRaises(TripError):
                parse_cash(bad)
        for bad in ("-1", "0.0001"):
            with self.assertRaises(TripError):
                parse_weight(bad)

    def test_the_document_prefix_is_distinct(self):
        from repositories.market_trip_repository import entry_code, trip_code
        self.assertEqual((trip_code(1), entry_code(12, 3)), ("MT000001", "MT000012-3"))


if __name__ == "__main__":
    unittest.main()
