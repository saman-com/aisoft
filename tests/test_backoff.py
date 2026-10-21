import unittest

from scripts.backoff import retry_delays, should_retry, total_wait


class DelaysTest(unittest.TestCase):
    def test_delays_double_from_the_base(self):
        self.assertEqual(retry_delays(4), [1.0, 2.0, 4.0, 8.0])
