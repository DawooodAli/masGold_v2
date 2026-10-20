"""The custody reads behind every trip screen: DISPLAY form (`current=False`) is plain snapshot reads with no locking clause anywhere, DECISION form
(`current=True`, also the default) keeps the outer row locks AND the nested reversal-lookup locks. The statements are recorded, not executed on a database;
the behaviour on MySQL is the subject of tests/test_custody_display_mysql.py."""
import ast
from pathlib import Path
import re
import unittest

from repositories.market_trip_repository import MarketTripRepository
from services.market_trip_service import MarketTripService

ROOT = Path(__file__).resolve().parents[1]
LOCKING = re.compile(r"FOR\s+(SHARE|UPDATE)|LOCK\s+IN\s+SHARE\s+MODE", re.IGNORECASE)
NESTED = " FOR SHARE)"


class Recorder:
    def __init__(self):
        self.statements = []

    def cursor(self, dictionary=False):
        outer = self

        class Cursor:
            lastrowid, rowcount = 1, 1

            def execute(self, sql, params=()):
                outer.statements.append(" ".join(sql.split()))

            def fetchone(self):
                return {"issued": 0, "returned": 0, "paid": 0, "proceeds": 0, "spent": 0}

            def fetchall(self):
                return []

            def close(self):
                pass
        return Cursor()


def recorded(call):
    connection = Recorder()
    repo = MarketTripRepository(connection)
    repo._settlement = False                                              # 060: the capability probe and the settlement reads are covered by their own tests
    repo._cash_settle = False                                                      # 064: the capability probe is not the subject of this test
    call(repo)
    return connection.statements


def custody(current):
    return lambda repo: MarketTripService._custody(repo, 5, current=current)


class DisplayFormTests(unittest.TestCase):
    def test_no_display_query_contains_a_locking_clause_outer_or_nested(self):
        for name, call in (("custody_cash", lambda r: r.custody_cash(5, current=False)), ("custody_gold", lambda r: r.custody_gold(5, current=False)),
                           ("custody_lots", lambda r: r.custody_lots(5, current=False)), ("custody_lots(lot)", lambda r: r.custody_lots(5, lot_id=3, current=False)),
                           ("_custody display", custody(False)), ("_custody default", lambda r: MarketTripService._custody(r, 5))):
            statements = recorded(call)
            self.assertTrue(statements, name)
            for sql in statements:
                self.assertIsNone(LOCKING.search(sql), (name, sql))
                self.assertIn("transaction_corrections", sql, name)                   # the reversal filter is still there, as a plain snapshot lookup

    def test_the_display_form_covers_every_query_of_the_custody_including_the_book_trades(self):
        cash = recorded(lambda r: r.custody_cash(5, current=False))
        self.assertEqual(len(cash), 5)
        self.assertTrue(any("market_trip_book_trades" in s for s in cash))
        gold = recorded(lambda r: r.custody_gold(5, current=False))
        self.assertEqual(len(gold), 7)


class DecisionFormTests(unittest.TestCase):
    def test_every_decision_query_keeps_the_outer_lock_and_the_nested_current_lookup(self):
        for name, call in (("custody_cash", lambda r: r.custody_cash(5, current=True)), ("custody_gold", lambda r: r.custody_gold(5, current=True)),
                           ("custody_lots", lambda r: r.custody_lots(5, current=True)), ("_custody decision", custody(True))):
            statements = recorded(call)
            self.assertTrue(statements, name)
            for sql in statements:
                self.assertIn(NESTED, sql, (name, "nested lookup must be a current read"))
                outer = sql.replace(NESTED, ")")
                self.assertRegex(outer, r"FOR SHARE", (name, "outer rows must be locked"))
                self.assertEqual(len(re.findall(r"FOR SHARE", sql)), 2, (name, sql))

    def test_the_repository_defaults_are_the_decision_form(self):
        """A caller that forgets the argument must stay locking, never silently downgrade to the display form."""
        for name in ("custody_cash", "custody_gold", "custody_lots"):
            explicit = recorded(lambda r: getattr(r, name)(5, current=True))
            default = recorded(lambda r: getattr(r, name)(5))
            self.assertEqual(default, explicit, name)

    def test_the_display_and_decision_forms_differ_only_by_their_locks(self):
        for name in ("custody_cash", "custody_gold", "custody_lots"):
            decision = [re.sub(r" FOR SHARE( OF [a-z, ]+)?", "", s) for s in recorded(lambda r: getattr(r, name)(5, current=True))]
            self.assertEqual(decision, recorded(lambda r: getattr(r, name)(5, current=False)), name)


class CallerAuditTests(unittest.TestCase):
    """The caller audit as a test: only the named DISPLAY callers read the custody without locks; every other caller states `current=True` or relies on the locking default."""
    DISPLAY_FUNCTIONS = {"trips", "trip_page", "trip_detail", "book_preview", "reconciliation_preview", "closure_preview", "preview_settle_melt"}      # R1 adds the read-only reconciliation preview

    @staticmethod
    def calls(path, attr):
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        found = []
        for function in ast.walk(tree):
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(function):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == attr:
                        keywords = {k.arg: k.value for k in node.keywords}
                        found.append((function.name, node.lineno, keywords))
        return found

    def test_the_service_helper_is_called_without_current_only_by_display_functions(self):
        seen = set()
        for path in ("services/market_trip_service.py", "services/market_trip_book_service.py"):
            for function, line, keywords in self.calls(path, "_custody"):
                current = keywords.get("current")
                if isinstance(current, ast.Constant) and current.value is True:
                    continue
                seen.add(function)
                self.assertIn(function, self.DISPLAY_FUNCTIONS, f"{path}:{line} reads the custody without locks inside a non-display function {function}")
        self.assertTrue(seen)

    def test_every_direct_repository_custody_read_in_services_is_explicit_or_the_locking_default(self):
        for path in ("services/market_trip_service.py", "services/market_trip_book_service.py", "services/correction_service.py", "services/intermediary_holding_service.py"):
            for attr in ("custody_cash", "custody_gold", "custody_lots"):
                for function, line, keywords in self.calls(path, attr):
                    current = keywords.get("current")
                    if current is None:
                        self.assertEqual(attr, "custody_lots", f"{path}:{line}")             # only the lots read is called by decisions through the locking default
                        continue
                    ok = ((isinstance(current, ast.Constant) and current.value is True) or (isinstance(current, ast.Name) and current.id == "current" and function == "_custody")
                          or (isinstance(current, ast.Constant) and current.value is False and function in self.DISPLAY_FUNCTIONS))      # the literal DISPLAY form, in a display function only
                    self.assertTrue(ok, f"{path}:{line} {attr}")


if __name__ == "__main__":
    unittest.main()
