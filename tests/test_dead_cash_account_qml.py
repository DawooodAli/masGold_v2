"""Dead Cash write-off reached from Customer Account (كشف الحساب) on the real Customers page (Main.qml).

One canonical write-off flow is reused (customerVM.openWriteOff / writeOffDialog / DeadCashService). These
tests pin that the account entry point targets the account's own customer, never a stale customerVM selection,
follows the existing eligibility (permission + receivable) and refreshes the open account after posting.
Real clicks at 1440×900 / 1280×800 / 1080×680; the database is blocked and services are in-memory doubles."""
from datetime import datetime
from decimal import Decimal as D
from unittest.mock import Mock

from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt
from PySide6.QtTest import QTest

from tests import test_invoice_page_qml as harness

A, B = "12", "13"


class Ledger:
    """Per-customer state shared by the customer profile, the account statement and the Dead Cash double."""

    def __init__(self, a_balance="23000", b_balance="5000"):
        self.customers = {A: {"name": "أحمد", "balance": D(a_balance)}, B: {"name": "بكر", "balance": D(b_balance)}}
        self.rows = {A: [], B: []}
        self.statement_calls = []

    def row(self, cid):
        c = self.customers[cid]
        balance = c["balance"]
        return {"customer_id": cid, "customer_code": "C0000" + cid, "name": c["name"], "mobile": "0100000000" + cid[-1],
                "place": "", "notes": None, "customer_type_id": None, "customer_type_code": "REGULAR", "is_active": 1,
                "balance": balance,
                "balance_status": "CUSTOMER_OWES_SHOP" if balance > 0 else "SHOP_OWES_CUSTOMER" if balance < 0 else "SETTLED",
                "last_transaction_at": datetime(2026, 9, 30, 12), "active_warning_count": 0,
                "has_active_dead_cash_warning": 0, "has_dead_cash_history": 0, "unreversed_dead_cash_count": 0}

    def profile(self, cid):
        cid = str(cid)
        return {"customer": self.row(cid), "active_flags": [],
                "transactions_page": {"items": [], "page": 1, "page_size": 100, "has_more": False},
                "statement_summary": {}, "ledger_token": None, "statement_loaded_at": datetime(2026, 9, 30),
                "dead_cash_page": {"items": [], "page": 1, "page_size": 100, "has_more": False},
                "dead_cash_summary": None}


class DeadCash:
    def __init__(self, ledger, allowed=True):
        self.ledger, self.allowed = ledger, allowed
        self.contexts, self.writes = [], []

    def can_write_off(self):
        return self.allowed

    def can_reverse(self):
        return False

    def context(self, customer_id):
        self.contexts.append(str(customer_id))
        c = self.ledger.customers[str(customer_id)]
        return {"customer_id": str(customer_id), "name": c["name"], "balance": str(c["balance"]),
                "eligible": c["balance"] > 0}

    def write_off(self, customer_id, amount, reason, password, expected_balance, request_key):
        cid = str(customer_id)
        self.writes.append((cid, amount, reason, password, expected_balance))
        self.ledger.customers[cid]["balance"] -= D(amount)
        self.ledger.rows[cid].append({"id": len(self.ledger.rows[cid]) + 1, "row_kind": "LEDGER",
                                      "occurred_at": datetime(2026, 10, 6, 10), "source_type": "DEAD_CASH_WRITE_OFF",
                                      "source_reference": "DC000001", "description": "شطب رصيد", "actor_name": "مالك",
                                      "amount_delta": -D(amount), "dead_cash_reason": reason,
                                      "running_balance": self.ledger.customers[cid]["balance"]})
        return {"id": "1", "code": "DC000001", "amount": str(D(amount)), "duplicate": False}


class AccountService:
    def __init__(self, ledger):
        self.ledger = ledger

    def statement(self, customer_id, *args, **kwargs):
        cid = str(customer_id)
        self.ledger.statement_calls.append(cid)
        c = self.ledger.customers[cid]
        return {"customer": {"name": c["name"], "mobile": "01000000000", "balance": c["balance"]},
                "rows": list(reversed(self.ledger.rows[cid])), "has_more": False, "page": 1, "accounts": [],
                "can_settle": True}


class DeadCashAccountTests(harness.InvoicePageTests):
    def prepare(self, a_balance="23000", b_balance="5000", allowed=True):
        self.ledger = Ledger(a_balance, b_balance)
        self.cvm = self.controller.operational_viewmodels["customerVM"]
        self.avm = self.controller.operational_viewmodels["customerAccountVM"]
        self.cvm._service = Mock(
            get_customer_types=Mock(return_value=[]), get_customer_transaction_types=Mock(return_value=[]),
            search_customers=Mock(side_effect=lambda **kw: {
                "items": [self.ledger.row(A), self.ledger.row(B)], "page": 1, "has_more": False}),
            get_customer_profile=Mock(side_effect=self.ledger.profile))
        self.dead = DeadCash(self.ledger, allowed)
        self.cvm._dead_cash_service = self.dead
        self.avm.service = AccountService(self.ledger)
        self.root.setProperty("currentPage", 3)
        QTest.qWait(150)

    def open_account(self, cid, select=True):
        if select:
            self.assertTrue(self.cvm.selectCustomer(cid))
        self.avm.open(cid)
        self.wait_until(lambda: not self.avm.busy and self.avm.customerId == cid and self.avm.isOpen, "account")
        QTest.qWait(80)

    def button(self):
        return self.visible_item("customerAccountDeadCash")

    def emit_request(self, customer_id):
        """The page's own signal (a stale caller cannot be produced with the button, which is hidden then)."""
        page = self.item("customerAccountPage")
        self.assertTrue(QMetaObject.invokeMethod(page, "deadCashRequested", Qt.DirectConnection,
                                                 Q_ARG("QString", customer_id)))
        QTest.qWait(120)

    def type_field(self, form_name, text):
        form = self.item(form_name)
        field = next(child for child in form.childItems() if child.inherits("QQuickTextField"))
        self.click(field)
        QTest.keyClick(self.root, harness.Qt.Key_A, harness.Qt.ControlModifier)
        self.type_text(text)

    def dialog_is_open(self):
        return bool(self.item("writeOffDialog").property("opened"))

    # ------------------------------------------------------------------ correct customer, success, refresh
    def test_account_entry_targets_its_customer_posts_and_refreshes_the_open_account(self):
        self.prepare()
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                self.root.setProperty("currentPage", 3)
                QTest.qWait(150)
                self.open_account(A)
                button = self.button()
                self.assertIsNotNone(button, "eligible debt + permission → action available")
                self.assertIn("⛔ تحويل إلى رصيد مشطوب", button.property("text"))
                self.assert_inside_window(button)                       # no scrolling through the history
                before = len(self.dead.writes)
                self.click(button)
                dialog = self.item("writeOffDialog")
                self.wait_until(lambda: dialog.property("opened"), "write-off dialog")
                self.assertEqual(self.cvm.selectedCustomerId, A)
                self.assertEqual(self.dead.contexts[-1], A)
                self.assertEqual(self.cvm.writeOff["name"], "أحمد")
                balance = self.ledger.customers[A]["balance"]
                self.assertIn(f"{balance:,.2f} ج", self.item("writeOffBalance").property("value"))
                self.type_field("writeOffAmount", "8000")
                self.type_field("writeOffReason", "عميل مسافر")
                self.click("writeOffNext")
                self.click("writeOffPassword")
                self.type_text("owner-password")
                self.click("writeOffConfirm")                           # the existing dialog + password re-entry
                self.wait_until(lambda: len(self.dead.writes) == before + 1, "write-off call")
                write = self.dead.writes[-1]
                self.assertEqual((write[0], write[1], write[2], write[3]), (A, "8000", "عميل مسافر", "owner-password"))
                self.assertEqual(write[4], str(balance))                # expected balance = the dialog's balance
                self.wait_until(lambda: not dialog.property("visible"), "dialog closes")
                # The open account refreshes by itself: new balance and the new ledger entry, no reopen.
                expected = self.ledger.customers[A]["balance"]
                self.wait_until(lambda: f"{expected:,.2f}".split(".")[0] in self.avm.balance and not self.avm.busy,
                                "account balance refresh")
                self.assertEqual(self.avm.rows[0]["source"], "شطب رصيد")
                self.assertEqual(self.avm.rows[0]["reference"], "DC000001")
                self.assertEqual(self.avm.rows[0]["notes"], "عميل مسافر")
                self.assertTrue(self.avm.isOpen)
                self.assertEqual(self.cvm.selectedCustomerId, A)
                self.assertEqual(D(self.cvm.selectedCustomer["balance"]), expected)
                self.ledger.customers[A]["balance"] = D("23000")          # next size starts from the same debt
                self.ledger.rows[A].clear()
                self.avm.close()
        self.assertEqual(self.warnings, [])

    # ------------------------------------------------------------------ عكس الشطب next to the write-off movement
    def reversal_fixture(self, reversed_already=False, can_reverse=True):
        self.prepare()
        self.dead.can_reverse = lambda: can_reverse
        self.dead.reversals = []

        def reverse(dead_cash_id, reason, password):
            self.dead.reversals.append((dead_cash_id, reason, password))
            return {"code": "DCR000001"}
        self.dead.reverse = reverse
        row = {"id": 1, "row_kind": "LEDGER", "occurred_at": datetime(2026, 10, 6, 10), "amount_delta": -D("8000"),
               "source_type": "DEAD_CASH_WRITE_OFF", "source_reference": "DC000007", "description": "شطب رصيد",
               "actor_name": "مالك", "dead_cash_reason": "عميل مسافر", "running_balance": D("15000"),
               "dead_cash_id": 7, "dead_cash_reversed": reversed_already}
        self.ledger.rows[A].append(row)
        original = self.ledger.profile

        def profile(cid):
            data = original(cid)
            if str(cid) == A:
                data["dead_cash_page"] = {"items": [{
                    "dead_cash_id": 7, "amount": D("8000"), "balance_before": D("23000"), "balance_after": D("15000"),
                    "is_reversed": reversed_already, "occurred_at": "2026-10-06 10:00", "reason": "عميل مسافر"}],
                    "page": 1, "page_size": 100, "has_more": False}
            return data
        self.cvm._service.get_customer_profile = Mock(side_effect=profile)
        return row

    def test_dead_cash_reversal_is_offered_on_its_own_movement_and_uses_the_existing_flow(self):
        self.reversal_fixture()
        self.open_account(A)
        button = self.visible_item("accountDeadCashReverse_LEDGER1")
        self.assertIsNotNone(button, "an unreversed write-off row carries its reversal")
        self.assertEqual(button.property("text"), "عكس الشطب")
        self.assert_inside_window(button)
        self.click(button)
        dialog = self.item("reverseDeadCashDialog")
        self.wait_until(lambda: dialog.property("opened"), "the existing reversal dialog")
        self.assertEqual(dialog.property("deadCashId"), "7")
        self.assertEqual(dialog.property("reference"), "DC000007")
        self.assertEqual(dialog.property("amountText"), "8,000.00")
        self.type_field("reverseDeadCashReason", "شطب بالخطأ")
        self.click("reverseDeadCashPassword")
        self.type_text("owner-password")
        self.click("reverseDeadCashConfirm")
        self.wait_until(lambda: self.dead.reversals, "reversal call")
        self.assertEqual(self.dead.reversals, [("7", "شطب بالخطأ", "owner-password")])
        self.assertEqual(self.warnings, [])

    def test_no_reversal_button_when_already_reversed_or_without_the_right(self):
        self.reversal_fixture(reversed_already=True)
        self.open_account(A)
        self.assertIsNone(self.visible_item("accountDeadCashReverse_LEDGER1"))
        self.avm.close()
        self.reversal_fixture(can_reverse=False)
        self.open_account(A)
        self.assertIsNone(self.visible_item("accountDeadCashReverse_LEDGER1"))
        self.assertEqual(self.warnings, [])

    # ------------------------------------------------------------------ stale selection
    def test_a_stale_customer_selection_never_becomes_the_write_off_target(self):
        self.prepare()
        self.assertTrue(self.cvm.selectCustomer(B))                     # customerVM still holds an older customer
        self.open_account(A, select=False)                              # …while Customer Account shows A
        self.assertEqual((self.cvm.selectedCustomerId, self.avm.customerId), (B, A))
        self.assertIsNone(self.button(), "no action is offered on state that belongs to another customer")
        # A request carrying A re-synchronises customerVM to A BEFORE the dialog; B is never contacted.
        self.emit_request(A)
        self.wait_until(self.dialog_is_open, "dialog")
        self.assertEqual(self.cvm.selectedCustomerId, A)
        self.assertEqual(self.dead.contexts, [A])
        self.assertEqual(self.cvm.writeOff["name"], "أحمد")
        self.assertIn("23,000.00 ج", self.item("writeOffBalance").property("value"))
        self.assertEqual(self.dead.writes, [])
        self.item("writeOffDialog").close()
        self.wait_until(lambda: not self.item("writeOffDialog").property("visible"))
        # A request for a customer that is not the open account is refused outright.
        self.emit_request(B)
        self.assertFalse(self.dialog_is_open())
        self.assertEqual(self.dead.contexts, [A])
        self.assertEqual(self.avm.customerId, A)
        self.assertEqual(self.dead.writes, [])
        self.assertEqual(self.warnings, [])

    def test_a_selection_that_cannot_be_loaded_for_the_account_customer_stops_the_flow(self):
        self.prepare()
        self.assertTrue(self.cvm.selectCustomer(B))
        self.open_account(A, select=False)
        self.cvm._service.get_customer_profile.side_effect = (
            lambda cid: (_ for _ in ()).throw(RuntimeError("boom")) if str(cid) == A else self.ledger.profile(cid))
        self.emit_request(A)
        self.assertFalse(self.dialog_is_open())
        self.assertEqual((self.dead.contexts, self.dead.writes), ([], []))     # never falls back to B
        self.assertEqual(self.warnings, [])

    # ------------------------------------------------------------------ no receivable / permission
    def test_no_receivable_offers_no_action(self):
        for balance in ("0", "-1500"):                                  # settled, and credit (shop owes customer)
            with self.subTest(balance=balance):
                self.prepare(a_balance=balance)
                self.open_account(A)
                self.assertIsNone(self.button())
                self.assertFalse(self.cvm.canWriteOff)
                self.emit_request(A)                                    # no path opens the dialog either
                self.assertFalse(self.dialog_is_open())
                self.assertEqual(self.dead.contexts, [])
                self.avm.close()
        self.assertEqual(self.warnings, [])

    def test_without_the_existing_dead_cash_permission_there_is_no_write_off_path(self):
        self.prepare(allowed=False)
        self.open_account(A)
        self.assertGreater(self.ledger.customers[A]["balance"], 0)
        self.assertIsNone(self.button(), "seeing Customer Account grants nothing")
        self.emit_request(A)
        self.assertFalse(self.dialog_is_open())
        self.assertEqual((self.dead.contexts, self.dead.writes), ([], []))
        self.assertEqual(self.warnings, [])

    # ------------------------------------------------------------------ one canonical flow
    def test_the_existing_dialog_and_view_model_are_reused_not_duplicated(self):
        self.prepare()
        self.open_account(A)
        self.assertEqual(len(self.root.findChildren(QObject, "writeOffDialog")), 1)
        self.assertEqual(len(self.root.findChildren(QObject, "writeOffPassword")), 1)
        self.assertEqual(len(self.root.findChildren(QObject, "customerAccountDeadCash")), 1)
        self.click(self.button())
        self.wait_until(self.dialog_is_open, "dialog")
        self.assertEqual(self.item("writeOffDialog").property("objectName"), "writeOffDialog")
        self.assertEqual(self.cvm.writeOff["name"], "أحمد")
        self.assertEqual(self.dead.contexts, [A])                       # customerVM.openWriteOff → DeadCashService
        self.assertEqual(self.warnings, [])


# unittest otherwise inherits every Billing harness test, inflating the suite count.
for _name in dir(harness.InvoicePageTests):
    if _name.startswith("test_"):
        setattr(DeadCashAccountTests, _name, None)
