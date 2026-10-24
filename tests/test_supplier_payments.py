"""Purchasing & Suppliers Phase 2A — supplier MAKING payments (SMxxxxxx) and their reversal.

Real services (SupplierPaymentService, the real CorrectionService, SupplierViewModel) over the in-memory transactional
database of tests/supplier_fakes.py: a failed transaction restores every table, so atomicity is testable. The SQL itself
is proven by the schema tests and the rehearsal run of migration 036; real-MySQL concurrency is NOT proven here."""
import copy
import dataclasses
import inspect
from mysql.connector import IntegrityError
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
import unittest

from app.session import AppSession
from app.viewmodels.supplier_viewmodel import SupplierViewModel
from repositories.correction_repository import SOURCE_REFERENCE
from repositories.supplier_payment_repository import SOURCE, payment_code
from services import supplier_rules as rules
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.correction_service import CorrectionError, CorrectionService
from services.supplier_payment_service import SupplierPaymentService
from services.supplier_rules import SupplierRuleError
from tests.auth_fakes import FakeAuthorization
from tests.supplier_fakes import (AccountsRepo, CASHIER_RIGHTS, MANAGER_RIGHTS, OWNER_RIGHTS, PaymentRepo, build_payments,
                                  session_for)

ROOT = Path(__file__).resolve().parents[1]
TODAY = "2026-10-09"


def key(n):
    return "22222222-2222-4222-8222-%012d" % n


# --------------------------------------------------------------------------- fakes of the CorrectionRepository contract
class Corrections:
    """The subset of CorrectionRepository that a standalone full reversal uses, with the 023/036 database contracts."""

    def __init__(self, db):
        self.db = db

    def corrections_for_source(self, source_type, source_id):
        return [{"correction_id": c["correction_id"], "correction_type": c["correction_type"], "chain_sequence": None,
                 "after_json": None, "occurred_at": c["occurred_at"]}
                for c in self.db.corrections if (c["source_type"], c["source_id"]) == (source_type, str(source_id))]

    def money_by_account(self, source_type, source_id, correction_ids=()):
        reference = SOURCE_REFERENCE[source_type]                      # the REAL mapping: a missing entry fails the lookup
        found, out = {(reference, str(source_id))} | {("TRANSACTION_CORRECTION", str(c)) for c in correction_ids}, {}
        for ledger, moves in (("CASH", self.db.cash_moves), ("BANK", self.db.bank_moves)):
            for m in moves:
                if (m["reference_type"], m["reference_id"]) in found:
                    out[(ledger, m["account_id"])] = out.get((ledger, m["account_id"]), D("0.00")) + m["amount_delta"]
        return out

    def account_by_customer(self, source_type, source_id, correction_ids=()):
        return {}

    def db_now(self):
        return self.db.now

    def _fail(self, step):
        if self.db.fail_at == step:
            raise RuntimeError(f"forced failure at {step}")

    def insert_correction(self, **kw):
        self._fail("correction")
        assert kw["reason"].strip()                                                    # chk_correction_reason
        assert kw["source_type"] == SOURCE and kw["invoice_id"] is None                # chk_correction_source (036)
        assert kw["correction_type"] == "FULL_REVERSAL"
        if any((c["source_type"], c["source_id"], c["correction_type"]) == (kw["source_type"], str(kw["source_id"]), "FULL_REVERSAL")
               for c in self.db.corrections):
            raise RuntimeError("Duplicate entry for key uq_correction_one_reversal")
        correction_id = str(self.db.next("correction"))
        self.db.corrections.append({"correction_id": correction_id, "correction_type": kw["correction_type"],
                                    "source_type": kw["source_type"], "source_id": str(kw["source_id"]),
                                    "source_reference": kw["source_reference"], "reason": kw["reason"],
                                    "occurred_at": kw["occurred_at"], "invoice_id": kw["invoice_id"],
                                    "user_id": kw["performed_by_user_id"]})
        return correction_id

    def insert_leg(self, **kw):
        self._fail("leg")

    def add_money(self, *, ledger, account_id, delta, type_code, correction_id, **kw):
        self._fail("reversal_money")
        assert type_code == "REVERSAL_PAYMENT"
        table = self.db.cash_moves if ledger == "CASH" else self.db.bank_moves
        table.append({"id": self.db.next("cash_move" if ledger == "CASH" else "bank_move"), "account_id": int(account_id),
                      "amount_delta": delta, "reference_type": "TRANSACTION_CORRECTION", "reference_id": str(correction_id),
                      "type_code": type_code})
        return str(len(table))

    def lock_items(self, ids):
        return {}


class Invoices:
    def __init__(self, db):
        self.db = db

    def active_bank_accounts(self):
        return [dict(b) for b in self.db.bank_accounts.values() if b["is_active"]]


class Customers:
    def __init__(self, db):
        pass


class MarketerPaymentRepo:
    """The marketer cash-ledger subset used by supplier-making payments and their canonical correction."""

    def __init__(self, db):
        self.db = db

    def cash_payment_sources(self):
        rows = []
        for party in self.db.suppliers.values():
            if not party.get("is_intermediary"):
                continue
            balance = self.cash_balance(party["id"])
            if balance > 0:
                rows.append({**party, "balance": balance})
        return rows

    def by_request(self, request_key):
        return next((d for d in self.db.marketer_documents.values() if d["request_key"] == request_key), None)

    def key_used_elsewhere(self, request_key):
        return any(p["request_key"] == request_key for p in self.db.payments.values())

    def party(self, party_id, lock=False):
        return self.db.suppliers.get(int(party_id))

    def cash_balance(self, party_id):
        return sum((m["amount_delta"] for m in self.db.marketer_cash_moves if m["party_id"] == int(party_id)), D("0.00"))

    def gold_balance(self, party_id):
        return D("0.000")

    def insert_document(self, *, kind, party_id, cash_amount, document_date, reference, notes, key, digest, actor, now, **unused):
        if any(d["request_key"] == key for d in self.db.marketer_documents.values()):
            raise IntegrityError(f"1062 (23000): Duplicate request key {key}", errno=1062)
        document_id = self.db.next("marketer_document")
        self.db.marketer_documents[document_id] = {
            "id": document_id, "doc_kind": kind, "party_id": int(party_id), "cash_amount": cash_amount,
            "document_date": document_date, "reference": reference, "notes": notes, "request_key": key,
            "request_hash": digest, "performed_by_user_id": actor.user_id, "occurred_at": now}
        return document_id

    def insert_cash_movement(self, *, party_id, document_id, doc_kind, movement_type, amount, delta, correction_id=None):
        movement_id = self.db.next("marketer_cash_move")
        self.db.marketer_cash_moves.append({
            "id": movement_id, "party_id": int(party_id), "document_id": int(document_id), "doc_kind": doc_kind,
            "movement_type": movement_type, "cash_amount": amount, "amount_delta": delta, "correction_id": correction_id})
        return movement_id

    def insert_supplier_payment_link(self, *, payment_id, document_id, party_id, amount):
        self.db.marketer_payment_links[int(payment_id)] = {
            "payment_id": int(payment_id), "marketer_document_id": int(document_id), "doc_kind": "SUPPLIER_PAY",
            "marketer_party_id": int(party_id), "amount": amount}

    def audit(self, *, actor, event_type, entity_id, reference, description, metadata, now, entity_type):
        self.db.audit_rows.append({"user_id": actor.user_id, "event_type": event_type, "entity_type": entity_type,
                                   "entity_id": entity_id, "reference": reference, "metadata": metadata})


class PaymentCase(unittest.TestCase):
    """One supplier (21K) with a posted invoice: making owed to the supplier = 5,000.00; gold 50.000 g."""

    def setUp(self):
        self.db, self.session, self.authorization, self.suppliers, self.purchases, self.payments = build_payments()
        self.supplier = self.suppliers.create_supplier("مورد المصنعيات", None, None, 1)
        draft = self.purchases.create_draft(self.supplier, 1, "INV-1", TODAY, [{"weight": "50", "making_per_gram": "100"}], "",
                                            key(900))
        self.purchases.post_invoice(draft["id"], key(901))
        self.n = 0
        for target, value in (("repositories.supplier_payment_repository.SupplierPaymentRepository", PaymentRepo),
                              ("repositories.financial_account_repository.FinancialAccountRepository", AccountsRepo),
                              ("repositories.marketer_custody_repository.MarketerCustodyRepository", MarketerPaymentRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)
        self.corrections = self.correction_service(self.session, self.authorization)
        self.payments._corrections = self.corrections

    def correction_service(self, session, authorization):
        return CorrectionService(session, authorization=authorization, transaction_factory=self.db.transaction,
                                 invoice_repository=Invoices, correction_repository=Corrections, customer_repository=Customers)

    # ------------------------------------------------------------ helpers
    def pay(self, amount="1000", method="BANK", bank="5", when=TODAY, reference="", notes="", request=None, service=None):
        self.n += 1
        return (service or self.payments).pay(self.supplier, method, bank, amount, when, reference, notes,
                                              request or key(self.n))

    def making(self):
        return sum((m["amount_delta"] for m in self.db.making if m["supplier_id"] == int(self.supplier)), D("0.00"))

    def gold(self):
        return sum((m["weight_delta_grams"] for m in self.db.gold), D("0.000"))

    def cash(self, account=1):
        return sum((m["amount_delta"] for m in self.db.cash_moves if m["account_id"] == account), D("0.00"))

    def bank(self, account):
        return sum((m["amount_delta"] for m in self.db.bank_moves if m["account_id"] == account), D("0.00"))

    def marketer(self, name="مسوقجي دفعات", cash="2000.00", *, same_as_supplier=False):
        if same_as_supplier:
            party_id = int(self.supplier)
            self.db.suppliers[party_id]["is_intermediary"] = True
        else:
            party_id = int(self.suppliers.create_supplier(name, karat_id=None, is_supplier=False, is_intermediary=True))
        self.db.marketer_cash_moves.append({"id": self.db.next("marketer_cash_move"), "party_id": party_id,
                                            "document_id": None, "doc_kind": "OPENING", "movement_type": "OPENING",
                                            "cash_amount": D(cash), "amount_delta": D(cash), "correction_id": None})
        return str(party_id)

    def state(self):
        return copy.deepcopy({k: v for k, v in vars(self.db).items() if k not in ("fail_at", "transactions", "lock_log")})

    def refused(self, code, call, error=SupplierRuleError):
        before = self.state()
        with self.assertRaises(error) as context:
            call()
        self.assertEqual(getattr(context.exception, "code", None), code)
        self.assertEqual(self.state(), before, "a refused request left a trace")
        return context.exception

    def service_for(self, permissions, user_id=8):
        session = session_for(permissions, user_id=user_id)
        authorization = FakeAuthorization(session)
        service = SupplierPaymentService(session, transaction_factory=self.db.transaction, repository=PaymentRepo,
                                         accounts=AccountsRepo, authorization=authorization,
                                         corrections=self.correction_service(session, authorization))
        return service, session, authorization

    def service_with_marketer_right(self, user_id=8):
        return self.service_for(OWNER_RIGHTS | {"INTERMEDIARY_HOLDING_MANAGE", "INTERMEDIARY_HOLDING_REVERSE"},
                                user_id)[0]


# --------------------------------------------------------------------------- the payment
class PaymentPostingTests(PaymentCase):
    def test_a_bank_payment_posts_one_money_out_and_one_negative_making_movement(self):
        gold_before = self.gold()
        result = self.pay("1000.00", reference="إيصال 77", notes="دفعة أولى")
        self.assertEqual((result["code"], result["amount"], result["balance_before"], result["balance_after"],
                          result["duplicate"]), ("SM000001", "1000.00", "5000.00", "4000.00", False))
        (money,) = self.db.bank_moves
        self.assertEqual((money["account_id"], money["amount_delta"], money["reference_type"], money["reference_id"],
                          money["reference_code"], money["type_code"]), (5, D("-1000.00"), SOURCE, "1", "SM000001", SOURCE))
        self.assertEqual(self.db.cash_moves, [])
        made = [m for m in self.db.making if m["source_type"] == SOURCE]
        self.assertEqual([(m["amount_delta"], m["source_id"], m["source_reference"], m["movement_type_id"]) for m in made],
                         [(D("-1000.00"), 1, "SM000001", 2)])
        self.assertEqual(self.making(), D("4000.00"))
        self.assertEqual(self.gold(), gold_before)                       # the supplier GOLD account never moves
        self.assertEqual(sorted((l["leg_kind"], l["bank_movement_id"], l["making_movement_id"]) for l in self.db.legs),
                         [("MAKING", None, made[0]["id"]), ("MONEY", money["id"], None)])
        (payment,) = self.db.payments.values()
        self.assertEqual((payment["performed_by_user_id"], payment["payment_method"], payment["cash_account_id"],
                          payment["bank_account_id"], payment["reference"], payment["notes"], payment["balance_after"]),
                         (7, "BANK", None, 5, "إيصال 77", "دفعة أولى", D("4000.00")))
        audit = self.db.audit_rows[-1]
        self.assertEqual((audit["event_type"], audit["entity_type"], audit["reference"]),
                         ("SUPPLIER_MAKING_PAYMENT", SOURCE, "SM000001"))

    def test_a_bank_payment_uses_the_selected_active_account_and_never_touches_the_safe(self):
        self.pay("2500.50", method="BANK", bank="5")
        self.pay("100", method="BANK", bank="6")                          # a wallet is a bank-ledger account too
        self.assertEqual((self.bank(5), self.bank(6), self.db.cash_moves), (D("-2500.50"), D("-100.00"), []))
        self.assertEqual(self.making(), D("2399.50"))
        self.assertEqual([(p["payment_method"], p["bank_account_id"], p["cash_account_id"]) for p in self.db.payments.values()],
                         [("BANK", 5, None), ("BANK", 6, None)])

    def test_main_safe_is_not_a_supplier_making_payment_source(self):
        self.refused("METHOD_INVALID", lambda: self.pay("300", method="CASH", bank=None))
        self.assertEqual((self.db.payments, self.db.cash_moves, self.db.bank_moves), ({}, [], []))
        source = (ROOT / "services" / "supplier_payment_service.py").read_text(encoding="utf-8")
        self.assertNotIn("main_safe()", source)

    def test_a_bank_payment_needs_an_existing_active_account(self):
        self.refused("ACCOUNT_REQUIRED", lambda: self.pay(method="BANK", bank=None))
        self.refused("ACCOUNT_REQUIRED", lambda: self.pay(method="BANK", bank=""))
        self.refused("ACCOUNT_REQUIRED", lambda: self.pay(method="BANK", bank="0"))
        self.refused("ACCOUNT_NOT_FOUND", lambda: self.pay(method="BANK", bank="99"))
        self.refused("ACCOUNT_INACTIVE", lambda: self.pay(method="BANK", bank="7"))
        self.assertEqual((self.db.payments, self.db.bank_moves), ({}, []))

    def test_cash_is_refused_even_when_a_bank_account_is_also_supplied(self):
        self.refused("METHOD_INVALID", lambda: self.pay("100", method="CASH", bank="5"))
        self.assertEqual((self.cash(1), self.bank(5)), (D("0.00"), D("0.00")))

    def test_a_marketer_cash_payment_debits_only_that_marketer_and_records_confirmation_state(self):
        marketer_id = self.marketer(cash="1500")
        service = self.service_with_marketer_right()
        context = service.payment_context(self.supplier)
        self.assertEqual([(row["id"], row["cash"]) for row in context["marketers"]], [(marketer_id, D("1500.00"))])
        result = service.pay(self.supplier, "MARKETER", marketer_id, "700", TODAY, "", "", key(52))
        self.assertEqual((result["method"], result["balance_after"], result["duplicate"]),
                         ("MARKETER", "4300.00", False))
        self.assertEqual((self.bank(5), self.cash(1), MarketerPaymentRepo(self.db).cash_balance(marketer_id)),
                         (D("0.00"), D("0.00"), D("800.00")))
        payment = next(iter(self.db.payments.values()))
        self.assertEqual((payment["payment_method"], payment["marketer_party_id"], payment["self_payment_confirmed"]),
                         ("MARKETER", int(marketer_id), False))
        self.assertEqual(len(self.db.marketer_payment_links), 1)
        movement = self.db.marketer_cash_moves[-1]
        self.assertEqual((movement["doc_kind"], movement["movement_type"], movement["amount_delta"]),
                         ("SUPPLIER_PAY", "SUPPLIER_PAY", D("-700.00")))

    def test_marketer_payment_requires_sufficient_held_cash_and_valid_self_confirmation(self):
        marketer_id = self.marketer(cash="50")
        service = self.service_with_marketer_right()
        self.refused("MARKETER_CASH_INSUFFICIENT",
                     lambda: service.pay(self.supplier, "MARKETER", marketer_id, "51", TODAY, "", "", key(53)))
        self.refused("CONFIRMATION_INVALID",
                     lambda: service.pay(self.supplier, "MARKETER", marketer_id, "10", TODAY, "", "", key(54),
                                         confirm_same_party="yes"))
        self.refused("CONFIRMATION_INVALID",
                     lambda: service.pay(self.supplier, "MARKETER", marketer_id, "10", TODAY, "", "", key(55),
                                         confirm_same_party=True))
        self.assertEqual((self.db.payments, len(self.db.marketer_cash_moves)), ({}, 1))

    def test_self_payment_requires_and_persists_explicit_confirmation(self):
        marketer_id = self.marketer(cash="700", same_as_supplier=True)
        service = self.service_with_marketer_right()
        self.refused("SELF_PAYMENT_CONFIRMATION_REQUIRED",
                     lambda: service.pay(self.supplier, "MARKETER", marketer_id, "500", TODAY, "", "", key(56)))
        result = service.pay(self.supplier, "MARKETER", marketer_id, "500", TODAY, "", "", key(57),
                             confirm_same_party=True)
        payment = self.db.payments[int(result["id"])]
        self.assertTrue(payment["self_payment_confirmed"])
        self.assertTrue(self.db.audit_rows[-1]["metadata"]["self_payment_confirmed"])
        self.assertEqual(MarketerPaymentRepo(self.db).cash_balance(marketer_id), D("200.00"))

    def test_marketer_payment_replay_is_idempotent_and_confirmation_is_part_of_the_payload(self):
        marketer_id = self.marketer(cash="1000")
        service = self.service_with_marketer_right()
        first = service.pay(self.supplier, "MARKETER", marketer_id, "300", TODAY, "r", "", key(58))
        before = self.state()
        replay = service.pay(self.supplier, "MARKETER", marketer_id, "300", TODAY, "r", "", key(58))
        self.assertTrue(replay["duplicate"])
        self.assertEqual((replay["id"], self.state()), (first["id"], before))
        self.refused("REQUEST_CONFLICT",
                     lambda: service.pay(self.supplier, "MARKETER", marketer_id, "300", TODAY, "r", "", key(58),
                                         confirm_same_party=True))

    def test_exact_partial_and_over_payments_cross_zero_into_supplier_credit(self):
        self.pay("1000")
        self.assertEqual(self.making(), D("4000.00"))                       # partial: still owed to the supplier
        self.pay("4000")
        self.assertEqual((self.making(), rules.direction(self.making())), (D("0.00"), rules.SETTLED))   # exact
        over = self.pay("600")
        self.assertEqual((self.making(), over["balance_before"], over["balance_after"]),
                         (D("-600.00"), "0.00", "-600.00"))                  # never capped at the payable balance
        self.assertEqual(rules.direction(self.making()), rules.SUPPLIER_OWES)
        self.pay("400")
        self.assertEqual(self.making(), D("-1000.00"))                      # a further payment from a credit position
        self.assertEqual(self.gold(), D("50.000"))

    def test_one_payment_larger_than_the_payable_balance_is_accepted_whole(self):
        result = self.pay("6000.00")
        self.assertEqual((result["balance_before"], result["balance_after"]), ("5000.00", "-1000.00"))
        self.assertEqual((self.bank(5), self.making()), (D("-6000.00"), D("-1000.00")))

    def test_amounts_are_exact_positive_and_at_most_two_decimals(self):
        for bad in ("0", "0.00", "-5", "abc", "", "  ", "1.234", "1.005", "1e30", "100000000000000", "NaN", "Infinity"):
            with self.subTest(amount=bad):
                self.refused("AMOUNT_INVALID", lambda a=bad: self.pay(a))
        self.pay("١٠٠٠٫٥".replace("٫", "."))                                # Arabic digits are normalised
        self.pay("1,000.50")
        self.assertEqual([p["amount"] for p in self.db.payments.values()], [D("1000.50"), D("1000.50")])
        self.assertTrue(all(isinstance(p["amount"], D) for p in self.db.payments.values()))

    def test_funds_rule_is_unchanged_an_account_may_go_negative(self):
        self.assertEqual(self.bank(5), D("0.00"))
        self.pay("1000")
        self.assertEqual(self.bank(5), D("-1000.00"))                         # no insufficient-funds policy was invented

    def test_method_date_reference_and_supplier_are_validated_by_the_service(self):
        self.refused("METHOD_INVALID", lambda: self.pay(method="CHEQUE"))
        self.refused("METHOD_INVALID", lambda: self.pay(method=""))
        self.refused("DATE_REQUIRED", lambda: self.pay(when=""))
        self.refused("DATE_INVALID", lambda: self.pay(when="09/10/2026"))
        self.refused("DATE_INVALID", lambda: self.pay(when="2026-13-40"))
        self.refused("TEXT_LONG", lambda: self.pay(reference="x" * 101))
        self.refused("TEXT_LONG", lambda: self.pay(notes="x" * 1001))
        self.refused("REQUEST_INVALID", lambda: self.pay(request="not-a-uuid"))
        with self.assertRaises(SupplierRuleError) as context:
            self.payments.pay("999", "BANK", "5", "10", TODAY, "", "", key(50))
        self.assertEqual(context.exception.code, "SUPPLIER_NOT_FOUND")
        self.pay("10", reference="", notes="")                                # reference and notes are optional
        self.pay("10", when="٢٠٢٦-١٠-٠٩")                                      # Arabic digits in the date

    def test_an_inactive_supplier_can_be_paid_with_the_same_movements_and_credit_crossing(self):
        self.suppliers.set_active(self.supplier, False)
        gold = self.gold()
        result = self.pay("1000")
        self.assertEqual((result["balance_before"], result["balance_after"]), ("5000.00", "4000.00"))
        self.assertEqual([(m["account_id"], m["amount_delta"]) for m in self.db.bank_moves], [(5, D("-1000.00"))])
        self.assertEqual([m["amount_delta"] for m in self.db.making if m["source_type"] == SOURCE], [D("-1000.00")])
        self.assertEqual((len(self.db.legs), self.gold()), (2, gold))
        self.assertEqual(self.pay("5000.00", method="BANK", bank="5")["balance_after"], "-1000.00")     # crossing is unchanged
        self.assertFalse(self.db.suppliers[int(self.supplier)]["is_active"])                           # still inactive

    def test_an_inactive_supplier_keeps_its_purchase_invoice_restrictions(self):
        self.suppliers.set_active(self.supplier, False)
        draft_error = None
        try:
            self.purchases.create_draft(self.supplier, 1, "NEW", TODAY, [{"weight": "5", "making_per_gram": "100"}], "", key(950))
        except SupplierRuleError as exc:
            draft_error = exc.code
        self.assertIsNotNone(draft_error)
        self.assertEqual(self.db.payments, {})

    def test_an_inactive_financial_account_is_still_refused_for_an_inactive_supplier_with_no_effects(self):
        self.suppliers.set_active(self.supplier, False)
        self.refused("ACCOUNT_INACTIVE", lambda: self.pay(method="BANK", bank="7"))
        self.db.bank_accounts[5]["is_active"] = 0
        self.refused("ACCOUNT_INACTIVE", lambda: self.pay())
        self.assertEqual((self.db.payments, self.db.cash_moves, self.db.bank_moves, self.db.legs), ({}, [], [], []))

    def test_the_actor_is_the_session_user_and_the_signature_accepts_no_identity(self):
        self.pay()
        self.assertEqual(next(iter(self.db.payments.values()))["performed_by_user_id"], 7)
        names = set(inspect.signature(SupplierPaymentService.pay).parameters)
        self.assertFalse(names & {"user_id", "employee_id", "actor", "permissions"}, names)


# --------------------------------------------------------------------------- rights, idempotency, atomicity
class PaymentGuardTests(PaymentCase):
    def test_owner_and_manager_pay_but_cashier_and_others_are_refused_server_side(self):
        for rights, user in ((OWNER_RIGHTS, 11), (MANAGER_RIGHTS, 12)):
            service, *_ = self.service_for(rights, user)
            self.assertTrue(service.can_pay())
            self.pay("10", service=service)
        count = len(self.db.payments)
        for rights in (CASHIER_RIGHTS, frozenset({"APP_LOGIN"}), frozenset({"APP_LOGIN", "TRANSACTION_CORRECT"})):
            service, *_ = self.service_for(rights, 13)
            self.assertFalse(service.can_pay())
            with self.assertRaises(PermissionError):
                self.pay("10", service=service)
        self.assertEqual(len(self.db.payments), count)

    def test_a_grant_revoked_in_the_database_refuses_even_with_a_stale_session(self):
        service, session, authorization = self.service_for(OWNER_RIGHTS, 14)
        authorization.permissions = OWNER_RIGHTS - {"SUPPLIER_MAKING_PAY"}      # revoked after login
        self.assertTrue(service.can_pay())                                       # the UI hint still says yes ...
        before = self.state()
        with self.assertRaises(PermissionError):
            self.pay("10", service=service)                                      # ... the transaction refuses
        self.assertEqual(self.state(), before)
        self.assertIn(("WRITE", "SUPPLIER_MAKING_PAY"), authorization.checked)

    def test_the_cashier_role_is_never_granted_the_new_rights_by_the_migration(self):
        forward = (ROOT / "database" / "schema" / "036_supplier_making_payments.sql").read_text(encoding="utf-8")
        self.assertIn("WHERE r.code IN ('OWNER','MANAGER')", forward)
        self.assertEqual(forward.count("WHERE r.code = 'OWNER'"), 1)
        self.assertNotIn("CASHIER", forward)
        self.assertNotIn("SALESPERSON", forward)

    def test_same_key_same_payload_returns_the_first_result_and_posts_nothing_twice(self):
        first = self.pay("700", reference="r", request=key(1))
        before = self.state()
        second = self.pay("700", reference="r", request=key(1))
        self.assertEqual((second["id"], second["code"], second["duplicate"]), (first["id"], first["code"], True))
        self.assertEqual(self.state(), before)
        self.assertEqual((len(self.db.payments), len(self.db.bank_moves), len(self.db.legs)), (1, 1, 2))

    def test_same_key_with_any_different_payload_is_refused(self):
        self.pay("700", reference="r", request=key(1))
        for call in (lambda: self.pay("701", reference="r", request=key(1)),
                     lambda: self.pay("700", reference="other", request=key(1)),
                     lambda: self.pay("700", reference="r", notes="n", request=key(1)),
                     lambda: self.pay("700", reference="r", when="2026-10-08", request=key(1)),
                     lambda: self.pay("700", method="BANK", bank="6", reference="r", request=key(1))):
            self.refused("REQUEST_CONFLICT", call)
        other, *_ = self.service_for(OWNER_RIGHTS, 21)                          # another user cannot reuse the key either
        self.refused("REQUEST_CONFLICT", lambda: self.pay("700", reference="r", request=key(1), service=other))

    # ---- the request-key unique key as the last line of defence (a concurrent twin that passed every check together).
    # Fakes cannot interleave two real transactions: the "twin" is simulated by a first read that misses the already
    # committed winner, so the insert meets the unique key. Real concurrency is NOT claimed here.
    def stale_first_read(self):
        original, calls = PaymentRepo.by_request, []

        def by_request(repo, request_key):
            calls.append(request_key)
            return None if len(calls) == 1 else original(repo, request_key)
        started = patch.object(PaymentRepo, "by_request", by_request)
        started.start()
        self.addCleanup(started.stop)
        return calls

    def test_a_concurrent_twin_with_the_same_payload_gets_the_original_document(self):
        winner = self.pay("700", reference="r", request=key(7))
        before = self.state()
        self.stale_first_read()
        twin = self.pay("700", reference="r", request=key(7))
        self.assertEqual((twin["id"], twin["code"], twin["duplicate"]), (winner["id"], winner["code"], True))
        self.assertEqual(self.state(), before)                                           # the loser left nothing behind
        self.assertEqual((len(self.db.payments), len(self.db.bank_moves), len(self.db.legs)), (1, 1, 2))

    def test_a_concurrent_twin_with_a_different_supplier_or_payload_is_a_request_conflict(self):
        self.pay("700", request=key(8))
        other = self.suppliers.create_supplier("مورد آخر", None, None, 1)
        self.stale_first_read()
        before = self.state()
        with self.assertRaises(SupplierRuleError) as context:
            self.payments.pay(other, "BANK", "5", "700", TODAY, "", "", key(8))      # same key, different supplier
        self.assertEqual(context.exception.code, "REQUEST_CONFLICT")
        self.assertEqual(self.state(), before)
        self.assertEqual(len(self.db.payments), 1)

    def test_a_concurrent_twin_from_another_user_is_a_request_conflict(self):
        self.pay("700", request=key(9))
        self.stale_first_read()
        other, *_ = self.service_for(OWNER_RIGHTS, 44)
        self.refused("REQUEST_CONFLICT", lambda: self.pay("700", request=key(9), service=other))

    def test_only_the_request_key_violation_is_handled_every_other_integrity_error_surfaces(self):
        original = PaymentRepo.insert_making_movement

        def other_violation(repo, **kw):
            raise IntegrityError("1062 (23000): Duplicate entry '1' for key 'supplier_making_movements.uq_smm_source'", errno=1062)
        with patch.object(PaymentRepo, "insert_making_movement", other_violation):
            before = self.state()
            with self.assertRaises(IntegrityError):
                self.pay("10", request=key(10))
            self.assertEqual(self.state(), before)                                       # and it still rolled back
        with patch.object(PaymentRepo, "insert_header", lambda repo, **kw: (_ for _ in ()).throw(
                IntegrityError("1452 (23000): Cannot add or update a child row", errno=1452))):
            with self.assertRaises(IntegrityError):
                self.pay("10", request=key(11))
        self.assertIs(PaymentRepo.insert_making_movement, original)

    def test_a_key_violation_that_no_committed_document_explains_is_not_hidden(self):
        # the unique key fired but the fresh read finds nothing: report the real error, never invent a duplicate
        with patch.object(PaymentRepo, "insert_header", lambda repo, **kw: (_ for _ in ()).throw(IntegrityError(
                "1062 (23000): Duplicate entry 'x' for key 'supplier_making_payments.uq_smp_request'", errno=1062))):
            with self.assertRaises(IntegrityError):
                self.pay("10", request=key(12))
        self.assertEqual(self.db.payments, {})

    def test_a_failure_at_any_step_leaves_no_partial_document_movement_or_audit(self):
        for step in ("header", "money", "making", "leg", "audit"):
            with self.subTest(step=step):
                self.db.fail_at = step
                before = self.state()
                with self.assertRaises(RuntimeError):
                    self.pay("900")
                self.db.fail_at = None
                self.assertEqual(self.state(), before)
                self.assertEqual((self.db.payments, self.db.cash_moves, self.db.legs), ({}, [], []))
        self.assertEqual(self.pay("900")["code"], "SM000001")                      # numbering was not consumed by the failures

    def test_bank_payment_failure_is_atomic_too(self):
        for step in ("money", "making", "leg"):
            self.db.fail_at = step
            before = self.state()
            with self.assertRaises(RuntimeError):
                self.pay("900", method="BANK", bank="5")
            self.assertEqual(self.state(), before)
        self.db.fail_at = None

    def test_an_expired_session_posts_nothing(self):
        service, session, _ = self.service_for(OWNER_RIGHTS, 15)
        original = service._repository

        def expiring(connection):
            repository = original(connection)
            real = repository.audit

            def audit(**kw):
                real(**kw)
                session.logout()
            repository.audit = audit
            return repository
        service._repository = expiring
        before = self.state()
        with self.assertRaises(PermissionError):
            self.pay("10", service=service)
        self.assertEqual(self.state(), before)


# --------------------------------------------------------------------------- reads
class PaymentReadTests(PaymentCase):
    def test_the_dialog_context_shows_position_and_only_active_bank_accounts(self):
        self.pay("1000")
        context = self.payments.payment_context(self.supplier)
        self.assertEqual((context["supplier"]["name"], context["making"], context["making_direction"]),
                         ("مورد المصنعيات", D("4000.00"), rules.SHOP_OWES))
        self.assertEqual((context["custodies"], context["marketers"]), ([], []))
        self.assertNotIn("safe", context)
        self.assertEqual([b["id"] for b in context["banks"]], ["5", "6"])        # the inactive account is not offered
        self.suppliers.set_active(self.supplier, False)
        inactive = self.payments.payment_context(self.supplier)                  # the context loads for an inactive supplier
        self.assertEqual((inactive["supplier"]["active"], inactive["making"]), (False, D("4000.00")))
        with self.assertRaises(PermissionError):
            self.service_for(CASHIER_RIGHTS)[0].payment_context(self.supplier)

    def test_the_document_names_method_account_actor_and_has_no_reversal_yet(self):
        result = self.pay("250", method="BANK", bank="6", reference="تحويل 5", notes="ن")
        document = self.payments.payment_detail(result["id"])
        self.assertEqual((document["code"], document["method"], document["account_name"], document["amount"],
                          document["reference"], document["notes"], document["reversed"], document["reversal_code"]),
                         ("SM000001", "BANK", "فودافون كاش", D("250.00"), "تحويل 5", "ن", False, None))
        with self.assertRaises(SupplierRuleError):
            self.payments.payment_detail("999")

    def test_the_making_ledger_marks_a_payment_with_its_method_and_account(self):
        self.pay("250")
        rows = {r["movement_code"]: r for r in self.suppliers.supplier_detail(self.supplier)["making_ledger"]}
        self.assertEqual((rows["PAYMENT"]["payment_method"], rows["PAYMENT"]["payment_account"], rows["PAYMENT"]["reference"],
                          rows["PAYMENT"]["reversed_by"], rows["PAYMENT"]["balance_after"]),
                         ("BANK", "بنك مصر", "SM000001", None, D("4750.00")))
        self.assertIsNone(rows["PURCHASE_INVOICE"]["payment_id"])


# --------------------------------------------------------------------------- reversal
class ReversalTests(PaymentCase):
    REASON = "دفعة بالخطأ"

    def reverse(self, payment_id="1", reason=None, service=None):
        return (service or self.payments).reverse(payment_id, self.REASON if reason is None else reason)

    def test_owner_reverses_a_bank_payment_and_the_supplier_is_restored(self):
        self.pay("1000")
        original = copy.deepcopy((self.db.payments, self.db.bank_moves[0], [m for m in self.db.making if m["source_type"] == SOURCE]))
        gold = self.gold()
        result = self.reverse()
        self.assertEqual((result["code"], result["source_reference"]), ("RV000001", "SM000001"))
        self.assertEqual((self.bank(5), self.making(), self.gold()), (D("0.00"), D("5000.00"), gold))
        self.assertEqual(self.db.bank_moves[0], original[1])                           # the original movement is untouched
        self.assertEqual(self.db.bank_moves[1]["amount_delta"], D("1000.00"))            # a new opposite movement
        self.assertEqual((self.db.bank_moves[1]["reference_type"], self.db.bank_moves[1]["type_code"]),
                         ("TRANSACTION_CORRECTION", "REVERSAL_PAYMENT"))
        self.assertEqual(self.db.payments, original[0])                                  # the payment document is immutable
        self.assertEqual([m for m in self.db.making if m["source_type"] == SOURCE], original[2])
        reversal = [m for m in self.db.making if m["source_type"] == "TRANSACTION_CORRECTION"]
        self.assertEqual([(m["amount_delta"], m["movement_type_id"], m["source_id"], m["source_reference"]) for m in reversal],
                         [(D("1000.00"), 3, 1, "RV000001")])
        (correction,) = self.db.corrections
        self.assertEqual((correction["source_type"], correction["source_id"], correction["invoice_id"], correction["reason"]),
                         (SOURCE, "1", None, self.REASON))
        self.assertEqual(self.db.audit_rows[-1]["event_type"], "TRANSACTION_REVERSED")

    def test_owner_reverses_a_marketer_payment_with_a_compensating_marketer_cash_movement(self):
        marketer_id = self.marketer(cash="1200")
        service = self.service_with_marketer_right()
        result = service.pay(self.supplier, "MARKETER", marketer_id, "400", TODAY, "", "", key(59))
        service.reverse(result["id"], self.REASON)
        self.assertEqual((self.making(), MarketerPaymentRepo(self.db).cash_balance(marketer_id),
                          self.bank(5), self.cash(1)), (D("5000.00"), D("1200.00"), D("0.00"), D("0.00")))
        self.assertEqual([(movement["movement_type"], movement["amount_delta"])
                          for movement in self.db.marketer_cash_moves[-2:]],
                         [("SUPPLIER_PAY", D("-400.00")), ("SUPPLIER_PAY_REVERSAL", D("400.00"))])
        self.assertEqual(len(self.db.corrections), 1)

    def test_a_bank_payment_is_restored_to_its_original_account_even_if_it_was_deactivated(self):
        self.pay("800", method="BANK", bank="6")
        self.db.bank_accounts[6]["is_active"] = 0
        self.reverse()
        self.assertEqual((self.bank(6), self.bank(5), self.cash(1)), (D("0.00"), D("0.00"), D("0.00")))   # no substitution
        self.assertEqual(self.making(), D("5000.00"))

    def test_reversal_works_after_the_supplier_is_deactivated_and_restores_the_original_account(self):
        self.pay("800", method="BANK", bank="6")
        self.pay("200")
        self.suppliers.set_active(self.supplier, False)
        self.db.bank_accounts[6]["is_active"] = 0
        self.reverse("1")
        self.reverse("2")
        self.assertEqual((self.bank(6), self.bank(5), self.cash(1), self.making()),
                         (D("0.00"), D("0.00"), D("0.00"), D("5000.00")))
        manager, *_ = self.service_for(MANAGER_RIGHTS, 61)
        self.pay("1", service=manager)                                                        # a manager may pay, still not reverse
        with self.assertRaises(PermissionError):
            self.reverse("3", service=manager)

    def test_a_payment_that_crossed_into_credit_is_restored_by_its_exact_stored_amounts(self):
        self.pay("6000")
        self.assertEqual(self.making(), D("-1000.00"))
        self.reverse()
        self.assertEqual((self.making(), self.bank(5)), (D("5000.00"), D("0.00")))

    def test_a_second_reversal_is_refused_and_leaves_no_trace(self):
        self.pay("1000")
        self.reverse()
        self.refused("ALREADY_REVERSED", lambda: self.reverse(), CorrectionError)
        self.assertEqual(len(self.db.corrections), 1)
        self.assertEqual((self.bank(5), self.making()), (D("0.00"), D("5000.00")))

    def test_a_concurrent_second_reversal_is_stopped_by_the_unique_key_and_rolls_back_completely(self):
        self.pay("1000")
        self.reverse()
        before = self.state()
        history = CorrectionService._history
        with patch.object(CorrectionService, "_history", lambda self_, c, st, si: (None, [])):     # a stale read sees no reversal
            with self.assertRaises(RuntimeError):
                self.reverse()
        self.assertEqual(self.state(), before)
        self.assertEqual((len(self.db.corrections), len(self.db.bank_moves), self.bank(5), self.making()), (1, 2, D("0.00"), D("5000.00")))
        self.assertIs(CorrectionService._history, history)

    def test_a_reason_is_mandatory(self):
        self.pay("1000")
        for reason in ("", "   "):
            self.refused("REASON_REQUIRED", lambda r=reason: self.reverse(reason=r), CorrectionError)

    def test_the_payment_must_exist(self):
        self.refused("SOURCE_NOT_FOUND", lambda: self.reverse("77"), CorrectionError)

    def test_only_the_owner_reverses_and_the_correction_right_is_never_weakened(self):
        self.pay("1000")
        manager, *_ = self.service_for(MANAGER_RIGHTS, 31)
        self.assertFalse(manager.can_reverse())
        no_correct, *_ = self.service_for(frozenset(OWNER_RIGHTS - {"TRANSACTION_CORRECT"}), 32)
        self.assertTrue(no_correct.can_reverse())
        cashier, *_ = self.service_for(CASHIER_RIGHTS, 33)
        before = self.state()
        for service in (manager, no_correct, cashier):
            with self.assertRaises(PermissionError):
                self.reverse(service=service)
        self.assertEqual(self.state(), before)
        direct = self.correction_service(*self.service_for(MANAGER_RIGHTS, 34)[1:])           # CorrectionService itself refuses a manager
        with self.assertRaises(PermissionError):
            direct.reverse(SOURCE, "1", self.REASON)
        self.reverse()                                                                             # the OWNER can
        self.assertEqual(self.bank(5), D("0.00"))

    def test_the_owner_right_is_rechecked_against_the_database_inside_the_transaction(self):
        self.pay("1000")
        service, session, authorization = self.service_for(OWNER_RIGHTS, 35)
        authorization.permissions = OWNER_RIGHTS - {"SUPPLIER_MAKING_PAY_REVERSE"}
        before = self.state()
        with self.assertRaises(PermissionError):
            self.reverse(service=service)
        self.assertEqual(self.state(), before)

    def test_a_failure_at_any_reversal_step_changes_nothing(self):
        self.pay("1000")
        for step in ("correction", "reversal_money", "making", "audit"):
            with self.subTest(step=step):
                self.db.fail_at = step
                before = self.state()
                with self.assertRaises(RuntimeError):
                    self.reverse()
                self.db.fail_at = None
                self.assertEqual(self.state(), before)
                self.assertEqual((self.db.corrections, self.bank(5), self.making()), ([], D("-1000.00"), D("4000.00")))
        self.reverse()

    def test_the_preview_states_the_exact_effects_and_needs_the_owner_right(self):
        self.pay("1000", method="BANK", bank="5")
        preview = self.corrections.preview_reversal(SOURCE, "1")
        self.assertEqual((preview["can_reverse"], preview["money"], preview["making"], preview["source_text"]),
                         (True, [{"account": "بنك مصر", "ledger": "BANK", "delta": "1000.00"}],
                          [{"supplier_id": "1", "delta": "1000.00"}], "دفعة المصنعيات"))
        manager = self.correction_service(*self.service_for(MANAGER_RIGHTS, 36)[1:])
        with self.assertRaises(PermissionError):
            manager.preview_reversal(SOURCE, "1")
        self.reverse()
        self.assertFalse(self.corrections.preview_reversal(SOURCE, "1")["can_reverse"])

    def test_a_payment_is_never_settlement_corrected_only_reversed(self):
        self.pay("1000")
        for call in (lambda: self.corrections.current_settlement(SOURCE, "1"),
                     lambda: self.corrections.preview_settlement(SOURCE, "1", [])):
            with self.assertRaises(CorrectionError) as context:
                call()
            self.assertEqual(context.exception.code, "SETTLEMENT_NOT_SUPPORTED")

    def test_history_shows_the_original_and_its_reversal_and_their_relationship(self):
        result = self.pay("1000")
        self.reverse()
        document = self.payments.payment_detail(result["id"])
        self.assertEqual((document["reversed"], document["reversal_code"], document["reversal_reason"],
                          document["amount"]), (True, "RV000001", self.REASON, D("1000.00")))
        rows = self.suppliers.supplier_detail(self.supplier)["making_ledger"]
        payment = next(r for r in rows if r["movement_code"] == "PAYMENT")
        reversal = next(r for r in rows if r["movement_code"] == "PAYMENT_REVERSAL")
        self.assertEqual((payment["reversed_by"], payment["amount_delta"]), ("RV000001", D("-1000.00")))
        self.assertEqual((reversal["reference"], reversal["reverses_reference"], reversal["amount_delta"]),
                         ("RV000001", "SM000001", D("1000.00")))
        self.assertEqual(rows[0]["balance_after"], D("5000.00"))

    def test_payments_after_a_reversal_still_work_and_number_on(self):
        self.pay("1000")
        self.reverse()
        again = self.pay("1000")
        self.assertEqual(again["code"], "SM000002")
        self.assertEqual(self.making(), D("4000.00"))


# --------------------------------------------------------------------------- the financial-account statement label
class StatementLabelTests(unittest.TestCase):
    def test_supplier_payments_have_their_own_arabic_label_on_both_ledgers_and_the_reversal_keeps_its_existing_one(self):
        from services.financial_account_service import BANK_LABELS, CASH_LABELS, FinancialAccountService
        self.assertEqual(CASH_LABELS["SUPPLIER_PAYMENT"], "دفعة مصنعيات مورد")
        self.assertEqual(BANK_LABELS["SUPPLIER_PAYMENT"], CASH_LABELS["SUPPLIER_PAYMENT"])
        payment = {"id": 5, "amount_delta": "-25.00", "balance_after": "100.00", "label_key": "SUPPLIER_PAYMENT",
                   "source_type": "SUPPLIER_MAKING_PAYMENT", "source_reference": "SM000001",
                   "description": "دفعة مصنعيات للمورد SM000001 — مورد", "reference_type": "SUPPLIER_MAKING_PAYMENT", "reference_id": 1}
        reversal = {"id": 6, "amount_delta": "25.00", "balance_after": "125.00", "label_key": "REVERSAL",
                    "source_type": "FULL_REVERSAL", "source_reference": "RV000001", "corrected_reference": "SM000001",
                    "corrected_source_type": "SUPPLIER_MAKING_PAYMENT", "description": "عكس", "reference_type": "TRANSACTION_CORRECTION"}
        for ledger in ("CASH", "BANK"):
            row = FinancialAccountService._row(payment, ledger)
            self.assertEqual((row["label"], row["reference"], row["description"], row["amount_delta"], row["kind"]),
                             ("دفعة مصنعيات مورد", "SM000001", payment["description"], D("-25.00"), "SUPPLIER_PAYMENT"))
            undo = FinancialAccountService._row(reversal, ledger)
            self.assertEqual((undo["label"], undo["reference"], undo["corrected_reference"], undo["amount_delta"]),
                             ("إلغاء عملية", "RV000001", "SM000001", D("25.00")))
        self.assertEqual(FinancialAccountService._row({**payment, "label_key": "NOT_A_LABEL"})["label"], CASH_LABELS["OTHER"])

    def test_the_read_model_resolves_a_payment_by_its_document_never_by_guessing(self):
        from repositories import financial_account_repository as module
        source = inspect.getsource(module.FinancialAccountRepository._resolved)
        for part in ("LEFT JOIN supplier_making_payments smp", "o.reference_type = 'SUPPLIER_MAKING_PAYMENT' AND smp.id = o.reference_id",
                     "WHEN smp.id IS NOT NULL THEN 'SUPPLIER_MAKING_PAYMENT'", "WHEN smp.id IS NOT NULL THEN 'SUPPLIER_PAYMENT'",
                     "CONCAT('SM', LPAD(smp.id"):
            self.assertIn(part, source)
        self.assertIn("SUPPLIER_MAKING_PAYMENT", module.SOURCE_TYPES)                    # the statement's source filter accepts it
        self.assertEqual(module.SOURCE_TYPES[-1], "UNKNOWN")                             # UNKNOWN stays the last resort

    def test_the_cash_screen_filter_offers_the_new_label(self):
        from services.financial_account_service import CASH_LABELS
        self.assertIn("SUPPLIER_PAYMENT", [k for k in CASH_LABELS])


# --------------------------------------------------------------------------- pure rules, numbering, wiring
class RulesAndWiringTests(unittest.TestCase):
    def test_parse_amount_and_the_position_after_a_payment(self):
        self.assertEqual(rules.parse_amount("1,250.5"), D("1250.50"))
        for bad in ("0", "-1", "1.001", "x", "", "1e99"):
            with self.assertRaises(SupplierRuleError):
                rules.parse_amount(bad)
        self.assertEqual(rules.making_after_payment("5000", "1200.25"), D("3799.75"))
        self.assertEqual(rules.making_after_payment("100", "250"), D("-150.00"))          # crossing zero is not capped
        self.assertEqual(rules.direction(rules.making_after_payment("100", "100")), rules.SETTLED)

    def test_the_document_prefix_is_distinct_and_follows_the_id_allocation_convention(self):
        self.assertEqual((payment_code(1), payment_code(123456)), ("SM000001", "SM123456"))
        taken = {rules.supplier_code(1)[:1], rules.invoice_code(1)[:2], "DP", "DR", "CS", "FO", "TR", "RV", "FC", "RT", "EX"}
        self.assertNotIn("SM", taken)
        for module in ("services/supplier_rules.py", "services/correction_rules.py", "repositories/customer_deposit_repository.py",
                       "repositories/financial_account_repository.py", "repositories/invoice_return_repository.py"):
            self.assertNotIn('f"SM{', (ROOT / module).read_text(encoding="utf-8"))

    def test_the_payment_does_not_reuse_other_money_semantics(self):
        sources = "".join((ROOT / path).read_text(encoding="utf-8") for path in (
            "services/supplier_payment_service.py", "repositories/supplier_payment_repository.py"))
        for forbidden in ("financial_operations", "SHOP_PAYMENT_OUT", "CUSTOMER_PAYMENT", "customer_transactions",
                          "golddb", "supplier_gold_movements"):
            self.assertNotIn(forbidden, sources, forbidden)
        self.assertIn("SOURCE_REFERENCE", (ROOT / "repositories" / "correction_repository.py").read_text(encoding="utf-8"))
        self.assertEqual(SOURCE_REFERENCE[SOURCE], SOURCE)

    def test_a_payment_reference_never_leaves_the_server_as_a_float(self):
        for path in ("services/supplier_payment_service.py", "repositories/supplier_payment_repository.py"):
            self.assertNotRegex((ROOT / path).read_text(encoding="utf-8"), r"\bfloat\(")


# --------------------------------------------------------------------------- the view model
class ViewModelTests(PaymentCase):
    def setUp(self):
        super().setUp()
        self.vm = SupplierViewModel(self.session, self.suppliers, payments=self.payments)
        actor = self.session.current_actor()

        def start(operation, call):                         # run the worker's job synchronously, as the worker would
            try:
                result, error = call(_expected_actor=actor), ""
            except (ValueError, PermissionError) as exc:
                result, error = None, str(exc)
            getattr(self.vm, f"_on_{operation}")(result, error)
            self.vm.changed.emit()
        self.vm._start = start
        self.posted, self.reversed = [], []
        self.vm.paymentPosted.connect(self.posted.append)
        self.vm.reversalDone.connect(self.reversed.append)
        self.vm.selectSupplier(self.supplier)

    def test_ui_hints_follow_the_rights(self):
        self.assertEqual((self.vm.canPayMaking, self.vm.canReversePayment), (True, True))
        manager = SupplierViewModel(session_for(MANAGER_RIGHTS), self.suppliers, payments=self.payments)
        self.assertEqual((manager.canPayMaking, manager.canReversePayment), (True, False))
        cashier = SupplierViewModel(session_for(CASHIER_RIGHTS), self.suppliers, payments=self.payments)
        self.assertEqual((cashier.canPayMaking, cashier.canReversePayment), (False, False))

    def test_the_dialog_context_and_the_resulting_position(self):
        self.assertEqual(self.vm.payContext, {})
        self.vm.loadPaymentContext(self.supplier)
        context = self.vm.payContext
        self.assertEqual((context["supplierName"], context["currentText"]),
                         ("مورد المصنعيات", "علينا للمورد 5,000.00 ج مصنعيات"))
        self.assertEqual([b["id"] for b in context["banks"]], ["5", "6"])
        self.assertTrue(context["banks"][1]["text"].startswith("فودافون كاش — محفظة"))
        preview = self.vm.previewPayment("1000")
        self.assertEqual((preview["valid"], preview["afterText"], preview["afterTone"], preview["note"]),
                         (True, "علينا للمورد 4,000.00 ج مصنعيات", "shopOwes", ""))
        self.assertEqual(self.vm.previewPayment("5000")["afterText"], "لا توجد مستحقات مصنعية")
        credit = self.vm.previewPayment("6000")
        self.assertEqual((credit["afterText"], credit["afterTone"]), ("لنا عند المورد 1,000.00 ج مصنعيات", "supplierOwes"))
        self.assertIn("رصيد دائن", credit["note"])
        self.assertNotIn("-", credit["afterText"])
        self.assertEqual(self.vm.previewPayment("")["error"], "")
        self.assertTrue(self.vm.previewPayment("1.234")["error"])
        self.assertFalse(self.vm.previewPayment("abc")["valid"])

    def test_a_posted_payment_closes_the_dialog_signal_refreshes_and_shows_in_history(self):
        self.vm.loadPaymentContext(self.supplier)
        self.vm.payMaking(self.supplier, "BANK", "5", False, "1000", TODAY, "ref", "", self.vm.newRequestKey())
        self.assertEqual(self.posted, ["SM000001"])
        self.assertIn("SM000001", self.vm.message)
        self.assertEqual(self.vm.errorMessage, "")
        self.assertEqual(self.vm.detail["makingText"], "علينا للمورد 4,000.00 ج مصنعيات")
        row = next(r for r in self.vm.detail["makingLedger"] if r["paymentId"])
        self.assertEqual((row["reference"], row["detail"], row["state"], row["canReverse"], row["reversed"]),
                         ("SM000001", "تحويل من بنك مصر", "", True, False))

    def test_a_refusal_keeps_the_dialog_open_with_the_reason_and_no_signal(self):
        self.vm.loadPaymentContext(self.supplier)
        self.vm.payMaking(self.supplier, "BANK", "7", False, "1000", TODAY, "", "", self.vm.newRequestKey())
        self.assertEqual(self.posted, [])
        self.assertIn("غير نشط", self.vm.errorMessage)
        self.assertEqual(self.db.payments, {})

    def test_a_second_submit_while_the_first_is_running_is_ignored(self):
        calls = []
        self.vm._payments = type("P", (), {"pay": lambda s, *a, **k: calls.append(a)})()
        self.vm._busy.add("pay")
        self.vm.payMaking(self.supplier, "BANK", "5", False, "1000", TODAY, "", "", self.vm.newRequestKey())
        self.assertEqual(calls, [])
        self.assertTrue(self.vm.paying)

    def test_the_same_request_key_posts_once_even_if_submitted_twice(self):
        request = self.vm.newRequestKey()
        for _ in range(2):
            self.vm.payMaking(self.supplier, "BANK", "5", False, "1000", TODAY, "", "", request)
        self.assertEqual((len(self.db.payments), len(self.db.bank_moves)), (1, 1))
        self.assertIn("مسجلة من قبل", self.vm.message)

    def test_reversal_through_the_view_model_marks_both_rows(self):
        self.vm.payMaking(self.supplier, "BANK", "5", False, "400", TODAY, "", "", self.vm.newRequestKey())
        self.vm.reversePayment("1", "غلط")
        self.assertEqual(self.reversed, ["RV000001"])
        rows = {r["paymentId"] or r["movement"]: r for r in self.vm.detail["makingLedger"]}
        original = rows["1"]
        self.assertEqual((original["state"], original["reversed"], original["canReverse"], original["detail"]),
                         ("معكوسة بالعملية RV000001", True, False, "تحويل من بنك مصر"))
        undo = next(r for r in self.vm.detail["makingLedger"] if r["state"].startswith("عكس الدفعة"))
        self.assertEqual((undo["state"], undo["reference"], undo["side"]), ("عكس الدفعة SM000001", "RV000001", "علينا للمورد"))
        self.assertEqual(self.vm.detail["makingText"], "علينا للمورد 5,000.00 ج مصنعيات")
        self.vm.reversePayment("1", "مرة ثانية")
        self.assertIn("تم عكس", self.vm.errorMessage)

    def test_a_manager_view_model_reversal_is_refused_with_a_message(self):
        self.vm.payMaking(self.supplier, "BANK", "5", False, "400", TODAY, "", "", self.vm.newRequestKey())
        manager, *_ = self.service_for(MANAGER_RIGHTS, 41)
        self.vm._payments = manager
        self.vm.reversePayment("1", "غلط")
        self.assertEqual(self.reversed, [])
        self.assertTrue(self.vm.errorMessage)
        self.assertEqual(self.bank(5), D("-400.00"))


if __name__ == "__main__":
    unittest.main()
