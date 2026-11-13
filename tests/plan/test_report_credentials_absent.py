"""No credential and no error text reaches the plan files or the report (spec 020 T048, SC-005, SC-007).

FR-029 and FR-030: a problem reason, a left-out or kept entry and a plan file are never built from
connection settings or from the text of an error, so their protection does not depend on
replacing values in text.

The shared problem fixture is planned end to end with two referenced credentials: one of five
characters, and one that holds `"`, `\\` and `,`. Both are declared as `$credential` references,
resolved from the worker's environment into the source and destination settings of the run's
configuration, as a run resolves them. Every peer error and every transform error the source
raises while it reads carries both values and an address with a user name and password in its
text. The checks read the raw bytes of every file under `plan/`, the decoded rows of
`plan.parquet` and every report entry.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from typing import TYPE_CHECKING, Any

import pyarrow.parquet as pq

from infrahub_sync import DiffSyncModelMixin, PeerReferenceError, TransformError
from infrahub_sync.configuration.credentials import EnvironmentCredentialProvider
from infrahub_sync.plan.writer import PLAN_DIR_NAME
from tests.plan.problem_plan import FixtureSource, plan_fixture

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from tests.plan.conftest import PlanProblemFixture

# The same two credentials as the failure record tests (tests/service/failure_cases.py).
SHORT_CREDENTIAL = "k3yQz"
SPECIAL_CREDENTIAL = 'Wq7Zm"ss\\wo,rd'
# The start of the special credential, which no escaped form changes.
SPECIAL_CREDENTIAL_RUN = "Wq7Zm"
SHORT_CREDENTIAL_NAME = "INFRAHUB_SYNC_CREDENTIAL_SHORT_TOKEN"
SPECIAL_CREDENTIAL_NAME = "INFRAHUB_SYNC_CREDENTIAL_SPECIAL_PASSWORD"

# Text that only the errors raised during the read hold.
ERROR_MARKER = "adapter-error-text"
ERROR_TEXT = (
    f"{ERROR_MARKER}: request to https://admin:{SHORT_CREDENTIAL}@netbox.example.test/api/"
    f"?token={SPECIAL_CREDENTIAL} failed"
)


def _forms(value: str) -> list[bytes]:
    """The value as raw UTF-8 and as JSON writes it inside a string."""
    return list(
        dict.fromkeys(
            [
                value.encode("utf-8"),
                json.dumps(value)[1:-1].encode("utf-8"),
                json.dumps(value, ensure_ascii=False)[1:-1].encode("utf-8"),
            ]
        )
    )


FORBIDDEN = [
    form
    for value in (SHORT_CREDENTIAL, SPECIAL_CREDENTIAL, SPECIAL_CREDENTIAL_RUN, ERROR_MARKER, "netbox.example.test")
    for form in _forms(value)
]


def _with_credentials(fixture: PlanProblemFixture, monkeypatch: pytest.MonkeyPatch) -> PlanProblemFixture:
    """The fixture whose configuration holds both credentials, resolved as a run resolves them."""
    monkeypatch.setenv(SHORT_CREDENTIAL_NAME, SHORT_CREDENTIAL)
    monkeypatch.setenv(SPECIAL_CREDENTIAL_NAME, SPECIAL_CREDENTIAL)
    provider = EnvironmentCredentialProvider()
    config_data = copy.deepcopy(fixture.config_data)
    config_data["source"]["settings"].update(
        token=provider.resolve(SHORT_CREDENTIAL_NAME), password=provider.resolve(SPECIAL_CREDENTIAL_NAME)
    )
    config_data["destination"]["settings"]["token"] = provider.resolve(SHORT_CREDENTIAL_NAME)
    return dataclasses.replace(fixture, config_data=config_data)


def _leak_into_errors(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Put the credentials in the text of every peer and transform error the source raises."""
    raised = {"peer": 0, "transform": 0}
    peer = FixtureSource._peer
    apply_transform = DiffSyncModelMixin.apply_transform.__func__

    def leaky_peer(self: FixtureSource, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        try:
            return peer(self, *args, **kwargs)
        except PeerReferenceError as exc:
            raised["peer"] += 1
            exc.args = (f"{exc} {ERROR_TEXT}",)
            raise

    def leaky_transform(cls: type[DiffSyncModelMixin], *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        try:
            apply_transform(cls, *args, **kwargs)
        except TransformError as exc:
            raised["transform"] += 1
            exc.args = (f"{exc} {ERROR_TEXT}",)
            raise

    monkeypatch.setattr(FixtureSource, "_peer", leaky_peer)
    monkeypatch.setattr(DiffSyncModelMixin, "apply_transform", classmethod(leaky_transform))
    return raised


def test_no_credential_and_no_error_text_reaches_the_plan_files_or_the_report(
    plan_problems: PlanProblemFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    fixture = _with_credentials(plan_problems, monkeypatch)
    raised = _leak_into_errors(monkeypatch)

    planned = plan_fixture(fixture)

    # The read raised errors whose text holds both credentials, and the plan reports problems.
    assert raised["peer"] >= 1
    assert raised["transform"] >= 1
    assert planned.problems
    assert planned.left_out
    plan_dir = planned.run_dir / PLAN_DIR_NAME
    files = {path.relative_to(plan_dir).as_posix(): path.read_bytes() for path in plan_dir.rglob("*") if path.is_file()}
    assert "report.jsonl" in files
    parquet = list(plan_dir.rglob("*.parquet"))
    rows = json.dumps([pq.read_table(path).to_pylist() for path in parquet], default=str, ensure_ascii=False)
    surfaces = {
        **files,
        "plan.parquet rows": rows.encode("utf-8"),
        "report entries": json.dumps(planned.report).encode(),
    }
    for surface, data in surfaces.items():
        for forbidden in FORBIDDEN:
            assert forbidden not in data, (forbidden, surface)
    for problem in planned.problems:
        for value in (SHORT_CREDENTIAL, SPECIAL_CREDENTIAL, ERROR_MARKER, "Traceback"):
            assert value not in problem["reason"]
