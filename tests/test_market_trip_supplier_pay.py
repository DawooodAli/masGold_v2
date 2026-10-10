"""Supplier making payment from market custody (043, «دفع مصنعيات من العهدة»): cash already in an OPEN custody pays a supplier's making,
so Main Safe moves only at issue and return. Real MarketTripService and the real CorrectionService over the in-memory transactional
database of tests/market_trip_fakes.py (a failed transaction restores every table; the 043 CHECK / unique constraints are mirrored as
asserts). NOT real-MySQL tests: the SQL, the constraints and the rollback were exercised on the masgold_rehearsal sandbox,
single-threaded. Concurrency is not exercised (the trip and supplier row locks are inferred from the code, not proven here)."""
import copy
from decimal import Decimal as D
import unittest
from uuid import uuid4

from services.correction_service import CorrectionError
from services.market_trip_service import TripError
from tests.market_trip_fakes import MANAGER, OWNER, OWNER_NO_CORRECT, SupplierPayRepo
from tests.test_market_trips import K21, TODAY, TripCase, key

ACTIVE, INACTIVE = "10", "11"


class PayCase(TripCase):
    def setUp(self):
        super().setUp()
        self.trip = self.issue("10000")["trip_id"]
        self.suppliers = SupplierPayRepo(self.db)

    def pay(self, amount, supplier=ACTIVE, trip=None, request=None, service=None, reference="", notes="", when=TODAY):
        self.n += 1
        return (service or self.service).pay_supplier(trip or self.trip, supplier, amount, when, reference, notes, request or key(4000 + self.n))

    def balance(self, supplier=ACTIVE):
        return self.suppliers.making_balance_current(supplier)

    def outstanding(self, trip=None):
        return self.service.trip_detail(trip or self.trip)["custody"]["cash"]["outstanding"]

    def denied(self, call):
        before = self.state()
        with self.assertRaises(PermissionError):
            call()
        self.assertEqual(self.state(), before, "a denied request left a trace")

    def reverse(self, result, reason="تصحيح", service=None):
        return (service or self.service).reverse(result["entry_id"], reason)


class PaymentTests(PayCase):
    def test_the_example_issue_pay_return_nets_to_the_payment_with_no_second_cash_out(self):
        moves = len(self.db.cash_moves)
        done = self.pay("7000", reference="ف-7")
        self.assertEqual((done["kind"], done["amount"], done["balance_before"], done["balance_after"]), ("SUPPLIER_PAY", "7000.00", "0.00", "-7000.00"))
        self.assertEqual(len(self.db.cash_moves), moves)                                       # no Main Safe / bank movement at payment
        self.assertEqual(self.db.cash(), D("40000.00"))
        self.assertEqual(self.outstanding(), D("3000.00"))
        self.ret(self.trip, "3000")
        self.assertEqual(self.db.cash(), D("43000.00"))                                        # net Main Safe reduction: exactly 7,000
        detail = self.service.trip_detail(self.trip)
        self.assertEqual((detail["custody"]["cash"]["paid"], detail["custody"]["cash"]["outstanding"], detail["custody"]["settled"]), (D("7000.00"), D("0.00"), True))
        self.assertEqual(self.service.close(self.trip)["status"], "CLOSED")
        self.assertEqual(self.balance(), D("-7000.00"))                                        # the supplier owes less (net)
        payment = next(iter(self.db.sm_payments.values()))
        self.assertEqual((payment["payment_method"], payment["cash_account_id"], payment["bank_account_id"], payment["reference"]), ("CUSTODY", None, None, "ف-7"))
        self.assertEqual([l["leg_kind"] for l in self.db.sm_legs], ["MAKING"])                  # no MONEY leg
        self.assertEqual(self.db.scrap[2:], [])                                                # no Scrap, and no supplier gold exists to touch
        row = next(e for e in detail["entries"] if e["kind"] == "SUPPLIER_PAY")
        self.assertEqual((row["supplier_payment"]["code"], row["supplier_payment"]["supplier_name"], row["supplier_payment"]["amount"]),
                         ("SM000001", "مورد نشط", D("7000.00")))

    def test_the_payment_is_capped_at_the_outstanding_custody_cash_and_may_take_all_of_it(self):
        self.refused("PAYMENT_EXCEEDS_CUSTODY_CASH", lambda: self.pay("10000.01"))
        self.pay("4000")
        self.refused("PAYMENT_EXCEEDS_CUSTODY_CASH", lambda: self.pay("6000.01"))
        self.pay("6000")                                                                       # the entire outstanding cash
        self.assertEqual(self.outstanding(), D("0.00"))
        self.assertTrue(self.service.trip_detail(self.trip)["custody"]["settled"])
        self.refused("PAYMENT_EXCEEDS_CUSTODY_CASH", lambda: self.pay("0.01"))

    def test_an_inactive_supplier_is_paid_and_a_credit_balance_is_allowed(self):
        done = self.pay("2500", supplier=INACTIVE)
        self.assertEqual(self.balance(INACTIVE), D("-2500.00"))                                # crossed zero into supplier credit
        self.pay("100", supplier=INACTIVE)
        self.assertEqual(self.balance(INACTIVE), D("-2600.00"))
        self.assertEqual(done["supplier_id"], INACTIVE)

    def test_refusals_leave_no_trace(self):
        self.refused("AMOUNT_REQUIRED", lambda: self.pay(""))
        self.refused("AMOUNT_REQUIRED", lambda: self.pay("0"))
        self.refused("CASH_INVALID", lambda: self.pay("-5"))
        self.refused("CASH_INVALID", lambda: self.pay("1.005"))
        self.refused("SUPPLIER_NOT_FOUND", lambda: self.pay("1", supplier="999"))
        self.refused("SUPPLIER_NOT_FOUND", lambda: self.pay("1", supplier="x"))
        self.refused("TRIP_NOT_FOUND", lambda: self.pay("1", trip="999"))
        self.refused("REQUEST_INVALID", lambda: self.pay("1", request="nope"))
        self.refused("DATE_INVALID", lambda: self.pay("1", when="2026-13-45"))
        self.denied(lambda: self.pay("1", service=self.owner_like(MANAGER)[0]))                # no MARKET_TRIP_MANAGE
        self.denied(lambda: self.pay("1", service=self.owner_like(OWNER - {"SUPPLIER_MAKING_PAY"})[0]))   # no SUPPLIER_MAKING_PAY
        self.assertEqual(self.db.sm_payments, {})

    def test_an_intermediary_only_party_cannot_be_paid_making_and_is_not_offered(self):
        self.refused("SUPPLIER_ROLE_REQUIRED", lambda: self.pay("100", supplier="12"))             # the role is enforced by the service
        self.assertEqual(self.db.sm_payments, {})
        self.assertEqual([s["id"] for s in self.service.context()["suppliers"]], ["10", "11"])      # and the dialog never lists it

    def test_a_closed_trip_refuses_until_it_is_reopened(self):
        self.ret(self.trip, "10000")
        self.service.close(self.trip)
        self.refused("TRIP_CLOSED", lambda: self.pay("1"))
        self.service.reopen(self.trip, "مراجعة")
        self.refused("PAYMENT_EXCEEDS_CUSTODY_CASH", lambda: self.pay("1"))                    # open again, but nothing is left in custody

    def test_a_failure_at_any_posting_step_persists_nothing(self):
        before = self.state()
        for step in ("sm_header", "entry", "sm_making", "sm_leg", "link", "sm_audit", "audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.pay("7000")
            self.db.fail_at = None
            self.assertEqual(self.state(), before, f"a failure at {step} left a trace")
        self.pay("7000")
        self.assertEqual(len(self.db.sm_payments), 1)

    def test_replay_returns_the_original_and_conflicting_reuse_is_refused_without_effects(self):
        request = key(4900)
        first = self.pay("3000", request=request, reference="a")
        after = self.state()
        again = self.pay("3000", request=request, reference="a")
        self.assertEqual((again["duplicate"], again["entry_id"], again["payment_code"], again["code"]),
                         (True, first["entry_id"], first["payment_code"], first["code"]))
        self.assertEqual(self.state(), after)
        other_trip = self.issue("500")["trip_id"]
        after = self.state()
        self.refused("REQUEST_CONFLICT", lambda: self.pay("3001", request=request, reference="a"))               # amount
        self.refused("REQUEST_CONFLICT", lambda: self.pay("3000", supplier=INACTIVE, request=request, reference="a"))  # supplier
        self.refused("REQUEST_CONFLICT", lambda: self.pay("3000", trip=other_trip, request=request, reference="a"))    # trip
        self.refused("REQUEST_CONFLICT", lambda: self.pay("3000", request=request, reference="b"))                # reference
        self.assertEqual(self.state(), after)
        self.refused("REQUEST_CONFLICT", lambda: self.pay("3000", request=request, reference="a", service=self.owner_like(OWNER, user_id=99)[0]))   # another user

    def test_the_same_key_in_another_flow_is_refused_in_both_directions(self):
        request = key(4950)
        self.n += 1
        self.service.issue(self.trip, "10", [], TODAY, "", request)                            # the key is an ENTRY key of an issue
        self.refused("REQUEST_CONFLICT", lambda: self.pay("5", request=request))
        direct = key(4951)
        self.suppliers.insert_header(supplier_id=10, method="CASH", cash_account_id=1, bank_account_id=None, amount=D("5.00"),
                                     payment_date=TODAY, reference=None, notes=None, before=D("0.00"), after=D("-5.00"), key=direct,
                                     digest="d" * 64, actor=self.session.require_authenticated(), now=self.db.now)   # a DIRECT payment's key
        self.refused("REQUEST_CONFLICT", lambda: self.pay("5", request=direct))
        done = self.pay("5", request=key(4952))
        self.refused("REQUEST_CONFLICT", lambda: self.service.issue(self.trip, "10", [], TODAY, "", key(4952)))   # and an issue reusing a payment key
        self.assertEqual(done["kind"], "SUPPLIER_PAY")


class ReversalTests(PayCase):
    def test_two_payments_then_reversing_one_restores_only_its_stored_amount(self):
        first, second = self.pay("3000"), self.pay("2000", supplier=INACTIVE)
        moves = len(self.db.cash_moves)
        self.assertEqual((self.outstanding(), self.balance(), self.balance(INACTIVE)), (D("5000.00"), D("-3000.00"), D("-2000.00")))
        done = self.reverse(first, "دفعة خاطئة")
        self.assertTrue(done["code"].startswith("RV"))
        self.assertEqual((self.outstanding(), self.balance(), self.balance(INACTIVE)), (D("8000.00"), D("0.00"), D("-2000.00")))   # only 3,000 came back
        self.assertEqual(len(self.db.cash_moves), moves)                                       # no money returned to Main Safe automatically
        reversal_row = self.db.sm_making[-1]
        self.assertEqual((reversal_row["type_id"], reversal_row["amount_delta"]), (2, D("3000.00")))                  # the STORED amount
        detail = self.service.trip_detail(self.trip)
        self.assertEqual(detail["custody"]["cash"]["paid"], D("2000.00"))
        flags = {e["supplier_payment"]["code"]: e["reversed"] for e in detail["entries"] if e["supplier_payment"]}
        self.assertEqual(flags, {"SM000001": True, "SM000002": False})
        self.refused("ALREADY_REVERSED", lambda: self.reverse(first), error=CorrectionError)
        self.reverse(second)
        self.assertEqual((self.outstanding(), self.balance(INACTIVE)), (D("10000.00"), D("0.00")))

    def test_the_issue_cannot_be_reversed_while_a_payment_depends_on_it(self):
        paid = self.pay("7000")
        issue_entry = next(e["id"] for e in self.db.entries.values() if e["trip_id"] == int(self.trip) and e["entry_kind"] == "ISSUE")
        self.refused("DEPENDENT_RETURN_EXISTS", lambda: self.service.reverse(str(issue_entry), "x"), error=CorrectionError)
        self.reverse(paid)
        self.service.reverse(str(issue_entry), "x")                                            # the dependency is gone

    def test_all_three_reversal_rights_are_required_and_the_trip_must_be_open(self):
        paid = self.pay("7000")
        for rights in (OWNER - {"SUPPLIER_MAKING_PAY_REVERSE"}, OWNER_NO_CORRECT | {"SUPPLIER_MAKING_PAY_REVERSE"}, OWNER - {"MARKET_TRIP_MANAGE"}):
            self.denied(lambda: self.reverse(paid, service=self.owner_like(rights)[0]))
        self.ret(self.trip, "3000")
        self.service.close(self.trip)
        self.refused("TRIP_CLOSED", lambda: self.reverse(paid), error=CorrectionError)
        self.service.reopen(self.trip, "مراجعة")
        self.reverse(paid)
        self.assertEqual(self.outstanding(), D("7000.00"))                                     # the money is recorded as back IN CUSTODY

    def test_a_failure_inside_the_reversal_changes_nothing(self):
        paid = self.pay("7000")
        before = self.state()
        for step in ("correction", "sm_making", "audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.reverse(paid)
            self.db.fail_at = None
            self.assertEqual(self.state(), before, step)

    def test_the_supplier_side_reversal_of_a_custody_payment_is_refused_with_guidance_and_no_effects(self):
        paid = self.pay("7000")
        before = self.state()
        with self.assertRaises(CorrectionError) as context:
            self.corrections.reverse("SUPPLIER_MAKING_PAYMENT", paid["payment_id"], "من صفحة المورد")
        self.assertEqual(context.exception.code, "CUSTODY_PAYMENT_REVERSE_FROM_TRIP")
        self.assertIn("عهدة السوق", str(context.exception))
        self.assertIn(paid["code"], str(context.exception))                                    # it names the custody entry to reverse
        self.assertEqual(self.state(), before)

    def test_the_supplier_document_and_the_custody_entry_share_one_reversal_record(self):
        paid = self.pay("7000")
        self.reverse(paid)
        links = self.db.supplier_links
        reversals = [c for c in self.db.corrections if c["correction_type"] == "FULL_REVERSAL"]
        self.assertEqual([(c["source_type"], c["source_id"]) for c in reversals], [("MARKET_TRIP_ENTRY", str(paid["entry_id"]))])
        self.assertEqual(links[int(paid["entry_id"])]["payment_id"], int(paid["payment_id"]))   # the SM document resolves its state through the link


if __name__ == "__main__":
    unittest.main()
