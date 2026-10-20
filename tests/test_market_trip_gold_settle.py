"""Supplier GOLD settlement from market custody (046, «تسوية ذهب من العهدة»): LOGO gold already in an OPEN custody is delivered to a supplier
against its gold account. Real MarketTripService, the real SupplierGoldSettlementService and the real CorrectionService over the in-memory
transactional database of tests/market_trip_fakes.py (a failed transaction restores every table; the 046 composite-foreign-key / CHECK / unique
constraints are mirrored as asserts). NOT real-MySQL tests: the SQL, the constraints and the rollback were exercised on the masgold_rehearsal
sandbox, single-threaded. Concurrency is not exercised (the trip and supplier row locks are inferred from the code, not proven here)."""
import copy
from decimal import Decimal as D
import unittest
from unittest.mock import patch

from mysql.connector import IntegrityError

from services import custody_rules
from services.correction_service import CorrectionError, CorrectionService
from services.market_trip_service import MarketTripService, TripError
from services.supplier_gold_settlement_service import SupplierGoldSettlementService
from services.supplier_rules import SupplierRuleError
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import (AccountsRepo, Corrections, Customers, Invoices, MANAGER, OWNER, ScrapRepo, SettlementRepo, SupplierPayRepo,
                                     TripRepo, session_for)
from tests.test_market_trips import K18, K21, K24, TODAY, TripCase, key

S21, S18_INACTIVE, INTERMEDIARY, NO_KARAT, S24 = "10", "11", "12", "13", "14"


def lkey(n):
    return "44444444-4444-4444-8444-%012d" % n


class GoldSettleCase(TripCase):
    """A custody of 20 g 21K LOGO + 5 g 18K LOGO + 3 g 21K MELT (and 10,000 EGP), the shop Scrap behind it, and suppliers of every karat."""

    def setUp(self):
        super().setUp()
        self.db.suppliers[13] = {"id": 13, "name": "بلا عيار", "is_active": 1, "is_supplier": True, "is_intermediary": False, "karat_id": None}
        self.db.suppliers[14] = {"id": 14, "name": "مورد 24", "is_active": 1, "is_supplier": True, "is_intermediary": True, "karat_id": 3}
        started = patch("repositories.supplier_gold_settlement_repository.SupplierGoldSettlementRepository", SettlementRepo)
        started.start()
        self.addCleanup(started.stop)
        self.trip = self.issue("10000", [(K21, "20.000", "LOGO"), (K18, "5.000", "LOGO"), (K21, "3.000", "MELT")])["trip_id"]
        self.direct = SupplierGoldSettlementService(self.session, transaction_factory=self.db.transaction, repository=SettlementRepo, scrap=ScrapRepo,
                                                    authorization=self.authorization, corrections=self.corrections)
        self.fakes = SettlementRepo(self.db)

    # ------------------------------------------------------------ helpers
    def settle(self, supplier=S18_INACTIVE, karat=K21, weight="10", designation="LOGO", trip=None, request=None, service=None, reference="",
               notes="", when=TODAY):
        self.n += 1
        return (service or self.service).settle_supplier_gold(self.trip if trip is None else trip, supplier, karat, designation, weight, when,
                                                              reference, notes, request or lkey(self.n))

    def as_user(self, permissions, user_id=9):
        session = session_for(permissions, user_id)
        authorization = FakeAuthorization(session)
        corrections = CorrectionService(session, authorization=authorization, transaction_factory=self.db.transaction, invoice_repository=Invoices,
                                        correction_repository=Corrections, customer_repository=Customers)
        return MarketTripService(session, transaction_factory=self.db.transaction, repository=TripRepo, accounts=AccountsRepo, scrap=ScrapRepo,
                                 authorization=authorization, corrections=corrections, supplier_payments=SupplierPayRepo,
                                 gold_settlements=SettlementRepo)

    def row(self, karat=K21, designation="LOGO", trip=None):
        rows = {(str(g["karat_id"]), g["designation"]): g for g in self.service.trip_detail(trip or self.trip)["custody"]["gold"]}
        return rows[(str(karat), designation)]

    def logo(self, karat=K21, trip=None):
        return self.row(karat, "LOGO", trip)["outstanding"]

    def payable(self, supplier, karat):
        return self.fakes.balance(supplier, karat)

    def denied(self, call):
        before = self.state()
        with self.assertRaises(PermissionError):
            call()
        self.assertEqual(self.state(), before, "a denied request left a trace")

    def reverse(self, done, reason="تصحيح", service=None):
        return (service or self.service).reverse(done["entry_id"], reason)


class PostingTests(GoldSettleCase):
    def test_the_example_delivers_custody_gold_credits_the_suppliers_karat_and_posts_no_scrap(self):
        scrap_rows, scrap_physical, holds, cash = len(self.db.scrap), self.db.physical(1), dict(self.db.holds), self.db.cash()
        done = self.settle(S18_INACTIVE, K21, "10", reference="ت-1", notes="ملاحظة")
        self.assertEqual((done["kind"], done["code"], done["settlement_code"], done["duplicate"]), ("GOLD_SETTLE", "MT000001-2", "SG000001", False))
        self.assertEqual((done["delivered_karat"], done["target_karat"], done["designation"], done["delivered_weight"], done["credited_weight"],
                          done["analytical"], done["rounding_difference"]), (21, 18, "LOGO", "10.000", "11.667", "11.666667", "0.000333"))
        self.assertEqual(self.logo(), D("10.000"))                                                # custody gold consumed: 20 - 10
        self.assertEqual(self.payable(11, 2), D("-11.667"))                                        # the supplier's OWN karat, never converted back
        # no Scrap movement, no cash movement, no supplier making / lot / hold change: the grams left the shop once, at issue
        self.assertEqual((len(self.db.scrap), self.db.physical(1), self.db.holds, self.db.cash()), (scrap_rows, scrap_physical, holds, cash))
        self.assertEqual(self.db.sm_making, [])
        self.assertEqual(self.db.lot_moves, [])
        self.assertEqual([m["type_code"] for m in self.db.scrap], ["OPENING", "OPENING", "TRIP_CUSTODY_OUT", "TRIP_CUSTODY_OUT", "TRIP_CUSTODY_OUT"])

    def test_the_document_the_legs_and_the_link_keep_every_figure_and_the_source(self):
        done = self.settle(S18_INACTIVE, K21, "10", reference="ت-1", notes="ملاحظة")
        header = self.db.settlements[int(done["settlement_id"])]
        self.assertEqual((header["source_kind"], header["delivered_karat_value"], header["target_karat_value"], header["delivered_weight_grams"],
                          header["credited_weight_grams"], header["credited_analytical_grams"], header["rounding_difference_grams"],
                          header["reference"], header["notes"]),
                         ("CUSTODY_GOLD", 21, 18, D("10.000"), D("11.667"), D("11.666667"), D("0.000333"), "ت-1", "ملاحظة"))
        self.assertEqual(header["rounding_difference_grams"], header["credited_weight_grams"] - header["credited_analytical_grams"])
        self.assertEqual([(l["leg_kind"], l["source_kind"]) for l in self.db.settlement_legs], [("GOLD", "CUSTODY_GOLD")])    # no SCRAP leg exists
        link = self.db.gold_settle_links[int(done["entry_id"])]
        self.assertEqual((link["settlement_id"], link["supplier_id"], link["karat_id"], link["designation"], link["weight_grams"]),
                         (header["id"], 11, 1, "LOGO", D("10.000")))
        movement = self.db.sup_gold[0]
        self.assertEqual((movement["supplier_id"], movement["karat_id"], movement["weight_delta"], movement["source_type"]),
                         (11, 2, D("-11.667"), "SUPPLIER_GOLD_SETTLEMENT"))
        self.assertLess(movement["eq21"], 0)                                                         # the internal compatibility column keeps the sign
        self.assertEqual({a["event_type"] for a in self.db.audit_rows[-2:]}, {"SUPPLIER_GOLD_SETTLEMENT", "MARKET_TRIP_GOLD_SETTLE"})

    def test_the_conversion_and_the_rounding_boundaries_come_from_the_one_shared_rule(self):
        same = self.settle(S21, K21, "4.000")                                                        # 21 -> 21: 1 : 1
        self.assertEqual((same["credited_weight"], same["analytical"], same["rounding_difference"]), ("4.000", "4.000000", "0.000000"))
        up = self.settle(S24, K21, "0.012")                                                          # 0.012 x 21 / 24 = 0.0105 exactly -> HALF_UP
        self.assertEqual((up["credited_weight"], up["analytical"], up["rounding_difference"]), ("0.011", "0.010500", "0.000500"))
        below = self.settle(S18_INACTIVE, K21, "0.001")                                              # 0.0011666.. -> 0.001 (a negative difference)
        self.assertEqual((below["credited_weight"], below["analytical"], below["rounding_difference"]), ("0.001", "0.001167", "-0.000167"))
        five = self.settle(S24, K18, "5.000")                                                        # 18 -> 24
        self.assertEqual((five["credited_weight"], five["analytical"]), ("3.750", "3.750000"))
        self.assertEqual(self.payable(14, 3), -(D("0.011") + D("3.750")))                            # the sums are the posted (rounded) figures
        self.assertEqual(self.logo(K21), D("20") - D("4") - D("0.012") - D("0.001"))

    def test_logo_settles_a_supplier_of_a_different_karat_and_a_zero_crossing_inactive_supplier_is_allowed(self):
        done = self.settle(S18_INACTIVE, K21, "1")                                                   # an INACTIVE 18K supplier, 21K gold
        self.assertEqual(done["credited_weight"], "1.167")
        self.assertEqual(self.payable(11, 2), D("-1.167"))                                           # crossed zero: the supplier owes the shop
        self.settle(S18_INACTIVE, K18, "2")                                                          # same karat: 1 : 1
        self.assertEqual(self.payable(11, 2), D("-3.167"))

    def test_delivery_is_capped_at_the_effective_outstanding_of_that_logo_line(self):
        self.refused("SETTLEMENT_EXCEEDS_CUSTODY", lambda: self.settle(weight="20.001"))
        self.settle(weight="12.500")
        self.refused("SETTLEMENT_EXCEEDS_CUSTODY", lambda: self.settle(weight="7.501"))
        self.settle(weight="7.500")                                                                  # exactly what is left
        self.assertEqual(self.logo(), D("0.000"))
        self.refused("SETTLEMENT_EXCEEDS_CUSTODY", lambda: self.settle(weight="0.001"))
        self.assertEqual(self.logo(K18), D("5.000"))                                                 # the other karat is a separate balance
        self.refused("SETTLEMENT_EXCEEDS_CUSTODY", lambda: self.settle(karat=K24, weight="1"))       # no custody gold of that karat at all

    def test_only_logo_and_unclassified_gold_is_settleable_and_melt_gold_is_refused_without_trace(self):
        self.refused("DESIGNATION_NOT_SETTLEABLE", lambda: self.settle(designation="MELT", weight="1"))
        self.refused("SETTLEMENT_EXCEEDS_CUSTODY", lambda: self.settle(designation="UNCLASSIFIED", weight="1"))     # 061: accepted as a source, but this trip holds none
        self.refused("DESIGNATION_REQUIRED", lambda: self.settle(designation="", weight="1"))
        self.refused("DESIGNATION_REQUIRED", lambda: self.settle(designation="xx", weight="1"))
        self.assertEqual(self.row(K21, "MELT")["outstanding"], D("3.000"))                           # the MELT gold is untouched
        self.assertEqual(self.db.settlements, {})

    def test_conservation_the_custody_figures_add_up_and_the_shop_scrap_is_never_touched(self):
        before = {"physical": self.db.physical(1), "available": self.db.available(1), "holds": dict(self.db.holds)}
        self.ret(self.trip, "", [(K21, "2.000", "LOGO")])
        self.heat_logo("4", "3.9")
        done = self.settle(weight="8.000")
        row = self.row(K21, "LOGO")
        self.assertEqual((row["issued"], row["returned"], row["heat_delta"], row["supplier_settled"], row["outstanding"]),
                         (D("20.000"), D("2.000"), D("-0.100"), D("8.000"), D("9.900")))
        self.assertEqual(row["outstanding"], custody_rules.gold_outstanding(row))                    # the service view IS the shared calculation
        # every gram is accounted for: what came back + what was delivered + what is still in custody - the (signed) heating difference = issued
        self.assertEqual(row["returned"] + row["supplier_settled"] + row["outstanding"] - row["heat_delta"], row["issued"])
        self.assertEqual(self.db.physical(1) - before["physical"], D("2.000"))                      # only the RETURN moved Scrap (+2 g), the settlement never
        self.assertEqual(self.db.holds, before["holds"])
        self.assertEqual(self.db.settlements[int(done["settlement_id"])]["delivered_weight_grams"], row["supplier_settled"])

    def heat_logo(self, before, after, karat=K21):
        self.n += 1
        return self.service.heat(self.trip, [{"karat_id": karat, "input_weight": before, "output_weight": after}], TODAY, "ورشة", "", "", "", lkey(9000 + self.n))


class ValidationTests(GoldSettleCase):
    def test_refusals_leave_no_trace(self):
        self.refused("WEIGHT_REQUIRED", lambda: self.settle(weight=""))
        self.refused("WEIGHT_REQUIRED", lambda: self.settle(weight="0"))
        self.refused("WEIGHT_INVALID", lambda: self.settle(weight="-1"))
        self.refused("WEIGHT_INVALID", lambda: self.settle(weight="1.0005"))
        self.refused("WEIGHT_INVALID", lambda: self.settle(weight="abc"))
        self.refused("SUPPLIER_NOT_FOUND", lambda: self.settle(supplier="999"))
        self.refused("SUPPLIER_NOT_FOUND", lambda: self.settle(supplier="x"))
        self.refused("SUPPLIER_NOT_FOUND", lambda: self.settle(supplier=""))
        self.refused("KARAT_REQUIRED", lambda: self.settle(karat=""))
        self.refused("KARAT_NOT_FOUND", lambda: self.settle(karat="999"))
        self.refused("KARAT_NOT_FOUND", lambda: self.settle(karat="4"))                              # an inactive master karat
        self.refused("TRIP_NOT_FOUND", lambda: self.settle(trip="999"))
        self.refused("TRIP_NOT_FOUND", lambda: self.settle(trip=""))
        self.refused("REQUEST_INVALID", lambda: self.settle(request="nope"))
        self.refused("DATE_INVALID", lambda: self.settle(when="2026-13-45"))
        self.refused("DATE_REQUIRED", lambda: self.settle(when=""))
        self.assertEqual((self.db.settlements, self.db.gold_settle_links, self.db.sup_gold), ({}, {}, []))

    def test_the_recipient_needs_the_supplier_role_and_a_resolved_karat(self):
        self.refused("SUPPLIER_ROLE_REQUIRED", lambda: self.settle(supplier=INTERMEDIARY))
        self.refused("SUPPLIER_KARAT_MISSING", lambda: self.settle(supplier=NO_KARAT))
        self.assertNotIn(INTERMEDIARY, [s["id"] for s in self.service.context()["suppliers"]])       # the dialog never lists a مسوقجي-only party
        listed = {s["id"]: s["karat_value"] for s in self.service.context()["suppliers"]}
        self.assertEqual((listed[S21], listed[S18_INACTIVE], listed[S24], listed[NO_KARAT]), (21, 18, 24, None))   # the dedicated karat is offered

    def test_a_closed_trip_refuses_until_it_is_reopened(self):
        self.ret(self.trip, "10000", [(K21, "20.000", "LOGO"), (K18, "5.000", "LOGO"), (K21, "3.000", "MELT")])
        self.service.close(self.trip)
        self.refused("TRIP_CLOSED", lambda: self.settle(weight="1"))
        self.service.reopen(self.trip, "مراجعة")
        self.refused("SETTLEMENT_EXCEEDS_CUSTODY", lambda: self.settle(weight="1"))                  # open again, but nothing is left


class PermissionTests(GoldSettleCase):
    def test_posting_needs_both_rights_in_the_session_and_in_the_transaction(self):
        self.denied(lambda: self.settle(service=self.as_user(MANAGER)))                              # no MARKET_TRIP_MANAGE
        self.denied(lambda: self.settle(service=self.as_user(OWNER - {"SUPPLIER_GOLD_SETTLE"})))     # no SUPPLIER_GOLD_SETTLE
        self.denied(lambda: self.settle(service=self.as_user(OWNER - {"MARKET_TRIP_MANAGE"})))
        self.authorization.permissions = OWNER - {"SUPPLIER_GOLD_SETTLE"}                            # revoked in the database after login
        self.denied(lambda: self.settle())
        self.authorization.permissions = OWNER - {"MARKET_TRIP_MANAGE"}
        self.denied(lambda: self.settle())
        self.authorization.permissions = OWNER
        self.assertEqual(self.settle()["kind"], "GOLD_SETTLE")

    def test_an_expired_session_posts_nothing(self):
        service = self.as_user(OWNER, 15)
        original = service._repository

        def expiring(connection):                                  # the session ends right after the last audit row, inside the transaction
            repository = original(connection)
            real = repository.audit

            def audit(**kw):
                real(**kw)
                service._session.logout()
            repository.audit = audit
            return repository
        service._repository = expiring
        before = self.state()
        with self.assertRaises(PermissionError):
            self.settle(service=service)
        self.assertEqual(self.state(), before)


class ReplayTests(GoldSettleCase):
    def test_same_key_same_payload_returns_the_first_result_and_posts_nothing_twice(self):
        first = self.settle(weight="3", request=lkey(1))
        before = self.state()
        again = self.settle(weight="3", request=lkey(1))
        self.assertTrue(again["duplicate"])
        self.assertEqual((again["entry_id"], again["settlement_code"], again["credited_weight"]), (first["entry_id"], first["settlement_code"], first["credited_weight"]))
        self.assertEqual(self.state(), before)

    def test_any_difference_in_the_payload_or_the_user_is_a_request_conflict_without_effects(self):
        self.settle(weight="3", request=lkey(1), reference="r")
        other_trip = self.issue("0", [(K21, "5.000", "LOGO")])["trip_id"]
        for label, call in (("weight", lambda: self.settle(weight="3.001", request=lkey(1), reference="r")),
                            ("supplier", lambda: self.settle(supplier=S21, weight="3", request=lkey(1), reference="r")),
                            ("karat", lambda: self.settle(karat=K18, weight="3", request=lkey(1), reference="r")),
                            ("reference", lambda: self.settle(weight="3", request=lkey(1), reference="x")),
                            ("trip", lambda: self.settle(weight="3", request=lkey(1), reference="r", trip=other_trip)),
                            ("user", lambda: self.settle(weight="3", request=lkey(1), reference="r", service=self.as_user(OWNER, user_id=41)))):
            with self.subTest(label):
                self.refused("REQUEST_CONFLICT", call)

    def test_a_key_is_shared_with_the_direct_settlement_in_both_directions(self):
        self.direct.settle(S18_INACTIVE, 1, "1", TODAY, "", "", lkey(500))                           # a direct SHOP_SCRAP settlement
        self.refused("REQUEST_CONFLICT", lambda: self.settle(weight="1", request=lkey(500)))          # custody reusing its key
        done = self.settle(weight="1", request=lkey(501))
        before = self.state()
        with self.assertRaises(SupplierRuleError) as context:                                         # the direct flow reusing the custody key
            self.direct.settle(S18_INACTIVE, 1, "1", TODAY, "", "", lkey(501))
        self.assertEqual(context.exception.code, "REQUEST_CONFLICT")
        self.assertEqual(self.state(), before)
        self.assertEqual(done["settlement_code"], "SG000002")

    def test_a_concurrent_twin_gets_the_original_and_only_the_request_key_violation_is_handled(self):
        first = self.settle(weight="2", request=lkey(7))
        before = self.state()
        twin = IntegrityError("1062 (23000): Duplicate entry 'x' for key 'supplier_gold_settlements.uq_sgs_request'", errno=1062)
        with patch.object(MarketTripService, "_post_gold_settle", side_effect=twin):
            again = self.settle(weight="2", request=lkey(7))
            self.assertEqual((again["duplicate"], again["entry_id"]), (True, first["entry_id"]))
            self.refused("REQUEST_CONFLICT", lambda: self.settle(weight="2.5", request=lkey(7)))       # a twin with another payload
            self.assertEqual(self.state(), before)
            other = IntegrityError("1062 (23000): Duplicate entry 'x' for key 'suppliers.uq_something_else'", errno=1062)
            with patch.object(MarketTripService, "_post_gold_settle", side_effect=other):
                with self.assertRaises(IntegrityError):
                    self.settle(weight="2", request=lkey(7))                                           # any other integrity error is a defect
            unexplained = IntegrityError("1062 (23000): Duplicate entry 'x' for key 'supplier_gold_settlements.uq_sgs_request'", errno=1062)
            with patch.object(MarketTripService, "_post_gold_settle", side_effect=unexplained):
                with self.assertRaises(IntegrityError):
                    self.settle(weight="2", request=lkey(999))                                         # no committed document explains it: not hidden


class AtomicityTests(GoldSettleCase):
    def test_a_failure_at_any_posting_step_persists_nothing(self):
        before = self.state()
        for step in ("sg_header", "entry", "sg_gold", "sg_leg", "gold_link", "sg_audit", "audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.settle(weight="5")
            self.db.fail_at = None
            self.assertEqual(self.state(), before, f"a failure at {step} left a trace")
        self.settle(weight="5")
        self.assertEqual((len(self.db.settlements), len(self.db.gold_settle_links), len(self.db.sup_gold)), (1, 1, 1))

    def test_a_failure_while_reversing_changes_nothing(self):
        done = self.settle(weight="5")
        before = self.state()
        for step in ("correction", "sg_gold", "audit"):                                              # (a settlement has no Scrap or cash step to fail)
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.reverse(done)
            self.db.fail_at = None
            self.assertEqual(self.state(), before, f"a failure at {step} left a trace")
        self.reverse(done)
        self.assertEqual(self.payable(11, 2), D("0.000"))


class DatabaseRuleMirrorTests(GoldSettleCase):
    """The 046 constraints, mirrored by the fakes exactly as the migration declares them."""

    def test_a_scrap_leg_can_never_belong_to_a_custody_document(self):
        done = self.settle(weight="1")
        with self.assertRaises(AssertionError):                                                      # chk_sgsl_scrap_only
            self.fakes.insert_leg(settlement_id=done["settlement_id"], kind="SCRAP", source_kind="CUSTODY_GOLD", old_gold_movement_id=1)
        with self.assertRaises(AssertionError):                                                      # fk_sgsl_source: the leg must state the document's source
            self.fakes.insert_leg(settlement_id=done["settlement_id"], kind="GOLD", source_kind="SHOP_SCRAP", supplier_gold_movement_id=1)

    def test_the_link_must_equal_the_document_in_source_supplier_karat_and_weight(self):
        done = self.settle(weight="1")
        trip = TripRepo(self.db)
        other_entry = self.issue("", [(K21, "1.000", "LOGO")], trip=self.trip)["entry_id"]
        for supplier, karat, weight in ((10, 1, D("1.000")), (11, 2, D("1.000")), (11, 1, D("1.001"))):
            with self.assertRaises(AssertionError):                                                  # fk_mtgs_settlement (composite)
                trip.insert_gold_settlement_link(entry_id=other_entry, settlement_id=done["settlement_id"], supplier_id=supplier, karat_id=karat,
                                                 designation="LOGO", weight=weight)
        with self.assertRaises(AssertionError):                                                      # chk_mtgs_designation: LOGO only in this slice
            trip.insert_gold_settlement_link(entry_id=other_entry, settlement_id=done["settlement_id"], supplier_id=11, karat_id=1,
                                             designation="MELT", weight=D("1.000"))
        with self.assertRaises(IntegrityError):                                                      # uq_mtgs_settlement: one link per document
            trip.insert_gold_settlement_link(entry_id=other_entry, settlement_id=done["settlement_id"], supplier_id=11, karat_id=1,
                                             designation="LOGO", weight=D("1.000"))


class ClosureTests(GoldSettleCase):
    def test_a_trip_closes_only_at_exactly_zero_and_a_settlement_counts_as_consumed(self):
        self.refused("OUTSTANDING_CUSTODY", lambda: self.service.close(self.trip))
        self.settle(weight="20.000")                                                                 # all the 21K LOGO
        self.settle(supplier=S24, karat=K18, weight="5.000")                                         # all the 18K LOGO
        self.assertFalse(self.service.trip_detail(self.trip)["custody"]["settled"])                  # cash and the MELT gold are still out
        error = self.refused("OUTSTANDING_CUSTODY", lambda: self.service.close(self.trip))
        self.assertNotIn("لوجو", str(error))                                                         # the settled LOGO is not reported as outstanding
        self.ret(self.trip, "10000", [(K21, "3.000", "MELT")])
        self.assertTrue(self.service.trip_detail(self.trip)["custody"]["settled"])
        self.assertEqual(self.service.close(self.trip)["status"], "CLOSED")

    def test_a_closed_custody_must_be_reopened_before_the_settlement_can_be_reversed(self):
        done = self.settle(weight="20.000")
        self.settle(supplier=S24, karat=K18, weight="5.000")
        self.ret(self.trip, "10000", [(K21, "3.000", "MELT")])
        self.service.close(self.trip)
        self.refused("TRIP_CLOSED", lambda: self.reverse(done), error=CorrectionError)
        self.service.reopen(self.trip, "مراجعة")
        self.reverse(done)
        self.assertEqual(self.logo(), D("20.000"))                                                   # the stored delivered grams are back in custody


class ReversalTests(GoldSettleCase):
    def test_the_owner_reverses_it_and_the_stored_quantities_return(self):
        done = self.settle(S18_INACTIVE, K21, "10")
        self.settle(S24, K21, "0.012")                                                               # a rounded one stays untouched
        self.assertEqual((self.logo(), self.payable(11, 2)), (D("9.988"), D("-11.667")))
        rv = self.reverse(done, "تسوية خاطئة")
        self.assertEqual(rv["code"][:2], "RV")
        self.assertEqual((self.logo(), self.payable(11, 2)), (D("19.988"), D("0.000")))               # custody and the supplier restored exactly
        self.assertEqual(self.payable(14, 3), D("-0.011"))                                           # the other settlement is untouched
        movement = [m for m in self.db.sup_gold if m["source_type"] == "TRANSACTION_CORRECTION"]
        self.assertEqual([(m["supplier_id"], m["karat_id"], m["weight_delta"]) for m in movement], [(11, 2, D("11.667"))])
        self.assertEqual(len(self.db.scrap), 5)                                                      # no Scrap movement either way
        self.assertEqual(len(self.db.corrections), 1)

    def test_it_never_recalculates_the_stored_credit(self):
        done = self.settle(S24, K21, "0.012")                                                        # posted 0.011, analytical 0.0105
        self.reverse(done)
        self.assertEqual(self.payable(14, 3), D("0.000"))
        self.assertEqual(self.logo(), D("20.000"))

    def test_a_reason_is_mandatory_a_second_reversal_is_refused_and_the_state_has_one_source(self):
        done = self.settle(weight="4")
        self.refused("REASON_REQUIRED", lambda: self.reverse(done, "  "), error=CorrectionError)
        rv = self.reverse(done, "غلط")
        self.refused("ALREADY_REVERSED", lambda: self.reverse(done, "مرة ثانية"), error=CorrectionError)
        entry = next(e for e in self.service.trip_detail(self.trip)["entries"] if e["id"] == done["entry_id"])
        document = self.direct.settlement_detail(done["settlement_id"])
        record = self.db.corrections[0]
        self.assertEqual(len(self.db.corrections), 1)
        self.assertEqual((entry["reversed"], entry["reversal_code"]), (True, rv["code"]))            # the custody history
        self.assertEqual((document["reversed"], document["reversal_code"]), (True, rv["code"]))      # the SG document / supplier history
        self.assertEqual((record["source_type"], record["source_id"]), ("MARKET_TRIP_ENTRY", done["entry_id"]))
        self.assertEqual((document["custody_reference"], document["source_kind"]), (done["code"], "CUSTODY_GOLD"))

    def test_reversal_needs_the_custody_the_correction_and_the_reverse_rights_in_the_transaction(self):
        done = self.settle(weight="4")
        before = self.state()
        for missing in ("MARKET_TRIP_MANAGE", "TRANSACTION_CORRECT", "SUPPLIER_GOLD_SETTLE_REVERSE"):
            with self.subTest(missing=missing):
                self.authorization.permissions = OWNER - {missing}
                with self.assertRaises((PermissionError, CorrectionError)):
                    self.reverse(done)
                self.assertEqual(self.state(), before)
        self.authorization.permissions = OWNER
        self.denied(lambda: self.reverse(done, service=self.as_user(MANAGER)))                       # a MANAGER cannot
        self.assertEqual(self.state(), before)
        self.reverse(done)

    def test_the_supplier_side_reversal_is_refused_with_guidance_naming_the_custody_entry(self):
        done = self.settle(weight="4")
        before = self.state()
        with self.assertRaises(CorrectionError) as context:
            self.direct.reverse(done["settlement_id"], "من صفحة المورد")
        self.assertEqual(context.exception.code, "CUSTODY_SETTLEMENT_REVERSE_FROM_TRIP")
        self.assertIn(done["code"], str(context.exception))
        self.assertEqual(self.state(), before)


class DependencyTests(GoldSettleCase):
    def test_a_supporting_issue_cannot_be_reversed_while_an_effective_settlement_depends_on_it(self):
        done = self.settle(weight="4")
        self.refused("DEPENDENT_SETTLEMENT_EXISTS", lambda: self.service.reverse("1", "x"), error=CorrectionError)
        self.reverse(done)                                                                           # newest first
        self.service.reverse("1", "x")                                                               # now the issue can be undone
        self.assertEqual(self.custody(self.trip)["gold"], [])                                        # nothing of it is left in custody

    def test_a_heat_cannot_be_reversed_while_a_later_settlement_of_its_karat_exists(self):
        heat = self.heat_logo("4", "3.9")
        done = self.settle(weight="5")
        self.refused("DEPENDENT_SETTLEMENT_EXISTS", lambda: self.service.reverse(heat["entry_id"], "x"), error=CorrectionError)
        self.reverse(done)
        self.service.reverse(heat["entry_id"], "x")
        self.assertEqual(self.logo(), D("20.000"))

    def test_the_heating_difference_is_part_of_the_settleable_balance(self):
        self.heat_logo("4", "3.9")                                                                   # 20 - 0.1 = 19.9
        self.refused("SETTLEMENT_EXCEEDS_CUSTODY", lambda: self.settle(weight="19.901"))
        self.settle(weight="19.900")
        self.assertEqual(self.logo(), D("0.000"))

    def test_returns_and_melts_see_the_settled_grams(self):
        self.settle(weight="15")
        self.refused("RETURN_EXCEEDS_CUSTODY", lambda: self.ret(self.trip, "", [(K21, "5.001", "LOGO")]))
        self.ret(self.trip, "", [(K21, "5.000", "LOGO")])
        self.assertEqual(self.logo(), D("0.000"))
        self.assertEqual(self.row(K21, "MELT")["outstanding"], D("3.000"))

    def heat_logo(self, before, after, karat=K21):
        self.n += 1
        return self.service.heat(self.trip, [{"karat_id": karat, "input_weight": before, "output_weight": after}], TODAY, "ورشة", "", "", "", lkey(9000 + self.n))


class UnchangedDirectFlowTests(GoldSettleCase):
    def test_the_direct_shop_scrap_settlement_still_posts_its_scrap_out_and_two_legs_and_reverses_on_its_own(self):
        scrap = self.db.physical(1)
        done = self.direct.settle(S18_INACTIVE, 1, "10", TODAY, "", "", lkey(600))
        self.assertEqual(self.db.physical(1), scrap - D("10.000"))                                   # the delivered grams left the SHOP Scrap
        self.assertEqual([(l["leg_kind"], l["source_kind"]) for l in self.db.settlement_legs], [("SCRAP", "SHOP_SCRAP"), ("GOLD", "SHOP_SCRAP")])
        self.assertEqual(self.db.settlements[int(done["id"])]["source_kind"], "SHOP_SCRAP")
        self.direct.reverse(done["id"], "تصحيح")
        self.assertEqual(self.db.physical(1), scrap)
        self.assertEqual(self.payable(11, 2), D("0.000"))
        self.assertEqual(self.logo(), D("20.000"))                                                   # custody was never involved

    def test_a_direct_and_a_custody_settlement_of_the_same_supplier_add_up_independently(self):
        self.direct.settle(S18_INACTIVE, 1, "10", TODAY, "", "", lkey(601))
        self.settle(S18_INACTIVE, K21, "10")
        self.assertEqual(self.payable(11, 2), D("-23.334"))
        self.assertEqual(self.logo(), D("10.000"))
        self.assertEqual(self.db.physical(1), D("100.000") - D("20.000") - D("3.000") - D("10.000"))   # opening - custody issue - the DIRECT delivery


class ArithmeticTests(unittest.TestCase):
    def test_custody_rules_are_the_single_definition(self):
        row = {"issued": D("20"), "returned": D("2"), "melted": D("0"), "heat_delta": D("-0.5"), "supplier_settled": D("6.25")}
        self.assertEqual(custody_rules.gold_outstanding(row), D("11.25"))
        self.assertEqual(custody_rules.gold_outstanding({"issued": D("5")}), D("5"))                 # missing terms are zero (a row from before a migration)
        self.assertEqual(custody_rules.cash_outstanding(D("100"), D("30"), D("20")), D("50"))
        self.assertEqual(custody_rules.cash_rows_outstanding((D("100"), D("30"))), D("70"))
        self.assertFalse(custody_rules.any_negative((D("1"), D("0"), D("0")), [row]))
        self.assertTrue(custody_rules.any_negative((D("1"), D("0"), D("2")), []))
        negative = {**row, "designation": "LOGO", "supplier_settled": D("30")}
        self.assertTrue(custody_rules.any_negative((D("1"), D("0")), [negative]))
        self.assertTrue(custody_rules.any_negative((D("1"), D("0")), [negative], designation="LOGO"))
        self.assertFalse(custody_rules.any_negative((D("1"), D("0")), [negative], designation="MELT"))
        self.assertFalse(custody_rules.any_negative((D("-1"), D("0")), [], designation="LOGO"))      # a designation judges the gold only


if __name__ == "__main__":
    unittest.main()
