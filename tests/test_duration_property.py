"""
tests/test_duration_property.py
Property 5: Projected Query Duration Formula Correctness
Validates: Requirements 9.3, 9.4

Formula:
projected_duration_ms = baseline_ms * (projected_rows / current_rows)
at_risk = True iff projected_duration_ms >= AT_RISK_FACTOR * baseline_ms
where AT_RISK_FACTOR defaults to 1.25
"""

import math
import pytest
from hypothesis import given, settings, strategies as st

DEFAULT_AT_RISK_FACTOR = 1.25


def compute_projected_query_duration(
    baseline_ms: float,
    current_rows: int,
    projected_rows: int,
    at_risk_factor: float = DEFAULT_AT_RISK_FACTOR
):
    """Computes projected query duration and at-risk assessment."""
    if current_rows <= 0:
        raise ValueError("current_rows must be positive")

    projected_duration_ms = baseline_ms * (float(projected_rows) / float(current_rows))
    at_risk = projected_duration_ms >= (at_risk_factor * baseline_ms)
    return projected_duration_ms, at_risk


@settings(max_examples=100)
@given(
    baseline_ms=st.floats(min_value=0.1, max_value=100000.0, allow_nan=False, allow_infinity=False),
    current_rows=st.integers(min_value=1, max_value=10_000_000),
    projected_rows=st.integers(min_value=1, max_value=50_000_000),
    at_risk_factor=st.floats(min_value=1.01, max_value=5.0, allow_nan=False, allow_infinity=False),
)
def test_projected_duration_formula(baseline_ms, current_rows, projected_rows, at_risk_factor):
    proj_duration, at_risk = compute_projected_query_duration(
        baseline_ms, current_rows, projected_rows, at_risk_factor
    )

    # 1. Exact formula check within floating-point tolerance
    expected_duration = baseline_ms * (projected_rows / current_rows)
    assert math.isclose(proj_duration, expected_duration, rel_tol=1e-9, abs_tol=1e-9)

    # 2. at_risk check: True iff proj_duration >= at_risk_factor * baseline_ms
    expected_at_risk = proj_duration >= (at_risk_factor * baseline_ms)
    assert at_risk == expected_at_risk

    # 3. Ratio relationship: iff projected_rows / current_rows >= at_risk_factor, at_risk must be True
    ratio = projected_rows / current_rows
    if ratio >= at_risk_factor:
        assert at_risk is True
    else:
        assert at_risk is False


if __name__ == "__main__":
    pytest.main(["-v", __file__])
