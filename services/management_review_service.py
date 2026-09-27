"""مراجعات المدير: counts for everyone, details for reviewers (read-only).

Review sources: unidentified sales (inventory_reconciliation_cases), making /
pricing reviews, approved cash settlements and old-gold discount changes
(management_reviews). Every
read runs in a transaction that is always rolled back.
"""
from repositories.management_review_repository import ManagementReviewRepository
from services.invoice_service import invoice_transaction


REVIEW_PERMISSIONS = ("MANAGEMENT_REVIEW", "INVENTORY_RECONCILE")


class _Discard(Exception):
    pass


class ManagementReviewService:
    def __init__(self, session, *, transaction_factory=invoice_transaction,
                 repository=ManagementReviewRepository):
        self._session = session
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

    def can_review(self, actor):
        return any(code in actor.permissions for code in REVIEW_PERMISSIONS)

    def counts(self, *, _expected_actor=None):
        """Visible to every logged-in user (the sidebar indicator)."""
        self._actor(_expected_actor)
        return self._read(lambda repository: repository.open_counts())

    def open_reviews(self, *, _expected_actor=None):
        actor = self._actor(_expected_actor)
        if not self.can_review(actor):
            raise PermissionError("فتح مراجعات المدير يحتاج صلاحية المراجعة.")

        def read(repository):
            return {"INVENTORY_MATCH": repository.open_inventory_cases(),
                    "MAKING_PRICE": repository.open_reviews("MAKING_PRICE"),
                    "CASH_SETTLEMENT": repository.open_reviews("CASH_SETTLEMENT"),
                    "OLD_GOLD_DISCOUNT": repository.open_reviews("OLD_GOLD_DISCOUNT")}
        return self._read(read)
