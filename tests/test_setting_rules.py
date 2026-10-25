"""System Settings (migration 081) in the BUSINESS rules the keys were seeded for.

The alert thresholds, the old-gold discount triple and the price-deviation warning are read through the caller's OWN repository (no
SYSTEM_SETTINGS_VIEW right, no extra connection), and anything unusable falls back to the hard-coded figure it replaced — so a
database below 081, a test fake and a mistyped value all keep the behaviour the code always had.

The last group runs the REAL ReportService and the real invoice rules over fakes that DO carry settings, to prove the configured value
is what is applied (the constants alone would silently pass)."""
from decimal import Decimal as D
from types import SimpleNamespace
import unittest

from mysql.connector import ProgrammingError

from repositories.base_repository import BaseRepository
from services import setting_rules
from services.invoice_service import InvoiceService
from services.report_service import ReportService
from services.system_settings_service import SettingsError, SystemSettingsService
from tests import test_report_alerts as alerts
from tests import test_report_service as base


# ------------------------------------------------------------------ pure parsing

class EffectiveTests(unittest.TestCase):

    def test_a_stored_value_replaces_the_default(self):
        got = setting_rules.effective({"dead_stock_days": "60", "max_old_gold_discount_pct": "7.5"})
        self.assertEqual(got["dead_stock_days"], 60)
        self.assertEqual(got["max_old_gold_discount_pct"], D("7.5"))

    def test_missing_blank_and_unusable_values_keep_the_default(self):
        for stored in (None, "", "   ", "not-a-number", "NaN", "Infinity"):
            with self.subTest(stored=stored):
                got = setting_rules.effective({"dead_stock_days": stored})
                self.assertEqual(got["dead_stock_days"], 180)

    def test_an_integer_setting_accepts_a_whole_decimal_and_refuses_a_fraction(self):
        self.assertEqual(setting_rules.effective({"velocity_days": "30.0"})["velocity_days"], 30)
        self.assertEqual(setting_rules.effective({"velocity_days": "30.5"})["velocity_days"], 90)     # a day count must be whole

    def test_a_value_outside_its_range_is_refused_rather_than_applied(self):
        self.assertEqual(setting_rules.effective({"dead_stock_days": "0"})["dead_stock_days"], 180)   # never negative / zero windows
        self.assertEqual(setting_rules.effective({"dead_stock_days": "-10"})["dead_stock_days"], 180)
        self.assertEqual(setting_rules.effective({"max_old_gold_discount_pct": "400"})["max_old_gold_discount_pct"], D("5.00"))
        self.assertEqual(setting_rules.effective({"price_deviation_warning_pct": "-1"})["price_deviation_warning_pct"], D("10"))

    def test_an_empty_read_yields_every_default(self):
        self.assertEqual(setting_rules.effective({}), setting_rules.DEFAULTS)

    def test_only_the_asked_keys_are_answered(self):
        self.assertEqual(set(setting_rules.effective({}, setting_rules.DISCOUNT_KEYS)), set(setting_rules.DISCOUNT_KEYS))


# ------------------------------------------------------------------ reading through a repository

class ReaderRepo:
    def __init__(self, rows):
        self.rows, self.asked = rows, None

    def business_values(self, keys):
        self.asked = tuple(keys)
        return {k: self.rows[k] for k in keys if k in self.rows}


class AccessorTests(unittest.TestCase):

    def test_the_configured_value_comes_back(self):
        repo = ReaderRepo({"dead_stock_days": "45", "min_sales_for_velocity": "6"})
        got = setting_rules.report_thresholds(repo)
        self.assertEqual((got["dead_stock_days"], got["min_sales_for_velocity"]), (45, 6))
        self.assertEqual(set(repo.asked), set(setting_rules.REPORT_KEYS))                        # only the report keys are read

    def test_a_repository_without_the_reader_keeps_every_default(self):
        got = setting_rules.report_thresholds(object())                                           # a test fake: no business_values at all
        self.assertEqual(got["dead_stock_days"], 180)
        self.assertEqual(setting_rules.discount_limits(object())["max_old_gold_discount_pct"], D("5.00"))
        self.assertEqual(setting_rules.price_warning_percent(object()), D("10"))

    def test_an_unusable_stored_value_falls_back_to_its_own_default_only(self):
        repo = ReaderRepo({"velocity_days": "abc", "low_cover_days": "21"})
        got = setting_rules.report_thresholds(repo)
        self.assertEqual(got["velocity_days"], 90)
        self.assertEqual(got["low_cover_days"], 21)                                               # the good key is still honoured


# ------------------------------------------------------------------ previews read the loaded payload

class PayloadTests(unittest.TestCase):

    def test_the_service_carried_value_is_used(self):
        key = setting_rules.PRICE_WARNING_KEY
        self.assertEqual(setting_rules.price_warning_from({key: D("4")}), D("4"))
        self.assertEqual(setting_rules.price_warning_from({key: "4"}), D("4"))

    def test_no_payload_or_an_unusable_one_keeps_the_default(self):
        key = setting_rules.PRICE_WARNING_KEY
        for payload in (None, {}, {key: None}, {key: "abc"}, {key: "999"}):
            with self.subTest(payload=payload):
                self.assertEqual(setting_rules.price_warning_from(payload), D("10"))


# ------------------------------------------------------------------ the repository read itself

class Cursor:
    def __init__(self, rows=None, errno=None):
        self.rows, self.errno, self.query, self.params, self.closed = rows or [], errno, None, None, False

    def execute(self, query, params=()):
        self.query, self.params = query, params
        if self.errno:
            raise ProgrammingError("Table 'db.system_settings' doesn't exist", errno=self.errno)

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, cursor):
        self._cursor, self.asked_for_dict = cursor, None

    def cursor(self, dictionary=True):
        self.asked_for_dict = dictionary
        return self._cursor


class BusinessValuesTests(unittest.TestCase):

    def test_one_query_returns_the_stored_text_of_exactly_those_keys(self):
        cursor = Cursor(rows=[{"setting_key": "dead_stock_days", "setting_value": "60"},
                              {"setting_key": "min_old_gold_discount_pct", "setting_value": "0.50"}])
        connection = Connection(cursor)
        got = BaseRepository(connection).business_values(("dead_stock_days", "min_old_gold_discount_pct"))
        self.assertEqual(got, {"dead_stock_days": "60", "min_old_gold_discount_pct": "0.50"})      # text: the caller decides the type
        self.assertTrue(connection.asked_for_dict)
        self.assertEqual(cursor.params, ("dead_stock_days", "min_old_gold_discount_pct"))
        self.assertEqual(cursor.query.count("%s"), 2)
        self.assertTrue(cursor.closed)

    def test_no_keys_asked_for_is_no_query_at_all(self):
        self.assertEqual(BaseRepository(Connection(Cursor())).business_values(()), {})

    def test_a_database_before_081_has_no_table_and_gets_no_rows(self):                            # the fallback path, not an error
        cursor = Cursor(errno=1146)
        self.assertEqual(BaseRepository(Connection(cursor)).business_values(("dead_stock_days",)), {})
        self.assertTrue(cursor.closed)

    def test_any_other_database_error_still_raises(self):
        with self.assertRaises(ProgrammingError):
            BaseRepository(Connection(Cursor(errno=1054))).business_values(("dead_stock_days",))


# ------------------------------------------------------------------ the alerts report applies what the owner configured

class Configured(alerts.AlertsRepo):
    """The same hand-checkable stock with two thresholds changed in `system_settings`."""

    def business_values(self, keys):
        return {"dead_stock_days": "60", "velocity_days": "30"}


class ConfiguredThresholds(alerts.AlertsCase):

    def run_with(self, repo_class):
        service = ReportService(self.session, transaction_factory=self.service._transaction,
                                repository=repo_class, authorization=self.authorization, today=lambda: alerts.TODAY)
        return service.run("alerts", {})

    def test_dead_stock_uses_the_configured_days_in_the_cutoff_and_in_the_text(self):
        result = self.run_with(Configured)
        table = self.table(result, "dead_21")
        self.assertIn("60 يوم", table["title"])                                                   # the title states the EFFECTIVE figure
        rows = {(r["type"], r["factory"]): r for r in table["rows"]}
        self.assertEqual(rows[("خاتم", "الأمل")]["count"], 2)                                      # 180 and 179 days are both older than 60
        self.assertEqual(rows[("خاتم", "الأمل")]["oldest"], 180)
        self.assertEqual(rows[("دبلة", "النور")]["count"], 2)                                      # the 10-day pieces stay out
        self.assertIn("60 يومًا", result["meaning"][0])

    def test_dead_stock_keeps_the_default_when_nothing_is_configured(self):
        rows = {(r["type"], r["factory"]): r for r in self.table(self.run_with(alerts.AlertsRepo), "dead_21")["rows"]}
        self.assertEqual(rows[("خاتم", "الأمل")]["count"], 1)                                      # 179 days is still alive at 180

    def test_the_sales_window_uses_the_configured_days(self):
        configured = self.run_with(Configured)
        titles = [c["title"] for c in self.table(configured, "low_21")["columns"]]
        self.assertTrue(any("30 يوم" in t for t in titles))                                       # the column says the effective window
        ring = next(r for r in self.table(configured, "low_21")["rows"] if (r["type"], r["factory"]) == ("دبلة", "النور"))
        self.assertEqual((ring["sold90"], ring["cover"]), (5, "12"))                               # 5 sales inside 30 days; 2 x 30 / 5 = 12
        self.assertEqual(self.table(configured, "low_18")["rows"][0]["sold90"], 3)                 # the 18K series sells only on days 1-3

        default = self.run_with(alerts.AlertsRepo)
        ring = next(r for r in self.table(default, "low_21")["rows"] if (r["type"], r["factory"]) == ("دبلة", "النور"))
        self.assertEqual((ring["sold90"], ring["cover"]), (13, "13"))                              # 13 sales inside 90 days; 2 x 90 / 13 = 13.8


# ------------------------------------------------------------------ the invoice discount triple

class InvoiceRepo:
    def __init__(self, billing=None, settings=None):
        self._billing, self._settings = billing, settings

    def billing_settings(self):
        return self._billing

    def business_values(self, keys):
        return {k: self._settings[k] for k in keys if k in (self._settings or {})}


def calculation(sales, discounts):
    return SimpleNamespace(sales=sales,
                           olds=[SimpleNamespace(discount_type="PERCENT", discount_value=D(v)) for v in discounts])


class InvoiceLimitsTests(unittest.TestCase):

    def test_the_discount_triple_comes_from_system_settings_and_the_settlement_limits_from_016(self):
        repo = InvoiceRepo(billing={"settlement_self_limit": D("50.00"), "settlement_approval_limit": D("500.00")},
                           settings={"default_old_gold_discount_pct": "2.25", "min_old_gold_discount_pct": "0.50",
                                     "max_old_gold_discount_pct": "7.00"})
        limits = InvoiceService._limits(repo)
        self.assertEqual(limits["old_gold_discount_default_percent"], D("2.25"))                   # the historic key name is kept for the screen
        self.assertEqual(limits["min_old_gold_discount_pct"], D("0.50"))
        self.assertEqual(limits["max_old_gold_discount_pct"], D("7.00"))
        self.assertEqual(limits["settlement_self_limit"], D("50.00"))

    def test_a_repository_without_settings_keeps_the_hard_coded_values(self):
        limits = InvoiceService._limits(InvoiceRepo(billing=None, settings=None))
        self.assertEqual(limits["old_gold_discount_default_percent"], D("1.50"))
        self.assertEqual(limits["min_old_gold_discount_pct"], D("1.00"))
        self.assertEqual(limits["max_old_gold_discount_pct"], D("5.00"))
        self.assertEqual(limits["settlement_self_limit"], D("50.00"))                              # the settlement defaults of 016

    def test_the_range_the_ui_is_shown_follows_the_configuration(self):
        limits = InvoiceService._limits(InvoiceRepo(settings={"min_old_gold_discount_pct": "0.50", "max_old_gold_discount_pct": "7.00"}))
        self.assertEqual((limits["min_old_gold_discount_pct"], limits["max_old_gold_discount_pct"]), (D("0.50"), D("7.00")))

    def test_a_discount_is_judged_against_the_configured_range(self):
        configured = InvoiceService._limits(InvoiceRepo(settings={"min_old_gold_discount_pct": "0.50", "max_old_gold_discount_pct": "7.00"}))
        self.assertEqual(InvoiceService._discount_problems(calculation([], ["6.00"]), None, configured), [])       # old gold only: 6% is inside 0.50-7.00
        self.assertEqual(InvoiceService._discount_problems(calculation([], ["0.50"]), None, configured), [])

        defaults = InvoiceService._limits(InvoiceRepo())
        self.assertEqual(len(InvoiceService._discount_problems(calculation([], ["6.00"]), None, defaults)), 1)     # the default maximum is still 5.00
        self.assertEqual(len(InvoiceService._discount_problems(calculation([], ["0.50"]), None, defaults)), 1)     # and the default minimum is 1.00

    def test_the_mixed_invoice_minimum_is_a_hard_rule_with_no_setting(self):
        configured = InvoiceService._limits(InvoiceRepo(settings={"min_old_gold_discount_pct": "0.00", "max_old_gold_discount_pct": "7.00"}))
        self.assertEqual(len(InvoiceService._discount_problems(calculation(["a line"], ["0.00"]), None, configured)), 1)   # mixed: never below 1.25
        self.assertEqual(InvoiceService._discount_problems(calculation(["a line"], ["1.25"]), None, configured), [])


# ------------------------------------------------------------------ the settings screen must not store what would be ignored

class WriterProblemTests(unittest.TestCase):

    def test_a_value_inside_the_range_is_accepted(self):
        self.assertIsNone(setting_rules.writer_problem("dead_stock_days", "60"))
        self.assertIsNone(setting_rules.writer_problem("dead_stock_days", "1"))            # the borders are inside
        self.assertIsNone(setting_rules.writer_problem("dead_stock_days", "3650"))
        self.assertIsNone(setting_rules.writer_problem("max_old_gold_discount_pct", "7.50"))

    def test_blank_is_refused_and_the_message_states_the_range_and_the_default(self):
        for text in (None, "", "   "):
            with self.subTest(text=text):
                problem = setting_rules.writer_problem("dead_stock_days", text)
                self.assertIsNotNone(problem)
                self.assertIn("1 و 3650", problem)
                self.assertIn("180", problem)

    def test_any_value_the_reader_would_silently_ignore_is_refused_by_the_writer(self):
        for text in ("0", "-5", "99999", "abc", "NaN", "Infinity"):                        # what `cast_and_check` alone would let through
            with self.subTest(text=text):
                self.assertIsNotNone(setting_rules.writer_problem("dead_stock_days", text))
        self.assertIsNotNone(setting_rules.writer_problem("velocity_days", "30.5"))        # a day count must be whole
        self.assertIsNotNone(setting_rules.writer_problem("max_old_gold_discount_pct", "400"))

    def test_a_key_the_business_rules_do_not_apply_is_not_this_modules_business(self):
        for key in ("shop_name", "enable_book_mode", "auto_login_enabled", "unknown_key"):
            self.assertIsNone(setting_rules.writer_problem(key, ""))                       # not even blank: only our nine keys
            self.assertIsNone(setting_rules.writer_problem(key, "anything"))


class WriteSession:
    def require_authenticated(self):
        return SimpleNamespace(user_id=1, permissions=())


class SetValueRefusalTests(unittest.TestCase):

    def service(self):
        service = SystemSettingsService(WriteSession())
        service._write = lambda _expected, _work: "written"                                # the refusal happens BEFORE the transaction
        return service

    def test_an_out_of_range_value_is_refused_with_the_range(self):
        with self.assertRaises(SettingsError) as caught:
            self.service().set_value("dead_stock_days", "0", "INT")
        self.assertEqual(caught.exception.code, "VALUE_RANGE")
        self.assertIn("3650", str(caught.exception))

    def test_the_type_check_still_runs_first_with_its_own_message(self):
        with self.assertRaises(SettingsError) as caught:
            self.service().set_value("dead_stock_days", "abc", "INT")
        self.assertEqual(caught.exception.code, "VALUE_INVALID")

    def test_a_good_value_reaches_the_write(self):
        self.assertEqual(self.service().set_value("dead_stock_days", "60", "INT"), "written")
        self.assertEqual(self.service().set_value("max_old_gold_discount_pct", "7.50", "DECIMAL"), "written")

    def test_a_key_outside_the_business_rules_keeps_the_old_behaviour(self):
        self.assertEqual(self.service().set_value("shop_name", "مجوهرات ماس", "STRING"), "written")
        self.assertEqual(self.service().set_value("some_future_key", "", "STRING"), "written")   # blank is our nine keys' business only


if __name__ == "__main__":
    unittest.main()
