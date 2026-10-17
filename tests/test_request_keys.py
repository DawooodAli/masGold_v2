"""The marketer request-key guard of the older flows, run through the REAL repository classes over a recording connection (no database, no SQL execution): it proves
which statements a guarded INSERT issues and in which order, i.e. that a marketer-owned key is refused BEFORE any INSERT of the older flow. It does NOT prove that the
SQL is valid for MySQL or anything about concurrent transactions (see REVIEW-STABILIZATION.md for the real-MySQL checks that remain)."""
from decimal import Decimal as D
import unittest

from repositories.intermediary_holding_repository import IntermediaryHoldingRepository
from repositories.market_trip_repository import MarketTripRepository
from repositories.marketer_custody_repository import MarketerCustodyRepository
from repositories.request_keys import RequestKeyInUse, marketer_key_in_use, require_no_marketer_key
from repositories.supplier_gold_settlement_repository import SupplierGoldSettlementRepository
from repositories.supplier_payment_repository import SupplierPaymentRepository

KEY = "11111111-1111-4111-8111-111111111111"
ACTOR = type("Actor", (), {"user_id": 7, "employee_id": None})()


class Cursor:
    def __init__(self, connection):
        self.connection, self.lastrowid, self.rowcount, self._row = connection, 1, 1, None

    def execute(self, sql, params=()):
        self.connection.statements.append(" ".join(sql.split()))
        text = self.connection.statements[-1]
        if "information_schema.TABLES" in text:
            self._row = {"n": self.connection.table}
        elif "FROM marketer_documents WHERE request_key" in text:
            self._row = {"n": self.connection.owned}
        else:
            self._row = {"n": 0, "used": 0}

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []

    def close(self):
        pass


class Connection:
    def __init__(self, table=1, owned=1):
        self.table, self.owned, self.statements = table, owned, []

    def cursor(self, dictionary=False):
        return Cursor(self)

    def inserts(self):
        return [s for s in self.statements if s.startswith("INSERT")]


POOL = {"id": 1, "party_id": 20, "lot_id": 3, "origin_melt_entry_id": 4, "karat_id": 1, "trip_id": 1, "weight_grams": D("1"), "fineness_permille": D("875")}
KARAT = {"id": 1, "karat_value": 21}


def holding_document(repo):
    return repo.insert_document(kind="HANDOVER", pool=POOL, trip_id=1, document_date="2026-10-17", reference="", notes="", confirmation_code=None,
                                confirmed_by=None, confirmed_at=None, key=KEY, digest="d", actor=ACTOR, now=None)


GUARDED = {
    "custody entry": (MarketTripRepository, lambda r: r.insert_entry(trip_id=1, entry_no=1, kind="ISSUE", entry_date="2026-10-17", notes="", key=KEY, digest="d",
                                                                       actor=ACTOR, now=None)),
    "holding document (IH)": (IntermediaryHoldingRepository, holding_document),
    "logo holding document (IG)": (IntermediaryHoldingRepository, lambda r: r.insert_logo_document(kind="HANDOVER", pool=POOL, weight=D("1"), document_date="2026-10-17",
                                                                                                     reference="", notes="", key=KEY, digest="d", actor=ACTOR, now=None)),
    "holding receipt (HR)": (IntermediaryHoldingRepository, lambda r: r.insert_receipt(pool=POOL, grams=D("1"), receipt_date="2026-10-17", reference="", notes="",
                                                                                       key=KEY, digest="d", actor=ACTOR, now=None)),
    "supplier gold settlement": (SupplierGoldSettlementRepository, lambda r: r.insert_header(supplier_id=23, delivered_karat=KARAT, target_karat=KARAT,
                                                                                              delivered_weight=D("1"), credited=D("1"), analytical=D("1"), difference=D("0"),
                                                                                              settlement_date="2026-10-17", reference="", notes="", key=KEY, digest="d",
                                                                                              actor=ACTOR, now=None)),
    "lot settlement": (SupplierGoldSettlementRepository, lambda r: r.insert_lot_header(supplier_id=23, lot={"id": 1, "entry_id": 1, "fineness_permille": 875,
                                                                                                             "weight_grams": D("1")}, target_karat=KARAT, credited=D("1"),
                                                                                         analytical=D("1"), difference=D("0"), settlement_date="2026-10-17", reference="",
                                                                                         notes="", key=KEY, digest="d", actor=ACTOR, now=None)),
    "holding settlement": (SupplierGoldSettlementRepository, lambda r: r.insert_holding_header(kind="HOLDING_LOGO", supplier_id=23, pool=POOL, target_karat=KARAT,
                                                                                                quantity=D("1"), credited=D("1"), analytical=D("1"), difference=D("0"),
                                                                                                declaration=None, self_confirmed=False, settlement_date="2026-10-17",
                                                                                                reference="", notes="", key=KEY, digest="d", actor=ACTOR, now=None)),
    "supplier making payment": (SupplierPaymentRepository, lambda r: r.insert_header(supplier_id=23, method="CUSTODY", cash_account_id=None, bank_account_id=None,
                                                                                       amount=D("1"), payment_date="2026-10-17", reference="", notes="", before=D("0"),
                                                                                       after=D("-1"), key=KEY, digest="d", actor=ACTOR, now=None)),
}


class HelperTests(unittest.TestCase):
    def test_a_marketer_owned_key_is_reported_and_refused(self):
        connection = Connection(table=1, owned=1)
        repo = MarketTripRepository(connection)
        self.assertTrue(marketer_key_in_use(repo._fetch_one, KEY))
        with self.assertRaises(RequestKeyInUse) as refused:
            require_no_marketer_key(repo._fetch_one, KEY)
        self.assertEqual(refused.exception.code, "REQUEST_CONFLICT")
        self.assertIsInstance(refused.exception, ValueError)                           # the workers show its Arabic message like any refusal

    def test_an_unused_key_passes_and_the_lookup_is_a_current_locking_read(self):
        connection = Connection(table=1, owned=0)
        repo = MarketTripRepository(connection)
        self.assertFalse(marketer_key_in_use(repo._fetch_one, KEY))
        self.assertTrue(connection.statements[-1].endswith("FOR SHARE"))

    def test_a_database_without_the_ledger_keeps_working(self):
        connection = Connection(table=0, owned=1)                                       # migration 054: no marketer_documents
        self.assertFalse(marketer_key_in_use(MarketTripRepository(connection)._fetch_one, KEY))
        self.assertEqual(len(connection.statements), 1)                                 # only the probe: the missing table is never queried


class GuardedInsertTests(unittest.TestCase):
    def test_every_older_insert_refuses_a_marketer_key_before_writing_anything(self):
        for name, (repository, insert) in GUARDED.items():
            with self.subTest(flow=name):
                connection = Connection(table=1, owned=1)
                with self.assertRaises(RequestKeyInUse):
                    insert(repository(connection))
                self.assertEqual(connection.inserts(), [], "an INSERT was issued before the guard refused")

    def test_an_unused_key_reaches_the_insert_after_the_guard(self):
        for name, (repository, insert) in GUARDED.items():
            with self.subTest(flow=name):
                connection = Connection(table=1, owned=0)
                try:
                    insert(repository(connection))
                except RequestKeyInUse:
                    self.fail("an unused key was refused")
                except Exception:
                    pass                                                                # later statements of a method may need richer rows than this recorder returns
                guard = next(i for i, s in enumerate(connection.statements) if "FROM marketer_documents WHERE request_key" in s)
                first_insert = next((i for i, s in enumerate(connection.statements) if s.startswith("INSERT")), None)
                self.assertTrue(first_insert is None or guard < first_insert)

    def test_a_database_still_on_054_does_not_break_the_older_inserts(self):
        for name, (repository, insert) in GUARDED.items():
            with self.subTest(flow=name):
                connection = Connection(table=0, owned=1)
                try:
                    insert(repository(connection))
                except RequestKeyInUse:
                    self.fail("the guard fired without the marketer ledger")
                except Exception:
                    pass
                self.assertFalse(any("marketer_documents WHERE" in s for s in connection.statements))


class MarketerSideTests(unittest.TestCase):
    def test_the_marketer_lookup_covers_every_older_flow_including_supplier_payments(self):
        connection = Connection()
        MarketerCustodyRepository(connection).key_used_elsewhere(KEY)
        sql = connection.statements[-1]
        for table in ("market_trip_entries", "intermediary_holding_documents", "logo_holding_documents", "holding_receipts", "supplier_gold_settlements",
                      "supplier_making_payments"):
            self.assertIn(f"FROM {table} WHERE request_key", sql)

    def test_the_older_settlement_lookup_now_includes_the_marketer_documents(self):
        connection = Connection(table=1, owned=1)
        self.assertTrue(SupplierGoldSettlementRepository(connection).request_key_used_elsewhere(KEY))
        self.assertTrue(any("FROM marketer_documents WHERE request_key" in s for s in connection.statements))


if __name__ == "__main__":
    unittest.main()
