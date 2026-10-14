"""Purchasing & Suppliers V1 — view models and the real Main.qml page. Positions are worded with their direction
(never a bare signed number), the invoice totals come from the rules module, and the whole flow works with real
clicks at the target sizes: supplier → invoice with making groups → post → piece-by-piece intake."""
from datetime import date
from decimal import Decimal as D
from pathlib import Path
import unittest

from PySide6.QtCore import Q_ARG, QMetaObject, QObject, QPointF, Qt
from PySide6.QtTest import QTest

from app.viewmodels import supplier_purchase_viewmodel as pvm_module
from app.viewmodels import supplier_viewmodel as svm_module
from app.viewmodels.supplier_purchase_viewmodel import SupplierPurchaseViewModel, variance_text
from app.viewmodels.supplier_viewmodel import (SupplierViewModel, gold_position_text,
                                               making_position_text)
from services import supplier_rules as rules
from tests import test_invoice_page_qml as harness
from tests.supplier_fakes import ALL, build, dev_categories, session_for


class TextTests(unittest.TestCase):
    def test_positions_always_carry_their_direction_in_words(self):
        self.assertEqual(gold_position_text(rules.SHOP_OWES, D("40.050")), "علينا للمورد 40.050 جرام")
        self.assertEqual(gold_position_text(rules.SUPPLIER_OWES, D("-9.950")), "لنا عند المورد 9.950 جرام")
        self.assertEqual(gold_position_text(rules.SHOP_OWES, D("100"), 21), "علينا للمورد 100.000 جرام عيار 21")
        self.assertEqual(making_position_text(rules.SHOP_OWES, D("6500")), "علينا للمورد 6,500.00 ج مصنعيات")
        self.assertEqual(making_position_text(rules.SUPPLIER_OWES, D("-1500")), "لنا عند المورد 1,500.00 ج مصنعيات")
        self.assertEqual(gold_position_text(rules.SHOP_OWES, D("7"), 18), "علينا للمورد 7.000 جرام عيار 18")   # physical grams
        self.assertEqual(gold_position_text(rules.SUPPLIER_OWES, D("-7"), 18), "لنا عند المورد 7.000 جرام عيار 18")
        for settled in (gold_position_text(rules.SETTLED, 0), gold_position_text(rules.SETTLED, 0, 18),
                        making_position_text(rules.SETTLED, 0)):
            self.assertEqual(settled, "الحساب متوازن")

    def test_position_parts_rebuild_the_exact_sentences_so_only_the_figure_is_emphasised(self):
        from app.viewmodels.supplier_viewmodel import position_parts
        for direction, amount in ((rules.SHOP_OWES, D("254.360")), (rules.SUPPLIER_OWES, D("-9.950")), (rules.SETTLED, 0)):
            gold = position_parts(direction, amount, "gold", 18)
            making = position_parts(direction, amount, "making")
            self.assertEqual(" ".join(x for x in (gold["lead"], gold["number"], gold["tail"]) if x),
                             gold_position_text(direction, amount, 18))
            self.assertEqual(" ".join(x for x in (making["lead"], making["number"], making["tail"]) if x),
                             making_position_text(direction, amount))
        self.assertEqual(position_parts(rules.SHOP_OWES, D("254.360"), "gold", 18),
                         {"lead": "علينا للمورد", "number": "254.360", "tail": "جرام عيار 18"})
        self.assertEqual(position_parts(rules.SUPPLIER_OWES, D("-1500"), "making")["lead"], "لنا عند المورد")   # direction kept
        self.assertEqual(position_parts(rules.SETTLED, 0, "making"), {"lead": "الحساب متوازن", "number": "", "tail": ""})

    def test_no_text_shows_a_bare_minus_or_plus_sign(self):
        for text in (gold_position_text(rules.SUPPLIER_OWES, D("-9.950")),
                     making_position_text(rules.SUPPLIER_OWES, D("-1500")),
                     gold_position_text(rules.SUPPLIER_OWES, D("-3.5"), 21)):
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
        preview = self.pvm.previewGroups(groups)
        self.assertEqual((preview["valid"], preview["weightText"], preview["makingText"]), (True, "100.000", "14,850.00 ج"))
        self.assertNotIn("eq21Text", preview)                                         # no Eq21 in a supplier invoice preview
        self.assertEqual(preview["groupMaking"], ["4,800.00 ج", "6,300.00 ج", "3,750.00 ج"])
        bad = self.pvm.previewGroups([{"weight": "", "rate": "5", "description": ""}])
        self.assertEqual((bad["valid"], bad["makingText"]), (False, "—"))
        self.assertIn("وزن", bad["error"])

    def test_supplier_list_and_detail_are_formatted_from_the_service_results(self):
        sid = self.suppliers.create_supplier("مورد", karat_id=1)
        draft = self.purchases.create_draft(sid, 1, "R1", "2026-10-08",
                                            [{"weight": "100", "making_per_gram": "65"}], "", "22222222-2222-4222-8222-000000000001")
        self.purchases.post_invoice(draft["id"], "22222222-2222-4222-8222-000000000002")
        self.svm._suppliers = self.suppliers.list_suppliers()
        row = self.svm.suppliers[0]
        self.assertEqual((row["goldText"], row["goldTone"], row["makingTone"], row["stateText"]),
                         ("علينا للمورد 100.000 جرام عيار 21", "shopOwes", "shopOwes", "نشط"))
        self.assertEqual(row["makingText"], "علينا للمورد 6,500.00 ج مصنعيات")
        self.svm._selected = sid
        self.svm._detail = self.suppliers.supplier_detail(sid)
        detail = self.svm.detail
        self.assertEqual(detail["goldLines"][0]["text"], "علينا للمورد 100.000 جرام عيار 21")
        self.assertEqual(detail["goldBreakdown"], [])                  # a lone 21K line only repeats the Eq21 headline
        self.assertEqual((detail["goldLedger"][0]["side"], detail["goldLedger"][0]["weight"], detail["goldLedger"][0]["karat"]),
                         ("علينا للمورد", "100.000", 21))
        self.assertEqual(detail["makingLedger"][0]["amount"], "6,500.00 ج")
        self.assertEqual(detail["makingLedger"][0]["balance"], "علينا للمورد 6,500.00 ج مصنعيات")

    def test_invoice_detail_shows_progress_and_variance_as_information(self):
        sid = self.suppliers.create_supplier("مورد", karat_id=1)
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

    def pick(self, name, index):
        """Choose entry `index` of a ComboBox: a real click first (reachable and enabled), then the choice exactly as the
        control reports it, currentIndex + activated(int)."""
        combo = self.item(name)
        self.click(combo)
        QTest.qWait(80)
        for child in combo.findChildren(QObject):                      # the combo's own drop-down (PySide cannot return it)
            if child.metaObject().indexOfMethod("close()") >= 0 and child.property("visible"):
                QMetaObject.invokeMethod(child, "close")
                QTest.qWait(150)
        combo.setProperty("currentIndex", index)
        combo.activated[int].emit(index)
        QTest.qWait(150)

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
        self.assertFalse(self.item("saveSupplierButton").property("enabled"))        # the karat is required: nothing defaults
        self.pick("supplierKaratCombo", 2)                                           # 18K (entries: placeholder, 21, 18)
        self.assertTrue(self.item("saveSupplierButton").property("enabled"))
        self.click("saveSupplierButton")
        self.wait_until(lambda: len(self.db.suppliers) == 1, "supplier saved")
        self.assertEqual(next(iter(self.db.suppliers.values()))["karat_id"], 2)
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
        self.assertIsNone(self.visual_item("groupCategory_0"))                       # no category editor in the group row
        self.assertTrue(self.item("postInvoiceButton").property("enabled"))         # weight and rate are all an invoice needs
        self.assertEqual(self.text("invoiceTotalWeight"), "وزن المورد: 100.000")
        self.assertEqual(self.text("invoiceTotalMaking"), "إجمالي المصنعيات: 10,800.00 ج")
        self.assertEqual(self.text("invoiceKaratText"), "عيار 18")                  # inherited, read-only: no karat dropdown
        self.assertIsNone(self.visual_item("invoiceKaratCombo"))
        self.assertIsNone(self.visual_item("invoiceTotalEq21"))                      # and no Eq21 total
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
                          item["acquisition_source"], item["barcode"]), (2, D("12.340"), D("100.00"), "SUPPLIER_PURCHASE", "01261008"))
        self.assertEqual(item["category_id"], 1)                                  # resolved from group 2's making rate, not chosen
        self.assertIn("01261008", self.text("lastPieceText"))
        self.assertIn("−87.660", self.text("intakeVariance"))                     # labelled tile: signed, value only
        self.assertEqual(self.text("intakeRemaining"), "87.660")
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
        sid = self.svm._service.create_supplier(self.NAME, karat_id=1)
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


    # ---------------------------------------------------------------- visual review (presentation only)
    KEYS = iter(range(1, 10_000))

    def key(self):
        return "66666666-6666-4666-8666-%012d" % next(self.KEYS)

    def posted_invoice(self, pieces=()):
        """A supplier with one POSTED invoice (two making groups: 40 g @ 120, 60 g @ 100) and optional pieces."""
        sid = self.svm._service.create_supplier(self.NAME, karat_id=1)
        purchases = self.pvm._service
        draft = purchases.create_draft(sid, 1, "R-77", "2026-10-08",
                                       [{"weight": "40", "making_per_gram": "120", "description": "خواتم"},
                                        {"weight": "60", "making_per_gram": "100", "description": "سلاسل"}], "", self.key())
        purchases.post_invoice(draft["id"], self.key())
        groups = purchases._repository(self.db).groups(draft["id"])
        for group_index, weight in pieces:
            purchases.add_piece(draft["id"], groups[group_index]["id"], 1, weight, self.key())
        self.svm.refresh()
        self.wait_idle()
        self.svm.selectSupplier(sid)
        self.pvm.showSupplier(sid)
        self.wait_idle()
        return sid, str(draft["id"])

    def test_the_tab_bar_shows_every_label_in_full_and_switches_tabs(self):
        self.rights(*ALL)
        self.open_suppliers()
        self.posted_invoice()
        labels = (("tabInvoices", "نظرة عامة"), ("tabGold", "سجل الذهب"), ("tabMaking", "سجل المصنعيات"))
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(150)
                tabs = [self.item(name) for name, _ in labels]
                for tab, (name, label) in zip(tabs, labels):
                    self.assertEqual(tab.property("text"), label)
                    self.assertTrue(40 <= tab.height() <= 44, (name, tab.height()))
                    # the cause of the old clipping: Material's 6 px inset shrank the background of a custom button
                    self.assertEqual((tab.property("topInset"), tab.property("bottomInset")), (0, 0))
                    content = tab.property("contentItem")
                    self.assertLessEqual(content.property("implicitWidth"), tab.width() - 20, name)     # whole label fits
                    self.assertGreaterEqual(content.height(), content.property("implicitHeight"), name)  # not clipped
                    self.assert_inside_window(tab)
                self.assertEqual(len({round(tab.width()) for tab in tabs}), 1)                          # equal tabs
                xs = [tab.mapToScene(QPointF(0, 0)).x() for tab in tabs]
                self.assertEqual(xs, sorted(xs, reverse=True))                       # RTL: the first tab is the rightmost
        self.click("tabGold")
        self.assertTrue(self.item("supplierGoldLedger").property("visible"))
        self.assertFalse(self.item("supplierInvoiceList").property("visible"))
        self.click("tabMaking")
        self.assertTrue(self.item("supplierMakingLedger").property("visible"))
        self.assertFalse(self.item("supplierGoldLedger").property("visible"))
        self.click("tabInvoices")
        self.assertTrue(self.item("supplierInvoiceList").property("visible"))
        self.assertEqual(self.warnings, [])

    def open_intake(self, invoice_id):
        dialog = self.item("supplierIntakeDialog")
        QMetaObject.invokeMethod(dialog, "openFor", Q_ARG("QVariant", invoice_id))
        self.wait_until(lambda: dialog.property("opened"), "intake dialog")
        self.wait_until(lambda: self.pvm.detail.get("posted") is True, "invoice detail")
        QTest.qWait(200)
        return dialog

    def test_the_intake_dialog_is_organised_fits_the_window_and_everything_is_reachable(self):
        self.rights(*ALL)
        self.open_suppliers()
        _, invoice_id = self.posted_invoice(pieces=((1, "12.340"),))
        dialog = self.open_intake(invoice_id)
        for width, height in ((1440, 900), (1080, 680)):
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(200)
                self.assertLessEqual(dialog.property("width"), width)
                self.assertLessEqual(dialog.property("height"), height)                   # bounded by the window
                self.assert_inside_window(self.item("intakeCloseButton"))
                # labelled summary: every existing value, signed and unit-labelled, none computed in QML
                self.assertEqual(self.text("intakeKarat"), "21")
                self.assertEqual(self.text("intakeSupplierWeight"), "100.000")
                self.assertIsNone(self.visual_item("intakeEq21"))                          # no Eq21 tile any more
                self.assertIn("10,800.00", self.text("intakeMaking"))
                self.assertEqual(self.text("intakeActual"), "12.340")
                self.assertEqual(self.text("intakeVariance"), "⁦−87.660⁩")
                self.assertEqual(self.text("intakeRemaining"), "87.660")
                self.assertEqual(self.text("intakePercent"), "⁦12.3%⁩")
                # compact group cards: every figure is still shown (labelled), in far less height than the old 148 px
                for index in (0, 1):
                    self.assertLess(self.item(f"intakeGroup_{index}").height(), 110, (width, index))
                shown, pending = set(), [self.item("intakeGroup_1")]
                while pending:                                               # the visual tree: every label of the card
                    node = pending.pop()
                    pending.extend(node.childItems())
                    if node.property("text"):
                        shown.add(str(node.property("text")).replace("⁦", "").replace("⁩", ""))
                for expected in ("وزن المورد (جرام):", "60.000", "مصنعية الجرام:", "100.00 ج / جرام",
                                 "إجمالي مصنعية المجموعة:", "6,000.00 ج", "عدد القطع:", "1",
                                 "الوزن المُدخل (جرام):", "12.340", "فرق الوزن (جرام):", "−47.660",
                                 "المتبقي للإدخال (مرجعي، جرام):", "47.660", "نسبة الإدخال:", "20.6%"):
                    self.assertIn(expected, shown, (width, expected))
                # a negative difference during incomplete intake is information: neutral colour, never red / amber
                self.assertNotIn(self.item("intakeVariance").property("color").name().lower(), ("#e06c75", "#e4a853"))
                # one scroll area: everything below the fold is reachable and the header stays put
                flick = self.item("intakeScroll")
                self.assertGreaterEqual(flick.property("contentHeight"), flick.height() - 1)
                content = flick.property("contentItem")
                maximum = max(0, flick.property("contentHeight") - flick.height())
                for name in ("intakeKarat", "pieceWeightField", "addPieceButton"):
                    target = self.item(name)
                    top = target.mapToItem(content, QPointF(0, 0)).y()
                    self.assertLessEqual(top + target.height(), flick.property("contentHeight") + 1, name)
                    flick.setProperty("contentY", min(maximum, max(0, top - 10)))
                    QTest.qWait(60)
                    inside = target.mapToItem(flick, QPointF(0, 0)).y()
                    self.assertTrue(-1 <= inside and inside + target.height() <= flick.height() + 1, (name, inside))
                    self.assert_inside_window(self.item("intakeCloseButton"))             # the close action never scrolls away
                flick.setProperty("contentY", 0)

    def test_the_selected_group_is_unmistakable_and_the_form_says_why_it_is_disabled(self):
        self.rights(*ALL)
        self.open_suppliers()
        _, invoice_id = self.posted_invoice()
        self.open_intake(invoice_id)
        first, second = self.item("intakeGroup_0"), self.item("intakeGroup_1")
        self.assertEqual((first.property("selected"), second.property("selected")), (False, False))
        self.assertEqual(self.text("pieceHint"), "اختر المجموعة أولًا")
        self.assertFalse(self.item("addPieceButton").property("enabled"))
        self.click("intakeGroup_1")
        QTest.qWait(80)
        self.assertEqual((first.property("selected"), second.property("selected")), (False, True))
        self.assertNotEqual(first.property("color"), second.property("color"))               # the selected card is tinted
        self.assertEqual(self.text("pieceHint"), "أدخل وزن القطعة")                      # next missing input
        self.assertFalse(self.item("addPieceButton").property("enabled"))
        self.click("pieceWeightField")
        self.type_text("5")
        QTest.qWait(80)
        self.assertTrue(self.item("addPieceButton").property("enabled"))
        self.assertIsNone(self.visible_item("pieceHint"))
        self.click("intakeGroup_0")
        QTest.qWait(80)
        self.assertEqual((first.property("selected"), second.property("selected")), (True, False))
        self.assertEqual(self.warnings, [])


    # ---------------------------------------------------------------- FR-03 / FR-04: invoice loading, gold card
    def seed_supplier(self, name, invoices=(), karat=1):
        """A supplier with POSTED invoices [(karat_id, weight)] created through the service (no view-model calls)."""
        self.grant(*ALL)
        sid = self.svm._service.create_supplier(name, karat_id=karat)
        purchases = self.pvm._service
        for karat_id, weight in invoices:
            draft = purchases.create_draft(sid, karat_id, f"{name}-{weight}", "2026-10-08",
                                           [{"weight": weight, "making_per_gram": "100"}], "", self.key())
            purchases.post_invoice(draft["id"], self.key())
        return sid

    def seed_invoice(self, supplier_id, weight):
        purchases = self.pvm._service
        draft = purchases.create_draft(supplier_id, 1, f"X-{weight}", "2026-10-09",
                                       [{"weight": weight, "making_per_gram": "100"}], "", self.key())
        purchases.post_invoice(draft["id"], self.key())

    def click_supplier(self, name):
        """A real mouse click on the row that shows this supplier's name (the delegates are anonymous)."""
        self.wait_until(lambda: name in [row["name"] for row in self.svm.suppliers], f"{name} listed")

        def texts(node):
            found = [str(node.property("text"))] if node.property("text") else []
            for child in node.childItems():
                found += texts(child)
            return found
        content = self.item("supplierList").property("contentItem")
        rows = [c for c in content.childItems() if c.height() > 20 and c.isVisible() and name in texts(c)]
        self.assertTrue(rows, f"no row shows {name}")
        row = rows[-1]
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier,
                         row.mapToScene(QPointF(row.width() / 2, row.height() / 2)).toPoint())

    def invoice_codes(self):
        return sorted(row["reference"] for row in self.pvm.invoices)

    def list_count(self):
        return self.item("supplierInvoiceList").property("count")

    def delay_invoice_lists(self, delays, fail=()):
        """Make the invoice-list read of chosen suppliers slow (seconds, by supplier id) or fail."""
        import time
        original = self.pvm._service.list_invoices

        def list_invoices(supplier, status=None, **kwargs):
            time.sleep(delays.get(str(supplier), 0))
            if str(supplier) in fail:
                raise RuntimeError("simulated read failure")
            return original(supplier, status, **kwargs)
        self.pvm._service.list_invoices = list_invoices

    def open_with_suppliers(self):
        self.rights(*ALL)
        self.open_suppliers()
        self.svm.refresh()
        self.wait_idle()

    def test_the_first_click_on_a_supplier_loads_its_invoices_once_without_a_second_click(self):
        a = self.seed_supplier("A-supplier", [(1, "10"), (1, "20")])
        self.seed_supplier("B-supplier", [(1, "5")])
        self.open_with_suppliers()
        self.assertEqual(self.list_count(), 0)
        before = self.pvm._request
        self.click_supplier("A-supplier")
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual(self.svm.selectedSupplierId, a)
        self.assertEqual((self.list_count(), self.pvm.invoicesState), (2, "loaded"))
        self.assertEqual(self.pvm._request - before, 1)                           # one request: no duplicate / loop
        self.assertFalse(self.item("invoiceListState").property("visible"))
        self.assertEqual(self.warnings, [])                                       # the handler used to die mid-way

    def test_a_selected_supplier_is_loaded_when_the_page_is_opened_or_returned_to(self):
        a = self.seed_supplier("A-supplier", [(1, "10")])
        self.rights(*ALL)
        self.svm.refresh()                                                        # selected before the page is ever shown
        self.wait_idle()
        self.svm.selectSupplier(a)
        self.wait_idle()
        self.open_suppliers()
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual((self.list_count(), self.pvm.invoicesState), (1, "loaded"))
        # leave, change the supplier's invoices elsewhere, return: the page reloads them (no click)
        self.root.setProperty("currentPage", 0)
        QTest.qWait(150)
        self.seed_invoice(a, "20")
        self.open_suppliers()
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual(self.list_count(), 2)
        self.assertEqual(self.warnings, [])

    def test_the_overview_shows_the_invoices_again_after_either_ledger_tab(self):
        self.seed_supplier("A-supplier", [(1, "10"), (1, "20")])
        self.open_with_suppliers()
        self.click_supplier("A-supplier")
        self.wait_idle()
        QTest.qWait(150)
        for ledger in ("tabGold", "tabMaking"):
            self.click(ledger)
            QTest.qWait(80)
            self.assertFalse(self.item("supplierInvoiceList").property("visible"))
            self.click("tabInvoices")
            QTest.qWait(80)
            self.assertTrue(self.item("supplierInvoiceList").property("visible"))
            self.assertEqual(self.list_count(), 2, ledger)
        self.assertEqual(self.warnings, [])

    def test_switching_suppliers_never_shows_the_previous_suppliers_invoices(self):
        self.seed_supplier("A-supplier", [(1, "10"), (1, "20")])
        b = self.seed_supplier("B-supplier", [(1, "5")])
        self.open_with_suppliers()
        self.click_supplier("A-supplier")
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual(self.invoice_codes(), ["A-supplier-10", "A-supplier-20"])
        self.delay_invoice_lists({b: 0.5})
        self.click_supplier("B-supplier")                                        # B is slow: A's rows must be gone at once
        QTest.qWait(120)
        self.assertEqual(self.list_count(), 0)
        self.assertEqual(self.text("invoiceListState"), "جار تحميل الفواتير...")
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual(self.invoice_codes(), ["B-supplier-5"])
        self.assertEqual(self.warnings, [])

    def test_a_slow_earlier_result_never_replaces_the_current_suppliers_invoices(self):
        a = self.seed_supplier("A-supplier", [(1, "10"), (1, "20")])
        b = self.seed_supplier("B-supplier", [(1, "5")])
        self.open_with_suppliers()
        self.delay_invoice_lists({a: 0.6})                                        # A answers AFTER B (out of order)
        self.click_supplier("A-supplier")
        QTest.qWait(80)
        self.click_supplier("B-supplier")
        self.wait_until(lambda: self.pvm.invoicesState == "loaded", "B loaded")
        QTest.qWait(900)                                                          # A's late answer arrives meanwhile
        self.wait_idle()
        self.assertEqual(self.svm.selectedSupplierId, b)
        self.assertEqual(self.invoice_codes(), ["B-supplier-5"])
        self.assertEqual(self.list_count(), 1)

    def test_empty_loading_and_failed_invoice_lists_are_different_states(self):
        c = self.seed_supplier("C-supplier")
        d = self.seed_supplier("D-supplier", [(1, "7")])
        self.open_with_suppliers()
        self.delay_invoice_lists({c: 0.4}, fail=(d,))
        self.click_supplier("C-supplier")
        QTest.qWait(100)
        self.assertEqual(self.text("invoiceListState"), "جار تحميل الفواتير...")      # not "no invoices" yet
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual((self.pvm.invoicesState, self.text("invoiceListState")), ("loaded", "لا توجد فواتير"))
        self.click_supplier("D-supplier")                                         # a failing read is not "no invoices"
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual(self.pvm.invoicesState, "failed")
        self.assertTrue(self.text("invoiceListState").startswith("تعذر تحميل الفواتير"))
        self.assertNotEqual(self.text("invoiceListState"), "لا توجد فواتير")

    def gold_card_texts(self):
        body = self.item("goldPosition").parentItem()
        return [str(c.property("text")) for c in body.childItems() if c.property("text") and c.objectName() != "settleGoldButton"]

    def test_the_gold_card_states_the_balance_once_in_grams_of_the_suppliers_karat(self):
        self.seed_supplier("A-supplier", [(1, "100.5"), (1, "50")])               # a 21K supplier
        self.seed_supplier("B-supplier", [(2, "7")], karat=2)                     # an 18K supplier: 7 g is 7 g of 18K
        self.seed_supplier("C-supplier")                                          # settled
        self.open_with_suppliers()
        self.click_supplier("A-supplier")
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual(self.text("goldAccountBalanceLabel"), "حساب المورد")
        self.assertEqual(self.gold_card_texts(), ["حساب المورد", "علينا للمورد 150.500 جرام عيار 21"])
        self.assertFalse(self.item("goldHeldPosition").property("visible"))
        self.assertTrue(self.item("supplierKaratChip").property("visible"))
        self.click_supplier("B-supplier")
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual(self.gold_card_texts(), ["حساب المورد", "علينا للمورد 7.000 جرام عيار 18"])     # not 6 g Eq21; exactly one supplier-balance line
        self.assertEqual(self.text("goldPosition"), "علينا للمورد 7.000 جرام عيار 18")
        self.click_supplier("C-supplier")
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual(self.gold_card_texts(), ["حساب المورد", "الحساب متوازن"])
        self.assertEqual(self.warnings, [])

    def test_the_selected_group_shows_its_allowance_and_a_known_excess_blocks_submission_without_losing_input(self):
        self.rights(*ALL)
        self.open_suppliers()
        _, invoice_id = self.posted_invoice()                                      # groups 40.000 and 60.000
        self.open_intake(invoice_id)
        self.assertEqual(self.text("pieceAllowance") if self.visual_item("pieceAllowance") else "", "")
        self.click("intakeGroup_0")
        QTest.qWait(80)
        self.assertEqual(self.text("pieceAllowance"), "المتاح للإدخال شامل السماحية: 40.333 جرام")
        self.type_into("pieceWeightField", "40.334")                               # one unit above the maximum
        QTest.qWait(80)
        self.assertTrue(self.text("pieceProblem").startswith("وزن القطعة يتجاوز المتاح للمجموعة. المتاح: 40.333 جرام"))
        self.assertIn("0.333", self.text("pieceProblem"))
        self.assertFalse(self.item("addPieceButton").property("enabled"))
        self.assertEqual(self.text("pieceWeightField"), "40.334")                  # the entered value is kept
        self.type_into("pieceWeightField", "40.333")                               # exactly the maximum
        QTest.qWait(80)
        self.assertIsNone(self.visible_item("pieceProblem"))
        self.assertTrue(self.item("addPieceButton").property("enabled"))
        self.click("addPieceButton")
        self.wait_until(lambda: len(self.db.items) == 1, "piece created")
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual(self.text("pieceAllowance"), "المتاح للإدخال شامل السماحية: 0.000 جرام")
        self.assertEqual(self.warnings, [])

    def test_a_stale_screen_is_refused_by_the_service_keeps_its_input_and_refreshes_the_allowance(self):
        self.rights(*ALL)
        self.open_suppliers()
        _, invoice_id = self.posted_invoice()
        self.open_intake(invoice_id)
        self.click("intakeGroup_1")                                                 # 60.000: allowance 60.333
        QTest.qWait(80)
        self.assertEqual(self.text("pieceAllowance"), "المتاح للإدخال شامل السماحية: 60.333 جرام")
        group_id = str(self.pvm._service._repository(self.db).groups(invoice_id)[1]["id"])
        self.pvm._service.add_piece(invoice_id, group_id, 1, "60.000", self.key())  # ANOTHER submission spends the weight
        self.assertEqual(self.text("pieceAllowance"), "المتاح للإدخال شامل السماحية: 60.333 جرام")   # this screen is stale
        self.type_into("pieceWeightField", "0.334")
        QTest.qWait(80)
        self.assertTrue(self.item("addPieceButton").property("enabled"))           # the UI cannot know yet
        before = (len(self.db.items), len(self.db.links), len(self.db.barcodes))
        self.click("addPieceButton")
        self.wait_idle()
        QTest.qWait(250)
        self.assertEqual((len(self.db.items), len(self.db.links), len(self.db.barcodes)), before)   # refused: nothing persisted
        self.assertTrue(self.text("intakeError").startswith("وزن القطعة يتجاوز المتاح للمجموعة. المتاح: 0.333 جرام"))
        self.assertEqual(self.text("pieceWeightField"), "0.334")                    # the user's input is intact
        self.assertEqual(self.text("pieceAllowance"), "المتاح للإدخال شامل السماحية: 0.333 جرام")   # refreshed
        self.assertFalse(self.item("addPieceButton").property("enabled"))          # now the excess is known
        self.assertEqual(self.warnings, [])

    def test_an_over_limit_legacy_group_explains_why_it_accepts_nothing_more(self):
        self.rights(*ALL)
        self.open_suppliers()
        _, invoice_id = self.posted_invoice()
        group_id = self.pvm._service._repository(self.db).groups(invoice_id)[0]["id"]
        piece = self.pvm._service.add_piece(invoice_id, str(group_id), 1, "40.000", self.key())
        self.db.items[int(piece["inventory_item_id"])]["weight_grams"] = D("45.000")   # entered before the rule existed
        self.pvm.refresh()
        self.wait_idle()
        self.open_intake(invoice_id)
        self.click("intakeGroup_0")
        QTest.qWait(80)
        self.assertEqual(self.text("pieceAllowance"), "المتاح للإدخال شامل السماحية: 0.000 جرام")
        self.assertIn("لا يمكن إدخال قطع إضافية", self.text("pieceProblem"))
        self.type_into("pieceWeightField", "0.001")
        QTest.qWait(80)
        self.assertFalse(self.item("addPieceButton").property("enabled"))
        self.assertEqual(self.db.items[int(piece["inventory_item_id"])]["weight_grams"], D("45.000"))   # data untouched
        self.assertEqual(self.warnings, [])


    # ---------------------------------------------------------------- the category is automatic (from the making rate)
    def test_a_new_invoice_is_created_and_loaded_without_any_category_and_shows_the_resolved_one_read_only(self):
        self.rights(*ALL)
        dev_categories(self.db)                                                      # A 50-150, B 200-250, C 250-300 ...
        self.open_suppliers()
        sid = self.svm._service.create_supplier(self.NAME, karat_id=1)
        self.svm.refresh()
        self.wait_idle()
        self.svm.selectSupplier(sid)
        self.pvm.showSupplier(sid)
        self.wait_idle()
        QTest.qWait(150)
        self.click("newInvoiceButton")
        self.wait_until(lambda: self.item("newSupplierInvoiceDialog").property("opened"), "invoice dialog")
        self.assertIsNone(self.visual_item("groupCategory_0"))                       # no category editor in the group row
        self.type_in("groupWeight_0", "40")
        self.type_in("groupRate_0", "100")                                           # A
        self.click("addGroupButton")
        self.type_in("groupWeight_1", "60")
        self.type_in("groupRate_1", "220")                                           # B
        QTest.qWait(80)
        self.assertTrue(self.item("postInvoiceButton").property("enabled"))          # weight and rate are all it needs
        self.assertTrue(self.item("saveDraftButton").property("enabled"))
        self.click("postInvoiceButton")
        self.wait_until(lambda: any(i["status"] == "POSTED" for i in self.db.invoices.values()), "posted")
        self.wait_until(lambda: self.item("supplierIntakeDialog").property("opened"), "intake dialog")
        self.wait_until(lambda: self.pvm.detail.get("posted") is True, "invoice detail")      # the detail loads (no error)
        QTest.qWait(250)
        self.assertEqual(self.pvm.errorMessage, "")
        self.assertEqual(len([i for i in self.db.invoices.values()]), 1)               # exactly one invoice, no duplicate
        self.click("intakeGroup_0")
        QTest.qWait(100)
        self.assertEqual(self.text("pieceInheritedCategory"), "الفئة: A")
        self.click("intakeGroup_1")
        QTest.qWait(100)
        self.assertEqual(self.text("pieceInheritedCategory"), "الفئة: B")           # switching never leaves a stale category
        self.assertIsNone(self.visual_item("pieceCategoryCombo"))                    # and it cannot be chosen
        self.type_into("pieceWeightField", "5")
        self.click("addPieceButton")
        self.wait_until(lambda: len(self.db.items) == 1, "piece created")
        self.wait_idle()
        (item,) = self.db.items.values()
        self.assertEqual((item["category_id"], item["acquisition_making_per_gram"]), (2, D("220.00")))
        self.assertEqual(self.warnings, [])

    def test_an_unresolvable_rate_shows_the_exact_reason_and_blocks_the_piece(self):
        self.rights(*ALL)
        dev_categories(self.db)
        self.open_suppliers()
        _, invoice_id = self.posted_invoice()                                        # groups: 120 EGP/g and 100 EGP/g (both A)
        groups = self.pvm._service._repository(self.db).groups(invoice_id)
        self.db.groups[int(groups[0]["id"])]["making_per_gram"] = D("175.00")        # a gap between A and B
        self.db.groups[int(groups[1]["id"])]["making_per_gram"] = D("250.00")        # an overlap: B and C
        self.open_intake(invoice_id)
        self.click("intakeGroup_0")
        QTest.qWait(100)
        self.assertEqual(self.text("pieceInheritedCategory"), "الفئة غير محددة")
        self.assertIn("لا توجد فئة نشطة تطابق مصنعية المجموعة (175.00 ج/جرام)", self.text("pieceCategoryProblem"))
        self.type_into("pieceWeightField", "5")
        self.assertFalse(self.item("addPieceButton").property("enabled"))
        self.assertEqual(self.text("pieceHint"), "الفئة غير محددة")
        self.click("intakeGroup_1")
        QTest.qWait(100)
        self.assertEqual(self.text("pieceInheritedCategory"), "الفئة غير محددة")
        self.assertIn("(B، C)", self.text("pieceCategoryProblem"))
        self.assertFalse(self.item("addPieceButton").property("enabled"))
        with self.assertRaises(ValueError):                                          # and the service refuses regardless
            self.pvm._service.add_piece(invoice_id, str(groups[1]["id"]), 1, "1.000", self.key())
        self.assertEqual(self.db.items, {})
        self.assertEqual(self.warnings, [])


    def test_an_existing_posted_invoice_opens_its_intake_dialog_with_summary_groups_form_and_categories(self):
        """SP000022: karat 21, 30 g, 6,500 EGP, no pieces; groups 10 g @ 100 (category A) and 20 g @ 275 (category C).
        The dialog used to open blank with the generic load error."""
        self.rights(*ALL)
        dev_categories(self.db)
        self.open_suppliers()
        sid = self.svm._service.create_supplier(self.NAME, karat_id=1)
        draft = self.pvm._service.create_draft(sid, 1, "SP22", "2026-10-08",
                                               [{"weight": "10", "making_per_gram": "100", "description": ""},
                                                {"weight": "20", "making_per_gram": "275", "description": ""}], "", self.key())
        self.pvm._service.post_invoice(draft["id"], self.key())
        self.svm.refresh()
        self.wait_idle()
        self.svm.selectSupplier(sid)
        self.pvm.showSupplier(sid)
        self.wait_idle()
        QTest.qWait(150)
        self.open_intake(str(draft["id"]))
        QTest.qWait(150)
        self.assertEqual(self.pvm.errorMessage, "")                                  # no generic load error
        self.assertFalse(self.visual_item("intakeError") and self.visual_item("intakeError").property("visible"))
        self.assertEqual((self.text("intakeKarat"), self.text("intakeSupplierWeight")), ("21", "30.000"))
        self.assertIn("6,500.00", self.text("intakeMaking"))
        self.assertEqual(self.text("intakeActual"), "0.000")                         # no pieces entered
        self.assertTrue(self.item("intakeGroup_0").property("visible") and self.item("intakeGroup_1").property("visible"))
        self.assertTrue(self.item("pieceWeightField").property("visible"))           # the entry form is there
        self.click("intakeGroup_0")
        QTest.qWait(100)
        self.assertEqual(self.text("pieceInheritedCategory"), "الفئة: A")
        self.assertEqual(self.text("pieceAllowance"), "المتاح للإدخال شامل السماحية: 10.333 جرام")
        self.click("intakeGroup_1")
        QTest.qWait(100)
        self.assertEqual(self.text("pieceInheritedCategory"), "الفئة: C")
        self.assertEqual(self.text("pieceAllowance"), "المتاح للإدخال شامل السماحية: 20.333 جرام")
        self.assertEqual(self.warnings, [])


    # ---------------------------------------------------------------- editing a saved draft (same form, same invoice)
    def seed_draft(self, supplier_id, reference="D-1"):
        rows = [{"weight": "40", "making_per_gram": "120", "description": "خواتم"},
                {"weight": "60", "making_per_gram": "100", "description": "سلاسل"}]
        return self.pvm._service.create_draft(supplier_id, 1, reference, "2026-10-08", rows, "ملاحظة المسودة", self.key())

    def open_details(self, invoice_id, **expect):
        dialog = self.item("supplierIntakeDialog")
        QMetaObject.invokeMethod(dialog, "openFor", Q_ARG("QVariant", str(invoice_id)))
        self.wait_until(lambda: dialog.property("opened"), "details dialog")
        self.wait_until(lambda: all(self.pvm.detail.get(k) is v for k, v in expect.items()), "invoice detail")
        QTest.qWait(200)
        return dialog

    def test_a_draft_is_edited_in_the_same_form_saved_under_the_same_invoice_and_posted_ones_have_no_edit(self):
        self.rights(*ALL)
        self.open_suppliers()
        sid = self.svm._service.create_supplier(self.NAME, karat_id=1)
        draft = self.seed_draft(sid)
        posted_draft = self.seed_draft(sid, "P-1")
        self.pvm._service.post_invoice(posted_draft["id"], self.key())
        self.svm.refresh()
        self.wait_idle()
        self.svm.selectSupplier(sid)
        self.pvm.showSupplier(sid)
        self.wait_idle()
        QTest.qWait(150)
        invoices_before = len(self.db.invoices)
        balances_before = self.svm._service.supplier_detail(sid)["balances"]
        ledgers_before = (list(self.db.gold), list(self.db.making))

        # a posted invoice offers no edit action, and the service refuses anyway
        self.open_details(posted_draft["id"], posted=True)
        self.assertIsNone(self.visible_item("editDraftButton"))
        self.item("supplierIntakeDialog").close()
        self.wait_until(lambda: not self.item("supplierIntakeDialog").property("visible"), "details closed")

        # a draft offers it, and the existing invoice form opens populated with the saved draft
        self.open_details(draft["id"], draft=True)
        self.click("editDraftButton")
        dialog = self.item("newSupplierInvoiceDialog")
        self.wait_until(lambda: dialog.property("opened"), "invoice form")
        self.wait_until(lambda: not self.item("supplierIntakeDialog").property("visible"), "details closed")
        QTest.qWait(150)
        self.assertEqual(self.text("invoiceReferenceField"), "D-1")
        self.assertEqual(self.text("invoiceDateField"), "2026-10-08")
        self.assertEqual(self.text("invoiceNotesField"), "ملاحظة المسودة")
        self.assertEqual(self.text("invoiceKaratText"), "عيار 21")                       # read-only, the supplier's karat
        self.assertEqual((self.text("groupWeight_0"), self.text("groupRate_0")), ("40.000", "120.00"))
        self.assertEqual((self.text("groupWeight_1"), self.text("groupRate_1")), ("60.000", "100.00"))
        self.assertEqual(self.text("saveDraftButton"), "حفظ التعديلات")
        self.assertIsNone(self.visible_item("postInvoiceButton"))                          # editing is not posting

        # closing WITHOUT saving keeps the saved draft exactly as it was
        self.type_into("groupRate_0", "999")
        dialog.close()
        self.wait_until(lambda: not dialog.property("visible"), "form closed")
        self.assertEqual(self.pvm._service.invoice_detail(draft["id"])["groups"][0]["making_per_gram"], D("120.00"))

        # reopen, add a group, change a weight and a rate, save
        self.open_details(draft["id"], draft=True)
        self.click("editDraftButton")
        self.wait_until(lambda: dialog.property("opened"), "invoice form")
        QTest.qWait(150)
        self.type_into("groupWeight_0", "45")
        self.type_into("groupRate_0", "130")
        self.click("addGroupButton")
        self.type_into("groupWeight_2", "5")
        self.type_into("groupRate_2", "90")
        self.type_into("invoiceReferenceField", "D-1-EDITED")
        QTest.qWait(100)
        self.assertEqual(self.text("invoiceTotalWeight"), "وزن المورد: 110.000")
        self.click("saveDraftButton")
        self.wait_until(lambda: self.pvm._service.invoice_detail(draft["id"])["supplier_reference"] == "D-1-EDITED", "saved")
        self.wait_until(lambda: not dialog.property("visible"), "form closed after saving")
        self.wait_idle()

        # reopen: persisted under the SAME invoice; nothing else moved
        detail = self.pvm._service.invoice_detail(draft["id"])
        self.assertEqual((detail["id"], detail["code"], detail["status"]), (draft["id"], draft["code"], "DRAFT"))
        self.assertEqual([(g["supplier_weight"], g["making_per_gram"]) for g in detail["groups"]],
                         [(D("45.000"), D("130.00")), (D("60.000"), D("100.00")), (D("5.000"), D("90.00"))])
        self.assertEqual((detail["supplier_weight"], detail["gross_making"]), (D("110.000"), D("12300.00")))
        self.assertEqual(len(self.db.invoices), invoices_before)                           # no new invoice
        self.assertEqual(self.svm._service.supplier_detail(sid)["balances"], balances_before)
        self.assertEqual((list(self.db.gold), list(self.db.making)), ledgers_before)       # no ledger movement
        self.open_details(draft["id"], draft=True)
        self.assertEqual(self.text("intakeSupplierWeight"), "110.000")                      # the reopened details agree
        self.assertEqual(self.warnings, [])


    # ---------------------------------------------------------------- supplier-specific karat
    def test_the_supplier_form_shows_the_karat_and_locks_it_once_an_invoice_exists(self):
        self.rights(*ALL)
        self.open_suppliers()
        self.svm._service.create_supplier("free", karat_id=1)
        used = self.svm._service.create_supplier("used", karat_id=1)
        self.pvm._service.create_draft(used, 1, "D", "2026-10-08", [{"weight": "5", "making_per_gram": "10"}], "", self.key())
        self.svm.refresh()
        self.wait_idle()
        for name, locked in (("free", False), ("used", True)):
            self.click_supplier(name)
            self.wait_idle()
            QTest.qWait(150)
            self.assertTrue(self.item("supplierKaratChip").property("visible"))
            self.click("editSupplierButton")
            dialog = self.item("supplierDialog")
            self.wait_until(lambda: dialog.property("opened"), "supplier dialog")
            self.assertEqual(self.item("supplierKaratCombo").property("currentText"), "عيار 21")
            self.assertEqual(self.item("supplierKaratCombo").property("enabled"), not locked)   # locked by any invoice, a draft too
            dialog.close()
            self.wait_until(lambda: not dialog.property("visible"), "closed")
        self.assertEqual(self.warnings, [])

    def test_an_unresolved_supplier_explains_itself_and_cannot_start_an_invoice(self):
        self.rights(*ALL)
        self.open_suppliers()
        sid = self.svm._service.create_supplier("legacy", karat_id=1)
        self.db.suppliers[int(sid)]["karat_id"] = None                                          # a legacy, unresolved supplier
        self.svm.refresh()
        self.wait_idle()
        self.click_supplier("legacy")
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual(self.text("goldPosition"), "العيار غير محدد")
        self.assertIn("حدّده من تعديل المورد", self.text("goldNote"))
        self.assertFalse(self.item("newInvoiceButton").property("enabled"))
        self.assertEqual(self.warnings, [])


for _name in dir(harness.InvoicePageTests):
    if _name.startswith("test_") and _name not in SupplierPageTests.__dict__:
        setattr(SupplierPageTests, _name, None)


if __name__ == "__main__":
    unittest.main()
