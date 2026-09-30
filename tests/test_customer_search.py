"""Customers search and save regressions; all database access is mocked."""

import unittest
from unittest.mock import MagicMock, patch

from database.connection import DatabaseConnection
from repositories.customer_repository import CustomerRepository
from services.customer_service import CustomerService
from utils.search_normalization import (
    mysql_search_expression,
    normalize_mobile_search,
    normalize_search_text,
)


class NoDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.connection_guard = patch.object(
            DatabaseConnection,
            "connect",
            side_effect=AssertionError("Tests must never connect to a database."),
        )
        self.connection_guard.start()
        self.addCleanup(self.connection_guard.stop)


class SearchNormalizationTests(NoDatabaseTestCase):
    def test_arabic_name_and_place_variants_match_in_both_directions(self):
        pairs = (
            ("أحمد", "احمد"),
            ("إبراهيم", "ابراهيم"),
            ("آدم", "ادم"),
            ("ٱحمد", "احمد"),
            ("فاطمة", "فاطمه"),
            ("على", "علي"),
            ("مصطفى", "مصطفي"),
            ("رؤوف", "رووف"),
            ("رئيس", "رييس"),
            ("مَدِينَة نَصْر", "مدينه نصر"),
            ("  عَبْدُ  الـرَّحْمٰن  ", "عبد الرحمن"),
        )
        for first, second in pairs:
            for query, stored in ((first, second), (second, first)):
                with self.subTest(query=query, stored=stored):
                    self.assertEqual(
                        normalize_search_text(query), normalize_search_text(stored),
                    )
                    self.assertIn(
                        normalize_search_text(query),
                        normalize_search_text("السيد " + stored + " القاهرة"),
                    )

    def test_arabic_and_persian_digits_match_ascii(self):
        for digits in ("٠١٢٣٤٥٦٧٨٩", "۰۱۲۳۴۵۶۷۸۹", "0123456789"):
            with self.subTest(digits=digits):
                self.assertEqual(normalize_search_text(digits), "0123456789")
                self.assertEqual(normalize_mobile_search(digits), "0123456789")

    def test_tatweel_and_arabic_combining_marks_are_search_only(self):
        marks = "ـ\u0610\u061a\u064b\u0652\u065f\u0670\u06d6\u06ed\u0898\u08ff"
        original = "أ" + marks + "حمد"
        self.assertEqual(normalize_search_text(original), "احمد")
        self.assertEqual(normalize_search_text(marks), "")
        # Unrelated punctuation and other scripts keep their original spelling.
        self.assertEqual(normalize_search_text("José e\u0301 !%_"), "José e\u0301 !%_")

    def test_whitespace_collapses_without_joining_words(self):
        self.assertEqual(
            normalize_search_text(" \tعبد\u00a0\u2003 الرحمن\n\r\u202fمحمد\u3000"),
            "عبد الرحمن محمد",
        )
        self.assertNotEqual(normalize_search_text("عبد الله"), normalize_search_text("عبدالله"))
        self.assertEqual(normalize_search_text(None), "")
        self.assertEqual(normalize_search_text(" \n\t"), "")

    def test_mobile_formatting_is_ignored_in_both_directions(self):
        forms = (
            "01012345678",
            "٠١٠ ١٢٣٤ ٥٦٧٨",
            "۰۱۰-۱۲۳۴-۵۶۷۸",
            " +(010).1234–5678 ",
            "010\t1234\n5678",
            "0‐1‑0‒1–2—3―4−5-6(7).8",
        )
        for query in forms:
            for stored in forms:
                with self.subTest(query=query, stored=stored):
                    self.assertEqual(normalize_mobile_search(query), "01012345678")
                    self.assertEqual(normalize_mobile_search(query), normalize_mobile_search(stored))
        self.assertEqual(normalize_search_text("+010-123"), "+010-123")
        self.assertNotEqual(normalize_mobile_search("+20 10 123"), normalize_mobile_search("010123"))

    def test_sql_expression_accepts_only_plain_column_identifiers(self):
        for column in ("c.name", "c.mobile", "place"):
            with self.subTest(column=column):
                expression, params = mysql_search_expression(column, mobile=column == "c.mobile")
                self.assertIn(column, expression)
                self.assertEqual(expression.count("%s"), len(params))
                self.assertNotIn("أ", expression)
                self.assertIn("أ", params)
        for invalid in ("c.name); DROP TABLE customers; --", "c.name COLLATE utf8mb4_bin", "x y", "a.b.c"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                mysql_search_expression(invalid)


class CustomerRepositorySearchTests(NoDatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value
        self.cursor.fetchall.return_value = []
        self.repository = CustomerRepository(self.connection)

    def search_query(self, search, **filters):
        self.repository.search_customers(search, **filters)
        query, params = self.cursor.execute.call_args.args
        self.assertEqual(query.count("%s"), len(params))
        return query, params

    def test_numeric_id_is_an_exact_unsigned_bigint_match(self):
        cases = (
            ("143", 143), ("١٤٣", 143), ("۱۴۳", 143), ("000143", 143),
            ("18446744073709551615", 18446744073709551615),
            ("١٨٤٤٦٧٤٤٠٧٣٧٠٩٥٥١٦١٥", 18446744073709551615),
            ("0", None), ("-1", None), ("1.43", None),
            ("18446744073709551616", None), ("9" * 200, None),
        )
        for term, expected_id in cases:
            with self.subTest(term=term):
                query, params = self.search_query(term)
                self.assertEqual(params[1], expected_id)
                self.assertIn("OR c.id = %s", query)
                self.assertNotIn("CAST(c.id", query)

    def test_customer_code_branch_keeps_original_text(self):
        for code in ("C000143", "c000143", "C٠٠٠١٤٣"):
            with self.subTest(code=code):
                query, params = self.search_query(code)
                self.assertIn("c.customer_code LIKE %s ESCAPE '!'", query)
                self.assertEqual(params[2], "%" + code + "%")
                self.assertIsNone(params[1])

    def test_normalized_search_applies_in_where_before_pagination(self):
        query, params = self.search_query("  أَحـمد  ")
        select, where = query.split("WHERE (", 1)
        for original in ("c.name", "c.mobile", "c.place"):
            self.assertIn(original, select)
            self.assertIn(original, where)
        self.assertNotIn("REGEXP_REPLACE", select)
        self.assertIn("%احمد%", params)
        self.assertIn("JOIN vw_customer_balances", select)
        self.assertLess(query.index("REGEXP_REPLACE"), query.index("LIMIT %s OFFSET %s"))
        self.cursor.close.assert_called_once()

    def test_search_payload_is_bound_and_like_metacharacters_are_literal(self):
        term = "أحمد' OR 1=1 -- !%_"
        query, params = self.search_query(term)
        self.assertNotIn(term, query)
        self.assertNotIn("OR 1=1", query)
        self.assertEqual(params[2], "%أحمد' OR 1=1 -- !!!%!_%")
        self.assertIn("%احمد' OR 1=1 -- !!!%!_%", params)
        self.assertEqual(query.count("ESCAPE '!'"), 4)

    def test_nonempty_terms_that_normalize_empty_cannot_match_every_row(self):
        for term in ("ــــ", "َُِّ", "+() - ."):
            with self.subTest(term=term):
                query, params = self.search_query(term)
                # Only a truly blank input bypasses search. Every normalized
                # branch separately guards against LIKE '%%'.
                self.assertIs(params[0], False)
                self.assertNotEqual(params[2], "%%")
                self.assertEqual(query.count("OR (%s AND"), 3)
                self.assertNotIn("%s = ''", query)
                self.assertNotIn("%s <> ''", query)
                self.assertEqual(normalize_mobile_search(term), "")
                guards = [value for value in params[:-8] if isinstance(value, bool)]
                self.assertEqual(guards, [False, bool(normalize_search_text(term)), False, bool(normalize_search_text(term))])
        _, params = self.search_query(" \n\t ")
        self.assertIs(params[0], True)

    def test_filters_and_limit_offset_remain_bound(self):
        query, params = self.search_query(
            "فاطمة", is_active=False, customer_type_id=7,
            balance_status="SHOP_OWES_CUSTOMER", limit=101, offset=200,
        )
        # Trailing False, False: LEGACY_PLACEHOLDER accounts hidden by default.
        self.assertEqual(params[-10:], (False, False, 7, 7, "SHOP_OWES_CUSTOMER", "SHOP_OWES_CUSTOMER",
                                        False, False, 101, 200))
        self.assertIn("c.is_active = %s", query)
        self.assertIn("c.customer_type_id = %s", query)
        self.assertIn("b.balance_status = %s", query)


class CustomerServiceSearchAndSaveTests(NoDatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value
        self.cursor.lastrowid = 143
        self.cursor.rowcount = 1
        transaction = MagicMock()
        transaction.connection = self.connection
        transaction.__enter__.return_value = transaction
        self.transactions = self.enterContext(patch(
            "services.customer_service.DatabaseTransaction", return_value=transaction,
        ))
        from tests.auth_fakes import customers_page_service
        self.service = customers_page_service()
        self.raw_values = {
            "name": "  أَحـمد  مُحَمَّد  ",
            "mobile": "  +٢٠ (٠١٠) ۱۲۳-۴۵۶  ",
            "place": "  مَدِينَة  نَصْر  ",
            "customer_type_id": None,
            "notes": "  ملاحظة أولى\n  ثانِية\t ",
        }

    def test_service_pagination_has_more_and_filters_are_unchanged(self):
        for size, has_more in ((101, True), (100, False), (0, False)):
            with self.subTest(size=size):
                rows = [{"customer_id": number} for number in range(size)]
                self.cursor.fetchall.return_value = rows
                result = self.service.search_customers(
                    "أحمد", status="INACTIVE", customer_type_id=7,
                    balance_status="CUSTOMER_OWES_SHOP", page=3,
                )
                self.assertEqual(result, {
                    "items": rows[:100], "page": 3, "page_size": 100, "has_more": has_more,
                })
                _, params = self.cursor.execute.call_args.args
                self.assertEqual(params[-10:], (False, False, 7, 7, "CUSTOMER_OWES_SHOP", "CUSTOMER_OWES_SHOP",
                                                False, False, 101, 200))

    def test_picker_page_size_reuses_search_and_fetches_one_extra(self):
        self.cursor.fetchall.return_value = [{"customer_id": number} for number in range(21)]
        result = self.service.search_customers("أحمد", status="ACTIVE", page=3, page_size=20)
        self.assertEqual(len(result["items"]), 20)
        self.assertEqual(result["page_size"], 20)
        self.assertTrue(result["has_more"])
        self.assertEqual(self.cursor.execute.call_args.args[1][-2:], (21, 40))

    def test_page_size_cannot_be_zero_negative_fractional_or_over_limit(self):
        for page_size in (0, -1, 1.5, True, 101):
            with self.subTest(page_size=page_size), self.assertRaises(ValueError):
                self.service.search_customers(page_size=page_size)
        self.transactions.assert_not_called()

    def test_create_preserves_exact_text_in_insert_and_generates_code_separately(self):
        self.assertEqual(self.service.create_customer(**self.raw_values), 143)
        calls = self.cursor.execute.call_args_list
        self.assertEqual(len(calls), 2)
        insert_query, insert_params = calls[0].args
        self.assertIn("INSERT INTO customers", insert_query)
        self.assertEqual(insert_params, (
            self.raw_values["name"], self.raw_values["mobile"], self.raw_values["place"],
            None, self.raw_values["notes"], 1,          # created by the verified actor
        ))
        self.assertEqual(calls[1].args[1], ("C000143", 143))

    def test_update_preserves_exact_text_and_does_not_rewrite_code(self):
        original = dict(self.raw_values, name="اسم سابق", is_active=True)
        self.cursor.fetchone.return_value = dict(original, id=143, customer_code="Legacy-Code")
        self.service.update_customer(143, **self.raw_values, original_values=original)
        query, params = self.cursor.execute.call_args.args
        self.assertIn("UPDATE customers", query)
        self.assertNotIn("customer_code", query)
        self.assertEqual(params, (
            self.raw_values["name"], self.raw_values["mobile"], self.raw_values["place"],
            None, self.raw_values["notes"], 1, 143,     # updated by the verified actor
        ))

    def test_optional_empty_whitespace_and_null_are_not_normalized_on_save(self):
        values = dict(name="عميل", mobile="", place=" \t ", customer_type_id=None, notes=None)
        self.service.create_customer(**values)
        self.assertEqual(self.cursor.execute.call_args_list[0].args[1], ("عميل", "", " \t ", None, None, 1))   # 1 = verified actor

    def test_normalized_equivalent_spelling_is_still_a_real_edit(self):
        original = dict(self.raw_values, name="أحمد", is_active=True)
        self.cursor.fetchone.return_value = dict(original, id=143)
        changed = dict(self.raw_values, name="احمد")
        self.service.update_customer(143, **changed, original_values=original)
        query, params = self.cursor.execute.call_args.args
        self.assertIn("UPDATE customers", query)
        self.assertEqual(params[0], "احمد")

    def test_snapshot_rejects_concurrent_normalized_equivalent_spelling_change(self):
        original = dict(self.raw_values, name="أحمد", is_active=True)
        self.cursor.fetchone.return_value = dict(original, id=143, name="احمد")
        with self.assertRaises(ValueError):
            self.service.update_customer(143, **self.raw_values, original_values=original)
        self.assertEqual(self.cursor.execute.call_count, 1)
        self.assertIn("FOR UPDATE", self.cursor.execute.call_args.args[0])

    def test_create_and_update_reject_blank_name_before_opening_transaction(self):
        for name in (None, "", " \t\n "):
            for edit in (False, True):
                with self.subTest(name=name, edit=edit), self.assertRaises(ValueError):
                    values = dict(self.raw_values, name=name)
                    if edit:
                        self.service.update_customer(143, **values, original_values={})
                    else:
                        self.service.create_customer(**values)
        self.transactions.assert_not_called()

    def test_create_and_update_validate_raw_length_including_whitespace(self):
        for field, limit in (("name", 150), ("mobile", 50), ("place", 150), ("notes", 1000)):
            for edit in (False, True):
                with self.subTest(field=field, edit=edit), self.assertRaises(ValueError):
                    values = dict(self.raw_values, **{field: "x" * limit + " "})
                    if edit:
                        self.service.update_customer(143, **values, original_values={})
                    else:
                        self.service.create_customer(**values)
        self.transactions.assert_not_called()


if __name__ == "__main__":
    unittest.main()
