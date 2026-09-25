"""014/015 constraint behaviour on the SQLite mirror; synthetic data only."""
from decimal import Decimal
import sqlite3
import unittest

from services.invoice_calculator import InvoiceCalculator as Calc
from tests.billing_v2_schema_fixture import BillingSchemaDatabase


D = Decimal
FINALIZED_AT = "2026-09-25 10:00:00"


class SchemaTestCase(unittest.TestCase):
    def setUp(self):
        self.db = BillingSchemaDatabase()
        self.addCleanup(self.db.close)
        self.customer_id = self.db.insert("customers", customer_code="C000001", name="عميل تجريبي")

    def assertRejected(self, pattern, table, **values):
        with self.assertRaisesRegex(sqlite3.IntegrityError, pattern):
            self.db.insert(table, **values)

    def movement(self, weight, *, code="CUSTOMER_BUY_IN", **extra):
        return self.db.insert("old_gold_movements", karat_id=1, weight_delta_grams=weight,
                              movement_type_id=self.db.id_of("old_gold_movement_types", code), **extra)

    def header(self, settlement, number="26092501", **overrides):
        values = dict(invoice_number=number, invoice_type=settlement.invoice_type,
                      invoice_status="FINALIZED", customer_id=self.customer_id,
                      sell_total=settlement.sale_total, buy_total=settlement.old_gold_total,
                      net_amount=settlement.net_amount, net_direction=settlement.net_direction,
                      settlement_status=settlement.settlement_status, finalized_at=FINALIZED_AT)
        values.update(overrides)
        return values

    @staticmethod
    def sale_values(invoice_id, line, number=1, **overrides):
        values = dict(invoice_id=invoice_id, line_number=number, line_type="SELL_INVENTORY", karat_id=1,
                      inventory_item_id=900 + number, weight_grams=line.weight_grams,
                      gold_price_per_gram=line.gold_price_per_gram, making_per_gram=line.making_per_gram,
                      making_total=line.making_total, gross_total=line.gold_value,
                      line_total=line.line_total)
        values.update(overrides)
        return values

    def old_gold_values(self, invoice_id, line, number=1, **overrides):
        values = dict(invoice_id=invoice_id, line_number=number, line_type="BUY_OLD_GOLD", karat_id=1,
                      old_gold_movement_id=self.movement(line.weight_grams),
                      weight_grams=line.weight_grams, gold_price_per_gram=line.gold_price_per_gram,
                      gross_total=line.gross_total, discount_type=line.discount_type,
                      discount_value=line.discount_value, discount_amount=line.discount_amount,
                      line_total=line.line_total)
        values.update(overrides)
        return values

    def legacy_invoice(self, number="26092411", **overrides):
        values = dict(invoice_number=number, invoice_type="MIXED", invoice_status="FINALIZED",
                      record_origin="LEGACY", legacy_source_system="golddb", legacy_bill_number=number,
                      legacy_completeness="PARTIAL", sell_total=None, buy_total=None,
                      net_amount=D("1500.00"), net_direction="CUSTOMER_PAYS_SHOP")
        values.update(overrides)
        return self.db.insert("invoices", **values)


class NativeInvoiceTests(SchemaTestCase):
    def setUp(self):
        super().setUp()
        self.sale = Calc.sale_line("10.500", "6232", making_per_gram="150")      # 65436.00 + 1575.00
        self.old = Calc.old_gold_line("10", "6207")                               # 62070.00
        self.settlement = Calc.settlement([self.sale], [])
        self.invoice_id = self.db.insert("invoices", **self.header(self.settlement))

    def test_calculator_results_satisfy_native_constraints(self):
        # Totals chosen to be exact in binary floating point (SQLite); MySQL DECIMAL is exact.
        sales = [Calc.sale_line("10.500", "6232", making_per_gram="150"),
                 Calc.sale_line("2", "6232.5", making_total="500")]
        olds = [Calc.old_gold_line("10", "6207"),
                Calc.old_gold_line("10", "6207", discount_type="PERCENT", discount_value="2.5"),
                Calc.old_gold_line("4", "5000", discount_type="AMOUNT", discount_value="73")]
        settlement = Calc.settlement(sales, olds, [Calc.payment("OUT", "40000")])
        self.assertEqual(settlement.net_direction, "SHOP_PAYS_CUSTOMER")
        invoice_id = self.db.insert("invoices", **self.header(settlement, "26092502"))
        for number, line in enumerate(sales, 1):
            self.db.insert("invoice_lines", **self.sale_values(invoice_id, line, number))
        for number, line in enumerate(olds, 3):
            self.db.insert("invoice_lines", **self.old_gold_values(invoice_id, line, number))
        rows = self.db.query("SELECT line_type, discount_type FROM invoice_lines WHERE invoice_id = ? "
                             "ORDER BY line_number", (invoice_id,))
        self.assertEqual([tuple(r) for r in rows], [
            ("SELL_INVENTORY", None), ("SELL_INVENTORY", None), ("BUY_OLD_GOLD", None),
            ("BUY_OLD_GOLD", "PERCENT"), ("BUY_OLD_GOLD", "AMOUNT")])
        self.assertEqual(self.db.query("SELECT record_origin FROM invoices WHERE id = ?",
                                       (invoice_id,))[0][0], "NATIVE")

    def test_exact_percent_discount_line_is_accepted(self):
        line = Calc.old_gold_line("10", "6207", discount_type="PERCENT", discount_value="1.5")
        self.assertEqual((line.discount_amount, line.line_total), (D("931.05"), D("61138.95")))
        self.db.insert("invoice_lines", **self.old_gold_values(self.invoice_id, line, 1))
        rounded = Calc.old_gold_line("3.333", "6207", discount_type="PERCENT", discount_value="0.5")
        self.assertEqual(rounded.discount_amount, D("103.44"))           # 103.43655 half-up
        self.db.insert("invoice_lines", **self.old_gold_values(self.invoice_id, rounded, 2))

    def test_native_header_rejections(self):
        s = self.settlement
        cases = [
            ({"invoice_number": "01260925"}, "chk_invoice_native_number"),      # NNYYMMDD style
            ({"invoice_number": "26092500"}, "chk_invoice_native_number"),
            ({"invoice_number": "26133101"}, "chk_invoice_native_number"),
            ({"invoice_number": "2609251"}, "chk_invoice_native_number"),
            ({"invoice_number": "C26092501"}, "chk_invoice_native_number"),
            ({"invoice_status": "DRAFT", "finalized_at": None}, "chk_invoice_native_status"),
            ({"finalized_at": None}, "chk_invoice_native_status"),
            ({"invoice_status": "VOIDED", "voided_at": FINALIZED_AT}, "chk_invoice_native_status"),
            ({"net_amount": s.net_amount + 1}, "chk_invoice_native_totals"),
            ({"net_direction": "SETTLED"}, "chk_invoice_native_totals"),
            ({"settlement_status": "OVERPAID"}, "chk_invoice_native_totals"),
            ({"sell_total": None}, "chk_invoice_native_totals"),
            ({"buy_total": None}, "chk_invoice_native_totals"),
            ({"legacy_source_system": "golddb"}, "chk_invoice_record_origin"),
            ({"legacy_completeness": "PARTIAL"}, "chk_invoice_record_origin"),
            ({"record_origin": "IMPORTED"}, "chk_invoice_record_origin"),
            ({"buy_total": D("1.00"), "net_amount": s.net_amount - 1}, "chk_invoice_native_type"),
        ]
        for number, (overrides, constraint) in enumerate(cases, 10):
            with self.subTest(overrides=overrides):
                self.assertRejected(constraint, "invoices", **self.header(s, f"260926{number:02d}", **overrides))

    def test_voided_native_invoice_needs_time_and_reason(self):
        self.db.insert("invoices", **self.header(self.settlement, "26092503", invoice_status="VOIDED",
                                                 voided_at=FINALIZED_AT, void_reason="خطأ في الوزن"))

    def test_native_sale_line_rejections(self):
        line = self.sale
        cases = [
            ({"gross_total": line.gold_value + D("0.01"),
              "line_total": line.line_total + D("0.01")}, "chk_invoice_line_native_values"),
            ({"gold_price_per_gram": None}, "chk_invoice_line_native_values"),
            ({"line_total": None}, "chk_invoice_line_native_values"),
            ({"gross_total": None}, "chk_invoice_line_native_values"),
            ({"quantity": 2}, "chk_invoice_line_native_values"),
            ({"line_type": "OTHER"}, "chk_invoice_line_native_values"),
            ({"making_total": None, "making_per_gram": None}, "chk_invoice_line_native_sale"),
            ({"making_total": line.making_total + 1,
              "line_total": line.line_total + 1}, "chk_invoice_line_native_sale"),
            ({"line_total": line.line_total + 1}, "chk_invoice_line_native_sale"),
            ({"old_gold_movement_id": self.movement("1")}, "chk_invoice_line_native_sale"),
            ({"discount_type": "AMOUNT", "discount_value": 1, "discount_amount": 1},
             "chk_invoice_line_(discount_scope|native_sale)"),
        ]
        for number, (overrides, constraint) in enumerate(cases, 1):
            with self.subTest(overrides=overrides):
                self.assertRejected(constraint, "invoice_lines",
                                    **self.sale_values(self.invoice_id, line, number, **overrides))

    def test_native_old_gold_line_rejections(self):
        plain = self.old
        percent = Calc.old_gold_line("10", "6207", discount_type="PERCENT", discount_value="1.5")
        cases = [
            (plain, {"old_gold_movement_id": None}, "chk_invoice_line_native_old_gold"),
            (plain, {"making_total": D("1")}, "chk_invoice_line_native_old_gold"),
            (plain, {"discount_amount": None}, "chk_invoice_line_native_old_gold"),
            (plain, {"line_total": plain.line_total - 1}, "chk_invoice_line_native_old_gold"),
            (percent, {"discount_amount": D("931.00"), "line_total": D("61139.00")},
             "chk_invoice_line_discount_amount"),
            (percent, {"discount_value": D("100.01")}, "chk_invoice_line_discount_amount"),
            (percent, {"discount_type": "AMOUNT"}, "chk_invoice_line_discount_amount"),
            (plain, {"discount_type": "AMOUNT", "discount_value": D("62070.01"),
                     "discount_amount": D("62070.01"), "line_total": D("-0.01")},
             "chk_invoice_line_(discount_amount|total)"),
            (plain, {"discount_type": "ROUND5", "discount_value": D("5")}, "chk_invoice_line_discount_type"),
            (plain, {"discount_type": "PERCENT"}, "chk_invoice_line_discount_type"),
            (plain, {"discount_value": D("5")}, "chk_invoice_line_discount_type"),
        ]
        for number, (line, overrides, constraint) in enumerate(cases, 1):
            with self.subTest(overrides=overrides):
                self.assertRejected(constraint, "invoice_lines",
                                    **self.old_gold_values(self.invoice_id, line, number, **overrides))

    def test_old_gold_movement_links_to_one_line_only(self):
        values = self.old_gold_values(self.invoice_id, self.old, 1)
        self.db.insert("invoice_lines", **values)
        values.update(line_number=2)
        self.assertRejected("UNIQUE constraint failed: invoice_lines.old_gold_movement_id",
                            "invoice_lines", **values)

    def test_line_origin_must_match_invoice_origin(self):
        self.assertRejected("FOREIGN KEY", "invoice_lines",
                            **self.sale_values(self.invoice_id, self.sale, 1, record_origin="LEGACY"))
        legacy_id = self.legacy_invoice()
        self.assertRejected("FOREIGN KEY", "invoice_lines", **self.sale_values(legacy_id, self.sale, 1))


class LegacyRelaxationTests(SchemaTestCase):
    def test_legacy_rows_may_keep_unknown_amounts_as_null(self):
        invoice_id = self.legacy_invoice()
        self.db.insert("invoice_lines", invoice_id=invoice_id, record_origin="LEGACY", line_number=1,
                       line_type="SELL_INVENTORY", karat_id=1, weight_grams=D("7.25"),
                       gold_price_per_gram=D("3950"), line_total=None, gross_total=None,
                       provenance_json='{"source_table": "stock"}')
        self.db.insert("invoice_lines", invoice_id=invoice_id, record_origin="LEGACY", line_number=2,
                       line_type="OTHER", karat_id=1, weight_grams=D("3.1"),
                       gold_price_per_gram=None, line_total=None)
        row = self.db.query("SELECT sell_total, buy_total, net_amount FROM invoices WHERE id = ?",
                            (invoice_id,))[0]
        self.assertEqual((row[0], row[1]), (None, None))
        self.assertEqual(D(str(row[2])), D("1500"))

    def test_legacy_marker_and_completeness_are_required(self):
        for number, overrides in enumerate(({"legacy_source_system": None},
                                            {"legacy_completeness": None},
                                            {"legacy_completeness": "GUESSED"}), 12):
            with self.subTest(overrides=overrides), self.assertRaisesRegex(
                    sqlite3.IntegrityError, "chk_invoice_(record_origin|legacy_completeness)"):
                self.legacy_invoice(f"260924{number}", **overrides)

    def test_legacy_invoice_keeps_required_net_and_value_ranges(self):
        with self.assertRaisesRegex(sqlite3.IntegrityError, "NOT NULL constraint failed: invoices.net_amount"):
            self.legacy_invoice(net_amount=None)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "chk_invoice_totals"):
            self.legacy_invoice("26092412", sell_total=D("-1"))


class OldGoldBalanceTests(SchemaTestCase):
    def balances(self):
        return {r["karat_id"]: (D(str(r["balance_grams"])), D(str(r["total_in_grams"])))
                for r in self.db.query("SELECT * FROM vw_old_gold_balances")}

    def test_reference_only_history_never_affects_balances(self):
        self.movement(D("100"), code="OPENING_BALANCE")
        self.movement(D("-10.5"), code="CUSTOMER_BUY_IN")
        self.movement(D("500"), code="LEGACY_IN", balance_effect="REFERENCE_ONLY",
                      legacy_source_table="stockold", legacy_source_id=77)
        self.assertEqual(self.balances()[1], (D("89.5"), D("100")))
        self.assertEqual(self.balances()[2], (D("0"), D("0")))
        effects = [r[0] for r in self.db.query("SELECT balance_effect FROM old_gold_movements ORDER BY id")]
        self.assertEqual(effects, ["COUNTED", "COUNTED", "REFERENCE_ONLY"])

    def test_reference_only_rows_must_name_their_legacy_source(self):
        for extra in ({}, {"legacy_source_table": "stockold"}, {"legacy_source_id": 5}):
            with self.subTest(extra=extra), self.assertRaisesRegex(sqlite3.IntegrityError,
                                                                   "chk_old_gold_balance_effect"):
                self.movement(D("1"), code="LEGACY_IN", balance_effect="REFERENCE_ONLY", **extra)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "chk_old_gold_balance_effect"):
            self.movement(D("1"), balance_effect="IGNORED")


class CustomerOriginTests(SchemaTestCase):
    def test_origin_and_completeness_combinations(self):
        accepted = [("NATIVE", "COMPLETE"), ("LEGACY", "COMPLETE"), ("LEGACY", "PARTIAL"),
                    ("LEGACY", "UNKNOWN"), ("LEGACY_PLACEHOLDER", "UNKNOWN")]
        rejected = [("NATIVE", "PARTIAL"), ("LEGACY_PLACEHOLDER", "PARTIAL"),
                    ("LEGACY_PLACEHOLDER", "COMPLETE"), ("SYSTEM", "COMPLETE"), ("LEGACY", "GUESSED")]
        for number, (origin, completeness) in enumerate(accepted, 2):
            self.db.insert("customers", id=number, name=f"c{number}", record_origin=origin,
                           data_completeness=completeness)
        for origin, completeness in rejected:
            with self.subTest(origin=origin, completeness=completeness):
                self.assertRejected("chk_customer_record_origin", "customers", name="x",
                                    record_origin=origin, data_completeness=completeness)

    def test_defaults_are_native_complete(self):
        row = self.db.query("SELECT record_origin, data_completeness FROM customers WHERE id = ?",
                            (self.customer_id,))[0]
        self.assertEqual(tuple(row), ("NATIVE", "COMPLETE"))


class SequenceTests(SchemaTestCase):
    def test_invoice_sequence_is_limited_to_two_digits(self):
        self.db.insert("invoice_number_sequences", sequence_date="2026-09-25", last_seq=99)
        self.assertRejected("chk_invoice_number_seq_range", "invoice_number_sequences",
                            sequence_date="2026-09-26", last_seq=100)
        self.assertRejected("UNIQUE|PRIMARY KEY", "invoice_number_sequences",
                            sequence_date="2026-09-25", last_seq=1)

    def test_barcode_sequence_allows_up_to_999_per_day(self):
        self.db.insert("barcode_number_sequences", sequence_date="2026-09-25", last_seq=999)
        self.assertRejected("chk_barcode_number_seq_range", "barcode_number_sequences",
                            sequence_date="2026-09-26", last_seq=1000)


class DeadCashTests(SchemaTestCase):
    def setUp(self):
        super().setUp()
        self.other_customer = self.db.insert("customers", customer_code="C000002", name="عميل آخر")
        tx = lambda code, amount: self.db.insert(
            "customer_transactions", customer_id=self.customer_id, amount_delta=amount,
            transaction_type_id=self.db.id_of("customer_transaction_types", code))
        self.writeoff = self.db.insert("dead_cash_writeoffs", customer_id=self.customer_id,
                                       amount=D("5000"), reason="ديون معدومة",
                                       writeoff_transaction_id=tx("DEAD_CASH_WRITE_OFF", D("-5000")))
        self.recovery_tx = tx("DEAD_CASH_RECOVERY", D("1000"))
        self.payment_tx = tx("CUSTOMER_PAYMENT", D("-1000"))

    def recovery(self, **overrides):
        values = dict(dead_cash_writeoff_id=self.writeoff, customer_id=self.customer_id,
                      amount=D("1000"), recovery_transaction_id=self.recovery_tx,
                      payment_transaction_id=self.payment_tx, created_by_user_id=17)
        values.update(overrides)
        return values

    def test_recovery_links_writeoff_customer_and_both_transactions(self):
        self.db.insert("dead_cash_recoveries", **self.recovery())
        self.assertRejected("UNIQUE", "dead_cash_recoveries", **self.recovery())

    def test_recovery_rejections(self):
        cases = [({"amount": D("0")}, "chk_dead_cash_recovery_amount"),
                 ({"payment_transaction_id": self.recovery_tx}, "chk_dead_cash_recovery_pair"),
                 ({"customer_id": self.other_customer}, "FOREIGN KEY")]
        for overrides, constraint in cases:
            with self.subTest(overrides=overrides):
                self.assertRejected(constraint, "dead_cash_recoveries", **self.recovery(**overrides))

    def test_writeoff_direction(self):
        row = self.db.query("SELECT writeoff_direction FROM dead_cash_writeoffs WHERE id = ?",
                            (self.writeoff,))[0]
        self.assertEqual(row[0], "CUSTOMER_OWES_SHOP")
        self.assertRejected("chk_dead_cash_writeoff_direction", "dead_cash_writeoffs",
                            customer_id=self.customer_id, amount=D("1"), reason="x",
                            writeoff_direction="BOTH", writeoff_transaction_id=self.payment_tx)


class CustomerSnapshotTests(SchemaTestCase):
    def test_balances_are_recorded_together(self):
        s = Calc.settlement([Calc.sale_line("1", "100", making_total="0")], [])
        for number, (before, after) in enumerate(((D("0"), D("100")), (None, None)), 1):
            invoice_id = self.db.insert("invoices", **self.header(s, f"2609250{number}"))
            self.db.insert("invoice_customer_snapshots", invoice_id=invoice_id, customer_id=self.customer_id,
                           balance_before=before, balance_after=after, dead_cash_warning_active=1)
        invoice_id = self.db.insert("invoices", **self.header(s, "26092509"))
        self.assertRejected("chk_invoice_customer_snapshot_balances", "invoice_customer_snapshots",
                            invoice_id=invoice_id, balance_before=D("0"), balance_after=None)


class ProvenanceTests(SchemaTestCase):
    SHA = "a" * 64

    def run_values(self, **overrides):
        values = dict(source_system="golddb", source_file="synthetic_dump.sql", source_sha256=self.SHA,
                      etl_version="0.1", run_mode="REHEARSAL", target_database="synthetic_target")
        values.update(overrides)
        return values

    def test_run_lifecycle(self):
        run_id = self.db.insert("legacy_import_runs", **self.run_values())
        self.assertEqual(self.db.query("SELECT status FROM legacy_import_runs")[0][0], "RUNNING")
        self.db.insert("legacy_import_runs", **self.run_values(
            status="APPROVED", run_mode="PRODUCTION_CUTOVER", finished_at=FINALIZED_AT,
            reconciliation_json="{}", approved_by_user_id=17, approved_at=FINALIZED_AT))
        self.assertTrue(run_id)

    def test_run_rejections(self):
        done = dict(finished_at=FINALIZED_AT, reconciliation_json="{}")
        approved = dict(done, status="APPROVED", approved_by_user_id=17, approved_at=FINALIZED_AT)
        cases = [({"source_sha256": "A" * 64}, "chk_legacy_import_run_sha256"),
                 ({"source_sha256": "a" * 63}, "chk_legacy_import_run_sha256"),
                 ({"run_mode": "PRODUCTION"}, "chk_legacy_import_run_mode"),
                 ({"status": "DONE", **done}, "chk_legacy_import_run_status"),
                 ({"finished_at": FINALIZED_AT}, "chk_legacy_import_run_finished"),
                 ({"status": "FAILED"}, "chk_legacy_import_run_finished"),
                 ({"status": "RECONCILED", "finished_at": FINALIZED_AT}, "chk_legacy_import_run_reconciled"),
                 (approved, "chk_legacy_import_run_approval"),                       # rehearsal
                 ({**approved, "run_mode": "PRODUCTION_CUTOVER", "approved_by_user_id": None},
                  "chk_legacy_import_run_approval"),
                 ({**done, "status": "RECONCILED", "approved_by_user_id": 17},
                  "chk_legacy_import_run_approval")]
        for overrides, constraint in cases:
            with self.subTest(overrides=overrides):
                self.assertRejected(constraint, "legacy_import_runs", **self.run_values(**overrides))

    def test_record_map_and_exceptions(self):
        run_id = self.db.insert("legacy_import_runs", **self.run_values())
        mapping = dict(run_id=run_id, source_system="golddb", source_table="customerdata", source_pk="977",
                       target_table="customers", target_id=977, mapping_role="PRIMARY",
                       completeness="UNKNOWN", metadata_json='{"placeholder": true}')
        self.db.insert("legacy_record_map", **mapping)
        self.assertRejected("UNIQUE", "legacy_record_map", **mapping)
        self.assertRejected("chk_legacy_record_map_role", "legacy_record_map",
                            **dict(mapping, source_pk="978", mapping_role="GUESS"))
        self.assertRejected("chk_legacy_record_map_completeness", "legacy_record_map",
                            **dict(mapping, source_pk="979", completeness="GOOD"))
        self.assertRejected("FOREIGN KEY", "legacy_record_map", **dict(mapping, source_pk="980", run_id=999))

        exception = dict(run_id=run_id, source_system="golddb", source_table="cash", source_pk="C26092401",
                         exception_code="EXCEPTIONAL_BALANCE", severity="WARNING")
        self.db.insert("legacy_exceptions", **exception)
        self.assertRejected("UNIQUE", "legacy_exceptions", **exception)
        resolved = dict(exception, exception_code="PLACEHOLDER_BALANCE", resolution_status="ACCEPTED",
                        resolved_by_user_id=17, resolved_at=FINALIZED_AT)
        self.assertRejected("chk_legacy_exception_resolution", "legacy_exceptions", **resolved)
        self.db.insert("legacy_exceptions", **dict(resolved, resolution_notes="راجعها المالك"))
        self.assertRejected("chk_legacy_exception_resolution", "legacy_exceptions",
                            **dict(exception, exception_code="X", resolved_by_user_id=17))
        self.assertRejected("chk_legacy_exception_severity", "legacy_exceptions",
                            **dict(exception, exception_code="Y", severity="LOW"))


if __name__ == "__main__":
    unittest.main()
