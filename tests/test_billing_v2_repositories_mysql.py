"""Billing V2 repositories on real MySQL: the disposable masgold_rehearsal database only.

Opt-in: set MASGOLD_REHEARSAL_DB_TESTS=1. Connects as masgold_build (password
from Windows Credential Manager 'masGold' / 'masgold_build_rehearsal'), refuses
any other database, and rolls every test back. All rows are synthetic.
"""
from datetime import date, datetime
from decimal import Decimal
import os
import unittest
import uuid

from repositories.barcode_repository import BarcodeRepository, BarcodeSequenceExhausted
from repositories.customer_repository import CustomerRepository
from repositories.dead_cash_repository import DeadCashRepository, DeadCashRepositoryError
from repositories.invoice_repository import InvoiceNumberExhausted, InvoiceRepository, InvoiceRepositoryError
from repositories.old_gold_conversion_repository import OldGoldConversionRepository
from services.invoice_calculator import InvoiceCalculator as Calc


ENABLED = os.environ.get("MASGOLD_REHEARSAL_DB_TESTS") == "1"
TARGET = "masgold_rehearsal"
DAY = date(2031, 1, 15)            # far from real business days


def rehearsal_connection():
    import keyring
    from mysql.connector import MySQLConnection
    password = keyring.get_password("masGold", "masgold_build_rehearsal")
    if not password:
        raise AssertionError("masgold_build credential missing")
    connection = MySQLConnection(host="localhost", port=3306, user="masgold_build", password=password,
                                 database=TARGET, autocommit=False)
    cursor = connection.cursor()
    cursor.execute("SELECT DATABASE(), CURRENT_USER()")
    database, user = cursor.fetchone()
    cursor.execute("SELECT target_database FROM masgold_rehearsal_marker WHERE marker_key = 'MASGOLD_SCHEMA_REHEARSAL'")
    marker = cursor.fetchone()
    cursor.close()
    if database != TARGET or not user.startswith("masgold_build@") or marker != (TARGET,):
        connection.close()
        raise AssertionError(f"Refusing: session {database}/{user}, marker {marker}")
    return connection


@unittest.skipUnless(ENABLED, "set MASGOLD_REHEARSAL_DB_TESTS=1 to run against masgold_rehearsal")
class RehearsalTestCase(unittest.TestCase):
    def setUp(self):
        self.connection = rehearsal_connection()
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)
        self.suffix = uuid.uuid4().hex[:8]

    def sql(self, query, params=(), fetch=False):
        cursor = self.connection.cursor(dictionary=True)
        try:
            cursor.execute(query, params)
            return cursor.fetchall() if fetch else cursor.lastrowid
        finally:
            cursor.close()

    def karat_id(self, value=21):
        return self.sql("SELECT id FROM karats WHERE karat_value = %s", (value,), fetch=True)[0]["id"]

    def employee(self):
        return self.sql("INSERT INTO employees (employee_code, name) VALUES (%s, 'موظف تجريبي')",
                        (f"T{self.suffix}",))

    def item(self, weight="10.500", barcode=None):
        token = uuid.uuid4().hex[:10]
        type_id = self.sql("INSERT INTO item_types (code, name) VALUES (%s, %s)",
                           (f"T{token}", f"خاتم تجريبي {token}"))
        item_id = self.sql("INSERT INTO inventory_items (item_type_id, karat_id, weight_grams, acquisition_source) "
                           "VALUES (%s, %s, %s, 'MANUAL_ENTRY')", (type_id, self.karat_id(), weight))
        if barcode:
            self.sql("INSERT INTO inventory_barcodes (inventory_item_id, barcode_value) VALUES (%s, %s)",
                     (item_id, barcode))
        return item_id

    def customer(self, origin="NATIVE", completeness="COMPLETE"):
        customer_id = CustomerRepository(self.connection).create_customer(
            f"عميل تجريبي {self.suffix}", None, None, None, None)
        self.sql("UPDATE customers SET record_origin = %s, data_completeness = %s WHERE id = %s",
                 (origin, completeness, customer_id))
        return customer_id


class InvoiceNumberingMySqlTests(RehearsalTestCase):
    def test_sequence_seeds_skips_and_limits(self):
        repository = InvoiceRepository(self.connection)
        self.assertEqual(repository.next_invoice_number(DAY), "31011501")
        self.assertEqual(repository.next_invoice_number(DAY), "31011502")
        other = date(2031, 1, 16)
        # An imported number already on that day seeds the sequence past it.
        self.sql("INSERT INTO invoices (invoice_number, invoice_type, invoice_status, record_origin, "
                 "legacy_source_system, legacy_completeness, net_amount, net_direction) "
                 "VALUES ('31011607', 'SELL', 'FINALIZED', 'LEGACY', 'golddb', 'PARTIAL', 10, 'CUSTOMER_PAYS_SHOP')")
        self.assertEqual(repository.next_invoice_number(other), "31011608")
        self.sql("UPDATE invoice_number_sequences SET last_seq = 98 WHERE sequence_date = %s", (DAY,))
        self.assertEqual(repository.next_invoice_number(DAY), "31011599")
        with self.assertRaises(InvoiceNumberExhausted):
            repository.next_invoice_number(DAY)

    def test_second_session_waits_for_the_day_lock(self):
        from mysql.connector import errors
        InvoiceRepository(self.connection).next_invoice_number(DAY)
        second = rehearsal_connection()
        self.addCleanup(second.close)
        self.addCleanup(second.rollback)
        cursor = second.cursor()
        cursor.execute("SET SESSION innodb_lock_wait_timeout = 1")
        cursor.close()
        with self.assertRaises(errors.DatabaseError) as context:
            InvoiceRepository(second).next_invoice_number(DAY)
        self.assertEqual(context.exception.errno, 1205)                   # lock wait timeout


class InvoiceWriteMySqlTests(RehearsalTestCase):
    def test_native_exchange_invoice_through_the_repository(self):
        invoices, customers = InvoiceRepository(self.connection), CustomerRepository(self.connection)
        employee_id, customer_id = self.employee(), self.customer()
        item_id = self.item(barcode=f"T-{self.suffix}")
        customer = invoices.lock_customer(str(customer_id))
        self.assertEqual((customer["customer_id"], customer["record_origin"]), (str(customer_id), "NATIVE"))
        [item] = invoices.lock_sale_items([str(item_id)])
        self.assertEqual((item["inventory_item_id"], item["inventory_status"]), (str(item_id), "IN_STOCK"))

        sale = Calc.sale_line(item["weight_grams"], "6232", making_per_gram="150")
        old = Calc.old_gold_line("5", "6207", discount_type="PERCENT", discount_value="1.5")
        settlement = Calc.settlement([sale], [old], [Calc.payment("IN", "30000")])
        number = invoices.next_invoice_number(DAY)
        now = datetime(2031, 1, 15, 11, 0, 0)
        invoice_id = invoices.insert_invoice(
            invoice_number=number, invoice_type=settlement.invoice_type, customer_id=str(customer_id),
            employee_id=employee_id, invoice_date=now, sell_total=settlement.sale_total,
            buy_total=settlement.old_gold_total, net_amount=settlement.net_amount,
            net_direction=settlement.net_direction, settlement_status=settlement.settlement_status,
            created_by_user_id=None, finalized_at=now)
        balance = customers.get_balance(customer_id)
        invoices.insert_customer_snapshot(invoice_id=invoice_id, customer=customer, balance_before=balance,
                                          balance_after=balance + settlement.residual_signed,
                                          dead_cash_warning_active=False)
        invoices.insert_price_snapshots(invoice_id=invoice_id, source_code="MANUAL", prices=[
            {"karat_id": self.karat_id(), "shop_buy_price_per_gram": Decimal("6207"),
             "shop_sell_price_per_gram": Decimal("6232"), "quote_id": None,
             "provider_timestamp": datetime(2031, 1, 15, 8, 59, 57)}])
        invoices.insert_sale_line(invoice_id=invoice_id, line_number=1, item=item,
                                  gold_price_per_gram=sale.gold_price_per_gram, making_per_gram=sale.making_per_gram,
                                  making_total=sale.making_total, gross_total=sale.gold_value,
                                  line_total=sale.line_total)
        invoices.mark_item_sold(inventory_item_id=item["inventory_item_id"], updated_by_user_id=None)
        invoices.add_inventory_sold_movement(item=item, invoice_id=invoice_id, invoice_number=number,
                                             performed_by_user_id=None, performed_by_employee_id=employee_id)
        movement_id = invoices.add_customer_buy_in_movement(
            karat_id=self.karat_id(), weight_grams=old.weight_grams, gold_price_per_gram=old.gold_price_per_gram,
            cash_value=old.line_total, price_quote_id=None, movement_group_id=str(uuid.uuid4()),
            invoice_id=invoice_id, invoice_number=number, customer_id=customer_id,
            created_by_user_id=None, received_by_employee_id=employee_id)
        invoices.insert_old_gold_line(
            invoice_id=invoice_id, line_number=2, karat_id=self.karat_id(), weight_grams=old.weight_grams,
            gold_price_per_gram=old.gold_price_per_gram, gross_total=old.gross_total,
            discount_type=old.discount_type, discount_value=old.discount_value,
            discount_amount=old.discount_amount, line_total=old.line_total, old_gold_movement_id=movement_id)
        cash = invoices.get_payment_method("PHYSICAL_CASH")
        invoices.insert_payment(invoice_id=invoice_id, payment_method_id=cash["payment_method_id"],
                                direction="IN", amount=Decimal("30000.00"), created_by_user_id=None)
        customers.add_transaction(customer_id=customer_id, type_code="SALE_BALANCE_DUE",
                                  amount_delta=settlement.residual_signed, created_by_user_id=None,
                                  reference_type="INVOICE", reference_id=invoice_id, reference_code=number)
        invoices.insert_event(invoice_id=invoice_id, event_type="FINALIZED", performed_by_user_id=None,
                              details={"net": settlement.net_signed, "residual": settlement.residual_signed})

        stored = invoices.get_invoice(invoice_id)
        self.assertEqual((stored["invoice_id"], stored["invoice_number"], stored["record_origin"]),
                         (invoice_id, number, "NATIVE"))
        self.assertEqual([(l["line_type"], l["line_total"]) for l in stored["lines"]],
                         [("SELL_INVENTORY", Decimal("67011.00")), ("BUY_OLD_GOLD", Decimal("30569.47"))])
        self.assertEqual(stored["lines"][1]["old_gold_movement_id"], movement_id)
        self.assertEqual(stored["lines"][0]["barcode_snapshot"], f"T-{self.suffix}")
        self.assertEqual(stored["payments"][0]["payment_method_code"], "PHYSICAL_CASH")
        self.assertEqual(stored["price_snapshots"][0]["provider_timestamp"], datetime(2031, 1, 15, 8, 59, 57))
        self.assertEqual(customers.get_balance(customer_id), settlement.residual_signed)
        self.assertEqual([r["invoice_id"] for r in invoices.search_invoices(number=number)], [invoice_id])
        self.assertEqual(self.sql("SELECT balance_effect, reference_type, invoice_id FROM old_gold_movements "
                                  "WHERE id = %s", (movement_id,), fetch=True),
                         [{"balance_effect": "COUNTED", "reference_type": "INVOICE", "invoice_id": None}])
        with self.assertRaises(InvoiceRepositoryError):                    # already SOLD
            invoices.mark_item_sold(inventory_item_id=item_id, updated_by_user_id=None)

    def test_database_rejects_inconsistent_native_rows(self):
        from mysql.connector import errors
        invoices = InvoiceRepository(self.connection)
        with self.assertRaises(errors.DatabaseError) as context:
            invoices.insert_invoice(
                invoice_number="31011501", invoice_type="SELL", customer_id=None, employee_id=None,
                invoice_date=datetime(2031, 1, 15), sell_total=Decimal("100.00"), buy_total=Decimal("0.00"),
                net_amount=Decimal("90.00"), net_direction="CUSTOMER_PAYS_SHOP", settlement_status="UNSETTLED",
                created_by_user_id=None, finalized_at=datetime(2031, 1, 15))
        self.assertEqual(context.exception.errno, 3819)                   # chk_invoice_native_totals


class CustomerMySqlTests(RehearsalTestCase):
    def test_placeholders_are_hidden_unless_explicitly_requested(self):
        repository = CustomerRepository(self.connection)
        native, placeholder = self.customer(), self.customer("LEGACY_PLACEHOLDER", "UNKNOWN")
        found = {row["customer_id"] for row in repository.search_customers(self.suffix)}
        self.assertEqual(found, {native})
        found = {row["customer_id"] for row in repository.search_legacy_placeholder_accounts(self.suffix)}
        self.assertEqual(found, {placeholder})
        found = {row["customer_id"] for row in repository.search_customers(self.suffix,
                                                                            include_legacy_placeholders=True)}
        self.assertEqual(found, {native, placeholder})
        self.assertEqual(repository.get_customer_for_update(placeholder)["record_origin"], "LEGACY_PLACEHOLDER")

    def test_ledger_writes_and_balance(self):
        repository = CustomerRepository(self.connection)
        customer_id = self.customer()
        repository.add_transaction(customer_id=customer_id, type_code="SALE_BALANCE_DUE",
                                   amount_delta=Decimal("500.00"), created_by_user_id=None)
        repository.add_transaction(customer_id=customer_id, type_code="CUSTOMER_PAYMENT",
                                   amount_delta=Decimal("-120.25"), created_by_user_id=None)
        self.assertEqual(repository.get_balance(customer_id), Decimal("379.75"))


class DeadCashMySqlTests(RehearsalTestCase):
    def test_writeoff_recovery_and_warning_lifecycle(self):
        customers, dead_cash = CustomerRepository(self.connection), DeadCashRepository(self.connection)
        customer_id = self.customer()
        customers.add_transaction(customer_id=customer_id, type_code="SALE_BALANCE_DUE",
                                  amount_delta=Decimal("5000.00"), created_by_user_id=None)
        writeoff_tx = customers.add_transaction(customer_id=customer_id, type_code="DEAD_CASH_WRITE_OFF",
                                                amount_delta=Decimal("-5000.00"), created_by_user_id=None)
        writeoff_id = dead_cash.insert_writeoff(customer_id=customer_id, amount=Decimal("5000.00"),
                                                balance_before=Decimal("5000.00"), balance_after=Decimal("0.00"),
                                                writeoff_transaction_id=writeoff_tx, reason="ديون معدومة",
                                                created_by_user_id=None)
        self.assertEqual(dead_cash.lock_writeoff(writeoff_id)["writeoff_direction"], "CUSTOMER_OWES_SHOP")
        flag_id = dead_cash.create_warning(customer_id=customer_id, reason="ديون معدومة",
                                           related_dead_cash_id=writeoff_id, created_by_user_id=None)
        self.assertEqual(dead_cash.get_active_warning(customer_id, for_update=True)["flag_id"], flag_id)
        from mysql.connector import errors
        with self.assertRaises(errors.IntegrityError):                    # one active warning only
            dead_cash.create_warning(customer_id=customer_id, reason="x", created_by_user_id=None)

        recovery_tx = customers.add_transaction(customer_id=customer_id, type_code="DEAD_CASH_RECOVERY",
                                                amount_delta=Decimal("1000.00"), created_by_user_id=None)
        payment_tx = customers.add_transaction(customer_id=customer_id, type_code="CUSTOMER_PAYMENT",
                                               amount_delta=Decimal("-1000.00"), created_by_user_id=None)
        dead_cash.insert_recovery(writeoff_id=writeoff_id, customer_id=customer_id, amount=Decimal("1000.00"),
                                  recovery_transaction_id=recovery_tx, payment_transaction_id=payment_tx,
                                  created_by_user_id=None)
        self.assertEqual(dead_cash.recovered_total(writeoff_id), Decimal("1000.00"))
        self.assertEqual(customers.get_balance(customer_id), Decimal("0.00"))

        dead_cash.deactivate_warning(flag_id=flag_id, deactivated_by_user_id=None, reason="تمت المراجعة")
        self.assertIsNone(dead_cash.get_active_warning(customer_id))
        with self.assertRaises(DeadCashRepositoryError):
            dead_cash.deactivate_warning(flag_id=flag_id, deactivated_by_user_id=None, reason="x")
        reversal_tx = customers.add_transaction(customer_id=customer_id, type_code="DEAD_CASH_REVERSAL",
                                                amount_delta=Decimal("5000.00"), created_by_user_id=None)
        dead_cash.mark_writeoff_reversed(writeoff_id=writeoff_id, reversal_transaction_id=reversal_tx,
                                         reversed_by_user_id=None, reason="خطأ")
        with self.assertRaises(DeadCashRepositoryError):
            dead_cash.mark_writeoff_reversed(writeoff_id=writeoff_id, reversal_transaction_id=reversal_tx,
                                             reversed_by_user_id=None, reason="x")


class BarcodeMySqlTests(RehearsalTestCase):
    def test_sequence_never_reissues_any_historical_barcode(self):
        repository = BarcodeRepository(self.connection)
        retired = self.item()
        self.sql("INSERT INTO inventory_barcodes (inventory_item_id, barcode_value, barcode_status) "
                 "VALUES (%s, '05310115', 'RETIRED')", (retired,))
        self.sql("INSERT INTO inventory_barcodes (inventory_item_id, barcode_value) VALUES (%s, '07310115')",
                 (self.item(),))
        self.assertEqual(repository.allocate_barcode_value(DAY), "08310115")    # seeded past RETIRED/ACTIVE
        self.sql("UPDATE barcode_number_sequences SET last_seq = 98 WHERE sequence_date = %s", (DAY,))
        self.sql("INSERT INTO inventory_barcodes (inventory_item_id, barcode_value) VALUES (%s, '99310115')",
                 (self.item(),))
        self.assertEqual(repository.allocate_barcode_value(DAY), "100310115")   # 99 exists: skipped
        self.sql("UPDATE barcode_number_sequences SET last_seq = 999 WHERE sequence_date = %s", (DAY,))
        with self.assertRaises(BarcodeSequenceExhausted):
            repository.allocate_barcode_value(DAY)


class OldGoldCountedMySqlTests(RehearsalTestCase):
    def test_reference_only_rows_are_ignored_by_balance_and_lock(self):
        repository = OldGoldConversionRepository(self.connection)
        karat = self.karat_id(18)
        before = {r["karat_id"]: Decimal(str(r["balance_grams"])) for r in repository.get_old_gold_balances()}
        adjustment = self.sql("SELECT id FROM old_gold_movement_types WHERE code = 'ADJUSTMENT_IN'", fetch=True)[0]["id"]
        legacy = self.sql("SELECT id FROM old_gold_movement_types WHERE code = 'LEGACY_IN'", fetch=True)[0]["id"]
        self.sql("INSERT INTO old_gold_movements (movement_type_id, karat_id, weight_delta_grams) "
                 "VALUES (%s, %s, 12.345)", (adjustment, karat))
        self.sql("INSERT INTO old_gold_movements (movement_type_id, karat_id, weight_delta_grams, balance_effect, "
                 "legacy_source_table, legacy_source_id) VALUES (%s, %s, 900, 'REFERENCE_ONLY', 'stockold', 1)",
                 (legacy, karat))
        after = {r["karat_id"]: Decimal(str(r["balance_grams"])) for r in repository.get_old_gold_balances()}
        self.assertEqual(after[karat] - before.get(karat, Decimal("0")), Decimal("12.345"))
        locked = repository.lock_old_gold_balance(karat)
        self.assertNotIn(Decimal("900.000"), [Decimal(str(r["weight_delta_grams"])) for r in locked])
        view = self.sql("SELECT balance_grams FROM vw_old_gold_balances WHERE karat_id = %s", (karat,), fetch=True)
        self.assertEqual(view[0]["balance_grams"], after[karat])


if __name__ == "__main__":
    unittest.main()
