"""MarketerCustodyRepository over the in-memory transactional database of tests/market_trip_fakes.py (the same Db: one snapshot / rollback covers the marketer
ledgers, the shop cash / Scrap, the SG document and the supplier ledgers). The 055 CHECK / unique / composite-foreign-key constraints are mirrored as asserts."""
from decimal import Decimal as D

from tests.market_trip_fakes import Db, _dup

CASH_SHAPE = {"CASH_HANDOVER": (1, "CASH_HANDOVER"), "CASH_RETURN": (-1, "CASH_RETURN"), "SALE_PROCEEDS": (1, "SALE"), "PURCHASE_SPEND": (-1, "PURCHASE")}
GOLD_SHAPE = {"PURCHASE_IN": (1, "PURCHASE"), "SALE_OUT": (-1, "SALE"), "GOLD_RETURN": (-1, "GOLD_RETURN")}


def prepare(db):
    """Adds the marketer tables to a Db (called once in setUp, before any snapshot)."""
    db.mk_docs, db.mk_lines, db.mk_cash, db.mk_gold, db.mk_links = {}, [], [], [], {}
    db.sequences.update({"mk_doc": 0, "mk_cash": 0, "mk_gold": 0})
    db.suppliers[20] = {"id": 20, "name": "مسوقجي أول", "is_active": 1, "is_supplier": False, "is_intermediary": True, "karat_id": None}
    db.suppliers[21] = {"id": 21, "name": "مسوقجي ومورد", "is_active": 1, "is_supplier": True, "is_intermediary": True, "karat_id": 2}     # 18K supplier account
    db.suppliers[22] = {"id": 22, "name": "مسوقجي موقوف", "is_active": 0, "is_supplier": False, "is_intermediary": True, "karat_id": None}
    db.suppliers[23] = {"id": 23, "name": "مورد 24", "is_active": 1, "is_supplier": True, "is_intermediary": False, "karat_id": 3}
    return db


class MarketerRepo:
    def __init__(self, db):
        self.db = db

    def _fail(self, step):
        if self.db.fail_at == step:
            raise RuntimeError(f"forced failure at {step}")

    def db_now(self):
        return self.db.now

    # ---- parties and master data
    def party(self, party_id, lock=False):
        if lock:
            self.db.lock_log.append(("party", int(party_id)))
        row = self.db.suppliers.get(int(party_id))
        return None if row is None else {"id": row["id"], "name": row["name"], "is_active": row["is_active"], "is_supplier": row.get("is_supplier", True),
                                         "is_intermediary": row.get("is_intermediary", False), "karat_id": row.get("karat_id")}

    def karat21(self):
        row = next((k for k in self.db.karats.values() if k["karat_value"] == 21), None)
        return None if row is None else dict(row)

    def main_safe(self):
        return {"id": 1, "code": "MAIN_SAFE", "name": "الخزنة الرئيسية", "is_active": 1}

    def recipients(self):
        return [{"id": r["id"], "name": r["name"], "is_active": r["is_active"], "karat_id": r["karat_id"], "karat_value": self.db.karats[r["karat_id"]]["karat_value"]}
                for r in sorted(self.db.suppliers.values(), key=lambda r: (-r["is_active"], r["name"], r["id"])) if r.get("is_supplier", True) and r.get("karat_id")]

    def party_gold_balances(self, party_id=None):
        """Mirrors MarketerCustodyRepository.party_gold_balances: the signed 21K ledger per party (compensations included), zero balances omitted."""
        sums = {}
        for m in self.db.mk_gold:
            sums[m["party_id"]] = sums.get(m["party_id"], D("0")) + m["weight_delta"]
        return [{"party_id": p, "party_name": self.db.suppliers[p]["name"], "balance": b} for p, b in sorted(sums.items())
                if b != 0 and (party_id is None or p == int(party_id))]

    def counterparties(self):
        return [{"id": r["id"], "name": r["name"], "is_active": r["is_active"], "is_supplier": r.get("is_supplier", True),
                 "is_intermediary": r.get("is_intermediary", False)} for r in sorted(self.db.suppliers.values(), key=lambda r: (-r["is_active"], r["name"], r["id"]))]

    def marketers(self):
        return [{"id": r["id"], "name": r["name"], "is_active": r["is_active"], "is_supplier": r.get("is_supplier", True)}
                for r in sorted(self.db.suppliers.values(), key=lambda r: (-r["is_active"], r["name"], r["id"])) if r.get("is_intermediary")]

    # ---- request keys
    def by_request(self, key):
        row = next((d for d in self.db.mk_docs.values() if d["request_key"] == key), None)
        return None if row is None else dict(row)

    def key_used_elsewhere(self, key):
        return (any(e["request_key"] == key for e in self.db.entries.values()) or any(d["request_key"] == key for d in self.db.holding_docs.values())
                or any(d["request_key"] == key for d in self.db.logo_docs.values()) or any(r["request_key"] == key for r in self.db.receipts.values())
                or any(g["request_key"] == key for g in self.db.settlements.values()) or any(p["request_key"] == key for p in self.db.sm_payments.values()))

    # ---- documents and ledgers
    def insert_document(self, *, kind, party_id, cash_amount, weight, counterparty, reference_total, deviation, acknowledged, document_date, reference,
                        notes, key, digest, actor, now):
        self._fail("mk_document")
        ckind, cparty, cname, ckey = counterparty if counterparty else (None, None, None, None)
        assert int(party_id) in self.db.suppliers                                                          # fk_mkd_party
        assert (reference_total is None) == (deviation is None)                                            # chk_mkd_warning
        if kind in ("CASH_HANDOVER", "CASH_RETURN"):                                                       # chk_mkd_shape
            assert cash_amount > 0 and weight is None and counterparty is None and reference_total is None
        elif kind == "GOLD_RETURN":
            assert cash_amount is None and weight > 0 and counterparty is None and reference_total is None
        else:
            assert kind in ("PURCHASE", "SALE") and cash_amount > 0 and weight > 0
            assert (ckind == "REGISTERED_PARTY" and cparty is not None) or (ckind == "THIRD_PARTY" and cparty is None)
            assert cname and cname.strip() and ckey and ckey.strip()
            assert cparty is None or int(cparty) in self.db.suppliers                                      # fk_mkd_counterparty
        if any(d["request_key"] == key for d in self.db.mk_docs.values()):
            raise _dup("uq_mkd_request")
        document_id = self.db.next("mk_doc")
        self.db.mk_docs[document_id] = {
            "id": document_id, "doc_kind": kind, "party_id": int(party_id), "karat_value": 21, "cash_amount": cash_amount, "weight_grams": weight,
            "counterparty_kind": ckind, "counterparty_party_id": cparty, "counterparty_name": cname, "counterparty_key": ckey,
            "reference_total": reference_total, "deviation_percent": deviation, "price_warning_acknowledged": 1 if acknowledged else 0,
            "document_date": document_date, "reference": reference, "notes": notes, "request_key": key, "request_hash": digest,
            "performed_by_user_id": actor.user_id, "occurred_at": now}
        return document_id

    def insert_purchase_line(self, *, document_id, line_no, weight, price):
        self._fail("mk_line")
        assert self.db.mk_docs[int(document_id)]["doc_kind"] == "PURCHASE" and weight > 0 and price > 0     # fk_mkpl_document / chk_mkpl_shape
        self.db.mk_lines.append({"document_id": int(document_id), "line_no": int(line_no), "weight_grams": weight, "line_price": price})

    def insert_cash_movement(self, *, party_id, document_id, doc_kind, movement_type, amount, delta, correction_id=None):
        self._fail("mk_cash")
        document = self.db.mk_docs[int(document_id)]                                                      # fk_mkcm_document (id, kind, party, amount)
        assert (document["doc_kind"], document["party_id"], document["cash_amount"]) == (doc_kind, int(party_id), amount)
        base = movement_type.removesuffix("_REVERSAL")
        sign, kind = CASH_SHAPE[base]                                                                      # chk_mkcm_shape
        assert kind == doc_kind and amount > 0
        if movement_type.endswith("_REVERSAL"):
            assert delta == -sign * amount and correction_id is not None
        else:
            assert delta == sign * amount and correction_id is None
        if any((m["document_id"], m["movement_type"]) == (int(document_id), movement_type) for m in self.db.mk_cash):
            raise _dup("uq_mkcm_document_type")
        movement_id = self.db.next("mk_cash")
        self.db.mk_cash.append({"id": movement_id, "party_id": int(party_id), "document_id": int(document_id), "doc_kind": doc_kind,
                                "movement_type": movement_type, "amount": amount, "amount_delta": delta, "correction_id": correction_id})
        return movement_id

    def insert_gold_movement(self, *, party_id, document_id=None, doc_kind=None, settlement_id=None, movement_type, weight, delta, correction_id=None,
                             trip_entry_id=None, trip_entry_kind=None):
        self._fail("mk_gold")
        assert weight > 0
        base = movement_type.removesuffix("_REVERSAL")
        if trip_entry_id is not None:                                                                     # chk_mkgm_shape, trip-entry branch (058)
            assert document_id is None and settlement_id is None and doc_kind is None
            entry = self.db.entries[int(trip_entry_id)]                                                   # fk_mkgm_trip_entry
            sign, kinds = {"TRIP_BOOK_IN": (1, ("BK_DLV_MKT", "ST_BAR_MKT")), "TRIP_BOOK_OUT": (-1, ("BK_RECEIVE",))}[base]       # 062: ST_BAR_MKT joins the IN leg
            assert entry["entry_kind"] == trip_entry_kind and trip_entry_kind in kinds
            assert (delta == sign * weight and correction_id is None) if base == movement_type else (delta == -sign * weight and correction_id is not None)
            assert not any((m.get("trip_entry_id"), m["movement_type"]) == (int(trip_entry_id), movement_type) for m in self.db.mk_gold)   # uq_mkgm_trip_entry_type
            movement_id = self.db.next("mk_gold")
            self.db.mk_gold.append({"id": movement_id, "party_id": int(party_id), "document_id": None, "doc_kind": None, "settlement_id": None,
                                    "trip_entry_id": int(trip_entry_id), "trip_entry_kind": trip_entry_kind, "movement_type": movement_type,
                                    "weight_grams": weight, "weight_delta": delta, "correction_id": correction_id})
            return movement_id
        if document_id is not None:                                                                       # chk_mkgm_shape, document branch
            assert settlement_id is None and base in GOLD_SHAPE
            sign, kind = GOLD_SHAPE[base]
            document = self.db.mk_docs[int(document_id)]                                                  # fk_mkgm_document
            assert (document["doc_kind"], document["party_id"], document["weight_grams"]) == (doc_kind, int(party_id), weight) and kind == doc_kind
        else:                                                                                              # settlement branch + fk_mkgm_settlement
            assert doc_kind is None and settlement_id is not None and base == "SETTLEMENT"
            sign = -1
            header = self.db.settlements[int(settlement_id)]
            assert (header["holding_party_id"], header["holding_weight_grams"]) == (int(party_id), weight)
        if movement_type.endswith("_REVERSAL"):
            assert delta == -sign * weight and correction_id is not None
        else:
            assert delta == sign * weight and correction_id is None
        if any((m["document_id"], m["settlement_id"], m["movement_type"]) == (None if document_id is None else int(document_id),
                                                                              None if settlement_id is None else int(settlement_id), movement_type)
               for m in self.db.mk_gold):
            raise _dup("uq_mkgm_document_type")
        movement_id = self.db.next("mk_gold")
        self.db.mk_gold.append({"id": movement_id, "party_id": int(party_id), "document_id": None if document_id is None else int(document_id),
                                "doc_kind": doc_kind, "settlement_id": None if settlement_id is None else int(settlement_id), "movement_type": movement_type,
                                "weight_grams": weight, "weight_delta": delta, "correction_id": correction_id})
        return movement_id

    def cash_balance(self, party_id):
        return sum((m["amount_delta"] for m in self.db.mk_cash if m["party_id"] == int(party_id)), D("0.00"))

    def gold_balance(self, party_id):
        return sum((m["weight_delta"] for m in self.db.mk_gold if m["party_id"] == int(party_id)), D("0.000"))

    display_cash_balance, display_gold_balance = cash_balance, gold_balance

    # ---- the shop side
    def post_shop_cash(self, *, kind, account_id, document_id, code, amount, description, actor, now):
        self._fail("mk_shop_cash")
        delta = -amount if kind == "CASH_HANDOVER" else amount
        move_id = self.db.next("cash_move")
        self.db.cash_moves.append({"id": move_id, "account_id": int(account_id), "amount_delta": delta, "reference_type": "MARKETER_DOCUMENT",
                                   "reference_id": str(document_id), "type_code": "MARKETER_CASH_HANDOVER" if kind == "CASH_HANDOVER" else "MARKETER_CASH_RETURN"})
        return move_id

    def post_shop_scrap_in(self, *, karat_id, weight, document_id, code, notes, actor, now):
        self._fail("mk_shop_scrap")
        move_id = self.db.next("scrap")
        self.db.scrap.append({"id": move_id, "karat_id": int(karat_id), "weight_delta": weight, "reference_type": "MARKETER_DOCUMENT",
                              "reference_id": str(document_id), "type_code": "MARKETER_GOLD_RETURN_IN"})
        return move_id

    def insert_shop_link(self, *, document_id, doc_kind, shop_cash_movement_id=None, shop_scrap_movement_id=None):
        self._fail("mk_link")
        assert self.db.mk_docs[int(document_id)]["doc_kind"] == doc_kind                                  # fk_mksl_document
        assert (doc_kind in ("CASH_HANDOVER", "CASH_RETURN") and shop_cash_movement_id and not shop_scrap_movement_id) or \
               (doc_kind == "GOLD_RETURN" and shop_scrap_movement_id and not shop_cash_movement_id)       # chk_mksl_shape
        self.db.mk_links[int(document_id)] = {"document_id": int(document_id), "doc_kind": doc_kind, "shop_cash_movement_id": shop_cash_movement_id,
                                              "shop_scrap_movement_id": shop_scrap_movement_id}

    def shop_link(self, document_id):
        return self.db.mk_links.get(int(document_id))

    # ---- the supplier settlement (the ONE bridge)
    def insert_settlement_header(self, *, supplier_id, party_id, karat, target_karat, quantity, credited, analytical, difference, self_confirmed,
                                 settlement_date, reference, notes, key, digest, actor, now):
        self._fail("sg_header")
        assert karat["karat_value"] == 21                                                                  # chk_sgs_shape: delivered_karat_value = 21
        assert quantity > 0 and credited > 0 and analytical > 0 and difference == credited - analytical
        assert int(supplier_id) != int(party_id) or self_confirmed                                         # chk_sgs_self
        if any(g["request_key"] == key for g in self.db.settlements.values()):
            raise _dup("uq_sgs_request")
        settlement_id = self.db.next("settlement")
        self.db.settlements[settlement_id] = {
            "id": settlement_id, "supplier_id": int(supplier_id), "source_kind": "HOLDING_MKT", "delivered_karat_id": int(karat["id"]),
            "delivered_karat_value": int(karat["karat_value"]), "target_karat_id": int(target_karat["id"]), "target_karat_value": int(target_karat["karat_value"]),
            "delivered_weight_grams": quantity, "credited_weight_grams": credited, "credited_analytical_grams": analytical,
            "rounding_difference_grams": difference, "settlement_date": settlement_date, "reference": reference, "notes": notes, "request_key": key,
            "request_hash": digest, "performed_by_user_id": actor.user_id, "occurred_at": now, "delivered_lot_id": None, "delivered_lot_weight_grams": None,
            "origin_melt_entry_id": None, "delivered_fineness_permille": None, "holding_party_id": int(party_id), "lot_pool_id": None, "logo_pool_id": None,
            "physical_declaration": None, "holding_weight_grams": quantity, "self_settlement_confirmed": 1 if self_confirmed else 0}
        return settlement_id

    # ---- reads
    def _reversal(self, source_type, source_id):
        return next((c for c in self.db.corrections if (c["source_type"], c["source_id"], c["correction_type"]) == (source_type, str(source_id), "FULL_REVERSAL")), None)

    def document(self, document_id, lock=False):
        row = self.db.mk_docs.get(int(document_id))
        if row is None:
            return None
        reversal = self._reversal("MARKETER_DOCUMENT", document_id)
        return {**row, "party_name": self.db.suppliers[row["party_id"]]["name"], "reversal_id": reversal and int(reversal["correction_id"]),
                "reversal_reason": reversal and reversal["reason"], "reversal_at": reversal and reversal["occurred_at"]}

    def documents(self, party_id, limit=300):
        rows = []
        for document in sorted((d for d in self.db.mk_docs.values() if d["party_id"] == int(party_id)), key=lambda d: -d["id"]):
            row = self.document(document["id"])
            row["actor_name"] = "المالك"
            row["lines"] = self.purchase_lines(document["id"]) if document["doc_kind"] == "PURCHASE" else []
            rows.append(row)
        return rows

    def purchase_lines(self, document_id):
        return [dict(l) for l in sorted((l for l in self.db.mk_lines if l["document_id"] == int(document_id)), key=lambda l: l["line_no"])]

    def settlements(self, party_id, limit=300):
        rows = []
        for header in sorted((g for g in self.db.settlements.values() if g.get("holding_party_id") == int(party_id) and g["source_kind"] == "HOLDING_MKT"),
                             key=lambda g: -g["id"]):
            reversal = self._reversal("SUPPLIER_GOLD_SETTLEMENT", header["id"])
            rows.append({**header, "supplier_name": self.db.suppliers[header["supplier_id"]]["name"], "actor_name": "المالك",
                         "reversal_id": reversal and int(reversal["correction_id"]), "reversal_reason": reversal and reversal["reason"]})
        return rows

    def trip_movements(self, party_id, limit=300):
        """The fake of MarketerCustodyRepository.trip_movements (058 legs of BOOK trip entries, newest first)."""
        rows = []
        for m in sorted((m for m in self.db.mk_gold if m.get("trip_entry_id") and m["party_id"] == int(party_id)), key=lambda m: -m["id"]):
            entry = self.db.entries[m["trip_entry_id"]]
            rows.append({"id": m["id"], "trip_entry_id": m["trip_entry_id"], "trip_entry_kind": m["trip_entry_kind"], "movement_type": m["movement_type"],
                         "weight_grams": m["weight_grams"], "weight_delta": m["weight_delta"], "created_at": entry.get("occurred_at"), "trip_id": entry["trip_id"],
                         "entry_no": entry["entry_no"], "entry_date": entry["entry_date"]})
        return rows

    def settlement_gold_movement(self, settlement_id):
        row = next((m for m in self.db.mk_gold if m["settlement_id"] == int(settlement_id) and m["movement_type"] == "SETTLEMENT"), None)
        return None if row is None else dict(row)

    def history_count(self, party_id):
        return sum(1 for d in self.db.mk_docs.values() if d["party_id"] == int(party_id))

    def audit(self, *, actor, event_type, entity_id, reference, description, metadata, now):
        self._fail("mk_audit")
        self.db.audit_rows.append({"user_id": actor.user_id, "event_type": event_type, "entity_type": "MARKETER_DOCUMENT", "entity_id": int(entity_id),
                                   "reference": reference, "metadata": metadata})
