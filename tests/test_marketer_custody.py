"""Marketer (مسوقجي) custody account (055): real MarketerCustodyService and CorrectionService over the in-memory transactional database of
tests/market_trip_fakes.py + tests/marketer_fakes.py (a failed transaction restores every table; the 055 CHECK / unique / composite-foreign-key constraints are
mirrored as asserts). NOT real-MySQL tests; no database is touched."""
import copy
from decimal import Decimal as D
import unittest
from unittest.mock import patch

from services.correction_service import CorrectionError
from services.market_trip_service import MarketTripService, TripError
from services.marketer_custody_service import MarketerCustodyService
from services.supplier_rules import SupplierRuleError
from tests.market_trip_fakes import (AccountsRepo, MANAGER, OWNER, ScrapRepo, SettlementRepo, build, session_for)
from tests.marketer_fakes import MarketerRepo, prepare

TODAY = "2026-10-17"
MARKETER, DUAL, INACTIVE, SUPPLIER24, PLAIN_SUPPLIER = "20", "21", "22", "23", "10"
REFERENCE = {21: D("6000.0000")}


def mkey(n):
    return "77777777-7777-4777-8777-%012d" % n


def line(weight="5", price="30000"):
    return {"weight": weight, "price": price}


class MarketerCase(unittest.TestCase):
    def setUp(self):
        self.db, self.session, self.authorization, _, self.corrections = build()
        prepare(self.db)
        self.n = 0
        for target, value in (("repositories.marketer_custody_repository.MarketerCustodyRepository", MarketerRepo),
                              ("repositories.supplier_gold_settlement_repository.SupplierGoldSettlementRepository", SettlementRepo),
                              ("repositories.financial_account_repository.FinancialAccountRepository", AccountsRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)
        self.service = self.make_service(self.session, self.authorization)

    def make_service(self, session, authorization):
        return MarketerCustodyService(session, transaction_factory=self.db.transaction, repository=MarketerRepo, accounts=AccountsRepo, scrap=ScrapRepo,
                                      settlements=SettlementRepo, authorization=authorization, corrections=self.corrections,
                                      price_reference=lambda: dict(REFERENCE))

    # ------------------------------------------------------------ helpers
    def key(self):
        self.n += 1
        return mkey(self.n)

    def hand(self, amount="100000", party=MARKETER, key=None):
        return self.service.cash_handover(party, amount, TODAY, "مرجع", "ملاحظة", key or self.key())

    def back(self, amount="1000", party=MARKETER, key=None):
        return self.service.cash_return(party, amount, TODAY, "", "", key or self.key())

    def buy(self, lines=None, party=MARKETER, vendor="", name="تاجر السوق", key=None, ack=False):
        return self.service.purchase(party, [line()] if lines is None else lines, vendor, name, TODAY, "", "", key or self.key(), ack)

    def sell(self, weight="2", total="12000", party=MARKETER, buyer="", name="مشتري", mode="TOTAL", key=None, ack=False):
        return self.service.sale(party, weight, mode, total, buyer, name, TODAY, "", "", key or self.key(), ack)

    def gold_back(self, weight="1", party=MARKETER, key=None):
        return self.service.gold_return(party, weight, TODAY, "", "", key or self.key())

    def settle(self, weight="1", supplier=SUPPLIER24, party=MARKETER, confirmed=False, key=None):
        return self.service.settle_supplier(party, supplier, weight, confirmed, TODAY, "", "", key or self.key())

    def cash(self, party=MARKETER):
        return MarketerRepo(self.db).cash_balance(party)

    def gold(self, party=MARKETER):
        return MarketerRepo(self.db).gold_balance(party)

    def state(self):
        return copy.deepcopy({k: v for k, v in vars(self.db).items() if k not in ("fail_at", "transactions", "lock_log")})

    def supplier_ledgers(self):
        db = self.db
        return copy.deepcopy((db.sup_gold, db.sm_making, db.sm_payments, db.settlements, db.settlement_legs, db.supplier_links))

    def refused(self, code, call, error=TripError):
        before = self.state()
        with self.assertRaises(error) as context:
            call()
        self.assertEqual(getattr(context.exception, "code", None), code)
        self.assertEqual(self.state(), before, "a refused request left a trace")
        return context.exception

    def reverse(self, done, reason="تصحيح", service=None):
        return (service or self.service).reverse(done["id"], reason)


class CashTests(MarketerCase):
    def test_a_handover_moves_cash_from_the_main_safe_to_the_marketer_and_replays_exactly(self):
        safe = self.db.cash()
        key = self.key()
        done = self.hand("25000.50", key=key)
        self.assertEqual((done["code"], done["kind"], done["cash"], done["duplicate"]), ("MH000001", "CASH_HANDOVER", "25000.50", False))
        self.assertEqual((self.db.cash(), self.cash()), (safe - D("25000.50"), D("25000.50")))
        (move,) = [m for m in self.db.cash_moves if m["reference_type"] == "MARKETER_DOCUMENT"]
        self.assertEqual((move["amount_delta"], move["type_code"], move["reference_id"]), (D("-25000.50"), "MARKETER_CASH_HANDOVER", "1"))
        self.assertEqual(self.db.mk_links[1]["shop_cash_movement_id"], move["id"])
        again = self.hand("25000.50", key=key)
        self.assertEqual((again["duplicate"], again["id"]), (True, done["id"]))
        self.assertEqual(len(self.db.mk_docs), 1)
        self.refused("REQUEST_CONFLICT", lambda: self.hand("25000.51", key=key))
        self.refused("REQUEST_CONFLICT", lambda: self.hand("25000.50", party=DUAL, key=key))

    def test_a_handover_needs_an_active_marketer_and_a_valid_amount(self):
        for code, call in (("MARKETER_ROLE_REQUIRED", lambda: self.hand(party=SUPPLIER24)), ("MARKETER_INACTIVE", lambda: self.hand(party=INACTIVE)),
                           ("PARTY_NOT_FOUND", lambda: self.hand(party="999")), ("PARTY_NOT_FOUND", lambda: self.hand(party="")),
                           ("AMOUNT_INVALID", lambda: self.hand("0")), ("AMOUNT_INVALID", lambda: self.hand("-5")),
                           ("AMOUNT_INVALID", lambda: self.hand("10.005")), ("AMOUNT_INVALID", lambda: self.hand("")),
                           ("DATE_REQUIRED", lambda: self.service.cash_handover(MARKETER, "1", "", "", "", self.key())),
                           ("REQUEST_INVALID", lambda: self.hand(key="x"))):
            with self.subTest(code=code):
                self.refused(code, call)

    def test_a_return_is_capped_by_the_cash_held_and_an_inactive_marketer_may_still_return(self):
        self.hand("5000")
        self.refused("CASH_EXCEEDS_HELD", lambda: self.back("5000.01"))
        safe = self.db.cash()
        done = self.back("5000")
        self.assertEqual((done["code"], self.cash(), self.db.cash()), ("MR000002", D("0.00"), safe + D("5000")))
        self.hand("100", party=MARKETER)
        self.db.suppliers[20]["is_active"] = 0                                             # what an inactive marketer holds can still be returned
        self.back("100")
        self.assertEqual(self.cash(), D("0.00"))


class PurchaseTests(MarketerCase):
    def test_a_purchase_spends_marketer_cash_and_adds_21k_gold_without_any_other_effect(self):
        self.hand("100000")
        shop, scrap, ledgers = self.db.cash(), len(self.db.scrap), self.supplier_ledgers()
        done = self.buy([line("10.250", "61500.50"), line("4.000", "24000")])
        self.assertEqual((done["code"], done["kind"], done["cash"], done["weight"], done["counterparty_kind"]), ("MB000002", "PURCHASE", "85500.50", "14.250", "THIRD_PARTY"))
        self.assertEqual((self.cash(), self.gold()), (D("14499.50"), D("14.250")))
        self.assertEqual((self.db.cash(), len(self.db.scrap), self.supplier_ledgers()), (shop, scrap, ledgers))     # no Main Safe, Scrap or supplier movement
        document = self.db.mk_docs[2]
        self.assertEqual((document["karat_value"], document["counterparty_name"], document["cash_amount"]), (21, "تاجر السوق", D("85500.50")))
        self.assertEqual(sum((l["line_price"] for l in self.db.mk_lines), D("0")), document["cash_amount"])
        again = self.buy([line("10.250", "61500.50"), line("4.000", "24000")], key=document["request_key"])
        self.assertTrue(again["duplicate"])

    def test_the_vendor_is_mandatory_registered_parties_never_change_and_the_cash_cap_holds(self):
        self.hand("100000")
        for blank in ("", "   ", None):
            self.refused("COUNTERPARTY_REQUIRED", lambda b=blank: self.buy(name=b))
        self.refused("COUNTERPARTY_INACTIVE", lambda: self.buy(vendor=INACTIVE))
        self.refused("COUNTERPARTY_NOT_FOUND", lambda: self.buy(vendor="999"))
        self.refused("COUNTERPARTY_IS_HOLDER", lambda: self.buy(vendor=MARKETER))
        self.refused("PURCHASE_EXCEEDS_CASH", lambda: self.buy([line("20", "100000.01")], ack=True))
        ledgers = self.supplier_ledgers()
        registered = self.buy([line("1", "6000")], vendor=SUPPLIER24)
        self.assertEqual((registered["counterparty_kind"], registered["counterparty_name"]), ("REGISTERED_PARTY", "مورد 24"))
        self.assertEqual(self.supplier_ledgers(), ledgers)                                 # a supplier vendor's payables are never touched
        typed = self.buy([line("1", "6000")], name="  أحمَد   الصّاغة ")
        self.assertEqual(self.db.mk_docs[int(typed["id"])]["counterparty_key"], MarketTripService.vendor_name_key("احمد الصاغه"))

    def test_invalid_lines_are_refused_and_the_price_warning_needs_acknowledgement(self):
        self.hand("100000")
        for code, lines in (("LINES_REQUIRED", []), ("LINES_REQUIRED", [line("", "")]), ("WEIGHT_REQUIRED", [line("0", "10")]),
                            ("WEIGHT_INVALID", [line("1.2345", "10")]), ("PRICE_INVALID", [line("1", "")]), ("PRICE_INVALID", [line("1", "0")]),
                            ("PRICE_INVALID", [line("1", "10.005")])):
            with self.subTest(code=code):
                self.refused(code, lambda l=lines: self.buy(l))
        error = self.refused("PRICE_DEVIATION", lambda: self.buy([line("10", "40000")]))          # reference 60,000: -33.33 %
        self.assertIn("33.33", str(error))
        done = self.buy([line("10", "40000")], ack=True)
        self.assertEqual((done["price_warning_acknowledged"], done["deviation_percent"], done["reference_total"]), (True, "-33.33", "60000.00"))


class SaleAndReturnTests(MarketerCase):
    def setUp(self):
        super().setUp()
        self.hand("100000")
        self.buy([line("10", "60000")])                                                    # 10 g of 21K, cash left 40,000

    def test_a_sale_removes_gold_and_adds_cash_and_a_per_gram_price_rounds_once(self):
        ledgers, shop = self.supplier_ledgers(), self.db.cash()
        done = self.sell("2.5", "6000", mode="PER_GRAM")
        self.assertEqual((done["kind"], done["weight"], done["cash"], done["counterparty_name"]), ("SALE", "2.500", "15000.00", "مشتري"))
        self.assertEqual((self.gold(), self.cash()), (D("7.500"), D("55000.00")))
        self.assertEqual((self.db.cash(), self.supplier_ledgers()), (shop, ledgers))
        self.refused("SALE_EXCEEDS_GOLD", lambda: self.sell("7.501", "1", ack=True))
        self.refused("COUNTERPARTY_REQUIRED", lambda: self.sell(name=""))
        self.refused("COUNTERPARTY_IS_HOLDER", lambda: self.sell(buyer=MARKETER))
        self.refused("COUNTERPARTY_INACTIVE", lambda: self.sell(buyer=INACTIVE))
        self.refused("PRICE_DEVIATION", lambda: self.sell("2", "6000"))                    # 12,000 reference vs 6,000
        self.assertTrue(self.sell("2", "6000", ack=True)["price_warning_acknowledged"])
        for code, call in (("WEIGHT_REQUIRED", lambda: self.sell("0")), ("PRICE_INVALID", lambda: self.sell("1", "0")),
                           ("PRICE_BASIS_REQUIRED", lambda: self.sell(mode=""))):
            self.refused(code, call)

    def test_a_gold_return_puts_21k_grams_into_the_counted_scrap_never_above_what_is_held(self):
        scrap = self.db.physical(1)
        done = self.gold_back("4")
        self.assertEqual((done["code"], done["kind"], done["weight"]), ("MR000003", "GOLD_RETURN", "4.000"))
        self.assertEqual((self.gold(), self.db.physical(1)), (D("6.000"), scrap + D("4.000")))
        (move,) = [m for m in self.db.scrap if m["reference_type"] == "MARKETER_DOCUMENT"]
        self.assertEqual((move["karat_id"], move["type_code"]), (1, "MARKETER_GOLD_RETURN_IN"))              # always the 21K row
        self.refused("GOLD_EXCEEDS_HELD", lambda: self.gold_back("6.001"))
        self.refused("WEIGHT_REQUIRED", lambda: self.gold_back("0"))

    def test_an_inactive_marketer_may_sell_and_return_what_he_holds(self):
        self.db.suppliers[20]["is_active"] = 0
        self.sell("1", "6000")
        self.gold_back("1")
        self.refused("MARKETER_INACTIVE", lambda: self.buy([line("1", "6000")]))


class SettlementTests(MarketerCase):
    def setUp(self):
        super().setUp()
        self.hand("200000")
        self.buy([line("20", "120000")])

    def test_a_settlement_delivers_21k_grams_to_a_supplier_with_the_existing_conversion(self):
        before = self.supplier_ledgers()
        done = self.settle("10.500", SUPPLIER24)                                           # 10.5 x 21 / 24 = 9.1875
        self.assertEqual((done["delivered_weight"], done["credited_weight"], done["target_karat"], done["remaining"]), ("10.500", "9.188", 24, "9.500"))
        self.assertEqual(self.gold(), D("9.500"))
        self.assertEqual((len(self.db.sup_gold), len(self.db.settlements), len(self.db.settlement_legs)), (1, 1, 1))
        movement = self.db.sup_gold[0]
        self.assertEqual((movement["supplier_id"], movement["weight_delta"]), (23, D("-9.188")))
        header = next(iter(self.db.settlements.values()))
        self.assertEqual((header["source_kind"], header["delivered_karat_value"], header["holding_party_id"], header["lot_pool_id"], header["logo_pool_id"]),
                         ("HOLDING_MKT", 21, 20, None, None))
        self.assertEqual(self.db.settlement_legs[0]["source_kind"], "HOLDING_MKT")
        self.assertEqual((self.db.sm_making, self.db.sm_payments), ([], {}))                  # no making ledger, no cash
        self.assertEqual(before[0], [])
        again = self.settle("10.500", SUPPLIER24, key=header["request_key"])
        self.assertTrue(again["duplicate"])
        self.assertEqual(len(self.db.settlements), 1)

    def test_the_recipient_must_be_a_supplier_with_a_karat_and_the_gold_must_be_held(self):
        self.refused("SUPPLIER_NOT_FOUND", lambda: self.settle(supplier="999"))
        self.refused("SUPPLIER_NOT_FOUND", lambda: self.settle(supplier=""))
        self.refused("SUPPLIER_ROLE_REQUIRED", lambda: self.settle(supplier=INACTIVE), SupplierRuleError)      # an intermediary-only party has no gold account
        self.refused("GOLD_EXCEEDS_HELD", lambda: self.settle("20.001"))
        self.refused("MARKETER_ROLE_REQUIRED", lambda: self.settle(party=SUPPLIER24))
        self.assertEqual(self.db.sup_gold, [])

    def test_a_party_that_is_both_marketer_and_supplier_needs_the_explicit_self_confirmation(self):
        self.hand("100000", party=DUAL)
        self.buy([line("10", "60000")], party=DUAL)
        self.refused("SELF_SETTLEMENT_CONFIRMATION_REQUIRED", lambda: self.settle("2", DUAL, party=DUAL))
        done = self.settle("2", DUAL, party=DUAL, confirmed=True)                         # 18K supplier account: 2 x 21 / 18 = 2.3333
        self.assertEqual((done["credited_weight"], done["target_karat"]), ("2.333", 18))
        self.assertEqual(self.db.settlements[int(done["id"])]["self_settlement_confirmed"], 1)
        self.assertEqual((self.gold(DUAL), MarketerRepo(self.db).gold_balance(MARKETER)), (D("8.000"), D("20.000")))      # the other marketer is untouched


class IsolationTests(MarketerCase):
    def test_a_dual_role_party_keeps_two_unrelated_accounts_through_the_whole_cycle(self):
        before = self.supplier_ledgers()
        self.hand("100000", party=DUAL)
        self.buy([line("10", "60000")], party=DUAL)
        self.sell("3", "18000", party=DUAL)
        self.gold_back("2", party=DUAL)
        self.back("1000", party=DUAL)
        self.assertEqual(self.supplier_ledgers(), before)                                  # not one supplier row, in either direction
        self.assertEqual((self.cash(DUAL), self.gold(DUAL)), (D("40000.00") + D("18000.00") - D("1000.00"), D("5.000")))
        view = self.service.account(DUAL)
        self.assertEqual((view["party"]["is_supplier"], view["cash"], view["gold"]), (True, D("57000.00"), D("5.000")))
        self.assertNotIn("supplier_balance", view)                                         # no supplier figure and no total of the two worlds

    def test_a_supplier_that_is_not_a_marketer_cannot_hold_a_custody_account(self):
        for call in (lambda: self.hand(party=SUPPLIER24), lambda: self.buy(party=SUPPLIER24), lambda: self.sell(party=SUPPLIER24),
                     lambda: self.gold_back(party=SUPPLIER24)):
            self.refused("MARKETER_ROLE_REQUIRED", call)

    def test_the_only_bridge_is_the_explicit_settlement_and_it_never_nets(self):
        self.hand("100000")
        self.buy([line("10", "60000")])
        before = self.supplier_ledgers()
        done = self.settle("4", SUPPLIER24)
        self.assertEqual(len(self.db.sup_gold), 1)
        self.assertEqual(self.cash(), D("40000.00"))                                       # the marketer's cash never moved with the settlement
        self.assertEqual(self.gold(), D("6.000"))
        self.assertNotEqual(self.supplier_ledgers(), before)
        self.reverse_settlement(done)
        self.assertEqual((self.gold(), sum((m["weight_delta"] for m in self.db.sup_gold), D("0"))), (D("10.000"), D("0.000")))

    def reverse_settlement(self, done, reason="تسوية بالخطأ"):
        return self.service.reverse_settlement(done["id"], reason)


class ReversalTests(MarketerCase):
    def test_every_document_reverses_with_explicit_compensations_and_the_originals_stay_counted(self):
        handover = self.hand("100000")
        purchase = self.buy([line("10", "60000")])
        sale = self.sell("2", "12000")
        gold_return = self.gold_back("3")
        cash_return = self.back("5000")
        shop_cash, scrap = self.db.cash(), self.db.physical(1)
        originals = len(self.db.mk_cash) + len(self.db.mk_gold)
        # newest first: each reversal restores exactly its own effect
        self.reverse(cash_return)
        self.assertEqual((self.cash(), self.db.cash()), (D("100000") - D("60000") + D("12000"), shop_cash - D("5000")))
        self.reverse(gold_return)
        self.assertEqual((self.gold(), self.db.physical(1)), (D("8.000"), scrap - D("3.000")))
        self.reverse(sale)
        self.assertEqual((self.gold(), self.cash()), (D("10.000"), D("40000.00")))
        self.reverse(purchase)
        self.assertEqual((self.gold(), self.cash()), (D("0.000"), D("100000.00")))
        self.reverse(handover)
        self.assertEqual((self.cash(), self.db.cash()), (D("0.00"), D("50000.00")))
        self.assertEqual(len(self.db.mk_cash) + len(self.db.mk_gold), originals + 7)        # compensations are ADDED; nothing was removed or rewritten
        self.assertEqual(len(self.db.corrections), 5)
        self.assertEqual(self.db.mk_docs[int(purchase["id"])]["cash_amount"], D("60000.00"))
        for done in (handover, purchase, sale, gold_return, cash_return):
            self.refused("ALREADY_REVERSED", lambda d=done: self.reverse(d), CorrectionError)

    def test_a_handover_cannot_be_reversed_after_its_cash_was_spent_or_returned(self):
        handover = self.hand("100000")
        purchase = self.buy([line("10", "60000")])
        error = self.refused("DEPENDENT_CASH_USE_EXISTS", lambda: self.reverse(handover), CorrectionError)
        self.assertIn("اعكس", str(error))
        self.reverse(purchase)
        self.reverse(handover)

    def test_a_purchase_cannot_be_reversed_after_its_gold_was_sold_returned_or_delivered(self):
        self.hand("100000")
        purchase = self.buy([line("10", "60000")])
        sale = self.sell("4", "24000")
        self.refused("DEPENDENT_GOLD_USE_EXISTS", lambda: self.reverse(purchase), CorrectionError)
        settlement = self.settle("6", SUPPLIER24)
        self.refused("DEPENDENT_GOLD_USE_EXISTS", lambda: self.reverse(purchase), CorrectionError)
        self.service.reverse_settlement(settlement["id"], "تراجع")
        self.reverse(sale)
        self.reverse(purchase)

    def test_a_sale_cannot_be_reversed_after_its_cash_was_used(self):
        self.hand("10000")
        self.buy([line("10", "10000")], ack=True)                                           # all the cash is spent
        sale = self.sell("5", "30000", ack=True)
        self.back("30000")                                                                  # the proceeds go back to the Main Safe
        self.refused("DEPENDENT_CASH_USE_EXISTS", lambda: self.reverse(sale), CorrectionError)

    def test_a_gold_return_reverses_only_while_the_scrap_is_still_there(self):
        self.hand("100000")
        self.buy([line("10", "60000")])
        done = self.gold_back("4")
        scrap = self.db.physical(1)
        self.reverse(done)
        self.assertEqual((self.db.physical(1), self.gold()), (scrap - D("4.000"), D("10.000")))

    def test_the_reversal_needs_the_reversal_rights_and_a_reason(self):
        self.hand("100")
        done = self.back("50")
        manager = session_for(MANAGER)
        weak = self.make_service(manager, type(self.authorization)(manager))
        with self.assertRaises(PermissionError):
            weak.reverse(done["id"], "x")
        with self.assertRaises(Exception):
            self.service.reverse(done["id"], "")
        self.assertEqual(self.cash(), D("50.00"))

    def test_a_settlement_reverses_with_the_stored_amounts_and_both_ledgers_return_together(self):
        self.hand("200000")
        self.buy([line("20", "120000")])
        done = self.settle("10.500", SUPPLIER24)
        stored = self.db.sup_gold[0]["weight_delta"]
        self.service.reverse_settlement(done["id"], "خطأ")
        self.assertEqual((self.gold(), sum((m["weight_delta"] for m in self.db.sup_gold), D("0"))), (D("20.000"), D("0.000")))
        self.assertEqual(self.db.sup_gold[-1]["weight_delta"], -stored)                      # the STORED credit is restored, never recomputed
        self.refused("ALREADY_REVERSED", lambda: self.service.reverse_settlement(done["id"], "x"), CorrectionError)
        view = self.service.account(MARKETER)
        self.assertTrue(view["settlements"][0]["reversed"])


class RightsAndAtomicityTests(MarketerCase):
    def test_a_user_without_the_holding_rights_cannot_write_or_read(self):
        manager = session_for(MANAGER)
        service = self.make_service(manager, type(self.authorization)(manager))
        for call in (lambda: service.cash_handover(MARKETER, "1", TODAY, "", "", self.key()),
                     lambda: service.purchase(MARKETER, [line()], "", "x", TODAY, "", "", self.key()),
                     lambda: service.sale(MARKETER, "1", "TOTAL", "1", "", "x", TODAY, "", "", self.key()),
                     lambda: service.gold_return(MARKETER, "1", TODAY, "", "", self.key()),
                     lambda: service.settle_supplier(MARKETER, SUPPLIER24, "1", False, TODAY, "", "", self.key()),
                     lambda: service.account(MARKETER), lambda: service.marketers()):
            with self.assertRaises(PermissionError):
                call()
        self.assertEqual(self.db.mk_docs, {})

    def test_a_failure_at_any_step_leaves_no_trace(self):
        self.hand("100000")
        self.buy([line("10", "60000")])
        cases = (("mk_document", lambda: self.hand("1")), ("mk_cash", lambda: self.hand("1")), ("mk_shop_cash", lambda: self.hand("1")), ("mk_link", lambda: self.hand("1")),
                 ("mk_audit", lambda: self.hand("1")), ("mk_line", lambda: self.buy([line("1", "6000")])), ("mk_gold", lambda: self.buy([line("1", "6000")])),
                 ("mk_shop_scrap", lambda: self.gold_back("1")), ("sg_header", lambda: self.settle("1")), ("sg_gold", lambda: self.settle("1")),
                 ("sg_leg", lambda: self.settle("1")), ("sg_audit", lambda: self.settle("1")))
        for step, call in cases:
            with self.subTest(step=step):
                before = self.state()
                self.db.fail_at = step
                with self.assertRaises(Exception):
                    call()
                self.db.fail_at = None
                self.assertEqual(self.state(), before)

    def test_the_account_read_shows_separate_figures_documents_and_pickers(self):
        self.hand("100000")
        self.buy([line("10", "60000")])
        view = self.service.account(MARKETER)
        self.assertEqual((view["cash"], view["gold"], view["karat"]), (D("40000.00"), D("10.000"), 21))
        self.assertEqual([d["code"] for d in view["documents"]], ["MB000002", "MH000001"])
        self.assertEqual(view["documents"][0]["lines"], [{"weight": D("10.000"), "price": D("60000.00")}])
        self.assertEqual({r["id"] for r in view["recipients"]}, {"10", "11", "21", "23"})     # only suppliers with a karat can receive a settlement
        self.assertTrue(view["can_manage"] and view["can_settle"] and view["can_reverse"])
        self.assertEqual(sorted(m["id"] for m in self.service.marketers()), ["12", "20", "21", "22"])
        with self.assertRaises(TripError):
            self.service.account(SUPPLIER24)                                                  # not a marketer


if __name__ == "__main__":
    unittest.main()
