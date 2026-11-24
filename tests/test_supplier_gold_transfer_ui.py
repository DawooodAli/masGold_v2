"""Supplier-to-supplier gold transfer («تسوية ذهب من حساب مورد») on the real Main.qml with real clicks at the three target sizes.

The recipient is the fake inactive 18K supplier 11 of the settlement UI tests; the source is the fake supplier 5 «المتحدة» (21K) holding 265.100 g for the shop. The transfer service runs over the
in-memory ledger of tests/test_supplier_gold_transfer.py; the supplier READ service is a stub that builds the gold ledger from that same store."""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal as D
from types import SimpleNamespace
import unittest

from PySide6.QtTest import QTest

from services.supplier_gold_transfer_service import SupplierGoldTransferService
from tests import test_invoice_page_qml as harness
from tests import test_supplier_gold_settlement_ui as base
from tests.auth_fakes import FakeAuthorization
from tests.test_supplier_gold_transfer import FakeRepo, FakeTrips, Store

RECIPIENT = int(base.SUPPLIER)


class StoreDetailStub(base.LiveDetailStub):
    """The inactive 18K supplier 11 with a gold ledger built from the transfers of the store."""
    def __init__(self, store):
        self.store = store

    def supplier_detail(self, supplier_id, _expected_actor=None):
        detail = super().supplier_detail(supplier_id, _expected_actor)
        rows = []
        for t in self.store.transfers:
            if RECIPIENT not in (t["from_supplier_id"], t["to_supplier_id"]):
                continue
            reverses = t["reverses_id"] is not None
            reversed_ = any(x["reverses_id"] == t["id"] for x in self.store.transfers)
            weight = t["to_weight_grams"] if reverses else -t["to_weight_grams"]
            rows.append({"id": str(100 + t["id"]), "occurred_at": datetime(2026, 11, 24, 12, 0), "movement": "عكس تسوية ذهب من حساب مورد" if reverses else "تسوية ذهب من حساب مورد",
                         "karat_value": 18, "weight_delta": weight, "weight_balance_after": D("0.000"), "reference": f"GT{t['id']:06d}", "source_id": str(t["id"]),
                         "source_type": "SUPPLIER_GOLD_TRANSFER", "actor": "المالك", "transfer_id": str(t["id"]), "transfer_reversed": reversed_, "transfer_reverses": reverses,
                         "transfer_state": "معكوس بتسوية عكس" if reversed_ else "عكس تسوية ذهب بين موردين" if reverses else ""})
        return {**detail, "gold_ledger": rows}


class TransferPageTests(base.GoldSettlementPageTests):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.store.suppliers[RECIPIENT] = dict(Store.party(RECIPIENT, "مورد متوقف", 2, 18), is_active=0)
        FakeRepo.store = self.store
        FakeTrips.store = self.store
        session = self.controller.session

        @contextmanager
        def transaction():
            yield SimpleNamespace()
        self.svm._transfers = SupplierGoldTransferService(session, transaction_factory=transaction, repository=FakeRepo, trips=FakeTrips, authorization=FakeAuthorization(session))
        self.svm._service = StoreDetailStub(self.store)
        self.trip_settlements.sources = []                                           # no custody gold: the only source is the supplier account
        self.svm.changed.emit()

    def test_one_click_flow_takes_gold_from_the_supplier_account_at_1080x680(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.open_supplier()
        self.open_dialog()
        self.assertEqual([row["key"] for row in self.svm.supplierGoldSources], ["supplier:5"])
        self.pick_source("supplier:5")
        self.type_in("goldSettleWeight", "10")
        self.assertIn("المتحدة", self.text("goldSettleSourceText"))
        self.assertEqual(self.text("goldSettleAvailable"), "المتاح من هذا المصدر: 265.100 جرام")
        self.assertEqual(self.text("goldSettleDelivered"), "المُسلَّم: 10.000 جرام (عيار 21)")
        self.assertEqual(self.text("goldSettleCredited"), "يُحتسب للمورد: 11.667 جرام عيار 18")
        for name in ("goldSettleSource", "goldSettleWeight", "goldSettleDate", "confirmGoldSettleButton", "cancelGoldSettleButton"):
            self.assert_inside_window(self.item(name))
        self.assertTrue(self.item("confirmGoldSettleButton").property("enabled"))
        self.click("confirmGoldSettleButton")
        self.wait_until(lambda: len(self.store.transfers) == 1, "transfer posted")
        self.closed("goldSettleDialog")
        self.wait_idle()
        self.assertEqual((self.store.balance(5, 1), self.store.balance(RECIPIENT, 2)), (D("-255.100"), D("-11.667")))          # 265.1 - 10 held ; the 18K supplier owed nothing, now it holds 11.667 for the shop
        self.assertEqual(self.warnings, [])

    def test_too_much_cannot_be_submitted(self):
        self.open_supplier()
        self.open_dialog()
        self.pick_source("supplier:5")
        self.type_in("goldSettleWeight", "265.101")
        self.assertIsNotNone(self.visible_item("goldSettleShort"))
        self.assertFalse(self.item("confirmGoldSettleButton").property("enabled"))
        self.assertEqual(self.store.transfers, [])

    def test_the_transfer_is_reversed_from_its_ledger_row_with_a_reason_at_every_size(self):
        self.open_supplier()
        self.open_dialog()
        self.pick_source("supplier:5")
        self.type_in("goldSettleWeight", "10")
        self.click("confirmGoldSettleButton")
        self.wait_until(lambda: len(self.store.transfers) == 1, "transfer posted")
        self.closed("goldSettleDialog")
        self.wait_idle()
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(150)
                self.click("tabGold")
                QTest.qWait(200)
                button = self.visible_item("reverseTransferButton")
                self.assertIsNotNone(button)
                self.assert_inside_window(button)
                if (width, height) != harness.SIZES[-1]:
                    continue
                self.click(button)
                self.wait_until(lambda: self.item("reverseTransferDialog").property("opened"), "reverse dialog")
                self.assertFalse(self.item("confirmReverseTransferButton").property("enabled"))            # a reason is mandatory
                for name in ("reverseTransferReason", "confirmReverseTransferButton"):
                    self.assert_inside_window(self.item(name))
                self.type_in("reverseTransferReason", "سُجِّل بالخطأ")
                self.click("confirmReverseTransferButton")
                self.wait_until(lambda: len(self.store.transfers) == 2, "reversed")
                self.closed("reverseTransferDialog")
                self.wait_idle()
        QTest.qWait(200)
        self.assertEqual((self.store.balance(5, 1), self.store.balance(RECIPIENT, 2)), (D("-265.100"), D("0.000")))
        self.assertIsNone(self.visible_item("reverseTransferButton"))                                 # neither the original nor its reversal offers a second reversal
        self.assertEqual(self.warnings, [])

    def test_the_reverse_button_needs_the_owner_right(self):
        self.open_supplier()
        self.open_dialog()
        self.pick_source("supplier:5")
        self.type_in("goldSettleWeight", "1")
        self.click("confirmGoldSettleButton")
        self.wait_until(lambda: len(self.store.transfers) == 1, "transfer posted")
        self.closed("goldSettleDialog")
        self.wait_idle()
        self.rights(revoke=("SUPPLIER_GOLD_SETTLE_REVERSE",))
        self.click("tabGold")
        QTest.qWait(200)
        self.assertIsNone(self.visible_item("reverseTransferButton"))


# The base classes carry the invoice-page and settlement tests: only this module's own tests are collected here.
for _inherited in [n for n in dir(base.GoldSettlementPageTests) if n.startswith("test_")]:
    setattr(TransferPageTests, _inherited, None)


if __name__ == "__main__":
    unittest.main()
