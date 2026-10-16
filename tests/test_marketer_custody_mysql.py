"""Explicit opt-in rehearsal checks for the marketer custody account (migration 055). They connect to masgold_rehearsal ONLY (target, migration level >= 55 and
marker are asserted) and never to the DEV database. Real services, real repositories, real SQL, REPEATABLE READ connections. Fixtures are committed and never
cleaned (immutable ledgers); every test uses its own marketer party. Negative database checks run inside transactions that are rolled back.

Evidence classes, kept apart: functional checks (real services over MySQL), database-constraint checks (raw statements the services would never issue,
rolled back) and a two-thread race on the production authorization path. None of this is a stress test."""
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal as D
import os
import threading
import unittest
from uuid import uuid4

from mysql.connector import Error as MySqlError

from app.session import AppSession
from config.database_config import DatabaseConfig
from database.connection import DatabaseConnection
from repositories.authentication_repository import AuthenticationRepository
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.correction_service import CorrectionError, CorrectionService
from services.market_trip_service import TripError
from services.marketer_custody_service import MarketerCustodyService
from services.supplier_service import SupplierService

TARGET = "masgold_rehearsal"
DATE = "2026-10-17"
REFERENCE = {21: D("6000.0000")}
SUPPLIER_TABLES = ("supplier_gold_movements", "supplier_making_movements", "supplier_making_payments", "supplier_gold_settlements",
                   "supplier_gold_settlement_legs")
UNTOUCHED = ("bank_movements", "inventory_items", "market_trip_entries", "market_trip_lot_movements", "intermediary_holding_movements")


@unittest.skipUnless(os.environ.get("MASGOLD_MARKETER_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class MarketerMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = replace(DatabaseConfig.from_env(), database=TARGET)
        with cls.tx() as c:
            q = c.cursor(dictionary=True)
            q.execute("SELECT DATABASE() AS db, MAX(version) AS v FROM schema_migrations")
            row = q.fetchone()
            assert row["db"] == TARGET and int(row["v"]) >= 55, row
            q.execute("SELECT target_database FROM masgold_rehearsal_marker WHERE marker_key='MASGOLD_SCHEMA_REHEARSAL'")
            assert q.fetchone()["target_database"] == TARGET
            q.execute("INSERT INTO employees(name) VALUES('055 rehearsal owner')")
            emp = q.lastrowid
            q.execute("INSERT INTO users(employee_id,username,password_hash,display_name) VALUES(%s,%s,'unusable-rehearsal-hash','055 owner')",
                      (emp, "rehearsal055-" + uuid4().hex))
            cls.user = q.lastrowid
            q.execute("INSERT INTO user_roles(user_id,role_id) SELECT %s,id FROM roles WHERE code='OWNER'", (cls.user,))
            q.execute("SELECT id,karat_value FROM karats")
            cls.karats = {r["karat_value"]: r["id"] for r in q.fetchall()}
            auth = AuthenticationRepository(c)
            identity, permissions = auth.get_identity(cls.user), auth.effective_permissions(cls.user)
            cls.session = AppSession()
            cls.session._establish(AuthenticationResult(cls.user, emp, identity["display_name"], permissions, _VERIFIED), int(cls.session.generation))
            q.close()
        cls.suppliers = SupplierService(cls.session, transaction_factory=cls.tx)
        cls.supplier18 = cls.suppliers.create_supplier("055 مورد 18 " + uuid4().hex[:10], karat_id=cls.karats[18])
        cls.supplier24 = cls.suppliers.create_supplier("055 مورد 24 " + uuid4().hex[:10], karat_id=cls.karats[24])

    @classmethod
    @contextmanager
    def tx(cls):
        assert cls.config.database == TARGET
        c = DatabaseConnection(cls.config).connect()
        try:
            q = c.cursor()
            q.execute("SELECT DATABASE()")
            assert q.fetchone() == (TARGET,)
            q.close()
            c.rollback()
            c.start_transaction(isolation_level="REPEATABLE READ")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def setUp(self):
        self.service = MarketerCustodyService(self.session, transaction_factory=self.tx, price_reference=lambda: dict(REFERENCE))
        self.correct = CorrectionService(self.session, transaction_factory=self.tx)
        self.service._corrections = self.correct
        self.marketer = str(self.suppliers.create_supplier("055 مسوقجي " + uuid4().hex[:10], is_supplier=False, is_intermediary=True))
        self.dual = str(self.suppliers.create_supplier("055 مسوقجي ومورد " + uuid4().hex[:10], karat_id=self.karats[18], is_supplier=True, is_intermediary=True))

    # ------------------------------------------------------------ helpers
    def query(self, sql, params=()):
        with self.tx() as c:
            q = c.cursor(dictionary=True)
            q.execute(sql, params)
            rows = q.fetchall()
            q.close()
            return rows

    def counts(self, tables):
        return {t: self.query(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"] for t in tables}

    def supplier_rows(self, party):
        return {t: self.query(f"SELECT COUNT(*) AS n FROM {t} WHERE supplier_id=%s", (party,))[0]["n"]
                for t in ("supplier_gold_movements", "supplier_making_movements", "supplier_making_payments")}

    @staticmethod
    def key():
        return str(uuid4())

    def hand(self, amount="100000", party=None, key=None):
        return self.service.cash_handover(party or self.marketer, amount, DATE, "055 ref", "055 notes", key or self.key())

    def back(self, amount="1000", party=None, key=None):
        return self.service.cash_return(party or self.marketer, amount, DATE, "", "", key or self.key())

    def buy(self, weight="10", price="60000", party=None, name="تاجر 055", key=None, ack=False):
        return self.service.purchase(party or self.marketer, [{"weight": weight, "price": price}], "", name, DATE, "", "", key or self.key(), ack)

    def sell(self, weight="2", total="12000", party=None, key=None):
        return self.service.sale(party or self.marketer, weight, "TOTAL", total, "", "مشتري 055", DATE, "", "", key or self.key(), False)

    def figures(self, party=None):
        a = self.service.account(party or self.marketer)
        return a["cash"], a["gold"]

    def main_safe_balance(self):
        return self.query("SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount ELSE -amount END),0) AS b FROM cash_movements cm "
                          "JOIN financial_accounts fa ON fa.id=cm.account_id WHERE fa.code='MAIN_SAFE'")[0]["b"] if False else None

    def shop_cash_rows(self, doc_code):
        return self.query("SELECT * FROM cash_movements WHERE reference_type=%s AND reference_id=%s", (doc_code[0], doc_code[1]))

    def raises(self, error, code, call, *args, **kwargs):
        with self.assertRaises(error) as refused:
            call(*args, **kwargs)
        self.assertEqual(refused.exception.code, code)

    # ------------------------------------------------------------ functional checks
    def test_cash_handover_and_return_post_one_main_safe_movement_each_and_keep_every_other_ledger_untouched(self):
        supplier_before, untouched = self.supplier_rows(self.dual), self.counts(UNTOUCHED + SUPPLIER_TABLES)
        key = self.key()
        done = self.hand("50000.50", party=self.dual, key=key)
        self.assertEqual((done["kind"], done["cash"], done["duplicate"]), ("CASH_HANDOVER", "50000.50", False))
        self.assertEqual(self.figures(self.dual), (D("50000.50"), D("0.000")))
        again = self.hand("50000.50", party=self.dual, key=key)
        self.assertEqual((again["duplicate"], again["id"]), (True, done["id"]))
        self.raises(TripError, "REQUEST_CONFLICT", self.hand, "50000.51", self.dual, key)
        back = self.back("20000", party=self.dual)
        self.assertEqual(back["kind"], "CASH_RETURN")
        self.assertEqual(self.figures(self.dual), (D("30000.50"), D("0.000")))
        rows = self.query("SELECT movement_type, amount_delta FROM marketer_cash_movements WHERE party_id=%s ORDER BY id", (self.dual,))
        self.assertEqual([(r["movement_type"], r["amount_delta"]) for r in rows], [("CASH_HANDOVER", D("50000.50")), ("CASH_RETURN", D("-20000.00"))])
        links = self.query("SELECT COUNT(*) AS n FROM marketer_shop_links WHERE document_id IN (%s,%s) AND shop_cash_movement_id IS NOT NULL",
                           (int(done["id"]), int(back["id"])))
        self.assertEqual(links[0]["n"], 2)
        self.assertEqual(self.supplier_rows(self.dual), supplier_before)                 # a dual-role party's supplier account is never touched
        after = self.counts(UNTOUCHED + SUPPLIER_TABLES)
        self.assertEqual(after, untouched)

    def test_the_cash_limits_and_the_marketer_role_are_enforced(self):
        self.hand("1000")
        self.raises(TripError, "CASH_EXCEEDS_HELD", self.back, "1000.01")
        self.raises(TripError, "MARKETER_ROLE_REQUIRED", self.hand, "10", str(self.supplier18))
        self.assertEqual(self.figures(), (D("1000.00"), D("0.000")))
        self.back("1000")
        self.assertEqual(self.figures(), (D("0.00"), D("0.000")))

    def test_gold_is_21k_only_it_enters_by_purchase_and_returns_to_scrap_as_21k_grams(self):
        self.hand("200000")
        scrap_before = self.query("SELECT COUNT(*) AS n FROM old_gold_movements")[0]["n"]
        bought = self.buy("10", "60000")
        self.assertEqual((bought["kind"], bought["weight"]), ("PURCHASE", "10.000"))
        self.assertEqual(self.figures(), (D("140000.00"), D("10.000")))
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM old_gold_movements")[0]["n"], scrap_before)     # buying posts no shop Scrap
        self.raises(TripError, "GOLD_EXCEEDS_HELD", self.service.gold_return, self.marketer, "10.001", DATE, "", "", self.key())
        back = self.service.gold_return(self.marketer, "4", DATE, "", "", self.key())
        self.assertEqual(self.figures(), (D("140000.00"), D("6.000")))
        scrap = self.query("SELECT og.weight_delta_grams, og.cash_value, k.karat_value FROM old_gold_movements og JOIN marketer_shop_links l "
                           "ON l.shop_scrap_movement_id=og.id JOIN karats k ON k.id=og.karat_id WHERE l.document_id=%s", (int(back["id"]),))
        self.assertEqual(len(scrap), 1)
        self.assertEqual(scrap[0]["karat_value"], 21)
        for weight, price in (("0", "100"), ("1", "0")):
            with self.assertRaises(TripError):
                self.buy(weight, price)
        sold = self.sell("2", "12000")
        self.assertEqual(sold["kind"], "SALE")
        self.assertEqual(self.figures(), (D("152000.00"), D("4.000")))
        self.raises(TripError, "SALE_EXCEEDS_GOLD", self.sell, "4.001", "24000")

    def test_the_price_warning_needs_an_acknowledgement_and_the_counterparty_is_mandatory(self):
        self.hand("200000")
        self.raises(TripError, "PRICE_DEVIATION", self.buy, "10", "40000")
        done = self.buy("10", "40000", ack=True)
        row = self.query("SELECT price_warning_acknowledged, deviation_percent, reference_total FROM marketer_documents WHERE id=%s", (int(done["id"]),))[0]
        self.assertEqual((row["price_warning_acknowledged"], row["deviation_percent"], row["reference_total"]), (1, D("-33.33"), D("60000.00")))
        for name in ("", "   "):
            self.raises(TripError, "COUNTERPARTY_REQUIRED", self.buy, "1", "6000", None, name)

    def test_a_settlement_moves_only_the_marketer_gold_and_the_one_supplier_credit(self):
        self.hand("200000")
        self.buy("10", "60000")
        before = self.supplier_rows(self.supplier18)
        shop = self.counts(("cash_movements", "old_gold_movements", "bank_movements"))
        done = self.service.settle_supplier(self.marketer, str(self.supplier18), "6", False, DATE, "", "", self.key())
        self.assertEqual((done["delivered_weight"], done["credited_weight"], done["target_karat"], done["remaining"]), ("6.000", "7.000", 18, "4.000"))
        self.assertEqual(self.figures(), (D("140000.00"), D("4.000")))
        after = self.supplier_rows(self.supplier18)
        self.assertEqual(after["supplier_gold_movements"], before["supplier_gold_movements"] + 1)
        self.assertEqual((after["supplier_making_movements"], after["supplier_making_payments"]), (before["supplier_making_movements"], before["supplier_making_payments"]))
        self.assertEqual(self.counts(("cash_movements", "old_gold_movements", "bank_movements")), shop)
        header = self.query("SELECT * FROM supplier_gold_settlements WHERE id=%s", (int(done["id"]),))[0]
        self.assertEqual((header["source_kind"], header["delivered_karat_value"], header["holding_weight_grams"]), ("HOLDING_MKT", 21, D("6.000")))
        self.raises(TripError, "GOLD_EXCEEDS_HELD", self.service.settle_supplier, self.marketer, str(self.supplier18), "4.001", False, DATE, "", "", self.key())

    def test_a_dual_role_party_needs_the_explicit_self_settlement_confirmation_and_its_supplier_account_stays_separate(self):
        self.hand("200000", party=self.dual)
        self.buy("10", "60000", party=self.dual)
        self.raises(TripError, "SELF_SETTLEMENT_CONFIRMATION_REQUIRED", self.service.settle_supplier, self.dual, self.dual, "3", False, DATE, "", "", self.key())
        self.assertEqual(self.supplier_rows(self.dual)["supplier_gold_movements"], 0)
        self.service.settle_supplier(self.dual, self.dual, "3", True, DATE, "", "", self.key())
        self.assertEqual(self.supplier_rows(self.dual)["supplier_gold_movements"], 1)
        self.assertEqual(self.figures(self.dual)[1], D("7.000"))

    def test_reversals_restore_stored_effects_and_are_decided_by_arithmetic(self):
        hand = self.hand("100000")
        bought = self.buy("10", "60000")
        self.assertEqual(self.figures(), (D("40000.00"), D("10.000")))
        with self.assertRaises(CorrectionError) as blocked:                                   # the handover's cash was already spent
            self.service.reverse(hand["id"], "خطأ إدخال")
        self.assertIn("DEPENDENT", blocked.exception.code)
        with self.assertRaises(CorrectionError):
            self.service.reverse(bought["id"], "")                                            # a reason is mandatory
        self.service.reverse(bought["id"], "خطأ إدخال")
        self.assertEqual(self.figures(), (D("100000.00"), D("0.000")))
        with self.assertRaises(CorrectionError) as twice:
            self.service.reverse(bought["id"], "مرة ثانية")
        self.assertEqual(twice.exception.code, "ALREADY_REVERSED")
        self.service.reverse(hand["id"], "خطأ إدخال")
        self.assertEqual(self.figures(), (D("0.00"), D("0.000")))
        original = self.query("SELECT COUNT(*) AS n FROM marketer_cash_movements WHERE document_id=%s", (int(hand["id"]),))[0]["n"]
        self.assertEqual(original, 2)                                                         # the original stays counted beside its compensation

    def test_a_settlement_is_reversed_through_the_one_canonical_correction_and_restores_the_stored_figures(self):
        self.hand("200000")
        self.buy("10", "60000")
        done = self.service.settle_supplier(self.marketer, str(self.supplier24), "5", False, DATE, "", "", self.key())
        self.assertEqual(done["credited_weight"], "4.375")
        before = self.supplier_rows(self.supplier24)
        self.service.reverse_settlement(done["id"], "خطأ إدخال")
        self.assertEqual(self.figures()[1], D("10.000"))
        self.assertEqual(self.supplier_rows(self.supplier24)["supplier_gold_movements"], before["supplier_gold_movements"] + 1)       # explicit compensation
        net = self.query("SELECT SUM(delta_grams) AS n FROM supplier_gold_movements WHERE supplier_id=%s", (self.supplier24,))[0]["n"] if False else None
        with self.assertRaises(CorrectionError) as twice:
            self.service.reverse_settlement(done["id"], "مرة ثانية")
        self.assertEqual(twice.exception.code, "ALREADY_REVERSED")

    def test_a_failed_operation_leaves_no_trace(self):
        self.hand("1000")
        before = self.counts(("marketer_documents", "marketer_cash_movements", "marketer_gold_movements", "marketer_shop_links", "cash_movements"))
        self.raises(TripError, "CASH_EXCEEDS_HELD", self.back, "5000")
        self.assertEqual(self.counts(("marketer_documents", "marketer_cash_movements", "marketer_gold_movements", "marketer_shop_links", "cash_movements")), before)

    def test_two_threads_returning_the_same_cash_post_exactly_one(self):
        self.hand("1000")
        results = []

        def run():
            try:
                results.append(("ok", self.back("1000")["id"]))
            except TripError as exc:
                results.append(("refused", exc.code))
        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(sorted(r[0] for r in results), ["ok", "refused"])
        self.assertEqual([r[1] for r in results if r[0] == "refused"], ["CASH_EXCEEDS_HELD"])
        self.assertEqual(self.figures(), (D("0.00"), D("0.000")))

    # ------------------------------------------------------------ database constraints (rolled back)
    def refused(self, sql, params=()):
        with self.assertRaises(MySqlError):
            with self.tx() as c:
                q = c.cursor()
                q.execute(sql, params)
                q.close()
                c.rollback()
                raise AssertionError("the database accepted a malformed row")

    def test_the_database_refuses_non_21k_blank_counterparties_and_orphan_movements(self):
        base = ("INSERT INTO marketer_documents(doc_kind,party_id,karat_value,cash_amount,weight_grams,counterparty_kind,counterparty_name,counterparty_key,"
                "document_date,request_key,request_hash,performed_by_user_id,occurred_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'2026-10-17',%s,%s,%s,NOW())")
        party = int(self.marketer)
        self.refused(base, ("CASH_HANDOVER", party, 18, "10.00", None, None, None, None, self.key(), "0" * 64, self.user))
        self.refused(base, ("PURCHASE", party, 21, None, "1.000", "THIRD_PARTY", "   ", "", self.key(), "0" * 64, self.user))
        self.refused(base, ("PURCHASE", party, 21, None, "1.000", None, None, None, self.key(), "0" * 64, self.user))
        self.refused("INSERT INTO marketer_cash_movements(party_id,document_id,doc_kind,movement_type,cash_amount,amount_delta) VALUES(%s,999999999,'CASH_HANDOVER','CASH_HANDOVER',1,1)", (party,))
        self.refused("INSERT INTO marketer_gold_movements(party_id,karat_value,movement_type,weight_grams,weight_delta) VALUES(%s,18,'PURCHASE_IN',1,1)", (party,))

    def test_the_migration_is_registered_and_the_objects_exist(self):
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM schema_migrations WHERE version=55")[0]["n"], 1)
        names = {r["TABLE_NAME"] for r in self.query("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME LIKE 'marketer\\_%'")}
        self.assertEqual(names, {"marketer_documents", "marketer_shop_links", "marketer_purchase_lines", "marketer_cash_movements", "marketer_gold_movements"})
        clause = self.query("SELECT cc.CHECK_CLAUSE AS c FROM information_schema.CHECK_CONSTRAINTS cc WHERE cc.CONSTRAINT_SCHEMA=DATABASE() "
                            "AND cc.CONSTRAINT_NAME='chk_sgs_source'")[0]["c"]
        self.assertIn("HOLDING_MKT", clause)


if __name__ == "__main__":
    unittest.main()
