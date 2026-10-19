"""RowcountGuardrail: refuses to proceed when a resource's row count collapses
since the last successful run."""

from __future__ import annotations

import pytest

from infrahub_sync.cache.guardrails import (
    RowcountGuardrail,
    RowcountGuardrailError,
)


def test_first_run_with_no_prior_baseline_allowed() -> None:
    g = RowcountGuardrail(previous={}, drop_threshold=0.5)
    g.check("BuiltinTag", current=10)


def test_no_drop_allowed() -> None:
    g = RowcountGuardrail(previous={"BuiltinTag": 100}, drop_threshold=0.5)
    g.check("BuiltinTag", current=100)
    g.check("BuiltinTag", current=200)
    g.check("BuiltinTag", current=51)  # exactly above the 50% threshold


def test_drop_over_threshold_raises() -> None:
    g = RowcountGuardrail(previous={"BuiltinTag": 100}, drop_threshold=0.5)
    with pytest.raises(RowcountGuardrailError, match="dropped from 100 to 49"):
        g.check("BuiltinTag", current=49)


def test_allow_override_skips_check() -> None:
    g = RowcountGuardrail(previous={"BuiltinTag": 100}, drop_threshold=0.5, allow_drop=True)
    g.check("BuiltinTag", current=0)  # no raise
