"""`Potenda.check_plan` is the pre-write gate `apply_plan` runs, and nothing after it (SC-004).

`verify` calls the gate to learn whether a saved plan would apply now. These cases hold the
three things that makes true: a valid plan returns the parsed plan and the checks that ran;
every gate failure is the same error, naming the same check, as an apply of the same artifact;
and the gate proves no ownership and dispatches nothing.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.adapters.infrahub import InfrahubAdapter
from infrahub_sync.plan.errors import (
    ImportAccountMismatchError,
    PlanArtifactTornError,
    PlanFormatApplyUnsupportedError,
    PlanVerificationError,
    ReviewedPayloadFieldMissingError,
    failed_checks,
)
from infrahub_sync.plan.reader import read_plan_artifact_bytes
from infrahub_sync.potenda import PlanCheck, Potenda
from tests.plan.artifact_fixtures import CONFIG_VERSION, operation_record, write_artifact
from tests.plan.ownership_fixtures import granted_ownership

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from infrahub_sync.plan.models import PlannedOperation

RUN_ID = "20260727T0915-4c1ab390"
FILE_AND_SURFACE_CHECKS = (
    "run_binding",
    "plan_checksum",
    "source_snapshot",
    "config_version",
    "format_version",
    "write_surface",
)


class RecordingDestination:
    """A planned-write destination that records every dispatch."""

    def __init__(self) -> None:
        self.dispatched: list[str] = []

    def new_peer_resolver(self) -> object:  # noqa: PLR6301
        """The per-apply resolver factory; nothing below this double's surface reads it."""
        return object()

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        del peers
        self.dispatched.append(operation.operation_id)
        return f"node-{len(self.dispatched)}"


class SurfacelessDestination:
    """A destination with no planned-write surface."""

    dispatched: tuple[str, ...] = ()


class LineageDestination(RecordingDestination):
    """A destination that resolves lineage, refusing when told to."""

    def __init__(self, *, refuse: bool = False) -> None:
        super().__init__()
        self.refuse = refuse

    def resolve_lineage(self, *, saved_plan: object = None) -> object:
        del saved_plan
        if self.refuse:
            msg = "The import account 'other' is not the account 'sync' the plan recorded."
            raise ImportAccountMismatchError(msg)
        return None


def _infrahub_destination(*, missing_field: bool) -> InfrahubAdapter:
    """An Infrahub destination double: the real class, so the planned-field check runs on it."""
    destination = InfrahubAdapter.__new__(InfrahubAdapter)
    dispatched: list[str] = []

    def validate(operations: object) -> None:
        del operations
        if missing_field:
            msg = "The live schema of kind 'BuiltinTag' has no field 'description'."
            raise ReviewedPayloadFieldMissingError(msg)

    def dispatch(*, operation: PlannedOperation, peers: object) -> str:
        del peers
        dispatched.append(operation.operation_id)
        return "node"

    vars(destination).update(
        validate_planned_payload_fields=validate,
        apply_planned_operation=dispatch,
        new_peer_resolver=object,
        resolve_lineage=lambda **_kwargs: None,
        dispatched=dispatched,
    )
    return destination


class RefusingOwnership:
    """An ownership boundary that fails the case if anything asks it for a proof."""

    def before_operation(self) -> None:  # noqa: PLR6301
        pytest.fail("check_plan proved write ownership")

    def after_final_operation(self) -> None:  # noqa: PLR6301
        pytest.fail("check_plan proved write ownership")


def _potenda(run_directory: Path, destination: object) -> Potenda:
    return Potenda(
        source=SimpleNamespace(top_level=[]),  # ty: ignore[invalid-argument-type]
        destination=destination,  # ty: ignore[invalid-argument-type]
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=["BuiltinTag"],
        run_dir=run_directory,
        run_id=RUN_ID,
    )


def _run_dir(tmp_path: Path) -> Path:
    directory = tmp_path / RUN_ID
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _check(directory: Path, destination: object, **kwargs: Any) -> PlanCheck:  # noqa: ANN401
    return _potenda(directory, destination).check_plan(
        run_id=RUN_ID,
        config_version=kwargs.pop("config_version", CONFIG_VERSION),
        artifact=read_plan_artifact_bytes(directory),
        **kwargs,
    )


def test_a_valid_plan_returns_the_parsed_plan_and_the_checks_it_ran(tmp_path: Path) -> None:
    directory = _run_dir(tmp_path)
    records = [operation_record(identity={"name": "alpha"}), operation_record(identity={"name": "bravo"})]
    write_artifact(directory, records, run_id=RUN_ID, source_snapshot=[])
    destination = RecordingDestination()

    result = _check(directory, destination)

    assert [operation.operation_id for operation in result.loaded.operations] == [
        record["operation_id"] for record in records
    ]
    assert result.checks == FILE_AND_SURFACE_CHECKS
    assert destination.dispatched == []


def test_the_lineage_and_planned_field_checks_are_reported_only_where_they_run(tmp_path: Path) -> None:
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record()], run_id=RUN_ID, source_snapshot=[])

    assert _check(directory, LineageDestination()).checks == (*FILE_AND_SURFACE_CHECKS, "lineage")
    assert _check(directory, _infrahub_destination(missing_field=False)).checks == (
        *FILE_AND_SURFACE_CHECKS,
        "planned_fields",
        "lineage",
    )


def _tear(directory: Path) -> None:
    (directory / "plan" / "operations.jsonl").unlink()


def _unapplyable_format(directory: Path) -> None:
    write_artifact(directory, [operation_record(action="create")], run_id=RUN_ID, source_snapshot=[], format_version=2)


# One case per way the gate can refuse: (arrange the artifact, the destination, keyword
# arguments to the call, the error type, the checks the refusal names).
_GATE_FAILURES: dict[
    str, tuple[Callable[[Path], None] | None, Callable[[], object], dict[str, Any], type[BaseException], tuple[str, ...]]
] = {
    "config_version": (None, RecordingDestination, {"config_version": "another-configuration"}, PlanVerificationError, ("config_version",)),
    "torn_operations": (_tear, RecordingDestination, {}, PlanVerificationError, ("torn_operations",)),
    "write_surface": (None, SurfacelessDestination, {}, PlanVerificationError, ("write_surface",)),
    "approval": (None, RecordingDestination, {"expected_checksum": "0" * 64}, PlanVerificationError, ()),
    "format_version": (_unapplyable_format, RecordingDestination, {}, PlanFormatApplyUnsupportedError, ("format_version",)),
    "planned_fields": (None, lambda: _infrahub_destination(missing_field=True), {}, ReviewedPayloadFieldMissingError, ("planned_fields",)),
    "lineage": (None, lambda: LineageDestination(refuse=True), {}, ImportAccountMismatchError, ("lineage",)),
}  # fmt: skip


@pytest.mark.parametrize("case", list(_GATE_FAILURES))
def test_a_gate_failure_is_the_same_error_and_check_as_the_apply_refusal(tmp_path: Path, case: str) -> None:
    arrange, destination_factory, kwargs, error_type, checks = _GATE_FAILURES[case]
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record()], run_id=RUN_ID, source_snapshot=[])
    if arrange is not None:
        arrange(directory)

    checked_destination = destination_factory()
    with pytest.raises(error_type) as checked:
        _check(directory, checked_destination, **kwargs)

    applied_destination = destination_factory()
    with pytest.raises(error_type) as applied:
        _potenda(directory, applied_destination).apply_plan(
            ownership=granted_ownership(),
            config_version=kwargs.get("config_version", CONFIG_VERSION),
            expected_checksum=kwargs.get("expected_checksum"),
        )

    assert type(checked.value) is type(applied.value)
    assert failed_checks(checked.value) == failed_checks(applied.value) == checks
    assert str(checked.value) == str(applied.value)
    assert list(getattr(checked_destination, "dispatched", ())) == []
    assert list(getattr(applied_destination, "dispatched", ())) == []


def test_a_torn_record_is_refused_by_the_gate_as_by_the_apply(tmp_path: Path) -> None:
    """A record the line count cannot see as torn is still refused by the parse, inside the gate."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [{**operation_record(), "tier": "not-a-tier"}], run_id=RUN_ID, source_snapshot=[])

    with pytest.raises(PlanArtifactTornError):
        _check(directory, RecordingDestination())
    with pytest.raises(PlanArtifactTornError):
        _potenda(directory, RecordingDestination()).apply_plan(
            ownership=granted_ownership(), config_version=CONFIG_VERSION
        )


class WriteForbiddingDestination(LineageDestination):
    """A lineage-resolving destination whose every write-path member fails the case."""

    def new_peer_resolver(self) -> object:  # noqa: PLR6301
        pytest.fail("check_plan built the apply's peer resolver")

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401, PLR6301
        del operation, peers
        pytest.fail("check_plan dispatched a planned operation")


def test_the_gate_proves_no_ownership_and_dispatches_nothing(tmp_path: Path) -> None:
    directory = _run_dir(tmp_path)
    write_artifact(
        directory,
        [operation_record(identity={"name": "alpha"}), operation_record(action="delete", identity={"name": "bravo"})],
        run_id=RUN_ID,
        source_snapshot=[],
    )
    ownership = RefusingOwnership()

    result = _check(directory, WriteForbiddingDestination())

    assert len(result.loaded.operations) == 2
    assert result.checks == (*FILE_AND_SURFACE_CHECKS, "lineage")
    # The control: an apply of the same artifact through the same destination does reach the
    # write path, right after the gate, so the case above is not passing by never getting there.
    with pytest.raises(pytest.fail.Exception, match="peer resolver"):
        _potenda(directory, WriteForbiddingDestination()).apply_plan(ownership=ownership, config_version=CONFIG_VERSION)
