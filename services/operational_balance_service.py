"""MainWindow balance strip, the manager's financial accounts and their controlled setup.

The strip shows only the shop cash balance and the COUNTED old-gold grams for
21 / 18 / 24; wallet, InstaPay and bank balances belong to مراجعات المدير.
Every read runs in a transaction that is always rolled back; setup writes
(new bank / wallet account, activation, one opening balance per account) re-check
BANK_MANAGE / CASH_ADJUST inside their own transaction and are audited.
"""
from decimal import Decimal, InvalidOperation

from repositories.financial_balance_repository import FinancialBalanceRepository
from services.authorization_service import AuthorizationService
from services.invoice_service import invoice_transaction


STRIP_KARATS = (21, 18, 22, 24)
FINANCE_PERMISSIONS = ("MANAGEMENT_REVIEW",)
ACCOUNT_TYPES = ("BANK", "MOBILE_WALLET")
# Setup uses the existing Cash/Bank permissions (007): accounts = BANK_MANAGE, opening cash = CASH_ADJUST.
SETUP_PERMISSION = {"BANK": "BANK_MANAGE", "CASH": "CASH_ADJUST"}


class AccountSetupError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class _Discard(Exception):
    pass


class OperationalBalanceService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=FinancialBalanceRepository,
                 authorization=None):
        self._session = session
        self._authorization = authorization or AuthorizationService(session)
        self._transaction = transaction_factory
        self._repository = repository

    def _actor(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        return actor

    def _read(self, work):
        try:
            with self._transaction() as connection:
                raise _Discard(work(self._repository(connection)))
        except _Discard as done:
            return done.args[0]

    def strip(self, *, _expected_actor=None):
        """Cash balance and counted old-gold grams per karat (21, 18, 22, 24 in that order)."""
        self._actor(_expected_actor)

        def read(repository):
            return repository.cash_balance(), repository.old_gold_counted()
        cash, karats = self._read(read)
        grams = {int(r["karat_value"]): Decimal(str(r["balance_grams"])) for r in karats}
        return {"cash_balance": str(Decimal(str(cash)).quantize(Decimal("0.01"))),
                "old_gold": [{"karat": karat, "grams": str(grams.get(karat, Decimal("0.000")).quantize(Decimal("0.001")))}
                             for karat in STRIP_KARATS]}

    def financial_accounts(self, *, _expected_actor=None):
        """Every cash / bank / wallet account with balance, money in / out and latest movements."""
        actor = self._actor(_expected_actor)
        if not any(code in actor.permissions for code in FINANCE_PERMISSIONS):
            raise PermissionError("الحسابات المالية للمدير فقط.")

        def read(repository):
            return repository.account_summaries(), repository.recent_movements()
        accounts, movements = self._read(read)
        recent = {}
        for row in movements:
            recent.setdefault((row["ledger"], int(row["account_id"])), []).append(row)
        can_setup = {ledger: permission in actor.permissions for ledger, permission in SETUP_PERMISSION.items()}
        return [{**row, "account_id": str(row["account_id"]),
                 # An opening balance is offered only while the account has never moved.
                 "can_initialize": can_setup[row["ledger"]] and int(row["movement_count"]) == 0
                 and bool(row["is_active"]),
                 "can_manage": can_setup[row["ledger"]] and row["ledger"] == "BANK",
                 "recent": recent.get((row["ledger"], int(row["account_id"])), [])} for row in accounts]

    # ------------------------------------------------------------ manager setup (audited writes)

    def can_setup(self, actor):
        return {ledger: permission in actor.permissions and "MANAGEMENT_REVIEW" in actor.permissions
                for ledger, permission in SETUP_PERMISSION.items()}

    @staticmethod
    def _amount(text, *, allow_zero=True):
        try:
            value = Decimal(str(text).strip() or "0")
        except (InvalidOperation, ValueError):
            raise AccountSetupError("AMOUNT_INVALID", "اكتب المبلغ بالأرقام.") from None
        if not value.is_finite() or value < 0 or (value == 0 and not allow_zero) \
                or value != value.quantize(Decimal("0.01")):
            raise AccountSetupError("AMOUNT_INVALID", "المبلغ يجب أن يكون أكبر من صفر بخانتين عشريتين على الأكثر.")
        return value.quantize(Decimal("0.01"))

    def _write(self, expected_actor, permission, work):
        """Re-checks the permission inside the transaction (never trusts the button)."""
        session_actor = self._actor(expected_actor)
        if not any(code in session_actor.permissions for code in FINANCE_PERMISSIONS):
            raise PermissionError("إعداد الحسابات المالية للمدير / المالك فقط.")
        with self._transaction() as connection:
            actor = self._authorization.require_permission(connection, session_actor, permission)
            return work(self._repository(connection), actor)

    def create_account(self, name, account_type, opening_balance="0", bank_name="", *, _expected_actor=None):
        """A real bank or wallet account; its opening balance (if any) is an OPENING_BALANCE movement."""
        name, bank_name = (name or "").strip(), (bank_name or "").strip()
        if not name or len(name) > 150 or len(bank_name) > 150:
            raise AccountSetupError("NAME_REQUIRED", "اكتب اسم الحساب (حتى 150 حرفاً).")
        if account_type not in ACCOUNT_TYPES:
            raise AccountSetupError("TYPE_INVALID", "اختر نوع الحساب: بنك أو محفظة.")
        opening = self._amount(opening_balance)

        def work(repository, actor):
            now = repository.now()
            account_id = repository.create_bank_account(name=name, account_type=account_type,
                                                        bank_name=bank_name or None, account_label=None)
            movement = None
            if opening:
                movement = repository.add_opening_balance(ledger="BANK", account_id=account_id, amount=opening,
                                                          created_by_user_id=actor.user_id, occurred_at=now)
            repository.audit(user_id=actor.user_id, event_type="FINANCIAL_ACCOUNT_CREATED", entity_type="BANK_ACCOUNT",
                             entity_id=account_id, description=f"حساب مالي جديد: {name}",
                             metadata={"account_type": account_type, "opening_balance": opening,
                                       "opening_movement_id": movement}, occurred_at=now)
            return {"account_id": str(account_id), "opening_movement_id": movement}
        return self._write(_expected_actor, SETUP_PERMISSION["BANK"], work)

    def set_account_active(self, account_id, active, *, _expected_actor=None):
        def work(repository, actor):
            row = repository.lock_account("BANK", account_id)
            if row is None:
                raise AccountSetupError("ACCOUNT_NOT_FOUND", "الحساب غير موجود.")
            repository.set_bank_account_active(account_id, active)
            now = repository.now()
            repository.audit(user_id=actor.user_id, event_type="FINANCIAL_ACCOUNT_ACTIVATED" if active
                             else "FINANCIAL_ACCOUNT_DEACTIVATED", entity_type="BANK_ACCOUNT", entity_id=account_id,
                             description=f"{'تفعيل' if active else 'إيقاف'} حساب: {row['name']}",
                             metadata={"was_active": bool(row["is_active"])}, occurred_at=now)
            return {"account_id": str(account_id), "is_active": bool(active)}
        return self._write(_expected_actor, SETUP_PERMISSION["BANK"], work)

    def initialize_opening_balance(self, ledger, account_id, amount, *, _expected_actor=None):
        """Once per account and only before any movement: the real counted opening balance.

        Later corrections are adjustments (not part of this pass), never a second opening."""
        if ledger not in SETUP_PERMISSION:
            raise AccountSetupError("LEDGER_INVALID", "نوع الحساب غير صحيح.")
        value = self._amount(amount, allow_zero=False)

        def work(repository, actor):
            row = repository.lock_account(ledger, account_id)
            if row is None or not row["is_active"]:
                raise AccountSetupError("ACCOUNT_NOT_FOUND", "الحساب غير موجود أو غير نشط.")
            if int(row["movement_count"]):
                raise AccountSetupError("ALREADY_INITIALIZED",
                                        "للحساب حركات بالفعل؛ الرصيد الافتتاحي يُسجل مرة واحدة فقط.")
            now = repository.now()
            movement = repository.add_opening_balance(ledger=ledger, account_id=account_id, amount=value,
                                                      created_by_user_id=actor.user_id, occurred_at=now)
            repository.audit(user_id=actor.user_id, event_type="FINANCIAL_OPENING_BALANCE",
                             entity_type="CASH_ACCOUNT" if ledger == "CASH" else "BANK_ACCOUNT", entity_id=account_id,
                             description=f"رصيد افتتاحي {value:,.2f} ج — {row['name']}",
                             metadata={"amount": value, "movement_id": movement}, occurred_at=now)
            return {"movement_id": str(movement), "amount": str(value)}
        return self._write(_expected_actor, SETUP_PERMISSION[ledger], work)
