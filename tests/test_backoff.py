import unittest

from scripts.backoff import retry_delays, total_wait


class DelaysTest(unittest.TestCase):
    def test_delays_double_from_the_base(self):
        self.assertEqual(retry_delays(4), [1.0, 2.0, 4.0, 8.0])

    def test_the_total_is_the_sum_of_the_delays(self):
        self.assertEqual(total_wait(4), 15.0)
