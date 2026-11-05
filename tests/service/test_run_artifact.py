"""The Prefect markdown artifact that ends each service stage.

The artifact names the run, the stage, its outcome, its configuration and destination branch,
its operation counts and the CLI commands that show the run. It is built from the run record's
named fields only, and redacted with the stage's secrets before it is published.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

import pytest

from infrahub_sync.execution import ACTION_KEYS
from infrahub_sync.product_store.models import ArtifactReference, ProductRun
from infrahub_sync.service.run_artifact import artifact_key, publish_run_artifact, run_markdown

if TYPE_CHECKING:
    from collections.abc import Callable

    from infrahub_sync.product_store import ProductProjection

_RUN_ID = "20261105T1514-f62dfb83"
_NOW = datetime(2026, 11, 5, 15, 14, tzinfo=timezone.utc)


def _run(
    *,
    operation: str = "plan",
    summary: dict[str, Any] | None = None,
    results: dict[str, Any] | None = None,
    artifact_refs: tuple[ArtifactReference, ...] = (),
    destination_branch: str | None = "b1",
) -> ProductRun:
    return ProductRun(
        run_id=_RUN_ID,
        operation=cast("Any", operation),
        configuration_reference="config-001@1",
        config_id="config-001",
        registry_version=1,
        package_checksum="a" * 64,
        started_at=_NOW,
        phase="planned",
        outcome="planned",
        summary=summary or {},
        results=results or {},
        destination_branch=destination_branch,
        artifact_refs=artifact_refs,
    )


def _review_reference() -> ArtifactReference:
    return ArtifactReference(
        artifact_id="plan",
        run_id=_RUN_ID,
        kind="saved-plan-review",
        media_type="application/json",
        digest="b" * 64,
        size=10,
        object_key=f"runs/{'b' * 64}/plan.json",
        manifest_key=f"runs/{'b' * 64}/manifest.json",
        created_at=_NOW,
    )


# --- artifact_key (T015) ----------------------------------------------------------------------


def test_artifact_key_lowercases_the_run_id() -> None:
    assert artifact_key(_RUN_ID, "plan") == "sync-run-20261105t1514-f62dfb83-plan"


@pytest.mark.parametrize(
    ("run_id", "expected"),
    [
        pytest.param("run_1.a", "sync-run-run-1-a-apply", id="other-characters-become-dashes"),
        pytest.param("run--__..x", "sync-run-run-x-apply", id="repeated-dashes-collapse"),
        pytest.param("run_", "sync-run-run-apply", id="dash-before-stage-collapses"),
    ],
)
def test_artifact_key_keeps_only_lowercase_letters_digits_and_dashes(run_id: str, expected: str) -> None:
    assert artifact_key(run_id, "apply") == expected


def test_artifact_key_strips_leading_and_trailing_dashes() -> None:
    assert artifact_key("x", "_plan_") == "sync-run-x-plan"


# --- run_markdown (T016) ----------------------------------------------------------------------

_PLAN_MARKDOWN = """# Sync run 20261105T1514-f62dfb83

- Stage: plan
- Outcome: planned
- Configuration: config-001@1
- Destination branch: b1
- Operations by action: create 2, delete 1
- Operations by kind: A 2, B 1

## Next steps

- `infrahub-sync runs show 20261105T1514-f62dfb83`
"""


def test_plan_run_markdown_shows_configuration_branch_and_sorted_counts() -> None:
    run = _run(summary={"by_action": {"create": 2, "delete": 1}, "by_kind": {"B": 1, "A": 2}})

    assert run_markdown(run, "plan", "planned", None) == _PLAN_MARKDOWN


def test_apply_run_markdown_shows_its_per_action_counts() -> None:
    run = _run(operation="apply", summary=dict(zip(ACTION_KEYS, (3, 0, 1), strict=True)))

    markdown = run_markdown(run, "apply", "applied", None)

    assert "- Operations by action: create 3, update 0, delete 1\n" in markdown
    assert "Operations by kind" not in markdown


def test_run_without_counts_shows_no_counts_line() -> None:
    markdown = run_markdown(_run(destination_branch=None), "plan", "failed", None)

    assert "Operations" not in markdown
    assert "Destination branch" not in markdown


def test_saved_plan_review_adds_the_plan_detail_command_after_runs_show() -> None:
    markdown = run_markdown(_run(artifact_refs=(_review_reference(),)), "plan", "planned", None)

    assert markdown.endswith(
        f"## Next steps\n\n- `infrahub-sync runs show {_RUN_ID}`\n- `infrahub-sync runs plan {_RUN_ID} --detail`\n"
    )


def test_next_action_is_the_last_next_step() -> None:
    markdown = run_markdown(_run(artifact_refs=(_review_reference(),)), "plan", "failed", "Re-run `plan`.")

    assert markdown.splitlines()[-1] == "- Re-run `plan`."


def test_failure_cause_never_appears_in_the_markdown() -> None:
    run = _run(results={"plan_failure": {"cause": "provider said: connection refused at 10.0.0.1"}})

    markdown = run_markdown(run, "plan", "failed", None)

    assert "connection refused" not in markdown
    assert "10.0.0.1" not in markdown


# --- publish_run_artifact (T017) --------------------------------------------------------------


class _Projection:
    def __init__(self, run: ProductRun | None) -> None:
        self._run = run

    def lookup_run(self, run_id: str) -> object:
        assert run_id == _RUN_ID
        return type("Lookup", (), {"value": self._run})()


class _RecordingLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    def log(self, level: int, msg: str, *args: object) -> None:
        self.calls.append((level, msg % args if args else msg))

    def info(self, msg: str, *args: object) -> None:
        self.calls.append((logging.INFO, msg % args if args else msg))


def _publish(
    run: ProductRun | None, create: Callable[..., object], logger: _RecordingLogger, secrets: list[str]
) -> None:
    publish_run_artifact(
        cast("ProductProjection", _Projection(run)),
        _RUN_ID,
        "plan",
        outcome="planned",
        next_action=None,
        secrets=secrets,
        run_logger=logger,
        create=create,
    )


def test_publish_creates_one_redacted_markdown_artifact() -> None:
    created: list[dict[str, Any]] = []
    logger = _RecordingLogger()
    run = _run(summary={"by_kind": {"s3cr3t-token": 1}})

    _publish(run, lambda **kwargs: created.append(kwargs), logger, ["s3cr3t-token"])

    assert len(created) == 1
    assert created[0]["key"] == "sync-run-20261105t1514-f62dfb83-plan"
    assert created[0]["description"] == f"Sync run {_RUN_ID}, stage plan"
    assert "s3cr3t-token" not in created[0]["markdown"]
    assert "- Operations by kind: *** 1\n" in created[0]["markdown"]
    assert logger.calls == []


def test_a_failed_publish_logs_one_warning_without_the_error_text() -> None:
    logger = _RecordingLogger()

    def create(**_kwargs: object) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    _publish(_run(), create, logger, [])

    assert logger.calls == [(logging.WARNING, "run summary artifact could not be published (RuntimeError)")]


def test_a_missing_run_logs_one_warning_and_publishes_nothing() -> None:
    created: list[dict[str, Any]] = []
    logger = _RecordingLogger()

    _publish(None, lambda **kwargs: created.append(kwargs), logger, [])

    assert created == []
    assert len(logger.calls) == 1
    assert logger.calls[0][0] == logging.WARNING
