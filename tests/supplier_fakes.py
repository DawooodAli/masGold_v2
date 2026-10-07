"""In-memory doubles for the supplier tests: a transactional fake database (a failed transaction restores every
table, so atomicity is testable), the repository contracts of SupplierRepository / SupplierPurchaseRepository, and
an InventoryRepository contract so the REAL InventoryService canonical path runs over it. The SQL itself is proven
by the schema tests and the rehearsal / opt-in MySQL run."""
import copy
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal as D

from mysql.connector import IntegrityError

from app.session import AppSession
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.inventory_service import InventoryService
from services.supplier_purchase_service import SupplierPurchaseService
from services.supplier_service import SupplierService
from tests.auth_fakes import FakeAuthorization

ALL = frozenset({"APP_LOGIN", "SUPPLIER_VIEW", "SUPPLIER_MANAGE", "SUPPLIER_INVOICE_EDIT", "SUPPLIER_INVOICE_POST",
                 "SUPPLIER_INTAKE"})


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
        self.item_types = {1: "خاتم", 2: "سلسلة"}
        self.sequences = {"supplier": 0, "invoice": 0, "group": 0, "gold": 0, "making": 0, "link": 0, "item": 100,
                          "barcode": 0}
        self.fail_after_item = False             # raise after the inventory item is written (atomicity tests)
        self.transactions = 0

    def next(self, name):
        self.sequences[name] += 1
        return self.sequences[name]

    @contextmanager
    def transaction(self):
        self.transactions += 1
        snapshot = copy.deepcopy({k: v for k, v in self.__dict__.items() if k not in ("fail_after_item", "transactions")})
        try:
            yield self
        except BaseException:
            for key, value in snapshot.items():
                setattr(self, key, value)
            raise


def _dup():
    return IntegrityError("duplicate", errno=1062)


class SupplierRepo:
    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------ master
    def list_suppliers(self, search=None, active=None, limit=500):
        rows = []
        for s in sorted(self.db.suppliers.values(), key=lambda r: (not r["is_active"], r["name"], r["id"])):
            if search and search not in s["name"] and search not in (s["phone"] or ""):
                continue
            if active is not None and bool(s["is_active"]) != bool(active):
                continue
            rows.append({**s, "gold_eq21_balance": sum((m["equivalent_21_delta_grams"] for m in self.db.gold
                                                        if m["supplier_id"] == s["id"]), D("0")),
                         "making_balance": sum((m["amount_delta"] for m in self.db.making
                                                if m["supplier_id"] == s["id"]), D("0")),
                         "posted_invoice_count": sum(1 for i in self.db.invoices.values()
                                                     if i["supplier_id"] == s["id"] and i["status"] == "POSTED")})
        return rows[:limit]

    def get_supplier(self, supplier_id, lock=False):
        return self.db.suppliers.get(int(supplier_id))

    def create_supplier(self, *, name, phone, notes, user_id):
        if any(s["name"] == name for s in self.db.suppliers.values()):
            raise _dup()
        new_id = self.db.next("supplier")
        self.db.suppliers[new_id] = {"id": new_id, "name": name, "phone": phone, "notes": notes, "is_active": 1,
                                     "created_by_user_id": user_id}
        return new_id

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
                + sum(1 for m in self.db.making if m["supplier_id"] == sid))

    def delete_supplier(self, supplier_id):
        del self.db.suppliers[int(supplier_id)]

    # ------------------------------------------------------------ balances / ledgers
    def gold_balances(self, supplier_id):
        out = {}
        for m in self.db.gold:
            if m["supplier_id"] == int(supplier_id):
                row = out.setdefault(m["karat_id"], {"karat_id": m["karat_id"],
                                                     "karat_value": self.db.karats[m["karat_id"]]["karat_value"],
                                                     "weight_balance": D("0"), "eq21_balance": D("0")})
                row["weight_balance"] += m["weight_delta_grams"]
                row["eq21_balance"] += m["equivalent_21_delta_grams"]
        return sorted(out.values(), key=lambda r: r["karat_value"])

    def making_balance(self, supplier_id):
        return sum((m["amount_delta"] for m in self.db.making if m["supplier_id"] == int(supplier_id)), D("0"))

    def gold_ledger(self, supplier_id, limit=200):
        rows, running = [], D("0")
        for m in sorted((m for m in self.db.gold if m["supplier_id"] == int(supplier_id)),
                        key=lambda r: (r["occurred_at"], r["id"])):
            running += m["equivalent_21_delta_grams"]
            rows.append({**m, "movement_code": "PURCHASE_INVOICE", "movement_name": "Supplier Purchase Invoice",
                         "karat_value": self.db.karats[m["karat_id"]]["karat_value"], "actor_name": "المدير",
                         "eq21_balance_after": running})
        return rows[::-1][:limit]

    def making_ledger(self, supplier_id, limit=200):
        rows, running = [], D("0")
        for m in sorted((m for m in self.db.making if m["supplier_id"] == int(supplier_id)),
                        key=lambda r: (r["occurred_at"], r["id"])):
            running += m["amount_delta"]
            rows.append({**m, "movement_code": "PURCHASE_INVOICE", "movement_name": "Supplier Purchase Invoice Making",
                         "actor_name": "المدير", "balance_after": running})
        return rows[::-1][:limit]

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
        invoice.update(supplier_id=int(supplier_id), karat_id=int(karat_id), supplier_reference=supplier_reference,
                       invoice_date=invoice_date, supplier_weight_grams=weight, gross_making_total=gross_making,
                       notes=notes)
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
            rows.append({**self._joined(invoice), "piece_count": len(links),
                         "actual_weight": sum((self.db.items[link["inventory_item_id"]]["weight_grams"]
                                               for link in links), D("0"))})
        return rows[:limit]

    # ------------------------------------------------------------ ledgers (insert only)
    def gold_type_id(self, code):
        return 1 if code == "PURCHASE_INVOICE" else None

    def making_type_id(self, code):
        return 1 if code == "PURCHASE_INVOICE" else None

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
        return {"id": item_type_id, "default_category_id": None} if int(item_type_id) in self.db.item_types else None

    def get_active_karat_by_id(self, karat_id):
        return self.db.karats.get(int(karat_id))

    def get_active_category_by_id(self, category_id):
        return {"id": category_id}

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


def build(permissions=ALL, user_id=7, employee_id=None, granted=None, session=None):
    """(db, session, authorization, SupplierService, SupplierPurchaseService) over one shared fake database."""
    db = Db()
    session = session or session_for(permissions, user_id, employee_id)
    authorization = FakeAuthorization(session, granted)
    inventory = InventoryService(repository=InventoryRepo)
    suppliers = SupplierService(session, transaction_factory=db.transaction, repository=SupplierRepo,
                                authorization=authorization)
    purchases = SupplierPurchaseService(session, transaction_factory=db.transaction, repository=PurchaseRepo,
                                        barcodes=Barcodes, inventory=inventory, authorization=authorization)
    return db, session, authorization, suppliers, purchases
