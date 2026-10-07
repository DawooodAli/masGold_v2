"""Return V2 (owner decisions 2026-10-03, migration 033): historical refund, one EGP-5 rounding, Return after an
Exchange (the replacement the customer holds now), the returned piece straight into a Scrap hold, the manager's
two decisions and reversal per hold state. In-memory stores only (no database)."""
from datetime import timedelta
from decimal import Decimal as D
import unittest

from services.correction_service import CorrectionError
from services.management_review_service import InspectionError, ManagementReviewService, with_scrap_story
from services.return_service import ReturnError
from services.return_valuation import ChainResolver, allocate_by_weight, f7_historical_basis
from tests import test_correction_service as cx
from tests import test_exchange_service as ex
from tests import test_return_service as rx


# ----------------------------------------------------------------------------- pure valuation
class ValuationRuleTests(unittest.TestCase):
    def test_f7_partial_share_and_exact_remainder(self):
        self.assertEqual(f7_historical_basis("100.00", "10.000", "0", "0", "3.333"), D("33.33"))
        self.assertEqual(f7_historical_basis("100.00", "10.000", "6.666", "66.66", "3.334"), D("33.34"))
        with self.assertRaises(ValueError):
            f7_historical_basis("100.00", "10.000", "9.000", "90.00", "1.001")

    def test_weight_allocation_adds_up_exactly_in_stored_order(self):
        parts = allocate_by_weight(D("100.00"), [("a", D("1")), ("b", D("1")), ("c", D("1"))])
        self.assertEqual(parts, {"a": D("33.33"), "b": D("33.33"), "c": D("33.34")})
        self.assertEqual(sum(parts.values()), D("100.00"))

    def test_one_for_one_exchange_both_directions(self):
        for signed, expected in (("400", "23400.00"), ("-400", "22600.00")):
            resolver = ChainResolver({"1": {"signed_amount": D(signed), "surrendered": [("INVOICE_LINE", "A")],
                                            "replacements": [("B", D("5.000"))]}}, {"A": D("23000.00")}, {"B": "1"})
            self.assertEqual(resolver.value(("LIFECYCLE_LINE", "B")), D(expected))

    def test_chain_a_b_c_resolves_recursively(self):
        resolver = ChainResolver(
            {"1": {"signed_amount": D("400"), "surrendered": [("INVOICE_LINE", "A")], "replacements": [("B", D("5"))]},
             "2": {"signed_amount": D("-150"), "surrendered": [("LIFECYCLE_LINE", "B")], "replacements": [("C", D("4"))]}},
            {"A": D("23000.00")}, {"B": "1", "C": "2"})
        self.assertEqual(resolver.value(("LIFECYCLE_LINE", "C")), D("23250.00"))   # 23,000 + 400 − 150

    def test_multi_replacement_pool_includes_unidentified_lines(self):
        resolver = ChainResolver({"1": {"signed_amount": D("400"), "surrendered": [("INVOICE_LINE", "A")],
                                        "replacements": [("B", D("4.000")), ("F7", D("6.000"))]}},
                                 {"A": D("37416.00")}, {"B": "1", "F7": "1"})
        self.assertEqual(resolver.value(("LIFECYCLE_LINE", "B")), D("15126.40"))        # 37,816 × 4 / 10
        self.assertEqual(resolver.value(("LIFECYCLE_LINE", "F7")), D("22689.60"))       # the remainder

    def test_a_lifecycle_loop_is_refused(self):
        resolver = ChainResolver({"1": {"signed_amount": D("0"), "surrendered": [("LIFECYCLE_LINE", "B")],
                                        "replacements": [("B", D("1"))]}}, {}, {"B": "1"})
        with self.assertRaises(ValueError):
            resolver.value(("LIFECYCLE_LINE", "B"))


# ----------------------------------------------------------------------------- service (Return store)
class ReturnV2Case(rx.ReturnTestCase):
    def exchange(self, signed="400.00", hours=48, replacements=(("91", "300", "6.000", "SCAN"),)):
        """EX000009 on invoice 500 took line 7 (item 203); the customer received the replacement pieces."""
        self.store.items["203"]["inventory_status"] = "CONVERTED_TO_OLD_GOLD"       # the surrendered piece
        lines = [{"lifecycle_line_id": "90", "line_role": "RETURNED", "line_number": 1, "source_invoice_line_id": "7",
                  "inventory_item_id": "203", "karat_id": 1, "karat_value": 21, "weight_grams": D("6.000"),
                  "item_name_snapshot": "خاتم", "barcode_snapshot": "B203", "identification_method": None}]
        for number, (line_id, item_id, weight, method) in enumerate(replacements, 2):
            if item_id:
                self.store.items[item_id] = {"inventory_item_id": item_id, "inventory_status": "SOLD",
                                             "weight_grams": D(weight), "stock_classification": "NEW_STOCK"}
            lines.append({"lifecycle_line_id": line_id, "line_role": "REPLACEMENT", "line_number": number,
                          "source_invoice_line_id": None, "inventory_item_id": item_id, "karat_id": 1,
                          "karat_value": 21, "weight_grams": D(weight), "item_name_snapshot": "دبلة",
                          "barcode_snapshot": None if item_id is None else f"B{item_id}",
                          "item_type_name": "دبلة", "identification_method": method})
        self.store.lineage = [{"lifecycle_id": "9", "invoice_id": "500", "signed_amount": D(signed),
                               "occurred_at": rx.T0 + timedelta(hours=hours), "lines": lines}]

    def rows(self):
        return {r["line_id"]: r for r in self.service.context("500")["lines"]}


class PinnedOwnerCaseTests(ReturnV2Case):
    def test_invoice_26100303_full_return_refunds_23000(self):
        """21K 3.500 g sold at 6,136 + making 1,524 = 23,000.00; today's SHOP_BUY 6,111 must not matter."""
        self.store.invoices["600"] = dict(self.store.invoices["501"], invoice_id="600", invoice_number="26100303")
        self.store.lines["600"] = [rx.line("60", 1, "SELL_INVENTORY", 1, "3.500", "260", "1524.00")]
        self.store.lines["600"][0]["line_total"] = D("23000.00")
        self.store.items["260"] = {"inventory_item_id": "260", "inventory_status": "SOLD", "weight_grams": D("3.500"),
                                   "stock_classification": "NEW_STOCK"}
        self.prices.prices = {**rx.PRICES, 1: (D("6111.0000"), D("6136.0000"))}
        p = self.service.preview(self.draft((("60", None),), invoice="600"))
        self.assertEqual((p["valuation_mode"], p["refund_basis"], p["rounding_adjustment"], p["total_amount"]),
                         ("HISTORICAL", "23000.00", "0.00", "23000.00"))
        self.assertNotEqual(p["total_amount"], "22912.50")                    # the old (wrong) result
        result = self.finalize(self.draft((("60", None),), invoice="600", payments=(rx.cash("23000.00"),)))
        self.assertEqual(result["total_amount"], "23000.00")


class ReturnAfterExchangeTests(ReturnV2Case):
    def test_the_original_is_history_and_the_replacement_is_the_current_piece(self):
        self.exchange()
        rows = self.rows()
        self.assertEqual((rows["7"]["returnable"], rows["7"]["reason"], rows["7"]["exchange_code"]),
                         (False, "تم استبدالها عبر EX000009", "EX000009"))
        current = rows["X91"]
        self.assertEqual((current["current_piece"], current["returnable"], current["barcode"],
                          current["inventory_item_id"], current["exchange_code"], current["karat"],
                          current["sold_grams"]), (True, True, "B300", "300", "EX000009", 21, "6.000"))
        self.assertRefused("ALREADY_EXCHANGED", lambda: self.finalize(self.full_cash((("7", None),))))

    def test_replacement_value_is_original_plus_the_exchange_amount_and_its_own_window(self):
        self.exchange(signed="400.00", hours=48)
        self.at(hours=100)              # the invoice is 100 h old; the replacement only 52 h → still HISTORICAL
        p = self.service.preview(self.draft((("X91", None),)))
        # line 7: 6 g × 6,136 + 600 = 37,416.00; + 400 = 37,816.00 → EGP 5: 37,815.00
        self.assertEqual((p["valuation_mode"], p["refund_basis"], p["rounding_adjustment"], p["total_amount"]),
                         ("HISTORICAL", "37816.00", "-1.00", "37815.00"))
        self.finalize(self.draft((("X91", None),), payments=(rx.cash("37815.00"),)))
        line = self.store.return_lines[0]
        self.assertEqual((line["lifecycle_line_id"], line["historical_line_value"], line["inventory_item_id"]),
                         ("91", D("37816.00"), "300"))
        self.assertEqual((self.store.items["300"]["inventory_status"], self.store.holds[0]["item"]),
                         ("CONVERTED_TO_OLD_GOLD", "300"))
        self.assertRefused("ALREADY_RETURNED", lambda: self.finalize(self.full_cash((("X91", None),))))

    def test_shop_paid_exchange_lowers_the_value(self):
        self.exchange(signed="-400.00")
        self.assertEqual(self.service.preview(self.draft((("X91", None),)))["refund_basis"], "37016.00")

    def test_after_the_replacement_window_current_buy_with_approval(self):
        self.exchange(hours=1)
        self.at(hours=80)
        p = self.service.preview(self.draft((("X91", None),)))
        self.assertEqual((p["valuation_mode"], p["total_amount"]), ("CURRENT_BUY", "37500.00"))   # 6 × 6,250
        self.assertIn("APPROVAL_REQUIRED", [x["code"] for x in p["blocking_problems"]])

    def test_multi_replacement_allocation_and_unidentified_line_is_not_returnable(self):
        self.exchange(replacements=(("91", "300", "4.000", "SCAN"), ("92", None, "6.000", "OLD_GOLD_STOCK")))
        rows = self.rows()
        self.assertEqual((rows["X92"]["returnable"], rows["X92"]["reason"]),
                         (False, "البديل غير محدد في المخزون (يدوي / من الكسر)؛ لا يُرد كقطعة."))
        self.assertEqual(self.service.preview(self.draft((("X91", None),)))["refund_basis"], "15126.40")
        self.assertRefused("REPLACEMENT_NOT_IDENTIFIED", lambda: self.finalize(self.full_cash((("X92", None),))))

    def test_the_whole_pool_not_only_the_difference_is_split_by_weight(self):
        """§12 pin: pool = surrendered historical value (line 7: 37,416.00) + signed final Exchange amount (+400)
        = 37,816.00, split by weight over ALL replacement lines in stored order (3 g, 2 g, 1 g); the last takes
        the remainder; the allocations add up to the pool exactly. Splitting only the 400 would give 200/133.33/…"""
        self.exchange(signed="400.00", replacements=(("91", "300", "3.000", "SCAN"), ("92", "301", "2.000", "SEARCH"),
                                                     ("93", None, "1.000", "OLD_GOLD_STOCK")))
        values = {key: D(self.service.preview(self.draft(((key, None),)))["refund_basis"]) for key in ("X91", "X92")}
        self.assertEqual(values, {"X91": D("18908.00"), "X92": D("12605.33")})     # 37,816 × 3/6, × 2/6
        from services.return_service import return_sources
        sources = return_sources(self.store.invoices["500"], rx.ReturnRepo(type("C", (), {"store": self.store})())
                                 .source_lines("500"), rx.ReturnRepo(type("C", (), {"store": self.store})())
                                 .exchange_lineage("500"))
        allocations = [r["line_total"] for r in sources if r.get("kind") == "REPLACEMENT"]
        self.assertEqual(allocations, [D("18908.00"), D("12605.33"), D("6302.67")])   # the last: the remainder
        self.assertEqual(sum(allocations), D("37416.00") + D("400.00"))               # == the whole pool

    def test_mixed_windows_in_one_return_are_refused(self):
        self.exchange(hours=48)
        self.at(hours=100)
        with self.assertRaises(ReturnError) as refused:
            self.service.preview(self.draft((("1", None), ("X91", None))))
        self.assertEqual(refused.exception.code, "MIXED_RETURN_WINDOWS")


class ReturnScrapHoldTests(ReturnV2Case):
    def test_identified_piece_enters_scrap_held_atomically(self):
        physical = self.store.old_gold_balance(1)
        self.finalize(self.full_cash())
        self.assertEqual(self.store.items["200"]["inventory_status"], "CONVERTED_TO_OLD_GOLD")
        self.assertEqual(self.store.old_gold_balance(1) - physical, D("10.000"))
        (hold,) = self.store.holds
        self.assertEqual((hold["item"], hold["weight"], hold["status"], hold["return_line"]),
                         ("200", D("10.000"), "HELD", self.store.return_lines[0]["line_id"]))
        (review,) = [r for r in self.store.reviews if r["type"] == "RETURNED_ITEM_INSPECTION"]
        self.assertEqual((review["details"]["stored_barcode"], review["details"]["inventory_item_id"]), ("B200", "200"))
        self.assertIn("محوّلة إلى الكسر", review["summary"])

    def test_any_failure_leaves_nothing(self):
        for point in ("mark_item_returned_to_scrap", "add_return_scrap_in", "open_inspection", "insert_scrap_hold"):
            with self.subTest(point=point):
                rx.ReturnRepo.fail_on = point
                before = self.store.tables()
                with self.assertRaises(RuntimeError):
                    self.finalize(self.full_cash())
                self.assertEqual(self.store.tables(), before)
        rx.ReturnRepo.fail_on = None


# ----------------------------------------------------------------------------- manager decisions (Return hold)
class _ReviewRepo:
    """ManagementReviewRepository over one held Return piece (the 033 contract, enforced as MySQL would)."""
    state = None

    def __init__(self, connection):
        self.s = _ReviewRepo.state

    def lock_inspection(self, review_id):
        s = self.s
        return {"review_id": "901", "review_status": s["review"], "lifecycle_line_id": None, "return_line_id": "101",
                "hold_id": "1", "inventory_item_id": "200", "exchange_code": "RT000001", "source_label": "مرتجع RT000001",
                "invoice_id": "500", "stored_barcode": "B200", "karat_value": 21}

    def lock_item(self, item_id):
        return {"inventory_item_id": "200", "karat_id": 1, "weight_grams": D("10.000"),
                "stock_classification": "NEW_STOCK", "inventory_status": self.s["item"]}

    def lock_old_gold_balance(self, karat_id):
        return self.s["physical"]

    def lock_hold(self, review_id):
        assert review_id == "901"
        return {"hold_id": "1", "return_line_id": "101", "lifecycle_line_id": None, "karat_id": 1,
                "weight_grams": D("10.000"), "in_movement_id": "7", "hold_status": self.s["hold"]}

    def db_now(self):
        return ex.NOW

    def add_old_gold_out_to_stock(self, *, movement_code, weight_grams, **kwargs):
        self.s["movements"].append((movement_code, -weight_grams))
        self.s["physical"] -= weight_grams
        return str(len(self.s["movements"]))

    def set_item_status(self, *, from_status, to_status, **kwargs):
        assert self.s["item"] == from_status
        self.s["item"] = to_status

    def add_inventory_movement(self, *, movement_code, from_status, to_status, **kwargs):
        self.s["inventory"].append((movement_code, from_status, to_status))
        return "1"

    def close_hold(self, *, hold_status, out_movement_id, **kwargs):
        if self.s["hold"] != "HELD":
            return False
        assert (hold_status == "RETURNED_TO_STOCK") == (out_movement_id is not None)
        self.s["hold"] = hold_status
        return True

    def resolve_inspection(self, **kwargs):
        if self.s["review"] != "OPEN":
            return False
        self.s["review"] = "RESOLVED"
        return True


class ReturnHoldDecisionTests(unittest.TestCase):
    def setUp(self):
        from contextlib import contextmanager
        from app.session import AppSession
        from services.authentication_service import AuthenticationResult, _VERIFIED
        import copy
        _ReviewRepo.state = {"review": "OPEN", "item": "CONVERTED_TO_OLD_GOLD", "hold": "HELD",
                             "physical": D("10.000"), "movements": [], "inventory": []}
        permissions = frozenset({"APP_LOGIN", "MANAGEMENT_REVIEW"})
        session = AppSession()
        session._establish(AuthenticationResult(3, None, "المدير", permissions, _VERIFIED), int(session.generation))

        @contextmanager
        def transaction():
            saved = copy.deepcopy(_ReviewRepo.state)
            try:
                yield type("C", (), {"cursor": lambda self: type("K", (), {"execute": lambda *a: None,
                                                                          "close": lambda *a: None})()})()
            except BaseException:
                _ReviewRepo.state.clear()
                _ReviewRepo.state.update(saved)
                raise
        self.service = ManagementReviewService(session, transaction_factory=transaction, repository=_ReviewRepo,
                                               authorization=ex.Authorization(session, permissions))

    def test_return_to_stock_uses_the_return_movement_and_restores_the_same_item(self):
        result = self.service.return_to_stock("901")
        s = _ReviewRepo.state
        self.assertEqual(s["movements"], [("RETURN_TO_STOCK_OUT", D("-10.000"))])     # never the Exchange code
        self.assertEqual((s["item"], s["hold"], s["review"], s["physical"]),
                         ("IN_STOCK", "RETURNED_TO_STOCK", "RESOLVED", D("0.000")))
        self.assertEqual(s["inventory"], [("RETURNED_ITEM_RESTOCKED", "CONVERTED_TO_OLD_GOLD", "IN_STOCK")])
        self.assertEqual((result["inventory_item_id"], result["stored_barcode"]), ("200", "B200"))

    def test_commit_posts_nothing_and_a_second_decision_is_refused(self):
        self.service.convert_to_old_gold("901")
        s = _ReviewRepo.state
        self.assertEqual((s["movements"], s["physical"], s["hold"], s["item"]),
                         ([], D("10.000"), "COMMITTED", "CONVERTED_TO_OLD_GOLD"))
        for decide in (self.service.return_to_stock, self.service.convert_to_old_gold):
            with self.assertRaises(InspectionError):
                decide("901")
        self.assertEqual((s["movements"], s["hold"]), ([], "COMMITTED"))

    def test_return_piece_history_names_the_return_and_the_scrap_entry(self):
        story = with_scrap_story({"hold_id": "1", "hold_status": "HELD", "karat_value": 21,
                                  "hold_weight_grams": D("3.500"), "source": "RETURN", "source_code": "RT000003",
                                  "invoice_number": "26100303", "invoice_finalized_at": ex.NOW,
                                  "exchanged_at": ex.NOW, "replacement_exchange_id": "9"})
        self.assertEqual([e["title"] for e in story["timeline"]],
                         ["بيع بالفاتورة", "حصل عليها العميل بالاستبدال", "مرتجع", "تم تحويل القطعة إلى الكسر"])
        self.assertEqual(story["timeline"][1]["detail"], "EX000009")


# ----------------------------------------------------------------------------- reversal per hold state (RV of a Return)
class ReturnReversalByHoldStateTests(cx.CorrectionTestCase):
    """Return 800 returned item 203 (21K 10.000 g). After 033 that piece is in Scrap with a hold."""

    def held(self, status, review_status="OPEN"):
        self.store.items["203"]["inventory_status"] = "IN_STOCK" if status == "RETURNED_TO_STOCK" else "CONVERTED_TO_OLD_GOLD"
        self.store.old_gold.append((1, D("10.000"), "RETURN_SCRAP_IN"))
        if status == "RETURNED_TO_STOCK":
            self.store.old_gold.append((1, D("-10.000"), "RETURN_TO_STOCK_OUT"))
        self.store.holds = [{"hold_id": "1", "line": None, "return_line": "8001", "item": "203", "karat_id": 1,
                             "weight": D("10.000"), "status": status, "out": None}]
        self.store.reviews[1].update(status=review_status, code=None if review_status == "OPEN" else
                                     ("RETURN_TO_STOCK" if status == "RETURNED_TO_STOCK" else "CONVERT_TO_OLD_GOLD"))

    def test_held_piece_reverses_with_its_exact_weight(self):
        self.held("HELD")
        physical = self.store.old_gold_balance()
        self.service.reverse("RETURN", "800", "خطأ في المرتجع")
        self.assertEqual(self.store.items["203"]["inventory_status"], "SOLD")
        self.assertIn(("203", "REVERSAL_RETURN_TO_SOLD", "CONVERTED_TO_OLD_GOLD", "SOLD"), self.store.inv_movements)
        self.assertEqual([g for g in self.store.old_gold if g[2] == "REVERSAL_RETURN_SCRAP_OUT"],
                         [(1, D("-10.000"), "REVERSAL_RETURN_SCRAP_OUT")])
        self.assertEqual(self.store.old_gold_balance() - physical, D("-10.000"))
        self.assertEqual(self.store.holds[0]["status"], "REVERSED")
        self.assertEqual(self.store.reviews[1]["status"], "DISMISSED")
        self.assertEqual(self.store.money[-1][:3], ("CASH", 1, D("12000.00")))         # the refund comes back once

    def test_committed_piece_blocks_the_reversal(self):
        self.held("COMMITTED", "RESOLVED")
        error = self.assertRefused("RETURNED_ITEM_COMMITTED_TO_SCRAP",
                                   lambda: self.service.reverse("RETURN", "800", "خطأ"))
        self.assertIn("المرتجع", str(error))

    def test_restocked_piece_blocks_the_reversal(self):
        self.held("RETURNED_TO_STOCK", "RESOLVED")
        self.assertRefused("RETURNED_ITEM_RESTOCKED", lambda: self.service.reverse("RETURN", "800", "خطأ"))
        self.assertEqual(self.store.items["203"]["inventory_status"], "IN_STOCK")

    def test_a_reversed_return_cannot_be_reversed_again(self):
        self.held("HELD")
        self.service.reverse("RETURN", "800", "خطأ")
        self.assertRefused("ALREADY_REVERSED", lambda: self.service.reverse("RETURN", "800", "خطأ"))
        self.assertEqual(len([g for g in self.store.old_gold if g[2] == "REVERSAL_RETURN_SCRAP_OUT"]), 1)


if __name__ == "__main__":
    unittest.main()
