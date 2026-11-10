"""FR-002: a run on a destination branch that does not exist names the branch and the fix."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.configuration import ConfigurationPackage, parse_configuration_package
from infrahub_sync.configuration.capabilities import DestinationSchemaReadError
from infrahub_sync.configuration.runtime import resolve_runtime_instance
from infrahub_sync.runtime_schema import DestinationSchemaUnavailableError, build_runtime_model_plan
from infrahub_sync.runtime_schema import worker as worker_module
from infrahub_sync.runtime_schema.errors import DestinationBranchNotFoundError
from tests.configuration.validation_packages import package_data

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_MAPPING = [
    {
        "name": "BuiltinTag",
        "mapping": "extras.tags",
        "fields": [{"name": "name", "mapping": "name"}],
    },
]


@pytest.fixture(name="credentials", autouse=True)
def _credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN", "netbox-branch-canary")
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN", "infrahub-branch-canary")


def _package() -> ConfigurationPackage:
    content = package_data()
    content["configuration"]["schema_mapping"] = copy.deepcopy(_MAPPING)
    return parse_configuration_package(content)


def _refuse_with(reason: str, detail: str | None = None) -> Callable[[ConfigurationPackage, str], dict[str, Any]]:
    def _refuse(package: ConfigurationPackage, branch: str) -> dict[str, Any]:
        del package, branch
        msg = "third-party text with infrahub-branch-canary inside"
        raise DestinationSchemaReadError(msg, reason=reason, detail=detail)

    return _refuse


def _plan_on(branch: str, tmp_path: Path) -> None:
    package = _package()
    build_runtime_model_plan(
        package=package,
        instance=resolve_runtime_instance(package, directory=str(tmp_path)),
        run_branch=branch,
        scope="both",
    )


def test_a_missing_branch_raises_the_typed_subclass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(worker_module, "read_destination_schema_snapshot", _refuse_with("nobranch"))

    with pytest.raises(DestinationBranchNotFoundError) as caught:
        _plan_on("demo-test", tmp_path)

    error = caught.value
    assert isinstance(error, DestinationSchemaUnavailableError)
    assert error.reason == "nobranch"
    assert error.branch == "demo-test"
    assert error.message == "Branch `demo-test` does not exist in Infrahub."
    assert error.next_action == "Create it, or start the run with `--branch <existing branch>`."
    assert "canary" not in str(error)


@pytest.mark.parametrize(
    "reason", ["timeout", "unreachable", "unauthorized", "forbidden", "credentials", "unconfigured", "rejected"]
)
def test_every_other_reason_keeps_the_base_class_with_a_fixed_cause_and_next_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reason: str
) -> None:
    monkeypatch.setattr(worker_module, "read_destination_schema_snapshot", _refuse_with(reason))

    with pytest.raises(DestinationSchemaUnavailableError) as caught:
        _plan_on("demo-test", tmp_path)

    error = caught.value
    assert not isinstance(error, DestinationBranchNotFoundError)
    assert error.reason == reason
    assert "`demo-test`" in error.message
    assert error.next_action
    assert "canary" not in error.message
    assert "canary" not in error.next_action
    assert "prefect" not in f"{error.message} {error.next_action}".lower()


def test_a_refused_permission_keeps_the_permission_infrahub_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    detail = "missing permission `global:manage_schema:allow_all`"
    monkeypatch.setattr(worker_module, "read_destination_schema_snapshot", _refuse_with("forbidden", detail))

    with pytest.raises(DestinationSchemaUnavailableError) as caught:
        _plan_on("demo-test", tmp_path)

    assert "`global:manage_schema:allow_all`" in caught.value.message
