"""Customer merge and delete (migration 087, owner 2026-10-29).

MERGE: one customer entered twice. The records of the MERGED customer (ledger, invoices and their snapshots, exchanges, returns, settlements,
deposits, dead-cash records, advances, old-gold movements) are moved to the SURVIVING customer in ONE transaction, so the account statement and
the invoices live in one place. The printed name on an old invoice stays as it was (the snapshot keeps its own name); the merged customer row is
kept, deactivated and points at the survivor, and the operation is audited with the counts moved. No money moves: the balances only add up.

DELETE: allowed only for a customer nothing points at (no ledger row, invoice, deposit, settlement, advance, flag or Billing V1 record, and
nobody merged into it).

Both need TRANSACTION_CORRECT (OWNER / MANAGER), rechecked inside the transaction by the strict locking read; the actor is the session user.
Lock order: the two customers in ASCENDING id order, then the moved tables.
"""
from decimal import Decimal

from repositories.customer_merge_repository import ALL_TABLES, MOVED, CustomerMergeRepository
from services.authorization_service import AuthorizationService
from services.customer_service import CustomerService
from services.invoice_service import invoice_transaction

PERMISSION = "TRANSACTION_CORRECT"

# What the screen says about each table (the preview and the refusal of a delete name them)
LABELS = {
    "customer_transactions": "حركات الحساب", "invoices": "فواتير", "invoice_customer_snapshots": "بيانات فواتير",
    "invoice_lifecycle_transactions": "استبدالات", "invoice_returns": "مرتجعات", "customer_account_settlements": "تسويات",
    "customer_deposits": "عربون", "dead_cash_writeoffs": "شطب رصيد", "dead_cash_recoveries": "تحصيل مشطوب",
    "cash_advances": "سلف وعهد", "old_gold_movements": "حركات كسر", "customer_flags": "تحذيرات",
    "billing_invoices": "فواتير النظام القديم",
}


class CustomerMergeError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class _Done(Exception):
    pass


def _reason(text):
    value = (text or "").strip()
    if not value:
        raise CustomerMergeError("REASON_REQUIRED", "اكتب سبب الدمج.")
    if len(value) > 500:
        raise CustomerMergeError("REASON_TOO_LONG", "سبب الدمج طويل جدًا.")
    return value


def _summary(counts):
    return [{"table": t, "label": LABELS[t], "count": n} for t, n in counts.items() if n]


class CustomerMergeService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=CustomerMergeRepository, authorization=None):
        self._session, self._transaction, self._repository = session, transaction_factory, repository
        self._authorization = authorization or AuthorizationService(session)

    # ------------------------------------------------------------ plumbing
    def _actor(self, expected_actor=None):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if PERMISSION not in actor.permissions:
            raise PermissionError("لا توجد صلاحية لدمج أو حذف العملاء.")
        return actor

    def can_manage(self):
        actor = self._session.current_actor()
        return bool(actor and PERMISSION in actor.permissions)

    @staticmethod
    def _id(value):
        try:
            return str(CustomerService._customer_id(value))
        except Exception:
            raise CustomerMergeError("CUSTOMER_REQUIRED", "اختر العميل.") from None

    def _row(self, locked, cid):
        row = locked.get(cid)
        if row is None:
            raise CustomerMergeError("CUSTOMER_NOT_FOUND", "العميل غير موجود.")
        return row

    # ------------------------------------------------------------ reads
    def usage(self, customer_id, *, _expected_actor=None):
        """What points at this customer: the counts, the balance and whether the customer may be deleted (non-locking)."""
        actor, cid = self._actor(_expected_actor), self._id(customer_id)
        try:
            with self._transaction() as connection:
                self._authorization.require_permission_read(connection, actor, PERMISSION)
                repository = self._repository(connection)
                row = repository._fetch_one("SELECT id, name, is_active, merged_into_customer_id FROM customers WHERE id = %s", (int(cid),))
                if row is None:
                    raise CustomerMergeError("CUSTOMER_NOT_FOUND", "العميل غير موجود.")
                counts = repository.usage(cid)
                merged_in = repository._fetch_one("SELECT COUNT(*) AS n FROM customers WHERE merged_into_customer_id = %s", (int(cid),))["n"]
                raise _Done({"customer_id": cid, "name": row["name"], "balance": str(repository.balance(cid)),
                             "usage": _summary(counts), "merged_in": int(merged_in), "merged": row["merged_into_customer_id"] is not None,
                             "deletable": not any(counts.values()) and not merged_in and row["merged_into_customer_id"] is None})
        except _Done as done:
            return done.args[0]

    def preview(self, source_id, target_id, *, _expected_actor=None):
        """What a merge would do: both names, what moves and the two balances / their sum (non-locking)."""
        actor, source, target = self._actor(_expected_actor), self._id(source_id), self._id(target_id)
        if source == target:
            raise CustomerMergeError("SAME_CUSTOMER", "اختر عميلًا آخر للدمج معه.")
        try:
            with self._transaction() as connection:
                self._authorization.require_permission_read(connection, actor, PERMISSION)
                repository = self._repository(connection)
                rows = {c: repository._fetch_one("SELECT id, name, is_active, merged_into_customer_id FROM customers WHERE id = %s", (int(c),))
                        for c in (source, target)}
                if any(r is None for r in rows.values()):
                    raise CustomerMergeError("CUSTOMER_NOT_FOUND", "العميل غير موجود.")
                if rows[target]["merged_into_customer_id"] is not None or not rows[target]["is_active"]:
                    raise CustomerMergeError("TARGET_INACTIVE", "العميل الذي سيبقى غير نشط أو مدموج في غيره.")
                if rows[source]["merged_into_customer_id"] is not None:
                    raise CustomerMergeError("ALREADY_MERGED", "هذا العميل مدموج بالفعل.")
                moved = {t: n for t, n in repository.usage(source).items() if n and (t, "customer_id") in MOVED}
                a, b = Decimal(str(repository.balance(source))), Decimal(str(repository.balance(target)))
                raise _Done({"source_id": source, "source_name": rows[source]["name"], "target_id": target,
                             "target_name": rows[target]["name"], "moved": _summary(moved), "source_balance": str(a),
                             "target_balance": str(b), "combined_balance": str(a + b)})
        except _Done as done:
            return done.args[0]

    # ------------------------------------------------------------ merge
    def merge(self, source_id, target_id, reason, *, _expected_actor=None):
        """Move every record of `source` to `target` and retire `source`. An exact repeat (already merged into that target) answers the same way."""
        session_actor = self._actor(_expected_actor)
        source, target, reason = self._id(source_id), self._id(target_id), _reason(reason)
        if source == target:
            raise CustomerMergeError("SAME_CUSTOMER", "اختر عميلًا آخر للدمج معه.")
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            locked = repository.lock_customers((source, target))
            src, tgt = self._row(locked, source), self._row(locked, target)
            if src["merged_into_customer_id"] is not None:
                if str(src["merged_into_customer_id"]) == target:
                    return {"source_id": source, "target_id": target, "duplicate": True, "moved": []}
                raise CustomerMergeError("ALREADY_MERGED", "هذا العميل مدموج بالفعل في عميل آخر.")
            if tgt["merged_into_customer_id"] is not None or not tgt["is_active"]:
                raise CustomerMergeError("TARGET_INACTIVE", "العميل الذي سيبقى غير نشط أو مدموج في غيره.")
            before_source = Decimal(str(repository.balance(source)))
            before_target = Decimal(str(repository.balance(target)))
            repository.deactivate_duplicate_flags(source, target, actor.user_id, f"دمج العميل {source} في {target}")
            moved = {}
            for table, column in MOVED + (("customer_flags", "customer_id"),):
                moved[table] = repository.move(table, column, source, target)
            left = {t: n for t, n in repository.usage(source).items() if n and (t, "customer_id") in MOVED + (("customer_flags", "customer_id"),)}
            if left:                                                      # nothing may stay behind: any leftover rolls the whole merge back
                raise CustomerMergeError("MERGE_INCOMPLETE", "تعذر نقل كل سجلات العميل؛ لم يتم الدمج.")
            repository.mark_merged(source, target, actor.user_id)
            after_target = Decimal(str(repository.balance(target)))
            if after_target != before_source + before_target:
                raise CustomerMergeError("BALANCE_MISMATCH", "اختلف الرصيد بعد الدمج؛ لم يتم الدمج.")
            repository.audit(actor=actor, event_type="CUSTOMER_MERGED", customer_id=source,
                             description=f"دمج العميل {source} في {target}",
                             metadata={"source": source, "target": target, "reason": reason, "moved": {t: n for t, n in moved.items() if n},
                                       "source_balance": str(before_source), "target_balance_before": str(before_target),
                                       "target_balance_after": str(after_target)})
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"source_id": source, "target_id": target, "duplicate": False, "target_name": tgt["name"],
                "moved": _summary(moved), "balance_after": str(after_target)}

    # ------------------------------------------------------------ delete
    def delete(self, customer_id, *, _expected_actor=None):
        """Remove a customer nothing points at. Anything referenced (even by a zero-balance ledger row) is refused with the list of what holds it."""
        session_actor, cid = self._actor(_expected_actor), self._id(customer_id)
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            row = self._row(repository.lock_customers((cid,)), cid)
            counts = {t: n for t, n in repository.usage(cid).items() if n}
            merged_in = repository._fetch_one("SELECT COUNT(*) AS n FROM customers WHERE merged_into_customer_id = %s", (int(cid),))["n"]
            if counts or merged_in or row["merged_into_customer_id"] is not None:
                names = "، ".join(f"{LABELS[t]} ({n})" for t, n in counts.items())
                if merged_in:
                    names = (names + "، " if names else "") + f"عملاء مدموجون فيه ({int(merged_in)})"
                raise CustomerMergeError("CUSTOMER_IN_USE", "لا يمكن حذف العميل لأن عليه سجلات: " + (names or "عميل مدموج") + ". استخدم الدمج أو التعطيل.")
            repository.audit(actor=actor, event_type="CUSTOMER_DELETED", customer_id=cid, description=f"حذف العميل {cid}",
                             metadata={"customer_id": cid, "name": row["name"], "code": row["customer_code"]})
            if repository.delete_customer(cid) != 1:
                raise CustomerMergeError("CUSTOMER_NOT_FOUND", "العميل غير موجود.")
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"customer_id": cid, "name": row["name"]}
