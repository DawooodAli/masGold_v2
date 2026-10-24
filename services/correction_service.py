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
from services import book_rules, custody_rules
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
HRC = "HOLDING_RECEIPT"              # physical receipt from an intermediary holding (051): full reversal only, OWNER only
SPI = "SUPPLIER_PURCHASE_INVOICE"     # posted supplier purchase invoice (052): full reversal only, OWNER only, no intaken piece
SPI_REVERSE_PERMISSION = "SUPPLIER_INVOICE_REVERSE"
MKD = "MARKETER_DOCUMENT"             # marketer custody document (055): MH / MR / MB / MS, full reversal only, OWNER only
SOURCE_RIGHTS = {SMP: SMP_REVERSE_PERMISSION, MTE: MTE_PERMISSION, SGS: SGS_REVERSE_PERMISSION,
                 SPI: SPI_REVERSE_PERMISSION, MKD: HOLDING_REVERSE_PERMISSION}   # a right ON TOP of TRANSACTION_CORRECT
SOURCES = ("INVOICE", "EXCHANGE", "RETURN", CS, FO, CD, SMP, MTE, SGS, HRC, SPI, MKD)
STANDALONE = (CS, FO, CD, SMP, MTE, SGS, HRC, SPI, MKD)    # sources without an invoice (never a fabricated one)
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
SOURCE_TEXT[HRC] = "استلام ذهب من المسوقجي"
SOURCE_TEXT[SPI] = "فاتورة شراء مورد"
SOURCE_TEXT[MKD] = "مستند عهدة المسوقجي"


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
    extra_permissions: list = field(default_factory=list)    # rights required ON TOP of the source's own, EVERY one checked (a custody supplier payment, a holding)
    supplier_gold: list = field(default_factory=list)    # (supplier id, karat id, signed grams, signed Eq21, movement code) a gold settlement reversal restores
    holdings: list = field(default_factory=list)         # (pool id, document id, doc kind, movement type, grams, signed delta) explicit holding compensations (048)
    logo_holdings: list = field(default_factory=list)    # the same for master-karat LOGO pools (049)
    holding_settlements: list = field(default_factory=list)   # (is LOGO pool, pool id, settlement id, grams) the compensation of a settlement from a holding (050)
    receipts: list = field(default_factory=list)         # (pool id, receipt id, piece id, grams) a physical receipt reversal: stock out, holding grams back (051)
    marketer: list = field(default_factory=list)         # (ledger CASH/GOLD, party id, document id, doc kind, settlement id, movement type, magnitude, signed delta) marketer compensations (055)
    book_stock: list = field(default_factory=list)       # (entry id, line no, grams, posted, analytical evidence, difference) a BK_RETURN reversal: the assayed stock item leaves (056)
    stock_issue_reversals: list = field(default_factory=list)   # (item entry, item line, grams, posted, analytical, difference, independent, basis) a BK_ISS_STK reversal (058)
    marketer_trip: list = field(default_factory=list)    # (party id, entry id, entry kind, movement type, grams, signed delta) a BK_DLV_MKT / BK_RECEIVE reversal (058)


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
                if source["entry_kind"] == "BK_RETURN":          # 056: trip -> stock item rows (ascending) -> Scrap karats; a later consumer of an item locks the same way
                    for item in repo.entry_stock_items(source_id):
                        repo.stock_item(source_id, item["line_no"], lock=True)
                if source["entry_kind"] == "BK_ISS_STK":         # 058: trip -> the consumed stock items (ascending), the same order as the issue
                    for item in repo.entry_stock_issues(source_id):
                        repo.stock_item(item["item_entry_id"], item["item_line_no"], lock=True)
                if source["entry_kind"] == "BK_DLV_SUP":         # 058: trip -> supplier (the delivery's own order)
                    delivery = repo.entry_book_supplier(source_id)
                    if delivery is not None:
                        from repositories.supplier_gold_settlement_repository import SupplierGoldSettlementRepository as _BookSuppliers
                        _BookSuppliers(connection).get_supplier(delivery["supplier_id"], lock=True)
                if source["entry_kind"] in ("BK_DLV_MKT", "BK_RECEIVE", "ST_BAR_MKT"):     # 058 / 062: trip -> the marketer account
                    marketer_leg = repo.entry_marketer_leg(source_id)
                    if marketer_leg is not None:
                        from repositories.marketer_custody_repository import MarketerCustodyRepository as _BookMarketers
                        _BookMarketers(connection).party(marketer_leg["party_id"], lock=True)
                logo_link = holdings.logo_link_by_entry(source_id)             # 049: trip -> party -> LOGO pool (no lots)
                if hold_link is not None:
                    from repositories.supplier_gold_settlement_repository import SupplierGoldSettlementRepository as _Parties
                    _Parties(connection).get_supplier(hold_link["party_id"], lock=True)
                    holdings.lock_pool(hold_link["pool_id"])
                if logo_link is not None:
                    from repositories.supplier_gold_settlement_repository import SupplierGoldSettlementRepository as _LogoParties
                    _LogoParties(connection).get_supplier(logo_link["party_id"], lock=True)
                    holdings.lock_logo_pool(logo_link["pool_id"])
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
            if lock and source.get("source_kind") in ("HOLDING_LOT", "HOLDING_LOGO", "HOLDING_MKT"):
                # 050: lot (lot pools) -> holder and recipient in ASCENDING party id -> holding source pool. No trip is locked or reopened.
                from repositories.intermediary_holding_repository import IntermediaryHoldingRepository
                from repositories.market_trip_repository import MarketTripRepository
                holdings = IntermediaryHoldingRepository(connection)
                if source["lot_pool_id"] is not None:
                    MarketTripRepository(connection).lock_lots([source["delivered_lot_id"]])
                for party in sorted({int(source["supplier_id"]), int(source["holding_party_id"])}):
                    repo.get_supplier(party, lock=True)
                if source["lot_pool_id"] is not None:
                    holdings.lock_pool(source["lot_pool_id"])
                elif source["logo_pool_id"] is not None:
                    holdings.lock_logo_pool(source["logo_pool_id"])             # a marketer-account settlement has no pool: the parties above are its locks
                source = repo.source(source_id, lock=True)
            elif lock:                                          # the settlement's own lock order: supplier -> Scrap karat
                repo.get_supplier(source["supplier_id"], lock=True)
                if source.get("source_kind", "SHOP_SCRAP") == "SHOP_SCRAP":       # a custody-sourced settlement never touched Scrap
                    corrections.lock_old_gold_balance(source["delivered_karat_id"])
                source = repo.source(source_id, lock=True)
            reference = settlement_code(source_id)
            context = {"invoice_id": None, "invoice_number": reference, "customer_name_snapshot": None, "customer_id": None,
                       "finalized_at": source["occurred_at"], "net_amount": source["credited_weight_grams"],
                       "supplier_id": str(source["supplier_id"]), "supplier_name": source.get("supplier_name")}
            return context, source, reference
        if source_type == MKD:
            from repositories.financial_account_repository import FinancialAccountRepository as MarketerAccounts
            from repositories.marketer_custody_repository import MarketerCustodyRepository, document_code
            repo = MarketerCustodyRepository(connection)
            source = repo.document(source_id)
            if source is None:
                raise CorrectionError("SOURCE_NOT_FOUND", "مستند عهدة المسوقجي غير موجود.")
            if lock:                                            # the account (party row) -> Main Safe (cash documents) / Scrap karat (a gold return)
                repo.party(source["party_id"], lock=True)
                if source["doc_kind"] in ("CASH_HANDOVER", "CASH_RETURN"):
                    safe = repo.main_safe()
                    if safe is not None:
                        MarketerAccounts(connection).account("CASH", safe["id"], lock=True)
                elif source["doc_kind"] == "GOLD_RETURN":
                    karat = repo.karat21()
                    if karat is not None:
                        corrections.lock_old_gold_balance(karat["id"])
                source = repo.document(source_id, lock=True)
            reference = document_code(source["doc_kind"], source_id)
            context = {"invoice_id": None, "invoice_number": reference, "customer_name_snapshot": None, "customer_id": None,
                       "finalized_at": source["occurred_at"], "net_amount": source["cash_amount"] if source["cash_amount"] is not None else source["weight_grams"],
                       "source_direction": source["doc_kind"]}
            return context, source, reference
        if source_type == HRC:
            from repositories.intermediary_holding_repository import IntermediaryHoldingRepository, receipt_code
            from repositories.market_trip_repository import MarketTripRepository
            from repositories.supplier_gold_settlement_repository import SupplierGoldSettlementRepository
            holdings = IntermediaryHoldingRepository(connection)
            source = holdings.receipt(source_id)
            if source is None:
                raise CorrectionError("SOURCE_NOT_FOUND", "الاستلام غير موجود.")
            if lock:                                            # lot -> holder party -> holding source pool -> receipt piece; no trip is locked or reopened
                MarketTripRepository(connection).lock_lots([source["lot_id"]])
                SupplierGoldSettlementRepository(connection).get_supplier(source["party_id"], lock=True)
                holdings.lock_pool(source["pool_id"])
                holdings.lock_piece(source["piece_id"])
                source = holdings.receipt(source_id)
            reference = receipt_code(source_id)
            context = {"invoice_id": None, "invoice_number": reference, "customer_name_snapshot": None, "customer_id": None,
                       "finalized_at": source["occurred_at"], "net_amount": source["grams"]}
            return context, source, reference
        if source_type == SPI:
            from repositories.supplier_purchase_repository import SupplierPurchaseRepository
            from services import supplier_rules
            repo = SupplierPurchaseRepository(connection)
            source = repo.get_invoice(source_id)
            if source is None:
                raise CorrectionError("SOURCE_NOT_FOUND", "فاتورة المورد غير موجودة.")
            if lock:                                            # the invoice's own order (posting and intake use it too): invoice row -> supplier row
                source = repo.get_invoice(source_id, lock=True)
                repo.get_supplier(source["supplier_id"], lock=True)
            reference = supplier_rules.invoice_code(source_id)
            context = {"invoice_id": None, "invoice_number": reference, "customer_name_snapshot": None, "customer_id": None,
                       "finalized_at": source["posted_at"] or source.get("created_at"), "net_amount": source["supplier_weight_grams"],
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
                elif source["payment_method"] == "MARKETER":
                    from repositories.marketer_custody_repository import MarketerCustodyRepository
                    MarketerCustodyRepository(connection).party(source["marketer_party_id"], lock=True)
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

    def _history(self, corrections, source_type, source_id, current=False):
        rows = corrections.corrections_for_source(source_type, source_id, current=True) if current else corrections.corrections_for_source(source_type, source_id)
        reversal = next((r for r in rows if r["correction_type"] == FULL_REVERSAL), None)
        chain = [r for r in rows if r["correction_type"] == SETTLEMENT]
        return reversal, chain

    # ------------------------------------------------------------ FULL REVERSAL: the plan
    def _plan(self, connection, source_type, source_id, lock):
        corrections, invoices = self._corrections(connection), self._invoices(connection)
        invoice, source, reference = self._load(connection, source_type, source_id, lock)
        current_history = bool(lock and (source_type in (HRC, SPI, MKD, MTE) or (source_type == SGS and (source or {}).get("source_kind") in ("HOLDING_LOT", "HOLDING_LOGO", "HOLDING_MKT"))))
        # Use CURRENT history after the source lock, including the trip lock for MTE:
        # a reversal committed while waiting must produce ALREADY_REVERSED,
        # rather than a raw duplicate-key error from a stale snapshot decision.
        reversal, chain = (self._history(corrections, source_type, source_id, True) if current_history
                           else self._history(corrections, source_type, source_id))
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
        if source_type == FO:                                   # an advance with an effective repayment is reversed only after its repayments (074)
            from repositories.financial_account_repository import FinancialAccountRepository
            guard = getattr(FinancialAccountRepository(connection), "advance_reversal_blockers", None)
            for text in (guard(source_id) if guard else []):
                plan.blockers.append(("ADVANCE_HAS_REPAYMENTS", text))
        if source_type == MTE:
            from repositories.market_trip_repository import ISSUE as TRIP_ISSUE, MarketTripRepository, entry_code
            trips = MarketTripRepository(connection)
            if source["trip_status"] != "OPEN":
                plan.blockers.append(("TRIP_CLOSED", "العهدة مغلقة: أعد فتحها أولاً قبل عكس أي حركة فيها."))
            for leg in trips.entry_gold_legs(source_id):       # the exact stored Scrap movement is negated, never recomputed
                reversed_delta = -Decimal(str(leg["weight_delta_grams"]))
                code = "REVERSAL_TRIP_ISSUE_IN" if reversed_delta > 0 else "REVERSAL_TRIP_RETURN_OUT"        # 065: the label follows the stored sign (ST_ADJUST goes both ways)
                plan.old_gold.append((int(leg["karat_id"]), int(leg["karat_value"]), reversed_delta, code))
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
                                          "لا يمكن عكس الإخراج لأن رجوعًا أو سبكًا أو حمّية أو تسوية ذهب أو بيعًا مسجلًا بعده يعتمد عليه — اعكسها أولاً."))
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
                plan.extra_permissions.append(SMP_REVERSE_PERMISSION)
            if source["entry_kind"] == "ST_BAR_FEE":
                # 080. The fee stops counting in custody cash and the stored supplier deduction is restored; blocked when the fee cash was used.
                fee = trips.entry_bar_fee(source_id)
                if fee is None:
                    plan.blockers.append(("BAR_FEE_MISSING", "سجل أجر السبيكة الأصلي غير موجود."))
                else:
                    if fee.get("making_movement_id") is not None:
                        plan.making.append((str(fee["party_id"]), Decimal(str(fee["amount"])), "BAR_FEE_REVERSAL"))
                        plan.extra_permissions.append(SMP_REVERSE_PERMISSION)
                    cash = trips.custody_cash(source["trip_id"], exclude_entry_id=source_id, current=True)
                    if custody_rules.cash_rows_outstanding(cash) < 0:
                        plan.blockers.append(("DEPENDENT_CASH_USE_EXISTS",
                                              "لا يمكن عكس الأجر لأن نقديته استُخدمت بعد تسجيله — اعكس ما يعتمد عليه أولًا."))
            if source["entry_kind"] == "ST_ADJUST":
                # 065. An INCREASE put the stored grams into the custody (RAW +g) and took them out of Scrap: its reversal removes them from the custody again, so it is blocked when
                # they were used afterwards. A DECREASE returned grams to Scrap: its reversal needs that Scrap (decided with the generic Scrap check).
                cash = trips.custody_cash(source["trip_id"], exclude_entry_id=source_id, current=True)
                gold = trips.custody_gold(source["trip_id"], exclude_entry_id=source_id, current=True)
                if custody_rules.any_negative(cash, gold):
                    plan.blockers.append(("DEPENDENT_GOLD_USE_EXISTS",
                                          "لا يمكن عكس التصحيح لأن الذهب المضاف للعهدة استُخدم بعد تسجيله (سبك أو بيع أو تسوية أو رجوع) — اعكس ما يعتمد عليه أولًا."))
            if source["entry_kind"] == "GOLD_SALE":
                # 053. The sale document is never rewritten: the entry stops counting, so the exact stored grams are back in custody (an aggregate
                # line, or the whole lot through its SALE_OUT movement) and the stored proceeds leave the custody cash. No supplier, making,
                # settlement, Scrap, cash or bank movement is posted (there was none). Decided by arithmetic only: if the proceeds were already
                # returned to Main Safe or paid to a supplier from custody, the custody cash without this sale would be negative.
                cash = trips.custody_cash(source["trip_id"], exclude_entry_id=source_id, current=True)
                if custody_rules.cash_rows_outstanding(cash) < 0:
                    plan.blockers.append(("DEPENDENT_CASH_USE_EXISTS",
                                          "لا يمكن عكس البيع لأن ثمنه استُخدم بعد تسجيله (رجوع نقدية إلى الخزنة أو دفع لمورد من العهدة) — اعكس ما يعتمد عليه أولًا."))
            if source["entry_kind"] == "GOLD_BUY":
                # 054. The purchase document is never rewritten: the entry stops counting, so the stored cash is back in custody and the stored scrap
                # grams leave it. Nothing else was posted (no supplier, Scrap, cash or bank movement), so nothing else is reversed. Decided by
                # arithmetic only: if any purchased (karat, designation) was used after the purchase (returned, melted, heated, sold, delivered or
                # handed over), the custody gold without this purchase would be negative.
                cash = trips.custody_cash(source["trip_id"], exclude_entry_id=source_id, current=True)
                gold = trips.custody_gold(source["trip_id"], exclude_entry_id=source_id, current=True)
                if custody_rules.any_negative(cash, gold):
                    plan.blockers.append(("DEPENDENT_GOLD_USE_EXISTS",
                                          "لا يمكن عكس الشراء لأن الذهب المشترى استُخدم بعد تسجيله (رجوع للمحل أو سبك أو حمّية أو بيع أو تسوية أو تسليم) — "
                                          "اعكس ما يعتمد عليه أولًا."))
            if source["entry_kind"] in ("GOLD_SETTLE", "LOT_SETTLE", "ST_BAR_SUP"):
                # The stored supplier gold movement is restored (never a recomputed amount) and the entry stops consuming custody gold, so the
                # stored delivered grams are back in custody. No Scrap, cash, making, bank or lot movement is posted. The supplier-side
                # reversal of this document is refused: the custody entry is the only reversal path.
                from repositories.supplier_gold_settlement_repository import GOLD_REVERSAL, SupplierGoldSettlementRepository
                link = (trips.entry_lot_settlement(source_id) if source["entry_kind"] == "LOT_SETTLE"
                        else trips.entry_bar_supplier(source_id) if source["entry_kind"] == "ST_BAR_SUP" else trips.entry_gold_settlement(source_id))
                movement = None if link is None else SupplierGoldSettlementRepository(connection).gold_movement(link["settlement_id"])
                if movement is None:
                    plan.blockers.append(("SETTLEMENT_MOVEMENT_MISSING", "حركة ذهب المورد الأصلية للتسوية غير موجودة."))
                else:
                    plan.supplier_gold.append((int(movement["supplier_id"]), int(movement["karat_id"]), -Decimal(str(movement["weight_delta_grams"])),
                                               -Decimal(str(movement["equivalent_21_delta_grams"])), GOLD_REVERSAL))
                plan.extra_permissions.append(SGS_REVERSE_PERMISSION)
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
                    plan.extra_permissions.append(HOLDING_REVERSE_PERMISSION)
                    grams = Decimal(str(link["weight_grams"]))
                    balance = Decimal(str(holdings.pool_balance(link["pool_id"])))                 # CURRENT, compensations included
                    others = trips.custody_lots(source["trip_id"], exclude_entry_id=source_id, lot_id=link["lot_id"])
                    in_custody = sum((Decimal(str(l["outstanding_grams"])) for l in others), Decimal("0.000"))
                    held_total = Decimal(str(holdings.lot_held_total(link["lot_id"])))
                    if source["entry_kind"] == "LOT_HOLD":
                        settled = (holdings.pool_settlement_history(link["pool_id"]) if lock                # a decision: CURRENT, reversed ones included
                                   else holdings.display_pool_history(link["pool_id"]))                       # a preview: a plain read
                        if settled:
                            from repositories.supplier_gold_settlement_repository import settlement_code
                            codes = "، ".join(settlement_code(r["id"]) for r in settled)
                            if any(r["physical_declaration"] in ("GRAM_ENTITLEMENT", "PHYSICALLY_DIVIDED") for r in settled):
                                # Settlement history protects the handover reversal; the distinct normal return rechecks restored balance and physical evidence.
                                advice = ("وسجل التسوية يمنع عكس هذا التسليم. بعد عكس كل التسويات واستعادة كامل الرصيد، يمكن طلب الرجوع العادي "
                                          "بتأكيد المالك أن السبكة الأصلية نفسها سليمة كاملة؛ وتحتاج سجلات القسمة أو الاستلام المادي المعكوسة إلى إقرار المالك بأنها سُجلت بالخطأ ولم تقع فعليًا.")
                            else:
                                advice = "ولا يُعاد الاعتماد على سلامتها الفعلية بعكس التسليم؛ سجّل رجوعها العادي بتأكيد المالك الصريح."
                            plan.blockers.append(("DEPENDENT_HOLDING_SETTLEMENT_EXISTS",
                                                  f"لا يمكن عكس التسليم لأن لهذه السبكة تسوية ذهب لمورد مسجلة ({codes}) ولو عُكست: {advice}"))
                        elif balance != grams:
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
            if source["entry_kind"] in ("LOGO_HOLD", "HOLD_BACK"):
                # 049, pooled LOGO grams. CUSTODY follows the established convention (this entry stops counting; the shared arithmetic then shows what
                # custody would hold); the HOLDING ledger posts an explicit signed compensation and the original movement stays counted. Every
                # reversal must leave non-negative custody LOGO and a holding balance between zero and the pool's initial quantity.
                from repositories.intermediary_holding_repository import (IntermediaryHoldingRepository, MOVE_HANDOVER_REVERSAL,
                                                                          MOVE_RETURN_REVERSAL)
                holdings = IntermediaryHoldingRepository(connection)
                link = holdings.logo_link_by_entry(source_id)
                if link is None:
                    plan.blockers.append(("HOLDING_LINK_MISSING", "ربط حركة المسوقجي الأصلية غير موجود."))
                else:
                    plan.extra_permissions.append(HOLDING_REVERSE_PERMISSION)
                    quantity = Decimal(str(link["weight_grams"]))
                    pool = holdings.logo_pool(link["pool_id"])
                    initial = Decimal(str(pool["weight_grams"]))
                    balance = Decimal(str(holdings.logo_pool_balance(link["pool_id"])))                # CURRENT, compensations included
                    cash = trips.custody_cash(source["trip_id"], exclude_entry_id=source_id, current=True)
                    gold = trips.custody_gold(source["trip_id"], exclude_entry_id=source_id, current=True)
                    if source["entry_kind"] == "LOGO_HOLD":
                        if balance != initial:
                            from repositories.supplier_gold_settlement_repository import settlement_code
                            named = holdings.logo_pool_settlements(link["pool_id"])           # explanatory only: the decision is the arithmetic above
                            plan.blockers.append(("DEPENDENT_HOLDING_RETURN_EXISTS",
                                                  "لا يمكن عكس التسليم لأن رجوعًا من هذا المصدر لدى المسوقجي ما زال قائمًا — اعكس الرجوع أولًا."
                                                  + ("" if not named else " (تسويات الذهب المسجلة لهذا المصدر: " + "، ".join(settlement_code(r["id"]) for r in named)
                                                                           + " — اعكس الفعّال منها أولًا.)")))
                        plan.logo_holdings.append((int(link["pool_id"]), int(link["document_id"]), link["doc_kind"], MOVE_HANDOVER_REVERSAL, quantity, -quantity))
                    else:
                        if custody_rules.any_negative(cash, gold, designation="LOGO"):
                            plan.blockers.append(("DEPENDENT_CUSTODY_USE_EXISTS",
                                                  "لا يمكن عكس الرجوع لأن الكمية العائدة استُخدمت في العهدة بعده (رجوع أو تسوية أو حمّية) — اعكس ما يعتمد عليها أولًا."))
                        if balance + quantity > initial:
                            plan.blockers.append(("HOLDING_BALANCE_INVALID", "رصيد المصدر لدى المسوقجي بعد العكس يتجاوز كمية التسليم الأصلية."))
                        plan.logo_holdings.append((int(link["pool_id"]), int(link["document_id"]), link["doc_kind"], MOVE_RETURN_REVERSAL, quantity, quantity))
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
            if source["entry_kind"] in book_rules.BOOK_KINDS:
                self._plan_book_entry(plan, trips, source, source_id)
            # a BK_RECON (057) reverses by the correction record alone: it posted no cash, Scrap, stock, supplier or lot movement, so there is nothing to undo and no dependency
            # to check; the closed-trip guard above is its only blocker, and the reversal makes a NEW generation possible again.
            if source["entry_kind"] == "MELT":
                # A melt can be undone only while its lots have no unreversed downstream use (their own CREATE is not one).
                for dependent in trips.lot_downstream(source_id):
                    plan.blockers.append(("DEPENDENT_LOT_USE_EXISTS",
                                          "لا يمكن عكس السبك لأن شحنة ناتجة عنه نُقلت أو سُوّيت للمورد في الحركة "
                                          f"{entry_code(dependent['trip_id'], dependent['entry_no'])} — اعكسها أولاً."))
            if source["entry_kind"] == "ST_LOGO":
                # 062: the classification posts nothing outside the settlement ledger: the entry stops counting, so the grams are back in the UNCLASSIFIED row. Decided by arithmetic only:
                # any later use of the LOGO gold (heating, supplier settlement, hand-over, sale, return) makes the LOGO row without this entry negative. No cascade.
                if custody_rules.any_negative(trips.custody_cash(source["trip_id"], exclude_entry_id=source_id, current=True),
                                              trips.custody_gold(source["trip_id"], exclude_entry_id=source_id, current=True), designation="LOGO"):
                    plan.blockers.append(("DEPENDENT_LOGO_USE_EXISTS",
                                          "لا يمكن عكس اعتبار المتبقي لوجو لأن ذهب اللوجو استُخدم بعده (حمّية أو تسوية مورد أو تسليم لمسوقجي أو بيع أو رجوع) — اعكس ما يعتمد عليه أولًا."))
            if source["entry_kind"] == "ST_BAR_MKT":
                # 062: the STORED marketer leg gets its explicit compensation (the original stays counted) and the entry stops counting, so the grams are back in the BAR pool; a hand-over
                # whose grams the marketer no longer holds (settled to a supplier, returned) cannot be undone. Rights on top: INTERMEDIARY_HOLDING_REVERSE.
                from repositories.marketer_custody_repository import MarketerCustodyRepository as _Held
                leg = trips.entry_marketer_leg(source_id)
                if leg is None:
                    plan.blockers.append(("MARKETER_LEG_MISSING", "حركة حساب المسوقجي الأصلية لهذا المستند غير موجودة."))
                else:
                    plan.extra_permissions.append(HOLDING_REVERSE_PERMISSION)
                    grams, delta = Decimal(str(leg["weight_grams"])), Decimal(str(leg["weight_delta"]))
                    held = Decimal(str(_Held(connection).gold_balance(leg["party_id"])))
                    if held - grams < 0:
                        plan.blockers.append(("DEPENDENT_MARKETER_USE_EXISTS",
                                              f"لا يمكن عكس التسليم لأن المسوقجي لم يعد يحمل {grams:,.3f} جرام من استحقاقه (المتبقي {held:,.3f}) — اعكس ما يعتمد عليه أولًا."))
                    plan.marketer_trip.append((int(leg["party_id"]), int(source_id), "ST_BAR_MKT", leg["movement_type"] + "_REVERSAL", grams, -delta))
            if source["entry_kind"] == "ST_MELT":
                # 060: the melt of the settlement posts nothing outside its own ledger (no cash, Scrap, supplier or lot movement): the entry stops counting, so the melted grams
                # are back in the RAW pool and the bar leaves the BAR pool. Decided by arithmetic only: a later use of the bar (distribution, sale) makes the BAR pool without
                # this entry negative. The melt document and its figures stay as history.
                if trips.settle_bar_balance(source["trip_id"], exclude_entry_id=source_id, current=True) < 0:
                    plan.blockers.append(("DEPENDENT_SETTLEMENT_USE_EXISTS",
                                          "لا يمكن عكس تسجيل السبك لأن جزءًا من السبيكة وُزِّع أو بيع بعده — اعكس ما يعتمد عليه أولًا."))
        if source_type == SGS:
            # The STORED movements are what is undone (never a recomputed amount): the delivered grams return to Scrap and the credit is restored.
            from repositories.supplier_gold_settlement_repository import GOLD_REVERSAL, SCRAP_REVERSAL_IN, SupplierGoldSettlementRepository
            settlements = SupplierGoldSettlementRepository(connection)
            holding_source = source.get("source_kind") in ("HOLDING_LOT", "HOLDING_LOGO", "HOLDING_MKT")
            custody = None if holding_source or source.get("source_kind", "SHOP_SCRAP") == "SHOP_SCRAP" else settlements.custody_entry(source_id)
            scrap_leg, gold_leg = settlements.scrap_movement(source_id), settlements.gold_movement(source_id)
            if holding_source:
                # 050: ONE canonical correction on the SG, from either page. The stored supplier credit and the stored delivered grams are restored.
                if source.get("source_kind") == "HOLDING_MKT":
                    self._plan_marketer_settlement(connection, plan, source, source_id, settlements, lock)
                else:
                    self._plan_holding_settlement(connection, plan, source, source_id, settlements, lock)
            elif source.get("source_kind", "SHOP_SCRAP") != "SHOP_SCRAP":
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
        if source_type == MKD:
            self._plan_marketer_document(connection, plan, source, lock)
        if source_type == HRC:
            self._plan_receipt(connection, plan, source, lock)
        if source_type == SPI:
            self._plan_supplier_invoice(connection, plan, source, source_id, lock)
        if source_type == SMP:
            from repositories.supplier_payment_repository import REVERSAL_MOVEMENT, SupplierPaymentRepository
            payments = SupplierPaymentRepository(connection)
            movement = payments.payment_making_movement(source_id)
            if movement is None:
                plan.blockers.append(("PAYMENT_MOVEMENT_MISSING", "حركة المصنعيات الأصلية للدفعة غير موجودة."))
            else:
                plan.making.append((str(movement["supplier_id"]), -Decimal(str(movement["amount_delta"])), REVERSAL_MOVEMENT))
            if source["payment_method"] == "CUSTODY":
                # Paid with market-custody cash: the custody entry is the only reversal path (it also restores the custody cash).
                from repositories.market_trip_repository import MarketTripRepository, entry_code
                entry = MarketTripRepository(connection).payment_entry(source_id)
                plan.blockers.append(("CUSTODY_PAYMENT_REVERSE_FROM_TRIP",
                                      "هذه الدفعة سُدِّدت من عهدة السوق"
                                      + ("" if entry is None else f" ({entry_code(entry['trip_id'], entry['entry_no'])})")
                                      + ": اعكسها من حركة العهدة نفسها (دفع مصنعيات من العهدة) من صفحة عهدة السوق."))
            elif source["payment_method"] == "MARKETER":
                from repositories.marketer_custody_repository import MarketerCustodyRepository
                link = payments.marketer_link(source_id)
                if link is None or int(link["marketer_party_id"]) != int(source.get("marketer_party_id") or 0) \
                        or Decimal(str(link["amount"])) != Decimal(str(source["amount"])):
                    plan.blockers.append(("MARKETER_PAYMENT_LINK_MISSING", "ربط دفعة المسوقجي غير موجود أو لا يطابق الدفعة."))
                else:
                    marketer_repo = MarketerCustodyRepository(connection)
                    party = marketer_repo.party(link["marketer_party_id"])
                    if party is None or not party["is_intermediary"]:
                        plan.blockers.append(("MARKETER_NOT_FOUND", "المسوقجي المرتبط بدفعة المصنعيات غير موجود."))
                    else:
                        amount = Decimal(str(link["amount"]))
                        plan.marketer.append(("CASH", int(link["marketer_party_id"]), int(link["marketer_document_id"]),
                                              "SUPPLIER_PAY", None, "SUPPLIER_PAY_REVERSAL", amount, amount))
                        plan.extra_permissions.append(HOLDING_REVERSE_PERMISSION)
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

    @staticmethod
    def _plan_book_entry(plan, trips, source, source_id):
        """056: a full reversal of a BOOK entry. The entry stops counting in the book (the custody convention, so the book without it is derived), the STORED Scrap
        movements are negated (never recomputed), and an assayed-stock receipt gets an explicit compensation row with the STORED grams and posted figure.
        Guards are arithmetic with CURRENT locking reads under the trip lock, no cascade:
          incoming (BK_ISSUE / BK_BUY)   the book of the OTHER effective lines must stay analytically non-negative and consistent;
          BK_SELL                        the custody cash without the proceeds must stay non-negative (they were not already returned / paid / spent);
          BK_RETURN to Scrap             the Scrap availability check below (the returned grams were not already used);
          BK_RETURN assayed              every returned item must still hold its FULL grams and posted amount (nothing issued to a later trip)."""
        kind, trip_id = source["entry_kind"], source["trip_id"]
        reconciled = trips.effective_reconciliation_entries(trip_id)              # 057 finality: a settled trip's gold documents are final (a CURRENT read under the trip lock)
        if reconciled:
            from repositories.market_trip_repository import entry_code as _recon_code
            plan.blockers.append(("RECONCILIATION_FINAL", f"للعهدة تسوية معتمدة ({_recon_code(trip_id, reconciled[0]['entry_no'])}): اعكس التسوية أولًا قبل عكس حركة ذهب."))
        lines = trips.entry_book_lines(source_id)
        if not lines:
            plan.blockers.append(("BOOK_LINES_MISSING", "سطور الحركة الدفترية الأصلية غير موجودة."))
            return
        for line in lines:
            if line["scrap_movement_id"] is not None:
                code = "REVERSAL_TRIP_ISSUE_IN" if kind == book_rules.BK_ISSUE else "REVERSAL_TRIP_RETURN_OUT"
                plan.old_gold.append((int(line["karat_id"]), int(line["karat_value"]), -Decimal(str(line["weight_delta_grams"])), code))
        if kind in book_rules.INCOMING_KINDS:
            blocker = book_rules.check_without_incoming(book_rules.balance_of_rows(trips.book_lines(trip_id, exclude_entry_id=source_id, current=True)))
            if blocker is not None:
                plan.blockers.append(blocker)
        if kind == book_rules.BK_SELL:
            if custody_rules.cash_rows_outstanding(trips.custody_cash(trip_id, exclude_entry_id=source_id, current=True)) < 0:
                plan.blockers.append(("DEPENDENT_CASH_USE_EXISTS",
                                      "لا يمكن عكس البيع لأن ثمنه استُخدم بعد تسجيله (رجوع نقدية إلى الخزنة أو دفع من العهدة أو شراء) — اعكس ما يعتمد عليه أولًا."))
        if kind == book_rules.BK_RETURN:
            for item in trips.entry_stock_items(source_id):
                grams, posted = Decimal(str(item["weight_grams"])), Decimal(str(item["eq21_posted"]))
                held, held_posted = (Decimal(str(v)) for v in trips.stock_balance(source_id, item["line_no"]))     # CURRENT, every movement counted
                if held != grams or held_posted != posted:
                    plan.blockers.append(("DEPENDENT_STOCK_USE_EXISTS",
                                          f"لا يمكن عكس الرجوع لأن جزءًا من الذهب العائد بعيار فعلي (سطر {int(item['line_no'])}) استُخدم بعد تسجيله: المتبقي "
                                          f"{held:,.3f} من {grams:,.3f} جرام — اعكس الحركة التي استخدمته أولًا."))
                plan.book_stock.append((int(source_id), int(item["line_no"]), grams, posted, Decimal(str(item["eq21_analytical"])), Decimal(str(item["posting_difference"]))))
        if kind == book_rules.BK_ISS_STK:
            # 058: the STORED issue rows are compensated (grams and allocated share back to each item); the book side stops counting with the entry
            issues = trips.entry_stock_issues(source_id)
            if not issues:
                plan.blockers.append(("BOOK_STOCK_LEG_MISSING", "حركات إخراج المخزون الأصلية غير موجودة."))
            for item in issues:
                plan.stock_issue_reversals.append((int(item["item_entry_id"]), int(item["item_line_no"]), -Decimal(str(item["grams_delta"])),
                                                   -Decimal(str(item["eq21_posted_delta"])), Decimal(str(item["eq21_analytical"])),
                                                   Decimal(str(item["posting_difference"])), Decimal(str(item["eq21_independent"])), item["posting_basis"]))
        if kind == book_rules.BK_DLV_SUP:
            # 058: the STORED supplier gold movement is negated (the credit is restored in the supplier's own karat, never recomputed)
            from repositories.supplier_gold_settlement_repository import GOLD_REVERSAL
            delivery = trips.entry_book_supplier(source_id)
            if delivery is None or delivery["gold_movement_id"] is None:
                plan.blockers.append(("SUPPLIER_LEG_MISSING", "حركة حساب المورد الأصلية لهذا التسليم غير موجودة."))
            else:
                plan.supplier_gold.append((int(delivery["supplier_id"]), int(delivery["gold_karat_id"]), -Decimal(str(delivery["weight_delta_grams"])),
                                           -Decimal(str(delivery["equivalent_21_delta_grams"])), GOLD_REVERSAL))
        if kind in (book_rules.BK_DLV_MKT, book_rules.BK_RECEIVE):
            # 058: the STORED marketer leg gets its explicit compensation; a delivery whose grams the marketer no longer holds cannot be undone
            leg = trips.entry_marketer_leg(source_id)
            if leg is None:
                plan.blockers.append(("MARKETER_LEG_MISSING", "حركة حساب المسوقجي الأصلية لهذا المستند غير موجودة."))
            else:
                grams, delta = Decimal(str(leg["weight_grams"])), Decimal(str(leg["weight_delta"]))
                if kind == book_rules.BK_DLV_MKT:
                    from repositories.marketer_custody_repository import MarketerCustodyRepository as _Held
                    held = Decimal(str(_Held(trips.connection).gold_balance(leg["party_id"])))
                    if held - grams < 0:
                        plan.blockers.append(("DEPENDENT_MARKETER_USE_EXISTS",
                                              f"لا يمكن عكس التسليم لأن المسوقجي لم يعد يحمل {grams:,.3f} جرام من استحقاقه (المتبقي {held:,.3f}) — اعكس ما استخدمه أولًا."))
                plan.marketer_trip.append((int(leg["party_id"]), int(source_id), kind, leg["movement_type"] + "_REVERSAL", grams, -delta))

    def _plan_supplier_invoice(self, connection, plan, source, source_id, lock):
        """052: the exact STORED purchase movements are negated (never recomputed). A piece intaken from the invoice is real inventory with its own
        identity, so any intake refuses the reversal (no cascade); the pieces are read CURRENT under the invoice lock when a decision is taken."""
        from repositories.supplier_purchase_repository import (REVERSAL_MOVEMENT, SupplierPurchaseRepository)
        from services import supplier_rules
        repo = SupplierPurchaseRepository(connection)
        if source["status"] != supplier_rules.POSTED:
            plan.blockers.append(("INVOICE_NOT_POSTED", "يمكن عكس فاتورة مورد مرحّلة فقط؛ المسودة تُلغى من شاشة الفواتير."))
            return
        pieces = repo.intaken_pieces(source_id, lock=lock)
        if pieces:
            plan.blockers.append(("INVOICE_HAS_INTAKE",
                                  f"لا يمكن عكس الفاتورة لأن {len(pieces)} قطعة أُدخلت منها إلى المخزون. "
                                  "القطع المُدخلة لها هوية وباركود في المخزون ولا تُعكس تلقائيًا."))
        gold, making = repo.posted_gold_movement(source_id), repo.posted_making_movement(source_id)
        if gold is None:
            plan.blockers.append(("INVOICE_MOVEMENT_MISSING", "حركة الذهب الأصلية للفاتورة غير موجودة."))
            return
        plan.supplier_gold.append((int(gold["supplier_id"]), int(gold["karat_id"]), -Decimal(str(gold["weight_delta_grams"])),
                                   -Decimal(str(gold["equivalent_21_delta_grams"])), REVERSAL_MOVEMENT))
        if making is not None:
            plan.making.append((str(making["supplier_id"]), -Decimal(str(making["amount_delta"])), REVERSAL_MOVEMENT))

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
    def _plan_receipt(connection, plan, source, lock):
        """051. The receipt piece is already locked (_load). Its FULL received stock must still be present: any later consumer of receipt stock (none exists in
        this slice) must lock the piece first, take a current stock read and post a negative movement, which makes this reversal refuse. The stored grams
        are restored to the holding; the resulting balance must stay within the pool's initial quantity."""
        from repositories.intermediary_holding_repository import IntermediaryHoldingRepository
        holdings = IntermediaryHoldingRepository(connection)
        piece_id, pool_id, grams = int(source["piece_id"]), int(source["pool_id"]), Decimal(str(source["grams"]))
        plan.extra_permissions.append(HOLDING_REVERSE_PERMISSION)
        if holdings.receipt_movement(source["id"]) is None:
            plan.blockers.append(("RECEIPT_MOVEMENT_MISSING", "حركة الاستلام الأصلية على رصيد المسوقجي غير موجودة."))
            return
        stock = Decimal(str(holdings.piece_stock(piece_id) if lock else holdings.display_piece_stock(piece_id)))      # a decision: CURRENT
        if stock != grams:
            plan.blockers.append(("RECEIPT_STOCK_NOT_PRESENT",
                                  f"لا يمكن عكس الاستلام لأن مخزون القطعة المستلمة لم يعد كاملًا ({stock:,.3f} من {grams:,.3f} جرام): "
                                  "ما استُخدم منها يُعاد أولًا."))
        pool = holdings.pool(pool_id)
        balance = Decimal(str(holdings.pool_balance(pool_id) if lock else holdings.display_balance(logo=False, pool_id=pool_id)))
        if balance + grams > Decimal(str(pool["weight_grams"])):
            plan.blockers.append(("HOLDING_BALANCE_INVALID", "رصيد المصدر لدى المسوقجي بعد العكس يتجاوز كمية التسليم الأصلية."))
        plan.receipts.append((pool_id, int(source["id"]), piece_id, grams))

    @staticmethod
    def _plan_marketer_document(connection, plan, source, lock):
        """055. The document's own movements are undone by explicit compensations (the originals stay counted). The shop-side Main Safe cash is undone by the
        generic money reversal (the document is the reference of that movement); a gold return also takes the 21K grams back out of the counted Scrap. Decided by
        arithmetic only (CURRENT reads under the account lock): neither the marketer's cash nor his gold may be negative after the reversal."""
        from repositories.marketer_custody_repository import (CASH_MOVEMENT, GOLD_MOVEMENT as MARKETER_GOLD, KARAT_VALUE, SHOP_SCRAP_REVERSAL,
                                                              MarketerCustodyRepository)
        repo = MarketerCustodyRepository(connection)
        kind, party, document_id = source["doc_kind"], int(source["party_id"]), int(source["id"])
        if kind in CASH_MOVEMENT:
            original, compensation, sign = CASH_MOVEMENT[kind]
            amount = Decimal(str(source["cash_amount"]))
            delta = -sign * amount
            held = Decimal(str(repo.cash_balance(party) if lock else repo.display_cash_balance(party)))
            plan.marketer.append(("CASH", party, document_id, kind, None, compensation, amount, delta))
            if held + delta < 0:
                plan.blockers.append(("DEPENDENT_CASH_USE_EXISTS",
                                      "لا يمكن عكس هذا المستند لأن نقدية المسوقجي بعده لا تكفي (اُستخدمت أو استُرجعت بعد تسجيله) — اعكس ما يعتمد عليها أولًا."))
        if kind in MARKETER_GOLD:
            original, compensation, sign = MARKETER_GOLD[kind]
            weight = Decimal(str(source["weight_grams"]))
            delta = -sign * weight
            held = Decimal(str(repo.gold_balance(party) if lock else repo.display_gold_balance(party)))
            plan.marketer.append(("GOLD", party, document_id, kind, None, compensation, weight, delta))
            if held + delta < 0:
                plan.blockers.append(("DEPENDENT_GOLD_USE_EXISTS",
                                      "لا يمكن عكس هذا المستند لأن ذهب المسوقجي (عيار 21) بعده لا يكفي (بيع أو سُلِّم لمورد أو استُرجع بعد تسجيله) — "
                                      "اعكس ما يعتمد عليه أولًا."))
            if kind == "GOLD_RETURN":                          # the grams the shop received leave the counted Scrap again
                karat = repo.karat21()
                plan.old_gold.append((int(karat["id"]), KARAT_VALUE, -weight, SHOP_SCRAP_REVERSAL))

    @staticmethod
    def _plan_marketer_settlement(connection, plan, source, source_id, settlements, lock):
        """055. A settlement from the marketer account: the STORED supplier credit is restored and the STORED delivered grams return to the marketer (an
        explicit SETTLEMENT_REVERSAL beside the original); nothing is recalculated and no cash, Scrap or other ledger moves."""
        from repositories.marketer_custody_repository import MOVE_SETTLEMENT_REVERSAL, MarketerCustodyRepository
        from repositories.supplier_gold_settlement_repository import GOLD_REVERSAL
        repo = MarketerCustodyRepository(connection)
        gold_leg, movement = settlements.gold_movement(source_id), repo.settlement_gold_movement(source_id)
        if gold_leg is None or movement is None:
            plan.blockers.append(("SETTLEMENT_MOVEMENT_MISSING", "حركات التسوية الأصلية غير موجودة."))
            return
        plan.supplier_gold.append((int(gold_leg["supplier_id"]), int(gold_leg["karat_id"]), -Decimal(str(gold_leg["weight_delta_grams"])),
                                   -Decimal(str(gold_leg["equivalent_21_delta_grams"])), GOLD_REVERSAL))
        plan.extra_permissions.append(HOLDING_REVERSE_PERMISSION)
        quantity = Decimal(str(movement["weight_grams"]))
        plan.marketer.append(("GOLD", int(movement["party_id"]), None, None, int(source_id), MOVE_SETTLEMENT_REVERSAL, quantity, quantity))

    @staticmethod
    def _plan_holding_settlement(connection, plan, source, source_id, settlements, lock):
        from repositories.intermediary_holding_repository import IntermediaryHoldingRepository
        from repositories.supplier_gold_settlement_repository import GOLD_REVERSAL
        holdings = IntermediaryHoldingRepository(connection)
        logo = source["source_kind"] == "HOLDING_LOGO"
        pool_id = int(source["logo_pool_id"] if logo else source["lot_pool_id"])
        quantity = Decimal(str(source["holding_weight_grams"]))
        gold_leg = settlements.gold_movement(source_id)
        if gold_leg is None or holdings.settlement_movement(logo=logo, settlement_id=source_id) is None:
            plan.blockers.append(("SETTLEMENT_MOVEMENT_MISSING", "حركات التسوية الأصلية غير موجودة."))
            return
        plan.supplier_gold.append((int(gold_leg["supplier_id"]), int(gold_leg["karat_id"]), -Decimal(str(gold_leg["weight_delta_grams"])),
                                   -Decimal(str(gold_leg["equivalent_21_delta_grams"])), GOLD_REVERSAL))
        plan.extra_permissions.append(HOLDING_REVERSE_PERMISSION)
        pool = holdings.logo_pool(pool_id) if logo else holdings.pool(pool_id)
        if lock:                                                              # a decision: CURRENT, compensations included
            balance = Decimal(str(holdings.logo_pool_balance(pool_id) if logo else holdings.pool_balance(pool_id)))
        else:                                                                 # a preview: a plain read, no lock
            balance = Decimal(str(holdings.display_balance(logo=logo, pool_id=pool_id)))
        if balance + quantity > Decimal(str(pool["weight_grams"])):
            plan.blockers.append(("HOLDING_BALANCE_INVALID", "رصيد المصدر لدى المسوقجي بعد العكس يتجاوز كمية التسليم الأصلية."))
        plan.holding_settlements.append((logo, pool_id, int(source_id), quantity))

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
            "logo_holdings": [{"pool_id": p, "document_id": d, "movement": m, "delta": str(delta)} for p, d, _, m, _, delta in plan.logo_holdings],
            "holding_settlements": [{"pool_id": p, "settlement_id": s, "logo": logo, "delta": str(g)} for logo, p, s, g in plan.holding_settlements],
            "receipts": [{"pool_id": p, "receipt_id": r, "piece_id": i, "grams": str(g)} for p, r, i, g in plan.receipts],
            "book_stock": [{"entry_id": e, "line_no": n, "grams": str(g), "posted": str(p)} for e, n, g, p, _, _ in plan.book_stock],
            "stock_issue_reversals": [{"item": f"{e}/{n}", "grams": str(g), "posted": str(p), "posting_basis": b} for e, n, g, p, _, _, _, b in plan.stock_issue_reversals],
            "marketer_trip": [{"party_id": p, "movement": m, "delta": str(d)} for p, _, _, m, _, d in plan.marketer_trip],
            "marketer": [{"ledger": k, "party_id": p, "document_id": d, "settlement_id": s, "movement": m, "delta": str(delta)}
                         for k, p, d, _, s, m, _, delta in plan.marketer],
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
            for extra in plan.extra_permissions:        # e.g. SUPPLIER_MAKING_PAY_REVERSE for a custody supplier payment; EVERY one is checked
                actor = self._authorization.require_permission(connection, session_actor, extra)
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
            if plan.logo_holdings:
                from repositories.intermediary_holding_repository import IntermediaryHoldingRepository as LogoLedger
                logo_ledger = LogoLedger(connection)
                for pool_id, document_id, doc_kind, movement_type, grams, delta in sorted(plan.logo_holdings):
                    logo_ledger.insert_logo_movement(pool_id=pool_id, document_id=document_id, doc_kind=doc_kind, movement_type=movement_type,
                                                     weight=grams, delta=delta, correction_id=correction_id)
            if plan.marketer:
                from repositories.marketer_custody_repository import MarketerCustodyRepository as MarketerLedger
                marketer_ledger = MarketerLedger(connection)
                parties = set()
                for ledger_kind, party_id, document_id, doc_kind, settlement_id, movement_type, magnitude, delta in sorted(
                        plan.marketer, key=lambda row: (row[0], row[1], row[2] or 0, row[4] or 0)):
                    parties.add(party_id)
                    if ledger_kind == "CASH":
                        marketer_ledger.insert_cash_movement(party_id=party_id, document_id=document_id, doc_kind=doc_kind, movement_type=movement_type,
                                                             amount=magnitude, delta=delta, correction_id=correction_id)
                    else:
                        marketer_ledger.insert_gold_movement(party_id=party_id, document_id=document_id, doc_kind=doc_kind, settlement_id=settlement_id,
                                                             movement_type=movement_type, weight=magnitude, delta=delta, correction_id=correction_id)
                for party_id in sorted(parties):                 # explicit check of the resulting balances (never negative)
                    if Decimal(str(marketer_ledger.cash_balance(party_id))) < 0 or Decimal(str(marketer_ledger.gold_balance(party_id))) < 0:
                        raise RuntimeError("marketer balance out of range after the reversal")
            if plan.holding_settlements:
                from repositories.intermediary_holding_repository import IntermediaryHoldingRepository as SettlementLedger, MOVE_SETTLEMENT_REVERSAL
                settlement_ledger = SettlementLedger(connection)
                for logo, pool_id, settlement_id, grams in sorted(plan.holding_settlements):
                    settlement_ledger.insert_settlement_movement(logo=logo, pool_id=pool_id, settlement_id=settlement_id,
                                                                 movement_type=MOVE_SETTLEMENT_REVERSAL, weight=grams, delta=grams, correction_id=correction_id)
                    pool = settlement_ledger.logo_pool(pool_id) if logo else settlement_ledger.pool(pool_id)
                    after = Decimal(str(settlement_ledger.logo_pool_balance(pool_id) if logo else settlement_ledger.pool_balance(pool_id)))
                    if not Decimal("0") <= after <= Decimal(str(pool["weight_grams"])):         # explicit check of the resulting pool balance
                        raise RuntimeError("holding balance out of range after the settlement reversal")
            if plan.receipts:
                from repositories.intermediary_holding_repository import IntermediaryHoldingRepository as ReceiptLedger, MOVE_RECEIPT_REVERSAL, RECEIVE_REVERSAL
                receipt_ledger = ReceiptLedger(connection)
                for pool_id, receipt_id, piece_id, grams in sorted(plan.receipts):
                    # the stock leaves and the holding grams come back in ONE transaction, under one correction
                    receipt_ledger.insert_stock_movement(piece_id=piece_id, receipt_id=receipt_id, movement_type=RECEIVE_REVERSAL, weight=grams, delta=-grams,
                                                         correction_id=correction_id)
                    receipt_ledger.insert_receipt_movement(pool_id=pool_id, receipt_id=receipt_id, movement_type=MOVE_RECEIPT_REVERSAL, weight=grams, delta=grams,
                                                           correction_id=correction_id)
                    pool = receipt_ledger.pool(pool_id)
                    stock_after = Decimal(str(receipt_ledger.piece_stock(piece_id)))
                    pool_after = Decimal(str(receipt_ledger.pool_balance(pool_id)))
                    if stock_after != 0 or not Decimal("0") <= pool_after <= Decimal(str(pool["weight_grams"])):     # explicit conservation check
                        raise RuntimeError("receipt stock or holding balance out of range after the receipt reversal")
            if plan.book_stock:
                from repositories.market_trip_repository import MarketTripRepository as StockLedger
                stock_ledger = StockLedger(connection)
                for entry_id, line_no, grams, posted, analytical, difference in sorted(plan.book_stock):
                    # an explicit compensation with the STORED figures (the original receipt stays counted); the item must end at exactly zero
                    stock_ledger.insert_stock_movement(item_entry_id=entry_id, item_line_no=line_no, movement_type=book_rules.RECEIVE_REVERSAL, grams_delta=-grams,
                                                       posted_delta=-posted, analytical=analytical, difference=difference, independent=posted,
                                                       posting_basis=book_rules.INDEPENDENT, source_entry_id=entry_id, correction_id=correction_id,
                                                       user_id=actor.user_id)
                    if tuple(Decimal(str(v)) for v in stock_ledger.stock_balance(entry_id, line_no)) != (Decimal("0.000"), Decimal("0.000")):
                        raise RuntimeError("assayed stock item is not empty after the return reversal")
            if plan.stock_issue_reversals:
                from repositories.market_trip_repository import MarketTripRepository as IssueStock
                stock_ledger = IssueStock(connection)
                for item_entry, item_line, grams, posted, analytical, difference, independent, basis in sorted(plan.stock_issue_reversals):
                    stock_ledger.insert_stock_movement(item_entry_id=item_entry, item_line_no=item_line, movement_type=book_rules.ISSUE_REVERSAL, grams_delta=grams,
                                                       posted_delta=posted, analytical=analytical, difference=difference, independent=independent,
                                                       posting_basis=basis, source_entry_id=int(source_id), correction_id=correction_id,
                                                       user_id=actor.user_id)
            if plan.marketer_trip:
                from repositories.marketer_custody_repository import MarketerCustodyRepository as TripMarketers
                trip_marketers = TripMarketers(connection)
                for party_id, entry_id, entry_kind, movement_type, grams, delta in plan.marketer_trip:
                    trip_marketers.insert_gold_movement(party_id=party_id, movement_type=movement_type, weight=grams, delta=delta, correction_id=correction_id,
                                                        trip_entry_id=entry_id, trip_entry_kind=entry_kind)
                    if Decimal(str(trip_marketers.gold_balance(party_id))) < 0:
                        raise RuntimeError("marketer gold balance below zero after the reversal")
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
        if source_type in (FO, CD, SMP, MTE, SGS, HRC, SPI, MKD):
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
