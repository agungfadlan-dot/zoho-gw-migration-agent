"""
Unit tests for engine/rate_limiter.py
"""

import unittest
import time
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.rate_limiter import TokenBucket, retry_with_backoff


class TestRateLimiter(unittest.TestCase):

    def test_token_bucket_acquire(self):
        bucket = TokenBucket(rate_per_second=20.0, capacity=5.0)
        start = time.time()
        for _ in range(5):
            bucket.acquire(1.0)
        elapsed = time.time() - start
        self.assertLess(elapsed, 0.5)

    def test_retry_with_backoff_success(self):
        attempts = 0

        def flaky_func():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise ValueError("Transient network error")
            return "SUCCESS"

        result = retry_with_backoff(flaky_func, max_retries=4, initial_delay=0.05, retryable_exceptions=(ValueError,))
        self.assertEqual(result, "SUCCESS")
        self.assertEqual(attempts, 3)

    def test_retry_with_backoff_exhausted(self):
        def permanently_failing_func():
            raise RuntimeError("Permanent failure")

        with self.assertRaises(RuntimeError):
            retry_with_backoff(permanently_failing_func, max_retries=2, initial_delay=0.05)


if __name__ == "__main__":
    unittest.main()
