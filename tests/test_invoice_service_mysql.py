"""InvoiceService on real MySQL: the disposable masgold_rehearsal database only.

Opt-in: MASGOLD_REHEARSAL_DB_TESTS=1. Real repositories, real AuthorizationService
and the real schema constraints run inside one transaction; the service's own
transaction is a SAVEPOINT, so a failure proves every service write is undone.
Everything (synthetic seed included) is rolled back at the end.

Gold Price locking has its own suite; here a double returns a price set built
from real gold_price_quotes rows so the quote foreign keys are exercised.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType
import unittest
import uuid

from app.session import AppSession
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.gold_price_service import BillingKaratPrice, BillingPriceSet
from services.invoice_service import (
    InvoiceDraft, InvoiceError, InvoiceService, OldGoldLineInput, PaymentInput, SaleLineInput,
)
from repositories.invoice_repository import InvoiceRepository
from tests.test_billing_v2_repositories_mysql import ENABLED, rehearsal_connection


D = Decimal
COUNTED_TABLES = ("invoices", "invoice_lines", "invoice_payments", "invoice_events", "invoice_customer_snapshots",
                  "invoice_price_snapshots", "customer_transactions", "old_gold_movements", "inventory_movements",
                  "invoice_number_sequences", "audit_log", "cash_movements", "bank_movements")


class QuotePrices:
    def __init__(self, quotes):
        self.quotes = quotes                       # karat_id -> (quote_id, karat, buy, sell, provider_ts)

    def _set(self, karat_ids):
        prices = {k: BillingKaratPrice(k, q[1], q[2], q[3], q[0], 0, q[4])
                  for k, q in self.quotes.items() if k in karat_ids}
        return BillingPriceSet("REHEARSAL-TOKEN", "MANUAL", "FRESH", (), False, MappingProxyType(prices),
                               MappingProxyType({"source_code": "MANUAL"}))

    def get_billing_price_set(self, karat_ids):
        return self._set(karat_ids)

    def lock_and_validate_for_finalization(self, connection, token, karat_ids, *, acknowledged=False):
        assert connection.in_transaction and token == "REHEARSAL-TOKEN"
        return self._set(karat_ids)


@unittest.skipUnless(ENABLED, "set MASGOLD_REHEARSAL_DB_TESTS=1 to run against masgold_rehearsal")
class InvoiceServiceMySqlTests(unittest.TestCase):
    def setUp(self):
        self.connection = rehearsal_connection()
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)
        self.connection.rollback()                 # end the guard's implicit read transaction
        self.connection.start_transaction()
        tag = uuid.uuid4().hex[:8]
        self.salesperson = self.sql("INSERT INTO employees (employee_code, name) VALUES (%s, 'بائع تجريبي')",
                                    (f"S{tag}",))
        self.user_id = self.sql("INSERT INTO users (username, password_hash, display_name, is_active, "
                                "must_change_password) VALUES (%s, 'not-a-real-hash', 'مستخدم تجريبي', 1, 0)",
                                (f"u{tag}",))
        self.sql("INSERT INTO user_roles (user_id, role_id) SELECT %s, id FROM roles WHERE code = 'OWNER'",
                 (self.user_id,))
        self.customer = self.sql("INSERT INTO customers (name) VALUES (%s)", (f"عميل تجريبي {tag}",))
        self.placeholder = self.sql("INSERT INTO customers (name, record_origin, data_completeness) "
                                    "VALUES (%s, 'LEGACY_PLACEHOLDER', 'UNKNOWN')", (f"عميل قديم {tag}",))
        karats = {r["karat_value"]: r["id"] for r in self.sql("SELECT id, karat_value FROM karats", fetch=True)}
        self.k21, self.k18 = karats[21], karats[18]
        type_id = self.sql("INSERT INTO item_types (code, name) VALUES (%s, %s)", (f"T{tag}", f"خاتم {tag}"))
        self.item = self.sql("INSERT INTO inventory_items (item_type_id, karat_id, weight_grams, acquisition_source) "
                             "VALUES (%s, %s, 10.500, 'MANUAL_ENTRY')", (type_id, self.k21))
        self.sql("INSERT INTO inventory_barcodes (inventory_item_id, barcode_value) VALUES (%s, %s)",
                 (self.item, f"T-{tag}"))
        source = self.sql("SELECT id FROM gold_price_sources WHERE code = 'MANUAL'", fetch=True)[0]["id"]
        group, quotes = str(uuid.uuid4()), {}
        for value, karat_id, buy, sell in ((21, self.k21, D("6207.0000"), D("6232.0000")),
                                           (18, self.k18, D("5320.0000"), D("5341.0000")),
                                           (24, karats[24], D("7093.0000"), D("7122.0000"))):
            provider = datetime(2026, 9, 25, 11, 29, 50 + value % 7)
            quote_id = self.sql("INSERT INTO gold_price_quotes (quote_group_id, source_id, karat_id, "
                                "shop_buy_price_per_gram, shop_sell_price_per_gram, provider_timestamp) "
                                "VALUES (%s, %s, %s, %s, %s, %s)", (group, source, karat_id, buy, sell, provider))
            quotes[karat_id] = (quote_id, value, buy, sell, provider.replace(tzinfo=timezone.utc))
        self.quotes = quotes

        session = AppSession()
        permissions = frozenset(r["code"] for r in self.sql("SELECT code FROM permissions", fetch=True))
        session._establish(AuthenticationResult(self.user_id, None, "مستخدم تجريبي", permissions, _VERIFIED),
                           int(session.generation))

        @contextmanager
        def savepoint_transaction():
            cursor = self.connection.cursor()
            cursor.execute("SAVEPOINT invoice_service")
            cursor.close()
            try:
                yield self.connection
            except BaseException:
                cursor = self.connection.cursor()
                cursor.execute("ROLLBACK TO SAVEPOINT invoice_service")
                cursor.close()
                raise

        self.service = InvoiceService(session, gold_prices=QuotePrices(quotes),
                                      transaction_factory=savepoint_transaction)

    def sql(self, query, params=(), fetch=False):
        cursor = self.connection.cursor(dictionary=True)
        try:
            cursor.execute(query, params)
            return cursor.fetchall() if fetch else cursor.lastrowid
        finally:
            cursor.close()

    def counts(self):
        return {t: self.sql(f"SELECT COUNT(*) AS n FROM {t}", fetch=True)[0]["n"] for t in COUNTED_TABLES}

    def draft(self, customer=None, payment="30000", method="PHYSICAL_CASH"):
        return InvoiceDraft(self.salesperson, None if customer is None else str(customer),
                            (SaleLineInput(str(self.item), making_per_gram="150"),),
                            (OldGoldLineInput(self.k18, "4.000", "AMOUNT", "73"),),
                            (PaymentInput(method, payment),) if payment else ())

    def test_exchange_invoice_is_written_atomically_with_real_constraints(self):
        before = self.counts()
        result = self.service.finalize(self.draft(self.customer), "REHEARSAL-TOKEN")
        # S = 10.5 x 6232 + 10.5 x 150 = 67011.00; B = 4 x 5320 - 73 = 21207.00; N = 45804.00; R = 15804.00
        self.assertEqual((result["sale_total"], result["old_gold_total"], result["net_amount"],
                          result["residual_amount"], result["settlement_status"]),
                         ("67011.00", "21207.00", "45804.00", "15804.00", "PARTIAL"))
        today = self.sql("SELECT DATE_FORMAT(NOW(), '%y%m%d') AS d", fetch=True)[0]["d"]
        self.assertRegex(result["invoice_number"], rf"^{today}(0[1-9]|[1-9][0-9])$")
        after = self.counts()
        delta = {t: after[t] - before[t] for t in COUNTED_TABLES}
        self.assertEqual(delta, {"invoices": 1, "invoice_lines": 2, "invoice_payments": 1, "invoice_events": 2,
                                 "invoice_customer_snapshots": 1, "invoice_price_snapshots": 3,
                                 "customer_transactions": 1, "old_gold_movements": 1, "inventory_movements": 1,
                                 "invoice_number_sequences": delta["invoice_number_sequences"], "audit_log": 1,
                                 "cash_movements": 0, "bank_movements": 0})

        invoice = InvoiceRepository(self.connection).get_invoice(result["invoice_id"])
        self.assertEqual((invoice["employee_id"], invoice["created_by_user_id"], invoice["finalized_by_user_id"]),
                         (self.salesperson, self.user_id, self.user_id))
        self.assertEqual((invoice["record_origin"], invoice["invoice_status"]), ("NATIVE", "FINALIZED"))
        self.assertEqual(invoice["balance_after"] - invoice["balance_before"], D("15804.00"))
        self.assertEqual({p["karat_id"]: (p["quote_id"], p["provider_timestamp"]) for p in invoice["price_snapshots"]},
                         {k: (str(q[0]), q[4].replace(tzinfo=None)) for k, q in self.quotes.items()})
        self.assertEqual(self.sql("SELECT inventory_status, updated_by_user_id FROM inventory_items WHERE id = %s",
                                  (self.item,), fetch=True), [{"inventory_status": "SOLD",
                                                                "updated_by_user_id": self.user_id}])
        self.assertEqual(self.sql("SELECT performed_by_user_id, performed_by_employee_id, reference_type "
                                  "FROM inventory_movements WHERE inventory_item_id = %s", (self.item,), fetch=True),
                         [{"performed_by_user_id": self.user_id, "performed_by_employee_id": self.salesperson,
                           "reference_type": "INVOICE"}])
        movement = self.sql("SELECT balance_effect, price_quote_id, created_by_user_id, received_by_employee_id "
                            "FROM old_gold_movements WHERE id = %s", (invoice["lines"][1]["old_gold_movement_id"],),
                            fetch=True)[0]
        self.assertEqual(movement, {"balance_effect": "COUNTED", "price_quote_id": self.quotes[self.k18][0],
                                    "created_by_user_id": self.user_id, "received_by_employee_id": self.salesperson})
        ledger = self.sql("SELECT t.code, ct.amount_delta, ct.reference_id FROM customer_transactions ct "
                          "JOIN customer_transaction_types t ON t.id = ct.transaction_type_id "
                          "WHERE ct.customer_id = %s", (self.customer,), fetch=True)
        self.assertEqual(ledger, [{"code": "SALE_BALANCE_DUE", "amount_delta": D("15804.00"),
                                   "reference_id": int(result["invoice_id"])}])

    def test_injected_failure_rolls_back_every_table(self):
        before = self.counts()
        original = InvoiceRepository.insert_payment

        def failing(repository, **kwargs):
            raise RuntimeError("injected failure after lines, movements and stock updates")
        InvoiceRepository.insert_payment = failing
        try:
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.service.finalize(self.draft(self.customer), "REHEARSAL-TOKEN")
        finally:
            InvoiceRepository.insert_payment = original
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.sql("SELECT inventory_status FROM inventory_items WHERE id = %s", (self.item,),
                                  fetch=True), [{"inventory_status": "IN_STOCK"}])

    def test_rules_are_enforced_before_anything_is_written(self):
        before = self.counts()
        cases = [(self.draft(self.placeholder), "CUSTOMER_PLACEHOLDER"),
                 (self.draft(None), "CUSTOMER_REQUIRED"),
                 (self.draft(self.customer, method="INSTAPAY"), "PAYMENT_METHOD_NOT_ALLOWED")]
        for draft, code in cases:
            with self.subTest(code=code), self.assertRaises(InvoiceError) as context:
                self.service.finalize(draft, "REHEARSAL-TOKEN")
            self.assertEqual(context.exception.code, code)
        self.assertEqual(self.counts(), before)
        self.service.finalize(self.draft(self.customer), "REHEARSAL-TOKEN")
        with self.assertRaises(InvoiceError) as context:                    # the item is now SOLD
            self.service.finalize(self.draft(self.customer), "REHEARSAL-TOKEN")
        self.assertEqual(context.exception.code, "ITEM_UNAVAILABLE")

    def test_preview_matches_finalize_and_writes_nothing(self):
        before = self.counts()
        preview = self.service.preview(self.draft(self.customer))
        self.assertEqual(self.counts(), before)
        result = self.service.finalize(self.draft(self.customer), preview["price_token"])
        for key in ("sale_total", "old_gold_total", "net_amount", "residual_amount", "settlement_status"):
            self.assertEqual(preview[key], result[key])


if __name__ == "__main__":
    unittest.main()
