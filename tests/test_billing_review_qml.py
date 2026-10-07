"""Owner manual-review fixes on the real Billing page (real clicks / keys, real InvoiceViewModel and
InvoiceService over the in-memory store): draft reset, cash following the total, automatic
على العميل, the new payment row, live reaction, تصفير الفاتورة, method refresh, disabled methods and
the category manager approval."""
from decimal import Decimal as D

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from tests import test_invoice_page_qml as harness


class BillingReviewTests(harness.InvoicePageTests):
    def piece(self, weight="3.600", barcode="06260925", item_id="106"):
        # 3.600 g × 6232 = 22,435.20 gold value.
        self.store.items[item_id] = {**self.store.items["100"], "inventory_item_id": item_id,
                                     "weight_grams": D(weight), "barcode_value": barcode}

    def start_sale(self, total):
        self.piece()
        self.select_salesperson()
        self.scan("06260925")
        self.total(total)

    def legs(self):
        return [leg["amount"] for leg in self.vm.paymentLegs]

    # ------------------------------------------------------------ BUG-BILL-08
    def test_no_trace_of_a_cancelled_draft_survives(self):
        self.select_salesperson()
        self.add_old_gold("21", "10")
        self.type_into("legAmount_0", "60270")                       # the cancelled old-gold draft
        self.settle()
        self.assertEqual(self.legs(), ["60270"])
        QTest.keyClick(self.root, Qt.Key_F2)                          # فاتورة جديدة
        self.wait_until(lambda: self.vm.oldGoldCount == 0)
        self.piece()
        self.scan("06260925")                                         # a different item
        self.settle()
        texts = [self.text(n) for n in ("invoiceTotalField", "legAmount_0", "accountAmountField")]
        self.assertEqual(texts, ["", "", ""])
        self.assertEqual((self.vm.invoiceTotal, self.legs(), self.vm.accountAmount), ("", [""], ""))
        self.assertNotIn("60", self.vm.summary.get("effectiveSale") or "")
        self.assertNotIn("60,270", " ".join(str(v) for v in self.vm.summary.values()))
        self.total("25035")
        self.wait_until(lambda: self.text("legAmount_0") == "25035")
        self.assertEqual(self.warnings, [])

    def test_clear_invoice_button_uses_the_same_reset(self):
        self.start_sale("25035")
        self.type_into("legAmount_0", "24000")
        self.click("clearInvoiceButton")
        dialog = self.item("clearInvoiceDialog")
        self.wait_until(lambda: dialog.property("opened"))
        self.click("clearInvoiceCancel")                              # cancel keeps everything
        self.wait_until(lambda: not dialog.property("visible"))
        self.assertEqual(self.vm.saleCount, 1)
        self.click("clearInvoiceButton")
        self.wait_until(lambda: dialog.property("opened"))
        self.click("clearInvoiceConfirm")
        self.wait_until(lambda: self.vm.saleCount == 0)
        self.assertEqual((self.vm.invoiceTotal, self.legs(), self.vm.accountAmount,
                          self.text("invoiceTotalField")), ("", [""], "", ""))
        self.assertEqual(self.vm.salespersonId, "5")                  # the salesperson stays
        self.assertEqual(self.warnings, [])

    # ------------------------------------------------------------ BUG-BILL-04
    def test_untouched_cash_follows_every_total_change(self):
        self.start_sale("32220")
        self.wait_until(lambda: self.text("legAmount_0") == "32220")
        self.total("31820")
        self.wait_until(lambda: self.text("legAmount_0") == "31820", "cash did not follow")
        self.assertEqual(self.vm.accountAmount, "")
        self.type_into("legAmount_0", "30000")                         # manual from now on
        self.total("31825")
        self.assertEqual((self.text("legAmount_0"), self.vm.accountAmount), ("30000", "1825"))
        self.assertEqual(self.warnings, [])

    # ------------------------------------------------------------ REQ-BILL-05 / BUG-BILL-11 / BUG-BILL-06
    def test_remainder_goes_on_the_customer_and_a_new_row_takes_it(self):
        self.start_sale("25035")
        self.name_customer()
        self.click("legAmount_0")
        QTest.keyClick(self.root, Qt.Key_A, Qt.ControlModifier)
        self.type_text("24000")                                        # no Tab: the account updates at once
        self.assertEqual(self.text("accountAmountField"), "1035")
        self.assertTrue(self.item("legAmount_0").property("activeFocus"))
        self.assertTrue(self.item("accountAmountField").property("readOnly"))
        self.settle()
        self.wait_until(lambda: self.vm.canFinalize, f"{self.vm.blockingProblems}")   # Save at once
        self.click("addPaymentLeg")
        self.wait_until(lambda: self.visual_item("legAmount_1") is not None)
        self.assertEqual((self.text("legAmount_1"), self.vm.accountAmount), ("1035", ""))
        self.choose("legMethod_1", 2)                                 # InstaPay keeps the 1,035
        self.wait_until(lambda: self.item("legAccount_1").property("visible"))
        self.choose("legAccount_1", 0)
        self.assertEqual(self.text("legAmount_1"), "1035")
        self.type_into("legAmount_1", "500")
        self.wait_until(lambda: self.vm.accountAmount == "535")
        self.type_into("legAmount_1", "1035")
        self.wait_until(lambda: self.vm.accountAmount == "")
        self.settle()
        self.finalize()
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("24000.00")])
        self.assertEqual([(m["bank_account_id"], m["amount_delta"]) for m in self.store.bank_movements],
                         [(7, D("1035.00"))])
        self.assertEqual(self.store.ledger, [])
        self.assertEqual(self.warnings, [])

    def test_partial_payment_posts_the_remainder_to_the_customer_ledger(self):
        self.start_sale("25035")
        self.type_into("legAmount_0", "24000")
        self.settle()
        self.finalize()
        self.assertEqual(self.store.ledger[0]["amount_delta"], D("1035.00"))
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("24000.00")])

    # ------------------------------------------------------------ BUG-BILL-08 (methods) / BUG-BILL-10
    def test_disabled_method_never_counts_and_activation_needs_no_restart(self):
        for account in self.store.bank_accounts.values():
            if account["account_type"] == "BANK":
                account["is_active"] = 0                               # InstaPay: no active account
        self.vm.refreshReference()
        self.start_sale("25035")
        self.type_into("legAmount_0", "24000")
        self.click("addPaymentLeg")
        self.wait_until(lambda: self.visual_item("legMethod_1") is not None)
        self.choose("legMethod_1", 2)                                 # InstaPay • unavailable
        self.wait_until(lambda: self.item("legUnavailable_1").property("visible"))
        self.assertEqual(self.vm.accountAmount, "1035")               # not counted: still on the account
        self.name_customer()
        self.settle()
        self.assertFalse(self.vm.canFinalize)
        self.assertIn("غير مفعّلة", " ".join(self.vm.blockingProblems))
        # Activated in Settings while the app runs: leaving and re-entering Billing sees it.
        self.store.bank_accounts[7]["is_active"] = 1
        self.root.setProperty("currentPage", 0)
        QTest.qWait(80)
        self.root.setProperty("currentPage", 1)
        self.wait_until(lambda: any(a["id"] == "7" for a in self.vm._accounts_for("INSTAPAY")), "no refresh")
        self.choose("legAccount_1", 0)
        self.wait_until(lambda: self.vm.accountAmount == "")
        self.settle()
        self.wait_until(lambda: self.vm.canFinalize, f"{self.vm.blockingProblems}")
        self.assertEqual(self.warnings, [])

    # ------------------------------------------------------------ BUG-BILL-07
    def test_making_below_the_manager_threshold_needs_a_manager(self):
        self.store.categories[1].update(sale_making_min_per_gram=D("200"), sale_making_max_per_gram=D("400"),
                                        manager_approval_below_per_gram=D("250"))
        self.piece()
        self.store.items["106"]["category_id"] = 1
        self.select_salesperson()
        self.scan("06260925")
        self.name_customer()
        self.total("23230")                                             # 794.80 / 3.6 = 220.78 / g
        self.settle()
        self.wait_until(lambda: self.vm.makingNeedsApproval)
        self.assertIn("حد موافقة المدير", self.text("makingApprovalNote"))
        self.assertFalse(self.vm.canFinalize)
        self.click("requestMakingApproval")
        popup = self.item("approvalPopup")
        self.wait_until(lambda: popup.property("opened"))
        self.vm.verifyApprover("manager", "manager-pass")              # the existing counter approval
        self.wait_until(lambda: self.vm.approverOk, "approval")
        self.settle()
        self.wait_until(lambda: self.vm.canFinalize, f"{self.vm.blockingProblems}")
        popup.close()
        self.wait_until(lambda: not popup.property("visible"))
        self.finalize()
        review = next(r for r in self.store.reviews if r["review_type"] == "MAKING_PRICE")
        self.assertEqual(review["details"]["making_approved_by_user_id"], 3)

    def test_an_authorized_manager_still_confirms_explicitly(self):
        from tests import test_invoice_page_qml as page
        self.store.categories[1].update(sale_making_min_per_gram=D("200"), sale_making_max_per_gram=D("400"),
                                        manager_approval_below_per_gram=D("250"))
        page_permissions = page.PERMISSIONS | {"INVOICE_SETTLEMENT_APPROVE"}
        session = self.controller.session
        session._actor = session.current_actor().__class__(17, 7, "مدير", page_permissions,
                                                             session.current_actor().generation)
        self.vm._service._authorization.permissions = page_permissions
        self.piece()
        self.store.items["106"]["category_id"] = 1
        self.select_salesperson()
        self.scan("06260925")
        self.name_customer()
        self.total("23230")
        self.settle()
        self.wait_until(lambda: self.vm.makingCanConfirm, "explicit confirmation expected")
        self.assertFalse(self.vm.canFinalize)                                  # the right alone never approves
        self.click("requestMakingApproval")
        dialog = self.item("makingConfirmDialog")
        self.wait_until(lambda: dialog.property("opened"))
        self.assertEqual(self.item("makingConfirmText").property("text"),
                         "المصنعية أقل من الحد المحدد للفئة A — هل تعتمد البيع؟")
        self.click("makingConfirmCancel")                                      # cancel: still blocked
        self.wait_until(lambda: not dialog.property("visible"))
        self.settle()
        self.assertFalse(self.vm.canFinalize)
        self.click("requestMakingApproval")
        self.wait_until(lambda: dialog.property("opened"))
        self.click("makingConfirmApprove")
        self.wait_until(lambda: not dialog.property("visible"))
        self.settle()
        self.wait_until(lambda: self.vm.canFinalize, f"{self.vm.blockingProblems}")
        self.finalize()
        review = next(r for r in self.store.reviews if r["review_type"] == "MAKING_PRICE")
        self.assertEqual((review["details"]["making_approved_by_user_id"],
                          review["details"]["making_approval_kind"]), (17, "CONFIRMED_BY_ACTOR"))
        self.assertEqual(self.warnings, [])


# unittest otherwise inherits every Billing harness test, inflating the suite count.
for _name in dir(harness.InvoicePageTests):
    if _name.startswith("test_"):
        setattr(BillingReviewTests, _name, None)
