"""Names of report totals that other code reads BY NAME (the iPad app's home cards and its attention list).

The report service builds these totals from the constants below and the iPad code looks them up with the same
constants, so renaming one is a single edit that both sides follow. Before this module the iPad matched the
Arabic text copied by hand, and a changed report label silently emptied a card.
"""

SALES_VALUE = "قيمة البيع"
OLD_GOLD_VALUE = "قيمة شراء الكسر"
RETURN_VALUE = "قيمة المرتجع"
COLLECTED = "المحصَّل"
RECORDED_EXPENSE = "مصروفات مسجلة"
RECORDED_INCOME = "إيرادات مسجلة"
CUSTOMERS_WE_OWE = "إجمالي اللي علينا"        # the report adds " (للعملاء)"
CUSTOMERS_OWE_US = "إجمالي اللي عليهم"        # the report adds " (على العملاء)"
BELOW_MIN = "تحت الحد الأدنى"
KPI_RED = "مؤشرات حمراء"
KPI_YELLOW = "مؤشرات صفراء"
