"""22K in Billing V2: inventory sale, customer old-gold purchase, mixed, F7 from counted Scrap, missing price, historical rounding.
In-memory store from tests.test_invoice_service (karat id 4 = 22K, priced as the derivation of the 24K quote); never a live database."""
import copy
from decimal import Decimal, ROUND_HALF_UP
import unittest
from unittest import mock

from services.invoice_service import (
    OLD_GOLD_STOCK, DescribedSaleInput, InvoiceDraft, InvoiceError, PaymentInput)
from tests import test_invoice_service as base
from tests.test_invoice_service import InvoiceServiceTestCase, cash, disc, old, sale

D = Decimal
K22 = 4                                           # karat id of 22K in this store
P22 = (D("6501.9167"), D("6528.5000"))           # 24K 7093 / 7122 * 22 / 24, 4 dp, ROUND_HALF_UP (GoldPriceService.derive_22k)
STONE = {"inventory_item_id": "110", "karat_id": K22, "karat_value": 22, "weight_grams": D("10.000"), "size": None,
         "stock_classification": "NEW_STOCK", "inventory_status": "IN_STOCK", "item_type_name": "خاتم",
         "factory_name": None, "category_code": "A", "barcode_value": "04260925"}


def money(value):
    return value.quantize(D("0.01"), ROUND_HALF_UP)


def egp5(value):
    return ((value / 5).quantize(D("1"), ROUND_HALF_UP) * 5).quantize(D("0.01"))


class Karat22Case(InvoiceServiceTestCase):
    def setUp(self):
        for patcher in (mock.patch.dict(base.KARATS, {K22: 22}), mock.patch.dict(base.PRICES, {K22: P22})):
            patcher.start()
            self.addCleanup(patcher.stop)
        super().setUp()
        self.store.items["110"] = copy.deepcopy(STONE)
        self.store.old_gold_opening[K22] = D("100.000")


class Karat22SaleAndPurchaseTests(Karat22Case):
    def test_sale_of_a_22k_piece_uses_the_derived_sell_price_and_the_entered_total_is_authoritative(self):
        gold = money(D("10.000") * P22[1])                                     # 65,285.00
        result = self.finalize(InvoiceDraft(5, "12", (sale("110"),), (), (cash("67000"),)))
        self.assertEqual(gold, D("65285.00"))
        self.assertEqual((result["sale_total"], result["average_making_per_gram"]), ("67000.00", "171.50"))
        line = [v for kind, v in self.store.lines if kind == "SALE"][0]
        self.assertEqual((line["item"]["karat_id"], line["gold_price_per_gram"]), (K22, P22[1]))
        self.assertEqual(sum(l["making_total"] for kind, l in self.store.lines if kind == "SALE"), D("1715.00"))
        self.assertEqual(self.store.items["110"]["inventory_status"], "SOLD")
        self.assertEqual([p["amount"] for p in self.store.payments], [D("67000.00")])
        snapshot = [p for p in self.store.price_snapshots if p["karat_id"] == K22]
        self.assertEqual(len(snapshot), 1)                                     # the snapshot keeps the quote id of the 22K quote
        self.assertEqual((snapshot[0]["quote_id"], snapshot[0]["shop_sell_price_per_gram"]), (50 + K22, P22[1]))

    def test_a_22k_sale_below_the_gold_value_is_refused_like_any_other_karat(self):
        before = copy.deepcopy(self.store.tables())
        with self.assertRaises(InvoiceError) as context:
            self.finalize(InvoiceDraft(5, "12", (sale("110"),), (), (cash("65000"),)))      # gold alone is 65,285.00
        self.assertEqual(context.exception.code, "SALE_BELOW_GOLD")
        self.assertEqual(self.store.tables(), before)

    def test_old_gold_purchase_22k_uses_the_derived_buy_price_and_rounds_once_to_egp5(self):
        gross = money(D("10") * P22[0])                                        # 65,019.17
        discount = money(gross * D("1.50") / 100)
        net = gross - discount
        final = egp5(net)
        self.assertEqual((gross, discount, net, final), (D("65019.17"), D("975.29"), D("64043.88"), D("64045.00")))
        result = self.finalize(InvoiceDraft(5, "12", (), (old(K22, "10", **disc()),), (cash("64045", direction="OUT"),)))
        self.assertEqual((result["invoice_type"], result["net_direction"], result["settlement_status"]),
                         ("BUY", "SHOP_PAYS_CUSTOMER", "SETTLED"))
        movement = self.store.old_gold[0]
        self.assertEqual((movement["karat_id"], movement["gold_price_per_gram"], movement["cash_value"], movement["price_quote_id"]),
                         (K22, P22[0], net, 50 + K22))
        self.assertEqual(movement["signed_weight"], D("10.000"))               # physical grams, no Eq21 conversion
        self.assertEqual([(p["direction"], p["amount"]) for p in self.store.payments], [("OUT", final)])
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [-final])

    def test_old_gold_purchase_22k_with_account_allocation_keeps_the_final_rounded_amount(self):
        result = self.finalize(InvoiceDraft(5, "12", (), (old(K22, "10", **disc()),), (cash("40000", direction="OUT"),),
                                            account_amount="24045", account_direction="SHOP_OWES"))
        self.assertEqual(result["settlement_status"], "PARTIAL")
        self.assertEqual(self.store.ledger[0]["amount_delta"], D("-24045.00"))
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("-40000.00")])

    def test_mixed_invoice_sells_a_22k_piece_against_22k_old_gold(self):
        # O = 5 g * 6501.9167 = 32,509.58 - 1.5% (487.64) = 32,021.94; the entered sale total 67,000 is authoritative.
        gross = money(D("5") * P22[0])
        net = gross - money(gross * D("1.50") / 100)
        self.assertEqual(net, D("32021.94"))
        owed = D("67000") - net
        result = self.finalize(InvoiceDraft(5, "12", (sale("110"),), (old(K22, "5", **disc()),), (cash("34978.06"),)))
        self.assertEqual((result["invoice_type"], result["sale_total"]), ("MIXED", "67000.00"))
        self.assertEqual(owed, D("34978.06"))
        self.assertEqual(self.store.old_gold[0]["karat_id"], K22)
        self.assertEqual(self.store.items["110"]["inventory_status"], "SOLD")

    def test_a_22k_line_with_no_approved_22k_price_is_refused_and_nothing_is_written(self):
        plain = self.prices._set
        self.prices._set = lambda karat_ids: plain([k for k in karat_ids if k != K22])       # the published set has no 22K
        before = copy.deepcopy(self.store.tables())
        for draft in (InvoiceDraft(5, "12", (sale("110"),), (), (cash("67000"),)),
                      InvoiceDraft(5, "12", (), (old(K22, "10", **disc()),), (cash("64045", direction="OUT"),))):
            with self.assertRaises(InvoiceError) as context:
                self.finalize(draft)
            self.assertEqual(context.exception.code, "PRICE_MISSING")
        self.assertEqual(self.store.tables(), before)
        # 21K still works with the same price source: no karat ever borrows another karat's price.
        self.finalize(InvoiceDraft(5, "12", (sale("100"),), (), (cash("67011"),)))


class Karat22ScrapSaleTests(Karat22Case):
    def test_f7_sells_counted_22k_scrap_grams_at_the_derived_sell_price_and_consumes_only_22k(self):
        f7 = DescribedSaleInput(OLD_GOLD_STOCK, K22, "3.200", item_type_id=4, description="دبلة سادة")
        before21 = self.store.old_gold_opening[1]
        gold = money(D("3.200") * P22[1])                                      # 20,891.20
        self.assertEqual(gold, D("20891.20"))
        result = self.finalize(InvoiceDraft(5, "12", (), (), (cash("21500"),), described_sale_lines=(f7,)))
        self.assertEqual(result["sale_total"], "21500.00")
        moved = [m for m in self.store.old_gold if m["karat_id"] == K22]
        self.assertEqual([m["signed_weight"] for m in moved], [D("-3.200")])
        self.assertEqual([m for m in self.store.old_gold if m["karat_id"] != K22], [])
        self.assertEqual(self.store.old_gold_opening[1], before21)

    def test_f7_refuses_more_22k_grams_than_the_counted_balance(self):
        f7 = DescribedSaleInput(OLD_GOLD_STOCK, K22, "100.001", item_type_id=4, description="دبلة سادة")
        self.assertRefused("OLD_GOLD_STOCK_INSUFFICIENT",
                           InvoiceDraft(5, "12", (), (), (cash("700000"),), described_sale_lines=(f7,)))


class HistoricalRegressionTests(Karat22Case):
    def test_existing_karat_results_are_unchanged_by_the_presence_of_22k(self):
        result = self.finalize(InvoiceDraft(5, "12", (), (old(1, "10", **disc()),),
                                            (PaymentInput("MOBILE_WALLET", "50000", bank_account_id="9"),),
                                            account_amount="11140", account_direction="SHOP_OWES"))
        self.assertEqual(result["settlement_status"], "PARTIAL")
        self.assertEqual(self.store.old_gold[0]["cash_value"], D("61138.95"))     # the same figure as without 22K
        self.assertEqual(self.store.ledger[0]["amount_delta"], D("-11140.00"))


if __name__ == "__main__":
    unittest.main()
