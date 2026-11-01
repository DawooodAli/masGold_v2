"""The backup folder is changeable from Settings (owner 2026-11-01): the stored choice, its checks, the owner-only service and the backup tool using it. No database, no real drive other than C:."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from config import backup_settings
from services.backup_location import BackupLocationService

MISSING_DRIVE = "Q:" + chr(92) + "no_such_drive"


class Session:
    class Actor:
        def __init__(self, permissions):
            self.permissions, self.generation = frozenset(permissions), 1

    def __init__(self, permissions):
        self.actor = self.Actor(permissions)

    def require_authenticated(self):
        return self.actor

    def current_actor(self):
        return self.actor

    def is_current(self, actor):
        return actor is self.actor


class SettingsFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.file = Path(self.dir.name) / "backup_settings.json"

    def test_without_a_file_the_default_folder_is_used(self):
        self.assertEqual(backup_settings.destination(self.file), backup_settings.DEFAULT_DEST)
        self.file.write_text("not json", encoding="utf-8")
        self.assertEqual(backup_settings.destination(self.file), backup_settings.DEFAULT_DEST)       # a damaged file never breaks the backup

    def test_save_creates_the_folder_and_the_choice_is_read_back(self):
        target = Path(self.dir.name) / "backups" / "daily"
        stored = backup_settings.save(str(target), self.file)
        self.assertTrue(target.is_dir())
        self.assertEqual(backup_settings.destination(self.file), stored)
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8"))["dest"], str(target))

    def test_bad_folders_are_refused_and_nothing_is_saved(self):
        for bad in ("", "relative/folder", MISSING_DRIVE):
            with self.assertRaises(ValueError):
                backup_settings.save(bad, self.file)
        self.assertFalse(self.file.exists())
        self.assertFalse(backup_settings.reachable(MISSING_DRIVE))


class ServiceTests(unittest.TestCase):
    def test_only_the_settings_right_may_read_save_or_test(self):
        service = BackupLocationService(Session({"APP_LOGIN"}))
        self.assertFalse(service.can_manage())
        for call in (service.current, lambda: service.save("C:/x"), lambda: service.test("C:/x")):
            with self.assertRaises(PermissionError):
                call()

    def test_current_reports_an_unreachable_drive_and_test_does_not_save(self):
        fake = mock.Mock(DEFAULT_DEST=Path(MISSING_DRIVE))
        fake.destination.return_value = Path(MISSING_DRIVE)
        fake.reachable.return_value = False
        fake.check.return_value = (True, "ok")
        service = BackupLocationService(Session({"SETTINGS_MANAGE"}), fake)
        view = service.current()
        self.assertFalse(view["ok"])
        self.assertIn("Q:", view["message"])
        self.assertTrue(service.test("D:/x")["ok"])
        fake.save.assert_not_called()


class BackupToolTests(unittest.TestCase):
    def test_the_tool_writes_to_the_saved_folder_unless_dest_is_given(self):
        from tools import backup_database
        seen = []

        def fake_dump(database, temp, dump):
            seen.append(Path(temp).parent)
            Path(temp).write_bytes(b"x")

        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(backup_settings, "destination", return_value=Path(folder)), \
                mock.patch.object(backup_database, "run_dump", side_effect=fake_dump), \
                mock.patch.object(backup_database, "verify", return_value=10), \
                mock.patch("config.database_config.DatabaseConfig.from_env", return_value=mock.Mock(database="masgold_test")):
            backup_database.main([])
        self.assertEqual(seen, [Path(folder)])


if __name__ == "__main__":
    unittest.main()
