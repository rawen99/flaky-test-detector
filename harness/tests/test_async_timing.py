"""
Ground truth category: ASYNC_TIMING

Simulates checking the result of an async operation before it has reliably
finished. The delay is random, so the check sometimes wins the race and
sometimes loses it -- same code, different outcome.
"""
import random
import time

from flake_config import rate


def flaky_async_operation():
    delay = random.uniform(0, 0.05)
    time.sleep(delay)
    return delay < 0.05 * (1 - rate("async"))


def test_async_timing():
    result = flaky_async_operation()
    assert result is True
