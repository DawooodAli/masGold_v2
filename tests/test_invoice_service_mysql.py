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

    def draft(self, customer=None, payment="45804", method="PHYSICAL_CASH"):
        # Exchange: O = 4 × 5320 − 1.5% (319.20) = 20,960.80; the customer pays 45,804 now and
        # defers 15,804 on the account (على العميل): commercial = O + P + L = 82,568.80.
        return InvoiceDraft(self.salesperson, None if customer is None else str(customer),
                            (SaleLineInput(str(self.item)),),
                            (OldGoldLineInput(self.k18, "4.000", "PERCENT", "1.50"),),
                            (PaymentInput(method, payment),) if payment else (), account_amount="15804",
                            account_direction="CUSTOMER_OWES")

    def test_exchange_invoice_is_written_atomically_with_real_constraints(self):
        before = self.counts()
        result = self.service.finalize(self.draft(self.customer), "REHEARSAL-TOKEN")
        # net = sale_total − old_gold_total = 82,568.80 − 20,960.80 = 61,608.00; residual (net − real
        # money 45,804) equals exactly the deferred account part (15,804): PARTIAL, never SETTLED,
        # because part of the commercial amount is still owed by the customer.
        self.assertEqual((result["sale_total"], result["old_gold_total"], result["net_amount"],
                          result["account_residual_amount"], result["settlement_status"]),
                         ("82568.80", "20960.80", "61608.00", "15804.00", "PARTIAL"))
        today = self.sql("SELECT DATE_FORMAT(NOW(), '%y%m%d') AS d", fetch=True)[0]["d"]
        self.assertRegex(result["invoice_number"], rf"^{today}(0[1-9]|[1-9][0-9])$")
        after = self.counts()
        delta = {t: after[t] - before[t] for t in COUNTED_TABLES}
        self.assertEqual(delta, {"invoices": 1, "invoice_lines": 2, "invoice_payments": 1, "invoice_events": 2,
                                 "invoice_customer_snapshots": 1, "invoice_price_snapshots": 3,
                                 "customer_transactions": 1, "old_gold_movements": 1, "inventory_movements": 1,
                                 "invoice_number_sequences": delta["invoice_number_sequences"], "audit_log": 1,
                                 "cash_movements": 1, "bank_movements": 0})
        cash = self.sql("SELECT cm.amount_delta, t.code, cm.invoice_payment_id, p.id AS payment_id "
                        "FROM cash_movements cm JOIN cash_movement_types t ON t.id = cm.movement_type_id "
                        "JOIN invoice_payments p ON p.id = cm.invoice_payment_id WHERE p.invoice_id = %s",
                        (result["invoice_id"],), fetch=True)
        self.assertEqual([(c["amount_delta"], c["code"]) for c in cash], [(D("45804.00"), "INVOICE_PAYMENT_IN")])

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
                 (self.draft(None), "CUSTOMER_NAME_REQUIRED"),
                 (self.draft(self.customer, method="CARD_POS"), "PAYMENT_METHOD_NOT_ALLOWED"),
                 (self.draft(self.customer, method="INSTAPAY"), "PAYMENT_ACCOUNT_MISSING")]    # no bank account yet
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
        for key in ("sale_total", "old_gold_total", "net_amount", "account_residual_amount", "settlement_status",
                    "effective_sale_total", "average_making_per_gram"):
            self.assertEqual(preview[key], result[key])


class InvoiceWorkflowMySqlTests(InvoiceServiceMySqlTests):
    """016 on real MySQL: every new CHECK, lock and link, rolled back at the end."""

    def opening_old_gold(self, karat_id, grams):
        movement_type = self.sql("SELECT id FROM old_gold_movement_types WHERE code = 'ADJUSTMENT_IN'",
                                 fetch=True)[0]["id"]
        self.sql("INSERT INTO old_gold_movements (movement_type_id, karat_id, weight_delta_grams, balance_effect) "
                 "VALUES (%s, %s, %s, 'COUNTED')", (movement_type, karat_id, grams))

    def balance(self, karat_id):
        return self.sql("SELECT COALESCE(SUM(weight_delta_grams), 0) AS g FROM old_gold_movements "
                        "WHERE karat_id = %s AND balance_effect = 'COUNTED'", (karat_id,), fetch=True)[0]["g"]

    def item_type(self):
        return self.sql("SELECT item_type_id FROM inventory_items WHERE id = %s", (self.item,),
                        fetch=True)[0]["item_type_id"]

    def cash_rows(self, invoice_id):
        return self.sql("SELECT cm.amount_delta, t.code FROM cash_movements cm "
                        "JOIN cash_movement_types t ON t.id = cm.movement_type_id "
                        "JOIN invoice_payments p ON p.id = cm.invoice_payment_id WHERE p.invoice_id = %s",
                        (invoice_id,), fetch=True)

    def bank_rows(self, invoice_id):
        return self.sql("SELECT bm.bank_account_id, bm.amount_delta, t.code FROM bank_movements bm "
                        "JOIN bank_movement_types t ON t.id = bm.movement_type_id "
                        "JOIN invoice_payments p ON p.id = bm.invoice_payment_id WHERE p.invoice_id = %s",
                        (invoice_id,), fetch=True)

    def test_the_money_received_is_the_sale_price_and_reaches_the_cash_ledger(self):
        # 10.5 × 6232 = 65,436.00 gold; 67,005 received → making 1,569.00 (149.43/g), no adjustment.
        draft = InvoiceDraft(self.salesperson, str(self.customer), (SaleLineInput(str(self.item)),), (),
                             (PaymentInput("PHYSICAL_CASH", "67005"),))
        result = self.service.finalize(draft, "REHEARSAL-TOKEN")
        self.assertEqual((result["settlement_status"], result["effective_sale_total"],
                          result["average_making_per_gram"]), ("SETTLED", "67005.00", "149.43"))
        line = self.sql("SELECT making_per_gram, making_total, identification_method FROM invoice_lines "
                        "WHERE invoice_id = %s", (result["invoice_id"],), fetch=True)[0]
        self.assertEqual(line, {"making_per_gram": None, "making_total": D("1569.00"), "identification_method": "SCAN"})
        self.assertEqual(self.cash_rows(result["invoice_id"]), [{"amount_delta": D("67005.00"),
                                                                 "code": "INVOICE_PAYMENT_IN"}])
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM invoice_settlement_adjustments WHERE invoice_id = %s",
                                  (result["invoice_id"],), fetch=True)[0]["n"], 0)
        from mysql.connector import errors
        with self.assertRaises(errors.Error):                # the DB refuses a settlement against the residual sign
            self.sql("INSERT INTO invoice_settlement_adjustments (invoice_id, residual_signed, amount_signed, "
                     "accepted_by_user_id) VALUES (%s, 5.00, -5.00, %s)", (result["invoice_id"], self.user_id))

    def test_old_gold_only_rounding_is_settled_with_approval_and_paid_out_of_cash(self):
        # 10 g × 6207 = 62,070.00 (old gold only: any discount); the shop pays 61,970 → 100 settled.
        draft = InvoiceDraft(self.salesperson, str(self.customer), (), (OldGoldLineInput(self.k21, "10.000"),),
                             (PaymentInput("PHYSICAL_CASH", "61970"),), settlement_choice="SETTLE",
                             settlement_reason="فكة")
        result = self.service.finalize(draft, "REHEARSAL-TOKEN")
        adjustment = self.sql("SELECT amount_signed, approved_by_user_id, reason FROM invoice_settlement_adjustments "
                              "WHERE invoice_id = %s", (result["invoice_id"],), fetch=True)[0]
        self.assertEqual(adjustment, {"amount_signed": D("-100.00"), "approved_by_user_id": self.user_id,
                                      "reason": "فكة"})
        self.assertEqual(self.cash_rows(result["invoice_id"]), [{"amount_delta": D("-61970.00"),
                                                                 "code": "INVOICE_PAYMENT_OUT"}])
        review = self.sql("SELECT review_type FROM management_reviews WHERE invoice_id = %s ORDER BY id",
                          (result["invoice_id"],), fetch=True)
        self.assertEqual([r["review_type"] for r in review], ["CASH_SETTLEMENT", "OLD_GOLD_DISCOUNT"])

    def test_bank_and_wallet_payments_reach_their_real_accounts(self):
        # Accounts created inside this rolled-back transaction only.
        bank = self.sql("INSERT INTO bank_accounts (code, name, account_type, bank_name) VALUES (%s, %s, 'BANK', 'CIB')",
                        (f"R{uuid.uuid4().hex[:8]}", "حساب اختبار"))
        wallet = self.sql("INSERT INTO bank_accounts (code, name, account_type) VALUES (%s, %s, 'MOBILE_WALLET')",
                          (f"W{uuid.uuid4().hex[:8]}", "محفظة اختبار"))
        before = self.counts()
        for method, account, code in (("INSTAPAY", None, "BANK_ACCOUNT_REQUIRED"),
                                      ("INSTAPAY", wallet, "BANK_ACCOUNT_TYPE"),
                                      ("MOBILE_WALLET", bank, "BANK_ACCOUNT_TYPE")):
            draft = InvoiceDraft(self.salesperson, str(self.customer), (SaleLineInput(str(self.item)),), (),
                                 (PaymentInput(method, "67011", bank_account_id=None if account is None else str(account)),))
            with self.subTest(method=method, code=code), self.assertRaises(InvoiceError) as refused:
                self.service.finalize(draft, "REHEARSAL-TOKEN")
            self.assertEqual(refused.exception.code, code)
        self.assertEqual(self.counts(), before)
        # A split payment: InstaPay into the bank account and the rest from the wallet.
        draft = InvoiceDraft(self.salesperson, str(self.customer), (SaleLineInput(str(self.item)),), (),
                             (PaymentInput("INSTAPAY", "60000", bank_account_id=str(bank)),
                              PaymentInput("MOBILE_WALLET", "7011", bank_account_id=str(wallet))))
        result = self.service.finalize(draft, "REHEARSAL-TOKEN")
        self.assertEqual(self.bank_rows(result["invoice_id"]),
                         [{"bank_account_id": bank, "amount_delta": D("60000.00"), "code": "INVOICE_PAYMENT_IN"},
                          {"bank_account_id": wallet, "amount_delta": D("7011.00"), "code": "INVOICE_PAYMENT_IN"}])
        after = self.counts()
        self.assertEqual((after["cash_movements"], after["bank_movements"], after["invoice_payments"]),
                         (before["cash_movements"], before["bank_movements"] + 2, before["invoice_payments"] + 2))
        balances = {r["bank_account_id"]: r["balance"] for r in self.sql(
            "SELECT bank_account_id, balance FROM vw_bank_balances WHERE bank_account_id IN (%s, %s)",
            (bank, wallet), fetch=True)}
        self.assertEqual(balances, {bank: D("60000.00"), wallet: D("7011.00")})

    def test_a_failing_money_ledger_rolls_the_invoice_back(self):
        before = self.counts()
        original = InvoiceRepository.add_cash_movement

        def failing(repository, **kwargs):
            raise RuntimeError("injected ledger failure")
        InvoiceRepository.add_cash_movement = failing
        try:
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.service.finalize(self.draft(self.customer), "REHEARSAL-TOKEN")
        finally:
            InvoiceRepository.add_cash_movement = original
        self.assertEqual(self.counts(), before)

    def test_exchange_discount_is_limited_and_a_changed_one_is_reviewed(self):
        def draft(percent, paid):
            return InvoiceDraft(self.salesperson, str(self.customer), (SaleLineInput(str(self.item)),),
                                (OldGoldLineInput(self.k21, "5.000", "PERCENT", percent),),
                                (PaymentInput("PHYSICAL_CASH", paid),))
        before = self.counts()
        for percent in ("1.00", "3.50"):
            with self.subTest(percent=percent), self.assertRaises(InvoiceError) as refused:
                self.service.finalize(draft(percent, "36596.70"), "REHEARSAL-TOKEN")
            self.assertEqual(refused.exception.code, "OLD_GOLD_DISCOUNT_RANGE")
        self.assertEqual(self.counts(), before)
        # 5 × 6207 = 31,035.00 − 2% (620.70) = 30,414.30; + 36,596.70 received = 67,011.00.
        result = self.service.finalize(draft("2.00", "36596.70"), "REHEARSAL-TOKEN")
        reviews = self.sql("SELECT review_type, JSON_UNQUOTE(JSON_EXTRACT(details_json, '$.default_percent')) AS d "
                           "FROM management_reviews WHERE invoice_id = %s", (result["invoice_id"],), fetch=True)
        self.assertEqual(reviews, [{"review_type": "OLD_GOLD_DISCOUNT", "d": "1.50"}])
        line = self.sql("SELECT making_total FROM invoice_lines WHERE invoice_id = %s AND line_type = 'SELL_INVENTORY'",
                        (result["invoice_id"],), fetch=True)[0]
        self.assertEqual(line["making_total"], D("1575.00"))

    def test_unidentified_sale_opens_a_linked_case_and_leaves_stock_alone(self):
        from services.invoice_service import UNIDENTIFIED, DescribedSaleInput
        draft = InvoiceDraft(self.salesperson, str(self.customer), (), (), (PaymentInput("PHYSICAL_CASH", "25500"),),
                             described_sale_lines=(DescribedSaleInput(UNIDENTIFIED, self.k21, "4.000",
                                                                      item_type_id=self.item_type(), size="18",
                                                                      notes="نقش", reason_code="NOT_FOUND"),))
        before = self.counts()
        result = self.service.finalize(draft, "REHEARSAL-TOKEN")
        line = self.sql("SELECT id, line_type, inventory_item_id, old_gold_movement_id, identification_method, "
                        "size_snapshot FROM invoice_lines WHERE invoice_id = %s", (result["invoice_id"],),
                        fetch=True)[0]
        self.assertEqual((line["line_type"], line["inventory_item_id"], line["old_gold_movement_id"],
                          line["identification_method"], line["size_snapshot"]),
                         ("SELL_UNIDENTIFIED", None, None, "MANUAL", "18"))
        case = self.sql("SELECT case_type, case_status, reason_code, invoice_line_id, salesperson_employee_id, "
                        "observed_weight_grams, created_by_user_id FROM inventory_reconciliation_cases "
                        "WHERE invoice_id = %s", (result["invoice_id"],), fetch=True)[0]
        self.assertEqual(case, {"case_type": "UNIDENTIFIED_SALE", "case_status": "OPEN", "reason_code": "NOT_FOUND",
                                "invoice_line_id": line["id"], "salesperson_employee_id": self.salesperson,
                                "observed_weight_grams": D("4.000"), "created_by_user_id": self.user_id})
        self.assertEqual(self.counts()["inventory_movements"], before["inventory_movements"])
        from repositories.management_review_repository import ManagementReviewRepository
        self.assertGreaterEqual(ManagementReviewRepository(self.connection).open_counts()["INVENTORY_MATCH"], 1)

    def test_sale_from_old_gold_stock_locks_and_deducts_exact_grams(self):
        from services.invoice_service import OLD_GOLD_STOCK, DescribedSaleInput
        self.opening_old_gold(self.k21, "10.000")
        start = self.balance(self.k21)
        stock_line = DescribedSaleInput(OLD_GOLD_STOCK, self.k21, "3.200", description="دبلة سادة")
        draft = InvoiceDraft(self.salesperson, str(self.customer), (), (), (PaymentInput("PHYSICAL_CASH", "20500"),),
                             described_sale_lines=(stock_line,))
        result = self.service.finalize(draft, "REHEARSAL-TOKEN")
        self.assertEqual(self.balance(self.k21), start - D("3.200"))
        movement = self.sql("SELECT m.weight_delta_grams, t.code, m.reference_id FROM invoice_lines l "
                            "JOIN old_gold_movements m ON m.id = l.old_gold_movement_id "
                            "JOIN old_gold_movement_types t ON t.id = m.movement_type_id WHERE l.invoice_id = %s",
                            (result["invoice_id"],), fetch=True)[0]
        self.assertEqual(movement, {"weight_delta_grams": D("-3.200"), "code": "INVOICE_SALE_OUT",
                                    "reference_id": int(result["invoice_id"])})
        # Old gold bought on the same invoice never counts toward what it sells.
        too_much = DescribedSaleInput(OLD_GOLD_STOCK, self.k21, str(self.balance(self.k21) + D("1.000")),
                                      description="غويشة")
        before = self.counts()
        with self.assertRaises(InvoiceError) as refused:
            self.service.finalize(InvoiceDraft(self.salesperson, str(self.customer), (),
                                               (OldGoldLineInput(self.k21, "5.000", "PERCENT", "1.50"),),
                                               (PaymentInput("PHYSICAL_CASH", "999999"),),
                                               described_sale_lines=(too_much,)), "REHEARSAL-TOKEN")
        self.assertEqual(refused.exception.code, "OLD_GOLD_STOCK_INSUFFICIENT")
        self.assertEqual(self.counts(), before)

    def test_new_customer_is_created_inside_the_invoice_transaction(self):
        from services.invoice_service import NewCustomerInput
        tag = uuid.uuid4().hex[:6]
        draft = InvoiceDraft(self.salesperson, None, (SaleLineInput(str(self.item)),), (),
                             (PaymentInput("PHYSICAL_CASH", "67011"),), account_amount="7011",
                             account_direction="CUSTOMER_OWES",
                             new_customer=NewCustomerInput(f"عميل جديد {tag}", f"0155{tag}", "بنها"))
        original = InvoiceRepository.insert_payment

        def failing(repository, **kwargs):
            raise RuntimeError("injected")
        InvoiceRepository.insert_payment = failing
        try:
            with self.assertRaises(RuntimeError):
                self.service.finalize(draft, "REHEARSAL-TOKEN")
        finally:
            InvoiceRepository.insert_payment = original
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM customers WHERE mobile = %s", (f"0155{tag}",),
                                  fetch=True)[0]["n"], 0)                     # rolled back with the invoice
        result = self.service.finalize(draft, "REHEARSAL-TOKEN")
        customer = self.sql("SELECT c.id, c.customer_code, c.place, c.created_by_user_id FROM customers c "
                            "JOIN invoices i ON i.customer_id = c.id WHERE i.id = %s", (result["invoice_id"],),
                            fetch=True)[0]
        self.assertEqual((customer["customer_code"], customer["place"], customer["created_by_user_id"]),
                         (f"C{customer['id']:06d}", "بنها", self.user_id))
        from repositories.customer_repository import CustomerRepository
        self.assertEqual([r["customer_id"] for r in CustomerRepository(self.connection).find_active_by_mobile(
            f"0155{tag}")], [customer["id"]])

    def test_making_outside_the_category_range_opens_a_review(self):
        category = self.sql("INSERT INTO item_categories (code, name, sale_making_min_per_gram) VALUES (%s, %s, 200)",
                            (f"Z{uuid.uuid4().hex[:6]}", "فئة اختبار"))
        self.sql("UPDATE inventory_items SET category_id = %s WHERE id = %s", (category, self.item))
        draft = InvoiceDraft(self.salesperson, str(self.customer), (SaleLineInput(str(self.item)),), (),
                             (PaymentInput("PHYSICAL_CASH", "66500"),))
        self.assertTrue(self.service.preview(draft)["making_review_required"])
        result = self.service.finalize(draft, "REHEARSAL-TOKEN")
        self.assertEqual(result["sale_total"], "66500.00")
        self.assertEqual(self.sql("SELECT review_type FROM management_reviews WHERE invoice_id = %s",
                                  (result["invoice_id"],), fetch=True), [{"review_type": "MAKING_PRICE"}])

    def test_cascade_facets_list_only_in_stock(self):
        repo = InvoiceRepository(self.connection)
        facets = repo.sellable_facets(item_type_id=self.item_type(), karat_id=self.k21)
        self.assertIn(self.item_type(), [int(t["id"]) for t in facets["item_types"]])
        self.assertEqual([int(k["id"]) for k in facets["karats"]], [self.k21])
        self.sql("UPDATE inventory_items SET inventory_status = 'SOLD' WHERE id = %s", (self.item,))
        facets = repo.sellable_facets(item_type_id=self.item_type())
        self.assertNotIn(self.item_type(), [int(t["id"]) for t in facets["item_types"]])


if __name__ == "__main__":
    unittest.main()
