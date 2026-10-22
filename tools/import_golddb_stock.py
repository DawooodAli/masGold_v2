"""نقل المخزون والفواتير القديمة: the old shop's pieces (in stock AND sold) and its bills, linked, into the NEW database.

usage:
    python -m tools.import_golddb_stock report prod            (or: report test masgold_import_x)
    python -m tools.import_golddb_stock apply prod             (or: apply test masgold_import_x)
    python -m tools.import_golddb_stock review prod <file.csv>   (the bills whose sold side stayed unknown, for a manual review)

Source (READ ONLY): golddb.stock (every piece row and every scrap-purchase row), golddb.cash (what each bill collected / paid / left unpaid), golddb.customer_buysell and golddb.employeebill (customer and employee of a bill).
Target: masgold_prod, or a masgold_import_* rehearsal copy; ONLY when its inventory and invoices are empty, the schema is at the chain's last level and the customers / item types / factories / employees were imported first. Insert-only, ONE transaction.

* Pieces: every piece row (operation 0, 1, 3) becomes an inventory item that keeps the OLD stock number as its id and its barcode as its active barcode; sold pieces are SOLD, the others IN_STOCK, acquisition source LEGACY_MIGRATION. An item type or factory that is not found keeps a note with the old text (the type falls back to «متنوع»; the factory stays empty) - nothing is guessed.
* Bills: every bill number of those rows becomes a FINALIZED LEGACY invoice (completeness PARTIAL, UNKNOWN when the cash book has no row of it) with its customer and employee, a SELL_INVENTORY line per sold piece (the price of the gram when the old row has it) and a BUY_OLD_GOLD line per scrap row. The sell / buy split and the line amounts stay unknown (NULL); the invoice amount is what the cash book proves (paid in + left unpaid - paid out), with the three numbers kept in its provenance.
* No payment, ledger entry or stock movement is created: the customers' balances were imported on their own (one opening entry each) and must not be counted twice."""
import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD_DATABASE = "golddb"
NOTE = "مستورد من البرنامج القديم (رقم {id})"
FALLBACK_TYPE = "متنوع"
BATCH = 400
VALID_KARATS = (18, 21, 24)


def _t(value):
    """Text of an old column: SQL NULL (printed as the word NULL) and blanks become ''."""
    text = "" if value is None else str(value).strip()
    return "" if text == "NULL" else text


def norm(text):
    """Spelling variants of the same Arabic / Latin name compare equal (أ إ آ -> ا, ة -> ه, ى -> ي, case, blanks)."""
    table = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي"})
    return " ".join(str(text or "").translate(table).lower().split())


def _weight(value):
    try:
        number = Decimal(str(_t(value))).quantize(Decimal("0.001"))
    except (InvalidOperation, ValueError):
        return None
    return number if number > 0 else None


def _price(value):
    try:
        number = Decimal(str(_t(value))).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    return number if number > 0 else None


def _day(value):
    try:
        return date.fromisoformat(_t(value))
    except ValueError:
        return None


def _money(value):
    try:
        return Decimal(str(_t(value) or 0))
    except InvalidOperation:
        return Decimal(0)


def transform(stock, cash, buysell, employee_bills, lookups):
    """stock rows: (idstock, barcodeId, operation, item, kerat, weight, factory, billNumber, soldDate, cust_id, high, low, size, cat, note, date); cash rows: (bill_id, cashAction, amount_in, amount_out, cust_id);
    buysell: (bill_id, customer_id); employee_bills: (bill_id, employee_name). lookups: {item_types: {name: id}, factories: {name: id}, factory_codes: {code: id}, karats: {value: id}, categories: {code: id},
    employees: {name: id}, customers: set of ids}. Returns {pieces, invoices, problems, notes}."""
    problems, skipped, unmatched_factories, unmatched_types = [], [], {}, {}
    pieces, scrap, seen_barcodes = [], [], set()
    for row in stock:
        (idstock, barcode, operation, item, karat, weight, factory, bill, sold_date, cust, high, low, size, cat, note, day) = row
        number, op = _t(idstock), _t(operation)
        if not number.isdigit():
            problems.append((idstock, "bad stock id"))
            continue
        number = int(number)
        kind = {"0": "piece", "1": "piece", "3": "piece", "2": "scrap"}.get(op)
        if kind == "piece" and _t(barcode).lower().endswith("-old"):
            kind = "oldsale"                                                              # a sale taken from the old-gold stock (the «-old» barcode): a line of the bill, no stock piece
        if kind is None:
            problems.append((number, f"unknown operation {op}"))
            continue
        w = _weight(weight)
        karat_value = int(_t(karat)) if _t(karat).isdigit() else None
        if w is None or karat_value not in VALID_KARATS or karat_value not in lookups["karats"]:
            skipped.append((number, _t(barcode), _t(item), _t(karat), _t(weight)))              # a piece the system cannot hold (no weight / unsupported karat): reported, never invented
            continue
        base = {"id": number, "weight": w, "karat_id": lookups["karats"][karat_value], "karat": karat_value, "bill": _t(bill), "sold_date": _day(sold_date), "day": _day(day), "high": _price(high), "low": _price(low),
                "barcode": _t(barcode), "item_name": _t(item), "factory_name": _t(factory), "size": _t(size), "cat": _t(cat).upper()}
        if kind in ("scrap", "oldsale"):
            base["kind"] = kind
            scrap.append(base)
            continue
        if not base["barcode"] or base["barcode"] in seen_barcodes or base["barcode"].lower().endswith("-old"):
            problems.append((number, "missing, repeated or scrap barcode"))
            continue
        seen_barcodes.add(base["barcode"])
        type_id = lookups["item_types"].get(norm(base["item_name"]))
        notes = [NOTE.format(id=number)]
        if type_id is None:
            type_id = lookups["item_types"].get(norm(FALLBACK_TYPE))
            unmatched_types[base["item_name"]] = unmatched_types.get(base["item_name"], 0) + 1
            notes.append("الصنف القديم: " + (base["item_name"] or "—"))
            if type_id is None:
                problems.append((number, "no item type and no fallback"))
                continue
        factory_id = None
        if base["factory_name"]:
            key = norm(base["factory_name"])
            factory_id = lookups["factories"].get(key) or lookups["factory_codes"].get(key)
            if factory_id is None:
                unmatched_factories[base["factory_name"]] = unmatched_factories.get(base["factory_name"], 0) + 1
                notes.append("المصنع القديم: " + base["factory_name"])
        if _t(note):
            notes.append(_t(note))
        base.update({"type_id": type_id, "factory_id": factory_id, "category_id": lookups["categories"].get(base["cat"]), "status": "SOLD" if (op in ("1", "3") or base["sold_date"]) else "IN_STOCK",
                     "notes": "؛ ".join(notes)[:1000]})
        pieces.append(base)

    cash_by_bill, cust_by_bill = {}, {}
    for bill, action, amount_in, amount_out, cust in cash:
        bill, action = _t(bill), _t(action)
        credit = bill[:1] in ("C", "c") and bill[1:].isdigit()                       # the remainder of a bill and the later payments of that remainder carry the bill number with a «C» in front
        if credit:
            bill = bill[1:]
        if not bill:
            continue
        entry = cash_by_bill.setdefault(bill, {"in": Decimal(0), "out": Decimal(0), "remainder": Decimal(0), "paid_later": Decimal(0), "rows": 0})
        if action == "فاتورة":
            entry["in"] += _money(amount_in)
            entry["out"] += _money(amount_out)
            entry["rows"] += 1
        elif action == "متبقي":
            entry["remainder"] += _money(amount_out)
            entry["rows"] += 1
        elif action == "سداد" and credit:
            entry["paid_later"] += _money(amount_in)
            entry["rows"] += 1
        if _t(cust).isdigit() and int(_t(cust)) in lookups["customers"]:
            cust_by_bill.setdefault(bill, int(_t(cust)))
    for bill, customer in buysell:
        if _t(customer).isdigit() and int(_t(customer)) in lookups["customers"]:
            cust_by_bill[_t(bill)] = int(_t(customer))
    employee_by_bill = {_t(bill): lookups["employees"].get(_t(name)) for bill, name in employee_bills}

    bills = {}
    for piece in pieces:
        if piece["status"] == "SOLD" and piece["bill"]:
            bills.setdefault(piece["bill"], {"sale": [], "buy": [], "oldsale": []})["sale"].append(piece)
    for row in scrap:
        if row["bill"]:
            bills.setdefault(row["bill"], {"sale": [], "buy": [], "oldsale": []})[("oldsale" if row["kind"] == "oldsale" else "buy")].append(row)
        else:
            problems.append((row["id"], "scrap row without a bill number"))
    invoices = []
    for bill, parts in sorted(bills.items()):
        parts.setdefault("oldsale", [])
        rows = sorted(parts["sale"], key=lambda r: r["id"]) + sorted(parts["oldsale"], key=lambda r: r["id"]) + sorted(parts["buy"], key=lambda r: r["id"])
        days = [r["sold_date"] or r["day"] for r in rows if (r["sold_date"] or r["day"])]
        if not days:
            problems.append((bill, "bill without a date"))
            continue
        money = cash_by_bill.get(bill)
        net = (money["in"] + money["remainder"] - money["out"]) if money else Decimal(0)
        sells = bool(parts["sale"] or parts["oldsale"])
        invoice_type = "MIXED" if sells and parts["buy"] else ("SELL" if sells else "BUY")
        left = (money["remainder"] - money["paid_later"]) if money else Decimal(0)
        if left > 0:
            settlement = "PARTIAL" if (money["in"] + money["paid_later"]) > 0 else "UNSETTLED"
        else:
            settlement = "SETTLED"
        invoices.append({
            "number": bill, "type": invoice_type, "customer_id": cust_by_bill.get(bill), "employee_id": employee_by_bill.get(bill), "day": min(days),
            "net_amount": abs(net), "net_direction": "CUSTOMER_PAYS_SHOP" if net > 0 else ("SHOP_PAYS_CUSTOMER" if net < 0 else "SETTLED"), "settlement": settlement,
            "completeness": "PARTIAL" if money else "UNKNOWN",
            "provenance": {"source": "golddb", "bill": bill, "paid_in": str(money["in"]) if money else None, "paid_out": str(money["out"]) if money else None,
                           "left_unpaid": str(money["remainder"]) if money else None,
                           "paid_later": str(money["paid_later"]) if money else None},
            "lines": [{"number": n, "kind": "SELL_INVENTORY" if r in parts["sale"] else ("SELL_OLD_GOLD" if r in parts["oldsale"] else "BUY_OLD_GOLD"), "piece_id": r["id"] if r in parts["sale"] else None, "karat_id": r["karat_id"], "weight": r["weight"],
                       "item_name": r["item_name"] or None, "factory_name": r["factory_name"] or None, "cat": r["cat"] or None, "size": r["size"] or None, "barcode": r["barcode"] or None,
                       "karat": r["karat"], "price": karat_price(r["high"] if (r in parts["sale"] or r in parts["oldsale"]) else r["low"], r["karat"]),
                       "provenance": {"idstock": r["id"], "high": str(r["high"]) if r["high"] else None, "low": str(r["low"]) if r["low"] else None}}
                      for n, r in enumerate(rows, 1)]})
    for invoice in invoices:
        invoice["economics"] = derive_economics(invoice)
    return {"pieces": pieces, "invoices": invoices, "problems": problems, "skipped": skipped, "unmatched_factories": unmatched_factories, "unmatched_types": unmatched_types, "scrap_rows": len(scrap)}


MAX_MAKING_SHARE = Decimal("0.25")             # a bill whose derived making is above 25 % of its gold value is not trusted (the cash book then holds more than the listed pieces): its sold side stays unknown
OLD_GOLD_DISCOUNT = Decimal("1.50")            # the owner's average discount on the old gold bought from customers (percent)


def karat_price(raw_21k, karat):
    """The old rows keep the 21K price of the day on EVERY row (an 18K piece carries the 21K price too): the price of the gram of the row's own karat = that price x karat / 21, the system's own scaling."""
    if not raw_21k:
        return None
    return (raw_21k * Decimal(int(karat)) / Decimal(21)).quantize(Decimal("0.0001"))


def old_discount(gross):
    return (gross * OLD_GOLD_DISCOUNT / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)         # the table's own CHECK rounds half up (MySQL ROUND)


def derive_economics(invoice):
    """The money of a bill from what its own rows and the cash book prove. Every line gets the price of the gram of its karat (the row's 21K price x karat / 21).
    * the old gold bought from the customer (each BUY_OLD_GOLD line): gross = weight x the shop's buying price, discount 1.50 % (the owner's average), line total = gross - discount; the bill's buy total is their sum;
    * the sold lines: gold value = weight x the selling price; the bill's sell total = what the cash book proves (+ the old gold taken in), so the making of the bill = sell total - gold value, spread over the
      sold lines by weight (the same making per gram on each, the last line takes the rounding). A negative making (the amount is below the gold: a discount or another price) leaves the sold side unknown.
    Returns {"lines": {number: {...}}, "sell_total", "buy_total"} or None when nothing can be derived (no cash proof, a line without a price, a negative making)."""
    lines = invoice["lines"]
    if not lines or invoice["completeness"] != "PARTIAL" or any(not l["price"] for l in lines):
        return None
    sale = [l for l in lines if l["kind"] in ("SELL_INVENTORY", "SELL_OLD_GOLD")]
    buy = [l for l in lines if l["kind"] == "BUY_OLD_GOLD"]
    parts, buy_total = {}, Decimal(0)
    for line in buy:
        gross = (line["weight"] * line["price"]).quantize(Decimal("0.01"))
        discount = old_discount(gross)
        parts[line["number"]] = {"price": line["price"], "gross": gross, "discount": discount, "total": gross - discount}
        buy_total += gross - discount
    if not sale:
        return {"lines": parts, "sell_total": Decimal(0), "buy_total": buy_total}
    signed = invoice["net_amount"] if invoice["net_direction"] == "CUSTOMER_PAYS_SHOP" else (-invoice["net_amount"] if invoice["net_direction"] == "SHOP_PAYS_CUSTOMER" else Decimal(0))
    sell_total = signed + buy_total
    gross = {l["number"]: (l["weight"] * l["price"]).quantize(Decimal("0.01")) for l in sale}
    making = sell_total - sum(gross.values(), Decimal(0))
    weight = sum((l["weight"] for l in sale), Decimal(0))
    if making < 0 or weight <= 0 or making > sum(gross.values(), Decimal(0)) * MAX_MAKING_SHARE:
        return {"lines": parts, "sell_total": None, "buy_total": buy_total} if buy else None
    per_gram = (making / weight).quantize(Decimal("0.01"))
    left = making
    for position, line in enumerate(sale):
        share = left if position == len(sale) - 1 else min(left, (line["weight"] * per_gram).quantize(Decimal("0.01")))
        parts[line["number"]] = {"price": line["price"], "gross": gross[line["number"]], "making_per_gram": per_gram, "making": share, "total": gross[line["number"]] + share}
        left -= share
    return {"lines": parts, "sell_total": sell_total, "buy_total": buy_total}


def diagnose(invoice):
    """Why a bill's sold side could not be derived, with the figures to look at: {reason, gold, buy_total, implied_sell, implied_making, share} (empty strings where a figure does not exist)."""
    lines = invoice["lines"]
    sale = [l for l in lines if l["kind"] in ("SELL_INVENTORY", "SELL_OLD_GOLD")]
    buy = [l for l in lines if l["kind"] == "BUY_OLD_GOLD"]
    out = {"reason": "", "gold": "", "buy_total": "", "implied_sell": "", "implied_making": "", "share": ""}
    if invoice["completeness"] != "PARTIAL":
        out["reason"] = "لا يوجد في دفتر النقدية مبلغ لهذه الفاتورة"
        return out
    if any(not l["price"] for l in lines):
        out["reason"] = "بند بدون سعر جرام في المخزون القديم"
        return out
    buy_total = sum(((l["weight"] * l["price"]).quantize(Decimal("0.01")) - old_discount((l["weight"] * l["price"]).quantize(Decimal("0.01"))) for l in buy), Decimal(0))
    signed = invoice["net_amount"] if invoice["net_direction"] == "CUSTOMER_PAYS_SHOP" else (-invoice["net_amount"] if invoice["net_direction"] == "SHOP_PAYS_CUSTOMER" else Decimal(0))
    gold = sum(((l["weight"] * l["price"]).quantize(Decimal("0.01")) for l in sale), Decimal(0))
    sell = signed + buy_total
    making = sell - gold
    out.update({"gold": f"{gold:.2f}", "buy_total": f"{buy_total:.2f}", "implied_sell": f"{sell:.2f}", "implied_making": f"{making:.2f}", "share": f"{(making / gold * 100):.1f}%" if gold else ""})
    if making < 0:
        out["reason"] = "المبلغ أقل من قيمة الذهب (خصم أو سعر مختلف)"
    elif gold and making > gold * MAX_MAKING_SHARE:
        out["reason"] = "المصنعية أكبر من 25% من قيمة الذهب (المبلغ فيه حاجة زيادة)"
    return out


def review_rows(data, customer_names, employee_names):
    """The bills whose sold side stayed unknown, one row each, for the owner's review (sorted by date)."""
    rows = []
    for invoice in data["invoices"]:
        economics = invoice["economics"]
        if economics and economics["sell_total"] is not None:
            continue
        info = diagnose(invoice)
        sold = [l for l in invoice["lines"] if l["kind"] != "BUY_OLD_GOLD"]
        rows.append([invoice["number"], invoice["day"].isoformat(), {"SELL": "بيع", "BUY": "شراء كسر", "MIXED": "بيع مع كسر"}[invoice["type"]], customer_names.get(invoice["customer_id"], ""),
                     employee_names.get(invoice["employee_id"], ""), f"{invoice['net_amount']:.2f}", {"CUSTOMER_PAYS_SHOP": "العميل دفع", "SHOP_PAYS_CUSTOMER": "المحل دفع", "SETTLED": "متعادل"}[invoice["net_direction"]],
                     len(sold), "، ".join(f"{l['karat']}ك {l['weight']}جم" for l in sold)[:200], info["gold"], info["buy_total"], info["implied_sell"], info["implied_making"], info["share"], info["reason"], "لم يُحسب شيء" if not economics else "حُسب الكسر فقط"])
    return sorted(rows, key=lambda r: (r[1], r[0]))


REVIEW_HEADER = ["رقم الفاتورة", "التاريخ", "النوع", "العميل", "الموظف", "المبلغ في دفتر النقدية", "الاتجاه", "عدد القطع المباعة", "القطع", "قيمة الذهب المباع",
                 "قيمة الكسر بعد الخصم", "إجمالي البيع المستنتج", "المصنعية المستنتجة", "نسبة المصنعية من الذهب", "سبب عدم الحساب", "الحالة"]


def _utf8(text):
    return "CONVERT(X'" + str(text).encode("utf-8").hex() + "' USING utf8mb4) COLLATE utf8mb4_unicode_ci"             # the tables' own collation, so a text compares with its column


def _opt(value):
    return "NULL" if value is None or value == "" else _utf8(value)


def _json(obj):
    import json
    return "CAST(" + _utf8(json.dumps(obj, ensure_ascii=False)) + " AS JSON)"


def _chunks(items):
    for index in range(0, len(items), BATCH):
        yield items[index:index + BATCH]


def _econ(economics, number):
    """gross, making per gram, making, discount type / value / amount and line total of one line (NULL when the bill's values are unknown)."""
    if not economics:
        return "NULL, NULL, NULL, NULL, NULL, NULL, NULL"
    part = economics["lines"][number]
    if "discount" in part:
        return f"{part['gross']}, NULL, NULL, 'PERCENT', {OLD_GOLD_DISCOUNT}, {part['discount']}, {part['total']}"
    return f"{part['gross']}, {part['making_per_gram']}, {part['making']}, NULL, NULL, NULL, {part['total']}"


def economics_sql(invoices):
    """A correction of an earlier import: the gold value / making of the derivable pure-sale bills (see derive_economics). Only LEGACY rows of golddb, one transaction."""
    parts = ["SET NAMES utf8mb4;", "START TRANSACTION;"]
    for i in invoices:
        number = _utf8(i["number"])
        where_invoice = f"invoice_id = (SELECT id FROM invoices WHERE record_origin = 'LEGACY' AND legacy_bill_number = {number})"
        for line in i["lines"]:                                          # the price of the gram of the line's own karat (also where the amounts stay unknown)
            parts.append(f"UPDATE invoice_lines SET gold_price_per_gram = {line['price'] if line['price'] else 'NULL'}, gross_total = NULL, making_per_gram = NULL, making_total = NULL, discount_type = NULL, "
                         f"discount_value = NULL, discount_amount = NULL, line_total = NULL WHERE record_origin = 'LEGACY' AND line_number = {line['number']} AND {where_invoice};")
        if not i["economics"]:
            parts.append(f"UPDATE invoices SET sell_total = NULL, buy_total = NULL WHERE record_origin = 'LEGACY' AND legacy_source_system = 'golddb' AND legacy_bill_number = {number};")
            continue
        sell = "NULL" if i["economics"]["sell_total"] is None else f"{i['economics']['sell_total']:.2f}"
        parts.append(f"UPDATE invoices SET sell_total = {sell}, buy_total = {i['economics']['buy_total']:.2f} WHERE record_origin = 'LEGACY' AND legacy_source_system = 'golddb' AND legacy_bill_number = {number};")
        for line_number, part in i["economics"]["lines"].items():
            if "discount" in part:
                parts.append(f"UPDATE invoice_lines SET gold_price_per_gram = {part['price']}, gross_total = {part['gross']}, discount_type = 'PERCENT', discount_value = {OLD_GOLD_DISCOUNT}, discount_amount = {part['discount']}, "
                             f"line_total = {part['total']} WHERE record_origin = 'LEGACY' AND line_number = {line_number} AND {where_invoice};")
            else:
                parts.append(f"UPDATE invoice_lines SET gold_price_per_gram = {part['price']}, gross_total = {part['gross']}, making_per_gram = {part['making_per_gram']}, making_total = {part['making']}, "
                             f"line_total = {part['total']} WHERE record_origin = 'LEGACY' AND line_number = {line_number} AND {where_invoice};")
    parts.append("COMMIT;")
    return "\n".join(parts) + "\n"


def insert_sql(data):
    parts = ["SET NAMES utf8mb4;", "START TRANSACTION;"]
    for chunk in _chunks(data["pieces"]):
        parts.append("INSERT INTO inventory_items (id, item_type_id, karat_id, category_id, factory_id, weight_grams, size, stock_classification, inventory_status, acquisition_source, acquired_at, notes) VALUES\n" + ",\n".join(
            f"({p['id']}, {p['type_id']}, {p['karat_id']}, {p['category_id'] or 'NULL'}, {p['factory_id'] or 'NULL'}, {p['weight']}, {_opt(p['size'])}, 'NEW_STOCK', '{p['status']}', 'LEGACY_MIGRATION', "
            f"'{(p['day'] or p['sold_date'] or date(2024, 1, 1)).isoformat()} 00:00:00', {_utf8(p['notes'])})" for p in chunk) + ";")
    for chunk in _chunks(data["pieces"]):
        parts.append("INSERT INTO inventory_barcodes (inventory_item_id, barcode_value, barcode_status, reason) VALUES\n" + ",\n".join(
            f"({p['id']}, {_utf8(p['barcode'])}, 'ACTIVE', {_utf8('مستورد من البرنامج القديم')})" for p in chunk) + ";")
    for chunk in _chunks(data["invoices"]):
        parts.append("INSERT INTO invoices (invoice_number, invoice_type, invoice_status, customer_id, employee_id, invoice_date, sell_total, buy_total, net_amount, net_direction, settlement_status, "
                     "legacy_source_system, legacy_bill_number, legacy_completeness, record_origin, provenance_json, finalized_at) VALUES\n" + ",\n".join(
            f"({_utf8(i['number'])}, '{i['type']}', 'FINALIZED', {i['customer_id'] or 'NULL'}, {i['employee_id'] or 'NULL'}, '{i['day'].isoformat()} 12:00:00', "
            f"{('%.2f' % i['economics']['sell_total']) if i['economics'] and i['economics']['sell_total'] is not None else 'NULL'}, {('%.2f' % i['economics']['buy_total']) if i['economics'] else 'NULL'}, {i['net_amount']:.2f}, "
            f"'{i['net_direction']}', '{i['settlement']}', 'golddb', {_utf8(i['number'])}, '{i['completeness']}', 'LEGACY', {_json(i['provenance'])}, '{i['day'].isoformat()} 12:00:00')" for i in chunk) + ";")
    lines = [(i["number"], line) for i in data["invoices"] for line in i["lines"]]
    lines_economics = {i["number"]: i["economics"] for i in data["invoices"] if i["economics"]}
    for chunk in _chunks(lines):
        parts.append("INSERT INTO invoice_lines (invoice_id, record_origin, line_number, line_type, inventory_item_id, karat_id, weight_grams, quantity, item_name_snapshot, factory_snapshot, category_snapshot, "
                     "size_snapshot, barcode_snapshot, gold_price_per_gram, gross_total, making_per_gram, making_total, discount_type, discount_value, discount_amount, line_total, provenance_json) VALUES\n" + ",\n".join(
            f"((SELECT id FROM invoices WHERE invoice_number = {_utf8(number)}), 'LEGACY', {line['number']}, '{line['kind']}', {line['piece_id'] or 'NULL'}, {line['karat_id']}, {line['weight']}, 1, "
            f"{_opt(line['item_name'])}, {_opt(line['factory_name'])}, {_opt(line['cat'])}, {_opt(line['size'])}, {_opt(line['barcode'])}, {line['price'] if line['price'] else 'NULL'}, "
            f"{_econ(lines_economics.get(number), line['number'])}, {_json(line['provenance'])})"
            for number, line in chunk) + ";")
    parts.append("INSERT INTO invoice_customer_snapshots (invoice_id, customer_id, customer_code_snapshot, customer_name_snapshot, mobile_snapshot, place_snapshot) "
                 "SELECT i.id, c.id, c.customer_code, c.name, c.mobile, c.place FROM invoices i JOIN customers c ON c.id = i.customer_id WHERE i.record_origin = 'LEGACY';")
    parts.append("COMMIT;")
    return "\n".join(parts) + "\n"


def update_sql(invoices):
    """The invoice amount fields only (a correction of an earlier import): one transaction, only LEGACY rows of golddb, matched by the old bill number."""
    parts = ["SET NAMES utf8mb4;", "START TRANSACTION;"]
    for i in invoices:
        parts.append(f"UPDATE invoices SET net_amount = {i['net_amount']:.2f}, net_direction = '{i['net_direction']}', settlement_status = '{i['settlement']}', legacy_completeness = '{i['completeness']}', "
                     f"provenance_json = {_json(i['provenance'])} WHERE record_origin = 'LEGACY' AND legacy_source_system = 'golddb' AND legacy_bill_number = {_utf8(i['number'])};")
    parts.append("COMMIT;")
    return "\n".join(parts) + "\n"


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    from tools.import_target import resolve
    mode = argv[0] if argv else ""
    out_path = argv[-1] if mode == "review" and len(argv) > 1 else None
    target = resolve(argv[1:-1] if mode == "review" else argv[1:]) if mode in ("report", "apply", "amounts", "economics", "review") else None
    if target is None:
        print(__doc__)
        return 2
    sys.path.insert(0, str(ROOT))
    from config.database_config import DatabaseConfig
    from tools.schema_rehearsal import chain as chain_module
    from tools.schema_rehearsal.mysql_client import DEFAULT_MYSQL, Credentials, MysqlClient, option_file
    cfg = DatabaseConfig.from_env()
    if cfg.host not in ("localhost", "127.0.0.1"):
        print("refused: the database server is not this machine")
        return 2
    with option_file(Credentials("localhost", cfg.port, cfg.user, cfg.password)) as options:
        client = MysqlClient(DEFAULT_MYSQL, options)
        old = lambda sql: client.query(sql, database=OLD_DATABASE)
        new = lambda sql: client.query(sql, database=target)
        lookups = {"item_types": {norm(r[1]): int(r[0]) for r in new("SELECT id, name FROM item_types;")}, "factories": {norm(r[1]): int(r[0]) for r in new("SELECT id, name FROM factories;")},
                   "factory_codes": {norm(r[1]): int(r[0]) for r in new("SELECT id, code FROM factories WHERE code IS NOT NULL;")},
                   "karats": {int(r[1]): int(r[0]) for r in new("SELECT id, karat_value FROM karats;")}, "categories": {r[1]: int(r[0]) for r in new("SELECT id, code FROM item_categories;")},
                   "employees": {r[1].strip(): int(r[0]) for r in new("SELECT id, name FROM employees;")}, "customers": {int(r[0]) for r in new("SELECT id FROM customers;")}}
        data = transform(old("SELECT idstock, barcodeId, operation, item, kerat, weight, factory, billNumber, soldDate, cust_id, high, low, size, cat, note, date FROM stock ORDER BY idstock;"),
                         old("SELECT bill_id, cashAction, amount_in, amount_out, cust_id FROM cash WHERE bill_id IS NOT NULL;"), old("SELECT bill_id, customer_id FROM customer_buysell ORDER BY id;"),
                         old("SELECT bill_id, employee_name FROM employeebill ORDER BY id;"), lookups)
        sold = sum(1 for p in data["pieces"] if p["status"] == "SOLD")
        lines = sum(len(i["lines"]) for i in data["invoices"])
        print({"pieces": len(data["pieces"]), "sold": sold, "in_stock": len(data["pieces"]) - sold, "scrap_rows": data["scrap_rows"], "invoices": len(data["invoices"]), "lines": lines,
               "with_customer": sum(1 for i in data["invoices"] if i["customer_id"]), "with_employee": sum(1 for i in data["invoices"] if i["employee_id"]), "problems": len(data["problems"])})
        print("item types not found (fell back to «متنوع»):", data["unmatched_types"])
        print("factories not found (left empty, kept in the note):", dict(sorted(data["unmatched_factories"].items(), key=lambda kv: -kv[1])[:15]), "...")
        if data["skipped"]:
            print("skipped (id, barcode, item, karat, weight):", data["skipped"])
        if data["problems"]:
            print("problems:", data["problems"][:15])
        if mode == "report":
            return 0
        if mode == "review":
            import csv
            names = {int(r[0]): r[1] for r in new("SELECT id, name FROM customers;")}
            staff = {int(r[0]): r[1] for r in new("SELECT id, name FROM employees;")}
            rows = review_rows(data, names, staff)
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(REVIEW_HEADER)
                writer.writerows(rows)
            reasons = {}
            for row in rows:
                reasons[row[-2]] = reasons.get(row[-2], 0) + 1
            nothing = sum(1 for row in rows if row[-1] == "لم يُحسب شيء")
            print(f"OK: {len(rows)} bills written to {out_path} ({nothing} with nothing derived, {len(rows) - nothing} with the old gold derived only)")
            for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
                print(f"   {count:4d}  {reason}")
            return 0
        derived = sum(1 for i in data["invoices"] if i["economics"])
        sell_bills = sum(1 for i in data["invoices"] if i["type"] == "SELL")
        print(f"money derived for {derived} of {len(data['invoices'])} bills ({sell_bills} pure sales)")
        if mode == "economics":
            count = int(new("SELECT COUNT(*) FROM invoices WHERE record_origin = 'LEGACY' AND legacy_source_system = 'golddb';")[0][0])
            if count != len(data["invoices"]):
                print(f"refused: the target holds {count} imported invoices, the source gives {len(data['invoices'])}; nothing was changed")
                return 2
            client.run_script(economics_sql(data["invoices"]), database=target)
            known = new("SELECT COUNT(*), ROUND(SUM(making_total), 2) FROM invoice_lines WHERE record_origin = 'LEGACY' AND making_total IS NOT NULL;")[0]
            print(f"OK: lines with a derived making: {known[0]} (making total {known[1]})")
            return 0
        if mode == "amounts":
            before = new("SELECT COUNT(*), SUM(net_amount) FROM invoices WHERE record_origin = 'LEGACY' AND legacy_source_system = 'golddb';")[0]
            if int(before[0]) != len(data["invoices"]):
                print(f"refused: the target holds {before[0]} imported invoices, the source gives {len(data['invoices'])}; nothing was changed")
                return 2
            client.run_script(update_sql(data["invoices"]), database=target)
            now = new("SELECT COUNT(*), SUM(net_amount), SUM(settlement_status = 'SETTLED'), SUM(settlement_status = 'PARTIAL'), SUM(settlement_status = 'UNSETTLED') FROM invoices WHERE record_origin = 'LEGACY' AND legacy_source_system = 'golddb';")[0]
            print(f"OK: amounts updated (invoices {now[0]}, total before {before[1]} -> after {now[1]}, settled {now[2]}, partial {now[3]}, unsettled {now[4]})")
            return 0
        if data["problems"]:
            print("refused: fix the problems first (nothing was written)")
            return 1
        last = int(chain_module.load_chain(require_committed=False)[-1].name[:3])
        level = int(new("SELECT MAX(version) FROM schema_migrations;")[0][0])
        counts = {t: int(new(f"SELECT COUNT(*) FROM {t};")[0][0]) for t in ("inventory_items", "invoices")}
        needed = {"customers": len(lookups["customers"]), "item_types": len(lookups["item_types"]), "factories": len(lookups["factories"]), "employees": len(lookups["employees"])}
        if level != last or any(counts.values()) or not all(needed.values()):
            print(f"refused: the target must be at level {last} with no stock or invoices and with the customers / item types / factories / employees imported (level {level}, {counts}, {needed}); nothing was written")
            return 2
        client.run_script(insert_sql(data), database=target)
        after = {"items": int(new("SELECT COUNT(*) FROM inventory_items;")[0][0]), "barcodes": int(new("SELECT COUNT(*) FROM inventory_barcodes;")[0][0]), "invoices": int(new("SELECT COUNT(*) FROM invoices;")[0][0]),
                 "lines": int(new("SELECT COUNT(*) FROM invoice_lines;")[0][0]), "snapshots": int(new("SELECT COUNT(*) FROM invoice_customer_snapshots;")[0][0]),
                 "in_stock": int(new("SELECT COUNT(*) FROM inventory_items WHERE inventory_status = 'IN_STOCK';")[0][0])}
        print("OK:", after)
        good = after["items"] == len(data["pieces"]) and after["barcodes"] == len(data["pieces"]) and after["invoices"] == len(data["invoices"]) and after["lines"] == lines
        return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
