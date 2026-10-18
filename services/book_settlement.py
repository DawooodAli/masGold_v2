"""BOOK reconciliation SETTLEMENT (R2-S1, migration 057): the pure arithmetic, canonical serialisation, digests, stored snapshot and policy checks of the document that
settles the unsettled analytical balance of a BOOK trip. No database, no float, no tolerance, no write, no import of a service or repository. R1 (`services.book_reconciliation`,
the read-only preview) stays exactly as accepted: this module REUSES its `reconcile_preview` / `_normalize_line` / `classify_a` and `services.book_rules`; it never re-implements
the balance equations.

THE EXACT LATTICE. Every exact quantity of the reconciliation is an integer multiple of U = 1/26,250,000 g (lcm(21,000; 8,750,000)): a master-karat line is
`weight_milligrams x karat x 1250` units and an assayed line `weight_milligrams x fineness_tenths x 3` units. 0.001 g = 26,250 units and 0.0005 g = 13,125 units. Everything
below is computed with Python `int` on that lattice (never `Decimal` arithmetic on 30-digit values: the default context has 28 digits and would round silently):

  A       = sum analytical IN - sum analytical OUT      units        (the exact unsettled analytical balance)
  B       = sum posted IN - sum posted OUT              milligrams   (the stored, posted book)
  L       = round3(A) = (A + 13,125) // 26,250 (A > 0)  milligrams   the posted loss the owner approves
  delta   = L - A                                       units        |delta| <= 13,125
  rho     = B - A                                       units        posting drift
  r       = B - L = rho - delta                         milligrams   «فرق تقريب الترحيل» - neither loss nor gain
  theoretical effect on the posted position: -B = -L - r

CANONICAL SERIALISATION (one algorithm for writing and for verifying). The input is always an OBJECT (dict / list); the bytes are produced by `canonical_bytes` and the digest
is `sha256` of THOSE bytes. A stored JSON column is never hashed as text: the verifier parses the column into an object and re-serialises it here, so a driver or the JSON
column re-ordering keys or changing whitespace cannot matter. Allowed leaves: str, None, bool. Decimal figures are fixed-scale STRINGS (`fixed`), integers are decimal STRINGS
(`istr`, no sign for positives, no leading zeros, never "-0"), exact quantities are integer unit strings, lists are ordered by identity (numeric id), keys are sorted by code point.
A float, an int or a Decimal leaf is refused: precision can never slip into a digest.

TWO DIGESTS. `source_digest` (algorithm `book-recon-source-v1`) fingerprints the SOURCE facts (trip, mode, source kinds, every source entry with its reversal state and its
lines' physical facts and posted figures). It is not an integrity verdict and it does not cover the side evidence. `snapshot_digest` (algorithm `book-recon-snapshot-v1`) fingerprints
the whole stored snapshot (facts + evidence + legs + trades + stock records + totals + versions). The snapshot digest proves only that the stored snapshot was not altered after it
was written; the source's soundness is proved by recomputing it from the live rows. The contract versions are inside both payloads."""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json

from services import book_reconciliation as rec
from services import book_rules as br
from services.number_text import normalize_digits

RECON_ALGORITHM = "book-recon-v1"
SOURCE_ALGORITHM = "book-recon-source-v1"
SNAPSHOT_FORMAT = "book-recon-snapshot-v1"
CONTRACT_VERSIONS = {"recon_algorithm": RECON_ALGORITHM, "source_algorithm": SOURCE_ALGORITHM, "snapshot_format": SNAPSHOT_FORMAT}
SOURCE_KINDS = rec.SOURCE_KINDS
BK_RECON = br.BK_RECON
REVERSAL = "FULL_REVERSAL"

UNITS_PER_GRAM = 26_250_000
MILLI_UNITS = 26_250
HALF_MILLI_UNITS = 13_125
GRAM_LIMIT = 10 ** 15                      # every gram figure is below 10^15 (DECIMAL(18,3) / DECIMAL(21,6) have 15 integer digits)
UNIT_LIMIT = 10 ** 30                      # DECIMAL(30,0)
COUNTER_LIMIT = 4_294_967_295              # INT UNSIGNED
GENERATION_LIMIT = 65_535                  # SMALLINT UNSIGNED
DIGEST_LENGTH = 64

REASONS = {"OPERATING_LOSS": "فاقد تشغيل (سبك/تنظيف)", "PURITY_DIFFERENCE": "فرق نقاوة", "TRADING_LOSS": "فاقد تداول", "OTHER": "أخرى", "UNEXPLAINED": "غير مفسَّر"}
NOTE_REQUIRED_REASONS = ("OTHER", "UNEXPLAINED")
CONFIRMATION_KEYS = ("recording_complete", "loss_recognised_as_posted", "rounding_separate")
FLOOR_LAST_GOLD_ENTRY, FLOOR_TRIP_CREATED = "LAST_GOLD_ENTRY", "TRIP_CREATED"
DATE_TODAY = "TODAY"                       # the request payload marker of an OMITTED date (the resolved date is stored, never re-derived on a retry)
NOTE_MAX = 1000


class CanonicalError(ValueError):
    """A value that cannot be represented losslessly in the canonical form: refused before hashing."""


class SettlementDataError(ValueError):
    """The stored source cannot be digested or snapshotted (a missing / invalid fact): the caller refuses, it never invents a value."""


class SettlementIntegrityError(RuntimeError):
    """The integer computation and the R1 preview disagree, or an identity that holds by construction does not: a defect of this module or its input, never absorbed."""


# ------------------------------------------------------------------ canonical form
def istr(number):
    """An integer as its canonical decimal string (bool and non-int are refused)."""
    if isinstance(number, bool) or not isinstance(number, int):
        raise CanonicalError("integer expected")
    return str(number)


def fixed(value, places):
    """A finite Decimal as a fixed-scale string. Lossless only: a value with more decimals than `places` is refused; negative zero is normalised to zero."""
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise CanonicalError("Decimal expected")
    number = Decimal(value)
    if not number.is_finite():
        raise CanonicalError("non-finite number")
    step = Decimal(1).scaleb(-places)
    try:
        quantum = number.quantize(step)
    except InvalidOperation:
        raise CanonicalError("number out of range") from None
    if quantum != number:
        raise CanonicalError(f"more than {places} decimals")
    if quantum == 0:
        quantum = Decimal(0).quantize(step)
    return format(quantum, "f")


def _check_leaves(value, path="$"):
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, dict):
        for key, inner in value.items():
            if not isinstance(key, str):
                raise CanonicalError(f"{path}: non-string key")
            _check_leaves(inner, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, inner in enumerate(value):
            _check_leaves(inner, f"{path}[{index}]")
        return
    raise CanonicalError(f"{path}: {type(value).__name__} is not allowed (numbers travel as strings)")


def canonical_bytes(obj):
    _check_leaves(obj)
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest_of(obj):
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def is_digest(text):
    """Exactly 64 lowercase ASCII hex characters (case-sensitive by construction)."""
    return isinstance(text, str) and len(text) == DIGEST_LENGTH and all(c in "0123456789abcdef" for c in text)


# ------------------------------------------------------------------ exact integer helpers
def _milli(value):
    """Milligrams of a Decimal with at most three decimals, as an int (exact)."""
    number = Decimal(value)
    scaled = number.scaleb(3)
    if scaled != scaled.to_integral_value():
        raise SettlementDataError("more than three decimals")
    return int(scaled)


def exact_units(fraction):
    units = Fraction(fraction) * UNITS_PER_GRAM
    if units.denominator != 1:
        raise SettlementIntegrityError("a reconciliation quantity is off the 1/26,250,000 g lattice")
    return int(units)


def line_units(line):
    """The exact analytical units of one VALIDATED stored line (physical facts only)."""
    return exact_units(br.analytical(line["weight_grams"], line["basis"], line["karat_value"], line["fineness_permille"]))


def loss_milli_of(a_units):
    """round3 HALF_UP of a non-negative exact A (units) in milligrams: integers only."""
    return (a_units + HALF_MILLI_UNITS) // MILLI_UNITS if a_units > 0 else 0


def grams_of_units(units):
    return Fraction(units, UNITS_PER_GRAM)


def milli_text(milligrams):
    """The canonical three-decimal string of an integer number of milligrams, built from the integer alone (no Decimal arithmetic, no negative zero)."""
    sign = "-" if milligrams < 0 else ""
    magnitude = abs(milligrams)
    return f"{sign}{magnitude // 1000}.{magnitude % 1000:03d}"


def milli_decimal(milligrams):
    return Decimal(milli_text(milligrams))


# ------------------------------------------------------------------ the validated source
def _id(value):
    number = rec._int(value)
    return number if number is not None and number > 0 else None


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _validated_lines(entry, rows, code):
    """The strictly validated lines of ONE source entry, ordered by line number. A reversed entry's lines are validated too (they enter the digest); any invalid fact or
    any missing evidence field refuses the whole build (SettlementDataError): nothing is dropped or invented."""
    if not rows:
        raise SettlementDataError(f"{code}: source document has no lines (including reversed history)")
    out, numbers = [], set()
    for row in sorted(rows, key=lambda r: (rec._int(r.get("line_no")) is None, rec._int(r.get("line_no")) or 0)):
        diagnostics = []
        line, usable = rec._normalize_line(row, entry, code, diagnostics)
        if not usable or any(d.category == rec.INTEGRITY for d in diagnostics) or line["line_no"] is None or line["line_no"] <= 0:
            raise SettlementDataError(f"{code}: invalid line {line.get('line_no')}")
        for name in rec._EVIDENCE_FIELDS:
            if line[name] is None:
                raise SettlementDataError(f"{code}: line {line['line_no']} has no evidence field {name}")
        if line["line_no"] in numbers:
            raise SettlementDataError(f"{code}: duplicate line {line['line_no']}")
        numbers.add(line["line_no"])
        exact = br.analytical(line["weight_grams"], line["basis"], line["karat_value"], line["fineness_permille"])
        if (line["eq21_posted"] != br.post(exact) or line["eq21_analytical"] != br.evidence(exact)
                or line["posting_difference"] != br.round_half_up(Fraction(line["eq21_posted"]) - exact, br.EVIDENCE_PLACES)
                or line["signed_eq21_posted"] != (line["eq21_posted"] if line["direction"] == br.IN else -line["eq21_posted"])):
            raise SettlementDataError(f"{code}: line {line['line_no']} posted / evidence figures do not match its physical facts")
        out.append(line)
    return out


def source_entries(source):
    """The source-kind entries of a locked read as snapshot-shaped dictionaries (see `snapshot_object`), ordered by numeric id. Raises SettlementDataError for any entry,
    line, leg, trade or stock row that cannot be validated. Cash kinds and the reconciliation document itself are not source entries."""
    trip_id = _id(source.get("trip_id"))
    if trip_id is None:
        raise SettlementDataError("invalid trip id")
    lines_by_entry, legs, trades, stock = {}, {}, {}, {}
    for row in source.get("lines") or []:
        entry_id = _id(row.get("entry_id")) if isinstance(row, dict) else None
        if entry_id is None:
            raise SettlementDataError("book line without a valid entry id")
        lines_by_entry.setdefault(entry_id, []).append(row)
    for row in source.get("scrap") or []:
        leg_id = _id(row.get("id")) if isinstance(row, dict) else None
        karat, grams, reference = (rec._int(row.get("karat_id")), rec._num(row.get("weight_delta_grams")), rec._int(row.get("reference_id"))) if leg_id is not None else (None,) * 3
        if leg_id is None or karat is None or grams is None or not rec._text(row.get("movement_code")) or reference is None:
            raise SettlementDataError("invalid Scrap leg row")
        legs[leg_id] = {"id": istr(leg_id), "karat_id": istr(karat), "weight_delta_grams": fixed(grams, 3), "movement_code": row["movement_code"], "reference_id": istr(reference)}
    for row in source.get("trades") or []:
        entry_id = _id(row.get("entry_id")) if isinstance(row, dict) else None
        total = rec._num(row.get("total_price")) if entry_id is not None else None
        if entry_id is None or total is None or not rec._text(row.get("entry_kind")):
            raise SettlementDataError("invalid trade row")
        trades[entry_id] = {"entry_id": istr(entry_id), "entry_kind": row["entry_kind"], "total_price": fixed(total, 2)}
    for row in source.get("stock") or []:
        stock_id = _id(row.get("id")) if isinstance(row, dict) else None             # the record identity: the locked read selects `s.id`
        item, number = (_id(row.get("item_entry_id")), _id(row.get("item_line_no"))) if stock_id is not None else (None, None)
        grams, posted = (rec._num(row.get("grams_delta")), rec._num(row.get("eq21_posted_delta"))) if item is not None else (None, None)
        if stock_id is None or item is None or number is None or grams is None or posted is None or not rec._text(row.get("movement_type")):
            raise SettlementDataError("invalid stock record row (identity, item, grams or posted figure missing)")
        stock.setdefault(item, []).append({"id": istr(stock_id), "item_entry_id": istr(item), "item_line_no": istr(number), "movement_type": row["movement_type"],
                                           "grams_delta": fixed(grams, 3), "eq21_posted_delta": fixed(posted, 3), "_order": stock_id})
    entries, seen = [], set()
    rows = [r for r in source.get("entries") or []]
    for row in rows:
        if not isinstance(row, dict) or _id(row.get("id")) is None or not rec._text(row.get("entry_kind")):
            raise SettlementDataError("entry without a valid id or kind")
    for row in sorted((r for r in rows if r["entry_kind"] in SOURCE_KINDS), key=lambda r: _id(r["id"])):
        entry_id, kind = _id(row["id"]), row["entry_kind"]
        number = _id(row.get("entry_no"))
        if entry_id in seen or number is None or _id(row.get("trip_id")) != trip_id or row.get("trip_mode") != br.BOOK:
            raise SettlementDataError(f"invalid source entry {entry_id}")
        seen.add(entry_id)
        entry_date = _as_date(row.get("entry_date"))
        if entry_date is None:
            raise SettlementDataError(f"source entry {entry_id} has no valid date")
        correction = row.get("correction_id")
        correction_id = None if correction is None else _id(correction)
        if correction is not None and correction_id is None:
            raise SettlementDataError(f"source entry {entry_id} has an invalid reversal state")
        if correction_id is not None and row.get("correction_type") != REVERSAL:
            raise SettlementDataError(f"source entry {entry_id} reversal type is not {REVERSAL}")
        code = rec._entry_code({"trip_id": trip_id, "entry_no": number})
        normalized = _validated_lines({"id": entry_id, "trip_id": trip_id, "entry_kind": kind}, lines_by_entry.get(entry_id, []), code)
        lines, entry_legs = [], []
        for line in normalized:
            lines.append({"line_no": istr(line["line_no"]), "direction": line["direction"], "basis": line["basis"],
                          "karat_id": None if line["karat_id"] is None else istr(line["karat_id"]),
                          "karat_value": None if line["karat_value"] is None else istr(line["karat_value"]),
                          "fineness_permille": None if line["fineness_permille"] is None else fixed(line["fineness_permille"], 1),
                          "weight_grams": fixed(line["weight_grams"], 3), "eq21_posted": fixed(line["eq21_posted"], 3),
                          "eq21_analytical": fixed(line["eq21_analytical"], 6), "posting_difference": fixed(line["posting_difference"], 6),
                          "signed_eq21_posted": fixed(line["signed_eq21_posted"], 3),
                          "line_price": None if line["line_price"] is None else fixed(line["line_price"], 2),
                          "scrap_movement_id": None if line["scrap_movement_id"] is None else istr(line["scrap_movement_id"]),
                          "a_units": istr(line_units(line))})
            leg_id = line["scrap_movement_id"]
            if leg_id is not None and leg_id in legs:
                entry_legs.append(legs[leg_id])
        trade = trades.get(entry_id)
        entries.append({"id": istr(entry_id), "entry_no": istr(number), "kind": kind, "entry_date": entry_date.isoformat(),
                        "state": "EFFECTIVE" if correction_id is None else "REVERSED",
                        "correction_id": None if correction_id is None else istr(correction_id),
                        "correction_type": None if correction_id is None else REVERSAL,
                        "lines": lines, "legs": sorted(entry_legs, key=lambda l: int(l["id"])), "trade": trade,
                        "stock": [{k: v for k, v in s.items() if k != "_order"} for s in sorted(stock.get(entry_id, []), key=lambda s: s["_order"])]})
    return entries


# ------------------------------------------------------------------ the source digest
_DIGEST_LINE_KEYS = ("line_no", "direction", "basis", "karat_id", "karat_value", "fineness_permille", "weight_grams", "eq21_posted")


def source_payload(trip_id, entries):
    """The facts the source digest fingerprints, projected from snapshot-shaped entries (so the live rows and a stored snapshot produce the SAME bytes)."""
    return {"algorithm": SOURCE_ALGORITHM, "trip_id": istr(trip_id), "accounting_mode": br.BOOK, "source_kinds": list(SOURCE_KINDS),
            "entries": [{"id": e["id"], "entry_no": e["entry_no"], "kind": e["kind"], "state": e["state"], "correction_id": e["correction_id"],
                         "correction_type": e["correction_type"],
                         "lines": [{k: l[k] for k in _DIGEST_LINE_KEYS} for l in sorted(e["lines"], key=lambda l: int(l["line_no"]))]}
                        for e in sorted(entries, key=lambda e: int(e["id"]))]}


def source_digest(trip_id, entries):
    return digest_of(source_payload(trip_id, entries))


# ------------------------------------------------------------------ the arithmetic
@dataclass(frozen=True)
class Settlement:
    a_in_units: int
    a_out_units: int
    p_in_milli: int
    p_out_milli: int
    sum_abs_units: int
    entry_count: int
    line_count: int
    reversed_count: int

    @property
    def a_star_units(self):
        return self.a_in_units - self.a_out_units

    @property
    def b_star_milli(self):
        return self.p_in_milli - self.p_out_milli

    @property
    def loss_milli(self):
        return loss_milli_of(self.a_star_units)

    @property
    def delta_units(self):
        return self.loss_milli * MILLI_UNITS - self.a_star_units

    @property
    def rho_units(self):
        return self.b_star_milli * MILLI_UNITS - self.a_star_units

    @property
    def rounding_milli(self):
        return self.b_star_milli - self.loss_milli

    @property
    def a_state(self):
        return rec.classify_a(grams_of_units(self.a_star_units))

    def check_identities(self):
        """Identities that hold by construction; a violation is a defect, never something to absorb."""
        if self.a_star_units < 0:
            raise SettlementIntegrityError("A is negative")
        if (self.b_star_milli - self.loss_milli - self.rounding_milli != 0 or self.rounding_milli * MILLI_UNITS != self.rho_units - self.delta_units
                or abs(self.delta_units) > HALF_MILLI_UNITS):
            raise SettlementIntegrityError("a settlement identity does not hold")
        if abs(self.rounding_milli) * MILLI_UNITS > self.sum_abs_units + HALF_MILLI_UNITS:
            raise SettlementIntegrityError("the rounding settlement exceeds the posting-drift bound")
        if self.loss_milli != int(br.post(grams_of_units(self.a_star_units)) * 1000):
            raise SettlementIntegrityError("the integer loss differs from services.book_rules.post")

    def totals(self):
        """The `totals` object of the snapshot: every figure as a canonical string."""
        return {"a_in_units": istr(self.a_in_units), "a_out_units": istr(self.a_out_units), "a_star_units": istr(self.a_star_units),
                "p_in_milli": istr(self.p_in_milli), "p_out_milli": istr(self.p_out_milli),
                "b_star": milli_text(self.b_star_milli), "sum_abs_units": istr(self.sum_abs_units), "rho_units": istr(self.rho_units),
                "loss_posted": milli_text(self.loss_milli), "rounding_settlement": milli_text(self.rounding_milli),
                "delta_units": istr(self.delta_units), "entry_count": istr(self.entry_count), "line_count": istr(self.line_count), "reversed_count": istr(self.reversed_count)}


def compute(entries):
    """The Settlement of snapshot-shaped entries: only the EFFECTIVE entries' lines enter the numbers; the counters follow R1 (every source entry, the reversed ones, the
    effective lines). Integers only."""
    a_in = a_out = p_in = p_out = bound = lines = reversed_count = 0
    for entry in entries:
        if entry["state"] == "REVERSED":
            reversed_count += 1
            continue
        for line in entry["lines"]:
            units, posted = int(line["a_units"]), _milli(Decimal(line["eq21_posted"]))
            bound += abs(posted * MILLI_UNITS - units)
            lines += 1
            if line["direction"] == br.IN:
                a_in += units
                p_in += posted
            else:
                a_out += units
                p_out += posted
    return Settlement(a_in, a_out, p_in, p_out, bound, len(entries), lines, reversed_count)


def match_preview(settlement, preview):
    """The independent integer computation must agree with the R1 preview of the same rows (A, B, the loss, the rounding settlement, the counters)."""
    same = (preview.a == grams_of_units(settlement.a_star_units) and preview.b == milli_decimal(settlement.b_star_milli)
            and preview.proposed_loss == milli_decimal(settlement.loss_milli) and preview.rounding_settlement == milli_decimal(settlement.rounding_milli)
            and preview.entry_count == settlement.entry_count and preview.line_count == settlement.line_count and preview.reversed_count == settlement.reversed_count
            and preview.sum_abs * UNITS_PER_GRAM == settlement.sum_abs_units)
    if not same:
        raise SettlementIntegrityError("the integer settlement and the R1 preview disagree")


def a_star_evidence(settlement):
    """Six-decimal display evidence of A (never an input of a comparison); the stored column is DECIMAL(21,6)."""
    return br.evidence(grams_of_units(settlement.a_star_units))


def check_capacity(settlement, generation=None):
    """Every stored / hashed figure fits its column AFTER the display / posting rounding. Absolute values for the signed ones. Raises br.BookError RECONCILIATION_CAPACITY."""
    def refuse():
        raise br.BookError("RECONCILIATION_CAPACITY", "حجم أرصدة هذه العهدة يتجاوز الحد المسموح لمستند التسوية: لا يمكن اعتمادها.")
    limit = GRAM_LIMIT * UNITS_PER_GRAM
    if max(settlement.a_in_units, settlement.a_out_units, abs(settlement.a_star_units), settlement.sum_abs_units) >= limit:
        refuse()
    if max(settlement.p_in_milli, settlement.p_out_milli, abs(settlement.b_star_milli), abs(settlement.loss_milli), abs(settlement.rounding_milli)) >= GRAM_LIMIT * 1000:
        refuse()
    if max(abs(settlement.a_star_units), abs(settlement.delta_units), abs(settlement.rho_units), settlement.sum_abs_units) >= UNIT_LIMIT:
        refuse()
    if a_star_evidence(settlement) >= GRAM_LIMIT:                       # the rounded evidence may reach 10^15 although A itself is below it
        refuse()
    if max(settlement.entry_count, settlement.line_count, settlement.reversed_count) > COUNTER_LIMIT:
        refuse()
    if generation is not None and not 1 <= generation <= GENERATION_LIMIT:
        raise br.BookError("RECONCILIATION_GENERATION_EXHAUSTED", "استُنفدت أجيال تسوية هذه العهدة: لا يمكن إنشاء تسوية جديدة.")


# ------------------------------------------------------------------ the stored snapshot
def snapshot_object(trip_id, entries, settlement):
    """The full snapshot (facts, evidence, identities, totals, versions): an object of strings, None and bools only."""
    return {"snapshot_version": SNAPSHOT_FORMAT, "recon_algorithm": RECON_ALGORITHM, "source_algorithm": SOURCE_ALGORITHM, "source_kinds": list(SOURCE_KINDS),
            "trip": {"id": istr(trip_id), "mode": br.BOOK, "code": f"MT{trip_id:06d}"}, "units": {"denominator": istr(UNITS_PER_GRAM)},
            "entries": sorted(entries, key=lambda e: int(e["id"])), "totals": settlement.totals()}


def build(source):
    """(trip_id, entries, settlement, source_digest, snapshot, snapshot_digest) of a locked read. Raises SettlementDataError / SettlementIntegrityError."""
    trip_id = _id(source.get("trip_id"))
    entries = source_entries(source)
    settlement = compute(entries)
    settlement.check_identities()
    check_capacity(settlement)                                           # before anything is serialised: a figure that does not fit its column is refused, never cut
    snapshot = snapshot_object(trip_id, entries, settlement)
    return trip_id, entries, settlement, source_digest(trip_id, entries), snapshot, digest_of(snapshot)


def parse_snapshot(stored):
    """The object of a stored JSON column (text, bytes or an already parsed object)."""
    if isinstance(stored, (bytes, bytearray)):
        stored = stored.decode("utf-8")
    return json.loads(stored) if isinstance(stored, str) else stored


def verify_snapshot(snapshot):
    """Recompute everything a snapshot states from its own content. Returns a list of problem codes (empty = consistent). It proves internal consistency of the stored
    object; it is NOT independent proof of the source (that needs the live rows)."""
    problems = []
    try:
        _check_leaves(snapshot)
        if (snapshot.get("snapshot_version") != SNAPSHOT_FORMAT or snapshot.get("recon_algorithm") != RECON_ALGORITHM or snapshot.get("source_algorithm") != SOURCE_ALGORITHM
                or snapshot.get("source_kinds") != list(SOURCE_KINDS)):
            return ["VERSION_MISMATCH"]
        trip_id = int(snapshot["trip"]["id"])
        entries = snapshot["entries"]
        for entry in entries:
            for line in entry["lines"]:
                facts = {"weight_grams": Decimal(line["weight_grams"]), "basis": line["basis"], "karat_value": None if line["karat_value"] is None else int(line["karat_value"]),
                         "fineness_permille": None if line["fineness_permille"] is None else Decimal(line["fineness_permille"])}
                exact = br.analytical(facts["weight_grams"], facts["basis"], facts["karat_value"], facts["fineness_permille"])
                posted = Decimal(line["eq21_posted"])
                if line["a_units"] != istr(exact_units(exact)):
                    problems.append("LINE_UNITS_MISMATCH")
                if posted != br.post(exact) or line["eq21_analytical"] != fixed(br.evidence(exact), 6):
                    problems.append("LINE_EVIDENCE_MISMATCH")
                if line["posting_difference"] != fixed(br.round_half_up(Fraction(posted) - exact, br.EVIDENCE_PLACES), 6):
                    problems.append("LINE_DIFFERENCE_MISMATCH")
                if line["signed_eq21_posted"] != fixed(posted if line["direction"] == br.IN else -posted, 3):
                    problems.append("LINE_SIGN_MISMATCH")
        settlement = compute(entries)
        settlement.check_identities()
        if settlement.totals() != snapshot["totals"]:
            problems.append("TOTALS_MISMATCH")
        if snapshot["units"] != {"denominator": istr(UNITS_PER_GRAM)} or snapshot["trip"]["mode"] != br.BOOK:
            problems.append("HEADER_MISMATCH")
        source_digest(trip_id, entries)
    except (CanonicalError, KeyError, TypeError, ValueError, InvalidOperation, AttributeError, SettlementIntegrityError, br.BookError):
        problems.append("SNAPSHOT_UNREADABLE")
    return sorted(set(problems))


_COLUMN_TOTALS = (("a_star_units", "a_star_units"), ("b_star", "b_star"), ("loss_posted", "loss_posted"), ("rounding_settlement", "rounding_settlement"),
                  ("delta_units", "delta_units"), ("rho_units", "rho_units"), ("sum_abs_units", "sum_abs_units"), ("entry_count", "entry_count"),
                  ("line_count", "line_count"), ("reversed_count", "reversed_count"))


def verify_record(row):
    """Verify one stored reconciliation row (the table columns, never a hash of stored JSON text): the snapshot is parsed and RE-SERIALISED, its digest, its totals, its source
    digest and its recomputed figures are compared with the columns. Returns the list of problem codes (empty = consistent)."""
    try:
        snapshot = parse_snapshot(row["snapshot"])
    except (ValueError, TypeError, KeyError):
        return ["SNAPSHOT_UNREADABLE"]
    problems = list(verify_snapshot(snapshot))
    if problems and problems == ["SNAPSHOT_UNREADABLE"]:
        return problems
    try:
        if row.get("algorithm") != RECON_ALGORITHM:
            problems.append("ALGORITHM_MISMATCH")
        if row.get("source_algorithm") != SOURCE_ALGORITHM:
            problems.append("SOURCE_ALGORITHM_MISMATCH")
        if row.get("snapshot_format") != SNAPSHOT_FORMAT:
            problems.append("SNAPSHOT_FORMAT_MISMATCH")
        if (_id(row.get("trip_id")) != _id(snapshot["trip"]["id"])
                or row.get("trip_mode") != snapshot["trip"]["mode"] or row.get("entry_kind") != BK_RECON):
            problems.append("COLUMN_IDENTITY_MISMATCH")
        if not is_digest(row.get("snapshot_digest")) or not is_digest(row.get("source_digest")):
            problems.append("DIGEST_FORMAT")
        elif digest_of(snapshot) != row["snapshot_digest"]:
            problems.append("SNAPSHOT_DIGEST_MISMATCH")
        elif source_digest(int(snapshot["trip"]["id"]), snapshot["entries"]) != row["source_digest"]:
            problems.append("SOURCE_DIGEST_MISMATCH")
        totals = snapshot["totals"]
        for column, key in _COLUMN_TOTALS:
            if str(row[column]) != totals[key] and not _same_number(row[column], totals[key]):
                problems.append(f"COLUMN_{column.upper()}_MISMATCH")
        if not _same_number(row["a_star"], fixed(br.evidence(grams_of_units(int(totals["a_star_units"]))), 6)):
            problems.append("COLUMN_A_STAR_MISMATCH")
        if int(row["unit_denominator"]) != UNITS_PER_GRAM:
            problems.append("COLUMN_UNIT_DENOMINATOR_MISMATCH")
    except (CanonicalError, KeyError, TypeError, ValueError, InvalidOperation, AttributeError):
        problems.append("RECORD_UNREADABLE")
    return sorted(set(problems))


def _same_number(left, right):
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except InvalidOperation:
        return False


# ------------------------------------------------------------------ the request contract
def clean_confirmations(confirmations):
    """The three confirmations, every one exactly True (a bool), nothing else accepted. Returns the normalised dict; raises RECONCILIATION_CONFIRM_REQUIRED."""
    if (not isinstance(confirmations, dict) or set(confirmations) != set(CONFIRMATION_KEYS) or any(confirmations[k] is not True for k in CONFIRMATION_KEYS)):
        raise br.BookError("RECONCILIATION_CONFIRM_REQUIRED", "أكّد الإقرارات الثلاثة كلها لاعتماد التسوية.")
    return {k: True for k in CONFIRMATION_KEYS}


def clean_versions(versions):
    if versions != CONTRACT_VERSIONS:
        raise br.BookError("RECONCILIATION_CONTRACT_MISMATCH", "إصدار عقد التسوية الذي عُرضت به الأرقام لا يطابق إصدار النظام: أعد فتح المعاينة.")
    return dict(CONTRACT_VERSIONS)


def clean_reason(reason_code, note):
    """(reason_code | None, note | None) after the shape rules that do not depend on the loss: the code is one of the five, a blank code is none, OTHER and UNEXPLAINED need a
    non-blank note, the note fits the column. The loss-dependent rule (a reason is mandatory when L != 0) is `require_reason`."""
    code = (str(reason_code).strip() if reason_code is not None else "") or None
    text = (str(note).strip() if note is not None else "") or None
    if code is not None and code not in REASONS:
        raise br.BookError("REASON_INVALID", "سبب التسوية غير معروف: اختر من القائمة.")
    if text is not None and len(text) > NOTE_MAX:
        raise br.BookError("TEXT_LONG", "الملاحظة أطول من الحد المسموح.")
    if code in NOTE_REQUIRED_REASONS and text is None:
        raise br.BookError("NOTE_REQUIRED", "اكتب ملاحظة توضيحية مع هذا السبب.")
    return code, text


def require_reason(reason_code, loss_milli):
    """The reason is mandatory exactly when the posted loss is not zero (L != 0 <=> A >= 0.0005 g); optional when L == 0 (A is stored and confirmed in both cases)."""
    if loss_milli != 0 and reason_code is None:
        raise br.BookError("REASON_REQUIRED", "اختر سبب الفاقد: السبب إلزامي عندما تكون قيمة الفاقد المرحّلة غير صفر.")


def clean_date_input(value):
    """The OMITTED date (None / blank) is the marker DATE_TODAY; an explicit date is an ISO string. Format errors are refused here, range errors against the window later."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return DATE_TODAY
    chosen = _as_date(normalize_digits(value) if isinstance(value, str) else value)
    if chosen is None:
        raise br.BookError("DATE_INVALID", "التاريخ غير صحيح.")
    return chosen.isoformat()


def date_window(source, trip_created_at):
    """(floor date, floor source): the latest `entry_date` of the SOURCE-kind documents of the trip (reversed ones included; cash entries and the reconciliation itself are
    excluded); with no source document, the trip's creation date. A source document without a valid date makes the window unavailable (no date is invented)."""
    dates = []
    for row in source.get("entries") or []:
        if isinstance(row, dict) and row.get("entry_kind") in SOURCE_KINDS:
            chosen = _as_date(row.get("entry_date"))
            if chosen is None:
                raise SettlementDataError(f"source entry {row.get('id')} has no valid date")
            dates.append(chosen)
    if dates:
        return max(dates), FLOOR_LAST_GOLD_ENTRY
    created = _as_date(trip_created_at)
    if created is None:
        raise SettlementDataError("the trip has no valid creation date")
    return created, FLOOR_TRIP_CREATED


def resolve_effective_date(marker, floor, today):
    """The effective date: today for the omitted marker, else the explicit date; it may not be before `floor` nor after `today` (the SERVER date)."""
    chosen = today if marker == DATE_TODAY else date.fromisoformat(marker)
    if floor > today:
        raise br.BookError("EFFECTIVE_DATE_RANGE", f"آخر حركة ذهب في العهدة بتاريخ {floor.isoformat()} بعد تاريخ اليوم: لا يوجد تاريخ تسوية صالح.")
    if chosen > today:
        raise br.BookError("EFFECTIVE_DATE_RANGE", "تاريخ التسوية لا يكون في المستقبل.")
    if chosen < floor:
        raise br.BookError("EFFECTIVE_DATE_RANGE", f"تاريخ التسوية لا يسبق آخر حركة ذهب في العهدة ({floor.isoformat()}).")
    return chosen


def request_payload(trip_id, source_digest_text, date_marker, reason_code, note, confirmations):
    return {"kind": BK_RECON, "trip_id": istr(trip_id), "source_digest": source_digest_text, "effective_date": date_marker, "reason_code": reason_code, "note": note,
            "confirmations": dict(confirmations), "contract_versions": dict(CONTRACT_VERSIONS)}


def request_hash(trip_id, source_digest_text, date_marker, reason_code, note, confirmations):
    return digest_of(request_payload(trip_id, source_digest_text, date_marker, reason_code, note, confirmations))


def confirmation_texts(a_text, loss_text, a_units=None):
    """The Arabic wording of the three confirmations. The second one covers the analytical balance A itself, so it stays meaningful when the posted loss is zero."""
    exact = "" if a_units is None else f"؛ القيمة الدقيقة {istr(a_units)} وحدة، والوحدة 1/{UNITS_PER_GRAM} جرام"
    return {"recording_complete": "أُقِرّ بأن كل حركات هذه العهدة قد سُجِّلت بالكامل.",
            "loss_recognised_as_posted": f"أعتمد الرصيد التحليلي غير المسوّى A = {a_text} جرام (مكافئ عيار 21{exact})، وأعتمد الفاقد المرحّل L = {loss_text} جرام منه، حتى لو كان L صفرًا.",
            "rounding_separate": "أُقِرّ بأن فرق تقريب الترحيل r منفصل: ليس فاقدًا ولا زيادة."}


def readiness_problems(preview):
    """Why a preview cannot be settled (empty = ready): incomplete / untrustworthy sources, a partial or unavailable balance, a negative A (an integrity alarm, never a
    gain path) or no proposal. Returns a list of (code, Arabic message)."""
    problems = [(d.code, d.message) for d in preview.diagnostics if d.category in rec.BLOCKING]
    if preview.balance_status != rec.FULL:
        problems.append(("BALANCE_NOT_FULL", "الرصيد غير كامل: لا تُعتمد تسوية على أرقام جزئية."))
    if preview.a_state not in (rec.ZERO, rec.POSITIVE_BELOW, rec.POSITIVE):
        problems.append(("A_STATE_NOT_SETTLEABLE", "حالة الرصيد التحليلي لا تسمح بالتسوية."))
    if preview.proposed_loss is None or preview.rounding_settlement is None:
        problems.append(("NO_PROPOSAL", "لا يوجد اقتراح تسوية للأرقام الحالية."))
    return problems
