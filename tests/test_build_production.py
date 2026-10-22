"""tools/build_production: the refusals that protect every other database (no database is contacted by these checks)."""
import unittest

from tools import build_production as build


class BuildProductionGuardTests(unittest.TestCase):
    def test_it_refuses_without_the_explicit_confirmation(self):
        self.assertEqual(build.main([]), 2)

    def test_it_never_targets_the_dev_old_or_system_databases(self):
        for name in ("masgold_v2", "golddb", "masgold_rehearsal", "mysql", "sys", "bad name", "Upper", ""):
            self.assertEqual(build.main(["--confirm", "--database", name]), 2, name)

    def test_it_refuses_an_invalid_account_name(self):
        self.assertEqual(build.main(["--confirm", "--user", "bad user;drop"]), 2)

    def test_the_application_account_gets_no_ddl_and_no_other_schema(self):
        for forbidden in ("ALL", "DROP", "CREATE", "ALTER", "GRANT OPTION", "*.*"):
            self.assertNotIn(forbidden, build.GRANTS)


if __name__ == "__main__":
    unittest.main()
