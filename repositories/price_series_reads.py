"""Read-only daily gold price series (the live quotes and the imported history of the old shop), shared by the report and the home-page repositories so both read ONE definition of a day's price."""


class PriceSeriesReads:
    def gold_price_daily(self):
        """The LAST accepted live quote of every day and karat (ONE statement): (karat, day, shop buy, shop sell)."""
        return self._fetch_all(
            """
            SELECT k.karat_value, DATE(q.provider_timestamp) AS day, q.shop_buy_price_per_gram AS buy, q.shop_sell_price_per_gram AS sell
            FROM gold_price_quotes q
            JOIN karats k ON k.id = q.karat_id
            JOIN (SELECT karat_id, DATE(provider_timestamp) AS d, MAX(id) AS last_id
                  FROM gold_price_quotes
                  WHERE validation_status = 'ACCEPTED' AND provider_timestamp IS NOT NULL AND shop_buy_price_per_gram IS NOT NULL AND shop_sell_price_per_gram IS NOT NULL
                  GROUP BY karat_id, DATE(provider_timestamp)) m ON m.last_id = q.id
            ORDER BY k.karat_value, day
            """)

    def gold_price_history_legacy(self):
        """The imported daily prices of the old shop (migration 066): (karat, day, shop buy, shop sell). Raises when the table does not exist (below 066)."""
        return self._fetch_all("SELECT karat_value, price_date AS day, shop_buy AS buy, shop_sell AS sell FROM gold_price_history_legacy ORDER BY karat_value, price_date")
