"""Neutral HTTP contract and server projection tests."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from importlib.util import resolve_name
from pathlib import Path

import pytest
from pydantic import ValidationError

from infrahub_sync.client.models import (
    KeptRecordResource,
    LeftOutRecordResource,
    OrchestrationSummary,
    PlanKeptPageResource,
    PlanLeftOutPageResource,
    PlanProblemResource,
    PlanProblemsPageResource,
    PlanRecordReferenceResource,
    PlanSummaryResource,
    PublicRunResource,
)
from infrahub_sync.product_store.models import PrefectExecutionLink, ProductRun
from infrahub_sync.service.models import public_run_resource


def _package_imports(root: Path, package: str) -> set[str]:
    imports: set[str] = set()
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).with_suffix("")
        module_parts = [*package.split("."), *relative.parts]
        if module_parts[-1] == "__init__":
            module_parts.pop()
        current_package = ".".join(module_parts if path.stem == "__init__" else module_parts[:-1])
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported = node.module or ""
                if node.level:
                    imported = resolve_name(f"{'.' * node.level}{imported}", current_package)
                imports.add(imported)
    return imports


def test_client_package_imports_no_product_or_service_module() -> None:
    imports = _package_imports(Path("infrahub_sync/client"), "infrahub_sync.client")

    assert not {name for name in imports if name.startswith("infrahub_sync.product_store")}
    assert not {name for name in imports if name.startswith("infrahub_sync.service")}
    assert not {name for name in imports if name.startswith("infrahub_sync.adapters")}
    assert not {name for name in imports if name.startswith("infrahub_sync.execution")}
    assert not {
        name for name in imports if name.partition(".")[0] in {"boto3", "fastapi", "prefect", "psycopg", "uvicorn"}
    }


def test_client_import_scan_resolves_nested_relative_imports(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "probe.py").write_text(
        "from ...product_store.models import ProductRun\n",
        encoding="utf-8",
    )

    assert "infrahub_sync.product_store.models" in _package_imports(tmp_path, "infrahub_sync.client")


@pytest.mark.parametrize(
    "field",
    [
        "submitted_at",
        "claimed_at",
        "stalled_at",
        "cancellation_requested_at",
        "cancellation_recovery_deadline_at",
        "cancellation_acknowledged_at",
        "terminal_at",
    ],
)
def test_orchestration_timestamps_require_a_timezone(field: str) -> None:
    payload: dict[str, object] = {
        "flow_run_id": "flow-1",
        "purpose": "plan",
        "attempt": 1,
        "state": "pending",
        "detail_available": True,
        "unavailable_reason": None,
        "submitted_at": None,
        "claimed_at": None,
        "stalled_at": None,
        "cancellation_requested_at": None,
        "cancellation_recovery_deadline_at": None,
        "cancellation_acknowledged_at": None,
        "terminal_at": None,
        "terminal_state": None,
        "terminal_outcome": None,
    }
    payload[field] = "2026-08-30T12:00:00"
    if field == "terminal_at":
        payload["terminal_state"] = "completed"
        payload["terminal_outcome"] = "succeeded"

    with pytest.raises(ValidationError, match="execution timestamps must include a timezone"):
        OrchestrationSummary.model_validate(payload)


def test_server_projects_store_run_into_a_self_contained_resource() -> None:
    now = datetime.now(timezone.utc)
    stored = ProductRun(
        run_id="run-1",
        operation="plan",
        configuration_reference="cfg@1",
        config_id="cfg",
        registry_version=1,
        package_checksum="a" * 64,
        started_at=now,
        phase="accepted",
        prefect_executions=(
            PrefectExecutionLink(
                flow_run_id="flow-1",
                purpose="plan",
                attempt=1,
                submitted_at=now,
                claiming_worker_id=None,
            ),
        ),
    )

    projected = public_run_resource(stored)

    assert isinstance(projected, PublicRunResource)
    assert not issubclass(PublicRunResource, ProductRun)
    assert projected.run_id == stored.run_id
    assert projected.prefect_executions[0].model_dump() == {
        "flow_run_id": "flow-1",
        "deployment_id": None,
        "purpose": "plan",
        "attempt": 1,
        "last_observed_state": None,
        "last_observed_at": None,
    }


# --- Spec 009: plan report entries and plan summary counts (contracts/sync-api.md) ---

_CONTRACT_PROBLEMS_PAGE: dict[str, object] = {
    "run_id": "20261015T1200-1a2b3c4d",
    "plan_checksum": "d" * 64,
    "entries": [
        {
            "problem_type": "dropdown_value_not_in_choices",
            "record": {"kind": "DcimDevice", "identity": {"location": "dc1", "name": "rtr1"}, "source_id": "42"},
            "field": "status",
            "value": "planned",
            "allowed": ["active", "deprecated", "maintenance", "provisioning", "reserved"],
            "peer": None,
            "other_records": [],
            "reason": "DcimDevice rtr1: status 'planned' is not one of the choices of the Dropdown attribute status",
        }
    ],
    "offset": 0,
    "limit": 500,
    "total": 1,
    "next_offset": None,
}


def test_the_problems_page_parses_the_contract_example() -> None:
    page = PlanProblemsPageResource.model_validate(_CONTRACT_PROBLEMS_PAGE)

    problem = page.entries[0]
    assert isinstance(problem, PlanProblemResource)
    assert problem.problem_type == "dropdown_value_not_in_choices"
    assert problem.record == PlanRecordReferenceResource(
        kind="DcimDevice", identity={"location": "dc1", "name": "rtr1"}, source_id="42"
    )
    assert problem.value == "planned"
    assert problem.allowed == ("active", "deprecated", "maintenance", "provisioning", "reserved")
    assert (page.offset, page.limit, page.total, page.next_offset) == (0, 500, 1, None)


def test_the_left_out_and_kept_pages_parse_their_entries() -> None:
    device = {"kind": "DcimDevice", "identity": {"location": "dc1", "name": "rtr1"}, "source_id": "42"}
    common = {"run_id": "run-1", "plan_checksum": "d" * 64, "offset": 0, "limit": 500, "total": 2, "next_offset": None}
    left_out = PlanLeftOutPageResource.model_validate(
        {
            **common,
            "entries": [
                {
                    "record": device,
                    "cause": "has_problem",
                    "problem_types": ["dropdown_value_not_in_choices"],
                    "depends_on": None,
                    "relationship": None,
                    "loaded": True,
                },
                {
                    "record": {
                        "kind": "InterfacePhysical",
                        "identity": {"device": "rtr1", "name": "eth0"},
                        "source_id": "40",
                    },
                    "cause": "depends_on_left_out",
                    "problem_types": [],
                    "depends_on": device,
                    "relationship": "device",
                    "loaded": True,
                },
            ],
        }
    )
    kept = PlanKeptPageResource.model_validate(
        {
            **common,
            "total": 1,
            "entries": [
                {
                    "record": {"kind": "IpamIPAddress", "identity": {"address": "10.0.0.9/32"}, "source_id": "9"},
                    "cause": "peer_not_found_in_source",
                    "relationship": "vrf",
                    "left_out_peer": {"kind": "IpamVRF", "identity": None, "source_id": "17"},
                }
            ],
        }
    )

    assert isinstance(left_out.entries[1], LeftOutRecordResource)
    assert left_out.entries[1].depends_on is not None
    assert left_out.entries[1].depends_on.kind == "DcimDevice"
    assert isinstance(kept.entries[0], KeptRecordResource)
    assert kept.entries[0].left_out_peer.identity is None
    assert kept.entries[0].left_out_peer.source_id == "17"


@pytest.mark.parametrize("limit", [0, 501])
def test_a_report_page_limit_outside_one_to_five_hundred_is_refused(limit: int) -> None:
    with pytest.raises(ValidationError):
        PlanProblemsPageResource.model_validate({**_CONTRACT_PROBLEMS_PAGE, "limit": limit})


def _summary(**counts: object) -> dict[str, object]:
    return {
        "by_action": {"create": 1},
        "by_kind": {"DcimDevice": 1},
        "total": 1,
        "delete_operations_computed": True,
        "deletes_not_executed": 0,
        **counts,
    }


def test_the_plan_summary_parses_the_report_counts() -> None:
    summary = PlanSummaryResource.model_validate(
        _summary(
            left_out=6,
            left_out_with_problem=1,
            left_out_dependent=5,
            left_out_by_kind={"DcimDevice": 1, "InterfacePhysical": 5},
            problems=1,
            problems_by_type={"dropdown_value_not_in_choices": 1},
            kept=0,
        )
    )

    assert (summary.left_out, summary.left_out_with_problem, summary.left_out_dependent) == (6, 1, 5)
    assert summary.left_out_by_kind == {"DcimDevice": 1, "InterfacePhysical": 5}
    assert (summary.problems, summary.kept) == (1, 0)
    assert summary.problems_by_type == {"dropdown_value_not_in_choices": 1}


def test_a_plan_summary_from_an_earlier_release_has_zero_report_counts() -> None:
    summary = PlanSummaryResource.model_validate(_summary())

    assert (summary.left_out, summary.left_out_with_problem, summary.left_out_dependent) == (0, 0, 0)
    assert (summary.left_out_by_kind, summary.problems, summary.problems_by_type, summary.kept) == ({}, 0, {}, 0)


@pytest.mark.parametrize(
    "counts",
    [
        {"left_out": 6, "left_out_with_problem": 1, "left_out_dependent": 4, "left_out_by_kind": {"DcimDevice": 6}},
        {"left_out": 6, "left_out_with_problem": 1, "left_out_dependent": 5, "left_out_by_kind": {"DcimDevice": 5}},
        {"problems": 2, "problems_by_type": {"wrong_value_type": 1}},
    ],
)
def test_plan_summary_counts_that_do_not_add_up_are_refused(counts: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PlanSummaryResource.model_validate(_summary(**counts))
