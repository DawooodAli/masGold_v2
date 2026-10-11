"""Supplier gold settlements (045): the immutable SG document, its ledger links, the COUNTED Scrap movement and the supplier gold movement.

All writes use the caller's transaction and only ever INSERT: a settlement, its legs and every ledger row are never updated or deleted (a
mistake is corrected by a full reversal through transaction_corrections). The settlement's Scrap movement lands on the canonical
old_gold_movements ledger (the actual physical grams of the DELIVERED karat); its gold movement on the supplier gold ledger in the supplier's
DEDICATED karat. Nothing here touches cash, bank or the supplier making ledger."""
from repositories.supplier_repository import SupplierRepository, _id

SOURCE = "SUPPLIER_GOLD_SETTLEMENT"          # transaction_corrections.source_type, Scrap reference_type, supplier gold source_type
SCRAP_OUT, SCRAP_REVERSAL_IN = "SUPPLIER_GOLD_SETTLEMENT_OUT", "REVERSAL_SUPPLIER_GOLD_SETTLEMENT_IN"
GOLD_MOVEMENT, GOLD_REVERSAL = "SETTLEMENT", "SETTLEMENT_REVERSAL"
CORRECTION_REFERENCE = "TRANSACTION_CORRECTION"
SOURCE_KIND = "SHOP_SCRAP"
CUSTODY_SOURCE_KIND = "CUSTODY_GOLD"          # 046: gold already held in an OPEN market custody (no Scrap movement)


def settlement_code(settlement_id):
    """SG + 6 digits (تسوية ذهب). Distinct from S (supplier), SP (supplier invoice), SM (making payment), DP/DR, CS, FO/TR, RV/FC, RT, EX, MT."""
    return f"SG{int(settlement_id):06d}"


class SupplierGoldSettlementRepository(SupplierRepository):

    # ------------------------------------------------------------------ the shop Scrap the dialog offers (reads only)
    def shop_scrap(self):
        """What the SHOP holds per karat: physical, held for manager review, and available. Custody gold is never in it."""
        return self._fetch_all("SELECT karat_id, karat_value, physical_grams, held_grams, available_grams "
                               "FROM vw_old_gold_available ORDER BY karat_value DESC")

    def active_karats(self):
        return self._fetch_all("SELECT id, karat_value, name FROM karats WHERE is_active = TRUE ORDER BY karat_value DESC")

    # ------------------------------------------------------------------ idempotency
    def by_request(self, key):
        return self._fetch_one("SELECT * FROM supplier_gold_settlements WHERE request_key = %s FOR UPDATE", (key,))

    # ------------------------------------------------------------------ the settlement
    def insert_header(self, *, supplier_id, delivered_karat, target_karat, delivered_weight, credited, analytical, difference,
                      settlement_date, reference, notes, key, digest, actor, now, source_kind=SOURCE_KIND):
        settlement_id, _ = self._execute(
            """
            INSERT INTO supplier_gold_settlements (supplier_id, source_kind, delivered_karat_id, delivered_karat_value, target_karat_id,
                target_karat_value, delivered_weight_grams, credited_weight_grams, credited_analytical_grams, rounding_difference_grams,
                settlement_date, reference, notes, request_key, request_hash, performed_by_user_id, performed_by_employee_id, occurred_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (_id(supplier_id), source_kind, int(delivered_karat["id"]), int(delivered_karat["karat_value"]), int(target_karat["id"]),
             int(target_karat["karat_value"]), delivered_weight, credited, analytical, difference, settlement_date, reference, notes, key,
             digest, actor.user_id, actor.employee_id, now))
        return int(settlement_id)

    def post_scrap_out(self, *, settlement_id, karat_id, weight, code, notes, actor, now):
        """The COUNTED Scrap OUT of the delivered karat (actual physical grams, never converted). Returns the movement id."""
        movement_id, count = self._execute(
            """
            INSERT INTO old_gold_movements (movement_type_id, karat_id, weight_delta_grams, balance_effect, reference_type, reference_id,
                reference_code, notes, created_by_user_id, performed_by_user_id, received_by_employee_id, movement_code, occurred_at)
            SELECT t.id, %s, %s, 'COUNTED', %s, %s, %s, %s, %s, %s, %s, %s, %s FROM old_gold_movement_types t
            WHERE t.code = %s AND t.is_active = TRUE
            """, (int(karat_id), -weight, SOURCE, _id(settlement_id), code, notes, actor.user_id, actor.user_id, actor.employee_id,
                  SCRAP_OUT, now, SCRAP_OUT))
        if count != 1:
            raise RuntimeError(f"old_gold_movement_types {SCRAP_OUT} is missing (migration 045)")
        return int(movement_id)

    def gold_type_id(self, code):
        row = self._fetch_one("SELECT id FROM supplier_gold_movement_types WHERE code = %s AND is_active = TRUE", (code,))
        return None if row is None else int(row["id"])

    def insert_gold_movement(self, *, supplier_id, type_id, karat_id, weight_delta, eq21_delta, source_type, source_id, reference,
                             description, now, user_id):
        """One supplier gold movement (physical grams of the supplier karat; the Eq21 column is the internal compatibility value only)."""
        new_id, _ = self._execute(
            """
            INSERT INTO supplier_gold_movements (supplier_id, movement_type_id, karat_id, weight_delta_grams, equivalent_21_delta_grams,
                source_type, source_id, source_reference, description, occurred_at, created_by_user_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (_id(supplier_id), int(type_id), int(karat_id), weight_delta, eq21_delta, source_type, _id(source_id), reference,
                  description, now, user_id))
        return int(new_id)

    def insert_leg(self, *, settlement_id, kind, source_kind, old_gold_movement_id=None, supplier_gold_movement_id=None):
        """One leg. The database keeps the leg's source kind equal to the document's and refuses a SCRAP leg on any source but SHOP_SCRAP,
        so a custody settlement can never carry a second Scrap deduction."""
        self._execute(
            "INSERT INTO supplier_gold_settlement_legs (settlement_id, source_kind, leg_kind, old_gold_movement_id, supplier_gold_movement_id) "
            "VALUES (%s, %s, %s, %s, %s)", (_id(settlement_id), source_kind, kind, old_gold_movement_id, supplier_gold_movement_id))

    # ------------------------------------------------------------------ reads
    _SELECT = """
        SELECT g.*, s.name AS supplier_name, s.is_active AS supplier_active, u.display_name AS actor_name,
               COALESCE(c.id, cc.id) AS reversal_id, COALESCE(c.reason, cc.reason) AS reversal_reason,
               COALESCE(c.occurred_at, cc.occurred_at) AS reversal_at, cu.display_name AS reversal_actor_name,
               lk.entry_id AS custody_entry_id, lk.designation AS custody_designation,
               le.trip_id AS custody_trip_id, le.entry_no AS custody_entry_no
        FROM supplier_gold_settlements g
        JOIN suppliers s ON s.id = g.supplier_id
        LEFT JOIN users u ON u.id = g.performed_by_user_id
        LEFT JOIN market_trip_gold_settlements lk ON lk.settlement_id = g.id
        LEFT JOIN market_trip_entries le ON le.id = lk.entry_id
        LEFT JOIN transaction_corrections c ON c.source_type = '""" + SOURCE + """' AND c.source_id = g.id
             AND c.correction_type = 'FULL_REVERSAL'
        LEFT JOIN transaction_corrections cc ON cc.source_type = 'MARKET_TRIP_ENTRY' AND cc.source_id = lk.entry_id
             AND cc.correction_type = 'FULL_REVERSAL'
        LEFT JOIN users cu ON cu.id = COALESCE(c.performed_by_user_id, cc.performed_by_user_id)
    """

    def settlement(self, settlement_id):
        return self._fetch_one(self._SELECT + " WHERE g.id = %s", (_id(settlement_id),))

    def source(self, settlement_id, lock=False):
        """The settlement as a correction source. With lock=True its row is locked (the caller holds the supplier lock first)."""
        return self._fetch_one("SELECT g.*, s.name AS supplier_name FROM supplier_gold_settlements g JOIN suppliers s ON s.id = g.supplier_id "
                               "WHERE g.id = %s" + (" FOR UPDATE OF g" if lock else ""), (_id(settlement_id),))

    def settlements_of(self, supplier_id, limit=200):
        return self._fetch_all(self._SELECT + " WHERE g.supplier_id = %s ORDER BY g.id DESC LIMIT %s", (_id(supplier_id), int(limit)))

    def custody_entry(self, settlement_id):
        """The custody entry a CUSTODY_GOLD settlement was made from (None for a direct settlement): where its reversal belongs."""
        return self._fetch_one(
            "SELECT l.entry_id, l.designation, e.trip_id, e.entry_no FROM market_trip_gold_settlements l "
            "JOIN market_trip_entries e ON e.id = l.entry_id WHERE l.settlement_id = %s", (_id(settlement_id),))

    def scrap_movement(self, settlement_id):
        """The STORED Scrap movement of the settlement (its delivered karat and signed grams: what a reversal negates)."""
        return self._fetch_one(
            "SELECT m.id, m.karat_id, m.weight_delta_grams FROM supplier_gold_settlement_legs l "
            "JOIN old_gold_movements m ON m.id = l.old_gold_movement_id WHERE l.settlement_id = %s AND l.leg_kind = 'SCRAP'", (_id(settlement_id),))

    def gold_movement(self, settlement_id):
        """The STORED supplier gold movement (its supplier, karat and signed grams: what a reversal restores)."""
        return self._fetch_one(
            "SELECT m.id, m.supplier_id, m.karat_id, m.weight_delta_grams, m.equivalent_21_delta_grams FROM supplier_gold_settlement_legs l "
            "JOIN supplier_gold_movements m ON m.id = l.supplier_gold_movement_id WHERE l.settlement_id = %s AND l.leg_kind = 'GOLD'",
            (_id(settlement_id),))
