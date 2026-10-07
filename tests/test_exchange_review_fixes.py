"""Owner manual Exchange review (2026-10-03): gold and making are independent, the final amount follows
the EGP-5 rule, nothing final while the replacement making is missing, one canonical recalculation (no
stale totals or payments), visible identity on both sides, and the same piece never on both sides.

Prices of the manual test: 24K SHOP_SELL 7,003 / SHOP_BUY 6,979. Returned ring: 21K 5.000 g (4.375 pure),
original invoice making 1,100 (220/g)."""
from datetime import datetime, timezone
from decimal import Decimal as D
from types import MappingProxyType
import unittest

from services.exchange_service import ExchangeDraft, ExchangeService, ReplacementInput
from services.gold_price_service import BillingKaratPrice, BillingPriceSet
from tests import test_exchange_service as fx

OWNER_PRICES = {1: (D("6100.0000"), D("6125.0000")), 2: (D("5230.0000"), D("5250.0000")),
                3: (D("6979.0000"), D("7003.0000"))}


def owner_store(store):
    store.lines["500"].append(fx.line("9", 9, "SELL_INVENTORY", 1, "5.000", "205", "1100.00"))
    for item in (fx.item("205", 1, "5.000", "SOLD", name="دبلة"), fx.item("401", 1, "5.000", name="دبلة"),
                 fx.item("402", 3, "10.000", name="دبلة"), fx.item("403", 2, "4.500", name="دبلة")):
        store.items[item["inventory_item_id"]] = item


def draft(replacement, making, payments=(), account=None):
    return ExchangeDraft("500", ("9",), (ReplacementInput(replacement, making),), tuple(payments), account)


class OwnerScenarioTests(fx.ExchangeTestCase):
    def setUp(self):
        super().setUp()
        owner_store(self.store)
        self.service = self.make_service(fx.Prices(prices=OWNER_PRICES))

    def values(self, preview, *keys):
        return tuple(preview[k] for k in keys)

    def test_a_same_gold_different_making_is_not_zero(self):
        p = self.service.preview(draft("401", "300"))
        self.assertEqual(self.values(p, "gold_amount", "making_refund", "making_charge", "making_difference",
                                     "raw_amount", "signed_amount", "direction_text"),
                         ("0.00", "1100.00", "1500.00", "400.00", "400.00", "400.00", "على العميل"))

    def test_b_more_expensive_replacement_owner_case(self):
        p = self.service.preview(draft("402", "300"))
        # 10.000 − 4.375 = +5.625 pure × 7,003 = 39,391.875 → 39,391.88; making 3,000 − 1,100 = +1,900;
        # raw 41,291.88 → 41,290 (rounding −1.88). Never the old 41,079.38 (5.625 × 300 making).
        self.assertEqual(self.values(p, "pure_delta_grams", "price_per_gram", "gold_amount", "making_charge",
                                     "making_refund", "making_difference", "raw_amount", "rounding_adjustment",
                                     "signed_amount", "direction_text"),
                         ("5.625", "7003.0000", "39391.88", "3000.00", "1100.00", "1900.00", "41291.88", "-1.88",
                          "41290.00", "على العميل"))
        self.assertNotIn("41079.38", p.values())
        self.finalize(draft("402", "300", payments=(fx.cash("41290.00"),)))
        stored = self.store.lifecycle[0]["calculation"]
        self.assertEqual((stored["signed_amount"], stored["rounding_adjustment"], stored["making_charge"],
                          stored["making_refund"]), (D("41290.00"), D("-1.88"), D("3000.00"), D("1100.00")))
        # The returned piece is in Scrap (032), held for one manager decision.
        self.assertEqual(self.store.items["205"]["inventory_status"], "CONVERTED_TO_OLD_GOLD")
        self.assertEqual(self.store.items["402"]["inventory_status"], "SOLD")
        self.assertEqual(len(self.store.inspections), 1)

    def test_c_cheaper_replacement_without_making_has_no_final_amount(self):
        p = self.service.preview(draft("403", None))
        # 3.375 − 4.375 = −1.000 pure × SHOP_BUY 6,979 = 6,979 للعميل (gold only).
        self.assertEqual(self.values(p, "pure_delta_grams", "price_basis", "gold_amount", "making_refund",
                                     "making_charge", "making_difference", "signed_amount", "amount", "complete"),
                         ("-1.000", "SHOP_BUY", "6979.00", "1100.00", None, None, None, None, False))
        self.assertEqual(p["direction_text"], "الحساب غير مكتمل")
        self.assertNotIn("7230.43", [str(v) for v in p.values()])
        self.assertEqual(p["blocking_problems"][0]["code"], "REPLACEMENT_MAKING_REQUIRED")
        # A caller bypassing the screen cannot save it, whatever money it sends.
        self.assertRefused("REPLACEMENT_MAKING_REQUIRED", draft("403", None, payments=(fx.cash("6980.00"),)))
        # Once typed (0/g): −6,979 + 0 − 1,100 = −8,079 → −8,080 للعميل.
        p = self.service.preview(draft("403", "0"))
        self.assertEqual(self.values(p, "raw_amount", "signed_amount", "direction_text"),
                         ("-8079.00", "-8080.00", "للعميل"))

    def test_making_is_never_derived_from_the_gold_difference(self):
        # Same making rate; only the replacement's PHYSICAL weight matters (5 g → 1,500; 10 g → 3,000).
        five = self.service.preview(draft("401", "300"))
        ten = self.service.preview(draft("402", "300"))
        self.assertEqual((five["making_charge"], ten["making_charge"]), ("1500.00", "3000.00"))
        self.assertNotEqual(ten["making_charge"], "1687.50")             # 5.625 pure × 300 (old rule)
        # Changing karat / weight never invents making: the returned refund is always the original 1,100.
        self.assertEqual({p["making_refund"] for p in (five, ten, self.service.preview(draft("403", "0")))},
                         {"1100.00"})

    def test_directions(self):
        self.assertEqual(self.service.preview(draft("401", "300"))["direction"], "CUSTOMER_OWES")
        self.assertEqual(self.service.preview(draft("403", "0"))["direction"], "SHOP_OWES")
        same = self.service.preview(draft("401", "220"))                  # same gold, same making
        self.assertEqual((same["direction"], same["signed_amount"]), ("NONE", "0.00"))

    def test_the_same_physical_piece_is_never_on_both_sides(self):
        self.store.items["205"]["inventory_status"] = "IN_STOCK"          # even if it looked available
        with self.assertRaises(Exception) as context:
            self.service.preview(draft("205", "300"))
        self.assertIn(getattr(context.exception, "code", ""), ("REPLACEMENT_IS_RETURNED", "RETURNED_NOT_SOLD"))
        self.store.items["205"]["inventory_status"] = "SOLD"
        self.assertRefused("REPLACEMENT_IS_RETURNED", draft("205", "300"))


class ViewModelStateTests(fx.ExchangeTestCase):
    def setUp(self):
        super().setUp()
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.instance() or QGuiApplication([])
        owner_store(self.store)
        self.service = self.make_service(fx.Prices(prices=OWNER_PRICES))
        from app.viewmodels.exchange_viewmodel import ExchangeViewModel
        self.vm = ExchangeViewModel(self.session, self.service)
        self.vm._context = self.service.context("500")
        self.vm._selected = ["9"]

    def add(self, item_id, making):
        row = self.service.find_replacement_by_barcode(f"B{item_id}")
        self.vm._append_row(dict(row, making=making, identified_by="SCAN"))
        self.vm._preview_timer.stop()
        self.vm._on_preview((self.vm._economics, self.service.preview(self.vm._draft())), "")

    def test_removing_the_last_replacement_clears_everything(self):
        self.add("402", "300")
        self.vm.payFullAmount()
        self.assertEqual((self.vm.summary["finalAmount"], self.vm.amount), ("41,290.00 ج", "41290.00"))
        self.vm._preview_timer.stop()
        old = self.vm._preview
        key = self.vm._replacements[0]["key"]
        version = self.vm._economics
        self.vm.removeReplacement(key)
        self.assertEqual((self.vm.replacements, self.vm.amount, self.vm.accountAmount, self.vm.settlement), ([], "", "", {}))
        self.assertEqual(self.vm.summary, {"shown": True, "complete": False, "statusText": "الاستبدال غير مكتمل"})
        self.assertFalse(self.vm.canFinalize)
        self.vm._on_preview((version, old), "")                       # a late answer for the old draft
        self.assertEqual(self.vm.summary["statusText"], "الاستبدال غير مكتمل")
        self.assertFalse(self.vm.canFinalize)

    def test_changing_the_replacement_discards_the_previous_settlement(self):
        self.add("402", "300")
        self.vm.payFullAmount()
        self.vm.removeReplacement(self.vm._replacements[0]["key"])
        self.add("401", "300")
        self.assertEqual((self.vm.amount, self.vm.summary["finalAmount"]), ("", "400.00 ج"))
        self.assertEqual(self.vm.settlement["remainingText"], "المتبقي 400.00 ج")
        self.vm.setMaking(self.vm._replacements[0]["key"], "220")           # making changed: settled again
        self.assertEqual((self.vm._preview, self.vm.amount), ({}, ""))

    def test_missing_making_shows_an_incomplete_breakdown(self):
        self.add("403", "")
        s = self.vm.summary
        self.assertEqual((s["complete"], s["statusText"], s["goldAmount"], s["makingRefund"], s["makingReplacement"],
                          s["makingDifference"], s["finalAmount"]),
                         (False, "الحساب غير مكتمل", "6,979.00 ج للعميل", "1,100.00 ج", "غير محددة", "غير مكتمل", ""))
        self.assertEqual(self.vm.settlement, {})
        self.assertFalse(self.vm.canFinalize)

    def test_identity_is_visible_on_both_sides(self):
        self.add("401", "300")
        returned = next(l for l in self.vm.lines if l["lineId"] == "9")
        replacement = self.vm.replacements[0]
        self.assertEqual((returned["karat"], returned["weight"]),
                         (replacement["karat"], replacement["weight"]))                    # look alike
        self.assertEqual((returned["identity"], replacement["identity"]), ("B205  •  #205", "B401  •  #401"))
        self.store.items["404"] = fx.item("404", 1, "5.000", name="دبلة")
        self.store.items["404"]["barcode_value"] = None
        self.assertEqual(self.vm._identity(None, "404"), "بدون باركود  •  #404")




class ExchangePageTests(fx.ExchangeTestCase):
    """The real ExchangeWorkspace.qml over a real ExchangeViewModel and the fake-store service (no database)."""

    def setUp(self):
        super().setUp()
        from pathlib import Path
        from PySide6.QtCore import Qt, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtQuickControls2 import QQuickStyle
        QQuickStyle.setStyle("Material")
        app = QGuiApplication.instance() or QGuiApplication([])
        app.setLayoutDirection(Qt.RightToLeft)
        owner_store(self.store)
        self.service = self.make_service(fx.Prices(prices=OWNER_PRICES))
        from app.viewmodels.exchange_viewmodel import ExchangeViewModel
        self.vm = ExchangeViewModel(self.session, self.service)
        self.engine = QQmlApplicationEngine()
        self.warnings = []
        self.engine.warnings.connect(lambda errors: self.warnings.extend(e.toString() for e in errors))
        self.engine.rootContext().setContextProperty("exchangeVM", self.vm)
        qml_dir = (Path(__file__).resolve().parents[1] / "qml").as_uri()
        source = (f'import QtQuick\nimport QtQuick.Window\nimport "{qml_dir}"\n'
                  'Window { width: 1080; height: 680; visible: true\n'
                  '  ExchangeWorkspace { anchors.fill: parent } }')
        self.engine.loadData(source.encode(), QUrl(qml_dir + "/"))
        self.root = self.engine.rootObjects()[0]
        self.vm.open("500")
        self.wait_until(lambda: bool(self.vm._context))
        self.vm._selected = ["9"]
        self.vm._economics_changed()

    def tearDown(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtTest import QTest
        self.vm._preview_timer.stop()
        self.root.hide()
        QTest.qWait(30)
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def wait_until(self, predicate):
        from PySide6.QtTest import QTest
        for _ in range(250):
            if predicate():
                return
            QTest.qWait(20)
        self.fail("timed out")

    def find(self, name, parent=None):
        parent = parent or self.root.contentItem()
        if parent.objectName() == name and parent.property("visible"):
            return parent
        for child in parent.childItems():
            found = self.find(name, child)
            if found is not None:
                return found
        return None

    def text(self, name):
        item = self.find(name)
        self.assertIsNotNone(item, name)
        text = item.property("text")
        return text if text is not None else item.property("value")

    def scan(self, barcode):
        before = len(self.vm.replacements)
        self.vm.addReplacementBarcode(barcode)
        self.wait_until(lambda: len(self.vm.replacements) > before)

    def test_incomplete_until_making_then_breakdown_and_identity(self):
        self.assertEqual(self.text("exchangeIncomplete"), "الاستبدال غير مكتمل")
        self.assertFalse(self.find("exchangeSave").property("enabled") if self.find("exchangeSave") else False)
        self.scan("B403")
        self.wait_until(lambda: self.vm.summary.get("statusText") == "الحساب غير مكتمل")
        index = [l["lineId"] for l in self.vm.lines].index("9")
        self.assertEqual(self.text(f"exchangeReturnedIdentity_{index}"), "B205  •  #205")
        self.assertEqual(self.text("exchangeReplacementIdentity_0"), "B403  •  #403")
        self.assertIn("6,979.00", self.text("exchangeGoldAmount"))
        self.assertEqual(self.text("exchangeMakingReplacement"), "غير محددة")
        save = self.find("exchangeSave")
        self.assertFalse(save is not None and save.property("enabled"))
        self.vm.setMaking(self.vm._replacements[0]["key"], "300")
        self.wait_until(lambda: self.vm.summary.get("complete"))
        # 4.5 g × 300 = 1,350; −6,979 + 1,350 − 1,100 = −6,729 → −6,730 للعميل.
        self.wait_until(lambda: self.find("exchangeFinalAmount") is not None)
        self.assertIn("6,730.00", self.text("exchangeFinalAmount"))
        self.assertIn("250.00", self.text("exchangeMakingDifference"))
        self.vm.removeReplacement(self.vm._replacements[0]["key"])
        self.wait_until(lambda: self.find("exchangeIncomplete") is not None
                        and self.text("exchangeIncomplete") == "الاستبدال غير مكتمل")
        self.assertIsNone(self.find("exchangeFinalAmount"))
        self.assertEqual(self.warnings, [])


    def test_money_fields_fit_the_largest_amount_without_shrinking_the_font(self):
        """UX-EXCH-09: the settlement money fields keep their large bold figures and grow to fit 9,999,999.99
        (never clipped) at 1440×900, 1280×800 and 1080×680 (alignment stays Field's AlignRight)."""
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QFontMetricsF
        from PySide6.QtTest import QTest
        self.scan("B402")
        self.vm.setMaking(self.vm._replacements[0]["key"], "300")
        self.wait_until(lambda: self.vm.summary.get("complete") and bool(self.vm.settlement.get("needsMoney")))
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(120)
                for name in ("exchangeAmount", "exchangeAccountAmount"):
                    field = self.find(name)
                    self.assertIsNotNone(field, name)
                    font = field.property("font")
                    self.assertGreaterEqual(font.pixelSize(), 18, name)          # large, never shrunk
                    self.assertTrue(font.bold(), name)
                    self.assertGreaterEqual(field.height(), 40, name)
                    room = field.width() - field.property("leftPadding") - field.property("rightPadding")
                    self.assertGreater(room, QFontMetricsF(font).horizontalAdvance("9,999,999.99"), name)
                    corner = field.mapToScene(QPointF(field.width(), field.height()))
                    origin = field.mapToScene(QPointF(0, 0))
                    self.assertTrue(origin.x() >= 0 and corner.x() <= width and corner.y() <= height, name)
        self.assertEqual(self.warnings, [])

if __name__ == "__main__":
    unittest.main()
