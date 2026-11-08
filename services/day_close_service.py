"""تسليم وإقفال اليوم: the end-of-day cash count (migration 100). The employee counts the physical cash; the program shows the expected cash (the ledger of the active cash accounts) and
the unfinished invoices; closing saves the minutes (expected, counted, difference with a MANDATORY explanation when it is not zero, a snapshot of every balance). A record only: it moves
no money and changes nothing else. Right DAY_CLOSE is rechecked in the transaction (the actor is the AppSession user); one request key makes a retry harmless."""
import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from repositories.day_close_repository import DayCloseRepository
from services.authorization_service import AuthorizationService
from services.invoice_service import invoice_transaction

PERMISSION = "DAY_CLOSE"
CENT = Decimal("0.01")
MAX_CASH = Decimal("1000000000.00")


class DayCloseError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _money(value, what):
    try:
        amount = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError, AttributeError):
        raise DayCloseError("AMOUNT_INVALID", f"اكتب {what} بالأرقام.") from None
    if not amount.is_finite() or amount < 0 or amount > MAX_CASH:
        raise DayCloseError("AMOUNT_INVALID", f"{what} لازم يكون صفر أو أكثر.")
    if amount != amount.quantize(CENT, rounding=ROUND_HALF_UP):
        raise DayCloseError("AMOUNT_INVALID", f"{what} بخانتين عشريتين على الأكثر.")
    return amount.quantize(CENT)


class DayCloseService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=DayCloseRepository, authorization=None, today=date.today):
        self._session = session
        self._transaction = transaction_factory
        self._repository = repository
        self._authorization = authorization or AuthorizationService(session)
        self._today = today

    def can_close(self):
        actor = self._session.current_actor()
        return actor is not None and PERMISSION in actor.permissions

    @staticmethod
    def _snapshot(repository):
        cash = [{"id": str(r["id"]), "name": r["name"], "balance": str(Decimal(str(r["balance"])).quantize(CENT))} for r in repository.cash_accounts()]
        bank = [{"id": str(r["id"]), "name": r["name"], "balance": str(Decimal(str(r["balance"])).quantize(CENT))} for r in repository.bank_accounts()]
        expected = sum((Decimal(a["balance"]) for a in cash), Decimal("0.00")).quantize(CENT)
        return cash, bank, expected

    def overview(self, *, _expected_actor=None):
        """The expected cash, every balance, the unfinished invoices and the latest closes (needs the day-close right)."""
        actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if PERMISSION not in actor.permissions:
            raise PermissionError("ليس لديك صلاحية إقفال اليوم.")
        with self._transaction() as connection:
            repository = self._repository(connection)
            if not repository.has_schema():
                raise DayCloseError("SCHEMA_OUTDATED", "قاعدة البيانات تحتاج تحديث 100 قبل استخدام إقفال اليوم.")
            cash, bank, expected = self._snapshot(repository)
            drafts = repository.draft_invoices()
            recent = repository.recent()
        return {"date": self._today().isoformat(), "expected": str(expected), "cash": cash, "bank": bank, "drafts": drafts,
                "recent": [{"id": str(r["id"]), "date": str(r["business_date"]), "expected": str(Decimal(str(r["expected_cash"]))), "counted": str(Decimal(str(r["counted_cash"]))),
                            "difference": str(Decimal(str(r["difference"]))), "reason": r["difference_reason"] or "", "drafts": int(r["draft_invoices"]), "note": r["note"] or "",
                            "at": str(r["closed_at"])[:16], "user": r["username"]} for r in recent]}

    def close(self, counted_cash, difference_reason, note, request_key, *, _expected_actor=None):
        """Save the close. The expected cash is derived under the transaction; the difference is derived, never typed."""
        session_actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if PERMISSION not in session_actor.permissions:
            raise PermissionError("ليس لديك صلاحية إقفال اليوم.")
        counted = _money(counted_cash, "الكاش المعدود")
        reason = str(difference_reason or "").strip()
        note = str(note or "").strip()
        if len(reason) > 500 or len(note) > 500:
            raise DayCloseError("TEXT_TOO_LONG", "النص 500 حرف على الأكثر.")
        try:
            key = str(uuid.UUID(str(request_key)))
        except (ValueError, TypeError, AttributeError):
            raise DayCloseError("REQUEST_INVALID", "أعد فتح النموذج وحاول مرة أخرى.") from None
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            if not repository.has_schema():
                raise DayCloseError("SCHEMA_OUTDATED", "قاعدة البيانات تحتاج تحديث 100 قبل استخدام إقفال اليوم.")
            prior = repository.by_key(key)
            if prior is not None:
                if Decimal(str(prior["counted_cash"])) != counted or int(prior["closed_by_user_id"]) != int(actor.user_id):
                    raise DayCloseError("REQUEST_CONFLICT", "طلب الإقفال مستخدم لعملية مختلفة.")
                expected_before = Decimal(str(prior["expected_cash"]))
                return {"close_id": str(prior["id"]), "expected": str(expected_before), "counted": str(counted), "difference": str(counted - expected_before), "replayed": True}
            cash, bank, expected = self._snapshot(repository)
            difference = (counted - expected).quantize(CENT)
            if difference != 0 and not reason:
                raise DayCloseError("REASON_REQUIRED", f"فيه فرق {difference:,.2f} ج بين المعدود والمتوقع: اكتب سببه.")
            drafts = repository.draft_invoices()
            business_date = self._today()
            close_id = repository.insert(business_date=business_date, expected=expected, counted=counted, difference=difference, reason=reason or None, drafts=drafts,
                                         balances={"cash": cash, "bank": bank}, note=note or None, request_key=key, user_id=actor.user_id)
            repository.audit(user_id=actor.user_id, close_id=close_id, description=f"إقفال يوم {business_date.isoformat()}: متوقع {expected:,.2f} ومعدود {counted:,.2f} والفرق {difference:,.2f}",
                             metadata={"expected": str(expected), "counted": str(counted), "difference": str(difference), "reason": reason, "drafts": drafts, "note": note})
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"close_id": close_id, "expected": str(expected), "counted": str(counted), "difference": str(difference), "replayed": False}
