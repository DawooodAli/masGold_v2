"""Physical / held / available Scrap, in the words an employee sees (032).

The top bar shows the PHYSICAL Scrap balance. Weight returned through an Exchange is physically in Scrap but
HELD for the manager's decision, so every consumer (Billing F7, Exchange F7, conversion, corrections) may use
only the AVAILABLE grams. These texts explain why physical and available differ, so a refusal never looks
like a wrong balance. The accounting itself lives in the repositories (vw_old_gold_available)."""
from decimal import Decimal

ZERO = Decimal("0.000")


def _grams(value):
    return f"{Decimal(str(value or 0)):,.3f} جرام"


def breakdown_text(physical, held, available):
    """«الرصيد الفعلي … • المحجوز لمراجعة المدير … • المتاح للاستخدام …» (only needed while something is held)."""
    return (f"الرصيد الفعلي: {_grams(physical)}  •  المحجوز لمراجعة المدير: {_grams(held)}"
            f"  •  المتاح للاستخدام: {_grams(available)}")


def shortage_message(karat, requested, available, held=ZERO):
    """The refusal when the requested grams exceed the available Scrap of one karat."""
    held = Decimal(str(held or 0))
    if held > 0:
        return (f"رصيد الكسر المتاح عيار {karat} غير كافٍ: المطلوب {_grams(requested)} والمتاح {_grams(available)}. "
                f"يوجد {_grams(held)} محجوز لمراجعة المدير ولا يمكن استخدامه حتى اعتماد القرار.")
    return f"رصيد الكسر عيار {karat} غير كافٍ: المتاح {Decimal(str(available)):,.3f} جرام والمطلوب " \
           f"{Decimal(str(requested)):,.3f} جرام."


def held_grams(repository, karat_id):
    """The HELD grams of one karat (a plain read; the caller already holds the ledger / hold locks)."""
    reader = getattr(repository, "held_old_gold", None)
    return Decimal(str(reader(karat_id))) if reader else ZERO
