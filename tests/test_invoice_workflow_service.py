"""Billing shop workflow on the in-memory store: real money → sale price → making,
F5 unidentified sales, F7 sales from old-gold stock, explicit settlement,
new customers created on finalize, making review. Never a live database."""
import copy
from decimal import Decimal
import unittest

from services.authentication_service import AuthenticationError, AuthenticationResult, GENERIC_ERROR
from services.invoice_service import (
    _APPROVER_PROOF, OLD_GOLD_STOCK, UNIDENTIFIED, DescribedSaleInput, InvoiceDraft, InvoiceError,
    NewCustomerInput, PaymentInput, SaleLineInput, SettlementApprover,
)
from services.invoice_calculator import InvoiceCalculationError, InvoiceCalculator as Calc
from tests.test_invoice_service import InvoiceRepo, InvoiceServiceTestCase, cash, old


D = Decimal


def scanned(item="100"):
    return SaleLineInput(item)


def manual(**kwargs):
    values = dict(kind=UNIDENTIFIED, karat_id=1, weight_grams="4.000", item_type_id=4, factory_id=5,
                  size="18", notes="نقش يدوي", reason_code="BARCODE_LOST")
    values.update(kwargs)
    return DescribedSaleInput(**values)


def from_stock(**kwargs):
    values = dict(kind=OLD_GOLD_STOCK, karat_id=1, weight_grams="3.200", item_type_id=4, description="دبلة سادة")
    values.update(kwargs)
    return DescribedSaleInput(**values)


class DerivedSaleTests(InvoiceServiceTestCase):
    def test_calculator_allocates_by_weight_with_remainder_on_the_last_line(self):
        agreed = Calc.agreed_sale([("10.500", "6232.0000"), ("3.333", "5341.0000")], "85000")
        # gold 65436.00 + 17801.55 = 83237.55; making 1762.45 over 13.833 g
        self.assertEqual((agreed.gold_value_total, agreed.making_total), (D("83237.55"), D("1762.45")))
        self.assertEqual(agreed.average_making_per_gram, D("127.41"))
        self.assertEqual(sum(agreed.allocations), D("1762.45"))
        self.assertEqual(agreed.allocations[0], D("1337.79"))          # 1762.45 × 10.5 / 13.833, rounded down
        with self.assertRaises(InvoiceCalculationError) as below:
            Calc.agreed_sale([("10.500", "6232.0000")], "60000")
        self.assertEqual(below.exception.code, "AGREED_BELOW_GOLD")

    def test_the_real_money_is_the_sale_price(self):
        draft = InvoiceDraft(5, "12", (scanned("100"), scanned("101")), (), (cash("85000"),))
        preview = self.service.preview(draft)
        self.assertEqual((preview["gold_value_total"], preview["effective_sale_total"], preview["making_total"],
                          preview["average_making_per_gram"]), ("83237.55", "85000.00", "1762.45", "127.41"))
        self.assertEqual(preview["sale_total"], "85000.00")
        self.finalize(draft)
        lines = [v for kind, v in self.store.lines if kind == "SALE"]
        self.assertEqual([l["making_per_gram"] for l in lines], [None, None])
        self.assertEqual(sum(l["making_total"] for l in lines), D("1762.45"))
        self.assertEqual([l["identification_method"] for l in lines], ["SCAN", "SCAN"])
        details = self.store.events[1]["details"]
        self.assertEqual((details["effective_sale_total"], details["making_total"]), (D("85000.00"), D("1762.45")))


class MandatoryCustomerTests(InvoiceServiceTestCase):
    """No anonymous SALE: every save that sells or uses the customer account needs a name. Old gold only, fully paid
    in real money, needs none (OldGoldOnlyCustomerOptionalTests)."""

    def cases(self, customer=None, new_customer=None):
        kw = {"new_customer": new_customer} if new_customer else {}
        return {"sale": InvoiceDraft(5, customer, (scanned(),), (), (pay("67011"),), **kw),
                "exchange": InvoiceDraft(5, customer, (scanned(),), (disc(weight="5"),), (pay("36441.53"),), **kw),
                "old gold only": InvoiceDraft(5, customer, (), (old(1, "5", discount_type="PERCENT",
                                                                    discount_value="1.50"),),
                                              (pay("30569.47"),), **kw),
                "manual F5": InvoiceDraft(5, customer, (), (), (pay("25500"),), described_sale_lines=(manual(),), **kw),
                "F7 from old-gold stock": InvoiceDraft(5, customer, (), (), (pay("20500"),),
                                                       described_sale_lines=(from_stock(),), **kw)}

    def test_every_kind_of_invoice_is_refused_without_a_customer_name(self):
        for label, new_customer in (("no customer", None), ("empty new name", NewCustomerInput("")),
                                    ("whitespace new name", NewCustomerInput("   ", "0100"))):
            for name, draft in self.cases(new_customer=new_customer).items():
                if name == "old gold only" and new_customer is None:
                    continue            # fully paid in real money: the customer is optional (see the dedicated tests)
                with self.subTest(customer=label, invoice=name):
                    self.assertRefused("CUSTOMER_NAME_REQUIRED", draft)          # nothing written
                    with self.assertRaises(InvoiceError) as refused:
                        self.finalize(draft)
                    self.assertEqual(str(refused.exception), "اسم العميل مطلوب لإتمام الفاتورة")
        self.assertEqual((self.store.invoices, self.store.payments, self.store.cash_movements), ([], [], []))

    def test_preview_reports_the_missing_name_first(self):
        preview = self.service.preview(self.cases()["sale"])
        self.assertEqual(preview["blocking_problems"][0]["code"], "CUSTOMER_NAME_REQUIRED")

    def test_a_typed_new_name_is_created_inside_the_invoice(self):
        result = self.finalize(self.cases(new_customer=NewCustomerInput("عميل جديد"))["sale"])
        self.assertTrue(result["customer_created"])


class UnidentifiedSaleTests(InvoiceServiceTestCase):
    def test_manual_sale_opens_a_review_case_without_an_inventory_identity(self):
        # 4 g × 6232 = 24928; received 25500 → making 572
        draft = InvoiceDraft(5, "12", (), (), (cash("25500"),), described_sale_lines=(manual(),))
        result = self.finalize(draft)
        self.assertEqual(result["unidentified_cases"], 1)
        kind, line = self.store.lines[0]
        self.assertEqual(kind, "SELL_UNIDENTIFIED")
        self.assertNotIn("inventory_item_id", line)
        self.assertEqual((line["item_name"], line["factory_name"], line["size"], line["making_total"]),
                         ("دبلة", "لوكس", "18", D("572.00")))
        case = self.store.cases[0]
        self.assertEqual((case["reason_code"], case["reason"], case["item_type_id"], case["factory_id"],
                          case["karat_id"], case["weight_grams"], case["size"], case["salesperson_employee_id"],
                          case["created_by_user_id"]),
                         ("BARCODE_LOST", "الباركود مفقود أو تالف", 4, 5, 1, D("4.000"), "18", 5, 17))
        self.assertEqual(self.store.case_events[0]["event_type"], "OPENED")
        self.assertEqual(self.store.inventory_movements, [])            # no piece is marked sold
        self.assertEqual({i["inventory_status"] for i in self.store.items.values() if i["inventory_item_id"] != "102"},
                         {"IN_STOCK"})

    def test_reason_and_item_type_are_required(self):
        for line, code in ((manual(reason_code=None), "REASON_REQUIRED"), (manual(item_type_id=None), "ITEM_TYPE_REQUIRED")):
            with self.subTest(code=code):
                self.assertRefused(code, InvoiceDraft(5, "12", (), (), (cash("25500"),), described_sale_lines=(line,)))

    def test_other_reason_needs_a_written_reason(self):
        self.assertRefused("REASON_NOTES_REQUIRED",
                           InvoiceDraft(5, "12", (), (), (cash("25500"),),
                                        described_sale_lines=(manual(reason_code="OTHER", notes=""),)))
        result = self.finalize(InvoiceDraft(5, "12", (), (), (cash("25500"),),
                                            described_sale_lines=(manual(reason_code="OTHER", notes="قطعة أمانة"),)))
        self.assertEqual((result["unidentified_cases"], self.store.cases[0]["reason"]), (1, "سبب آخر"))

    def test_piece_found_through_search_is_a_normal_identified_sale(self):
        # A missing label alone is not a manual sale: the piece chosen through F3 is sold by its identity.
        result = self.finalize(InvoiceDraft(5, "12", (scanned(),), (), (cash("67011"),)))
        self.assertEqual((result["unidentified_cases"], self.store.cases), (0, []))
        self.assertEqual(self.store.lines[0][0], "SALE")
        self.assertEqual(self.store.lines[0][1]["item"]["inventory_item_id"], "100")

    def test_real_money_is_required_for_described_lines(self):
        self.assertRefused("PAYMENT_REQUIRED",
                           InvoiceDraft(5, "12", (), (), (), described_sale_lines=(manual(),)))


class OldGoldStockSaleTests(InvoiceServiceTestCase):
    def test_sale_from_stock_deducts_exact_grams_through_its_own_movement(self):
        # 3.2 g × 6232 = 19942.40; received 20500 → making 557.60
        draft = InvoiceDraft(5, "12", (), (), (cash("20500"),), described_sale_lines=(from_stock(),))
        self.finalize(draft)
        movement = self.store.old_gold[0]
        self.assertEqual((movement["code"], movement["signed_weight"], movement["karat_id"], movement["employee_id"],
                          movement["created_by_user_id"]), ("INVOICE_SALE_OUT", D("-3.200"), 1, 5, 17))
        kind, line = self.store.lines[0]
        self.assertEqual((kind, line["old_gold_movement_id"], line["item_name"]),
                         ("SELL_OLD_GOLD", "701", "دبلة — دبلة سادة"))
        self.assertEqual(self.store.locked_karats, [1])
        self.assertEqual(self.store.cases, [])                          # not an inventory review

    def test_insufficient_stock_shows_available_grams(self):
        draft = InvoiceDraft(5, "12", (), (), (cash("700000"),), described_sale_lines=(from_stock(weight_grams="100.500"),))
        preview = self.service.preview(draft)
        problem = [p for p in preview["blocking_problems"] if p["code"] == "OLD_GOLD_STOCK_INSUFFICIENT"][0]
        self.assertIn("المتاح 100.000 جرام", problem["message"])
        self.assertRefused("OLD_GOLD_STOCK_INSUFFICIENT", draft)

    def test_old_gold_bought_on_the_same_invoice_does_not_count(self):
        # Opening COUNTED 21K = 100 g; the customer brings 50 g, the shop sells 120 g from stock.
        draft = InvoiceDraft(5, "12", (), (old(1, "50", discount_type="PERCENT", discount_value="1.50"),),
                             (cash("760000"),), described_sale_lines=(from_stock(weight_grams="120.000"),))
        self.assertRefused("OLD_GOLD_STOCK_INSUFFICIENT", draft)


class SettlementTests(InvoiceServiceTestCase):
    permissions = frozenset({"APP_LOGIN", "INVOICE_CREATE", "INVOICE_FINALIZE", "PAYMENT_RECORD"})

    def draft(self, paid, **kwargs):
        # Old gold only (the customer only sells): 22.107 g × 6207 = 137,218.15 − 1.50% (2,058.27)
        # = 135,159.88 owed. The 1.25–5.00% discount rule applies here too.
        return InvoiceDraft(5, kwargs.pop("customer", "12"), (), (disc("1.50", "22.107"),), (cash(paid),), **kwargs)

    def test_small_difference_is_settled_explicitly_without_customer_or_ledger(self):
        net = self.service.preview(self.draft("135150", settlement_choice="SETTLE"))
        self.assertEqual((net["gap_signed"], net["account_residual_amount"]), ("-9.88", "0.00"))
        self.assertEqual(net["blocking_problems"], [])
        result = self.finalize(self.draft("135150", settlement_choice="SETTLE"))
        self.assertEqual(result["settlement_status"], "SETTLED")
        self.assertEqual(self.store.ledger, [])                           # no BUY_BALANCE_DUE, no pending cash
        adjustment = self.store.adjustments[0]
        self.assertEqual((adjustment["residual_signed"], adjustment["amount_signed"], adjustment["approved_by_user_id"]),
                         (D("-9.88"), D("-9.88"), None))
        self.assertEqual(self.store.payments[0]["amount"], D("135150.00"))  # the real money only
        # No sold pieces: A stays an invoice-level adjustment. Within 50 ج: no settlement review
        # (the default 1.50% discount is not a changed discount, so no discount review either).
        self.assertEqual([r["review_type"] for r in self.store.reviews], [])

    def test_never_automatic_and_account_choice_needs_a_customer(self):
        self.assertRefused("SETTLEMENT_CHOICE_REQUIRED", self.draft("135150"))
        self.assertRefused("CUSTOMER_NAME_REQUIRED", self.draft("135150", customer=None, account_amount="9.88",
                                                                account_direction="SHOP_OWES"))
        # The account follows the FINAL (EGP-5 rounded) amount: 135,160 − 135,150 = 10.00, never the exact 9.88.
        self.assertRefused("ACCOUNT_AMOUNT_NOT_ROUNDED", self.draft(
            "135150", account_amount="9.88", account_direction="SHOP_OWES", customer="12"))
        self.finalize(self.draft("135150", account_amount="10", account_direction="SHOP_OWES", customer="12"))
        self.assertEqual((self.store.ledger[0]["type_code"], self.store.ledger[0]["amount_delta"]),
                         ("BUY_BALANCE_DUE", D("-10.00")))
        self.assertEqual(self.store.adjustments, [])               # rounded UP: no same-sign audit row exists

    def test_above_fifty_needs_reason_and_manager_approval(self):
        draft = self.draft("135100", settlement_choice="SETTLE")               # 59.88
        preview = self.service.preview(draft)
        self.assertEqual({p["code"] for p in preview["blocking_problems"]},
                         {"SETTLEMENT_REASON_REQUIRED", "SETTLEMENT_APPROVAL_REQUIRED"})
        self.assertTrue(preview["settlement_needs_approval"])
        self.assertRefused("SETTLEMENT_REASON_REQUIRED", draft)

    def test_manager_credentials_approve_at_the_counter(self):
        class Approvers:
            def __init__(self, connection):
                pass

            def get_identity(self, user_id, lock=False):
                return {"id": user_id, "is_active": True, "must_change_password": False, "employee_id": None}

            def effective_permissions(self, user_id, lock=False):
                return frozenset({"APP_LOGIN", "INVOICE_SETTLEMENT_APPROVE"})

        class Credentials:
            def verify_credentials(self, username, password):
                if password != "manager-pass":
                    raise AuthenticationError(GENERIC_ERROR)
                return AuthenticationResult(3, None, "المدير", frozenset({"APP_LOGIN", "INVOICE_SETTLEMENT_APPROVE"}))

        self.service._approvers, self.service._authentication = Approvers, Credentials()
        with self.assertRaises(AuthenticationError):
            self.service.verify_settlement_approver("manager", "wrong")
        grant = self.service.verify_settlement_approver("manager", "manager-pass")
        self.finalize(self.draft("135100", settlement_choice="SETTLE", settlement_reason="كسر فكة",
                                 settlement_approver=grant))
        adjustment = self.store.adjustments[0]
        self.assertEqual((adjustment["approved_by_user_id"], adjustment["reason"], adjustment["amount_signed"]),
                         (3, "كسر فكة", D("-59.88")))
        self.assertEqual(self.store.reviews[0]["review_type"], "CASH_SETTLEMENT")
        forged = SettlementApprover(3, "المدير")                             # not produced by verification
        self.assertRefused("SETTLEMENT_APPROVAL_REQUIRED",
                           self.draft("135100", settlement_choice="SETTLE", settlement_reason="x", settlement_approver=forged))

    def test_owner_with_permission_approves_directly_and_over_500_is_refused(self):
        self.session.logout()
        from services.authentication_service import _VERIFIED
        owner = frozenset(self.permissions | {"INVOICE_SETTLEMENT_APPROVE"})
        self.session._establish(AuthenticationResult(1, None, "المالك", owner, _VERIFIED), int(self.session.generation))
        self.authorization.permissions = owner
        self.finalize(self.draft("135100", settlement_choice="SETTLE", settlement_reason="فكة"))
        self.assertEqual(self.store.adjustments[0]["approved_by_user_id"], 1)
        self.assertRefused("SETTLEMENT_OVER_LIMIT", self.draft("134500", settlement_choice="SETTLE", settlement_reason="x"))


class NewCustomerTests(InvoiceServiceTestCase):
    def draft(self, **kwargs):
        return InvoiceDraft(5, kwargs.pop("customer", None), (scanned(),), (), (cash("67011"),),
                            account_amount="7011", account_direction="CUSTOMER_OWES", **kwargs)

    def test_created_only_on_finalize_inside_the_invoice_transaction(self):
        draft = self.draft(new_customer=NewCustomerInput("عميل جديد", "0100 123 4567", "المنصورة"))
        preview = self.service.preview(draft)
        self.assertEqual(preview["blocking_problems"], [])                # a new customer carries the account part
        self.assertEqual(self.store.new_customers, [])                     # preview creates nothing
        result = self.finalize(draft)
        self.assertTrue(result["customer_created"])
        customer_id = self.store.new_customers[0]
        self.assertEqual(self.store.invoices[0]["customer_id"], customer_id)
        self.assertEqual(self.store.ledger[0]["customer_id"], int(customer_id))
        self.assertEqual(self.store.customers[customer_id]["created_by"], 17)

    def test_failure_rolls_the_new_customer_back(self):
        InvoiceRepo.fail_on = "insert_sale_line"
        before = copy.deepcopy(self.store.tables())
        with self.assertRaises(RuntimeError):
            self.finalize(self.draft(new_customer=NewCustomerInput("عميل جديد", "01001234567", None)))
        self.assertNothingWritten(before)

    def test_same_mobile_links_the_existing_customer(self):
        self.store.customers["12"]["mobile"] = "01001234567"
        draft = self.draft(new_customer=NewCustomerInput("اسم آخر", "٠١٠٠-١٢٣-٤٥٦٧", None))
        self.assertEqual(self.service.preview(draft)["new_customer_same_mobile"][0]["customer_id"], "12")
        result = self.finalize(draft)
        self.assertFalse(result["customer_created"])
        self.assertEqual(self.store.invoices[0]["customer_id"], "12")

    def test_validation_and_conflict(self):
        self.assertRefused("CUSTOMER_NAME_REQUIRED", self.draft(new_customer=NewCustomerInput("  ")))
        with self.assertRaises(InvoiceError):
            self.finalize(InvoiceDraft(5, "12", (scanned(),), (), (cash("67011"),),
                                       new_customer=NewCustomerInput("x")))

    def test_inline_search_by_name_or_phone(self):
        self.store.customers["12"]["mobile"] = "01001234567"
        self.assertEqual([r["customer_id"] for r in self.service.search_customers("عميل")], ["12"])
        self.assertEqual([r["customer_id"] for r in self.service.search_customers("0100 123")], ["12"])
        self.assertEqual(self.service.search_customers(""), [])


class MakingReviewTests(InvoiceServiceTestCase):
    def test_average_outside_the_category_range_opens_a_review_only(self):
        self.store.items["100"].update(category_id=1)
        self.store.categories[1].update(sale_making_min_per_gram=D("200.00"), sale_making_max_per_gram=D("600.00"))
        draft = InvoiceDraft(5, "12", (scanned(),), (), (cash("66500"),))  # 101.52/g
        self.assertTrue(self.service.preview(draft)["making_review_required"])
        result = self.finalize(draft)
        self.assertEqual((result["sale_total"], result["settlement_status"]), ("66500.00", "SETTLED"))
        review = self.store.reviews[0]
        self.assertEqual(review["review_type"], "MAKING_PRICE")
        self.assertEqual(review["details"]["groups"][0]["category_code"], "A")

    def test_mixed_categories_flag_only_the_bad_group(self):
        self.store.items["100"].update(category_id=1)
        self.store.items["101"].update(category_id=2)
        self.store.categories[1].update(sale_making_min_per_gram=D("200.00"))
        self.store.categories[2] = {"id": 2, "code": "B", "name": "B", "sale_making_min_per_gram": D("50.00"),
                                    "sale_making_max_per_gram": None}
        draft = InvoiceDraft(5, "12", (scanned("100"), scanned("101")), (), (cash("85000"),))
        self.finalize(draft)                                             # 127.41/g overall
        groups = self.store.reviews[0]["details"]["groups"]
        self.assertEqual([(g["category_code"], g["status"]) for g in groups],
                         [("A", "OUT_OF_RANGE"), ("B", "WITHIN_RANGE")])
        self.assertEqual(len(self.store.reviews), 1)

    def test_within_range_or_unconfigured_opens_nothing(self):
        draft = InvoiceDraft(5, "12", (scanned(),), (), (cash("66500"),))
        self.assertFalse(self.service.preview(draft)["making_review_required"])
        self.finalize(draft)
        self.assertEqual(self.store.reviews, [])


class CascadingSearchTests(InvoiceServiceTestCase):
    def setUp(self):
        super().setUp()
        for item_id, type_id, factory_id in (("100", 2, 5), ("101", 4, None), ("102", 2, 5)):
            self.store.items[item_id].update(item_type_id=type_id, factory_id=factory_id)
        self.store.items["100"]["factory_name"] = "لوكس"

    def test_each_step_offers_only_what_is_in_stock(self):
        facets = self.service.item_search_facets()
        self.assertEqual([(t["name"], t["count"]) for t in facets["item_types"]], [("خاتم", 1), ("سلسلة", 1)])
        self.assertEqual((facets["karats"], facets["factories"]), ([], []))
        facets = self.service.item_search_facets(item_type_id=2)
        self.assertEqual([(k["name"], k["count"]) for k in facets["karats"]], [("21", 1)])  # 102 is SOLD
        facets = self.service.item_search_facets(item_type_id=2, karat_id=1)
        self.assertEqual([f["name"] for f in facets["factories"]], ["لوكس"])
        self.assertEqual(self.service.item_search_facets(item_type_id=2, karat_id=1, exclude_ids=("100",))["factories"], [])

    def test_results_follow_type_karat_and_factory(self):
        self.assertEqual([i["inventory_item_id"] for i in self.service.search_sellable_items(
            item_type_id=2, karat_id=1, factory_id=5)["items"]], ["100"])
        self.assertEqual(self.service.search_sellable_items(item_type_id=4, factory_id=5)["items"], [])


def disc(percent="1.50", weight="20"):
    return old(1, weight, discount_type="PERCENT", discount_value=percent)


def pay(amount, method="PHYSICAL_CASH", account=None):
    return PaymentInput(method, amount, bank_account_id=account)


class CommercialModelTests(InvoiceServiceTestCase):
    """effective_sale_total = O + P + L (no typed price anywhere; L = deferred account part).

    Item 100: 21K 10.500 g × 6232 = 65,436.00 gold. Old gold 21K 20 g × 6207 = 124,140.00
    − 1.5% (1,862.10) = O 122,277.90."""

    def restock(self):
        self.store.items["100"]["inventory_status"] = "IN_STOCK"

    # ---- 1 / 3: customer pays the shop
    def test_sale_only_cash_received_is_the_sale_price(self):
        for received, average in (("67011", "150.00"), ("67111", "159.52")):
            with self.subTest(received=received):
                preview = self.service.preview(InvoiceDraft(5, "12", (scanned(),), (), (pay(received),)))
                self.assertEqual((preview["payment_direction"], preview["effective_sale_total"],
                                  preview["average_making_per_gram"]), ("IN", f"{received}.00", average))

    # ---- 2 / 4 / 7: exchange where the shop pays the customer
    def test_exchange_old_gold_minus_money_paid_is_the_sale_price(self):
        first = self.service.preview(InvoiceDraft(5, "12", (scanned(),), (disc(),), (pay("55000"),)))
        # 122,277.90 − 55,000 = 67,277.90 → making 1,841.90 → 175.42/g
        self.assertEqual((first["payment_direction"], first["effective_sale_total"], first["making_total"],
                          first["average_making_per_gram"]), ("OUT", "67277.90", "1841.90", "175.42"))
        # Paying the customer 1,000 more lowers the sale price, the making and the average at once.
        second = self.service.preview(InvoiceDraft(5, "12", (scanned(),), (disc(),), (pay("56000"),)))
        self.assertEqual((second["effective_sale_total"], second["average_making_per_gram"]), ("66277.90", "80.18"))
        self.finalize(InvoiceDraft(5, "12", (scanned(),), (disc(),), (pay("55000"),)))
        self.assertEqual(self.store.invoices[0]["sell_total"], D("67277.90"))
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("-55000.00")])

    def test_exchange_where_the_customer_pays_the_shop(self):
        # O (5 g) = 30,569.47; the customer adds 36,441.53 → 67,011.00.
        draft = InvoiceDraft(5, "12", (scanned(),), (disc(weight="5"),), (pay("36441.53"),))
        preview = self.service.preview(draft)
        self.assertEqual((preview["payment_direction"], preview["effective_sale_total"]), ("IN", "67011.00"))
        more = self.service.preview(InvoiceDraft(5, "12", (scanned(),), (disc(weight="5"),), (pay("36541.53"),)))
        self.assertEqual(more["effective_sale_total"], "67111.00")                    # IN raises the sale price

    def test_exchange_direction_is_the_employees_explicit_choice(self):
        # O (10.8 g) = 66,030.07 > gold 65,436.00, so the baseline is OUT; the making makes the customer pay.
        base = InvoiceDraft(5, "12", (scanned(),), (disc(weight="10.8"),), (pay("1000"),))
        self.assertEqual(self.service.preview(base)["payment_direction"], "OUT")
        chosen = InvoiceDraft(5, "12", (scanned(),), (disc(weight="10.8"),), (pay("1000"),), payment_direction="IN")
        preview = self.service.preview(chosen)
        self.assertEqual((preview["payment_direction"], preview["effective_sale_total"]), ("IN", "67030.07"))

    # ---- customer account: the deferred part of the same commercial amount (P + L)
    def test_live_regression_moving_money_to_the_account_never_moves_the_average(self):
        # The live screenshot: 21K 3.600 g sold (22,460.40), old gold net 36,724.73, 12,800 owed to the customer.
        from unittest.mock import patch as patched
        from tests import test_invoice_service as fixture
        self.store.items["106"] = {**self.store.items["100"], "inventory_item_id": "106", "weight_grams": D("3.600")}
        prices = {**fixture.PRICES, 1: (D("6213.9983"), D("6239.0000"))}           # 6 g → 37,283.99 gross
        with patched.dict(fixture.PRICES, prices):
            def draft(paid, **account):
                return InvoiceDraft(5, "12", (scanned("106"),), (disc(weight="6"),), (pay(paid),), **account)
            cases = {"all paid": draft("12800"),
                     "shop owes 1000": draft("11800", account_amount="1000", account_direction="SHOP_OWES")}
            for name, d in cases.items():
                preview = self.service.preview(d)
                with self.subTest(case=name):
                    self.assertEqual((preview["gold_value_total"], preview["old_gold_total"],
                                      preview["effective_sale_total"], preview["making_total"],
                                      preview["average_making_per_gram"], preview["payment_direction"]),
                                     ("22460.40", "36724.73", "23924.73", "1464.33", "406.76", "OUT"))
                    self.assertEqual(preview["blocking_problems"], [])
            wrong = self.service.preview(draft("12800", account_amount="1000", account_direction="CUSTOMER_OWES"))
            self.assertEqual([p["code"] for p in wrong["blocking_problems"]], ["ACCOUNT_DIRECTION_MISMATCH"])

    def sale_23000(self, paid, method="PHYSICAL_CASH", account=None, **extra):
        # 3.600 g × 6232 = 22,435.20 gold; commercial 23,000 → making 564.80 → 156.89/g.
        self.store.items["106"] = {**self.store.items["100"], "inventory_item_id": "106", "weight_grams": D("3.600")}
        legs = tuple(pay(amount, m, a) for amount, m, a in paid) if isinstance(paid, tuple) \
            else (pay(paid, method, account),)
        return InvoiceDraft(5, "12", (scanned("106"),), (), legs, **extra)

    def test_regression_23000_with_3000_on_the_customer_is_20000_cash(self):
        full = self.service.preview(self.sale_23000("23000"))
        deferred = self.service.preview(self.sale_23000("20000", account_amount="3000",
                                                        account_direction="CUSTOMER_OWES"))
        for preview in (full, deferred):
            self.assertEqual((preview["effective_sale_total"], preview["making_total"],
                              preview["average_making_per_gram"]), ("23000.00", "564.80", "156.89"))
        self.assertEqual((deferred["money_received"], deferred["account_signed"]), ("20000.00", "3000.00"))
        self.finalize(self.sale_23000("20000", account_amount="3000", account_direction="CUSTOMER_OWES"))
        self.assertEqual([p["amount"] for p in self.store.payments], [D("20000.00")])
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("20000.00")])   # never 23,000
        self.assertEqual([(r["type_code"], r["amount_delta"]) for r in self.store.ledger],
                         [("SALE_BALANCE_DUE", D("3000.00"))])
        self.assertEqual((self.store.invoices[0]["sell_total"], [v for k, v in self.store.lines][0]["making_total"]),
                         (D("23000.00"), D("564.80")))

    def test_regression_outgoing_23000_with_3000_to_the_customer_is_20000_paid(self):
        # O (20 g) = 122,277.90; the shop owes the customer 23,000 of it: 20,000 paid now + للعميل 3,000.
        def draft(paid, **account):
            return InvoiceDraft(5, "12", (scanned(),), (disc(),), (pay(paid),), **account)
        full = self.service.preview(draft("23000"))
        deferred = self.service.preview(draft("20000", account_amount="3000", account_direction="SHOP_OWES"))
        self.assertEqual((full["effective_sale_total"], full["making_total"]),
                         (deferred["effective_sale_total"], deferred["making_total"]))
        self.assertEqual((deferred["payment_direction"], deferred["money_paid"]), ("OUT", "20000.00"))
        self.finalize(draft("20000", account_amount="3000", account_direction="SHOP_OWES"))
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("-20000.00")])
        self.assertEqual([(r["type_code"], r["amount_delta"]) for r in self.store.ledger],
                         [("BUY_BALANCE_DUE", D("-3000.00"))])

    def test_bank_and_wallet_receive_only_the_money_paid_now(self):
        for method, account in (("INSTAPAY", "7"), ("BANK_TRANSFER", "7"), ("MOBILE_WALLET", "9")):
            with self.subTest(method=method):
                self.store.bank_movements.clear()
                self.store.ledger.clear()
                self.finalize(self.sale_23000("20000", method, account, account_amount="3000",
                                              account_direction="CUSTOMER_OWES"))
                self.store.items["106"]["inventory_status"] = "IN_STOCK"
                self.assertEqual([(m["bank_account_id"], m["amount_delta"]) for m in self.store.bank_movements],
                                 [(int(account), D("20000.00"))])
                self.assertEqual([r["amount_delta"] for r in self.store.ledger], [D("3000.00")])
        self.assertEqual(self.store.cash_movements, [])

    def test_split_payment_and_account_reconcile_exactly(self):
        self.finalize(self.sale_23000((("5000", "PHYSICAL_CASH", None), ("15000", "INSTAPAY", "7")),
                                      account_amount="3000", account_direction="CUSTOMER_OWES"))
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("5000.00")])
        self.assertEqual([m["amount_delta"] for m in self.store.bank_movements], [D("15000.00")])
        self.assertEqual([r["amount_delta"] for r in self.store.ledger], [D("3000.00")])
        self.assertEqual(self.store.invoices[0]["sell_total"], D("23000.00"))          # 5,000 + 15,000 + 3,000

    def test_bypassing_the_screen_cannot_create_money_or_reverse_the_account(self):
        # Whatever a caller sends, the money ledgers get exactly the payment legs and the commercial
        # value is P + L: 23,000 cash + 3,000 على العميل is a 26,000 sale, never 23,000 with extra cash.
        self.finalize(self.sale_23000("23000", account_amount="3000", account_direction="CUSTOMER_OWES"))
        self.assertEqual(self.store.invoices[0]["sell_total"], D("26000.00"))
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("23000.00")])
        # An account part against the money direction is refused before anything is written.
        self.store.items["106"]["inventory_status"] = "IN_STOCK"
        self.assertRefused("ACCOUNT_DIRECTION_MISMATCH",
                           self.sale_23000("23000", account_amount="3000", account_direction="SHOP_OWES"))

    def test_account_entry_needs_a_customer_and_a_direction(self):
        self.assertRefused("CUSTOMER_NAME_REQUIRED", InvoiceDraft(5, None, (scanned(),), (), (pay("66011"),),
                                                             account_amount="1000", account_direction="CUSTOMER_OWES"))
        self.assertRefused("ACCOUNT_DIRECTION_REQUIRED", InvoiceDraft(5, "12", (scanned(),), (), (pay("66011"),),
                                                                      account_amount="1000"))
        self.finalize(InvoiceDraft(5, "12", (scanned(),), (), (pay("67011"),), account_amount="0"))
        self.assertEqual(self.store.ledger, [])                                   # zero: no ledger movement

    def test_a_failing_customer_ledger_rolls_the_invoice_back(self):
        from tests.test_invoice_service import CustomerRepo
        original = CustomerRepo.add_transaction

        def failing(repository, **values):
            raise RuntimeError("injected ledger failure")
        CustomerRepo.add_transaction = failing
        self.addCleanup(setattr, CustomerRepo, "add_transaction", original)
        before = copy.deepcopy(self.store.tables())
        with self.assertRaisesRegex(RuntimeError, "injected"):
            self.finalize(InvoiceDraft(5, "12", (scanned(),), (), (pay("67011"),), account_amount="1000",
                                       account_direction="CUSTOMER_OWES"))
        self.assertNothingWritten(before)

    # ---- 9–13: old-gold discount is 1.25–5.00% (mixed AND old gold only)
    def test_exchange_discount_must_be_between_1_25_and_5(self):
        for percent in ("1.25", "1.50", "2.00", "5.00"):
            with self.subTest(percent=percent):
                preview = self.service.preview(InvoiceDraft(5, "12", (scanned(),), (disc(percent),), (pay("55000"),)))
                self.assertNotIn("OLD_GOLD_DISCOUNT_RANGE", [p["code"] for p in preview["blocking_problems"]])
        for line in (disc("1.24"), disc("5.01"), disc("0"), old(1, "20"),
                     old(1, "20", discount_type="AMOUNT", discount_value="1862.10")):
            with self.subTest(line=line):
                draft = InvoiceDraft(5, "12", (scanned(),), (line,), (pay("55000"),))
                self.assertIn("OLD_GOLD_DISCOUNT_RANGE",
                              [p["code"] for p in self.service.preview(draft)["blocking_problems"]])
                self.assertRefused("OLD_GOLD_DISCOUNT_RANGE", draft)

    def test_old_gold_only_may_go_down_to_one_percent_while_a_sale_keeps_1_25(self):
        # Owner decision 2026-10-23: buying old gold only accepts 1.00–5.00%; the mixed invoice keeps 1.25–5.00%.
        for percent in ("1.00", "1.25", "1.50", "2.00", "5.00"):
            with self.subTest(percent=percent):
                preview = self.service.preview(InvoiceDraft(5, "12", (), (disc(percent, "10"),), ()))
                self.assertNotIn("OLD_GOLD_DISCOUNT_RANGE", [p["code"] for p in preview["blocking_problems"]])
                self.assertEqual(preview["old_gold_discount_range"], ["1.00", "5.00"])
        mixed = self.service.preview(InvoiceDraft(5, "12", (scanned(),), (disc("1.24"),), (pay("55000"),)))
        self.assertEqual(mixed["old_gold_discount_range"], ["1.25", "5.00"])
        self.assertIn("OLD_GOLD_DISCOUNT_RANGE", [p["code"] for p in mixed["blocking_problems"]])
        for line in (disc("0.99", "10"), disc("5.01", "10"), disc("0.5", "10"), old(1, "10"),
                     old(1, "10", discount_type="AMOUNT", discount_value="500")):
            with self.subTest(line=line):
                draft = InvoiceDraft(5, "12", (), (line,), ())
                self.assertIn("OLD_GOLD_DISCOUNT_RANGE",
                              [p["code"] for p in self.service.preview(draft)["blocking_problems"]])
                self.assertRefused("OLD_GOLD_DISCOUNT_RANGE", draft)

    def test_changed_discount_is_reviewed_and_the_same_price_keeps_the_same_average(self):
        # For the same final sale price the discount only moves the money, never the making.
        at_150 = self.service.preview(InvoiceDraft(5, "12", (scanned(),), (disc("1.50"),), (pay("55000"),)))
        at_125 = self.service.preview(InvoiceDraft(5, "12", (scanned(),), (disc("1.25"),), (pay("55310.35"),)))
        self.assertEqual((at_150["effective_sale_total"], at_150["average_making_per_gram"]), ("67277.90", "175.42"))
        self.assertEqual((at_125["effective_sale_total"], at_125["average_making_per_gram"]), ("67277.90", "175.42"))
        self.finalize(InvoiceDraft(5, "12", (scanned(),), (disc("2"),), (pay("55000"),)))
        self.assertEqual([r["review_type"] for r in self.store.reviews], ["OLD_GOLD_DISCOUNT"])

    # ---- 14–20: every payment posts once to its real ledger
    def test_each_method_posts_to_its_real_ledger_in_both_directions(self):
        cases = (("PHYSICAL_CASH", None, "cash", None), ("MOBILE_WALLET", "9", "bank", 9),
                 ("INSTAPAY", "7", "bank", 7), ("BANK_TRANSFER", "7", "bank", 7))
        for method, account, ledger, account_id in cases:
            for draft, delta in ((InvoiceDraft(5, "12", (scanned(),), (), (pay("67011", method, account),)),
                                  D("67011.00")),
                                 (InvoiceDraft(5, "12", (scanned(),), (disc(),), (pay("55000", method, account),)),
                                  D("-55000.00"))):
                with self.subTest(method=method, delta=delta):
                    self.store.cash_movements.clear()
                    self.store.bank_movements.clear()
                    self.finalize(draft)
                    self.restock()
                    rows = self.store.cash_movements if ledger == "cash" else self.store.bank_movements
                    other = self.store.bank_movements if ledger == "cash" else self.store.cash_movements
                    self.assertEqual([r["amount_delta"] for r in rows], [delta])
                    self.assertEqual(other, [])                                           # no double posting
                    self.assertEqual(rows[0]["invoice_payment_id"], str(400 + len(self.store.payments)))
                    if account_id:
                        self.assertEqual(rows[0]["bank_account_id"], account_id)

    def test_accounts_must_be_real_active_and_of_the_right_type(self):
        def draft(method, account=None):
            return InvoiceDraft(5, "12", (scanned(),), (), (pay("67011", method, account),))
        for method in ("MOBILE_WALLET", "INSTAPAY", "BANK_TRANSFER"):
            with self.subTest(method=method):
                self.assertRefused("BANK_ACCOUNT_REQUIRED", draft(method))
                self.assertRefused("BANK_ACCOUNT_INVALID", draft(method, "8"))            # inactive
                self.assertRefused("BANK_ACCOUNT_INVALID", draft(method, "99"))           # unknown
        self.assertRefused("BANK_ACCOUNT_TYPE", draft("INSTAPAY", "9"))                   # a wallet, not a bank
        self.assertRefused("BANK_ACCOUNT_TYPE", draft("MOBILE_WALLET", "7"))              # a bank, not a wallet
        self.assertRefused("BANK_ACCOUNT_NOT_EXPECTED", draft("PHYSICAL_CASH", "7"))

    def test_a_method_without_any_active_account_is_refused_with_a_clear_message(self):
        def draft(method):
            return InvoiceDraft(5, "12", (scanned(),), (), (pay("67011", method),))
        wallets = {k: v for k, v in self.store.bank_accounts.items() if v.get("account_type") == "MOBILE_WALLET"}
        banks = {k: v for k, v in self.store.bank_accounts.items() if v.get("account_type") != "MOBILE_WALLET"}
        for key in wallets:
            self.store.bank_accounts.pop(key)
        self.assertRefused("PAYMENT_ACCOUNT_MISSING", draft("MOBILE_WALLET"))
        with self.assertRaises(InvoiceError) as refused:
            self.finalize(draft("MOBILE_WALLET"))
        self.assertIn("لا توجد محفظة مفعلة", str(refused.exception))
        self.assertRefused("BANK_ACCOUNT_REQUIRED", draft("INSTAPAY"))               # a bank exists: choose it
        for key in banks:
            self.store.bank_accounts.pop(key)
        for method in ("INSTAPAY", "BANK_TRANSFER"):
            with self.subTest(method=method):
                self.assertRefused("PAYMENT_ACCOUNT_MISSING", draft(method))
        self.assertEqual((self.store.payments, self.store.cash_movements, self.store.bank_movements), ([], [], []))

    def test_money_with_no_real_ledger_is_refused(self):
        self.store.methods["MOBILE_WALLET"] = (5, 0, 0)                  # before 017: no account behind it
        self.assertRefused("PAYMENT_ACCOUNT_NOT_CONFIGURED",
                           InvoiceDraft(5, "12", (scanned(),), (), (pay("67011", "MOBILE_WALLET"),)))
        self.store.cash_accounts.clear()
        self.assertRefused("CASH_ACCOUNT_UNRESOLVED", InvoiceDraft(5, "12", (scanned(),), (), (pay("67011"),)))

    def test_reference_lists_offer_methods_with_their_real_accounts(self):
        lists = self.service.reference_lists()
        self.assertEqual([(m["code"], m["account_type"]) for m in lists["payment_methods"]],
                         [("PHYSICAL_CASH", None), ("MOBILE_WALLET", "MOBILE_WALLET"), ("INSTAPAY", "BANK"),
                          ("BANK_TRANSFER", "BANK")])
        self.assertEqual([(a["id"], a["account_type"]) for a in lists["bank_accounts"]],
                         [("7", "BANK"), ("9", "MOBILE_WALLET")])

    # ---- making review re-runs on the derived price
    def test_making_review_follows_the_money(self):
        self.store.items["100"].update(category_id=1)
        self.store.categories[1].update(sale_making_min_per_gram=D("150.00"))
        within = self.service.preview(InvoiceDraft(5, "12", (scanned(),), (), (pay("67011"),)))
        below = self.service.preview(InvoiceDraft(5, "12", (scanned(),), (), (pay("67003"),)))
        self.assertEqual((within["making_review_status"], below["making_review_status"]),
                         ("WITHIN_RANGE", "OUT_OF_RANGE"))
        self.store.categories[1].update(sale_making_min_per_gram=None)
        self.assertEqual(self.service.preview(InvoiceDraft(5, "12", (scanned(),), (), (pay("67011"),)))
                         ["making_review_status"], "NOT_CONFIGURED")
        self.store.items["100"].update(category_id=None)
        preview = self.service.preview(InvoiceDraft(5, "12", (scanned(),), (), (pay("67011"),)))
        self.assertEqual(preview["making_groups"][0]["status"], "NOT_CONFIGURED")


if __name__ == "__main__":
    unittest.main()
