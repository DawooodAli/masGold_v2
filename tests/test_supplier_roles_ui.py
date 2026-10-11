"""Market-party roles on the real Suppliers page (044): «مورد» / «مسوقجي», both allowed, a role filter, supplier account cards and actions only
for the supplier role, and the form at the three target sizes - with real mouse clicks. Settlement, holdings, sales, bar allocation and reports are
NOT part of this slice."""
import unittest

from PySide6.QtTest import QTest

from tests import test_invoice_page_qml as harness
from tests import test_supplier_ui as base
from tests.supplier_fakes import ALL


class SupplierRolePageTests(base.SupplierPageTests):
    def open_with_parties(self):
        self.rights(*ALL)
        self.open_suppliers()
        self.svm.refresh()
        self.wait_idle()

    def create_through_the_form(self, name, supplier=True, intermediary=False, karat_index=0):
        self.click("newSupplierButton")
        self.type_in("supplierNameField", name)
        if not supplier:
            self.click("supplierRoleSupplier")                                        # checked by default: a click clears it
        if intermediary:
            self.click("supplierRoleIntermediary")
        if karat_index:
            self.pick("supplierKaratCombo", karat_index)

    def test_an_intermediary_only_party_is_created_without_a_karat_and_shows_no_supplier_account(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.open_with_parties()
        self.click("newSupplierButton")
        self.type_in("supplierNameField", "وسيط السوق")
        self.assertFalse(self.item("saveSupplierButton").property("enabled"))          # a supplier by default: its karat is still required
        self.click("supplierRoleSupplier")
        self.click("supplierRoleIntermediary")
        self.assertFalse(self.item("supplierKaratCombo").property("visible"))           # an intermediary has no account karat
        self.assertTrue(self.item("saveSupplierButton").property("enabled"))
        self.snapshot("role-form-intermediary")
        self.click("saveSupplierButton")
        self.wait_until(lambda: len(self.db.suppliers) == 1, "party saved")
        stored = next(iter(self.db.suppliers.values()))
        self.assertEqual((stored["is_supplier"], stored["is_intermediary"], stored["karat_id"]), (False, True, None))
        self.wait_idle()
        self.click_supplier("وسيط السوق")
        self.wait_until(lambda: self.svm.detail.get("name") == "وسيط السوق", "detail loaded")
        QTest.qWait(150)
        self.assertIsNotNone(self.visible_item("supplierRoleChip"))                    # the role is shown clearly
        self.assertIsNone(self.visible_item("supplierKaratChip"))                      # no karat, no "unresolved" warning
        self.assertIsNone(self.visible_item("goldPosition"))                           # no invented balances
        self.assertIsNone(self.visible_item("makingPosition"))
        self.assertIsNone(self.visible_item("payMakingButton"))                        # no nonfunctional transaction buttons
        self.assertIsNone(self.visible_item("newInvoiceButton"))
        self.assertIsNotNone(self.visible_item("intermediaryOnlyNote"))
        self.assertIsNotNone(self.visible_item("editSupplierButton"))                  # the party itself remains editable
        self.assertEqual(self.svm.detail["roleText"], "مسوقجي")
        self.assertEqual(self.warnings, [])

    def test_both_roles_need_the_karat_and_no_role_cannot_be_saved(self):
        self.open_with_parties()
        self.click("newSupplierButton")
        self.type_in("supplierNameField", "كلاهما")
        self.click("supplierRoleSupplier")                                             # clear the default role
        self.assertFalse(self.item("saveSupplierButton").property("enabled"))          # neither role
        self.assertIsNotNone(self.visible_item("roleRequiredHint"))
        self.click("supplierRoleSupplier")
        self.click("supplierRoleIntermediary")                                         # both roles
        self.assertIsNone(self.visible_item("roleRequiredHint"))
        self.assertFalse(self.item("saveSupplierButton").property("enabled"))          # the supplier role still needs its karat
        self.pick("supplierKaratCombo", 2)
        self.assertTrue(self.item("saveSupplierButton").property("enabled"))
        self.click("saveSupplierButton")
        self.wait_until(lambda: len(self.db.suppliers) == 1, "party saved")
        stored = next(iter(self.db.suppliers.values()))
        self.assertEqual((stored["is_supplier"], stored["is_intermediary"], stored["karat_id"]), (True, True, 2))

    def test_the_role_filter_lists_suppliers_or_intermediaries_and_both_role_parties_appear_in_each(self):
        service = self.svm._service
        self.grant(*ALL)
        service.create_supplier("مورد فقط", None, None, 1)
        service.create_supplier("وسيط فقط", None, None, None, is_supplier=False, is_intermediary=True)
        service.create_supplier("الاثنان", None, None, 2, is_supplier=True, is_intermediary=True)
        self.open_with_parties()
        names = lambda: sorted(r["name"] for r in self.svm.suppliers)
        self.assertEqual(names(), ["الاثنان", "مورد فقط", "وسيط فقط"])
        self.pick("supplierRoleFilter", 1)
        self.wait_until(lambda: names() == ["الاثنان", "مورد فقط"], "suppliers only")
        self.pick("supplierRoleFilter", 2)
        self.wait_until(lambda: names() == ["الاثنان", "وسيط فقط"], "intermediaries only")
        self.assertEqual(self.item("supplierList").property("count"), 2)
        self.pick("supplierRoleFilter", 0)
        self.wait_until(lambda: len(names()) == 3, "everyone again")
        self.assertEqual(self.warnings, [])

    def test_a_supplier_keeps_its_account_cards_and_an_existing_supplier_edit_keeps_the_karat_lock(self):
        sid = self.seed_supplier("مورد قديم", [(1, "10")])
        self.open_with_parties()
        self.click_supplier("مورد قديم")
        self.wait_until(lambda: self.svm.detail.get("name") == "مورد قديم", "detail loaded")
        QTest.qWait(150)
        self.assertIsNotNone(self.visible_item("goldPosition"))
        self.assertIsNotNone(self.visible_item("makingPosition"))
        self.assertIsNotNone(self.visible_item("supplierKaratChip"))
        self.assertIsNone(self.visible_item("intermediaryOnlyNote"))
        self.click("editSupplierButton")
        self.assertTrue(self.item("supplierRoleSupplier").property("checked"))
        self.assertFalse(self.item("supplierRoleIntermediary").property("checked"))
        self.assertFalse(self.item("supplierKaratCombo").property("enabled"))           # an invoice exists: the karat stays locked
        self.click("supplierRoleIntermediary")                                         # adding the other role changes nothing else
        self.click("saveSupplierButton")
        self.wait_until(lambda: self.db.suppliers[int(sid)]["is_intermediary"] is True, "role added")
        stored = self.db.suppliers[int(sid)]
        self.assertEqual((stored["is_supplier"], stored["karat_id"]), (True, 1))
        self.assertEqual(self.warnings, [])

    def test_the_role_form_fits_every_target_size(self):
        self.open_with_parties()
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(150)
                self.click("newSupplierButton")
                for name in ("supplierNameField", "supplierRoleSupplier", "supplierRoleIntermediary", "supplierKaratCombo", "supplierPhoneField",
                             "supplierNotesField", "saveSupplierButton"):
                    self.assert_inside_window(self.item(name))
                self.item("supplierDialog").close()
                QTest.qWait(150)
                self.assert_inside_window(self.item("supplierRoleFilter"))


for _name in dir(base.SupplierPageTests):
    if _name.startswith("test_") and _name not in SupplierRolePageTests.__dict__:
        setattr(SupplierRolePageTests, _name, None)


if __name__ == "__main__":
    unittest.main()
