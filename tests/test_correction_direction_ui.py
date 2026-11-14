"""«تصحيح طريقة التسوية» of a MIXED invoice in the real Main.qml (owner 2026-11-14, invoice 26111411): the manager turns the money direction around («المستلم من العميل» /
«المدفوع للعميل») with real clicks at 1440x900 / 1280x800 / 1080x680. The database is blocked; the service is a double that records what the screen asks for."""
from PySide6.QtCore import QPointF
from PySide6.QtTest import QTest

from tests import test_invoice_page_qml as harness

SIZES = ((1440, 900), (1280, 800), (1080, 680))
TEXT = {"IN": "المستلم من العميل", "OUT": "المدفوع للعميل"}


class Service:
    """A MIXED invoice recorded as 350 paid out in cash; every call is kept."""

    def __init__(self, mixed=True):
        self.mixed, self.direction, self.amount = mixed, "OUT", "350.00"
        self.previews, self.corrections = [], []

    def targets(self, invoice_id, *, _expected_actor=None):
        return {"invoice_id": "5114", "invoice_number": "26111411", "customer_id": "4940", "customer_name": "تيا اشلام رجب شعلان", "bank_accounts": [],
                "targets": [{"reference": "26111411", "source_type": "INVOICE", "source_id": "5114", "source_text": "الفاتورة", "direction": "SALE",
                             "reversed_by": None, "settlement_corrections": len(self.corrections), "settlement_allowed": True}]}

    def current_settlement(self, source_type, source_id, *, _expected_actor=None):
        return {"reference": "26111411", "direction": self.direction, "direction_text": TEXT[self.direction], "can_flip": self.mixed, "total": self.amount, "account": "0.00",
                "has_customer": True, "next_sequence": len(self.corrections) + 1, "combined_note": "",
                "legs": [{"method_code": "PHYSICAL_CASH", "bank_account_id": "", "amount": self.amount, "method_text": "نقدي", "account_name": ""}]}

    def preview_settlement(self, source_type, source_id, legs, account, *, direction=None, _expected_actor=None):
        self.previews.append((tuple((l.method_code, l.amount) for l in legs), account, direction))
        turned = direction not in (None, self.direction)
        same = tuple(l.amount for l in legs) == (self.amount,) or (legs and legs[0].amount.split(".")[0] == self.amount.split(".")[0])
        problems = [] if turned or not same else [{"code": "NO_EFFECT_CORRECTION", "message": "التسوية الجديدة مطابقة للحالية؛ لا يوجد تصحيح."}]
        return {"money_deltas": [{"account": "النقدية", "delta": "650.00" if turned else "50.00"}], "account_delta": "0.00", "problems": problems}

    def correct_settlement(self, source_type, source_id, legs, account, reason, *, expected_sequence=None, direction=None, _expected_actor=None):
        self.corrections.append({"legs": [(l.method_code, l.amount) for l in legs], "account": account, "reason": reason, "direction": direction, "sequence": expected_sequence})
        self.direction, self.amount = direction or self.direction, legs[0].amount
        return {"correction_id": "49", "code": "FC000050", "sequence": expected_sequence, "source_reference": "26111411"}


class CorrectionDirectionTests(harness.InvoicePageTests):
    def open_correction(self, size, mixed=True):
        self.root.setWidth(size[0])
        self.root.setHeight(size[1])
        self.service = Service(mixed)
        self.correction = self.controller.operational_viewmodels["correctionVM"]
        self.correction._service = self.service
        self.root.setProperty("currentPage", 1)
        QTest.qWait(80)
        self.correction.open("5114")
        self.wait_until(lambda: self.correction.isOpen and self.correction.targets and not self.correction.busy, "correction targets")
        self.click("correctionModeSettlement")
        self.wait_until(lambda: self.item("correctionLeg_0") is not None and self.visible_item("correctionLegAmount_0") is not None and not self.correction.busy, "settlement loaded")
        QTest.qWait(120)

    def close_correction(self):
        self.correction.close()
        QTest.qWait(40)

    def test_the_direction_choice_is_offered_for_a_mixed_invoice_and_changes_the_preview(self):
        for size in SIZES:
            with self.subTest(size=size):
                self.open_correction(size)
                self.assertTrue(self.visible_item("correctionDirectionRow"))
                for name in ("correctionDirectionIn", "correctionDirectionOut", "correctionLegAmount_0", "correctionReason", "correctionExecute"):
                    self.assert_inside_window(self.visible_item(name))
                self.assertIn("المدفوع للعميل", self.item("correctionCurrent").property("text"))
                self.assertTrue(self.item("correctionDirectionOut").property("selected"))                  # the recorded direction starts selected
                self.assertFalse(self.item("correctionDirectionIn").property("selected"))
                self.assertFalse(self.correction.canExecute)                                              # no reason yet and nothing changed
                self.click("correctionDirectionIn")                                                       # the customer is the one who paid
                self.wait_until(lambda: self.service.previews and self.service.previews[-1][2] == "IN" and not self.correction.busy, "preview of the turned direction")
                QTest.qWait(320)                                                                          # the debounced preview lands
                self.assertEqual(self.correction.settlement["direction"], "IN")
                self.assertTrue(self.correction.settlement["directionTurned"])
                self.assertIn("الاتجاه اتغيّر", self.item("correctionRemaining").property("text"))
                self.assertIn("المستلم من العميل", self.item("correctionSettlement").property("title"))
                self.click("correctionDirectionOut")                                                      # back to the recorded direction: nothing is passed on
                QTest.qWait(320)
                self.assertFalse(self.correction.settlement["directionTurned"])
                self.assertEqual(self.service.previews[-1][2], None)
                self.close_correction()
        self.assertEqual(self.warnings, [])

    def test_the_customer_paid_300_instead_of_350_paid_to_him_by_real_clicks(self):
        self.open_correction((1080, 680))
        self.click("correctionDirectionIn")
        self.type_into("correctionLegAmount_0", "300")
        self.type_into("correctionReason", "العميل هو اللي دفع")
        self.wait_until(lambda: self.correction.canExecute, "the correction can be executed")
        self.click("correctionExecute")
        self.wait_until(lambda: self.item("correctionConfirm").property("visible"), "confirmation")
        self.assertIn("سيتم عكس الحركة المسجلة (المدفوع للعميل)", self.item("correctionConfirmText").property("text"))
        self.assertIn("المستلم من العميل", self.item("correctionConfirmText").property("text"))
        self.click("correctionConfirmYes")
        self.wait_until(lambda: self.service.corrections and not self.correction.busy, "the correction is saved")
        saved = self.service.corrections[0]
        self.assertEqual((saved["direction"], saved["legs"], saved["reason"], saved["sequence"]), ("IN", [("PHYSICAL_CASH", "300")], "العميل هو اللي دفع", 1))
        self.wait_until(lambda: self.correction.settlement.get("direction") == "IN", "the screen reloads the corrected settlement")
        self.assertIn("المستلم من العميل", self.item("correctionCurrent").property("text"))            # the NEW recorded direction
        self.assertIn("FC000050", self.correction.notice)
        self.close_correction()
        self.assertEqual(self.warnings, [])

    def test_a_correction_without_a_turn_does_not_pass_a_direction_on(self):
        self.open_correction((1440, 900))
        self.type_into("correctionLegAmount_0", "340")
        self.type_into("correctionReason", "نقص عشرة")
        self.wait_until(lambda: self.correction.canExecute, "the correction can be executed")
        self.click("correctionExecute")
        self.wait_until(lambda: self.item("correctionConfirm").property("visible"), "confirmation")
        self.assertIn("سيتم عكس الحركة من الحساب المسجل بالخطأ", self.item("correctionConfirmText").property("text"))
        self.click("correctionConfirmYes")
        self.wait_until(lambda: self.service.corrections and not self.correction.busy, "the correction is saved")
        self.assertIsNone(self.service.corrections[0]["direction"])
        self.close_correction()

    def test_no_choice_is_offered_for_an_invoice_that_is_not_mixed(self):
        self.open_correction((1280, 800), mixed=False)
        self.assertIsNone(self.visible_item("correctionDirectionRow"))
        self.correction.setDirection("IN")                                                                 # even a stray call changes nothing
        self.assertFalse(self.correction.settlement["directionTurned"])
        self.assertNotIn("المدفوع للعميل:", self.item("correctionCurrent").property("text"))             # the recorded line keeps its old wording
        self.close_correction()


# unittest otherwise inherits every Billing harness test, inflating the suite count.
for _name in dir(harness.InvoicePageTests):
    if _name.startswith("test_"):
        setattr(CorrectionDirectionTests, _name, None)
