import unittest
from security_guards import SlidingWindowLimiter


class SecurityGuardTests(unittest.TestCase):
    def test_window_and_per_user(self):
        now = [0.0]
        guard = SlidingWindowLimiter(2, 10, clock=lambda: now[0])
        self.assertTrue(guard.allow("a"))
        self.assertTrue(guard.allow("a"))
        self.assertFalse(guard.allow("a"))
        self.assertTrue(guard.allow("b"))
        now[0] = 10.0
        self.assertTrue(guard.allow("a"))

    def test_invalid_limits(self):
        for limit, window in [(0, 10), (1, 0)]:
            with self.assertRaises(ValueError):
                SlidingWindowLimiter(limit, window)


if __name__ == "__main__":
    unittest.main()
