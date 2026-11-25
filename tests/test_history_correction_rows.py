"""Invoice History: a settlement correction shows ONLY the new split, one line per part (owner 2026-11-25); a reversal shows no narrative. No database."""
from datetime import datetime
import unittest

from tests import test_invoice_history as fixture


def correction(kind, *, source="INVOICE", after=None, code="FC000083"):
    return {"code": code, "correction_type": kind, "source_type": source, "source_reference": "26112520", "reason": "خطأ", "actor": "admin",
            "occurred_at": datetime(2026, 11, 25, 20, 18), "before": {}, "after": after or {}, "posted": [{"ledger": "CASH", "account_name": None, "amount": "-29400.00"}]}


class CorrectionRowTests(unittest.TestCase):
    def setUp(self):
        self.vm = fixture.InvoiceHistoryViewModel(fixture.AppSession())
        self.addCleanup(self.vm.shutdown)

    def rows(self, *corrections):
        self.vm._detail = {"corrections": list(corrections)}
        return self.vm.corrections

    def test_a_settlement_correction_lists_the_new_split_one_part_per_line(self):
        after = {"direction": "IN", "legs": [{"method_code": "PHYSICAL_CASH", "amount": "20000.00"}, {"method_code": "INSTAPAY", "amount": "5000.00"}], "account": "5000.00"}
        row = self.rows(correction("SETTLEMENT_CORRECTION", after=after))[0]
        self.assertEqual(row["title"], "تصحيح التسوية FC000083")
        self.assertEqual(len(row["lines"]), 3)
        self.assertIn("نقدي", row["lines"][0])
        self.assertIn("20,000", row["lines"][0])
        self.assertTrue(row["lines"][1].startswith("إنستاباي"))
        self.assertTrue(row["lines"][2].startswith("على العميل"))
        self.assertEqual(row["linesTitle"], "المستلم من العميل")
        self.assertEqual(row["reason"], "السبب: خطأ")

    def test_no_before_after_or_postings_narrative_remains_in_the_row(self):
        after = {"direction": "IN", "legs": [{"method_code": "PHYSICAL_CASH", "amount": "4000.00"}], "account": "29400.00"}
        row = self.rows(correction("SETTLEMENT_CORRECTION", after=after))[0]
        for gone in ("before", "after", "posted", "detail"):
            self.assertNotIn(gone, row)

    def test_money_paid_to_the_customer_says_so_and_an_account_part_is_for_the_customer(self):
        after = {"direction": "OUT", "legs": [{"method_code": "PHYSICAL_CASH", "amount": "1000.00"}], "account": "500.00"}
        row = self.rows(correction("SETTLEMENT_CORRECTION", source="RETURN", after=after))[0]
        self.assertEqual(row["linesTitle"], "المدفوع للعميل")
        self.assertTrue(row["lines"][-1].startswith("للعميل"))

    def test_a_reversal_has_no_lines(self):
        row = self.rows(correction("FULL_REVERSAL", code="RV000084"))[0]
        self.assertEqual((row["title"], row["lines"], row["isReversal"]), ("تم عكس العملية — RV000084", [], True))


if __name__ == "__main__":
    unittest.main()


class CustomerChangePreviewTests(unittest.TestCase):
    """The change-customer dialog reads short lines, never a story and never an arrow glyph the font lacks."""
    FACTS = {"invoice_number": "26112520", "old": {"id": "1", "code": "C000001", "name": "اسلم بدران"}, "new": {"id": "2", "code": "C000002", "name": "أسلم بدران"},
             "account_effect": "29400.00", "old_balance": "29400.00", "old_balance_after": "0.00", "new_balance": "-500.00", "new_balance_after": "28900.00"}

    def test_an_invoice_with_an_account_entry_shows_both_accounts_now_and_after(self):
        view = fixture.InvoiceHistoryViewModel._customer_view(self.FACTS)
        values = {line["label"]: line["value"] for line in view["lines"]}
        self.assertTrue(view["ok"] and view["movesAccount"])
        self.assertEqual(values["العميل الحالي"], "اسلم بدران")
        self.assertEqual(values["العميل الجديد"], "أسلم بدران (C000002)")
        self.assertIn("29,400 ج على العميل", values["حساب هذه الفاتورة"])
        self.assertEqual(values["حساب العميل الحالي"], "الآن 29,400 ج على العميل • بعد الحساب متوازن")
        self.assertEqual(values["حساب العميل الجديد"], "الآن 500 ج للعميل • بعد 28,900 ج على العميل")
        self.assertNotIn("←", "".join(values.values()))

    def test_an_invoice_without_an_account_entry_shows_only_the_two_customers(self):
        view = fixture.InvoiceHistoryViewModel._customer_view({**self.FACTS, "account_effect": "0.00"})
        self.assertEqual([line["label"] for line in view["lines"]], ["العميل الحالي", "العميل الجديد"])
        self.assertFalse(view["movesAccount"])

    def test_a_walk_in_invoice_says_there_was_no_customer(self):
        view = fixture.InvoiceHistoryViewModel._customer_view({**self.FACTS, "account_effect": "0.00", "old": {"id": None, "code": None, "name": None}, "old_balance": None, "old_balance_after": None})
        self.assertEqual(view["lines"][0]["value"], "بدون عميل")
