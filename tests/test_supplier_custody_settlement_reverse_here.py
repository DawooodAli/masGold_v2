"""Owner 2026-11-08: a supplier gold settlement delivered from market custody is reversed from the supplier page; the service routes it to the canonical reversal of its CUSTODY ENTRY."""
import unittest
from contextlib import contextmanager

from services.supplier_gold_settlement_service import SupplierGoldSettlementService
from tests.auth_fakes import FakeAuthorization
from tests.test_inventory_stock import session_for

OWNER = frozenset({"APP_LOGIN", "SUPPLIER_GOLD_SETTLE_REVERSE", "TRANSACTION_CORRECT"})


class Corrections:
    def __init__(self):
        self.calls = []

    def reverse(self, source, source_id, reason, *, _expected_actor=None):
        self.calls.append((source, source_id, reason))
        return {"code": "RV000001", "source_reference": "SG000013"}


def make(kind, entry):
    class Repo:
        def __init__(self, connection):
            pass

        def source(self, settlement_id):
            return {"id": settlement_id, "source_kind": kind}

        def custody_entry(self, settlement_id):
            return entry

    @contextmanager
    def transaction():
        yield object()
    session = session_for(OWNER, 9)
    corrections = Corrections()
    return SupplierGoldSettlementService(session, transaction_factory=transaction, repository=Repo, authorization=FakeAuthorization(session), corrections=corrections), corrections


class ReverseHereTests(unittest.TestCase):
    def test_a_custody_gold_settlement_is_reversed_through_its_custody_entry(self):
        service, corrections = make("CUSTODY_GOLD", {"entry_id": 77, "trip_id": 5, "entry_no": 9})
        result = service.reverse("13", "خطأ في التسليم")
        self.assertEqual(corrections.calls, [("MARKET_TRIP_ENTRY", "77", "خطأ في التسليم")])
        self.assertEqual(result["code"], "RV000001")

    def test_a_bar_or_lot_settlement_from_custody_follows_the_same_route(self):
        for kind in ("TRIP_BAR", "CUSTODY_LOT"):
            service, corrections = make(kind, {"entry_id": 88, "trip_id": 5, "entry_no": 10})
            service.reverse("14", "تصحيح")
            self.assertEqual(corrections.calls[0][:2], ("MARKET_TRIP_ENTRY", "88"))

    def test_a_direct_shop_scrap_settlement_keeps_its_own_correction(self):
        service, corrections = make("SHOP_SCRAP", None)
        service.reverse("12", "تصحيح")
        self.assertEqual(corrections.calls[0][0], "SUPPLIER_GOLD_SETTLEMENT")


if __name__ == "__main__":
    unittest.main()
