"""Explicit opt-in rehearsal checks (settlement steps 2d / 2e / 2f, migrations 063-065): masgold_rehearsal ONLY, level >= 65, real services, real repositories, real SQL. Positive fixtures are
committed and never cleaned (the ledgers are insert-only); every test builds its own trip; negative statements always run inside transactions that are rolled back.

Evidence: the bar sold for custody cash (TRIP_BAR source, pool move), the cash expense / bank deposit / write-off (document, bank movement, constraints), the gold purchase without a
destination, the market return (bar as 21K Scrap), the loss with its classification and the issue-weight correction in both directions; reversals and the CHECK refusals."""
from decimal import Decimal as D
import os
from uuid import uuid4
import unittest

from services.correction_service import CorrectionError
from services.market_trip_service import TripError
from tests.test_market_trip_book_mysql import BookCoreMySqlTests, DATE


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class SettleCashDisposalMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        with cls.h.tx() as c:
            q = c.cursor()
            q.execute("SELECT MAX(version) FROM schema_migrations")
            assert int(q.fetchone()[0]) >= 65
            q.execute("SELECT id FROM bank_accounts WHERE code = 'SETTLE065_FIXTURE' FOR UPDATE")
            row = q.fetchone()
            if row is None:
                q.execute("INSERT INTO bank_accounts(code,name,account_type,notes) VALUES ('SETTLE065_FIXTURE','065 rehearsal bank','BANK','065 rehearsal fixture')")
                q.execute("SELECT id FROM bank_accounts WHERE code = 'SETTLE065_FIXTURE'")
                row = q.fetchone()
            q.execute("UPDATE bank_accounts SET is_active = TRUE WHERE id = %s", (row[0] if not isinstance(row, dict) else row["id"],))
            cls.bank = row[0] if not isinstance(row, dict) else row["id"]
            q.close()

    # ------------------------------------------------------------ helpers
    def lines(self, *rows):
        return [{"karat_id": str(self.h.karats[k]), "weight": w} for k, w in rows]

    def melted_trip(self):
        """21K 50 g + 18K 10 g left without a destination; 40 g + 5 g melted into a 44.8 g bar at 850 (43.520 g 21K eq)."""
        trip = self.h.service.issue("", "", self.lines((21, "50"), (18, "10")), DATE, "065 rehearsal", self.h.key())["trip_id"]
        self.h.service.settle_melt(trip, self.lines((21, "40"), (18, "5")), "44.8", "850", "ورشة 065", DATE, "", self.h.key())
        return trip

    def custody(self, trip):
        return self.h.service.trip_detail(trip)["custody"]

    def gold(self, trip, karat, designation="UNCLASSIFIED"):
        return next((g["outstanding"] for g in self.custody(trip)["gold"] if g["karat_value"] == karat and g["designation"] == designation), D("0.000"))

    def bank_total(self):
        return D(str(self.h.query("SELECT COALESCE(SUM(amount_delta),0) AS s FROM bank_movements WHERE bank_account_id=%s", (self.bank,))[0]["s"]))

    def funded_trip(self, cash="100000"):
        """A melted trip with custody cash: a sale of 10 g of the bar for `cash` EGP (the proceeds enter the custody cash)."""
        trip = self.melted_trip()
        self.h.service.sell_gold(trip, "TRIP_BAR", None, None, None, "10", "TOTAL", cash, "", "تاجر 065", DATE, "", "", self.h.key(), True)
        return trip

    # ------------------------------------------------------------ schema
    def test_the_objects_of_063_064_065_exist(self):
        for name, needle in (("chk_mtgsale_source", "TRIP_BAR"), ("chk_mtsm_kind", "ST_ADJUST"), ("chk_mte_kind", "ST_CASH_BANK"), ("chk_mte_mode_kind", "ST_LOSS"),
                             ("chk_mtgbl_designation", "UNCLASSIFIED"), ("chk_mtsc_shape", "ST_CASH_BANK"), ("chk_mtsl_kind", "CRUCIBLE"), ("chk_mtsa_direction", "DECREASE")):
            clause = self.h.query("SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() AND CONSTRAINT_NAME = %s", (name,))[0]["CHECK_CLAUSE"]
            self.assertIn(needle, clause, name)
        self.assertEqual(len(self.h.query("SELECT 1 FROM bank_movement_types WHERE code = 'TRIP_CUSTODY_BANK_IN'")), 1)

    def test_the_new_documents_refuse_malformed_rows(self):
        trip = self.funded_trip()
        expense = self.h.service.record_custody_expense(trip, "100", "أجرة", DATE, "", "", self.h.key())
        self.h.refuses("UPDATE market_trip_settle_cash SET description = '  ' WHERE entry_id=%s", (expense["entry_id"],))               # chk_mtsc_shape (blank text)
        self.h.refuses("UPDATE market_trip_settle_cash SET amount = 0 WHERE entry_id=%s", (expense["entry_id"],))                       # chk_mtsc_amount
        lost = self.h.service.record_loss(trip, [{"source": "BAR", "weight": "1", "classification": "MARKET"}], DATE, "", self.h.key())
        self.h.refuses("UPDATE market_trip_settle_lines SET classification = 'OTHER' WHERE entry_id=%s", (lost["entry_id"],))           # chk_mtsl_kind
        self.h.refuses("UPDATE market_trip_settle_lines SET karat_id = 1 WHERE entry_id=%s", (lost["entry_id"],))                        # chk_mtsl_source (BAR has no karat)

    # ------------------------------------------------------------ 2d: the bar sold
    def test_the_bar_is_sold_for_custody_cash_and_the_sale_reverses(self):
        trip = self.melted_trip()
        safe = D(str(self.h.query("SELECT COALESCE(SUM(m.amount_delta),0) AS s FROM cash_movements m JOIN cash_accounts a ON a.id=m.cash_account_id WHERE a.code='MAIN_SAFE'")[0]["s"]))
        sold = self.h.service.sell_gold(trip, "TRIP_BAR", None, None, None, "10", "TOTAL", "60000", "", "تاجر 065", DATE, "", "", self.h.key(), True)
        self.assertEqual((sold["source_kind"], sold["weight"]), ("TRIP_BAR", "10.000"))
        self.assertEqual((self.custody(trip)["bar"], self.custody(trip)["cash"]["outstanding"]), (D("33.520"), D("60000.00")))
        with self.assertRaises(TripError) as over:
            self.h.service.sell_gold(trip, "TRIP_BAR", None, None, None, "33.521", "TOTAL", "1000", "", "تاجر", DATE, "", "", self.h.key(), True)
        self.assertEqual(over.exception.code, "BAR_EXCEEDS_POOL")
        self.h.correct(sold["entry_id"])
        self.assertEqual((self.custody(trip)["bar"], self.custody(trip)["cash"]["outstanding"]), (D("43.520"), D("0.00")))
        self.assertEqual(safe, D(str(self.h.query("SELECT COALESCE(SUM(m.amount_delta),0) AS s FROM cash_movements m JOIN cash_accounts a ON a.id=m.cash_account_id WHERE a.code='MAIN_SAFE'")[0]["s"])))

    # ------------------------------------------------------------ 2e: the custody cash
    def test_expense_bank_deposit_and_write_off_settle_the_custody_cash(self):
        trip = self.funded_trip("100000")
        bank = self.bank_total()
        self.h.service.record_custody_expense(trip, "1500.50", "أجرة نقل", DATE, "", "", self.h.key())
        deposit = self.h.service.deposit_custody_cash(trip, "5000", str(self.bank), DATE, "", "", self.h.key())
        self.h.service.write_off_custody_cash(trip, "3499.50", "فرق عد", DATE, "", self.h.key())
        self.assertEqual(self.custody(trip)["cash"]["outstanding"], D("90000.00"))
        self.assertEqual(self.bank_total(), bank + D("5000"))
        with self.assertRaises(TripError) as over:
            self.h.service.write_off_custody_cash(trip, "90000.01", "x", DATE, "", self.h.key())
        self.assertEqual(over.exception.code, "CASH_EXCEEDS_CUSTODY")
        self.h.correct(deposit["entry_id"])                                                              # the bank movement is reversed by the generic money plan
        self.assertEqual(self.bank_total(), bank)
        self.assertEqual(self.custody(trip)["cash"]["outstanding"], D("95000.00"))

    def test_a_sale_whose_proceeds_were_settled_cannot_be_reversed(self):
        trip = self.melted_trip()
        sold = self.h.service.sell_gold(trip, "TRIP_BAR", None, None, None, "5", "TOTAL", "30000", "", "تاجر", DATE, "", "", self.h.key(), True)
        self.h.service.write_off_custody_cash(trip, "30000", "إعدام", DATE, "", self.h.key())
        with self.assertRaises(CorrectionError) as refused:
            self.h.correct(sold["entry_id"])
        self.assertEqual(refused.exception.code, "DEPENDENT_CASH_USE_EXISTS")

    def test_bought_gold_needs_no_destination_and_replay_posts_once(self):
        trip = self.funded_trip("100000")
        key = self.h.key()
        first = self.h.service.buy_gold(trip, [{"karat_id": str(self.h.karats[21]), "weight": "5", "price": "30000"}], "", "تاجر 065", DATE, "", "", key, False)
        again = self.h.service.buy_gold(trip, [{"karat_id": str(self.h.karats[21]), "weight": "5", "price": "30000"}], "", "تاجر 065", DATE, "", "", key, False)
        self.assertEqual((again["entry_id"], again["duplicate"]), (first["entry_id"], True))
        self.assertEqual(self.gold(trip, 21), D("15.000"))                                                # 10 unmelted + 5 bought, one row: UNCLASSIFIED

    # ------------------------------------------------------------ 2f: the remainder
    def test_market_return_loss_and_the_cycle_close_the_trip(self):
        trip = self.melted_trip()
        s21 = self.h.scrap(21)
        back = self.h.service.return_to_shop(trip, [{"source": "BAR", "weight": "20"}, {"source": "RAW", "karat_id": str(self.h.karats[21]), "weight": "3"}], DATE, "", self.h.key())
        self.assertEqual(self.h.scrap(21), s21 + D("23.000"))
        self.assertEqual(self.custody(trip)["bar"], D("23.520"))
        self.h.service.record_loss(trip, [{"source": "BAR", "weight": "23.52", "classification": "CRUCIBLE"}, {"source": "RAW", "karat_id": str(self.h.karats[21]), "weight": "7", "classification": "MARKET"},
                                          {"source": "RAW", "karat_id": str(self.h.karats[18]), "weight": "5", "classification": "MARKET"}], DATE, "", self.h.key())
        self.assertEqual(self.h.service.close(trip)["status"], "CLOSED")
        self.assertEqual(len(self.h.query("SELECT 1 FROM market_trip_settle_lines WHERE classification IS NOT NULL AND entry_id IN (SELECT id FROM market_trip_entries WHERE trip_id=%s)", (trip,))), 3)
        self.assertIsNotNone(back)

    def test_a_market_return_reversal_takes_the_grams_out_of_scrap_again(self):
        trip = self.melted_trip()
        s21 = self.h.scrap(21)
        back = self.h.service.return_to_shop(trip, [{"source": "BAR", "weight": "20"}], DATE, "", self.h.key())
        self.h.correct(back["entry_id"])
        self.assertEqual((self.h.scrap(21), self.custody(trip)["bar"]), (s21, D("43.520")))

    def test_the_issue_weight_correction_goes_both_ways_and_reverses(self):
        trip = self.melted_trip()
        s21 = self.h.scrap(21)
        up = self.h.service.correct_issue_weight(trip, str(self.h.karats[21]), "INCREASE", "2.5", "وزن غلط", DATE, self.h.key())
        self.assertEqual((self.h.scrap(21), self.gold(trip, 21)), (s21 - D("2.500"), D("12.500")))
        down = self.h.service.correct_issue_weight(trip, str(self.h.karats[21]), "DECREASE", "4", "وزن غلط", DATE, self.h.key())
        self.assertEqual((self.h.scrap(21), self.gold(trip, 21)), (s21 + D("1.500"), D("8.500")))
        with self.assertRaises(TripError) as shortage:
            self.h.service.correct_issue_weight(trip, str(self.h.karats[21]), "INCREASE", "999999", "x", DATE, self.h.key())
        self.assertEqual(shortage.exception.code, "SCRAP_SHORTAGE")
        self.h.correct(down["entry_id"])
        self.h.correct(up["entry_id"])
        self.assertEqual((self.h.scrap(21), self.gold(trip, 21)), (s21, D("10.000")))


if __name__ == "__main__":
    unittest.main()
