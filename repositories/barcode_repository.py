class BarcodeRepository:
    """
    Database access for barcode queue preparation, barcode assignment,
    and append-only barcode print-batch history.
    """

    def __init__(self, connection):
        self.connection = connection

    @staticmethod
    def _clean_ids(item_ids):
        cleaned = []
        seen = set()

        for value in item_ids or []:
            item_id = int(value)

            if item_id <= 0 or item_id in seen:
                continue

            cleaned.append(item_id)
            seen.add(item_id)

        return cleaned

    def get_items_for_print(self, item_ids):
        item_ids = self._clean_ids(item_ids)

        if not item_ids:
            return []

        placeholders = ", ".join(["%s"] * len(item_ids))

        sql = f"""
            SELECT
                ii.id AS inventory_item_id,
                ii.weight_grams,
                ii.size,
                ii.stock_classification,
                ii.inventory_status,
                ii.acquired_at,

                it.name AS item_type_name,

                k.karat_value,

                c.code AS category_code,

                f.code AS factory_code,
                f.name AS factory_name,

                ib.barcode_value,
                ib.assigned_at AS barcode_assigned_at

            FROM inventory_items ii

            JOIN item_types it
                ON it.id = ii.item_type_id

            JOIN karats k
                ON k.id = ii.karat_id

            LEFT JOIN item_categories c
                ON c.id = ii.category_id

            LEFT JOIN factories f
                ON f.id = ii.factory_id

            LEFT JOIN inventory_barcodes ib
                ON ib.inventory_item_id = ii.id
                AND ib.barcode_status = 'ACTIVE'

            WHERE
                ii.id IN ({placeholders})
                AND ii.inventory_status = 'IN_STOCK'
        """

        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(sql, tuple(item_ids))
            rows = cursor.fetchall()
        finally:
            cursor.close()

        by_id = {
            int(row["inventory_item_id"]): row
            for row in rows
        }

        return [
            by_id[item_id]
            for item_id in item_ids
            if item_id in by_id
        ]

    def get_active_barcode(self, inventory_item_id):
        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT
                    id,
                    inventory_item_id,
                    barcode_value,
                    barcode_status,
                    assigned_at
                FROM inventory_barcodes
                WHERE
                    inventory_item_id = %s
                    AND barcode_status = 'ACTIVE'
                LIMIT 1
                """,
                (int(inventory_item_id),),
            )

            return cursor.fetchone()

        finally:
            cursor.close()

    def get_max_sequence_for_date(self, date_part):
        """
        Preserve the prototype barcode style:
            01YYMMDD
            02YYMMDD
            ...

        Example:
            01260921
        """

        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                SELECT barcode_value
                FROM inventory_barcodes
                WHERE
                    barcode_status = 'ACTIVE'
                    AND barcode_value REGEXP '^[0-9]+$'
                    AND RIGHT(barcode_value, 6) = %s
                """,
                (date_part,),
            )

            maximum = 0

            for (barcode_value,) in cursor.fetchall():
                value = str(barcode_value or "")

                if len(value) <= 6:
                    continue

                prefix = value[:-6]

                if prefix.isdigit():
                    maximum = max(
                        maximum,
                        int(prefix),
                    )

            return maximum

        finally:
            cursor.close()

    def barcode_exists(self, barcode_value):
        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                SELECT 1
                FROM inventory_barcodes
                WHERE barcode_value = %s
                LIMIT 1
                """,
                (barcode_value,),
            )

            return cursor.fetchone() is not None

        finally:
            cursor.close()

    def assign_barcode(
        self,
        inventory_item_id,
        barcode_value,
        assigned_by_user_id=None,
        reason="BULK_BARCODE_PRINT",
    ):
        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO inventory_barcodes (
                    inventory_item_id,
                    barcode_value,
                    barcode_status,
                    assigned_by_user_id,
                    reason
                )
                VALUES (
                    %s,
                    %s,
                    'ACTIVE',
                    %s,
                    %s
                )
                """,
                (
                    int(inventory_item_id),
                    str(barcode_value),
                    assigned_by_user_id,
                    reason,
                ),
            )

            return int(cursor.lastrowid)

        finally:
            cursor.close()

    # =========================================================
    # PRINT JOB LEDGER
    # =========================================================

    def create_print_job(
        self,
        batch_code,
        printer_name,
        item_count,
        physical_label_count,
        reprint_of_job_id=None,
        requested_by_user_id=None,
    ):
        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO barcode_print_jobs (
                    batch_code,
                    printer_name,
                    item_count,
                    physical_label_count,
                    reprint_of_job_id,
                    requested_by_user_id
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    str(batch_code),
                    str(printer_name),
                    int(item_count),
                    int(physical_label_count),
                    (
                        int(reprint_of_job_id)
                        if reprint_of_job_id
                        else None
                    ),
                    requested_by_user_id,
                ),
            )

            return int(cursor.lastrowid)

        finally:
            cursor.close()

    def add_print_job_item(
        self,
        print_job_id,
        position_order,
        inventory_item_id,
        barcode_value,
        was_new_barcode=False,
    ):
        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO barcode_print_job_items (
                    print_job_id,
                    position_order,
                    inventory_item_id,
                    barcode_value,
                    was_new_barcode
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    int(print_job_id),
                    int(position_order),
                    int(inventory_item_id),
                    str(barcode_value),
                    bool(was_new_barcode),
                ),
            )

            return int(cursor.lastrowid)

        finally:
            cursor.close()

    def add_print_job_event(
        self,
        print_job_id,
        event_type,
        message=None,
    ):
        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO barcode_print_job_events (
                    print_job_id,
                    event_type,
                    message
                )
                VALUES (
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    int(print_job_id),
                    str(event_type),
                    (
                        str(message)
                        if message
                        else None
                    ),
                ),
            )

            return int(cursor.lastrowid)

        finally:
            cursor.close()

    def get_print_history(
        self,
        print_date=None,
        limit=200,
    ):
        where = ""
        params = []

        if print_date:
            where = """
                WHERE DATE(j.requested_at) = %s
            """
            params.append(str(print_date))

        params.append(int(limit))

        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(
                f"""
                SELECT
                    j.id,
                    j.batch_code,
                    j.printer_name,
                    j.item_count,
                    j.physical_label_count,
                    j.reprint_of_job_id,
                    j.requested_at,

                    e.event_type AS status,
                    e.message AS status_message,
                    e.event_at AS status_at,

                    original.batch_code
                        AS reprint_of_batch_code

                FROM barcode_print_jobs j

                LEFT JOIN barcode_print_jobs original
                    ON original.id = j.reprint_of_job_id

                LEFT JOIN barcode_print_job_events e
                    ON e.id = (
                        SELECT e2.id
                        FROM barcode_print_job_events e2
                        WHERE e2.print_job_id = j.id
                        ORDER BY e2.id DESC
                        LIMIT 1
                    )

                {where}

                ORDER BY
                    j.requested_at DESC,
                    j.id DESC

                LIMIT %s
                """,
                tuple(params),
            )

            return cursor.fetchall()

        finally:
            cursor.close()

    def get_print_job(self, print_job_id):
        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT
                    j.id,
                    j.batch_code,
                    j.printer_name,
                    j.item_count,
                    j.physical_label_count,
                    j.reprint_of_job_id,
                    j.requested_at,

                    e.event_type AS status,
                    e.message AS status_message,
                    e.event_at AS status_at

                FROM barcode_print_jobs j

                LEFT JOIN barcode_print_job_events e
                    ON e.id = (
                        SELECT e2.id
                        FROM barcode_print_job_events e2
                        WHERE e2.print_job_id = j.id
                        ORDER BY e2.id DESC
                        LIMIT 1
                    )

                WHERE j.id = %s
                """,
                (int(print_job_id),),
            )

            return cursor.fetchone()

        finally:
            cursor.close()

    def get_print_job_items(self, print_job_id):
        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT
                    pji.id,
                    pji.position_order,
                    pji.inventory_item_id,
                    pji.barcode_value,
                    pji.was_new_barcode,

                    ii.weight_grams,
                    ii.size,
                    ii.inventory_status,

                    it.name AS item_type_name,

                    k.karat_value,

                    c.code AS category_code,

                    f.code AS factory_code,
                    f.name AS factory_name,

                    active_barcode.barcode_value
                        AS active_barcode_value

                FROM barcode_print_job_items pji

                JOIN inventory_items ii
                    ON ii.id = pji.inventory_item_id

                JOIN item_types it
                    ON it.id = ii.item_type_id

                JOIN karats k
                    ON k.id = ii.karat_id

                LEFT JOIN item_categories c
                    ON c.id = ii.category_id

                LEFT JOIN factories f
                    ON f.id = ii.factory_id

                LEFT JOIN inventory_barcodes active_barcode
                    ON active_barcode.inventory_item_id
                        = ii.id
                    AND active_barcode.barcode_status
                        = 'ACTIVE'

                WHERE pji.print_job_id = %s

                ORDER BY
                    pji.position_order ASC,
                    pji.id ASC
                """,
                (int(print_job_id),),
            )

            return cursor.fetchall()

        finally:
            cursor.close()
