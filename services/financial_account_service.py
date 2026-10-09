"""Financial Accounts V1 (025): one operational view of every cash / bank / wallet account and the
manual money operations that are not a sale, a customer payment, a Return or a CS.

Balances and running balances come only from the canonical cash_movements / bank_movements ledgers.
A balance may be negative (allowed by business rule): nothing here refuses an OUT for lack of funds
and nothing falls back to another account.

Manual operations (FINANCIAL_ACCOUNT_OPERATE, re-checked inside the transaction):
  MANUAL_DEPOSIT     FOxxxxxx  one IN leg,  reason required
  MANUAL_WITHDRAWAL  FOxxxxxx  one OUT leg, reason required
  ACCOUNT_TRANSFER   TRxxxxxx  OUT on the source + IN on the destination, one atomic operation
Each is idempotent on its request key (a double click returns the first result) and immutable once
finalized; its only correction is a full reversal (RV) through CorrectionService.

Lock order: actor → accounts (CASH before BANK, ascending id) → request key.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal
import hashlib
import json
from uuid import UUID

from repositories.financial_account_repository import (FinancialAccountRepository, SOURCE, SOURCE_TYPES,
                                                       account_key, operation_code, parse_account_key)
from services.authorization_service import AuthorizationService
from services.correction_service import CorrectionError, _money_value
from services.customer_service import CustomerService
from services.invoice_service import invoice_transaction

OPERATE = "FINANCIAL_ACCOUNT_OPERATE"
VIEW_PERMISSIONS = ("MANAGEMENT_REVIEW", "CASH_VIEW", "BANK_VIEW", OPERATE)
ALL_LEDGERS = ("CASH", "BANK")
DEPOSIT, WITHDRAWAL, TRANSFER = "MANUAL_DEPOSIT", "MANUAL_WITHDRAWAL", "ACCOUNT_TRANSFER"
TYPE_TEXT = {DEPOSIT: "إيداع يدوي", WITHDRAWAL: "سحب يدوي", TRANSFER: "تحويل بين الحسابات"}
PAGE_SIZE = 50
CASH_ROWS = 200                         # per table on the cash screen (the period totals stay exact)

# The ONE employee-facing name of every cash movement (read-model label_key → Arabic). Never built in QML.
CASH_LABELS = {
    "SALE": "بيع", "OLD_GOLD_PURCHASE": "شراء كسر", "EXCHANGE": "استبدال", "RETURN": "مرتجع",
    "CUSTOMER_PAYMENT": "سداد عميل", "CUSTOMER_PAYOUT": "دفع لعميل", "DEPOSIT": "عربون",
    "DEPOSIT_REFUND": "استرداد عربون", "SUPPLIER_PAYMENT": "دفعة مصنعيات مورد", "REVERSAL": "إلغاء عملية",
    "CORRECTION": "تصحيح تسوية", "TRANSFER_IN": "سحب بنك", "TRANSFER_OUT": "إيداع بنك",
    "EXPENSE": "مصروفات", "SALARY": "مرتبات", "RENT": "إيجار", "REVENUE": "إيراد", "REPAIR": "مرمة",
    "MANUAL_DEPOSIT": "إيداع يدوي", "MANUAL_WITHDRAWAL": "سحب يدوي", "OPENING_BALANCE": "رصيد افتتاحي",
    "OTHER": "حركة أخرى"}
# The same business names seen from a bank / wallet / InstaPay account (a transfer INTO the account is a deposit).
BANK_LABELS = {**CASH_LABELS, "TRANSFER_IN": "إيداع في الحساب", "TRANSFER_OUT": "سحب من الحساب"}
# Manual cash movement types (prototype CASH_TYPES without a customer) and the cash directions they allow.
# BANK_DEPOSIT / BANK_WITHDRAWAL are internal transfers (cash ↔ a real bank / wallet account).
CASH_TYPES = {"EXPENSE": ("OUT",), "SALARY": ("OUT",), "RENT": ("OUT",), "REVENUE": ("IN",),
              "REPAIR": ("IN", "OUT"), "BANK_WITHDRAWAL": ("IN",), "BANK_DEPOSIT": ("OUT",)}
CASH_TYPE_TEXT = {**{k: CASH_LABELS[k] for k in ("EXPENSE", "SALARY", "RENT", "REVENUE", "REPAIR")},
                  "BANK_DEPOSIT": "إيداع بنك", "BANK_WITHDRAWAL": "سحب بنك"}


class _Discard(Exception):
    pass


def movement_context(row):
    """What identifies a ledger movement to an accountant (all of it already resolved by the read model):
    its own reference (invoice number / CS / DP / DR / RT / EX / RV / FC / FO / TR), the invoice it belongs to,
    the corrected transaction's reference and the customer. Stored facts only — nothing is guessed."""
    def text(key):
        value = row.get(key)
        return "" if value in (None, "") else str(value)
    return {"reference": text("source_reference"), "invoice_number": text("invoice_number"),
            "corrected_reference": text("corrected_reference"), "customer": text("customer_name"),
            "customer_id": text("customer_id"), "kind": text("label_key")}


def _text(value, *, required, label):
    text = (value or "").strip()
    if required and not text:
        raise CorrectionError("REASON_REQUIRED", f"اكتب {label}.")
    if len(text) > 1000:
        raise CorrectionError("REASON_TOO_LONG", f"{label} طويل جداً.")
    return text


def _amount(value):
    amount = _money_value(value, "AMOUNT_INVALID", "اكتب المبلغ بالأرقام بخانتين عشريتين على الأكثر.")
    if amount <= 0:
        raise CorrectionError("AMOUNT_INVALID", "المبلغ يجب أن يكون أكبر من صفر.")
    if amount >= Decimal("100000000000000"):
        raise CorrectionError("AMOUNT_INVALID", "المبلغ أكبر من المسموح.")
    return amount


def visible_ledgers(permissions):
    """Least privilege: managers and operators see every ledger; CASH_VIEW only cash accounts,
    BANK_VIEW only bank / wallet accounts."""
    permissions = set(permissions)
    if permissions & {"MANAGEMENT_REVIEW", OPERATE}:
        return ALL_LEDGERS
    return tuple(ledger for ledger, code in (("CASH", "CASH_VIEW"), ("BANK", "BANK_VIEW")) if code in permissions)


def _account(key):
    parsed = parse_account_key(key)
    if parsed is None:
        raise CorrectionError("ACCOUNT_NOT_FOUND", "اختر الحساب.")
    return parsed


class FinancialAccountService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=FinancialAccountRepository,
                 authorization=None):
        self._session = session
        self._transaction = transaction_factory
        self._repository = repository
        self._authorization = authorization or AuthorizationService(session)

    # ------------------------------------------------------------ plumbing
    def _actor(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        return actor

    def _viewer(self, expected_actor):
        actor = self._actor(expected_actor)
        if not set(VIEW_PERMISSIONS) & set(actor.permissions):
            raise PermissionError("ليس لديك صلاحية عرض الحسابات المالية.")
        return actor

    def _read(self, actor, work):
        """A read transaction that is always rolled back. The view permission is re-checked inside it and
        the visible ledgers come from the database-verified permissions (never from the UI)."""
        try:
            with self._transaction() as connection:
                verified = self._authorization.require_any_permission_read(connection, actor, VIEW_PERMISSIONS)
                ledgers = visible_ledgers(verified.permissions)
                if not ledgers:
                    raise PermissionError("ليس لديك صلاحية عرض الحسابات المالية.")
                raise _Discard(work(self._repository(connection), ledgers))
        except _Discard as done:
            return done.args[0]

    @staticmethod
    def _require_ledgers(ledgers, *needed):
        if any(ledger not in ledgers for ledger in needed if ledger):
            raise PermissionError("ليس لديك صلاحية عرض هذا الحساب.")

    def can_view(self):
        actor = self._session.current_actor()
        # The daily screen is the cash safe: shown only to users who may see the cash ledger.
        return bool(actor and "CASH" in visible_ledgers(actor.permissions))

    # ------------------------------------------------------------ read side
    def accounts(self, *, _expected_actor=None):
        """Every account with its authoritative balance; the operator's rights for the UI."""
        actor = self._viewer(_expected_actor)
        rows = self._read(actor, lambda repository, ledgers: repository.accounts(ledgers))
        return {"accounts": [{"key": account_key(r["ledger"], r["account_id"]), "ledger": r["ledger"],
                              "account_id": str(r["account_id"]), "name": r["name"],
                              "account_type": r["account_type"], "bank_name": r.get("bank_name"),
                              "is_active": bool(r["is_active"]), "balance": str(Decimal(str(r["balance"]))),
                              "movement_count": int(r["movement_count"]),
                              "last_movement_at": r["last_movement_at"]} for r in rows],
                "can_operate": OPERATE in actor.permissions,
                "can_correct": "TRANSACTION_CORRECT" in actor.permissions}

    def statement(self, key, start="", end="", direction="", source="", search="", page=1, *, _expected_actor=None):
        actor = self._viewer(_expected_actor)
        ledger, account_id = _account(key)
        page = CustomerService._page_number(page)
        first = CustomerService._statement_date(start, "تاريخ البداية")
        last = CustomerService._statement_date(end, "تاريخ النهاية")
        if first and last and first > last:
            raise ValueError("تاريخ البداية بعد النهاية.")
        if direction not in ("", "IN", "OUT"):
            raise ValueError("الاتجاه غير صحيح.")
        if source not in ("",) + SOURCE_TYPES:
            raise ValueError("نوع المصدر غير صحيح.")
        search = (search or "").strip()
        if len(search) > 150:
            raise ValueError("نص البحث طويل.")

        def read(repository, ledgers):
            self._require_ledgers(ledgers, ledger)
            account = repository.account(ledger, account_id)
            if account is None:
                raise ValueError("الحساب غير موجود.")
            rows = repository.statement(
                ledger, account_id, datetime.combine(first, time.min) if first else None,
                datetime.combine(last + timedelta(days=1), time.min) if last else None,
                direction, source, search, (page - 1) * PAGE_SIZE)
            return account, repository.balance(ledger, account_id), rows
        account, balance, rows = self._read(actor, read)
        return {"account": {"key": account_key(ledger, account_id), "ledger": ledger, "name": account["name"],
                            "account_type": account["account_type"], "is_active": bool(account["is_active"]),
                            "balance": str(Decimal(str(balance)))},
                "rows": [self._row(r, ledger) for r in rows[:PAGE_SIZE]], "has_more": len(rows) > PAGE_SIZE,
                "page": page}

    @staticmethod
    def _row(r, ledger="CASH"):
        out = dict(r)
        for name in ("id", "reference_id", "invoice_id", "corrected_source_id", "operation_id", "customer_id"):
            out[name] = None if r.get(name) is None else str(r[name])
        out["amount_delta"] = Decimal(str(r["amount_delta"]))
        out["balance_after"] = Decimal(str(r["balance_after"]))
        labels = BANK_LABELS if ledger == "BANK" else CASH_LABELS
        out["label"] = labels.get(r.get("label_key"), CASH_LABELS["OTHER"])
        out.update(movement_context(r))
        return out

    def operation(self, operation_id, *, _expected_actor=None):
        """The compact immutable detail of one FO / TR operation (and its reversal, if any)."""
        actor = self._viewer(_expected_actor)
        if not str(operation_id or "").isdigit():
            raise ValueError("العملية غير موجودة.")
        def read(repository, ledgers):
            row = repository.operation(operation_id)
            if row is not None:                               # every side of the operation must be visible
                self._require_ledgers(ledgers, *(ledger for ledger in ALL_LEDGERS for end in ("from", "to")
                                                  if row.get(f"{end}_{ledger.lower()}_account_id") is not None))
            return row
        row = self._read(actor, read)
        if row is None:
            raise ValueError("العملية غير موجودة.")
        from services.correction_rules import correction_code
        reversal = row["reversal"]
        return {"operation_id": str(row["id"]), "code": operation_code(row["operation_type"], row["id"]),
                "operation_type": row["operation_type"], "type_text": CASH_LABELS[row["category"]] if row.get("category")
                else TYPE_TEXT[row["operation_type"]],
                "amount": str(row["amount"]), "from_name": row.get("from_name"), "to_name": row.get("to_name"),
                "reason": row.get("reason") or "", "actor_name": row.get("actor_name") or "",
                "occurred_at": row["occurred_at"],
                "legs": [{"leg_role": l["leg_role"], "amount": str(l["amount"])} for l in row["legs"]],
                "reversed_by": None if reversal is None else correction_code("FULL_REVERSAL", reversal["correction_id"]),
                "reversal_reason": None if reversal is None else reversal["reason"]}

    # ------------------------------------------------------------ manual operations
    def deposit(self, key, amount, reason, request_key, *, _expected_actor=None):
        return self._finalize(DEPOSIT, None, _account(key), _amount(amount),
                              _text(reason, required=True, label="سبب الإيداع"), request_key, _expected_actor)

    def withdraw(self, key, amount, reason, request_key, *, _expected_actor=None):
        return self._finalize(WITHDRAWAL, _account(key), None, _amount(amount),
                              _text(reason, required=True, label="سبب السحب"), request_key, _expected_actor)

    def transfer(self, from_key, to_key, amount, note, request_key, *, _expected_actor=None):
        source, destination = _account(from_key), _account(to_key)
        if source == destination:
            raise CorrectionError("SAME_ACCOUNT", "لا يمكن التحويل إلى نفس الحساب.")
        return self._finalize(TRANSFER, source, destination, _amount(amount),
                              _text(note, required=False, label="الملاحظة"), request_key, _expected_actor)

    # ------------------------------------------------------------ the cash screen
    def cash_view(self, start="", end="", label="", *, _expected_actor=None):
        """الخزنة: its authoritative balance and the period's IN / OUT movements by business name only.

        Default period: today (database date). Bank / wallet balances are never part of this view."""
        actor = self._viewer(_expected_actor)
        first = CustomerService._statement_date(start, "تاريخ البداية")
        last = CustomerService._statement_date(end, "تاريخ النهاية")
        if first and last and first > last:
            raise ValueError("تاريخ البداية بعد النهاية.")
        if label not in ("",) + tuple(CASH_LABELS):
            raise ValueError("نوع الحركة غير صحيح.")

        def read(repository, ledgers):
            self._require_ledgers(ledgers, "CASH")
            cash = next((a for a in repository.accounts(("CASH",)) if a["is_active"]), None)
            if cash is None:
                raise ValueError("لا توجد خزنة نشطة.")
            today = repository.db_now().date()
            end_day = last or today                               # «من» alone runs through today
            period = (first or end_day, end_day)
            if period[0] > period[1]:
                raise ValueError("تاريخ البداية بعد النهاية.")
            rows = repository.cash_flow("CASH", cash["account_id"], datetime.combine(period[0], time.min),
                                        datetime.combine(period[1] + timedelta(days=1), time.min), label, CASH_ROWS)
            banks = repository.operation_accounts() if OPERATE in actor.permissions else []
            return cash, period, rows, banks
        cash, period, rows, banks = self._read(actor, read)

        def side(sign):
            chosen = [r for r in rows if (r["amount_delta"] > 0) == (sign > 0)]
            return {"rows": [{"id": str(r["id"]), "label": CASH_LABELS.get(r["label_key"], CASH_LABELS["OTHER"]),
                              "amount": str(abs(Decimal(str(r["amount_delta"]))).quantize(Decimal("0.01"))),
                              "invoice_id": None if r.get("invoice_id") is None else str(r["invoice_id"]),
                              "operation_id": None if r.get("operation_id") is None else str(r["operation_id"]),
                              **movement_context(r)}
                             for r in chosen],
                    "total": str(abs(Decimal(str(chosen[0]["side_total"]))).quantize(Decimal("0.01"))) if chosen else "0.00",
                    "count": int(chosen[0]["side_count"]) if chosen else 0}
        return {"account": {"key": account_key("CASH", cash["account_id"]), "name": cash["name"],
                            "balance": str(Decimal(str(cash["balance"])))},
                "start": period[0].isoformat(), "end": period[1].isoformat(),
                "in": side(1), "out": side(-1),
                "labels": [{"code": k, "text": v} for k, v in CASH_LABELS.items()],
                "types": [{"code": k, "text": CASH_TYPE_TEXT[k], "directions": list(v)} for k, v in CASH_TYPES.items()],
                "bank_accounts": [{"key": account_key("BANK", b["id"]), "name": b["name"],
                                   "account_type": b["account_type"]} for b in banks],
                "can_operate": OPERATE in actor.permissions,
                "can_correct": "TRANSACTION_CORRECT" in actor.permissions}

    def cash_movement(self, cash_key, direction, kind, amount, note, request_key, bank_key="", *,
                      _expected_actor=None):
        """حركة نقدية جديدة: an operational cash movement. Bank deposit / withdrawal are atomic transfers;
        the other types are categorised manual operations (note optional). Customer سداد is NOT here: it is
        the existing Customer Account Settlement (CS)."""
        cash = _account(cash_key)
        if cash[0] != "CASH":
            raise CorrectionError("ACCOUNT_NOT_FOUND", "اختر الخزنة.")
        if kind not in CASH_TYPES or direction not in CASH_TYPES[kind]:
            raise CorrectionError("TYPE_INVALID", "نوع الحركة لا يناسب الاتجاه المختار.")
        value, text = _amount(amount), _text(note, required=False, label="الملاحظة")
        if kind in ("BANK_DEPOSIT", "BANK_WITHDRAWAL"):
            bank = _account(bank_key)
            if bank[0] != "BANK":
                raise CorrectionError("ACCOUNT_NOT_FOUND", "اختر حساب البنك / المحفظة.")
            source, destination = (cash, bank) if kind == "BANK_DEPOSIT" else (bank, cash)
            return self._finalize(TRANSFER, source, destination, value, text, request_key, _expected_actor)
        if direction == "IN":
            return self._finalize(DEPOSIT, None, cash, value, text, request_key, _expected_actor, category=kind)
        return self._finalize(WITHDRAWAL, cash, None, value, text, request_key, _expected_actor, category=kind)

    def _finalize(self, operation_type, source, destination, amount, reason, request_key, expected_actor,
                  category=None):
        session_actor = self._actor(expected_actor)
        if OPERATE not in session_actor.permissions:
            raise PermissionError("ليس لديك صلاحية تنفيذ العمليات المالية.")
        try:
            key = str(UUID(str(request_key)))
        except (ValueError, TypeError, AttributeError):
            raise CorrectionError("REQUEST_INVALID", "أعد فتح النموذج وحاول مرة أخرى.") from None
        digest = hashlib.sha256(json.dumps(
            [operation_type, category, list(source) if source else None,
             list(destination) if destination else None, str(amount), reason], ensure_ascii=False).encode()).hexdigest()
        with self._transaction() as connection:
            actor = self._authorization.require_permission(connection, session_actor, OPERATE)
            repository = self._repository(connection)
            accounts = {}
            for side in sorted({s for s in (source, destination) if s}):       # deterministic lock order
                row = repository.account(side[0], side[1], lock=True)
                if row is None:
                    raise CorrectionError("ACCOUNT_NOT_FOUND", "الحساب غير موجود.")
                accounts[side] = row
            prior = repository.by_request(key)
            if prior:
                if prior["request_hash"] != digest or int(prior["performed_by_user_id"]) != int(actor.user_id):
                    raise CorrectionError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
                return self._result(prior["operation_type"], prior["id"], prior["amount"], duplicate=True)
            for side, row in accounts.items():
                if not row["is_active"]:
                    raise CorrectionError("ACCOUNT_INACTIVE", f"الحساب «{row['name']}» غير نشط.")
            now = repository.db_now()
            operation_id = repository.insert_operation(
                operation_type=operation_type, key=key, digest=digest, amount=amount, source=source,
                destination=destination, reason=reason or None, actor=actor, now=now, category=category)
            code = operation_code(operation_type, operation_id)
            group = key if operation_type == TRANSFER else None
            if operation_type == TRANSFER:
                source_name, destination_name = accounts[source]["name"], accounts[destination]["name"]
                repository.post_leg(operation_id=operation_id, code=code, leg_role="OUT", ledger=source[0],
                                    account_id=source[1], amount=amount, type_code="ACCOUNT_TRANSFER_OUT",
                                    description=f"تحويل إلى {destination_name} — {code}", notes=reason or None,
                                    group=group, actor=actor, now=now)
                repository.post_leg(operation_id=operation_id, code=code, leg_role="IN", ledger=destination[0],
                                    account_id=destination[1], amount=amount, type_code="ACCOUNT_TRANSFER_IN",
                                    description=f"تحويل من {source_name} — {code}", notes=reason or None,
                                    group=group, actor=actor, now=now)
                text = f"تحويل {amount:,.2f} ج من {source_name} إلى {destination_name} — {code}"
            else:
                side, role = (destination, "IN") if operation_type == DEPOSIT else (source, "OUT")
                repository.post_leg(operation_id=operation_id, code=code, leg_role=role, ledger=side[0],
                                    account_id=side[1], amount=amount, type_code=operation_type,
                                    description=(CASH_LABELS[category] if category else TYPE_TEXT[operation_type])
                                    + (f" — {reason}" if reason else ""), notes=reason or None,
                                    group=None, actor=actor, now=now)
                text = (f"{CASH_LABELS[category] if category else TYPE_TEXT[operation_type]} {amount:,.2f} ج — "
                        f"{accounts[side]['name']} — {code}")
            repository.audit(actor=actor, event_type=operation_type, operation_id=operation_id, code=code,
                             description=text,
                             metadata={"amount": str(amount), "reason": reason, "category": category,
                                       "from": account_key(*source) if source else None,
                                       "to": account_key(*destination) if destination else None,
                                       "employee_id": actor.employee_id}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return self._result(operation_type, operation_id, amount)

    @staticmethod
    def _result(operation_type, operation_id, amount, duplicate=False):
        return {"operation_id": str(operation_id), "code": operation_code(operation_type, operation_id),
                "operation_type": operation_type, "amount": str(amount), "duplicate": duplicate}
