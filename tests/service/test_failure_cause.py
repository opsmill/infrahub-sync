"""FR-001: a destination schema read failure records a cause and a next action the reader acts on."""

from __future__ import annotations

import pytest

from infrahub_sync.client.models import MAX_FAILURE_CAUSE_TEXT
from infrahub_sync.runtime_schema import DestinationSchemaUnavailableError
from infrahub_sync.runtime_schema.errors import DestinationBranchNotFoundError
from infrahub_sync.service.failure_cause import operator_cause

_REASONS = ("timeout", "unreachable", "unauthorized", "forbidden", "credentials", "unconfigured", "rejected")


def test_a_missing_branch_records_the_branch_and_the_fix() -> None:
    recorded = operator_cause(DestinationBranchNotFoundError(branch="demo-test"), [])

    assert recorded["cause"] == "Branch `demo-test` does not exist in Infrahub."
    assert recorded["next_action"] == "Create it, or start the run with `--branch <existing branch>`."


@pytest.mark.parametrize("reason", _REASONS)
def test_every_schema_read_reason_records_a_cause_and_a_next_action(reason: str) -> None:
    recorded = operator_cause(DestinationSchemaUnavailableError(reason=reason, branch="demo-test"), [])

    assert "`demo-test`" in recorded["cause"]
    assert recorded["next_action"]
    assert "prefect" not in f"{recorded['cause']} {recorded['next_action']}".lower()


@pytest.mark.parametrize("reason", _REASONS)
def test_the_reasons_do_not_share_one_cause(reason: str) -> None:
    others = {
        operator_cause(DestinationSchemaUnavailableError(reason=other, branch="b"), [])["cause"]
        for other in _REASONS
        if other != reason
    }

    assert operator_cause(DestinationSchemaUnavailableError(reason=reason, branch="b"), [])["cause"] not in others


def test_a_schema_read_failure_wrapped_by_a_stage_is_still_described() -> None:
    # A plan wraps a side's load failure in a `ValueError` raised `from` the original.
    outer = ValueError("destination side could not be loaded")
    outer.__cause__ = DestinationBranchNotFoundError(branch="demo-test")

    recorded = operator_cause(outer, [])

    assert recorded["cause"] == "Branch `demo-test` does not exist in Infrahub."


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(DestinationBranchNotFoundError(branch="demo-s3cr3t-value"), id="branch-missing"),
        pytest.param(DestinationSchemaUnavailableError(reason="timeout", branch="demo-s3cr3t-value"), id="timeout"),
        pytest.param(
            DestinationSchemaUnavailableError(
                reason="forbidden", branch="main", detail="missing permission `s3cr3t-value`"
            ),
            id="forbidden-detail",
        ),
    ],
)
def test_the_cause_and_next_action_are_redacted(error: DestinationSchemaUnavailableError) -> None:
    recorded = operator_cause(error, ["s3cr3t-value"])

    assert "s3cr3t-value" not in recorded["cause"]
    assert "s3cr3t-value" not in recorded["next_action"]


@pytest.mark.parametrize("reason", [*_REASONS, "nobranch"])
def test_the_cause_and_next_action_are_capped(reason: str) -> None:
    branch = "b" * (MAX_FAILURE_CAUSE_TEXT * 2)
    error = (
        DestinationBranchNotFoundError(branch=branch)
        if reason == "nobranch"
        else DestinationSchemaUnavailableError(reason=reason, branch=branch)
    )

    recorded = operator_cause(error, [])

    assert len(recorded["cause"]) <= MAX_FAILURE_CAUSE_TEXT
    assert len(recorded["next_action"]) <= MAX_FAILURE_CAUSE_TEXT


def test_an_unknown_reason_still_records_a_cause_and_a_next_action() -> None:
    recorded = operator_cause(DestinationSchemaUnavailableError(reason="novel", branch="demo-test"), [])

    assert "`demo-test`" in recorded["cause"]
    assert recorded["next_action"]
