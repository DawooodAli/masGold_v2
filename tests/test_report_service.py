"""packet26 basic reports through the REAL ReportService over a fake repository whose fixtures have small, hand-checkable numbers (every expected figure below is computed
independently in the comment next to it). The SQL of ReportRepository is NOT exercised here (no MySQL): it is covered only by the static checks at the end."""
from contextlib import contextmanager
import csv
from datetime import date, datetime
from decimal import Decimal as D
import re
import tempfile
import unittest
from pathlib import Path

from services import report_service as rs
from services.report_service import ReportError, ReportService
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import session_for

ROOT = Path(__file__).resolve().parents[1]
ALL = frozenset({"APP_LOGIN", "REPORTS_VIEW", "INVOICE_VIEW", "INVENTORY_VIEW", "OLD_GOLD_VIEW", "CUSTOMERS_VIEW", "SUPPLIER_VIEW", "CASH_VIEW", "BANK_VIEW",
                 "MARKET_TRIP_MANAGE", "INTERMEDIARY_HOLDING_VIEW"})
TODAY = date(2026, 10, 20)


def at(day, hour=12):
    return datetime(2026, 10, day, hour)


class FakeRepo:
    """Fixtures (see each test for the arithmetic)."""
    tables = {"market_trip_book_lines": True, "marketer_gold_movements": True, "shop_assayed_stock_movements": True}
    extra_invoices = 0
    legacy = False
    recons, recon_docs, groups = [], [], None
    calls = []

    def __init__(self, connection=None):
        self.statements = 0

    def has_table(self, name):
        return self.tables.get(name, True)

    # ---- gold movement (the owner redesign): pieces sold by type and scrap bought by karat
    def gold_sold_pieces(self, start, end):
        def piece(i, kind, factory, karat, weight, day, reversal=None):
            return {"item_id": i, "item_type": kind, "category": "ذهب", "factory_id": factory, "factory": {1: "مصنع النور", 2: "مصنع الأمل", None: None}[factory], "karat_value": karat,
                    "weight_grams": D(weight), "invoice_id": 100 + i, "invoice_number": f"INV{100 + i}", "doc_date": at(day), "reversal_id": reversal}
        rows = [piece(1, "دبلة", 1, 21, "5.000", 2), piece(2, "دبلة", 1, 21, "6.500", 3), piece(3, "خاتم", 2, 21, "4.250", 4), piece(4, "غويشة", 1, 18, "10.000", 5),
                piece(5, "خاتم", 1, 18, "3.000", 6), piece(6, "دبلة", 1, 21, "9.999", 7, reversal=5), piece(7, None, None, 24, "2.000", 8)]
        return [r for r in rows if start <= r["doc_date"] < end]

    def scrap_purchases(self, start, end):
        return [{"karat_value": 21, "movement_code": "CUSTOMER_BUY_IN", "n": 3, "grams": D("30.000")}, {"karat_value": 21, "movement_code": "CUSTOMER_BUY_IN_REVERSAL", "n": 1, "grams": D("-5.000")},
                {"karat_value": 18, "movement_code": "CUSTOMER_BUY_IN", "n": 2, "grams": D("12.500")}]

    def factories(self):
        return [{"id": 2, "name": "مصنع الأمل"}, {"id": 1, "name": "مصنع النور"}]

    # ---- sales
    def sales_invoices(self, start, end):
        def inv(i, kind, day, sell, buy, money_in, money_out, account, status="FINALIZED", reversal=None, customer="عميل أ"):
            return {"id": i, "number": f"INV{i:04d}", "status": status, "origin": "NATIVE", "doc_date": at(day), "customer_id": 1, "sell_total": D(sell), "buy_total": D(buy), "net_amount": D(sell) - D(buy),
                    "net_direction": "CUSTOMER_PAYS_SHOP", "settlement_status": "SETTLED", "customer_name": customer, "kind": kind, "money_in": D(money_in), "money_out": D(money_out),
                    "account_delta": D(account), "reversal_id": reversal, "reversal_at": at(6) if reversal else None,
                    "payment_count": 1 if (D(money_in) or D(money_out)) else 0, "account_count": 1 if D(account) else 0}
        rows = [inv(1, "SALE", 2, "10000", "0", "6000", "0", "4000"), inv(2, "SALE", 5, "5000", "0", "5000", "0", "0", reversal=7, customer="عميل ب"),
                inv(3, "OLD_GOLD_PURCHASE", 7, "0", "3000", "0", "3000", "0"), inv(4, "MIXED", 8, "8000", "1000", "7000", "0", "0"),
                inv(6, "SALE", 9, "999", "0", "0", "0", "0", status="VOIDED"), inv(5, "SALE", 1, "777", "0", "777", "0", "0")]
        rows[-1]["doc_date"] = datetime(2026, 9, 30, 12)                                      # outside the October range
        rows += [inv(100 + n, "SALE", 12, "100", "0", "100", "0", "0", customer="عميل كثير") for n in range(self.extra_invoices)]
        if self.legacy:
            def old(i, kind, sell, buy, money_in, money_out, account, payments, ledger):
                r = inv(i, kind, 13, "0", "0", money_in, money_out, account)
                r.update(origin="LEGACY", sell_total=None if sell is None else D(sell), buy_total=None if buy is None else D(buy), payment_count=payments, account_count=ledger, customer_name="عميل قديم")
                return r
            rows += [old(200, "SALE", None, None, "0", "0", "0", 0, 0),                          # legacy: the split, the payments and the ledger are all unproven
                     old(201, "MIXED", "2000", "500", "1500", "0", "0", 2, 1),                  # legacy with everything proven (the account effect is a proven 0)
                     old(202, "OLD_GOLD_PURCHASE", "0", None, "0", "0", "0", 0, 0),            # legacy purchase whose amount is unknown
                     old(203, "SALE", "1000", None, "1000", "0", "0", 1, 0),                    # one side known (1000), the other NULL although the inferred kind is SALE
                     old(204, "SALE", "0", "0", "0", "0", "0", 1, 1)]                           # stored zeros: proven zeros, nothing unknown
            gone = old(205, "SALE", None, None, "0", "0", "0", 0, 0)                            # legacy, unknown split, and REVERSED
            gone.update(reversal_id=7, reversal_at=at(14))
            rows.append(gone)
        return [r for r in rows if start <= r["doc_date"] < end]

    def sales_returns(self, start, end):
        rows = [{"id": 1, "source_invoice_id": 1, "source_number": "INV0001", "doc_date": at(10), "customer_id": 1, "customer_name": "عميل أ", "total_amount": D("2000"), "gold_amount": D("1900"),
                 "making_refund": D("100"), "money_paid": D("1500"), "account_amount": D("500"), "reversal_id": None, "reversal_at": None}]
        return [r for r in rows if start <= r["doc_date"] < end]

    def sales_exchanges(self, start, end):
        rows = [{"id": 1, "source_invoice_id": 1, "source_number": "INV0001", "doc_date": at(11), "customer_id": 1, "customer_name": "عميل أ", "signed_amount": D("300"), "money_received": D("300"),
                 "money_paid": D("0"), "account_signed": D("0"), "reversal_id": None, "reversal_at": None}]
        return [r for r in rows if start <= r["doc_date"] < end]

    # ---- stock
    def stock_items(self, statuses):
        def item(i, karat, cls, status, weight, barcode, invoice=None):
            return {"item_id": i, "weight_grams": D(weight), "size": "", "stock_classification": cls, "inventory_status": status, "acquisition_source": "MARKET_PURCHASE", "acquired_at": at(1), "karat_id": karat,
                    "karat_value": {1: 21, 2: 18}[karat], "item_type": "خاتم", "category": "ذهب", "factory": "مصنع", "barcode": barcode, "invoice_id": invoice,
                    "invoice_number": f"INV{invoice:04d}" if invoice else None, "invoice_line_type": "SELL_INVENTORY" if invoice else None}
        rows = [item(1, 1, "NEW_STOCK", "IN_STOCK", "10.000", "B1001"), item(2, 1, "NEW_STOCK", "IN_STOCK", "5.500", "B1002", invoice=9), item(3, 1, "NEW_STOCK", "IN_STOCK", "4.250", "B1003"),
                item(4, 1, "OLD_STOCK", "IN_STOCK", "2.000", "B1004"), item(5, 2, "NEW_STOCK", "IN_STOCK", "3.000", "B2001"), item(6, 2, "NEW_STOCK", "IN_STOCK", "1.000", "B2002"),
                item(7, 1, "NEW_STOCK", "SOLD", "7.000", "B1007", invoice=1)]
        return [r for r in rows if r["inventory_status"] in statuses]

    # ---- gold
    def scrap_opening(self, start):
        return [{"karat_id": 1, "karat_value": 21, "grams": D("100.000")}]

    def scrap_movements(self, start, end):
        return [{"id": 1, "karat_id": 1, "karat_value": 21, "movement_code": "CUSTOMER_BUY_IN", "grams": D("20.000"), "balance_effect": "COUNTED", "reference_type": "INVOICE", "reference_id": 3,
                 "reference_code": "INV0003", "invoice_id": 3, "customer_id": 1, "occurred_at": at(7)},
                {"id": 2, "karat_id": 1, "karat_value": 21, "movement_code": "MANUFACTURE_OUT", "grams": D("-30.000"), "balance_effect": "COUNTED", "reference_type": None, "reference_id": None,
                 "reference_code": "", "invoice_id": None, "customer_id": None, "occurred_at": at(8)},
                {"id": 3, "karat_id": 1, "karat_value": 21, "movement_code": "LEGACY_NOTE", "grams": D("5.000"), "balance_effect": "REFERENCE_ONLY", "reference_type": None, "reference_id": None,
                 "reference_code": "", "invoice_id": None, "customer_id": None, "occurred_at": at(9)}]

    def piece_movements(self, start, end):
        return []

    def assayed_opening(self, start):
        return {"grams": D("1.800"), "posted": D("1.783")}

    def assayed_movements(self, start, end):
        return [{"id": 9, "item_entry_id": 2, "item_line_no": 1, "movement_type": "ISSUE", "grams_delta": D("-0.900"), "eq21_posted_delta": D("-0.891"), "source_entry_id": 5, "correction_id": None,
                 "created_at": at(5), "fineness_permille": D("866.7"), "trip_id": 3, "entry_no": 1}]

    def book_lines(self, end):
        def line(entry, trip, kind, day, direction, signed, reversal=None):
            return {"entry_id": entry, "line_no": 1, "trip_id": trip, "entry_no": entry, "entry_kind": kind, "entry_date": date(2026, 10, day) if day else date(2026, 9, 20), "occurred_at": at(max(day, 1)),
                    "direction": direction, "weight_grams": abs(D(signed)), "basis": "MASTER_KARAT", "karat_value": 21, "fineness_permille": None, "eq21_posted": abs(D(signed)),
                    "signed_eq21_posted": D(signed), "reversal_id": reversal}
        return [line(1, 1, "BK_ISSUE", 0, "IN", "20.000"), line(2, 1, "BK_RETURN", 4, "OUT", "-6.000"), line(3, 1, "BK_SELL", 6, "OUT", "-2.000", reversal=11), line(4, 1, "BK_BUY", 7, "IN", "1.500")]

    def book_groups_current(self):
        self.calls.append("book_groups_current")
        if FakeRepo.groups is not None:
            return FakeRepo.groups
        grouped = {}
        for l in self.book_lines(datetime(2100, 1, 1)):
            if l["reversal_id"] is not None:
                continue
            g = grouped.setdefault(l["trip_id"], {}).setdefault((l["direction"], l["basis"], l["karat_value"], l["fineness_permille"]), {
                "trip_id": l["trip_id"], "direction": l["direction"], "basis": l["basis"], "karat_value": l["karat_value"], "fineness_permille": l["fineness_permille"],
                "weight_grams": D("0"), "eq21_posted": D("0"), "lines": 0})
            g["weight_grams"] += l["weight_grams"]
            g["eq21_posted"] += l["eq21_posted"]
            g["lines"] += 1
        return {t: list(v.values()) for t, v in grouped.items()}

    def book_effective_reconciliations(self):
        self.calls.append("book_effective_reconciliations")
        return list(FakeRepo.recons)

    def book_recon_documents(self, start, end):
        return list(FakeRepo.recon_docs)

    def marketer_gold(self, end):
        return [{"id": 1, "party_id": 20, "party_name": "مسوقجي", "movement_type": "PURCHASE_IN", "weight_delta": D("10.000"), "doc_kind": "PURCHASE", "document_id": 1, "settlement_id": None,
                 "trip_entry_id": None, "trip_entry_kind": None, "correction_id": None, "created_at": datetime(2026, 9, 25), "trip_id": None, "trip_entry_no": None},
                {"id": 2, "party_id": 20, "party_name": "مسوقجي", "movement_type": "TRIP_BOOK_IN", "weight_delta": D("2.000"), "doc_kind": None, "document_id": None, "settlement_id": None,
                 "trip_entry_id": 5, "trip_entry_kind": "BK_DLV_MKT", "correction_id": None, "created_at": at(6), "trip_id": 1, "trip_entry_no": 5}]

    # ---- customers
    def customer_rows(self):
        return [{"id": 1, "customer_code": "C000001", "name": "أحمد", "mobile": "0100", "is_active": 1, "balance": D("1000.00")},
                {"id": 2, "customer_code": "C000002", "name": "بسمة", "mobile": "0111", "is_active": 1, "balance": D("-300.00")},
                {"id": 3, "customer_code": "C000003", "name": "جمال", "mobile": "", "is_active": 1, "balance": D("0.00")},
                {"id": 4, "customer_code": "C000004", "name": "خالد", "mobile": "", "is_active": 0, "balance": D("50.00")}]

    def customer_before(self, start):
        return [{"customer_id": 1, "total": D("400.00")}]

    def customer_period(self, start, end):
        return [{"customer_id": 1, "reference_type": "INVOICE", "n": 1, "owed_up": D("1000.00"), "owed_down": D("0")},
                {"customer_id": 1, "reference_type": "CUSTOMER_ACCOUNT_SETTLEMENT", "n": 1, "owed_up": D("0"), "owed_down": D("-300.00")},
                {"customer_id": 1, "reference_type": "RETURN", "n": 1, "owed_up": D("0"), "owed_down": D("-100.00")},
                {"customer_id": 2, "reference_type": "CUSTOMER_ACCOUNT_SETTLEMENT", "n": 1, "owed_up": D("0"), "owed_down": D("-300.00")}]

    # ---- suppliers
    def supplier_rows(self):
        return [{"id": 1, "name": "مورد ذهب", "phone": "", "is_active": 1, "is_supplier": 1, "is_intermediary": 0, "karat_id": 2, "karat_value": 18}]

    def supplier_gold_before(self, start):
        return [{"supplier_id": 1, "karat_id": 2, "grams": D("10.000")}]

    def supplier_gold_movements(self, start, end):
        return [{"id": 1, "supplier_id": 1, "karat_id": 2, "karat_value": 18, "type_code": "PURCHASE_INVOICE", "type_name": "فاتورة شراء", "grams": D("5.000"), "source_type": "INVOICE", "source_id": 1,
                 "source_reference": "SP000001", "description": "", "occurred_at": at(3)},
                {"id": 2, "supplier_id": 1, "karat_id": 2, "karat_value": 18, "type_code": "GOLD_SETTLEMENT", "type_name": "تسوية ذهب مورد", "grams": D("-3.000"), "source_type": "SG", "source_id": 1,
                 "source_reference": "SG000001", "description": "", "occurred_at": at(4)}]

    def supplier_making_before(self, start):
        return [{"supplier_id": 1, "amount": D("500.00")}]

    def supplier_making_movements(self, start, end):
        return [{"id": 1, "supplier_id": 1, "type_code": "PURCHASE_INVOICE", "type_name": "فاتورة شراء", "amount": D("200.00"), "source_type": "INVOICE", "source_id": 1, "source_reference": "SP000001",
                 "description": "", "occurred_at": at(3)},
                {"id": 2, "supplier_id": 1, "type_code": "PAYMENT", "type_name": "دفعة مصنعيات", "amount": D("-100.00"), "source_type": "SM", "source_id": 1, "source_reference": "SM000001",
                 "description": "", "occurred_at": at(5)}]

    # ---- treasury
    def cash_accounts(self):
        return [{"id": 1, "code": "MAIN_SAFE", "name": "الخزنة", "is_active": 1}]

    def bank_accounts(self):
        return [{"id": 2, "code": "BANK1", "name": "بنك", "bank_name": "بنك", "account_type": "BANK", "is_active": 1}]

    def cash_before(self, start):
        return [{"account_id": 1, "total": D("1000.00")}]

    def bank_before(self, start):
        return []

    def cash_movements(self, start, end):
        return [{"id": 1, "account_id": 1, "type_code": "INVOICE_IN", "type_name": "تحصيل فاتورة", "amount": D("500.00"), "transfer_group_id": None, "reference_type": "INVOICE", "reference_id": 1,
                 "reference_code": "INV0001", "description": "", "occurred_at": at(2)},
                {"id": 2, "account_id": 1, "type_code": "EXPENSE", "type_name": "مصروف", "amount": D("-200.00"), "transfer_group_id": None, "reference_type": None, "reference_id": None,
                 "reference_code": "", "description": "إيجار", "occurred_at": at(3)},
                {"id": 3, "account_id": 1, "type_code": "TRANSFER_OUT", "type_name": "تحويل صادر", "amount": D("-300.00"), "transfer_group_id": 9, "reference_type": None, "reference_id": None,
                 "reference_code": "FO000001", "description": "", "occurred_at": at(4)}]

    def bank_movements(self, start, end):
        return [{"id": 1, "account_id": 2, "type_code": "TRANSFER_IN", "type_name": "تحويل وارد", "amount": D("300.00"), "transfer_group_id": 9, "reference_type": None, "reference_id": None,
                 "reference_code": "FO000001", "description": "", "occurred_at": at(4)},
                {"id": 2, "account_id": 2, "type_code": "DEPOSIT", "type_name": "إيداع", "amount": D("100.00"), "transfer_group_id": None, "reference_type": None, "reference_id": None,
                 "reference_code": "", "description": "", "occurred_at": at(5)}]


def _logged(name):
    original = getattr(FakeRepo, name)

    def wrapper(self, *args, **kwargs):
        FakeRepo.calls.append(name)
        return original(self, *args, **kwargs)
    return wrapper


for _name in ("cash_accounts", "cash_before", "cash_movements", "bank_accounts", "bank_before", "bank_movements", "scrap_movements", "piece_movements", "assayed_movements",
              "marketer_gold", "book_lines", "stock_items"):
    setattr(FakeRepo, _name, _logged(_name))


class Case(unittest.TestCase):
    permissions = ALL

    def setUp(self):
        FakeRepo.extra_invoices = 0
        FakeRepo.legacy = False
        FakeRepo.recons, FakeRepo.recon_docs, FakeRepo.groups, FakeRepo.calls = [], [], None, []
        FakeRepo.tables = {"market_trip_book_lines": True, "marketer_gold_movements": True, "shop_assayed_stock_movements": True}
        self.session = session_for(self.permissions)
        self.authorization = FakeAuthorization(self.session)
        self.opened = 0

        @contextmanager
        def snapshot():
            self.opened += 1
            yield object()
        self.service = ReportService(self.session, transaction_factory=snapshot, repository=FakeRepo, authorization=self.authorization, today=lambda: TODAY)

    def totals(self, result):
        return {t["label"]: t["value"] for t in result["totals"]}

    def rows(self, result, key=None):
        return next(t for t in result["tables"] if (key is None and t["paged"]) or t["key"] == key)["rows"]


class SalesTests(Case):
    def test_effective_reversed_and_voided_documents_are_separated(self):
        result = self.service.run("sales", {"from": "2026-10-01", "to": "2026-10-31"})
        t = self.totals(result)
        self.assertEqual(t["عدد المستندات الفعّالة"], 5)                                                   # I1, I3, I4, return R1, exchange E1
        self.assertEqual(t["قيمة البيع (فعّال)"], D("18000"))                                              # 10000 (I1) + 8000 (I4); I2 reversed, I6 voided, I5 out of range
        self.assertEqual(t["قيمة شراء الكسر (فعّال)"], D("4000"))                                           # 3000 (I3) + 1000 (I4)
        self.assertEqual(t["قيمة المرتجع (فعّال)"], D("2000"))
        self.assertEqual(t["فرق قيمة البيع والمرتجع"], D("16000"))                                          # 18000 - 2000, labelled «ليس ربحًا»
        self.assertEqual(t["المحصَّل (فعّال)"], D("13300"))                                                 # 6000 + 7000 + 300 (exchange)
        self.assertEqual(t["المدفوع للعملاء (فعّال)"], D("4500"))                                           # 3000 (I3) + 1500 (return)
        self.assertEqual(t["أثر المستندات على حسابات العملاء (فعّال)"], D("3500"))                            # +4000 (I1) - 500 (return credit)
        self.assertEqual((t["المستندات المعكوسة (عددها)"], t["قيمة البيع المعكوسة"], t["الفواتير الملغاة (عددها)"]), (1, D("5000"), 1))
        self.assertNotIn("ربح", " ".join(x["label"] for x in result["totals"]).replace("ليس ربحًا", ""))   # never named profit
        self.assertTrue(any("ليس ربحًا" in x["note"] for x in result["totals"]))

    def test_the_sale_value_is_not_the_collection(self):
        t = self.totals(self.service.run("sales", {"from": "2026-10-01", "to": "2026-10-01"}))
        self.assertEqual((t["قيمة البيع (فعّال)"], t["المحصَّل (فعّال)"]), (D("0"), D("0")))             # nothing on the first day
        t = self.totals(self.service.run("sales", {"from": "2026-10-02", "to": "2026-10-02"}))
        self.assertEqual((t["قيمة البيع (فعّال)"], t["المحصَّل (فعّال)"]), (D("10000"), D("6000")))       # sold 10000, collected 6000, 4000 on the account

    def test_reversed_documents_keep_their_reference_and_date_and_are_openable(self):
        rows = self.rows(self.service.run("sales", {"from": "2026-10-01", "to": "2026-10-31"}))
        reversed_row = next(r for r in rows if r["number"] == "INV0002")
        self.assertIn("RV000007", reversed_row["state"])
        self.assertIn("2026-10-06", reversed_row["state"])
        self.assertEqual(reversed_row["ref"], {"type": "INVOICE", "id": "2"})                            # opens the existing invoice screen
        returned = next(r for r in rows if r["kind"] == "مرتجع")
        self.assertEqual((returned["number"].split(" ")[0], returned["ref"]), ("RT000001", {"type": "INVOICE", "id": "1"}))

    def test_filters_kind_state_and_arabic_text(self):
        only_returns = self.service.run("sales", {"from": "2026-10-01", "to": "2026-10-31", "kind": "RETURN"})
        self.assertEqual([r["kind"] for r in self.rows(only_returns)], ["مرتجع"])
        self.assertEqual(self.totals(only_returns)["قيمة البيع (فعّال)"], D("0"))
        reversed_only = self.service.run("sales", {"from": "2026-10-01", "to": "2026-10-31", "state": "REVERSED"})
        self.assertEqual([r["number"] for r in self.rows(reversed_only)], ["INV0002"])
        by_name = self.service.run("sales", {"from": "2026-10-01", "to": "2026-10-31", "text": "عميل ب"})
        self.assertEqual([r["number"] for r in self.rows(by_name)], ["INV0002"])

    def test_totals_cover_every_page_not_only_the_displayed_one(self):
        FakeRepo.extra_invoices = 120                                                                      # 120 more sales of 100 on 12 October
        first = self.service.run("sales", {"from": "2026-10-01", "to": "2026-10-31", "page": 1})
        third = self.service.run("sales", {"from": "2026-10-01", "to": "2026-10-31", "page": 3})
        for result in (first, third):
            self.assertEqual(self.totals(result)["قيمة البيع (فعّال)"], D("18000") + D("12000"))        # 18000 + 120 x 100, whichever page is shown
        table_ = next(t for t in first["tables"] if t["paged"])
        self.assertEqual((table_["row_count"], len(table_["rows"]), table_["has_more"]), (127, 50, True))   # 5 invoices + 120 extra + 1 return + 1 exchange (I5 is in September)
        third_table = next(t for t in third["tables"] if t["paged"])
        self.assertEqual((len(third_table["rows"]), third_table["has_more"]), (27, False))                  # 127 - 100

    def test_invalid_input_is_refused_not_ignored(self):
        for bad in ({"from": "2026-13-01"}, {"from": "2026-10-10", "to": "2026-10-01"}, {"kind": "NOPE"}, {"state": "X"}):
            with self.assertRaises(ReportError):
                self.service.run("sales", bad)


class StockTests(Case):
    """The stock report is CARDS ONLY (owner review): one card per karat with the WEIGHT on top and the piece count under it, then one row of Scrap cards (weight only, own karat)."""

    @staticmethod
    def pieces(result):
        return sum(int(x["note"].split()[0]) for x in result["totals"] if x["group"] == "pieces")

    def test_weight_on_top_and_the_count_under_it_never_added_across_karats(self):
        result = self.service.run("stock", {})
        t = self.totals(result)
        notes = {x["label"]: x["note"] for x in result["totals"]}
        self.assertEqual(self.pieces(result), 6)                                                           # 6 pieces IN_STOCK (the SOLD one is not available stock)
        self.assertEqual((t["عيار 21"], notes["عيار 21"]), ("21.750 جرام", "4 قطعة"))                      # 10.000 + 5.500 + 4.250 + 2.000
        self.assertEqual((t["عيار 18"], notes["عيار 18"]), ("4.000 جرام", "2 قطعة"))                       # 3.000 + 1.000
        self.assertNotIn("25.750", " ".join(str(v) for v in t.values()))                                    # no cross-karat weight total
        self.assertFalse(any(x["label"] == "عدد القطع" for x in result["totals"]))                          # the overall count card was removed
        self.assertEqual(t["كسر المحل — عيار 21"], "100.000 جرام")                                         # the shop Scrap: weight only, in its own karat (the fake balance)
        self.assertEqual([x["group"] for x in result["totals"]], ["pieces", "pieces", "scrap"])             # two rows: the pieces, then the Scrap

    def test_barcode_search_and_the_linked_invoice_stay_in_the_service_rows_for_the_csv(self):
        result = self.service.run("stock", {"text": "B1002"})
        row = self.rows(result, "items")[0]
        self.assertEqual((len(self.rows(result, "items")), row["barcode"], row["invoice"], row["ref"]), (1, "B1002", "INV0009 (بيع قطعة)", {"type": "INVOICE", "id": "9"}))
        plain = self.service.run("stock", {"text": "B1001"})
        self.assertEqual(self.rows(plain, "items")[0]["invoice"], "غير مرتبطة بفاتورة")
        summary = self.rows(self.service.run("stock", {}), "summary")
        self.assertEqual([(r["karat"], r["classification"], r["count"], r["weight"]) for r in summary],
                         [("عيار 21", "جديد", 3, D("19.750")), ("عيار 21", "قديم", 1, D("2.000")), ("عيار 18", "جديد", 2, D("4.000"))])

    def test_status_classification_and_karat_filters(self):
        self.assertEqual(self.pieces(self.service.run("stock", {"status": "SOLD"})), 1)
        self.assertEqual(self.pieces(self.service.run("stock", {"status": "ALL"})), 7)
        self.assertEqual(self.pieces(self.service.run("stock", {"classification": "OLD_STOCK"})), 1)
        self.assertEqual(self.pieces(self.service.run("stock", {"karat": "18"})), 2)
        with self.assertRaises(ReportError):
            self.service.run("stock", {"status": "NOPE"})

    def test_the_scrap_cards_need_the_old_gold_right_and_follow_the_karat_filter(self):
        session = session_for(ALL - {"OLD_GOLD_VIEW"})
        limited = ReportService(session, transaction_factory=self.service._transaction, repository=FakeRepo, authorization=FakeAuthorization(session), today=lambda: TODAY)
        self.assertFalse(any(x["label"].startswith("كسر المحل") for x in limited.run("stock", {})["totals"]))      # left out, never shown as zero
        self.assertFalse(any(x["label"].startswith("كسر المحل") for x in self.service.run("stock", {"karat": "18"})["totals"]))      # the fake Scrap is 21K only


class GoldMovementTests(Case):
    """«حركة الذهب» (owner redesign): the pieces SOLD in the period, ONE row per item type (karats broken down in a text column), cards per karat (weight on top of the count), a monthly
    chart per type under the same filters, and the scrap BOUGHT by karat."""
    PERIOD = {"from": "2026-10-01", "to": "2026-10-31"}

    def run_gold(self, **filters):
        return self.service.run("gold", {**self.PERIOD, **filters})

    def sales(self, result):
        """{(karat label, type): (count, weight)} over EVERY karat table of the result."""
        return {(t["title"].rsplit(" — ", 1)[1], r["type"]): (r["count"], r["weight"]) for t in result["tables"] if t["key"].startswith("sales_") for r in t["rows"]}

    def test_one_table_per_karat_with_one_row_per_type_and_a_reversed_invoice_is_not_counted(self):
        result = self.run_gold()
        sales = self.sales(result)
        self.assertEqual([t["key"] for t in result["tables"] if t["key"].startswith("sales_")], ["sales_24", "sales_21", "sales_18"])     # a table per karat, highest first
        self.assertEqual(sales[("عيار 21", "دبلة")], (2, D("11.500")))                              # 5.000 + 6.500 (the 9.999 piece belongs to a REVERSED invoice)
        self.assertEqual(sales[("عيار 21", "خاتم")], (1, D("4.250")))                               # the 18K ring is NOT added to the 21K ring
        self.assertEqual(sales[("عيار 18", "خاتم")], (1, D("3.000")))
        self.assertEqual(sales[("عيار 18", "غويشة")], (1, D("10.000")))
        self.assertEqual(sales[("عيار 24", "بدون صنف")], (1, D("2.000")))                           # a piece without a type is still shown, never dropped
        self.assertEqual(len(sales), 5)
        ring = next(r for t in result["tables"] if t["key"] == "sales_18" for r in t["rows"] if r["type"] == "خاتم")
        self.assertEqual(ring["ref"], {"type": "CHART", "id": "خاتم", "extra": "18"})               # the chart button carries the karat of its table

    def test_one_card_per_karat_with_the_weight_on_top_and_the_count_under_it(self):
        result = self.run_gold()
        cards = {x["label"]: (x["value"], x["note"], x["group"]) for x in result["totals"]}
        self.assertEqual(cards["مبيع عيار 21"], ("15.750 جرام", "3 قطعة", "pieces"))               # 11.500 + 4.250
        self.assertEqual(cards["مبيع عيار 18"], ("13.000 جرام", "2 قطعة", "pieces"))               # 10.000 + 3.000
        self.assertEqual(cards["مبيع عيار 24"], ("2.000 جرام", "1 قطعة", "pieces"))
        self.assertNotIn("30.750", " ".join(str(v[0]) for v in cards.values()))                     # no cross-karat card

    def test_every_karat_table_and_card_carries_its_karat_for_the_colors(self):
        result = self.run_gold()
        self.assertEqual({t["key"]: t["karat"] for t in result["tables"] if t["key"].startswith("sales_")}, {"sales_24": 24, "sales_21": 21, "sales_18": 18})
        self.assertEqual({x["label"]: x["karat"] for x in result["totals"]}, {"مبيع عيار 24": 24, "مبيع عيار 21": 21, "مبيع عيار 18": 18})
        stock = self.service.run("stock", {})
        self.assertEqual({x["label"]: x["karat"] for x in stock["totals"]}, {"عيار 21": 21, "عيار 18": 18, "كسر المحل — عيار 21": 21})

    def test_the_factory_weight_and_karat_filters(self):
        self.assertEqual(set(self.sales(self.run_gold(factory="2"))), {("عيار 21", "خاتم")})
        self.assertEqual(set(self.sales(self.run_gold(weight_min="5"))), {("عيار 21", "دبلة"), ("عيار 18", "غويشة")})                    # >= 5: 5.000, 6.500, 10.000
        self.assertEqual(set(self.sales(self.run_gold(weight_max="4.25"))), {("عيار 21", "خاتم"), ("عيار 18", "خاتم"), ("عيار 24", "بدون صنف")})
        self.assertEqual(set(self.sales(self.run_gold(weight_min="4", weight_max="6"))), {("عيار 21", "دبلة"), ("عيار 21", "خاتم")})
        self.assertEqual(set(self.sales(self.run_gold(karat="18"))), {("عيار 18", "غويشة"), ("عيار 18", "خاتم")})
        self.assertEqual(set(self.sales(self.run_gold(text="خاتم"))), {("عيار 21", "خاتم"), ("عيار 18", "خاتم")})

    def test_the_period_is_by_the_invoice_date_and_the_applied_filters_name_the_factory(self):
        self.assertEqual(set(self.sales(self.run_gold(**{"from": "2026-10-04", "to": "2026-10-04"}))), {("عيار 21", "خاتم")})
        result = self.run_gold(factory="1", weight_min="5")
        self.assertIn("المصنع: مصنع النور", result["applied"])
        self.assertIn("الوزن من: 5", result["applied"])

    def test_the_factory_options_come_from_the_service(self):
        options = self.run_gold()["options"]["factory"]
        self.assertEqual([o["text"] for o in options], ["الكل", "مصنع الأمل", "مصنع النور"])

    # ---- the monthly chart of one type
    def chart(self, item_type, karat=None, **filters):
        return self.service.gold_chart({**self.PERIOD, **filters}, item_type, karat)

    def test_the_chart_is_monthly_per_type_and_includes_empty_months(self):
        result = self.chart("دبلة", **{"from": "2026-08-15", "to": "2026-10-31"})
        self.assertEqual([(p["month"], p["count"], p["weight"]) for p in result["points"]],
                         [("2026-08", 0, D("0.000")), ("2026-09", 0, D("0.000")), ("2026-10", 2, D("11.500"))])           # August and September are zero bars
        self.assertEqual((result["type"], result["count"], result["weight"]), ("دبلة", 2, D("11.500")))

    def test_the_chart_follows_every_filter_of_the_report(self):
        self.assertEqual(self.chart("خاتم")["weight"], D("7.250"))                                  # no karat given: every karat of the filters
        self.assertEqual(self.chart("خاتم", "18")["weight"], D("3.000"))                            # the chart of a row of the 18K table
        self.assertEqual(self.chart("خاتم", "21")["weight"], D("4.250"))                            # the chart of a row of the 21K table
        self.assertEqual(self.chart("خاتم", factory="2")["weight"], D("4.250"))
        self.assertEqual(self.chart("خاتم", weight_min="4")["weight"], D("4.250"))
        self.assertEqual(self.chart("خاتم", **{"from": "2026-10-05", "to": "2026-10-31"})["weight"], D("3.000"))             # the date range too
        self.assertEqual(self.chart("دبلة", text="خاتم")["count"], 0)                                                        # the search too
        self.assertEqual(self.chart("بدون صنف")["weight"], D("2.000"))

    def test_the_chart_totals_equal_the_table_row(self):
        row = next(r for r in self.rows(self.run_gold(), "sales_21") if r["type"] == "خاتم")
        chart = self.chart("خاتم", "21")
        self.assertEqual((chart["count"], chart["weight"]), (row["count"], row["weight"]))

    def test_the_chart_needs_the_invoice_and_inventory_rights_and_validates_the_filters(self):
        session = session_for(ALL - {"INVENTORY_VIEW"})
        limited = ReportService(session, transaction_factory=self.service._transaction, repository=FakeRepo, authorization=FakeAuthorization(session), today=lambda: TODAY)
        with self.assertRaises(PermissionError):
            limited.gold_chart(self.PERIOD, "دبلة")
        with self.assertRaises(ReportError):
            self.chart("دبلة", "19")

    def test_the_scrap_purchases_are_net_per_karat_with_the_purchase_count(self):
        rows = {r["karat"]: (r["count"], r["weight"]) for r in self.rows(self.run_gold(), "scrap")}
        self.assertEqual(rows["عيار 21"], (3, D("25.000")))                                         # 30.000 bought - 5.000 reversed; 3 purchases
        self.assertEqual(rows["عيار 18"], (2, D("12.500")))
        self.assertEqual(set(r["karat"] for r in self.rows(self.run_gold(karat="18"), "scrap")), {"عيار 18"})

    def test_invalid_filters_are_refused_not_ignored(self):
        for bad in ({"karat": "19"}, {"weight_min": "abc"}, {"weight_min": "-1"}, {"weight_min": "9", "weight_max": "1"}):
            with self.assertRaises(ReportError, msg=str(bad)):
                self.run_gold(**bad)

    def test_a_missing_right_marks_its_section_unavailable_and_never_reads_it(self):
        only_scrap = ALL - {"INVOICE_VIEW", "INVENTORY_VIEW"}
        FakeRepo.calls = []
        session = session_for(only_scrap)
        service = ReportService(session, transaction_factory=self.service._transaction, repository=FakeRepo, authorization=FakeAuthorization(session), today=lambda: TODAY)
        result = service.run("gold", self.PERIOD)
        by_key = {t["key"]: t for t in result["tables"]}
        self.assertFalse(by_key["sales"]["available"])
        self.assertTrue(by_key["scrap"].get("available", True))
        only_sales = ALL - {"OLD_GOLD_VIEW"}
        session = session_for(only_sales)
        service = ReportService(session, transaction_factory=self.service._transaction, repository=FakeRepo, authorization=FakeAuthorization(session), today=lambda: TODAY)
        self.assertFalse({t["key"]: t for t in service.run("gold", self.PERIOD)["tables"]}["scrap"]["available"])
        nothing = session_for({"APP_LOGIN", "REPORTS_VIEW"})
        with self.assertRaises(PermissionError):
            ReportService(nothing, transaction_factory=self.service._transaction, repository=FakeRepo, authorization=FakeAuthorization(nothing), today=lambda: TODAY).run("gold", self.PERIOD)

    def test_the_csv_has_both_tables(self):
        with tempfile.TemporaryDirectory() as folder:
            text = Path(self.service.export_csv("gold", self.PERIOD, folder)).read_text(encoding="utf-8-sig")
        for needle in ("حركة البيع حسب الصنف", "شراء الكسر بالعيار", "دبلة", "غويشة", "عدد عمليات الشراء"):
            self.assertIn(needle, text)


class CustomerTests(Case):
    def test_balances_and_period_movements_are_separated(self):
        result = self.service.run("customers", {"from": "2026-10-01", "to": "2026-10-31"})
        t = self.totals(result)
        self.assertEqual(t["إجمالي المستحق على العملاء (الأرصدة الحالية الموجبة)"], D("1000.00"))         # أحمد (the inactive customer is not listed by default)
        self.assertEqual(t["إجمالي المستحق للعملاء (الأرصدة الحالية السالبة)"], D("300.00"))
        self.assertEqual((t["مستحق جديد في الفترة"], t["تحصيل وعربون في الفترة"], t["مرتجع واستبدال في الفترة"]), (D("1000.00"), D("600.00"), D("100.00")))   # 300 + 300 collected
        first = self.rows(result)[0]
        self.assertEqual((first["name"], first["opening"], first["up"], first["collections"], first["returns"], first["closing"]), ("أحمد", D("400.00"), D("1000.00"), D("300.00"), D("100.00"), D("1000.00")))   # 400 + 1000 - 300 - 100
        self.assertEqual(first["ref"], {"type": "CUSTOMER", "id": "1"})

    def test_state_filter_inactive_and_search(self):
        credit = self.service.run("customers", {"balance": "CREDIT"})
        self.assertEqual([r["name"] for r in self.rows(credit)], ["بسمة"])
        self.assertEqual(len(self.rows(self.service.run("customers", {"inactive": "1"}))), 4)
        self.assertEqual([r["name"] for r in self.rows(self.service.run("customers", {"text": "0111"}))], ["بسمة"])


class SupplierTests(Case):
    def test_gold_stays_in_the_suppliers_own_karat_and_making_is_separate(self):
        result = self.service.run("suppliers", {"from": "2026-10-01", "to": "2026-10-31"})
        gold = self.rows(result, "gold")[0]
        self.assertEqual((gold["karat"], gold["opening"], gold["up"], gold["down"], gold["closing"]), ("عيار 18", D("10.000"), D("5.000"), D("3.000"), D("12.000")))    # 10 + 5 - 3
        making = self.rows(result, "making")[0]
        self.assertEqual((making["opening"], making["up"], making["down"], making["closing"]), (D("500.00"), D("200.00"), D("100.00"), D("600.00")))
        movements = self.rows(result, "movements")
        self.assertEqual(len(movements), 4)
        self.assertEqual({m["source"] for m in movements}, {"SP000001", "SG000001", "SM000001"})
        t = self.totals(result)
        self.assertEqual(t["عيار 18: صافي حساب الذهب (جرام، علينا +)"], D("12.000"))
        self.assertFalse(any("21" in label for label in t))                                                # never converted to 21K


class TreasuryTests(Case):
    def test_opening_period_closing_and_internal_transfers(self):
        result = self.service.run("treasury", {"from": "2026-10-01", "to": "2026-10-31"})
        cash, bank = self.rows(result, "accounts")
        self.assertEqual((cash["opening"], cash["in"], cash["out"], cash["t_in"], cash["t_out"], cash["closing"]), (D("1000.00"), D("500.00"), D("200.00"), D("0.00"), D("300.00"), D("1000.00")))    # 1000 + 500 - 200 - 300
        self.assertEqual((bank["opening"], bank["in"], bank["t_in"], bank["closing"]), (D("0.00"), D("100.00"), D("300.00"), D("400.00")))
        t = self.totals(result)
        self.assertEqual(t["صافي التحويلات الداخلية لكل الحسابات (يجب أن يساوي صفرًا)"], D("0.00"))   # -300 + 300: a transfer is neither income nor expense
        moves = self.rows(result, "movements")
        self.assertEqual(len(moves), 5)
        self.assertTrue(any("تحويل داخلي" in m["type"] for m in moves))
        self.assertEqual(next(m for m in moves if m["reference"] == "INV0001")["ref"], {"type": "INVOICE", "id": "1"})

    def test_account_filter_and_rights_per_section(self):
        only = self.service.run("treasury", {"account": "BANK:2"})
        self.assertEqual([r["account"] for r in self.rows(only, "accounts")], ["بنكي / محفظة: بنك"])
        limited = session_for({"APP_LOGIN", "REPORTS_VIEW", "CASH_VIEW"})
        cash_only = ReportService(limited, transaction_factory=self.service._transaction, repository=FakeRepo, authorization=FakeAuthorization(limited), today=lambda: TODAY)
        self.assertEqual(len(self.rows(cash_only.run("treasury", {}), "accounts")), 1)


class SafetyTests(Case):
    def test_permissions_are_rechecked_per_report(self):
        for removed, report in (("REPORTS_VIEW", "sales"), ("INVOICE_VIEW", "sales"), ("INVENTORY_VIEW", "stock"), ("CUSTOMERS_VIEW", "customers"), ("SUPPLIER_VIEW", "suppliers")):
            session = session_for(ALL - {removed})
            service = ReportService(session, transaction_factory=self.service._transaction, repository=FakeRepo, authorization=FakeAuthorization(session), today=lambda: TODAY)
            with self.assertRaises(PermissionError, msg=f"{report} without {removed}"):
                service.run(report, {})
        no_money = session_for({"APP_LOGIN", "REPORTS_VIEW"})
        with self.assertRaises(PermissionError):
            ReportService(no_money, transaction_factory=self.service._transaction, repository=FakeRepo, authorization=FakeAuthorization(no_money), today=lambda: TODAY).run("treasury", {})

    def test_a_failing_read_is_an_error_never_an_empty_report(self):
        class Broken(FakeRepo):
            def sales_returns(self, start, end):
                raise RuntimeError("read failed")
        service = ReportService(self.session, transaction_factory=self.service._transaction, repository=Broken, authorization=self.authorization, today=lambda: TODAY)
        with self.assertRaises(RuntimeError):
            service.run("sales", {})

    def test_an_oversized_result_is_refused_not_truncated(self):
        FakeRepo.extra_invoices = 30
        old = rs.MAX_ROWS
        rs.MAX_ROWS = 10
        try:
            with self.assertRaises(ReportError) as raised:
                self.service.run("sales", {"from": "2026-10-01", "to": "2026-10-31"})
            self.assertEqual(raised.exception.code, "TOO_MANY_ROWS")
        finally:
            rs.MAX_ROWS = old

    def test_the_session_is_checked_at_the_end(self):
        class Logout(FakeRepo):
            session = None

            def sales_returns(self, start, end):
                Logout.session.logout()
                return []
        Logout.session = self.session
        service = ReportService(self.session, transaction_factory=self.service._transaction, repository=Logout, authorization=self.authorization, today=lambda: TODAY)
        with self.assertRaises(PermissionError):
            service.run("sales", {})

    def test_available_reports_follow_the_rights(self):
        self.assertEqual([r["key"] for r in self.service.available_reports()], ["sales", "stock", "gold", "customers", "suppliers", "treasury", "market"])
        self.session = session_for({"APP_LOGIN", "REPORTS_VIEW", "CUSTOMERS_VIEW"})
        limited = ReportService(self.session, repository=FakeRepo, authorization=FakeAuthorization(self.session))
        self.assertEqual([r["key"] for r in limited.available_reports()], ["customers"])

    def test_the_reads_are_one_snapshot_per_report(self):
        self.service.run("sales", {})
        self.assertEqual(self.opened, 1)


class ExportTests(Case):
    def test_csv_exports_every_row_not_the_page_and_is_excel_readable(self):
        FakeRepo.extra_invoices = 120
        with tempfile.TemporaryDirectory() as folder:
            path = self.service.export_csv("sales", {"from": "2026-10-01", "to": "2026-10-31"}, folder)
            raw = Path(path).read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))                                               # UTF-8 BOM for Excel
            rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
        numbers = [r[2] for r in rows if len(r) > 2 and r[2].startswith("INV")]
        self.assertEqual(len(numbers), 125)                                                                 # 5 + 120 invoices: more than one page of detail
        value = next(r for r in rows if r and r[0] == "قيمة البيع")
        self.assertEqual(D(value[1]), D("30000"))                                                           # 10000 + 8000 + 120 x 100
        self.assertIn("المستندات", [r[0] for r in rows if r])


class SearchConsistencyTests(Case):
    """F2: every visible filter has a known effect; the search narrows the MOVEMENTS and their totals, never an account balance."""

    def test_treasury_search_filters_the_movements_and_leaves_every_balance_alone(self):
        everything = self.service.run("treasury", {"from": "2026-10-01", "to": "2026-10-31"})
        rent = self.service.run("treasury", {"from": "2026-10-01", "to": "2026-10-31", "text": "إيجار"})
        self.assertEqual([m["description"] for m in self.rows(rent, "movements")], ["إيجار"])
        self.assertEqual(self.rows(rent, "accounts"), self.rows(everything, "accounts"))          # the real balances are complete
        t = self.totals(rent)
        self.assertEqual((t["عدد الحركات المطابقة للبحث"], t["الحركات المطابقة للبحث — خارج (بلا التحويلات)"], t["الحركات المطابقة للبحث — داخل (بلا التحويلات)"]), (1, D("200.00"), D("0.00")))
        self.assertEqual((t["رصيد آخر الفترة — النقدي (كل حركات الفترة)"], t["رصيد آخر الفترة — البنكي والمحافظ (كل حركات الفترة)"]), (D("1000.00"), D("400.00")))
        self.assertEqual(t["صافي التحويلات الداخلية لكل الحسابات (يجب أن يساوي صفرًا)"], D("0.00"))   # the all-accounts proof is not narrowed by the search
        nothing = self.service.run("treasury", {"from": "2026-10-01", "to": "2026-10-31", "text": "لا يوجد"})
        self.assertEqual((self.rows(nothing, "movements"), self.rows(nothing, "accounts")), ([], self.rows(everything, "accounts")))

    def test_an_internal_transfer_stays_apart_and_is_not_income_or_expense(self):
        result = self.service.run("treasury", {"from": "2026-10-01", "to": "2026-10-31", "text": "FO000001"})
        t = self.totals(result)
        self.assertEqual(len(self.rows(result, "movements")), 2)                                  # both legs of the transfer
        self.assertEqual((t["الحركات المطابقة للبحث — داخل (بلا التحويلات)"], t["الحركات المطابقة للبحث — خارج (بلا التحويلات)"], t["الحركات المطابقة للبحث — صافي التحويلات الداخلية"]),
                         (D("0.00"), D("0.00"), D("0.00")))

    def test_the_csv_carries_the_applied_filters_and_exactly_the_matching_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.service.export_csv("treasury", {"from": "2026-10-01", "to": "2026-10-31", "text": "إيجار"}, folder)
            rows = list(csv.reader(Path(path).read_text(encoding="utf-8-sig").splitlines()))
        flat = [r[0] for r in rows if r]
        self.assertIn("البحث: إيجار", flat)
        self.assertIn("الفترة: من 2026-10-01 إلى 2026-10-31", flat)
        k = next(i for i, r in enumerate(rows) if r == ["الحركات"])
        movements = [r for r in rows[k + 2:] if r]                                                  # the header is rows[k + 1]
        movements = movements[:1]
        self.assertEqual([r[5] for r in movements], ["إيجار"])                                      # one matching row, nothing else
        self.assertTrue(any("لا يغيّرها البحث" in line for line in flat))                         # the balances' meaning is stated in the file too

    def test_the_applied_filters_match_on_screen_and_in_the_file(self):
        result = self.service.run("sales", {"from": "2026-10-01", "to": "2026-10-31", "kind": "SALE", "text": "عميل"})
        self.assertEqual(result["applied"], ["الفترة: من 2026-10-01 إلى 2026-10-31", "البحث: عميل", "النوع: SALE"])


class CurrentRightsTests(Case):
    """F3: the sections are decided from the CURRENT database rights (the authorization answer), never from the older ActorContext of the session."""

    def service_with(self, granted):
        authorization = FakeAuthorization(self.session, permissions=frozenset(granted))
        return ReportService(self.session, transaction_factory=self.service._transaction, repository=FakeRepo, authorization=authorization, today=lambda: TODAY)

    def test_a_revoked_bank_right_hides_that_section_and_its_data_is_never_read(self):
        service = self.service_with(ALL - {"BANK_VIEW"})                                           # the session still holds BANK_VIEW
        result = service.run("treasury", {"from": "2026-10-01", "to": "2026-10-31"})
        self.assertEqual([r["account"] for r in self.rows(result, "accounts")], ["نقدي: الخزنة"])
        self.assertNotIn("bank_movements", FakeRepo.calls)
        self.assertNotIn("bank_accounts", FakeRepo.calls)
        self.assertTrue(any(not t.get("available", True) for t in result["tables"]))              # shown as unavailable, never as a known zero
        self.assertFalse(any("البنكي" in x["label"] for x in result["totals"]))
        self.assertNotIn("صافي التحويلات الداخلية لكل الحسابات (يجب أن يساوي صفرًا)", [x["label"] for x in result["totals"]])

    def test_losing_every_treasury_right_is_refused_and_nothing_is_read(self):
        service = self.service_with(ALL - {"CASH_VIEW", "BANK_VIEW"})
        with self.assertRaises(PermissionError):
            service.run("treasury", {})
        with self.assertRaises(PermissionError):
            service.export_csv("treasury", {})
        self.assertEqual(FakeRepo.calls, [])

    def test_an_allowed_section_survives_when_another_is_revoked(self):
        service = self.service_with(ALL - {"CASH_VIEW"})
        result = service.run("treasury", {"from": "2026-10-01", "to": "2026-10-31"})
        self.assertEqual([r["account"] for r in self.rows(result, "accounts")], ["بنكي / محفظة: بنك"])
        self.assertNotIn("cash_movements", FakeRepo.calls)

    def test_a_right_granted_after_login_is_honoured_and_the_base_right_is_rechecked(self):
        self.session = session_for({"APP_LOGIN"})                                                  # an old context with nothing
        service = ReportService(self.session, transaction_factory=self.service._transaction, repository=FakeRepo,
                                authorization=FakeAuthorization(self.session, permissions=ALL), today=lambda: TODAY)
        self.assertGreater(len(self.rows(service.run("sales", {"from": "2026-10-01", "to": "2026-10-31"}))), 0)
        with self.assertRaises(PermissionError):
            self.service_with(ALL - {"REPORTS_VIEW"}).run("sales", {})


class LegacyKnowledgeTests(Case):
    """F4: NULL legacy facts stay unknown; totals say when they are partial; nothing is inferred from the net amount."""

    def run_sales(self, **kw):
        return self.service.run("sales", {"from": "2026-10-01", "to": "2026-10-31", **kw})

    def test_unknown_legacy_values_are_not_zeros_and_the_totals_are_partial(self):
        FakeRepo.legacy = True
        result = self.run_sales()
        rows = {r["number"]: r for r in self.rows(result)}
        unknown = rows["INV0200"]
        self.assertEqual((unknown["sales"], unknown["oldgold"], unknown["in"], unknown["out"], unknown["account"]), ("غير معلوم",) * 5)   # kind=SALE is only inferred: no proven zero
        t = self.totals(result)
        self.assertEqual(t["قيمة البيع (فعّال) — جزئي"], D("21000.00"))                              # 10000 + 8000 native + 2000 (0201) + 1000 (0203) + 0 (0204); INV0200 is NOT added as 0
        self.assertEqual(t["قيمة شراء الكسر (فعّال) — جزئي"], D("4500.00"))                          # 4000 native + 500 (0201) + 0 (0204); 0200, 0202 and 0203 are unknown
        self.assertEqual(t["مستندات قديمة ناقصة المعرفة (عددها)"], 3)                               # INV0200, INV0202, INV0203 (0201 and 0204 are fully proven)
        notes = {x["label"]: x["note"] for x in result["totals"]}
        self.assertIn("يستثني 1 مستندًا قديمًا غير معلوم القيمة", notes["قيمة البيع (فعّال) — جزئي"])
        self.assertIn("يستثني 3 مستندًا قديمًا غير معلوم القيمة", notes["قيمة شراء الكسر (فعّال) — جزئي"])
        self.assertEqual(t["قيمة البيع المعكوسة — جزئي"], D("5000.00"))                              # the reversed total excludes the unknown reversed legacy sale (INV0205) and says so
        self.assertIn("يستثني 1 مستندًا قديمًا معكوسًا", notes["قيمة البيع المعكوسة — جزئي"])

    def test_a_proven_zero_is_a_zero_and_a_fully_known_legacy_invoice_is_complete(self):
        FakeRepo.legacy = True
        rows = {r["number"]: r for r in self.rows(self.run_sales())}
        proven = rows["INV0201"]
        self.assertEqual((proven["sales"], proven["oldgold"], proven["in"], proven["out"], proven["account"]), (D("2000"), D("500"), D("1500"), D("0"), D("0")))   # the account effect is a proven 0
        self.assertNotIn("غير معلوم", proven["state"])
        zero_sale = rows["INV0202"]
        self.assertEqual((zero_sale["sales"], zero_sale["oldgold"]), (D("0"), "غير معلوم"))         # a STORED 0 sale is proven; the unknown purchase amount stays unknown
        stored = rows["INV0204"]
        self.assertEqual((stored["sales"], stored["oldgold"], stored["in"], stored["account"]), (D("0"), D("0"), D("0"), D("0")))   # stored zeros stay zeros
        one_side = rows["INV0203"]
        self.assertEqual((one_side["sales"], one_side["oldgold"]), (D("1000"), "غير معلوم"))       # the known side is shown, the NULL side is not turned into 0 by the inferred kind

    def test_a_clean_period_has_no_partial_marks(self):
        t = self.totals(self.run_sales())
        self.assertIn("قيمة البيع (فعّال)", t)                                                      # no legacy rows: the label is not marked partial
        self.assertEqual(t["مستندات قديمة ناقصة المعرفة (عددها)"], 0)

    def test_the_net_amount_is_never_used_to_fill_an_unknown_split(self):
        FakeRepo.legacy = True
        unknown = next(r for r in self.rows(self.run_sales()) if r["number"] == "INV0200")
        self.assertEqual(unknown["sales"], "غير معلوم")                                             # net_amount of the fixture is 0 - 0; nothing is derived from it either way

    def test_the_csv_says_unknown_in_the_same_words_and_the_same_limits(self):
        FakeRepo.legacy = True
        with tempfile.TemporaryDirectory() as folder:
            path = self.service.export_csv("sales", {"from": "2026-10-01", "to": "2026-10-31"}, folder)
            text = Path(path).read_text(encoding="utf-8-sig")
        self.assertIn("غير معلوم", text)
        self.assertIn("جزئي", text)
        self.assertIn("يستثني 1 مستندًا قديمًا غير معلوم القيمة", text)
        self.assertIn("قيمة البيع المعكوسة — جزئي", text)

    def test_native_invoices_keep_their_original_rules(self):
        FakeRepo.legacy = True
        t = self.totals(self.run_sales())
        self.assertEqual(t["المحصَّل (فعّال) — جزئي"], D("15800.00"))                               # 6000 + 7000 + 300 + 1500 (0201) + 1000 (0203) + 0 (0204); 0200 / 0202 have no payment evidence
        self.assertEqual(t["قيمة المرتجع (فعّال)"], D("2000"))                                       # the return and its sign are unchanged


class StaticSqlTests(unittest.TestCase):
    def test_the_report_repository_only_reads(self):
        text = (ROOT / "repositories" / "report_repository.py").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"(?i)\bFOR\s+(UPDATE|SHARE)\b|\bLOCK IN SHARE MODE\b|^\s*(INSERT|UPDATE|DELETE)\b|\"\"\"\s*(INSERT|UPDATE|DELETE)\b|\b_execute\b")
        self.assertNotRegex(text, r"(?i)\bLIMIT\b\s+%s")                                                   # no hidden truncation: the service refuses oversized results

    def test_every_repository_method_the_service_calls_really_exists(self):
        """The fake repository of these tests cannot prove the real one has the methods the service uses: this static check does (a method missing in production would only fail there)."""
        from repositories.report_repository import ReportRepository
        service = (ROOT / "services" / "report_service.py").read_text(encoding="utf-8")
        called = set(re.findall(r"repo\.(\w+)\(", service))
        called |= {"cash_accounts", "cash_before", "cash_movements", "bank_accounts", "bank_before", "bank_movements"}      # reached through getattr(repo, <name>) in the treasury report
        self.assertEqual([name for name in sorted(called) if not hasattr(ReportRepository, name)], [])
        for name in ("sales_invoices", "book_groups_current", "book_effective_reconciliations", "book_recon_documents"):
            self.assertTrue(hasattr(ReportRepository, name), name)

    def test_the_sales_query_returns_the_evidence_counts_the_service_reads(self):
        text = (ROOT / "repositories" / "report_repository.py").read_text(encoding="utf-8")
        for needed in ("AS payment_count", "AS account_count", "i.record_origin AS origin", "i.sell_total", "i.buy_total"):
            self.assertIn(needed, text)

    def test_every_method_is_a_fixed_number_of_statements(self):
        text = (ROOT / "repositories" / "report_repository.py").read_text(encoding="utf-8")
        for match in re.finditer(r"    def (\w+)\(self[^)]*\):(.*?)(?=\n    def |\Z)", text, re.S):
            name, body = match.group(1), match.group(2)
            self.assertLessEqual(body.count("self._fetch_"), 3 if name in ("marketer_gold", "book_report_rows", "book_recon_documents") else 1, name)   # fixed statements per method (a capability probe + the read); never a loop


if __name__ == "__main__":
    unittest.main()
