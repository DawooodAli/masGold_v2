"""The other cards of the iPad app: today's figures, a customer's statement, a stock piece lookup, the open manager reviews (all READ ONLY), a CSV download, and the transfers card (the second
write of the app, after the Dead Cash write-off).

The old (paper ledger) balance of a customer is the third write: it puts a balance on the customer's account only (no cash, bank or wallet), OWNER only, with the current password.

Transfers move real money between BANK / wallet accounts and to / from the owner's private account through the existing `FinancialAccountService.transfer` / `private_transfer`: the user's CURRENT
password is re-verified first (the shared `AuthenticationService`, its failure cooldown), FINANCIAL_ACCOUNT_OPERATE (and PRIVATE_ACCOUNT_MANAGE for the private account) is rechecked by the service in its
transaction, only active bank / wallet accounts that the service itself listed are accepted (never the main safe), and the client request key makes a retry idempotent."""
from decimal import Decimal
import logging
from pathlib import Path
import shutil
import tempfile

from app.formatting import money
from app.viewmodels.customer_account_viewmodel import CustomerAccountViewModel, balance_text
from app.viewmodels.inventory_viewmodel import STATUS_TEXT
from app.viewmodels.reports_viewmodel import _cell
from services.audit_view_service import AuditViewService
from services.authentication_service import AuthenticationError
from services.correction_service import CorrectionError
from services.customer_account_service import CustomerAccountService
from services.financial_account_service import FinancialAccountService
from services.gold_position_service import GoldPositionService
from services.gold_price_service import GoldPriceService
from services.inventory_service import InventoryService
from services.invoice_service import PaymentInput
from services.management_review_service import InspectionError, ManagementReviewService
from webapp.errors import ApiError

logger = logging.getLogger("webapp")
MAX_TEXT = 100
SALES_CARDS = ("قيمة البيع", "قيمة شراء الكسر", "قيمة المرتجع", "المحصَّل")
SCRAP_KARATS = (18, 21, 22, 24)
SIMPLE_REVIEWS = ("MAKING_PRICE", "CASH_SETTLEMENT", "OLD_GOLD_DISCOUNT")                 # the reviews a manager closes with «تمت المراجعة»
REVIEW_TITLES = (("MAKING_PRICE", "مراجعة سعر ومصنعية"), ("CASH_SETTLEMENT", "تسوية فرق نقدي"), ("OLD_GOLD_DISCOUNT", "خصم كسر"),
                 ("INVENTORY_MATCH", "قطع يدوية تحتاج مطابقة بالمخزون"), ("INSPECTION", "فحص بضاعة مرتجعة"), ("RETURN_APPROVAL", "موافقات مرتجع بعد 72 ساعة"))


class Extras:
    def __init__(self, api):
        self._api = api

    # ------------------------------------------------------------------ today's figures (each part is left out when the user may not see it)
    def today(self, token):
        entry = self._api._entry(token)
        session, actor = entry.session, entry.session.current_actor()
        today = self._api._today().isoformat()
        cards, prices = [], []
        try:
            sales = self._api.report(token, "sales", {"from": today, "to": today})
            by_label = {t["label"]: t for t in sales["totals"]}
            for label in SALES_CARDS:
                if label in by_label:
                    cards.append({"label": label + " اليوم", "value": by_label[label]["value"], "note": "", "tone": "receivable" if label == "قيمة البيع" else ""})
        except ApiError:
            pass
        try:
            accounts = FinancialAccountService(session).accounts(_expected_actor=actor)["accounts"]
            for ledger, label in (("CASH", "رصيد الخزنة"), ("BANK", "رصيد البنوك والمحافظ")):
                rows = [a for a in accounts if a["ledger"] == ledger and a["is_active"]]
                if rows:
                    cards.append({"label": label, "value": _cell("money", sum((Decimal(a["balance"]) for a in rows), Decimal("0"))), "note": f"{len(rows)} حساب", "tone": ""})
        except PermissionError:
            pass
        except Exception:
            logger.exception("today: balances failed")
        stock_cards = []
        try:
            position = GoldPositionService(session).position(_expected_actor=actor)
            summary = position["summary"]
            stock_cards.append({"label": "الذهب في المحل (مكافئ 21)", "value": f"{Decimal(str(summary['total_eq21'])):,.3f} جرام", "note": "مخزون وكسر وعهدة ومسوقجية", "tone": "gold"})
            by_karat = {int(r["karat"]): r for r in position.get("shop", {}).get("scrap", [])}
            for karat in SCRAP_KARATS:
                row = by_karat.get(karat)
                grams = Decimal(str(row["physical"])) if row else Decimal("0")
                cards.append({"label": f"كسر عيار {karat}", "value": f"{grams:,.3f} جرام", "note": "", "tone": "gold"})
        except PermissionError:
            pass
        except Exception:
            logger.exception("today: gold position failed")
        status = ""
        try:
            snapshot = GoldPriceService(session).snapshot()
            status = snapshot.get("status", "")
            for p in snapshot.get("prices", []):
                if str(p["karat"]) in ("18", "21", "24"):
                    prices.append({"karat": str(p["karat"]), "buy": f"{Decimal(p['shop_buy_price_per_gram']):,.0f}", "sell": f"{Decimal(p['shop_sell_price_per_gram']):,.0f}",
                                   "buyRaw": str(Decimal(p['shop_buy_price_per_gram'])), "sellRaw": str(Decimal(p['shop_sell_price_per_gram']))})
        except Exception:
            logger.exception("today: prices failed")
        return {"date": today, "cards": cards, "stockCards": stock_cards, "prices": prices, "priceStatus": status}

    # ------------------------------------------------------------------ a customer's statement (كشف الحساب), read only
    def statement(self, token, params):
        entry = self._api._entry(token)
        text = lambda name: str(params.get(name) or "")[:MAX_TEXT]
        page = text("page")
        try:
            result = CustomerAccountService(entry.session).statement(text("customer"), text("from"), text("to"), "", text("text"), int(page) if page.isdigit() and int(page) > 0 else 1,
                                                                     _expected_actor=entry.session.current_actor())
        except PermissionError as exc:
            raise ApiError(403, str(exc) or "ليس لديك صلاحية عرض الحساب.") from None
        except ValueError as exc:
            raise ApiError(400, str(exc) or "بيانات غير صحيحة.") from None
        except Exception:
            logger.exception("statement failed")
            raise ApiError(500, "تعذر تحميل كشف الحساب؛ أعد المحاولة.") from None
        vm = CustomerAccountViewModel(entry.session)
        vm._data = result
        keep = ("key", "date", "source", "reference", "invoiceId", "invoiceNumber", "amount", "balance", "description", "actor", "notes", "payments")
        rows = [{k: str(r.get(k) or "") for k in keep} for r in vm.rows]
        customer = result["customer"]
        return {"customer": {"id": text("customer"), "name": customer.get("name") or "", "code": customer.get("customer_code") or "", "mobile": customer.get("mobile") or "",
                             "balance": balance_text(customer.get("balance") or 0)},
                "rows": rows, "page": int(result.get("page") or 1), "hasMore": bool(result.get("has_more")),
                "settle": self._settle_info(result)}

    @staticmethod
    def _settle_info(result):
        """What the settlement form needs: the side of the balance, the exact amount and the real accounts (cash, or an active bank / wallet account)."""
        balance = Decimal(str(result["customer"].get("balance") or 0))
        if not result.get("can_settle") or not balance:
            return {"can": False}
        return {"can": True, "direction": "IN" if balance > 0 else "OUT", "directionText": "نستلم من العميل" if balance > 0 else "ندفع للعميل",
                "amount": str(abs(balance)), "amountText": _cell("money", abs(balance)),
                "accounts": [{"id": str(a["id"]), "name": a["name"]} for a in result.get("accounts") or []]}

    def settle(self, token, body):
        """تسوية حساب عميل (CSxxxxxx) from the iPad: ONE real payment leg (cash or one bank / wallet account) against the balance; the current password is re-verified, the right is rechecked by the
        service in its transaction, the oversettlement and direction rules are the service's, and the client request key makes a retry idempotent."""
        entry = self._api._entry(token)
        actor = entry.session.current_actor()
        text = lambda name: str(body.get(name) if body.get(name) is not None else "")
        password = body.get("password")
        if "CUSTOMER_ACCOUNT_SETTLE" not in actor.permissions:
            raise ApiError(403, "ليس لديك صلاحية تسوية الحساب.")
        try:
            self._api._authentication.verify_user_password(int(actor.user_id), password if isinstance(password, str) else "")
        except AuthenticationError:
            raise ApiError(400, "كلمة المرور غير صحيحة أو انتظر قليلاً ثم أعد المحاولة.") from None
        finally:
            password = None
        service = CustomerAccountService(entry.session)
        try:
            current = service.statement(text("customer"), "", "", "", "", 1, _expected_actor=actor)
            info = self._settle_info(current)
            if not info["can"]:
                raise ApiError(409, "الحساب متوازن أو لا يمكنك تسويته.")
            account = text("account")
            if account:
                chosen = next((a for a in current.get("accounts") or [] if str(a["id"]) == account), None)
                if chosen is None:
                    raise ApiError(400, "اختر حسابًا صحيحًا.")
                method = "MOBILE_WALLET" if chosen.get("account_type") == "MOBILE_WALLET" else "BANK_TRANSFER"
                leg = PaymentInput(method, text("amount"), info["direction"], None, account)
            else:
                leg = PaymentInput("PHYSICAL_CASH", text("amount"), info["direction"], None, None)
            result = service.finalize(text("customer"), (leg,), text("request_key"), text("note"), _expected_actor=actor)
        except ApiError:
            raise
        except PermissionError as exc:
            raise ApiError(403, str(exc)) from None
        except CorrectionError as exc:
            raise ApiError(409 if getattr(exc, "code", "") == "REQUEST_CONFLICT" else 400, str(exc)) from None
        except ValueError as exc:
            raise ApiError(400, str(exc) or "بيانات غير صحيحة.") from None
        except Exception:
            logger.exception("customer settlement failed")
            raise ApiError(500, "تعذرت التسوية؛ راجع حساب العميل ثم أعد المحاولة.") from None
        return {"code": result["code"], "balanceAfter": balance_text(result["balance_after"])}

    # ------------------------------------------------------------------ what needs attention now (also polled by the open page), read only
    def attention(self, token):
        entry = self._api._entry(token)
        session, actor = entry.session, entry.session.current_actor()
        items, reviews = [], 0
        try:
            data = ManagementReviewService(session).open_reviews(_expected_actor=actor)
            reviews = sum(len(rows or []) for rows in data.values())
            if reviews:
                items.append({"label": "مراجعات بانتظارك", "value": str(reviews), "module": "reviews", "tone": "payable"})
        except PermissionError:
            pass
        except Exception:
            logger.exception("attention: reviews failed")
        for key, label, module, tone, wanted in (("alerts", "تحت الحد الأدنى للمخزون", "inventory", "payable", "تحت الحد الأدنى"),
                                                 ("customers", "ديون العملاء", "customers", "receivable", "إجمالي اللي عليهم")):
            try:
                built = self._api.report(token, key, {})
                total = next((t for t in built["totals"] if t["label"].startswith(wanted)), None)
                if total and str(total["value"]).strip() not in ("", "0", "0.00", "٠"):
                    items.append({"label": label, "value": str(total["value"]), "module": module, "tone": tone})
            except ApiError:
                pass
            except Exception:
                logger.exception("attention: %s failed", key)
        return {"items": items, "reviews": reviews}

    # ------------------------------------------------------------------ the recent activity (audit_log), owner only, read only
    def activity(self, token, params):
        entry = self._api._entry(token)
        try:
            rows = AuditViewService(entry.session).recent(str(params.get("text") or ""), _expected_actor=entry.session.current_actor())
        except PermissionError as exc:
            raise ApiError(403, str(exc) or "سجل النشاط للمالك فقط.") from None
        except Exception:
            logger.exception("activity failed")
            raise ApiError(500, "تعذر تحميل سجل النشاط؛ أعد المحاولة.") from None
        return {"rows": [{"date": str(r["occurred_at"])[:16], "user": r.get("user_name") or "—", "event": r["event_type"], "reference": r.get("entity_reference") or "",
                          "text": r.get("description") or ""} for r in rows]}


    # ------------------------------------------------------------------ a stock piece by barcode or item number (any status), read only
    def piece(self, token, params):
        entry = self._api._entry(token)
        actor = entry.session.current_actor()
        if "INVENTORY_VIEW" not in actor.permissions:
            raise ApiError(403, "ليس لديك صلاحية عرض المخزون.")
        text = str(params.get("text") or "").strip()[:MAX_TEXT]
        if not text:
            return {"found": False, "piece": None}
        try:
            found = InventoryService(session=entry.session).find_piece(text)
        except Exception:
            logger.exception("piece lookup failed")
            raise ApiError(500, "تعذر البحث؛ أعد المحاولة.") from None
        if not found:
            return {"found": False, "piece": None}
        sale = found.get("sale") or {}
        weight = Decimal(str(found.get("weight_grams") or 0)).quantize(Decimal("0.001"))
        return {"found": True, "piece": {
            "number": f"#{found['inventory_item_id']}", "barcode": found.get("barcode_value") or "—", "itemType": found.get("item_type_name") or "—", "karat": str(found.get("karat_value") or ""),
            "weight": f"{weight:,.3f} جرام", "factory": found.get("factory_name") or found.get("factory_code") or "—", "category": found.get("category_code") or "—", "size": found.get("size") or "—",
            "status": STATUS_TEXT.get(found.get("inventory_status"), found.get("inventory_status") or ""), "statusCode": found.get("inventory_status") or "",
            "invoiceId": str(sale["invoice_id"]) if sale.get("invoice_id") is not None else "", "invoiceNumber": sale.get("invoice_number") or "",
            "invoiceDate": str(sale.get("invoice_date") or ""), "customer": sale.get("customer_name") or ""}}

    # ------------------------------------------------------------------ the open manager reviews (read only: the approvals stay on the desktop)
    def reviews(self, token):
        entry = self._api._entry(token)
        service, actor = ManagementReviewService(entry.session), entry.session.current_actor()
        try:
            data = service.open_reviews(_expected_actor=actor)
        except PermissionError as exc:
            raise ApiError(403, str(exc) or "ليس لديك صلاحية المراجعات.") from None
        except Exception:
            logger.exception("reviews failed")
            raise ApiError(500, "تعذر تحميل المراجعات؛ أعد المحاولة.") from None
        can_resolve = service.can_resolve(actor)
        sections, total = [], 0
        for key, title in REVIEW_TITLES:
            rows = data.get(key) or []
            total += len(rows)
            if not rows:
                continue
            shown = []
            for r in rows[:50]:
                shown.append({"text": str(r.get("summary") or r.get("reason") or r.get("notes") or "—"), "invoiceNumber": str(r.get("invoice_number") or ""),
                              "invoiceId": str(r.get("invoice_id") or ""), "date": str(r.get("created_at") or r.get("sold_at") or ""), "by": str(r.get("created_by_name") or ""),
                              "reviewId": str(r.get("review_id")) if key in SIMPLE_REVIEWS and can_resolve and r.get("review_id") is not None else ""})
            sections.append({"key": key, "title": title, "count": len(rows), "rows": shown})
        return {"total": total, "sections": sections}

    def review_done(self, token, body):
        """«تمت المراجعة» on a pricing / cash-settlement / discount review: the current password is re-verified, MANAGEMENT_REVIEW is rechecked by the service in its transaction."""
        entry = self._api._entry(token)
        actor = entry.session.current_actor()
        password = body.get("password")
        if "MANAGEMENT_REVIEW" not in actor.permissions:
            raise ApiError(403, "تأكيد المراجعة للمدير فقط.")
        try:
            self._api._authentication.verify_user_password(int(actor.user_id), password if isinstance(password, str) else "")
        except AuthenticationError:
            raise ApiError(400, "كلمة المرور غير صحيحة أو انتظر قليلاً ثم أعد المحاولة.") from None
        finally:
            password = None
        try:
            ManagementReviewService(entry.session).mark_reviewed(str(body.get("review_id") or ""), _expected_actor=actor)
        except PermissionError as exc:
            raise ApiError(403, str(exc)) from None
        except InspectionError as exc:
            raise ApiError(409, str(exc)) from None
        except Exception:
            logger.exception("review done failed")
            raise ApiError(500, "تعذر تسجيل المراجعة؛ حدّث الصفحة ثم أعد المحاولة.") from None
        return {"ok": True}

    # ------------------------------------------------------------------ CSV of a report (every row of the current filters), read only
    def export(self, token, params):
        entry = self._api._entry(token)
        key = str(params.get("key") or "")
        filters = self._api._filters(key, params)
        folder = tempfile.mkdtemp(prefix="masgold-export-")
        try:
            path = entry.service.export_csv(key, filters, directory=folder, _expected_actor=entry.session.current_actor())
            data = Path(path).read_bytes()
        except PermissionError as exc:
            raise ApiError(403, str(exc) or "ليس لديك صلاحية.") from None
        except ValueError as exc:
            raise ApiError(400, str(exc)) from None
        except Exception:
            logger.exception("export failed")
            raise ApiError(500, "تعذر تصدير التقرير؛ أعد المحاولة.") from None
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        return f"{key}-{filters.get('from', '')}-{filters.get('to', '')}.csv".strip("-"), data

    # ------------------------------------------------------------------ an OLD (paper ledger) balance on a customer's account: no cash / bank movement
    def legacy_balance(self, token, body):
        entry = self._api._entry(token)
        actor = entry.session.current_actor()
        text = lambda name: str(body.get(name) if body.get(name) is not None else "")
        password = body.get("password")
        if "CUSTOMER_BALANCE_ADJUST" not in actor.permissions:
            raise ApiError(403, "إضافة رصيد قديم للمالك فقط.")
        try:
            self._api._authentication.verify_user_password(int(actor.user_id), password if isinstance(password, str) else "")
        except AuthenticationError:
            raise ApiError(400, "كلمة المرور غير صحيحة أو انتظر قليلاً ثم أعد المحاولة.") from None
        try:
            result = CustomerAccountService(entry.session).add_legacy_balance(text("customer"), text("direction"), text("amount"), text("note"), text("old_date"), text("request_key"),
                                                                              _expected_actor=actor)
        except PermissionError as exc:
            raise ApiError(403, str(exc)) from None
        except CorrectionError as exc:
            raise ApiError(409 if getattr(exc, "code", "") == "REQUEST_CONFLICT" else 400, str(exc)) from None
        except ValueError as exc:
            raise ApiError(400, str(exc) or "بيانات غير صحيحة.") from None
        except Exception:
            logger.exception("legacy balance failed")
            raise ApiError(500, "تعذر إضافة الرصيد؛ راجع حساب العميل ثم أعد المحاولة.") from None
        finally:
            password = None
        return {"id": result["id"], "balanceBefore": balance_text(result["balance_before"]), "balanceAfter": balance_text(result["balance_after"]), "duplicate": bool(result["duplicate"])}

    # ------------------------------------------------------------------ transfers between bank / wallet accounts and the private account
    def transfers_overview(self, token):
        entry = self._api._entry(token)
        service, actor = FinancialAccountService(entry.session), entry.session.current_actor()
        try:
            accounts = service.accounts(_expected_actor=actor)
        except PermissionError as exc:
            raise ApiError(403, str(exc) or "ليس لديك صلاحية عرض الحسابات.") from None
        except Exception:
            logger.exception("transfers overview failed")
            raise ApiError(500, "تعذر تحميل الحسابات؛ أعد المحاولة.") from None
        banks = [{"key": a["key"], "name": a["name"], "type": a["account_type"], "balance": a["balance"], "balanceText": _cell("money", Decimal(a["balance"]))}
                 for a in accounts["accounts"] if a["ledger"] == "BANK" and a["is_active"]]
        private = None
        if "PRIVATE_ACCOUNT_MANAGE" in actor.permissions:
            try:
                data = service.private_account(_expected_actor=actor)
                private = {"available": bool(data["available"]), "balance": _cell("money", Decimal(data["balance"])), "totalIn": _cell("money", Decimal(data["total_in"])),
                           "totalOut": _cell("money", Decimal(data["total_out"])),
                           "entries": [{"date": e["date"], "text": e["text"], "account": e["account"], "amount": _cell("money", Decimal(e["amount"])), "note": e["note"],
                                        "voided": e["voided"], "balance": _cell("money", Decimal(e["balance"]))} for e in data["entries"][:20]]}
            except Exception:
                logger.exception("private account failed")
        return {"accounts": banks, "private": private, "canOperate": bool(accounts.get("can_operate")), "canPrivate": private is not None}

    def transfer(self, token, body):
        entry = self._api._entry(token)
        actor, service = entry.session.current_actor(), FinancialAccountService(entry.session)
        text = lambda name: str(body.get(name) if body.get(name) is not None else "")
        kind = text("kind")
        if kind not in ("BETWEEN", "TO_PRIVATE", "FROM_PRIVATE"):
            raise ApiError(400, "اختر نوع التحويل.")
        password = body.get("password")
        try:
            allowed = {a["key"] for a in service.accounts(_expected_actor=actor)["accounts"] if a["ledger"] == "BANK" and a["is_active"]}
        except PermissionError as exc:
            raise ApiError(403, str(exc) or "ليس لديك صلاحية.") from None
        except Exception:
            logger.exception("transfer: accounts failed")
            raise ApiError(500, "تعذر تنفيذ التحويل؛ راجع الأرصدة ثم أعد المحاولة.") from None
        chosen = [text("from"), text("to")] if kind == "BETWEEN" else [text("account")]
        if any(k not in allowed for k in chosen):
            raise ApiError(400, "اختر حسابات بنك أو محفظة فعّالة فقط.")
        try:
            self._api._authentication.verify_user_password(int(actor.user_id), password if isinstance(password, str) else "")
        except AuthenticationError:
            raise ApiError(400, "كلمة المرور غير صحيحة أو انتظر قليلاً ثم أعد المحاولة.") from None
        try:
            if kind == "BETWEEN":
                result = service.transfer(text("from"), text("to"), text("amount"), text("note"), text("request_key"), _expected_actor=actor)
            else:
                result = service.private_transfer(text("account"), "TO" if kind == "TO_PRIVATE" else "FROM", text("amount"), text("note"), text("request_key"), _expected_actor=actor)
        except PermissionError as exc:
            raise ApiError(403, str(exc)) from None
        except CorrectionError as exc:
            raise ApiError(409 if getattr(exc, "code", "") in ("REQUEST_CONFLICT", "BALANCE_CHANGED") else 400, str(exc)) from None
        except ValueError as exc:
            raise ApiError(400, str(exc) or "بيانات التحويل غير صحيحة.") from None
        except Exception:
            logger.exception("transfer failed")
            raise ApiError(500, "تعذر تنفيذ التحويل؛ راجع الأرصدة ثم أعد المحاولة.") from None
        finally:
            password = None
        return {k: (v if isinstance(v, (str, int, float, bool, type(None))) else str(v)) for k, v in (result or {}).items()}
