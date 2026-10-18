"""Manager Review in the real Main.qml (post manual-review fix pack): category cards that only filter,
card backgrounds that open the SOURCE by immutable id while action buttons never navigate, and the
bank / wallet account movement history. Real clicks; the database is blocked."""
from datetime import datetime
from decimal import Decimal as D

from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from tests import test_invoice_page_qml as harness

HUGE = "9007199254740993"


class Fin:
    """FinancialAccountService.statement stand-in: a wallet with a sale and a settlement correction."""

    def __init__(self):
        self.calls = []

    def statement(self, key, start, end, direction, source, search, page, *, _expected_actor=None):
        self.calls.append((key, direction, page))
        rows = [
            {"id": "12", "amount_delta": D("1000.00"), "balance_after": D("19000.00"), "label": "تصحيح تسوية",
             "reference": "FC000001", "invoice_number": "26100601", "corrected_reference": "26100601",
             "customer": "عميل الاختبار", "customer_id": "77", "kind": "CORRECTION", "invoice_id": HUGE,
             "actor_name": "المدير", "occurred_at": datetime(2026, 10, 5, 12, 0), "operation_reason": None,
             "description": None},
            {"id": "11", "amount_delta": D("-500.00"), "balance_after": D("18000.00"), "label": "سحب من الحساب",
             "reference": "TR000002", "invoice_number": "", "corrected_reference": "", "customer": "",
             "customer_id": "", "kind": "TRANSFER_OUT", "invoice_id": None, "actor_name": "المدير",
             "occurred_at": datetime(2026, 10, 5, 11, 0), "operation_reason": None, "description": None}]
        if direction == "IN":
            rows = [r for r in rows if r["amount_delta"] > 0]
        elif direction == "OUT":
            rows = [r for r in rows if r["amount_delta"] < 0]
        return {"account": {"key": key, "name": "محفظة المحل", "balance": "19000.00"}, "rows": rows,
                "has_more": False, "page": page}


class ManagerReviewPageTests(harness.InvoicePageTests):
    def open_reviews(self):
        # The page needs the manager's review right (the harness user is a salesperson-like actor).
        actor = self.controller.session.current_actor()
        object.__setattr__(actor, "permissions", frozenset(actor.permissions | {"MANAGEMENT_REVIEW"}))
        vm = self.controller.operational_viewmodels["reviewsVM"]
        self.reviews_vm = vm
        self.requested = []
        vm.invoiceRequested.connect(self.requested.append)
        vm._counts = {"INVENTORY_MATCH": 0, "MAKING_PRICE": 1, "CASH_SETTLEMENT": 0, "OLD_GOLD_DISCOUNT": 0,
                      "RETURNED_ITEM_INSPECTION": 1, "RETURN_APPROVAL": 0}
        vm._on_reviews({
            "INVENTORY_MATCH": [], "CASH_SETTLEMENT": [], "OLD_GOLD_DISCOUNT": [], "RETURN_APPROVAL": [],
            "MAKING_PRICE": [{"review_id": "5", "invoice_id": HUGE, "invoice_number": "26100601",
                              "summary": "مصنعية خارج النطاق", "details_json": {}, "created_at": datetime(2026, 10, 5),
                              "created_by_name": "علي"}],
            "RETURNED_ITEM_INSPECTION": [{
                "review_id": "9", "invoice_id": "21", "invoice_number": "26100602", "exchange_code": "RT000001",
                "source_label": "مرتجع RT000001", "customer_name_snapshot": "عميل", "created_at": datetime(2026, 10, 5),
                "item_type_name": "خاتم", "karat_value": 21, "item_weight_grams": D("3.500"),
                "stored_barcode": "01260925", "inventory_status": "RETURNED_PENDING", "inventory_item_id": "300",
                "source": "RETURN", "timeline": []}]}, "")
        vm._on_accounts([{"ledger": "BANK", "account_id": "3", "name": "محفظة المحل", "account_type": "MOBILE_WALLET",
                          "is_active": 1, "balance": D("19000.00"), "money_in": D("20000"), "money_out": D("1000"),
                          "movement_count": 2, "recent": []}], "")
        vm.history._service = Fin()
        vm.openReviews = lambda: None          # keep the prepared rows (the page entry would reload the stand-in)
        vm.changed.emit()
        self.root.setProperty("currentPage", 10)
        self.wait_until(lambda: self.visible_item("reviewCard_MAKING_PRICE") is not None, "cards")
        QTest.qWait(300)                       # let the page lay out before real clicks

    def back_to_reviews(self):
        self.requested.clear()
        self.root.setProperty("currentPage", 10)
        QTest.qWait(300)

    def test_cards_filter_and_never_approve_anything(self):
        self.open_reviews()
        counts = {c["code"]: (c["count"], c["pending"]) for c in self.reviews_vm.dashboardCards}
        self.assertEqual(counts["MAKING_PRICE"], (1, True))
        self.assertEqual(counts["RETURN_APPROVAL"], (0, False))
        self.assertEqual(counts["ACCOUNTS"], (1, False))
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(100)
            for code in ("MAKING_PRICE", "RETURNED_ITEM_INSPECTION", "ACCOUNTS"):
                self.assert_inside_window(self.visible_item(f"reviewCard_{code}"))
        self.assertIsNotNone(self.visible_item("reviewMakingSection"))
        self.assertIsNotNone(self.visible_item("reviewInspectionSection"))
        self.click("reviewCard_RETURNED_ITEM_INSPECTION")
        self.assertEqual(self.reviews_vm.selectedCategory, "RETURNED_ITEM_INSPECTION")
        self.assertIsNone(self.visible_item("reviewMakingSection"))
        self.assertIsNone(self.visible_item("financialAccountsSection"))
        self.assertIsNotNone(self.visible_item("reviewInspectionSection"))
        self.click("reviewCard_RETURNED_ITEM_INSPECTION")              # again: everything
        self.assertEqual(self.reviews_vm.selectedCategory, "")
        self.assertIsNotNone(self.visible_item("reviewMakingSection"))
        # A pricing / making review is informational: no approve / reject control exists on its card.
        self.assertIsNone(self.visible_item("reviewApprove"))
        self.assertEqual(self.requested, [])                            # a card is never a navigation

    def test_card_background_opens_the_source_but_a_button_never_navigates(self):
        self.open_reviews()
        self.click(f"reviewSource_{HUGE}")
        self.assertEqual(self.requested, [HUGE])                        # the exact id, no JavaScript rounding
        self.assertEqual(self.root.property("currentPage"), 1)          # the existing Invoice History page opened
        self.back_to_reviews()
        # The inspection card: clicking its background opens the original invoice …
        self.click("inspectionSource_0")
        self.assertEqual(self.requested, ["21"])
        self.back_to_reviews()
        # … but its action button runs ITS action (the confirmation) and does not navigate.
        self.click("inspectionRestock_0")
        self.assertEqual(self.requested, [])
        self.wait_until(lambda: self.item("inspectionConfirm").property("opened"), "confirmation dialog")
        QTest.keyClick(self.root, Qt.Key_Escape)
        self.assertEqual(self.requested, [])

    def test_a_review_without_a_source_is_not_navigable(self):
        self.open_reviews()
        self.reviews_vm.openSource("")
        self.reviews_vm.openSource("abc")
        self.reviews_vm.openSource("12; DROP")
        self.assertEqual(self.requested, [])

    def test_account_card_opens_its_movement_history_with_sources(self):
        self.open_reviews()
        self.click("accountOpenHistory_0")
        self.wait_until(lambda: self.visible_item("accountHistory") is not None
                        and self.visible_item("accountMovement_1") is not None, "history")
        self.assertIsNone(self.visible_item("reviewScroll"))
        self.assertEqual(self.item("accountHistoryBalance").property("text"), "19,000.00 ج")
        self.assertEqual(self.reviews_vm.history.rows[0]["incoming"], True)
        self.assertEqual(self.item("accountMovementAmount_0").property("text"), "1,000.00 ج")
        self.assertEqual(self.item("accountMovementAmount_1").property("text"), "500.00 ج")
        self.assertIn("FC000001", self.item("accountMovementRef_0").property("text"))
        self.assertIn("26100601", self.item("accountMovementRef_0").property("text"))
        self.assertEqual(self.item("accountMovementCustomer_0").property("text"), "عميل الاختبار")
        # The invoice reference opens the existing Invoice History detail by its exact id.
        self.click("accountMovementRefArea_0")
        self.assertEqual(self.requested, [HUGE])
        self.back_to_reviews()
        # Direction filter and back.
        self.click("accountHistoryFilter_OUT")
        self.wait_until(lambda: len(self.reviews_vm.history.rows) == 1)
        self.assertFalse(self.reviews_vm.history.rows[0]["incoming"])
        self.click("accountHistoryBack")
        self.wait_until(lambda: self.visible_item("reviewScroll") is not None)
        self.assertEqual(self.warnings, [])


_OWN = [n for n in dir(ManagerReviewPageTests) if n.startswith('test_') and n in ManagerReviewPageTests.__dict__]
# unittest otherwise inherits every Billing harness test, inflating the suite count.
for _name in dir(harness.InvoicePageTests):
    if _name.startswith("test_") and _name not in _OWN:
        setattr(ManagerReviewPageTests, _name, None)


# ---------------------------------------------------------------------------------------------------------
# Settings → المستخدمون والصلاحيات in the real Main.qml
# ---------------------------------------------------------------------------------------------------------
class UserAdminFake:
    """UserAdminService stand-in: records every write; the screen never decides anything itself."""

    def __init__(self):
        self.calls = []
        self.active = {"11": True}

    def overview(self, *, _expected_actor=None):
        return {"current_user_id": "10",
                "users": [{"id": "10", "username": "admin", "display_name": "المالك", "is_active": True,
                           "last_login_at": None, "employee_id": None, "employee_name": None, "employee_active": None,
                           "roles": [{"code": "OWNER", "name": "Owner", "active": True}], "is_owner": True,
                           "is_self": True},
                          {"id": "11", "username": "sara", "display_name": "سارة", "is_active": self.active["11"],
                           "last_login_at": datetime(2026, 10, 5, 9, 0), "employee_id": "5", "employee_name": "سارة",
                           "employee_active": True, "roles": [{"code": "CASHIER", "name": "Cashier", "active": True}],
                           "is_owner": False, "is_self": False}],
                "roles": [{"id": "1", "code": "OWNER", "name": "Owner", "is_system": True, "is_active": True,
                           "user_count": 1, "locked": True, "permissions": ["APP_LOGIN", "USERS_MANAGE", "BILLING_USE"]},
                          {"id": "2", "code": "MANAGER", "name": "Manager", "is_system": True, "is_active": True,
                           "user_count": 0, "locked": False, "permissions": ["APP_LOGIN"]},
                          {"id": "3", "code": "CASHIER", "name": "Cashier", "is_system": True, "is_active": True,
                           "user_count": 1, "locked": False, "permissions": ["APP_LOGIN"]}],
                "permissions": [{"code": "APP_LOGIN", "name": "Application Login", "description": "", "owner_only": False},
                                {"code": "BILLING_USE", "name": "Use Billing", "description": "", "owner_only": False},
                                {"code": "USERS_MANAGE", "name": "Manage Users", "description": "", "owner_only": True}],
                "employees": [{"id": "6", "name": "علي", "code": "E6"}]}

    def set_user_active(self, user_id, active, password, *, _expected_actor=None):
        self.calls.append(("active", user_id, active, password))
        self.active[user_id] = active
        return {"changed": True}

    def set_user_roles(self, user_id, role_codes, password, *, _expected_actor=None):
        self.calls.append(("roles", user_id, sorted(role_codes), password))
        return {"changed": True}

    def set_role_permission(self, role_code, permission_code, granted, password, *, _expected_actor=None):
        self.calls.append(("grant", role_code, permission_code, granted, password))
        return {"changed": True}

    def reset_password(self, user_id, new_password, confirmation, reason, password, *, _expected_actor=None):
        self.calls.append(("reset", user_id, new_password, confirmation, reason, password))
        return {"changed": True}

    def create_user(self, *args, _expected_actor=None):
        self.calls.append(("create",) + args)
        return {"changed": True}


class UserAdminPageTests(harness.InvoicePageTests):
    def open_users(self, owner=True):
        actor = self.controller.session.current_actor()
        if owner:
            object.__setattr__(actor, "permissions", frozenset(actor.permissions | {"USERS_MANAGE"}))
        self.vm_users = self.controller.operational_viewmodels["userAdminVM"]
        self.fake = UserAdminFake()
        self.vm_users._service = self.fake
        self.vm_users.changed.emit()
        self.root.setProperty("currentPage", 6)
        self.assert_provider_settings_loaded()
        QTest.qWait(200)

    def go_to_page(self):
        self.vm_users.refresh()
        self.wait_until(lambda: len(self.vm_users.users) == 2, "users loaded")
        self.root.setProperty("currentPage", 13)
        QTest.qWait(250)

    def type_in(self, name, text):
        self.click(name)
        self.type_text(text)

    def test_settings_card_is_owner_only(self):
        self.open_users(owner=False)
        self.assertIsNone(self.visible_item("settingsUsers"))
        self.assertFalse(self.vm_users.canManage)
        self.vm_users.refresh()                                        # a non-owner never reaches the service
        QTest.qWait(100)
        self.assertEqual(self.vm_users.users, [])

    def test_owner_manages_users_with_real_clicks_at_all_sizes(self):
        self.open_users()
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            card = self.visible_item("settingsUsers")
            self.assertIsNotNone(card, "owner sees the Settings card")
            self.assert_inside_window(card)
        self.click("settingsUsers")
        self.wait_until(lambda: self.root.property("currentPage") == 13, "users page")
        self.wait_until(lambda: len(self.vm_users.users) == 2, "users loaded")
        QTest.qWait(250)
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            for name in ("userAdminUserRow_10", "userAdminUserRow_11", "userAdminCreate", "userAdminBack"):
                self.assert_inside_window(self.visible_item(name))
        self.root.setWidth(1440)                                  # the form below the fold is reached by scrolling
        self.root.setHeight(900)
        QTest.qWait(200)
        # No hash or password text anywhere in the view model's data.
        self.assertNotIn("argon2", repr(self.vm_users.users) + repr(self.vm_users.roles))
        # Select sara → deactivate asks for the administrator's password, then calls the service once.
        self.click("userAdminUserRow_11")
        self.wait_until(lambda: self.vm_users.selectedUserId == "11")
        QTest.qWait(150)
        self.click("userAdminToggleActive")
        prompt = self.item("userAdminPasswordDialog")
        self.wait_until(lambda: prompt.property("opened"), "password prompt")
        self.assertFalse(self.item("userAdminPasswordConfirm").property("enabled"))      # nothing typed yet
        self.type_in("userAdminPasswordField", "admin-pass")
        self.click("userAdminPasswordConfirm")
        self.wait_until(lambda: not prompt.property("visible"), "prompt closed")
        self.wait_until(lambda: self.fake.calls, "service call")
        self.assertEqual(self.fake.calls, [("active", "11", False, "admin-pass")])
        self.wait_until(lambda: self.vm_users.message == "تم تعطيل المستخدم.")
        self.assertEqual(self.item("userAdminPasswordField").property("text"), "")      # the password is not kept
        # Roles: tick MANAGER, save → asks the password.
        QTest.qWait(150)
        self.click("userAdminRoleCheck_MANAGER")
        self.wait_until(lambda: self.item("userAdminSaveRoles").property("enabled"), "roles changed")
        self.click("userAdminSaveRoles")
        self.wait_until(lambda: prompt.property("opened"), "password prompt")
        self.type_in("userAdminPasswordField", "admin-pass")
        self.click("userAdminPasswordConfirm")
        self.wait_until(lambda: not prompt.property("visible"), "prompt closed")
        self.wait_until(lambda: len(self.fake.calls) == 2)
        self.assertEqual(self.fake.calls[1], ("roles", "11", ["CASHIER", "MANAGER"], "admin-pass"))
        # The own account offers neither deactivation nor role changes.
        self.click("userAdminUserRow_10")
        self.wait_until(lambda: self.vm_users.selectedUserId == "10")
        QTest.qWait(150)
        self.assertFalse(self.item("userAdminToggleActive").property("enabled"))
        self.assertFalse(self.item("userAdminSaveRoles").property("enabled"))
        # Role grants: OWNER is fixed, USERS_MANAGE is owner-only, MANAGER may get BILLING_USE.
        self.click("userAdminRoleTab_OWNER")
        QTest.qWait(150)
        self.assertFalse(self.item("userAdminPerm_BILLING_USE").property("enabled"))
        self.click("userAdminRoleTab_MANAGER")
        QTest.qWait(150)
        self.assertFalse(self.item("userAdminPerm_USERS_MANAGE").property("enabled"))
        self.click("userAdminPerm_BILLING_USE")
        self.wait_until(lambda: prompt.property("opened"), "password prompt")
        self.type_in("userAdminPasswordField", "admin-pass")
        self.click("userAdminPasswordConfirm")
        self.wait_until(lambda: not prompt.property("visible"), "prompt closed")
        self.wait_until(lambda: len(self.fake.calls) == 3)
        self.assertEqual(self.fake.calls[2], ("grant", "MANAGER", "BILLING_USE", True, "admin-pass"))
        self.assertEqual(self.warnings, [])

    def test_reset_password_dialog_sends_everything_once_and_clears_the_fields(self):
        self.open_users()
        self.go_to_page()
        self.click("userAdminUserRow_11")
        self.wait_until(lambda: self.vm_users.selectedUserId == "11")
        QTest.qWait(150)
        self.click("userAdminResetPassword")
        dialog = self.item("userAdminResetDialog")
        self.wait_until(lambda: dialog.property("opened"), "reset dialog")
        self.assertFalse(self.item("userAdminResetConfirm").property("enabled"))
        self.type_in("userAdminNewPassword", "new-pass-1")
        self.type_in("userAdminConfirmPassword", "new-pass-1")
        self.type_in("userAdminResetReason", "نسيت")
        self.type_in("userAdminResetAdmin", "admin-pass")
        self.click("userAdminResetConfirm")
        self.wait_until(lambda: self.fake.calls)
        self.assertEqual(self.fake.calls, [("reset", "11", "new-pass-1", "new-pass-1", "نسيت", "admin-pass")])
        self.assertEqual(self.item("userAdminNewPassword").property("text"), "")
        self.assertEqual(self.warnings, [])


for _cls in (ManagerReviewPageTests, UserAdminPageTests):
    for _name in dir(harness.InvoicePageTests):
        if _name.startswith("test_") and _name not in _cls.__dict__:
            setattr(_cls, _name, None)


# ---------------------------------------------------------------------------------------------------------
# Old gold only, fully paid in real money: the customer is optional on the real Billing page
# ---------------------------------------------------------------------------------------------------------
class OldGoldOnlyCustomerOptionalPageTests(harness.InvoicePageTests):
    def prepare(self):
        self.select_salesperson()
        self.add_old_gold("21", "10")          # 10 g × 6207 − 1.5% = 61,138.95 exact → final 61,140
        self.wait_until(lambda: self.text("paymentDirectionLabel") == "المدفوع للعميل")

    def test_full_cash_payout_saves_without_any_customer(self):
        self.prepare()
        self.receive("61140")
        self.settle()
        self.assertEqual(self.vm.blockingProblems, [])
        self.assertNotIn("اسم العميل", self.text("invoiceProblems"))
        self.wait_until(lambda: self.item("finalizeInvoice").property("enabled"), "F9 disabled")
        self.click("finalizeInvoice")
        dialog = self.item("confirmFinalizeDialog")
        self.wait_until(lambda: dialog.property("opened"), "confirmation")
        QTest.qWait(250)
        self.click("confirmFinalize")
        self.wait_until(lambda: self.vm.hasSuccess, f"no success: {self.vm.errorMessage!r}")
        self.assertIsNone(self.store.invoices[0]["customer_id"])
        self.assertEqual(self.store.ledger, [])
        self.assertEqual([abs(m["amount_delta"]) for m in self.store.cash_movements], [D("61140.00")])
        self.assertEqual(self.warnings, [])

    def test_an_account_part_still_asks_for_the_customer(self):
        self.prepare()
        self.receive("61000")
        self.click("differenceToAccount")               # the account follows the FINAL amount: 140.00
        self.settle()
        self.wait_until(lambda: self.text("accountAmountField") == "140.00")
        self.assertIn("اسم العميل مطلوب لإتمام الفاتورة", self.text("invoiceProblems"))
        self.assertFalse(self.item("finalizeInvoice").property("enabled"))
        self.assertEqual(self.warnings, [])

    # ---- كسر فقط = حفظ فقط: no print action at all for old gold only; SALE and MIXED keep حفظ وطباعة
    def open_confirmation(self):
        self.wait_until(lambda: self.vm.canFinalize, f"finalize not enabled: {self.vm.blockingProblems}")
        QTest.keyClick(self.root, Qt.Key_F9)
        dialog = self.item("confirmFinalizeDialog")
        self.wait_until(lambda: dialog.property("opened"), "confirmation")
        QTest.qWait(250)
        return dialog

    def test_old_gold_only_offers_save_only_and_never_prints(self):
        self.prepare()
        self.receive("61140")
        self.settle()
        self.assertFalse(self.vm.printable)
        dialog = self.open_confirmation()
        self.assertEqual(self.text("confirmFinalize"), "حفظ (Enter)")             # not «حفظ فقط» / «حفظ وطباعة»
        self.assertIsNone(self.visible_item("confirmSaveAndPrint"))                # no print button exists
        self.assert_inside_window(self.item("confirmFinalize"))
        self.click("confirmFinalize")
        self.wait_until(lambda: self.vm.hasSuccess, f"no success: {self.vm.errorMessage!r}")
        self.wait_until(lambda: not dialog.property("visible"))
        self.assertEqual(len(self.store.invoices), 1)                               # saved normally
        self.assertEqual([abs(m["amount_delta"]) for m in self.store.cash_movements], [D("61140.00")])
        self.assertEqual(self.printer.calls, [])                                    # the print path is never reached
        self.assertFalse(self.item("printStatusBanner").property("visible"))
        self.assertFalse(self.vm.printBusy)
        self.assertEqual(self.warnings, [])

    def test_a_print_request_for_old_gold_only_is_dropped_by_the_view_model(self):
        self.prepare()
        self.receive("61140")
        self.settle()
        self.vm.finalizeInvoice(False, False, True)                                 # even if a caller asks to print
        self.wait_until(lambda: self.vm.hasSuccess, f"no success: {self.vm.errorMessage!r}")
        QTest.qWait(150)
        self.assertEqual(self.printer.calls, [])

    def test_sale_keeps_save_only_and_save_and_print(self):
        self.select_salesperson()
        self.scan("01260925")
        self.receive("67010")
        self.settle()
        self.name_customer()
        self.assertTrue(self.vm.printable)
        dialog = self.open_confirmation()
        self.assertEqual([self.text(n) for n in ("confirmFinalize", "confirmSaveAndPrint")],
                         ["حفظ فقط (Enter)", "حفظ وطباعة"])
        self.click("confirmSaveAndPrint")
        self.wait_until(lambda: self.vm.hasSuccess and self.vm.printSucceeded)
        self.assertEqual(self.printer.calls, ["901"])

    def test_mixed_keeps_save_only_and_save_and_print(self):
        self.select_salesperson()
        self.scan("01260925")
        self.add_old_gold("21", "5")
        self.receive("36440")
        self.settle()
        self.name_customer()
        self.assertTrue(self.vm.printable)
        dialog = self.open_confirmation()
        self.assertEqual([self.text(n) for n in ("confirmFinalize", "confirmSaveAndPrint")],
                         ["حفظ فقط (Enter)", "حفظ وطباعة"])
        self.click("confirmSaveAndPrint")
        self.wait_until(lambda: self.vm.hasSuccess and self.vm.printSucceeded)
        self.assertEqual(self.printer.calls, ["901"])


for _name in dir(harness.InvoicePageTests):
    if _name.startswith("test_") and _name not in OldGoldOnlyCustomerOptionalPageTests.__dict__:
        setattr(OldGoldOnlyCustomerOptionalPageTests, _name, None)


class StockEntryPermissionPageTests(harness.InvoicePageTests):
    """Final stabilization R3: the add-stock controls follow the actor's rights (a convenience), and the
    services refuse regardless of what the screen shows. Real clicks; the database is blocked."""

    def open_inventory(self):
        self.root.setProperty("currentPage", 2)
        QTest.qWait(150)

    def open_dialog(self):
        from PySide6.QtCore import QMetaObject
        dialog = self.item("newInventoryItemDialog")
        QMetaObject.invokeMethod(dialog, "openForNewItem")
        self.wait_until(lambda: dialog.property("opened"), "entry dialog")
        return dialog

    def test_the_add_button_is_hidden_without_a_stock_right_and_shown_with_either(self):
        self.open_inventory()
        self.assertIsNone(self.visible_item("newInventoryItemButton"))
        self.grant("INVENTORY_MANAGE")
        QTest.qWait(60)
        self.assertIsNotNone(self.visible_item("newInventoryItemButton"))
        self.grant("OLD_GOLD_MANAGE", revoke=("INVENTORY_MANAGE",))
        QTest.qWait(60)
        self.assertIsNotNone(self.visible_item("newInventoryItemButton"))
        self.grant(revoke=("INVENTORY_MANAGE", "OLD_GOLD_MANAGE"))
        QTest.qWait(60)
        self.assertIsNone(self.visible_item("newInventoryItemButton"))
        self.assertEqual(self.warnings, [])

    def test_the_dialog_disables_a_method_the_actor_may_not_use_at_all_sizes(self):
        self.open_inventory()
        self.grant("INVENTORY_MANAGE")
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                dialog = self.open_dialog()
                method = self.item("inventoryEntryMethodCombo")
                method.setProperty("currentIndex", 0)
                QTest.qWait(40)
                self.assertTrue(self.item("saveInventoryItemButton").property("enabled"))
                self.assertIsNone(self.visible_item("inventoryEntryNoRight"))
                method.setProperty("currentIndex", 1)                      # converted from Scrap: OLD_GOLD_MANAGE
                QTest.qWait(40)
                save = self.item("saveInventoryItemButton")
                self.assertFalse(save.property("enabled"))
                self.assertEqual(self.visible_item("inventoryEntryNoRight").property("text"),
                                 "لا تملك صلاحية هذا النوع من الإدخال.")
                self.assert_inside_window(save)
                self.assert_inside_window(self.visible_item("inventoryEntryNoRight"))
                dialog.close()
                self.wait_until(lambda: not dialog.property("visible"), "closed")
        self.assertEqual(self.warnings, [])

    def test_the_services_refuse_a_write_the_screen_never_offered(self):
        from services.inventory_service import InventoryService
        from services.old_gold_conversion_service import OldGoldConversionService
        vms = self.controller.operational_viewmodels
        session = self.controller.session
        vms["inventoryVM"]._service = InventoryService(session=session)
        vms["oldGoldConversionVM"]._service = OldGoldConversionService(session=session)
        self.assertFalse(vms["inventoryVM"].canManage)
        self.assertFalse(vms["oldGoldConversionVM"].canManage)
        # Bypassing the hidden button: the slots still run and the service refuses before any database access.
        self.assertFalse(vms["inventoryVM"].createInventoryItem(1, 1, 0, 0, "3.500", "", "NEW_STOCK", "", ""))
        self.assertFalse(vms["inventoryVM"].actionSuccess)
        self.assertIn("صلاحية", vms["inventoryVM"].actionMessage)
        self.assertFalse(vms["oldGoldConversionVM"].convertToNewStock(1, 1, 0, 0, "3.500", "3.500", "", "", ""))
        self.assertFalse(vms["oldGoldConversionVM"].actionSuccess)
        self.assertIn("صلاحية", vms["oldGoldConversionVM"].actionMessage)


for _name in dir(harness.InvoicePageTests):
    if _name.startswith("test_") and _name not in StockEntryPermissionPageTests.__dict__:
        setattr(StockEntryPermissionPageTests, _name, None)
