"""The invoice seal (anti-forgery code): rules, service and what lands on the bill / PDF. No database."""
from contextlib import contextmanager
import os
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtGui import QGuiApplication

from services import bill_overlay, invoice_seal as R
from services import invoice_seal_service as S
from tests import test_invoice_print_service as T

QGuiApplication.instance() or QGuiApplication([])
KEY = "a" * 64


class RuleTests(unittest.TestCase):
    def code(self, **overrides):
        values = {"number": "26111503", "amount": "2500", "direction": "IN", "date": "2026-11-15"}
        values.update(overrides)
        return R.code_for(KEY, values["number"], values["amount"], values["direction"], values["date"])

    def test_the_code_is_stable_and_has_the_printed_shape(self):
        self.assertEqual(self.code(), self.code())
        self.assertRegex(self.code(), r"^[0-9A-Z]{4}-[0-9A-Z]{4}$")
        self.assertEqual(self.code(amount="2500.00"), self.code(amount="2500"))      # the amount is fixed to two decimals
        self.assertEqual(self.code(date="2026-11-15 13:42:00"), self.code())         # only the day counts

    def test_every_covered_fact_and_the_key_change_the_code(self):
        base = self.code()
        for change in ({"number": "26111504"}, {"amount": "2500.05"}, {"direction": "OUT"}, {"date": "2026-11-16"}):
            self.assertNotEqual(self.code(**change), base, change)
        self.assertNotEqual(R.code_for("b" * 64, "26111503", "2500", "IN", "2026-11-15"), base)

    def test_the_check_characters_depend_on_the_number_only(self):
        self.assertEqual(len(R.check_for(KEY, "26111503")), R.CHECK_LEN)
        self.assertEqual(R.check_for(KEY, "26111503"), R.check_for(KEY, " 26111503 "))
        self.assertNotEqual(R.check_for(KEY, "26111503"), R.check_for(KEY, "26111504"))

    def test_typed_codes_are_normalised(self):
        code = self.code()
        self.assertTrue(R.matches(code, code.lower()))
        self.assertTrue(R.matches(code, code.replace("-", " ")))
        self.assertFalse(R.matches(code, "AAAA-AAAA"))
        self.assertFalse(R.matches(code, ""))


class FakeSealRepository:
    keys = {}

    def __init__(self, connection):
        pass

    def key(self, *, create=True):
        if "k" not in self.keys and create:
            self.keys["k"] = KEY
        return self.keys.get("k")


class FakeInvoices:
    rows = {}

    def __init__(self, connection):
        pass

    def get_invoice_id_by_number(self, number):
        return number if number in self.rows else None

    def get_invoice(self, invoice_id):
        return self.rows[invoice_id]


@contextmanager
def fake_transaction():
    yield object()


def invoice(**overrides):
    row = {"invoice_number": "26111503", "net_amount": "2500.00", "net_direction": "IN", "invoice_date": "2026-11-15",
           "invoice_status": "FINALIZED", "events": []}
    row.update(overrides)
    return row


class ServiceTests(unittest.TestCase):
    def setUp(self):
        FakeSealRepository.keys = {}
        FakeInvoices.rows = {"26111503": invoice()}
        self.service = S.InvoiceSealService(None, transaction_factory=fake_transaction, repository=FakeSealRepository)

    def verify(self, number, code):
        with patch.object(S, "InvoiceRepository", FakeInvoices):
            return self.service.verify(number, code)

    def test_the_key_is_created_once_and_the_seal_matches_the_verification(self):
        seal = self.service.seal(S.facts_of(invoice()))
        self.assertEqual(FakeSealRepository.keys["k"], KEY)
        self.assertEqual(self.verify("26111503", seal["code"])["state"], "GENUINE")
        self.assertEqual(self.verify("26111503", seal["code"].lower())["state"], "GENUINE")

    def test_a_changed_amount_date_or_code_is_not_genuine_and_shows_no_invoice(self):
        seal = self.service.seal(S.facts_of(invoice()))
        FakeInvoices.rows["26111503"] = invoice(net_amount="25000.00")
        result = self.verify("26111503", seal["code"])
        self.assertEqual((result["state"], result["invoice"]), ("MISMATCH", None))
        FakeInvoices.rows["26111503"] = invoice()
        self.assertEqual(self.verify("26111503", "ZZZZ-ZZZZ")["state"], "MISMATCH")

    def test_unknown_number_and_blank_number(self):
        self.assertEqual(self.verify("999", "AAAA-AAAA")["state"], "NOT_FOUND")
        self.assertEqual(self.verify("", "")["state"], "NOT_FOUND")

    def test_a_genuine_code_of_a_reversed_invoice_says_so(self):
        seal = self.service.seal(S.facts_of(invoice()))
        FakeInvoices.rows["26111503"] = invoice(invoice_id=7, events=[{"event_type": "REVERSED", "details_json": {
            "source_type": "INVOICE", "source_id": "7", "correction_code": "RV000001"}}])
        result = self.verify("26111503", seal["code"])
        self.assertEqual(result["state"], "GENUINE")
        self.assertEqual(result["invoice"]["reversed_by"], "RV000001")

    def test_a_failing_key_read_never_raises_from_seal(self):
        def broken():
            raise RuntimeError("db down")
        service = S.InvoiceSealService(None, transaction_factory=broken, repository=FakeSealRepository)
        self.assertIsNone(service.seal(S.facts_of(invoice())))


class FakeSeal:
    def seal(self, facts):
        return {"code": R.code_for(KEY, facts["number"], facts["net_amount"], facts["net_direction"], facts["invoice_date"]),
                "check": R.check_for(KEY, facts["number"])}


class BillTests(unittest.TestCase):
    def test_the_number_carries_its_check_and_the_barcode_stays_the_pure_number(self):
        service, _ = T.make_service(seal=FakeSeal())
        invoice_data = service._invoice_data("42")
        self.assertEqual(set(invoice_data["seal"]), {"code", "check"})
        ops, problem = service.compose(invoice_data)
        self.assertEqual(problem, "")
        number_ops = [op for op in ops if op.kind == "text" and " · " in op.text]
        self.assertEqual(len(number_ops), 1)
        self.assertIn(invoice_data["seal"]["check"], number_ops[0].text)
        self.assertEqual(bill_overlay.build_overlay(invoice_data).invoice_number, str(invoice_data["invoice_number"]))
        self.assertEqual(ops.seal_code, invoice_data["seal"]["code"])

    def test_without_a_seal_the_bill_is_exactly_what_it_was(self):
        service, _ = T.make_service()
        data = service._invoice_data("42")
        self.assertIsNone(data["seal"])
        ops, _ = service.compose(data)
        self.assertFalse([op for op in ops if " · " in getattr(op, "text", "")])
        self.assertEqual([op.kind for op in bill_overlay.with_stamp(ops)][-1], "stamp")

    def test_the_code_alone_is_under_the_table_above_the_lower_gold_line_and_never_covered(self):
        def overlap(p, q):
            return p.x < q.x + q.w and q.x < p.x + p.w and p.y < q.y + q.h and q.y < p.y + p.h
        for count in (1, 5, 10):
            service, _ = T.make_service(invoices={"42": T.invoice_row(employee_name="على", lines=[T.sale_line(i) for i in range(count)])}, seal=FakeSeal())
            data = service._invoice_data("42")
            ops, problem = service.compose(data)
            self.assertEqual(problem, "")
            self.assertFalse([op for op in ops if op.region == "header" and op.size_pt and op.size_pt <= 7], "nothing under the number any more")
            stamped = bill_overlay.with_stamp(ops)
            line = next(op for op in stamped if op.region == "seal")
            self.assertIn(data["seal"]["code"], line.text)
            self.assertNotIn("كود", line.text)                                 # the code alone
            self.assertLessEqual(line.y + line.h, bill_overlay.BillCalibration().lower_gold_y_mm)
            self.assertGreater(line.y, max(op.y + op.h for op in ops))        # under every printed row
            for other in stamped:
                if other.kind in ("stamp", "signature"):
                    self.assertFalse(overlap(line, other), f"{other.kind} covers the code at {count} rows")
            if count < 8:                                                      # with room: the far left, under the signature + stamp row
                self.assertEqual(line.x, bill_overlay.SEAL_CODE_LEFT_MM)

    def test_the_number_moves_down_to_make_room_for_a_bigger_qr(self):
        service, _ = T.make_service(seal=FakeSeal())
        plain, _ = T.make_service()
        before, _ = plain.compose(plain._invoice_data("42"))
        after, _ = service.compose(service._invoice_data("42"))
        number_before = next(op for op in before if op.kind == "text" and op.region == "header" and "26092699" in op.text)
        number_after = next(op for op in after if op.kind == "text" and " · " in op.text)
        self.assertAlmostEqual(number_after.y - number_before.y, bill_overlay.NUMBER_SHIFT_MM)
        self.assertLessEqual(number_after.y + number_after.h, bill_overlay.BillCalibration().upper_gold_y_mm)
        qr = next(op for op in after if op.kind == "qr")
        self.assertGreaterEqual(qr.w, 11.5)
        self.assertLessEqual(qr.y + qr.h, number_after.y)

    def test_the_qr_replaces_the_barcode_in_the_header_and_carries_number_and_code(self):
        for style in (bill_overlay.STYLE_PROTOTYPE, bill_overlay.STYLE_VARIANT_C):
            service, _ = T.make_service(seal=FakeSeal(), style=style)
            data = service._invoice_data("42")
            ops, problem = service.compose(data, style=style)
            self.assertEqual(problem, "", style)
            qr = next(op for op in ops if op.kind == "qr")
            self.assertEqual(qr.text, f"MG1.{data['invoice_number']}.{data['seal']['code']}")
            self.assertFalse([op for op in ops if op.kind == "bar"], "the barcode is replaced")
            number = next(op for op in ops if op.kind == "text" and " · " in op.text)
            self.assertLessEqual(qr.y + qr.h, number.y)                       # above the number
            self.assertGreaterEqual(qr.w, 8.5)
            self.assertEqual(qr.region, "header")
            self.assertEqual(len([op for op in bill_overlay.with_stamp(ops) if op.kind == "qr"]), 1, "no second QR beside the stamp")

    def test_without_a_seal_the_barcode_stays_and_there_is_no_qr(self):
        service, _ = T.make_service()
        ops, _ = service.compose(service._invoice_data("42"))
        self.assertTrue([op for op in ops if op.kind == "bar"])
        self.assertFalse([op for op in ops if op.kind == "qr"])

    def test_the_scanned_qr_text_opens_the_invoice_by_its_number(self):
        from app.viewmodels.invoice_history_viewmodel import InvoiceHistoryViewModel
        opened = []

        class Session:
            def current_actor(self):
                return None
        vm = InvoiceHistoryViewModel(Session())
        vm._start = lambda operation, call: opened.append(operation)
        vm.openByNumber("MG1.26111503.K7M2-9QX4")
        self.assertEqual(vm.lookupMessage, "")                                 # accepted as a number, not refused as non-numeric

    def test_the_pdf_is_written_with_the_seal(self):
        service, _ = T.make_service(seal=FakeSeal())
        with tempfile.TemporaryDirectory() as folder:
            path, number, problem = service.share_pdf("42", folder=folder)
            self.assertEqual(problem, "")
            self.assertGreater(os.path.getsize(path), 20_000)


class SellerSignatureTests(unittest.TestCase):
    def test_the_name_is_matched_whatever_the_spelling_and_unknown_sellers_get_none(self):
        self.assertTrue(bill_overlay.signature_path("علي"))
        self.assertEqual(bill_overlay.signature_path("على"), bill_overlay.signature_path("علي"))
        for name in ("مصطفي", "مصطفى", "حسين", "محمد", "محمود", "شلاطة", "شلاطه"):
            self.assertTrue(bill_overlay.signature_path(name), name)
        self.assertEqual(len({bill_overlay.signature_path(n) for n in ("علي", "مصطفى", "حسين", "محمد", "محمود", "شلاطة")}), 6)
        self.assertIsNone(bill_overlay.signature_path("بائع بلا توقيع"))
        self.assertIsNone(bill_overlay.signature_path(""))

    def test_the_signature_is_always_on_the_left_of_the_stamp_on_the_same_level(self):
        for count in (1, 5, 10):
            service, _ = T.make_service(invoices={"42": T.invoice_row(employee_name="على", lines=[T.sale_line(i) for i in range(count)])}, seal=FakeSeal())
            ops, _ = service.compose(service._invoice_data("42"))
            stamped = bill_overlay.with_stamp(ops)
            stamp = next(op for op in stamped if op.kind == "stamp")
            sign = next(op for op in stamped if op.kind == "signature")
            content_bottom = max(op.y + op.h for op in ops if op.region == "table")
            self.assertLessEqual(sign.x + sign.w, stamp.x, count)               # the signature is to the LEFT of the stamp
            self.assertAlmostEqual(sign.w, 20.0, places=2, msg=count)           # always 20 mm wide
            self.assertGreaterEqual(sign.y, stamp.y - 0.01, count)
            self.assertLessEqual(sign.y + sign.h, stamp.y + stamp.h + 0.01, count)   # on the stamp's level
            self.assertGreater(sign.y, content_bottom, count)                   # under the table
            self.assertLessEqual(sign.y + sign.h, bill_overlay.BillCalibration().lower_gold_y_mm, count)
            self.assertLessEqual(stamp.x + stamp.w, 127, count)                 # clear of the total amount text of the money box
            words = min((op for op in ops if op.region == "money" and op.kind == "text"), key=lambda op: op.x)
            box = next(op for op in ops if op.region == "money" and op.kind == "box")
            if count < 9:                                                       # room under the amount box: hung from its bottom line, centred under the «العدد» column
                cal = bill_overlay.BillCalibration()
                qty_centre = cal.x(bill_overlay.PROTO_TABLE_X + bill_overlay.PROTO_COLUMNS[0][3] + bill_overlay.PROTO_COLUMNS[1][3] / 2)
                self.assertAlmostEqual(stamp.x + stamp.w / 2, qty_centre, delta=0.05, msg=count)
                self.assertAlmostEqual(stamp.y, box.y + box.h - bill_overlay.STAMP_TOUCH_MM, delta=0.05, msg=count)
            else:                                                               # a full sheet: after the Arabic words
                self.assertGreater(sign.x, words.x + 17, count)

    def test_a_seller_without_a_picture_prints_no_signature(self):
        service, _ = T.make_service()                                       # «بائع تجريبي»
        ops, _ = service.compose(service._invoice_data("42"))
        self.assertFalse([op for op in bill_overlay.with_stamp(ops) if op.kind == "signature"])


class SecretKeyIsNeverOnTheSettingsScreenTests(unittest.TestCase):
    def test_the_internal_namespace_cannot_be_created_or_edited_from_the_settings_screen(self):
        from services.system_settings_service import SettingsError, clean_key
        with self.assertRaises(SettingsError) as raised:
            clean_key(R.KEY_SETTING)
        self.assertEqual(raised.exception.code, "KEY_RESERVED")
        self.assertEqual(clean_key("report.alert_days"), "report.alert_days")


if __name__ == "__main__":
    unittest.main()
