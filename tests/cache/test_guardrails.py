"""RowcountGuardrail: refuses a per-resource count collapse against a caller's mapping."""

from __future__ import annotations

import pytest

from infrahub_sync.cache.guardrails import (
    RowcountGuardrail,
    RowcountGuardrailError,
)


def test_a_resource_absent_from_the_caller_mapping_is_allowed() -> None:
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


def test_the_refusal_names_the_interface_that_actually_accepts_a_drop() -> None:
    """The remediation has to be reachable: there is no operator flag for this.

    The filesystem baseline this primitive once read is deleted and no command exposes an
    override, so a message naming `--allow-rowcount-drop` would send a reader looking for
    something that does not exist. The only way to accept a drop is the field a caller
    constructs the guardrail with.
    """
    guardrail = RowcountGuardrail(previous={"BuiltinTag": 100}, drop_threshold=0.5)

    with pytest.raises(RowcountGuardrailError) as failure:
        guardrail.check("BuiltinTag", current=10)

    message = str(failure.value)
    assert "allow_drop=True" in message
    assert "--allow-rowcount-drop" not in message
    assert "last-successful-rowcounts" not in message


def test_the_primitive_claims_no_persisted_baseline_and_no_operator_flag() -> None:
    """Its own prose must not describe state it does not read or a flag that is gone."""
    from pathlib import Path

    from infrahub_sync.cache import guardrails

    source = Path(guardrails.__file__ or "").read_text(encoding="utf-8")

    for stale in ("last-successful-rowcounts.json", "--allow-rowcount-drop", "the next run loads"):
        assert stale not in source, stale


def test_the_primitive_reads_no_file() -> None:
    """A check against a caller's mapping touches no filesystem at all."""
    guardrail = RowcountGuardrail(previous={"BuiltinTag": 100})

    def refuse_open(*_args: object, **_kwargs: object) -> None:
        message = "the row-count comparison must not open anything"
        raise AssertionError(message)

    import builtins

    original = builtins.open
    builtins.open = refuse_open  # ty: ignore[invalid-assignment]
    try:
        guardrail.check("BuiltinTag", current=100)
    finally:
        builtins.open = original  # ty: ignore[invalid-assignment]
