"""BOOK reconciliation PREVIEW (R1): the pure, read-only arithmetic and diagnostics that compare the stored source documents of a BOOK trip. No database, no float, no
tolerance, no write. Nothing here posts, saves, closes or approves anything: the result is a proposal the owner can look at.

The arithmetic is NOT re-implemented here: `services.book_rules` stays the single reference (`analytical`, `post`, `evidence`, `round_half_up`, `stored_line`,
`balance_of`). This module only combines its results:

  A       = sum analytical IN - sum analytical OUT      exact Fraction     «الرصيد التحليلي غير المسوّى»
  B       = sum posted IN - sum posted OUT              Decimal            «الرصيد المرحّل»
  rho     = B - A                                       exact              posting drift
  L       = round3(A)  when A > 0, else 0.000           Decimal            the PROPOSED posted loss (only a proposal)
  delta   = L - A                                       exact              |delta| <= 0.0005
  r       = B - L = rho - delta                         Decimal            «فرق تقريب الترحيل» - neither loss nor gain
  the theoretical effect of an approval on the posted position is -B = -L - r (shown as «معاينة فقط», never applied)

A is classified from the EXACT comparison, never from a rounded display: zero / positive_below_posting_precision (0 < A < 1/2000, so L = 0.000) / positive /
negative_integrity (A < 0: not a gain path, a defect of the record; no loss is proposed and no gain is suggested).

STORED DATA IS NOT TRUSTED. Every stored field the preview needs is validated at the boundary (`_num`, `_int`, `_normalize_line`, the leg / trade / stock readers). A
missing, malformed or non-finite value becomes a DIAGNOSTIC tied to the entry / line / field (never an exception, never a silent zero), and a line whose FACTS fail
validation is excluded from the numbers and from the source token. Therefore:
  balance_status  "full"         every source line was usable: A and B are the whole balance (still `trustworthy` only without blocking diagnostics)
                  "partial"      some source line / entry could not be used: A and B cover only the usable ones and are labelled partial; no proposal is made
                  "unavailable"  the entries or the lines could not be read: no balance figure exists (it is NEVER presented as «no unsettled balance»)
Only the failures the validators expect are handled (bad number, missing key, wrong type); an unexpected programming error is not caught here.

SOURCE documents are an explicit list (`SOURCE_KINDS`: the four BOOK kinds that exist today), never "every kind that starts with BK_". Cash-only kinds and the future
reconciliation document (`BK_RECON`) are KNOWN and EXCLUDED; any other kind is an `UNSUPPORTED_KIND` diagnostic and makes the preview untrustworthy (it is never silently
ignored). Phase 2 kinds and ALLOCATED stock rules are NOT implemented here: they are unsupported by design.

`source_token` is a fingerprint of the SOURCE documents only (their facts, stored posted values and effective / reversed state). It is NOT a hash of a stored
snapshot (none exists in R1) and it stores nothing. It does not change when a cash entry, an event, a leg or a future `BK_RECON` is added; it changes when a source
document is posted, reversed or its lines change. When the inputs are incomplete or invalid NO token is produced (`token is None`, with `token_reason`): a fingerprint
of silently dropped inputs would look like a real one. (It does not carry the trip id / mode / a stored algorithm contract: it is not a financial reference in R1.)

What the diagnostics can and cannot show is stated in `COVERAGE_NOTES` and repeated in the preview: «no problem found within the available checks» is NOT a
guarantee of financial soundness."""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json

from services import book_rules as br

ALGORITHM = "book-source-v1"
BK_RECON = "BK_RECON"                                                       # the future reconciliation document: known, excluded from the source, not implemented
SOURCE_KINDS = (br.BK_ISSUE, br.BK_BUY, br.BK_SELL, br.BK_RETURN)           # explicit; NOT derived from a prefix
EXCLUDED_KNOWN_KINDS = tuple(br.SHARED_CASH_KINDS) + (BK_RECON,)
SCRAP_LEG_CODE = {br.BK_ISSUE: "TRIP_CUSTODY_OUT", br.BK_RETURN: "TRIP_CUSTODY_RETURN_IN"}          # movement_code of the ORIGINAL Scrap leg (tests pin it to the repository)
PRICED_KINDS = (br.BK_BUY, br.BK_SELL)
POSTING_PRECISION = Fraction(1, 2000)                                       # A below this posts a loss of 0.000 (round3, HALF_UP)

ZERO, POSITIVE_BELOW, POSITIVE, NEGATIVE = "zero", "positive_below_posting_precision", "positive", "negative_integrity"
PARTIAL, UNAVAILABLE = "partial", "unavailable"                             # not a classification of A: the figures are incomplete / absent
FULL = "full"
INTEGRITY, UNSUPPORTED, INCOMPLETE, COVERAGE = "integrity", "unsupported", "incomplete", "coverage"
BLOCKING = (INTEGRITY, UNSUPPORTED, INCOMPLETE)                             # a diagnostic of these categories makes the preview untrustworthy
BALANCE_SECTIONS = ("SOURCE_ENTRIES_UNREADABLE", "SOURCE_LINES_UNREADABLE")  # without these two there is NO balance (never «zero»)

COVERAGE_NOTES = (
    ("COVERAGE_LINE_DELETION", "حذف سطر مستند مع ساقه المرتبطة معًا لا يترك مرجعًا مستقلًا في المخطط الحالي، فلا يمكن كشفه من البيانات المتاحة."),
    ("COVERAGE_MULTI_LINE", "حذف سطر واحد من مستند متعدد الأسطر لا يُكشف إلا إذا تركت له الساق أو مجموع الصفقة أثرًا مستقلًا (ساق كسر يتيمة أو مجموع صفقة لا يطابق)."),
    ("COVERAGE_NUMBERING", "ترقيم الأسطر المتتابع وحده لا يُستخدم دليلًا على حذف سطر."),
    ("COVERAGE_SCOPE", "الفحوص محصورة في الأنواع الأربعة الحالية؛ قواعد التخصيص (ALLOCATED) وأنواع المرحلة الثانية خارج هذه المعاينة."),
)


@dataclass(frozen=True)
class Diagnostic:
    code: str
    category: str
    message: str
    entry_id: int | None = None
    entry_code: str = ""
    line_no: int | None = None

    def view(self):
        return {"code": self.code, "category": self.category, "message": self.message, "entryId": None if self.entry_id is None else str(self.entry_id),
                "entryCode": self.entry_code, "lineNo": self.line_no}


class SourceTokenError(ValueError):
    """The token cannot be built from these inputs (missing / invalid facts): the caller reports it as unavailable, never as a fingerprint of what remained."""


# ------------------------------------------------------------------ boundary validators (expected failures only)
def _num(value):
    """A finite Decimal, or None for a missing / malformed / non-finite value (bool is not a number here)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() else None


def _int(value):
    """An int, or None for a missing / malformed value (a fraction such as 5.5 is not an int)."""
    number = _num(value)
    if number is None or number != number.to_integral_value():
        return None
    return int(number)


def _text(value):
    return value if isinstance(value, str) and value != "" else None


def _places(value, places):
    """True when `value` (a finite Decimal) has no more than `places` decimals."""
    return value == value.quantize(Decimal(1).scaleb(-places))


def _fixed(value, places):
    return format(value.quantize(Decimal(1).scaleb(-places)), "f")


def _entry_code(entry):
    trip, number = _int(entry.get("trip_id")), _int(entry.get("entry_no"))
    return f"MT{trip:06d}-{number}" if trip is not None and number is not None else ""


# ------------------------------------------------------------------ line normalisation (the single place a stored line is read)
_REQUIRED_FACT_FIELDS = ("weight_grams", "eq21_posted")
_EVIDENCE_FIELDS = ("eq21_analytical", "posting_difference", "signed_eq21_posted")


def _normalize_line(row, entry, code, diagnostics):
    """(line, usable): `line` is a dict of VALIDATED values; `usable` is True when the line's FACTS are valid and it can enter the numbers / token. Every failure appends a
    diagnostic tied to the entry, line and field. Nothing is read from the row outside this function."""
    eid = int(entry["id"])
    no = _int(row.get("line_no"))

    def bad(c, message, category=INTEGRITY):
        diagnostics.append(Diagnostic(c, category, f"{code} سطر {no}: {message}", eid, code, no))

    out = {"entry_id": eid, "line_no": no}
    usable = True
    kind = entry["entry_kind"]
    out["direction"] = row.get("direction") if row.get("direction") in (br.IN, br.OUT) else None
    if out["direction"] is None:
        bad("LINE_FIELD_INVALID", "الحقل «direction» ناقص أو غير صالح.")
        usable = False
    elif out["direction"] != (br.IN if kind in br.INCOMING_KINDS else br.OUT):
        bad("LINE_DIRECTION_INVALID", "اتجاه السطر لا يناسب نوع المستند.")
    out["trip_id"], out["entry_kind"], out["trip_mode"] = _int(row.get("trip_id")), row.get("entry_kind"), row.get("trip_mode", br.BOOK)
    if out["trip_id"] is None:
        bad("LINE_FIELD_INVALID", "الحقل «trip_id» ناقص أو غير صالح.")
    elif out["entry_kind"] != kind or out["trip_id"] != int(entry["trip_id"]) or out["trip_mode"] != br.BOOK:
        bad("LINE_LINK_MISMATCH", "السطر غير مرتبط بالمستند أو الرحلة أو النمط الصحيح.")
    for name in _REQUIRED_FACT_FIELDS:
        value = _num(row.get(name))
        if value is None:
            bad("LINE_FIELD_INVALID", f"الحقل «{name}» ناقص أو غير صالح.")
            usable = False
        out[name] = value
    for name in _EVIDENCE_FIELDS:
        value = _num(row.get(name))
        if value is None:
            bad("LINE_FIELD_INVALID", f"حقل الدليل «{name}» ناقص أو غير صالح.")
        out[name] = value
    weight, posted = out["weight_grams"], out["eq21_posted"]
    if weight is not None and (weight <= 0 or not _places(weight, 3)):
        bad("LINE_FACTS_INVALID", "الوزن يجب أن يكون موجبًا بثلاث منازل على الأكثر.")
        usable = False
    if posted is not None and (posted < 0 or not _places(posted, 3)):
        bad("LINE_FACTS_INVALID", "القيمة المرحَّلة يجب ألا تكون سالبة وبثلاث منازل على الأكثر.")
        usable = False
    basis = row.get("basis")
    out["basis"] = basis if basis in (br.MASTER_KARAT, br.FINENESS) else None
    out["karat_id"], out["karat_value"], out["fineness_permille"] = _int(row.get("karat_id")), _int(row.get("karat_value")), _num(row.get("fineness_permille"))
    facts_ok = False
    if out["basis"] == br.MASTER_KARAT:
        facts_ok = out["karat_id"] is not None and out["karat_value"] is not None and out["karat_value"] > 0 and row.get("fineness_permille") is None
    elif out["basis"] == br.FINENESS:
        fineness = out["fineness_permille"]
        facts_ok = (row.get("karat_id") is None and row.get("karat_value") is None and fineness is not None and 0 < fineness <= 1000 and _places(fineness, 1))
    if not facts_ok:
        bad("LINE_FACTS_INVALID", "العيار أو النقاوة المخزّنة ناقصة أو لا تناسب أسلوب العيار.")
        usable = False
    if kind == br.BK_ISSUE and out["basis"] not in (None, br.MASTER_KARAT):
        bad("LINE_FACTS_INVALID", "الإخراج من كسر المحل يكون بعيار معياري فقط.")
    price_raw = row.get("line_price")
    out["line_price"] = _num(price_raw)
    if price_raw is not None and out["line_price"] is None:
        bad("LINE_FIELD_INVALID", "الحقل «line_price» غير صالح.")
    elif kind in PRICED_KINDS:
        if out["line_price"] is None or out["line_price"] <= 0:
            bad("LINE_PRICE_INVALID", "سطر الصفقة بلا ثمن موجب.")
    elif price_raw is not None:
        bad("LINE_PRICE_INVALID", "هذا النوع من المستندات بلا ثمن للسطر.")
    leg_raw = row.get("scrap_movement_id")
    out["scrap_movement_id"] = _int(leg_raw)
    if leg_raw is not None and out["scrap_movement_id"] is None:
        bad("LINE_FIELD_INVALID", "الحقل «scrap_movement_id» غير صالح.")
    out["usable"] = usable
    return out, usable


def _line_checks(entry, line, code, diagnostics):
    """The independent per-line checks that need valid FACTS (the line is usable): every recomputation starts from the stored physical facts."""
    eid, no = int(entry["id"]), line["line_no"]

    def bad(c, message):
        diagnostics.append(Diagnostic(c, INTEGRITY, f"{code} سطر {no}: {message}", eid, code, no))

    posted = line["eq21_posted"]
    exact = br.analytical(line["weight_grams"], line["basis"], line["karat_value"], line["fineness_permille"])
    expected_signed = posted if line["direction"] == br.IN else -posted
    if line["signed_eq21_posted"] is not None and line["signed_eq21_posted"] != expected_signed:
        bad("LINE_SIGN_MISMATCH", "القيمة الموقّعة لا تطابق القيمة المرحَّلة واتجاه السطر.")
    if posted != br.post(exact):
        bad("LINE_POSTED_MISMATCH", f"القيمة المرحَّلة {posted:,.3f} لا تساوي ترحيل القيمة التحليلية الدقيقة {br.post(exact):,.3f}.")
    if line["eq21_analytical"] is not None and line["eq21_analytical"] != br.evidence(exact):                  # the evidence field and the difference field are DIFFERENT equations
        bad("LINE_EVIDENCE_MISMATCH", "حقل الدليل التحليلي لا يطابق القيمة المعاد اشتقاقها من الحقائق.")
    if line["posting_difference"] is not None and line["posting_difference"] != br.round_half_up(Fraction(posted) - exact, br.EVIDENCE_PLACES):
        bad("LINE_DIFFERENCE_MISMATCH", "حقل فرق الترحيل المخزّن لا يطابق (المرحَّل − التحليلي الدقيق).")


# ------------------------------------------------------------------ the source token
def source_token(entries, lines):
    """The fingerprint of the SOURCE documents (see the module docstring). Inputs: the stored entry rows and book-line rows of ONE trip. Stable order (entry id, line no),
    fixed-scale Decimal strings, canonical JSON. Cash entries, events, legs and BK_RECON are not part of it; unknown kinds are not hashed either (they produce a
    diagnostic instead). Raises `SourceTokenError` when ANY source entry or line it would hash is missing or invalid: no fingerprint of dropped inputs is produced."""
    by_entry = {}
    for line in lines:
        eid = _int(line.get("entry_id")) if isinstance(line, dict) else None
        if eid is None:
            raise SourceTokenError("سطر بلا معرّف مستند صالح")
        by_entry.setdefault(eid, []).append(line)
    items = []
    for entry in entries:
        if not isinstance(entry, dict) or _int(entry.get("id")) is None or not _text(entry.get("entry_kind")):
            raise SourceTokenError("مستند بلا معرّف أو نوع صالح")
    for entry in sorted((e for e in entries if e["entry_kind"] in SOURCE_KINDS), key=lambda e: _int(e["id"])):
        eid = _int(entry["id"])
        rows = []
        for l in by_entry.get(eid, []):
            number = _int(l.get("line_no"))
            weight, posted, fineness = _num(l.get("weight_grams")), _num(l.get("eq21_posted")), _num(l.get("fineness_permille"))
            karat_id, karat_value = _int(l.get("karat_id")), _int(l.get("karat_value"))
            if (number is None or weight is None or posted is None or l.get("direction") not in (br.IN, br.OUT) or l.get("basis") not in (br.MASTER_KARAT, br.FINENESS)
                    or (l.get("fineness_permille") is not None and fineness is None) or (l.get("karat_id") is not None and karat_id is None)
                    or (l.get("karat_value") is not None and karat_value is None)):
                raise SourceTokenError(f"سطر غير صالح في المستند {eid}")
            rows.append((number, l["direction"], l["basis"], karat_id, karat_value, None if fineness is None else _fixed(fineness, 1), _fixed(weight, 3), _fixed(posted, 3)))
        correction = entry.get("correction_id")
        if correction is not None and _int(correction) is None:
            raise SourceTokenError(f"حالة عكس غير صالحة في المستند {eid}")
        items.append({"id": str(eid), "kind": entry["entry_kind"], "effective": correction is None, "correction": None if correction is None else str(_int(correction)),
                      "lines": [list(r) for r in sorted(rows, key=lambda r: r[0])]})
    payload = json.dumps({"algorithm": ALGORITHM, "entries": items}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ the preview
@dataclass
class Preview:
    trip_id: int
    a_in: Fraction = Fraction(0)
    a_out: Fraction = Fraction(0)
    b_in: Decimal = br.ZERO3
    b_out: Decimal = br.ZERO3
    sum_abs: Fraction = Fraction(0)
    a_state: str = ZERO
    balance_status: str = FULL
    proposed_loss: Decimal | None = None
    rounding_settlement: Decimal | None = None
    delta: Fraction | None = None
    entry_count: int = 0
    line_count: int = 0
    reversed_count: int = 0
    excluded_count: int = 0
    cash_outstanding: Decimal | None = None
    token: str | None = None
    token_reason: str = ""
    diagnostics: list = field(default_factory=list)

    @property
    def a(self):
        return self.a_in - self.a_out

    @property
    def b(self):
        return self.b_in - self.b_out

    @property
    def rho(self):
        return Fraction(self.b) - self.a

    @property
    def bound(self):
        return self.sum_abs + POSTING_PRECISION

    @property
    def complete(self):
        return not any(d.category == INCOMPLETE for d in self.diagnostics)

    @property
    def trustworthy(self):
        return not any(d.category in BLOCKING for d in self.diagnostics)

    @property
    def effect(self):
        """The THEORETICAL effect of an approval on the posted position (-B = -L - r). A preview only: nothing is applied. None while there is no proposal."""
        if self.proposed_loss is None or self.rounding_settlement is None:
            return None
        return {"position": br.ZERO3 - self.b, "loss_part": br.ZERO3 - self.proposed_loss, "rounding_part": br.ZERO3 - self.rounding_settlement}      # 0 - x: never a negative zero

    def view(self):
        """Primitive, display-ready figures (Decimal / Fraction -> strings). Exact internals stay Fractions; the six-decimal strings are display evidence only. When the
        balance is UNAVAILABLE every balance figure is None (it is not zero); when PARTIAL the figures are those of the usable lines only and `balanceStatus` says so."""
        figures = self.balance_status != UNAVAILABLE
        a6 = br.evidence(self.a) if figures else None
        effect = self.effect
        return {
            "tripId": str(self.trip_id), "algorithm": ALGORITHM, "sourceToken": self.token, "sourceTokenReason": self.token_reason,
            "aState": self.a_state, "balanceStatus": self.balance_status, "excludedCount": self.excluded_count,
            "a": None if not figures else format(a6, "f"), "aExact": None if not figures else f"{self.a.numerator}/{self.a.denominator}",
            "aPositiveBelowDisplay": bool(figures and self.balance_status == FULL and self.a > 0 and a6 == 0),
            "aIn": None if not figures else format(br.evidence(self.a_in), "f"), "aOut": None if not figures else format(br.evidence(self.a_out), "f"),
            "b": None if not figures else format(self.b, ".3f"), "bIn": None if not figures else format(self.b_in, ".3f"), "bOut": None if not figures else format(self.b_out, ".3f"),
            "rho": None if not figures else format(br.evidence(self.rho), "f"), "sumAbs": None if not figures else format(br.evidence(self.sum_abs), "f"),
            "bound": None if not figures else format(br.evidence(self.bound), "f"),
            "proposedLoss": None if self.proposed_loss is None else format(self.proposed_loss, ".3f"),
            "roundingSettlement": None if self.rounding_settlement is None else format(self.rounding_settlement, ".3f"),
            "delta": None if self.delta is None else format(br.evidence(self.delta), "f"),
            "effect": None if effect is None else {k: format(v, ".3f") for k, v in effect.items()},
            "entryCount": self.entry_count, "lineCount": self.line_count, "reversedCount": self.reversed_count,
            "cashOutstanding": None if self.cash_outstanding is None else format(self.cash_outstanding, ".2f"),
            "complete": self.complete, "trustworthy": self.trustworthy,
            "diagnostics": [d.view() for d in self.diagnostics if d.category != COVERAGE],
            "coverage": [{"code": c, "message": m} for c, m in COVERAGE_NOTES],
        }


POSITIVE_BELOW_LIMIT = POSTING_PRECISION                                    # A < 1/2000  <=>  round3(A) = 0.000 (HALF_UP: 1/2000 itself rounds up to 0.001)


def classify_a(a):
    """The state of A from the EXACT value (a display rounding to six decimals never turns a positive A into zero)."""
    if a < 0:
        return NEGATIVE
    if a == 0:
        return ZERO
    return POSITIVE_BELOW if a < POSITIVE_BELOW_LIMIT else POSITIVE


def _rows(source, name):
    """The rows of one section as a list of dicts; a non-dict row is dropped and counted (it becomes a diagnostic, never an exception)."""
    good, invalid = [], 0
    for row in list(source.get(name) or []):
        if isinstance(row, dict):
            good.append(row)
        else:
            invalid += 1
    return good, invalid


def reconcile_preview(source):
    """Build the Preview from the stored source rows of ONE trip. `source`: {"trip_id", "entries", "lines", "scrap", "trades", "stock", "cash_outstanding",
    "incomplete": [(code, reason)]} - rows are the repository's dictionaries. Pure: it never reads or writes anything."""
    trip_id = _int(source.get("trip_id"))
    out = Preview(trip_id=0 if trip_id is None else trip_id, cash_outstanding=_num(source.get("cash_outstanding")))
    diagnostics = out.diagnostics
    if trip_id is None:
        diagnostics.append(Diagnostic("TRIP_INVALID", INTEGRITY, "معرّف الرحلة غير صالح."))
    incomplete_codes = set()
    for code, reason in source.get("incomplete") or []:
        incomplete_codes.add(code)
        diagnostics.append(Diagnostic(code, INCOMPLETE, f"تعذّرت قراءة جزء لازم من البيانات ({reason}): المعاينة غير مكتملة ولا تُعتمد."))
    entries, bad_entries = _rows(source, "entries")
    lines, bad_lines = _rows(source, "lines")
    scrap, bad_scrap = _rows(source, "scrap")
    trades, bad_trades = _rows(source, "trades")
    stock, bad_stock = _rows(source, "stock")
    for count, code, what in ((bad_entries, "ENTRY_INVALID", "مستند"), (bad_lines, "LINE_KEYS_INVALID", "سطر"), (bad_scrap, "LEG_DATA_INVALID", "ساق كسر"),
                              (bad_trades, "TRADE_DATA_INVALID", "وثيقة صفقة"), (bad_stock, "STOCK_DATA_INVALID", "سجل مخزون")):
        if count:
            diagnostics.append(Diagnostic(code, INTEGRITY, f"{count} من بيانات ({what}) ليست صفًا صالحًا: لا يمكن فحصها."))
            if code in ("ENTRY_INVALID", "LINE_KEYS_INVALID"):
                out.excluded_count += count
    no_balance = bool(incomplete_codes & set(BALANCE_SECTIONS))
    if no_balance:                                                            # entries or lines could not be read: there is NO balance and every check that needs them is meaningless
        out.balance_status, out.a_state = UNAVAILABLE, UNAVAILABLE
        out.token_reason = "تعذّرت قراءة المستندات أو الأسطر: لا تُنتَج بصمة."
        return out                                                            # only the «unreadable» diagnostics are reported: «entry without lines» would be an artefact of the failed read
    legs_ok, trades_ok, stock_ok = "SOURCE_LEGS_UNREADABLE" not in incomplete_codes, "SOURCE_TRADES_UNREADABLE" not in incomplete_codes, "SOURCE_STOCK_UNREADABLE" not in incomplete_codes

    # ---- entries: keys validated once; an entry without a usable id / kind is excluded (and says so)
    by_id = {}
    for e in entries:
        eid, kind = _int(e.get("id")), _text(e.get("entry_kind"))
        trip, number = _int(e.get("trip_id")), _int(e.get("entry_no"))
        if eid is None or kind is None or trip is None:
            diagnostics.append(Diagnostic("ENTRY_INVALID", INTEGRITY, "مستند بلا معرّف أو نوع أو رحلة صالحة: لا يمكن فحصه ولا يدخل الحساب.", eid, _entry_code(e)))
            out.excluded_count += 1
            continue
        if eid in by_id:
            diagnostics.append(Diagnostic("ENTRY_DUPLICATE", INTEGRITY, "معرّف مستند مكرر في بيانات الرحلة.", eid, _entry_code(e)))
            continue
        by_id[eid] = {"id": eid, "trip_id": trip, "entry_no": number, "entry_kind": kind, "trip_mode": e.get("trip_mode"), "correction_id": e.get("correction_id")}

    # ---- lines: key validation, orphan / duplicate detection
    lines_of, seen = {}, set()
    for row in lines:
        eid, no = _int(row.get("entry_id")), _int(row.get("line_no"))
        if eid is None or no is None:
            diagnostics.append(Diagnostic("LINE_KEYS_INVALID", INTEGRITY, "سطر دفتر بلا رقم مستند أو رقم سطر صالح: لا يمكن فحصه ولا يدخل الحساب.", eid, "", no))
            out.excluded_count += 1
            continue
        if (eid, no) in seen:
            diagnostics.append(Diagnostic("LINE_DUPLICATE", INTEGRITY, "رقم سطر مكرر داخل المستند.", eid, _entry_code(by_id.get(eid, {})), no))
        seen.add((eid, no))
        if eid not in by_id:
            diagnostics.append(Diagnostic("LINE_ORPHAN", INTEGRITY, "سطر دفتر يتبع مستندًا غير موجود في الرحلة.", eid, "", no))
            out.excluded_count += 1
            continue
        lines_of.setdefault(eid, []).append(row)

    # ---- legs / trades / stock: each row validated once; an unreadable row is a diagnostic and never a silently missing reference
    scrap_by_id, bad_leg_ids = {}, set()
    for m in scrap:
        mid = _int(m.get("id"))
        ok = (mid is not None and _int(m.get("karat_id")) is not None and _num(m.get("weight_delta_grams")) is not None and _text(m.get("movement_code")) is not None
              and (m.get("reference_id") is None or _int(m.get("reference_id")) is not None))
        if not ok:
            diagnostics.append(Diagnostic("LEG_DATA_INVALID", INTEGRITY, "ساق كسر ببيانات ناقصة أو غير صالحة: لا يمكن مقارنتها.", _int(m.get("reference_id")), ""))
            if mid is not None:
                bad_leg_ids.add(mid)
            continue
        scrap_by_id[mid] = {"id": mid, "karat_id": _int(m["karat_id"]), "weight_delta_grams": _num(m["weight_delta_grams"]), "movement_code": m["movement_code"],
                            "reference_id": None if m.get("reference_id") is None else _int(m["reference_id"])}
    trade_of, bad_trade_entries = {}, set()
    for t in trades:
        tid, total = _int(t.get("entry_id")), _num(t.get("total_price"))
        if tid is None or total is None or _text(t.get("entry_kind")) is None:
            diagnostics.append(Diagnostic("TRADE_DATA_INVALID", INTEGRITY, "وثيقة صفقة ببيانات ناقصة أو غير صالحة: لا يمكن مقارنتها.", tid, _entry_code(by_id.get(tid, {}))))
            if tid is not None:
                bad_trade_entries.add(tid)
            continue
        trade_of[tid] = {"entry_id": tid, "entry_kind": t["entry_kind"], "total_price": total}
    receive_of, bad_stock_items = {}, set()
    for s in stock:
        sid, sno = _int(s.get("item_entry_id")), _int(s.get("item_line_no"))
        grams, posted = _num(s.get("grams_delta")), _num(s.get("eq21_posted_delta"))
        if sid is None or sno is None or grams is None or posted is None or _text(s.get("movement_type")) is None:
            diagnostics.append(Diagnostic("STOCK_DATA_INVALID", INTEGRITY, "سجل مخزون ببيانات ناقصة أو غير صالحة: لا يمكن مقارنته.", sid, _entry_code(by_id.get(sid, {})), sno))
            if sid is not None and sno is not None:
                bad_stock_items.add((sid, sno))
            continue
        if s["movement_type"] == br.RECEIVE:
            receive_of[(sid, sno)] = {"grams_delta": grams, "eq21_posted_delta": posted}

    usable_lines = []                                                         # (entry, normalized line): the ONLY lines that reach the numbers
    claimed_scrap = set()
    for eid in sorted(by_id):
        entry = by_id[eid]
        kind, code = entry["entry_kind"], _entry_code(entry)
        if kind in EXCLUDED_KNOWN_KINDS:
            continue                                                          # cash-only entries and the future BK_RECON: known, never a gold source
        if kind not in SOURCE_KINDS:
            diagnostics.append(Diagnostic("UNSUPPORTED_KIND", UNSUPPORTED, f"{code}: نوع المستند «{kind}» غير مدعوم في هذه المعاينة ولا يُهمَل: المعاينة غير موثوقة.", eid, code))
            out.excluded_count += 1
            continue
        out.entry_count += 1
        correction = entry["correction_id"]
        effective = correction is None
        if correction is not None and _int(correction) is None:
            diagnostics.append(Diagnostic("ENTRY_INVALID", INTEGRITY, f"{code}: حالة العكس المخزّنة غير صالحة؛ لا تدخل أرقام هذا المستند في الحساب ولا تُعتمد.", eid, code))
            out.excluded_count += 1
            continue
        if not effective:
            out.reversed_count += 1
        rows = sorted(lines_of.get(eid, []), key=lambda r: _int(r.get("line_no")))
        if not effective:                                                     # a reversed entry keeps its original legs: they are claimed, not judged
            for row in rows:
                if _int(row.get("scrap_movement_id")) is not None:
                    claimed_scrap.add(_int(row.get("scrap_movement_id")))
            continue
        normalized = []
        for row in rows:
            line, usable = _normalize_line(row, entry, code, diagnostics)
            if line.get("scrap_movement_id") is not None:
                claimed_scrap.add(line["scrap_movement_id"])
            normalized.append((line, usable))
        if entry["trip_mode"] != br.BOOK or (trip_id is not None and entry["trip_id"] != trip_id):
            diagnostics.append(Diagnostic("ENTRY_MODE_MISMATCH", INTEGRITY, f"{code}: المستند ليس من النمط الدفتري لهذه الرحلة.", eid, code))
        if not normalized:
            diagnostics.append(Diagnostic("ENTRY_WITHOUT_LINES", INTEGRITY, f"{code}: مستند فعّال بلا أي سطر دفتر.", eid, code))
            continue
        for line, usable in normalized:
            no = line["line_no"]
            if not usable:
                out.excluded_count += 1
                continue                                                      # an invalid-FACTS line is not used for the numbers, the legs or the token
            _line_checks(entry, line, code, diagnostics)
            usable_lines.append((entry, line))
            leg = line["scrap_movement_id"]
            master = line["basis"] == br.MASTER_KARAT
            if not legs_ok:
                pass                                                          # the legs section could not be read: no leg judgement (the unreadable section is already reported)
            elif kind in SCRAP_LEG_CODE and master:
                if leg is not None and leg in bad_leg_ids:
                    continue                                                  # the leg row is unreadable (already diagnosed): do not also call it missing
                move = None if leg is None else scrap_by_id.get(leg)
                if leg is None or move is None:
                    diagnostics.append(Diagnostic("LEG_MISSING", INTEGRITY, f"{code} سطر {no}: ساق الكسر المطلوبة غير موجودة.", eid, code, no))
                else:
                    sign = -1 if kind == br.BK_ISSUE else 1
                    if (move["karat_id"] != line["karat_id"] or move["weight_delta_grams"] != sign * line["weight_grams"]
                            or move["movement_code"] != SCRAP_LEG_CODE[kind] or move["reference_id"] != eid):
                        diagnostics.append(Diagnostic("LEG_MISMATCH", INTEGRITY, f"{code} سطر {no}: ساق الكسر لا تطابق عيار السطر ووزنه واتجاهه ومرجعه.", eid, code, no))
            elif leg is not None:
                diagnostics.append(Diagnostic("LEG_UNEXPECTED", INTEGRITY, f"{code} سطر {no}: ساق كسر لسطر لا يحتاجها.", eid, code, no))
            if stock_ok and kind == br.BK_RETURN and line["basis"] == br.FINENESS and (eid, no) not in bad_stock_items:        # the ORIGINAL stock record exists (not "the item is still whole")
                record = receive_of.get((eid, no))
                if record is None:
                    diagnostics.append(Diagnostic("STOCK_RECORD_MISSING", INTEGRITY, f"{code} سطر {no}: سجل استلام المخزون المرتجع غير موجود.", eid, code, no))
                elif record["grams_delta"] != line["weight_grams"] or record["eq21_posted_delta"] != line["eq21_posted"]:
                    diagnostics.append(Diagnostic("STOCK_RECORD_MISMATCH", INTEGRITY, f"{code} سطر {no}: سجل المخزون المرتجع لا يطابق وزن السطر وقيمته المرحَّلة.", eid, code, no))
        if trades_ok and kind in PRICED_KINDS and eid not in bad_trade_entries:
            trade = trade_of.get(eid)
            prices = [line["line_price"] for line, _ in normalized]
            if trade is None:
                diagnostics.append(Diagnostic("TRADE_MISSING", INTEGRITY, f"{code}: وثيقة الصفقة غير موجودة.", eid, code))
            else:
                if trade["entry_kind"] != kind:
                    diagnostics.append(Diagnostic("TRADE_MISMATCH", INTEGRITY, f"{code}: نوع وثيقة الصفقة لا يطابق نوع المستند.", eid, code))
                if any(p is None for p in prices) or trade["total_price"] != sum(prices, Decimal("0.00")):
                    diagnostics.append(Diagnostic("TRADE_TOTAL_MISMATCH", INTEGRITY, f"{code}: إجمالي الصفقة لا يساوي مجموع أثمان الأسطر.", eid, code))
        elif trades_ok and kind not in PRICED_KINDS and eid in trade_of:
            diagnostics.append(Diagnostic("TRADE_ORPHAN", INTEGRITY, f"{code}: وثيقة صفقة لمستند ليس بيعًا ولا شراءً.", eid, code))
    for entry_id in (trade_of if trades_ok else ()):                          # a trade whose entry is not a source entry of this trip
        if entry_id not in by_id or by_id[entry_id]["entry_kind"] not in PRICED_KINDS:
            diagnostics.append(Diagnostic("TRADE_ORPHAN", INTEGRITY, "وثيقة صفقة لا تتبع بيعًا أو شراءً في هذه الرحلة.", entry_id, _entry_code(by_id.get(entry_id, {}))))
    for mid, move in (scrap_by_id.items() if legs_ok else ()):                # the reverse direction: a Scrap leg of a source entry that no line claims
        if mid in claimed_scrap or move["reference_id"] is None:
            continue
        owner = by_id.get(move["reference_id"])
        if owner is not None:
            diagnostics.append(Diagnostic("LEG_ORPHAN", INTEGRITY, f"{_entry_code(owner)}: ساق كسر لا يطالب بها أي سطر (سطر محذوف أو غير مربوط).", owner["id"], _entry_code(owner)))
    all_line_keys = {(_int(l.get("entry_id")), _int(l.get("line_no"))) for l in lines}
    for (entry_id, line_no) in (receive_of if stock_ok else ()):
        if (entry_id, line_no) not in all_line_keys:
            diagnostics.append(Diagnostic("STOCK_ORPHAN", INTEGRITY, "سجل استلام مخزون بلا سطر دفتر.", entry_id, _entry_code(by_id.get(entry_id, {})), line_no))

    # ---- the numbers: only the usable effective source lines (exact A, posted B, exact sum of |posted - exact|)
    stored = [br.stored_line({"direction": l["direction"], "weight_grams": l["weight_grams"], "basis": l["basis"], "karat_id": l["karat_id"], "karat_value": l["karat_value"],
                              "fineness_permille": l["fineness_permille"], "eq21_posted": l["eq21_posted"]}) for _, l in usable_lines]
    balance = br.balance_of(stored)
    out.a_in, out.a_out, out.b_in, out.b_out, out.sum_abs = balance.analytical_in, balance.analytical_out, balance.posted_in, balance.posted_out, balance.abs_difference
    out.line_count = len(stored)
    out.balance_status = PARTIAL if out.excluded_count else FULL
    out.a_state = PARTIAL if out.excluded_count else classify_a(out.a)
    try:
        out.token = None if out.excluded_count else source_token(entries, lines)
        if out.token is None:
            out.token_reason = "البيانات غير مكتملة أو غير صالحة: لا تُنتَج بصمة لمصدر ناقص."
    except SourceTokenError as exc:
        out.token, out.token_reason = None, f"تعذّر بناء بصمة المصدر: {exc}"
    if out.balance_status == FULL and out.a < 0:
        out.a_state = NEGATIVE
        diagnostics.append(Diagnostic("A_NEGATIVE", INTEGRITY, "الرصيد التحليلي سالب: اختلال في السجل لا تفسره زيادة؛ لا يُقترح فاقد ولا تُسجَّل زيادة لتغطيته — راجع المستندات.", None, ""))
    elif out.balance_status == FULL and out.trustworthy:
        loss = br.post(out.a) if out.a > 0 else br.ZERO3
        out.proposed_loss, out.delta, out.rounding_settlement = loss, Fraction(loss) - out.a, out.b - loss
        # identities that must hold by construction; a violation is a defect of this module or its input, never something to absorb
        if (out.b - out.proposed_loss - out.rounding_settlement != 0 or Fraction(out.rounding_settlement) != out.rho - out.delta
                or abs(out.delta) > POSTING_PRECISION or abs(Fraction(out.rounding_settlement)) > out.bound):
            diagnostics.append(Diagnostic("IDENTITY_VIOLATION", INTEGRITY, "هوية التسوية الحسابية لم تتحقق: لا يُقترح فاقد.", None, ""))
            out.proposed_loss = out.delta = out.rounding_settlement = None
    return out
