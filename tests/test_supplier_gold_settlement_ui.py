"""Direct supplier gold settlement (045) on the real Main.qml: the «تسوية ذهب» dialog, the history rows and the reversal dialog, with real mouse
clicks at the three target sizes. The settlement service and the real CorrectionService run over the in-memory database of tests/market_trip_fakes.py;
the supplier READ service is a small stub that renders the ledger from that same database (the real read SQL was exercised on the masgold_rehearsal
sandbox). The post-commit refresh is checked through the REAL controller wiring: the balance service stand-in is counted."""
from datetime import datetime
from decimal import Decimal as D
from unittest.mock import patch
import unittest

from PySide6.QtCore import QMetaObject, QObject, QPointF, Qt
from PySide6.QtTest import QTest

from services.correction_rules import correction_code
from services.correction_service import CorrectionService
from services.supplier_gold_settlement_service import SupplierGoldSettlementService
from tests import test_invoice_page_qml as harness
from tests.market_trip_fakes import Corrections, Customers, Invoices, OWNER, ScrapRepo, SettlementRepo, build
from tests.supplier_fakes import ALL as SUPPLIER_RIGHTS

TODAY = "2026-10-10"
SUPPLIER = "11"                      # the fake inactive 18K supplier: an inactive supplier still receives a settlement
# What an OWNER really has: the supplier rights (view / manage / edit / post / intake) AND the settlement rights.
RIGHTS = tuple(SUPPLIER_RIGHTS) + ("SUPPLIER_GOLD_SETTLE", "SUPPLIER_GOLD_SETTLE_REVERSE", "TRANSACTION_CORRECT")


class LiveDetailStub:
    """SupplierService.list_suppliers / supplier_detail, rendering the gold ledger from the fake database's settlements and corrections."""

    def __init__(self, db):
        self.db = db

    def list_suppliers(self, search="", active=False, _expected_actor=None, **kw):
        detail = self.supplier_detail(SUPPLIER)
        return [{**detail["supplier"], "gold_status": "RESOLVED", "gold_direction": detail["balances"]["gold_direction"],
                 "gold_balance": detail["balances"]["gold_balance"], "making_direction": "SETTLED", "making": D("0.00"), "posted_invoice_count": 0}]

    def supplier_detail(self, supplier_id, _expected_actor=None):
        reversals = {c["source_id"]: c for c in self.db.corrections if c["source_type"] == "SUPPLIER_GOLD_SETTLEMENT"}
        rows, running = [], D("0.000")
        for settlement in sorted(self.db.settlements.values(), key=lambda s: s["id"]):
            sid, credit = settlement["id"], settlement["credited_weight_grams"]
            reversal = reversals.get(str(sid))
            code = None if reversal is None else correction_code("FULL_REVERSAL", reversal["correction_id"])
            running -= credit
            rows.append({"id": f"g{sid}", "occurred_at": datetime(2026, 10, 10, 10, 0), "movement": "تسوية ذهب", "karat_value": settlement["target_karat_value"],
                         "weight_delta": -credit, "weight_balance_after": running, "reference": f"SG{sid:06d}", "source_id": str(sid), "actor": "المالك",
                         "settlement_id": str(sid), "delivered_weight": settlement["delivered_weight_grams"],
                         "delivered_karat": settlement["delivered_karat_value"], "reversed_by": code, "reverses_reference": None})
            if reversal is not None:
                running += credit
                rows.append({"id": f"r{sid}", "occurred_at": datetime(2026, 10, 10, 11, 0), "movement": "عكس تسوية ذهب", "karat_value": settlement["target_karat_value"],
                             "weight_delta": credit, "weight_balance_after": running, "reference": code, "source_id": reversal["correction_id"],
                             "actor": "المالك", "settlement_id": None, "delivered_weight": None, "delivered_karat": None, "reversed_by": None,
                             "reverses_reference": f"SG{sid:06d}"})
        direction = "SETTLED" if running == 0 else "SUPPLIER_OWES"
        return {"supplier": {"id": SUPPLIER, "code": "S000011", "name": "مورد متوقف", "phone": "", "notes": "", "is_active": False, "karat_id": 2,
                             "karat_value": 18, "karat_locked": False, "is_supplier": True, "is_intermediary": False, "role_text": "مورد"},
                "balances": {"gold_status": "RESOLVED", "gold_karat_value": 18, "gold_by_karat": [], "gold_direction": direction,
                             "gold_balance": running, "making_direction": "SETTLED", "making": D("0.00")},
                "gold_ledger": rows, "making_ledger": []}


class GoldSettlementPageTests(harness.InvoicePageTests):
    def setUp(self):
        super().setUp()
        session = self.controller.session
        self.db, _, self.authorization, _, _ = build(permissions=OWNER, session=session)
        started = patch("repositories.supplier_gold_settlement_repository.SupplierGoldSettlementRepository", SettlementRepo)
        started.start()
        self.addCleanup(started.stop)
        corrections = CorrectionService(session, authorization=self.authorization, transaction_factory=self.db.transaction, invoice_repository=Invoices,
                                        correction_repository=Corrections, customer_repository=Customers)
        self.svm = self.controller.operational_viewmodels["supplierVM"]
        self.svm._service = LiveDetailStub(self.db)
        self.controller.operational_viewmodels["supplierPurchaseVM"]._service = type(
            "NoInvoices", (), {"list_invoices": lambda s, supplier, status=None, **kw: []})()      # the purchase side is not under test
        self.svm._settlements = SupplierGoldSettlementService(session, transaction_factory=self.db.transaction, repository=SettlementRepo, scrap=ScrapRepo,
                                                              authorization=self.authorization, corrections=corrections)
        self.grant(*RIGHTS)
        self.svm.changed.emit()
        self.strips = []                                                  # every reload of the top-bar balances, through the real controller wiring
        original = harness.Balances.strip

        def counting(service, **kwargs):
            self.strips.append(1)
            return original(service, **kwargs)
        counter = patch.object(harness.Balances, "strip", counting)
        counter.start()
        self.addCleanup(counter.stop)

    # ------------------------------------------------------------ helpers
    def rights(self, *codes, revoke=()):
        self.grant(*codes, revoke=revoke)
        self.svm.changed.emit()
        QTest.qWait(60)

    def wait_idle(self):
        self.wait_until(lambda: not self.svm.busy, "worker idle")
        QTest.qWait(80)

    def open_supplier(self):
        self.root.setProperty("currentPage", 14)
        QTest.qWait(200)
        self.svm.refresh()
        self.wait_idle()
        self.svm.selectSupplier(SUPPLIER)
        self.wait_until(lambda: "id" in self.svm.detail, "supplier detail")
        self.wait_idle()

    def open_dialog(self):
        self.click("settleGoldButton")
        self.wait_until(lambda: self.item("goldSettleDialog").property("opened"), "settlement dialog")
        self.wait_until(lambda: self.svm.settlementContext.get("supplierId"), "settlement context")
        QTest.qWait(120)

    def type_in(self, name, text):
        self.click(name)
        QTest.keyClick(self.root, Qt.Key_A, Qt.ControlModifier)
        self.type_text(text)
        QTest.qWait(60)

    def pick_karat(self, karat_id):
        """Choose the entry of the delivered-karat ComboBox: a real click first, then the choice exactly as the control reports it."""
        options = [""] + [row["karatId"] for row in self.svm.settlementContext["scrap"]]
        index = options.index(str(karat_id))
        combo = self.item("goldSettleKarat")
        self.click(combo)
        QTest.qWait(80)
        for child in combo.findChildren(QObject):
            if child.metaObject().indexOfMethod("close()") >= 0 and child.property("visible"):
                QMetaObject.invokeMethod(child, "close")
                QTest.qWait(150)
        combo.setProperty("currentIndex", index)
        combo.activated[int].emit(index)
        QTest.qWait(150)

    def double_click(self, name):
        item = self.item(name)
        self.assertTrue(item.property("enabled"))
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)

    def visible_texts(self, name, parent=None):
        parent = parent or self.root.contentItem()
        found = [str(parent.property("text"))] if parent.objectName() == name and parent.property("visible") else []
        for child in self.child_items(parent):
            found += self.visible_texts(name, child)
        return found

    def closed(self, name):
        self.wait_until(lambda: not self.item(name).property("visible"), f"{name} closed")

    def post_one(self, weight="10"):
        self.open_dialog()
        self.pick_karat("1")
        self.type_in("goldSettleWeight", weight)
        self.click("confirmGoldSettleButton")
        self.wait_until(lambda: len(self.db.settlements) >= 1, "settlement posted")
        self.closed("goldSettleDialog")
        self.wait_idle()
        QTest.qWait(150)

    # ------------------------------------------------------------ tests
    def test_owner_settles_21k_scrap_against_an_18k_supplier_with_real_clicks_at_1080(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.open_supplier()
        self.assert_inside_window(self.item("settleGoldButton"))
        self.open_dialog()
        self.assertEqual(self.text("goldSettleSupplierKarat"), "عيار حساب المورد: عيار 18")
        self.assertFalse(self.item("confirmGoldSettleButton").property("enabled"))              # no karat and no weight yet
        self.assertEqual(self.item("goldSettleKarat").property("currentIndex"), 0)               # the placeholder: nothing defaults
        self.pick_karat("1")
        self.assertFalse(self.item("confirmGoldSettleButton").property("enabled"))              # still no weight
        self.type_in("goldSettleWeight", "10")
        self.assertEqual(self.text("goldSettleAvailable"), "المتاح في كسر المحل: 95.000 جرام عيار 21")          # the held 5 g are not available
        self.assertEqual(self.text("goldSettleDelivered"), "المُسلَّم: 10.000 جرام عيار 21")
        self.assertEqual(self.text("goldSettleFormula"), "الحساب: 10.000 × 21 ÷ 18")
        self.assertEqual(self.text("goldSettleCredited"), "يُحتسب للمورد: 11.667 جرام عيار 18")
        self.assertIn("11.666667", self.text("goldSettleAnalytical"))
        self.assertIn("0.000333", self.text("goldSettleAnalytical"))
        self.assertIsNone(self.visible_item("goldSettleShort"))
        self.assertTrue(self.item("confirmGoldSettleButton").property("enabled"))                # no extra confirmation checkbox is needed
        for name in ("goldSettleKarat", "goldSettleWeight", "goldSettleDate", "confirmGoldSettleButton", "cancelGoldSettleButton"):
            self.assert_inside_window(self.item(name))
        strips = len(self.strips)
        self.click("confirmGoldSettleButton")
        self.wait_until(lambda: len(self.db.settlements) == 1, "settlement posted")
        self.closed("goldSettleDialog")
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual(self.db.physical(1), D("90.000"))                                       # the delivered grams left Scrap once
        self.assertEqual(self.db.sup_gold[0]["weight_delta"], D("-11.667"))
        self.assertEqual(self.db.cash_moves[0]["amount_delta"], D("50000.00"))                   # no cash was written
        self.assertEqual(len(self.db.cash_moves), 1)
        self.assertIn("SG000001", self.svm.message)
        self.assertEqual(self.text("goldPosition"), "لنا عند المورد 11.667 جرام عيار 18")
        self.assertGreater(len(self.strips), strips)                                             # the top-bar balances reloaded after the commit
        self.assertEqual(self.warnings, [])

    def test_more_than_the_available_grams_is_flagged_and_cannot_be_submitted(self):
        self.open_supplier()
        self.open_dialog()
        self.pick_karat("1")
        self.type_in("goldSettleWeight", "95.001")                                               # physical 100 would cover it, the hold does not
        self.assertIsNotNone(self.visible_item("goldSettleShort"))
        self.assertFalse(self.item("confirmGoldSettleButton").property("enabled"))
        self.type_in("goldSettleWeight", "95")
        self.assertIsNone(self.visible_item("goldSettleShort"))
        self.assertTrue(self.item("confirmGoldSettleButton").property("enabled"))
        self.assertEqual(self.db.settlements, {})

    def test_an_invalid_weight_is_explained_and_never_submittable(self):
        self.open_supplier()
        self.open_dialog()
        self.pick_karat("1")
        for bad in ("abc", "0", "1.2345"):
            self.type_in("goldSettleWeight", bad)
            self.assertFalse(self.item("confirmGoldSettleButton").property("enabled"), bad)
            self.assertIsNotNone(self.visible_item("goldSettlePreviewError"), bad)
            self.assertIsNone(self.visible_item("goldSettleCredited"), bad)
        self.assertEqual(self.db.settlements, {})

    def test_a_refusal_keeps_every_field_and_a_corrected_retry_posts_once(self):
        self.open_supplier()
        self.open_dialog()
        self.pick_karat("1")
        self.type_in("goldSettleWeight", "7")
        self.type_in("goldSettleReference", "REF-9")
        self.type_in("goldSettleNotes", "ملاحظة")
        self.authorization.permissions = OWNER - {"SUPPLIER_GOLD_SETTLE"}                        # revoked in the database after the dialog opened
        self.click("confirmGoldSettleButton")
        self.wait_until(lambda: self.svm.errorMessage != "", "refusal")
        QTest.qWait(80)
        self.assertTrue(self.item("goldSettleDialog").property("visible"))                       # still open
        self.assertTrue(self.text("goldSettleError"))
        self.assertEqual((self.text("goldSettleWeight"), self.text("goldSettleReference"), self.text("goldSettleNotes")), ("7", "REF-9", "ملاحظة"))
        options = [""] + [row["karatId"] for row in self.svm.settlementContext["scrap"]]
        self.assertEqual(self.item("goldSettleKarat").property("currentIndex"), options.index("1"))        # the chosen karat is kept too
        self.assertEqual((self.db.settlements, self.db.physical(1)), ({}, D("100.000")))
        self.authorization.permissions = OWNER
        self.click("confirmGoldSettleButton")
        self.wait_until(lambda: len(self.db.settlements) == 1, "settlement posted")
        self.closed("goldSettleDialog")
        self.assertEqual(next(iter(self.db.settlements.values()))["reference"], "REF-9")
        self.assertEqual(self.db.physical(1), D("93.000"))

    def test_a_double_click_posts_exactly_once(self):
        self.open_supplier()
        self.open_dialog()
        self.pick_karat("1")
        self.type_in("goldSettleWeight", "4")
        self.double_click("confirmGoldSettleButton")
        self.wait_until(lambda: len(self.db.settlements) >= 1, "settlement posted")
        self.closed("goldSettleDialog")
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual((len(self.db.settlements), len(self.db.sup_gold), self.db.physical(1)), (1, 1, D("96.000")))

    def test_history_shows_delivered_beside_credited_and_the_owner_reverses_it_with_a_reason(self):
        self.open_supplier()
        self.post_one("10")
        self.click("tabGold")
        QTest.qWait(150)
        self.assertIn("سُلِّم 10.000 جرام عيار 21 ← احتُسب 11.667 جرام عيار 18", " ".join(self.visible_texts("settlementDetail")))
        reverse = self.visible_item("reverseSettlementButton")
        self.assertIsNotNone(reverse)
        self.assert_inside_window(reverse)
        strips = len(self.strips)
        self.click(reverse)
        self.wait_until(lambda: self.item("reverseSettlementDialog").property("opened"), "reversal dialog")
        self.assertIn("استُرد فعليًا", self.text("reverseSettlementNote"))                        # the gold was actually recovered
        self.assertFalse(self.item("confirmReverseSettlementButton").property("enabled"))        # a reason is mandatory
        self.assert_inside_window(self.item("confirmReverseSettlementButton"))
        self.type_in("reverseSettlementReason", "تسوية خاطئة")
        self.assertTrue(self.item("confirmReverseSettlementButton").property("enabled"))
        self.click("confirmReverseSettlementButton")
        self.wait_until(lambda: len(self.db.corrections) == 1, "reversed")
        self.closed("reverseSettlementDialog")
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual(self.db.physical(1), D("100.000"))                                      # the stored delivered grams came back
        self.assertEqual(sum((m["weight_delta"] for m in self.db.sup_gold), D("0.000")), D("0.000"))
        states = " | ".join(self.visible_texts("settlementState"))
        self.assertIn("معكوسة بالعملية RV000001", states)
        self.assertIn("عكس التسوية SG000001", states)
        self.assertIsNone(self.visible_item("reverseSettlementButton"))                          # a reversed settlement cannot be reversed again
        self.assertGreater(len(self.strips), strips)
        self.assertEqual(self.warnings, [])

    def test_buttons_follow_the_rights_a_manager_settles_but_cannot_reverse_a_cashier_cannot_settle(self):
        self.open_supplier()
        self.post_one("2")
        self.click("tabGold")
        QTest.qWait(120)
        self.assertIsNotNone(self.visible_item("reverseSettlementButton"))
        self.rights(revoke=("SUPPLIER_GOLD_SETTLE_REVERSE",))                                    # MANAGER
        self.assertIsNotNone(self.visible_item("settleGoldButton"))
        self.assertIsNone(self.visible_item("reverseSettlementButton"))
        self.rights(revoke=("SUPPLIER_GOLD_SETTLE",))                                            # no settlement right at all
        self.assertIsNone(self.visible_item("settleGoldButton"))

    def test_the_server_refuses_when_the_reversal_right_is_revoked_even_though_the_button_was_visible(self):
        self.open_supplier()
        self.post_one("2")
        self.click("tabGold")
        QTest.qWait(120)
        self.click(self.visible_item("reverseSettlementButton"))
        self.wait_until(lambda: self.item("reverseSettlementDialog").property("opened"), "reversal dialog")
        self.type_in("reverseSettlementReason", "x")
        self.authorization.permissions = OWNER - {"SUPPLIER_GOLD_SETTLE_REVERSE"}
        self.click("confirmReverseSettlementButton")
        self.wait_until(lambda: self.svm.errorMessage != "", "refusal")
        self.assertEqual((self.db.corrections, self.db.physical(1)), ([], D("98.000")))
        self.assertTrue(self.item("reverseSettlementDialog").property("visible"))
        self.assertTrue(self.text("reverseSettlementError"))

    def test_the_dialogs_fit_at_every_target_size(self):
        self.open_supplier()
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(150)
                self.assert_inside_window(self.item("settleGoldButton"))
                self.open_dialog()
                self.pick_karat("1")
                self.type_in("goldSettleWeight", "10")
                for name in ("goldSettleKarat", "goldSettleWeight", "goldSettleDate", "goldSettleCredited", "confirmGoldSettleButton",
                             "cancelGoldSettleButton"):
                    self.assert_inside_window(self.item(name))
                dialog = self.item("goldSettleDialog")
                self.assertLessEqual(dialog.property("height"), height)
                self.assertLessEqual(dialog.property("width"), width)
                dialog.close()
                self.closed("goldSettleDialog")
                self.item("goldSettleWeight").setProperty("text", "")
        self.assertEqual(self.warnings, [])

    def test_the_reversal_dialog_fits_at_every_target_size(self):
        self.open_supplier()
        self.post_one("3")
        self.click("tabGold")
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(200)
                button = self.visible_item("reverseSettlementButton")
                self.assertIsNotNone(button)
                self.assert_inside_window(button)
                self.click(button)
                self.wait_until(lambda: self.item("reverseSettlementDialog").property("opened"), "reversal dialog")
                for name in ("reverseSettlementReason", "confirmReverseSettlementButton"):
                    self.assert_inside_window(self.item(name))
                self.item("reverseSettlementDialog").close()
                self.closed("reverseSettlementDialog")
        self.assertEqual(self.warnings, [])


# The base class carries the invoice-page tests: only this module's own tests are collected here.
for _inherited in [n for n in dir(harness.InvoicePageTests) if n.startswith("test_")]:
    setattr(GoldSettlementPageTests, _inherited, None)


if __name__ == "__main__":
    unittest.main()
