"""Supplier gold transfer in the market custody log (migration 109, entry kinds SUP_XFER / SUP_XFER_REV): the log row, the missing reverse button, the blocked correction and the static
contract of the migration. The service side (marker written in the newest open custody, no leg) is in tests/test_supplier_gold_transfer.py. No database is used."""
from datetime import date, datetime
from decimal import Decimal as D
import re
import unittest

from app.viewmodels.market_trip_viewmodel import MarketTripViewModel
from services.market_trip_service import KIND_TEXT, MarketTripService
from services.correction_service import CorrectionError
from tests import migration_sql as sql
from tests.market_trip_fakes import TripRepo
from tests.test_market_trip_gold_settle import GoldSettleCase
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain


def stored_entry(kind="SUP_XFER", reverses_id=None):
    return {"id": 501, "trip_id": 10, "entry_no": 9, "entry_kind": kind, "entry_date": date(2026, 11, 24), "notes": "GT000001", "occurred_at": datetime(2026, 11, 24, 12, 0),
            "actor_name": "admin", "cash_amount": None, "gold": [], "reversal_id": None, "reversal_reason": None, "reversal_at": None,
            "supplier_transfer": {"transfer_id": 1, "reverses_id": reverses_id, "from_karat_value": 21, "to_karat_value": 18, "from_weight_grams": D("10.000"),
                                  "to_weight_grams": D("11.667"), "from_name": "المتحدة", "to_name": "مكرم"}}


def view_row(kind="SUP_XFER", reverses_id=None):
    row = MarketTripService._entry_row(stored_entry(kind, reverses_id))
    row.update(reversed=False, reversal_code=None, reversal_reason=None)
    return row


class LogRowTests(unittest.TestCase):
    def test_the_service_row_carries_the_transfer_figures(self):
        transfer = view_row()["supplier_transfer"]
        self.assertEqual((transfer["code"], transfer["from_name"], transfer["to_name"], transfer["transferred_weight"], transfer["credited_weight"], transfer["is_reversal"]),
                         ("GT000001", "المتحدة", "مكرم", D("10.000"), D("11.667"), False))
        self.assertTrue(view_row("SUP_XFER_REV", reverses_id=1)["supplier_transfer"]["is_reversal"])

    def test_the_kind_has_a_label_in_the_service_and_the_view_model(self):
        self.assertEqual(KIND_TEXT["SUP_XFER"], "تسوية ذهب من حساب مورد")
        shown = MarketTripViewModel._entry(view_row(), True)
        self.assertEqual(shown["kindText"], "تسوية ذهب من حساب مورد")
        self.assertEqual(MarketTripViewModel._entry(view_row("SUP_XFER_REV", 1), True)["kindText"], "عكس تسوية ذهب من حساب مورد")

    def test_the_log_text_names_both_accounts_and_says_the_custody_gold_is_untouched(self):
        what = MarketTripViewModel._entry(view_row(), True)["what"]
        for needle in ("GT000001", "من حساب المتحدة", "10.000 جرام عيار 21", "11.667 جرام عيار 18", "للمورد مكرم", "لا أثر على ذهب العهدة"):
            self.assertIn(needle, what)
        reversal = MarketTripViewModel._entry(view_row("SUP_XFER_REV", 1), True)["what"]
        self.assertIn("عُكست التسوية", reversal)
        self.assertIn("لا أثر على ذهب العهدة", reversal)

    def test_a_marker_never_offers_the_custody_reverse_button(self):
        self.assertFalse(MarketTripViewModel._entry(view_row(), True)["canReverse"])
        self.assertFalse(MarketTripViewModel._entry(view_row("SUP_XFER_REV", 1), True)["canReverse"])

    def test_an_ordinary_entry_is_unchanged(self):
        row = MarketTripService._entry_row({**stored_entry("ISSUE"), "supplier_transfer": None})
        row.update(reversed=False, reversal_code=None, reversal_reason=None)
        self.assertIsNone(row["supplier_transfer"])
        self.assertTrue(MarketTripViewModel._entry(row, True)["canReverse"])


class MarkerCorrectionTests(GoldSettleCase):
    """A marker is a log row only: reversing it as a custody entry is refused and points to the supplier page; nothing moves."""
    def marker(self, kind="SUP_XFER"):
        repository = TripRepo(self.db)
        return repository.insert_entry(trip_id=int(self.trip), entry_no=repository.next_entry_no(self.trip), kind=kind, entry_date=date(2026, 11, 24), notes="GT000001",
                                       key="00000000-0000-4000-8000-0000000000aa", digest="x", actor=self.session.current_actor(), now=datetime(2026, 11, 24, 12, 0))

    def test_a_marker_cannot_be_reversed_from_the_custody_and_changes_nothing(self):
        entry_id = self.marker()
        before = self.state()
        for kind_id in (entry_id,):
            with self.assertRaises(CorrectionError) as refused:
                self.service.reverse(str(kind_id), "من العهدة")
            self.assertEqual(refused.exception.code, "SUPPLIER_TRANSFER_REVERSE_FROM_SUPPLIER")
            self.assertIn("صفحة المورد", str(refused.exception))
        self.assertEqual(self.state(), before)

    def test_a_marker_changes_no_custody_balance(self):
        gold, cash = self.service.trip_detail(self.trip)["custody"]["gold"], self.service.trip_detail(self.trip)["custody"]["cash"]
        self.marker()
        detail = self.service.trip_detail(self.trip)
        self.assertEqual((detail["custody"]["gold"], detail["custody"]["cash"]), (gold, cash))
        self.assertEqual([e["kind"] for e in detail["entries"]][:1], ["SUP_XFER"])


class MigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read("109_supplier_gold_transfer_custody_log.sql")
        cls.rollback = sql.read("109_supplier_gold_transfer_custody_log_rollback.sql", sql.ROLLBACK_DIR)

    def test_the_chain_replays_and_109_requires_108(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("MIGRATION_109_REFUSED_missing_108", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(109,")
        self.assertLessEqual(len(re.search(r"VALUES \(109,'([^']*)'", self.forward).group(1)), 150)
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 112)

    def test_both_entry_checks_gain_the_two_marker_kinds_for_legacy_trips_only(self):
        for name in ("chk_mte_kind", "chk_mte_mode_kind"):
            add = re.search(rf'ADD CONSTRAINT {name} CHECK (.*?)", \'DO 0\'', self.forward, re.S).group(1)
            self.assertIn("'SUP_XFER','SUP_XFER_REV'", add)
        mode = re.search(r"ADD CONSTRAINT chk_mte_mode_kind CHECK (.*?)\", 'DO 0'", self.forward, re.S).group(1)
        legacy, book = mode.split("trip_mode = 'BOOK'")
        self.assertIn("SUP_XFER", legacy)
        self.assertNotIn("SUP_XFER", book)
        self.assertIn("NOT LIKE '%SUP_XFER%'", self.forward)                                   # guarded and re-run safe

    def test_the_link_table_ties_one_marker_to_one_transfer_row(self):
        for needle in ("CREATE TABLE IF NOT EXISTS supplier_gold_transfer_entries", "PRIMARY KEY (transfer_id)", "UNIQUE KEY uq_sgte_entry (entry_id)",
                       "REFERENCES supplier_gold_transfers(id)", "REFERENCES market_trip_entries(id)"):
            self.assertIn(needle, self.forward)
        self.assertNotRegex(self.forward.replace("ON DUPLICATE KEY UPDATE", ""), r"(?i)\b(UPDATE|DELETE)\b\s")

    def test_the_rollback_refuses_when_a_marker_exists_and_restores_the_105_checks(self):
        for needle in ("ROLLBACK_109_REFUSED_transfer_markers_exist", "version > 109", "DELETE FROM schema_migrations WHERE version = 109", "DROP TABLE IF EXISTS supplier_gold_transfer_entries"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_109_REFUSED_transfer_markers_exist"), self.rollback.index("DROP TABLE"))
        self.assertIn("CHECK_CLAUSE LIKE '%SUP_XFER%'", self.rollback)
        add_lists = "".join(re.findall(r'ADD CONSTRAINT chk_mte\w+ CHECK .*', self.rollback))
        self.assertNotIn("SUP_XFER", add_lists)


if __name__ == "__main__":
    unittest.main()
