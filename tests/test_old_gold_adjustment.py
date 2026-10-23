"""تعديل رصيد الكسر: the manager corrects the Scrap of one karat with a mandatory reason. In-memory ledger, no database."""
import unittest
from contextlib import contextmanager
from decimal import Decimal as D
from uuid import uuid4

from PySide6.QtTest import QTest

from services.old_gold_adjustment_service import OldGoldAdjustmentError, OldGoldAdjustmentService
from tests import test_invoice_page_qml as harness
from tests.auth_fakes import FakeAuthorization
from tests.test_inventory_stock import session_for

MANAGER = frozenset({"APP_LOGIN", "OLD_GOLD_ADJUST"})


class Ledger:
    def __init__(self):
        self.movements, self.audits = [], []
        self.karats = {1: {"id": 1, "karat_value": 21, "name": "عيار 21", "is_active": 1}, 2: {"id": 2, "karat_value": 18, "name": "عيار 18", "is_active": 0}}
        self.held = D("0.000")


class Repo:
    def __init__(self, connection):
        self.ledger = connection.ledger

    def karat(self, karat_id):
        return self.ledger.karats.get(int(karat_id))

    def lock_ledger(self, karat_id):
        return sum((m["delta"] for m in self.ledger.movements if m["karat"] == karat_id), D("0.000")), self.ledger.held

    def by_group(self, group):
        row = next((m for m in self.ledger.movements if m["group"] == group), None)
        return None if row is None else {"id": row["id"], "karat_id": row["karat"], "weight_delta_grams": row["delta"], "performed_by_user_id": row["user"]}

    def insert_adjustment(self, *, direction, karat_id, delta, reason, group, user_id):
        self.ledger.movements.append({"id": str(len(self.ledger.movements) + 1), "karat": karat_id, "delta": delta, "reason": reason, "group": group, "user": user_id, "direction": direction})
        return self.ledger.movements[-1]["id"]

    def audit(self, **values):
        self.ledger.audits.append(values)

    def balances(self):
        return [{"karat_id": 1, "karat_value": 21, "karat_name": "عيار 21", "physical_grams": sum((m["delta"] for m in self.ledger.movements), D("0")), "held_grams": self.ledger.held}]


def service_for(ledger, permissions=MANAGER):
    @contextmanager
    def transaction():
        yield type("C", (), {"ledger": ledger})()
    session = session_for(permissions, 9)
    return OldGoldAdjustmentService(session, transaction_factory=transaction, repository=Repo, authorization=FakeAuthorization(session))


class AdjustmentTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger()
        self.service = service_for(self.ledger)
        self.service.adjust(1, "IN", "100.000", "رصيد مبدئي ناقص", str(uuid4()))

    def adjust(self, direction, grams, reason="تسوية", karat=1, key=None):
        return self.service.adjust(karat, direction, grams, reason, key or str(uuid4()))

    def test_an_increase_and_a_decrease_post_signed_movements_with_the_reason_and_an_audit(self):
        self.adjust("OUT", "12.500", "قطعة اتحسبت مرتين")
        self.assertEqual([m["delta"] for m in self.ledger.movements], [D("100.000"), D("-12.500")])
        self.assertEqual(self.ledger.movements[-1]["reason"], "قطعة اتحسبت مرتين")
        audit = self.ledger.audits[-1]
        self.assertEqual((audit["metadata"]["direction"], audit["metadata"]["physical_before"], audit["metadata"]["physical_after"]), ("OUT", "100.000", "87.500"))

    def test_the_reason_is_mandatory_and_the_numbers_are_checked_before_any_write(self):
        before = len(self.ledger.movements)
        for direction, grams, reason, code in (("IN", "5", "   ", "REASON_REQUIRED"), ("IN", "0", "x", "GRAMS_INVALID"), ("IN", "-3", "x", "GRAMS_INVALID"), ("IN", "abc", "x", "GRAMS_INVALID"),
                                               ("IN", "1.2345", "x", "GRAMS_INVALID"), ("SIDEWAYS", "5", "x", "DIRECTION_INVALID")):
            with self.assertRaises(OldGoldAdjustmentError) as refused:
                self.adjust(direction, grams, reason)
            self.assertEqual(refused.exception.code, code)
        with self.assertRaises(OldGoldAdjustmentError):
            self.adjust("IN", "5", karat=2)                                                      # an inactive karat
        self.assertEqual(len(self.ledger.movements), before)

    def test_a_decrease_never_takes_more_than_the_available_scrap(self):
        self.ledger.held = D("30.000")                                                           # held for a manager review: not usable
        with self.assertRaises(OldGoldAdjustmentError) as refused:
            self.adjust("OUT", "80.000")
        self.assertEqual(refused.exception.code, "SCRAP_SHORTAGE")
        self.adjust("OUT", "70.000")                                                             # exactly the available grams is fine
        self.assertEqual(sum(m["delta"] for m in self.ledger.movements), D("30.000"))

    def test_a_retry_with_the_same_key_posts_once_and_a_different_payload_is_refused(self):
        key = str(uuid4())
        self.adjust("IN", "4.000", key=key)
        again = self.adjust("IN", "4.000", key=key)
        self.assertTrue(again["replayed"])
        self.assertEqual(len(self.ledger.movements), 2)
        with self.assertRaises(OldGoldAdjustmentError) as refused:
            self.adjust("IN", "5.000", key=key)
        self.assertEqual(refused.exception.code, "REQUEST_CONFLICT")

    def test_only_a_user_with_the_adjust_right_may_adjust_or_even_read_the_form(self):
        cashier = service_for(self.ledger, frozenset({"APP_LOGIN", "OLD_GOLD_MANAGE"}))
        with self.assertRaises(PermissionError):
            cashier.adjust(1, "IN", "1", "x", str(uuid4()))
        with self.assertRaises(PermissionError):
            cashier.balances()
        self.assertFalse(cashier.can_adjust())
        self.assertTrue(self.service.can_adjust())
        self.assertEqual(self.service.balances()[0]["available"], "100.000")


class ScreenTests(harness.InvoicePageTests):
    def setUp(self):
        super().setUp()
        self.ledger = Ledger()

        @contextmanager
        def transaction():
            yield type("C", (), {"ledger": self.ledger})()
        self.grant("OLD_GOLD_ADJUST")
        session = self.controller.session
        service = OldGoldAdjustmentService(session, transaction_factory=transaction, repository=Repo, authorization=FakeAuthorization(session))
        service.adjust(1, "IN", "100.000", "رصيد مبدئي", str(uuid4()))
        self.adjust_vm = self.controller.operational_viewmodels["oldGoldAdjustVM"]
        self.adjust_vm._service = service
        self.adjust_vm.changed.emit()

    def test_the_manager_adjusts_the_scrap_with_real_clicks_at_all_sizes(self):
        self.open_settings_dialog(10, "reviewsOpenScrapAdjust", "scrapAdjustDialog")
        self.wait_until(lambda: len(self.adjust_vm.balances) == 1, "balances loaded")
        self.assertFalse(self.item("scrapAdjustSave").property("enabled"))                          # karat, grams and reason are missing
        self.choose("scrapAdjustKarat", 0)
        self.click("scrapAdjustOut")
        self.type_into("scrapAdjustGrams", "12.500")
        self.type_into("scrapAdjustReason", "قطعة اتحسبت مرتين")
        self.wait_until(lambda: self.item("scrapAdjustSave").property("enabled"), "ready")
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            self.scroll_dialog_to("scrapAdjustDialog", "scrapAdjustSave")
            self.assert_inside_window(self.item("scrapAdjustSave"))
        self.click("scrapAdjustSave")
        self.wait_until(lambda: len(self.ledger.movements) == 2, "posted")
        self.assertEqual((self.ledger.movements[-1]["delta"], self.ledger.movements[-1]["reason"]), (D("-12.500"), "قطعة اتحسبت مرتين"))
        self.wait_until(lambda: "تم تسجيل نقص" in str(self.item("scrapAdjustMessage").property("text")), "the result is shown")
        self.assertEqual(self.warnings, [])


for _cls in (ScreenTests,):
    for _name in dir(harness.InvoicePageTests):
        if _name.startswith("test_") and _name not in _cls.__dict__:
            setattr(_cls, _name, None)


if __name__ == "__main__":
    unittest.main()
