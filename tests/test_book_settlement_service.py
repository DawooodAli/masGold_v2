"""R2-S1 reconciliation SETTLEMENT through the REAL MarketTripBookService / CorrectionService over the in-memory transactional database of tests/market_trip_fakes.py
(a failed transaction restores every table; the 057 CHECK / unique / key rules are mirrored as asserts). NOT real-MySQL tests: what fakes show says nothing about the SQL, the
locks or the races of the real engine, and a fake never proves a lock; those checks are listed in the MySQL verification plan and are NOT run here.

Covers: the path is DISABLED by default and touches no new table; the policy boundaries end to end; atomic posting (entry + record + audit) and rollback at every step; replay /
conflict / permission / session; stale, not-ready and defective sources; the date window and the midnight retry; finality for gold documents (and not for cash); reversal, a new
generation and the protection of history."""
import copy
from datetime import date, datetime
from decimal import Decimal as D
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from repositories.request_keys import RequestKeyInUse
from services import book_settlement as bs
from services.correction_service import CorrectionError
from services.market_trip_service import TripError
from tests.market_trip_fakes import OWNER, TripRepo, build, session_for
from tests.test_market_trip_book import K18, K21, TODAY, BookCase, assay, karat, key

ROOT = Path(__file__).resolve().parents[1]
ALL = {k: True for k in bs.CONFIRMATION_KEYS}
VERSIONS = dict(bs.CONTRACT_VERSIONS)
RECON_TABLES = ("reconciliations",)


class ReconCase(BookCase):
    def setUp(self):
        super().setUp()
        self.service._reconciliation_enabled = True

    # ------------------------------------------------------------ scenarios
    def zero_trip(self):
        """A BOOK trip with A = 0 (issued and returned), and custody cash for purchases."""
        trip = self.issue([karat(K21, "1")])["trip_id"]
        self.ret(trip, [karat(K21, "1")])
        self.cash(trip, "100")
        return trip

    def healthy_trip(self):
        """10 g of 21K issued, 4 g returned: A = 6 g, L = 6.000 (a real loss: a reason is mandatory)."""
        trip = self.issue([karat(K21, "10")])["trip_id"]
        self.ret(trip, [karat(K21, "4")])
        return trip

    def digest(self, trip):
        source = TripRepo(self.db).reconciliation_source_current(trip)
        return bs.build(source)[3]

    def reconcile(self, trip, **over):
        args = {"source_digest": None, "effective_date": None, "reason_code": "OPERATING_LOSS", "note": None, "confirmations": dict(ALL), "contract_versions": dict(VERSIONS),
                "request_key": None}
        args.update(over)
        if args["source_digest"] is None:
            args["source_digest"] = self.digest(trip)
        if args["request_key"] is None:
            args["request_key"] = self.k()
        return self.service.reconcile(trip, args["source_digest"], args["effective_date"], args["reason_code"], args["note"], args["confirmations"],
                                      args["contract_versions"], args["request_key"])

    def trace(self):
        """Everything a refused request must leave untouched."""
        return self.state()

    def refused_recon(self, code, call, error=TripError):
        return self.refused(code, call, error=error)

    def recon_rows(self):
        return list(self.db.reconciliations.values())

    def entries_of(self, kind):
        return [e for e in self.db.entries.values() if e["entry_kind"] == kind]


# --------------------------------------------------------------------------- disabled by default
class DisabledByDefaultTests(unittest.TestCase):
    def setUp(self):
        self.db, self.session, self.authorization, self.service, self.corrections = build(book=True)       # production flag: reconciliation_enabled stays False
        self.n = 0
        from tests.market_trip_fakes import AccountsRepo, HoldingRepo, SupplierPayRepo
        self.service._price_reference = lambda: {21: D("6000.0000"), 18: D("5142.8571"), 24: D("7000.0000")}
        for target, value in (("repositories.market_trip_repository.MarketTripRepository", TripRepo),
                              ("repositories.supplier_payment_repository.SupplierPaymentRepository", SupplierPayRepo),
                              ("repositories.financial_account_repository.FinancialAccountRepository", AccountsRepo),
                              ("repositories.intermediary_holding_repository.IntermediaryHoldingRepository", HoldingRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)

    def test_the_default_is_off_and_the_refusal_comes_before_any_transaction(self):
        self.assertFalse(self.service._reconciliation_enabled)
        trip = self.service.book_issue("", [karat(K21, "5")], TODAY, "", key(1))["trip_id"]
        before, transactions = copy.deepcopy({k: v for k, v in vars(self.db).items() if k not in ("fail_at", "transactions", "lock_log")}), self.db.transactions
        with self.assertRaises(TripError) as refused:
            self.service.reconcile(trip, "a" * 64, None, "OPERATING_LOSS", None, dict(ALL), dict(VERSIONS), key(2))
        self.assertEqual(refused.exception.code, "RECONCILIATION_NOT_ACTIVE")
        self.assertEqual(self.db.transactions, transactions)                       # no transaction was even opened: no table of any kind was read
        self.assertEqual(before, copy.deepcopy({k: v for k, v in vars(self.db).items() if k not in ("fail_at", "transactions", "lock_log")}))

    def test_the_new_table_is_never_read_or_written_by_the_existing_paths(self):
        """With the path disabled the whole BOOK flow, the R1 preview, the listing, a reversal and the legacy flows never call a method that touches the 057 table."""
        forbidden = ("reconciliation_source_current", "next_reconciliation_generation", "insert_reconciliation", "reconciliation_of_entry")
        calls = []
        patches = [patch.object(TripRepo, name, side_effect=lambda *a, _n=name, **k: calls.append(_n) or (_ for _ in ()).throw(AssertionError(_n))) for name in forbidden]
        for started in patches:
            started.start()
            self.addCleanup(started.stop)
        trip = self.service.book_issue("", [karat(K21, "10")], TODAY, "", key(10))["trip_id"]
        self.service.issue(trip, "100", [], TODAY, "", key(11))
        back = self.service.book_return(trip, [karat(K21, "4")], TODAY, "", key(12))
        self.service.book_buy(trip, [assay("900", "1", "10")], "", "x", TODAY, "", "", key(13), True)
        self.service.book_sell(trip, [karat(K21, "1", "10")], "", "y", TODAY, "", "", key(14), True)
        self.service.reconciliation_preview(trip)
        self.service.trip_detail(trip)
        self.service.book_preview("BK_RETURN", trip, [karat(K21, "1")])
        self.service.reverse(back["entry_id"], "خطأ إدخال")
        self.service.issue("", "500", [], TODAY, "", key(15))
        self.assertEqual(calls, [])

    def test_legacy_flows_and_a_new_book_trip_never_ask_about_reconciliations(self):
        """LEGACY custody (create, issue, return, reversal) and the creation of a BOOK trip never reach the finality guard."""
        calls = []
        with patch.object(TripRepo, "effective_reconciliation_entries", side_effect=lambda *a, **k: calls.append(a) or []):
            legacy = self.service.issue("", "500", [], TODAY, "", key(30))
            second = self.service.issue(legacy["trip_id"], "100", [], TODAY, "", key(31))
            self.service.reverse(second["entry_id"], "خطأ إدخال")                        # a LEGACY reversal goes through CorrectionService without the BOOK finality guard
            self.service.book_issue("", [karat(K21, "5")], TODAY, "", key(32))
        self.assertEqual(calls, [])
        with patch.object(TripRepo, "effective_reconciliation_entries", side_effect=lambda *a, **k: calls.append(a) or []):
            trip = self.service.book_issue("", [karat(K21, "5")], TODAY, "", key(33))["trip_id"]
            self.service.book_return(trip, [karat(K21, "1")], TODAY, "", key(34))         # a gold document on an EXISTING BOOK trip does ask (once, under the trip lock)
        self.assertEqual(len(calls), 1)

    def test_the_finality_guard_reads_only_the_entry_table(self):
        """The guard added to the gold documents (and to their reversal) asks `effective_reconciliation_entries`, which reads market_trip_entries / transaction_corrections only."""
        text = (ROOT / "repositories" / "market_trip_repository.py").read_text(encoding="utf-8")
        body = text[text.index("    def effective_reconciliation_entries"):text.index("    def next_reconciliation_generation")]
        self.assertNotIn("market_trip_reconciliations", body)
        self.assertIn("FROM market_trip_entries", body)
        users = {}
        for name in ("reconciliation_source_current", "next_reconciliation_generation", "insert_reconciliation", "reconciliation_of_entry"):
            start = text.index(f"    def {name}")
            users[name] = text.index("\n    def ", start + 10)
        offenders = []
        for match in re.finditer(r"\n    def (\w+)\(", text):
            name = match.group(1)
            end = text.find("\n    def ", match.end())
            if "market_trip_reconciliations" in text[match.start():end if end > 0 else len(text)] and name not in ("next_reconciliation_generation", "insert_reconciliation", "reconciliation_of_entry",
                                                                                                                       "effective_reconciliations_display", "reconciliation_display", "book_report_rows"):   # display reads (Gold Position, documents list, report), each behind its own capability check
                offenders.append(name)
        self.assertEqual(offenders, [])

    def test_no_production_code_turns_the_flag_on(self):
        offenders = []
        for folder in ("app", "services", "repositories", "qml", "main.py", "tools"):
            path = ROOT / folder
            files = [path] if path.is_file() else [p for p in path.rglob("*") if p.suffix in (".py", ".qml")]
            for file in files:
                if re.search(r"reconciliation_enabled\s*=\s*True", file.read_text(encoding="utf-8", errors="ignore")):
                    offenders.append(file.relative_to(ROOT).as_posix())
        self.assertEqual(offenders, [])
        import inspect
        parameters = inspect.signature(self.service.reconcile).parameters
        self.assertNotIn("enabled", " ".join(parameters))


# --------------------------------------------------------------------------- the posting
class PostingTests(ReconCase):
    def test_a_reconciliation_posts_the_entry_the_record_and_the_audit_and_nothing_else(self):
        trip = self.healthy_trip()
        before = self.state()
        result = self.reconcile(trip, note="جرد", reason_code="OPERATING_LOSS")
        self.assertEqual((result["kind"], result["duplicate"], result["reversed"], result["generation"]), ("BK_RECON", False, False, 1))
        self.assertEqual((result["a_star_units"], result["a_star"], result["b_star"], result["loss_posted"], result["rounding_settlement"]),
                         ("157500000", "6.000000", "6.000", "6.000", "0.000"))
        after = self.state()
        for name in ("scrap", "cash_moves", "cash_legs", "book_lines", "book_trades", "assayed_moves", "trips", "events", "corrections", "gold_legs"):
            self.assertEqual(after[name], before[name], name)                                  # no Scrap, cash, stock, line, trade, status or correction effect
        self.assertEqual(self.db.trips[int(trip)]["status"], "OPEN")
        entry = self.db.entries[int(result["entry_id"])]
        self.assertEqual((entry["entry_kind"], entry["trip_mode"], entry["entry_date"], entry["notes"]), ("BK_RECON", "BOOK", date(2026, 10, 10), "جرد"))
        row = self.db.reconciliations[int(result["entry_id"])]
        self.assertEqual((row["generation"], row["effective_date"], row["date_floor"], row["date_floor_source"], row["confirmed_by_user_id"], row["confirmed_at"]),
                         (1, date(2026, 10, 10), date(2026, 10, 10), bs.FLOOR_LAST_GOLD_ENTRY, 7, self.db.now))
        self.assertEqual(bs.verify_record(row), [])                                            # the record, its snapshot and its digests are consistent
        audit = self.db.audit_rows[-1]
        self.assertEqual((audit["event_type"], audit["entity_id"]), ("MARKET_TRIP_BK_RECON", int(result["entry_id"])))
        meta = audit["metadata"]
        self.assertEqual((meta["source_digest"], meta["snapshot_digest"], meta["contract_versions"], meta["confirmations"]),
                         (row["source_digest"], row["snapshot_digest"], VERSIONS, ALL))
        self.assertNotIn("snapshot", meta)                                                     # the audit carries the digests, never the snapshot body
        self.assertIn("حتى لو كان L صفرًا", meta["confirmation_texts"]["loss_recognised_as_posted"])

    def test_a_reconciled_trip_with_zero_cash_closes(self):
        trip = self.healthy_trip()
        self.reconcile(trip)
        self.assertEqual(self.service.close(trip)["status"], "CLOSED")

    def test_the_documents_a_trip_shows_include_the_reconciliation_without_failing(self):
        trip = self.healthy_trip()
        result = self.reconcile(trip)
        detail = self.service.trip_detail(trip)
        self.assertIn(result["code"], [e["code"] for e in detail["entries"]])
        self.assertEqual(self.service.reconciliation_preview(trip)["aState"], "positive")        # the R1 preview ignores BK_RECON and is unchanged

    def test_r1_preview_is_unchanged_by_a_reconciliation_and_its_reversal(self):
        trip = self.healthy_trip()
        before = {k: v for k, v in self.service.reconciliation_preview(trip).items() if k not in ("status",)}
        first = self.reconcile(trip)
        self.assertEqual({k: v for k, v in self.service.reconciliation_preview(trip).items() if k != "status"}, before)
        self.service.reverse(first["entry_id"], "إعادة")
        self.assertEqual({k: v for k, v in self.service.reconciliation_preview(trip).items() if k != "status"}, before)


class PolicyBoundaryTests(ReconCase):
    def buy_only(self, rows):
        trip = self.zero_trip()
        self.buy(trip, rows, ack=True)
        return trip

    def test_a_below_precision_residual_is_stored_confirmed_and_needs_no_reason(self):
        trip = self.buy_only([assay("100", "0.001", "1.00")])                                   # A = 1/8750 g = 3,000 units, L = 0
        result = self.reconcile(trip, reason_code=None)
        row = self.db.reconciliations[int(result["entry_id"])]
        self.assertEqual((row["a_star_units"], row["loss_posted"], row["b_star"], row["rounding_settlement"], row["reason_code"]), (3000, D("0.000"), D("0.000"), D("0.000"), None))
        self.assertEqual(row["a_star"], D("0.000114"))                                         # A is kept and shown even though the posted loss is zero
        self.assertEqual(bs.verify_record(row), [])

    def test_one_unit_below_the_half_milligram_still_needs_no_reason_and_keeps_a_positive_rounding_settlement(self):
        self.db.karats[4]["is_active"] = 1                                                      # 22K is a master karat (the only one that reaches 13,124 units)
        trip = self.zero_trip()
        self.buy(trip, [assay("81.1", "0.028", "1.00")], ack=True)
        self.sell(trip, [karat("4", "0.002", "1.00")], ack=True)
        result = self.reconcile(trip, reason_code=None)
        row = self.db.reconciliations[int(result["entry_id"])]
        self.assertEqual((row["a_star_units"], row["loss_posted"], row["b_star"], row["rounding_settlement"]), (13124, D("0.000"), D("0.001"), D("0.001")))

    def test_exactly_half_a_milligram_requires_a_reason_and_a_refusal_leaves_no_trace(self):
        trip = self.buy_only([assay("437.5", "0.001", "1.00")])                                 # A = 13,125 units, L = 0.001
        self.refused_recon("REASON_REQUIRED", lambda: self.reconcile(trip, reason_code=None))
        result = self.reconcile(trip, reason_code="TRADING_LOSS")
        row = self.db.reconciliations[int(result["entry_id"])]
        self.assertEqual((row["a_star_units"], row["loss_posted"], row["b_star"], row["rounding_settlement"], row["reason_code"]),
                         (13125, D("0.001"), D("0.001"), D("0.000"), "TRADING_LOSS"))

    def test_the_reviewers_example_b_zero_loss_milli_rounding_minus_milli(self):
        trip = self.zero_trip()
        self.buy(trip, [assay("87.5", "0.014", "1.00")], ack=True)
        self.sell(trip, [assay("437.5", "0.001", "1.00")], ack=True)
        result = self.reconcile(trip, reason_code="PURITY_DIFFERENCE")
        row = self.db.reconciliations[int(result["entry_id"])]
        self.assertEqual((row["a_star_units"], row["b_star"], row["loss_posted"], row["rounding_settlement"], row["delta_units"], row["rho_units"]),
                         (23625, D("0.000"), D("0.001"), D("-0.001"), 2625, -23625))
        self.assertEqual(bs.verify_record(row), [])

    def test_reasons_and_notes_end_to_end(self):
        trip = self.healthy_trip()
        self.refused_recon("NOTE_REQUIRED", lambda: self.reconcile(trip, reason_code="OTHER", note="   "))
        self.refused_recon("NOTE_REQUIRED", lambda: self.reconcile(trip, reason_code="UNEXPLAINED"))
        self.refused_recon("REASON_INVALID", lambda: self.reconcile(trip, reason_code="WHATEVER"))
        self.refused_recon("TEXT_LONG", lambda: self.reconcile(trip, note="x" * 1001))
        self.refused_recon("REASON_REQUIRED", lambda: self.reconcile(trip, reason_code=None))
        result = self.reconcile(trip, reason_code="UNEXPLAINED", note="غير معروف بعد")
        self.assertEqual((result["reason_code"], result["reason"], result["note"]), ("UNEXPLAINED", "غير مفسَّر", "غير معروف بعد"))

    def test_the_three_confirmations_are_required_before_anything_is_read(self):
        trip = self.healthy_trip()
        transactions = self.db.transactions
        for bad in ({}, {**ALL, "recording_complete": False}, {**ALL, "loss_recognised_as_posted": None}, {k: "yes" for k in ALL}, None):
            self.refused_recon("RECONCILIATION_CONFIRM_REQUIRED", lambda bad=bad: self.reconcile(trip, confirmations=bad))
        self.refused_recon("RECONCILIATION_CONTRACT_MISMATCH", lambda: self.reconcile(trip, contract_versions={**VERSIONS, "recon_algorithm": "book-recon-v0"}))
        self.refused_recon("DIGEST_INVALID", lambda: self.reconcile(trip, source_digest="A" * 64))
        self.refused_recon("DIGEST_INVALID", lambda: self.reconcile(trip, source_digest="short"))
        self.refused_recon("DATE_INVALID", lambda: self.reconcile(trip, effective_date="not-a-date"))
        self.refused_recon("REQUEST_INVALID", lambda: self.reconcile(trip, request_key="not-a-uuid"))
        self.assertEqual(self.db.transactions, transactions)


class DateTests(ReconCase):
    def test_an_omitted_date_is_the_server_date_and_a_retry_after_midnight_returns_the_original(self):
        trip = self.healthy_trip()
        self.db.now = datetime(2026, 10, 10, 23, 59, 58)
        digest, request = self.digest(trip), self.k()
        first = self.reconcile(trip, source_digest=digest, request_key=request)
        self.assertEqual(first["effective_date"], "2026-10-10")
        self.db.now = datetime(2026, 10, 11, 0, 0, 3)                                           # midnight passed before the retry
        again = self.reconcile(trip, source_digest=digest, request_key=request)
        self.assertEqual((again["duplicate"], again["entry_id"], again["effective_date"]), (True, first["entry_id"], "2026-10-10"))
        self.assertEqual(len(self.recon_rows()), 1)

    def test_an_explicit_date_equal_to_the_stored_one_is_a_different_payload(self):
        trip = self.healthy_trip()
        digest, request = self.digest(trip), self.k()
        self.reconcile(trip, source_digest=digest, request_key=request)
        self.refused_recon("REQUEST_CONFLICT", lambda: self.reconcile(trip, source_digest=digest, request_key=request, effective_date="2026-10-10"))

    def test_the_window_floor_is_the_last_gold_document_reversed_ones_included(self):
        trip = self.service.book_issue("", [karat(K21, "10")], "2026-10-03", "", self.k())["trip_id"]
        back = self.service.book_return(trip, [karat(K21, "4")], "2026-10-07", "", self.k())
        self.service.reverse(back["entry_id"], "خطأ")                                           # the reversed return still dates the floor
        self.service.book_return(trip, [karat(K21, "4")], "2026-10-05", "", self.k())
        self.cash(trip)                                                                          # a cash entry dated TODAY is NOT a gold document
        self.refused_recon("EFFECTIVE_DATE_RANGE", lambda: self.reconcile(trip, effective_date="2026-10-06"))
        result = self.reconcile(trip, effective_date="2026-10-07")
        row = self.db.reconciliations[int(result["entry_id"])]
        self.assertEqual((row["effective_date"], row["date_floor"], row["date_floor_source"]), (date(2026, 10, 7), date(2026, 10, 7), bs.FLOOR_LAST_GOLD_ENTRY))
        self.assertEqual(self.db.entries[int(result["entry_id"])]["entry_date"], date(2026, 10, 7))

    def test_a_past_date_inside_the_window_and_today_are_accepted_a_future_date_is_not(self):
        trip = self.service.book_issue("", [karat(K21, "10")], "2026-10-04", "", self.k())["trip_id"]
        self.refused_recon("EFFECTIVE_DATE_RANGE", lambda: self.reconcile(trip, effective_date="2026-10-11"))
        self.refused_recon("EFFECTIVE_DATE_RANGE", lambda: self.reconcile(trip, effective_date="2026-10-03"))
        result = self.reconcile(trip, effective_date="2026-10-08")
        self.assertEqual(result["effective_date"], "2026-10-08")

    def test_a_trip_without_gold_documents_uses_its_creation_date(self):
        trip = TripRepo(self.db).insert_trip(notes=None, actor=type("A", (), {"user_id": 7})(), now=datetime(2026, 10, 2, 9, 0), mode="BOOK")
        self.db.trips[trip]["created_at"] = datetime(2026, 10, 2, 9, 0)
        self.refused_recon("EFFECTIVE_DATE_RANGE", lambda: self.reconcile(trip, reason_code=None, effective_date="2026-10-01"))
        result = self.reconcile(trip, reason_code=None, effective_date="2026-10-02")
        row = self.db.reconciliations[int(result["entry_id"])]
        self.assertEqual((row["date_floor"], row["date_floor_source"], row["a_star_units"], row["entry_count"]), (date(2026, 10, 2), bs.FLOOR_TRIP_CREATED, 0, 0))

    def test_a_document_dated_after_the_server_date_makes_the_window_unavailable(self):
        trip = self.service.book_issue("", [karat(K21, "10")], "2026-10-20", "", self.k())["trip_id"]
        self.refused_recon("EFFECTIVE_DATE_RANGE", lambda: self.reconcile(trip))

    def test_a_source_entry_without_a_valid_date_refuses_instead_of_inventing_one(self):
        trip = self.healthy_trip()
        digest = self.digest(trip)
        self.db.entries[1]["entry_date"] = None
        self.refused_recon("RECONCILIATION_NOT_READY", lambda: self.reconcile(trip, source_digest=digest))


# --------------------------------------------------------------------------- readiness, digest, defects
class ReadinessTests(ReconCase):
    def setUp(self):
        super().setUp()
        self.trip = self.healthy_trip()
        self.digest0 = self.digest(self.trip)

    def test_a_changed_gold_document_makes_the_digest_stale_a_cash_entry_does_not(self):
        self.cash(self.trip, "250")                                                              # cash is outside the source digest
        self.assertEqual(self.digest(self.trip), self.digest0)
        self.ret(self.trip, [karat(K21, "1")])                                                   # a gold document changes it
        self.refused_recon("RECONCILIATION_STALE", lambda: self.reconcile(self.trip, source_digest=self.digest0))
        self.reconcile(self.trip)                                                                # the fresh digest is accepted

    def test_the_digest_of_an_old_trip_does_not_fit_another_trip(self):
        other = self.healthy_trip()
        self.refused_recon("RECONCILIATION_STALE", lambda: self.reconcile(other, source_digest=self.digest0))

    def test_defective_sources_are_refused_with_diagnostics_and_leave_no_trace(self):
        defects = {
            "missing Scrap leg": lambda: self.db.scrap.__delitem__(-1),
            "unsupported kind": lambda: self.db.entries.__setitem__(99, {"id": 99, "trip_id": int(self.trip), "trip_mode": "BOOK", "entry_no": 99, "entry_kind": "BK_NEXT_KIND",
                                                                         "entry_date": date(2026, 10, 10), "request_key": key(99), "request_hash": "", "performed_by_user_id": 7,
                                                                         "occurred_at": self.db.now}),
            "line weight changed": lambda: self.db.book_lines[0].__setitem__("weight_grams", D("10.001")),
            "line posted changed": lambda: self.db.book_lines[0].__setitem__("eq21_posted", D("9.999")),
            "line deleted": lambda: self.db.book_lines.pop(),
        }
        for name, defect in defects.items():
            with self.subTest(defect=name):
                saved = copy.deepcopy(self.db.__dict__)
                defect()
                before = self.state()
                with self.assertRaises(TripError) as refused:
                    self.service.reconcile(self.trip, self.digest0, None, "OPERATING_LOSS", None, dict(ALL), dict(VERSIONS), self.k())
                self.assertEqual(refused.exception.code, "RECONCILIATION_NOT_READY")
                self.assertTrue(refused.exception.diagnostics)
                self.assertEqual(self.state(), before)
                self.db.__dict__.update(saved)

    def test_a_negative_balance_is_an_integrity_alarm_never_a_gain_path(self):
        trip = self.healthy_trip()
        digest = self.digest(trip)
        for line in self.db.book_lines:                                                          # shrink the issue so the return exceeds it (a record defect)
            if line["trip_id"] == int(trip) and line["entry_kind"] == "BK_ISSUE":
                line["weight_grams"], line["eq21_posted"], line["eq21_analytical"], line["signed_eq21_posted"] = D("1.000"), D("1.000"), D("1.000000"), D("1.000")
        error = self.refused_recon("RECONCILIATION_NOT_READY", lambda: self.reconcile(trip, source_digest=digest))
        self.assertIn("A_NEGATIVE", [d["code"] for d in error.diagnostics])
        self.assertEqual(self.recon_rows(), [])

    def test_a_reversed_documents_defect_refuses_the_snapshot(self):
        trip = self.healthy_trip()
        extra = self.ret(trip, [karat(K21, "1")])
        self.service.reverse(extra["entry_id"], "خطأ")
        digest = self.digest(trip)
        for line in self.db.book_lines:
            if line["entry_id"] == int(extra["entry_id"]):
                line["eq21_posted"] = D("5.000")
        with self.assertRaises(bs.SettlementDataError):
            self.digest(trip)
        error = self.refused_recon("RECONCILIATION_NOT_READY", lambda: self.reconcile(trip, source_digest=digest))
        self.assertIn("SNAPSHOT_SOURCE_INVALID", [d["code"] for d in error.diagnostics])

    def test_programming_errors_are_not_swallowed_and_roll_everything_back(self):
        before = self.state()
        with patch.object(bs, "build", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.reconcile(self.trip, source_digest=self.digest0)
        self.assertEqual(self.state(), before)
        real = bs.compute

        def wrong(entries):
            settlement = real(entries)
            # shifting the analytical AND the posted figure by the same milligram keeps every internal identity true (so only the agreement with R1 can notice it)
            return bs.Settlement(settlement.a_in_units + 26250, settlement.a_out_units, settlement.p_in_milli + 1, settlement.p_out_milli, settlement.sum_abs_units,
                                 settlement.entry_count, settlement.line_count, settlement.reversed_count)
        with patch.object(bs, "compute", wrong):                                                 # the integer computation disagrees with R1: a defect, raised
            with self.assertRaises((bs.SettlementIntegrityError, RuntimeError)):
                self.reconcile(self.trip, source_digest=self.digest0)
        self.assertEqual(self.state(), before)

    def test_mode_status_and_existence_refusals(self):
        legacy = self.service.issue("", "500", [], TODAY, "", self.k())["trip_id"]
        self.refused_recon("TRIP_MODE_MISMATCH", lambda: self.reconcile(legacy, source_digest="a" * 64))
        self.refused_recon("TRIP_NOT_FOUND", lambda: self.reconcile("999", source_digest="a" * 64))
        self.db.trips[int(self.trip)]["status"] = "CLOSED"
        self.refused_recon("TRIP_CLOSED", lambda: self.reconcile(self.trip, source_digest=self.digest0))


# --------------------------------------------------------------------------- replay, authority, atomicity
class ReplayAndAuthorityTests(ReconCase):
    def setUp(self):
        super().setUp()
        self.trip = self.healthy_trip()

    def test_replay_conflict_and_foreign_keys(self):
        digest, request = self.digest(self.trip), self.k()
        first = self.reconcile(self.trip, source_digest=digest, request_key=request, note="أ")
        before = self.state()
        again = self.reconcile(self.trip, source_digest=digest, request_key=request, note="أ")
        self.assertEqual((again["duplicate"], again["entry_id"], again["snapshot_digest"]), (True, first["entry_id"], first["snapshot_digest"]))
        self.assertEqual(self.state(), before)                                                    # a replay writes nothing
        self.refused_recon("REQUEST_CONFLICT", lambda: self.reconcile(self.trip, source_digest=digest, request_key=request, note="ب"))
        self.refused_recon("REQUEST_CONFLICT", lambda: self.reconcile(self.trip, source_digest=digest, request_key=request, reason_code="TRADING_LOSS", note="أ"))
        other, _ = self.other_service(user_id=9)
        other._reconciliation_enabled = True
        with self.assertRaises(TripError) as refused:                                             # the same payload from ANOTHER user is not a replay
            other.reconcile(self.trip, digest, None, "OPERATING_LOSS", "أ", dict(ALL), dict(VERSIONS), request)
        self.assertEqual(refused.exception.code, "REQUEST_CONFLICT")
        used = self.db.entries[1]["request_key"]                                                  # a key an ordinary BOOK document used
        self.refused_recon("REQUEST_CONFLICT", lambda: self.reconcile(self.trip, source_digest=digest, request_key=used))
        self.assertEqual(len(self.recon_rows()), 1)

    def test_a_key_owned_by_a_marketer_document_is_refused_and_nothing_is_written(self):
        trip = self.healthy_trip()
        digest = self.digest(trip)
        self.db.mk_docs = {1: {"request_key": key(500)}}
        before = self.state()
        with self.assertRaises(RequestKeyInUse):
            self.reconcile(trip, source_digest=digest, request_key=key(500))
        self.assertEqual(self.state(), before)

    def test_a_replay_comes_before_the_state_refusals_but_a_new_request_does_not(self):
        digest, request = self.digest(self.trip), self.k()
        first = self.reconcile(self.trip, source_digest=digest, request_key=request)
        self.refused_recon("RECONCILIATION_EXISTS", lambda: self.reconcile(self.trip, source_digest=digest))          # S1: a NEW request is refused ...
        self.assertEqual(self.reconcile(self.trip, source_digest=digest, request_key=request)["entry_id"], first["entry_id"])   # ... the replay returns the original
        self.db.trips[int(self.trip)]["status"] = "CLOSED"                                         # S2
        self.refused_recon("TRIP_CLOSED", lambda: self.reconcile(self.trip, source_digest=digest))
        self.assertEqual(self.reconcile(self.trip, source_digest=digest, request_key=request)["duplicate"], True)
        self.db.trips[int(self.trip)]["status"] = "OPEN"
        self.service.reverse(first["entry_id"], "إعادة")                                           # after the reversal the replay still returns the original, flagged
        again = self.reconcile(self.trip, source_digest=digest, request_key=request)
        self.assertEqual((again["entry_id"], again["reversed"]), (first["entry_id"], True))

    def test_the_right_is_rechecked_in_the_transaction_and_the_session_must_still_be_current(self):
        digest = self.digest(self.trip)
        before = self.state()
        weak = session_for(frozenset({"APP_LOGIN"}), 12)
        from services.market_trip_book_service import MarketTripBookService
        from tests.auth_fakes import FakeAuthorization
        service = MarketTripBookService(weak, transaction_factory=self.db.transaction, repository=TripRepo, authorization=FakeAuthorization(weak), reconciliation_enabled=True)
        with self.assertRaises(PermissionError):
            service.reconcile(self.trip, digest, None, "OPERATING_LOSS", None, dict(ALL), dict(VERSIONS), self.k())
        self.authorization.permissions = {"APP_LOGIN"}                                             # revoked in the "database" while the session stays
        with self.assertRaises(PermissionError):
            self.reconcile(self.trip, source_digest=digest)
        self.authorization.permissions = set(OWNER)
        self.assertEqual(self.state(), before)
        with patch.object(type(self.session), "is_current", side_effect=[True, False]):           # the session ends before the commit
            with self.assertRaises(PermissionError):
                self.reconcile(self.trip, source_digest=digest)
        self.assertEqual(self.state(), before)

    def test_a_failure_at_any_step_rolls_the_whole_document_back(self):
        digest = self.digest(self.trip)
        for point in ("recon_read", "recon_generation", "entry", "reconciliation", "audit"):
            with self.subTest(step=point):
                before = self.state()
                self.db.fail_at = point
                with self.assertRaises(RuntimeError):
                    self.reconcile(self.trip, source_digest=digest)
                self.db.fail_at = None
                self.assertEqual(self.state(), before)
        self.assertEqual(self.reconcile(self.trip, source_digest=digest)["generation"], 1)

    def test_the_decision_reads_are_the_five_locked_sections_in_order_after_the_trip_lock(self):
        digest = self.digest(self.trip)
        self.db.lock_log.clear()
        self.reconcile(self.trip, source_digest=digest)
        log = self.db.lock_log
        self.assertEqual(log[0], ("trip",))                                                       # the trip row first
        recon = [i for i, entry in enumerate(log) if entry[0] in ("recon_effective", "recon_read")]
        self.assertEqual([log[i] for i in recon], [("recon_effective",), ("recon_read", "entries"), ("recon_read", "lines"), ("recon_read", "scrap"),
                                                   ("recon_read", "trades"), ("recon_read", "stock"), ("recon_read", "phase2"), ("recon_read", "gains")])   # 058 legs, then the 059 gain records, locked last
        self.assertGreater(recon[0], 0)


# --------------------------------------------------------------------------- finality, reversal, generations
class FinalityTests(ReconCase):
    def setUp(self):
        super().setUp()
        self.trip = self.healthy_trip()
        self.first = self.reconcile(self.trip)

    def test_gold_documents_are_refused_after_a_reconciliation_and_cash_is_not(self):
        before = self.state()
        for call in (lambda: self.issue(trip=self.trip), lambda: self.ret(self.trip), lambda: self.buy(self.trip, ack=True), lambda: self.sell(self.trip, ack=True)):
            self.refused("RECONCILIATION_FINAL", call)
        self.assertEqual(self.state(), before)
        self.cash(self.trip, "300")                                                              # cash keeps its existing guards (no new Main Safe effect from the reconciliation)
        self.assertEqual(self.state()["cash_legs"][-1]["amount"], D("300.00"))

    def test_a_gold_document_cannot_be_reversed_while_the_reconciliation_stands(self):
        issued = self.db.entries[1]
        error = self.refused("RECONCILIATION_FINAL", lambda: self.service.reverse(issued["id"], "خطأ"), error=CorrectionError)
        self.assertIn(self.first["code"], str(error))

    def test_a_closed_trip_keeps_the_closed_guard_first(self):
        self.db.trips[int(self.trip)]["status"] = "CLOSED"
        self.refused("TRIP_CLOSED", lambda: self.ret(self.trip))
        self.refused("TRIP_CLOSED", lambda: self.service.reverse(self.db.entries[1]["id"], "خطأ"), error=CorrectionError)
        self.refused("TRIP_CLOSED", lambda: self.service.reverse(self.first["entry_id"], "خطأ"), error=CorrectionError)

    def test_reversal_goes_through_the_canonical_correction_and_keeps_the_record(self):
        snapshot = copy.deepcopy(self.db.reconciliations)
        entries = copy.deepcopy(self.db.entries)
        cash, scrap = copy.deepcopy(self.db.cash_moves), copy.deepcopy(self.db.scrap)
        result = self.service.reverse(self.first["entry_id"], "تسوية خاطئة")
        self.assertTrue(result["code"].startswith("RV"))
        self.assertEqual(self.db.reconciliations, snapshot)                                       # the record and its snapshot are never rewritten or deleted
        self.assertEqual(self.db.entries, entries)
        self.assertEqual((self.db.cash_moves, self.db.scrap), (cash, scrap))                      # no money, Scrap or stock effect
        self.assertEqual(self.db.corrections[-1]["source_type"], "MARKET_TRIP_ENTRY")
        self.assertEqual(self.service.reconciliation_preview(self.trip)["aState"], "positive")
        self.refused("ALREADY_REVERSED", lambda: self.service.reverse(self.first["entry_id"], "مرة أخرى"), error=CorrectionError)
        self.assertEqual(bs.verify_record(self.db.reconciliations[int(self.first["entry_id"])]), [])

    def test_reversal_needs_a_reason_and_the_correction_right(self):
        self.refused("REASON_REQUIRED", lambda: self.service.reverse(self.first["entry_id"], "  "), error=CorrectionError)
        weak, _ = self.other_service(frozenset({"APP_LOGIN", "MARKET_TRIP_MANAGE"}), 14)
        with self.assertRaises((PermissionError, CorrectionError)):
            weak.reverse(self.first["entry_id"], "x")
        self.assertEqual(len(self.db.corrections), 0)

    def test_after_the_reversal_gold_documents_work_and_a_new_generation_can_be_created(self):
        self.service.reverse(self.first["entry_id"], "إعادة")
        later = self.ret(self.trip, [karat(K21, "1")])                                             # gold documents are possible again
        self.service.reverse(later["entry_id"], "تراجع")
        second = self.reconcile(self.trip)
        self.assertEqual(second["generation"], 2)
        self.assertNotEqual(second["entry_id"], self.first["entry_id"])
        self.assertEqual(sorted(r["generation"] for r in self.recon_rows()), [1, 2])
        self.refused("RECONCILIATION_EXISTS", lambda: self.reconcile(self.trip))                  # only ONE effective reconciliation at a time
        self.assertEqual(self.db.reconciliations[int(self.first["entry_id"])]["snapshot"], self.db.reconciliations[int(self.first["entry_id"])]["snapshot"])

    def test_the_generation_never_wraps(self):
        self.service.reverse(self.first["entry_id"], "إعادة")
        self.db.reconciliations[int(self.first["entry_id"])]["generation"] = bs.GENERATION_LIMIT
        before = self.state()
        self.refused("RECONCILIATION_GENERATION_EXHAUSTED", lambda: self.reconcile(self.trip))
        self.assertEqual(self.state(), before)

    def test_the_unique_generation_is_a_defence_not_the_rule(self):
        """Two records for one trip with the same generation are refused by the (mirrored) unique key; the rule 'one effective' lives in the service."""
        row = self.db.reconciliations[int(self.first["entry_id"])]
        self.service.reverse(self.first["entry_id"], "إعادة")
        repo = TripRepo(self.db)
        with self.assertRaises(Exception):
            with self.db.transaction():
                second = repo.insert_entry(trip_id=int(self.trip), entry_no=repo.next_entry_no(self.trip), kind="BK_RECON", entry_date=date(2026, 10, 10), notes=None,
                                           key=self.k(), digest="x", actor=type("A", (), {"user_id": 7, "employee_id": None})(), now=self.db.now)
                repo.insert_reconciliation(entry_id=second, trip_id=int(self.trip), generation=row["generation"], effective_date=row["effective_date"],
                                           date_floor=row["date_floor"], date_floor_source=row["date_floor_source"], a_star_units=row["a_star_units"], a_star=row["a_star"],
                                           b_star=row["b_star"], loss_posted=row["loss_posted"], rounding_settlement=row["rounding_settlement"], delta_units=row["delta_units"],
                                           rho_units=row["rho_units"], sum_abs_units=row["sum_abs_units"], entry_count=row["entry_count"], line_count=row["line_count"],
                                           reversed_count=row["reversed_count"], source_digest=row["source_digest"], snapshot_digest=row["snapshot_digest"],
                                           snapshot_text=row["snapshot"], reason_code=row["reason_code"], note=row["note"], user_id=7, confirmed_at=self.db.now,
                                           algorithm=row["algorithm"], source_algorithm=row["source_algorithm"], snapshot_format=row["snapshot_format"],
                                           unit_denominator=row["unit_denominator"])


class FakeMirrorTests(ReconCase):
    def test_the_mirrored_database_rules_refuse_what_the_sql_checks_would(self):
        trip = self.healthy_trip()
        result = self.reconcile(trip)
        row = copy.deepcopy(self.db.reconciliations[int(result["entry_id"])])
        repo = TripRepo(self.db)

        def attempt(**changes):
            with self.db.transaction():
                entry = repo.insert_entry(trip_id=int(trip), entry_no=repo.next_entry_no(trip), kind="BK_RECON", entry_date=date(2026, 10, 10), notes=None, key=self.k(),
                                          digest="x", actor=type("A", (), {"user_id": 7, "employee_id": None})(), now=self.db.now)
                data = {**row, **changes}
                repo.insert_reconciliation(entry_id=entry, trip_id=int(trip), generation=2, effective_date=data["effective_date"], date_floor=data["date_floor"],
                                           date_floor_source=data["date_floor_source"], a_star_units=data["a_star_units"], a_star=data["a_star"], b_star=data["b_star"],
                                           loss_posted=data["loss_posted"], rounding_settlement=data["rounding_settlement"], delta_units=data["delta_units"],
                                           rho_units=data["rho_units"], sum_abs_units=data["sum_abs_units"], entry_count=data["entry_count"], line_count=data["line_count"],
                                           reversed_count=data["reversed_count"], source_digest=data["source_digest"], snapshot_digest=data["snapshot_digest"],
                                           snapshot_text=data["snapshot"], reason_code=data["reason_code"], note=data["note"], user_id=7, confirmed_at=self.db.now,
                                           algorithm=data["algorithm"], source_algorithm=data["source_algorithm"], snapshot_format=data["snapshot_format"],
                                           unit_denominator=data["unit_denominator"])
        self.service.reverse(result["entry_id"], "إعادة")
        for name, change in {"upper-case digest": {"source_digest": row["source_digest"].upper()}, "identity broken": {"loss_posted": D("5.999"), "rounding_settlement": D("0.001")},
                             "reason missing with a loss": {"reason_code": None}, "blank note": {"note": "   "}, "date after confirmation": {"effective_date": date(2026, 10, 11)},
                             "floor after date": {"date_floor": date(2026, 10, 11)}, "bad evidence": {"a_star": D("6.000050")}}.items():
            with self.subTest(case=name):
                with self.assertRaises(AssertionError):
                    attempt(**change)


if __name__ == "__main__":
    unittest.main()
