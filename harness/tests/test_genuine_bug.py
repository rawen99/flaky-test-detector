"""
Ground truth category: NOT FLAKY -- a genuine, deterministic bug.

Included in the harness deliberately as a DISCRIMINATING case for the
comparative evaluation. Any detector that reports this as flaky would be
teaching developers to ignore a real failure.
"""


def test_genuine_bug():
    # A real defect: this assertion is simply wrong, every time.
    assert 1 + 1 == 3
