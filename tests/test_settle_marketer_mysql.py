"""Explicit opt-in rehearsal checks (settlement step 2c, migration 062): masgold_rehearsal ONLY, level >= 62, real services, real repositories, real SQL. Positive fixtures are
committed and never cleaned (the ledgers are insert-only); every test builds its own trip; negative statements always run inside transactions that are rolled back.

Evidence: the bar handed to a marketer (his 21K entitlement, the BAR pool), the CHECK refusals of the new kinds, the reversal and its dependent-use refusal, the unmelted remainder
classified as LOGO (RAW -> LOGO, closure, reversal)."""
from decimal import Decimal as D
import os
from uuid import uuid4
import unittest

from services.correction_service import CorrectionError
from services.market_trip_service import TripError
from services.supplier_service import SupplierService
from tests.test_market_trip_book_mysql import BookCoreMySqlTests, DATE


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class SettleMarketerMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        with cls.h.tx() as c:
            q = c.cursor()
            q.execute("SELECT MAX(version) FROM schema_migrations")
            assert int(q.fetchone()[0]) >= 62
            q.close()
        suppliers = SupplierService(cls.h.session, transaction_factory=cls.h.tx)
        cls.marketer = suppliers.create_supplier("062 مسوقجي " + uuid4().hex[:10], is_supplier=False, is_intermediary=True)

    def lines(self, *rows):
        return [{"karat_id": str(self.h.karats[k]), "weight": w} for k, w in rows]

    def melted_trip(self):
        trip = self.h.service.issue("", "", self.lines((21, "50"), (18, "10")), DATE, "062 rehearsal", self.h.key())["trip_id"]
        self.h.service.settle_melt(trip, self.lines((21, "40"), (18, "5")), "44.8", "850", "ورشة 062", DATE, "", self.h.key())
        return trip

    def custody(self, trip):
        return self.h.service.trip_detail(trip)["custody"]

    def entitlement(self):
        rows = self.h.query("SELECT COALESCE(SUM(weight_delta),0) AS g FROM marketer_gold_movements WHERE party_id=%s", (self.marketer,))
        return D(str(rows[0]["g"]))

    def test_the_objects_of_062_exist(self):
        for name, needle in (("chk_mte_kind", "ST_LOGO"), ("chk_mte_mode_kind", "ST_BAR_MKT"), ("chk_mtsm_kind", "ST_LOGO"), ("chk_mtsm_pool", "LOGO"), ("chk_mkgm_shape", "ST_BAR_MKT")):
            clause = self.h.query("SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() AND CONSTRAINT_NAME = %s", (name,))[0]["CHECK_CLAUSE"]
            self.assertIn(needle, clause, name)

    def test_the_bar_goes_to_the_marketer_and_back_with_the_stored_amount(self):
        trip = self.melted_trip()
        before = self.entitlement()
        done = self.h.service.settle_bar_to_marketer(trip, str(self.marketer), "20", DATE, "", self.h.key())
        self.assertEqual(self.custody(trip)["bar"], D("23.520"))
        self.assertEqual(self.entitlement(), before + D("20"))
        leg = self.h.query("SELECT movement_type, trip_entry_kind, weight_delta FROM marketer_gold_movements WHERE trip_entry_id=%s", (done["entry_id"],))
        self.assertEqual([(r["movement_type"], r["trip_entry_kind"], r["weight_delta"]) for r in leg], [("TRIP_BOOK_IN", "ST_BAR_MKT", D("20.000"))])
        with self.assertRaises(TripError) as over:
            self.h.service.settle_bar_to_marketer(trip, str(self.marketer), "23.521", DATE, "", self.h.key())
        self.assertEqual(over.exception.code, "BAR_EXCEEDS_POOL")
        self.h.correct(done["entry_id"])
        self.assertEqual(self.custody(trip)["bar"], D("43.520"))
        self.assertEqual(self.entitlement(), before)

    def test_the_new_kinds_refuse_malformed_rows(self):
        trip = self.melted_trip()
        done = self.h.service.settle_bar_to_marketer(trip, str(self.marketer), "5", DATE, "", self.h.key())
        self.h.refuses("UPDATE marketer_gold_movements SET trip_entry_kind = 'BK_RECEIVE' WHERE trip_entry_id=%s", (done["entry_id"],))
        self.h.refuses("UPDATE market_trip_entries SET entry_kind = 'ST_BAR_MKT', trip_mode = 'BOOK' WHERE id=%s", (done["entry_id"],))

    def test_the_melt_cannot_be_reversed_while_its_bar_was_handed_over(self):
        trip = self.melted_trip()
        melt = self.h.query("SELECT id FROM market_trip_entries WHERE trip_id=%s AND entry_kind='ST_MELT'", (trip,))[0]["id"]
        self.h.service.settle_bar_to_marketer(trip, str(self.marketer), "4", DATE, "", self.h.key())
        with self.assertRaises(CorrectionError) as refused:
            self.h.correct(melt)
        self.assertEqual(refused.exception.code, "DEPENDENT_SETTLEMENT_USE_EXISTS")

    def test_the_remainder_becomes_logo_in_its_own_karat_and_the_trip_closes(self):
        trip = self.melted_trip()
        done = self.h.service.classify_remaining_as_logo(trip, DATE, "", self.h.key())
        rows = {(g["karat_value"], g["designation"]): g["outstanding"] for g in self.custody(trip)["gold"]}
        self.assertEqual(rows.get((21, "LOGO")), D("10.000"))
        self.assertEqual(rows.get((18, "LOGO")), D("5.000"))
        with self.assertRaises(TripError):
            self.h.service.classify_remaining_as_logo(trip, DATE, "", self.h.key())                 # nothing unclassified remains
        self.h.correct(done["entry_id"])
        rows = {(g["karat_value"], g["designation"]): g["outstanding"] for g in self.custody(trip)["gold"]}
        self.assertEqual(rows.get((21, "UNCLASSIFIED")), D("10.000"))

    def test_the_logo_classification_is_replayed_exactly(self):
        trip = self.melted_trip()
        key = self.h.key()
        first = self.h.service.classify_remaining_as_logo(trip, DATE, "", key)
        replay = self.h.service.classify_remaining_as_logo(trip, DATE, "", key)
        self.assertEqual(replay["entry_id"], first["entry_id"])


if __name__ == "__main__":
    unittest.main()
