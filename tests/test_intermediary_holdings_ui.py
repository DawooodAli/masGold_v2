"""Whole-lot holdings at a مسوقجي (048) on the real Main.qml with real mouse clicks at the three target sizes: «تسليم ذهب لمسوقجي» on the custody page and
«ذهب المحل لدى المسوقجي» (with the intact-return confirmation) on the party page. The services are the real ones over the in-memory database of
tests/market_trip_fakes.py; the supplier READ service is a small stub that renders the party from the same database (the real SQL was exercised on the
masgold_rehearsal sandbox). Post-commit refreshes are observed through the REAL controller wiring. NOT real-MySQL tests."""
from datetime import datetime
from decimal import Decimal as D
from unittest.mock import patch
import unittest

from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from services.correction_service import CorrectionService
from services.intermediary_holding_service import IntermediaryHoldingService
from tests import test_invoice_page_qml as harness
from tests import test_market_trip_ui as base
from tests.market_trip_fakes import Corrections, Customers, HoldingRepo, Invoices, OWNER, SettlementRepo, TripRepo, build
from tests.test_market_trip_gold_settle_ui import GoldSettlePageTests
from tests.supplier_fakes import ALL as SUPPLIER_RIGHTS

TODAY = "2026-10-10"
RIGHTS = tuple(SUPPLIER_RIGHTS) + tuple(OWNER)
PARTY = "20"


def parties(db):
    db.suppliers[20] = {"id": 20, "name": "مسوقجي أول", "is_active": 1, "is_supplier": False, "is_intermediary": True, "karat_id": None}
    db.suppliers[22] = {"id": 22, "name": "مسوقجي موقوف", "is_active": 0, "is_supplier": False, "is_intermediary": True, "karat_id": None}


class HoldingCustodyPageTests(GoldSettlePageTests):
    """Custody page: the handover dialog, the external-holdings section, the closure wording and the reversal."""

    def setUp(self):
        super().setUp()
        parties(self.db)
        self.holding = IntermediaryHoldingService(self.controller.session, transaction_factory=self.db.transaction, repository=TripRepo, holdings=HoldingRepo,
                                                  parties=SettlementRepo, authorization=self.authorization)
        self.tvm._holdings = self.holding

    def prepare(self, residues=()):
        self.open_page()
        service = self.tvm._service
        trip = service.issue("", "", [{"karat_id": "1", "weight": "20", "designation": "MELT"}], TODAY, "", self.tvm.newRequestKey())
        service.melt(trip["trip_id"], [{"karat_id": "1", "weight": "20"}], TODAY, "ورشة", {"weight": "10", "fineness": "875"},
                     [{"weight": w, "fineness": f} for w, f in residues], "", "", "", self.tvm.newRequestKey())
        self.tvm.selectTrip(trip["trip_id"])
        self.tvm.refresh()
        self.wait_idle()
        self.wait_until(lambda: self.tvm.detail.get("holdAvailable"), "handover available")
        return trip["trip_id"]

    def open_hold(self):
        self.reveal(self.item("custodyLotHoldButton"))
        self.click("custodyLotHoldButton")
        self.wait_until(lambda: self.item("tripLotHoldDialog").property("opened"), "handover dialog")
        self.wait_until(lambda: len(self.tvm.context.get("holdParties", [])) == 3, "parties loaded")
        QTest.qWait(120)

    def party_index(self, party_id):
        return [""] .index("") + 1 + [p["id"] for p in self.tvm.context["holdParties"]].index(str(party_id))

    def fill(self, party=PARTY, lot_index=1):
        self.pick("custodyLotHoldPartyCombo", self.party_index(party))
        self.pick("custodyLotHoldLotCombo", lot_index)

    def type_replace(self, name, text):
        self.click(name)
        QTest.keyClick(self.root, Qt.Key_A, Qt.ControlModifier)
        self.type_text(text)
        QTest.qWait(80)

    def untouched(self):
        return (len(self.db.scrap), self.db.cash(), len(self.db.shop_moves), len(self.db.sm_making), len(self.db.sup_gold), len(self.db.settlements))

    def test_handover_with_real_clicks_at_1080_keeps_every_other_ledger_and_refreshes_the_supplier_views(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        trip = self.prepare()
        before, reloads = self.untouched(), self.stub.reloads
        self.open_hold()
        for name in ("custodyLotHoldPartyCombo", "custodyLotHoldLotCombo", "confirmCustodyLotHoldButton", "cancelCustodyLotHoldButton"):
            self.assert_inside_window(self.item(name))
        self.assertFalse(self.item("confirmCustodyLotHoldButton").property("enabled"))
        self.fill()
        self.assertIn("875", self.text("custodyLotHoldFacts"))
        self.assertIn("10.000", self.text("custodyLotHoldFacts"))
        self.assertTrue(self.item("confirmCustodyLotHoldButton").property("enabled"))
        self.click("confirmCustodyLotHoldButton")
        self.wait_until(lambda: len(self.db.holding_docs) == 1, "handover posted")
        self.closed("tripLotHoldDialog")
        self.wait_idle()
        self.assertEqual(self.untouched(), before)
        self.wait_until(lambda: self.stub.reloads > reloads, "supplier views refreshed after the commit")
        self.assertIn("IH000001", " ".join(self.visible_texts("entryWhat")))
        self.assertEqual(self.tvm._service.trip_detail(trip)["custody"]["lots"], [])
        self.assertEqual(len(self.visible_texts("externalHoldingRow")), 1)
        self.assertIn("لا يعني", self.text("closeHint"))                       # the closure wording never implies that the gold came back
        self.assertTrue(self.tvm.detail["closable"])
        self.assertEqual(self.warnings, [])

    def test_the_handover_dialog_is_fully_visible_at_the_three_target_sizes(self):
        self.prepare()
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            with self.subTest(size=f"{width}x{height}"):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(200)
                self.open_hold()
                for name in ("custodyLotHoldPartyCombo", "custodyLotHoldLotCombo", "custodyLotHoldDateField", "confirmCustodyLotHoldButton",
                             "cancelCustodyLotHoldButton"):
                    self.assert_inside_window(self.item(name))
                self.click("cancelCustodyLotHoldButton")
                self.closed("tripLotHoldDialog")


    # ------------------------------------------------------------ 049: master-karat LOGO grams
    def prepare_logo(self, grams="20"):
        self.open_page()
        trip = self.tvm._service.issue("", "", [{"karat_id": "1", "weight": grams, "designation": "LOGO"}], TODAY, "", self.tvm.newRequestKey())
        self.tvm.selectTrip(trip["trip_id"])
        self.tvm.refresh()
        self.wait_idle()
        self.wait_until(lambda: self.tvm.detail.get("logoHoldAvailable"), "logo handover available")
        return trip["trip_id"]

    def open_logo_hold(self):
        self.reveal(self.item("custodyLogoHoldButton"))
        self.click("custodyLogoHoldButton")
        self.wait_until(lambda: self.item("tripLogoHoldDialog").property("opened"), "logo handover dialog")
        self.wait_until(lambda: len(self.tvm.context.get("holdParties", [])) == 3, "parties loaded")
        QTest.qWait(120)

    def fill_logo(self, grams):
        self.pick("custodyLogoHoldPartyCombo", self.party_index(PARTY))
        self.pick("custodyLogoHoldKaratCombo", 1)
        self.type_in("custodyLogoHoldGramsField", grams)

    def test_logo_handover_with_real_clicks_at_1080_moves_exact_grams_and_nothing_else(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        trip = self.prepare_logo()
        before, reloads = self.untouched(), self.stub.reloads
        self.open_logo_hold()
        for name in ("custodyLogoHoldPartyCombo", "custodyLogoHoldKaratCombo", "custodyLogoHoldGramsField", "confirmCustodyLogoHoldButton", "cancelCustodyLogoHoldButton"):
            self.assert_inside_window(self.item(name))
        self.assertFalse(self.item("confirmCustodyLogoHoldButton").property("enabled"))
        self.fill_logo("8")
        self.assertIn("20.000", self.text("custodyLogoHoldFacts"))
        self.assertTrue(self.item("confirmCustodyLogoHoldButton").property("enabled"))
        self.click("confirmCustodyLogoHoldButton")
        self.wait_until(lambda: len(self.db.logo_docs) == 1, "handover posted")
        self.closed("tripLogoHoldDialog")
        self.wait_idle()
        self.assertEqual(self.untouched(), before)
        self.wait_until(lambda: self.stub.reloads > reloads, "supplier views refreshed after the commit")
        gold = next(g for g in self.tvm._service.trip_detail(trip)["custody"]["gold"] if g["designation"] == "LOGO")
        self.assertEqual((gold["outstanding"], gold["held_out"]), (D("12.000"), D("8.000")))
        self.assertIn("IG000001", " ".join(self.visible_texts("entryWhat")))
        self.assertEqual(len(self.visible_texts("externalHoldingRow")), 1)
        self.assertEqual(self.warnings, [])

    def test_logo_handover_refusals_keep_the_fields_and_a_double_submission_posts_once(self):
        self.prepare_logo()
        self.open_logo_hold()
        self.fill_logo("25")
        self.assertFalse(self.item("confirmCustodyLogoHoldButton").property("enabled"))        # more than the effective custody
        self.assertIn("20.000", self.text("custodyLogoHoldPreviewError"))
        self.type_replace("custodyLogoHoldGramsField", "5")
        self.type_in("custodyLogoHoldReferenceField", "مرجع لوجو")
        self.db.fail_at = "logo_link"
        self.click("confirmCustodyLogoHoldButton")
        self.wait_idle()
        self.assertTrue(self.item("tripLogoHoldDialog").property("opened"))
        self.assertEqual((self.text("custodyLogoHoldGramsField"), self.text("custodyLogoHoldReferenceField")), ("5", "مرجع لوجو"))
        self.assertEqual(self.db.logo_docs, {})
        self.db.fail_at = None
        self.click("confirmCustodyLogoHoldButton")
        # a double click re-sends the SAME request key of the opened dialog: it is idempotent, never a second handover
        self.tvm.handOverLogo(self.tvm.selectedTripId, PARTY, "1", "5", TODAY, "", "", self.item("tripLogoHoldDialog").property("requestKey"))
        self.wait_until(lambda: len(self.db.logo_docs) == 1, "retry posted")
        self.wait_idle()
        self.assertEqual(len(self.db.logo_pools), 1)
        self.assertEqual(len(self.db.logo_links), 1)

    def test_the_logo_dialog_is_fully_visible_at_the_three_target_sizes(self):
        self.prepare_logo()
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            with self.subTest(size=f"{width}x{height}"):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(200)
                self.open_logo_hold()
                for name in ("custodyLogoHoldPartyCombo", "custodyLogoHoldKaratCombo", "custodyLogoHoldGramsField", "custodyLogoHoldDateField",
                             "confirmCustodyLogoHoldButton", "cancelCustodyLogoHoldButton"):
                    self.assert_inside_window(self.item(name))
                self.click("cancelCustodyLogoHoldButton")
                self.closed("tripLogoHoldDialog")

    def test_reversing_a_logo_handover_from_the_custody_page_restores_the_stored_grams(self):
        trip = self.prepare_logo()
        self.open_logo_hold()
        self.fill_logo("8")
        self.click("confirmCustodyLogoHoldButton")
        self.wait_until(lambda: len(self.db.logo_docs) == 1, "posted")
        self.closed("tripLogoHoldDialog")
        self.wait_idle()
        buttons = self.reverse_buttons()
        self.reveal(buttons[-1])
        self.click(buttons[-1])
        self.wait_until(lambda: self.item("tripReasonDialog").property("opened"), "reversal dialog")
        self.type_in("tripReasonField", "تسليم بالخطأ")
        self.click("confirmTripReasonButton")
        self.wait_until(lambda: len(self.db.corrections) == 1, "reversed")
        self.closed("tripReasonDialog")
        self.wait_idle()
        gold = next(g for g in self.tvm._service.trip_detail(trip)["custody"]["gold"] if g["designation"] == "LOGO")
        self.assertEqual(gold["outstanding"], D("20.000"))
        self.assertEqual([m["movement_type"] for m in self.db.logo_moves], ["HANDOVER", "HANDOVER_REVERSAL"])
        self.assertEqual(self.warnings, [])

    def test_unknown_assay_inactive_party_and_a_closed_custody_offer_no_handover(self):
        self.prepare([("2", "")])
        self.open_hold()
        self.pick("custodyLotHoldLotCombo", 1)
        self.assertEqual(len(self.tvm.detail["holdSources"]), 1)               # the unknown-assay residue is not offered
        self.pick("custodyLotHoldPartyCombo", self.party_index(22))
        self.assertIn("موقوف", self.text("custodyLotHoldPreviewError"))
        self.assertFalse(self.item("confirmCustodyLotHoldButton").property("enabled"))
        self.assertEqual(self.db.holding_docs, {})

    def test_refusal_keeps_the_fields_and_a_double_submission_posts_once(self):
        self.prepare()
        self.open_hold()
        self.fill()
        self.type_in("custodyLotHoldReferenceField", "مرجع خاص")
        lot_id = self.tvm.detail["holdSources"][0]["id"]
        self.db.fail_at = "holding_link"
        self.click("confirmCustodyLotHoldButton")
        self.wait_idle()
        self.assertTrue(self.item("tripLotHoldDialog").property("opened"))
        self.assertEqual(self.text("custodyLotHoldReferenceField"), "مرجع خاص")
        self.assertEqual(self.db.holding_docs, {})
        self.db.fail_at = None
        self.click("confirmCustodyLotHoldButton")
        # a second submission while the worker owns the write cannot launch another handover
        self.tvm.handOverLot(self.tvm.selectedTripId, PARTY, lot_id, TODAY, "", "", self.tvm.newRequestKey())
        self.wait_until(lambda: len(self.db.holding_docs) == 1, "retry posted")
        self.wait_idle()
        self.assertEqual(len(self.db.holding_links), 1)

    def test_reversal_from_the_custody_page_restores_the_lot_with_a_compensation(self):
        trip = self.prepare()
        self.open_hold()
        self.fill()
        self.click("confirmCustodyLotHoldButton")
        self.wait_until(lambda: len(self.db.holding_docs) == 1, "posted")
        self.closed("tripLotHoldDialog")
        self.wait_idle()
        reloads = self.stub.reloads
        buttons = self.reverse_buttons()
        self.assertEqual(len(buttons), 3)                                         # issue, melt and the handover
        self.reveal(buttons[-1])
        self.click(buttons[-1])
        self.wait_until(lambda: self.item("tripReasonDialog").property("opened"), "reversal dialog")
        self.assertIn("تعويض", self.text("tripReasonNote"))
        self.assertFalse(self.item("confirmTripReasonButton").property("enabled"))
        self.type_in("tripReasonField", "تسليم بالخطأ")
        self.click("confirmTripReasonButton")
        self.wait_until(lambda: len(self.db.corrections) == 1, "reversed")
        self.closed("tripReasonDialog")
        self.wait_idle()
        restored = self.tvm._service.trip_detail(trip)["custody"]["lots"][0]
        self.assertEqual(restored["outstanding"], D("10"))
        self.assertEqual([m["movement_type"] for m in self.db.holding_moves], ["HANDOVER", "HANDOVER_REVERSAL"])
        self.assertIn("RV000001", " ".join(self.visible_texts("entryState")))
        self.wait_until(lambda: self.stub.reloads > reloads, "supplier views refreshed after the reversal")
        self.assertEqual(len(self.visible_texts("externalHoldingRow")), 0)
        self.assertEqual(self.warnings, [])


class HoldingSupplierStub:
    """SupplierService.list_suppliers / supplier_detail for the intermediary parties of the fake database, with the holdings of the REAL holding service."""

    def __init__(self, db, holding):
        self.db, self.holding, self.reloads = db, holding, 0

    def _supplier(self, row):
        return {"id": str(row["id"]), "code": f"S{row['id']:06d}", "name": row["name"], "phone": "", "notes": "", "is_active": bool(row["is_active"]),
                "karat_id": None, "karat_value": None, "karat_locked": False, "is_supplier": False, "is_intermediary": True, "role_text": "مسوقجي",
                "history_karats": []}

    def list_suppliers(self, search="", active_only=False, *, _expected_actor=None, **kw):
        self.reloads += 1
        return [{**self._supplier(r), "gold_status": "NOT_APPLICABLE", "gold_balance": None, "gold_direction": None, "making": D("0.00"),
                 "making_direction": "SETTLED", "posted_invoice_count": 0} for r in self.db.suppliers.values() if r.get("is_intermediary") and r["id"] in (20, 22)]

    def supplier_detail(self, supplier_id, *, _expected_actor=None):
        holdings = self.holding.party_holdings(supplier_id)
        return {"supplier": self._supplier(self.db.suppliers[int(supplier_id)]),
                "balances": {"gold_status": "NOT_APPLICABLE", "gold_karat_value": None, "gold_karat_id": None, "gold_by_karat": [], "gold_direction": None,
                             "gold_balance": None, "making_direction": "SETTLED", "making": D("0.00")},
                "gold_ledger": [], "making_ledger": [], "holdings": {"pools": holdings["pools"], "history": holdings["history"], "total": holdings["total"],
                                                                      "pure": holdings["pure"], "can_manage": holdings["can_manage"],
                                                                      "logo_pools": holdings["logo_pools"], "logo_history": holdings["logo_history"],
                                                                      "settlements": holdings["settlements"], "can_settle": holdings["can_settle"],
                                                                      "can_reverse": holdings["can_reverse"], "receipts": holdings["receipts"],
                                                                      "can_receive": holdings["can_receive"], "can_reverse_receipt": holdings["can_reverse_receipt"]}}


class HoldingPartyPageTests(harness.InvoicePageTests):
    """Party page: the holdings panel, the return dialog with the required intact confirmation, refusals and closed custodies."""

    def setUp(self):
        super().setUp()
        session = self.controller.session
        self.db, _, self.authorization, self.trips, self.corrections = build(permissions=OWNER, session=session)
        for target, value in (("repositories.market_trip_repository.MarketTripRepository", TripRepo),
                              ("repositories.intermediary_holding_repository.IntermediaryHoldingRepository", HoldingRepo),
                              ("repositories.supplier_gold_settlement_repository.SupplierGoldSettlementRepository", SettlementRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)
        parties(self.db)
        self.holding = IntermediaryHoldingService(session, transaction_factory=self.db.transaction, repository=TripRepo, holdings=HoldingRepo,
                                                  parties=SettlementRepo, authorization=self.authorization)
        vms = self.controller.operational_viewmodels
        self.svm, self.tvm = vms["supplierVM"], vms["marketTripVM"]
        self.stub = HoldingSupplierStub(self.db, self.holding)
        self.svm._service, self.svm._holdings = self.stub, self.holding
        self.tvm._service, self.tvm._holdings = self.trips, self.holding
        vms["supplierPurchaseVM"]._service = type("NoInvoices", (), {"list_invoices": lambda s, supplier, status=None, **kw: []})()
        self.grant(*RIGHTS)
        self.svm.changed.emit()
        self.tvm.changed.emit()
        QTest.qWait(60)

    # ------------------------------------------------------------ helpers
    def closed(self, name):
        self.wait_until(lambda: not self.item(name).property("visible"), f"{name} closed")

    visible_texts = base.MarketTripPageTests.visible_texts

    # the secondary actions sit behind «المزيد» (never preselecting or hiding a declaration: those live in the dialogs)
    MORE = {"holdingSettleButton": "holdingMoreButton", "holdingReturnButton": "holdingMoreButton",
            "logoSettleButton": "logoMoreButton", "logoReturnButton": "logoMoreButton", "receiptReverseButton": "receiptMoreButton"}

    def item(self, name):
        if name in self.MORE:
            more = self.root.findChild(QObject, self.MORE[name]) or self.visual_item(self.MORE[name])
            if more is not None and more.property("visible") and more.property("text") == "المزيد":
                self.click(more)
        return super().item(name)

    def open_gold_tab(self):
        """The intermediary holdings live in «سجل الذهب»: the employee opens that tab (it is not the default one)."""
        tab = self.item("tabGold")
        if tab.property("visible") and not tab.property("on"):
            self.click(tab)
            QTest.qWait(80)

    def open_details(self):
        toggle = self.item("holdingDetailsToggle")
        if toggle.property("visible") and not self.item("holdingDetailsCard").property("visible"):
            self.click(toggle)
            QTest.qWait(80)

    def key(self):
        return self.svm.newRequestKey()

    def hand_over(self, party=PARTY):
        trip = self.trips.issue("", "", [{"karat_id": "1", "weight": "20", "designation": "MELT"}], TODAY, "", self.key())
        melt = self.trips.melt(trip["trip_id"], [{"karat_id": "1", "weight": "20"}], TODAY, "ورشة", {"weight": "10", "fineness": "875"}, [], "", "", "", self.key())
        lot = next(l["id"] for l in self.db.lots.values() if l["entry_id"] == int(melt["entry_id"]))
        done = self.holding.hand_over_lot(trip["trip_id"], party, str(lot), TODAY, "", "", self.key())
        return trip["trip_id"], lot, done

    def wait_idle(self):
        self.wait_until(lambda: not self.svm.busy and not self.tvm.busy, "workers idle")
        QTest.qWait(80)

    def open_party(self, party=PARTY):
        self.root.setProperty("currentPage", 14)
        QTest.qWait(200)
        self.svm.refresh()
        self.wait_idle()
        self.svm.selectSupplier(party)
        self.wait_until(lambda: "id" in self.svm.detail, "party detail")
        self.wait_idle()
        QTest.qWait(80)
        self.open_gold_tab()
        self.open_details()

    def open_return(self):
        self.click("holdingReturnButton")
        self.wait_until(lambda: self.item("holdingReturnDialog").property("opened"), "return dialog")
        QTest.qWait(100)

    def test_the_panel_lists_each_source_with_its_references_and_the_return_needs_the_confirmation_at_1080(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        trip, lot, done = self.hand_over()
        self.tvm.selectTrip(trip)
        self.open_party()
        self.assertTrue(self.item("holdingsPanel").property("visible"))
        self.assertIn("الناتج الرئيسي", self.text("holdingSource"))
        self.assertIn("10.000", self.text("holdingFigures"))
        self.assertIn("875", self.text("holdingFigures"))
        refs = self.text("holdingRefs")
        self.assertIn("IH000001", refs)
        self.assertIn("MT000001-3", refs)
        self.assertIn("المسوقجي", self.text("holdingsDetailsNote"))                 # full ownership explanation is under Details, not repeated on the main card
        self.assert_inside_window(self.item("holdingReturnButton"))
        self.open_return()
        for name in ("holdingIntactConfirm", "confirmHoldingReturnButton", "cancelHoldingReturnButton", "holdingReturnDate"):
            self.assert_inside_window(self.item(name))
        self.assertIn("تم استلام نفس السبكة كاملة دون تجزئة", self.text("holdingIntactText"))
        self.assertFalse(self.item("confirmHoldingReturnButton").property("enabled"))        # a full balance alone is not enough
        self.click("holdingIntactConfirm")
        self.assertTrue(self.item("confirmHoldingReturnButton").property("enabled"))
        reloads = self.stub.reloads
        self.click("confirmHoldingReturnButton")
        self.wait_until(lambda: len(self.db.holding_docs) == 2, "return posted")
        self.closed("holdingReturnDialog")
        self.wait_idle()
        self.assertEqual(self.holding.party_holdings(PARTY)["pools"], [])
        self.assertEqual(self.trips.trip_detail(trip)["custody"]["lots"][0]["outstanding"], D("10"))
        self.wait_until(lambda: self.stub.reloads > reloads, "party reloaded after the commit")
        self.wait_until(lambda: self.tvm.detail.get("lots") and self.tvm.detail["lots"][0]["outstanding"] == "10.000", "custody view reloaded after the commit")
        self.assertEqual(len(self.visible_texts("holdingHistoryRow")), 2)
        self.assertEqual(self.warnings, [])

    def test_the_return_dialog_is_fully_visible_at_the_three_target_sizes(self):
        self.hand_over()
        self.open_party()
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            with self.subTest(size=f"{width}x{height}"):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(200)
                self.assert_inside_window(self.item("holdingReturnButton"))
                self.open_return()
                for name in ("holdingIntactConfirm", "holdingReturnDate", "confirmHoldingReturnButton", "cancelHoldingReturnButton"):
                    self.assert_inside_window(self.item(name))
                self.click("cancelHoldingReturnButton")
                self.closed("holdingReturnDialog")

    def test_a_closed_original_custody_disables_the_return_with_its_reason(self):
        trip, lot, done = self.hand_over()
        self.trips.close(trip)
        self.open_party()
        self.assertFalse(self.item("holdingReturnButton").property("enabled"))
        self.assertIn("مغلقة", self.text("holdingReason"))
        self.assertEqual(len(self.db.holding_docs), 1)

    def test_a_refusal_after_opening_keeps_the_dialog_the_confirmation_and_the_fields(self):
        trip, lot, done = self.hand_over()
        self.open_party()
        self.open_return()
        self.click("holdingIntactConfirm")
        self.type_in("holdingReturnReference", "استلام")
        self.trips.close(trip)                                                      # the custody is closed while the dialog is open
        self.click("confirmHoldingReturnButton")
        self.wait_idle()
        self.assertTrue(self.item("holdingReturnDialog").property("opened"))
        self.assertIn("مغلقة", self.text("holdingReturnError"))
        self.assertTrue(self.item("holdingIntactConfirm").property("checked"))
        self.assertEqual(self.text("holdingReturnReference"), "استلام")
        self.assertEqual(len(self.db.holding_docs), 1)
        self.trips.reopen(trip, "x")
        self.click("confirmHoldingReturnButton")
        self.wait_until(lambda: len(self.db.holding_docs) == 2, "retry posted")
        self.closed("holdingReturnDialog")

    def type_in(self, name, text):
        self.click(name)
        self.type_text(text)
        QTest.qWait(50)

    # ------------------------------------------------------------ 049: master-karat LOGO grams
    def logo_hand_over(self, grams="8", party=PARTY):
        trip = self.trips.issue("", "", [{"karat_id": "1", "weight": "20", "designation": "LOGO"}], TODAY, "", self.key())
        done = self.holding.hand_over_logo(trip["trip_id"], party, "1", grams, TODAY, "", "", self.key())
        return trip["trip_id"], done

    def open_logo_return(self):
        self.reveal_party()
        self.click("logoReturnButton")
        self.wait_until(lambda: self.item("logoReturnDialog").property("opened"), "logo return dialog")
        QTest.qWait(100)

    def reveal_party(self):
        QTest.qWait(50)

    def test_logo_panel_partial_returns_with_real_clicks_at_1080(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        trip, done = self.logo_hand_over("8")
        self.tvm.selectTrip(trip)
        self.open_party()
        self.assertTrue(self.item("logoSource").property("visible"))
        self.assertIn("IG000001", self.text("logoRefs"))                                     # the code is under Details
        self.assertNotIn("IG000001", self.text("logoSource"))
        self.assertIn("8.000", self.text("logoFigures"))
        self.assertIn("عيار 21", self.text("logoSource"))
        self.assertIn("لوجو عيار 21: 8.000 جرام", " ".join(self.visible_texts("holdingGroupRow")))
        self.assertEqual(self.text("logoReturnButton"), "إرجاع ذهب للعهدة")
        self.assertIn("MT000001-2", self.text("logoRefs"))
        self.assert_inside_window(self.item("logoReturnButton"))
        self.open_logo_return()
        for name in ("logoReturnGrams", "logoReturnDate", "confirmLogoReturnButton", "cancelLogoReturnButton"):
            self.assert_inside_window(self.item(name))
        self.assertFalse(self.item("confirmLogoReturnButton").property("enabled"))
        self.type_in("logoReturnGrams", "9")                                                  # more than the source holds: refused by the service
        self.click("confirmLogoReturnButton")
        self.wait_idle()
        self.assertTrue(self.item("logoReturnDialog").property("opened"))
        self.assertIn("8.000", self.text("logoReturnError"))
        self.assertEqual(self.text("logoReturnGrams"), "9")
        self.assertEqual(len(self.db.logo_docs), 1)
        self.type_replace("logoReturnGrams", "3")
        reloads = self.stub.reloads
        self.click("confirmLogoReturnButton")
        self.wait_until(lambda: len(self.db.logo_docs) == 2, "partial return posted")
        self.closed("logoReturnDialog")
        self.wait_idle()
        self.assertEqual(self.holding.party_holdings(PARTY)["logo_pools"][0]["balance"], D("5.000"))
        gold = next(g for g in self.trips.trip_detail(trip)["custody"]["gold"] if g["designation"] == "LOGO")
        self.assertEqual(gold["outstanding"], D("15.000"))
        self.wait_until(lambda: self.stub.reloads > reloads, "party reloaded after the commit")
        self.wait_until(lambda: self.tvm.detail.get("gold") and self.tvm.detail["gold"][0]["outstanding"] == "15.000", "custody view reloaded after the commit")
        self.open_logo_return()
        self.type_in("logoReturnGrams", "5")
        self.click("confirmLogoReturnButton")
        self.wait_until(lambda: len(self.db.logo_docs) == 3, "rest returned")
        self.closed("logoReturnDialog")
        self.wait_idle()
        self.assertEqual(self.holding.party_holdings(PARTY)["logo_pools"], [])
        self.assertEqual(len(self.visible_texts("logoHistoryRow")), 3)
        self.assertEqual(self.warnings, [])

    def test_the_logo_and_bar_sections_are_distinct_and_a_closed_custody_disables_the_logo_return(self):
        trip, done = self.logo_hand_over("4")
        self.hand_over()
        self.trips.return_custody(trip, "", [{"karat_id": "1", "weight": "16", "designation": "LOGO"}], TODAY, "", self.key())
        self.trips.close(trip)
        self.open_party()
        self.assertTrue(self.item("holdingSource").property("visible"))
        self.assertTrue(self.item("logoSource").property("visible"))
        self.assertFalse(self.item("logoMoreRow").property("visible"))
        self.assertFalse(self.visual_item("logoReturnButton").property("visible"))
        self.assertTrue(self.item("logoMoreButton").property("visible"))
        self.assertIn("مغلقة", self.text("logoReason"))
        self.assertNotIn("MT", self.text("logoReason"))
        self.click("holdingDetailsToggle")
        self.assertFalse(self.item("holdingDetailsCard").property("visible"))
        self.assertNotIn("MT", " ".join(self.visible_texts("logoRefs")))
        self.click("holdingDetailsToggle")
        self.assertIn("MT000001", self.text("logoRefs"))
        self.assertIn("مغلقة", self.text("logoRefs"))
        self.assertFalse(self.item("logoReturnButton").property("enabled"))
        self.assertTrue(self.item("logoMoreRow").property("visible"))
        self.assertTrue(self.item("holdingReturnButton").property("enabled"))              # the bar belongs to another (open) custody

    def test_the_logo_return_dialog_is_fully_visible_at_the_three_target_sizes(self):
        self.logo_hand_over("6")
        self.open_party()
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            with self.subTest(size=f"{width}x{height}"):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(200)
                self.assert_inside_window(self.item("logoReturnButton"))
                self.open_logo_return()
                for name in ("logoReturnGrams", "logoReturnDate", "confirmLogoReturnButton", "cancelLogoReturnButton"):
                    self.assert_inside_window(self.item(name))
                self.click("cancelLogoReturnButton")
                self.closed("logoReturnDialog")

    def type_replace(self, name, text):
        self.click(name)
        QTest.keyClick(self.root, Qt.Key_A, Qt.ControlModifier)
        self.type_text(text)
        QTest.qWait(80)

    def test_the_panel_is_hidden_without_the_view_right_and_the_return_button_without_the_manage_right(self):
        self.hand_over()
        self.grant(revoke=("INTERMEDIARY_HOLDING_VIEW",))
        self.svm.changed.emit()
        QTest.qWait(60)
        self.assertFalse(self.svm.canViewHoldings)
        self.grant("INTERMEDIARY_HOLDING_VIEW", revoke=("INTERMEDIARY_HOLDING_MANAGE",))
        self.svm.changed.emit()
        QTest.qWait(60)
        self.assertFalse(self.svm.canManageHoldings)


def load_tests(loader, tests, pattern):
    """Only the tests defined here: the page classes inherit the whole harness, which other modules already run."""
    suite = unittest.TestSuite()
    for case in (HoldingCustodyPageTests, HoldingPartyPageTests):
        suite.addTests(case(name) for name in loader.getTestCaseNames(case) if name in case.__dict__)
    return suite


if __name__ == "__main__":
    unittest.main()
