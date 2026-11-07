"""عهدة السوق والموردين on the iPad (owner request 2026-11-07): ONE overview read plus ONE whitelisted write endpoint over the EXISTING services.

Nothing here decides a business rule. Every write goes through the same service the desktop uses (`MarketTripService`, `SupplierPaymentService`, `SupplierGoldSettlementService`), which rechecks its own
right inside its transaction (MARKET_TRIP_MANAGE, SUPPLIER_MAKING_PAY, SUPPLIER_GOLD_SETTLE ...) with the actor taken from the verified session, never from the request. On top of that every write
re-verifies the user's CURRENT password (the shared `AuthenticationService`, so its failure cooldown spans attempts) and needs the client's request key (an exact retry replays, never posts twice).

Operations (the whitelist `OPERATIONS`):
  trip_create       a new custody from MAIN_SAFE cash and Scrap lines (no melt / logo choice: the owner decides after the trip)
  trip_return       return custody cash / gold to the shop
  trip_pay_supplier pay a supplier's making with cash already in an OPEN custody
  trip_expense      a free-text expense paid from the custody cash
  trip_close        close a settled custody
  sup_pay_cash      pay a supplier's making from the main safe
  sup_gold_settle   deliver shop Scrap to a supplier against its gold account
Melting, heating, lots, bars, sales and purchases of custody gold, marketer holdings and every reversal stay on the desktop."""
from decimal import Decimal
import logging

from services.authentication_service import AuthenticationError
from webapp.errors import ApiError

logger = logging.getLogger("webapp")
MAX_LINES = 8


def _str(value):
    return str(value) if value is not None else ""


def _dec(value):
    return str(Decimal(str(value))) if value is not None else None


def _money(value):
    return f"{Decimal(str(value)):,.2f}"


def _side(delta, text):
    """One sign convention of the supplier ledgers: delta > 0 = the shop owes the supplier (علينا للمورد), delta < 0 = the supplier owes the shop (لنا عند المورد)."""
    value = Decimal(str(delta))
    return text + (" علينا للمورد" if value > 0 else " لنا عند المورد" if value < 0 else " (متوازن)")


def _grams(value):
    return f"{Decimal(str(value)):,.3f}"


class Market:
    def __init__(self, api):
        self._api = api

    # ------------------------------------------------------------------ one read for the whole card
    def overview(self, token):
        from services.market_trip_service import OPEN, MarketTripService
        from services.supplier_service import SupplierService
        entry = self._api._entry(token)
        session, actor = entry.session, entry.session.current_actor()
        service = MarketTripService(session)
        try:
            context = service.context(_expected_actor=actor)
            page = service.trip_page(OPEN, 1, 30, _expected_actor=actor)
        except PermissionError as exc:
            raise ApiError(403, str(exc) or "عهدة السوق للمالك فقط.") from None
        except ValueError as exc:
            raise ApiError(400, str(exc)) from None
        except Exception:
            logger.exception("market overview failed")
            raise ApiError(500, "تعذر تحميل عهدة السوق؛ أعد المحاولة.") from None
        trips = []
        for row in page["rows"]:
            custody = row["custody"]
            trips.append({
                "id": row["id"], "code": row["code"], "notes": row["notes"], "settled": bool(custody["settled"]),
                "cash": _dec(custody["cash"]["outstanding"]), "cashText": _money(custody["cash"]["outstanding"]),
                "gold": [{"karatId": str(g["karat_id"]), "karat": g["karat_value"], "designation": g["designation"], "outstanding": _dec(g["outstanding"]),
                          "text": _grams(g["outstanding"])} for g in custody["gold"] if g["outstanding"] != 0],
                "bar": _grams(custody["bar"]) if custody["bar"] else "", "lots": len(custody["lots"]),
                "held": len(row.get("external_holdings") or [])})
        suppliers = []
        try:
            for s in SupplierService(session).list_suppliers("", False, None, _expected_actor=actor):
                if not s["is_supplier"] or not s["is_active"]:
                    continue
                suppliers.append({"id": s["id"], "name": s["name"], "karat": s["karat_value"], "karatId": None if s["karat_id"] is None else str(s["karat_id"]),
                                  "gold": None if s["gold_balance"] is None else _side(s["gold_balance"], _grams(abs(Decimal(str(s["gold_balance"]))))),
                                  "making": _money(abs(Decimal(str(s["making"])))), "makingSide": _side(s["making"], _money(abs(Decimal(str(s["making"])))))})
        except PermissionError:
            pass
        except Exception:
            logger.exception("market overview: suppliers failed")
        safe = context.get("safe")
        return {"safe": None if not safe else {"id": safe["id"], "name": safe["name"], "balance": _dec(safe["balance"]), "text": _money(safe["balance"])},
                "scrap": [{"karatId": str(r["karat_id"]), "karat": r["karat_value"], "available": _dec(r["available"]), "text": _grams(r["available"])} for r in context["shop_scrap"]],
                "karats": [{"id": str(k["id"]), "karat": k["karat_value"]} for k in context["karats"]],
                "trips": trips, "hasMore": bool(page["has_more"]), "suppliers": suppliers}

    # ------------------------------------------------------------------ the writes
    def op(self, token, body):
        from services.market_trip_service import MarketTripService
        from services.supplier_gold_settlement_service import SupplierGoldSettlementService
        from services.supplier_payment_service import SupplierPaymentService
        entry = self._api._entry(token)
        session, actor = entry.session, entry.session.current_actor()
        operation = _str(body.get("op"))
        if operation not in OPERATIONS:
            raise ApiError(400, "عملية غير معروفة.")
        text = lambda name: _str(body.get(name)).strip()
        key = text("request_key")
        if body.get("confirmed") is not True:
            raise ApiError(400, "أكّد العملية أولًا.")
        password = body.get("password")
        try:
            self._api._authentication.verify_user_password(int(actor.user_id), password if isinstance(password, str) else "")
        except AuthenticationError:
            raise ApiError(400, "كلمة المرور غير صحيحة أو انتظر قليلاً ثم أعد المحاولة.") from None
        finally:
            password = None
        today = self._api._today()
        trips, payments, settlements = MarketTripService(session), SupplierPaymentService(session), SupplierGoldSettlementService(session)
        gold = lambda: self._gold(body.get("gold"))
        try:
            if operation == "trip_create":
                result = trips.create_trip(text("cash") or "0", gold(), [], today, text("notes"), key, _expected_actor=actor)
            elif operation == "trip_return":
                result = trips.return_custody(text("trip_id"), text("cash") or "0", gold(), today, text("notes"), key, _expected_actor=actor)
            elif operation == "trip_pay_supplier":
                result = payments.pay(text("supplier_id"), "CUSTODY", text("trip_id"), text("amount"), today, "", text("notes"), key, _expected_actor=actor)
            elif operation == "trip_expense":
                result = trips.record_custody_expense(text("trip_id"), text("amount"), text("description"), today, "", text("notes"), key, _expected_actor=actor)
            elif operation == "trip_close":
                result = trips.close(text("trip_id"), _expected_actor=actor)
            elif operation == "sup_pay_cash":
                safe = trips.context(_expected_actor=actor).get("safe")
                if not safe or not safe.get("active"):
                    raise ApiError(400, "الخزنة الرئيسية غير متاحة.")
                result = payments.pay(text("supplier_id"), "CASH", safe["id"], text("amount"), today, "", text("notes"), key, _expected_actor=actor)
            else:                                                                                                         # sup_gold_settle
                result = settlements.settle(text("supplier_id"), text("karat_id"), text("weight"), today, "", text("notes"), key, _expected_actor=actor)
        except ApiError:
            raise
        except PermissionError as exc:
            raise ApiError(403, str(exc) or "ليس لديك صلاحية لهذه العملية.") from None
        except ValueError as exc:
            code = getattr(exc, "code", "")
            raise ApiError(409 if code in ("REQUEST_CONFLICT", "STOCK_CHANGED", "BALANCE_CHANGED") else 400, str(exc) or "بيانات غير صحيحة.") from None
        except Exception:
            logger.exception("market op %s failed", operation)
            raise ApiError(500, "تعذر تنفيذ العملية؛ راجع الأرصدة ثم أعد المحاولة.") from None
        return {k: (v if isinstance(v, (str, int, float, bool, type(None))) else str(v)) for k, v in (result or {}).items()
                if not isinstance(v, (list, dict))} | {"op": operation}

    @staticmethod
    def _gold(value):
        """[{karat_id, weight, designation?}] from the client, at most MAX_LINES, every field text; the service validates the rest."""
        if value in (None, ""):
            return []
        if not isinstance(value, list) or len(value) > MAX_LINES:
            raise ApiError(400, "بيانات الذهب غير صحيحة.")
        lines = []
        for item in value:
            if not isinstance(item, dict):
                raise ApiError(400, "بيانات الذهب غير صحيحة.")
            line = {"karat_id": _str(item.get("karat_id")).strip(), "weight": _str(item.get("weight")).strip()}
            if item.get("designation"):
                line["designation"] = _str(item.get("designation")).strip()
            if line["weight"] not in ("", "0"):
                lines.append(line)
        return lines


OPERATIONS = frozenset({"trip_create", "trip_return", "trip_pay_supplier", "trip_expense", "trip_close", "sup_pay_cash", "sup_gold_settle"})
