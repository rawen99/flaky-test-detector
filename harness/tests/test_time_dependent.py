"""
Ground truth category: TIME_DEPENDENT

Asserts against wall-clock time. Real time-dependent tests fail at particular
moments (midnight, month end, DST); here the boundary is scaled so the failure
occurs at a controllable frequency while keeping a genuine datetime signature.
"""
from datetime import datetime

from flake_config import rate


def test_time_dependent():
    now = datetime.now()
    boundary = int((1 - rate("time")) * 1_000_000)
    assert now.microsecond < boundary, f"datetime boundary crossed at {now.isoformat()}"
