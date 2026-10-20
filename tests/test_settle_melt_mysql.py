"""Explicit opt-in rehearsal checks (settlement step 2a, migration 060): masgold_rehearsal ONLY, level >= 60, real services, real repositories, real SQL.
Positive fixtures are committed and never cleaned (the ledgers are insert-only); every test builds its own trip; negative statements always run inside transactions that
are rolled back. Run with the same dedicated-account runner as the other rehearsal modules (MASGOLD_BOOK_REHEARSAL=1).

    .\\.venv\\Scripts\\python.exe -B -m unittest -v tests.test_settle_melt_mysql

Evidence: schema objects and CHECK refusals, the whole cycle (issue without destination -> melt -> bar pool -> return of the rest -> closure refused while the bar stays
-> reversal), the display and decision reads of the RAW / BAR pools, and a two-connection race for the same gold."""
from decimal import Decimal as D
import os
import unittest

from repositories.market_trip_repository import MarketTripRepository
from services.correction_service import CorrectionError, CorrectionService
from services.market_trip_service import TripError
from tests.test_market_trip_book_mysql import BookCoreMySqlTests, DATE


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class SettleMeltMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        with cls.h.tx() as c:
            q = c.cursor()
            q.execute("SELECT MAX(version) FROM schema_migrations")
            assert int(q.fetchone()[0]) >= 60
            q.close()

    # ------------------------------------------------------------ helpers
    def lines(self, *rows):
        return [{"karat_id": str(self.h.karats[k]), "weight": w} for k, w in rows]

    def trip(self, *rows):
        """A LEGACY trip holding gold that left WITHOUT a destination (stage 1)."""
        done = self.h.service.issue("", "", self.lines(*rows), DATE, "060 rehearsal", self.h.key())
        return done["trip_id"]

    def melt(self, trip, rows=None, weight="44.8", fineness="850", service=None, key=None):
        return (service or self.h.service).settle_melt(trip, self.lines(*(rows or [(21, "40"), (18, "5")])), weight, fineness, "ورشة 060", DATE, "", key or self.h.key())

    def custody(self, trip):
        return self.h.service.trip_detail(trip)["custody"]

    def gold(self, trip):
        return {(g["karat_value"], g["designation"]): g["outstanding"] for g in self.custody(trip)["gold"]}

    # ------------------------------------------------------------ schema
    def test_the_objects_of_060_exist_and_the_check_constraints_refuse_bad_documents(self):
        tables = {r["TABLE_NAME"] for r in self.h.query("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()")}
        for name in ("market_trip_settle_melts", "market_trip_settle_melt_inputs", "market_trip_settle_moves"):
            self.assertIn(name, tables)
        clause = self.h.query("SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() AND CONSTRAINT_NAME = 'chk_mte_kind'")[0]["CHECK_CLAUSE"]
        self.assertIn("ST_MELT", clause)
        trip = self.trip((21, "10"))
        done = self.melt(trip, [(21, "5")], "4.9", "800")
        entry = int(done["entry_id"])
        self.h.refuses("UPDATE market_trip_settle_melts SET loss_eq21 = loss_eq21 + 1 WHERE entry_id=%s", (entry,))                    # chk_mtstm_loss
        self.h.refuses("UPDATE market_trip_settle_melts SET fineness_permille = 1001 WHERE entry_id=%s", (entry,))                      # chk_mtstm_fineness
        self.h.refuses("UPDATE market_trip_settle_melts SET workshop = '  ' WHERE entry_id=%s", (entry,))                               # chk_mtstm_workshop
        self.h.refuses("INSERT INTO market_trip_settle_moves(entry_id, entry_kind, trip_id, pool, karat_id, delta) VALUES (%s,'ST_MELT',%s,'BAR',1,1)", (entry, trip))   # chk_mtsm_pool
        self.h.refuses("INSERT INTO market_trip_settle_moves(entry_id, entry_kind, trip_id, pool, karat_id, delta) VALUES (%s,'ST_MELT',%s,'BAR',NULL,0)", (entry, trip))  # chk_mtsm_delta
        self.h.refuses("INSERT INTO market_trip_settle_moves(entry_id, entry_kind, trip_id, pool, karat_id, delta) VALUES (%s,'ST_MELT',%s,'BAR',NULL,1)", (entry, int(trip) + 1000000),
                       errnos=(1452, 3819, 1451, 1048, 1264, 1265, 1406))                                                                  # fk_mtsm_entry: the trip must be the entry's

    def test_a_st_melt_entry_cannot_exist_on_a_book_trip(self):
        trip = self.h.book_trip("5")
        self.h.refuses("INSERT INTO market_trip_entries(trip_id, trip_mode, entry_no, entry_kind, entry_date, request_key, request_hash, performed_by_user_id, occurred_at) "
                       "VALUES (%s,'BOOK',99,'ST_MELT',CURDATE(),UUID(),'x',%s,NOW())", (trip, self.h.user))                                # chk_mte_mode_kind

    # ------------------------------------------------------------ the whole cycle on real SQL
    def test_issue_without_destination_melt_bar_pool_return_closure_and_reversal(self):
        trip = self.trip((21, "50"), (18, "10"))
        self.assertEqual(self.gold(trip), {(21, "UNCLASSIFIED"): D("50.000"), (18, "UNCLASSIFIED"): D("10.000")})
        done = self.melt(trip)                                                                      # the owner's worked example
        self.assertEqual((done["figures"]["theoretical_eq21"], done["figures"]["actual_eq21"], done["figures"]["loss_eq21"]), ("44.286", "43.520", "0.766"))
        self.assertEqual((done["figures"]["weight_loss_eq21"], done["figures"]["fineness_loss_eq21"]), ("0.197", "0.569"))
        self.assertEqual(self.gold(trip), {(21, "UNCLASSIFIED"): D("10.000"), (18, "UNCLASSIFIED"): D("5.000")})
        self.assertEqual(self.custody(trip)["bar"], D("43.520"))
        with self.h.tx() as c:                                                                      # the DISPLAY aggregation sees the same pools as the decision read
            repo = MarketTripRepository(c)
            self.assertEqual(repo.settle_bar_by_trip()[int(trip)], D("43.520"))
            raw = {(r["karat_value"]): D(str(r["settle_out"])) for r in repo.custody_gold_by_trip()[int(trip)] if r["designation"] == "UNCLASSIFIED"}
            self.assertEqual(raw, {21: D("40.000"), 18: D("5.000")})                                  # the grams CONSUMED by the melt (outstanding = issued - returned - consumed)
        self.h.service.return_custody(trip, "", [{"karat_id": str(self.h.karats[21]), "weight": "10", "designation": "UNCLASSIFIED"},
                                                 {"karat_id": str(self.h.karats[18]), "weight": "5", "designation": "UNCLASSIFIED"}], DATE, "", self.h.key())
        with self.assertRaises(TripError) as refused:
            self.h.service.close(trip)                                                              # only the undistributed bar remains
        self.assertEqual(refused.exception.code, "OUTSTANDING_CUSTODY")
        self.assertIn("رصيد السبيكة", str(refused.exception))
        self.h.correct(done["entry_id"])                                                            # the ordinary full reversal
        self.assertEqual(self.custody(trip)["bar"], D("0.000"))
        self.assertEqual((self.gold(trip)[(21, "UNCLASSIFIED")], self.gold(trip)[(18, "UNCLASSIFIED")]), (D("40.000"), D("5.000")))      # 50-10 and 10-5: the reversed melt gives its grams back
        self.assertEqual(len(self.h.query("SELECT 1 FROM market_trip_settle_melts WHERE entry_id=%s", (done["entry_id"],))), 1)   # the document stays as history

    def test_the_issue_cannot_be_reversed_while_its_gold_is_melted(self):
        trip = self.trip((21, "20"))
        self.melt(trip, [(21, "10")], "9.9", "870")
        issue = self.h.query("SELECT id FROM market_trip_entries WHERE trip_id=%s AND entry_kind='ISSUE'", (trip,))[0]["id"]
        with self.assertRaises(CorrectionError) as refused:
            self.h.correct(issue)
        self.assertEqual(refused.exception.code, "DEPENDENT_RETURN_EXISTS")

    def test_replay_is_idempotent_and_a_changed_payload_conflicts(self):
        trip = self.trip((21, "20"))
        key = self.h.key()
        first = self.melt(trip, [(21, "10")], "9.9", "870", key=key)
        again = self.melt(trip, [(21, "10")], "9.9", "870", key=key)
        self.assertEqual((again["entry_id"], again["duplicate"]), (first["entry_id"], True))
        self.assertEqual(self.custody(trip)["bar"], D(first["figures"]["actual_eq21"]))                  # posted once
        with self.assertRaises(TripError) as conflict:
            self.melt(trip, [(21, "10")], "9.8", "870", key=key)
        self.assertEqual(conflict.exception.code, "REQUEST_CONFLICT")

    # ------------------------------------------------------------ the race
    def test_race_two_melts_of_the_same_gold_only_one_fits(self):
        trip = self.trip((21, "10"))
        sessions = [self.h.make_session(f"st{n}")[0] for n in range(2)]
        services = [self.h.build_service(s) for s in sessions]
        outcomes = self.h.race([lambda s=s: self.melt(trip, [(21, "7")], "6.9", "870", service=s)["entry_id"] for s in services])
        self.assertEqual(sorted(code for code, _ in outcomes), ["SETTLE_MELT_EXCEEDS_CUSTODY", "ok"])
        self.assertEqual(self.gold(trip)[(21, "UNCLASSIFIED")], D("3.000"))


if __name__ == "__main__":
    unittest.main()
