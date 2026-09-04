"""AR6/AR8: registered apply compares the live destination schema before any write.

The retained artifact is verified first, its recorded checksum must be the operator's
approved one, and only then is the live schema read and its consumed-semantics
fingerprint compared against the one the plan recorded. Every refusal below happens
before `execute_run` — the one door to a source read or a write-capable destination — so
the sentinel's empty call list is the "nothing was written" evidence.
"""

from __future__ import annotations

import copy
import inspect
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from infrahub_sdk.schema.main import AttributeKind, NodeSchemaAPI

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from infrahub_sync.client.models import PlanResource
from infrahub_sync.configuration import ConfigurationPackage, parse_configuration_package
from infrahub_sync.configuration import capabilities as capabilities_module
from infrahub_sync.configuration.capabilities import DestinationSchemaReadError
from infrahub_sync.configuration.runtime import resolve_runtime_instance
from infrahub_sync.execution import RunResult
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.checksum import compute_plan_checksum
from infrahub_sync.plan.config_version import resolve_config_version
from infrahub_sync.plan.errors import PlanSchemaChangedError
from infrahub_sync.plan.ownership import WriteDispatchTracker
from infrahub_sync.plan.review import read_saved_plan
from infrahub_sync.plan.writer import MANIFEST_FILE_NAME, OPERATIONS_FILE_NAME, PLAN_DIR_NAME, write_plan_artifact
from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, local_product_projection
from infrahub_sync.runtime_schema import compute_consumed_schema_fingerprint, normalize_destination_schema
from infrahub_sync.runtime_schema import worker as worker_module
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.flow import service_sync_run
from infrahub_sync.service.scratch import stage_scratch
from infrahub_sync.service.service import PLAN_ARTIFACT_ID
from tests.configuration.validation_packages import package_data
from tests.plan.artifact_fixtures import duplicated_key_manifest_bytes
from tests.service.execution_fixtures import append_execution, bind_granting_guard

if TYPE_CHECKING:
    from infrahub_sync import SyncInstance

FLOW_RUN_ID = "3f1b0d8c-1c6a-4a1e-9a52-2f8f0c0b7a11"
WORKER_ID = "6b3a8f21-0d44-4c2f-9d5a-4d1c8e2b7f30"
RUN_ID = "registered-schema-guard"

# Short, distinctive credential values, so a scan for them in a refusal is unambiguous.
NETBOX_CANARY = "netbox-schema-guard-canary"
INFRAHUB_CANARY = "infrahub-schema-guard-canary"

_SNAPSHOT: dict[str, Any] = {
    "BuiltinTag": {
        "human_friendly_id": ["name__value"],
        "uniqueness_constraints": [["name__value"]],
        "attributes": {
            "name": {"kind": "Text", "optional": False, "default_value": None, "unique": True},
            "description": {"kind": "Text", "optional": True, "default_value": None, "unique": False},
        },
        "relationships": {},
    },
    "LocationSite": {
        "human_friendly_id": ["name__value"],
        "uniqueness_constraints": [["name__value"]],
        "attributes": {"name": {"kind": "Text", "optional": False, "default_value": None, "unique": True}},
        "relationships": {"tags": {"peer": "BuiltinTag", "cardinality": "many", "optional": True, "kind": "Generic"}},
    },
}

_MAPPING = [
    {
        "name": "BuiltinTag",
        "mapping": "extras.tags",
        "fields": [{"name": "name", "mapping": "name"}, {"name": "description", "mapping": "description"}],
    },
    {
        "name": "LocationSite",
        "mapping": "dcim.sites",
        "fields": [{"name": "name", "mapping": "name"}, {"name": "tags", "mapping": "tags", "reference": "BuiltinTag"}],
    },
]


def _package() -> ConfigurationPackage:
    content = package_data()
    content["configuration"]["schema_mapping"] = copy.deepcopy(_MAPPING)
    return parse_configuration_package(content)


def _mutated(kind: str, **changes: object) -> dict[str, Any]:
    """Return the snapshot with one dotted path per keyword replaced on `kind`."""
    snapshot = copy.deepcopy(_SNAPSHOT)
    for path, value in changes.items():
        target: Any = snapshot[kind]
        *parents, leaf = path.split(".")
        for step in parents:
            target = target[step]
        target[leaf] = value
    return snapshot


class _SnapshotSpy:
    """The worker's one schema read, answering with whatever the case installed."""

    def __init__(self) -> None:
        self.snapshot: dict[str, Any] = copy.deepcopy(_SNAPSHOT)
        self.failure: DestinationSchemaReadError | None = None
        # A live response to normalize through the real accessor boundary, in place of the
        # already-normalized snapshot. Only the network call is stubbed.
        self.raw_schema: dict[str, Any] | None = None
        self.branches: list[str] = []

    def __call__(self, package: ConfigurationPackage, branch: str) -> Mapping[str, Any]:
        del package
        self.branches.append(branch)
        if self.failure is not None:
            raise self.failure
        if self.raw_schema is not None:
            return capabilities_module._normalized_schema_snapshot(self.raw_schema)
        return self.snapshot


@dataclass
class _Harness:
    """One registered run whose retained plan is ready to apply."""

    binding: tuple[str, int, str]
    checksum: str
    calls: list[dict[str, Any]]
    spy: _SnapshotSpy
    run_dir: Path
    instance: SyncInstance
    projection: Any


def _fingerprint(instance: SyncInstance, snapshot: dict[str, Any]) -> str:
    return compute_consumed_schema_fingerprint(configuration=instance, snapshot=normalize_destination_schema(snapshot))


def _harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    recorded_snapshot: dict[str, Any] | None = None,
    schema_fingerprint: str | None = None,
) -> _Harness:
    """Register one configuration, retain one plan against it, and disarm execution."""
    monkeypatch.setenv("NETBOX_TOKEN", NETBOX_CANARY)
    monkeypatch.setenv("INFRAHUB_API_TOKEN", INFRAHUB_CANARY)
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)

    package = _package()
    projection = local_product_projection(tmp_path / "product")
    registered = projection.create_configuration(package)
    binding = (registered.config_id, registered.registry_version, registered.package_checksum)
    projection.create_run(
        ProductRun(
            run_id=RUN_ID,
            operation="apply",
            configuration_reference=f"{binding[0]}@{binding[1]}",
            config_id=binding[0],
            registry_version=binding[1],
            package_checksum=binding[2],
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="planned",
        )
    )
    append_execution(
        projection,
        RUN_ID,
        PrefectExecutionLink(
            flow_run_id=FLOW_RUN_ID, purpose="apply", attempt=1, submitted_at=datetime.now(timezone.utc)
        ),
    )

    instance = resolve_runtime_instance(package, directory=str(tmp_path))
    instance._configuration_binding = binding
    recorded = (
        schema_fingerprint
        if schema_fingerprint is not None
        else _fingerprint(instance, recorded_snapshot if recorded_snapshot is not None else _SNAPSHOT)
    )
    run_dir = tmp_path / "runs" / instance.name / RUN_ID
    manifest = write_plan_artifact(
        run_dir=run_dir,
        run_id=RUN_ID,
        config_version=resolve_config_version(instance),
        source_snapshot=[],
        deletes_computed=True,
        operations=[],
        configuration_binding=binding,
        schema_fingerprint=recorded,
    )

    spy = _SnapshotSpy()
    monkeypatch.setattr(worker_module, "read_destination_schema_snapshot", spy)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (service_flow.logger, False))
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: FLOW_RUN_ID)
    monkeypatch.setattr(service_flow, "_require_current_worker_identity", lambda *_args: None)

    calls: list[dict[str, Any]] = []

    def _execution_sentinel(*_args: object, **kwargs: Any) -> RunResult:  # noqa: ANN401 — the stage's own kwargs
        calls.append(kwargs)
        return RunResult(
            sync_name=instance.name,
            operation="apply",
            run_id=RUN_ID,
            status="no-change",
            changed=False,
            summary={"create": 0, "update": 0, "delete": 0},
            artifact_path=str(run_dir),
        )

    monkeypatch.setattr(service_flow, "execute_run", _execution_sentinel)
    return _Harness(
        binding=binding,
        checksum=manifest.plan_checksum,
        calls=calls,
        spy=spy,
        run_dir=run_dir,
        instance=instance,
        projection=projection,
    )


def _apply(harness: _Harness, *, expected_checksum: str | None = None) -> dict[str, Any]:
    return service_sync_run.fn(
        RUN_ID,
        "apply",
        *harness.binding,
        expected_checksum=harness.checksum if expected_checksum is None else expected_checksum,
        confirm_writes=True,
    )


def _execute_apply_stage(harness: _Harness) -> tuple[dict[str, Any], Any]:
    """Drive the production stage boundary before remote exception sanitization."""
    with stage_scratch("apply") as scratch:
        return service_flow._execute_stage(
            RUN_ID,
            "apply",
            *harness.binding,
            None,
            harness.checksum,
            confirm_writes=True,
            run_logger=service_flow.logger,
            secrets=[],
            config_directory=str(harness.run_dir.parents[2]),
            projection=harness.projection,
            tracker=WriteDispatchTracker(),
            scratch=scratch,
        )


def _rewrite_manifest(harness: _Harness, mapping: dict[str, Any], *, recompute_checksum: bool) -> None:
    """Replace the retained manifest's bytes, optionally repairing its own checksum."""
    plan_dir = harness.run_dir / PLAN_DIR_NAME
    if recompute_checksum:
        mapping["plan_checksum"] = compute_plan_checksum(mapping, (plan_dir / OPERATIONS_FILE_NAME).read_bytes())
    (plan_dir / MANIFEST_FILE_NAME).write_bytes(canonical_json_bytes(mapping))


def _retained_mapping(harness: _Harness) -> dict[str, Any]:
    return json.loads((harness.run_dir / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes())


# ======================================================================================
# Compatible: the retained plan applies
# ======================================================================================


def test_an_unchanged_consumed_schema_applies_the_retained_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch)

    result = _apply(harness)

    assert result["outcome"] == "no-change"
    assert len(harness.calls) == 1
    # The guard read the live schema on the run's effective destination branch.
    assert harness.spy.branches == ["main"]


@pytest.mark.parametrize(
    "live",
    [
        pytest.param(
            {
                **_SNAPSHOT,
                "InfraDevice": {
                    "human_friendly_id": ["name__value"],
                    "uniqueness_constraints": [],
                    "attributes": {"name": {"kind": "Text", "optional": True, "default_value": None, "unique": False}},
                    "relationships": {},
                },
            },
            id="unmapped-kind-added",
        ),
        pytest.param(
            _mutated(
                "BuiltinTag",
                **{"attributes.colour": {"kind": "Text", "optional": True, "default_value": None, "unique": False}},
            ),
            id="optional-unmapped-attribute-added",
        ),
        pytest.param(
            _mutated(
                "BuiltinTag",
                **{"attributes.weight": {"kind": "Number", "optional": False, "default_value": 1, "unique": False}},
            ),
            id="defaulted-unmapped-attribute-added",
        ),
        pytest.param({kind: _SNAPSHOT[kind] for kind in reversed(list(_SNAPSHOT))}, id="kind-delivery-order"),
    ],
)
def test_compatible_destination_growth_applies_the_retained_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live: dict[str, Any]
) -> None:
    """Growth the configuration does not consume needs no new plan and no restart."""
    harness = _harness(tmp_path, monkeypatch)
    harness.spy.snapshot = live

    _apply(harness)

    assert len(harness.calls) == 1


# ======================================================================================
# Incompatible: the retained plan refuses before any write
# ======================================================================================


@pytest.mark.parametrize(
    "live",
    [
        pytest.param(_mutated("BuiltinTag", **{"attributes.name.kind": "Number"}), id="mapped-attribute-kind"),
        pytest.param(_mutated("BuiltinTag", **{"attributes.name.optional": True}), id="mapped-attribute-optional"),
        pytest.param(_mutated("BuiltinTag", **{"attributes.name.unique": False}), id="mapped-attribute-uniqueness"),
        pytest.param(
            _mutated("BuiltinTag", **{"attributes.description.default_value": "unset"}),
            id="mapped-attribute-default",
        ),
        pytest.param(
            _mutated("LocationSite", **{"relationships.tags.cardinality": "one"}),
            id="mapped-relationship-cardinality",
        ),
        pytest.param(
            _mutated("LocationSite", **{"relationships.tags.peer": "BuiltinStatus"}),
            id="mapped-relationship-peer",
        ),
        pytest.param(
            _mutated("LocationSite", **{"relationships.tags.kind": "Attribute"}),
            id="mapped-relationship-kind",
        ),
        pytest.param(
            _mutated("BuiltinTag", human_friendly_id=["description__value"]),
            id="destination-human-friendly-id",
        ),
        pytest.param(
            _mutated("BuiltinTag", uniqueness_constraints=[["name__value", "description__value"]]),
            id="uniqueness-constraint",
        ),
        pytest.param(
            _mutated(
                "BuiltinTag",
                **{"attributes.owner": {"kind": "Text", "optional": False, "default_value": None, "unique": False}},
            ),
            id="mandatory-unmapped-attribute-added",
        ),
        pytest.param({"BuiltinTag": _SNAPSHOT["BuiltinTag"]}, id="consumed-kind-removed"),
    ],
)
def test_an_incompatible_consumed_schema_change_refuses_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live: dict[str, Any]
) -> None:
    harness = _harness(tmp_path, monkeypatch)
    harness.spy.snapshot = live
    retained = (harness.run_dir / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes()

    with pytest.raises(PlanSchemaChangedError):
        _execute_apply_stage(harness)

    assert harness.calls == []
    # No source was read, no destination was written, and the artifact is untouched.
    assert (harness.run_dir / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes() == retained


def test_the_refusal_names_both_fingerprints_and_the_remedy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _harness(tmp_path, monkeypatch)
    live = _mutated("BuiltinTag", **{"attributes.name.kind": "Number"})
    harness.spy.snapshot = live

    with pytest.raises(PlanSchemaChangedError) as caught:
        _execute_apply_stage(harness)

    message = str(caught.value)
    assert _fingerprint(harness.instance, _SNAPSHOT) in message
    assert _fingerprint(harness.instance, live) in message
    assert "review" in message
    assert "new plan" in message


def test_the_refusal_carries_no_credential_or_endpoint_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cross-entry: credential resolution runs before the guard; nothing of it escapes."""
    harness = _harness(tmp_path, monkeypatch)
    harness.spy.snapshot = _mutated("BuiltinTag", **{"attributes.name.kind": "Number"})

    with pytest.raises(RuntimeError) as caught:
        _apply(harness)

    rendered = repr(caught.value.args) + str(caught.value)
    for secret in (NETBOX_CANARY, INFRAHUB_CANARY):
        assert secret not in rendered


# ======================================================================================
# The schema binding itself: missing, malformed, edited, duplicated
# ======================================================================================


def test_a_registered_plan_with_no_schema_binding_refuses_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch)
    mapping = _retained_mapping(harness)
    del mapping["schema_fingerprint"]
    _rewrite_manifest(harness, mapping, recompute_checksum=True)

    with pytest.raises(RuntimeError):
        _apply(harness, expected_checksum=_retained_mapping(harness)["plan_checksum"])

    assert harness.calls == []


def test_a_registered_plan_with_a_malformed_schema_binding_refuses_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The legacy 12-hex kind-name subhash is not a consumed-semantics fingerprint."""
    harness = _harness(tmp_path, monkeypatch)
    mapping = _retained_mapping(harness)
    mapping["schema_fingerprint"] = "5f2c9b1e7a4d"
    _rewrite_manifest(harness, mapping, recompute_checksum=True)

    with pytest.raises(RuntimeError):
        _apply(harness, expected_checksum=_retained_mapping(harness)["plan_checksum"])

    assert harness.calls == []


def test_a_schema_binding_edited_after_review_fails_the_artifact_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hostile: swapping the recorded binding leaves the artifact self-inconsistent."""
    harness = _harness(tmp_path, monkeypatch)
    drifted = _mutated("BuiltinTag", **{"attributes.name.kind": "Number"})
    harness.spy.snapshot = drifted
    mapping = _retained_mapping(harness)
    mapping["schema_fingerprint"] = _fingerprint(harness.instance, drifted)
    _rewrite_manifest(harness, mapping, recompute_checksum=False)

    with pytest.raises(RuntimeError, match="verification failed"):
        _apply(harness)

    assert harness.calls == []


def test_a_reswapped_binding_with_a_repaired_checksum_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checksum closure: repairing `plan_checksum` breaks the operator's approved value."""
    harness = _harness(tmp_path, monkeypatch)
    drifted = _mutated("BuiltinTag", **{"attributes.name.kind": "Number"})
    harness.spy.snapshot = drifted
    mapping = _retained_mapping(harness)
    mapping["schema_fingerprint"] = _fingerprint(harness.instance, drifted)
    _rewrite_manifest(harness, mapping, recompute_checksum=True)

    with pytest.raises(RuntimeError, match="checksum"):
        _apply(harness)

    assert harness.calls == []


def test_a_duplicate_schema_binding_key_refuses_before_the_schema_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hostile: two values for one binding, self-consistent and operator-approved.

    The checksum is repaired over the collapsed parse and that repaired value is what the
    operator passes as `expected_checksum`, so neither the artifact checksum nor the
    approval can catch it. The manifest is refused for being ambiguous, before the live
    schema is read, before any source is touched, and before execution.
    """
    harness = _harness(tmp_path, monkeypatch)
    plan_dir = harness.run_dir / PLAN_DIR_NAME
    manifest_path = plan_dir / MANIFEST_FILE_NAME
    approved = duplicated_key_manifest_bytes(
        json.loads(manifest_path.read_bytes()),
        key="schema_fingerprint",
        first=_fingerprint(harness.instance, _mutated("BuiltinTag", **{"attributes.name.kind": "Number"})),
        operations_bytes=(plan_dir / OPERATIONS_FILE_NAME).read_bytes(),
    )
    manifest_path.write_bytes(approved)

    with pytest.raises(RuntimeError, match="verification failed"):
        _apply(harness, expected_checksum=json.loads(approved)["plan_checksum"])

    assert harness.calls == []
    assert harness.spy.branches == []


# ======================================================================================
# Checksum closure across the two artifact reads
# ======================================================================================


def test_the_early_gate_requires_the_operator_approved_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="checksum"):
        _apply(harness, expected_checksum="a" * 64)

    assert harness.calls == []


def test_the_later_artifact_read_is_given_the_same_approved_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One operator value gates both reads, so bytes swapped between them cannot apply."""
    harness = _harness(tmp_path, monkeypatch)

    _apply(harness)

    assert harness.calls[0]["expected_checksum"] == harness.checksum


def test_no_worker_parameter_offers_a_schema_override() -> None:
    """AR8: no flag or argument reaches the guard's decision."""
    assert tuple(inspect.signature(service_sync_run.fn).parameters) == (
        "run_id",
        "stage",
        "config_id",
        "registry_version",
        "package_checksum",
        "branch",
        "expected_checksum",
        "confirm_writes",
    )


# ======================================================================================
# Unusable live schema
# ======================================================================================


@pytest.mark.parametrize(
    "live",
    [
        pytest.param(_mutated("BuiltinTag", **{"attributes.name.kind": "Unicorn"}), id="unsupported-attribute-kind"),
        pytest.param(
            _mutated("LocationSite", **{"relationships.tags.cardinality": "several"}), id="unsupported-cardinality"
        ),
        pytest.param({kind: value for kind, value in _SNAPSHOT.items() if kind != "LocationSite"}, id="missing-kind"),
    ],
)
def test_an_unusable_live_schema_refuses_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live: dict[str, Any]
) -> None:
    harness = _harness(tmp_path, monkeypatch)
    harness.spy.snapshot = live

    with pytest.raises(RuntimeError):
        _apply(harness)

    assert harness.calls == []


def test_a_failed_schema_read_refuses_with_only_its_short_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cross-entry: hostile accessor text stays inside the adapter boundary."""
    harness = _harness(tmp_path, monkeypatch)
    harness.spy.failure = DestinationSchemaReadError(
        f"\x07token={INFRAHUB_CANARY} at https://destination.invalid/graphql", reason="unauthorized"
    )

    with pytest.raises(RuntimeError) as caught:
        _apply(harness)

    message = str(caught.value)
    assert "unauthorized" in message
    assert INFRAHUB_CANARY not in message
    assert "https://destination.invalid" not in message
    assert harness.calls == []


# ======================================================================================
# AR7 — the binding survives publication
# ======================================================================================


def _published_plan(harness: _Harness) -> PlanResource:
    """Publish the retained plan through the real review path and read the stored bytes back."""
    saved = read_saved_plan(sync_name=harness.instance.name, run_id=RUN_ID)
    service_flow._publish_plan(harness.projection, RUN_ID, saved, [])
    stored = harness.projection.lookup_artifact(RUN_ID, PLAN_ARTIFACT_ID)
    assert stored.value is not None
    return PlanResource.model_validate_json(stored.value)


def test_a_published_registered_plan_carries_its_schema_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reviewer reading the published document sees the semantics the plan is bound to."""
    harness = _harness(tmp_path, monkeypatch)

    published = _published_plan(harness)

    assert published.schema_fingerprint == _fingerprint(harness.instance, _SNAPSHOT)
    assert published.checksum == harness.checksum
    assert published.checksum_ok


def test_a_published_unregistered_plan_carries_no_schema_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy publication is unchanged: the field is present and null, like every other optional."""
    harness = _harness(tmp_path, monkeypatch)
    write_plan_artifact(
        run_dir=harness.run_dir.parent / "legacy-run",
        run_id="legacy-run",
        config_version=resolve_config_version(harness.instance),
        source_snapshot=[],
        deletes_computed=True,
        operations=[],
    )
    saved = read_saved_plan(sync_name=harness.instance.name, run_id="legacy-run")

    document = service_flow._review_document("legacy-run", saved)

    assert document.schema_fingerprint is None
    assert json.loads(document.model_dump_json())["schema_fingerprint"] is None


# ======================================================================================
# AR4/AR8 — an ambiguous live member name refuses before any write
# ======================================================================================


def _live_node(*, attributes: list[dict[str, Any]], relationships: list[dict[str, Any]]) -> NodeSchemaAPI:
    """One typed installed-SDK node, as the live destination would deliver it."""
    return NodeSchemaAPI.model_validate(
        {
            "name": "Tag",
            "namespace": "Builtin",
            "human_friendly_id": ["name__value"],
            "uniqueness_constraints": [["name__value"]],
            "attributes": attributes,
            "relationships": relationships,
        }
    )


@pytest.mark.parametrize(
    "node",
    [
        pytest.param(
            _live_node(
                attributes=[
                    {"name": "name", "kind": AttributeKind.TEXT, "optional": False, "unique": True},
                    {"name": "name", "kind": AttributeKind.NUMBER, "optional": True},
                ],
                relationships=[],
            ),
            id="duplicate-attribute-name",
        ),
        pytest.param(
            _live_node(
                attributes=[{"name": "name", "kind": AttributeKind.TEXT, "optional": False, "unique": True}],
                relationships=[
                    {
                        "name": "name",
                        "peer": "LocationSite",
                        "cardinality": "one",
                        "optional": True,
                        "kind": "Attribute",
                    }
                ],
            ),
            id="attribute-relationship-conflict",
        ),
        pytest.param(
            _live_node(
                attributes=[{"name": "na\x00me\x1b[31m", "kind": AttributeKind.TEXT, "optional": True}],
                relationships=[],
            ),
            id="control-bearing-name",
        ),
    ],
)
def test_an_ambiguous_live_member_name_refuses_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, node: NodeSchemaAPI
) -> None:
    """The guard's live read goes through the accessor boundary, so it refuses there."""
    harness = _harness(tmp_path, monkeypatch)
    # Every other mapped kind is well formed, so the ambiguous member name is the only
    # thing that can refuse this apply.
    harness.spy.raw_schema = {
        "BuiltinTag": node,
        "LocationSite": _live_node(
            attributes=[{"name": "name", "kind": AttributeKind.TEXT, "optional": False, "unique": True}],
            relationships=[
                {"name": "tags", "peer": "BuiltinTag", "cardinality": "many", "optional": True, "kind": "Generic"}
            ],
        ),
    }
    retained = (harness.run_dir / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes()

    with pytest.raises(RuntimeError) as caught:
        _apply(harness)

    assert harness.calls == []
    assert (harness.run_dir / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes() == retained
    message = str(caught.value)
    assert "rejected" in message
    for leaked in ("\x00", "\x1b", INFRAHUB_CANARY, NETBOX_CANARY):
        assert leaked not in message


def test_a_separator_bearing_unmapped_member_refuses_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The compatible-growth path: an unusable name the fingerprint would never notice.

    `"bad name"` is printable, and as an optional unmapped attribute it is excluded from
    the consumed-semantics projection — so the recorded fingerprint still matches and the
    drift guard has nothing to compare. The adapter boundary is the only place that can
    refuse it, and it must, before the source or a write-capable destination is reached.
    """
    harness = _harness(tmp_path, monkeypatch)
    harness.spy.raw_schema = {
        "BuiltinTag": _live_node(
            attributes=[
                {"name": "name", "kind": AttributeKind.TEXT, "optional": False, "unique": True},
                {"name": "description", "kind": AttributeKind.TEXT, "optional": True},
                {"name": "bad name", "kind": AttributeKind.TEXT, "optional": True},
            ],
            relationships=[],
        ),
        "LocationSite": _live_node(
            attributes=[{"name": "name", "kind": AttributeKind.TEXT, "optional": False, "unique": True}],
            relationships=[
                {"name": "tags", "peer": "BuiltinTag", "cardinality": "many", "optional": True, "kind": "Generic"}
            ],
        ),
    }
    retained = (harness.run_dir / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes()

    with pytest.raises(RuntimeError, match="rejected"):
        _apply(harness)

    assert harness.calls == []
    assert (harness.run_dir / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes() == retained
