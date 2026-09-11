import concurrent.futures
from pathlib import Path
import tempfile
import unittest
from budget_guard import reserve_attempt, record_outcome, TEST_LIMIT_MICRO_USD, ATTEMPT_RESERVATION_MICRO_USD


class BudgetTests(unittest.TestCase):
    def test_uncertain_requests_stay_reserved_across_reopens(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.sqlite'
            for i in range(TEST_LIMIT_MICRO_USD // ATTEMPT_RESERVATION_MICRO_USD):
                reserve_attempt(path, str(i))
                record_outcome(path, str(i), 'uncertain')
            with self.assertRaises(ValueError):
                reserve_attempt(path, 'eleventh')
            with self.assertRaises(ValueError):
                reserve_attempt(path, '0')

    def test_concurrent_attempts_cannot_overbook(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.sqlite'
            def attempt(i):
                try:
                    return reserve_attempt(path, str(i))
                except ValueError:
                    return None
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(attempt, range(TEST_LIMIT_MICRO_USD // ATTEMPT_RESERVATION_MICRO_USD + 14)))
            self.assertEqual(sum(result is not None for result in results), TEST_LIMIT_MICRO_USD // ATTEMPT_RESERVATION_MICRO_USD)

    def test_no_automatic_refund_or_duplicate_finalization(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.sqlite'
            reserve_attempt(path, 'one')
            record_outcome(path, 'one', 'failed')
            with self.assertRaises(ValueError):
                record_outcome(path, 'one', 'completed')
            self.assertEqual(reserve_attempt(path, 'two')['remaining_micro_usd'], TEST_LIMIT_MICRO_USD - 2 * ATTEMPT_RESERVATION_MICRO_USD)


if __name__ == '__main__':
    unittest.main()
