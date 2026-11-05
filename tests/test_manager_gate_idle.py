"""The manager page locks itself after 15 minutes without any input (owner 2026-11-04)."""
import unittest

from app.viewmodels.manager_gate_viewmodel import IDLE_LOCK_SECONDS, ManagerGateViewModel


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


class IdleLockTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.gate = ManagerGateViewModel(clock=self.clock)
        self.gate._unlocked = True
        self.gate.touch()

    def test_five_minutes_is_the_limit(self):
        self.assertEqual(IDLE_LOCK_SECONDS, 900)

    def test_it_stays_open_just_under_the_limit(self):
        self.clock.now += 899
        self.assertFalse(self.gate.checkIdle())
        self.assertTrue(self.gate.unlocked)

    def test_it_locks_at_five_minutes_without_input(self):
        self.clock.now += 900
        self.assertTrue(self.gate.checkIdle())
        self.assertFalse(self.gate.unlocked)

    def test_any_input_restarts_the_clock(self):
        self.clock.now += 890
        self.gate.touch()
        self.clock.now += 890
        self.assertFalse(self.gate.checkIdle())
        self.assertTrue(self.gate.unlocked)
        self.clock.now += 20
        self.assertTrue(self.gate.checkIdle())

    def test_a_locked_gate_is_untouched(self):
        self.gate.lock()
        self.clock.now += 1000
        self.assertFalse(self.gate.checkIdle())

    def test_a_successful_unlock_starts_the_clock_fresh(self):
        self.gate.lock()
        self.clock.now += 1000
        self.gate._request = 1
        self.gate._session = type("S", (), {"current_actor": lambda self: type("A", (), {"generation": 1})()})()
        self.gate._completed(1, "1", object(), None)
        self.assertTrue(self.gate.unlocked)
        self.clock.now += 100
        self.assertFalse(self.gate.checkIdle())


if __name__ == "__main__":
    unittest.main()
