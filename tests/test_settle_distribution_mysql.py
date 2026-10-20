"""Explicit opt-in rehearsal checks (settlement step 2b, migration 061): masgold_rehearsal ONLY, level >= 61, real services, real repositories, real SQL. Positive fixtures are
committed and never cleaned (the ledgers are insert-only); every test builds its own trip; negative statements always run inside transactions that are rolled back.

    .\\.venv\\Scripts\\python.exe -B -m unittest -v tests.test_settle_distribution_mysql   (through the dedicated-account runner, MASGOLD_BOOK_REHEARSAL=1)

Evidence: the CHECK / FK refusals of the link and the SG document, the bar delivery cycle (credit in the supplier's OWN karat, pool, replay, reversal restoring the STORED credit,
the melt blocked while its bar was delivered), the unmelted gold delivered in its own karat, the supplier-side reversal refused, and a two-connection race for the same bar."""
from decimal import Decimal as D
import os
from uuid import uuid4
import unittest

from services.correction_service import CorrectionError
from services.market_trip_service import TripError
from services.supplier_service import SupplierService
from tests.test_market_trip_book_mysql import BookCoreMySqlTests, DATE


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class SettleDistributionMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        with cls.h.tx() as c:
            q = c.cursor()
            q.execute("SELECT MAX(version) FROM schema_migrations")
            assert int(q.fetchone()[0]) >= 61
            q.close()
        suppliers = SupplierService(cls.h.session, transaction_factory=cls.h.tx)
        cls.sup21 = suppliers.create_supplier("061 مورد 21 " + uuid4().hex[:10], karat_id=cls.h.karats[21])
        cls.sup18 = suppliers.create_supplier("061 مورد 18 " + uuid4().hex[:10], karat_id=cls.h.karats[18])

    # ------------------------------------------------------------ helpers
    def lines(self, *rows):
        return [{"karat_id": str(self.h.karats[k]), "weight": w} for k, w in rows]

    def melted_trip(self):
        """21K 50 g + 18K 10 g left without a destination; 40 g + 5 g melted into a 44.8 g bar at 850 (43.520 g 21K eq)."""
        trip = self.h.service.issue("", "", self.lines((21, "50"), (18, "10")), DATE, "061 rehearsal", self.h.key())["trip_id"]
        self.h.service.settle_melt(trip, self.lines((21, "40"), (18, "5")), "44.8", "850", "ورشة 061", DATE, "", self.h.key())
        return trip

    def bar_to(self, trip, supplier, weight, key=None, service=None):
        return (service or self.h.service).settle_supplier_bar(trip, str(supplier), weight, DATE, "", "", key or self.h.key())

    def custody(self, trip):
        return self.h.service.trip_detail(trip)["custody"]

    def payable(self, supplier, karat_value):
        rows = self.h.query("SELECT COALESCE(SUM(weight_delta_grams),0) AS g FROM supplier_gold_movements WHERE supplier_id=%s AND karat_id=%s", (supplier, self.h.karats[karat_value]))
        return D(str(rows[0]["g"]))

    # ------------------------------------------------------------ schema
    def test_the_objects_of_061_exist(self):
        self.assertEqual(len(self.h.query("SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'market_trip_bar_supplier_links'")), 1)
        for name, needle in (("chk_mte_kind", "ST_BAR_SUP"), ("chk_sgs_source", "TRIP_BAR"), ("chk_sgs_shape", "TRIP_BAR"), ("chk_sgsl_source", "TRIP_BAR"),
                             ("chk_mtgs_designation", "UNCLASSIFIED"), ("chk_mtsm_kind", "ST_BAR_SUP")):
            clause = self.h.query("SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() AND CONSTRAINT_NAME = %s", (name,))[0]["CHECK_CLAUSE"]
            self.assertIn(needle, clause, name)

    def test_the_link_and_the_document_refuse_malformed_rows(self):
        trip = self.melted_trip()
        done = self.bar_to(trip, self.sup21, "5")
        entry = int(done["entry_id"])
        self.h.refuses("UPDATE market_trip_bar_supplier_links SET weight_grams = weight_grams + 1 WHERE entry_id=%s", (entry,))           # fk_mtbarl_settlement (weight tie)
        self.h.refuses("UPDATE supplier_gold_settlements SET source_kind = 'TRIP_BOOK' WHERE id=%s", (done["settlement_id"],))             # the link / legs keep the source
        self.h.refuses("INSERT INTO supplier_gold_settlement_legs(settlement_id, source_kind, leg_kind, old_gold_movement_id) VALUES (%s,'TRIP_BAR','SCRAP',1)",
                       (done["settlement_id"],))                                                                                        # a bar delivery never carries a Scrap leg

    # ------------------------------------------------------------ the cycle on real SQL
    def test_a_part_of_the_bar_is_credited_in_the_suppliers_own_karat_and_the_pool_follows(self):
        trip = self.melted_trip()
        self.assertEqual(self.custody(trip)["bar"], D("43.520"))
        first = self.bar_to(trip, self.sup21, "18.8")
        self.assertEqual((first["credited_weight"], first["target_karat"]), ("18.800", 21))
        other = self.bar_to(trip, self.sup18, "9")                                                     # 9 x 21 / 18 = 10.500
        self.assertEqual((other["credited_weight"], other["target_karat"]), ("10.500", 18))
        self.assertEqual(self.custody(trip)["bar"], D("15.720"))
        self.assertEqual((self.payable(self.sup21, 21) <= D("-18.800"), self.payable(self.sup18, 18) <= D("-10.500")), (True, True))
        with self.assertRaises(TripError) as refused:
            self.bar_to(trip, self.sup21, "15.721")
        self.assertEqual(refused.exception.code, "BAR_EXCEEDS_POOL")
        header = self.h.query("SELECT source_kind, delivered_karat_value, delivered_weight_grams FROM supplier_gold_settlements WHERE id=%s", (first["settlement_id"],))[0]
        self.assertEqual((header["source_kind"], header["delivered_karat_value"], header["delivered_weight_grams"]), ("TRIP_BAR", 21, D("18.800")))

    def test_replay_conflict_and_reversal_restores_the_stored_credit(self):
        trip = self.melted_trip()
        key = self.h.key()
        first = self.bar_to(trip, self.sup18, "9", key=key)
        again = self.bar_to(trip, self.sup18, "9", key=key)
        self.assertEqual((again["entry_id"], again["duplicate"]), (first["entry_id"], True))
        self.assertEqual(self.custody(trip)["bar"], D("34.520"))                                       # posted once
        with self.assertRaises(TripError) as conflict:
            self.bar_to(trip, self.sup18, "8", key=key)
        self.assertEqual(conflict.exception.code, "REQUEST_CONFLICT")
        before = self.payable(self.sup18, 18)
        self.h.correct(first["entry_id"])
        self.assertEqual(self.custody(trip)["bar"], D("43.520"))
        self.assertEqual(self.payable(self.sup18, 18), before + D("10.500"))                           # the STORED credit is restored, never recomputed

    def test_the_melt_cannot_be_reversed_while_its_bar_was_delivered_and_the_supplier_side_refuses(self):
        trip = self.melted_trip()
        melt = self.h.query("SELECT id FROM market_trip_entries WHERE trip_id=%s AND entry_kind='ST_MELT'", (trip,))[0]["id"]
        done = self.bar_to(trip, self.sup21, "4")
        with self.assertRaises(CorrectionError) as refused:
            self.h.correct(melt)
        self.assertEqual(refused.exception.code, "DEPENDENT_SETTLEMENT_USE_EXISTS")
        with self.assertRaises(CorrectionError) as supplier_side:
            self.h.corrector.reverse("SUPPLIER_GOLD_SETTLEMENT", done["settlement_id"], "من صفحة المورد")
        self.assertEqual(supplier_side.exception.code, "CUSTODY_SETTLEMENT_REVERSE_FROM_TRIP")

    def test_unmelted_gold_is_delivered_in_its_own_karat_and_the_trip_closes_at_zero(self):
        trip = self.melted_trip()
        raw = self.h.service.settle_supplier_gold(trip, str(self.sup18), str(self.h.karats[21]), "UNCLASSIFIED", "10", DATE, "", "", self.h.key())
        self.assertEqual((raw["delivered_weight"], raw["credited_weight"], raw["designation"]), ("10.000", "11.667", "UNCLASSIFIED"))
        self.h.service.settle_supplier_gold(trip, str(self.sup21), str(self.h.karats[18]), "UNCLASSIFIED", "5", DATE, "", "", self.h.key())
        self.bar_to(trip, self.sup21, "43.520")
        self.assertEqual(self.h.service.close(trip)["status"], "CLOSED")                               # every pool is exactly zero

    # ------------------------------------------------------------ the race
    def test_race_two_deliveries_of_the_same_bar_only_one_fits(self):
        trip = self.melted_trip()
        services = [self.h.build_service(self.h.make_session(f"sd{n}")[0]) for n in range(2)]
        outcomes = self.h.race([lambda s=s: self.bar_to(trip, self.sup21, "30", service=s)["entry_id"] for s in services])
        self.assertEqual(sorted(code for code, _ in outcomes), ["BAR_EXCEEDS_POOL", "ok"])
        self.assertEqual(self.custody(trip)["bar"], D("13.520"))


if __name__ == "__main__":
    unittest.main()
