"""Gold price persistence; publication writes always use the caller's transaction.

Existing quote and activation rows are append-only. Only the three active
pointers and the engine settings row may be updated by this repository.
All price timestamps written here are UTC, including on a Billing connection
whose session timezone is owned by its caller.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import json
import re
from uuid import uuid4

from mysql.connector.connection import MySQLConnection

from config.database_config import DatabaseConfig
from repositories.base_repository import BaseRepository


@contextmanager
def gold_price_transaction():
    """A worker-owned connection with bounded connection, I/O and lock waits."""
    config = DatabaseConfig.from_env()
    connection = MySQLConnection(
        host=config.host, port=config.port, database=config.database,
        user=config.user, password=config.password, autocommit=False,
        connection_timeout=5, read_timeout=5, write_timeout=5,
    )
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SET SESSION time_zone = '+00:00'")
            cursor.execute("SET SESSION innodb_lock_wait_timeout = 5")
        finally:
            cursor.close()
        connection.start_transaction(isolation_level="READ COMMITTED")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _utc(value):
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Price timestamps must include an explicit timezone.")
    # Existing columns use DATETIME without fractional seconds.
    return value.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Unsupported audit value: {type(value).__name__}")


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      default=_json_default)


class GoldPriceRepository(BaseRepository):
    def get_provider_options(self):
        row = self._fetch_one("SELECT notes FROM gold_price_sources WHERE code = 'GOLDTIMO'")
        try:
            data = json.loads(row["notes"] or "{}") if row else {}
            return data.get("connection", {}) if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}

    def save_provider_settings(self, options, refresh, stale, actor, occurred_at):
        before = self.get_settings(lock=True)
        if before is None:
            raise ValueError("إعدادات محرك الذهب غير موجودة.")
        previous_options = self.get_provider_options()
        sources = self.get_sources()
        source = sources.get("GOLDTIMO")
        notes = _json({"connection": options})
        if source:
            source_id = source["id"]
            self._execute("UPDATE gold_price_sources SET notes=%s, is_enabled=1 WHERE id=%s", (notes, source_id))
        else:
            source_id = self._execute(
                "INSERT INTO gold_price_sources (code,name,source_type,priority,is_enabled,notes) "
                "VALUES ('GOLDTIMO','GoldTimo Egypt','LOCAL_MARKET',10,1,%s)", (notes,))
        self._execute("UPDATE gold_price_engine_settings SET primary_source_id=%s, "
                      "refresh_seconds=%s, stale_after_seconds=%s, updated_by_user_id=%s, "
                      "updated_at=%s WHERE id=1", (source_id, refresh, stale, actor.user_id, _utc(occurred_at)))
        self._audit("GOLD_PRICE_CONFIG_CHANGED", actor=actor, reference="GOLDTIMO",
                    description="تحديث إعدادات مصدر الذهب.", occurred_at=occurred_at,
                    before={"settings": before, "connection": previous_options},
                    after={"settings": self.get_settings(), "connection": options}, metadata={})

    def _execute(self, sql, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            return cursor.lastrowid
        finally:
            cursor.close()

    def get_settings(self, lock=False):
        return self._fetch_one(
            "SELECT id, mode, primary_source_id, fallback_source_id, "
            "reference_source_id, refresh_seconds, stale_after_seconds, "
            "max_deviation_percent, updated_by_user_id, updated_at "
            "FROM gold_price_engine_settings WHERE id = 1"
            + (" FOR UPDATE" if lock else ""))

    def get_sources(self):
        rows = self._fetch_all(
            "SELECT id, code, name, source_type, priority, is_enabled "
            "FROM gold_price_sources ORDER BY priority, id")
        return {row["code"]: row for row in rows}

    def get_karats(self):
        rows = self._fetch_all(
            "SELECT id, karat_value FROM karats WHERE is_active = TRUE "
            "AND karat_value IN (18, 21, 22, 24) ORDER BY karat_value")
        return {int(row["karat_value"]): int(row["id"]) for row in rows}

    def get_active(self, lock=False):
        return self._fetch_all(
            "SELECT p.karat_id, p.quote_id, p.activation_id, k.karat_value, "
            "q.quote_group_id, q.source_id, s.code AS source_code, "
            "s.name AS source_name, s.source_type, "
            "q.shop_buy_price_per_gram, q.shop_sell_price_per_gram, "
            "q.reference_price_per_gram, q.provider_timestamp, q.fetched_at, "
            "q.quote_method, q.validation_status, q.deviation_percent, "
            "q.validation_message, q.entered_by_user_id, q.notes, q.derived_from_quote_id, "
            "a.activated_at, a.activation_type, a.activated_by_user_id, a.reason "
            "FROM active_gold_prices p "
            "JOIN gold_price_quotes q ON q.id = p.quote_id AND q.karat_id = p.karat_id "
            "JOIN gold_price_activations a ON a.id = p.activation_id "
            "AND a.quote_id = p.quote_id AND a.karat_id = p.karat_id "
            "JOIN karats k ON k.id = p.karat_id "
            "JOIN gold_price_sources s ON s.id = q.source_id "
            "WHERE k.karat_value IN (18, 21, 22, 24) "
            "ORDER BY k.karat_value" + (" FOR UPDATE" if lock else ""))

    def latest_source_quotes(self, source_id):
        return self._fetch_all(
            "SELECT q.id AS quote_id, q.quote_group_id, q.source_id, "
            "q.karat_id, k.karat_value, q.shop_buy_price_per_gram, "
            "q.shop_sell_price_per_gram, q.provider_timestamp, q.fetched_at "
            "FROM gold_price_quotes q JOIN karats k ON k.id = q.karat_id "
            "WHERE q.source_id = %s AND q.validation_status = 'ACCEPTED' "
            "AND q.quote_group_id = (SELECT recent.quote_group_id "
            "FROM gold_price_quotes recent WHERE recent.source_id = %s "
            "AND recent.validation_status = 'ACCEPTED' "
            "ORDER BY recent.id DESC LIMIT 1) ORDER BY k.karat_value",
            (source_id, source_id))

    def _audit(self, event_type, *, actor, reference, description, occurred_at,
               metadata, before=None, after=None):
        return self._execute(
            "INSERT INTO audit_log (user_id, event_type, entity_type, "
            "entity_id, entity_reference, description, before_json, after_json, "
            "metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s)",
            (actor.user_id if actor else None, event_type, "GOLD_PRICE_ENGINE",
             1, reference, description, _json(before) if before is not None else None,
             _json(after) if after is not None else None, _json(metadata),
             _utc(occurred_at)))

    def append_publication(self, quotes, *, karat_ids, source_id,
                           activation_type, actor, reason, activated_at,
                           derivation=None):
        """Append and publish one complete set; caller holds the settings lock."""
        quotes = tuple(quotes)
        published = {q.karat for q in quotes}
        if (published not in ({18, 21, 24}, {18, 21, 22, 24}) or len(quotes) != len(published)
                or not published <= set(karat_ids)
                or len({karat_ids[k] for k in published}) != len(published)
                or len({q.source_code for q in quotes}) != 1):
            raise ValueError("A publication requires one coherent 18K/21K/24K set (plus a derived 22K).")
        if activation_type not in {"AUTO", "MANUAL", "FALLBACK", "RESTORE"}:
            raise ValueError("Unsupported gold price activation type.")
        if activation_type == "MANUAL" and (actor is None or not reason or not reason.strip()):
            raise ValueError("Manual publication requires an authenticated actor and reason.")
        if reason is not None and len(reason) > 500:
            raise ValueError("Gold price activation reason exceeds 500 characters.")
        activated = _utc(activated_at)
        group_id = str(uuid4())
        publication_rows = []
        timestamps = [q.provider_timestamp for q in quotes]
        skew = (max(timestamps) - min(timestamps)).total_seconds()
        source_quote_id = None                               # the exact 24K quote the derived 22K quote points to
        for quote in sorted(quotes, key=lambda item: (item.karat == 22, item.karat)):          # 24K is inserted before 22K
            derived22 = quote.karat == 22
            method = "DERIVED" if derived22 else (("DERIVED" if derivation and quote.karat != 21 else "MANUAL"
                                                  ) if activation_type == "MANUAL" else "DIRECT")
            notes = _json({
                "currency": quote.currency, "unit": quote.unit,
                "employee_id": actor.employee_id if actor else None,
                "derivation": ({"method": "KARAT_RATIO_FROM_24K", "base_karat": 24, "formula": "price(22K) = price(24K) * 22 / 24",
                                "rounding": "ROUND_HALF_UP", "decimal_places": 4, "source_quote_id": source_quote_id}
                               if derived22 else derivation),
                "refresh_attempt_id": getattr(quote, "refresh_attempt_id", None),
                "timestamp_skew_seconds": skew,
            })
            if len(notes) > 500:
                raise ValueError("Gold price provenance exceeds the quote notes capacity.")
            quote_id = self._execute(
                "INSERT INTO gold_price_quotes (quote_group_id, source_id, "
                "karat_id, shop_buy_price_per_gram, shop_sell_price_per_gram, "
                "quote_method, provider_timestamp, fetched_at, validation_status, "
                "entered_by_user_id, notes, derived_from_quote_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'ACCEPTED', %s, %s, %s)",
                (group_id, source_id, karat_ids[quote.karat],
                 quote.shop_buy_price_per_gram, quote.shop_sell_price_per_gram,
                 method, _utc(quote.provider_timestamp), _utc(quote.fetched_at),
                 actor.user_id if actor else None, notes, source_quote_id if derived22 else None))
            if quote.karat == 24:
                source_quote_id = quote_id
            activation_id = self._execute(
                "INSERT INTO gold_price_activations (karat_id, quote_id, "
                "activation_type, activated_at, activated_by_user_id, reason) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (karat_ids[quote.karat], quote_id, activation_type, activated,
                 actor.user_id if actor else None, reason))
            publication_rows.append({
                "karat": quote.karat, "karat_id": karat_ids[quote.karat],
                "quote_id": quote_id, "activation_id": activation_id,
                "quote_method": method, "derived_from_quote_id": source_quote_id if derived22 else None,
                "shop_buy_price_per_gram": quote.shop_buy_price_per_gram,
                "shop_sell_price_per_gram": quote.shop_sell_price_per_gram,
                "provider_timestamp": quote.provider_timestamp,
                "fetched_at": quote.fetched_at,
            })
        for row in publication_rows:
            self._execute(
                "INSERT INTO active_gold_prices (karat_id, quote_id, activation_id, "
                "updated_at) VALUES (%s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE quote_id = %s, activation_id = %s, updated_at = %s",
                (row["karat_id"], row["quote_id"], row["activation_id"], activated,
                 row["quote_id"], row["activation_id"], activated))
        self._audit(
            "GOLD_PRICE_ACTIVATED", actor=actor, reference=group_id,
            description=reason or "تحديث أسعار الذهب التلقائي.", occurred_at=activated_at,
            metadata={
                "quote_group_id": group_id, "source_id": source_id,
                "source_code": quotes[0].source_code,
                "currency": quotes[0].currency, "unit": quotes[0].unit,
                "activation_type": activation_type, "activated_at": activated_at,
                "user_id": actor.user_id if actor else None,
                "employee_id": actor.employee_id if actor else None,
                "reason": reason, "derivation": derivation, "quotes": publication_rows,
                "timestamp_skew_seconds": skew,
            })
        return group_id

    def set_mode(self, mode, actor, reason, occurred_at):
        if mode not in {"AUTO", "MANUAL"} or actor is None:
            raise ValueError("An authenticated actor and a valid price mode are required.")
        settings = self.get_settings(lock=True)
        if settings is None:
            raise ValueError("Gold price engine settings are missing.")
        self._execute(
            "UPDATE gold_price_engine_settings SET mode = %s, updated_by_user_id = %s, "
            "updated_at = %s WHERE id = 1", (mode, actor.user_id, _utc(occurred_at)))
        self._audit(
            "GOLD_PRICE_MODE_CHANGED", actor=actor, reference="settings:1",
            description=reason or "تغيير وضع أسعار الذهب.", occurred_at=occurred_at,
            before={"mode": settings["mode"]}, after={"mode": mode},
            metadata={"user_id": actor.user_id, "employee_id": actor.employee_id,
                      "reason": reason})

    def append_rejection(self, source_code, message, occurred_at):
        """Record a controlled validation error, never a raw response or credentials."""
        # URLs and common credential-bearing fields must not leak through an error.
        message = re.sub(r"https?://\S+", "[URL omitted]", str(message))
        message = re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer [redacted]", message)
        message = re.sub(
            r"(?i)([\"']?(?:api[_ -]?key|access[_ -]?token|authorization|password|secret)"
            r"[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
            r"\1[redacted]", message)[:500]
        source_code = re.sub(r"[^A-Za-z0-9_:-]", "", str(source_code))[:50]
        self._audit(
            "GOLD_PRICE_REJECTED", actor=None, reference=source_code,
            description="تعذر اعتماد تحديث أسعار الذهب.", occurred_at=occurred_at,
            metadata={"source_code": source_code, "validation_message": message})
