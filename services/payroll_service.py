"""المرتبات (migration 104): a section of its own under مراجعات المدير, outside the expenses. Per employee a monthly salary (rates by effective month), bonuses and penalties of a month (each
with a reason; a bonus / penalty not yet paid can be voided) and ONE slip per month: the NET = salary + bonuses - penalties - advances deducted is paid by ONE real cash / bank withdrawal
(category SALARY, through FinancialAccountService so the treasury, the account balance and the reversal are the ordinary ones). The slip, the adjustments it settles and the advance repayments
are written in the SAME transaction as the money; reversing the salary operation voids the slip and brings the advances back. The right PAYROLL_MANAGE (owner) is rechecked in the transaction;
the actor is the AppSession user. A retry with the same request key returns the first result."""
import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from repositories.financial_account_repository import FinancialAccountRepository, account_key, parse_account_key
from repositories.payroll_repository import PayrollRepository
from services.authorization_service import AuthorizationService
from services.correction_service import CorrectionError
from services.financial_account_service import FinancialAccountService, WITHDRAWAL
from services.invoice_service import invoice_transaction

PERMISSION = "PAYROLL_MANAGE"
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
MAX_AMOUNT = Decimal("100000000.00")
KINDS = {"BONUS": "حافز", "PENALTY": "جزاء"}


class PayrollError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _money(value, what, *, allow_zero=False):
    try:
        amount = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError, AttributeError):
        raise PayrollError("AMOUNT_INVALID", f"اكتب {what} بالأرقام.") from None
    if not amount.is_finite() or amount > MAX_AMOUNT or amount < 0 or (amount == 0 and not allow_zero):
        raise PayrollError("AMOUNT_INVALID", f"{what} لازم يكون " + ("صفر أو أكثر." if allow_zero else "أكبر من صفر."))
    if amount != amount.quantize(CENT, rounding=ROUND_HALF_UP):
        raise PayrollError("AMOUNT_INVALID", f"{what} بخانتين عشريتين على الأكثر.")
    return amount.quantize(CENT)


def month_start(value, today):
    """'YYYY-MM' (or a date) -> the first day of that month. A month after the current one is refused."""
    text = str(value or "").strip()
    try:
        if len(text) >= 7:
            year, month = int(text[:4]), int(text[5:7])
            result = date(year, month, 1)
        else:
            raise ValueError
    except (ValueError, TypeError):
        raise PayrollError("MONTH_INVALID", "اختر الشهر بصيغة السنة-الشهر (مثال 2026-11).") from None
    if result > today.replace(day=1):
        raise PayrollError("MONTH_FUTURE", "مينفعش تسجّل مرتب لشهر لسه ماجاش.")
    return result


def _text(value, label, *, required, limit=300):
    text = str(value or "").strip()
    if required and not text:
        raise PayrollError("REASON_REQUIRED", f"اكتب {label}.")
    if len(text) > limit:
        raise PayrollError("TEXT_TOO_LONG", f"{label} {limit} حرف على الأكثر.")
    return text


def figures(repository, employee_id, month, lock=False):
    """What the slip of the month is made of, read from the database (the screen never decides it)."""
    rate = repository.salary_rate(employee_id, month)
    base = Decimal(str(rate["monthly_amount"])).quantize(CENT) if rate else None
    open_rows = repository.open_adjustments(employee_id, month, lock=lock)
    bonuses = sum((Decimal(str(r["amount"])) for r in open_rows if r["kind"] == "BONUS"), ZERO).quantize(CENT)
    penalties = sum((Decimal(str(r["amount"])) for r in open_rows if r["kind"] == "PENALTY"), ZERO).quantize(CENT)
    advances = repository.open_advances(employee_id, lock=lock)
    owed = sum((Decimal(str(a["amount"])) - Decimal(str(a["repaid"])) for a in advances), ZERO).quantize(CENT)
    return {"base": base, "bonuses": bonuses, "penalties": penalties, "owed": owed, "advances": advances, "adjustment_ids": [int(r["id"]) for r in open_rows]}


def default_deduction(parts):
    """Suggested deduction: every advance owed, but never so much that nothing is left to pay."""
    if parts["base"] is None:
        return ZERO
    room = parts["base"] + parts["bonuses"] - parts["penalties"] - CENT
    return max(ZERO, min(parts["owed"], room)).quantize(CENT)


class PayrollService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=PayrollRepository, authorization=None, finance=None, today=date.today):
        self._session = session
        self._transaction = transaction_factory
        self._repository = repository
        self._authorization = authorization or AuthorizationService(session)
        self._finance = finance or FinancialAccountService(session, transaction_factory=transaction_factory)
        self._today = today

    def can_manage(self):
        actor = self._session.current_actor()
        return actor is not None and PERMISSION in actor.permissions

    def _actor(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if PERMISSION not in actor.permissions:
            raise PermissionError("ليس لديك صلاحية المرتبات.")
        return actor

    @staticmethod
    def _ready(repository):
        if not repository.has_schema():
            raise PayrollError("SCHEMA_OUTDATED", "قاعدة البيانات تحتاج تحديث 104 قبل استخدام المرتبات.")

    # ------------------------------------------------------------ read
    def overview(self, month="", *, _expected_actor=None):
        actor = self._actor(_expected_actor)
        today = self._today()
        start = month_start(month or today.strftime("%Y-%m"), today)
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, PERMISSION)
            repository = self._repository(connection)
            self._ready(repository)
            slips = {}
            for s in repository.slips(start):
                if not s["voided"]:
                    slips.setdefault(int(s["employee_id"]), s)
            rows = []
            for e in repository.active_employees():
                parts = figures(repository, e["id"], start)
                slip = slips.get(int(e["id"]))
                suggested = default_deduction(parts)
                rows.append({"id": str(e["id"]), "name": e["name"], "base": None if parts["base"] is None else str(parts["base"]), "bonuses": str(parts["bonuses"]),
                             "penalties": str(parts["penalties"]), "owed": str(parts["owed"]), "default_deduction": str(suggested),
                             "net_preview": None if parts["base"] is None else str(parts["base"] + parts["bonuses"] - parts["penalties"] - suggested),
                             "status": "PAID" if slip else ("NO_SALARY" if parts["base"] is None else "READY"),
                             "paid": None if not slip else {"net": str(Decimal(str(slip["net_amount"])).quantize(CENT)), "base": str(Decimal(str(slip["base_salary"])).quantize(CENT)),
                                                             "bonuses": str(Decimal(str(slip["bonuses"])).quantize(CENT)), "penalties": str(Decimal(str(slip["penalties"])).quantize(CENT)),
                                                             "advances": str(Decimal(str(slip["advances_deducted"])).quantize(CENT)), "date": str(slip["created_at"])[:10]}})
            adjustments = [{"id": str(a["id"]), "employee": a["employee_name"], "kind": a["kind"], "kind_text": KINDS[a["kind"]], "amount": str(Decimal(str(a["amount"])).quantize(CENT)),
                            "reason": a["reason"], "state": "VOIDED" if a["voided_at"] else ("PAID" if a["slip_id"] and not a["slip_voided"] else "OPEN"), "void_reason": a["void_reason"] or "",
                            "date": str(a["created_at"])[:10]} for a in repository.adjustments(start)]
            accounts = [{"key": account_key(r["ledger"], r["account_id"]), "name": ("الخزنة: " if r["ledger"] == "CASH" else "") + r["name"], "balance": str(Decimal(str(r["balance"])).quantize(CENT))}
                        for r in self._finance._repository(connection).accounts(("CASH", "BANK")) if r["is_active"]]
        paid_total = sum((Decimal(r["paid"]["net"]) for r in rows if r["paid"]), ZERO)
        due_total = sum((Decimal(r["net_preview"]) for r in rows if r["status"] == "READY"), ZERO)
        return {"month": start.strftime("%Y-%m"), "employees": rows, "adjustments": adjustments, "accounts": accounts, "paid_total": str(paid_total), "due_total": str(due_total)}

    # ------------------------------------------------------------ salaries, bonuses, penalties
    def set_salary(self, employee_id, monthly_amount, month, *, _expected_actor=None):
        """The monthly salary from `month` on (the same month again corrects it). A slip already paid keeps the salary it was paid with."""
        session_actor = self._actor(_expected_actor)
        start = month_start(month or self._today().strftime("%Y-%m"), self._today())
        amount = _money(monthly_amount, "المرتب الشهري", allow_zero=True)
        employee_number = _positive_int(employee_id, "اختر الموظف.")
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            self._ready(repository)
            employee = repository.lock_employee(employee_number)
            if employee is None or not employee["is_active"]:
                raise PayrollError("EMPLOYEE_NOT_FOUND", "الموظف غير موجود أو غير نشط.")
            repository.set_rate(employee_number, start, amount, actor.user_id)
            repository.audit(user_id=actor.user_id, event_type="PAYROLL_SALARY_SET", entity_id=employee_number, reference=f"EMP{employee_number}",
                             description=f"مرتب {employee['name']} الشهري {amount:,.2f} ج من {start.strftime('%Y-%m')}", metadata={"amount": str(amount), "from": start.isoformat()})
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"employee_id": str(employee_number), "amount": str(amount)}

    def add_adjustment(self, employee_id, month, kind, amount, reason, *, _expected_actor=None):
        session_actor = self._actor(_expected_actor)
        start = month_start(month or self._today().strftime("%Y-%m"), self._today())
        if kind not in KINDS:
            raise PayrollError("KIND_INVALID", "اختر حافز أو جزاء.")
        value = _money(amount, "المبلغ")
        why = _text(reason, "السبب", required=True)
        employee_number = _positive_int(employee_id, "اختر الموظف.")
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            self._ready(repository)
            employee = repository.lock_employee(employee_number)
            if employee is None or not employee["is_active"]:
                raise PayrollError("EMPLOYEE_NOT_FOUND", "الموظف غير موجود أو غير نشط.")
            if repository.effective_slip(employee_number, start):
                raise PayrollError("ALREADY_PAID", "مرتب الشهر ده اتصرف لهذا الموظف: الحافز أو الجزاء لازم يتسجل على شهر لسه ما اتصرفش.")
            new_id = repository.insert_adjustment(employee_id=employee_number, month=start, kind=kind, amount=value, reason=why, user_id=actor.user_id)
            repository.audit(user_id=actor.user_id, event_type="PAYROLL_ADJUSTMENT_ADDED", entity_id=new_id, reference=f"PA{int(new_id):06d}",
                             description=f"{KINDS[kind]} {value:,.2f} ج على {employee['name']} عن {start.strftime('%Y-%m')}: {why}", metadata={"kind": kind, "amount": str(value), "reason": why})
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"adjustment_id": new_id}

    def void_adjustment(self, adjustment_id, reason, *, _expected_actor=None):
        """Cancels a bonus / penalty that is not on a paid slip (the row stays, marked voided with its reason)."""
        session_actor = self._actor(_expected_actor)
        why = _text(reason, "سبب الإلغاء", required=True)
        number = _positive_int(adjustment_id, "الحافز أو الجزاء غير موجود.")
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            self._ready(repository)
            row = repository.adjustment(number, lock=True)
            if row is None:
                raise PayrollError("NOT_FOUND", "الحافز أو الجزاء غير موجود.")
            if row["voided_at"]:
                raise PayrollError("ALREADY_VOIDED", "ده ملغي بالفعل.")
            if row["slip_id"]:
                raise PayrollError("ALREADY_PAID", "ده اتحسب في مرتب اتصرف: لازم تعكس عملية الصرف الأول من حركة الخزنة.")
            if not repository.void_adjustment(number, why, actor.user_id):
                raise PayrollError("CHANGED", "حالته اتغيّرت؛ أعد المحاولة.")
            repository.audit(user_id=actor.user_id, event_type="PAYROLL_ADJUSTMENT_VOIDED", entity_id=number, reference=f"PA{number:06d}",
                             description=f"إلغاء {KINDS[row['kind']]} {Decimal(str(row['amount'])):,.2f} ج", metadata={"reason": why})
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"adjustment_id": str(number)}

    # ------------------------------------------------------------ paying
    def pay(self, employee_id, month, account_key_value, advance_deduction, note, request_key, *, _expected_actor=None):
        """Pays the employee's slip of the month: ONE withdrawal of the net from the chosen cash / bank account, the slip, the settled adjustments and the advance repayments, atomically."""
        session_actor = self._actor(_expected_actor)
        today = self._today()
        start = month_start(month or today.strftime("%Y-%m"), today)
        employee_number = _positive_int(employee_id, "اختر الموظف.")
        deduction = _money(advance_deduction or "0", "خصم السلف", allow_zero=True)
        text = _text(note, "الملاحظة", required=False, limit=500)
        account = parse_account_key(account_key_value)
        if account is None:
            raise PayrollError("ACCOUNT_REQUIRED", "اختر الحساب اللي هيتصرف منه المرتب.")
        try:
            key = str(uuid.UUID(str(request_key)))
        except (ValueError, TypeError, AttributeError):
            raise PayrollError("REQUEST_INVALID", "أعد فتح النموذج وحاول مرة أخرى.") from None
        with self._transaction() as connection:                                             # a plain read: the amount and an exact replay (the locked re-check happens below)
            self._authorization.require_permission_read(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            self._ready(repository)
            prior = self._finance._repository(connection).by_request(key)
            if prior is not None:
                if int(prior["performed_by_user_id"]) != int(session_actor.user_id):
                    raise CorrectionError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
                return {"operation_id": str(prior["id"]), "amount": str(Decimal(str(prior["amount"])).quantize(CENT)), "duplicate": True}
            employee = repository.lock_employee(employee_number)
            if employee is None or not employee["is_active"]:
                raise PayrollError("EMPLOYEE_NOT_FOUND", "الموظف غير موجود أو غير نشط.")
            if repository.effective_slip(employee_number, start):
                raise PayrollError("ALREADY_PAID", "مرتب الشهر ده اتصرف لهذا الموظف قبل كده.")
            parts = figures(repository, employee_number, start)
        net = self._net(parts, deduction)
        holder = {}

        def before(fin_repository):
            payroll = self._repository(fin_repository.connection)
            locked = payroll.lock_employee(employee_number)
            if locked is None or not locked["is_active"]:
                raise PayrollError("EMPLOYEE_NOT_FOUND", "الموظف غير موجود أو غير نشط.")
            if payroll.effective_slip(employee_number, start):
                raise PayrollError("ALREADY_PAID", "مرتب الشهر ده اتصرف لهذا الموظف قبل كده.")
            now_parts = figures(payroll, employee_number, start, lock=True)
            if self._net(now_parts, deduction) != net or now_parts["base"] != parts["base"]:
                raise PayrollError("CHANGED", "الأرقام اتغيّرت (حافز أو جزاء أو سلفة)؛ افتح الصرف تاني.")
            holder["parts"], holder["name"] = now_parts, locked["name"]

        def link(fin_repository, operation_id, actor, now):
            payroll = self._repository(fin_repository.connection)
            now_parts = holder["parts"]
            slip_id = payroll.insert_slip(employee_id=employee_number, month=start, base=now_parts["base"], bonuses=now_parts["bonuses"], penalties=now_parts["penalties"],
                                          advances=deduction, net=net, operation_id=operation_id, note=text or None, user_id=actor.user_id)
            payroll.attach_adjustments(now_parts["adjustment_ids"], slip_id)
            left = deduction
            for advance in now_parts["advances"]:
                if left <= 0:
                    break
                part = min(left, (Decimal(str(advance["amount"])) - Decimal(str(advance["repaid"]))).quantize(CENT))
                payroll.insert_repayment(advance_id=advance["id"], amount=part, operation_id=operation_id, user_id=actor.user_id)
                left -= part
            payroll.audit(user_id=actor.user_id, event_type="PAYROLL_PAID", entity_id=slip_id, reference=f"PS{int(slip_id):06d}",
                          description=f"صرف مرتب {holder['name']} عن {start.strftime('%Y-%m')}: صافي {net:,.2f} ج",
                          metadata={"net": str(net), "base": str(now_parts["base"]), "bonuses": str(now_parts["bonuses"]), "penalties": str(now_parts["penalties"]),
                                    "advances_deducted": str(deduction), "operation_id": str(operation_id)})
            holder["slip_id"] = slip_id
        result = self._finance._finalize(WITHDRAWAL, account, None, net, text or f"مرتب {employee['name']} عن {start.strftime('%Y-%m')}", key, session_actor, category="SALARY",
                                         link=link, before=before, also_requires=PERMISSION, extra=["PAYROLL", employee_number, start.isoformat(), str(deduction)])
        return {"operation_id": result["operation_id"], "amount": str(net), "slip_id": holder.get("slip_id"), "duplicate": bool(result.get("duplicate"))}

    @staticmethod
    def _net(parts, deduction):
        if parts["base"] is None:
            raise PayrollError("NO_SALARY", "سجّل المرتب الشهري للموظف الأول.")
        if deduction > parts["owed"]:
            raise PayrollError("DEDUCTION_TOO_HIGH", f"السلف المتبقية على الموظف {parts['owed']:,.2f} ج فقط.")
        net = (parts["base"] + parts["bonuses"] - parts["penalties"] - deduction).quantize(CENT)
        if net <= 0:
            raise PayrollError("NET_NOT_POSITIVE", f"الصافي {net:,.2f} ج: قلّل خصم السلف أو راجع الجزاءات، لازم يفضل مبلغ يتصرف.")
        return net


def _positive_int(value, message):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise PayrollError("ID_INVALID", message) from None
    if number <= 0:
        raise PayrollError("ID_INVALID", message)
    return number
