from datetime import date, datetime
from decimal import Decimal
from math import ceil
import re

from database.transaction import DatabaseTransaction
from repositories.barcode_repository import BarcodeRepository


DEFAULT_BARCODE_PRINTER_NAME = "ZDesigner GK420t"


NO_BARCODE_REFUSAL = "لا يمكن طباعة لاصقة لقطعة بدون باركود؛ استبعد القطع بدون باركود من التحديد."


class BarcodePrinterError(RuntimeError):
    """
    Raised when barcode assignment/history may already be committed
    but sending the ZPL job to the printer fails.
    """

    def __init__(
        self,
        message,
        assigned_count=0,
        batch_code=None,
    ):
        super().__init__(message)
        self.assigned_count = int(
            assigned_count or 0
        )
        self.batch_code = batch_code


class PrototypeZplPrinter:
    """
    Exact physical baseline from the working prototype:
    - Zebra GK420t
    - raw ZPL
    - PW641 / LL0310
    - two jewelry labels side-by-side
    - same embedded prototype logo/frame graphics
    """

    _CODE39_SAFE = re.compile(
        r"^[0-9A-Z \-.\$/+%]+$"
    )

    def __init__(
        self,
        printer_name=None,
        settings=None,
        send_raw=None,
    ):
        # The Label printer: an explicit name, else this workstation's Settings → الطباعة choice
        # (read at print time), else the prototype's printer name. Never the Bill printer.
        self._printer_override = printer_name
        self._settings = settings
        self._send_raw_override = send_raw

    @property
    def printer_name(self):
        if self._printer_override is not None:
            return self._printer_override
        from config.printer_settings import PrinterSettings
        return (self._settings or PrinterSettings()).label_printer() or DEFAULT_BARCODE_PRINTER_NAME

    def test_label_zpl(self):
        """Settings → طباعة ليبل تجريبي: one prototype-format label with sample values."""
        return self.build_zpl_label({"barcode_value": "TEST0001", "weight_grams": "3.600", "size": "18",
                                     "karat_value": "21", "factory_code": "TEST", "category_code": "T"})

    def print_test_label(self):
        """Sends one test label to the Label printer; writes nothing to the database."""
        return self._send_raw([self.test_label_zpl()])

    @staticmethod
    def _safe_text(value):
        text = "" if value is None else str(value)

        return (
            text
            .replace("^", "")
            .replace("~", "")
            .replace("\r", " ")
            .replace("\n", " ")
            .strip()
        )

    @staticmethod
    def _weight_text(value):
        if value is None:
            return ""

        try:
            return f"{Decimal(str(value)):.3f}"
        except Exception:
            return str(value)

    def _barcode_text(self, value):
        value = self._safe_text(
            value
        ).upper()

        if not value:
            raise ValueError(
                "Barcode value is required for printing."
            )

        if not self._CODE39_SAFE.fullmatch(
            value
        ):
            raise ValueError(
                f"Barcode '{value}' contains characters "
                "not supported by the prototype Code 39 layout."
            )

        return value

    def _print_fields(self, item):
        return {
            "barcode": self._barcode_text(
                item.get("barcode_value")
            ),
            "weight": self._weight_text(
                item.get("weight_grams")
            ),
            "size": self._safe_text(
                item.get("size") or "0"
            ),
            "karat": self._safe_text(
                item.get("karat_value")
            ),
            "factory": self._safe_text(
                item.get("factory_code")
                or item.get("factory_name")
                or ""
            ),
            "category": self._safe_text(
                item.get("category_code")
                or ""
            ),
        }

    def build_zpl_label(
        self,
        left_item,
        right_item=None,
    ):
        left = self._print_fields(
            left_item
        )

        right = (
            self._print_fields(
                right_item
            )
            if right_item
            else {
                "barcode": "",
                "weight": "",
                "size": "",
                "karat": "",
                "factory": "",
                "category": "",
            }
        )

        return (
            f"^XA~TA000~JSN^LT0^MNW^MTT^PON^PMN^LH0,0^JMA^PR5,5~SD24^JUS^LRN^CI0^XZ"
            f"^CI28^XA^MD15^XZ"
            f"^XA^MMT^PW641^LL0310^LS0"

            f"^FO525,0^GFA,02048,02048,00016,:Z64:eJzt0sENgzAMBdAgDjlmBEZhNNigKzFKugFVLzmkuN9gO9RSu0CxkOCBiL+chHDVf1WkNQSi4kyLOe+u4iQmb/lh4Ad2di7OsuBI8+HNmdThw5N5buaYEkDX8Z4kUOuzu9Mcg3NSc+6+wiu7V8ezoznSE2/SDz/QYTAnujtnJBy/GuODp7MxgWaMDybLe3IW4+KWh/HRm1tUnZdZ5onmu/tmjviS/ehwv8F0chIHMZLafmOZTVzV/eEiRqvClvGZF3HBltnxSOaqXg9ndUbmdpyvuuoqqzdjwVQh:A1E8"
            f"^FO495,60^GFA,01280,01280,00020,:Z64:eJxjYBgFowAKeIDYAE2MjYGhQoKB8QeamIcEA8M/BgGogA0/e1sFM4NkAwMze3/DB7BYmWTPsTfszECxxzwHHs4Ai6UZzjh2hg0oxrhxxoHEHrAYM1CMDZ+YIUSMDSjGAxbbLHGgECJWJ99/7A/zf8n////z//8PcWMNf/uxH4z/gGL/2P/fh7iFgbHhAJgEEgdgvoCIMYOYh2FizI0wMUaEGFgbG0z/KBhOAABcVT6I:B31C"
            f"^FO0,192^GFA,02048,02048,00016,:Z64:eJzt0jEOgzAMBVBQBkaOwFE4WpA69FpsvQZHyEilKO43/Bhqqe1eYSHBAxF/HJrmqqt+VJC1aaJkZ1nMaXOhO1omZ77Q64U6OWdnLjjIvFu8J3p682ieD2tMBohcJzqPDBStz+6ao3fuqjV3W+BV2VaHs4M5yBN3ui9e0aE3o403Eg4fjfHB49mYwGGMD46W9+RE44D5vXjorS1KnZeZ80Tzza2YNWKp+4HzQyOffKcn+iar7TeWkbC7VLe7M41WWc3xmRc6Y8vsf+zMhbuI9zan6oTM0X7nq67613oBZm1H+g==:946F"
            f"^FO0,256^GFA,01280,01280,00020,:Z64:eJxjYBgFowAKeIDYAE2MjYGhQoKB8QeamIcEA8M/BgGogA0/e1sFM4NkAwMze3/DB7BYmWTPsTfszECxxzwHHs4Ai6UZzjh2hg0oxrhxxoHEHrAYM1CMDZ+YIUSMDSjGAxbbLHGgECJWJ99/7A/zf8n////z//8PcWMNf/uxH4z/gGL/2P/fh7iFgbHhAJgEEgdgvoCIMYOYh2FizI0wMUaEGFgbG0z/KBhOAABcVT6I:B31C"

            f"^FT17,199^ACN,18,10^FD{left['barcode']}^FS"
            f"^FT119,126^ACN,17,10^FD{left['karat']}k^FS"
            f"^FT18,126^ACN,17,10^FD{left['weight']}^FS"
            f"^FT84,126^ACN,17,10^FD{left['factory']}^FS"
            f"^FT124,199^ACN,17,10^FD{left['size']}^FS"
            f"^FT135,230^ACN,10,7^FD{left['category']}^FS"
            f"^BY1.2,2.0,45^FT16,176^B3N,N,45,N,N^FD>;{left['barcode']}>^FS"

            f"^FT498,199^ACN,18,10^FD{right['barcode']}^FS"
            f"^FT605,126^ACN,17,10^FD{right['karat'] + 'k' if right['karat'] else ''}^FS"
            f"^FT496,126^ACN,17,10^FD{right['weight']}^FS"
            f"^FT571,126^ACN,17,10^FD{right['factory']}^FS"
            f"^FT610,199^ACN,17,10^FD{right['size']}^FS"
            f"^FT500,35^ACN,10,7^FD{right['category']}^FS"
            f"^BY1.2,2.0,45^FT499,176^B3N,N,45,N,N^FD>;{right['barcode']}>^FS"

            f"^PQ1,0,1,Y^XZ"
        )

    def print_items(self, items):
        if not items:
            raise ValueError(
                "There are no barcode items to print."
            )

        print_items = list(
            reversed(items)
        )

        labels = []

        for index in range(
            0,
            len(print_items),
            2,
        ):
            left = print_items[index]

            right = (
                print_items[index + 1]
                if index + 1
                < len(print_items)
                else None
            )

            labels.append(
                self.build_zpl_label(
                    left,
                    right,
                )
            )

        return self._send_raw(labels)

    def _send_raw(self, labels):
        """Raw ZPL to the Label printer (prototype mechanism: win32print RAW job)."""
        if self._send_raw_override is not None:
            return self._send_raw_override(self.printer_name, labels)
        try:
            import win32print

        except ImportError as exc:
            raise RuntimeError(
                "pywin32/win32print is not installed."
            ) from exc

        printer_handle = (
            win32print.OpenPrinter(
                self.printer_name
            )
        )

        try:
            for label in labels:
                doc_started = False
                page_started = False

                try:
                    win32print.StartDocPrinter(
                        printer_handle,
                        1,
                        (
                            "masGold Barcode",
                            None,
                            "RAW",
                        ),
                    )

                    doc_started = True

                    win32print.StartPagePrinter(
                        printer_handle
                    )

                    page_started = True

                    win32print.WritePrinter(
                        printer_handle,
                        label.encode("utf-8"),
                    )

                finally:
                    if page_started:
                        win32print.EndPagePrinter(
                            printer_handle
                        )

                    if doc_started:
                        win32print.EndDocPrinter(
                            printer_handle
                        )

        finally:
            win32print.ClosePrinter(
                printer_handle
            )

        return len(labels)


class BarcodePrintService:
    """
    Bulk barcode workflow + dated append-only print history.
    """

    def __init__(
        self,
        printer_name=None,
    ):
        self._printer = (
            PrototypeZplPrinter(
                printer_name=printer_name
            )
        )

    @staticmethod
    def _clean_ids(item_ids):
        cleaned = []
        seen = set()

        for value in item_ids or []:
            item_id = int(value)

            if (
                item_id <= 0
                or item_id in seen
            ):
                continue

            cleaned.append(item_id)
            seen.add(item_id)

        return cleaned

    @staticmethod
    def _pair_count(item_count):
        return int(
            ceil(item_count / 2)
        )

    @staticmethod
    def _validate_date_string(value):
        value = str(
            value or ""
        ).strip()

        if not value:
            return None

        try:
            datetime.strptime(
                value,
                "%Y-%m-%d",
            )

        except ValueError as exc:
            raise ValueError(
                "Date must be YYYY-MM-DD."
            ) from exc

        return value

    @staticmethod
    def _new_batch_code():
        return datetime.now().strftime(
            "BP-%Y%m%d-%H%M%S-%f"
        )

    @staticmethod
    def _refuse_without_barcode(items):
        """A piece marked بدون باركود (no active barcode) can never enter the print queue or consume a label."""
        missing = [str(item.get("inventory_item_id")) for item in items if not item.get("barcode_value")]
        if missing:
            raise ValueError(
                NO_BARCODE_REFUSAL + " (القطع: " + "، ".join(missing) + ")")

    def get_queue(self, item_ids):
        item_ids = self._clean_ids(
            item_ids
        )

        if not item_ids:
            raise ValueError(
                "No inventory items were selected."
            )

        with DatabaseTransaction() as transaction:
            repository = BarcodeRepository(
                transaction.connection
            )

            items = (
                repository
                .get_items_for_print(
                    item_ids
                )
            )

        if len(items) != len(item_ids):
            raise ValueError(
                "One or more selected items are no longer "
                "available in inventory."
            )

        self._refuse_without_barcode(items)

        existing_count = sum(
            1
            for item in items
            if item.get(
                "barcode_value"
            )
        )

        preview_items = list(
            reversed(items)
        )

        return {
            "items": preview_items,
            "item_count": len(items),
            "existing_barcode_count": existing_count,
            "missing_barcode_count": (
                len(items)
                - existing_count
            ),
            "label_count": self._pair_count(
                len(items)
            ),
            "has_odd_item": bool(
                len(items) % 2
            ),
        }

    def _ensure_barcodes(
        self,
        item_ids,
    ):
        item_ids = self._clean_ids(
            item_ids
        )

        newly_assigned_item_ids = set()

        with DatabaseTransaction() as transaction:
            repository = BarcodeRepository(
                transaction.connection
            )

            items = (
                repository
                .get_items_for_print(
                    item_ids
                )
            )

            if len(items) != len(item_ids):
                raise ValueError(
                    "One or more selected items are no longer "
                    "available in inventory."
                )

            # بدون باركود is never printable: a label is printed only for a piece that already has its stored
            # barcode (always the SAME one). Printing never allocates, assigns or changes a barcode.
            self._refuse_without_barcode(items)

            assigned_count = 0

        return (
            items,
            assigned_count,
            newly_assigned_item_ids,
        )

    def _create_print_job(
        self,
        items,
        newly_assigned_item_ids=None,
        reprint_of_job_id=None,
    ):
        physical_items = list(
            reversed(items)
        )

        batch_code = (
            self._new_batch_code()
        )

        with DatabaseTransaction() as transaction:
            repository = BarcodeRepository(
                transaction.connection
            )

            print_job_id = (
                repository.create_print_job(
                    batch_code=batch_code,
                    printer_name=(
                        self._printer.printer_name
                    ),
                    item_count=len(
                        physical_items
                    ),
                    physical_label_count=(
                        self._pair_count(
                            len(
                                physical_items
                            )
                        )
                    ),
                    reprint_of_job_id=(
                        reprint_of_job_id
                    ),
                    requested_by_user_id=None,
                )
            )

            newly_assigned_item_ids = (
                newly_assigned_item_ids
                or set()
            )

            for position, item in enumerate(
                physical_items,
                start=1,
            ):
                item_id = int(
                    item[
                        "inventory_item_id"
                    ]
                )

                repository.add_print_job_item(
                    print_job_id=print_job_id,
                    position_order=position,
                    inventory_item_id=item_id,
                    barcode_value=(
                        item[
                            "barcode_value"
                        ]
                    ),
                    was_new_barcode=(
                        item_id
                        in newly_assigned_item_ids
                    ),
                )

            repository.add_print_job_event(
                print_job_id=print_job_id,
                event_type="STARTED",
                message=None,
            )

        return (
            print_job_id,
            batch_code,
            physical_items,
        )

    def _append_print_event(
        self,
        print_job_id,
        event_type,
        message=None,
    ):
        with DatabaseTransaction() as transaction:
            repository = BarcodeRepository(
                transaction.connection
            )

            repository.add_print_job_event(
                print_job_id=print_job_id,
                event_type=event_type,
                message=message,
            )

    def print_items(self, item_ids):
        item_ids = self._clean_ids(
            item_ids
        )

        if not item_ids:
            raise ValueError(
                "No inventory items were selected."
            )

        (
            items,
            assigned_count,
            newly_assigned_item_ids,
        ) = self._ensure_barcodes(
            item_ids
        )

        (
            print_job_id,
            batch_code,
            _physical_items,
        ) = self._create_print_job(
            items=items,
            newly_assigned_item_ids=(
                newly_assigned_item_ids
            ),
            reprint_of_job_id=None,
        )

        try:
            label_count = (
                self._printer.print_items(
                    items
                )
            )

        except Exception as exc:
            self._append_print_event(
                print_job_id=print_job_id,
                event_type="FAILED",
                message=str(exc),
            )

            raise BarcodePrinterError(
                str(exc),
                assigned_count=assigned_count,
                batch_code=batch_code,
            ) from exc

        self._append_print_event(
            print_job_id=print_job_id,
            event_type="PRINTED",
            message=None,
        )

        return {
            "item_count": len(items),
            "new_barcode_count": assigned_count,
            "label_count": label_count,
            "batch_code": batch_code,
            "print_job_id": print_job_id,
        }

    def reprint_item_barcode(self, item_id):
        """إعادة طباعة الباركود of ONE identified piece with its SAME stored barcode, whatever its status (e.g. a
        returned piece held in Scrap). Never allocates, assigns or changes a barcode: a piece without one is
        refused. Logged as an ordinary print job and sent to the same printer."""
        with DatabaseTransaction() as transaction:
            item = BarcodeRepository(transaction.connection).get_item_for_label_reprint(item_id)
        if item is None:
            raise ValueError("Inventory item does not exist.")
        if not item.get("barcode_value"):
            raise ValueError("This item has no stored barcode.")
        print_job_id, batch_code, _ = self._create_print_job(items=[item], newly_assigned_item_ids=set(),
                                                             reprint_of_job_id=None)
        try:
            label_count = self._printer.print_items([item])
        except Exception as exc:
            self._append_print_event(print_job_id=print_job_id, event_type="FAILED", message=str(exc))
            raise BarcodePrinterError(str(exc), assigned_count=0, batch_code=batch_code) from exc
        self._append_print_event(print_job_id=print_job_id, event_type="PRINTED", message=None)
        return {"inventory_item_id": str(item["inventory_item_id"]), "barcode_value": item["barcode_value"],
                "label_count": label_count, "batch_code": batch_code, "print_job_id": print_job_id}

    # =========================================================
    # PRINT HISTORY
    # =========================================================

    def get_print_history(
        self,
        print_date=None,
    ):
        print_date = (
            self._validate_date_string(
                print_date
            )
        )

        with DatabaseTransaction() as transaction:
            repository = BarcodeRepository(
                transaction.connection
            )

            return (
                repository
                .get_print_history(
                    print_date=print_date,
                    limit=200,
                )
            )

    def get_print_job_items(
        self,
        print_job_id,
    ):
        with DatabaseTransaction() as transaction:
            repository = BarcodeRepository(
                transaction.connection
            )

            job = repository.get_print_job(
                print_job_id
            )

            if job is None:
                raise ValueError(
                    "Print batch does not exist."
                )

            items = (
                repository
                .get_print_job_items(
                    print_job_id
                )
            )

        return {
            "job": job,
            "items": items,
        }

    def reprint_job(
        self,
        print_job_id,
    ):
        print_job_id = int(
            print_job_id
        )

        if print_job_id <= 0:
            raise ValueError(
                "Invalid print batch."
            )

        with DatabaseTransaction() as transaction:
            repository = BarcodeRepository(
                transaction.connection
            )

            source_job = (
                repository.get_print_job(
                    print_job_id
                )
            )

            if source_job is None:
                raise ValueError(
                    "Print batch does not exist."
                )

            physical_items = (
                repository
                .get_print_job_items(
                    print_job_id
                )
            )

        if not physical_items:
            raise ValueError(
                "The selected print batch has no items."
            )

        # Safety: never reprint a barcode that is no longer
        # the ACTIVE barcode for that physical inventory item.
        for item in physical_items:
            expected = str(
                item.get(
                    "barcode_value"
                )
                or ""
            )

            active = str(
                item.get(
                    "active_barcode_value"
                )
                or ""
            )

            if (
                not expected
                or active != expected
            ):
                raise ValueError(
                    "One or more barcodes in this batch "
                    "are no longer active for their items. "
                    "Reprinting was stopped for safety."
                )

            if (
                item.get(
                    "inventory_status"
                )
                != "IN_STOCK"
            ):
                raise ValueError(
                    "One or more items in this batch "
                    "are no longer in stock. "
                    "Reprinting was stopped for safety."
                )

            # Printer expects barcode_value under this key.
            item["barcode_value"] = expected

        # physical_items are already stored in physical queue order.
        # PrototypeZplPrinter reverses internally, so reverse them once
        # here to reproduce the exact original left/right order.
        printer_input = list(
            reversed(
                physical_items
            )
        )

        (
            new_job_id,
            new_batch_code,
            _physical_items,
        ) = self._create_print_job(
            items=printer_input,
            newly_assigned_item_ids=set(),
            reprint_of_job_id=print_job_id,
        )

        try:
            label_count = (
                self._printer.print_items(
                    printer_input
                )
            )

        except Exception as exc:
            self._append_print_event(
                print_job_id=new_job_id,
                event_type="FAILED",
                message=str(exc),
            )

            raise BarcodePrinterError(
                str(exc),
                assigned_count=0,
                batch_code=new_batch_code,
            ) from exc

        self._append_print_event(
            print_job_id=new_job_id,
            event_type="PRINTED",
            message=(
                f"Reprint of "
                f"{source_job['batch_code']}"
            ),
        )

        return {
            "item_count": len(
                physical_items
            ),
            "label_count": label_count,
            "batch_code": new_batch_code,
            "print_job_id": new_job_id,
            "reprint_of_batch_code": (
                source_job[
                    "batch_code"
                ]
            ),
        }
