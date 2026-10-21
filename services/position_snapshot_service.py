"""Position snapshots service: the owner's initial balance (once, from Settings) and the stocktaking sheets the gold profit report compares.

OWNER only: the three gold-position rights (a partial gold figure would mislead) are rechecked in the transaction; the initial balance also needs SETTINGS_MANAGE. The gold parts come from
`GoldPositionService.position()` (the reviewed consolidated read); the supplier gold, cash and bank are plain ledger sums read in the writing transaction. A DAILY snapshot is taken at most once per
calendar day (the first time an authorised user opens the application) and only after the initial balance exists; a MANUAL one can be taken any time. Snapshots are insert-only."""
from decimal import Decimal, InvalidOperation

from repositories.position_snapshot_repository import PositionSnapshotRepository
from services import position_snapshot_rules as rules
from services.authorization_service import AuthorizationService
from services.gold_position_service import REQUIRED_PERMISSIONS, GoldPositionService
from services.invoice_service import invoice_transaction

OPENING_PERMISSION = "SETTINGS_MANAGE"
AUDITED = (*rules.PARTS, "gold_total_eq21", "cash_egp", "bank_egp")


class SnapshotError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class PositionSnapshotService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=PositionSnapshotRepository, authorization=None, position_service=None):
        self._session = session
        self._transaction = transaction_factory
        self._repository = repository
        self._authorization = authorization or AuthorizationService(session)
        self._position = position_service or GoldPositionService(session)

    def _actor(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        return actor

    def can_snapshot(self):
        actor = self._session.current_actor()
        return actor is not None and set(REQUIRED_PERMISSIONS) <= set(actor.permissions)

    @staticmethod
    def _number(value, label, *, signed=False):
        try:
            number = Decimal(str(value).strip().replace(",", ""))
        except (InvalidOperation, ValueError):
            raise SnapshotError("NUMBER_INVALID", f"{label}: اكتب رقمًا صحيحًا.") from None
        if not number.is_finite() or abs(number) >= Decimal("1000000000"):
            raise SnapshotError("NUMBER_INVALID", f"{label}: رقم غير صالح.")
        if number < 0 and not signed:
            raise SnapshotError("NUMBER_NEGATIVE", f"{label}: لا يقبل رقمًا سالبًا.")
        return number

    @staticmethod
    def _view(row):
        return {"id": str(row["id"]), "taken_at": row["taken_at"], **{k: row[k] for k in AUDITED}}

    def opening_state(self, *, _expected_actor=None):
        """The initial balance for the Settings card, or None while it was not entered."""
        self._actor(_expected_actor)
        if not self.can_snapshot():
            raise PermissionError("الرصيد الافتتاحي للمالك فقط.")
        with self._transaction() as connection:
            row = self._repository(connection).opening()
        return None if row is None else self._view(row)

    def set_opening(self, gold_shop_eq21, gold_suppliers_eq21, cash_egp, bank_egp, *, _expected_actor=None):
        """The initial balance, ONCE: the shop's gold and the net supplier gold (signed) in 21K grams, the cash and the bank balances. A second attempt is refused."""
        session_actor = self._actor(_expected_actor)
        gold = self._number(gold_shop_eq21, "ذهب المحل")
        suppliers = self._number(gold_suppliers_eq21 or "0", "ذهب الموردين", signed=True)
        cash = self._number(cash_egp or "0", "الكاش", signed=True)
        bank = self._number(bank_egp or "0", "البنوك والمحافظ", signed=True)
        with self._transaction() as connection:
            actor = session_actor
            for code in (*REQUIRED_PERMISSIONS, OPENING_PERMISSION):
                actor = self._authorization.require_permission(connection, session_actor, code)
            repository = self._repository(connection)
            if repository.opening() is not None:
                raise SnapshotError("ALREADY_INITIALIZED", "الرصيد الافتتاحي مسجل بالفعل ولا يتغير.")
            now = repository.now()
            values = {**rules.opening_snapshot(gold, suppliers, cash, bank), "snapshot_kind": "OPENING", "taken_at": now, "created_by_user_id": actor.user_id}
            snapshot_id = repository.insert(values)
            repository.audit(user_id=actor.user_id, event_type="POSITION_OPENING_BALANCE", entity_id=snapshot_id, description="تسجيل الرصيد الافتتاحي للذهب والكاش",
                             metadata={k: str(values[k]) for k in AUDITED}, occurred_at=now)
        return {"id": str(snapshot_id)}

    def take(self, kind="MANUAL", *, _expected_actor=None):
        """A new stocktaking sheet from the live position."""
        if kind not in ("DAILY", "MANUAL"):
            raise SnapshotError("KIND_INVALID", "نوع اللقطة غير صحيح.")
        session_actor = self._actor(_expected_actor)
        if not self.can_snapshot():
            raise PermissionError("لقطة الرصيد للمالك فقط.")
        summary = self._position.position(_expected_actor=session_actor)["summary"]
        with self._transaction() as connection:
            actor = session_actor
            for code in REQUIRED_PERMISSIONS:
                actor = self._authorization.require_permission(connection, session_actor, code)
            repository = self._repository(connection)
            if repository.opening() is None:
                raise SnapshotError("NO_OPENING", "سجّل الرصيد الافتتاحي أولًا من الإعدادات.")
            now = repository.now()
            cash, bank = repository.cash_and_bank()
            values = {**rules.build_snapshot(summary, repository.supplier_gold(), cash, bank), "snapshot_kind": kind, "taken_at": now, "created_by_user_id": actor.user_id}
            snapshot_id = repository.insert(values)
            if kind == "MANUAL":
                repository.audit(user_id=actor.user_id, event_type="POSITION_SNAPSHOT", entity_id=snapshot_id, description="لقطة رصيد يدوية",
                                 metadata={k: str(values[k]) for k in AUDITED}, occurred_at=now)
        return {"taken": True, **self._view({**values, "id": snapshot_id})}

    def ensure_daily(self, *, _expected_actor=None):
        """The first authorised opening of the application each day takes the day's sheet; 'not applicable' (no right, no initial balance, already taken) is an answer, never an error."""
        session_actor = self._actor(_expected_actor)
        if not self.can_snapshot():
            return {"taken": False, "reason": "NOT_AUTHORISED"}
        with self._transaction() as connection:
            repository = self._repository(connection)
            if repository.opening() is None:
                return {"taken": False, "reason": "NO_OPENING"}
            if repository.latest_on_date(repository.now().date()) is not None:
                return {"taken": False, "reason": "ALREADY_TAKEN"}
        return self.take("DAILY", _expected_actor=session_actor)
