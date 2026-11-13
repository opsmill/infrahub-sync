"""Permission and lineage findings of destination validation (spec 020 FR-036, SC-011, T062).

`configs validate` with a branch reads, for the import account on that branch, whether the
account can view, create and update each mapped kind, and whether each `source` or `owner`
lineage setting names exactly one node the account can look up (spec 014 decision D-1 left
this check to spec 020). The lineage settings are `{kind, name}` references (spec 014); there
is no default owner, so a package that names neither `source` nor `owner` looks nothing up.

Every test goes through `collect_destination_schema_findings` with the bundled Infrahub
accessor and a stand-in SDK client, so no server is contacted. The stand-in answers the SDK
calls a permission and lineage read can make: the schema, the account's permission
identifiers (`get_user_permissions`, grouped by account group as the SDK returns them), the
branches, and a node lookup by kind and name (`get` raises `IndexError` for more than one
node, as the SDK does; `filters` returns the list). The validated branch is the package's
declared destination branch: the named-branch option of `configs validate --branch` is tested
by T061.

Permission identifiers follow Infrahub's grammar `object:<namespace>:<name>:<action>:<decision>`
and `global:<permission>:<decision>`, where the decision `allow_default` applies only to the
default branch.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from infrahub_sdk import exceptions as sdk_exceptions

from infrahub_sync.configuration.schema_validation import collect_destination_schema_findings
from tests.configuration.validation_packages import package, package_data

if TYPE_CHECKING:
    from infrahub_sync.configuration.models import ValidationFinding

_TOKEN = "permission-test-token-0042"  # noqa: S105 - deliberate non-secret canary.
_NEW_CODES = frozenset(
    {"destination-permission-missing", "destination-lineage-node-not-found", "destination-lineage-node-ambiguous"}
)
_DEVICE_WRITE = (
    "object:Infra:Device:view:allow_all",
    "object:Infra:Device:create:allow_all",
    "object:Infra:Device:update:allow_all",
)
_LINEAGE_VIEW = (
    "object:Core:AccountGroup:view:allow_all",
    "object:Core:Account:view:allow_all",
    "object:Core:Repository:view:allow_all",
)
_SUPER_ADMIN = ("global:super_admin:allow_all",)
# Infrahub renders a global permission as `global:<action>:<decision>` (`GlobalPermission.__str__`).
_EDIT_DEFAULT_BRANCH = "global:edit_default_branch:allow_all"


def _attribute(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        kind="Text",
        optional=False,
        default_value=None,
        unique=True,
        read_only=False,
        choices=None,
        computed_attribute=None,
    )


def _node(kind: str, namespace: str, name: str) -> SimpleNamespace:
    """A schema node as the SDK returns it, with one unique `name` attribute as its key."""
    return SimpleNamespace(
        kind=kind,
        namespace=namespace,
        name=name,
        human_friendly_id=["name__value"],
        uniqueness_constraints=[["name__value"]],
        attributes=[_attribute("name")],
        relationships=[],
    )


_SCHEMA = {
    "InfraDevice": _node("InfraDevice", "Infra", "Device"),
    "CoreAccount": _node("CoreAccount", "Core", "Account"),
    "CoreAccountGroup": _node("CoreAccountGroup", "Core", "AccountGroup"),
    "CoreRepository": _node("CoreRepository", "Core", "Repository"),
}


class _SchemaEndpoint:
    def __init__(self) -> None:
        self.branches: list[str | None] = []

    def all(self, branch: str | None = None, **_kwargs: Any) -> dict[str, SimpleNamespace]:  # noqa: ANN401
        self.branches.append(branch)
        return dict(_SCHEMA)

    def get(self, kind: str, branch: str | None = None, **_kwargs: Any) -> SimpleNamespace:  # noqa: ANN401, PLR6301
        del branch
        if kind not in _SCHEMA:
            raise sdk_exceptions.SchemaNotFoundError(identifier=kind)
        return _SCHEMA[kind]


class _BranchEndpoint:
    def all(self, **_kwargs: Any) -> dict[str, SimpleNamespace]:  # noqa: ANN401, PLR6301
        return {
            "main": SimpleNamespace(name="main", is_default=True),
            "review": SimpleNamespace(name="review", is_default=False),
        }


class _StandInClient:
    """The SDK client a validation read builds, answering from fixed permissions and nodes."""

    def __init__(self, permissions: tuple[str, ...], nodes: dict[str, list[str]]) -> None:
        self.schema = _SchemaEndpoint()
        self.branch = _BranchEndpoint()
        self._permissions = permissions
        self._nodes = nodes
        self.lookups: list[tuple[str, str | None]] = []

    def get_user_permissions(self) -> dict[str, list[str]]:
        return {"import-accounts": list(self._permissions)}

    def _matching(self, kind: str, filters: dict[str, Any]) -> list[SimpleNamespace]:
        name = filters.get("name__value")
        self.lookups.append((kind, name))
        return [
            SimpleNamespace(id=f"{kind}-{index}", typename=kind, display_label=node_name)
            for index, node_name in enumerate(self._nodes.get(kind, ()))
            if node_name == name
        ]

    def filters(self, kind: str, **filters: Any) -> list[SimpleNamespace]:  # noqa: ANN401
        return self._matching(kind, filters)

    def get(
        self,
        kind: str,
        raise_when_missing: bool = True,  # noqa: FBT001, FBT002
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        branch: str | None = None,
        **filters: Any,  # noqa: ANN401
    ) -> SimpleNamespace | None:
        del include, exclude, branch
        found = self._matching(kind, filters)
        if not found:
            if raise_when_missing:
                raise sdk_exceptions.NodeNotFoundError(identifier={"name__value": [str(filters.get("name__value"))]})
            return None
        if len(found) > 1:
            msg = "More than 1 node returned"
            raise IndexError(msg)
        return found[0]


def _validate(
    monkeypatch: pytest.MonkeyPatch,
    *,
    permissions: tuple[str, ...],
    branch: str = "review",
    nodes: dict[str, list[str]] | None = None,
    lineage: dict[str, dict[str, str]] | None = None,
) -> tuple[list[ValidationFinding], _StandInClient]:
    """Validate a one-kind package on `branch` against the stand-in client; return the new findings."""
    client = _StandInClient(permissions, nodes or {})
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN", _TOKEN)

    def _client(address: str, config: object) -> _StandInClient:
        del address, config
        return client

    monkeypatch.setattr("infrahub_sdk.InfrahubClientSync", _client)
    data = package_data()
    data["configuration"]["destination"]["settings"]["branch"] = branch
    data["configuration"]["destination"]["settings"].update(lineage or {})
    data["configuration"]["schema_mapping"] = [
        {
            "name": "InfraDevice",
            "mapping": "dcim.devices",
            "identifiers": ["name"],
            "fields": [{"name": "name", "mapping": "name"}],
        }
    ]
    findings = collect_destination_schema_findings(package(data)).findings
    # The stand-in schema read succeeds, so no finding here comes from a failed read.
    assert "destination-schema-read-failed" not in {finding.code for finding in findings}
    assert set(client.schema.branches) == {branch}
    for finding in findings:
        assert _TOKEN not in finding.message
    return [finding for finding in findings if finding.code in _NEW_CODES], client


@pytest.mark.parametrize("action", ["view", "create", "update"])
def test_a_missing_action_on_a_mapped_kind_names_the_kind_and_the_permission(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    """SC-011: the report names the mapped kind and the permission the account lacks."""
    granted = tuple(item for item in _DEVICE_WRITE if f":{action}:" not in item)

    found, _client = _validate(monkeypatch, permissions=granted)

    assert [(finding.code, finding.severity) for finding in found] == [("destination-permission-missing", "error")]
    assert found[0].location.startswith("/configuration/schema_mapping/0")
    assert "InfraDevice" in found[0].message
    assert f"object:Infra:Device:{action}" in found[0].message
    assert "review" in found[0].message


def test_an_account_with_every_needed_permission_gets_no_permission_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    found, _client = _validate(monkeypatch, permissions=_DEVICE_WRITE)

    assert found == []


def test_a_super_administrator_gets_no_permission_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    found, _client = _validate(monkeypatch, permissions=_SUPER_ADMIN)

    assert found == []


def test_a_grant_for_the_default_branch_only_is_missing_on_another_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The permissions are judged on the branch the team names, not on the default branch."""
    default_only = tuple(item.replace(":allow_all", ":allow_default") for item in _DEVICE_WRITE)

    found, _client = _validate(monkeypatch, permissions=default_only, branch="review")

    assert {finding.code for finding in found} == {"destination-permission-missing"}
    named = " ".join(finding.message for finding in found)
    for action in ("view", "create", "update"):
        assert f"object:Infra:Device:{action}" in named


def test_a_grant_for_the_default_branch_is_enough_on_the_default_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    default_only = tuple(item.replace(":allow_all", ":allow_default") for item in _DEVICE_WRITE)

    found, _client = _validate(monkeypatch, permissions=(*default_only, _EDIT_DEFAULT_BRANCH), branch="main")

    assert found == []


def test_a_write_on_the_default_branch_needs_edit_default_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Infrahub refuses every mutation on the default branch without `global:edit_default_branch`."""
    found, _client = _validate(monkeypatch, permissions=_DEVICE_WRITE, branch="main")

    assert [finding.code for finding in found] == ["destination-permission-missing"]
    assert "global:edit_default_branch" in found[0].message
    assert "object:Infra:Device" not in found[0].message


def test_a_denied_edit_default_branch_is_missing_on_the_default_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `deny` of the global permission takes precedence over its allow, as in Infrahub."""
    permissions = (*_DEVICE_WRITE, _EDIT_DEFAULT_BRANCH, "global:edit_default_branch:deny")

    found, _client = _validate(monkeypatch, permissions=permissions, branch="main")

    assert ["global:edit_default_branch" in finding.message for finding in found] == [True]


def test_edit_default_branch_is_not_needed_on_another_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    found, _client = _validate(monkeypatch, permissions=_DEVICE_WRITE, branch="review")

    assert found == []


def test_an_account_that_cannot_look_up_the_named_owner_kind_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lookup of a named `owner` needs `view` on the kind it names, and only that."""
    found, _client = _validate(
        monkeypatch,
        permissions=_DEVICE_WRITE,
        nodes={"CoreAccountGroup": ["network-team"]},
        lineage={"owner": {"kind": "CoreAccountGroup", "name": "network-team"}},
    )

    assert [(finding.code, finding.location) for finding in found] == [
        ("destination-permission-missing", "/configuration/destination/settings/owner")
    ]
    assert "object:Core:AccountGroup:view" in found[0].message


@pytest.mark.parametrize(
    ("setting", "kind"),
    [("owner", "CoreAccountGroup"), ("source", "CoreAccount"), ("source", "CoreRepository")],
)
def test_a_named_lineage_node_that_does_not_exist_is_reported(
    monkeypatch: pytest.MonkeyPatch, setting: str, kind: str
) -> None:
    found, _client = _validate(
        monkeypatch,
        permissions=_DEVICE_WRITE + _LINEAGE_VIEW,
        nodes={kind: ["someone-else"]},
        lineage={setting: {"kind": kind, "name": "no-such-node"}},
    )

    assert [(finding.code, finding.severity, finding.location) for finding in found] == [
        ("destination-lineage-node-not-found", "error", f"/configuration/destination/settings/{setting}")
    ]
    assert setting in found[0].message
    assert kind in found[0].message
    assert "no-such-node" in found[0].message


def test_a_named_lineage_node_that_matches_more_than_one_node_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    found, _client = _validate(
        monkeypatch,
        permissions=_DEVICE_WRITE + _LINEAGE_VIEW,
        nodes={"CoreAccountGroup": ["network-team", "network-team"]},
        lineage={"owner": {"kind": "CoreAccountGroup", "name": "network-team"}},
    )

    assert [(finding.code, finding.severity, finding.location) for finding in found] == [
        ("destination-lineage-node-ambiguous", "error", "/configuration/destination/settings/owner")
    ]
    assert "CoreAccountGroup" in found[0].message
    assert "network-team" in found[0].message


def test_a_named_lineage_node_that_exists_once_gives_no_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    found, _client = _validate(
        monkeypatch,
        permissions=_DEVICE_WRITE + _LINEAGE_VIEW,
        nodes={"CoreAccountGroup": ["network-team"], "CoreAccount": ["netbox"]},
        lineage={
            "owner": {"kind": "CoreAccountGroup", "name": "network-team"},
            "source": {"kind": "CoreAccount", "name": "netbox"},
        },
    )

    assert found == []


def test_no_named_source_or_owner_looks_nothing_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """No default owner (spec 014 D-3): without `source` or `owner` there is no lookup and no finding."""
    found, client = _validate(monkeypatch, permissions=_DEVICE_WRITE)

    assert found == []
    assert client.lookups == []


def test_a_named_lineage_kind_the_branch_schema_does_not_hold_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """`CoreReadOnlyRepository` is an allowed lineage kind, but this branch's schema has no such kind."""
    found, client = _validate(
        monkeypatch,
        permissions=_DEVICE_WRITE + _LINEAGE_VIEW,
        lineage={"source": {"kind": "CoreReadOnlyRepository", "name": "mirror"}},
    )

    assert [(finding.code, finding.location) for finding in found] == [
        ("destination-lineage-node-not-found", "/configuration/destination/settings/source")
    ]
    assert "CoreReadOnlyRepository" in found[0].message
    assert "mirror" in found[0].message
    assert client.lookups == []
