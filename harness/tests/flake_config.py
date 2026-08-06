"""
Tunable flakiness configuration.

Every injected fault reads its failure probability from here, so the harness
can be dialled up or down without editing the tests. This matters for
evaluation: you can measure how detection accuracy changes as flakiness
becomes rarer (a rare flaky test is much harder to catch in N runs).

Override globally:      FLAKE_RATE=0.1 pytest
Override per category:  FLAKE_RATE_ASYNC=0.5 pytest
"""

import os

DEFAULT_RATE = float(os.environ.get("FLAKE_RATE", "0.3"))


def rate(category: str) -> float:
    """Failure probability for a given taxonomy category."""
    key = f"FLAKE_RATE_{category.upper()}"
    return float(os.environ.get(key, DEFAULT_RATE))
