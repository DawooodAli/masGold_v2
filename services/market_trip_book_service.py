"""Market-trip BOOK core (Phase 1, migration 056): the operations of a BOOK trip, whose gold is ONE number - the book balance in 21K-equivalent grams.

  BK_ISSUE   existing shop Scrap  -> trip      counted Scrap OUT per master-karat line; book + the posted equivalent of every line
  BK_BUY     trip cash            -> trip      custody cash - the total price; book + posted equivalents; the vendor is mandatory
  BK_SELL    trip                 -> buyer     custody cash + the total price; book - posted equivalents; the buyer is mandatory
  BK_RETURN  trip                 -> shop      MASTER_KARAT lines: counted Scrap IN of the SAME karat; FINENESS (permille) lines: an identified assayed-return
                                               stock item (physical facts + posted amount + origin, ledger shop_assayed_stock_movements); book - posted

Every line keeps its PHYSICAL facts and posts ONE equivalent computed once from them (services.book_rules: exact analytical value, 0.001 g ROUND_HALF_UP); a
document's figure is the sum of its posted lines. Availability is the exact analytical balance (a legitimate final output is never refused because independent
rounding leaves the POSTED book a little negative); the posted book is the sum of the stored posted amounts. Rounding drift is neither loss nor gain and is never
booked as either. No gain, loss, reconciliation or closure exists in this phase: closing a BOOK trip is refused (BOOK_CLOSURE_NOT_AVAILABLE).

Atomicity (one transaction per document): permission re-checked inside it (MARKET_TRIP_MANAGE, actor = the AppSession user) -> trip row lock -> exact replay of the
request key (before anything is derived) -> mode / status / counterparty (party row, shared lock) -> CURRENT locked reads (book lines, custody cash, Scrap per karat
ascending) -> checks -> the entry, its lines, the Scrap legs / the stock receipt, the trade document, the OPENED event of a new trip and the audit row. Any failure
rolls everything back. A changed payload, actor or kind under a used request key is REQUEST_CONFLICT; a key owned by another flow is refused by the one
entry-insert path (a marketer document, or an entry of another kind). There is no global cross-table key constraint (see the review notes): the guard is a
current locking read, not a proof against two simultaneous different-flow inserts.

A BOOK trip can be created ONLY by a BK_ISSUE with no trip id on a service built with `book_creation_enabled=True` (tests / the future release step). Normal
construction leaves it False: the production screens never create a BOOK trip and there is no mode input anywhere."""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
import hashlib
import json

from mysql.connector import Error as MySqlError, IntegrityError

from repositories.market_trip_repository import (BK_BUY, BK_ISSUE, BK_RETURN, BK_SELL, BOOK, ISSUE, OPEN, RETURN, entry_code, trip_code)
from services import book_reconciliation, book_rules, book_settlement, custody_rules
from services.market_trip_service import (KIND_TEXT, MAX_MONEY, MONEY_STEP, NOTES_MAX, PAYMENT_REFERENCE_MAX, PERMISSION,
                                          PRICE_WARNING_PERCENT, ZERO_MONEY, MarketTripService, TripError, _decimal, _entry_date, _is_request_key_violation, _key,
                                          _text, _trip_id, parse_fineness, parse_weight)
from services.old_gold_availability import held_grams, shortage_message

_BASIS_INPUT = {"MASTER_KARAT": book_rules.MASTER_KARAT, "KARAT": book_rules.MASTER_KARAT, "FINENESS": book_rules.FINENESS, "ASSAY": book_rules.FINENESS}
KIND_AUDIT = {BK_ISSUE: "MARKET_TRIP_BK_ISSUE", BK_BUY: "MARKET_TRIP_BK_BUY", BK_SELL: "MARKET_TRIP_BK_SELL", BK_RETURN: "MARKET_TRIP_BK_RETURN",
              book_rules.BK_RECON: "MARKET_TRIP_BK_RECON"}
CLOSED_TEXT = {BK_ISSUE: "لإخراج ذهب منها", BK_BUY: "لشراء ذهب منها", BK_SELL: "لبيع ذهب منها", BK_RETURN: "لتسجيل رجوع ذهب منها"}
MAX_LINES = 20


def _plain(value):
    return str(value) if value is not None else None


class MarketTripBookService(MarketTripService):
    """MarketTripService plus the BOOK operations. Everything the legacy service does is inherited unchanged."""

    def __init__(self, *args, book_creation_enabled=False, reconciliation_enabled=False, **kwargs):
        super().__init__(*args, **kwargs)
        self._book_creation_enabled = bool(book_creation_enabled)
        self._reconciliation_enabled = bool(reconciliation_enabled)         # R2-S1: OFF in production construction; tests / the future release step turn it on

    # ------------------------------------------------------------------ parsing (pure)
    @staticmethod
    def parse_book_lines(lines, kind):
        """Validated lines: {basis, karat_id | fineness, weight, price (BUY / SELL only)}. The basis is explicit on every line (nothing is guessed): a master karat or an
        assay in permille. A Scrap issue accepts master karats only. An untouched row is ignored; a half-filled one is refused."""
        priced = kind in (BK_BUY, BK_SELL)
        parsed = []
        for index, line in enumerate(lines or [], 1):
            text = {k: str(line.get(k, "") if line.get(k, "") is not None else "").strip() for k in ("basis", "karat_id", "fineness", "weight", "price")}
            if not any(text.values()):
                continue
            basis = _BASIS_INPUT.get(text["basis"].upper())
            if basis is None:
                raise TripError("BASIS_REQUIRED", f"السطر {index}: اختر أسلوب العيار (عيار معياري أو عيار فعلي بالألف).")
            if kind == BK_ISSUE and basis != book_rules.MASTER_KARAT:
                raise TripError("SCRAP_BASIS", f"السطر {index}: الإخراج من كسر المحل بعيار معياري فقط.")
            row = {"basis": basis, "karat_id": None, "fineness": None}
            if basis == book_rules.MASTER_KARAT:
                if text["fineness"]:
                    raise TripError("BASIS_CONFLICT", f"السطر {index}: لا تكتب عيارًا فعليًا مع عيار معياري.")
                try:
                    row["karat_id"] = int(text["karat_id"])
                except ValueError:
                    raise TripError("KARAT_REQUIRED", f"السطر {index}: اختر العيار.") from None
                if row["karat_id"] <= 0:
                    raise TripError("KARAT_REQUIRED", f"السطر {index}: اختر العيار.")
            else:
                if text["karat_id"]:
                    raise TripError("BASIS_CONFLICT", f"السطر {index}: لا تختر عيارًا معياريًا مع عيار فعلي.")
                row["fineness"] = parse_fineness(text["fineness"], f"السطر {index}: العيار الفعلي")
            row["weight"] = parse_weight(text["weight"])
            if row["weight"] == 0:
                raise TripError("WEIGHT_REQUIRED", f"السطر {index}: أدخل الوزن الفعلي بقيمة موجبة.")
            if priced:
                price = _decimal(text["price"], "ثمن السطر", "PRICE_INVALID")
                if price is None or price <= 0 or price >= MAX_MONEY or price != price.quantize(MONEY_STEP):
                    raise TripError("PRICE_INVALID", f"السطر {index}: أدخل الثمن المتفق عليه بقيمة موجبة وخانتين عشريتين على الأكثر.")
                row["price"] = price.quantize(MONEY_STEP)
            elif text["price"]:
                raise TripError("PRICE_NOT_ALLOWED", f"السطر {index}: هذه الحركة بلا ثمن.")
            parsed.append(row)
        if not parsed:
            raise TripError("LINES_REQUIRED", "أضف سطر ذهب واحدًا على الأقل (الوزن والعيار).")
        if len(parsed) > MAX_LINES:
            raise TripError("LINES_TOO_MANY", f"الحد الأقصى {MAX_LINES} سطرًا في المستند الواحد.")
        return parsed

    @staticmethod
    def _digest(kind, trip_number, parsed, extra):
        rows = [[p["basis"], p["karat_id"], None if p["fineness"] is None else str(p["fineness"]), str(p["weight"]), str(p.get("price", ""))] for p in parsed]
        return hashlib.sha256(json.dumps([kind, trip_number, rows, extra], ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def book_reference(prices, parsed, karat_values, total):
        """(reference total, deviation %) of a purchase / sale against the SHOP_BUY reference: a master karat uses its own karat price, an assayed line its pure
        grams x the 24K price (never a mapped master karat). (None, None) when any line has no reference: a partial reference would mislead."""
        with localcontext() as context:
            context.prec = 50
            reference = Decimal(0)
            for line in parsed:
                if line["basis"] == book_rules.MASTER_KARAT:
                    price = prices.get(karat_values.get(line["karat_id"]))
                    value = None if price is None else line["weight"] * price
                else:
                    price = prices.get(24)
                    value = None if price is None else line["weight"] * line["fineness"] / Decimal(1000) * price
                if value is None:
                    return None, None
                reference += value
            if reference <= 0:
                return None, None
            deviation = ((total - reference) / reference * Decimal(100)).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
            return reference.quantize(MONEY_STEP, rounding=ROUND_HALF_UP), deviation

    # ------------------------------------------------------------------ public operations
    def book_issue(self, trip_id, lines, entry_date, notes, request_key, *, _expected_actor=None):
        """BK_ISSUE: existing shop Scrap goes to the trip (master-karat lines only). With no trip id it creates a BOOK trip - only when this service was built
        with `book_creation_enabled=True`, otherwise BOOK_NOT_ACTIVE (nothing is written)."""
        return self._book_call(BK_ISSUE, trip_id, lines, entry_date, notes, request_key, _expected_actor, None, "", False)

    def book_buy(self, trip_id, lines, vendor_party_id, vendor_name, buy_date, reference, notes, request_key, acknowledge_price=False, *, _expected_actor=None):
        """BK_BUY: gold bought with the trip's cash (vendor mandatory, total = the sum of the line prices, never above the custody cash)."""
        return self._book_call(BK_BUY, trip_id, lines, buy_date, notes, request_key, _expected_actor, (vendor_party_id, vendor_name), reference, acknowledge_price)

    def book_sell(self, trip_id, lines, buyer_party_id, buyer_name, sale_date, reference, notes, request_key, acknowledge_price=False, *, _expected_actor=None):
        """BK_SELL: gold sold for cash that enters the trip's cash (buyer mandatory, outgoing equivalent never above the book balance)."""
        return self._book_call(BK_SELL, trip_id, lines, sale_date, notes, request_key, _expected_actor, (buyer_party_id, buyer_name), reference, acknowledge_price)

    def book_return(self, trip_id, lines, entry_date, notes, request_key, *, _expected_actor=None):
        """BK_RETURN: gold back to the shop - master karats into the counted Scrap, any other assay into identified assayed-return stock."""
        return self._book_call(BK_RETURN, trip_id, lines, entry_date, notes, request_key, _expected_actor, None, "", False)

    def _book_call(self, kind, trip_id, lines, entry_date, notes, request_key, expected_actor, counterparty, reference, acknowledged):
        session_actor = self._actor(expected_actor)
        new_trip = str(trip_id or "").strip() == ""
        if new_trip and kind != BK_ISSUE:
            raise TripError("TRIP_NOT_FOUND", "اختر العهدة.")
        trip_number = None if new_trip else _trip_id(trip_id)
        parsed = self.parse_book_lines(lines, kind)
        on_date, notes_text, reference_text, key = _entry_date(entry_date), _text(notes, NOTES_MAX, "الملاحظات"), _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _key(request_key)
        party_text, name_text = ("", "") if counterparty is None else (str(counterparty[0] if counterparty[0] is not None else "").strip(), str(counterparty[1] or "").strip())
        digest = self._digest(kind, trip_number, parsed, [party_text, "" if party_text else name_text, on_date.isoformat(), reference_text, notes_text])
        try:
            return self._post_book(kind, session_actor, trip_number, parsed, party_text, name_text, on_date, reference_text, notes_text, key, digest, bool(acknowledged))
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise
            with self._transaction() as connection:                           # a twin committed first: decide from a FRESH read
                actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
                repository = self._repository(connection)
                prior = repository.by_request(key)
                if prior is None:
                    raise
                return self._book_prior(repository, prior, kind, digest, actor)

    # ------------------------------------------------------------------ replay and results
    def _book_prior(self, repository, prior, kind, digest, actor):
        """The replay of an already-recorded request: the SAME kind, payload and user return the original document; anything else is REQUEST_CONFLICT."""
        if prior["entry_kind"] != kind or prior["request_hash"] != digest or int(prior["performed_by_user_id"]) != int(actor.user_id):
            raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
        return self._book_result(repository, prior, True)

    def _book_result(self, repository, entry, duplicate):
        lines = repository.entry_book_lines(entry["id"])
        trade = repository.entry_book_trade(entry["id"])
        return {"entry_id": str(entry["id"]), "trip_id": str(entry["trip_id"]), "trip_code": trip_code(entry["trip_id"]),
                "code": entry_code(entry["trip_id"], entry["entry_no"]), "kind": entry["entry_kind"], "created": False, "duplicate": duplicate,
                "document_posted": str(sum((Decimal(str(l["eq21_posted"])) for l in lines), Decimal("0.000")).quantize(Decimal("0.001"))),
                "lines": [{"line_no": int(l["line_no"]), "basis": l["basis"], "weight": str(l["weight_grams"]), "karat_value": l["karat_value"],
                           "fineness": _plain(l["fineness_permille"]), "analytical": str(l["eq21_analytical"]), "posted": str(l["eq21_posted"]),
                           "difference": str(l["posting_difference"]), "price": _plain(l["line_price"]),
                           "scrap": l["scrap_movement_id"] is not None, "stock_item": l["entry_kind"] == BK_RETURN and l["basis"] == book_rules.FINENESS}
                          for l in lines],
                "trade": None if trade is None else {"counterparty_kind": trade["counterparty_kind"], "name": trade["counterparty_name"],
                                                     "total_price": str(trade["total_price"]), "reference_total": _plain(trade["reference_total"]),
                                                     "deviation_percent": _plain(trade["deviation_percent"]),
                                                     "price_warning_acknowledged": bool(trade["price_warning_acknowledged"])}}

    # ------------------------------------------------------------------ reconciliation PREVIEW (R1: read only)
    def reconciliation_preview(self, trip_id, *, _expected_actor=None):
        """The read-only «مطابقة العهدة» of a BOOK trip: A, B, the proposed loss and rounding settlement, the theoretical effect (a preview only), and the diagnostics.

        READ ONLY by construction: the same non-writing permission check as the trip list / detail (`require_permission_read`, MARKET_TRIP_MANAGE), ONE transaction, only
        plain snapshot reads (`reconciliation_source` + the display form of the custody cash: no locking clause anywhere), the pure `book_reconciliation.reconcile_preview`,
        and a session check at the end. It never posts, saves, audits, approves, closes or corrects anything, and its success never makes a trip closable
        (`BOOK_CLOSURE_NOT_AVAILABLE` stays). A part that cannot be read makes the preview INCOMPLETE (no proposal); it is never read as zero."""
        actor = self._actor(_expected_actor)
        trip_number = _trip_id(trip_id)
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, PERMISSION)
            repository = self._repository(connection)
            trip = repository.trip(trip_number)
            if trip is None:
                raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
            book_rules.require_book_trip(trip)
            source = repository.reconciliation_source(trip_number)
            try:
                source["cash_outstanding"] = custody_rules.cash_rows_outstanding(repository.custody_cash(trip_number, current=False))
            except MySqlError as exc:
                source["cash_outstanding"] = None
                source["incomplete"].append(("SOURCE_CASH_UNREADABLE", type(exc).__name__))
            preview = book_reconciliation.reconcile_preview(source)
            if not self._session.is_current(actor):
                raise PermissionError("انتهت جلسة الدخول.")
        view = preview.view()
        view["status"] = trip["status"]
        return view

    # ------------------------------------------------------------------ reconciliation SETTLEMENT (R2-S1, migration 057) - DISABLED unless `reconciliation_enabled`
    @property
    def reconciliation_enabled(self):
        """Presentation hint only; reconcile rechecks this gate before any transaction."""
        return self._reconciliation_enabled

    def reconciliation_approval_preview(self, trip_id, *, _expected_actor=None):
        """One read-only snapshot for the approval dialog; posting revalidates under locks."""
        actor = self._actor(_expected_actor)
        if not self._reconciliation_enabled:
            raise TripError("RECONCILIATION_NOT_ACTIVE", "اعتماد التسوية غير مفعّل.")
        trip_number = _trip_id(trip_id)
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, PERMISSION)
            repository = self._repository(connection)
            trip = repository.trip(trip_number)
            if trip is None:
                raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
            book_rules.require_book_trip(trip)
            if trip["status"] != OPEN:
                raise TripError("TRIP_CLOSED", "العهدة مغلقة.")
            source = repository.reconciliation_source(trip_number, approval=True)
            if any(e["entry_kind"] == book_rules.BK_RECON and e.get("correction_id") is None for e in source["entries"]):
                raise TripError("RECONCILIATION_EXISTS", "للعهدة تسوية معتمدة بالفعل؛ راجع مستندها في السجل.")
            try:
                preview = book_reconciliation.reconcile_preview(source)
                problems = book_settlement.readiness_problems(preview)
                if problems:
                    raise self._not_ready(problems)
                floor, floor_source = book_settlement.date_window(source, trip.get("created_at"))
                _, _, settlement, digest, _, _ = book_settlement.build(source)
                book_settlement.check_capacity(settlement)
            except (book_settlement.SettlementDataError, book_settlement.CanonicalError, InvalidOperation) as exc:
                raise self._not_ready([("SNAPSHOT_SOURCE_INVALID", "تعذّر إعداد معاينة الاعتماد؛ راجع بيانات المصدر.")]) from exc
            now = repository.db_now()
            a = format(book_settlement.a_star_evidence(settlement), "f")
            loss = book_settlement.milli_text(settlement.loss_milli)
            result = {"tripId": str(trip_number), "sourceDigest": digest,
                      "contractVersions": dict(book_settlement.CONTRACT_VERSIONS),
                      "a": a, "aUnits": str(settlement.a_star_units), "b": book_settlement.milli_text(settlement.b_star_milli),
                      "loss": loss, "rounding": book_settlement.milli_text(settlement.rounding_milli),
                      "reasonRequired": settlement.loss_milli != 0, "dateFloor": floor.isoformat(),
                      "dateFloorSource": floor_source, "defaultDate": now.date().isoformat(),
                      "reasons": [{"id": "", "text": "اختر السبب" if settlement.loss_milli else "بدون سبب", "noteRequired": False}]
                                 + [{"id": key, "text": text, "noteRequired": key in ("OTHER", "UNEXPLAINED")}
                                    for key, text in book_settlement.REASONS.items()],
                      "confirmations": book_settlement.confirmation_texts(a, loss, settlement.a_star_units)}
            if not self._session.is_current(actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result

    def reconcile(self, trip_id, source_digest, effective_date, reason_code, note, confirmations, contract_versions, request_key, *, _expected_actor=None):
        """Approve the reconciliation of a BOOK trip: the owner recognises the unsettled analytical balance A, the posted loss L = round3(A) and the rounding settlement
        r = B - L (never a gain), and the document is recorded with its canonical snapshot. Nothing else moves: no cash, Scrap, stock, supplier or inventory effect, and the
        trip is NOT closed (closure stays `BOOK_CLOSURE_NOT_AVAILABLE`).

        DISABLED by default (`reconciliation_enabled=False`): the refusal comes before any transaction, so nothing reads or writes the 057 table. The request payload that
        enters the hash is `book_settlement.request_payload` (trip, the source digest the owner reviewed, the OMITTED-date marker or the explicit ISO date, reason, note, the three
        confirmations, the contract versions); the executor is verified separately against the stored entry, as for every other flow. An omitted date hashes as the marker
        TODAY: the RESOLVED date (the server date at approval) is stored, and a retry of the same request returns the original after midnight instead of deriving another date.
        An explicit date that happens to equal the stored one is a DIFFERENT payload (REQUEST_CONFLICT)."""
        session_actor = self._actor(_expected_actor)
        if not self._reconciliation_enabled:
            raise TripError("RECONCILIATION_NOT_ACTIVE", "تسوية الرصيد الدفتري غير مفعّلة: لا يمكن اعتمادها.")
        trip_number = _trip_id(trip_id)
        digest_text = str(source_digest or "")
        if not book_settlement.is_digest(digest_text):
            raise TripError("DIGEST_INVALID", "بصمة المصدر غير صالحة: أعد فتح معاينة التسوية.")
        book_settlement.clean_versions(contract_versions)
        confirmed = book_settlement.clean_confirmations(confirmations)
        reason, note_text = book_settlement.clean_reason(reason_code, note)
        marker = book_settlement.clean_date_input(effective_date)
        key = _key(request_key)
        request_digest = book_settlement.request_hash(trip_number, digest_text, marker, reason, note_text, confirmed)
        try:
            return self._post_reconciliation(session_actor, trip_number, digest_text, marker, reason, note_text, confirmed, key, request_digest)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise
            with self._transaction() as connection:                           # a twin committed first: decide from a FRESH read
                actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
                repository = self._repository(connection)
                if repository.trip(trip_number, lock=True) is None:
                    raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
                prior = repository.by_request(key)
                if prior is None:
                    raise
                return self._reconciliation_prior(repository, prior, request_digest, actor, session_actor)

    def _reconciliation_prior(self, repository, prior, request_digest, actor, session_actor):
        """An already-recorded request: the SAME kind, payload and user return the original record (even after it was reversed); anything else is REQUEST_CONFLICT."""
        if (prior["entry_kind"] != book_rules.BK_RECON or prior["request_hash"] != request_digest or int(prior["performed_by_user_id"]) != int(actor.user_id)):
            raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
        result = self._reconciliation_result(repository, prior, True)
        if not self._session.is_current(session_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        return result

    def _reconciliation_result(self, repository, entry, duplicate):
        row = repository.reconciliation_of_entry(entry["id"])
        if row is None:
            raise RuntimeError("a BK_RECON entry has no reconciliation record")        # a defect, never an answer
        problems = book_settlement.verify_record(row)
        if (row.get("entry_id") != entry["id"] or row.get("trip_id") != entry["trip_id"]
                or row.get("confirmed_by_user_id") != entry["performed_by_user_id"]):
            problems.append("ENTRY_IDENTITY_MISMATCH")
        if problems:
            error = TripError("RECONCILIATION_MISMATCH", "سجل التسوية لا يطابق لقطته المحفوظة: لا يمكن إرجاع نتيجة اعتماد سليمة.")
            error.diagnostics = sorted(set(problems))
            raise error
        return {"entry_id": str(entry["id"]), "trip_id": str(entry["trip_id"]), "trip_code": trip_code(entry["trip_id"]), "code": entry_code(entry["trip_id"], entry["entry_no"]),
                "kind": book_rules.BK_RECON, "duplicate": duplicate, "reversed": repository.entry_reversal_id(entry["id"]) is not None, "generation": int(row["generation"]),
                "effective_date": row["effective_date"].isoformat(), "a_star_units": str(int(row["a_star_units"])), "a_star": str(row["a_star"]), "b_star": str(row["b_star"]),
                "loss_posted": str(row["loss_posted"]), "rounding_settlement": str(row["rounding_settlement"]), "delta_units": str(int(row["delta_units"])),
                "rho_units": str(int(row["rho_units"])), "reason_code": row["reason_code"], "reason": book_settlement.REASONS.get(row["reason_code"]), "note": row["note"],
                "source_digest": row["source_digest"], "snapshot_digest": row["snapshot_digest"], "entry_count": int(row["entry_count"]), "line_count": int(row["line_count"]),
                "reversed_count": int(row["reversed_count"])}

    @staticmethod
    def _not_ready(problems):
        shown = "؛ ".join(message for _, message in problems[:3])
        error = TripError("RECONCILIATION_NOT_READY", f"لا يمكن اعتماد التسوية قبل معالجة الملاحظات التالية: {shown}" if shown else "لا يمكن اعتماد التسوية: بيانات المصدر غير مكتملة.")
        error.diagnostics = [{"code": code, "message": message} for code, message in problems]
        return error

    def _post_reconciliation(self, session_actor, trip_number, digest_text, marker, reason, note_text, confirmed, key, request_digest):
        """ONE transaction: permission (re-checked, current session) -> trip row lock -> replay -> mode / status / effectiveness -> generation -> the five CURRENT locked reads ->
        readiness, digest, evidence, capacity and policy -> the entry, the record and the audit row -> the session check. Any failure rolls everything back."""
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            trip = repository.trip(trip_number, lock=True)
            if trip is None:
                raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
            prior = repository.by_request(key)
            if prior is not None:
                return self._reconciliation_prior(repository, prior, request_digest, actor, session_actor)  # before state refusals
            book_rules.require_book_trip(trip)
            if trip["status"] != OPEN:
                raise TripError("TRIP_CLOSED", "العهدة مغلقة: أعد فتحها أولاً لاعتماد تسوية.")
            effective = repository.effective_reconciliation_entries(trip_number)
            if effective:
                raise TripError("RECONCILIATION_EXISTS", f"للعهدة تسوية فعّالة ({entry_code(trip_number, effective[0]['entry_no'])}): اعكسها أولًا قبل اعتماد تسوية جديدة.")
            generation = repository.next_reconciliation_generation(trip_number)
            source = repository.reconciliation_source_current(trip_number)
            try:
                preview = book_reconciliation.reconcile_preview(source)
            except InvalidOperation:
                raise self._not_ready([("SOURCE_NUMBER_INVALID", "رقم مخزّن في المصدر خارج نطاق التمثيل الدقيق.")]) from None
            problems = book_settlement.readiness_problems(preview)
            if problems:
                raise self._not_ready(problems)
            try:
                floor, floor_source = book_settlement.date_window(source, trip.get("created_at"))
                _, entries, settlement, source_digest, snapshot, snapshot_digest = book_settlement.build(source)
            except book_settlement.SettlementDataError as exc:
                raise self._not_ready([("SNAPSHOT_SOURCE_INVALID", f"تعذّر بناء لقطة التسوية من بيانات المصدر ({exc}).")]) from None
            except (book_settlement.CanonicalError, InvalidOperation):
                raise self._not_ready([("SNAPSHOT_SOURCE_INVALID", "المصدر يحتوي قيمة لا يمكن حفظها بدقة في لقطة التسوية.")]) from None
            book_settlement.match_preview(settlement, preview)                              # the integer computation must agree with R1 (a mismatch is a defect: it raises)
            if source_digest != digest_text:
                raise TripError("RECONCILIATION_STALE", "تغيّرت مستندات العهدة منذ عرض المعاينة: أعد فتح المعاينة وراجع الأرقام قبل الاعتماد.")
            book_settlement.check_capacity(settlement, generation)
            book_settlement.require_reason(reason, settlement.loss_milli)
            now = repository.db_now()
            effective_date = book_settlement.resolve_effective_date(marker, floor, now.date())
            snapshot_text = book_settlement.canonical_bytes(snapshot).decode("utf-8")
            reparsed = json.loads(snapshot_text)                                            # the round trip the verifier will make: parse, re-serialise, compare
            if book_settlement.verify_snapshot(reparsed) or book_settlement.digest_of(reparsed) != snapshot_digest:
                raise book_settlement.SettlementIntegrityError("the snapshot does not survive its own round trip")
            a_star = book_settlement.a_star_evidence(settlement)
            entry_no = repository.next_entry_no(trip_number)
            entry_id = repository.insert_entry(trip_id=trip_number, entry_no=entry_no, kind=book_rules.BK_RECON, entry_date=effective_date, notes=note_text, key=key,
                                               digest=request_digest, actor=actor, now=now)
            repository.insert_reconciliation(
                entry_id=entry_id, trip_id=trip_number, generation=generation, effective_date=effective_date, date_floor=floor, date_floor_source=floor_source,
                a_star_units=settlement.a_star_units, a_star=a_star, b_star=book_settlement.milli_decimal(settlement.b_star_milli),
                loss_posted=book_settlement.milli_decimal(settlement.loss_milli), rounding_settlement=book_settlement.milli_decimal(settlement.rounding_milli),
                delta_units=settlement.delta_units, rho_units=settlement.rho_units, sum_abs_units=settlement.sum_abs_units, entry_count=settlement.entry_count,
                line_count=settlement.line_count, reversed_count=settlement.reversed_count, source_digest=source_digest, snapshot_digest=snapshot_digest,
                snapshot_text=snapshot_text, reason_code=reason, note=note_text, user_id=actor.user_id, confirmed_at=now, algorithm=book_settlement.RECON_ALGORITHM,
                source_algorithm=book_settlement.SOURCE_ALGORITHM, snapshot_format=book_settlement.SNAPSHOT_FORMAT, unit_denominator=book_settlement.UNITS_PER_GRAM)
            code = entry_code(trip_number, entry_no)
            a_text, loss_text = format(a_star, "f"), book_settlement.milli_text(settlement.loss_milli)
            repository.audit(actor=actor, event_type=KIND_AUDIT[book_rules.BK_RECON], entity_id=entry_id, reference=code,
                             description=f"{KIND_TEXT[book_rules.BK_RECON]} {code} — {trip_code(trip_number)}: A {a_text} • الفاقد المرحّل {loss_text} جرام عيار 21",
                             metadata={"trip_id": trip_number, "kind": book_rules.BK_RECON, "mode": BOOK, "generation": generation, "effective_date": effective_date.isoformat(),
                                       "date_floor": floor.isoformat(), "date_floor_source": floor_source, "date_defaulted": marker == book_settlement.DATE_TODAY,
                                       "employee_id": actor.employee_id, "a_star_units": str(settlement.a_star_units), "a_star": a_text,
                                       "b_star": book_settlement.milli_text(settlement.b_star_milli), "loss_posted": loss_text,
                                       "rounding_settlement": book_settlement.milli_text(settlement.rounding_milli), "delta_units": str(settlement.delta_units),
                                       "rho_units": str(settlement.rho_units), "source_digest": source_digest, "snapshot_digest": snapshot_digest,
                                       "contract_versions": dict(book_settlement.CONTRACT_VERSIONS), "reason_code": reason, "note": note_text,
                                       "confirmations": dict(confirmed), "confirmation_texts": book_settlement.confirmation_texts(a_text, loss_text, settlement.a_star_units),
                                       "entry_count": settlement.entry_count, "line_count": settlement.line_count, "reversed_count": settlement.reversed_count}, now=now)
            entry = repository.by_request(key)
            result = self._reconciliation_result(repository, entry, False)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result

    # ------------------------------------------------------------------ the posting transaction
    def _counterparty(self, kind, repository, party_text, name_text):
        if kind == BK_BUY:
            return self._vendor(repository, party_text, name_text)                    # (kind, party id, label, key); the party row is locked (shared)
        buyer_kind, party_id, label = self._sale_buyer(repository, party_text, name_text)
        key = self.vendor_name_key(label)
        if not key:
            raise TripError("BUYER_REQUIRED", "اختر المشتري من الأطراف المسجلة أو اكتب اسمه.")
        return buyer_kind, party_id, label, key

    def _post_book(self, kind, session_actor, trip_number, parsed, party_text, name_text, on_date, reference, notes, key, digest, acknowledged):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            created = trip_number is None
            if created:
                if not self._book_creation_enabled:
                    raise TripError("BOOK_NOT_ACTIVE", "النمط الدفتري غير مفعّل: لا تُنشأ عهدة دفترية.")
                prior = repository.by_request(key)
                if prior is not None:
                    return self._book_prior(repository, prior, kind, digest, actor)
                trip_id = repository.insert_trip(notes=notes, actor=actor, now=repository.db_now(), mode=BOOK)
            else:
                trip = repository.trip(trip_number, lock=True)                          # lock order: actor -> trip -> counterparty -> Scrap karats (ascending)
                if trip is None:
                    raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
                prior = repository.by_request(key)
                if prior is not None:
                    return self._book_prior(repository, prior, kind, digest, actor)     # the original document, before any availability is derived
                book_rules.require_book_trip(trip)
                if trip["status"] != OPEN:
                    raise TripError("TRIP_CLOSED", f"العهدة مغلقة: أعد فتحها أولاً {CLOSED_TEXT[kind]}.")
                reconciled = repository.effective_reconciliation_entries(trip_number)       # finality: reads market_trip_entries only (safe before migration 057)
                if reconciled:
                    raise TripError("RECONCILIATION_FINAL", f"للعهدة تسوية معتمدة ({entry_code(trip_number, reconciled[0]['entry_no'])}): اعكسها أولًا قبل تسجيل حركة ذهب.")
                trip_id = trip_number
            counterparty = self._counterparty(kind, repository, party_text, name_text) if kind in (BK_BUY, BK_SELL) else None
            karat_values = {}
            for karat_id in sorted({p["karat_id"] for p in parsed if p["karat_id"] is not None}):
                karat = repository.karat(karat_id)
                if karat is None or not karat["is_active"]:
                    raise TripError("KARAT_NOT_FOUND", "العيار غير موجود أو غير نشط.")
                karat_values[karat_id] = int(karat["karat_value"])
            direction = book_rules.IN if kind in book_rules.INCOMING_KINDS else book_rules.OUT
            planned = [book_rules.plan_line(direction, p["weight"], p["basis"], p["karat_id"], karat_values.get(p["karat_id"]), p["fineness"]) for p in parsed]
            total, reference_total, deviation, warned = None, None, None, False
            if kind == BK_ISSUE:
                self._check_scrap(connection, parsed, karat_values)
            elif direction == book_rules.OUT:                                           # BK_SELL / BK_RETURN: the exact analytical balance, a CURRENT read under the trip lock
                book_rules.check_outgoing(book_rules.balance_of_rows(repository.book_lines(trip_id, current=True)), planned)
            if kind in (BK_BUY, BK_SELL):
                total = sum((p["price"] for p in parsed), ZERO_MONEY)
                if total >= MAX_MONEY:
                    raise TripError("PRICE_INVALID", "إجمالي الصفقة كبير جدًا.")
                if kind == BK_BUY:
                    available = self._custody(repository, trip_id, current=True)["cash"]["outstanding"]
                    if total > available:
                        raise TripError("PURCHASE_EXCEEDS_CASH", f"نقدية العهدة المتاحة {available:,.2f} ج فقط: لا يمكن شراء بإجمالي {total:,.2f} ج.")
                reference_total, deviation = self.book_reference(self._reference_prices(), parsed, karat_values, total)
                warned = deviation is not None and abs(deviation) > PRICE_WARNING_PERCENT
                if warned and not acknowledged:
                    raise TripError("PRICE_DEVIATION", self._buy_warning_text(deviation, reference_total) if kind == BK_BUY
                                    else self._price_warning_text(deviation, reference_total))
            now = repository.db_now()
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=kind, entry_date=on_date, notes=notes, key=key, digest=digest, actor=actor, now=now)
            code = entry_code(trip_id, entry_no)
            label = f"{KIND_TEXT[kind]} {code} — {trip_code(trip_id)}"
            for number, (item, line) in enumerate(zip(parsed, planned), 1):
                scrap_id = None
                if kind == BK_ISSUE or (kind == BK_RETURN and item["basis"] == book_rules.MASTER_KARAT):
                    scrap_id = repository.post_scrap(kind=ISSUE if kind == BK_ISSUE else RETURN, karat_id=item["karat_id"], weight=item["weight"], entry_id=entry_id,
                                                     code=code, notes=f"{label} — سطر {number}", actor=actor, now=now)
                repository.insert_book_line(entry_id=entry_id, line_no=number, trip_id=trip_id, kind=kind, line=line, line_price=item.get("price"),
                                            scrap_movement_id=scrap_id)
                if kind == BK_RETURN and item["basis"] == book_rules.FINENESS:                  # an identified assayed-return stock item, with its posted amount
                    repository.insert_stock_movement(item_entry_id=entry_id, item_line_no=number, movement_type=book_rules.RECEIVE, grams_delta=line.weight,
                                                     posted_delta=line.posted, analytical=line.analytical_evidence, difference=line.difference,
                                                     independent=line.posted, posting_basis=book_rules.INDEPENDENT, source_entry_id=entry_id,
                                                     correction_id=None, user_id=actor.user_id)
            if counterparty is not None:
                party_kind, party_id, party_label, party_key = counterparty
                repository.insert_book_trade(entry_id=entry_id, kind=kind, counterparty_kind=party_kind, party_id=party_id, name=party_label, name_key=party_key,
                                             total=total, reference_total=reference_total, deviation=deviation, acknowledged=warned and acknowledged,
                                             trade_date=on_date, reference=reference)
            if created:
                repository.insert_event(trip_id=trip_id, event_type="OPENED", reason=None, actor=actor, now=now)
            posted_total = sum((l.posted for l in planned), Decimal("0.000"))
            repository.audit(actor=actor, event_type=KIND_AUDIT[kind], entity_id=entry_id, reference=code,
                             description=f"{label}: {len(planned)} سطر • المكافئ المرحَّل {posted_total:,.3f} جرام عيار 21" + ("" if total is None else f" • {total:,.2f} ج"),
                             metadata={"trip_id": trip_id, "kind": kind, "mode": BOOK, "date": on_date.isoformat(), "employee_id": actor.employee_id,
                                       "lines": [{"basis": p["basis"], "karat": karat_values.get(p["karat_id"]), "fineness": _plain(p["fineness"]),
                                                  "weight": str(p["weight"]), "analytical": str(l.analytical_evidence), "posted": str(l.posted),
                                                  "difference": str(l.difference), "price": _plain(p.get("price"))} for p, l in zip(parsed, planned)],
                                       "document_posted": str(posted_total),
                                       "trade": None if counterparty is None else {"counterparty_kind": counterparty[0], "party_id": counterparty[1], "name": counterparty[2],
                                                                                    "total": str(total), "reference_total": _plain(reference_total),
                                                                                    "deviation_percent": _plain(deviation),
                                                                                    "price_warning_acknowledged": bool(warned and acknowledged)}}, now=now)
            entry = repository.by_request(key)
            result = self._book_result(repository, entry, False)
            result["created"] = created
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result

    def _check_scrap(self, connection, parsed, karat_values):
        """Only AVAILABLE Scrap leaves (manager-review holds excluded), summed per karat and locked in ascending karat order."""
        per_karat = {}
        for p in parsed:
            per_karat[p["karat_id"]] = per_karat.get(p["karat_id"], Decimal("0.000")) + p["weight"]
        scrap = self._scrap(connection)
        for karat_id in sorted(per_karat):
            available = sum((Decimal(str(r["weight_delta_grams"])) for r in scrap.lock_old_gold_balance(karat_id)), Decimal("0.000"))
            if per_karat[karat_id] > available:
                raise TripError("SCRAP_SHORTAGE", shortage_message(karat_values[karat_id], per_karat[karat_id], available, held_grams(scrap, karat_id)))

    # ------------------------------------------------------------------ dialog preview (read only, no locks, nothing written)
    def book_preview(self, kind, trip_id, lines, *, _expected_actor=None):
        """What the BOOK dialog shows before saving: the posted equivalent of every line, the document total, the current book balance, and the first refusal the
        service would raise (insufficient balance / cash / Scrap). Every figure is computed by the same rules as the posting; nothing is written."""
        actor = self._actor(_expected_actor)
        if kind not in book_rules.BOOK_KINDS:
            raise TripError("KIND_INVALID", "نوع الحركة غير صحيح.")
        try:
            parsed = self.parse_book_lines(lines, kind)
        except TripError as exc:
            return {"error": str(exc), "code": exc.code}
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, PERMISSION)
            repository = self._repository(connection)
            trip = repository.trip(_trip_id(trip_id)) if str(trip_id or "").strip() else None
            karat_values = {}
            for karat_id in sorted({p["karat_id"] for p in parsed if p["karat_id"] is not None}):
                karat = repository.karat(karat_id)
                if karat is None or not karat["is_active"]:
                    return {"error": "العيار غير موجود أو غير نشط.", "code": "KARAT_NOT_FOUND"}
                karat_values[karat_id] = int(karat["karat_value"])
            direction = book_rules.IN if kind in book_rules.INCOMING_KINDS else book_rules.OUT
            planned = [book_rules.plan_line(direction, p["weight"], p["basis"], p["karat_id"], karat_values.get(p["karat_id"]), p["fineness"]) for p in parsed]
            out = {"lines": [{"posted": str(l.posted), "analytical": str(l.analytical_evidence), "difference": str(l.difference)} for l in planned],
                   "document_posted": str(sum((l.posted for l in planned), Decimal("0.000"))), "error": "", "code": ""}
            if trip is None:
                return out
            balance = book_rules.balance_of_rows(repository.book_lines(trip["id"]))
            out["balance"] = {k: str(v) for k, v in balance.view().items() if k in ("posted", "analytical", "analytical_available", "drift")}
            try:
                if direction == book_rules.OUT:
                    book_rules.check_outgoing(balance, planned)
                if kind == BK_BUY:
                    available = self._custody(repository, trip["id"])["cash"]["outstanding"]
                    total = sum((p["price"] for p in parsed), ZERO_MONEY)
                    out["total"] = str(total)
                    if total > available:
                        raise TripError("PURCHASE_EXCEEDS_CASH", f"نقدية العهدة المتاحة {available:,.2f} ج فقط: لا يمكن شراء بإجمالي {total:,.2f} ج.")
                if kind == BK_SELL:
                    out["total"] = str(sum((p["price"] for p in parsed), ZERO_MONEY))
                if kind in (BK_BUY, BK_SELL):
                    total = Decimal(out["total"])
                    reference_total, deviation = self.book_reference(self._reference_prices(), parsed, karat_values, total)
                    if reference_total is not None:
                        out.update(reference_total=str(reference_total), deviation=str(deviation), warning=abs(deviation) > PRICE_WARNING_PERCENT)
                if kind == BK_ISSUE:
                    scrap = {r["karat_id"]: Decimal(str(r["available_grams"])) for r in repository.shop_scrap()}
                    per_karat = {}
                    for p in parsed:
                        per_karat[p["karat_id"]] = per_karat.get(p["karat_id"], Decimal("0.000")) + p["weight"]
                    for karat_id, weight in per_karat.items():
                        if weight > scrap.get(karat_id, Decimal("0.000")):
                            raise TripError("SCRAP_SHORTAGE", f"المتاح من عيار {karat_values[karat_id]} في كسر المحل {scrap.get(karat_id, Decimal('0.000')):,.3f} جرام فقط.")
            except TripError as exc:
                out["error"], out["code"] = str(exc), exc.code
        return out
