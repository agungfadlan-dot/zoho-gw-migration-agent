"""
Rate Limiter & Resilient Retry Engine.

Security & Operational Guardrails:
- Token bucket algorithm for API throttling.
- Exponential backoff with full jitter to handle HTTP 429 & transient errors.
- Dynamic concurrency control to respect Google & Zoho API quotas.
"""

import time
import random
import threading
import asyncio
from typing import Callable, Any, TypeVar, Optional

T = TypeVar("T")


class TokenBucket:
    """Thread-safe Token Bucket Rate Limiter."""

    def __init__(self, rate_per_second: float = 10.0, capacity: float = 20.0):
        self.rate = rate_per_second
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = time.time()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> None:
        """Blocks until required tokens are available."""
        while True:
            with self._lock:
                now = time.time()
                elapsed = now - self.last_update
                self.last_update = now
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)

                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return

                wait_time = (tokens - self.tokens) / self.rate

            time.sleep(max(0.01, wait_time))

    async def acquire_async(self, tokens: float = 1.0) -> None:
        """Asynchronous token acquisition."""
        while True:
            with self._lock:
                now = time.time()
                elapsed = now - self.last_update
                self.last_update = now
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)

                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return

                wait_time = (tokens - self.tokens) / self.rate

            await asyncio.sleep(max(0.01, wait_time))


def retry_with_backoff(
    func: Callable[..., T],
    max_retries: int = 5,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 30.0,
    retryable_exceptions: tuple = (Exception,)
) -> T:
    """Executes a function with exponential backoff and jitter."""
    attempt = 0
    while attempt < max_retries:
        try:
            return func()
        except retryable_exceptions as exc:
            attempt += 1
            if attempt >= max_retries:
                raise exc

            # Calculate exponential backoff with full jitter
            delay = min(max_delay, initial_delay * (backoff_factor ** (attempt - 1)))
            jittered_delay = random.uniform(delay * 0.5, delay * 1.5)
            time.sleep(jittered_delay)


async def retry_with_backoff_async(
    coro_func: Callable[..., Any],
    *args: Any,
    max_retries: int = 5,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 30.0,
    retryable_exceptions: tuple = (Exception,),
    **kwargs: Any
) -> Any:
    """Executes an async function with exponential backoff and jitter."""
    attempt = 0
    while attempt < max_retries:
        try:
            return await coro_func(*args, **kwargs)
        except retryable_exceptions as exc:
            attempt += 1
            if attempt >= max_retries:
                raise exc

            delay = min(max_delay, initial_delay * (backoff_factor ** (attempt - 1)))
            jittered_delay = random.uniform(delay * 0.5, delay * 1.5)
            await asyncio.sleep(jittered_delay)
