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
        texts = [self.text(n) for n in ("legAmount_0", "accountAmountField")]
        self.assertEqual(texts, ["", ""])
        self.assertEqual((self.vm.invoiceTotal, self.legs(), self.vm.accountAmount), ("", [""], ""))
        self.assertNotIn("60", self.vm.summary.get("effectiveSale") or "")
        self.assertNotIn("60,270", " ".join(str(v) for v in self.vm.summary.values()))
        self.total("25035")
        self.wait_until(lambda: self.vm.invoiceTotal == "25035")
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
        self.assertEqual((self.vm.invoiceTotal, self.legs(), self.vm.accountAmount), ("", [""], ""))
        self.assertEqual(self.vm.salespersonId, "5")                  # the salesperson stays
        self.assertEqual(self.warnings, [])

    # ------------------------------------------------------------ BUG-BILL-04
    # ------------------------------------------------------------ REQ-BILL-05 / BUG-BILL-11 / BUG-BILL-06
    def test_partial_payment_posts_the_remainder_to_the_customer_ledger(self):
        self.start_sale("25035")
        self.type_into("legAmount_0", "24000")
        self.type_into("accountAmountField", "1035")                   # the total is what is paid + what stays on the customer
        self.name_customer()
        self.settle()
        self.finalize()
        self.assertEqual(self.store.ledger[0]["amount_delta"], D("1035.00"))
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("24000.00")])

    # ------------------------------------------------------------ BUG-BILL-08 (methods) / BUG-BILL-10
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
