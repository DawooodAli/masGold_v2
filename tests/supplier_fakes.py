"""In-memory doubles for the supplier tests: a transactional fake database (a failed transaction restores every
table, so atomicity is testable), the repository contracts of SupplierRepository / SupplierPurchaseRepository, and
an InventoryRepository contract so the REAL InventoryService canonical path runs over it. The SQL itself is proven
by the schema tests and the rehearsal / opt-in MySQL run."""
import copy
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal as D

from mysql.connector import IntegrityError, OperationalError

from app.session import AppSession
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.inventory_service import InventoryService
from services.supplier_purchase_service import SupplierPurchaseService
from services.supplier_service import SupplierService
from tests.auth_fakes import FakeAuthorization

ALL = frozenset({"APP_LOGIN", "SUPPLIER_VIEW", "SUPPLIER_MANAGE", "SUPPLIER_INVOICE_EDIT", "SUPPLIER_INVOICE_POST",
                 "SUPPLIER_INTAKE"})


def dev_categories(db):
    """The real DEV category rules: purchase-making ranges A 50-150, B 200-250, C 250-300, D 300-350, L 350-400, T none
    (they overlap at 250 / 300 / 350 and leave gaps)."""
    def category(category_id, code, low, high):
        return {"id": category_id, "code": code, "name": code, "is_active": 1,
                "purchase_making_min_per_gram": None if low is None else D(low),
                "purchase_making_max_per_gram": None if high is None else D(high)}
    db.categories = {c["id"]: c for c in (category(1, "A", "50", "150"), category(2, "B", "200", "250"),
                                          category(3, "C", "250", "300"), category(4, "D", "300", "350"),
                                          category(5, "L", "350", "400"), category(6, "T", None, None))}


def session_for(permissions=ALL, user_id=7, employee_id=None):
    session = AppSession()
    session._establish(AuthenticationResult(user_id, employee_id, "المدير", frozenset(permissions), _VERIFIED),
                       int(session.generation))
    return session


class Db:
    """The whole 'database'. `transaction()` snapshots every table and restores it when the block raises."""

    def __init__(self):
        self.now = datetime(2026, 10, 8, 10, 0, 0)
        self.suppliers, self.invoices, self.groups = {}, {}, {}
        self.gold, self.making, self.links, self.audit_rows = [], [], [], []
        self.karats = {1: {"id": 1, "karat_value": 21, "name": "21K", "is_active": 1},
                       2: {"id": 2, "karat_value": 18, "name": "18K", "is_active": 1},
                       3: {"id": 3, "karat_value": 24, "name": "24K", "is_active": 1}}
        self.items, self.barcodes, self.movements = {}, {}, []
        # Supplier making payments (036): accounts, the money ledgers, the payment documents and the corrections.
        self.cash_accounts = {1: {"id": 1, "code": "MAIN_SAFE", "name": "الخزنة الرئيسية", "is_active": 1}}
        self.bank_accounts = {
            5: {"id": 5, "name": "بنك مصر", "account_type": "BANK", "bank_name": "بنك مصر", "account_label": None, "is_active": 1},
            6: {"id": 6, "name": "فودافون كاش", "account_type": "MOBILE_WALLET", "bank_name": None,
                "account_label": "01000000000", "is_active": 1},
            7: {"id": 7, "name": "حساب موقوف", "account_type": "BANK", "bank_name": None, "account_label": None, "is_active": 0}}
        self.payments, self.legs, self.cash_moves, self.bank_moves, self.corrections = {}, [], [], [], []
        self.marketer_documents, self.marketer_cash_moves, self.marketer_payment_links = {}, [], {}
        self.fail_at = None                       # raise at a named step of a payment / reversal (atomicity tests)
        # The category master with its purchase-making ranges. The default is ONE catch-all grade so that every rate used by
        # the general tests resolves; the category tests install a realistic (overlapping / gapped) set instead.
        self.categories = {1: {"id": 1, "code": "A", "name": "A", "is_active": 1,
                               "purchase_making_min_per_gram": D("0.00"), "purchase_making_max_per_gram": D("100000.00")}}
        self.item_types = {1: "خاتم", 2: "سلسلة"}
        self.item_type_defaults = {}              # item type id -> its default_category_id (never allowed to win over the group)
        self.sequences = {"supplier": 0, "invoice": 0, "group": 0, "gold": 0, "making": 0, "link": 0, "item": 100,
                          "barcode": 0, "payment": 0, "cash_move": 0, "bank_move": 0, "correction": 0,
                          "marketer_document": 0, "marketer_cash_move": 0}
        self.fail_after_item = False             # raise after the inventory item is written (atomicity tests)
        self.transactions = 0

    def next(self, name):
        self.sequences[name] += 1
        return self.sequences[name]

    def cursor(self, dictionary=False):
        return _AuditCursor(self)

    @contextmanager
    def transaction(self):
        self.transactions += 1
        snapshot = copy.deepcopy({k: v for k, v in self.__dict__.items() if k not in ("fail_after_item", "transactions", "fail_at")})
        connection = Connection(self)
        try:
            yield connection
        except BaseException:
            for key, value in snapshot.items():
                setattr(self, key, value)
            raise
        finally:
            connection.close()                    # like MySQL: nothing may be read through it after the transaction


class Connection:
    """What a service receives inside a transaction: the fake database while the transaction is open and, exactly like a
    closed MySQL connection, an OperationalError afterwards. (A repository call placed after the `with` block, which the
    old always-open fake let through, is how SP000022's intake dialog failed to load on the real database.)"""

    def __init__(self, db):
        object.__setattr__(self, "_db", db)
        object.__setattr__(self, "_open", True)

    def close(self):
        object.__setattr__(self, "_open", False)

    def _live(self):
        if not self._open:
            raise OperationalError("MySQL Connection not available")
        return self._db

    def __getattr__(self, name):
        return getattr(self._live(), name)

    def __setattr__(self, name, value):
        setattr(self._live(), name, value)


def _dup():
    return IntegrityError("duplicate", errno=1062)


class _AuditCursor:
    """CorrectionService writes its audit row through a plain cursor."""

    def __init__(self, db):
        self.db = db

    def execute(self, sql, params=()):
        assert sql.startswith("INSERT INTO audit_log"), sql[:60]
        if self.db.fail_at == "audit":
            raise RuntimeError("forced failure at audit")
        self.db.audit_rows.append({"user_id": params[0], "event_type": params[1], "entity_type": params[2],
                                   "entity_id": params[3], "reference": params[4], "metadata": params[6]})

    def close(self):
        pass


class SupplierRepo:
    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------ master
    def list_suppliers(self, search=None, active=None, limit=500, role=None):
        rows = []
        for s in sorted(self.db.suppliers.values(), key=lambda r: (not r["is_active"], r["name"], r["id"])):
            if search and search not in s["name"] and search not in (s["phone"] or ""):
                continue
            if role == "SUPPLIER" and not s.get("is_supplier", True):
                continue
            if role == "INTERMEDIARY" and not s.get("is_intermediary", False):
                continue
            if active is not None and bool(s["is_active"]) != bool(active):
                continue
            karat_id = s.get("karat_id")
            ledger = [m for m in self.db.gold if m["supplier_id"] == s["id"]]
            rows.append({**s, "is_supplier": s.get("is_supplier", True), "is_intermediary": s.get("is_intermediary", False), "karat_id": karat_id,
                         "karat_value": None if karat_id is None else self.db.karats[karat_id]["karat_value"],
                         "gold_grams_balance": sum((m["weight_delta_grams"] for m in ledger if m["karat_id"] == karat_id), D("0")),
                         "gold_foreign_rows": sum(1 for m in ledger if karat_id is None or m["karat_id"] != karat_id),
                         "making_balance": sum((m["amount_delta"] for m in self.db.making
                                                if m["supplier_id"] == s["id"]), D("0")),
                         "posted_invoice_count": sum(1 for i in self.db.invoices.values()
                                                     if i["supplier_id"] == s["id"] and i["status"] == "POSTED")})
        return rows[:limit]

    def get_supplier(self, supplier_id, lock=False):
        row = self.db.suppliers.get(int(supplier_id))
        if row is None:
            return None
        karat_id = row.get("karat_id")
        return {**row, "karat_id": karat_id,
                "karat_value": None if karat_id is None else self.db.karats[karat_id]["karat_value"]}

    def create_supplier(self, *, name, phone, notes, karat_id, user_id, is_supplier=True, is_intermediary=False):
        assert is_supplier or is_intermediary                                                  # chk_suppliers_role
        assert is_supplier or karat_id is None                                                 # chk_suppliers_intermediary_karat
        if any(s["name"] == name for s in self.db.suppliers.values()):
            raise _dup()
        new_id = self.db.next("supplier")
        self.db.suppliers[new_id] = {"id": new_id, "name": name, "karat_id": None if karat_id is None else int(karat_id), "phone": phone,
                                     "notes": notes, "is_active": 1, "created_by_user_id": user_id,
                                     "is_supplier": bool(is_supplier), "is_intermediary": bool(is_intermediary)}
        return new_id

    def set_roles(self, *, supplier_id, is_supplier, is_intermediary, user_id):
        assert is_supplier or is_intermediary                                                  # chk_suppliers_role
        row = self.db.suppliers[int(supplier_id)]
        assert is_supplier or row.get("karat_id") is None                                      # chk_suppliers_intermediary_karat
        row.update(is_supplier=bool(is_supplier), is_intermediary=bool(is_intermediary))

    def clear_karat(self, *, supplier_id, user_id):
        self.db.suppliers[int(supplier_id)]["karat_id"] = None

    def role_history_count(self, supplier_id):
        sid = int(supplier_id)
        return (sum(1 for i in self.db.invoices.values() if i["supplier_id"] == sid) + sum(1 for m in self.db.gold if m["supplier_id"] == sid)
                + sum(1 for m in self.db.making if m["supplier_id"] == sid)
                + sum(1 for p in getattr(self.db, "payments", {}).values() if p["supplier_id"] == sid))

    def set_karat(self, *, supplier_id, karat_id, user_id):
        assert self.db.suppliers[int(supplier_id)].get("is_supplier", True)                    # chk_suppliers_intermediary_karat
        self.db.suppliers[int(supplier_id)]["karat_id"] = int(karat_id)

    def history_karats(self, supplier_id, lock=False):
        """The distinct karats of ALL invoices (any status) and gold movements, as the real repository."""
        sid = int(supplier_id)
        ids = {i["karat_id"] for i in self.db.invoices.values() if i["supplier_id"] == sid}
        ids |= {m["karat_id"] for m in self.db.gold if m["supplier_id"] == sid}
        return sorted(({"karat_id": k, "karat_value": self.db.karats[k]["karat_value"]} for k in ids),
                      key=lambda h: h["karat_value"])

    def update_supplier(self, *, supplier_id, name, phone, notes, user_id):
        if any(s["name"] == name and s["id"] != int(supplier_id) for s in self.db.suppliers.values()):
            raise _dup()
        self.db.suppliers[int(supplier_id)].update(name=name, phone=phone, notes=notes)

    def set_active(self, *, supplier_id, active, user_id):
        self.db.suppliers[int(supplier_id)]["is_active"] = 1 if active else 0

    def get_karat(self, karat_id):
        return self.db.karats.get(int(karat_id))

    def reference_count(self, supplier_id):
        sid = int(supplier_id)
        return (sum(1 for i in self.db.invoices.values() if i["supplier_id"] == sid)
                + sum(1 for m in self.db.gold if m["supplier_id"] == sid)
                + sum(1 for m in self.db.making if m["supplier_id"] == sid)
                + getattr(self.db, "holding_history", {}).get(sid, 0))                         # 048: a holding pool / document

    def delete_supplier(self, supplier_id):
        del self.db.suppliers[int(supplier_id)]

    # ------------------------------------------------------------ balances / ledgers
    def gold_balances(self, supplier_id):
        out = {}
        for m in self.db.gold:
            if m["supplier_id"] == int(supplier_id):
                row = out.setdefault(m["karat_id"], {"karat_id": m["karat_id"],
                                                     "karat_value": self.db.karats[m["karat_id"]]["karat_value"],
                                                     "weight_balance": D("0")})
                row["weight_balance"] += m["weight_delta_grams"]
        return sorted(out.values(), key=lambda r: r["karat_value"])

    def making_balance(self, supplier_id):
        return sum((m["amount_delta"] for m in self.db.making if m["supplier_id"] == int(supplier_id)), D("0"))

    def gold_ledger(self, supplier_id, limit=200):
        rows, running = [], {}
        for m in sorted((m for m in self.db.gold if m["supplier_id"] == int(supplier_id)),
                        key=lambda r: (r["occurred_at"], r["id"])):
            running[m["karat_id"]] = running.get(m["karat_id"], D("0")) + m["weight_delta_grams"]   # per karat, as the SQL window
            code, name = GOLD_TYPES[m["movement_type_id"]]
            rows.append({**m, "movement_code": code, "movement_name": name,
                         "karat_value": self.db.karats[m["karat_id"]]["karat_value"], "actor_name": "المدير",
                         "weight_balance_after": running[m["karat_id"]], **self._invoice_reversal_keys(code, m)})
        return rows[::-1][:limit]

    def making_ledger(self, supplier_id, limit=200):
        rows, running = [], D("0")
        for m in sorted((m for m in self.db.making if m["supplier_id"] == int(supplier_id)),
                        key=lambda r: (r["occurred_at"], r["id"])):
            running += m["amount_delta"]
            code, name = MAKING_TYPES[m["movement_type_id"]]
            payment = (self.db.payments.get(m["source_id"])
                       if code == "PAYMENT" and m["source_type"] == "SUPPLIER_MAKING_PAYMENT" else None)
            reversal = next((c for c in self.db.corrections if payment and c["source_type"] == "SUPPLIER_MAKING_PAYMENT"
                             and c["source_id"] == str(payment["id"]) and c["correction_type"] == "FULL_REVERSAL"), None)
            undone = next((c for c in self.db.corrections if code == "PAYMENT_REVERSAL"
                           and m["source_type"] == "TRANSACTION_CORRECTION" and c["correction_id"] == str(m["source_id"])), None)
            cash = self.db.cash_accounts.get(payment["cash_account_id"]) if payment and payment["cash_account_id"] else None
            bank = self.db.bank_accounts.get(payment["bank_account_id"]) if payment and payment["bank_account_id"] else None
            rows.append({**m, "movement_code": code, "movement_name": name, "actor_name": "المدير", "balance_after": running,
                         "payment_id": payment and payment["id"], "payment_method": payment and payment["payment_method"],
                         "cash_account_name": cash and cash["name"], "bank_account_name": bank and bank["name"],
                         "reversal_id": reversal and int(reversal["correction_id"]),
                         "reversed_payment_id": undone and int(undone["source_id"]),
                         "reversed_reference": undone and undone["source_reference"], **self._invoice_reversal_keys(code, m)})
        return rows[::-1][:limit]

    def _invoice_reversal_keys(self, code, movement):
        """The 052 ledger keys: the RV that reversed a purchase-invoice row / the invoice a reversal row undoes (as the SQL joins)."""
        reversal = next((c for c in self.db.corrections if code == "PURCHASE_INVOICE" and c["source_type"] == "SUPPLIER_PURCHASE_INVOICE"
                         and c["source_id"] == str(movement["source_id"]) and c["correction_type"] == "FULL_REVERSAL"), None)
        undone = next((c for c in self.db.corrections if code == "PURCHASE_INVOICE_REVERSAL" and movement["source_type"] == "TRANSACTION_CORRECTION"
                       and c["correction_id"] == str(movement["source_id"]) and c["source_type"] == "SUPPLIER_PURCHASE_INVOICE"), None)
        return {"invoice_reversal_id": reversal and int(reversal["correction_id"]),
                "reversed_invoice_id": undone and int(undone["source_id"])}

    def audit(self, *, actor, event_type, entity_id, reference, description, metadata, now, entity_type="SUPPLIER"):
        self.db.audit_rows.append({"user_id": actor.user_id, "event_type": event_type, "entity_type": entity_type,
                                   "entity_id": int(entity_id), "reference": reference, "metadata": metadata})

    def db_now(self):
        return self.db.now


class PurchaseRepo(SupplierRepo):
    def _joined(self, invoice):
        supplier, karat = self.db.suppliers[invoice["supplier_id"]], self.db.karats[invoice["karat_id"]]
        return {**invoice, "supplier_name": supplier["name"], "supplier_active": supplier["is_active"],
                "karat_value": karat["karat_value"], "karat_name": karat["name"]}

    def by_draft_key(self, key):
        found = next((i for i in self.db.invoices.values() if i["draft_request_key"] == key), None)
        return self._joined(found) if found else None

    def get_invoice(self, invoice_id, lock=False):
        found = self.db.invoices.get(int(invoice_id))
        return self._joined(found) if found else None

    def insert_invoice(self, *, supplier_id, karat_id, supplier_reference, invoice_date, weight, gross_making, notes,
                       key, user_id):
        if any(i["draft_request_key"] == key for i in self.db.invoices.values()):
            raise _dup()
        new_id = self.db.next("invoice")
        self.db.invoices[new_id] = {
            "id": new_id, "supplier_id": int(supplier_id), "karat_id": int(karat_id),
            "supplier_reference": supplier_reference, "invoice_date": invoice_date, "supplier_weight_grams": weight,
            "gross_making_total": gross_making, "status": "DRAFT", "notes": notes, "draft_request_key": key,
            "post_request_key": None, "created_by_user_id": user_id, "posted_by_user_id": None, "posted_at": None,
            "cancelled_at": None}
        return new_id

    def update_draft(self, *, invoice_id, supplier_id, karat_id, supplier_reference, invoice_date, weight,
                     gross_making, notes):
        invoice = self.db.invoices[int(invoice_id)]
        if invoice["status"] != "DRAFT":
            return 0
        values = dict(supplier_id=int(supplier_id), karat_id=int(karat_id), supplier_reference=supplier_reference,
                      invoice_date=invoice_date, supplier_weight_grams=weight, gross_making_total=gross_making, notes=notes)
        if all(invoice.get(key) == value for key, value in values.items()):
            return 0                            # like MySQL: CHANGED rows are counted, an identical UPDATE reports 0
        invoice.update(values)
        return 1

    def replace_groups(self, invoice_id, groups):
        self.db.groups = {k: v for k, v in self.db.groups.items() if v["invoice_id"] != int(invoice_id)}
        for group in groups:
            new_id = self.db.next("group")
            self.db.groups[new_id] = {"id": new_id, "invoice_id": int(invoice_id), **group}

    def groups(self, invoice_id):
        return sorted((g for g in self.db.groups.values() if g["invoice_id"] == int(invoice_id)),
                      key=lambda g: g["group_no"])

    def lock_group(self, group_id):
        return self.db.groups.get(int(group_id))

    def mark_posted(self, *, invoice_id, key, user_id, now):
        invoice = self.db.invoices[int(invoice_id)]
        if invoice["status"] != "DRAFT":
            return 0
        invoice.update(status="POSTED", post_request_key=key, posted_by_user_id=user_id, posted_at=now)
        return 1

    def mark_cancelled(self, *, invoice_id, now):
        invoice = self.db.invoices[int(invoice_id)]
        if invoice["status"] != "DRAFT":
            return 0
        invoice.update(status="CANCELLED", cancelled_at=now)
        return 1

    def list_invoices(self, supplier_id=None, status=None, limit=200):
        rows = []
        for invoice in sorted(self.db.invoices.values(), key=lambda i: (i["invoice_date"], i["id"]), reverse=True):
            if supplier_id is not None and invoice["supplier_id"] != int(supplier_id):
                continue
            if status and invoice["status"] != status:
                continue
            links = [link for link in self.db.links if link["invoice_id"] == invoice["id"]]
            reversal = self.reversal(invoice["id"])
            rows.append({**self._joined(invoice), "piece_count": len(links),
                         "reversal_id": None if reversal is None else reversal["correction_id"],
                         "actual_weight": sum((self.db.items[link["inventory_item_id"]]["weight_grams"]
                                               for link in links), D("0"))})
        return rows[:limit]

    # ------------------------------------------------------------ reversal (052): the reversed state is the correction record
    def reversal(self, invoice_id, lock=False):
        found = next((c for c in self.db.corrections if c["source_type"] == "SUPPLIER_PURCHASE_INVOICE"
                      and c["source_id"] == str(int(invoice_id)) and c["correction_type"] == "FULL_REVERSAL"), None)
        return None if found is None else {"correction_id": int(found["correction_id"]), "occurred_at": found["occurred_at"],
                                           "reason": found["reason"]}

    def intaken_pieces(self, invoice_id, lock=False):
        return [dict(link) for link in self.db.links if link["invoice_id"] == int(invoice_id)]

    def posted_gold_movement(self, invoice_id):
        found = next((m for m in self.db.gold if (m["source_type"], m["source_id"], m["movement_type_id"])
                      == ("SUPPLIER_PURCHASE_INVOICE", int(invoice_id), 1)), None)
        return None if found is None else dict(found)

    def posted_making_movement(self, invoice_id):
        found = next((m for m in self.db.making if (m["source_type"], m["source_id"], m["movement_type_id"])
                      == ("SUPPLIER_PURCHASE_INVOICE", int(invoice_id), 1)), None)
        return None if found is None else dict(found)

    # ------------------------------------------------------------ ledgers (insert only)
    def gold_type_id(self, code):
        return {"PURCHASE_INVOICE": 1, "PURCHASE_INVOICE_REVERSAL": 2}.get(code)

    def making_type_id(self, code):
        return {"PURCHASE_INVOICE": 1, "PURCHASE_INVOICE_REVERSAL": 4}.get(code)

    def insert_gold_movement(self, *, supplier_id, type_id, karat_id, weight_delta, eq21_delta, source_type,
                             source_id, reference, description, now, user_id):
        if any((m["source_type"], m["source_id"], m["movement_type_id"]) == (source_type, int(source_id), type_id)
               for m in self.db.gold):
            raise _dup()
        new_id = self.db.next("gold")
        self.db.gold.append({"id": new_id, "supplier_id": int(supplier_id), "movement_type_id": type_id,
                             "karat_id": int(karat_id), "weight_delta_grams": weight_delta,
                             "equivalent_21_delta_grams": eq21_delta, "source_type": source_type,
                             "source_id": int(source_id), "source_reference": reference, "description": description,
                             "occurred_at": now, "created_by_user_id": user_id})
        return new_id

    def insert_making_movement(self, *, supplier_id, type_id, amount_delta, source_type, source_id, reference,
                               description, now, user_id):
        if any((m["source_type"], m["source_id"], m["movement_type_id"]) == (source_type, int(source_id), type_id)
               for m in self.db.making):
            raise _dup()
        new_id = self.db.next("making")
        self.db.making.append({"id": new_id, "supplier_id": int(supplier_id), "movement_type_id": type_id,
                               "amount_delta": amount_delta, "source_type": source_type, "source_id": int(source_id),
                               "source_reference": reference, "description": description, "occurred_at": now,
                               "created_by_user_id": user_id})
        return new_id

    # ------------------------------------------------------------ intake
    def group_progress(self, invoice_id):
        out = []
        for group in self.groups(invoice_id):
            links = [link for link in self.db.links if link["group_id"] == group["id"]]
            out.append({"group_id": group["id"], "piece_count": len(links),
                        "actual_weight": sum((self.db.items[link["inventory_item_id"]]["weight_grams"]
                                              for link in links), D("0"))})
        return out

    def purchase_categories(self):
        """As the real repository: ACTIVE categories having at least one purchase-making bound."""
        return [dict(c) for c in sorted(self.db.categories.values(), key=lambda c: c["id"])
                if c["is_active"] and (c["purchase_making_min_per_gram"] is not None
                                       or c["purchase_making_max_per_gram"] is not None)]

    def group_actual_weight(self, group_id):
        """Every piece ever linked to the group counts, whatever its inventory status (as the real repository)."""
        return sum((self.db.items[link["inventory_item_id"]]["weight_grams"]
                    for link in self.db.links if link["group_id"] == int(group_id)), D("0"))

    def piece_by_request(self, key):
        return next((link for link in self.db.links if link["request_key"] == key), None)

    def insert_piece(self, *, inventory_item_id, invoice_id, group_id, making_per_gram, key, user_id):
        if any(link["request_key"] == key or link["inventory_item_id"] == int(inventory_item_id)
               for link in self.db.links):
            raise _dup()
        new_id = self.db.next("link")
        self.db.links.append({"id": new_id, "inventory_item_id": int(inventory_item_id), "invoice_id": int(invoice_id),
                              "group_id": int(group_id), "making_per_gram": making_per_gram, "request_key": key,
                              "created_by_user_id": user_id})
        return new_id

    def pieces(self, invoice_id, group_id=None, limit=500):
        rows = []
        for link in reversed(self.db.links):
            if link["invoice_id"] != int(invoice_id) or (group_id is not None and link["group_id"] != int(group_id)):
                continue
            item = self.db.items[link["inventory_item_id"]]
            rows.append({**link, "group_no": self.db.groups[link["group_id"]]["group_no"], "created_at": self.db.now,
                         "weight_grams": item["weight_grams"], "size": item["size"],
                         "inventory_status": item["inventory_status"],
                         "item_type_name": self.db.item_types.get(item["item_type_id"], "?"),
                         "barcode_value": item["barcode"]})
        return rows[:limit]

    def piece_for_item(self, inventory_item_id):
        link = next((l for l in self.db.links if l["inventory_item_id"] == int(inventory_item_id)), None)
        if link is None:
            return None
        invoice = self.db.invoices[link["invoice_id"]]
        return {**link, "group_no": self.db.groups[link["group_id"]]["group_no"], "supplier_id": invoice["supplier_id"],
                "supplier_name": self.db.suppliers[invoice["supplier_id"]]["name"],
                "supplier_reference": invoice["supplier_reference"]}


class InventoryRepo:
    """The InventoryRepository contract used by InventoryService._create_item, over the shared Db."""

    def __init__(self, db):
        self.db = db

    def get_active_employee_by_id(self, employee_id):
        return {"id": employee_id}

    def get_active_item_type_by_id(self, item_type_id):
        return ({"id": item_type_id, "default_category_id": self.db.item_type_defaults.get(int(item_type_id))}
                if int(item_type_id) in self.db.item_types else None)

    def get_active_karat_by_id(self, karat_id):
        return self.db.karats.get(int(karat_id))

    def get_active_category_by_id(self, category_id):
        category = self.db.categories.get(int(category_id))
        return dict(category) if category and category["is_active"] else None

    def get_active_factory_by_id(self, factory_id):
        return {"id": factory_id}

    def get_inventory_movement_type_id(self, code):
        return 1

    def create_inventory_item(self, *, item_type_id, karat_id, category_id, factory_id, weight_grams, size,
                              stock_classification, acquisition_source, notes, entered_by_employee_id,
                              created_by_user_id=None, acquisition_making_per_gram=None):
        new_id = self.db.next("item")
        self.db.items[new_id] = {"id": new_id, "item_type_id": item_type_id, "karat_id": karat_id,
                                 "category_id": category_id, "factory_id": factory_id, "weight_grams": weight_grams,
                                 "size": size, "stock_classification": stock_classification,
                                 "inventory_status": "IN_STOCK", "acquisition_source": acquisition_source,
                                 "acquisition_making_per_gram": acquisition_making_per_gram, "notes": notes,
                                 "created_by_user_id": created_by_user_id, "employee_id": entered_by_employee_id,
                                 "barcode": None}
        if self.db.fail_after_item:
            raise RuntimeError("simulated failure after the inventory row")
        return new_id

    def assign_barcode(self, inventory_item_id, barcode_value, assigned_by_user_id=None):
        if barcode_value in self.db.barcodes:
            raise _dup()
        self.db.barcodes[barcode_value] = inventory_item_id
        self.db.items[inventory_item_id]["barcode"] = barcode_value
        return len(self.db.barcodes)

    def create_inventory_movement(self, *, inventory_item_id, movement_type_id, stock_classification, weight_grams,
                                  performed_by_employee_id, performed_by_user_id=None):
        self.db.movements.append({"inventory_item_id": inventory_item_id, "weight_grams": weight_grams,
                                  "performed_by_user_id": performed_by_user_id})
        return len(self.db.movements)


class Barcodes:
    """BarcodeRepository.allocate_barcode_value contract: NNYYMMDD from a daily sequence."""

    def __init__(self, db):
        self.db = db

    def allocate_barcode_value(self, for_date):
        assert isinstance(for_date, date)
        return f"{self.db.next('barcode'):02d}{for_date:%y%m%d}"


class HoldingHistoryRepo:
    """The slice of IntermediaryHoldingRepository the Supplier Master uses: the history count (a pool or document, reversed ones included) and the
    two display reads. db.holding_history maps party id -> number of pools + documents."""

    def __init__(self, db):
        self.db = db

    def history_count(self, party_id):
        return getattr(self.db, "holding_history", {}).get(int(party_id), 0)

    def marketer_gold_balance(self, party_id):
        """Mirrors IntermediaryHoldingRepository.marketer_gold_balance: the signed 21K ledger of the marketer account (055), 0 when there is none."""
        return sum((m["weight_delta"] for m in getattr(self.db, "mk_gold", []) if m["party_id"] == int(party_id)), D("0.000"))

    def open_pools(self, *, party_id=None, trip_id=None):
        return []

    def pool_return_events(self, pool_id, *, lock=False):
        return []

    def party_documents(self, party_id):
        return []

    def logo_open_pools(self, *, party_id=None, trip_id=None):
        return []

    def logo_party_documents(self, party_id):
        return []

    def holding_settlements(self, party_id):
        return []

    def party_receipts(self, party_id):
        return []


def build(permissions=ALL, user_id=7, employee_id=None, granted=None, session=None):
    """(db, session, authorization, SupplierService, SupplierPurchaseService) over one shared fake database."""
    db = Db()
    session = session or session_for(permissions, user_id, employee_id)
    authorization = FakeAuthorization(session, granted)
    inventory = InventoryService(repository=InventoryRepo)
    suppliers = SupplierService(session, transaction_factory=db.transaction, repository=SupplierRepo,
                                authorization=authorization, holdings=HoldingHistoryRepo)
    purchases = SupplierPurchaseService(session, transaction_factory=db.transaction, repository=PurchaseRepo,
                                        barcodes=Barcodes, inventory=inventory, authorization=authorization)
    return db, session, authorization, suppliers, purchases

# ------------------------------------------------------------------------------------------ supplier making payments (036)
# The money ledgers, the payment document and the corrections are fake tables of the same Db, so one transaction
# snapshot/rollback covers the payment, its money movement, its making movement and a reversal together (atomicity).
OWNER_RIGHTS = frozenset(ALL | {"SUPPLIER_MAKING_PAY", "SUPPLIER_MAKING_PAY_REVERSE", "TRANSACTION_CORRECT", "SUPPLIER_INVOICE_REVERSE"})
MANAGER_RIGHTS = frozenset(ALL | {"SUPPLIER_MAKING_PAY", "TRANSACTION_CORRECT"})
CASHIER_RIGHTS = frozenset({"APP_LOGIN", "SUPPLIER_VIEW"})
MAKING_TYPES = {1: ("PURCHASE_INVOICE", "Supplier Purchase Invoice Making"), 2: ("PAYMENT", "Supplier Making Payment"),
                3: ("PAYMENT_REVERSAL", "Supplier Making Payment Reversal"),
                4: ("PURCHASE_INVOICE_REVERSAL", "Supplier Purchase Invoice Making Reversal")}
GOLD_TYPES = {1: ("PURCHASE_INVOICE", "Supplier Purchase Invoice"), 2: ("PURCHASE_INVOICE_REVERSAL", "Supplier Purchase Invoice Reversal")}
MAKING_TYPE_ID = {code: type_id for type_id, (code, _) in MAKING_TYPES.items()}


class PaymentRepo(PurchaseRepo):
    """SupplierPaymentRepository contract, including the 036 database checks and unique keys."""

    def _fail(self, step):
        if self.db.fail_at == step:
            raise RuntimeError(f"forced failure at {step}")

    def main_safe(self):
        row = next((a for a in self.db.cash_accounts.values() if a["code"] == "MAIN_SAFE"), None)
        return None if row is None else dict(row)

    def active_bank_accounts(self):
        return [dict(b) for b in sorted(self.db.bank_accounts.values(), key=lambda b: (b["account_type"], b["name"], b["id"]))
                if b["is_active"]]

    def making_balance_current(self, supplier_id):
        return self.making_balance(supplier_id)

    def by_request(self, key):
        row = next((p for p in self.db.payments.values() if p["request_key"] == key), None)
        return None if row is None else dict(row)

    def insert_header(self, *, supplier_id, method, cash_account_id, bank_account_id, amount, payment_date, reference,
                      notes, before, after, key, digest, actor, now, marketer_party_id=None, self_payment_confirmed=False):
        assert amount > 0 and after == before - amount                                  # chk_smp_amount / chk_smp_balance
        assert ((method == "BANK" and bank_account_id is not None and cash_account_id is None
                 and marketer_party_id is None and not self_payment_confirmed)
                or (method == "MARKETER" and bank_account_id is None and cash_account_id is None
                    and marketer_party_id is not None))
        if any(p["request_key"] == key for p in self.db.payments.values()):            # uq_smp_request, as MySQL words it
            raise IntegrityError(f"1062 (23000): Duplicate entry '{key}' for key 'supplier_making_payments.uq_smp_request'",
                                 errno=1062)
        self._fail("header")
        payment_id = self.db.next("payment")
        self.db.payments[payment_id] = {
            "id": payment_id, "supplier_id": int(supplier_id), "payment_method": method, "cash_account_id": cash_account_id,
            "bank_account_id": bank_account_id, "marketer_party_id": marketer_party_id,
            "self_payment_confirmed": bool(self_payment_confirmed), "amount": amount, "payment_date": payment_date, "reference": reference,
            "notes": notes, "balance_before": before, "balance_after": after, "request_key": key, "request_hash": digest,
            "performed_by_user_id": actor.user_id, "performed_by_employee_id": actor.employee_id, "occurred_at": now}
        return payment_id

    def post_money(self, *, ledger, account_id, payment_id, code, amount, description, notes, actor, now):
        self._fail("money")
        table = self.db.cash_moves if ledger == "CASH" else self.db.bank_moves
        movement_id = self.db.next("cash_move" if ledger == "CASH" else "bank_move")
        table.append({"id": movement_id, "account_id": int(account_id), "amount_delta": -amount,
                      "reference_type": "SUPPLIER_MAKING_PAYMENT", "reference_id": str(payment_id),
                      "reference_code": code, "type_code": "SUPPLIER_MAKING_PAYMENT", "description": description})
        return movement_id

    def making_type_id(self, code):
        return MAKING_TYPE_ID.get(code)

    def insert_gold_movement(self, *, supplier_id, type_id, karat_id, weight_delta, eq21_delta, source_type,
                             source_id, reference, description, now, user_id):
        self._fail("gold")
        return super().insert_gold_movement(supplier_id=supplier_id, type_id=type_id, karat_id=karat_id, weight_delta=weight_delta,
                                            eq21_delta=eq21_delta, source_type=source_type, source_id=source_id, reference=reference,
                                            description=description, now=now, user_id=user_id)

    def insert_making_movement(self, *, supplier_id, type_id, amount_delta, source_type, source_id, reference,
                               description, now, user_id):
        self._fail("making")
        return super().insert_making_movement(supplier_id=supplier_id, type_id=type_id, amount_delta=amount_delta,
                                              source_type=source_type, source_id=source_id, reference=reference,
                                              description=description, now=now, user_id=user_id)

    def insert_leg(self, *, payment_id, kind, cash_movement_id=None, bank_movement_id=None, making_movement_id=None):
        self._fail("leg")
        if (kind, payment_id) in {(l["leg_kind"], l["payment_id"]) for l in self.db.legs}:
            raise _dup()                                                                # uq_smpl_kind
        shaped = (kind == "MONEY" and making_movement_id is None and (cash_movement_id is None) != (bank_movement_id is None)) or \
                 (kind == "MAKING" and making_movement_id is not None and cash_movement_id is None and bank_movement_id is None)
        assert shaped                                                                   # chk_smpl_leg
        self.db.legs.append({"payment_id": payment_id, "leg_kind": kind, "cash_movement_id": cash_movement_id,
                             "bank_movement_id": bank_movement_id, "making_movement_id": making_movement_id})

    def audit(self, **kw):
        self._fail("audit")
        super().audit(**kw)

    def _joined(self, payment):
        supplier = self.db.suppliers[payment["supplier_id"]]
        cash = self.db.cash_accounts.get(payment["cash_account_id"]) if payment["cash_account_id"] else None
        bank = self.db.bank_accounts.get(payment["bank_account_id"]) if payment["bank_account_id"] else None
        marketer = self.db.suppliers.get(payment.get("marketer_party_id"))
        reversal = next((c for c in self.db.corrections if c["source_type"] == "SUPPLIER_MAKING_PAYMENT"
                         and c["source_id"] == str(payment["id"]) and c["correction_type"] == "FULL_REVERSAL"), None)
        return {**payment, "supplier_name": supplier["name"], "supplier_active": supplier["is_active"],
                "cash_account_name": cash and cash["name"], "bank_account_name": bank and bank["name"],
                "marketer_name": marketer and marketer["name"],
                "bank_account_type": bank and bank["account_type"], "actor_name": "المدير",
                "reversal_id": reversal and int(reversal["correction_id"]),
                "reversal_reason": reversal and reversal["reason"], "reversal_at": reversal and reversal["occurred_at"],
                "reversal_actor_name": reversal and "المدير"}

    def payment(self, payment_id):
        row = self.db.payments.get(int(payment_id))
        return None if row is None else self._joined(row)

    def source(self, payment_id, lock=False):
        row = self.db.payments.get(int(payment_id))
        return None if row is None else {**row, "supplier_name": self.db.suppliers[row["supplier_id"]]["name"]}

    def payment_making_movement(self, payment_id):
        leg = next((l for l in self.db.legs if l["payment_id"] == int(payment_id) and l["leg_kind"] == "MAKING"), None)
        if leg is None:
            return None
        move = next(m for m in self.db.making if m["id"] == leg["making_movement_id"])
        return {"id": move["id"], "supplier_id": move["supplier_id"], "amount_delta": move["amount_delta"]}

    def marketer_link(self, payment_id):
        return self.db.marketer_payment_links.get(int(payment_id))

    def payments_of(self, supplier_id, limit=200):
        return [self._joined(p) for p in sorted(self.db.payments.values(), key=lambda p: -p["id"])
                if p["supplier_id"] == int(supplier_id)][:limit]


class AccountsRepo:
    """FinancialAccountRepository.account(): one account row (None when it does not exist)."""

    def __init__(self, db):
        self.db = db

    def account(self, ledger, account_id, lock=False):
        table = self.db.cash_accounts if ledger == "CASH" else self.db.bank_accounts
        row = table.get(int(account_id))
        if row is None:
            return None
        return {"id": row["id"], "name": row["name"], "account_type": "CASH" if ledger == "CASH" else row["account_type"],
                "is_active": row["is_active"]}


def build_payments(permissions=OWNER_RIGHTS, user_id=7, granted=None, session=None):
    """(db, session, authorization, SupplierService, SupplierPurchaseService, SupplierPaymentService) over one fake database."""
    from services.supplier_payment_service import SupplierPaymentService
    db, session, authorization, suppliers, purchases = build(permissions, user_id, None, granted, session)
    payments = SupplierPaymentService(session, transaction_factory=db.transaction, repository=PaymentRepo,
                                      accounts=AccountsRepo, authorization=authorization)
    return db, session, authorization, suppliers, purchases, payments
