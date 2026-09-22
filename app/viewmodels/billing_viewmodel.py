from datetime import date, datetime
from decimal import Decimal

from PySide6.QtCore import QObject, Property, Signal, Slot

from services.billing_service import BillingService


class BillingViewModel(QObject):
    draftChanged = Signal()
    actionChanged = Signal()
    busyChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._service = BillingService()
        self._sale_lines = []
        self._old_gold_lines = []
        self._sale_total = 0.0
        self._old_gold_total = 0.0
        self._net_due = 0.0
        self._action_message = ""
        self._action_success = True
        self._busy = False
        self._last_invoice_code = ""
        self._sell_price_21 = 0.0
        self._buy_price_21 = 0.0

    @staticmethod
    def _qml_safe(value):
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(value, date):
            return value.strftime("%Y-%m-%d")
        if isinstance(value, dict):
            return {key: BillingViewModel._qml_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [BillingViewModel._qml_safe(item) for item in value]
        return value

    def _set_action(self, message, success):
        self._action_message = str(message or "")
        self._action_success = bool(success)
        self.actionChanged.emit()

    def _set_busy(self, value):
        value = bool(value)
        if self._busy == value:
            return
        self._busy = value
        self.busyChanged.emit()

    def _recalculate(self):
        sale_total = 0.0
        for line in self._sale_lines:
            weight = float(line.get("weight_grams") or 0)
            karat = int(line.get("karat_value") or 0)
            making = float(line.get("making_per_gram") or 0)
            if self._sell_price_21 > 0 and karat > 0:
                price = self._sell_price_21 * karat / 21.0
            else:
                price = 0.0
            line["gold_price_per_gram"] = round(price, 4)
            line["line_total"] = round(weight * (price + making), 2)
            sale_total += line["line_total"]

        old_total = 0.0
        for line in self._old_gold_lines:
            weight = float(line.get("weight_grams") or 0)
            karat = int(line.get("karat_value") or 0)
            if self._buy_price_21 > 0 and karat > 0:
                price = self._buy_price_21 * karat / 21.0
            else:
                price = 0.0
            line["buy_price_per_gram"] = round(price, 4)
            line["line_total"] = round(weight * price, 2)
            old_total += line["line_total"]

        self._sale_total = round(sale_total, 2)
        self._old_gold_total = round(old_total, 2)
        self._net_due = round(self._sale_total - self._old_gold_total, 2)
        self.draftChanged.emit()

    @Property("QVariantList", notify=draftChanged)
    def saleLines(self):
        return self._qml_safe(self._sale_lines)

    @Property("QVariantList", notify=draftChanged)
    def oldGoldLines(self):
        return self._qml_safe(self._old_gold_lines)

    @Property(float, notify=draftChanged)
    def saleTotal(self):
        return self._sale_total

    @Property(float, notify=draftChanged)
    def oldGoldTotal(self):
        return self._old_gold_total

    @Property(float, notify=draftChanged)
    def netDue(self):
        return self._net_due

    @Property(int, notify=draftChanged)
    def saleCount(self):
        return len(self._sale_lines)

    @Property(int, notify=draftChanged)
    def oldGoldCount(self):
        return len(self._old_gold_lines)

    @Property(str, notify=actionChanged)
    def actionMessage(self):
        return self._action_message

    @Property(bool, notify=actionChanged)
    def actionSuccess(self):
        return self._action_success

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=actionChanged)
    def lastInvoiceCode(self):
        return self._last_invoice_code

    @Slot()
    def clearActionMessage(self):
        self._set_action("", True)

    @Slot(str, str)
    def updateBasePrices(self, sell_price_21, buy_price_21):
        try:
            self._sell_price_21 = float(str(sell_price_21 or "0").strip() or 0)
        except ValueError:
            self._sell_price_21 = 0.0

        try:
            self._buy_price_21 = float(str(buy_price_21 or "0").strip() or 0)
        except ValueError:
            self._buy_price_21 = 0.0

        self._recalculate()

    @Slot(str, str, result=bool)
    def addSaleByBarcode(self, barcode_value, sell_price_21):
        barcode_value = str(barcode_value or "").strip()
        try:
            price = float(str(sell_price_21 or "0").strip())
            if price <= 0:
                raise ValueError("Sell 21K price is required.")

            if any(
                str(line.get("barcode_value") or "") == barcode_value
                for line in self._sale_lines
            ):
                raise ValueError("Barcode is already in the invoice.")

            row = self._service.find_sellable_by_barcode(barcode_value)

            if any(
                int(line.get("inventory_item_id") or 0)
                == int(row["inventory_item_id"])
                for line in self._sale_lines
            ):
                raise ValueError("Item is already in the invoice.")

            self._sell_price_21 = price
            line = {
                "inventory_item_id": int(row["inventory_item_id"]),
                "barcode_value": row.get("barcode_value"),
                "item_type_name": row.get("item_type_name"),
                "karat_value": int(row.get("karat_value") or 0),
                "weight_grams": float(row.get("weight_grams") or 0),
                "factory_name": row.get("factory_name"),
                "category_code": row.get("category_code"),
                "stock_classification": row.get("stock_classification"),
                "making_per_gram": 0.0,
                "gold_price_per_gram": 0.0,
                "line_total": 0.0,
            }
            self._sale_lines.append(line)
            self._recalculate()
            self._set_action("تمت إضافة القطعة إلى جانب البيع.", True)
            return True

        except ValueError as exc:
            translations = {
                "Sell 21K price is required.": "أدخل سعر بيع جرام عيار 21 أولاً.",
                "Barcode is already in the invoice.": "هذا الباركود موجود بالفعل في الفاتورة.",
                "Item is already in the invoice.": "هذه القطعة موجودة بالفعل في الفاتورة.",
                "Barcode is required.": "أدخل الباركود.",
                "Barcode is not available in inventory.": "الباركود غير موجود أو القطعة غير متاحة للبيع.",
            }
            self._set_action(translations.get(str(exc), str(exc)), False)
            return False

        except Exception as exc:
            self._set_action(f"تعذر إضافة القطعة: {exc}", False)
            return False

    @Slot(int)
    def removeSaleLine(self, index):
        if 0 <= index < len(self._sale_lines):
            self._sale_lines.pop(index)
            self._recalculate()

    @Slot(int, str)
    def setSaleMakingPerGram(self, index, value):
        if not (0 <= index < len(self._sale_lines)):
            return
        try:
            making = float(str(value or "0").strip() or 0)
            if making < 0:
                making = 0.0
        except ValueError:
            making = 0.0
        self._sale_lines[index]["making_per_gram"] = making
        self._recalculate()

    @Slot(int, str, str, str, result=bool)
    def addOldGold(self, karat_id, weight_grams, buy_price_21, notes):
        try:
            weight = float(str(weight_grams or "0").strip())
            if weight <= 0:
                raise ValueError("Old-gold weight is required.")

            price = float(str(buy_price_21 or "0").strip())
            if price <= 0:
                raise ValueError("Buy 21K price is required.")

            karat = self._service.get_karat(int(karat_id or 0))
            self._buy_price_21 = price

            self._old_gold_lines.append(
                {
                    "karat_id": int(karat["id"]),
                    "karat_value": int(karat["karat_value"]),
                    "weight_grams": round(weight, 3),
                    "buy_price_per_gram": 0.0,
                    "line_total": 0.0,
                    "notes": str(notes or "").strip(),
                }
            )
            self._recalculate()
            self._set_action("تمت إضافة الكسر إلى جانب الشراء.", True)
            return True

        except ValueError as exc:
            translations = {
                "Old-gold weight is required.": "أدخل وزن الكسر بشكل صحيح.",
                "Buy 21K price is required.": "أدخل سعر شراء جرام عيار 21 أولاً.",
                "Karat does not exist or is inactive.": "العيار غير موجود أو غير نشط.",
            }
            self._set_action(translations.get(str(exc), str(exc)), False)
            return False

        except Exception as exc:
            self._set_action(f"تعذر إضافة الكسر: {exc}", False)
            return False

    @Slot(int)
    def removeOldGoldLine(self, index):
        if 0 <= index < len(self._old_gold_lines):
            self._old_gold_lines.pop(index)
            self._recalculate()

    @Slot()
    def clearDraft(self):
        self._sale_lines = []
        self._old_gold_lines = []
        self._sale_total = 0.0
        self._old_gold_total = 0.0
        self._net_due = 0.0
        self._last_invoice_code = ""
        self._set_action("", True)
        self.draftChanged.emit()

    @Slot(str, int, str, str, str, result=bool)
    def finalizeInvoice(
        self,
        customer_name,
        employee_id,
        sell_price_21,
        buy_price_21,
        notes,
    ):
        self._set_busy(True)

        try:
            result = self._service.finalize_invoice(
                customer_name=customer_name,
                customer_id=None,
                employee_id=employee_id,
                sell_price_21=sell_price_21,
                buy_price_21=buy_price_21,
                sale_lines=self._sale_lines,
                old_gold_lines=self._old_gold_lines,
                notes=notes,
            )

            self._last_invoice_code = str(result["invoice_code"])
            code = self._last_invoice_code
            net = float(result["net_customer_due"])

            if net > 0:
                settlement = f"على العميل {net:,.2f} ج"
            elif net < 0:
                settlement = f"لصالح العميل {abs(net):,.2f} ج"
            else:
                settlement = "الحساب متعادل"

            self._sale_lines = []
            self._old_gold_lines = []
            self._sale_total = 0.0
            self._old_gold_total = 0.0
            self._net_due = 0.0
            self.draftChanged.emit()

            self._set_action(
                f"تم حفظ الفاتورة {code} بنجاح — {settlement}.",
                True,
            )
            return True

        except ValueError as exc:
            translations = {
                "Customer name is required.": "أدخل اسم العميل.",
                "Employee is required.": "اختر الموظف المسؤول.",
                "Invoice is empty.": "الفاتورة فارغة. أضف بيعاً أو كسرًا أولاً.",
                "Employee does not exist or is inactive.": "الموظف غير موجود أو غير نشط.",
                "One or more sale items no longer exist.": "إحدى قطع البيع لم تعد موجودة في المخزون.",
                "Old-gold karat is invalid or inactive.": "يوجد عيار كسر غير صالح أو غير نشط.",
            }
            message = str(exc)
            if message.startswith("Inventory item #") and "no longer available" in message:
                message = "إحدى قطع البيع تم بيعها أو لم تعد متاحة. حدّث الفاتورة وأعد المحاولة."
            else:
                message = translations.get(message, message)
            self._set_action(message, False)
            return False

        except Exception as exc:
            self._set_action(f"تعذر حفظ الفاتورة: {exc}", False)
            return False

        finally:
            self._set_busy(False)
