"""CLI `diff` and remote `operation=plan` must agree exactly (DBA-009, SC-007).

Both callers go through `execution.execute_run`, so on reset copies of the same
fixture they must report the same `status`, `changed` flag and per-action summary,
and their plan directories must produce the same canonical fingerprint.

The engine here is a BEHAVIORAL fake, not a `MagicMock`: `write_plan` really writes
rows through `cache.parquet_io.write_plan`, because `compute_plan_fingerprint` reads
`plan.parquet` from disk and a no-op `write_plan` would leave nothing to compare.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from infrahub_sync.cache.fingerprint import compute_plan_fingerprint
from infrahub_sync.cache.parquet_io import write_plan
from infrahub_sync.cli import app
from infrahub_sync.execution import RunResult, execute_run, run_remote_request

if TYPE_CHECKING:
    from infrahub_sync import SyncInstance

# Module-scope alias: inside `_FakeDiff` the name `str` resolves to that class's own
# diff-rendering method, which makes `list[dict[str, Any]]` unusable as an annotation there.
PlanRows = list[dict[str, Any]]

SYNC_NAME = "parity-example"
CLI_RUN_ID = "20260731T1200-c1i0c1i0"
REMOTE_RUN_ID = "20260731T1201-4e307e11"

CONFIG = f"""
name: {SYNC_NAME}
source:
  name: mockdb
  settings:
    url: http://localhost:9999
destination:
  name: infrahub
  settings:
    url: http://localhost:8000
schema_mapping:
  - name: InfraDevice
    mapping: device
    identifiers: [name]
    fields:
      - name: name
        mapping: name
"""

PLAN_ROW_DEFAULTS = {
    "dest_id": None,
    "attribute": None,
    "old_value": None,
    "new_value": None,
    "owner": None,
    "skip_reason": None,
    "conflict_class": None,
}

# The fixture's unchanging source state: five devices to create, one attribute
# update, one deletion — every action key non-zero so an all-zero summary can
# never pass by accident.
FIXTURE_ROWS: PlanRows = [
    *(
        {"action": "create", "resource": "InfraDevice", "source_id": name, **PLAN_ROW_DEFAULTS}
        for name in ("core01", "core02", "core03", "edge01", "edge02")
    ),
    {
        "action": "update",
        "resource": "InfraDevice",
        "source_id": "core01",
        **PLAN_ROW_DEFAULTS,
        "attribute": "description",
        "old_value": "old",
        "new_value": "new",
    },
    {"action": "delete", "resource": "InfraDevice", "source_id": "retired01", **PLAN_ROW_DEFAULTS},
]


class _FakeDiff:
    """The diff surface the lifecycles touch, with a controllable row set."""

    def __init__(self, rows: PlanRows, *, has_diffs: bool | None = None) -> None:
        self.rows = rows
        self._has_diffs = bool(rows) if has_diffs is None else has_diffs

    def has_diffs(self) -> bool:
        return self._has_diffs

    def str(self) -> str:  # ty: ignore[invalid-type-form]
        return f"fake-diff({len(self.rows)} rows)"


class _FakePotenda:
    """A behavioral fake engine: `write_plan` really materializes `plan.parquet`."""

    def __init__(self, *, run_dir: Path, rows: PlanRows, has_diffs: bool | None = None) -> None:
        self.run_dir = run_dir
        self.run_id = run_dir.name
        self.top_level = ["InfraDevice"]
        self.force_full_extract = False
        self.rows = rows
        self.has_diffs_override = has_diffs
        self.synced = False

    def load_both_sides(self) -> None:
        """No source to read — the fixture is the row list itself."""

    def diff(self) -> _FakeDiff:
        return _FakeDiff(list(self.rows), has_diffs=self.has_diffs_override)

    def _diff_to_rows(self, diff: _FakeDiff) -> PlanRows:  # noqa: PLR6301 - mirrors Potenda's API
        return list(diff.rows)

    def write_plan(self, diff: _FakeDiff) -> None:
        write_plan(run_dir=self.run_dir, rows=self._diff_to_rows(diff))

    def check_rowcount_guardrail(self, *, allow_drop: bool) -> None:
        """Guardrails are not what this comparison is about."""

    def sync(self, diff: _FakeDiff | None = None) -> None:  # noqa: ARG002 - keyword name is part of the API
        self.synced = True

    def persist_baseline_counts(self) -> None:
        """No baseline to persist for a fake destination."""


def _factory(run_dir: Path, rows: PlanRows, *, has_diffs: bool | None = None) -> Any:  # noqa: ANN401
    """A factory that ignores the pinned kwargs and yields one prepared fake engine."""
    run_dir.mkdir(parents=True, exist_ok=True)
    engine = _FakePotenda(run_dir=run_dir, rows=rows, has_diffs=has_diffs)

    def build(**_kwargs: object) -> Any:  # noqa: ANN401 - a fake engine, not a real Potenda
        return engine

    return build


class _ResultObserver:
    """Wrap the real `execute_run` so the CLI's discarded `RunResult` is observable.

    `diff_cmd` returns nothing, so the only honest way to compare its result fields
    with the remote caller's is to watch what the real surface returned.
    """

    def __init__(self) -> None:
        self.results: list[RunResult] = []

    def __call__(self, *args: Any, **kwargs: Any) -> RunResult:  # noqa: ANN401 - passthrough wrapper
        result = execute_run(*args, **kwargs)
        self.results.append(result)
        return result


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """A configuration directory holding the one fixture `config.yml`."""
    target = tmp_path / "configs" / SYNC_NAME
    target.mkdir(parents=True)
    (target / "config.yml").write_text(CONFIG, encoding="utf-8")
    return tmp_path / "configs"


@pytest.fixture
def cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the cache — and therefore the pipeline lock — at the temp directory."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path / "cache" / SYNC_NAME


def test_cli_diff_and_remote_plan_agree_on_result_and_fingerprint(config_dir: Path, cache_root: Path) -> None:
    """Same fixture, two callers, fresh run ids: identical result fields and digest."""
    cli_run_dir = cache_root / CLI_RUN_ID
    remote_run_dir = cache_root / REMOTE_RUN_ID

    observer = _ResultObserver()

    with (
        patch("infrahub_sync.cli.get_potenda_from_instance", _factory(cli_run_dir, FIXTURE_ROWS)),
        patch("infrahub_sync.cli.execute_run", observer),
    ):
        cli_invocation = CliRunner().invoke(app, ["diff", "--name", SYNC_NAME, "--directory", str(config_dir)])
    assert cli_invocation.exit_code == 0, cli_invocation.output
    (cli_result,) = observer.results

    remote_result = run_remote_request(
        SYNC_NAME,
        operation="plan",
        config_directory=str(config_dir),
        _potenda_factory=_factory(remote_run_dir, FIXTURE_ROWS),
    )

    assert cli_result.status == remote_result.status == "planned"
    assert cli_result.changed == remote_result.changed is True
    assert dict(cli_result.summary) == dict(remote_result.summary) == {"create": 5, "update": 1, "delete": 1}
    # Different run ids and run directories — the digest excludes both by construction.
    assert cli_result.run_id != remote_result.run_id
    assert compute_plan_fingerprint(cli_run_dir) == compute_plan_fingerprint(remote_run_dir)


def test_cli_diff_and_remote_plan_agree_on_an_unchanged_fixture(config_dir: Path, cache_root: Path) -> None:
    """The no-change case agrees too — including the all-zero summary."""
    cli_run_dir = cache_root / CLI_RUN_ID
    remote_run_dir = cache_root / REMOTE_RUN_ID
    observer = _ResultObserver()

    with (
        patch("infrahub_sync.cli.get_potenda_from_instance", _factory(cli_run_dir, [])),
        patch("infrahub_sync.cli.execute_run", observer),
    ):
        cli_invocation = CliRunner().invoke(app, ["diff", "--name", SYNC_NAME, "--directory", str(config_dir)])
    assert cli_invocation.exit_code == 0, cli_invocation.output
    (cli_result,) = observer.results

    remote_result = run_remote_request(
        SYNC_NAME,
        operation="plan",
        config_directory=str(config_dir),
        _potenda_factory=_factory(remote_run_dir, []),
    )

    assert cli_result.status == remote_result.status == "no-change"
    assert cli_result.changed == remote_result.changed is False
    assert dict(cli_result.summary) == dict(remote_result.summary) == {"create": 0, "update": 0, "delete": 0}
    assert compute_plan_fingerprint(cli_run_dir) == compute_plan_fingerprint(remote_run_dir)


def test_nested_only_diff_reports_no_change_even_though_the_sync_ran(
    config_dir: Path, cache_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Materialized rows are the result contract's fidelity boundary (contract step 7).

    `Potenda._diff_to_rows` walks only the diff root's direct children while
    `Diff.has_diffs()` is recursive, so a diff whose only changes sit in nested
    child elements gates the sync ON but materializes ZERO rows — and the result
    fields, which derive from the rows, therefore report no change.
    """
    from infrahub_sync.utils import get_instance

    sync_instance: SyncInstance | None = get_instance(name=SYNC_NAME, directory=str(config_dir))
    assert sync_instance is not None
    run_dir = cache_root / CLI_RUN_ID
    factory = _factory(run_dir, [], has_diffs=True)

    with caplog.at_level(logging.INFO, logger="infrahub_sync.execution"):
        result = execute_run(
            sync_instance,
            operation="sync",
            confirm_writes=True,
            potenda_factory=factory,
        )

    engine = factory()
    assert engine.synced is True, "has_diffs() gates execution exactly as it does today"
    assert result.status == "no-change"
    assert result.changed is False
    assert dict(result.summary) == {"create": 0, "update": 0, "delete": 0}
    assert "No difference found. Nothing to sync" not in [record.getMessage() for record in caplog.records]
