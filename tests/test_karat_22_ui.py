"""22K on the real pages (real clicks, real Main.qml / InvoicePage.qml, no live database):
the Scrap strip with four karat boxes, the 22K old-gold chip + a complete 22K old-gold purchase, and the Gold Price dialog
that labels 22K as derived from 24K. Sizes: 1440x900, 1280x800, 1080x680."""
from decimal import Decimal as D
from unittest import mock

from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from tests import test_gold_price_qml as price_harness
from tests import test_invoice_page_qml as harness
from tests import test_invoice_service as service_fixture

K22 = 4
P22 = (D("6501.9167"), D("6528.5000"))
SIZES = ((1440, 900), (1280, 800), (1080, 680))
BOXES = ("balanceBoxCash", "balanceBox_21", "balanceBox_18", "balanceBox_22", "balanceBox_24")


def strip_with_22k(self, **kwargs):
    cash = sum((m["amount_delta"] for m in self.store.cash_movements), D("0.00"))
    repo = harness.InvoiceRepo(type("C", (), {"store": self.store})())
    grams = {row["karat_value"]: row["balance_grams"] for row in repo.old_gold_balances()}
    return {"cash_balance": str(cash),
            "old_gold": [{"karat": k, "grams": str(grams.get(k, D("0.000")))} for k in (21, 18, 22, 24)]}


class Karat22BillingPageTests(harness.InvoicePageTests):
    def setUp(self):
        super().setUp()                                                              # the legacy 3-karat page first ...
        for patcher in (mock.patch.dict(service_fixture.KARATS, {K22: 22}), mock.patch.dict(service_fixture.PRICES, {K22: P22}),
                        mock.patch.object(harness.Balances, "strip", strip_with_22k)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store.old_gold_opening[K22] = D("50.000")
        self.vm.refreshPrices()                                                      # ... then 22K is published
        self.wait_until(lambda: len(self.vm.priceRows) == 4 and not self.vm.busy, "22K price row not loaded")
        balances = self.controller.operational_viewmodels["balancesVM"]
        balances.refresh() if hasattr(balances, "refresh") else None

    def test_the_scrap_strip_shows_four_karat_boxes_in_one_row_at_every_size(self):
        self.select_salesperson()
        balances = self.controller.operational_viewmodels["balancesVM"]
        self.wait_until(lambda: balances.ready)
        self.assertEqual([self.text(n) for n in ("balanceCash", "balanceKarat_21", "balanceKarat_18", "balanceKarat_22",
                                                 "balanceKarat_24")], ["0.00 ج", "100.000", "0.000", "50.000", "0.000"])
        self.assertEqual([(self.item(b).property("caption"), self.item(b).property("karat")) for b in BOXES[1:]],
                         [("كسر", "21"), ("كسر", "18"), ("كسر", "22"), ("كسر", "24")])
        colours = [self.item(n).property("accent").name() for n in BOXES]
        self.assertEqual(len(set(colours)), 5)                                    # 22K has its own colour, nothing shared
        balances._strip = {"cash_balance": "1250300.50", "old_gold": [
            {"karat": 21, "grams": "1234.567"}, {"karat": 18, "grams": "456.789"},
            {"karat": 22, "grams": "345.678"}, {"karat": 24, "grams": "78.901"}]}
        balances.changed.emit()
        for width, height in SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            with self.subTest(size=(width, height)):
                for name in BOXES + ("balanceKarat_22", "balanceStrip", "goldPriceArea", "topUserName", "logoutButton"):
                    self.assert_inside_window(self.item(name))
                for name in ("balanceCash", "balanceKarat_21", "balanceKarat_18", "balanceKarat_22", "balanceKarat_24"):
                    item = self.item(name)
                    self.assertLessEqual(item.property("contentWidth"), item.width() + 1, f"{name} clipped")
                centres = [self.item(n).mapToScene(QPointF(0, self.item(n).height() / 2)) for n in BOXES]
                self.assertLess(max(c.y() for c in centres) - min(c.y() for c in centres), 1.5, "strip wrapped")
                xs = [c.x() for c in centres]
                self.assertEqual(xs, sorted(xs, reverse=True))                    # النقدية | 21 | 18 | 22 | 24, right to left
                box = self.item("balanceBox_22")
                card = self.item("goldPriceArea")
                self.assertLessEqual(self.item("balanceStrip").mapToScene(QPointF(0, 0)).x() + self.item("balanceStrip").width(),
                                     card.mapToScene(QPointF(0, 0)).x() + 1, "strip runs into the price card")
                self.assertGreater(box.width(), 40)
                self.snapshot(f"strip22-{width}x{height}")

    def test_a_22k_old_gold_purchase_by_real_clicks_posts_22k_grams_at_the_derived_buy_price(self):
        self.select_salesperson()
        for width, height in SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(120)
            with self.subTest(size=(width, height)):
                chip = self.item("karatChip_22")
                self.assert_inside_window(chip)
                self.assertEqual([self.item(f"karatChip_{k}").property("visible") for k in (18, 21, 22, 24)], [True] * 4)
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.add_old_gold("22", "10")
        # 10 g * 6501.9167 = 65,019.17 - 1.5% (975.29) = 64,043.88 -> final 64,045 (EGP 5, once)
        self.wait_until(lambda: self.text("paymentDirectionLabel") == "المدفوع للعميل")
        self.receive("64045")
        self.settle()
        self.assertEqual(self.text("boughtWeights_22"), "مشترى 22K: 10.000 جرام")
        self.assertEqual(self.text("soldWeights_22"), "مباع 22K: —")
        self.assertEqual(self.text("boughtWeights_21"), "مشترى 21K: —")
        self.snapshot("old-gold-22k")
        self.finalize()
        movement = self.store.old_gold[0]
        self.assertEqual((movement["karat_id"], movement["gold_price_per_gram"], movement["cash_value"], movement["signed_weight"]),
                         (K22, P22[0], D("64043.88"), D("10.000")))
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("-64045.00")])
        self.assertEqual(self.text("balanceKarat_22"), "60.000")                  # 50 + 10, 22K only
        self.assertEqual(self.text("balanceKarat_21"), "100.000")

    def test_f7_and_manual_karat_choices_include_22k_when_a_22k_price_exists(self):
        self.select_salesperson()
        self.assertIn("22", [r["karat"] for r in self.vm.priceRows])
        self.assertTrue(self.item("karatChip_22").property("visible"))
        self.assertTrue(self.item("openStockSale").property("visible"))


class Karat22PriceDialogTests(price_harness.GoldPricePanelTests):
    def test_the_dialog_labels_22k_as_derived_from_24k_at_every_size(self):
        self.open_panel()
        vm = self.controller.gold_prices
        rows = [dict(karat=k, karat_id=str(i), shop_buy_price_per_gram=b, shop_sell_price_per_gram=s,
                     provider_timestamp="2026-10-09T09:00:00+00:00", derived=k == "22",
                     derived_note="مشتق من عيار 24" if k == "22" else "")
                for i, (k, b, s) in enumerate((("18", "5320.0000", "5341.0000"), ("21", "6207.0000", "6232.0000"),
                                               ("22", "6501.9167", "6528.5000"), ("24", "7093.0000", "7122.0000")), 1)]
        vm._data.update(prices=rows, mode="AUTO", status="FRESH", warnings=[])
        vm.changed.emit()
        for width, height in SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(120)
            with self.subTest(size=(width, height)):
                note = self.root.findChild(QObject, "goldCurrentPrices-derivedNote")
                self.assertTrue(note.property("visible"))
                self.assertEqual(note.property("value"), "عيار 22: مشتق من عيار 24")
                self.assert_in_viewport(note)
                for karat in ("18", "21", "22", "24"):
                    for column in (0, 1, 2):
                        cell = self.visual_item(f"goldCurrentPrices-{column}-{karat}")
                        self.assert_in_viewport(cell)
                        self.assertLessEqual(cell.property("contentWidth"), cell.width() + 1)
                buy, sell = (self.visual_item(f"goldCurrentPrices-{c}-22") for c in (1, 2))
                self.assertIn("6501.9167", buy.property("value"))
                self.assertIn("6528.5000", sell.property("value"))
                self.assertNotEqual(buy.property("color"), sell.property("color"))        # shop-buy vs shop-sell semantics kept
        self.assertEqual(self.warnings, [])

    def test_without_a_22k_row_no_derived_note_is_shown(self):
        self.open_panel()
        vm = self.controller.gold_prices
        vm._data.update(prices=[dict(karat="21", karat_id="2", shop_buy_price_per_gram="6207.0000",
                                     shop_sell_price_per_gram="6232.0000", provider_timestamp="2026-10-09T09:00:00+00:00",
                                     derived=False, derived_note="")], mode="AUTO", status="FRESH", warnings=[])
        vm.changed.emit()
        QTest.qWait(80)
        self.assertFalse(self.root.findChild(QObject, "goldCurrentPrices-derivedNote").property("visible"))


# unittest otherwise inherits every harness test, inflating the suite count.
for _name in dir(harness.InvoicePageTests):
    if _name.startswith("test_") and _name not in Karat22BillingPageTests.__dict__:
        setattr(Karat22BillingPageTests, _name, None)
for _name in dir(price_harness.GoldPricePanelTests):
    if _name.startswith("test_") and _name not in Karat22PriceDialogTests.__dict__:
        setattr(Karat22PriceDialogTests, _name, None)
