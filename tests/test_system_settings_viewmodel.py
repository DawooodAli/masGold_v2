import unittest

from app.viewmodels.system_settings_viewmodel import SystemSettingsViewModel


class FakeService:
    def __init__(self, can):
        self.can = can

    def can_manage(self):
        return self.can


class SystemSettingsViewModelTests(unittest.TestCase):
    def test_the_card_is_available_to_an_owner_before_any_refresh(self):
        # Regression (2026-10-28): canManage waited for a refresh that only ran once the card was visible, so the entry never appeared.
        self.assertTrue(SystemSettingsViewModel(object(), FakeService(True)).canManage)

    def test_it_stays_hidden_without_the_right_or_the_service(self):
        self.assertFalse(SystemSettingsViewModel(object(), FakeService(False)).canManage)
        self.assertFalse(SystemSettingsViewModel(None, None).canManage)


if __name__ == "__main__":
    unittest.main()
