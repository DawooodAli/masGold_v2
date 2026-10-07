"""Purchasing & Suppliers V1 — view models and the real Main.qml page. Positions are worded with their direction
(never a bare signed number), the invoice totals come from the rules module, and the whole flow works with real
clicks at the target sizes: supplier → invoice with making groups → post → piece-by-piece intake."""
from datetime import date
from decimal import Decimal as D
from pathlib import Path
import unittest

from PySide6.QtCore import QObject
from PySide6.QtTest import QTest

from app.viewmodels import supplier_purchase_viewmodel as pvm_module
from app.viewmodels import supplier_viewmodel as svm_module
from app.viewmodels.supplier_purchase_viewmodel import SupplierPurchaseViewModel, variance_text
from app.viewmodels.supplier_viewmodel import (SupplierViewModel, eq21_position_text, gold_position_text,
                                               making_position_text)
from services import supplier_rules as rules
from tests import test_invoice_page_qml as harness
from tests.supplier_fakes import ALL, build, session_for


class TextTests(unittest.TestCase):
    def test_positions_always_carry_their_direction_in_words(self):
        self.assertEqual(gold_position_text(rules.SHOP_OWES, D("40.050")), "علينا للمورد 40.050 جرام")
        self.assertEqual(gold_position_text(rules.SUPPLIER_OWES, D("-9.950")), "لنا عند المورد 9.950 جرام")
        self.assertEqual(gold_position_text(rules.SHOP_OWES, D("100"), 21), "علينا للمورد 100.000 جرام عيار 21")
        self.assertEqual(making_position_text(rules.SHOP_OWES, D("6500")), "علينا للمورد 6,500.00 ج مصنعيات")
        self.assertEqual(making_position_text(rules.SUPPLIER_OWES, D("-1500")), "لنا عند المورد 1,500.00 ج مصنعيات")
        self.assertEqual(eq21_position_text(rules.SHOP_OWES, D("142.857")), "علينا للمورد 142.857 جرام (مكافئ عيار 21)")
        for settled in (gold_position_text(rules.SETTLED, 0), making_position_text(rules.SETTLED, 0),
                        eq21_position_text(rules.SETTLED, 0)):
            self.assertEqual(settled, "الحساب متوازن")

    def test_no_text_shows_a_bare_minus_or_plus_sign(self):
        for text in (gold_position_text(rules.SUPPLIER_OWES, D("-9.950")),
                     making_position_text(rules.SUPPLIER_OWES, D("-1500")),
                     eq21_position_text(rules.SUPPLIER_OWES, D("-3.5"))):
            self.assertNotIn("-", text)
            self.assertNotIn("−", text)

    def test_weight_variance_wording_is_neutral_never_a_shortage_claim(self):
        self.assertEqual(variance_text(D("-0.080")), "فرق الوزن: ⁦−0.080⁩")
        self.assertIn("فرق الوزن", variance_text(D("0.150")))
        for word in ("عجز", "نقص", "سرقة"):
            self.assertNotIn(word, variance_text(D("-5")))
            source = (Path(pvm_module.__file__).read_text(encoding="utf-8")
                      + Path(svm_module.__file__).read_text(encoding="utf-8"))
            self.assertNotIn(word, source)


class ViewModelTests(unittest.TestCase):
    def setUp(self):
        self.db, self.session, self.authorization, self.suppliers, self.purchases = build()
        self.svm = SupplierViewModel(self.session, self.suppliers)
        self.pvm = SupplierPurchaseViewModel(self.session, self.purchases)

    def test_ui_hints_follow_the_session_rights(self):
        self.assertEqual((self.svm.canView, self.svm.canManage, self.pvm.canEdit, self.pvm.canPost, self.pvm.canIntake),
                         (True,) * 5)
        limited = SupplierViewModel(session_for({"APP_LOGIN", "SUPPLIER_VIEW"}), self.suppliers)
        self.assertEqual((limited.canView, limited.canManage), (True, False))
        none = SupplierPurchaseViewModel(session_for({"APP_LOGIN"}), self.purchases)
        self.assertEqual((none.canView, none.canEdit, none.canPost, none.canIntake), (False,) * 4)

    def test_preview_uses_the_same_rules_as_the_service(self):
        groups = [{"weight": "40", "rate": "120", "description": ""}, {"weight": "35", "rate": "180", "description": ""},
                  {"weight": "25", "rate": "150", "description": ""}]
        preview = self.pvm.previewGroups(groups, 21)
        self.assertEqual((preview["valid"], preview["weightText"], preview["makingText"], preview["eq21Text"]),
                         (True, "100.000", "14,850.00 ج", "100.000"))
        self.assertEqual(preview["groupMaking"], ["4,800.00 ج", "6,300.00 ج", "3,750.00 ج"])
        self.assertEqual(self.pvm.previewGroups(groups, 18)["eq21Text"], "85.714")
        bad = self.pvm.previewGroups([{"weight": "", "rate": "5", "description": ""}], 21)
        self.assertEqual((bad["valid"], bad["makingText"]), (False, "—"))
        self.assertIn("وزن", bad["error"])

    def test_supplier_list_and_detail_are_formatted_from_the_service_results(self):
        sid = self.suppliers.create_supplier("مورد")
        draft = self.purchases.create_draft(sid, 1, "R1", "2026-10-08",
                                            [{"weight": "100", "making_per_gram": "65"}], "", "22222222-2222-4222-8222-000000000001")
        self.purchases.post_invoice(draft["id"], "22222222-2222-4222-8222-000000000002")
        self.svm._suppliers = self.suppliers.list_suppliers()
        row = self.svm.suppliers[0]
        self.assertEqual((row["goldText"], row["goldTone"], row["makingTone"], row["stateText"]),
                         ("علينا للمورد 100.000 جرام (مكافئ عيار 21)", "shopOwes", "shopOwes", "نشط"))
        self.assertEqual(row["makingText"], "علينا للمورد 6,500.00 ج مصنعيات")
        self.svm._selected = sid
        self.svm._detail = self.suppliers.supplier_detail(sid)
        detail = self.svm.detail
        self.assertEqual(detail["goldLines"][0]["text"], "علينا للمورد 100.000 جرام عيار 21")
        self.assertEqual((detail["goldLedger"][0]["side"], detail["goldLedger"][0]["weight"], detail["goldLedger"][0]["karat"]),
                         ("علينا للمورد", "100.000", 21))
        self.assertEqual(detail["makingLedger"][0]["amount"], "6,500.00 ج")
        self.assertEqual(detail["makingLedger"][0]["balance"], "علينا للمورد 6,500.00 ج مصنعيات")

    def test_invoice_detail_shows_progress_and_variance_as_information(self):
        sid = self.suppliers.create_supplier("مورد")
        draft = self.purchases.create_draft(sid, 1, "R1", "2026-10-08",
                                            [{"weight": "100", "making_per_gram": "65"}], "", "33333333-3333-4333-8333-000000000001")
        self.purchases.post_invoice(draft["id"], "33333333-3333-4333-8333-000000000002")
        group = self.purchases._repository(self.db).groups(draft["id"])[0]["id"]
        self.purchases.add_piece(draft["id"], group, 1, "99.920", "33333333-3333-4333-8333-000000000003")
        self.pvm._detail = self.purchases.invoice_detail(draft["id"])
        detail = self.pvm.detail
        self.assertEqual((detail["weightText"], detail["actualText"], detail["pieceCount"], detail["posted"]),
                         ("100.000", "99.920", 1, True))
        self.assertEqual(detail["varianceText"], "فرق الوزن: ⁦−0.080⁩")
        self.assertEqual(detail["remainingText"], "المتبقي للإدخال (مرجعي): 0.080")
        self.assertEqual(detail["groups"][0]["rateText"], "65.00 ج / جرام")
        self.assertEqual((detail["pieces"][0]["barcode"], detail["pieces"][0]["hasBarcode"]), ("01261008", True))


class SupplierPageTests(harness.InvoicePageTests):
    """The real Main.qml with the supplier view models over the in-memory database; real clicks."""

    NAME = "مورد الاختبار"

    def setUp(self):
        super().setUp()
        vms = self.controller.operational_viewmodels
        self.svm, self.pvm, self.master = vms["supplierVM"], vms["supplierPurchaseVM"], vms["masterDataVM"]
        self.db, _, self.authorization, suppliers, purchases = build(session=self.controller.session)
        self.svm._service, self.pvm._service = suppliers, purchases
        self.master._karats = [{"id": 1, "karat_value": 21}, {"id": 2, "karat_value": 18}]
        self.master._item_types = [{"id": 1, "name": "خاتم"}, {"id": 2, "name": "سلسلة"}]
        self.master._categories, self.master._factories = [], []
        self.master.dataChanged.emit()

    def rights(self, *codes):
        self.grant(*codes)
        self.svm.changed.emit()
        self.pvm.changed.emit()
        QTest.qWait(60)

    def open_suppliers(self):
        self.root.setProperty("currentPage", 14)
        QTest.qWait(200)

    def wait_idle(self):
        self.wait_until(lambda: not self.svm.busy and not self.pvm.busy, "workers idle")
        QTest.qWait(80)

    def type_in(self, name, text):
        self.click(name)
        self.type_text(text)

    def test_the_page_and_its_nav_button_follow_the_view_right(self):
        self.assertIsNone(self.visible_item("suppliersNavButton"))
        self.rights("SUPPLIER_VIEW")
        self.assertIsNotNone(self.visible_item("suppliersNavButton"))
        self.assertIsNone(self.visible_item("newSupplierButton"))                 # no SUPPLIER_MANAGE
        self.assertIsNone(self.visible_item("newInvoiceButton"))
        self.open_suppliers()
        self.assertIsNotNone(self.visible_item("suppliersPage"))
        self.assertEqual(self.warnings, [])

    def test_the_whole_workflow_with_real_clicks_at_1080(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.rights(*ALL)
        self.open_suppliers()
        # ---- supplier
        self.click("suppliersNavButton")
        self.click("newSupplierButton")
        self.type_in("supplierNameField", "supplier-A")
        self.click("saveSupplierButton")
        self.wait_until(lambda: len(self.db.suppliers) == 1, "supplier saved")
        self.wait_until(lambda: not self.item("supplierDialog").property("visible"), "dialog closed")   # exit transition
        self.wait_idle()
        self.wait_until(lambda: self.svm.selectedSupplierId != "" and "id" in self.svm.detail, "supplier selected")
        self.assertEqual(self.text("goldPosition"), "الحساب متوازن")
        # ---- invoice with two making groups: totals come from the rules module
        self.click("newInvoiceButton")
        QTest.qWait(200)
        self.assertEqual(self.warnings, [])
        self.wait_until(lambda: self.item("newSupplierInvoiceDialog").property("opened"), "invoice dialog")
        self.type_in("groupWeight_0", "40")
        self.type_in("groupRate_0", "120")
        self.click("addGroupButton")
        self.type_in("groupWeight_1", "60")
        self.type_in("groupRate_1", "100")
        QTest.qWait(80)
        self.assertEqual(self.text("invoiceTotalWeight"), "وزن المورد: 100.000")
        self.assertEqual(self.text("invoiceTotalMaking"), "إجمالي المصنعيات: 10,800.00 ج")
        self.assertEqual(self.text("invoiceTotalEq21"), "مكافئ عيار 21: 100.000")
        for name in ("postInvoiceButton", "saveDraftButton"):
            self.assert_inside_window(self.item(name))
        self.click("postInvoiceButton")
        self.wait_until(lambda: any(i["status"] == "POSTED" for i in self.db.invoices.values()), "posted")
        self.assertEqual(([m["weight_delta_grams"] for m in self.db.gold], [m["amount_delta"] for m in self.db.making]),
                         ([D("100.000")], [D("10800.00")]))
        # ---- the intake dialog opens on the posted invoice
        self.wait_until(lambda: self.item("supplierIntakeDialog").property("opened"), "intake dialog")
        self.wait_until(lambda: self.pvm.detail.get("posted") is True, "invoice detail")
        QTest.qWait(150)
        self.click("intakeGroup_1")
        self.type_in("pieceWeightField", "12.340")
        self.assert_inside_window(self.item("addPieceButton"))
        self.click("addPieceButton")
        self.wait_until(lambda: len(self.db.items) == 1, "piece created")
        self.wait_idle()
        (item,) = self.db.items.values()
        self.assertEqual((item["karat_id"], item["weight_grams"], item["acquisition_making_per_gram"],
                          item["acquisition_source"], item["barcode"]), (1, D("12.340"), D("100.00"), "SUPPLIER_PURCHASE", "01261008"))
        self.assertIn("01261008", self.text("lastPieceText"))
        self.assertIn("فرق الوزن", self.text("intakeProgress"))
        self.assertEqual(len(self.db.gold), 1)                                    # intake never touches the ledgers
        self.assertEqual(self.warnings, [])

    def test_the_controls_fit_at_every_target_size(self):
        self.rights(*ALL)
        self.open_suppliers()
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(120)
                self.assert_inside_window(self.item("newSupplierButton"))
                self.click("newSupplierButton")
                dialog = self.item("supplierDialog")
                self.wait_until(lambda: dialog.property("opened"), "supplier dialog")
                self.assert_inside_window(self.item("saveSupplierButton"))
                dialog.close()
                self.wait_until(lambda: not dialog.property("visible"), "closed")
        self.assertEqual(self.warnings, [])

    def test_a_user_without_the_post_right_sees_no_post_button_and_the_service_refuses_anyway(self):
        self.rights("SUPPLIER_VIEW", "SUPPLIER_MANAGE", "SUPPLIER_INVOICE_EDIT")
        self.assertFalse(self.pvm.canPost)
        self.open_suppliers()
        sid = self.svm._service.create_supplier(self.NAME)
        self.svm.refresh()
        self.wait_idle()
        self.svm.selectSupplier(sid)
        self.wait_idle()
        self.click("newInvoiceButton")
        self.assertIsNone(self.visible_item("postInvoiceButton"))
        self.assertIsNotNone(self.visible_item("saveDraftButton"))
        with self.assertRaises(PermissionError):                                 # bypassing the hidden button
            self.pvm._service.post_invoice("1", "44444444-4444-4444-8444-000000000001")
        self.assertEqual(self.db.gold, [])


for _name in dir(harness.InvoicePageTests):
    if _name.startswith("test_") and _name not in SupplierPageTests.__dict__:
        setattr(SupplierPageTests, _name, None)


if __name__ == "__main__":
    unittest.main()
