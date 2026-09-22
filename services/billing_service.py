from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from database.transaction import DatabaseTransaction
from repositories.billing_repository import BillingRepository


class BillingService:
    """Unified sale + customer old-gold purchase service."""

    MONEY = Decimal("0.01")
    WEIGHT = Decimal("0.001")
    PRICE = Decimal("0.0001")

    @staticmethod
    def _decimal(value, quant, *, allow_zero=False, label="Value"):
        try:
            result = Decimal(str(value).strip()).quantize(
                quant,
                rounding=ROUND_HALF_UP,
            )
        except (InvalidOperation, ValueError, AttributeError) as exc:
            raise ValueError(f"{label} must be a valid number.") from exc

        if allow_zero:
            if result < 0:
                raise ValueError(f"{label} cannot be negative.")
        elif result <= 0:
            raise ValueError(f"{label} must be greater than zero.")

        return result

    @classmethod
    def price_for_karat(cls, base_21_price, karat_value):
        base = cls._decimal(
            base_21_price,
            cls.PRICE,
            label="21K price",
        )
        karat = Decimal(str(int(karat_value)))
        return (base * karat / Decimal("21")).quantize(
            cls.PRICE,
            rounding=ROUND_HALF_UP,
        )

    @classmethod
    def sale_line_total(cls, weight, price, making_per_gram):
        weight = cls._decimal(weight, cls.WEIGHT, label="Weight")
        price = cls._decimal(price, cls.PRICE, label="Gold price")
        making = cls._decimal(
            making_per_gram,
            cls.PRICE,
            allow_zero=True,
            label="Making",
        )
        return (weight * (price + making)).quantize(
            cls.MONEY,
            rounding=ROUND_HALF_UP,
        )

    @classmethod
    def old_gold_line_total(cls, weight, price):
        weight = cls._decimal(weight, cls.WEIGHT, label="Weight")
        price = cls._decimal(price, cls.PRICE, label="Gold price")
        return (weight * price).quantize(
            cls.MONEY,
            rounding=ROUND_HALF_UP,
        )

    def find_sellable_by_barcode(self, barcode_value):
        barcode_value = str(barcode_value or "").strip()
        if not barcode_value:
            raise ValueError("Barcode is required.")

        with DatabaseTransaction() as transaction:
            repository = BillingRepository(transaction.connection)
            row = repository.get_sellable_item_by_barcode(barcode_value)

        if not row:
            raise ValueError("Barcode is not available in inventory.")

        return row

    def get_karat(self, karat_id):
        with DatabaseTransaction() as transaction:
            repository = BillingRepository(transaction.connection)
            row = repository.get_karat(karat_id)

        if not row or not bool(row.get("is_active")):
            raise ValueError("Karat does not exist or is inactive.")

        return row

    def finalize_invoice(
        self,
        *,
        customer_name,
        customer_id,
        employee_id,
        sell_price_21,
        buy_price_21,
        sale_lines,
        old_gold_lines,
        notes,
    ):
        customer_name = str(customer_name or "").strip()
        notes = str(notes or "").strip() or None
        customer_id = int(customer_id or 0)
        employee_id = int(employee_id or 0)

        if not customer_name:
            raise ValueError("Customer name is required.")

        if employee_id <= 0:
            raise ValueError("Employee is required.")

        sale_lines = list(sale_lines or [])
        old_gold_lines = list(old_gold_lines or [])

        if not sale_lines and not old_gold_lines:
            raise ValueError("Invoice is empty.")

        sell_price_21_dec = Decimal("0.0000")
        buy_price_21_dec = Decimal("0.0000")

        if sale_lines:
            sell_price_21_dec = self._decimal(
                sell_price_21,
                self.PRICE,
                label="Sell 21K price",
            )

        if old_gold_lines:
            buy_price_21_dec = self._decimal(
                buy_price_21,
                self.PRICE,
                label="Buy 21K price",
            )

        normalized_sale = []
        sale_total = Decimal("0.00")
        sale_weight_total = Decimal("0.000")
        seen_items = set()

        for line in sale_lines:
            item_id = int(line.get("inventory_item_id") or 0)
            if item_id <= 0 or item_id in seen_items:
                raise ValueError("Invalid or duplicate inventory item in invoice.")
            seen_items.add(item_id)

            weight = self._decimal(
                line.get("weight_grams"),
                self.WEIGHT,
                label="Sale weight",
            )
            karat_value = int(line.get("karat_value") or 0)
            price = self.price_for_karat(sell_price_21_dec, karat_value)
            making = self._decimal(
                line.get("making_per_gram", 0),
                self.PRICE,
                allow_zero=True,
                label="Making",
            )
            total = self.sale_line_total(weight, price, making)

            normalized_sale.append(
                {
                    **line,
                    "inventory_item_id": item_id,
                    "weight_grams": weight,
                    "karat_value": karat_value,
                    "gold_price_per_gram": price,
                    "making_per_gram": making,
                    "line_total": total,
                }
            )
            sale_total += total
            sale_weight_total += weight

        normalized_old = []
        old_total = Decimal("0.00")
        old_weight_total = Decimal("0.000")

        for line in old_gold_lines:
            karat_id = int(line.get("karat_id") or 0)
            karat_value = int(line.get("karat_value") or 0)
            weight = self._decimal(
                line.get("weight_grams"),
                self.WEIGHT,
                label="Old-gold weight",
            )
            price = self.price_for_karat(buy_price_21_dec, karat_value)
            total = self.old_gold_line_total(weight, price)

            normalized_old.append(
                {
                    **line,
                    "karat_id": karat_id,
                    "karat_value": karat_value,
                    "weight_grams": weight,
                    "buy_price_per_gram": price,
                    "line_total": total,
                }
            )
            old_total += total
            old_weight_total += weight

        sale_total = sale_total.quantize(self.MONEY)
        old_total = old_total.quantize(self.MONEY)
        sale_weight_total = sale_weight_total.quantize(self.WEIGHT)
        old_weight_total = old_weight_total.quantize(self.WEIGHT)
        net_due = (sale_total - old_total).quantize(self.MONEY)

        with DatabaseTransaction() as transaction:
            repository = BillingRepository(transaction.connection)

            employee = repository.get_active_employee(employee_id)
            if not employee or not bool(employee.get("is_active")):
                raise ValueError("Employee does not exist or is inactive.")

            locked = repository.lock_inventory_items(
                [line["inventory_item_id"] for line in normalized_sale]
            )

            if len(locked) != len(normalized_sale):
                raise ValueError("One or more sale items no longer exist.")

            locked_by_id = {
                int(row["inventory_item_id"]): row
                for row in locked
            }

            for line in normalized_sale:
                current = locked_by_id[line["inventory_item_id"]]
                if current.get("inventory_status") != "IN_STOCK":
                    raise ValueError(
                        f"Inventory item #{line['inventory_item_id']} is no longer available."
                    )

                # Re-read trusted physical attributes from DB.
                line["weight_grams"] = Decimal(
                    str(current["weight_grams"])
                ).quantize(self.WEIGHT)
                line["karat_value"] = int(current["karat_value"])
                line["gold_price_per_gram"] = self.price_for_karat(
                    sell_price_21_dec,
                    line["karat_value"],
                )
                line["line_total"] = self.sale_line_total(
                    line["weight_grams"],
                    line["gold_price_per_gram"],
                    line["making_per_gram"],
                )
                line["barcode_value"] = current.get("barcode_value")
                line["item_type_name"] = current.get("item_type_name")
                line["stock_classification"] = current.get(
                    "stock_classification"
                )

            # Recalculate after DB lock/re-read.
            sale_total = sum(
                (line["line_total"] for line in normalized_sale),
                Decimal("0.00"),
            ).quantize(self.MONEY)
            sale_weight_total = sum(
                (line["weight_grams"] for line in normalized_sale),
                Decimal("0.000"),
            ).quantize(self.WEIGHT)
            net_due = (sale_total - old_total).quantize(self.MONEY)

            invoice_code = repository.next_invoice_code(date.today())

            invoice_id = repository.create_invoice(
                invoice_code=invoice_code,
                customer_name=customer_name,
                customer_id=customer_id if customer_id > 0 else None,
                employee_id=employee_id,
                sell_price_21=sell_price_21_dec,
                buy_price_21=buy_price_21_dec,
                sale_total=sale_total,
                old_gold_total=old_total,
                net_customer_due=net_due,
                sale_weight_total=sale_weight_total,
                old_gold_weight_total=old_weight_total,
                notes=notes,
            )

            sold_type_id = None
            if normalized_sale:
                sold_type_id = repository.get_inventory_movement_type_id("SOLD")

            for line in normalized_sale:
                repository.add_sale_line(
                    invoice_id=invoice_id,
                    inventory_item_id=line["inventory_item_id"],
                    barcode_snapshot=line.get("barcode_value"),
                    item_type_snapshot=line.get("item_type_name") or "",
                    karat_value_snapshot=line["karat_value"],
                    weight_grams=line["weight_grams"],
                    gold_price_per_gram=line["gold_price_per_gram"],
                    making_per_gram=line["making_per_gram"],
                    line_total=line["line_total"],
                )

                repository.mark_inventory_sold(line["inventory_item_id"])

                repository.add_inventory_sold_movement(
                    inventory_item_id=line["inventory_item_id"],
                    movement_type_id=sold_type_id,
                    stock_classification=line.get("stock_classification") or "NEW_STOCK",
                    weight_grams=line["weight_grams"],
                    employee_id=employee_id,
                    invoice_code=invoice_code,
                )

            old_gold_type_id = None
            movement_group_id = str(uuid4())

            if normalized_old:
                old_gold_type_id = repository.get_old_gold_movement_type_id(
                    "CUSTOMER_BUY_IN",
                    expected_direction="IN",
                )

            for line in normalized_old:
                karat = repository.get_karat(line["karat_id"])
                if (
                    not karat
                    or not bool(karat.get("is_active"))
                    or int(karat.get("karat_value") or 0) != line["karat_value"]
                ):
                    raise ValueError("Old-gold karat is invalid or inactive.")

                movement_id = repository.add_customer_old_gold_movement(
                    movement_type_id=old_gold_type_id,
                    karat_id=line["karat_id"],
                    weight_grams=line["weight_grams"],
                    gold_price_per_gram=line["buy_price_per_gram"],
                    cash_value=line["line_total"],
                    movement_group_id=movement_group_id,
                    invoice_id=invoice_id,
                    invoice_code=invoice_code,
                    customer_id=customer_id if customer_id > 0 else None,
                    customer_name=customer_name,
                    employee_id=employee_id,
                    notes=str(line.get("notes") or "").strip() or None,
                )

                repository.add_old_gold_line(
                    invoice_id=invoice_id,
                    old_gold_movement_id=movement_id,
                    karat_id=line["karat_id"],
                    karat_value_snapshot=line["karat_value"],
                    weight_grams=line["weight_grams"],
                    buy_price_per_gram=line["buy_price_per_gram"],
                    line_total=line["line_total"],
                    notes=str(line.get("notes") or "").strip() or None,
                )

            return {
                "invoice_id": invoice_id,
                "invoice_code": invoice_code,
                "sale_total": sale_total,
                "old_gold_total": old_total,
                "net_customer_due": net_due,
                "sale_weight_total_grams": sale_weight_total,
                "old_gold_weight_total_grams": old_weight_total,
            }
