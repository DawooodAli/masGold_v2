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
LEDGER_PERMISSIONS = ("CASH_ADJUST", "BANK_MANAGE")                  # the rights of an account's opening balance (the same as the treasury / account setup)
KARATS = (21, 18, 24)
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
        """The initial balance card: stage 1 (scrap, cash and bank openings) and stage 2 (the new gold, which records the OPENING sheet and closes the card)."""
        self._actor(_expected_actor)
        if not self.can_snapshot():
            raise PermissionError("الرصيد الافتتاحي للمالك فقط.")
        with self._transaction() as connection:
            repository = self._repository(connection)
            row = repository.opening()
            posted = repository.ledgers_posted()
            accounts = [{"id": str(a["id"]), "ledger": a["ledger"], "name": a["name"], "type": a["account_type"], "free": int(a["movements"]) == 0} for a in repository.accounts_for_opening()]
            totals = repository.opening_totals() if posted else None
        scrap, cash, bank = totals if totals else ([], Decimal(0), Decimal(0))
        return {"opening": None if row is None else self._view(row), "ledgers_posted": posted, "accounts": accounts,
                "posted": {"scrap": {int(r["karat_value"]): Decimal(str(r["grams"])) for r in scrap}, "cash": Decimal(str(cash)), "bank": Decimal(str(bank))}}

    def _require_opening_rights(self, connection, session_actor):
        actor = session_actor
        for code in (*REQUIRED_PERMISSIONS, OPENING_PERMISSION, *LEDGER_PERMISSIONS):
            actor = self._authorization.require_permission(connection, session_actor, code)
        return actor

    def post_opening_ledgers(self, scrap, cash, banks, *, _expected_actor=None):
        """STAGE 1, once: the counted scrap per karat (21 / 18 / 24), the cash of the main safe and the opening balance of each bank / wallet account, all as ordinary OPENING_BALANCE ledger movements
        in ONE transaction. An account that already has movements refuses the whole stage (an opening is the first movement of an account). Nothing about the gold sheet is recorded here."""
        session_actor = self._actor(_expected_actor)
        weights = {karat: self._number((scrap or {}).get(str(karat)) or "0", f"كسر عيار {karat}") for karat in KARATS}
        for karat, grams in weights.items():
            if grams != grams.quantize(Decimal("0.001")):
                raise SnapshotError("NUMBER_INVALID", f"كسر عيار {karat}: ثلاث خانات عشرية على الأكثر.")
        cash_amount = self._number(cash or "0", "الكاش")
        amounts = {str(key): self._number(value or "0", "رصيد حساب") for key, value in (banks or {}).items()}
        for value in (cash_amount, *amounts.values()):
            if value != value.quantize(Decimal("0.01")):
                raise SnapshotError("NUMBER_INVALID", "المبالغ بخانتين عشريتين على الأكثر.")
        cash_amount = cash_amount.quantize(Decimal("0.01"))
        amounts = {key: value.quantize(Decimal("0.01")) for key, value in amounts.items()}
        with self._transaction() as connection:
            actor = self._require_opening_rights(connection, session_actor)
            repository = self._repository(connection)
            if repository.opening() is not None or repository.ledgers_posted():
                raise SnapshotError("ALREADY_INITIALIZED", "الكسر والكاش والبنوك سُجّلت بالفعل ولا تتغير.")
            accounts = {(a["ledger"], str(a["id"])): a for a in repository.accounts_for_opening(lock=True)}
            now = repository.now()
            posted = []
            for karat, grams in weights.items():
                if grams > 0:
                    repository.add_scrap_opening(karat_value=karat, grams=grams, user_id=actor.user_id, now=now)
                    posted.append(f"كسر {karat}: {grams}")
            safe = next((a for key, a in accounts.items() if key[0] == "CASH"), None)
            if cash_amount > 0:
                if safe is None or int(safe["movements"]):
                    raise SnapshotError("ACCOUNT_USED", "للخزنة حركات بالفعل: الرصيد الافتتاحي يُسجّل على حساب لا حركات له.")
                repository.add_money_opening(ledger="CASH", account_id=safe["id"], amount=cash_amount, user_id=actor.user_id, now=now)
                posted.append(f"الكاش: {cash_amount}")
            for account_id, amount in amounts.items():
                if amount <= 0:
                    continue
                account = accounts.get(("BANK", account_id))
                if account is None:
                    raise SnapshotError("ACCOUNT_NOT_FOUND", "حساب بنك / محفظة غير موجود أو غير نشط.")
                if int(account["movements"]):
                    raise SnapshotError("ACCOUNT_USED", f"للحساب «{account['name']}» حركات بالفعل: الرصيد الافتتاحي يُسجّل على حساب لا حركات له.")
                repository.add_money_opening(ledger="BANK", account_id=account["id"], amount=amount, user_id=actor.user_id, now=now)
                posted.append(f"{account['name']}: {amount}")
            repository.audit(user_id=actor.user_id, event_type="POSITION_OPENING_LEDGERS", entity_id=0, description="تسجيل رصيد الكسر والكاش والبنوك الافتتاحي", metadata={"posted": posted}, occurred_at=now)
        return {"posted": posted}

    def set_opening(self, new_gold, gold_suppliers_eq21, *, _expected_actor=None):
        """STAGE 2, once: the new gold per karat (physical grams, typed after the stocktake) and the optional net supplier gold. The OPENING sheet takes the shop's gold = the new gold + the scrap posted in
        stage 1 (both in 21K-equivalent grams, computed here, never typed) and the cash / bank openings of stage 1; recording it closes the card."""
        session_actor = self._actor(_expected_actor)
        weights = {karat: self._number((new_gold or {}).get(str(karat)) or "0", f"ذهب جديد عيار {karat}") for karat in KARATS}
        suppliers = self._number(gold_suppliers_eq21 or "0", "ذهب الموردين", signed=True)
        with self._transaction() as connection:
            actor = self._require_opening_rights(connection, session_actor)
            repository = self._repository(connection)
            if repository.opening() is not None:
                raise SnapshotError("ALREADY_INITIALIZED", "الرصيد الافتتاحي مسجل بالفعل ولا يتغير.")
            if not repository.ledgers_posted():
                raise SnapshotError("LEDGERS_FIRST", "سجّل الكسر والكاش والبنوك أولًا.")
            scrap, cash, bank = repository.opening_totals()
            gold_new = rules.eq21({karat: grams for karat, grams in weights.items()})
            gold_scrap = rules.eq21({int(r["karat_value"]): Decimal(str(r["grams"])) for r in scrap})
            now = repository.now()
            values = {**rules.opening_snapshot(gold_new + gold_scrap, suppliers, cash, bank), "snapshot_kind": "OPENING", "taken_at": now, "created_by_user_id": actor.user_id}
            snapshot_id = repository.insert(values)
            repository.audit(user_id=actor.user_id, event_type="POSITION_OPENING_BALANCE", entity_id=snapshot_id, description="تسجيل الرصيد الافتتاحي للذهب",
                             metadata={**{k: str(values[k]) for k in AUDITED}, "new_gold_grams": {str(k): str(v) for k, v in weights.items()}, "new_gold_eq21": str(gold_new), "scrap_eq21": str(gold_scrap)},
                             occurred_at=now)
        return {"id": str(snapshot_id), "gold_new_eq21": gold_new, "scrap_eq21": gold_scrap}

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
