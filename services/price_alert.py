"""Price alert of the home page (a look of THIS computer, like the currency rates choices): the owner sets a SHOP_BUY floor and / or a SHOP_SELL ceiling for karat 21;
the home page says when the latest stored price is beyond one of them. Display only: nothing is stored in the database, nothing is posted, nothing is sent."""
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QSettings

ORGANIZATION, APPLICATION = "masGold", "masGold_v2"


def parse_price(text):
    """A positive Decimal, or None for an empty text; a bad text raises ValueError with an Arabic message."""
    value = str(text or "").strip().replace(",", "").replace("٫", ".")
    if not value:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("اكتب السعر بالأرقام.") from None
    if not number.is_finite() or number <= 0 or number > Decimal("1000000"):
        raise ValueError("السعر لازم يكون رقم موجب معقول.")
    return number.quantize(Decimal("0.01"))


def evaluate(buy_below, sell_above, buy, sell):
    """The alert lines for the latest prices: [{kind, text}] (empty when no threshold is crossed or no price is known)."""
    out = []
    if buy_below is not None and buy is not None and Decimal(buy) <= buy_below:
        out.append({"kind": "buy", "text": f"شراء المحل {Decimal(buy):,.2f} نزل لحد أو تحت {buy_below:,.2f}"})
    if sell_above is not None and sell is not None and Decimal(sell) >= sell_above:
        out.append({"kind": "sell", "text": f"بيع المحل {Decimal(sell):,.2f} وصل لحد أو فوق {sell_above:,.2f}"})
    return out


class PriceAlertSettings:
    def __init__(self, store=None):
        self._store = store if store is not None else QSettings(QSettings.NativeFormat, QSettings.UserScope, ORGANIZATION, APPLICATION)

    def _get(self, key):
        try:
            return parse_price(self._store.value(key, ""))
        except ValueError:
            return None

    def buy_below(self):
        return self._get("priceAlert/buyBelow")

    def sell_above(self):
        return self._get("priceAlert/sellAbove")

    def save(self, buy_below, sell_above):
        low, high = parse_price(buy_below), parse_price(sell_above)
        self._store.setValue("priceAlert/buyBelow", "" if low is None else str(low))
        self._store.setValue("priceAlert/sellAbove", "" if high is None else str(high))
        self._store.sync()
