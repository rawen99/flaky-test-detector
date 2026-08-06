"""
Ground truth category: UNSEEDED_RANDOM

Generates test data with no fixed seed, so the assertion holds for most draws
but not all. Classic symptom: the failure message names a value that differs
every time the test fails.
"""
import random

from flake_config import rate


def test_unseeded_random():
    threshold = int(rate("random") * 100)
    value = random.randint(1, 100)
    assert value > threshold, f"random value {value} fell below threshold {threshold}"
