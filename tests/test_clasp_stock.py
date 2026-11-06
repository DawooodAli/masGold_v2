"""رصيد القفل: weight-only clasp stock per karat. In-memory ledger, no database."""
import unittest
from contextlib import contextmanager
from decimal import Decimal as D
from uuid import uuid4

from services.clasp_stock_service import ClaspStockError, ClaspStockService
from PySide6.QtTest import QTest

from tests import test_invoice_page_qml as harness
from tests.auth_fakes import FakeAuthorization
from tests.test_inventory_stock import session_for

MANAGER = frozenset({"APP_LOGIN", "CLASP_STOCK_MANAGE"})
VIEWER = frozenset({"APP_LOGIN", "INVENTORY_VIEW"})


class Ledger:
    def __init__(self):
        self.movements, self.audits = [], []
        self.karats = {1: {"id": 1, "karat_value": 21, "name": "عيار 21", "is_active": 1}, 2: {"id": 2, "karat_value": 18, "name": "عيار 18", "is_active": 0}}


class Repo:
    def __init__(self, connection):
        self.ledger = connection.ledger

    def has_schema(self):
        return True

    def karat(self, karat_id):
        return self.ledger.karats.get(int(karat_id))

    def lock_balance(self, karat_id):
        return sum((m["delta"] for m in self.ledger.movements if m["karat"] == karat_id), D("0.000"))

    def by_key(self, key):
        row = next((m for m in self.ledger.movements if m["key"] == key), None)
        return None if row is None else {"id": row["id"], "karat_id": row["karat"], "movement_code": row["code"], "delta_grams": row["delta"], "created_by_user_id": row["user"]}

    def insert(self, *, karat_id, code, delta, reason, request_key, user_id, reference_type=None, reference_id=None):
        self.ledger.movements.append({"id": str(len(self.ledger.movements) + 1), "karat": karat_id, "code": code, "delta": delta, "reason": reason, "key": request_key, "user": user_id})
        return self.ledger.movements[-1]["id"]

    def audit(self, **values):
        self.ledger.audits.append(values)

    def balances(self):
        return [{"karat_id": 1, "karat_value": 21, "karat_name": "عيار 21", "grams": sum((m["delta"] for m in self.ledger.movements), D("0"))}]

    def recent(self):
        return [{"id": m["id"], "karat_value": 21, "movement_code": m["code"], "delta_grams": m["delta"], "reason": m["reason"], "created_at": "2026-11-06 10:00:00", "username": "admin"}
                for m in reversed(self.ledger.movements)]


def service_for(ledger, permissions=MANAGER):
    @contextmanager
    def transaction():
        yield type("C", (), {"ledger": ledger})()
    session = session_for(permissions, 9)
    return ClaspStockService(session, transaction_factory=transaction, repository=Repo, authorization=FakeAuthorization(session))


class ClaspStockTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger()
        self.service = service_for(self.ledger)
        self.service.receive(1, "50.000", "رفايع جديدة", str(uuid4()))

    def test_entry_and_adjustments_post_signed_grams_with_reason_and_audit(self):
        self.service.adjust(1, "OUT", "12.250", "اتباع بدون فاتورة", str(uuid4()))
        self.service.adjust(1, "IN", "2.000", "جرد", str(uuid4()))
        self.assertEqual([m["delta"] for m in self.ledger.movements], [D("50.000"), D("-12.250"), D("2.000")])
        self.assertEqual(self.ledger.audits[-1]["metadata"]["balance_after"], "39.750")
        overview = self.service.overview()
        self.assertEqual(overview["balances"][0]["grams"], "39.750")
        self.assertEqual(overview["recent"][0]["kind"], "زيادة")

    def test_a_removal_never_takes_the_balance_below_zero(self):
        with self.assertRaises(ClaspStockError) as caught:
            self.service.adjust(1, "OUT", "50.001", "خطأ", str(uuid4()))
        self.assertEqual(caught.exception.code, "CLASP_SHORTAGE")
        self.service.adjust(1, "OUT", "50.000", "صفر", str(uuid4()))
        self.assertEqual(self.service.overview()["balances"][0]["grams"], "0.000")

    def test_reason_weight_karat_and_key_are_validated(self):
        for args, code in (((1, "5", "", str(uuid4())), "REASON_REQUIRED"), ((1, "0", "x", str(uuid4())), "GRAMS_INVALID"), ((1, "1.0005", "x", str(uuid4())), "GRAMS_INVALID"),
                           ((2, "1", "x", str(uuid4())), "KARAT_INVALID"), ((1, "1", "x", "bad"), "REQUEST_INVALID")):
            with self.assertRaises(ClaspStockError) as caught:
                self.service.receive(*args)
            self.assertEqual(caught.exception.code, code)

    def test_an_exact_retry_replays_and_a_different_payload_is_refused(self):
        key = str(uuid4())
        first = self.service.receive(1, "3.000", "رفايع", key)
        again = self.service.receive(1, "3.000", "رفايع", key)
        self.assertTrue(again["replayed"])
        self.assertEqual((again["movement_id"], len(self.ledger.movements)), (first["movement_id"], 2))
        with self.assertRaises(ClaspStockError) as caught:
            self.service.receive(1, "4.000", "رفايع", key)
        self.assertEqual(caught.exception.code, "REQUEST_CONFLICT")

    def test_only_the_manage_right_writes_and_a_viewer_may_read(self):
        viewer = service_for(self.ledger, VIEWER)
        with self.assertRaises(PermissionError):
            viewer.receive(1, "1", "x", str(uuid4()))
        self.assertEqual(viewer.overview()["balances"][0]["grams"], "50.000")
        nobody = service_for(self.ledger, frozenset({"APP_LOGIN"}))
        with self.assertRaises(PermissionError):
            nobody.overview()
        self.assertFalse(nobody.can_manage())
        self.assertTrue(self.service.can_manage())


class ScreenTests(harness.InvoicePageTests):
    def setUp(self):
        super().setUp()
        self.ledger = Ledger()

        @contextmanager
        def transaction():
            yield type("C", (), {"ledger": self.ledger})()
        self.grant("CLASP_STOCK_MANAGE")
        session = self.controller.session
        service = ClaspStockService(session, transaction_factory=transaction, repository=Repo, authorization=FakeAuthorization(session))
        service.receive(1, "50.000", "رفايع جديدة", str(uuid4()))
        self.clasp_vm = self.controller.operational_viewmodels["claspStockVM"]
        self.clasp_vm._service = service
        self.clasp_vm.changed.emit()

    def test_the_manager_enters_and_removes_weight_with_real_clicks_at_all_sizes(self):
        self.open_settings_dialog(10, "reviewsOpenClasp", "claspStockDialog")
        self.wait_until(lambda: len(self.clasp_vm.balances) == 1, "balances loaded")
        self.assertFalse(self.item("claspSave").property("enabled"))
        self.choose("claspKarat", 0)
        self.click("claspKindOut")
        self.type_into("claspGrams", "12.500")
        self.type_into("claspNote", "قفل اتباع")
        self.wait_until(lambda: self.item("claspSave").property("enabled"), "ready")
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            self.scroll_dialog_to("claspStockDialog", "claspSave")
            self.assert_inside_window(self.item("claspSave"))
        self.click("claspSave")
        self.wait_until(lambda: len(self.ledger.movements) == 2, "posted")
        self.assertEqual((self.ledger.movements[-1]["delta"], self.ledger.movements[-1]["reason"]), (D("-12.500"), "قفل اتباع"))
        self.wait_until(lambda: "تم تسجيل نقص" in str(self.item("claspMessage").property("text")), "the result is shown")
        self.assertEqual(self.warnings, [])


for _cls in (ScreenTests,):
    for _name in dir(harness.InvoicePageTests):
        if _name.startswith("test_") and _name not in _cls.__dict__:
            setattr(_cls, _name, None)


if __name__ == "__main__":
    unittest.main()
