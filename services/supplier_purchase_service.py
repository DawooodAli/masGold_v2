"""Supplier purchase invoices, posting to the supplier ledgers, and piece-by-piece barcode intake.

Locked rules implemented here:
  * Each supplier has ONE dedicated karat (18, 21 or 24) and every invoice inherits it (a different karat is refused).
    The supplier invoice weight (the sum of its making-group weights) is the authoritative contractual gold: posting
    writes +weight, in physical grams of that karat, to the immutable GOLD ledger. The ledger's Eq21 column is stored
    only as an internal compatibility value and drives no supplier amount or direction.
  * Making is EGP per gram per group: gross making = SUM(group supplier weight x EGP/g), posted to the immutable
    MAKING ledger. Gold and making are independent; neither depends on any barcoded weight.
  * Posting is atomic and idempotent (row lock + status transition + unique ledger source keys + request key).
  * Intake creates real inventory pieces ONLY through InventoryService.create_item_on_connection (the canonical
    path) with a barcode from the canonical BarcodeRepository sequence, on the SAME transaction as the supplier link,
    so a failure leaves no orphan piece, barcode or link.
  * Weight variance (actual barcoded weight - supplier weight) is informational: nothing here reads it back into a
    supplier balance, a making liability, an inventory weight, Scrap or cash.

Permissions (explicit grants, strict locking write path re-checked inside each transaction):
  SUPPLIER_VIEW (read), SUPPLIER_INVOICE_EDIT (draft), SUPPLIER_INVOICE_POST (post), SUPPLIER_INTAKE (pieces).
"""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from mysql.connector import IntegrityError

from repositories.barcode_repository import BarcodeRepository
from repositories.supplier_purchase_repository import SupplierPurchaseRepository
from services import supplier_rules as rules
from services.authorization_service import AuthorizationService
from services.inventory_service import InventoryService
from services.invoice_service import invoice_transaction
from services.number_text import normalize_digits
from services.supplier_service import VIEW, authenticated_actor, clean_text

EDIT, POST, INTAKE = "SUPPLIER_INVOICE_EDIT", "SUPPLIER_INVOICE_POST", "SUPPLIER_INTAKE"
REFERENCE_MAX, NOTES_MAX = 100, 1000
INTAKE_SOURCE = "SUPPLIER_PURCHASE"          # توريد مورد: never MARKET_PURCHASE (gold bought on the market)

_INVENTORY_MESSAGES = {
    "Item type is required.": "يجب اختيار الصنف.",
    "Item type is not active or does not exist.": "الصنف غير موجود أو غير نشط.",
    "Karat is not active or does not exist.": "العيار غير موجود أو غير نشط.",
    "Category is not active or does not exist.": "الفئة غير موجودة أو غير نشطة.",
    "Factory is not active or does not exist.": "المصنع غير موجود أو غير نشط.",
    "Employee is not active or does not exist.": "الموظف غير موجود أو غير نشط.",
    "Barcode already exists.": "هذا الباركود مستخدم بالفعل.",
    "Barcode is too long.": "الباركود أطول من الحد المسموح.",
    "Size is too long.": "المقاس أطول من الحد المسموح.",
    "Notes are too long.": "الملاحظات أطول من الحد المسموح.",
    "Weight must be a valid number.": "الوزن غير صحيح.",
    "Weight must be greater than zero.": "الوزن يجب أن يكون أكبر من صفر.",
    "ITEM_CREATED movement type is not configured.": "نوع حركة إدخال المخزون غير مهيأ في قاعدة البيانات.",
}


def _key(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise rules.SupplierRuleError("REQUEST_INVALID", "أعد فتح النموذج وحاول مرة أخرى.") from None


def _id(value, code, message):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise rules.SupplierRuleError(code, message) from None
    if number <= 0:
        raise rules.SupplierRuleError(code, message)
    return number


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(normalize_digits(str(value or "")).strip())
    except ValueError:
        raise rules.SupplierRuleError("DATE_INVALID", "تاريخ الفاتورة غير صحيح.") from None


class SupplierPurchaseService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=SupplierPurchaseRepository,
                 barcodes=BarcodeRepository, inventory=None, authorization=None):
        self._session, self._transaction, self._repository = session, transaction_factory, repository
        self._barcodes = barcodes
        self._inventory = inventory or InventoryService()
        self._authorization = authorization or AuthorizationService(session)

    # ------------------------------------------------------------------ UI hints (never the authority)
    def _can(self, permission):
        actor = self._session.current_actor()
        return actor is not None and permission in actor.permissions

    def can_view(self):
        return self._can(VIEW)

    def can_edit(self):
        return self._can(EDIT)

    def can_post(self):
        return self._can(POST)

    def can_intake(self):
        return self._can(INTAKE)

    # ------------------------------------------------------------------ authorization paths
    def _read_verified(self, connection, actor):
        return self._authorization.require_permission_read(connection, actor, VIEW)

    def _write_verified(self, connection, actor, permission):
        return self._authorization.require_permission_for_write(connection, actor, permission)

    # ------------------------------------------------------------------ drafts
    @staticmethod
    def _inherited_karat(repository, supplier, supplied=None):
        """The invoice karat IS the supplier's dedicated karat. A supplier without one cannot take an invoice, and a supplied
        karat that differs is refused: the screen is never trusted and an incompatible draft is never silently converted."""
        if supplier["karat_id"] is None:
            raise rules.SupplierRuleError(
                "SUPPLIER_KARAT_MISSING", "عيار هذا المورد غير محدد. حدّد عياره من تعديل المورد قبل إنشاء أو ترحيل فواتيره.")
        if supplied not in (None, "", 0, "0"):
            try:
                same = int(str(supplied).strip()) == int(supplier["karat_id"])
            except ValueError:
                same = False
            if not same:
                raise rules.SupplierRuleError(
                    "KARAT_MISMATCH", f"عيار الفاتورة يتبع عيار المورد (عيار {int(supplier['karat_value'])}) ولا يمكن أن يختلف عنه.")
        karat = repository.get_karat(supplier["karat_id"])
        if karat is None or not karat["is_active"]:
            raise rules.SupplierRuleError("KARAT_NOT_FOUND", "العيار غير موجود أو غير نشط.")
        return int(supplier["karat_id"])

    def _validated_header(self, repository, supplier_id, karat_id, reference, invoice_date, groups, notes):
        supplier_id = _id(supplier_id, "SUPPLIER_REQUIRED", "اختر المورد.")
        supplier = repository.get_supplier(supplier_id, lock=True)       # the supplier row lock also guards its karat
        if supplier is None:
            raise rules.SupplierRuleError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
        if not supplier["is_active"]:
            raise rules.SupplierRuleError("SUPPLIER_INACTIVE", "المورد غير نشط.")
        karat_id = self._inherited_karat(repository, supplier, karat_id)
        normalized = rules.normalize_groups(groups)
        return {"supplier_id": supplier_id, "karat_id": karat_id,
                "supplier_reference": clean_text(reference, REFERENCE_MAX, "رقم فاتورة المورد"),
                "invoice_date": _date(invoice_date), "notes": clean_text(notes, NOTES_MAX, "الملاحظات"),
                "weight": normalized["total_weight"], "gross_making": normalized["gross_making"],
                "groups": normalized["groups"]}

    def create_draft(self, supplier_id, karat_id, supplier_reference, invoice_date, groups, notes, request_key, *,
                     _expected_actor=None):
        actor = authenticated_actor(self._session, EDIT, "لا توجد صلاحية لتحرير فواتير الموردين.", _expected_actor)
        key = _key(request_key)
        with self._transaction() as connection:
            actor = self._write_verified(connection, actor, EDIT)
            repository = self._repository(connection)
            prior = repository.by_draft_key(key)
            if prior is not None:
                if int(prior["created_by_user_id"]) != int(actor.user_id):
                    raise rules.SupplierRuleError("REQUEST_CONFLICT", "طلب الفاتورة مستخدم لعملية مختلفة.")
                return self._header_result(prior, duplicate=True)
            data = self._validated_header(repository, supplier_id, karat_id, supplier_reference, invoice_date,
                                          groups, notes)
            invoice_id = repository.insert_invoice(
                supplier_id=data["supplier_id"], karat_id=data["karat_id"],
                supplier_reference=data["supplier_reference"], invoice_date=data["invoice_date"],
                weight=data["weight"], gross_making=data["gross_making"], notes=data["notes"], key=key,
                user_id=actor.user_id)
            repository.replace_groups(invoice_id, data["groups"])
            repository.audit(actor=actor, event_type="SUPPLIER_INVOICE_DRAFTED", entity_id=invoice_id,
                             entity_type="SUPPLIER_INVOICE", reference=rules.invoice_code(invoice_id),
                             description="إنشاء مسودة فاتورة مورد",
                             metadata={"supplier_id": data["supplier_id"], "karat_id": data["karat_id"],
                                       "weight": str(data["weight"]), "gross_making": str(data["gross_making"]),
                                       "groups": len(data["groups"])}, now=repository.db_now())
            return self._header_result(repository.get_invoice(invoice_id))

    def update_draft(self, invoice_id, supplier_id, karat_id, supplier_reference, invoice_date, groups, notes, *,
                     _expected_actor=None):
        actor = authenticated_actor(self._session, EDIT, "لا توجد صلاحية لتحرير فواتير الموردين.", _expected_actor)
        invoice_id = _id(invoice_id, "INVOICE_NOT_FOUND", "فاتورة المورد غير موجودة.")
        with self._transaction() as connection:
            actor = self._write_verified(connection, actor, EDIT)
            repository = self._repository(connection)
            invoice = repository.get_invoice(invoice_id, lock=True)
            self._require_draft(invoice)
            data = self._validated_header(repository, supplier_id, karat_id, supplier_reference, invoice_date,
                                          groups, notes)
            changed = repository.update_draft(invoice_id=invoice_id, supplier_id=data["supplier_id"],
                                              karat_id=data["karat_id"], supplier_reference=data["supplier_reference"],
                                              invoice_date=data["invoice_date"], weight=data["weight"],
                                              gross_making=data["gross_making"], notes=data["notes"])
            # MySQL reports CHANGED rows: saving a draft without edits legitimately reports 0. The row is locked and was
            # just verified as a DRAFT, so 0 only means "nothing differed"; a non-draft still never gets here.
            if changed != 1 and repository.get_invoice(invoice_id)["status"] != rules.DRAFT:
                raise rules.SupplierRuleError("INVOICE_NOT_DRAFT", "لا يمكن تعديل الفاتورة بعد ترحيلها.")
            repository.replace_groups(invoice_id, data["groups"])
            repository.audit(actor=actor, event_type="SUPPLIER_INVOICE_DRAFT_UPDATED", entity_id=invoice_id,
                             entity_type="SUPPLIER_INVOICE", reference=rules.invoice_code(invoice_id),
                             description="تعديل مسودة فاتورة مورد",
                             metadata={"weight": str(data["weight"]), "gross_making": str(data["gross_making"]),
                                       "groups": len(data["groups"])}, now=repository.db_now())
            return self._header_result(repository.get_invoice(invoice_id))

    def cancel_draft(self, invoice_id, *, _expected_actor=None):
        actor = authenticated_actor(self._session, EDIT, "لا توجد صلاحية لتحرير فواتير الموردين.", _expected_actor)
        invoice_id = _id(invoice_id, "INVOICE_NOT_FOUND", "فاتورة المورد غير موجودة.")
        with self._transaction() as connection:
            actor = self._write_verified(connection, actor, EDIT)
            repository = self._repository(connection)
            self._require_draft(repository.get_invoice(invoice_id, lock=True))
            now = repository.db_now()
            if repository.mark_cancelled(invoice_id=invoice_id, now=now) != 1:
                raise rules.SupplierRuleError("INVOICE_NOT_DRAFT", "لا يمكن إلغاء الفاتورة بعد ترحيلها.")
            repository.audit(actor=actor, event_type="SUPPLIER_INVOICE_DRAFT_CANCELLED", entity_id=invoice_id,
                             entity_type="SUPPLIER_INVOICE", reference=rules.invoice_code(invoice_id),
                             description="إلغاء مسودة فاتورة مورد", metadata={}, now=now)

    @staticmethod
    def _require_draft(invoice):
        if invoice is None:
            raise rules.SupplierRuleError("INVOICE_NOT_FOUND", "فاتورة المورد غير موجودة.")
        if invoice["status"] != rules.DRAFT:
            raise rules.SupplierRuleError("INVOICE_NOT_DRAFT", "لا يمكن تعديل الفاتورة بعد ترحيلها.")

    # ------------------------------------------------------------------ posting
    def post_invoice(self, invoice_id, request_key, *, _expected_actor=None):
        """Atomic posting: status POSTED + one gold movement (+ one making movement when making > 0). Retrying the
        same request returns the posted result and creates nothing; any other second post is refused."""
        session_actor = authenticated_actor(self._session, POST, "لا توجد صلاحية لترحيل فواتير الموردين.",
                                            _expected_actor)
        invoice_id = _id(invoice_id, "INVOICE_NOT_FOUND", "فاتورة المورد غير موجودة.")
        key = _key(request_key)
        with self._transaction() as connection:
            actor = self._write_verified(connection, session_actor, POST)
            repository = self._repository(connection)
            invoice = repository.get_invoice(invoice_id, lock=True)
            if invoice is None:
                raise rules.SupplierRuleError("INVOICE_NOT_FOUND", "فاتورة المورد غير موجودة.")
            if invoice["status"] == rules.POSTED:
                if invoice["post_request_key"] == key and int(invoice["posted_by_user_id"]) == int(actor.user_id):
                    return self._post_result(invoice, duplicate=True)
                raise rules.SupplierRuleError("ALREADY_POSTED", "الفاتورة مرحّلة بالفعل.")
            if invoice["status"] != rules.DRAFT:
                raise rules.SupplierRuleError("INVOICE_NOT_DRAFT", "الفاتورة ملغاة ولا يمكن ترحيلها.")
            if not invoice["supplier_active"]:
                raise rules.SupplierRuleError("SUPPLIER_INACTIVE", "المورد غير نشط.")
            # Revalidated at posting under the supplier row lock: the draft's karat must still be the supplier's karat.
            supplier = repository.get_supplier(invoice["supplier_id"], lock=True)
            self._inherited_karat(repository, supplier, invoice["karat_id"])
            groups = repository.groups(invoice_id)
            reconciled = rules.normalize_groups(
                [{"weight": g["supplier_weight_grams"], "making_per_gram": g["making_per_gram"],
                  "description": g["description"]} for g in groups]) if groups else None
            if (reconciled is None or reconciled["total_weight"] != Decimal(str(invoice["supplier_weight_grams"]))
                    or reconciled["gross_making"] != Decimal(str(invoice["gross_making_total"]))):
                raise rules.SupplierRuleError(
                    "INVOICE_INCONSISTENT", "مجموع أوزان المجموعات لا يطابق وزن الفاتورة. راجع المجموعات قبل الترحيل.")
            gold_type = repository.gold_type_id(rules.PURCHASE_MOVEMENT)
            making_type = repository.making_type_id(rules.PURCHASE_MOVEMENT)
            if gold_type is None or making_type is None:
                raise RuntimeError("supplier movement types are missing (migration 034)")
            weight = Decimal(str(invoice["supplier_weight_grams"]))
            gross = Decimal(str(invoice["gross_making_total"]))
            # The stored Eq21 column is an internal compatibility field (NOT NULL, sign-checked); it drives nothing.
            eq21 = rules.equivalent_21(weight, invoice["karat_value"])
            code = rules.invoice_code(invoice_id)
            now = repository.db_now()
            label = f"فاتورة شراء مورد {code}"
            repository.insert_gold_movement(
                supplier_id=invoice["supplier_id"], type_id=gold_type, karat_id=invoice["karat_id"],
                weight_delta=weight, eq21_delta=eq21, source_type=rules.PURCHASE_SOURCE, source_id=invoice_id,
                reference=code, description=label, now=now, user_id=actor.user_id)
            if gross > 0:
                repository.insert_making_movement(
                    supplier_id=invoice["supplier_id"], type_id=making_type, amount_delta=gross,
                    source_type=rules.PURCHASE_SOURCE, source_id=invoice_id, reference=code,
                    description=label, now=now, user_id=actor.user_id)
            if repository.mark_posted(invoice_id=invoice_id, key=key, user_id=actor.user_id, now=now) != 1:
                raise rules.SupplierRuleError("ALREADY_POSTED", "الفاتورة مرحّلة بالفعل.")
            repository.audit(actor=actor, event_type="SUPPLIER_INVOICE_POSTED", entity_id=invoice_id,
                             entity_type="SUPPLIER_INVOICE", reference=code, description=label,
                             metadata={"supplier_id": invoice["supplier_id"], "karat_value": int(invoice["karat_value"]),
                                       "weight": str(weight), "gross_making": str(gross),
                                       "groups": len(groups)}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
            return self._post_result(repository.get_invoice(invoice_id))

    # ------------------------------------------------------------------ piece-by-piece intake
    def add_piece(self, invoice_id, group_id, item_type_id, weight, request_key, *, category_id=None, factory_id=None,
                  size=None, barcode_mode="AUTO", barcode=None, notes=None, _expected_actor=None):
        """One physical piece of a POSTED invoice group: canonical inventory entry + barcode + source link, atomic.

        The piece's category (grade) is resolved HERE from the group's making rate and the existing category purchase-making
        ranges, never taken from the caller and never from the item type. `category_id` exists only so that a caller that
        still supplies one is checked: a value that differs from the resolved category is refused, it never overrides.

        barcode_mode: AUTO (next barcode of the canonical daily sequence), MANUAL (the value given) or NONE (the
        piece is entered without a barcode and stays non-printable until it has one)."""
        actor = authenticated_actor(self._session, INTAKE, "لا توجد صلاحية لإدخال قطع فواتير الموردين.",
                                    _expected_actor)
        invoice_id = _id(invoice_id, "INVOICE_NOT_FOUND", "فاتورة المورد غير موجودة.")
        group_id = _id(group_id, "GROUP_NOT_FOUND", "مجموعة الفاتورة غير موجودة.")
        key = _key(request_key)
        weight = rules.parse_weight(weight, "وزن القطعة")
        mode = str(barcode_mode or "AUTO").strip().upper()
        if mode not in ("AUTO", "MANUAL", "NONE"):
            raise rules.SupplierRuleError("BARCODE_MODE", "وضع الباركود غير صحيح.")
        manual = str(barcode or "").strip()
        if mode == "MANUAL" and not manual:
            raise rules.SupplierRuleError("BARCODE_REQUIRED", "أدخل قيمة الباركود.")
        with self._transaction() as connection:
            actor = self._write_verified(connection, actor, INTAKE)
            repository = self._repository(connection)
            prior = repository.piece_by_request(key)
            if prior is not None:
                if int(prior["invoice_id"]) != invoice_id or int(prior["group_id"]) != group_id:
                    raise rules.SupplierRuleError("REQUEST_CONFLICT", "طلب الإدخال مستخدم لعملية مختلفة.")
                return self._piece_result(repository, prior, duplicate=True)
            invoice = repository.get_invoice(invoice_id, lock=True)
            if invoice is None:
                raise rules.SupplierRuleError("INVOICE_NOT_FOUND", "فاتورة المورد غير موجودة.")
            if invoice["status"] != rules.POSTED:
                raise rules.SupplierRuleError("INVOICE_NOT_POSTED", "يمكن إدخال القطع من فاتورة مرحّلة فقط.")
            group = repository.lock_group(group_id)
            if group is None or int(group["invoice_id"]) != invoice_id:
                raise rules.SupplierRuleError("GROUP_NOT_FOUND", "مجموعة الفاتورة غير موجودة.")
            # The category comes from the group's making rate (existing purchase-making ranges), resolved under the group lock
            # and before any write. No match, or several, refuses the piece with the exact reason: it is never guessed.
            category = rules.require_category(group["making_per_gram"], repository.purchase_categories())
            if category_id not in (None, "", 0, "0"):
                try:
                    supplied = int(Decimal(str(category_id)))
                except (ValueError, ArithmeticError):
                    supplied = -1
                if supplied != int(category["id"]):
                    raise rules.SupplierRuleError(
                        "CATEGORY_CONFLICT", "فئة القطعة تُحدَّد تلقائيًا من مصنعية المجموعة ولا يمكن اختيار فئة مختلفة.")
            if weight > Decimal(str(invoice["supplier_weight_grams"])):
                raise rules.SupplierRuleError("PIECE_TOO_HEAVY", "وزن القطعة يتجاوز وزن فاتورة المورد بالكامل.")
            # The group's cumulative actual weight (+ this piece) may not exceed its supplier weight + 0.333 g. Read
            # under the group lock and in this same transaction, before any barcode or inventory write, so a refusal
            # persists nothing and two concurrent intakes of one group cannot both spend the same remaining allowance.
            rules.check_intake(group["supplier_weight_grams"], repository.group_actual_weight(group_id), weight)
            now = repository.db_now()
            value = (self._barcodes(connection).allocate_barcode_value(now.date()) if mode == "AUTO"
                     else manual if mode == "MANUAL" else None)
            try:
                item_id = self._inventory.create_item_on_connection(
                    connection, actor_user_id=actor.user_id, employee_id=actor.employee_id,
                    item_type_id=item_type_id, karat_id=invoice["karat_id"], weight_grams=weight,
                    acquisition_source=INTAKE_SOURCE, category_id=int(category["id"]), factory_id=factory_id, size=size,
                    barcode=value, notes=notes, acquisition_making_per_gram=group["making_per_gram"])
            except ValueError as exc:
                raise rules.SupplierRuleError("INVENTORY_REFUSED", _INVENTORY_MESSAGES.get(str(exc), str(exc))) from exc
            link_id = repository.insert_piece(
                inventory_item_id=item_id, invoice_id=invoice_id, group_id=group_id,
                making_per_gram=group["making_per_gram"], key=key, user_id=actor.user_id)
            repository.audit(actor=actor, event_type="SUPPLIER_INVOICE_PIECE_ADDED", entity_id=invoice_id,
                             entity_type="SUPPLIER_INVOICE", reference=rules.invoice_code(invoice_id),
                             description="إدخال قطعة من فاتورة مورد",
                             metadata={"inventory_item_id": item_id, "group_id": group_id, "weight": str(weight),
                                       "barcode": value, "making_per_gram": str(group["making_per_gram"]),
                                       "category_id": int(category["id"]), "category_code": category["code"],
                                       "category_basis": "PURCHASE_MAKING_RANGE"}, now=now)
            return {"id": str(link_id), "inventory_item_id": str(item_id), "invoice_id": str(invoice_id),
                    "group_id": str(group_id), "barcode": value or "", "weight": weight,
                    "making_per_gram": Decimal(str(group["making_per_gram"])),
                    "category_code": category["code"], "duplicate": False}

    def _piece_result(self, repository, link, duplicate):
        piece = next((p for p in repository.pieces(link["invoice_id"], link["group_id"])
                      if int(p["id"]) == int(link["id"])), None) or {}
        return {"id": str(link["id"]), "inventory_item_id": str(link["inventory_item_id"]),
                "invoice_id": str(link["invoice_id"]), "group_id": str(link["group_id"]),
                "barcode": piece.get("barcode_value") or "", "weight": Decimal(str(piece.get("weight_grams") or 0)),
                "making_per_gram": Decimal(str(link["making_per_gram"])), "duplicate": duplicate}

    # ------------------------------------------------------------------ reads
    def list_invoices(self, supplier_id=None, status=None, *, _expected_actor=None):
        actor = authenticated_actor(self._session, VIEW, "لا توجد صلاحية لعرض فواتير الموردين.", _expected_actor)
        supplier = None if supplier_id in (None, "") else _id(supplier_id, "SUPPLIER_NOT_FOUND", "المورد غير موجود.")
        status = str(status or "").strip().upper() or None
        if status not in (None, rules.DRAFT, rules.POSTED, rules.CANCELLED):
            raise rules.SupplierRuleError("STATUS_INVALID", "حالة الفاتورة غير صحيحة.")
        with self._transaction() as connection:
            self._read_verified(connection, actor)
            rows = self._repository(connection).list_invoices(supplier, status)
        return [self._summary(row) for row in rows]

    def invoice_detail(self, invoice_id, *, _expected_actor=None):
        """The invoice, its groups with intake progress and weight variance, and its pieces."""
        actor = authenticated_actor(self._session, VIEW, "لا توجد صلاحية لعرض فواتير الموردين.", _expected_actor)
        invoice_id = _id(invoice_id, "INVOICE_NOT_FOUND", "فاتورة المورد غير موجودة.")
        with self._transaction() as connection:
            self._read_verified(connection, actor)
            repository = self._repository(connection)
            invoice = repository.get_invoice(invoice_id)
            if invoice is None:
                raise rules.SupplierRuleError("INVOICE_NOT_FOUND", "فاتورة المورد غير موجودة.")
            groups = repository.groups(invoice_id)
            progress = {int(r["group_id"]): r for r in repository.group_progress(invoice_id)}
            pieces = repository.pieces(invoice_id)
            categories = repository.purchase_categories()        # a read: it belongs inside the transaction (the connection is closed after it)
        group_rows, total_actual, total_count = [], Decimal("0"), 0
        for group in groups:
            resolution = rules.resolve_category(group["making_per_gram"], categories)
            stats = progress.get(int(group["id"]), {})
            actual = Decimal(str(stats.get("actual_weight") or 0))
            count = int(stats.get("piece_count") or 0)
            total_actual += actual
            total_count += count
            group_rows.append({
                "id": str(group["id"]), "group_no": int(group["group_no"]), "description": group["description"] or "",
                "supplier_weight": Decimal(str(group["supplier_weight_grams"])),
                "making_per_gram": Decimal(str(group["making_per_gram"])),
                "making_total": Decimal(str(group["making_total"])), "piece_count": count,
                # the category the group's making rate resolves to under the existing rules (nothing is stored)
                "category_status": resolution["status"],
                "category_id": None if resolution["category"] is None else int(resolution["category"]["id"]),
                "category_code": "" if resolution["category"] is None else resolution["category"]["code"],
                "category_name": "" if resolution["category"] is None else resolution["category"]["name"],
                "category_problem": rules.category_problem(group["making_per_gram"], resolution),
                # tolerance-inclusive allowance (separate from the reference remaining weight; may be negative)
                "intake_limit": rules.intake_limit(group["supplier_weight_grams"]),
                "intake_allowance": rules.intake_allowance(group["supplier_weight_grams"], actual),
                **rules.progress(actual, group["supplier_weight_grams"])})
        weight = Decimal(str(invoice["supplier_weight_grams"]))
        return {**self._header_result(invoice),
                "supplier_name": invoice["supplier_name"], "karat_value": int(invoice["karat_value"]),
                "posted_at": invoice["posted_at"], "groups": group_rows, "piece_count": total_count,
                "intake": rules.progress(total_actual, weight),
                "pieces": [{"id": str(p["id"]), "inventory_item_id": str(p["inventory_item_id"]),
                            "group_no": int(p["group_no"]), "weight": Decimal(str(p["weight_grams"])),
                            "making_per_gram": Decimal(str(p["making_per_gram"])), "item_type": p["item_type_name"],
                            "size": p["size"] or "", "status": p["inventory_status"],
                            "barcode": p["barcode_value"] or ""} for p in pieces]}

    def piece_origin(self, inventory_item_id, *, _expected_actor=None):
        """Reverse traceability of a piece: its supplier invoice and group (None for stock from elsewhere)."""
        actor = authenticated_actor(self._session, VIEW, "لا توجد صلاحية لعرض فواتير الموردين.", _expected_actor)
        item = _id(inventory_item_id, "ITEM_NOT_FOUND", "القطعة غير موجودة.")
        with self._transaction() as connection:
            self._read_verified(connection, actor)
            row = self._repository(connection).piece_for_item(item)
        if row is None:
            return None
        return {"inventory_item_id": str(row["inventory_item_id"]), "invoice_id": str(row["invoice_id"]),
                "invoice_code": rules.invoice_code(row["invoice_id"]), "group_id": str(row["group_id"]),
                "group_no": int(row["group_no"]), "making_per_gram": Decimal(str(row["making_per_gram"])),
                "supplier_id": str(row["supplier_id"]), "supplier_name": row["supplier_name"],
                "supplier_reference": row["supplier_reference"] or ""}

    # ------------------------------------------------------------------ shapes
    @staticmethod
    def _header_result(invoice, duplicate=False):
        return {"id": str(invoice["id"]), "code": rules.invoice_code(invoice["id"]),
                "supplier_id": str(invoice["supplier_id"]), "karat_id": invoice["karat_id"],
                "supplier_reference": invoice["supplier_reference"] or "", "invoice_date": invoice["invoice_date"],
                "supplier_weight": Decimal(str(invoice["supplier_weight_grams"])),
                "gross_making": Decimal(str(invoice["gross_making_total"])), "status": invoice["status"],
                "notes": invoice["notes"] or "", "duplicate": duplicate}

    def _post_result(self, invoice, duplicate=False):
        weight = Decimal(str(invoice["supplier_weight_grams"]))
        return {**self._header_result(invoice, duplicate), "karat_value": int(invoice["karat_value"])}

    @staticmethod
    def _summary(row):
        weight = Decimal(str(row["supplier_weight_grams"]))
        return {"id": str(row["id"]), "code": rules.invoice_code(row["id"]), "supplier_id": str(row["supplier_id"]),
                "supplier_name": row["supplier_name"], "karat_value": int(row["karat_value"]),
                "supplier_reference": row["supplier_reference"] or "", "invoice_date": row["invoice_date"],
                "supplier_weight": weight, "gross_making": Decimal(str(row["gross_making_total"])),
                "status": row["status"], "piece_count": int(row["piece_count"]),
                **{f"intake_{k}": v for k, v in rules.progress(row["actual_weight"], weight).items()}}
