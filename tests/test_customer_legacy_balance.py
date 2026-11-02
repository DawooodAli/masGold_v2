"""رصيد قديم: an OLD (paper-ledger) balance put on a customer's account. One ledger row + one audit row, never a cash / bank / wallet movement. Transactional fakes, no database."""
import json
from decimal import Decimal as D
from unittest.mock import patch
from uuid import uuid4
import unittest

from services.customer_account_service import CustomerAccountService, LEGACY_PERMISSION
from services.correction_service import CorrectionError
from tests import test_correction_service as fixture
from tests import test_customer_account as base


class LegacyRepo(base.AccountRepo):
    def legacy_entry(self, cid, key):
        return next((r for r in self.store.legacy if r["customer_id"] == str(cid) and r["reference_code"] == key), None)

    def _execute(self, sql, params=()):
        self.store.audit.append((params[1], params[3], json.loads(params[6])))


class LegacyCustomers(fixture.CustomerRepo):
    def add_transaction(self, **kw):
        self.store.ledger.append((str(kw["customer_id"]), kw["amount_delta"], kw["reference_type"], str(kw["reference_code"])))
        self.store.legacy.append({"id": str(len(self.store.ledger)), "customer_id": str(kw["customer_id"]), "amount_delta": kw["amount_delta"], "created_by_user_id": kw["created_by_user_id"],
                                  "metadata_json": json.dumps(kw["metadata"]), "reference_code": kw["reference_code"], "type": kw["type_code"], "description": kw["description"], "notes": kw["notes"]})
        return str(len(self.store.ledger))


class LegacyBalanceTests(fixture.CorrectionTestCase):
    def setUp(self):
        super().setUp()
        self.store.cs, self.store.legacy, self.store.money = {}, [], []
        self.store.ledger = [("12", D("20000"), "INVOICE", "500")]
        self.store.audit = []
        service = self.make(fixture.MANAGER | {LEGACY_PERMISSION})
        self.account = CustomerAccountService(service._session, transaction_factory=service._transaction, repository=LegacyRepo, invoices=fixture.InvoiceRepo, customers=LegacyCustomers,
                                              authorization=service._authorization)
        patcher = patch("repositories.customer_account_repository.CustomerAccountRepository", LegacyRepo)
        patcher.start()
        self.addCleanup(patcher.stop)

    def add(self, direction="OWES", amount="1500", note="دفتر ورقي 2023", day="2023-05-10", key=None, customer="12"):
        return self.account.add_legacy_balance(customer, direction, amount, note, day, key or str(uuid4()))

    def balance(self):
        return sum((r[1] for r in self.store.ledger), D("0"))

    def test_an_old_debt_is_added_as_one_ledger_row_with_an_audit_and_no_money_movement(self):
        result = self.add()
        self.assertEqual((D(result["balance_before"]), D(result["balance_after"]), result["duplicate"]), (D("20000"), D("21500"), False))
        self.assertEqual(self.balance(), D("21500.00"))
        row = self.store.legacy[0]
        self.assertEqual((row["type"], row["notes"], row["amount_delta"]), ("LEGACY_ADJUSTMENT", "دفتر ورقي 2023", D("1500.00")))
        self.assertIn("2023-05-10", row["description"])
        self.assertEqual(self.store.money, [])                                          # never a cash / bank / wallet movement
        self.assertEqual([a[0] for a in self.store.audit], ["CUSTOMER_LEGACY_BALANCE_ADDED"])
        self.assertEqual(self.store.audit[0][2]["amount"], "1500.00")

    def test_a_balance_we_owe_the_customer_is_a_negative_entry(self):
        self.add("CREDIT", "300")
        self.assertEqual(self.store.legacy[0]["amount_delta"], D("-300.00"))
        self.assertEqual(self.balance(), D("19700.00"))

    def test_only_the_right_holder_may_add_and_every_refusal_posts_nothing(self):
        self.account.authorization.permissions = fixture.MANAGER
        with self.assertRaises(PermissionError):
            self.add()
        self.account.authorization.permissions = fixture.MANAGER | {LEGACY_PERMISSION}
        for kwargs, code in (({"direction": "X"}, "DIRECTION_INVALID"), ({"amount": "0"}, "AMOUNT_INVALID"), ({"note": "  "}, "NOTE_REQUIRED"),
                             ({"day": "2999-01-01"}, "DATE_FUTURE")):
            with self.subTest(code=code), self.assertRaises(CorrectionError) as caught:
                self.add(**kwargs)
            self.assertEqual(caught.exception.code, code)
        for bad_amount in ("-5", "NaN", "1.001", "abc"):
            with self.subTest(amount=bad_amount), self.assertRaises(ValueError):
                self.add(amount=bad_amount)
        with self.assertRaises(CorrectionError):
            self.account.add_legacy_balance("12", "OWES", "1", "x", "", "not-a-uuid")
        self.assertEqual((self.balance(), self.store.legacy, self.store.audit), (D("20000"), [], []))

    def test_the_same_request_key_posts_once_and_a_different_request_with_it_is_refused(self):
        key = str(uuid4())
        first = self.add(key=key)
        again = self.add(key=key)
        self.assertEqual((again["id"], again["duplicate"], again["balance_after"]), (first["id"], True, "21500.00"))
        self.assertEqual((len(self.store.legacy), self.balance()), (1, D("21500.00")))
        with self.assertRaises(CorrectionError) as conflict:
            self.add(amount="9999", key=key)
        self.assertEqual(conflict.exception.code, "REQUEST_CONFLICT")

    def test_the_old_date_is_optional(self):
        self.add(day="")
        self.assertNotIn("بتاريخ", self.store.legacy[0]["description"])

    def test_can_add_is_the_owners_right(self):
        self.assertTrue(self.account.can_add_legacy_balance())


class StatementLabelTests(unittest.TestCase):
    def test_an_old_balance_row_is_named_in_the_statement_and_other_unknown_rows_keep_their_label(self):
        from app.session import AppSession
        from app.viewmodels.customer_account_viewmodel import CustomerAccountViewModel
        vm = CustomerAccountViewModel(AppSession())
        base = dict(row_kind="LEDGER", id=1, occurred_at="2026-11-03", source_type="UNKNOWN", source_reference=None, invoice_id=None, invoice_number=None, amount_delta=D("100"),
                    running_balance=D("100"), actor_name="م")
        vm._data = {"rows": [dict(base, description="رصيد قديم من دفتر ورقي — بتاريخ 2023-05-10"), dict(base, id=2, description="حركة قديمة")]}
        self.assertEqual([r["source"] for r in vm.rows], ["رصيد قديم", "حركة قديمة / غير محددة"])


if __name__ == "__main__":
    unittest.main()
