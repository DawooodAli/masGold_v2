"""Marketer (مسوقجي) custody account (migration 055): the shop's CASH and 21K GOLD held in trust by a market intermediary.

Persistence only. Every write uses the caller's transaction and only INSERTs documents / movements (nothing here is ever updated or deleted). The two ledgers
are signed and insert-only: a balance is the SUM of EVERY movement and a reversal adds an explicit compensation (the original stays counted). The gold ledger is
ALWAYS 21K physical grams. No query here touches a supplier ledger (supplier_gold_movements / supplier_making_movements): the party is an identity reference only;
the single bridge is a supplier gold settlement (SG, source HOLDING_MKT) that ties ONE marketer gold movement to ONE supplier gold movement."""
import json

from repositories.base_repository import BaseRepository
from repositories.invoice_repository import _id

SOURCE = "MARKETER_DOCUMENT"                 # the correction source type and the reference type of the shop-side cash / Scrap rows
HOLDING_MKT = "HOLDING_MKT"        # the SG source kind (050 widened by 055)
KARAT_VALUE = 21
MAIN_SAFE = "MAIN_SAFE"

CASH_HANDOVER, CASH_RETURN, GOLD_RETURN, PURCHASE, SALE = "CASH_HANDOVER", "CASH_RETURN", "GOLD_RETURN", "PURCHASE", "SALE"
SUPPLIER_PAY = "SUPPLIER_PAY"
KINDS = (CASH_HANDOVER, CASH_RETURN, GOLD_RETURN, PURCHASE, SALE, SUPPLIER_PAY)
CODE_PREFIX = {SALE: "MS", PURCHASE: "MB", CASH_HANDOVER: "MH", CASH_RETURN: "MR", GOLD_RETURN: "MR", SUPPLIER_PAY: "MK"}

# cash ledger movement types: (original, compensation)
CASH_MOVEMENT = {CASH_HANDOVER: ("CASH_HANDOVER", "CASH_HANDOVER_REVERSAL", 1), CASH_RETURN: ("CASH_RETURN", "CASH_RETURN_REVERSAL", -1),
                 SALE: ("SALE_PROCEEDS", "SALE_PROCEEDS_REVERSAL", 1), PURCHASE: ("PURCHASE_SPEND", "PURCHASE_SPEND_REVERSAL", -1)}
# gold ledger movement types: (original, compensation, sign of the original)
GOLD_MOVEMENT = {PURCHASE: ("PURCHASE_IN", "PURCHASE_IN_REVERSAL", 1), SALE: ("SALE_OUT", "SALE_OUT_REVERSAL", -1),
                 GOLD_RETURN: ("GOLD_RETURN", "GOLD_RETURN_REVERSAL", -1)}
MOVE_SETTLEMENT, MOVE_SETTLEMENT_REVERSAL = "SETTLEMENT", "SETTLEMENT_REVERSAL"
SHOP_CASH_TYPE = {CASH_HANDOVER: "MARKETER_CASH_HANDOVER", CASH_RETURN: "MARKETER_CASH_RETURN"}
SHOP_SCRAP_TYPE = "MARKETER_GOLD_RETURN_IN"
SHOP_SCRAP_REVERSAL = "REVERSAL_MARKETER_GOLD_RETURN_OUT"


def document_code(kind, document_id):
    """MS sale, MB purchase, MH cash handover, MR return (cash or gold) + 6 digits. Free prefixes (S, SP, SM, SG, MT, DP, DR, CS, FO, TR, RV, FC, RT, EX, IH, IG,
    HR, RP, DC are taken)."""
    return f"{CODE_PREFIX[kind]}{int(document_id):06d}"


class MarketerCustodyRepository(BaseRepository):
    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.lastrowid, cursor.rowcount
        finally:
            cursor.close()

    def db_now(self):
        return self._fetch_one("SELECT NOW() AS now")["now"].replace(microsecond=0)

    # ------------------------------------------------------------------ parties and master data
    def party(self, party_id, lock=False):
        """The party row (an identity reference). `lock=True` is the account lock: every writer of the account takes it first."""
        return self._fetch_one("SELECT id, name, is_active, is_supplier, is_intermediary, karat_id FROM suppliers WHERE id = %s"
                               + (" FOR UPDATE" if lock else ""), (_id(party_id),))

    def karat21(self):
        return self._fetch_one("SELECT id, karat_value, is_active FROM karats WHERE karat_value = %s", (KARAT_VALUE,))

    def main_safe(self):
        return self._fetch_one("SELECT id, code, name, is_active FROM cash_accounts WHERE code = %s", (MAIN_SAFE,))

    def recipients(self):
        """Every SUPPLIER with a resolved account karat (the possible recipients of a settlement), active ones first."""
        return self._fetch_all(
            "SELECT s.id, s.name, s.is_active, s.karat_id, k.karat_value FROM suppliers s JOIN karats k ON k.id = s.karat_id "
            "WHERE s.is_supplier = 1 ORDER BY s.is_active DESC, s.name, s.id")

    def counterparties(self):
        """Parties that may be named as a buyer / vendor (any role), active ones first."""
        return self._fetch_all("SELECT id, name, is_active, is_supplier, is_intermediary FROM suppliers ORDER BY is_active DESC, name, id")

    def marketers(self):
        return self._fetch_all("SELECT id, name, is_active, is_supplier FROM suppliers WHERE is_intermediary = 1 ORDER BY is_active DESC, name, id")

    def cash_payment_sources(self):
        """Marketers with positive available cash; this is a display read, never the posting authority."""
        return self._fetch_all(
            """SELECT s.id, s.name, s.is_active, s.is_supplier, COALESCE(SUM(m.amount_delta), 0) AS balance
               FROM suppliers s LEFT JOIN marketer_cash_movements m ON m.party_id = s.id
               WHERE s.is_intermediary = 1
               GROUP BY s.id, s.name, s.is_active, s.is_supplier
               HAVING COALESCE(SUM(m.amount_delta), 0) > 0
               ORDER BY s.is_active DESC, s.name, s.id""")

    # ------------------------------------------------------------------ request keys
    def by_request(self, key):
        return self._fetch_one("SELECT * FROM marketer_documents WHERE request_key = %s FOR UPDATE", (key,))

    def key_used_elsewhere(self, key):
        """True when another flow already carries this request key (a CURRENT read): a document must not reuse it."""
        row = self._fetch_one(
            "SELECT (SELECT COUNT(*) FROM market_trip_entries WHERE request_key = %s FOR SHARE) "
            "+ (SELECT COUNT(*) FROM intermediary_holding_documents WHERE request_key = %s FOR SHARE) "
            "+ (SELECT COUNT(*) FROM logo_holding_documents WHERE request_key = %s FOR SHARE) "
            "+ (SELECT COUNT(*) FROM holding_receipts WHERE request_key = %s FOR SHARE) "
            "+ (SELECT COUNT(*) FROM supplier_gold_settlements WHERE request_key = %s FOR SHARE) "
            "+ (SELECT COUNT(*) FROM supplier_making_payments WHERE request_key = %s FOR SHARE) AS used", (key, key, key, key, key, key))
        return int(row["used"]) > 0

    # ------------------------------------------------------------------ documents and ledgers (insert-only)
    def insert_document(self, *, kind, party_id, cash_amount, weight, counterparty, reference_total, deviation, acknowledged, document_date, reference,
                        notes, key, digest, actor, now):
        """One immutable document. `counterparty` is (kind, party id, name, key) for a SALE / PURCHASE, else None."""
        ckind, cparty, cname, ckey = counterparty if counterparty else (None, None, None, None)
        document_id, _ = self._execute(
            """INSERT INTO marketer_documents (doc_kind, party_id, karat_value, cash_amount, weight_grams, counterparty_kind, counterparty_party_id,
               counterparty_name, counterparty_key, reference_total, deviation_percent, price_warning_acknowledged, document_date, reference, notes,
               request_key, request_hash, performed_by_user_id, performed_by_employee_id, occurred_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (kind, _id(party_id), KARAT_VALUE, cash_amount, weight, ckind, cparty, cname, ckey, reference_total, deviation, 1 if acknowledged else 0,
             document_date, reference or None, notes or None, key, digest, actor.user_id, actor.employee_id, now))
        return int(document_id)

    def insert_shop_link(self, *, document_id, doc_kind, shop_cash_movement_id=None, shop_scrap_movement_id=None):
        """Ties a document to the shop-side movement it posted (written after both exist: the ledgers stay insert-only)."""
        self._execute("INSERT INTO marketer_shop_links (document_id, doc_kind, shop_cash_movement_id, shop_scrap_movement_id) VALUES (%s, %s, %s, %s)",
                      (_id(document_id), doc_kind, shop_cash_movement_id, shop_scrap_movement_id))

    def shop_link(self, document_id):
        return self._fetch_one("SELECT * FROM marketer_shop_links WHERE document_id = %s", (_id(document_id),))

    def insert_purchase_line(self, *, document_id, line_no, weight, price):
        self._execute("INSERT INTO marketer_purchase_lines (document_id, line_no, weight_grams, line_price) VALUES (%s, %s, %s, %s)",
                      (_id(document_id), int(line_no), weight, price))

    def insert_cash_movement(self, *, party_id, document_id, doc_kind, movement_type, amount, delta, correction_id=None):
        movement_id, _ = self._execute(
            "INSERT INTO marketer_cash_movements (party_id, document_id, doc_kind, movement_type, cash_amount, amount_delta, correction_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (_id(party_id), _id(document_id), doc_kind, movement_type, amount, delta, None if correction_id is None else _id(correction_id)))
        return int(movement_id)

    def insert_supplier_payment_link(self, *, payment_id, document_id, party_id, amount):
        self._execute(
            "INSERT INTO supplier_making_marketer_payments "
            "(payment_id, marketer_document_id, doc_kind, marketer_party_id, amount) "
            "VALUES (%s, %s, 'SUPPLIER_PAY', %s, %s)",
            (_id(payment_id), _id(document_id), _id(party_id), amount))

    def supplier_payment_link(self, payment_id):
        return self._fetch_one("SELECT * FROM supplier_making_marketer_payments WHERE payment_id = %s", (_id(payment_id),))

    def insert_gold_movement(self, *, party_id, document_id=None, doc_kind=None, settlement_id=None, movement_type, weight, delta, correction_id=None,
                             trip_entry_id=None, trip_entry_kind=None):
        if trip_entry_id is not None:                                     # 058: a leg of a BOOK trip entry (BK_DLV_MKT / BK_RECEIVE); never a document or settlement
            movement_id, _ = self._execute(
                "INSERT INTO marketer_gold_movements (party_id, karat_value, trip_entry_id, trip_entry_kind, movement_type, weight_grams, weight_delta, correction_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (_id(party_id), KARAT_VALUE, _id(trip_entry_id), trip_entry_kind, movement_type, weight, delta, None if correction_id is None else _id(correction_id)))
            return int(movement_id)
        movement_id, _ = self._execute(
            "INSERT INTO marketer_gold_movements (party_id, karat_value, document_id, doc_kind, settlement_id, movement_type, weight_grams, weight_delta, "
            "correction_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (_id(party_id), KARAT_VALUE, None if document_id is None else _id(document_id), doc_kind,
             None if settlement_id is None else _id(settlement_id), movement_type, weight, delta, None if correction_id is None else _id(correction_id)))
        return int(movement_id)

    def cash_balance(self, party_id):
        """EGP held by the marketer: the SUM of every movement, compensations included (a CURRENT locking read)."""
        return self._fetch_one("SELECT COALESCE(SUM(amount_delta), 0) AS balance FROM marketer_cash_movements WHERE party_id = %s FOR SHARE",
                               (_id(party_id),))["balance"]

    def gold_balance(self, party_id):
        """21K grams held by the marketer: the SUM of every movement, compensations included (a CURRENT locking read)."""
        return self._fetch_one("SELECT COALESCE(SUM(weight_delta), 0) AS balance FROM marketer_gold_movements WHERE party_id = %s FOR SHARE",
                               (_id(party_id),))["balance"]

    def party_gold_balances(self, party_id=None):
        """Display only: the 21K grams every marketer holds (or one party), from the signed ledger - every movement, compensations included. Parties with a
        zero balance are omitted. A database still on migration 054 has no ledger: nothing is held there."""
        exists = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'marketer_gold_movements'")
        if not exists or not int(exists["n"]):
            return []
        where, params = ("", ()) if party_id is None else ("WHERE m.party_id = %s", (_id(party_id),))
        return self._fetch_all(
            f"""SELECT m.party_id, s.name AS party_name, COALESCE(SUM(m.weight_delta), 0) AS balance
                FROM marketer_gold_movements m JOIN suppliers s ON s.id = m.party_id {where}
                GROUP BY m.party_id, s.name HAVING SUM(m.weight_delta) <> 0 ORDER BY s.name, m.party_id""", params)

    def display_cash_balance(self, party_id):
        return self._fetch_one("SELECT COALESCE(SUM(amount_delta), 0) AS balance FROM marketer_cash_movements WHERE party_id = %s", (_id(party_id),))["balance"]

    def display_gold_balance(self, party_id):
        return self._fetch_one("SELECT COALESCE(SUM(weight_delta), 0) AS balance FROM marketer_gold_movements WHERE party_id = %s", (_id(party_id),))["balance"]

    # ------------------------------------------------------------------ the shop side (a handover / return of cash, a return of gold)
    def post_shop_cash(self, *, kind, account_id, document_id, code, amount, description, actor, now):
        """The real Main Safe movement: OUT for a handover, IN for a return (a negative balance is allowed). It carries the document as its reference, so
        the generic correction reads exactly what the document posted."""
        delta = -amount if kind == CASH_HANDOVER else amount
        type_code = SHOP_CASH_TYPE[kind]
        movement_id, count = self._execute(
            """INSERT INTO cash_movements (cash_account_id, movement_type_id, amount_delta, reference_type, reference_id, reference_code, description,
               occurred_at, created_by_user_id) SELECT %s, t.id, %s, %s, %s, %s, %s, %s, %s FROM cash_movement_types t WHERE t.code = %s AND t.is_active = TRUE""",
            (int(account_id), delta, SOURCE, _id(document_id), code, description, now, actor.user_id, type_code))
        if count != 1:
            raise RuntimeError(f"cash_movement_types {type_code} is missing (migration 055)")
        return int(movement_id)

    def post_shop_scrap_in(self, *, karat_id, weight, document_id, code, notes, actor, now):
        """The counted Scrap IN of a gold return: 21K physical grams, never converted."""
        movement_id, count = self._execute(
            """INSERT INTO old_gold_movements (movement_type_id, karat_id, weight_delta_grams, balance_effect, reference_type, reference_id, reference_code,
               notes, created_by_user_id, performed_by_user_id, received_by_employee_id, movement_code, occurred_at)
               SELECT t.id, %s, %s, 'COUNTED', %s, %s, %s, %s, %s, %s, %s, %s, %s FROM old_gold_movement_types t WHERE t.code = %s AND t.is_active = TRUE""",
            (int(karat_id), weight, SOURCE, _id(document_id), code, notes, actor.user_id, actor.user_id, actor.employee_id, SHOP_SCRAP_TYPE, now,
             SHOP_SCRAP_TYPE))
        if count != 1:
            raise RuntimeError(f"old_gold_movement_types {SHOP_SCRAP_TYPE} is missing (migration 055)")
        return int(movement_id)

    # ------------------------------------------------------------------ the supplier settlement (the ONE bridge)
    def insert_settlement_header(self, *, supplier_id, party_id, karat, target_karat, quantity, credited, analytical, difference, self_confirmed,
                                 settlement_date, reference, notes, key, digest, actor, now):
        """The SG header of a settlement from the marketer account: source HOLDING_MKT, 21K delivered grams, no pool or lot (the database refuses one)."""
        settlement_id, _ = self._execute(
            """INSERT INTO supplier_gold_settlements (supplier_id, source_kind, delivered_karat_id, delivered_karat_value, target_karat_id, target_karat_value,
               delivered_weight_grams, credited_weight_grams, credited_analytical_grams, rounding_difference_grams, settlement_date, reference, notes,
               request_key, request_hash, performed_by_user_id, performed_by_employee_id, occurred_at, holding_party_id, holding_weight_grams,
               self_settlement_confirmed) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (_id(supplier_id), HOLDING_MKT, int(karat["id"]), int(karat["karat_value"]), int(target_karat["id"]), int(target_karat["karat_value"]),
             quantity, credited, analytical, difference, settlement_date, reference or None, notes or None, key, digest, actor.user_id, actor.employee_id,
             now, _id(party_id), quantity, 1 if self_confirmed else 0))
        return int(settlement_id)

    # ------------------------------------------------------------------ reads
    def document(self, document_id, lock=False):
        return self._fetch_one(
            """SELECT d.*, p.name AS party_name, c.id AS reversal_id, c.reason AS reversal_reason, c.occurred_at AS reversal_at,
                      sp.name AS supplier_payment_supplier_name
               FROM marketer_documents d JOIN suppliers p ON p.id = d.party_id
               LEFT JOIN supplier_making_marketer_payments smmp ON smmp.marketer_document_id = d.id
               LEFT JOIN supplier_making_payments smp ON smp.id = smmp.payment_id
               LEFT JOIN suppliers sp ON sp.id = smp.supplier_id
               LEFT JOIN transaction_corrections c ON c.correction_type = 'FULL_REVERSAL'
                    AND ((c.source_type = %s AND c.source_id = d.id)
                         OR (c.source_type = 'SUPPLIER_MAKING_PAYMENT' AND c.source_id = smp.id))
               WHERE d.id = %s""" + (" FOR UPDATE OF d" if lock else ""), (SOURCE, _id(document_id)))

    def documents(self, party_id, limit=300):
        rows = self._fetch_all(
            """SELECT d.*, u.display_name AS actor_name, c.id AS reversal_id, c.reason AS reversal_reason, c.occurred_at AS reversal_at,
                      sp.name AS supplier_payment_supplier_name
               FROM marketer_documents d LEFT JOIN users u ON u.id = d.performed_by_user_id
               LEFT JOIN supplier_making_marketer_payments smmp ON smmp.marketer_document_id = d.id
               LEFT JOIN supplier_making_payments smp ON smp.id = smmp.payment_id
               LEFT JOIN suppliers sp ON sp.id = smp.supplier_id
               LEFT JOIN transaction_corrections c ON c.correction_type = 'FULL_REVERSAL'
                    AND ((c.source_type = %s AND c.source_id = d.id)
                         OR (c.source_type = 'SUPPLIER_MAKING_PAYMENT' AND c.source_id = smp.id))
               WHERE d.party_id = %s ORDER BY d.id DESC LIMIT %s""", (SOURCE, _id(party_id), int(limit)))
        for row in rows:
            row["lines"] = self.purchase_lines(row["id"]) if row["doc_kind"] == PURCHASE else []
        return rows

    def purchase_lines(self, document_id):
        return self._fetch_all("SELECT line_no, weight_grams, line_price FROM marketer_purchase_lines WHERE document_id = %s ORDER BY line_no",
                               (_id(document_id),))

    def settlements(self, party_id, limit=300):
        """The supplier settlements made from this marketer's account (newest first), with the recipient and the reversal, if any."""
        return self._fetch_all(
            """SELECT g.id, g.supplier_id, s.name AS supplier_name, g.delivered_weight_grams, g.credited_weight_grams, g.target_karat_value,
                      g.credited_analytical_grams, g.rounding_difference_grams, g.self_settlement_confirmed, g.settlement_date, g.reference, g.notes,
                      g.occurred_at, u.display_name AS actor_name, c.id AS reversal_id, c.reason AS reversal_reason
               FROM supplier_gold_settlements g JOIN suppliers s ON s.id = g.supplier_id LEFT JOIN users u ON u.id = g.performed_by_user_id
               LEFT JOIN transaction_corrections c ON c.source_type = 'SUPPLIER_GOLD_SETTLEMENT' AND c.source_id = g.id AND c.correction_type = 'FULL_REVERSAL'
               WHERE g.holding_party_id = %s AND g.source_kind = %s ORDER BY g.id DESC LIMIT %s""", (_id(party_id), HOLDING_MKT, int(limit)))

    def settlement_gold_movement(self, settlement_id):
        """The marketer gold movement of a settlement (its stored delivered grams)."""
        return self._fetch_one("SELECT * FROM marketer_gold_movements WHERE settlement_id = %s AND movement_type = %s", (_id(settlement_id), MOVE_SETTLEMENT))

    def trip_movements(self, party_id, limit=300):
        """DISPLAY: the 21K legs of BOOK trip entries for this marketer (058; none before it): delivered from / received into a BOOK trip, with reversals."""
        exists = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'marketer_gold_movements' "
                                 "AND COLUMN_NAME = 'trip_entry_id'")
        if not exists or not int(exists["n"]):
            return []
        return self._fetch_all(
            """SELECT m.id, m.trip_entry_id, m.trip_entry_kind, m.movement_type, m.weight_grams, m.weight_delta, m.created_at, e.trip_id, e.entry_no, e.entry_date
               FROM marketer_gold_movements m JOIN market_trip_entries e ON e.id = m.trip_entry_id
               WHERE m.party_id = %s ORDER BY m.id DESC LIMIT %s""", (_id(party_id), int(limit)))

    def history_count(self, party_id):
        """Every marketer document of the party, reversed ones included (a CURRENT read): it keeps the intermediary role and the party row."""
        return int(self._fetch_one("SELECT COUNT(*) AS n FROM marketer_documents WHERE party_id = %s FOR SHARE", (_id(party_id),))["n"])

    def audit(self, *, actor, event_type, entity_id, reference, description, metadata, now):
        self._execute(
            "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (actor.user_id, event_type, SOURCE, int(entity_id), reference, description, json.dumps(metadata, ensure_ascii=False, default=str), now))
