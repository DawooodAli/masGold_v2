"""الخزنة in the real Main.qml: two cash tables (name + amount only), no bank / wallet cards, حركة نقدية جديدة,
event refresh and existing Invoice History / correction navigation — real clicks at 1440×900 / 1280×800 /
1080×680. The database is blocked."""
import copy
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from tests import test_invoice_page_qml as harness
from services.financial_account_service import CASH_LABELS, CASH_TYPES, CASH_TYPE_TEXT

HUGE = "9007199254740993"                                      # beyond JavaScript's exact integer range


def row(i, label, amount, invoice_id=None, operation_id=None):
    return {"id": str(i), "label": CASH_LABELS[label], "amount": amount, "invoice_id": invoice_id,
            "operation_id": operation_id}


IN_ROWS = [row(1, "SALE", "25000.00", invoice_id=HUGE), row(2, "CUSTOMER_PAYMENT", "5000.00"),
           row(3, "REVENUE", "2000.00", operation_id="3"), row(4, "TRANSFER_IN", "10000.00", operation_id="4")]
OUT_ROWS = [row(5, "OLD_GOLD_PURCHASE", "15000.00", invoice_id="21"), row(6, "EXPENSE", "1500.00", operation_id="6"),
            row(7, "SALARY", "12000.00", operation_id="7"), row(8, "REPAIR", "600.00", operation_id="8"),
            row(9, "TRANSFER_OUT", "20000.00", operation_id="9")]


class Service:
    def __init__(self):
        self.calls, self.balance = [], "82540.00"

    def can_view(self):
        return True

    def cash_view(self, start, end, label, *, _expected_actor=None):
        self.calls.append(("cash_view", start, end, label))
        return {"account": {"key": "CASH:1", "name": "الخزنة الرئيسية", "balance": self.balance},
                "start": start or "2026-09-30", "end": end or "2026-09-30",
                "in": {"rows": copy.deepcopy(IN_ROWS), "total": "42000.00", "count": 4},
                "out": {"rows": copy.deepcopy(OUT_ROWS), "total": "49100.00", "count": 5},
                "labels": [{"code": k, "text": v} for k, v in CASH_LABELS.items()],
                "types": [{"code": k, "text": CASH_TYPE_TEXT[k], "directions": list(v)} for k, v in CASH_TYPES.items()],
                "bank_accounts": [{"key": "BANK:1", "name": "بنك مصر", "account_type": "BANK"}],
                "can_operate": True, "can_correct": True}

    def cash_movement(self, cash_key, direction, kind, amount, note, request_key, bank_key="", *, _expected_actor=None):
        self.calls.append(("cash_movement", cash_key, direction, kind, amount, note, request_key, bank_key))
        return {"operation_id": "10", "code": "FO000010", "operation_type": "X", "amount": amount, "duplicate": False}

    def operation(self, operation_id, *, _expected_actor=None):
        self.calls.append(("operation", operation_id))
        return {"operation_id": operation_id, "code": "FO000006", "operation_type": "MANUAL_WITHDRAWAL",
                "type_text": "مصروفات", "amount": "1500.00", "from_name": "الخزنة الرئيسية", "to_name": None,
                "reason": "", "actor_name": "المدير", "occurred_at": datetime(2026, 9, 30, 12, 5), "legs": [],
                "reversed_by": None, "reversal_reason": None}


class CashScreenTests(harness.InvoicePageTests):
    def open_page(self):
        self.fin = self.controller.operational_viewmodels["financialAccountsVM"]
        self.service = Service()
        self.fin._service = self.service
        self.controller.operational_viewmodels["customerVM"]._service = Mock(
            get_customer_types=Mock(return_value=[]), get_customer_transaction_types=Mock(return_value=[]),
            search_customers=Mock(return_value={"items": [], "page": 1, "has_more": False}))
        self.root.setProperty("currentPage", 4)
        self.wait_until(lambda: len(self.fin.inRows) == 4 and not self.fin.busy, "cash view")

    def count(self, name):
        return sum(1 for c in self.service.calls if c[0] == name)

    def last(self, name):
        return [c for c in self.service.calls if c[0] == name][-1]

    def texts(self, prefix, n):
        return [(self.visible_item(f"{prefix}Label_{i}").property("text"),
                 self.visible_item(f"{prefix}Amount_{i}").property("text")) for i in range(n)]

    def test_two_tables_name_and_amount_only_no_bank_cards_at_all_sizes(self):
        self.open_page()
        self.fin.changed.emit()
        self.wait_until(lambda: self.item("cashNavButton").property("visible"), "nav shown to cash viewers")
        self.assertEqual(self.item("cashNavButton").property("text"), "💰  الخزنة")
        target = os.getenv("MASGOLD_UI_REVIEW_DIR")
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            inside, outside = self.visible_item("cashInTable"), self.visible_item("cashOutTable")
            for item in (inside, outside):
                self.assert_inside_window(item)
            # داخل on the physical right, خارج on the left.
            self.assertGreater(inside.mapToScene(QPointF(0, 0)).x(), outside.mapToScene(QPointF(0, 0)).x())
            self.assertEqual(self.texts("cashIn", 4), [("بيع", "25,000.00 ج"), ("سداد عميل", "5,000.00 ج"),
                                                      ("إيراد", "2,000.00 ج"), ("سحب بنك", "10,000.00 ج")])
            self.assertEqual([t[0] for t in self.texts("cashOut", 5)],
                             ["شراء كسر", "مصروفات", "مرتبات", "مرمة", "إيداع بنك"])
            self.assertEqual(self.visible_item("cashInTotal").property("text"), "42,000.00 ج")
            self.assertEqual(self.visible_item("cashOutTotal").property("text"), "49,100.00 ج")
            self.assertEqual(self.item("cashBalance").property("text"), "82,540.00 ج")
            self.assertEqual(self.item("cashAccountName").property("text"), "الخزنة الرئيسية")
            for name in ("cashBalance", "cashNewMovement", "cashRefresh", "cashStart", "cashEnd", "cashLabelFilter",
                         "cashApplyFilter", "cashInRow_0", "cashOutRow_0", "cashInTotal", "cashOutTotal"):
                item = self.visible_item(name)
                self.assertIsNotNone(item, name)
                self.assert_inside_window(item)
            # Only two cells per row: no reference / actor / time / balance / code text anywhere in a row.
            for prefix, n in (("cashIn", 4), ("cashOut", 5)):
                for i in range(n):
                    labels = [c for c in self._labels(self.visible_item(f"{prefix}Row_{i}"))]
                    self.assertEqual(len(labels), 2, labels)
                    for text in labels:
                        self.assertNotRegex(text, r"[A-Z]{2}\d{6}|_[A-Z]|\d{2}:\d{2}|^CS|^RV|^FC")
            # No bank / wallet cards or balances on this page.
            for gone in ("financialAccountList", "financialAccount_0", "financialStatement"):
                self.assertIsNone(self.visible_item(gone), gone)
            if target:
                Path(target).mkdir(parents=True, exist_ok=True)
                self.root.grabWindow().save(str(Path(target) / f"cash-screen-{width}x{height}.png"))
        self.assertEqual(self.warnings, [])

    def _labels(self, item):
        out = []
        for child in item.childItems():
            text = child.property("text")
            if isinstance(text, str) and child.property("visible") and child.inherits("QQuickLabel"):
                out.append(text)
            out += self._labels(child)
        return out

    def test_visual_polish_header_proportions_totals_and_temporary_message(self):
        self.open_page()
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            # Safe name, «الرصيد الحالي» and the balance form one compact block.
            name, balance = self.item("cashAccountName"), self.item("cashBalance")
            block = self.visible_item("cashSafeBlock")
            self.assert_inside_window(block)
            self.assertLess(block.width(), width * 0.45)
            gap = abs(name.mapToScene(QPointF(name.width(), 0)).x() - balance.mapToScene(QPointF(balance.width(), 0)).x())
            self.assertLess(gap, 260, f"name/balance too far apart at {width}")
            for prefix, rows in (("cashIn", 4), ("cashOut", 5)):
                label, amount = self.visible_item(f"{prefix}Label_0"), self.visible_item(f"{prefix}Amount_0")
                share = label.width() / (label.width() + amount.width())
                self.assertTrue(0.62 <= share <= 0.72, f"{prefix} label share {share:.2f} at {width}")
                total, bar = self.visible_item(f"{prefix}Total"), self.visible_item(f"{prefix}TotalBar")
                self.assert_inside_window(bar)
                self.assertGreaterEqual(total.property("font").pixelSize(),
                                        amount.property("font").pixelSize() * 1.3)      # the strongest element
                self.assertTrue(total.property("font").bold())
                self.assertEqual(total.property("color"), amount.property("color"))     # same direction color
                self.assertLessEqual(total.property("contentWidth"), total.property("width") + 1)
            self.assertEqual(self.visible_item("cashInTotalLabel").property("text"), "إجمالي النقدية الداخلة")
            self.assertEqual(self.visible_item("cashOutTotalLabel").property("text"), "إجمالي النقدية الخارجة")
        # The success message is temporary (one single-shot timer; shortened here).
        self.fin._message_timer.setInterval(300)
        self.fin._form = True
        self.fin._on_operate({"code": "FO000010"}, "")
        self.wait_until(lambda: not self.fin.busy)
        self.assertEqual(self.item("cashMessage").property("text"), "تم تسجيل الحركة.")
        self.wait_until(lambda: not self.item("cashMessage").property("visible"), "message hides itself")
        self.fin._on_view(None, "تعذر تحميل الخزنة.")                          # errors are not auto-hidden
        self.fin.changed.emit()
        QTest.qWait(450)
        self.assertEqual(self.item("cashMessage").property("text"), "تعذر تحميل الخزنة.")
        self.assertEqual(self.warnings, [])

    def test_new_expense_movement_real_clicks_confirmation_and_double_click(self):
        self.open_page()
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(120)
            before = self.count("cash_movement")
            self.click("cashNewMovement")
            self.wait_until(lambda: self.item("cashMovementDialog").property("opened"))
            self.click("cashDirectionOut")
            self.choose("cashMovementType", 0)                                  # مصروفات
            self.type_into("cashAmount", "1500")
            self.click("cashMovementNext")
            self.assertEqual(self.item("cashConfirmText").property("text"),
                             "سيتم تسجيل مصروفات 1,500.00 ج (خارج الخزنة الرئيسية).")
            button = self.item("cashMovementConfirm")
            self.assert_inside_window(button)
            point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
            QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
            QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)    # an impatient double click
            self.wait_until(lambda: self.count("cash_movement") == before + 1 and not self.fin.busy)
            QTest.qWait(80)
            self.assertEqual(self.count("cash_movement"), before + 1)
            self.wait_until(lambda: not self.item("cashMovementDialog").property("visible"), "dialog closes")
        self.assertEqual(self.last("cash_movement")[1:6], ("CASH:1", "OUT", "EXPENSE", "1500", ""))
        self.assertEqual(self.warnings, [])

    def test_revenue_in_and_bank_deposit_forms(self):
        self.open_page()
        self.click("cashNewMovement")
        self.wait_until(lambda: self.item("cashMovementDialog").property("opened"))
        self.click("cashDirectionIn")
        self.assertEqual([t["text"] for t in self.fin.typesFor("IN")], ["إيراد", "مرمة", "سحب بنك"])
        self.assertEqual([t["text"] for t in self.fin.typesFor("OUT")],
                         ["مصروفات", "مرتبات", "إيجار", "مرمة", "إيداع بنك"])
        self.choose("cashMovementType", 0)
        self.type_into("cashAmount", "2000")
        self.click("cashMovementNext")
        self.assertEqual(self.item("cashConfirmText").property("text"), "سيتم تسجيل إيراد 2,000.00 ج (داخل الخزنة الرئيسية).")
        self.click("cashMovementConfirm")
        self.wait_until(lambda: self.count("cash_movement") == 1 and not self.fin.busy)
        self.assertEqual(self.last("cash_movement")[2:4], ("IN", "REVENUE"))
        self.wait_until(lambda: not self.item("cashMovementDialog").property("visible"))
        # إيداع بنك needs its bank account (names only, never balances).
        self.click("cashNewMovement")
        self.wait_until(lambda: self.item("cashMovementDialog").property("opened"))
        self.click("cashDirectionOut")
        self.choose("cashMovementType", 4)                                      # إيداع بنك
        self.type_into("cashAmount", "20000")
        self.assertFalse(self.item("cashMovementNext").property("enabled"))    # bank not chosen yet
        self.choose("cashBankAccount", 0)
        self.click("cashMovementNext")
        self.assertEqual(self.item("cashConfirmText").property("text"),
                         "سيتم إيداع 20,000.00 ج من الخزنة الرئيسية في بنك مصر.")
        self.click("cashMovementConfirm")
        self.wait_until(lambda: self.count("cash_movement") == 2 and not self.fin.busy)
        self.assertEqual(self.last("cash_movement")[2:4] + self.last("cash_movement")[-1:],
                         ("OUT", "BANK_DEPOSIT", "BANK:1"))
        self.assertEqual(self.warnings, [])

    def test_event_refresh_filters_and_period_preserved(self):
        self.open_page()
        self.type_into("cashStart", "2026-09-01")
        self.choose("cashLabelFilter", 6)                                       # مصروفات? any code
        self.click("cashApplyFilter")
        self.wait_until(lambda: not self.fin.busy)
        applied = self.last("cash_view")
        self.assertEqual(applied[1], "2026-09-01")
        for key in ("invoiceVM", "exchangeVM", "returnVM", "customerAccountVM", "correctionVM"):
            before = self.count("cash_view")
            self.service.balance = str(80000 + before)
            self.controller.operational_viewmodels[key].finalized.emit("x")
            self.wait_until(lambda: self.count("cash_view") > before and not self.fin.busy, key)
            self.assertEqual(self.last("cash_view"), applied)                   # period / type kept
            self.assertIn(f"{80000 + before:,}", self.item("cashBalance").property("text"))
        self.root.setProperty("currentPage", 1)
        QTest.qWait(60)
        before = self.count("cash_view")
        self.controller.operational_viewmodels["invoiceVM"].finalized.emit("x")
        QTest.qWait(120)
        self.assertEqual(self.count("cash_view"), before)                      # no polling, no off-page read
        self.root.setProperty("currentPage", 4)
        self.wait_until(lambda: self.count("cash_view") > before and not self.fin.busy)
        self.assertEqual(self.last("cash_view"), applied)

    def test_invoice_row_opens_existing_history_and_manual_row_routes_reversal(self):
        self.open_page()
        history = self.controller.operational_viewmodels["invoiceHistoryVM"]
        self.root.setWidth(1080)
        self.root.setHeight(680)
        QTest.qWait(120)
        self.click(self.visible_item("cashInRow_0"))
        self.wait_until(lambda: ("detail", HUGE) in self.history.calls, "history detail")
        self.assertEqual(self.root.property("currentPage"), 1)
        self.wait_until(lambda: history.detailOpen and not history.detailBusy)
        history.closeDetail()
        self.assertEqual(len(self.root.findChildren(QObject, "invoiceHistoryPage")), 1)
        correction = self.controller.operational_viewmodels["correctionVM"]
        correction._service = Mock(financial_operation_target=Mock(return_value={
            "invoice_id": "", "invoice_number": "FO000006", "customer_name": None, "bank_accounts": [],
            "targets": [{"source_type": "FINANCIAL_OPERATION", "source_id": "6", "reference": "FO000006",
                         "source_text": "العملية المالية", "settlement_allowed": False, "reversed_by": None,
                         "settlement_corrections": 0}]}))
        self.root.setProperty("currentPage", 4)
        self.wait_until(lambda: not self.fin.busy and len(self.fin.outRows) == 5)
        QTest.qWait(80)
        self.click(self.visible_item("cashOutRow_1"))
        self.wait_until(lambda: self.item("cashDetailDialog").property("visible"))
        reverse = self.item("cashDetailReverse")
        self.assert_inside_window(reverse)
        self.click(reverse)
        self.wait_until(lambda: correction.isOpen and correction.targets)
        self.assertEqual(correction._service.financial_operation_target.call_args[0][0], "6")
        self.assertFalse(self.visible_item("correctionModeSettlement"))       # full reversal only
        correction.close()
        self.assertEqual(self.warnings, [])


# unittest otherwise inherits every Billing harness test, inflating the suite count.
for _name in dir(harness.InvoicePageTests):
    if _name.startswith("test_"):
        setattr(CashScreenTests, _name, None)
