"""Reversal / Correction V1 (023): corrections of finalized transactions, never edits of them.

A finalized Invoice, Exchange or Return stays exactly as finalized. A manager (TRANSACTION_CORRECT,
re-checked inside the transaction) records one new FINALIZED correction with a mandatory reason:

FULL_REVERSAL (RV)  — the source should not have happened. Every posted effect is negated from the
  STORED rows (never a current price, making or weight):
    money       the signed cash / bank movements of the source (+ its settlement corrections),
                negated per real account (a negative balance is allowed);
    customer    the customer-ledger rows of the source, negated per customer;
    Invoice     sold pieces SOLD → IN_STOCK (same item, same barcode); F7 grams back IN; the
                customer's old gold bought on the invoice OUT again (only while the counted stock
                still holds it); open F5 cases CANCELLED;
    Exchange    returned pieces RETURNED_PENDING → SOLD (open inspection DISMISSED); a piece held in
                Scrap (032, hold HELD): CONVERTED_TO_OLD_GOLD → SOLD, its exact held weight leaves Scrap
                (REVERSAL_EXCHANGE_RETURN_SCRAP_OUT), hold REVERSED, review DISMISSED; a piece the
                manager already decided (hold COMMITTED / RETURNED_TO_STOCK) blocks the reversal; identified
                replacements SOLD → IN_STOCK; F7 replacement grams back IN; F5 case CANCELLED;
    Return      pieces RETURNED_PENDING → SOLD (open inspection DISMISSED); a piece held in Scrap (033, HELD):
                CONVERTED_TO_OLD_GOLD → SOLD, REVERSAL_RETURN_SCRAP_OUT of the held weight, hold REVERSED;
                a decided piece (COMMITTED / RETURNED_TO_STOCK) blocks it; F7 grams OUT again.
  The reversed lifecycle lines are linked to the reversal, so the sale line is eligible again.
  One reversal per source (UNIQUE key). Anything downstream blocks it (newest first, no cascade).

SETTLEMENT_CORRECTION (FC) — the transaction is right, the money route was wrong. The new
  allocation (real legs in the source's own direction + customer-account part) must equal the
  current settled total; only the per-account / customer differences are posted. A chain per
  source (sequence 1..n, UNIQUE); each correction acts on the current effective settlement.

Lock order: actor → original invoice row (serializes every lifecycle of the invoice) → items →
reviews / cases → old-gold ledger → bank accounts.
"""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from services.number_text import normalize_digits
from services.old_gold_availability import held_grams
import json

from repositories.correction_repository import CorrectionRepository
from repositories.customer_repository import CustomerRepository
from repositories.invoice_lifecycle_repository import exchange_code
from repositories.invoice_repository import InvoiceRepository
from repositories.invoice_return_repository import return_code
from services.authorization_service import AuthorizationService
from services import custody_rules
from services.correction_rules import correction_code, reversal_of
from services.invoice_service import InvoiceService, PaymentInput, invoice_transaction


CORRECT_PERMISSION = "TRANSACTION_CORRECT"
FULL_REVERSAL, SETTLEMENT = "FULL_REVERSAL", "SETTLEMENT_CORRECTION"
CS = "CUSTOMER_ACCOUNT_SETTLEMENT"
FO = "FINANCIAL_OPERATION"            # manual deposit / withdrawal / transfer (025): full reversal only
CD = "CUSTOMER_DEPOSIT"               # عربون / استرداد عربون (027): full reversal only
SMP = "SUPPLIER_MAKING_PAYMENT"       # supplier making payment (036): full reversal only, OWNER only
SMP_REVERSE_PERMISSION = "SUPPLIER_MAKING_PAY_REVERSE"
MTE = "MARKET_TRIP_ENTRY"             # market-trip custody issue / return (037): full reversal only, OWNER only
MTE_PERMISSION = "MARKET_TRIP_MANAGE"
SGS = "SUPPLIER_GOLD_SETTLEMENT"      # direct supplier gold settlement from shop Scrap (045): full reversal only, OWNER only
SGS_REVERSE_PERMISSION = "SUPPLIER_GOLD_SETTLE_REVERSE"
HOLDING_REVERSE_PERMISSION = "INTERMEDIARY_HOLDING_REVERSE"
SOURCE_RIGHTS = {SMP: SMP_REVERSE_PERMISSION, MTE: MTE_PERMISSION, SGS: SGS_REVERSE_PERMISSION}   # a right ON TOP of TRANSACTION_CORRECT
SOURCES = ("INVOICE", "EXCHANGE", "RETURN", CS, FO, CD, SMP, MTE, SGS)
STANDALONE = (CS, FO, CD, SMP, MTE, SGS)    # sources without an invoice (never a fabricated one)
MONEY = Decimal("0.01")
ZERO, ZERO_GRAMS = Decimal("0.00"), Decimal("0.000")
METHOD_TEXT = {"PHYSICAL_CASH": "نقدي", "MOBILE_WALLET": "محفظة", "INSTAPAY": "InstaPay", "BANK_TRANSFER": "تحويل بنكي"}
SOURCE_TEXT = {"INVOICE": "الفاتورة", "EXCHANGE": "الاستبدال", "RETURN": "المرتجع"}


SOURCE_TEXT[CS] = "\u062a\u0633\u0648\u064a\u0629 \u062d\u0633\u0627\u0628"
SOURCE_TEXT[FO] = "العملية المالية"
SOURCE_TEXT[CD] = "العربون"
SOURCE_TEXT[SMP] = "دفعة المصنعيات"
SOURCE_TEXT[MTE] = "حركة عهدة السوق"
SOURCE_TEXT[SGS] = "تسوية ذهب المورد"


def correction_source_text(source_type, direction=None, *, definite=True):
    """Canonical correction labels, using the stored operation type and deposit direction."""
    if source_type == CD and direction == "OUT":
        return "استرداد العربون" if definite else "استرداد عربون"
    if source_type == FO and not definite:
        return "عملية مالية"
    label = SOURCE_TEXT.get(source_type, "العملية")
    return label if definite else label.removeprefix("ال")


class CorrectionError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class _Discard(Exception):
    pass


@dataclass(frozen=True)
class SettlementLeg:
    method_code: str
    amount: str
    bank_account_id: str | None = None


@dataclass
class _Plan:
    source_type: str
    source_id: str
    reference: str
    invoice: dict
    customer_id: str | None
    money: dict = field(default_factory=dict)            # (ledger, account id) → signed delta
    account: dict = field(default_factory=dict)          # customer id → signed delta
    inventory: list = field(default_factory=list)        # (item id, from, to, movement code)
    old_gold: list = field(default_factory=list)         # (karat id, karat, signed grams, movement code)
    reviews: list = field(default_factory=list)          # open inspection review ids to dismiss
    holds: list = field(default_factory=list)            # (hold id, karat id, karat, grams, item id, OUT code)
    cases: list = field(default_factory=list)            # open reconciliation case ids to cancel
    lines: tuple = ()                                    # (table, [line ids]) linked to the reversal
    blockers: list = field(default_factory=list)         # (code, Arabic reason)
    making: list = field(default_factory=list)           # (supplier id, signed making delta, movement code)
    lot_returns: list = field(default_factory=list)      # (lot id, grams) a LOT_RETURN reversal takes back out of the shop lot stock
    lot_issues: list = field(default_factory=list)       # (lot id, grams) a LOT_ISSUE reversal puts back into the shop lot stock
    extra_permission: str = ""                           # a right required ON TOP of the source's own (a custody supplier payment)
    supplier_gold: list = field(default_factory=list)    # (supplier id, karat id, signed grams, signed Eq21, movement code) a gold settlement reversal restores
    holdings: list = field(default_factory=list)         # (pool id, document id, doc kind, movement type, grams, signed delta) explicit holding compensations (048)


def _reason(text):
    reason = (text or "").strip()
    if not reason:
        raise CorrectionError("REASON_REQUIRED", "اكتب سبب التصحيح.")
    if len(reason) > 1000:
        raise CorrectionError("REASON_TOO_LONG", "السبب طويل جداً.")
    return reason


def _money_value(text, code="AMOUNT_INVALID", message="المبلغ غير صحيح."):
    try:
        value = Decimal(normalize_digits(str(text)).strip())
    except (InvalidOperation, ValueError):
        raise CorrectionError(code, message) from None
    if not value.is_finite() or value < 0 or value != value.quantize(MONEY):
        raise CorrectionError(code, message)
    return value


class CorrectionService:
    def __init__(self, session, *, authorization=None, transaction_factory=invoice_transaction,
                 invoice_repository=InvoiceRepository, correction_repository=CorrectionRepository,
                 customer_repository=CustomerRepository):
        self._session = session
        self._authorization = authorization or AuthorizationService(session)
        self._transaction = transaction_factory
        self._invoices = invoice_repository
        self._corrections = correction_repository
        self._customers = customer_repository

    # ------------------------------------------------------------ plumbing
    def _actor(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if CORRECT_PERMISSION not in actor.permissions:
            raise PermissionError("تصحيح العمليات النهائية للمدير فقط.")
        return actor

    def can_correct(self):
        actor = self._session.current_actor()
        return bool(actor and CORRECT_PERMISSION in actor.permissions)

    def _read(self, work):
        try:
            with self._transaction() as connection:
                raise _Discard(work(connection))
        except _Discard as done:
            return done.args[0]

    @staticmethod
    def _source_type(value):
        if value not in SOURCES:
            raise CorrectionError("SOURCE_INVALID", "نوع العملية غير صحيح.")
        return value

    @staticmethod
    def _source_right(actor, source_type):
        """A source may need a right ON TOP of TRANSACTION_CORRECT (never instead of it): a supplier making payment is
        reversed by the OWNER only. The transaction re-checks it against the database."""
        needed = SOURCE_RIGHTS.get(source_type)
        if needed and needed not in actor.permissions:
            raise PermissionError("عكس هذه العملية للمالك فقط.")

    # ------------------------------------------------------------ the source and what it posted
    def _load(self, connection, source_type, source_id, lock):
        """(invoice row, source row or None, reference) — the invoice row is locked first when writing."""
        invoices, corrections = self._invoices(connection), self._corrections(connection)
        if not str(source_id or "").isdigit():
            raise CorrectionError("SOURCE_NOT_FOUND", "العملية غير موجودة.")
        if source_type == CS:
            from repositories.customer_account_repository import CustomerAccountRepository, settlement_code
            repo = CustomerAccountRepository(connection)
            source = repo.source(source_id, lock=lock)
            if source is None:
                raise CorrectionError("SOURCE_NOT_FOUND", "تسوية حساب العميل غير موجودة.")
            if lock:
                repo.lock_balance(source["customer_id"])
            # Presentation context only: no synthetic invoice identity or invoice write.
            context = {"invoice_id": None, "invoice_number": settlement_code(source_id),
                       "customer_name_snapshot": source.get("customer_name"),
                       "customer_id": source["customer_id"], "finalized_at": source["occurred_at"],
                       "net_amount": source["amount"]}
            return context, source, settlement_code(source_id)
        if source_type == CD:
            from repositories.customer_account_repository import CustomerAccountRepository
            from repositories.customer_deposit_repository import CustomerDepositRepository, deposit_code
            from repositories.financial_account_repository import FinancialAccountRepository
            source = CustomerDepositRepository(connection).source(source_id, lock=lock)
            if source is None:
                raise CorrectionError("SOURCE_NOT_FOUND", "العربون غير موجود.")
            if lock:                                            # the deposit's own lock order: customer → safe
                CustomerAccountRepository(connection).lock_balance(source["customer_id"])
                FinancialAccountRepository(connection).account("CASH", source["cash_account_id"], lock=True)
            reference = deposit_code(source["direction"], source_id)
            context = {"invoice_id": None, "invoice_number": reference,
                       "source_direction": source["direction"],
                       "customer_name_snapshot": source.get("customer_name"), "customer_id": source["customer_id"],
                       "finalized_at": source["occurred_at"], "net_amount": source["amount"]}
            return context, source, reference
        if source_type == MTE:
            from repositories.financial_account_repository import FinancialAccountRepository
            from repositories.market_trip_repository import MarketTripRepository, entry_code
            repo = MarketTripRepository(connection)
            source = repo.entry_source(source_id)
            if source is None:
                raise CorrectionError("SOURCE_NOT_FOUND", "حركة العهدة غير موجودة.")
            if lock:                                            # the entry's own lock order: trip → MAIN_SAFE → Scrap
                repo.trip(source["trip_id"], lock=True)
                lot_link = repo.entry_lot_settlement(source_id)
                lot_ids = [r["lot_id"] for r in repo.entry_lot_returns(source_id)] + [r["lot_id"] for r in repo.entry_lot_issues(source_id)]
                if source["entry_kind"] == "MELT":
                    lot_ids += repo.melt_lot_ids(source_id)        # protect origin melt vs downstream settlement/transfer
                if lot_link is not None:
                    lot_ids.append(lot_link["lot_id"])
                from repositories.intermediary_holding_repository import IntermediaryHoldingRepository
                holdings = IntermediaryHoldingRepository(connection)
                hold_link = holdings.link_by_entry(source_id)                  # 048: trip -> lot -> party -> holding source pool
                if hold_link is not None:
                    lot_ids.append(hold_link["lot_id"])
                repo.lock_lots(lot_ids)                         # trip -> lots ascending -> supplier; no origin-trip lock
                if hold_link is not None:
                    from repositories.supplier_gold_settlement_repository import SupplierGoldSettlementRepository as _Parties
                    _Parties(connection).get_supplier(hold_link["party_id"], lock=True)
                    holdings.lock_pool(hold_link["pool_id"])
                if lot_link is not None:
                    from repositories.supplier_gold_settlement_repository import SupplierGoldSettlementRepository as _LotSuppliers
                    _LotSuppliers(connection).get_supplier(lot_link["supplier_id"], lock=True)
                payment_link = repo.entry_supplier_payment(source_id)         # trip -> supplier (the payment's own order)
                if payment_link is not None:
                    from repositories.supplier_payment_repository import SupplierPaymentRepository as _Suppliers
                    _Suppliers(connection).get_supplier(payment_link["supplier_id"], lock=True)
                gold_link = repo.entry_gold_settlement(source_id)             # trip -> supplier (the gold settlement's own order, 046)
                if gold_link is not None:
                    from repositories.supplier_gold_settlement_repository import SupplierGoldSettlementRepository as _GoldSuppliers
                    _GoldSuppliers(connection).get_supplier(gold_link["supplier_id"], lock=True)
                leg = repo.entry_cash_leg(source_id)
                if leg is not None:
                    FinancialAccountRepository(connection).account("CASH", leg["cash_account_id"], lock=True)
                source = repo.entry_source(source_id, lock=True)
            reference = entry_code(source["trip_id"], source["entry_no"])
            leg = repo.entry_cash_leg(source_id)
            context = {"invoice_id": None, "invoice_number": reference, "customer_name_snapshot": None, "customer_id": None,
                       "finalized_at": source["occurred_at"], "net_amount": None if leg is None else leg["amount"],
                       "trip_id": str(source["trip_id"]), "source_direction": source["entry_kind"]}
            return context, source, reference
        if source_type == SGS:
            from repositories.supplier_gold_settlement_repository import SupplierGoldSettlementRepository, settlement_code
            repo = SupplierGoldSettlementRepository(connection)
            source = repo.source(source_id)
            if source is None:
                raise CorrectionError("SOURCE_NOT_FOUND", "تسوية ذهب المورد غير موجودة.")
            if lock:                                            # the settlement's own lock order: supplier -> Scrap karat
                repo.get_supplier(source["supplier_id"], lock=True)
                if source.get("source_kind", "SHOP_SCRAP") == "SHOP_SCRAP":       # a custody-sourced settlement never touched Scrap
                    corrections.lock_old_gold_balance(source["delivered_karat_id"])
                source = repo.source(source_id, lock=True)
            reference = settlement_code(source_id)
            context = {"invoice_id": None, "invoice_number": reference, "customer_name_snapshot": None, "customer_id": None,
                       "finalized_at": source["occurred_at"], "net_amount": source["credited_weight_grams"],
                       "supplier_id": str(source["supplier_id"]), "supplier_name": source.get("supplier_name")}
            return context, source, reference
        if source_type == SMP:
            from repositories.financial_account_repository import FinancialAccountRepository
            from repositories.supplier_payment_repository import SupplierPaymentRepository, payment_code
            repo = SupplierPaymentRepository(connection)
            source = repo.source(source_id)
            if source is None:
                raise CorrectionError("SOURCE_NOT_FOUND", "دفعة المصنعيات غير موجودة.")
            if lock:                                            # the payment's own lock order: supplier → account
                repo.get_supplier(source["supplier_id"], lock=True)
                accounts = FinancialAccountRepository(connection)
                if source["payment_method"] == "CASH":
                    accounts.account("CASH", source["cash_account_id"], lock=True)
                elif source["payment_method"] == "BANK":
                    accounts.account("BANK", source["bank_account_id"], lock=True)
                source = repo.source(source_id, lock=True)
            reference = payment_code(source_id)
            context = {"invoice_id": None, "invoice_number": reference, "customer_name_snapshot": None,
                       "customer_id": None, "finalized_at": source["occurred_at"], "net_amount": source["amount"],
                       "supplier_id": str(source["supplier_id"]), "supplier_name": source.get("supplier_name")}
            return context, source, reference
        if source_type == FO:
            from repositories.financial_account_repository import FinancialAccountRepository, operation_code
            repo = FinancialAccountRepository(connection)
            source = repo.operation(source_id, lock=lock)
            if source is None:
                raise CorrectionError("SOURCE_NOT_FOUND", "العملية المالية غير موجودة.")
            if lock:                                            # same account lock order as the operation itself
                sides = {("CASH", source[f"{end}_cash_account_id"]) for end in ("from", "to")} | \
                        {("BANK", source[f"{end}_bank_account_id"]) for end in ("from", "to")}
                for ledger, account_id in sorted((l, int(a)) for l, a in sides if a is not None):
                    repo.account(ledger, account_id, lock=True)
            reference = operation_code(source["operation_type"], source_id)
            context = {"invoice_id": None, "invoice_number": reference, "customer_name_snapshot": None,
                       "customer_id": None, "finalized_at": source["occurred_at"], "net_amount": source["amount"]}
            return context, source, reference
        if source_type == "INVOICE":
            invoice_id, source, reference = str(source_id), None, None
        elif source_type == "EXCHANGE":
            source = corrections.exchange_source(source_id)
            if source is None:
                raise CorrectionError("SOURCE_NOT_FOUND", "الاستبدال غير موجود.")
            invoice_id, reference = source["source_invoice_id"], exchange_code(source_id)
        else:
            source = corrections.return_source(source_id)
            if source is None:
                raise CorrectionError("SOURCE_NOT_FOUND", "المرتجع غير موجود.")
            if source["return_status"] != "FINALIZED":
                raise CorrectionError("SOURCE_NOT_FINALIZED",
                                      "المرتجع لم يُنفذ بعد؛ يُلغى من شاشة المرتجع ولا يحتاج تصحيحاً.")
            invoice_id, reference = source["source_invoice_id"], return_code(source_id)
        if lock and invoices.lock_invoice(invoice_id) is None:
            raise CorrectionError("SOURCE_NOT_FOUND", "الفاتورة غير موجودة.")
        invoice = invoices.get_invoice(invoice_id)
        if invoice is None:
            raise CorrectionError("SOURCE_NOT_FOUND", "الفاتورة غير موجودة.")
        if invoice["record_origin"] != "NATIVE" or invoice["invoice_status"] != "FINALIZED":
            raise CorrectionError("SOURCE_NOT_CORRECTABLE", "التصحيح متاح لفواتير النظام الجديدة النهائية فقط.")
        if lock and source_type != "INVOICE":                   # re-read the source under the invoice lock
            source = corrections.exchange_source(source_id) if source_type == "EXCHANGE" else corrections.return_source(source_id)
        return invoice, source, reference or invoice["invoice_number"]

    def _history(self, corrections, source_type, source_id):
        rows = corrections.corrections_for_source(source_type, source_id)
        reversal = next((r for r in rows if r["correction_type"] == FULL_REVERSAL), None)
        chain = [r for r in rows if r["correction_type"] == SETTLEMENT]
        return reversal, chain

    # ------------------------------------------------------------ FULL REVERSAL: the plan
    def _plan(self, connection, source_type, source_id, lock):
        corrections, invoices = self._corrections(connection), self._invoices(connection)
        invoice, source, reference = self._load(connection, source_type, source_id, lock)
        reversal, chain = self._history(corrections, source_type, source_id)
        plan = _Plan(source_type, str(source_id), reference, invoice,
                     (source or invoice).get("customer_id"))
        if reversal is not None:
            plan.blockers.append(("ALREADY_REVERSED",
                                  f"تم عكس هذه العملية من قبل ({correction_code(FULL_REVERSAL, reversal['correction_id'])})."))
            return plan
        if source_type not in ("INVOICE",) + STANDALONE and reversal_of(invoice):
            plan.blockers.append(("ALREADY_REVERSED", f"تم عكس الفاتورة الأصلية ({reversal_of(invoice)})."))
            return plan
        # Money and customer account: the exact opposite of what was posted (source + its settlement chain).
        chain_ids = [c["correction_id"] for c in chain]
        for key, total in corrections.money_by_account(source_type, source_id, chain_ids).items():
            if Decimal(str(total)) != 0:
                plan.money[key] = -Decimal(str(total))
        for customer, total in corrections.account_by_customer(source_type, source_id, chain_ids).items():
            if Decimal(str(total)) != 0:
                plan.account[customer] = -Decimal(str(total))
        if source_type not in STANDALONE:
            getattr(self, f"_plan_{source_type.lower()}")(connection, plan, source, lock)
        if source_type == MTE:
            from repositories.market_trip_repository import ISSUE as TRIP_ISSUE, MarketTripRepository, entry_code
            trips = MarketTripRepository(connection)
            if source["trip_status"] != "OPEN":
                plan.blockers.append(("TRIP_CLOSED", "العهدة مغلقة: أعد فتحها أولاً قبل عكس أي حركة فيها."))
            for leg in trips.entry_gold_legs(source_id):       # the exact stored Scrap movement is negated, never recomputed
                code = "REVERSAL_TRIP_ISSUE_IN" if source["entry_kind"] == TRIP_ISSUE else "REVERSAL_TRIP_RETURN_OUT"
                plan.old_gold.append((int(leg["karat_id"]), int(leg["karat_value"]), -Decimal(str(leg["weight_delta_grams"])), code))
            if source["entry_kind"] == TRIP_ISSUE:
                # Undoing an issue must not leave a later (unreversed) return larger than what remains in custody.
                cash = trips.custody_cash(source["trip_id"], exclude_entry_id=source_id, current=True)
                gold = trips.custody_gold(source["trip_id"], exclude_entry_id=source_id, current=True)
                # Gold delivered to a supplier from this custody (046) depends on the issue of its LOGO karat (pooled per karat, newest first,
                # even when the balance would stay non-negative).
                logo_issued = [int(leg["karat_id"]) for leg in trips.entry_gold_legs(source_id) if leg["designation"] == "LOGO"]
                for dependent in trips.later_gold_settlements(source["trip_id"], source["entry_no"], logo_issued):
                    plan.blockers.append(("DEPENDENT_SETTLEMENT_EXISTS",
                                          "لا يمكن عكس الإخراج لأن تسوية ذهب لمورد من العهدة مسجلة بعده على نفس العيار "
                                          f"({entry_code(dependent['trip_id'], dependent['entry_no'])}) — اعكسها أولاً."))
                # Per (karat, designation): later returns, melts, heats AND supplier settlements consume the issued custody - ONE calculation
                # (services.custody_rules), the same one posting, return checks and closure use.
                if custody_rules.any_negative(cash, gold):
                    plan.blockers.append(("DEPENDENT_RETURN_EXISTS",
                                          "لا يمكن عكس الإخراج لأن رجوعًا أو سبكًا أو حمّية أو تسوية ذهب مسجلة بعده يعتمد عليه — اعكسها أولاً."))
                # Logo is pooled per karat: a later effective heating of an issued LOGO karat depends on this issue (newest first), even
                # when the balance would stay non-negative.
                logo_karats = [int(leg["karat_id"]) for leg in trips.entry_gold_legs(source_id) if leg["designation"] == "LOGO"]
                for dependent in trips.later_heat_entries(source["trip_id"], source["entry_no"], logo_karats):
                    plan.blockers.append(("DEPENDENT_HEAT_EXISTS",
                                          "لا يمكن عكس الإخراج لأن حمّية لوجو مسجلة بعده على نفس العيار "
                                          f"({entry_code(dependent['trip_id'], dependent['entry_no'])}) — اعكسها أولاً."))
            if source["entry_kind"] == "LOT_RETURN":
                # The exact stored grams leave the shop lot stock again; the custody grams return because this entry stops counting.
                for line in trips.entry_lot_returns(source_id):
                    grams = Decimal(str(line["weight_grams"]))
                    plan.lot_returns.append((int(line["lot_id"]), grams))
                    held = Decimal(str(trips.shop_lot_balance(line["lot_id"])))
                    if held < grams:
                        plan.blockers.append(("SHOP_LOT_STOCK_INSUFFICIENT",
                                              f"لا يمكن عكس رجوع الشحنة {int(line['lot_no'])}: الرصيد المتاح بالمحل {held:,.3f} جرام "
                                              f"والمطلوب {grams:,.3f} — اعكس ما استُخدم منها أولاً."))
            if source["entry_kind"] == "LOT_ISSUE":
                # The bar goes back into the shop stock (the exact stored grams); the custody grams disappear because this entry stops
                # counting. If a later effective return already took the bar out of this custody, undoing the issue would leave the
                # custody negative AND put the bar in two places: the return has to be reversed first (newest first).
                for line in trips.entry_lot_issues(source_id):
                    plan.lot_issues.append((int(line["lot_id"]), Decimal(str(line["weight_grams"]))))
                if any(Decimal(str(l["outstanding_grams"])) < 0 for l in trips.custody_lots(source["trip_id"], exclude_entry_id=source_id)):
                    plan.blockers.append(("DEPENDENT_LOT_RETURN_EXISTS",
                                          "لا يمكن عكس إخراج السبكة لأن رجوعها أو تسويتها للمورد مسجل بعده — اعكس الحركة التابعة أولًا."))
            if source["entry_kind"] == "SUPPLIER_PAY":
                # The stored making movement is restored (never a recomputed amount) and the entry stops consuming custody cash. Nothing
                # moves in Main Safe or a bank: the money is recorded as actually back in custody (return it with a normal custody return).
                from repositories.supplier_payment_repository import REVERSAL_MOVEMENT, SupplierPaymentRepository
                link = trips.entry_supplier_payment(source_id)
                movement = None if link is None else SupplierPaymentRepository(connection).payment_making_movement(link["payment_id"])
                if movement is None:
                    plan.blockers.append(("PAYMENT_MOVEMENT_MISSING", "حركة المصنعيات الأصلية للدفعة غير موجودة."))
                else:
                    plan.making.append((str(movement["supplier_id"]), -Decimal(str(movement["amount_delta"])), REVERSAL_MOVEMENT))
                plan.extra_permission = SMP_REVERSE_PERMISSION
            if source["entry_kind"] in ("GOLD_SETTLE", "LOT_SETTLE"):
                # The stored supplier gold movement is restored (never a recomputed amount) and the entry stops consuming custody gold, so the
                # stored delivered grams are back in custody. No Scrap, cash, making, bank or lot movement is posted. The supplier-side
                # reversal of this document is refused: the custody entry is the only reversal path.
                from repositories.supplier_gold_settlement_repository import GOLD_REVERSAL, SupplierGoldSettlementRepository
                link = trips.entry_lot_settlement(source_id) if source["entry_kind"] == "LOT_SETTLE" else trips.entry_gold_settlement(source_id)
                movement = None if link is None else SupplierGoldSettlementRepository(connection).gold_movement(link["settlement_id"])
                if movement is None:
                    plan.blockers.append(("SETTLEMENT_MOVEMENT_MISSING", "حركة ذهب المورد الأصلية للتسوية غير موجودة."))
                else:
                    plan.supplier_gold.append((int(movement["supplier_id"]), int(movement["karat_id"]), -Decimal(str(movement["weight_delta_grams"])),
                                               -Decimal(str(movement["equivalent_21_delta_grams"])), GOLD_REVERSAL))
                plan.extra_permission = SGS_REVERSE_PERMISSION
            if source["entry_kind"] in ("LOT_HOLD", "HOLD_RETURN"):
                # 048. The CUSTODY side follows the established convention (this entry stops counting, so the lot is back in / leaves the custody
                # exactly as before it); the HOLDING side posts an explicit signed compensation and the original movement stays counted.
                from repositories.intermediary_holding_repository import (IntermediaryHoldingRepository, MOVE_HANDOVER_REVERSAL,
                                                                          MOVE_RETURN_REVERSAL)
                holdings = IntermediaryHoldingRepository(connection)
                link = holdings.link_by_entry(source_id)
                if link is None:
                    plan.blockers.append(("HOLDING_LINK_MISSING", "ربط حركة المسوقجي الأصلية غير موجود."))
                else:
                    plan.extra_permission = HOLDING_REVERSE_PERMISSION
                    grams = Decimal(str(link["weight_grams"]))
                    balance = Decimal(str(holdings.pool_balance(link["pool_id"])))                 # CURRENT, compensations included
                    others = trips.custody_lots(source["trip_id"], exclude_entry_id=source_id, lot_id=link["lot_id"])
                    in_custody = sum((Decimal(str(l["outstanding_grams"])) for l in others), Decimal("0.000"))
                    held_total = Decimal(str(holdings.lot_held_total(link["lot_id"])))
                    if source["entry_kind"] == "LOT_HOLD":
                        if balance != grams:
                            plan.blockers.append(("DEPENDENT_HOLDING_RETURN_EXISTS",
                                                  "لا يمكن عكس التسليم لأن رجوعًا لهذه السبكة من المسوقجي مسجل بعده — اعكس الرجوع أولًا."))
                        elif in_custody != grams:
                            plan.blockers.append(("HOLDING_CUSTODY_CHANGED", "حالة السبكة في العهدة تغيّرت بعد التسليم؛ راجع الحركات اللاحقة أولًا."))
                        plan.holdings.append((int(link["pool_id"]), int(link["document_id"]), link["doc_kind"], MOVE_HANDOVER_REVERSAL, grams, -grams))
                    else:
                        if in_custody != 0:
                            plan.blockers.append(("DEPENDENT_LOT_USE_EXISTS",
                                                  "لا يمكن عكس الرجوع لأن السبكة استُخدمت أو سُلّمت بعده — اعكس الحركة اللاحقة أولًا."))
                        elif balance != 0 or held_total != 0:
                            plan.blockers.append(("HOLDING_CUSTODY_CHANGED", "رصيد المسوقجي لم يعد صفرًا بعد الرجوع؛ راجع الحركات اللاحقة أولًا."))
                        plan.holdings.append((int(link["pool_id"]), int(link["document_id"]), link["doc_kind"], MOVE_RETURN_REVERSAL, grams, grams))
            if source["entry_kind"] == "HEAT":
                # Nothing is posted outside the custody ledger (no cash, Scrap, supplier or lot movement): only the dependencies are
                # checked, newest first. The heat row and its measurements stay as history.
                karats = [int(l["karat_id"]) for l in trips.entry_heat_lines(source_id)]
                for dependent in trips.later_heat_entries(source["trip_id"], source["entry_no"], karats):
                    plan.blockers.append(("DEPENDENT_HEAT_EXISTS",
                                          "لا يمكن عكس الحمّية لأن حمّية لوجو أحدث على نفس العيار مسجلة بعدها "
                                          f"({entry_code(dependent['trip_id'], dependent['entry_no'])}) — اعكسها أولاً."))
                for dependent in trips.later_logo_returns(source["trip_id"], source["entry_no"], karats):
                    plan.blockers.append(("DEPENDENT_LOGO_RETURN_EXISTS",
                                          "لا يمكن عكس الحمّية لأن رجوع لوجو من نفس العيار مسجل بعدها "
                                          f"({entry_code(dependent['trip_id'], dependent['entry_no'])}) — اعكس الرجوع أولاً."))
                for dependent in trips.later_gold_settlements(source["trip_id"], source["entry_no"], karats):
                    plan.blockers.append(("DEPENDENT_SETTLEMENT_EXISTS",
                                          "لا يمكن عكس الحمّية لأن تسوية ذهب لمورد من لوجو نفس العيار مسجلة بعدها "
                                          f"({entry_code(dependent['trip_id'], dependent['entry_no'])}) — اعكسها أولاً."))
                if custody_rules.any_negative(trips.custody_cash(source["trip_id"], exclude_entry_id=source_id, current=True),
                                              trips.custody_gold(source["trip_id"], exclude_entry_id=source_id, current=True),
                                              designation="LOGO"):
                    plan.blockers.append(("DEPENDENT_RETURN_EXISTS",
                                          "لا يمكن عكس الحمّية لأن رصيد اللوجو بعد عكسها يصبح سالبًا — اعكس ما يعتمد عليها أولاً."))
            if source["entry_kind"] == "MELT":
                # A melt can be undone only while its lots have no unreversed downstream use (their own CREATE is not one).
                for dependent in trips.lot_downstream(source_id):
                    plan.blockers.append(("DEPENDENT_LOT_USE_EXISTS",
                                          "لا يمكن عكس السبك لأن شحنة ناتجة عنه نُقلت أو سُوّيت للمورد في الحركة "
                                          f"{entry_code(dependent['trip_id'], dependent['entry_no'])} — اعكسها أولاً."))
        if source_type == SGS:
            # The STORED movements are what is undone (never a recomputed amount): the delivered grams return to Scrap and the credit is restored.
            from repositories.supplier_gold_settlement_repository import GOLD_REVERSAL, SCRAP_REVERSAL_IN, SupplierGoldSettlementRepository
            settlements = SupplierGoldSettlementRepository(connection)
            custody = None if source.get("source_kind", "SHOP_SCRAP") == "SHOP_SCRAP" else settlements.custody_entry(source_id)
            scrap_leg, gold_leg = settlements.scrap_movement(source_id), settlements.gold_movement(source_id)
            if source.get("source_kind", "SHOP_SCRAP") != "SHOP_SCRAP":
                # Delivered from market custody (046): the custody entry is the only reversal path (it also restores the custody gold).
                from repositories.market_trip_repository import entry_code as _entry_code
                plan.blockers.append(("CUSTODY_SETTLEMENT_REVERSE_FROM_TRIP",
                                      "هذه التسوية سُلِّم ذهبها من عهدة السوق"
                                      + ("" if custody is None else f" ({_entry_code(custody['trip_id'], custody['entry_no'])})")
                                      + ": اعكسها من حركة العهدة نفسها (تسوية ذهب من العهدة) من صفحة عهدة السوق."))
            elif scrap_leg is None or gold_leg is None:
                plan.blockers.append(("SETTLEMENT_MOVEMENT_MISSING", "حركات التسوية الأصلية غير موجودة."))
            else:
                plan.old_gold.append((int(scrap_leg["karat_id"]), int(source["delivered_karat_value"]),
                                      -Decimal(str(scrap_leg["weight_delta_grams"])), SCRAP_REVERSAL_IN))
                plan.supplier_gold.append((int(gold_leg["supplier_id"]), int(gold_leg["karat_id"]), -Decimal(str(gold_leg["weight_delta_grams"])),
                                           -Decimal(str(gold_leg["equivalent_21_delta_grams"])), GOLD_REVERSAL))
        if source_type == SMP and source["payment_method"] == "CUSTODY":
            # Paid with market-custody cash: the custody entry is the only reversal path (it also restores the custody cash).
            from repositories.market_trip_repository import MarketTripRepository, entry_code
            entry = MarketTripRepository(connection).payment_entry(source_id)
            plan.blockers.append(("CUSTODY_PAYMENT_REVERSE_FROM_TRIP",
                                  "هذه الدفعة سُدِّدت من عهدة السوق"
                                  + ("" if entry is None else f" ({entry_code(entry['trip_id'], entry['entry_no'])})")
                                  + ": اعكسها من حركة العهدة نفسها (دفع مصنعيات من العهدة) من صفحة عهدة السوق."))
        elif source_type == SMP:
            # The exact stored making movement is restored (never a recomputed amount): the payment's negative delta, negated.
            from repositories.supplier_payment_repository import REVERSAL_MOVEMENT, SupplierPaymentRepository
            movement = SupplierPaymentRepository(connection).payment_making_movement(source_id)
            if movement is None:
                plan.blockers.append(("PAYMENT_MOVEMENT_MISSING", "حركة المصنعيات الأصلية للدفعة غير موجودة."))
            else:
                plan.making.append((str(movement["supplier_id"]), -Decimal(str(movement["amount_delta"])), REVERSAL_MOVEMENT))
        if source_type == CD and source["direction"] == "IN":
            # Reversing a deposit must not leave refunds larger than the remaining deposits.
            from repositories.customer_deposit_repository import CustomerDepositRepository
            outstanding = Decimal(str(CustomerDepositRepository(connection).outstanding(source["customer_id"])))
            if outstanding - Decimal(str(source["amount"])) < 0:
                plan.blockers.append(("DEPOSIT_ALREADY_REFUNDED",
                                      "لا يمكن عكس العربون لأنه تم استرداده — اعكس الاسترداد أولاً."))
        # Counted old-gold stock must stay physically coherent: removals only while the grams are there.
        removals = {}
        for karat_id, karat, grams, _ in plan.old_gold:
            if grams < 0:
                removals[karat_id] = (karat, removals.get(karat_id, (karat, ZERO_GRAMS))[1] - grams)
        for karat_id, (karat, grams) in sorted(removals.items()):
            available = corrections.lock_old_gold_balance(karat_id)
            if grams > available:
                held = held_grams(corrections, karat_id)
                plan.blockers.append(("OLD_GOLD_DEPENDENCY_EXISTS",
                                      f"لا يمكن عكس حركة الكسر لأن الوزن تم استهلاكه لاحقاً: عيار {karat} المتاح "
                                      f"{available:,.3f} والمطلوب {grams:,.3f} جرام."
                                      + (f" يوجد {held:,.3f} جرام محجوز لمراجعة المدير ولا يمكن استخدامه حتى اعتماد القرار."
                                         if held > 0 else "")))
        return plan

    def _plan_invoice(self, connection, plan, _source, lock):
        corrections = self._corrections(connection)
        invoice = plan.invoice
        exchanges, returns = corrections.lifecycle_of_invoice(invoice["invoice_id"])
        for x in exchanges:
            if int(x["active_lines"]):
                plan.blockers.append(("DOWNSTREAM_DEPENDENCY_EXISTS",
                                      f"لا يمكن عكس الفاتورة لأن القطعة دخلت الاستبدال {exchange_code(x['lifecycle_id'])}"
                                      " — اعكس الاستبدال أولاً."))
        for r in returns:
            if int(r["active_lines"]):
                plan.blockers.append(("DOWNSTREAM_DEPENDENCY_EXISTS",
                                      f"لا يمكن عكس الفاتورة لأن لها مرتجعاً {return_code(r['return_id'])}"
                                      " — اعكس المرتجع أولاً."))
        items = corrections.lock_items([l["inventory_item_id"] for l in invoice["lines"]
                                        if l["line_type"] == "SELL_INVENTORY" and l.get("inventory_item_id")])
        for line in invoice["lines"]:
            weight = Decimal(str(line["weight_grams"]))
            if line["line_type"] == "SELL_INVENTORY" and line.get("inventory_item_id"):
                item = items.get(str(line["inventory_item_id"]))
                if item is None or item["inventory_status"] != "SOLD":
                    plan.blockers.append(("DOWNSTREAM_DEPENDENCY_EXISTS",
                                          f"لا يمكن عكس الفاتورة: حالة القطعة {line.get('barcode_snapshot') or ''} "
                                          "تغيرت بعد البيع."))
                else:
                    plan.inventory.append((str(line["inventory_item_id"]), "SOLD", "IN_STOCK", "REVERSAL_SALE_RESTOCK"))
            elif line["line_type"] == "SELL_OLD_GOLD":
                plan.old_gold.append((int(line["karat_id"]), line["karat_value"], weight, "REVERSAL_OLD_GOLD_SALE_IN"))
            elif line["line_type"] == "BUY_OLD_GOLD":
                plan.old_gold.append((int(line["karat_id"]), line["karat_value"], -weight, "REVERSAL_OLD_GOLD_PURCHASE_OUT"))
            elif line["line_type"] == "SELL_UNIDENTIFIED":
                case = corrections.case_for(invoice_line_id=line["line_id"])
                if case is not None and case["case_status"] == "OPEN":
                    plan.cases.append(case["case_id"])
                elif case is not None and case["case_status"] != "CANCELLED":
                    plan.blockers.append(("DOWNSTREAM_DEPENDENCY_EXISTS",
                                          "لا يمكن عكس الفاتورة: حالة البيع اليدوي حُسمت في مراجعة المخزون."))

    def _plan_exchange(self, connection, plan, source, lock):
        corrections = self._corrections(connection)
        ids = [l["inventory_item_id"] for l in source["lines"] if l.get("inventory_item_id")]
        items = corrections.lock_items(ids)
        for line in source["lines"]:
            weight = Decimal(str(line["weight_grams"]))
            item = items.get(str(line.get("inventory_item_id")))
            if line["line_role"] == "RETURNED":
                review = corrections.inspection_for(lifecycle_line_id=line["line_id"])
                corrections.lock_old_gold_balance(int(line["karat_id"]))      # ledger before the hold
                hold = corrections.hold_for(lifecycle_line_id=line["line_id"])
                if hold is not None:
                    self._plan_held_piece(plan, line, item, review, hold)
                    continue
                if item is None or item["inventory_status"] != "RETURNED_PENDING" or (review and review["review_status"] != "OPEN"):
                    plan.blockers.append(("RETURNED_ITEM_ALREADY_DISPOSED",
                                          "لا يمكن عكس الاستبدال لأن المدير حسم القطعة المرتجعة"
                                          + (f" ({review['resolution_code']})" if review and review.get("resolution_code") else "")
                                          + " — راجع فحص القطع المرتجعة."))
                    continue
                plan.inventory.append((str(line["inventory_item_id"]), "RETURNED_PENDING", "SOLD",
                                       "REVERSAL_EXCHANGE_RETURN_TO_SOLD"))
                if review:
                    plan.reviews.append(review["review_id"])
            elif line.get("identification_method") in ("SCAN", "SEARCH"):
                if item is None or item["inventory_status"] != "SOLD":
                    plan.blockers.append(("DOWNSTREAM_DEPENDENCY_EXISTS",
                                          "لا يمكن عكس الاستبدال: حالة القطعة البديلة تغيرت بعده."))
                else:
                    plan.inventory.append((str(line["inventory_item_id"]), "SOLD", "IN_STOCK",
                                           "REVERSAL_EXCHANGE_REPLACEMENT_RESTOCK"))
            elif line.get("identification_method") == "OLD_GOLD_STOCK":
                plan.old_gold.append((int(line["karat_id"]), line["karat_value"], weight, "REVERSAL_EXCHANGE_OLD_GOLD_IN"))
            elif line.get("identification_method") == "MANUAL":
                case = corrections.case_for(lifecycle_line_id=line["line_id"])
                if case is not None and case["case_status"] == "OPEN":
                    plan.cases.append(case["case_id"])
                elif case is not None and case["case_status"] != "CANCELLED":
                    plan.blockers.append(("DOWNSTREAM_DEPENDENCY_EXISTS",
                                          "لا يمكن عكس الاستبدال: حالة البديل اليدوي حُسمت في مراجعة المخزون."))
        plan.lines = ("invoice_lifecycle_lines", [l["line_id"] for l in source["lines"]])

    @staticmethod
    def _plan_held_piece(plan, line, item, review, hold, source="EXCHANGE"):
        """An Exchange piece that entered Scrap at the exchange (032). Only a HELD piece can be reversed:
        its reserved weight is guaranteed to still be in Scrap. A COMMITTED piece became ordinary Scrap
        that may already be consumed; a RETURNED_TO_STOCK piece is sellable stock again."""
        status = hold["hold_status"]
        what = "الاستبدال" if source == "EXCHANGE" else "المرتجع"
        if status == "COMMITTED":
            plan.blockers.append(("RETURNED_ITEM_COMMITTED_TO_SCRAP",
                                  f"لا يمكن عكس {what} لأن القطعة المرتجعة ثُبّتت في الكسر وأصبح وزنها كسراً عادياً"
                                  " قد يكون استُهلك."))
            return
        if status == "RETURNED_TO_STOCK":
            plan.blockers.append(("RETURNED_ITEM_RESTOCKED",
                                  f"لا يمكن عكس {what} لأن المدير أعاد القطعة المرتجعة من الكسر إلى المخزون."))
            return
        if (status != "HELD" or item is None or item["inventory_status"] != "CONVERTED_TO_OLD_GOLD"
                or review is None or review["review_status"] != "OPEN"):
            plan.blockers.append(("RETURNED_ITEM_ALREADY_DISPOSED",
                                  f"لا يمكن عكس {what}: حالة القطعة المرتجعة في الكسر تغيرت."))
            return
        plan.inventory.append((str(line["inventory_item_id"]), "CONVERTED_TO_OLD_GOLD", "SOLD",
                               "REVERSAL_EXCHANGE_RETURN_TO_SOLD" if source == "EXCHANGE" else "REVERSAL_RETURN_TO_SOLD"))
        plan.reviews.append(review["review_id"])
        plan.holds.append((hold["hold_id"], int(hold["karat_id"]), line["karat_value"],
                           Decimal(str(hold["weight_grams"])), str(line["inventory_item_id"]),
                           "REVERSAL_EXCHANGE_RETURN_SCRAP_OUT" if source == "EXCHANGE"
                           else "REVERSAL_RETURN_SCRAP_OUT"))

    def _plan_return(self, connection, plan, source, lock):
        corrections = self._corrections(connection)
        items = corrections.lock_items([l["inventory_item_id"] for l in source["lines"] if l.get("inventory_item_id")])
        for line in source["lines"]:
            weight = Decimal(str(line["weight_grams"]))
            if line["line_type"] == "SELL_INVENTORY":
                item = items.get(str(line["inventory_item_id"]))
                review = corrections.inspection_for(return_line_id=line["line_id"])
                corrections.lock_old_gold_balance(int(line["karat_id"]))      # ledger before the hold
                hold = corrections.hold_for(return_line_id=line["line_id"])
                if hold is not None:                                         # a Return piece in Scrap (033)
                    self._plan_held_piece(plan, line, item, review, hold, source="RETURN")
                    continue
                if item is None or item["inventory_status"] != "RETURNED_PENDING" or (review and review["review_status"] != "OPEN"):
                    target = {"RETURN_TO_STOCK": "المخزون", "CONVERT_TO_OLD_GOLD": "الكسر"}.get(
                        (review or {}).get("resolution_code"), "حالة أخرى")
                    plan.blockers.append(("RETURN_ITEM_ALREADY_DISPOSED",
                                          f"لا يمكن عكس المرتجع لأن المدير حسم القطعة إلى {target}."))
                    continue
                plan.inventory.append((str(line["inventory_item_id"]), "RETURNED_PENDING", "SOLD", "REVERSAL_RETURN_TO_SOLD"))
                if review:
                    plan.reviews.append(review["review_id"])
            else:
                plan.old_gold.append((int(line["karat_id"]), line["karat_value"], -weight, "REVERSAL_RETURN_OLD_GOLD_OUT"))
        plan.lines = ("invoice_return_lines", [l["line_id"] for l in source["lines"]])

    @staticmethod
    def _plan_view(plan, accounts):
        names = {("BANK", int(a["id"])): a["name"] for a in accounts}

        def ledger_name(key):
            return "النقدية" if key[0] == "CASH" else names.get(key, f"حساب {key[1]}")
        return {
            "source_type": plan.source_type, "source_id": plan.source_id, "reference": plan.reference,
            "source_text": correction_source_text(plan.source_type, plan.invoice.get("source_direction")),
            "customer_id": str(plan.customer_id or ""),
            "direction": plan.invoice.get("source_direction"),
            "invoice_id": plan.invoice["invoice_id"], "invoice_number": plan.invoice["invoice_number"],
            "customer_name": plan.invoice.get("customer_name_snapshot"),
            "original_date": (plan.invoice.get("finalized_at") or plan.invoice.get("invoice_date")).isoformat(sep=" "),
            "original_total": str(plan.invoice.get("net_amount")),
            "money": [{"account": ledger_name(k), "ledger": k[0], "delta": str(v)} for k, v in sorted(plan.money.items())],
            "account": [{"customer_id": k, "delta": str(v)} for k, v in plan.account.items()],
            "making": [{"supplier_id": s, "delta": str(d)} for s, d, _ in plan.making],
            "supplier_gold": [{"supplier_id": s, "karat_id": k, "delta": str(d)} for s, k, d, _, _ in plan.supplier_gold],
            "holdings": [{"pool_id": p, "document_id": d, "movement": m, "delta": str(delta)} for p, d, _, m, _, delta in plan.holdings],
            "inventory": [{"inventory_item_id": i, "from": f, "to": t} for i, f, t, _ in plan.inventory],
            "old_gold": [{"karat": k, "grams": str(g)} for _, k, g, _ in plan.old_gold]
                        + [{"karat": k, "grams": str(-g)} for _, _, k, g, _, _ in plan.holds],
            "reviews": len(plan.reviews), "cases": len(plan.cases),
            "blockers": [{"code": c, "message": m} for c, m in plan.blockers],
            "can_reverse": not plan.blockers,
        }

    # ------------------------------------------------------------ public: read side
    def preview_reversal(self, source_type, source_id, *, _expected_actor=None):
        """Exactly what a full reversal would do (or what blocks it). Read-only."""
        actor = self._actor(_expected_actor)
        source_type = self._source_type(source_type)
        self._source_right(actor, source_type)

        def read(connection):
            plan = self._plan(connection, source_type, source_id, lock=False)
            return self._plan_view(plan, self._invoices(connection).active_bank_accounts())
        return self._read(read)

    def targets(self, invoice_id, *, _expected_actor=None):
        """The invoice and its Exchanges / finalized Returns that a manager may correct, with their state."""
        self._actor(_expected_actor)

        def read(connection):
            corrections, invoices = self._corrections(connection), self._invoices(connection)
            invoice = invoices.get_invoice(invoice_id)
            if invoice is None:
                raise CorrectionError("SOURCE_NOT_FOUND", "الفاتورة غير موجودة.")
            exchanges, returns = corrections.lifecycle_of_invoice(invoice_id)
            rows = [("INVOICE", invoice["invoice_id"], invoice["invoice_number"])]
            rows += [("EXCHANGE", x["lifecycle_id"], exchange_code(x["lifecycle_id"])) for x in exchanges]
            rows += [("RETURN", r["return_id"], return_code(r["return_id"])) for r in returns]
            out = []
            for source_type, source_id, reference in rows:
                reversal, chain = self._history(corrections, source_type, source_id)
                out.append({"source_type": source_type, "source_id": str(source_id), "reference": reference,
                            "source_text": SOURCE_TEXT[source_type],
                            "reversed_by": None if reversal is None else correction_code(FULL_REVERSAL, reversal["correction_id"]),
                            "settlement_corrections": len(chain)})
            return {"invoice_id": invoice["invoice_id"], "invoice_number": invoice["invoice_number"],
                    "customer_id": str(invoice.get("customer_id") or ""),
                    "customer_name": invoice.get("customer_name_snapshot"), "targets": out,
                    "bank_accounts": [{"id": a["id"], "name": a["name"], "account_type": a.get("account_type") or "BANK"}
                                      for a in invoices.active_bank_accounts()]}
        return self._read(read)

    def settlement_target(self, settlement_id, *, _expected_actor=None):
        self._actor(_expected_actor)
        def read(connection):
            context, source, reference = self._load(connection, CS, settlement_id, False)
            reversal, chain = self._history(self._corrections(connection), CS, settlement_id)
            return {"invoice_id": "", "invoice_number": reference, "customer_name": context.get("customer_name_snapshot"),
                    "customer_id": str(context.get("customer_id") or ""),
                    "targets": [{"source_type": CS, "source_id": str(settlement_id),
                        "reference": reference, "source_text": SOURCE_TEXT[CS],
                        "reversed_by": None if reversal is None else correction_code(FULL_REVERSAL,reversal['correction_id']),
                        "settlement_corrections": len(chain)}],
                    "bank_accounts": self._invoices(connection).active_bank_accounts()}
        return self._read(read)

    def financial_operation_target(self, operation_id, *, _expected_actor=None):
        """A manual FO / TR operation as the single target: full reversal only (no settlement correction)."""
        self._actor(_expected_actor)

        def read(connection):
            _, _source, reference = self._load(connection, FO, operation_id, False)
            reversal, _ = self._history(self._corrections(connection), FO, operation_id)
            return {"invoice_id": "", "invoice_number": reference, "customer_name": None,
                    "targets": [{"source_type": FO, "source_id": str(operation_id), "reference": reference,
                                 "source_text": SOURCE_TEXT[FO], "settlement_allowed": False,
                                 "reversed_by": None if reversal is None else correction_code(
                                     FULL_REVERSAL, reversal["correction_id"]),
                                 "settlement_corrections": 0}],
                    "bank_accounts": self._invoices(connection).active_bank_accounts()}
        return self._read(read)

    def deposit_target(self, deposit_id, *, _expected_actor=None):
        """An عربون / استرداد عربون as the single target: full reversal only (no settlement correction)."""
        self._actor(_expected_actor)

        def read(connection):
            context, _source, reference = self._load(connection, CD, deposit_id, False)
            reversal, _ = self._history(self._corrections(connection), CD, deposit_id)
            return {"invoice_id": "", "invoice_number": reference, "customer_name": context.get("customer_name_snapshot"),
                    "customer_id": str(context.get("customer_id") or ""),
                    "targets": [{"source_type": CD, "source_id": str(deposit_id), "reference": reference,
                                 "source_text": correction_source_text(CD, _source["direction"]),
                                 "direction": _source["direction"], "settlement_allowed": False,
                                 "reversed_by": None if reversal is None else correction_code(
                                     FULL_REVERSAL, reversal["correction_id"]),
                                 "settlement_corrections": 0}],
                    "bank_accounts": self._invoices(connection).active_bank_accounts()}
        return self._read(read)

    # ------------------------------------------------------------ public: FULL REVERSAL
    def reverse(self, source_type, source_id, reason, *, _expected_actor=None):
        session_actor = self._actor(_expected_actor)
        source_type, reason = self._source_type(source_type), _reason(reason)
        self._source_right(session_actor, source_type)
        with self._transaction() as connection:
            actor = self._authorization.require_permission(connection, session_actor, CORRECT_PERMISSION)
            if source_type in SOURCE_RIGHTS:
                actor = self._authorization.require_permission(connection, session_actor, SOURCE_RIGHTS[source_type])
            corrections, invoices = self._corrections(connection), self._invoices(connection)
            customers = self._customers(connection)
            plan = self._plan(connection, source_type, source_id, lock=True)
            if plan.extra_permission:                   # e.g. SUPPLIER_MAKING_PAY_REVERSE for a custody supplier payment
                actor = self._authorization.require_permission(connection, session_actor, plan.extra_permission)
            if plan.blockers:
                code, message = plan.blockers[0]
                raise CorrectionError(code, message)
            now = corrections.db_now()
            effects = self._plan_view(plan, invoices.active_bank_accounts())
            correction_id = corrections.insert_correction(
                correction_type=FULL_REVERSAL, source_type=source_type, source_id=source_id,
                source_reference=plan.reference, invoice_id=plan.invoice["invoice_id"], chain_sequence=None,
                previous_correction_id=None, money_direction=None, settled_total=None, reason=reason, before=None,
                after=None, effects=effects, performed_by_user_id=actor.user_id,
                performed_by_employee_id=actor.employee_id, occurred_at=now)
            code = correction_code(FULL_REVERSAL, correction_id)
            text = f"عكس {code} — {SOURCE_TEXT[source_type]} {plan.reference}"
            self._post_money(corrections, correction_id, code, plan.money, "REVERSAL_PAYMENT", text, now, actor)
            if plan.making:
                from repositories.supplier_payment_repository import CORRECTION_REFERENCE, SupplierPaymentRepository
                supplier_ledger = SupplierPaymentRepository(connection)
                for supplier_id, delta, movement_code in plan.making:
                    type_id = supplier_ledger.making_type_id(movement_code)
                    if type_id is None:
                        raise RuntimeError(f"supplier making movement type {movement_code} is missing (migration 036)")
                    supplier_ledger.insert_making_movement(
                        supplier_id=supplier_id, type_id=type_id, amount_delta=delta, source_type=CORRECTION_REFERENCE,
                        source_id=correction_id, reference=code, description=text, now=now, user_id=actor.user_id)
            if plan.supplier_gold:
                from repositories.supplier_gold_settlement_repository import CORRECTION_REFERENCE as GOLD_CORRECTION, SupplierGoldSettlementRepository
                gold_ledger = SupplierGoldSettlementRepository(connection)
                for supplier_id, karat_id, delta, eq21, movement_code in plan.supplier_gold:
                    type_id = gold_ledger.gold_type_id(movement_code)
                    if type_id is None:
                        raise RuntimeError(f"supplier gold movement type {movement_code} is missing (migration 045)")
                    gold_ledger.insert_gold_movement(supplier_id=supplier_id, type_id=type_id, karat_id=karat_id, weight_delta=delta,
                                                     eq21_delta=eq21, source_type=GOLD_CORRECTION, source_id=correction_id, reference=code,
                                                     description=text, now=now, user_id=actor.user_id)
            for customer_id, delta in plan.account.items():
                transaction_id = customers.add_transaction(
                    customer_id=int(customer_id), type_code="REVERSAL_ACCOUNT", amount_delta=delta,
                    created_by_user_id=actor.user_id, reference_type="TRANSACTION_CORRECTION", reference_id=correction_id,
                    reference_code=code, description=text)
                corrections.insert_leg(correction_id=correction_id, leg_role="POSTED", leg_kind="ACCOUNT", amount=delta,
                                       customer_transaction_id=transaction_id)
            items = corrections.lock_items([i for i, _, _, _ in plan.inventory])
            for item_id, from_status, to_status, movement_code in plan.inventory:
                corrections.set_item_status(inventory_item_id=item_id, from_status=from_status, to_status=to_status,
                                            updated_by_user_id=actor.user_id)
                corrections.add_inventory_movement(item=items[item_id], movement_code=movement_code,
                                                   from_status=from_status, to_status=to_status,
                                                   correction_id=correction_id, code=code, reason=f"{text} — {reason}",
                                                   performed_by_user_id=actor.user_id,
                                                   performed_by_employee_id=actor.employee_id)
            for karat_id, _, grams, movement_code in plan.old_gold:
                corrections.add_old_gold(karat_id=karat_id, weight_delta=grams, movement_code=movement_code,
                                         correction_id=correction_id, code=code, notes=text,
                                         created_by_user_id=actor.user_id, employee_id=actor.employee_id,
                                         customer_id=plan.customer_id)
            if plan.holdings:
                from repositories.intermediary_holding_repository import IntermediaryHoldingRepository as HoldingLedger
                ledger = HoldingLedger(connection)
                for pool_id, document_id, doc_kind, movement_type, grams, delta in sorted(plan.holdings):
                    ledger.insert_movement(pool_id=pool_id, document_id=document_id, doc_kind=doc_kind, movement_type=movement_type,
                                           weight=grams, delta=delta, correction_id=correction_id)
            if plan.lot_issues:
                from repositories.market_trip_repository import SHOP_ISSUE_REVERSAL, MarketTripRepository as IssueLedger
                for lot_id, grams in sorted(plan.lot_issues):
                    IssueLedger(connection).insert_shop_lot_movement(lot_id=lot_id, entry_id=source_id, movement_type=SHOP_ISSUE_REVERSAL,
                                                                     weight_delta=grams, correction_id=correction_id)
            if plan.lot_returns:
                from repositories.market_trip_repository import SHOP_RETURN_REVERSAL, MarketTripRepository as TripLedger
                shop_lots = TripLedger(connection)
                for lot_id, grams in sorted(plan.lot_returns):
                    shop_lots.insert_shop_lot_movement(lot_id=lot_id, entry_id=source_id, movement_type=SHOP_RETURN_REVERSAL,
                                                       weight_delta=-grams, correction_id=correction_id)
            for hold_id, karat_id, _, grams, item_id, out_code in plan.holds:
                # The exact HELD weight leaves Scrap with the piece (never more, never twice).
                out_id = corrections.add_old_gold(karat_id=karat_id, weight_delta=-grams,
                                                  movement_code=out_code,
                                                  correction_id=correction_id, code=code, notes=text,
                                                  created_by_user_id=actor.user_id, employee_id=actor.employee_id,
                                                  customer_id=plan.customer_id, inventory_item_id=item_id)
                if not corrections.reverse_hold(hold_id=hold_id, out_movement_id=out_id, user_id=actor.user_id,
                                                at=now):
                    raise RuntimeError("scrap hold is no longer held")
            for review_id in plan.reviews:
                corrections.dismiss_review(review_id=review_id, user_id=actor.user_id, at=now,
                                           notes=f"{text} — {reason}")
            for case_id in plan.cases:
                corrections.cancel_case(case_id=case_id, user_id=actor.user_id, at=now, notes=f"{text} — {reason}")
            if plan.lines:
                corrections.mark_lines_reversed(table=plan.lines[0], line_ids=plan.lines[1], correction_id=correction_id)
            event = {"correction_code": code, "correction_id": correction_id, "correction_type": FULL_REVERSAL,
                     "source_type": source_type, "source_id": str(source_id), "source_reference": plan.reference,
                     "reason": reason}
            if source_type not in STANDALONE:
                invoices.insert_event(invoice_id=plan.invoice["invoice_id"], event_type="REVERSED",
                                      performed_by_user_id=actor.user_id, details=event)
            self._audit(connection, actor, "TRANSACTION_REVERSED", correction_id, code, {**event, "effects": effects}, now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"correction_id": correction_id, "code": code, "source_reference": plan.reference, "effects": effects}

    @staticmethod
    def _post_money(corrections, correction_id, code, deltas, type_code, text, now, actor):
        for (ledger, account_id), delta in sorted(deltas.items()):
            if delta == 0:
                continue
            movement_id = corrections.add_money(ledger=ledger, account_id=account_id, delta=delta, type_code=type_code,
                                                correction_id=correction_id, code=code, description=text,
                                                occurred_at=now, created_by_user_id=actor.user_id)
            corrections.insert_leg(correction_id=correction_id, leg_role="POSTED", leg_kind="MONEY", amount=delta,
                                   cash_movement_id=movement_id if ledger == "CASH" else None,
                                   bank_movement_id=movement_id if ledger == "BANK" else None)

    # ------------------------------------------------------------ SETTLEMENT CORRECTION
    def _settlement_state(self, connection, source_type, source_id, lock):
        """The current EFFECTIVE settlement of a source: direction, legs, account part, total, chain."""
        corrections = self._corrections(connection)
        if source_type in (FO, CD, SMP, MTE, SGS):
            raise CorrectionError("SETTLEMENT_NOT_SUPPORTED",
                                  "هذه العملية تُصحح بالعكس الكامل ثم إعادة إدخالها صحيحة.")
        invoice, source, reference = self._load(connection, source_type, source_id, lock)
        reversal, chain = self._history(corrections, source_type, source_id)
        if reversal is not None or (source_type not in ("INVOICE", CS) and reversal_of(invoice)):
            raise CorrectionError("ALREADY_REVERSED", "تم عكس هذه العملية؛ لا يمكن تصحيح تسويتها.")
        if source_type == CS:
            original, direction = source["payments"], source["direction"]
        elif source_type == "INVOICE":
            original = corrections.invoice_payment_legs(invoice["invoice_id"])
            direction = original[0]["direction"] if original else \
                ("OUT" if invoice["net_direction"] == "SHOP_PAYS_CUSTOMER" else "IN")
        elif source_type == "EXCHANGE":
            original = source["payments"]
            signed = Decimal(str(source["signed_amount"]))
            if signed == 0:
                raise CorrectionError("NO_SETTLEMENT", "لا يوجد فرق مالي في هذا الاستبدال؛ لا توجد تسوية لتصحيحها.")
            direction = "IN" if signed > 0 else "OUT"
        else:
            original, direction = source["payments"], "OUT"
        if any(p["direction"] != direction for p in original):
            raise CorrectionError("MIXED_DIRECTION", "تسوية هذه العملية بها اتجاهان؛ لا يدعمها هذا الإصدار.")
        customer_id = (source or invoice).get("customer_id")
        chain_ids = [c["correction_id"] for c in chain]
        account_signed = sum((Decimal(str(v)) for v in
                              corrections.account_by_customer(source_type, source_id, chain_ids).values()), ZERO)
        if chain:
            legs = corrections.effective_legs(chain[-1]["correction_id"])
            money_legs = [{"method_code": l["payment_method_code"], "bank_account_id": l.get("bank_account_id"),
                           "amount": Decimal(str(l["amount"]))} for l in legs if l["leg_kind"] == "MONEY"]
        else:
            money_legs = [{"method_code": p["method_code"], "bank_account_id": p.get("bank_account_id"),
                           "amount": Decimal(str(p["amount"]))} for p in original]
        sign = 1 if direction == "IN" else -1
        if source_type == CS:
            account_signed = ZERO  # CS debt reduction is fixed, not a payment-allocation leg.
        account = abs(account_signed)
        if account_signed * sign < 0:
            raise CorrectionError("MIXED_DIRECTION", "حساب العميل في هذه العملية عكس اتجاهها؛ لا يدعمه هذا الإصدار.")
        total = sum((l["amount"] for l in money_legs), ZERO) + account
        if total <= 0:
            raise CorrectionError("NO_SETTLEMENT", "لا توجد تسوية مالية لتصحيحها.")
        return {"source_type": source_type, "invoice": invoice, "reference": reference, "direction": direction, "sign": sign,
                "customer_id": customer_id, "legs": money_legs, "account": account, "account_signed": account_signed,
                "total": total, "chain": chain, "chain_ids": chain_ids}

    @staticmethod
    def _normalized(legs, account):
        grouped = {}
        for leg in legs:
            key = (leg["method_code"], str(leg.get("bank_account_id") or ""))
            grouped[key] = grouped.get(key, ZERO) + Decimal(str(leg["amount"]))
        return sorted((k, v) for k, v in grouped.items() if v), Decimal(str(account))

    def current_settlement(self, source_type, source_id, *, _expected_actor=None):
        self._actor(_expected_actor)
        source_type = self._source_type(source_type)

        def read(connection):
            state = self._settlement_state(connection, source_type, source_id, lock=False)
            return self._settlement_view(state, self._invoices(connection).active_bank_accounts())
        return self._read(read)

    @staticmethod
    def _settlement_view(state, accounts):
        names = {str(a["id"]): a["name"] for a in accounts}
        return {"reference": state["reference"], "direction": state["direction"],
                "direction_text": "المستلم من العميل" if state["direction"] == "IN" else "المدفوع للعميل",
                "total": str(state["total"]), "account": str(state["account"]),
                "has_customer": state.get("source_type") != CS and state["customer_id"] not in (None, ""), "next_sequence": len(state["chain"]) + 1,
                "legs": [{"method_code": l["method_code"], "method_text": METHOD_TEXT.get(l["method_code"], l["method_code"]),
                          "bank_account_id": str(l["bank_account_id"]) if l.get("bank_account_id") else "",
                          "account_name": names.get(str(l.get("bank_account_id")), ""), "amount": str(l["amount"])}
                         for l in state["legs"]]}

    def _proposal(self, invoices, state, legs, account_amount, lock):
        """Validated new allocation: [(PaymentInput, amount)], account, per-account money map, problems."""
        problems, parsed = [], []
        for leg in legs:
            try:
                amount = _money_value(leg.amount, "PAYMENT_INVALID", "اكتب المبلغ بالأرقام.")
            except CorrectionError as error:
                problems.append(error)
                continue
            if amount == 0:
                problems.append(CorrectionError("PAYMENT_INVALID", "المبلغ يجب أن يكون أكبر من صفر."))
                continue
            parsed.append((PaymentInput(leg.method_code, str(amount), state["direction"], None,
                                        leg.bank_account_id or None), amount))
        account = ZERO
        if account_amount not in (None, ""):
            try:
                account = _money_value(account_amount, "ACCOUNT_INVALID", "مبلغ حساب العميل غير صحيح.")
            except CorrectionError as error:
                problems.append(error)
        if state.get("source_type") == CS and account_amount not in (None, "", "0", "0.00"):
            problems.append(CorrectionError("CS_ACCOUNT_FORBIDDEN", "تصحيح تسوية الحساب يقبل طرق الدفع الفعلية فقط."))
        if account and state["customer_id"] in (None, ""):
            problems.append(CorrectionError("ACCOUNT_NEEDS_CUSTOMER", "العملية بدون عميل مسجل؛ لا يمكن استخدام حسابه."))
        methods, accounts, cash, account_problems = InvoiceService._payment_accounts(
            invoices, [(p, None) for p, _ in parsed], lock=lock)
        problems += [CorrectionError(getattr(p, "code", "PAYMENT_INVALID"), str(p)) for p in account_problems]
        new_total = sum((a for _, a in parsed), ZERO) + account
        if not problems and new_total != state["total"]:
            problems.append(CorrectionError("TOTAL_MISMATCH",
                                            f"التسوية الجديدة {new_total:,.2f} ج لا تساوي إجمالي العملية "
                                            f"{state['total']:,.2f} ج."))
        new_money = {}
        if not problems:
            for index, (payment, amount) in enumerate(parsed):
                key = ("BANK", int(accounts[index]["id"])) if index in accounts else ("CASH", int(cash["id"]))
                new_money[key] = new_money.get(key, ZERO) + amount * state["sign"]
            new_legs = [{"method_code": p.method_code, "bank_account_id": p.bank_account_id, "amount": a}
                        for p, a in parsed]
            if self._normalized(new_legs, account) == self._normalized(state["legs"], state["account"]):
                problems.append(CorrectionError("NO_EFFECT_CORRECTION", "التسوية الجديدة مطابقة للحالية؛ لا يوجد تصحيح."))
        return parsed, account, new_money, problems

    def preview_settlement(self, source_type, source_id, legs, account_amount=None, *, _expected_actor=None):
        self._actor(_expected_actor)
        source_type = self._source_type(source_type)

        def read(connection):
            invoices, corrections = self._invoices(connection), self._corrections(connection)
            state = self._settlement_state(connection, source_type, source_id, lock=False)
            _, account, new_money, problems = self._proposal(invoices, state, legs, account_amount, lock=False)
            current = corrections.money_by_account(source_type, source_id, state["chain_ids"])
            return state, account, new_money, current, problems, invoices.active_bank_accounts()
        state, account, new_money, current, problems, accounts = self._read(read)
        view = self._settlement_view(state, accounts)
        names = {("BANK", int(a["id"])): a["name"] for a in accounts}
        deltas = []
        if not problems:
            for key in sorted(set(current) | set(new_money)):
                delta = new_money.get(key, ZERO) - Decimal(str(current.get(key, ZERO)))
                if delta:
                    deltas.append({"account": "النقدية" if key[0] == "CASH" else names.get(key, str(key[1])),
                                   "delta": str(delta)})
        account_delta = state["sign"] * account - state["account_signed"]
        return {**view, "new_account": str(account), "money_deltas": deltas, "account_delta": str(account_delta),
                "problems": [{"code": p.code, "message": str(p)} for p in problems]}

    def correct_settlement(self, source_type, source_id, legs, account_amount, reason, *, expected_sequence=None,
                           _expected_actor=None):
        session_actor = self._actor(_expected_actor)
        source_type, reason = self._source_type(source_type), _reason(reason)
        with self._transaction() as connection:
            actor = self._authorization.require_permission(connection, session_actor, CORRECT_PERMISSION)
            corrections, invoices, customers = (self._corrections(connection), self._invoices(connection),
                                                self._customers(connection))
            state = self._settlement_state(connection, source_type, source_id, lock=True)
            sequence = len(state["chain"]) + 1
            if expected_sequence is not None and int(expected_sequence) != sequence:
                raise CorrectionError("STALE_CORRECTION", "تم تصحيح هذه التسوية للتو من جهاز آخر؛ أعد التحميل.")
            parsed, account, new_money, problems = self._proposal(invoices, state, legs, account_amount, lock=True)
            if account:
                customer = invoices.lock_customer(state["customer_id"])
                if customer is None or not customer["is_active"] or customer["record_origin"] == "LEGACY_PLACEHOLDER":
                    problems.append(CorrectionError("CUSTOMER_INACTIVE", "حساب العميل غير متاح."))
            if problems:
                raise problems[0]
            current = corrections.money_by_account(source_type, source_id, state["chain_ids"])
            deltas = {key: new_money.get(key, ZERO) - Decimal(str(current.get(key, ZERO)))
                      for key in set(current) | set(new_money)}
            account_delta = state["sign"] * account - state["account_signed"]
            now = corrections.db_now()
            before = {"legs": [dict(l, amount=str(l["amount"])) for l in state["legs"]], "account": str(state["account"])}
            after = {"legs": [{"method_code": p.method_code, "bank_account_id": p.bank_account_id, "amount": str(a)}
                              for p, a in parsed], "account": str(account)}
            correction_id = corrections.insert_correction(
                correction_type=SETTLEMENT, source_type=source_type, source_id=source_id,
                source_reference=state["reference"], invoice_id=state["invoice"]["invoice_id"], chain_sequence=sequence,
                previous_correction_id=state["chain"][-1]["correction_id"] if state["chain"] else None,
                money_direction=state["direction"], settled_total=state["total"], reason=reason, before=before,
                after=after, effects={"money": {f"{k[0]}:{k[1]}": str(v) for k, v in deltas.items() if v},
                                      "account": str(account_delta)},
                performed_by_user_id=actor.user_id, performed_by_employee_id=actor.employee_id, occurred_at=now)
            code = correction_code(SETTLEMENT, correction_id)
            text = f"تصحيح تسوية {code} — {SOURCE_TEXT[source_type]} {state['reference']}"
            for payment, amount in parsed:
                corrections.insert_leg(correction_id=correction_id, leg_role="EFFECTIVE", leg_kind="MONEY", amount=amount,
                                       payment_method_code=payment.method_code,
                                       bank_account_id=None if payment.bank_account_id in (None, "") else int(payment.bank_account_id))
            if account:
                corrections.insert_leg(correction_id=correction_id, leg_role="EFFECTIVE", leg_kind="ACCOUNT", amount=account)
            self._post_money(corrections, correction_id, code, deltas, "SETTLEMENT_CORRECTION", text, now, actor)
            if account_delta:
                transaction_id = customers.add_transaction(
                    customer_id=int(state["customer_id"]), type_code="SETTLEMENT_CORRECTION", amount_delta=account_delta,
                    created_by_user_id=actor.user_id, reference_type="TRANSACTION_CORRECTION", reference_id=correction_id,
                    reference_code=code, description=text)
                corrections.insert_leg(correction_id=correction_id, leg_role="POSTED", leg_kind="ACCOUNT",
                                       amount=account_delta, customer_transaction_id=transaction_id)
            event = {"correction_code": code, "correction_id": correction_id, "correction_type": SETTLEMENT,
                     "source_type": source_type, "source_id": str(source_id), "source_reference": state["reference"],
                     "chain_sequence": sequence, "reason": reason, "before": before, "after": after}
            if source_type != CS:
                invoices.insert_event(invoice_id=state["invoice"]["invoice_id"], event_type="SETTLEMENT_CORRECTED",
                                      performed_by_user_id=actor.user_id, details=event)
            self._audit(connection, actor, "SETTLEMENT_CORRECTED", correction_id, code,
                        {**event, "money_deltas": {f"{k[0]}:{k[1]}": v for k, v in deltas.items() if v},
                         "account_delta": account_delta}, now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"correction_id": correction_id, "code": code, "sequence": sequence, "source_reference": state["reference"]}

    @staticmethod
    def _audit(connection, actor, event_type, correction_id, code, details, now):
        cursor = connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, "
                "description, metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (actor.user_id, event_type, "TRANSACTION_CORRECTION", int(correction_id), code,
                 f"{code} — {details.get('source_reference')} — {details.get('reason')}",
                 json.dumps(details, ensure_ascii=False, default=str), now))
        finally:
            cursor.close()
