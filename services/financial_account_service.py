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
    "ON_CUSTOMER": "على العميل", "CUSTOMER_PAID_OUTSIDE": "سداد العميل (انستا / محفظة)", "EXCHANGE_REFUND": "استرداد فاتورة قديمة", "SALE": "بيع", "MIXED": "بيع مع كسر", "OLD_GOLD_PURCHASE": "شراء كسر", "EXCHANGE": "استبدال", "RETURN": "مرتجع",
    "CUSTOMER_PAYMENT": "سداد عميل", "CUSTOMER_PAYOUT": "دفع لعميل", "DEPOSIT": "عربون",
    "DEPOSIT_REFUND": "استرداد عربون", "SUPPLIER_PAYMENT": "دفعة مصنعيات مورد",
    "TRIP_CASH_ISSUE": "إخراج عهدة السوق", "TRIP_CASH_RETURN": "رجوع عهدة السوق", "REVERSAL": "إلغاء عملية",
    "CORRECTION": "تصحيح تسوية", "TRANSFER_IN": "سحب بنك", "TRANSFER_OUT": "إيداع بنك",
    "EXPENSE": "مصروفات", "SALARY": "مرتبات", "RENT": "إيجار", "REVENUE": "إيراد", "REPAIR": "مرمة",
    "UTILITIES": "كهرباء ومياه", "TRANSPORT": "نقل", "MAINTENANCE": "صيانة", "HOSPITALITY": "بوفيه وضيافة", "STAFF_ADVANCE": "سلف موظفين",
    "MANUAL_DEPOSIT": "إيداع يدوي", "MANUAL_WITHDRAWAL": "سحب يدوي", "OPENING_BALANCE": "رصيد افتتاحي",
    "OTHER": "حركة أخرى"}
# The same business names seen from a bank / wallet / InstaPay account (a transfer INTO the account is a deposit).
BANK_LABELS = {**CASH_LABELS, "TRANSFER_IN": "إيداع في الحساب", "TRANSFER_OUT": "سحب من الحساب"}
# The two transfers between the cash safe and a real bank / wallet account (BANK_DEPOSIT / BANK_WITHDRAWAL) and the cash directions they allow. The income / expense categories are NOT here:
# they live in the cash_categories table (migration 069, managed in Settings) and `category_table()` is their fixed fallback.
PRIVATE = "PRIVATE_ACCOUNT_MANAGE"      # the OWNER's right (090): the private account and its report
ADVANCE_CATEGORIES = ("STAFF_ADVANCE", "CUSTOMER_CUSTODY", "ADVANCE_REPAY", "BORROWED_CASH", "BORROW_REPAY",
                      "CUSTOMER_CASH_IN", "CUSTOMER_CASH_OUT", "PRIVATE_OUT", "PRIVATE_IN")      # recorded only through the advance flows (they need an owner of the money)
CASH_TYPES = {"BANK_WITHDRAWAL": ("IN",), "BANK_DEPOSIT": ("OUT",)}
CASH_TYPE_TEXT = {"BANK_DEPOSIT": "إيداع بنك", "BANK_WITHDRAWAL": "سحب بنك"}


def category_table(rows):
    """{code: {name, directions, active}} from the cash_categories rows; None (a database below 069) falls back to the fixed list of the code before the feature."""
    from services.cash_category_service import SEED
    if rows is None:
        rows = [{"code": c, "name": n, "direction": d, "is_active": 1} for c, n, d in SEED]
        rows += [{"code": "CUSTOMER_CUSTODY", "name": "عهدة مؤقتة لعميل", "direction": "OUT", "is_active": 1},       # 074
                 {"code": "ADVANCE_REPAY", "name": "سداد سلفة / عهدة", "direction": "IN", "is_active": 1},
                 {"code": "BORROWED_CASH", "name": "سلفة مستلفة", "direction": "IN", "is_active": 1},                    # 076
                 {"code": "BORROW_REPAY", "name": "سداد سلفة مستلفة", "direction": "OUT", "is_active": 1},
                 {"code": "CUSTOMER_CASH_IN", "name": "نقدية داخلة من عميل", "direction": "IN", "is_active": 1},          # 086
                 {"code": "CUSTOMER_CASH_OUT", "name": "نقدية خارجة لعميل", "direction": "OUT", "is_active": 1},
                 {"code": "PRIVATE_OUT", "name": "تحويل إلى الحساب الخاص", "direction": "OUT", "is_active": 1},               # 090
                 {"code": "PRIVATE_IN", "name": "تحويل من الحساب الخاص", "direction": "IN", "is_active": 1}]
    return {r["code"]: {"name": r["name"], "directions": ("IN", "OUT") if r["direction"] == "BOTH" else (r["direction"],), "active": bool(r["is_active"])} for r in rows}


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
            "customer_id": text("customer_id"), "kind": text("label_key"), "detail": text("operation_reason")}


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
        self._categories = category_table(None)

    # ------------------------------------------------------------ plumbing
    def _load_categories(self, repository):
        """The current categories, read inside the caller's transaction (a repository without the read keeps the fixed list)."""
        reader = getattr(repository, "cash_categories", None)
        self._categories = category_table(reader() if reader else None)

    def _label(self, code):
        """The employee-facing name of a movement kind: a category's current name, otherwise the fixed business names."""
        category = self._categories.get(code)
        return category["name"] if category else CASH_LABELS.get(code, CASH_LABELS["OTHER"])

    def _all_labels(self):
        return {**CASH_LABELS, **{code: c["name"] for code, c in self._categories.items()}}
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
                repository = self._repository(connection)
                self._load_categories(repository)
                raise _Discard(work(repository, ledgers))
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
                "rows": [self._row(r, ledger, self._all_labels()) for r in rows[:PAGE_SIZE]], "has_more": len(rows) > PAGE_SIZE,
                "page": page}

    @staticmethod
    def _row(r, ledger="CASH", names=None):
        """One statement row. `names` = {code: current Arabic name} (the categories table); without it the fixed business names are used."""
        out = dict(r)
        for name in ("id", "reference_id", "invoice_id", "corrected_source_id", "operation_id", "customer_id"):
            out[name] = None if r.get(name) is None else str(r[name])
        out["amount_delta"] = Decimal(str(r["amount_delta"]))
        out["balance_after"] = Decimal(str(r["balance_after"]))
        key = r.get("label_key")
        out["label"] = ((BANK_LABELS.get(key) if ledger == "BANK" and key in ("TRANSFER_IN", "TRANSFER_OUT") else None) or (names or {}).get(key) or CASH_LABELS.get(key, CASH_LABELS["OTHER"]))
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
                "operation_type": row["operation_type"], "type_text": self._label(row["category"]) if row.get("category")
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

        def doc_key(r):
            if Decimal(str(r["amount_delta"])) <= 0:
                return None
            if r.get("label_key") in ("SALE", "MIXED") and r.get("invoice_id") is not None:
                return ("INVOICE", str(r["invoice_id"]))
            if r.get("label_key") == "EXCHANGE" and r.get("reference_id") is not None:
                return ("INVOICE_LIFECYCLE", str(r["reference_id"]))
            return None
        def read(repository, ledgers):
            if label not in ("",) + tuple(self._all_labels()):
                raise ValueError("نوع الحركة غير صحيح.")
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
            account_rows = repository.operation_accounts()
            banks = account_rows if OPERATE in actor.permissions else []
            external = []                                          # bank / wallet movements: shown, never part of the safe's figures
            if "BANK" in ledgers:
                for account in account_rows:
                    for r in repository.cash_flow("BANK", account["id"], datetime.combine(period[0], time.min),
                                                  datetime.combine(period[1] + timedelta(days=1), time.min), label, CASH_ROWS):
                        if r.get("label_key") not in ("TRANSFER_IN", "TRANSFER_OUT"):      # the safe's own table already shows those
                            external.append({**r, "account_name": "محفظة" if account.get("account_type") == "MOBILE_WALLET" else "انستا", "external": True})       # the owner reads انستا (any bank) or محفظة, never the account name
            try:
                debts = repository.invoice_account_debits([k for k in (doc_key(r) for r in rows + external) if k])
            except Exception:                                      # display only: never break the screen
                debts = {}
            if label in ("", "EXCHANGE") and hasattr(repository, "exchanges_without_money"):
                try:                                               # display only: never break the screen
                    for x in repository.exchanges_without_money(datetime.combine(period[0], time.min), datetime.combine(period[1] + timedelta(days=1), time.min)):
                        old, account_part = Decimal(str(x["old_value"])), Decimal(str(x["account_signed"]))
                        if old + account_part <= 0:
                            continue
                        rows.append({"id": "X" + str(x["id"]), "amount_delta": old + account_part, "label_key": "EXCHANGE", "invoice_id": x["invoice_id"],
                                     "invoice_number": x["invoice_number"], "operation_id": None, "reference_id": x["id"], "occurred_at": x["occurred_at"],
                                     "source_reference": "EX" + str(x["id"]).zfill(6), "customer_name": x["customer_name"], "customer_id": x["customer_id"],
                                     "side_total": 0, "side_count": 0, "no_money": True})
                        debts[("INVOICE_LIFECYCLE", str(x["id"]))] = (old, account_part)
                except Exception:
                    pass
            return cash, period, rows, banks, external, debts
        cash, period, rows, banks, external, debts = self._read(actor, read)

        first_leg = {}                                         # a split payment (cash + انستا / محفظة): the rest is shown ONCE, on the first leg of the invoice
        for r in rows + external:
            if doc_key(r) and doc_key(r) not in first_leg:
                first_leg[doc_key(r)] = (bool(r.get("external")), r["id"])

        ZERO = Decimal("0")
        # Owner 2026-11-04: a customer's later payment into a bank / wallet account is shown in BOTH tables (paid in; cleared off the customer out),
        # so in - out stays the safe's real movement
        paid_outside = [r for r in external if r.get("label_key") == "CUSTOMER_PAYMENT" and Decimal(str(r["amount_delta"])) > 0]

        def add_in(r):
            """Owner 2026-11-03 (paper books): the incoming row carries the document's WHOLE value. A sale paid in part: + what stayed on the customer.
            An exchange: + the old pieces' value + the customer-account part (the old value comes back out below)."""
            key = doc_key(r)
            if r.get("no_money"):
                return ZERO
            if key and first_leg.get(key) == (bool(r.get("external")), r["id"]):
                value = debts.get(key, ZERO)
                return value[0] + value[1] if isinstance(value, tuple) else value
            return ZERO

        def out_parts(r):
            key = doc_key(r)
            if key and first_leg.get(key) == (bool(r.get("external")), r["id"]):
                value = debts.get(key, ZERO)
                if isinstance(value, tuple):
                    return [("EXCHANGE_REFUND", value[0])] * (value[0] > 0) + [("ON_CUSTOMER", value[1])] * (value[1] > 0)
                return [("ON_CUSTOMER", value)] * (value > 0)
            return []

        def side(sign):
            chosen = [r for r in rows if (r["amount_delta"] > 0) == (sign > 0)]
            outside = [r for r in external if (Decimal(str(r["amount_delta"])) > 0) == (sign > 0)]
            if sign > 0:
                chosen = [{**r, "amount_delta": Decimal(str(r["amount_delta"])) + add_in(r)} if add_in(r) else r for r in chosen]
                outside = [{**r, "amount_delta": Decimal(str(r["amount_delta"])) + add_in(r)} if add_in(r) else r for r in outside]
                extra, added = sum((add_in(r) for r in rows + external), ZERO) + sum((Decimal(str(r["amount_delta"])) for r in rows if r.get("no_money")), ZERO) + sum((Decimal(str(r["amount_delta"])) for r in paid_outside), ZERO), 0
            else:
                virtual = [{**r, "id": "D" + str(n) + "_" + str(r["id"]), "amount_delta": -amount, "label_key": label_key, "external": False, "side_total": 0, "side_count": 0}
                           for r in rows + external for n, (label_key, amount) in enumerate(out_parts(r))]
                virtual += [{**r, "id": "P" + str(r["id"]), "amount_delta": -Decimal(str(r["amount_delta"])), "label_key": "CUSTOMER_PAID_OUTSIDE",
                             "external": False, "side_total": 0, "side_count": 0} for r in paid_outside]
                chosen = chosen + virtual
                chosen.sort(key=lambda r: (r.get("occurred_at") or datetime.min, str(r["id"])), reverse=True)
                extra, added = sum((-v["amount_delta"] for v in virtual), ZERO), len(virtual)
            merged = sorted(chosen + outside, key=lambda r: (r.get("occurred_at") or datetime.min, str(r["id"]).zfill(20)), reverse=True) if outside else chosen   # the safe's own order is kept unless bank rows are merged in
            return {"rows": [{"id": ("B" if r.get("external") else "") + str(r["id"]), "label": self._label(r["label_key"]),
                              "amount": str(abs(Decimal(str(r["amount_delta"]))).quantize(Decimal("0.01"))),
                              "invoice_id": None if r.get("invoice_id") is None else str(r["invoice_id"]),
                              "operation_id": None if r.get("operation_id") is None else str(r["operation_id"]),
                              "external": bool(r.get("external")), "account": r.get("account_name") or "",
                              **movement_context(r)}
                             for r in merged],
                    "total": str((abs(Decimal(str(next((r["side_total"] for r in chosen if r.get("side_total")), 0)))) + extra).quantize(Decimal("0.01"))) if chosen or extra else "0.00",
                    "count": (int(next((r["side_count"] for r in chosen if r.get("side_count")), 0)) + added + (sum(1 for r in chosen if r.get("no_money")) if sign > 0 else 0)) if chosen else 0,
                    "bank_total": str(abs(sum((Decimal(str(r["amount_delta"])) for r in outside), Decimal("0"))).quantize(Decimal("0.01"))),
                    "bank_count": len(outside)}
        return {"account": {"key": account_key("CASH", cash["account_id"]), "name": cash["name"],
                            "balance": str(Decimal(str(cash["balance"])))},
                "start": period[0].isoformat(), "end": period[1].isoformat(),
                "in": side(1), "out": side(-1),
                "labels": [{"code": k, "text": v} for k, v in self._all_labels().items()],
                "types": [{"code": k, "text": c["name"], "directions": list(c["directions"])} for k, c in self._categories.items() if c["active"] and k not in ADVANCE_CATEGORIES]
                         + [{"code": k, "text": CASH_TYPE_TEXT[k], "directions": list(CASH_TYPES[k])} for k in ("BANK_DEPOSIT", "BANK_WITHDRAWAL")],
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
        if direction not in ("IN", "OUT") or not kind or (kind in CASH_TYPES and direction not in CASH_TYPES[kind]):
            raise CorrectionError("TYPE_INVALID", "نوع الحركة لا يناسب الاتجاه المختار.")
        if kind in ADVANCE_CATEGORIES:
            raise CorrectionError("USE_ADVANCE", "السلف والعهد المؤقتة تُسجّل من زر «سلفة / عهدة مؤقتة» باسم صاحبها.")
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

    # ------------------------------------------------------------ customer cash in / out (owner 2026-10-28)
    def customer_cash(self, direction, customer_id, account_key_value, amount, note, request_key, *, _expected_actor=None):
        """نقدية داخلة (IN) / خارجة (OUT) لعميل: the customer pays the shop (a debt payment or a deposit) or takes money from it, through any cash / bank / wallet
        account. One transaction: the manual deposit / withdrawal on that account AND one row in the customer's statement (IN lowers his balance toward للعميل,
        OUT raises it toward على العميل; no cap either way). Its only correction is the ordinary full reversal of the financial operation."""
        if direction not in ("IN", "OUT"):
            raise CorrectionError("TYPE_INVALID", "اختر نقدية داخلة أو خارجة.")
        account = _account(account_key_value)
        value, text = _amount(amount), _text(note, required=False, label="الملاحظة")
        try:
            cid = str(CustomerService._customer_id(customer_id))
        except Exception:
            raise CorrectionError("CUSTOMER_REQUIRED", "اختر عميلًا مسجلًا.") from None
        ledger_type = "CUSTOMER_CASH_IN" if direction == "IN" else "CUSTOMER_CASH_OUT"      # the customer-statement type
        category = ledger_type
        operation = DEPOSIT if direction == "IN" else WITHDRAWAL
        state = {}

        def before(repository):
            row = repository.lock_customer(cid)
            if row is None or not row["is_active"]:
                raise CorrectionError("CUSTOMER_REQUIRED", "العميل غير موجود أو غير نشط.")
            state["name"] = row["name"]

        def link(repository, operation_id, actor, now):
            code = operation_code(operation, operation_id)
            delta = -value if direction == "IN" else value
            current = repository.customer_balance(cid)
            label = "نقدية داخلة من العميل" if direction == "IN" else "نقدية خارجة للعميل"
            repository.add_customer_transaction(customer_id=cid, type_code=ledger_type, amount_delta=delta, actor=actor,
                                                operation_id=operation_id, code=code, description=f"{label} {code}", notes=text or None)
            state["after"] = current + delta
        result = self._finalize(operation, None if direction == "IN" else account, account if direction == "IN" else None,
                                value, text or None, request_key, _expected_actor, category=category, link=link,
                                extra=["CUSTOMER_CASH", cid], before=before)
        result["customer_name"] = state.get("name", "")
        if "after" in state:
            result["balance_after"] = str(state["after"])
        return result

    def customer_balances(self, search="", *, _expected_actor=None):
        """The two lists of the customer cash screen: customers who owe the shop and customers the shop owes (name / phone / code search)."""
        actor = self._actor(_expected_actor)
        if OPERATE not in actor.permissions:
            raise PermissionError("ليس لديك صلاحية تنفيذ العمليات المالية.")

        class _Done(Exception):
            pass
        try:
            with self._transaction() as connection:
                self._authorization.require_permission_read(connection, actor, OPERATE)
                raise _Done(self._repository(connection).customer_balances(search, CASH_ROWS))
        except _Done as done:
            owes, owed, settled = done.args[0]

        def row(r):
            return {"customer_id": str(r["customer_id"]), "code": r["customer_code"], "name": r["name"], "mobile": r.get("mobile") or "",
                    "balance": str(r["balance"]), "amount": str(abs(Decimal(str(r["balance"])))),
                    "last_at": "" if r.get("last_at") is None else r["last_at"].strftime("%Y-%m-%d %H:%M")}
        return {"owes": [row(r) for r in owes], "owed": [row(r) for r in owed], "settled": [row(r) for r in settled]}

    # ------------------------------------------------------------ temporary advances / custody (074)
    def open_advance(self, cash_key, kind, party_id, amount, note, request_key, *, _expected_actor=None):
        """سلفة موظف (EMPLOYEE) أو عهدة مؤقتة لعميل (CUSTOMER): cash leaves the safe on that person's name and stays owed until repaid. One transaction with the withdrawal."""
        cash = _account(cash_key)
        if cash[0] != "CASH":
            raise CorrectionError("ACCOUNT_NOT_FOUND", "اختر الخزنة.")
        if kind not in ("EMPLOYEE", "CUSTOMER"):
            raise CorrectionError("TYPE_INVALID", "اختر موظفًا أو عميلًا.")
        try:
            party = int(str(party_id))
        except (TypeError, ValueError):
            raise CorrectionError("PARTY_REQUIRED", "اختر الموظف أو العميل صاحب السلفة.") from None
        if party <= 0:
            raise CorrectionError("PARTY_REQUIRED", "اختر الموظف أو العميل صاحب السلفة.")
        value, text = _amount(amount), _text(note, required=False, label="الملاحظة")
        category = "STAFF_ADVANCE" if kind == "EMPLOYEE" else "CUSTOMER_CUSTODY"
        holder = {"name": ""}

        def link(repository, operation_id, actor, now):
            row = repository.employee(party) if kind == "EMPLOYEE" else repository.customer(party)
            if row is None or not row["is_active"]:
                raise CorrectionError("PARTY_NOT_FOUND", "الموظف غير موجود أو غير نشط." if kind == "EMPLOYEE" else "العميل غير موجود أو غير نشط.")
            repository.insert_advance(kind=kind, employee_id=party if kind == "EMPLOYEE" else None, customer_id=party if kind == "CUSTOMER" else None,
                                      amount=value, operation_id=operation_id, note=text or None, actor=actor, now=now)
            holder["name"] = row["name"]
        result = self._finalize(WITHDRAWAL, cash, None, value, text or ("سلفة موظف" if kind == "EMPLOYEE" else "عهدة مؤقتة لعميل"), request_key, _expected_actor,
                                category=category, link=link, extra=[kind, party])
        result["party_name"] = holder["name"]
        return result

    def repay_advance(self, advance_id, account_key_value, amount, note, request_key, *, _expected_actor=None):
        """سداد سلفة / عهدة: for an advance the shop LENT the money comes back into the chosen cash / bank account; for cash the shop BORROWED (076) it goes OUT of that account.
        Partial repayments are allowed, never above what is still owed."""
        account = _account(account_key_value)
        value, text = _amount(amount), _text(note, required=False, label="الملاحظة")
        try:
            advance_number = int(str(advance_id))
        except (TypeError, ValueError):
            raise CorrectionError("ADVANCE_NOT_FOUND", "السلفة غير موجودة.") from None
        session_actor = self._actor(_expected_actor)
        with self._transaction() as connection:                                  # which way the money goes depends on the advance (a plain read; the link below re-checks it under the lock)
            first = self._repository(connection).advance(advance_number)
        if first is None:
            raise CorrectionError("ADVANCE_NOT_FOUND", "السلفة غير موجودة.")
        direction = first.get("direction") or "LEND"

        def link(repository, operation_id, actor, now):
            row = repository.advance(advance_number, lock=True)
            if row is None:
                raise CorrectionError("ADVANCE_NOT_FOUND", "السلفة غير موجودة.")
            if (row.get("direction") or "LEND") != direction:
                raise CorrectionError("ADVANCE_CHANGED", "تغيّرت السلفة؛ أعد المحاولة.")
            if row["voided"]:
                raise CorrectionError("ADVANCE_REVERSED", "هذه السلفة ملغاة بالعكس.")
            owed = Decimal(str(row["amount"])) - Decimal(str(row["repaid"]))
            if value > owed:
                raise CorrectionError("REPAY_EXCEEDS", f"المتبقي على السلفة {owed:,.2f} ج فقط.")
            repository.insert_repayment(advance_id=advance_number, amount=value, operation_id=operation_id, actor=actor, now=now)
        if direction == "BORROW":
            return self._finalize(WITHDRAWAL, account, None, value, text or "سداد سلفة مستلفة", request_key, session_actor,
                                  category="BORROW_REPAY", link=link, extra=["REPAY", advance_number])
        return self._finalize(DEPOSIT, None, account, value, text or "سداد سلفة / عهدة", request_key, session_actor,
                              category="ADVANCE_REPAY", link=link, extra=["REPAY", advance_number])

    def open_borrow(self, account_key_value, kind, party_id, lender_name, amount, note, request_key, *, _expected_actor=None):
        """سلفة مستلفة: the shop takes cash from an employee (EMPLOYEE), a customer (CUSTOMER) or an outside person (THIRD_PARTY, typed name) and owes it back, no interest. The money enters the
        chosen cash / bank account (one manual deposit) and the debt is tied to the lender in the SAME transaction."""
        account = _account(account_key_value)
        if kind not in ("EMPLOYEE", "CUSTOMER", "THIRD_PARTY"):
            raise CorrectionError("TYPE_INVALID", "اختر موظفًا أو عميلًا أو اكتب اسم الشخص.")
        name = (lender_name or "").strip()
        party = None
        if kind == "THIRD_PARTY":
            if not name:
                raise CorrectionError("PARTY_REQUIRED", "اكتب اسم اللي استلفنا منه.")
            if len(name) > 100:
                raise CorrectionError("PARTY_REQUIRED", "الاسم طويل جداً.")
        else:
            try:
                party = int(str(party_id))
            except (TypeError, ValueError):
                raise CorrectionError("PARTY_REQUIRED", "اختر الموظف أو العميل اللي استلفنا منه.") from None
            if party <= 0:
                raise CorrectionError("PARTY_REQUIRED", "اختر الموظف أو العميل اللي استلفنا منه.")
        value, text = _amount(amount), _text(note, required=False, label="الملاحظة")
        holder = {"name": name}

        def link(repository, operation_id, actor, now):
            if kind != "THIRD_PARTY":
                row = repository.employee(party) if kind == "EMPLOYEE" else repository.customer(party)
                if row is None or not row["is_active"]:
                    raise CorrectionError("PARTY_NOT_FOUND", "الموظف غير موجود أو غير نشط." if kind == "EMPLOYEE" else "العميل غير موجود أو غير نشط.")
                holder["name"] = row["name"]
            repository.insert_advance(kind=kind, direction="BORROW", employee_id=party if kind == "EMPLOYEE" else None, customer_id=party if kind == "CUSTOMER" else None,
                                      lender_name=name if kind == "THIRD_PARTY" else None, amount=value, operation_id=operation_id, note=text or None, actor=actor, now=now)
        result = self._finalize(DEPOSIT, None, account, value, text or "سلفة مستلفة", request_key, _expected_actor,
                                category="BORROWED_CASH", link=link, extra=["BORROW", kind, party, name])
        result["party_name"] = holder["name"]
        return result

    def advances(self, *, _expected_actor=None):
        """Every advance with who owes it, what was repaid and what is left, plus the employees an advance can be given to."""
        actor = self._actor(_expected_actor)
        if OPERATE not in actor.permissions and "MANAGEMENT_REVIEW" not in actor.permissions:
            raise PermissionError("ليس لديك صلاحية عرض السلف.")
        with self._transaction() as connection:
            repository = self._repository(connection)
            rows = repository.advances() or []
            employees = [{"id": str(e["id"]), "name": e["name"]} for e in repository.active_employees()]
        result = []
        for r in rows:
            cent = Decimal("0.01")
            amount, repaid = Decimal(str(r["amount"])).quantize(cent), Decimal(str(r["repaid"])).quantize(cent)
            voided = bool(r["voided"])
            borrowed = (r.get("direction") or "LEND") == "BORROW"
            result.append({"id": str(r["id"]), "code": f"SA{int(r['id']):06d}", "kind": r["kind"], "direction": "BORROW" if borrowed else "LEND",
                           "kind_text": (("سلفة مستلفة من موظف" if r["kind"] == "EMPLOYEE" else "سلفة مستلفة من عميل" if r["kind"] == "CUSTOMER" else "سلفة مستلفة")
                                         if borrowed else ("سلفة موظف" if r["kind"] == "EMPLOYEE" else "عهدة عميل")),
                           "party": r["party_name"] or "", "amount": str(amount), "repaid": str(repaid),
                           "owed": str(Decimal("0.00") if voided else amount - repaid), "voided": voided,
                           "date": r["created_at"].strftime("%Y-%m-%d") if r["created_at"] else "", "note": r["note"] or "",
                           "operation": operation_code(DEPOSIT if borrowed else WITHDRAWAL, r["operation_id"])})
        result.sort(key=lambda r: (r["voided"], Decimal(r["owed"]) == 0, -int(r["id"])))
        return {"advances": result, "employees": employees}

    def attach_existing_advance(self, operation_id, employee_id, *, _expected_actor=None):
        """Links an OLD manual «سلف موظفين» withdrawal (recorded before 074, no employee) to its employee. Owner only; never twice, never a reversed or other-category operation."""
        session_actor = self._actor(_expected_actor)
        with self._transaction() as connection:
            actor = self._authorization.require_permission(connection, session_actor, "SETTINGS_MANAGE")
            repository = self._repository(connection)
            operation = repository.operation_category(operation_id)
            if operation is None or operation["operation_type"] != WITHDRAWAL or operation["category"] != "STAFF_ADVANCE":
                raise CorrectionError("TYPE_INVALID", "هذه ليست حركة سلف موظفين.")
            if repository.advance_by_operation(operation_id) is not None:
                raise CorrectionError("ALREADY_LINKED", "هذه السلفة مربوطة بالفعل.")
            row = repository.operation(operation_id)
            if row["reversal"] is not None:
                raise CorrectionError("ADVANCE_REVERSED", "هذه الحركة معكوسة.")
            employee = repository.employee(employee_id)
            if employee is None:
                raise CorrectionError("PARTY_NOT_FOUND", "الموظف غير موجود.")
            now = repository.db_now()
            repository.insert_advance(kind="EMPLOYEE", employee_id=int(employee_id), customer_id=None, amount=Decimal(str(row["amount"])),
                                      operation_id=operation_id, note=row.get("reason"), actor=actor, now=now)
            repository.audit(actor=actor, event_type="ADVANCE_ATTACHED", operation_id=operation_id, code=operation_code(WITHDRAWAL, operation_id),
                             description=f"ربط سلفة قديمة بالموظف {employee['name']}", metadata={"employee_id": int(employee_id)}, now=now)
        return {"operation_id": str(operation_id), "employee": employee["name"]}

    # ------------------------------------------------------------ the owner's private account (090)
    def private_transfer(self, account_key_value, direction, amount, note, request_key, *, _expected_actor=None):
        """تحويل خاص: TO = from a bank / wallet account to the private account, FROM = back from it. One manual operation on the bank account
        (PRIVATE_OUT / PRIVATE_IN) and its private-account entry, in one transaction. OWNER only (PRIVATE_ACCOUNT_MANAGE, rechecked)."""
        account = _account(account_key_value)
        if account[0] != "BANK":
            raise CorrectionError("ACCOUNT_INVALID", "الحساب الخاص يتعامل مع حسابات البنوك والمحافظ فقط.")
        if direction not in ("TO", "FROM"):
            raise CorrectionError("TYPE_INVALID", "اختر اتجاه التحويل.")
        value, text = _amount(amount), _text(note, required=False, label="الملاحظة")

        def link(repository, operation_id, actor, now):
            repository.insert_private_entry(direction="TO_PRIVATE" if direction == "TO" else "FROM_PRIVATE", amount=value,
                                            operation_id=operation_id, note=text or None, actor=actor, now=now)
        if direction == "TO":
            return self._finalize(WITHDRAWAL, account, None, value, text or "تحويل إلى الحساب الخاص", request_key, _expected_actor,
                                  category="PRIVATE_OUT", link=link, extra=["PRIVATE", direction], also_requires=PRIVATE)
        return self._finalize(DEPOSIT, None, account, value, text or "تحويل من الحساب الخاص", request_key, _expected_actor,
                              category="PRIVATE_IN", link=link, extra=["PRIVATE", direction], also_requires=PRIVATE)

    def private_account(self, *, _expected_actor=None):
        """The private account's balance and every entry with its running balance (a reversed operation is listed apart and not counted). OWNER only."""
        session_actor = self._actor(_expected_actor)
        if PRIVATE not in session_actor.permissions:
            raise PermissionError("الحساب الخاص للمالك فقط.")
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, session_actor, PRIVATE)
            rows = self._repository(connection).private_entries()
        if rows is None:
            return {"available": False, "balance": "0.00", "total_in": "0.00", "total_out": "0.00", "entries": []}
        balance = total_in = total_out = Decimal("0.00")
        entries = []
        for r in rows:
            amount, voided = Decimal(str(r["amount"])).quantize(Decimal("0.01")), bool(r["voided"])
            to_private = r["direction"] == "TO_PRIVATE"
            if not voided:
                balance += amount if to_private else -amount
                total_in, total_out = (total_in + amount, total_out) if to_private else (total_in, total_out + amount)
            entries.append({"id": str(r["id"]), "date": r["created_at"].strftime("%Y-%m-%d %H:%M") if r["created_at"] else "",
                            "direction": r["direction"], "text": "إلى الحساب الخاص" if to_private else "من الحساب الخاص",
                            "account": r["account_name"] or "", "amount": str(amount), "note": r["note"] or "", "voided": voided,
                            "balance": str(balance), "operation": operation_code(WITHDRAWAL if to_private else DEPOSIT, r["operation_id"])})
        entries.reverse()
        return {"available": True, "balance": str(balance), "total_in": str(total_in), "total_out": str(total_out), "entries": entries}

    def _finalize(self, operation_type, source, destination, amount, reason, request_key, expected_actor,
                  category=None, link=None, extra=None, before=None, also_requires=None):
        session_actor = self._actor(expected_actor)
        if OPERATE not in session_actor.permissions:
            raise PermissionError("ليس لديك صلاحية تنفيذ العمليات المالية.")
        try:
            key = str(UUID(str(request_key)))
        except (ValueError, TypeError, AttributeError):
            raise CorrectionError("REQUEST_INVALID", "أعد فتح النموذج وحاول مرة أخرى.") from None
        digest = hashlib.sha256(json.dumps(
            [operation_type, category, list(source) if source else None,
             list(destination) if destination else None, str(amount), reason] + ([extra] if extra is not None else []), ensure_ascii=False).encode()).hexdigest()
        with self._transaction() as connection:
            actor = self._authorization.require_permission(connection, session_actor, OPERATE)
            if also_requires:                                                  # e.g. the owner's private-account right, rechecked in this transaction
                self._authorization.require_permission(connection, session_actor, also_requires)
            repository = self._repository(connection)
            if before is not None:                                             # e.g. the customer lock, taken BEFORE the account locks like every customer-money flow
                before(repository)
            prior = repository.by_request(key)                                 # an exact replay is answered BEFORE any state refusal (the category may have been disabled since)
            if prior:
                if prior["request_hash"] != digest or int(prior["performed_by_user_id"]) != int(actor.user_id):
                    raise CorrectionError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
                return self._result(prior["operation_type"], prior["id"], prior["amount"], duplicate=True)
            if category:                                                       # the category must exist, be active and fit the direction of the operation (decided from the table)
                self._load_categories(repository)
                chosen = self._categories.get(category)
                wanted = "IN" if operation_type == DEPOSIT else "OUT"
                owned = category in ADVANCE_CATEGORIES                          # recorded only through its own flow (customer cash, advances, private account): disabling it in Settings must not break that flow
                if chosen is None or wanted not in chosen["directions"] or (not chosen["active"] and not owned):
                    raise CorrectionError("TYPE_INVALID", "نوع الحركة لا يناسب الاتجاه المختار.")
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
            if link is not None:                                               # the advance row of this operation, in the SAME transaction (any refusal rolls the money back too)
                link(repository, operation_id, actor, now)
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
                                    description=(self._label(category) if category else TYPE_TEXT[operation_type])
                                    + (f" — {reason}" if reason else ""), notes=reason or None,
                                    group=None, actor=actor, now=now)
                text = (f"{self._label(category) if category else TYPE_TEXT[operation_type]} {amount:,.2f} ج — "
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
