"""Which settings file a run uses: the development `.env` by default, a plain `.env.<name>` file of the project folder when named, never anything else."""
import unittest

from config.database_config import PROJECT_ROOT, env_file_path


class EnvFileTests(unittest.TestCase):
    def test_the_default_is_the_development_file(self):
        self.assertEqual(env_file_path({}), PROJECT_ROOT / ".env")
        self.assertEqual(env_file_path({"MASGOLD_ENV_FILE": ""}), PROJECT_ROOT / ".env")

    def test_a_named_production_file_is_used(self):
        self.assertEqual(env_file_path({"MASGOLD_ENV_FILE": ".env.production"}), PROJECT_ROOT / ".env.production")

    def test_anything_that_is_not_a_plain_env_file_is_ignored(self):
        for value in ("../.env", "C:/x/.env", "config.py", ".env.a/b", ".envX", ".env."):
            self.assertEqual(env_file_path({"MASGOLD_ENV_FILE": value}), PROJECT_ROOT / ".env", value)


if __name__ == "__main__":
    unittest.main()
