"""The command-line review mode, its errors, its isolation, and the apply refusals.

`diff --from-plan <run-id>` and the `apply` it exists to make safe share one file because
they share one surface: splitting them would duplicate the fixtures and let the review
assertions and the apply assertions drift apart, which is the very divergence between what
was reviewed and what was applied that this feature closes.

Two properties here are deliberate and **must not be "tidied"**. The review cases assert
against **stdout** (`typer.echo`) while every error case asserts against the **logger**,
because `print_error_and_abort` reports there — a test reading both from one stream would
pass against an implementation that merged them and broke FR-008's channel split. And the
apply cases run against a real `Potenda` over a recording destination rather than a
`MagicMock`: a mock answers `hasattr` for every name, so the missing-write-surface refusal
cannot be expressed against one.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import subprocess  # noqa: S404 — an exited producer is the point: FR-007 measures reading after it is gone
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from filelock import FileLock, Timeout
from infrahub_sdk.exceptions import GraphQLError
from typer.testing import CliRunner

from infrahub_sync.cache.locks import pipeline_lock
from infrahub_sync.cache.parquet_io import write_resource_side
from infrahub_sync.cli import app
from infrahub_sync.execution import execute_run
from infrahub_sync.plan.checksum import source_snapshot_records
from infrahub_sync.plan.config_version import default_config_version
from infrahub_sync.plan.errors import (
    ApplyRecordInvariantError,
    ConvergenceIdentityError,
    DuplicateOperationIdError,
    OperationApplyFailedError,
    PeerAmbiguousError,
    PeerNotFoundError,
    PlanArtifactError,
    PlanArtifactTornError,
    PlanArtifactUnreadableError,
    PlanFormatV1Error,
    PlanFormatVersionError,
    PlanGenerationExistsError,
    PlanVerificationError,
    SourcePeerUnresolvedError,
    UnaccountedIdentityComponentError,
    UnformableDestinationIdentityError,
    UnkeyedWriteRefusedError,
    UnknownPlanKindError,
    UnknownRunIdentifierError,
    UnsafeRunIdentifierError,
    UnserializablePayloadValueError,
    UnsupportedOperationActionError,
    UnwalkedDiffChildrenError,
)
from infrahub_sync.plan.models import ACTIONS, SUPPORTED_FORMAT_VERSIONS, ApplyRecord, DestinationBindingRecord
from infrahub_sync.plan.reader import parse_plan_artifact
from infrahub_sync.plan.review import RUN_ID_LISTING_LIMIT
from infrahub_sync.potenda import Potenda
from infrahub_sync.utils import PlanApplier, get_instance
from tests.plan.artifact_fixtures import (
    OTHER_RUN_ID,
    RUN_ID,
    manifest_path,
    operation_record,
    operations_path,
    plan_dir,
    tamper_with_operations,
    tamperable_operation,
    write_artifact,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from infrahub_sync.plan.models import PlannedOperation

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"

# The real example configuration, reached through the real loader: review is adapter-free but
# stays configuration-bound, because a stored run is located only as
# `cache_root_for(<sync name>)/<run_id>`.
SYNC_NAME = "from-netbox"

# Declared by `examples/netbox_to_infrahub/config.yml` and deliberately absent from every
# fixture plan below — AD058's "declared but unrepresented" arm needs both halves to be true.
DECLARED_BUT_UNPLANNED_KIND = "LocationRack"
UNDECLARED_KIND = "NotAKindThisSyncDeclares"

UNSUPPORTED_FORMAT_VERSION = 99

# The AD020 minimum per-object field set, as it is rendered.
DETAIL_IDENTITIES = ("name=prod", "name=staging", "name=dc1", "name=retired")

runner = CliRunner()

_ANSI_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")


# ======================================================================================
# Shared helpers
# ======================================================================================


def _strip_ansi(text: str) -> str:
    """Return `text` with ANSI SGR (colour) escape sequences removed."""
    return _ANSI_SGR_RE.sub("", text)


def _flat(text: str) -> str:
    """Collapse every run of whitespace to one space.

    The renderer hard-wraps its `NOTE` blocks and rich hard-wraps its help panels, so a
    phrase an operator reads as one sentence is several lines on disk. Flattening is what
    lets a test assert the sentence rather than the column width the sentence happened to be
    laid out at — which AD030 explicitly refuses to make a contract.
    """
    return " ".join(text.split())


def _tree(directory: Path) -> set[str]:
    """Every path under `directory`, relative and POSIX, for a before/after comparison."""
    if not directory.exists():
        return set()
    return {path.relative_to(directory).as_posix() for path in directory.rglob("*")}


@pytest.fixture(autouse=True)
def _isolated_cache_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep every case inside `tmp_path`, so no test reads a real developer cache."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))


@pytest.fixture(autouse=True)
def adapter_construction_log(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Make adapter construction fail loudly, for every case in this file.

    "Source and destination unreachable" is a precondition of SC-009's CLI cases, and the
    honest way to hold it is to make the attempt fail rather than to hope the code path
    avoided it. `import_adapter` is the single function `get_potenda_from_instance` reaches
    both adapters through (`infrahub_sync/utils.py`), so refusing here refuses both.

    Returns the call log, so a test can assert the sentinel never fired rather than only
    that nothing blew up.
    """
    calls: list[dict[str, Any]] = []

    def _refuse(**kwargs: Any) -> Any:  # noqa: ANN401 — mirrors the real keyword-only signature
        calls.append(kwargs)
        msg = "no adapter may be constructed on this path (FR-008, SC-009)"
        raise AssertionError(msg)

    monkeypatch.setattr("infrahub_sync.utils.import_adapter", _refuse)
    for name in ("INFRAHUB_ADDRESS", "INFRAHUB_API_TOKEN", "INFRAHUB_TOKEN", "NETBOX_ADDRESS", "NETBOX_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    return calls


def _cache_root(tmp_path: Path) -> Path:
    return tmp_path / SYNC_NAME


def _run_directory(tmp_path: Path, run_id: str = RUN_ID) -> Path:
    directory = _cache_root(tmp_path) / run_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _config_version() -> str:
    """The value the CLI's apply path recomputes and compares the manifest's against.

    Read from the real configuration through the real rule, not hard-coded: a hard-coded
    value would make every clean-apply case in this file fail the moment the example
    configuration changed, and would prove nothing about the comparison the CLI actually
    makes (FR-011, AD013).
    """
    instance = get_instance(name=SYNC_NAME, directory=str(EXAMPLES_DIR))
    assert instance is not None
    return default_config_version(instance)


def _review(*options: str) -> Any:  # noqa: ANN401 — click's Result type is not exported for annotation
    """Invoke `diff` against the example configuration with `options` appended."""
    return runner.invoke(app, ["diff", "--name", SYNC_NAME, "--directory", str(EXAMPLES_DIR), *options])


def _apply(run_id: str, *, quiet: bool = False) -> Any:  # noqa: ANN401 — click's Result type is not exported for annotation
    """Invoke `apply` for `run_id` against the example configuration.

    `quiet` passes the app-level `--quiet`, which floors the package logger at
    `logging.WARNING` (`infrahub_sync/cli.py`) — the invocation SC-007's warning
    obligations are pinned above, and therefore the one that can tell an `INFO` emission from
    a `WARNING` one.
    """
    prefix = ["--quiet"] if quiet else []
    return runner.invoke(
        app, [*prefix, "apply", "--name", SYNC_NAME, "--directory", str(EXAMPLES_DIR), "--run-id", run_id]
    )


# The plan every rendering case reads. Its stored order is deliberately not its sorted order,
# and it carries exactly one delete so the AD056 annotation has a count to name.
MIXED_PLAN: tuple[dict[str, Any], ...] = (
    operation_record(identity={"name": "prod"}),
    operation_record(identity={"name": "staging"}),
    operation_record(action="update", kind="LocationSite", identity={"name": "dc1"}, tier=1),
    operation_record(action="delete", identity={"name": "retired"}),
)

DELETELESS_PLAN: tuple[dict[str, Any], ...] = MIXED_PLAN[:3]


def _store(
    tmp_path: Path,
    records: Sequence[Mapping[str, Any]] = MIXED_PLAN,
    *,
    run_id: str = RUN_ID,
    deletes_computed: bool = True,
    config_version: str | None = None,
    **manifest_overrides: Any,  # noqa: ANN401 — a manifest field's value is any JSON value
) -> Path:
    """Store an artifact where the CLI looks for it, and return its run directory."""
    directory = _run_directory(tmp_path, run_id)
    write_artifact(
        directory,
        list(records),
        run_id=run_id,
        deletes_computed=deletes_computed,
        config_version=_config_version() if config_version is None else config_version,
        **manifest_overrides,
    )
    return directory


# ======================================================================================
# SC-009's CLI half
# ======================================================================================

# Writes the artifacts and then **exits**. FR-007's claim is that the producing process need
# not be alive, and the only way to hold that claim honestly is for it not to be.
WRITER_SCRIPT = '''\
"""Write two plan artifacts and exit, so the CLI reads them from a dead producer."""

import json
import pathlib
import sys

from tests.plan.artifact_fixtures import operation_record, write_artifact

for spec in json.loads(sys.argv[1]):
    directory = pathlib.Path(spec["directory"])
    directory.mkdir(parents=True, exist_ok=True)
    write_artifact(
        directory,
        [operation_record(**kwargs) for kwargs in spec["records"]],
        run_id=spec["run_id"],
        config_version=spec["config_version"],
        deletes_computed=spec["deletes_computed"],
    )
'''

# The `operation_record` keyword sets the writer subprocess rebuilds. Kept as data rather than
# as objects because they cross a process boundary as JSON.
COMPUTED_RECORD_SPECS: tuple[dict[str, Any], ...] = (
    {"identity": {"name": "prod"}},
    {"identity": {"name": "staging"}},
    {"action": "update", "kind": "LocationSite", "identity": {"name": "dc1"}, "tier": 1},
    {"action": "delete", "identity": {"name": "retired"}},
)
INCREMENTAL_RECORD_SPECS: tuple[dict[str, Any], ...] = COMPUTED_RECORD_SPECS[:3]

INCREMENTAL_RUN_ID = OTHER_RUN_ID


@pytest.fixture
def _artifacts_from_an_exited_process(tmp_path: Path) -> tuple[Path, Path]:
    """Write both SC-009 fixtures in one subprocess and wait for it to exit.

    Two plans, because SC-009 requires two of its four cases to run against a plan whose
    destination side was loaded incrementally: `RUN_ID` records `delete_operations_computed:
    true` and carries a delete, `INCREMENTAL_RUN_ID` records it false and carries none.
    """
    script = tmp_path / "write_artifacts.py"
    script.write_text(WRITER_SCRIPT, encoding="utf-8")
    computed = _cache_root(tmp_path) / RUN_ID
    incremental = _cache_root(tmp_path) / INCREMENTAL_RUN_ID
    spec = [
        {
            "directory": str(computed),
            "run_id": RUN_ID,
            "records": list(COMPUTED_RECORD_SPECS),
            "config_version": _config_version(),
            "deletes_computed": True,
        },
        {
            "directory": str(incremental),
            "run_id": INCREMENTAL_RUN_ID,
            "records": list(INCREMENTAL_RECORD_SPECS),
            "config_version": _config_version(),
            "deletes_computed": False,
        },
    ]
    environment = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    completed = subprocess.run(  # noqa: S603 — a fixed argument vector run through sys.executable
        [sys.executable, str(script), json.dumps(spec)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert completed.returncode == 0, f"the writer subprocess failed:\n{completed.stderr}"
    return computed, incremental


SC009_CLI_CASES: dict[str, tuple[tuple[str, ...], bool]] = {
    # (extra options, whether the plan's deletes were computed)
    "summary_deletes_computed": ((), True),
    "detail_deletes_computed": (("--detail",), True),
    "summary_loaded_incrementally": ((), False),
    "detail_with_kind_loaded_incrementally": (("--detail", "--kind", "BuiltinTag"), False),
}


@pytest.mark.parametrize(("options", "deletes_computed"), list(SC009_CLI_CASES.values()), ids=list(SC009_CLI_CASES))
@pytest.mark.usefixtures("_artifacts_from_an_exited_process")
def test_sc009_cli_case_renders_from_a_stored_artifact(
    options: tuple[str, ...],
    deletes_computed: bool,  # noqa: FBT001 — a parametrized case discriminator, not a caller-facing switch
) -> None:
    """Each of SC-009's CLI cases renders, and states its delete-computation record (AD056)."""
    run_id = RUN_ID if deletes_computed else INCREMENTAL_RUN_ID

    result = _review("--from-plan", run_id, *options)

    assert result.exit_code == 0, result.output
    output = _flat(result.output)
    assert f"Plan {run_id}" in output
    assert f"deletes computed: {'yes' if deletes_computed else 'NO'}" in output
    if not deletes_computed:
        assert "Delete operations were NOT computed for this plan" in output


@pytest.mark.usefixtures("_artifacts_from_an_exited_process")
def test_the_cli_summary_presents_a_count_per_action_and_a_count_per_kind() -> None:
    """FR-006's summary depth, as an operator reads it (SC-009)."""
    result = _review("--from-plan", RUN_ID)

    assert result.exit_code == 0, result.output
    output = _flat(result.output)
    assert "By action create 2 delete 1 update 1" in output
    assert "By kind BuiltinTag 3 LocationSite 1" in output
    assert "operations: 4" in output


@pytest.mark.usefixtures("_artifacts_from_an_exited_process")
def test_the_cli_detail_presents_one_record_per_operation_with_the_ad020_field_set() -> None:
    """One record per operation, each carrying identifier, action, kind and identity (AD020)."""
    result = _review("--from-plan", RUN_ID, "--detail")

    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.startswith("op_")]
    assert len(lines) == len(COMPUTED_RECORD_SPECS)
    for record, line in zip(MIXED_PLAN, lines):
        assert line.startswith(str(record["operation_id"]))
        assert f" {record['action']} " in line
        assert f" {record['kind']} " in line
    for identity in DETAIL_IDENTITIES:
        assert identity in result.output


@pytest.mark.usefixtures("_artifacts_from_an_exited_process")
def test_the_cli_detail_narrows_to_one_kind(tmp_path: Path) -> None:
    """`--kind` is a narrowing of the same listing, not a second reading path (FR-029)."""
    _ = tmp_path

    result = _review("--from-plan", RUN_ID, "--detail", "--kind", "LocationSite")

    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.startswith("op_")]
    assert len(lines) == 1
    assert "LocationSite" in lines[0]
    assert "BuiltinTag" not in lines[0]


# A relationship-bearing identity, which every other plan in this file lacks: each of the
# four flat `{"name": …}` identities above renders through one branch of
# `_identity_value_text`, so the nested and collection branches AD043 exists for are
# reachable from no other case here. Three shapes in one plan:
#
# - `location` — a `{peer_kind, identity}` pair whose own identity contains another one, so
#   the recursion AD043 requires is rendered to two levels rather than assumed at one;
# - `tags` — a list of pairs, the cardinality-many spelling;
# - `meta` — a mapping that is *not* a peer pair, the fallback the renderer keeps for a
#   value it cannot read as a reference.
NESTED_PEER_PLAN: tuple[dict[str, Any], ...] = (
    operation_record(
        kind="DcimDevice",
        identity={
            "name": "router1",
            "location": {
                "peer_kind": "LocationRack",
                "identity": {"name": "rack-7", "site": {"peer_kind": "LocationSite", "identity": {"name": "dc1"}}},
            },
            "tags": [{"peer_kind": "BuiltinTag", "identity": {"name": "edge"}}],
        },
    ),
    operation_record(kind="BuiltinTag", identity={"meta": {"b": 2, "a": 1}}),
)

# Rendered key-sorted, one entry per identity attribute. Asserted as whole strings rather
# than by fragment: a renderer that dropped the nesting would still contain "rack-7".
NESTED_PEER_DETAIL = (
    "location=LocationRack(name=rack-7 site=LocationSite(name=dc1)) name=router1 tags=[BuiltinTag(name=edge)]",
    "meta={a=1, b=2}",
)


def test_the_cli_detail_renders_a_nested_peer_identity_recursively(tmp_path: Path) -> None:
    """AD043's identity shapes, as `--detail` renders them (FR-006, AD020)."""
    _store(tmp_path, NESTED_PEER_PLAN, run_id=OTHER_RUN_ID)

    result = _review("--from-plan", OTHER_RUN_ID, "--detail")

    assert result.exit_code == 0, result.output
    lines = [line for line in _strip_ansi(result.output).splitlines() if line.startswith("op_")]
    assert len(lines) == len(NESTED_PEER_PLAN)
    for rendered, line in zip(NESTED_PEER_DETAIL, lines, strict=True):
        assert line.endswith(rendered), f"expected identity {rendered!r} at the end of {line!r}"


# ======================================================================================
# `--detail` renders the desired destination state
# ======================================================================================

# Two operations on the **same** object: same action, same kind, same identity, and therefore
# the same operation identifier — differing only in the change they propose. Before that,
# these two rendered byte-identically, which is the defect: a reviewer could approve the
# object's presence in a plan without ever seeing what would be written to it.
ROUTER_IDENTITY: dict[str, Any] = {"name": "router1"}


def _payload_variant(**payload: Any) -> tuple[dict[str, Any], ...]:  # noqa: ANN401 — a payload value is any JSON value
    """A one-operation plan whose payload is `payload` plus the identity component."""
    return (operation_record(kind="DcimDevice", identity=ROUTER_IDENTITY, payload={**ROUTER_IDENTITY, **payload}),)


def _peer_variant(*peers: str) -> tuple[dict[str, Any], ...]:
    """A one-operation plan whose `tags` peer set is `peers`, empty set included."""
    return (
        operation_record(
            kind="DcimDevice",
            identity=ROUTER_IDENTITY,
            payload=dict(ROUTER_IDENTITY),
            relationships=[
                {
                    "field": "tags",
                    "peer_kind": "BuiltinTag",
                    "cardinality": "many",
                    "peers": [{"name": peer} for peer in peers],
                }
            ],
        ),
    )


def _detail_of(tmp_path: Path, records: tuple[dict[str, Any], ...], *, run_id: str) -> str:
    """Store `records` under `run_id` and return the `--detail` rendering."""
    _store(tmp_path, records, run_id=run_id)
    result = _review("--from-plan", run_id, "--detail")
    assert result.exit_code == 0, result.output
    return _strip_ansi(result.output)


def test_two_operations_differing_only_in_payload_render_differently(tmp_path: Path) -> None:
    """As the reviewer reproduced it: `role=router` versus `role=server`."""
    first = _detail_of(tmp_path, _payload_variant(role="router"), run_id=RUN_ID)
    second = _detail_of(tmp_path, _payload_variant(role="server"), run_id=OTHER_RUN_ID)

    assert "role = router" in first
    assert "role = server" in second
    assert "role = server" not in first
    assert _flat(first).replace(RUN_ID, "") != _flat(second).replace(OTHER_RUN_ID, "")


def test_two_operations_differing_only_in_their_peer_set_render_differently(tmp_path: Path) -> None:
    """The relationship half: peer kind and every peer's identity, per relationship field."""
    two_peers = _detail_of(tmp_path, _peer_variant("edge", "prod"), run_id=RUN_ID)
    one_peer = _detail_of(tmp_path, _peer_variant("edge"), run_id=OTHER_RUN_ID)
    emptied = _detail_of(tmp_path, _peer_variant(), run_id="20260728T0000-cccccccc")

    assert "tags -> BuiltinTag (many, 2 peer(s)): name=edge | name=prod" in two_peers
    assert "tags -> BuiltinTag (many, 1 peer(s)): name=edge" in one_peer
    assert "name=prod" not in one_peer
    assert "tags -> BuiltinTag (many, empty peer set)" in emptied


def test_the_desired_state_is_labelled_as_desired_state_and_not_as_a_diff(tmp_path: Path) -> None:
    """The label is part of the fix: the plan holds nothing about the destination's current state."""
    output = _flat(_detail_of(tmp_path, _payload_variant(role="router"), run_id=RUN_ID))

    assert "desired destination state" in output
    assert "not a diff" in output


def test_a_credential_shaped_field_name_is_redacted_while_its_siblings_render(tmp_path: Path) -> None:
    """The redaction policy, stated rather than implied — and scoped rather than total."""
    output = _detail_of(
        tmp_path,
        _payload_variant(
            role="router",
            api_token="tok-should-not-be-shown",  # noqa: S106 — synthetic literals, not credentials
            config={"password": "pw-should-not-be-shown", "hostname": "router1.example"},
        ),
        run_id=RUN_ID,
    )

    assert "tok-should-not-be-shown" not in output
    assert "pw-should-not-be-shown" not in output
    assert "api_token = <redacted" in output
    assert "password=<redacted" in output
    assert "role = router" in output
    assert "hostname=router1.example" in output


def test_an_overlong_value_is_elided_and_says_it_was(tmp_path: Path) -> None:
    """Elision is a readability bound and is rendered distinguishably from a redaction."""
    output = _detail_of(tmp_path, _payload_variant(description="x" * 500), run_id=RUN_ID)

    assert "elided, 500 characters" in output
    assert "<redacted" not in output


def test_a_delete_record_renders_no_desired_state(tmp_path: Path) -> None:
    """A delete carries no payload by construction, so there is nothing to show beneath it."""
    output = _detail_of(tmp_path, (operation_record(action="delete", identity={"name": "retired"}),), run_id=RUN_ID)

    record_lines = [line for line in output.splitlines() if line.startswith("op_")]
    assert len(record_lines) == 1
    trailing = output.splitlines()[output.splitlines().index(record_lines[0]) + 1 :]
    assert [line for line in trailing if line.strip()] == []


# ======================================================================================
# The four delete-disclosure cases
# ======================================================================================


def _both_depths(run_id: str) -> tuple[str, str]:
    """Render `run_id` at both depths and return both outputs, flattened.

    AD056 attaches its obligations to **both** depths, so every case below asserts against
    both. Asserting one and inferring the other is how a disclosure ends up on the summary
    and missing from the listing an operator actually approves from.
    """
    summary = _review("--from-plan", run_id)
    detail = _review("--from-plan", run_id, "--detail")
    assert summary.exit_code == 0, summary.output
    assert detail.exit_code == 0, detail.output
    return _flat(summary.output), _flat(detail.output)


def test_a_computed_plan_with_deletes_discloses_them_at_both_depths(tmp_path: Path) -> None:
    """(a) `deletes computed: yes`, the not-executed annotation, and a per-record marker."""
    _store(tmp_path, MIXED_PLAN, deletes_computed=True)

    summary, detail = _both_depths(RUN_ID)

    for output in (summary, detail):
        assert "deletes computed: yes" in output
        assert "1 delete operation(s) are recorded in this plan and NONE will be executed" in output
    # The per-record marker, which is the detail depth's own half of the obligation.
    delete_id = str(MIXED_PLAN[3]["operation_id"])
    detail_lines = _review("--from-plan", RUN_ID, "--detail").output.splitlines()
    marked = [line for line in detail_lines if line.startswith(delete_id)]
    assert len(marked) == 1
    assert "(not executed)" in marked[0]
    assert [line for line in detail_lines if line.startswith("op_") and "(not executed)" in line] == marked


def test_the_delete_note_does_not_promise_markers_a_kind_filter_removed(tmp_path: Path) -> None:
    """The note's promise has to survive `--kind` narrowing the listing."""
    _store(tmp_path, MIXED_PLAN, deletes_computed=True)

    result = _review("--from-plan", RUN_ID, "--detail", "--kind", "LocationSite")

    assert result.exit_code == 0, result.output
    output = _flat(result.output)
    assert "1 delete operation(s) are recorded in this plan and NONE will be executed" in output
    assert "a --kind filter may narrow the listing so that none of them are shown" in output
    assert [line for line in result.output.splitlines() if line.startswith("op_") and "(not executed)" in line] == []


def test_a_computed_plan_without_deletes_carries_no_not_executed_annotation(tmp_path: Path) -> None:
    """(b) The annotation is conditional, not unconditional noise."""
    _store(tmp_path, DELETELESS_PLAN, deletes_computed=True)

    summary, detail = _both_depths(RUN_ID)

    for output in (summary, detail):
        assert "deletes computed: yes" in output
        assert "will be executed" not in output
        assert "NONE will be executed" not in output
        assert "not executed" not in output


def test_a_plan_whose_deletes_were_not_computed_says_so_in_words_at_both_depths(tmp_path: Path) -> None:
    """(c) The not-computed wording, and its distinctness from (b)'s output."""
    _store(tmp_path, DELETELESS_PLAN, deletes_computed=False)
    not_computed_summary, not_computed_detail = _both_depths(RUN_ID)

    _store(tmp_path, DELETELESS_PLAN, run_id=OTHER_RUN_ID, deletes_computed=True)
    computed_summary, computed_detail = _both_depths(OTHER_RUN_ID)

    for output in (not_computed_summary, not_computed_detail):
        assert "deletes computed: NO" in output
        assert "Delete operations were NOT computed for this plan" in output
        assert "This plan may be missing deletes that exist" in output
        assert "Re-run with a full destination extract" in output
    # Textually distinct from the plan that genuinely has no deletes, which is the claim.
    assert "deletes computed: yes" in computed_summary
    assert "Delete operations were NOT computed" not in computed_summary
    assert "Delete operations were NOT computed" not in computed_detail
    assert not_computed_summary != computed_summary
    assert not_computed_detail != computed_detail


def test_the_in_process_reader_carries_both_disclosure_fields(tmp_path: Path) -> None:
    """(d) The CLI and the in-process reader disclose from **one** source (FR-029, AD056)."""
    from infrahub_sync.plan import read_saved_plan

    _store(tmp_path, MIXED_PLAN, deletes_computed=True)
    _store(tmp_path, DELETELESS_PLAN, run_id=OTHER_RUN_ID, deletes_computed=False)

    with_deletes = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID).summary()
    incremental = read_saved_plan(sync_name=SYNC_NAME, run_id=OTHER_RUN_ID).summary()

    assert with_deletes.delete_operations_computed is True
    assert with_deletes.deletes_not_executed == 1
    assert incremental.delete_operations_computed is False
    assert incremental.deletes_not_executed == 0


# ======================================================================================
# The error paths
# ======================================================================================


def _failed_review(caplog: pytest.LogCaptureFixture, *options: str) -> str:
    """Invoke a failing review and return the error text the CLI logged.

    Errors leave through `print_error_and_abort`, which reports on the **logger**, while the
    rendering leaves through `typer.echo` on stdout (AD023, AD032). Reading the error from
    the logger is therefore reading it where the implementation actually puts it, and a
    change that merged the two channels would fail the review cases rather than passing
    silently here.
    """
    with caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"):
        result = _review(*options)
    assert result.exit_code != 0, result.output
    messages = [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]
    assert messages, "the refusal logged no error"
    return _flat(" ".join(messages))


def test_an_unknown_run_identifier_names_it_the_path_the_stored_runs_and_the_next_action(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The commonest typo in this feature, answered with the list the command already holds."""
    _store(tmp_path)
    _store(tmp_path, DELETELESS_PLAN, run_id=OTHER_RUN_ID)
    before = _tree(_cache_root(tmp_path))

    message = _failed_review(caplog, "--from-plan", "20260101T0000-deadbeef")

    assert "'20260101T0000-deadbeef'" in message
    assert str(_cache_root(tmp_path) / "20260101T0000-deadbeef" / "plan" / "manifest.json") in message
    assert RUN_ID in message
    assert OTHER_RUN_ID in message
    assert "Next action:" in message
    assert _tree(_cache_root(tmp_path)) == before


def test_an_unknown_run_identifier_is_never_presented_as_a_zero_operation_plan(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The refusal is a refusal, not an empty rendering (AD021)."""
    _store(tmp_path)

    with caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"):
        result = _review("--from-plan", "20260101T0000-deadbeef")

    assert result.exit_code != 0
    assert "This plan contains no operations." not in result.output
    assert "operations: 0" not in result.output
    assert not (_cache_root(tmp_path) / "20260101T0000-deadbeef").exists()


def test_a_sync_whose_cache_root_does_not_exist_gets_the_no_runs_message(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """AD073's first-run arm: a stated message, not a `FileNotFoundError` traceback."""
    assert not _cache_root(tmp_path).exists()

    message = _failed_review(caplog, "--from-plan", RUN_ID)

    assert "no stored runs at all" in message
    assert f"infrahub-sync diff --name {SYNC_NAME}" in message
    assert "to produce a plan first" in message
    assert not _cache_root(tmp_path).exists()


def test_a_sync_whose_cache_root_is_empty_gets_the_same_no_runs_message(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The other half of AD073's arm: present but holding no run directories."""
    _cache_root(tmp_path).mkdir(parents=True)

    message = _failed_review(caplog, "--from-plan", RUN_ID)

    assert "no stored runs at all" in message
    assert f"infrahub-sync diff --name {SYNC_NAME}" in message


def test_the_run_identifier_enumeration_is_bounded_and_states_the_total(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """AD073's bound: nothing prunes a run directory, so an hourly pipeline would flood here."""
    stored = [
        f"20260726T{hour:02d}{minute:02d}-0000000{index % 10}"
        for index, (hour, minute) in enumerate([(h, m) for h in range(5) for m in range(6)])
    ]
    assert len(stored) > RUN_ID_LISTING_LIMIT
    for run_id in stored:
        _run_directory(tmp_path, run_id)

    message = _failed_review(caplog, "--from-plan", "20260101T0000-deadbeef")

    listed = [run_id for run_id in stored if run_id in message]
    assert len(listed) == RUN_ID_LISTING_LIMIT
    assert set(listed) == set(sorted(stored, reverse=True)[:RUN_ID_LISTING_LIMIT])
    assert f"Showing the {RUN_ID_LISTING_LIMIT} most recent of {len(stored)} stored runs" in message


def test_a_run_with_no_plan_directory_errors_with_the_re_plan_message(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """FR-019: a run with no `plan/` directory has nothing to review."""
    directory = _run_directory(tmp_path)
    (directory / "plan.parquet").write_bytes(b"pre-existing row format")

    message = _failed_review(caplog, "--from-plan", RUN_ID)

    assert f"Run '{RUN_ID}' holds no plan artifact" in message
    assert str(directory / "plan") in message
    assert "was never written or has since been removed" in message
    assert "Re-run `diff` for this sync to rebuild the plan artifact in the current format" in message


def test_a_torn_artifact_names_which_part_is_torn_and_expected_versus_found(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """FR-010: the operator learns *what* is incomplete, not only *that* something is."""
    directory = _store(tmp_path)
    operations_path(directory).unlink()

    message = _failed_review(caplog, "--from-plan", RUN_ID)

    assert f"The plan artifact of run '{RUN_ID}' is incomplete" in message
    assert "operations.jsonl is absent" in message
    assert "Expected 4 operation line(s)" in message
    assert "found no operations file" in message
    assert "Re-run `diff` for this sync to rebuild the plan artifact" in message


def test_an_unrecognized_format_version_lists_the_versions_supported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """FR-027 and SC-018: the version found, the versions supported, and the next action."""
    _store(tmp_path, format_version=UNSUPPORTED_FORMAT_VERSION)

    message = _failed_review(caplog, "--from-plan", RUN_ID)

    assert f"declares format version {UNSUPPORTED_FORMAT_VERSION}" in message
    for version in sorted(SUPPORTED_FORMAT_VERSIONS):
        assert f"Supported plan format versions: {version}" in message
    assert "rebuild the plan artifact with this version of infrahub-sync, or apply it with the version" in message


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses the permission bits this case relies on")
def test_an_unreadable_path_names_the_path_and_the_permission_remedy(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """AD036: unreadable is never degraded to absent, v1, or zero-operation."""
    directory = _store(tmp_path)
    artifact = plan_dir(directory)
    artifact.chmod(0o000)
    try:
        message = _failed_review(caplog, "--from-plan", RUN_ID)
    finally:
        artifact.chmod(stat.S_IRWXU)

    assert str(manifest_path(directory)) in message
    assert "Check permissions and ownership on the named path" in message
    assert "holds no plan artifact" not in message
    assert "This plan contains no operations" not in message


def test_a_declared_kind_the_plan_holds_no_operation_for_lists_the_kinds_it_does_hold(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """AD058's renderer arm: never empty output, and never an echo of the operator's input."""
    _store(tmp_path)

    message = _failed_review(caplog, "--from-plan", RUN_ID, "--detail", "--kind", DECLARED_BUT_UNPLANNED_KIND)

    assert f"holds no operation for destination kind '{DECLARED_BUT_UNPLANNED_KIND}'" in message
    assert "This synchronization declares that kind" in message
    assert "The plan holds operations for: BuiltinTag, LocationSite." in message
    assert "Re-run naming one of the destination kinds listed above." in message


def test_an_undeclared_kind_errors_the_same_way_from_the_reader(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """AD058's reader arm."""
    _store(tmp_path)

    message = _failed_review(caplog, "--from-plan", RUN_ID, "--detail", "--kind", UNDECLARED_KIND)

    assert f"No destination kind '{UNDECLARED_KIND}' is declared for this synchronization" in message
    assert "The plan holds operations for: BuiltinTag, LocationSite." in message
    assert "declares that kind" not in message


def test_a_kind_the_plan_holds_but_the_configuration_omits_errors_at_the_cli(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """T104's CLI arm: the case the two above cannot reach."""
    _store(tmp_path, [*MIXED_PLAN, operation_record(kind=UNDECLARED_KIND, identity={"name": "held"})])

    message = _failed_review(caplog, "--from-plan", RUN_ID, "--detail", "--kind", UNDECLARED_KIND)

    assert f"No destination kind '{UNDECLARED_KIND}' is declared for this synchronization" in message
    assert "Re-run naming one of the destination kinds listed above." in message
    assert "declares that kind" not in message, (
        "This is the undeclared arm, not the declared-but-unplanned one; the remedies differ."
    )


MISSING_PREREQUISITE_CASES: dict[str, tuple[tuple[str, ...], str]] = {
    "detail_without_from_plan": (("--detail",), "--detail requires --from-plan"),
    "kind_without_from_plan": (("--kind", "BuiltinTag"), "--kind requires --from-plan"),
    "kind_without_detail": (("--from-plan", RUN_ID, "--kind", "BuiltinTag"), "--kind requires --detail"),
}


@pytest.mark.parametrize(
    ("options", "expected"), list(MISSING_PREREQUISITE_CASES.values()), ids=list(MISSING_PREREQUISITE_CASES)
)
def test_a_missing_option_prerequisite_is_enforced_and_not_silently_ignored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, options: tuple[str, ...], expected: str
) -> None:
    """A prerequisite an option's help text states and nothing checks is the same defect class
    as an option silently ignored (AD061)."""
    _store(tmp_path)
    before = _tree(_cache_root(tmp_path))

    message = _failed_review(caplog, *options)

    assert expected in message
    assert _tree(_cache_root(tmp_path)) == before


def test_run_id_alongside_from_plan_is_ignored_with_a_warning_naming_the_selected_run(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The one ignored option this mode warns about, because it is the only other one that names a run (AD057)."""
    _store(tmp_path, MIXED_PLAN, run_id=RUN_ID)
    _store(tmp_path, DELETELESS_PLAN, run_id=OTHER_RUN_ID)

    with caplog.at_level(logging.WARNING, logger="infrahub_sync.cli"):
        result = _review("--from-plan", RUN_ID, "--run-id", OTHER_RUN_ID)

    assert result.exit_code == 0, result.output
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert OTHER_RUN_ID in warnings[0]
    assert "--run-id" in warnings[0]
    assert "--from-plan" in warnings[0]
    assert RUN_ID in warnings[0]
    # The run actually reviewed is the `--from-plan` one: four operations, not three.
    assert f"Plan {RUN_ID}" in result.output
    assert "operations: 4" in _flat(result.output)


def test_an_operation_whose_action_is_outside_the_vocabulary_refuses_the_review(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The one bound on "review renders rather than refuses" (AD055)."""
    _store(tmp_path, [operation_record(action="purge", identity={"name": "prod"})])

    message = _failed_review(caplog, "--from-plan", RUN_ID)

    assert "declares action 'purge'" in message
    assert f"Recognized actions: {', '.join(ACTIONS)}." in message
    assert "rebuild the plan artifact with this version of infrahub-sync" in message


def test_a_plan_that_would_fail_verification_is_still_rendered(tmp_path: Path) -> None:
    """AD031's other half, kept next to the refusal above so the pair cannot be collapsed."""
    directory = _store(tmp_path, [tamperable_operation()])
    tamper_with_operations(directory)

    result = _review("--from-plan", RUN_ID)

    assert result.exit_code == 0, result.output
    output = _flat(result.output)
    assert "checksum: FAILED" in output
    assert "The plan checksum does not match" in output
    assert "operations: 1" in output


# ======================================================================================
# Isolation
# ======================================================================================

# Generous by two orders of magnitude against the 60-second lock timeout: the claim is "does
# not wait for the lock", and a bound tight enough to flake on a loaded CI box would test the
# runner rather than the code.
LOCK_FREE_SECONDS = 20.0


def test_the_review_path_constructs_no_adapter(tmp_path: Path, adapter_construction_log: list[dict[str, Any]]) -> None:
    """FR-008 and SC-009: the mode branches above `get_potenda_from_instance`."""
    _store(tmp_path)

    result = _review("--from-plan", RUN_ID, "--detail")

    assert result.exit_code == 0, result.output
    assert adapter_construction_log == []


def test_the_review_path_creates_nothing_under_the_cache_root(tmp_path: Path) -> None:
    """No run directory, no `run.json`, no cached rendering — the whole subtree is unchanged."""
    _store(tmp_path)
    before = _tree(_cache_root(tmp_path))

    for options in ((), ("--detail",), ("--detail", "--kind", "BuiltinTag")):
        result = _review("--from-plan", RUN_ID, *options)
        assert result.exit_code == 0, result.output

    # The run identifier that does **not** exist is the case that matters: a mode which
    # allocated before checking would leave a directory here and, worse, render the typo as a
    # valid zero-operation plan. Reviewing only a run that already exists cannot see that,
    # because the allocation is a no-op on a directory that is already there.
    missing = _review("--from-plan", "20260101T0000-deadbeef")
    assert missing.exit_code != 0

    assert _tree(_cache_root(tmp_path)) == before
    assert not any(name.endswith("run.json") for name in before)


def test_the_review_path_is_not_blocked_by_a_held_pipeline_lock(tmp_path: Path) -> None:
    """AD021: review neither blocks nor is blocked by a running sync."""
    _store(tmp_path)
    lock_path = _cache_root(tmp_path) / ".lock"
    holder = FileLock(str(lock_path), timeout=0.5)
    holder.acquire()
    try:
        with pytest.raises(Timeout):
            FileLock(str(lock_path), timeout=0.5).acquire()
        before = _tree(_cache_root(tmp_path))
        started = time.monotonic()
        result = _review("--from-plan", RUN_ID, "--detail")
        elapsed = time.monotonic() - started
        # Captured before the release, because filelock unlinks its own lock file there.
        after = _tree(_cache_root(tmp_path))
    finally:
        holder.release()

    assert result.exit_code == 0, result.output
    assert elapsed < LOCK_FREE_SECONDS, f"review waited {elapsed:.1f}s, so it took the lock"
    assert after == before


def test_the_review_path_never_takes_the_pipeline_lock(tmp_path: Path) -> None:
    """The same claim as a call assertion, so a review that acquired an *uncontended* lock
    fails here rather than passing the timing bound above."""
    _store(tmp_path)
    taken: list[str] = []

    def _record(sync_name: str, **kwargs: Any) -> Any:  # noqa: ANN401 — mirrors the real signature
        taken.append(sync_name)
        return pipeline_lock(sync_name, **kwargs)

    with patch("infrahub_sync.execution.pipeline_lock", _record):
        result = _review("--from-plan", RUN_ID)

    assert result.exit_code == 0, result.output
    assert taken == []


# ======================================================================================
# SC-012 — review is reachable through an existing command and adds no group
# ======================================================================================

# A fixed rendering environment, so a help assertion is about the help text and not about
# the terminal the suite happens to run in.
HELP_ENVIRONMENT = {"COLUMNS": "80", "TERM": "dumb", "NO_COLOR": "1"}
WIDE_HELP_ENVIRONMENT = {"COLUMNS": "400", "TERM": "dumb", "NO_COLOR": "1"}

EXPECTED_COMMANDS = ("list", "diff", "sync", "apply", "generate")
NEW_REVIEW_OPTIONS = ("--from-plan", "--detail", "--kind")


def _help_text(*args: str, wide: bool = False) -> str:
    """Capture `--help` for `args` in a fixed rendering environment."""
    result = runner.invoke(
        app,
        [*args, "--help"],
        prog_name="infrahub-sync",
        env=WIDE_HELP_ENVIRONMENT if wide else HELP_ENVIRONMENT,
    )
    assert result.exit_code == 0, result.output
    return _strip_ansi(result.output)


def test_the_command_set_is_five_commands_with_no_group_added() -> None:
    """SC-012's bar: no command added, none removed, and no sub-command group."""
    assert app.registered_groups == []
    assert [command.name for command in app.registered_commands] == list(EXPECTED_COMMANDS)


def test_the_new_options_appear_only_under_diff() -> None:
    """Review is reachable through a command that already exists, and only there (SC-012)."""
    diff_help = _help_text("diff", wide=True)
    for option in NEW_REVIEW_OPTIONS:
        assert option in diff_help

    for command in ("list", "sync", "apply", "generate"):
        other = _help_text(command, wide=True)
        for option in NEW_REVIEW_OPTIONS:
            assert option not in other, f"{option} leaked into `{command} --help`"
    top_level = _help_text(wide=True)
    for option in NEW_REVIEW_OPTIONS:
        assert option not in top_level


# ======================================================================================
# The review options' own help rows
# ======================================================================================


def test_the_run_id_help_carries_the_cross_reference_to_from_plan() -> None:
    """AD057: `--run-id` alone cannot tell an operator that reviewing a plan is another option."""
    line = _option_help_line("--run-id")

    assert "--from-plan <run-id>" in line
    assert "To review a saved plan instead" in line


def _option_help_line(option: str) -> str:
    """The `diff --help` row for `option`, flattened.

    Row-scoped rather than whole-output: "`--from-plan` carries a metavar" is a claim about
    its own row, and a whole-output search would be satisfied by any other option's metavar.
    A Rich table row can span multiple terminal lines, so collect its continuation lines
    rather than treating every physical line containing an option name as a new row.
    """
    rows = _help_text("diff").splitlines()
    option_prefix = f"│ {option}"
    starts = [index for index, row in enumerate(rows) if row.startswith(option_prefix)]
    assert len(starts) == 1, f"expected exactly one help row for {option}, got {len(starts)}"

    logical_row: list[str] = []
    for row in rows[starts[0] :]:
        if logical_row and (not row.startswith("│") or row.startswith("│ --")):
            break
        logical_row.append(row.strip("│ "))
    return _flat(" ".join(logical_row))


def test_from_plan_is_documented_as_taking_a_run_identifier_and_not_as_a_flag() -> None:
    """AD057: the option's value *is* the run identifier, so the help must show a value."""
    from_plan_row = _option_help_line("--from-plan")
    detail_row = _option_help_line("--detail")

    assert "TEXT" in from_plan_row
    # Paired with a real flag, so "every row says TEXT" cannot satisfy the assertion above.
    assert "TEXT" not in detail_row
    assert "for this run id" in from_plan_row


# ======================================================================================
# The next-action obligation across the taxonomy
# ======================================================================================

# One entry per declared taxonomy member, built the way its own raising site builds it. The
# two AD071 additions and both AD082 arms are here because the point of naming them was to
# bring them inside this sweep.
TAXONOMY_CASES: dict[str, Callable[[], PlanArtifactError]] = {
    "plan_format_v1": lambda: PlanFormatV1Error("Run 'r' holds no plan artifact."),
    "torn_artifact": lambda: PlanArtifactTornError("The plan artifact of run 'r' is incomplete."),
    "format_version": lambda: PlanFormatVersionError("Run 'r' declares format version 99."),
    "unreadable": lambda: PlanArtifactUnreadableError("The run directory at '/x' could not be read."),
    "unknown_run_identifier": lambda: UnknownRunIdentifierError("No run 'r' is stored."),
    "unknown_run_identifier_no_runs": lambda: UnknownRunIdentifierError.no_runs(
        "No run 'r' is stored.", sync_name=SYNC_NAME
    ),
    "unknown_kind": lambda: UnknownPlanKindError("No destination kind 'K' is declared."),
    "unformable_destination_identity": lambda: UnformableDestinationIdentityError(
        "No destination identity can be formed for kind 'K'."
    ),
    "unwalked_diff_children": lambda: UnwalkedDiffChildrenError("Element 'e' of kind 'K' carries 2 children."),
    "source_peer_absent": lambda: SourcePeerUnresolvedError.absent("Peer 'p' is absent from the source store."),
    "source_peer_ambiguous": lambda: SourcePeerUnresolvedError.ambiguous("Peer 'p' resolved in two kinds."),
    "unsupported_action": lambda: UnsupportedOperationActionError("Operation 'o' declares action 'purge'."),
    "duplicate_operation_id": lambda: DuplicateOperationIdError("Two operations share identifier 'o'."),
    "unserializable_payload": lambda: UnserializablePayloadValueError("Field 'f' of kind 'K' holds a set."),
    "peer_not_found": lambda: PeerNotFoundError("Peer 'p' of kind 'K' matches no destination object."),
    "peer_ambiguous": lambda: PeerAmbiguousError("Peer 'p' of kind 'K' matches 2 destination objects."),
    "unaccounted_identity_component": lambda: UnaccountedIdentityComponentError("Kind 'K' omits component 'c'."),
    "convergence_identity": lambda: ConvergenceIdentityError("Kind 'K' has a finer writable identity."),
    "unkeyed_write_refused": lambda: UnkeyedWriteRefusedError("The rendered mutation for 'K' carries no key."),
    "plan_verification": lambda: PlanVerificationError("The plan artifact of run 'r' cannot be applied."),
    "operation_apply_failed": lambda: OperationApplyFailedError(
        "Applying operation 'o' failed.", apply_record=ApplyRecord()
    ),
    "apply_record_invariant": lambda: ApplyRecordInvariantError(
        "The apply record does not account for the plan.", apply_record=ApplyRecord()
    ),
    "plan_generation_exists": lambda: PlanGenerationExistsError("Run 'r' already holds a committed plan."),
    "unsafe_run_identifier": lambda: UnsafeRunIdentifierError("Run identifier '../evil' is not usable."),
}


@pytest.mark.parametrize("build", list(TAXONOMY_CASES.values()), ids=list(TAXONOMY_CASES))
def test_every_taxonomy_entry_names_a_next_action(build: Callable[[], PlanArtifactError]) -> None:
    """AD059 over the declared taxonomy, message included."""
    error = build()

    assert error.next_action.strip()
    assert error.next_action in str(error)
    assert str(error).startswith(error.message)


def _plan_artifact_subclasses(root: type[PlanArtifactError] = PlanArtifactError) -> set[type[PlanArtifactError]]:
    """Every `PlanArtifactError` subclass, transitively."""
    found: set[type[PlanArtifactError]] = set()
    for subclass in root.__subclasses__():
        found.add(subclass)
        found |= _plan_artifact_subclasses(subclass)
    return found


def test_every_taxonomy_subclass_declares_a_non_empty_next_action() -> None:
    """The structural half: a class added later trips here rather than shipping a dead end."""
    subclasses = _plan_artifact_subclasses()

    assert subclasses, "the taxonomy walk found no subclasses, so this test would pass vacuously"
    for subclass in subclasses:
        assert subclass.next_action.strip(), f"{subclass.__name__} declares no next_action (AD059)"


def test_the_parametrization_covers_every_declared_subclass() -> None:
    """The sweep is only as good as its coverage, so the coverage is itself asserted."""
    covered = {type(build()) for build in TAXONOMY_CASES.values()}

    assert covered == _plan_artifact_subclasses()


def test_a_subclass_without_a_next_action_cannot_be_constructed() -> None:
    """The guarantee is enforced where it can be observed, not left to review (AD059)."""

    class NextActionlessError(PlanArtifactError):
        """A deliberately incomplete taxonomy member, defined only to be refused."""

    with pytest.raises(TypeError, match="declares no next_action"):
        NextActionlessError("something went wrong.")


def test_the_two_source_peer_conditions_carry_textually_distinct_next_actions() -> None:
    """AD082: one class, two conditions, two remedies."""
    absent = SourcePeerUnresolvedError.absent("Peer 'p' is absent from the source store.")
    ambiguous = SourcePeerUnresolvedError.ambiguous("Peer 'p' resolved in two candidate kinds.")

    assert absent.next_action != ambiguous.next_action
    assert "Add the peer's kind to the configuration" in absent.next_action
    assert "Disambiguate the field's `reference`" in ambiguous.next_action
    assert absent.next_action not in str(ambiguous)


def test_the_source_peer_remedies_are_distinct_from_the_destination_peer_remedy() -> None:
    """AD071: `PeerNotFoundError`'s remedy is a **destination**-side one and fixes nothing at
    plan time, which is why the source-side condition stopped borrowing it."""
    destination_side = PeerNotFoundError("Peer 'p' matches no destination object.").next_action

    assert destination_side != SourcePeerUnresolvedError.ABSENT_NEXT_ACTION
    assert destination_side != SourcePeerUnresolvedError.AMBIGUOUS_NEXT_ACTION


# A run *path* pasted where a run *id* goes, and an absolute one — the two shapes the cache
# layout's traversal guard rejects, which used to escape as a raw `ValueError` traceback.
TRAVERSAL_RUN_IDS = ("../evil", "/tmp/evil")  # noqa: S108 — a literal in a refusal case, nothing is written


@pytest.mark.parametrize("run_id", TRAVERSAL_RUN_IDS, ids=("relative_traversal", "absolute_path"))
def test_a_traversal_shaped_run_id_is_refused_as_one_line_on_the_review_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, run_id: str
) -> None:
    """The guard's `ValueError` reaches the operator as a designed refusal."""
    _store(tmp_path)

    with caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"):
        result = _review("--from-plan", run_id)

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"the refusal escaped as a raw {type(result.exception).__name__} traceback"
    )
    message = _operator_errors(caplog)
    assert repr(run_id) in message
    assert "is not usable" in message
    assert UnsafeRunIdentifierError.next_action in message


@pytest.mark.parametrize("run_id", TRAVERSAL_RUN_IDS, ids=("relative_traversal", "absolute_path"))
def test_a_traversal_shaped_run_id_is_refused_as_one_line_on_the_apply_path(
    tmp_path: Path, destination_double: RecordingDestination, caplog: pytest.LogCaptureFixture, run_id: str
) -> None:
    """The same verdict from the other command, which is why the translation is at one site."""
    _appliable_run(tmp_path)
    constructed: list[str] = []

    with (
        caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"),
        patch(
            "infrahub_sync.cli.PlanApplier.open_existing",
            _patched_open_existing(destination_double, constructed=constructed),
        ),
    ):
        result = _apply(run_id)

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"the refusal escaped as a raw {type(result.exception).__name__} traceback"
    )
    assert constructed == []
    assert destination_double.writes == []
    message = _operator_errors(caplog)
    assert "is not usable" in message
    assert UnsafeRunIdentifierError.next_action in message


@pytest.mark.parametrize("run_id", TRAVERSAL_RUN_IDS, ids=("relative_traversal", "absolute_path"))
def test_a_traversal_shaped_run_id_is_refused_before_the_live_diff_builds_anything(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, adapter_construction_log: list[dict[str, Any]], run_id: str
) -> None:
    """The third command that takes a run id gives the same verdict, and gives it as early.

    `diff --run-id` used to import and construct both adapters before the layout guard's
    `ValueError` surfaced as a mislabelled initialization failure — the one command of the three
    where a pasted run path was answered with the wrong diagnosis.
    """
    _ = tmp_path

    with caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"):
        result = _diff_into(run_id)

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"the refusal escaped as a raw {type(result.exception).__name__} traceback"
    )
    assert adapter_construction_log == [], "the refusal must precede adapter construction"
    message = _operator_errors(caplog)
    assert repr(run_id) in message
    assert "is not usable" in message
    assert UnsafeRunIdentifierError.next_action in message
    assert "Failed to initialize the Sync Instance" not in message


def test_the_unknown_kind_message_lists_the_kinds_the_plan_holds(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Enumeration 1. Fails if the message degrades to echoing the operator's input back."""
    _store(tmp_path)

    message = _failed_review(caplog, "--from-plan", RUN_ID, "--detail", "--kind", UNDECLARED_KIND)

    assert "BuiltinTag" in message
    assert "LocationSite" in message


def test_the_unknown_run_message_lists_the_run_identifiers_that_exist(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Enumeration 2, in its populated arm — the bounded and no-runs arms are asserted above."""
    _store(tmp_path)
    _store(tmp_path, DELETELESS_PLAN, run_id=OTHER_RUN_ID)

    message = _failed_review(caplog, "--from-plan", "20260101T0000-deadbeef")

    assert RUN_ID in message
    assert OTHER_RUN_ID in message
    assert "The most recent run identifiers for this sync are" in message


def test_the_version_refusal_lists_the_supported_format_versions(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Enumeration 3."""
    _store(tmp_path, format_version=UNSUPPORTED_FORMAT_VERSION)

    message = _failed_review(caplog, "--from-plan", RUN_ID)

    expected = ", ".join(str(version) for version in sorted(SUPPORTED_FORMAT_VERSIONS))
    assert f"Supported plan format versions: {expected}." in message


# ======================================================================================
# The apply path
# ======================================================================================

APPLY_SNAPSHOT_ROWS: list[dict[str, Any]] = [
    {"name": "prod", "description": "production"},
    {"name": "staging", "description": "staging"},
    {"name": "retired", "description": None},
]

_EXTRACT_TS = datetime(2026, 7, 26, 18, 4, 11, tzinfo=timezone.utc)

APPLY_PLAN: tuple[dict[str, Any], ...] = (
    tamperable_operation(),
    operation_record(action="update", kind="LocationSite", identity={"name": "dc1"}, tier=1),
)


class RecordingDestination:
    """A destination that implements the planned-write surface and counts every write.

    A plain recording object rather than a `MagicMock`, deliberately: a mock answers every
    attribute lookup, so the "zero destination writes" claim would be unfalsifiable against
    one and the missing-write-surface case could not be expressed at all — a mock satisfies
    the write-surface protocol's presence check for free.

    Both protocol members are defined, since the pre-write gate is an `isinstance` check
    against the protocol and a destination missing either one is refused (AD086).
    """

    # `None` — no captured binding — skips the destination comparison, so every case
    # not about that check behaves exactly as before the field existed; the binding cases
    # assign a record here.
    destination_binding: DestinationBindingRecord | None = None

    def __init__(self) -> None:
        self.writes: list[str] = []

    def new_peer_resolver(self) -> object:  # noqa: PLR6301
        """The per-apply resolver factory; nothing below this double's surface reads it."""
        return object()

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        _ = peers
        self.writes.append(operation.operation_id)
        return f"node-{len(self.writes)}"


class RejectingDestination(RecordingDestination):
    """A destination that accepts the first operation and rejects the next (AD027).

    The rejection is the SDK's own `GraphQLError` — what a server refusing a mutation
    actually raises — because the engine's operational boundary is defined by the destination
    library's error base. A stand-in `RuntimeError` would be a defect, and defects escape
    unwrapped by design.
    """

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        if self.writes:
            raise GraphQLError([{"message": f"the destination rejected {operation.operation_id!r}"}])
        return super().apply_planned_operation(operation=operation, peers=peers)


class DefectiveDestination(RecordingDestination):
    """A destination whose second operation trips a **code defect** after the first succeeded.

    `AssertionError` deliberately: it is outside the plan taxonomy and outside the SDK's error
    hierarchy, so nothing about it looks like a destination refusing a write. The in-tree
    example it stands for is the adapter's own schema-type guard, which raises `TypeError` when
    `client.schema.get` returns something other than a `NodeSchemaAPI`.
    """

    def __init__(self, message: str = "a code defect, not a destination refusal") -> None:
        super().__init__()
        self.message = message

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        if self.writes:
            raise AssertionError(self.message)
        return super().apply_planned_operation(operation=operation, peers=peers)


class ValueErrorDestination(RecordingDestination):
    """A destination whose second write exposes an unexpected SDK-shape defect."""

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        if self.writes:
            msg = "SDK shape defect mid-apply, after one write landed"
            raise ValueError(msg)
        return super().apply_planned_operation(operation=operation, peers=peers)


def _write_apply_snapshot(run_directory: Path, rows: list[dict[str, Any]]) -> None:
    """Write the source side's snapshot through the engine's own writer.

    The engine's writer rather than a hand-built table, because check 4 digests the table's
    **logical rows** with `_extract_ts` dropped (AD037): a digest over a hand-built table
    missing the injected columns would not be the digest the verifier computes.
    """
    write_resource_side(
        run_dir=run_directory,
        side="A",
        resource="BuiltinTag",
        rows=list(rows),
        source_ids=[str(row["name"]) for row in rows],
        extract_ts=_EXTRACT_TS,
        tombstones=None,
    )


def _apply_snapshot_path(run_directory: Path) -> Path:
    return run_directory / "A" / "BuiltinTag.parquet"


def _appliable_run(
    tmp_path: Path,
    records: Sequence[Mapping[str, Any]] = APPLY_PLAN,
    *,
    run_id: str = RUN_ID,
    manifest_run_id: str | None = None,
    config_version: str | None = None,
    **manifest_overrides: Any,  # noqa: ANN401 — a manifest field's value is any JSON value
) -> Path:
    """A run directory whose artifact applies clean, as every case's starting point."""
    directory = _run_directory(tmp_path, run_id)
    _write_apply_snapshot(directory, APPLY_SNAPSHOT_ROWS)
    write_artifact(
        directory,
        list(records),
        run_id=run_id if manifest_run_id is None else manifest_run_id,
        config_version=_config_version() if config_version is None else config_version,
        source_snapshot=source_snapshot_records(directory),
        **manifest_overrides,
    )
    return directory


@pytest.fixture
def destination_double() -> RecordingDestination:
    return RecordingDestination()


def _patched_open_existing(destination: RecordingDestination, *, constructed: list[str] | None = None) -> Any:  # noqa: ANN401
    """A `PlanApplier.open_existing` replacement assembling a real engine over `destination`.

    A **real** `Potenda` inside a real `PlanApplier`, so the pre-apply gate, the reader and
    the apply loop under test are the shipped ones; only the destination adapter is
    replaced, because no live destination is reachable here and a mocked engine would
    assert nothing about either.
    """

    def _open_existing(sync_instance: Any, *, run_id: str, **kwargs: Any) -> PlanApplier:  # noqa: ANN401
        _ = kwargs
        if constructed is not None:
            constructed.append(run_id)
        from infrahub_sync.cache.paths import run_dir as run_dir_for

        directory = run_dir_for(sync_instance.name, run_id)
        engine = Potenda(
            source=SimpleNamespace(top_level=[]),  # ty: ignore[invalid-argument-type]
            destination=destination,  # ty: ignore[invalid-argument-type]
            config=sync_instance,
            top_level=["BuiltinTag", "LocationSite"],
            run_dir=directory,
            run_id=run_id,
        )
        return PlanApplier(engine, run_dir=directory, run_id=run_id)

    return _open_existing


def _run_apply(destination: RecordingDestination, run_id: str = RUN_ID, *, quiet: bool = False) -> Any:  # noqa: ANN401
    """Invoke the CLI's `apply` with a real engine over `destination`."""
    with patch("infrahub_sync.cli.PlanApplier.open_existing", _patched_open_existing(destination)):
        return _apply(run_id, quiet=quiet)


def _operator_errors(caplog: pytest.LogCaptureFixture) -> str:
    """The flattened ERROR lines the command reported, as the operator reads them.

    `apply` reports a designed refusal through `print_error_and_abort`, which logs at ERROR
    and aborts, so the message lives in the log rather than on the exception. Reading it from
    `str(result.exception)` instead would pass just as well against a command that let the
    taxonomy error escape as a stack trace — which is exactly the presentation these cases
    assert against (AD059).
    """
    return _flat(" ".join(record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR))


def _run_json(tmp_path: Path, run_id: str = RUN_ID) -> dict[str, Any]:
    """Read the run sidecar back **from disk** after the command returned.

    From disk rather than from an in-memory object: `RunFile.save()` writes the whole payload
    from its own instance with no merge (`infrahub_sync/cache/sidecars.py`), so the
    file is the only place the CLI's merge can be observed to have happened (AD069).
    """
    path = _cache_root(tmp_path) / run_id / "run.json"
    assert path.is_file(), f"no run sidecar at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


# --- SC-004's six, individually, plus SC-011, SC-015, SC-018 and AD055's tenth -----------


def _case_checksum_mismatch(tmp_path: Path) -> tuple[str, ...]:
    """SC-004: the artifact's contents changed after it was written."""
    _appliable_run(tmp_path)
    tamper_with_operations(_cache_root(tmp_path) / RUN_ID)
    return ("plan_checksum", "what would be applied is not what was reviewed")


def _case_config_version_mismatch(tmp_path: Path) -> tuple[str, ...]:
    """SC-004: the configuration changed after the plan was saved."""
    _appliable_run(tmp_path, config_version="a-different-configuration-version")
    return ("config_version", "The configuration changed after the plan was saved")


def _case_snapshot_binding_mismatch(tmp_path: Path) -> tuple[str, ...]:
    """SC-004: the source snapshot changed in place — same row count, different values."""
    directory = _appliable_run(tmp_path)
    _write_apply_snapshot(directory, [dict(row, description="edited") for row in APPLY_SNAPSHOT_ROWS])
    return ("source_snapshot", "The source snapshot changed after the plan was computed")


def _case_absent_operations(tmp_path: Path) -> tuple[str, ...]:
    """SC-004: the operations file is gone, so no checksum can be computed over it (FR-010).

    Reported by the **verifier**, which is what runs before the reader on this path (T097):
    the tear is named as `torn_operations`, and the message still names the file, the recorded
    line count and the next action, which is what FR-010 requires of it.
    """
    directory = _appliable_run(tmp_path)
    operations_path(directory).unlink()
    return (
        "torn_operations",
        "operations.jsonl",
        "no operations file",
        "Re-run `diff` for this sync to rebuild the plan artifact",
    )


def _case_truncated_snapshot(tmp_path: Path) -> tuple[str, ...]:
    """SC-004: the snapshot lost rows, so its digest and its row count both disagree."""
    directory = _appliable_run(tmp_path)
    _write_apply_snapshot(directory, APPLY_SNAPSHOT_ROWS[:1])
    return ("source_snapshot", "no longer describes it")


def _case_absent_snapshot(tmp_path: Path) -> tuple[str, ...]:
    """SC-004: the snapshot the plan was computed against is gone (User Story 2 scenario 1)."""
    directory = _appliable_run(tmp_path)
    _apply_snapshot_path(directory).unlink()
    return ("source_snapshot", "The source snapshot the plan was computed against is gone")


def _case_run_binding_mismatch(tmp_path: Path) -> tuple[str, ...]:
    """SC-015: a `plan/` directory copied from another run verifies clean on checksum alone."""
    _appliable_run(tmp_path, manifest_run_id=OTHER_RUN_ID)
    return ("run_binding", "written by a different run")


def _case_unsupported_format_version(tmp_path: Path) -> tuple[str, ...]:
    """SC-018: a manifest revision this release cannot interpret.

    Reported by the verifier's **gate**, which runs before the reader on this path (T097), so
    the operator also learns that the remaining four checks were not evaluated and why — the
    disclosure FR-009 requires and which the reader's own refusal could not carry.
    """
    _appliable_run(tmp_path, format_version=UNSUPPORTED_FORMAT_VERSION)
    return (
        "format_version",
        f"found {UNSUPPORTED_FORMAT_VERSION}",
        "one of the supported plan format versions: 2",
        "were not evaluated",
    )


def _case_unrecognized_action(tmp_path: Path) -> tuple[str, ...]:
    """AD055's tenth case: an action outside the closed vocabulary, refused while reading."""
    _appliable_run(tmp_path, [operation_record(action="purge", identity={"name": "prod"})])
    return ("declares action 'purge'", f"Recognized actions: {', '.join(ACTIONS)}.")


APPLY_REFUSAL_CASES: dict[str, Callable[[Path], tuple[str, ...]]] = {
    "checksum_mismatch": _case_checksum_mismatch,
    "config_version_mismatch": _case_config_version_mismatch,
    "snapshot_binding_mismatch": _case_snapshot_binding_mismatch,
    "absent_operations": _case_absent_operations,
    "truncated_snapshot": _case_truncated_snapshot,
    "absent_snapshot": _case_absent_snapshot,
    "run_binding_mismatch": _case_run_binding_mismatch,
    "unsupported_format_version": _case_unsupported_format_version,
    "unrecognized_action": _case_unrecognized_action,
}


@pytest.mark.parametrize("mutate", list(APPLY_REFUSAL_CASES.values()), ids=list(APPLY_REFUSAL_CASES))
def test_an_apply_refusal_writes_nothing_and_records_failed(
    tmp_path: Path,
    destination_double: RecordingDestination,
    mutate: Callable[[Path], tuple[str, ...]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every refusal: named cause, its next action, zero writes, and `failed` on disk."""
    expected_fragments = mutate(tmp_path)

    with caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"):
        result = _run_apply(destination_double)

    assert result.exit_code != 0
    assert destination_double.writes == []
    assert not isinstance(result.exception, PlanArtifactError), (
        f"the refusal escaped the command as a raw {type(result.exception).__name__} traceback"
    )
    message = _operator_errors(caplog)
    for fragment in expected_fragments:
        assert fragment in message, f"{fragment!r} missing from the refusal: {message}"
    assert "Next action" in message or "next action" in message
    recorded = _run_json(tmp_path)
    assert recorded["status"] == "failed"
    assert recorded["summary"]["applied_operations"] == []
    assert recorded["summary"]["skipped_delete_operations"] == []
    assert recorded["summary"]["skipped_delete_count"] == 0


RECORDED_BINDING = {"url": "http://recorded.example:8000", "branch": "main"}
LIVE_BINDING = DestinationBindingRecord(url="http://live.example:8000", branch="main")


def test_an_apply_to_a_drifted_destination_refuses_with_the_binding_message(
    tmp_path: Path, destination_double: RecordingDestination, caplog: pytest.LogCaptureFixture
) -> None:
    """The plan is bound to its effective destination, and a mismatch refuses."""
    _appliable_run(tmp_path, destination_binding=RECORDED_BINDING)
    destination_double.destination_binding = LIVE_BINDING

    with caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"):
        result = _run_apply(destination_double)

    assert result.exit_code != 0
    assert destination_double.writes == []
    message = _operator_errors(caplog)
    assert "bound to a different destination" in message
    assert "http://recorded.example:8000" in message
    assert "http://live.example:8000" in message
    assert "--allow-destination-change" in message
    recorded = _run_json(tmp_path)
    assert recorded["status"] == "failed"
    assert recorded["summary"]["applied_operations"] == []


def test_allow_destination_change_applies_across_the_mismatch(
    tmp_path: Path, destination_double: RecordingDestination
) -> None:
    """The binding check's override: an explicit flag for the deliberate cross-environment apply."""
    _appliable_run(tmp_path, destination_binding=RECORDED_BINDING)
    destination_double.destination_binding = LIVE_BINDING

    with patch("infrahub_sync.cli.PlanApplier.open_existing", _patched_open_existing(destination_double)):
        result = runner.invoke(
            app,
            [
                "apply",
                "--name",
                SYNC_NAME,
                "--directory",
                str(EXAMPLES_DIR),
                "--run-id",
                RUN_ID,
                "--allow-destination-change",
            ],
        )

    assert result.exit_code == 0, result.output
    assert len(destination_double.writes) == len(APPLY_PLAN)
    assert _run_json(tmp_path)["status"] == "applied"


def test_a_plan_without_a_recorded_binding_applies_to_any_destination(
    tmp_path: Path, destination_double: RecordingDestination
) -> None:
    """The absent-field skip: plans older than the field are not refused."""
    _appliable_run(tmp_path)
    destination_double.destination_binding = LIVE_BINDING

    result = _run_apply(destination_double)

    assert result.exit_code == 0, result.output
    assert len(destination_double.writes) == len(APPLY_PLAN)
    assert _run_json(tmp_path)["status"] == "applied"


def test_a_v1_plan_is_refused_before_anything_is_constructed(
    tmp_path: Path, destination_double: RecordingDestination
) -> None:
    """SC-011, under AD026's rule that this refusal creates nothing."""
    directory = _run_directory(tmp_path)
    (directory / "plan.parquet").write_bytes(b"pre-existing row format")
    before = _tree(_cache_root(tmp_path))
    constructed: list[str] = []

    with patch(
        "infrahub_sync.cli.PlanApplier.open_existing",
        _patched_open_existing(destination_double, constructed=constructed),
    ):
        result = _apply(RUN_ID)

    assert result.exit_code != 0
    assert destination_double.writes == []
    assert constructed == []
    assert _tree(_cache_root(tmp_path)) == before
    assert not (directory / "run.json").exists()


def test_the_v1_message_differs_from_the_unrecognized_version_message(
    tmp_path: Path, destination_double: RecordingDestination, caplog: pytest.LogCaptureFixture
) -> None:
    """SC-018's comparison half: the two remedies differ, so the two messages must too."""
    directory = _run_directory(tmp_path)
    (directory / "plan.parquet").write_bytes(b"pre-existing row format")
    with caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"):
        v1_result = _run_apply(destination_double)
        assert v1_result.exit_code != 0
        v1_text = _operator_errors(caplog)

        # Both messages are read from the same operator-facing channel, so the comparison is
        # between what an operator actually reads in each case (AD059).
        caplog.clear()
        _appliable_run(tmp_path, run_id=OTHER_RUN_ID, format_version=UNSUPPORTED_FORMAT_VERSION)
        versioned = _run_apply(RecordingDestination(), OTHER_RUN_ID)
        versioned_text = _operator_errors(caplog)
    assert versioned.exit_code != 0

    assert v1_text != versioned_text
    assert "holds no plan artifact" in v1_text
    assert "format_version" in versioned_text
    assert f"found {UNSUPPORTED_FORMAT_VERSION}" in versioned_text
    assert "holds no plan artifact" not in versioned_text
    assert "supported plan format versions" not in v1_text


def test_a_missing_run_refuses_naming_the_runs_that_exist_and_creates_no_directory(
    tmp_path: Path, destination_double: RecordingDestination, caplog: pytest.LogCaptureFixture
) -> None:
    """AD026, AD059 and AD073 on the apply path, guarded exactly as review's are."""
    _appliable_run(tmp_path)
    before = _tree(_cache_root(tmp_path))
    constructed: list[str] = []

    with (
        caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"),
        patch(
            "infrahub_sync.cli.PlanApplier.open_existing",
            _patched_open_existing(destination_double, constructed=constructed),
        ),
    ):
        result = _apply("20260101T0000-deadbeef")

    assert result.exit_code != 0
    assert constructed == []
    assert destination_double.writes == []
    message = _flat(" ".join(record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR))
    assert "'20260101T0000-deadbeef'" in message
    assert RUN_ID in message
    assert "Next action:" in message
    assert message.count("Next action:") == 1
    assert _tree(_cache_root(tmp_path)) == before


# --- the apply assembly seam: destination only, stored sidecars immutable ---------------


def _side_sensitive_import(destination: RecordingDestination, *, allow_source: bool = False) -> Any:  # noqa: ANN401
    """An `import_adapter` replacement that knows which side it is being asked for.

    The destination side yields a factory producing `destination`. The source side raises —
    apply must never ask for it — unless `allow_source`, which yields an inert stand-in so
    a case about something else entirely does not fail on the import instead.
    """

    def _import_adapter(*, sync_instance: Any, adapter: Any) -> Any:  # noqa: ANN401
        if adapter is sync_instance.source:
            if allow_source:
                return lambda **kwargs: SimpleNamespace(top_level=[])  # noqa: ARG005
            msg = "apply must not import or construct the source adapter"
            raise AssertionError(msg)
        return lambda **kwargs: destination  # noqa: ARG005

    return _import_adapter


def test_apply_assembles_no_source_adapter_and_still_applies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, destination_double: RecordingDestination
) -> None:
    """The real assembly seam constructs the destination only."""
    _appliable_run(tmp_path)
    monkeypatch.setattr("infrahub_sync.utils.import_adapter", _side_sensitive_import(destination_double))

    result = _apply(RUN_ID)

    assert result.exit_code == 0, result.output
    assert destination_double.writes == [str(record["operation_id"]) for record in APPLY_PLAN]
    recorded = _run_json(tmp_path)
    assert recorded["status"] == "applied"


class SchemaBearingDestination(RecordingDestination):
    """A recording destination that also exposes a live schema, as a real adapter does.

    The live schema is exactly what an assembly that recomputes extraction sidecars would
    hash — so seeding the stored `schema-sub-hash.txt` with a value the live schema does
    not hash to makes any rewrite observable as a byte change.
    """

    def __init__(self) -> None:
        super().__init__()
        self.schema = {"BuiltinTag": {}, "LocationSite": {}}


def test_apply_leaves_the_stored_schema_sub_hash_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stored run's sidecars are immutable extraction provenance."""
    seeded = b"OLD\n"
    destination = SchemaBearingDestination()
    monkeypatch.setattr("infrahub_sync.utils.import_adapter", _side_sensitive_import(destination, allow_source=True))

    # The refusal outcome: a plan bound to a configuration version that no longer matches.
    refused_run = _appliable_run(tmp_path, config_version="a-different-configuration-version")
    (refused_run / "schema-sub-hash.txt").write_bytes(seeded)
    refused = _apply(RUN_ID)
    assert refused.exit_code != 0
    assert destination.writes == []
    assert (refused_run / "schema-sub-hash.txt").read_bytes() == seeded

    # The successful outcome, on a second stored run.
    applied_run = _appliable_run(tmp_path, run_id=OTHER_RUN_ID)
    (applied_run / "schema-sub-hash.txt").write_bytes(seeded)
    applied = _apply(OTHER_RUN_ID)
    assert applied.exit_code == 0, applied.output
    assert (applied_run / "schema-sub-hash.txt").read_bytes() == seeded


# --- the positive case, where AD069's merge is asserted by name -------------------------


def test_a_delete_bearing_plan_applies_exits_zero_and_records_the_skipped_deletes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """SC-007's **local half** and AD055 on the CLI path — and AD069's merge, from `run.json`."""
    delete = operation_record(action="delete", identity={"name": "retired"})
    records = [*APPLY_PLAN, delete]
    _appliable_run(tmp_path, records)
    destination = RecordingDestination()

    with caplog.at_level(logging.DEBUG, logger="infrahub_sync"):
        result = _run_apply(destination)

    assert result.exit_code == 0, result.output
    applied_ids = [str(record["operation_id"]) for record in APPLY_PLAN]
    assert destination.writes == applied_ids
    assert str(delete["operation_id"]) not in destination.writes

    recorded = _run_json(tmp_path)
    assert recorded["status"] == "applied"
    assert recorded["summary"]["applied_operations"] == applied_ids
    assert recorded["summary"]["skipped_delete_operations"] == [str(delete["operation_id"])]
    assert recorded["summary"]["skipped_delete_count"] == 1
    # DBR-016's knowability invariant, read from the file rather than inferred.
    assert set(recorded["summary"]["applied_operations"]) | set(recorded["summary"]["skipped_delete_operations"]) == {
        str(record["operation_id"]) for record in records
    }

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 2, [entry.getMessage() for entry in warnings]
    engine_warning = next(entry for entry in warnings if entry.name == "infrahub_sync.potenda")
    completion = next(entry for entry in warnings if entry.name == "infrahub_sync.cli")
    assert "1" in engine_warning.getMessage()
    # `--quiet` floors the package logger at WARNING, so an INFO emission would vanish for
    # exactly the scripted runs where this warning is the only signal.
    assert engine_warning.levelno == logging.WARNING
    # SC-007's completion-line clause: the command's own last line names the skipped count,
    # at the same level as the count it reports (AD089).
    assert "1 deletes skipped" in completion.getMessage()
    assert completion.levelno == logging.WARNING


def test_the_completion_line_naming_the_skipped_count_survives_quiet(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """T098 / SC-007: the command's own completion line is required evidence, so it must arrive."""
    delete = operation_record(action="delete", identity={"name": "retired"})
    _appliable_run(tmp_path, [*APPLY_PLAN, delete])
    destination = RecordingDestination()

    with caplog.at_level(logging.DEBUG, logger="infrahub_sync"):
        result = _run_apply(destination, quiet=True)

    assert result.exit_code == 0, result.output
    completion = [entry for entry in caplog.records if entry.name == "infrahub_sync.cli"]
    assert len(completion) == 1, [entry.getMessage() for entry in completion]
    message = completion[0].getMessage()
    assert f"{len(APPLY_PLAN)} operations applied" in message
    assert "1 deletes skipped" in message
    assert completion[0].levelno >= logging.WARNING


def test_a_quiet_apply_with_nothing_to_disclose_emits_no_completion_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The negative control for the case above (AD089)."""
    _appliable_run(tmp_path)
    destination = RecordingDestination()

    with caplog.at_level(logging.DEBUG, logger="infrahub_sync"):
        result = _run_apply(destination, quiet=True)

    assert result.exit_code == 0, result.output
    assert [entry.getMessage() for entry in caplog.records if entry.name == "infrahub_sync.cli"] == []


def test_a_clean_apply_records_the_applied_operations_it_actually_performed(tmp_path: Path) -> None:
    """The merge again, without a delete in the plan, so the two halves are separable."""
    _appliable_run(tmp_path)
    destination = RecordingDestination()

    result = _run_apply(destination)

    assert result.exit_code == 0, result.output
    recorded = _run_json(tmp_path)
    assert recorded["status"] == "applied"
    assert recorded["summary"]["applied_operations"] == [str(record["operation_id"]) for record in APPLY_PLAN]
    assert recorded["summary"]["skipped_delete_count"] == 0
    assert recorded["summary"]["skipped_delete_operations"] == []


def test_a_rejection_mid_plan_records_the_partial_applied_set(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """FR-025's last-applied pointer survives a partial apply (AD027, AD069)."""
    _appliable_run(tmp_path)
    destination = RejectingDestination()

    with caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"):
        result = _run_apply(destination)

    assert result.exit_code != 0
    assert not isinstance(result.exception, OperationApplyFailedError), (
        "the rejection escaped the command as a raw traceback"
    )
    reported = _operator_errors(caplog)
    assert "stay written" in reported
    assert "Next action:" in reported
    first_id = str(APPLY_PLAN[0]["operation_id"])
    assert destination.writes == [first_id]

    recorded = _run_json(tmp_path)
    assert recorded["status"] == "failed"
    assert recorded["summary"]["applied_operations"] == [first_id]
    # FR-025's pointer is the final element, not a separate field.
    assert recorded["summary"]["applied_operations"][-1] == first_id
    assert recorded["summary"]["skipped_delete_count"] == 0
    # The operation that failed is in neither recorded set, and its own write may
    # have landed in part — so the run names it rather than leaving the write uncounted.
    assert recorded["summary"]["failed_operation"] == str(APPLY_PLAN[1]["operation_id"])
    assert recorded["summary"]["may_have_partially_written"] is True


class PeerlessDestination(RecordingDestination):
    """A destination whose first operation names a peer that matches nothing (SC-016).

    Raises the real `PeerNotFoundError` with the message shape the adapter builds — the peer
    kind, the peer identity and the referring operation identifier — because what this case is
    about is the **run's** fate once that refusal leaves the write surface, and a stand-in
    exception would not travel the taxonomy arm the CLI dispatches on.
    """

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401, PLR6301
        _ = peers
        msg = (
            f"Operation {operation.operation_id!r} references peer kind 'LocationSite' with identity "
            f"{{'name': 'dc-nowhere'}}, which matches no object at the destination."
        )
        raise PeerNotFoundError(msg)


def test_a_zero_match_peer_fails_the_run_and_records_it(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """T100 / SC-016: the refusal must **fail the run**, not merely leave the write surface."""
    _appliable_run(tmp_path)
    destination = PeerlessDestination()

    with caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"):
        result = _run_apply(destination)

    assert result.exit_code != 0
    assert destination.writes == []
    reported = _operator_errors(caplog)
    assert "LocationSite" in reported, "the refusal must reach the operator naming the peer kind"
    assert "dc-nowhere" in reported, "and the peer identity"
    assert PeerNotFoundError.next_action in reported, "and its next action (AD059)"

    recorded = _run_json(tmp_path)
    assert recorded["status"] == "failed"
    assert recorded["summary"]["applied_operations"] == []
    assert recorded["summary"]["skipped_delete_count"] == 0


class InterruptedDestination(RecordingDestination):
    """A destination interrupted mid-plan — the operator's Ctrl-C on a long apply."""

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        if self.writes:
            raise KeyboardInterrupt
        return super().apply_planned_operation(operation=operation, peers=peers)


def test_an_interrupt_mid_apply_records_failed_and_the_partial_applied_set(tmp_path: Path) -> None:
    """Ctrl-C between two operations: the run says what was written, and still exits 130."""
    _appliable_run(tmp_path)
    destination = InterruptedDestination()

    result = _run_apply(destination)

    first_id = str(APPLY_PLAN[0]["operation_id"])
    assert destination.writes == [first_id]
    assert result.exit_code == 130
    assert isinstance(result.exception, SystemExit)

    recorded = _run_json(tmp_path)
    assert recorded["status"] == "failed"
    assert recorded["summary"]["applied_operations"] == [first_id]
    assert recorded["summary"]["skipped_delete_operations"] == []
    assert recorded["summary"]["skipped_delete_count"] == 0


def test_a_code_defect_escapes_as_a_sanitized_wrapper_while_the_run_records_what_was_written(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The defect shape and partial record survive, but its config secret cannot render."""
    sentinel = "db004-apply-config-secret-sentinel"
    sync_instance = get_instance(name=SYNC_NAME, directory=str(EXAMPLES_DIR))
    assert sync_instance is not None
    sync_instance = sync_instance.model_copy(deep=True)
    assert sync_instance.destination.settings is not None
    sync_instance.destination.settings["api_token"] = sentinel
    _appliable_run(tmp_path, config_version=default_config_version(sync_instance))
    destination = DefectiveDestination(f"a code defect, not a destination refusal: {sentinel}")

    with (
        patch("infrahub_sync.cli.get_instance", return_value=sync_instance),
        caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"),
    ):
        result = _run_apply(destination)

    assert result.exit_code != 0
    assert isinstance(result.exception, RuntimeError)
    assert "AssertionError" in str(result.exception)
    reported = _operator_errors(caplog)
    assert "defect rather than a destination refusal" in reported, (
        "the operator has to be told the destination is not the thing to repair"
    )
    assert "***" in reported
    assert sentinel not in reported
    assert sentinel not in result.output
    assert result.exception is not None
    assert isinstance(result.exception.__cause__, RuntimeError)
    assert "AssertionError" in str(result.exception.__cause__)
    rendered = "".join(traceback.format_exception(result.exception))
    assert sentinel not in rendered
    assert "apply_planned_operation" in rendered
    raised_chain: list[BaseException] = [result.exception]
    index = 0
    while index < len(raised_chain):
        error = raised_chain[index]
        for linked in (error.__cause__, error.__context__):
            if linked is not None and linked not in raised_chain:
                raised_chain.append(linked)
        index += 1
    assert all(sentinel not in str(error) for error in raised_chain)
    assert OperationApplyFailedError.next_action not in reported, "and must not be given the refusal's remedy"

    first_id = str(APPLY_PLAN[0]["operation_id"])
    assert destination.writes == [first_id]
    recorded = _run_json(tmp_path)
    assert recorded["status"] == "failed"
    assert recorded["summary"]["applied_operations"] == [first_id]
    assert recorded["summary"]["failed_operation"] == str(APPLY_PLAN[1]["operation_id"])
    assert recorded["summary"]["may_have_partially_written"] is True


def test_a_mid_apply_value_error_is_reported_as_a_defect_with_its_partial_write(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A ValueError after a write is not mislabeled as applier construction failure."""
    _appliable_run(tmp_path)
    destination = ValueErrorDestination()

    with caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"):
        result = _run_apply(destination)

    assert result.exit_code != 0
    assert isinstance(result.exception, RuntimeError)
    assert "ValueError" in str(result.exception)
    assert destination.writes == [str(APPLY_PLAN[0]["operation_id"])]
    reported = _operator_errors(caplog)
    assert "defect rather than a destination refusal" in reported
    assert "Failed to initialize the destination for the apply" not in reported
    assert "may have written part of its change" in reported
    assert _run_json(tmp_path)["summary"]["may_have_partially_written"] is True


def test_apply_does_not_reread_the_plan_after_successful_destination_writes(tmp_path: Path) -> None:
    """A CLI apply remains successful even if a now-unneeded post-apply reader would fail."""
    _appliable_run(tmp_path)
    destination = RecordingDestination()

    with patch(
        "infrahub_sync.execution.read_saved_plan",
        side_effect=PlanArtifactUnreadableError("post-apply reread must not happen"),
    ):
        result = _run_apply(destination)

    assert result.exit_code == 0, result.output
    assert len(destination.writes) == len(APPLY_PLAN)
    assert _run_json(tmp_path)["status"] == "applied"


def test_apply_result_counts_come_from_the_artifact_consumed_before_destination_writes(tmp_path: Path) -> None:
    """The core returns correct counts without a failure point after completed writes."""
    _appliable_run(tmp_path)
    destination = RecordingDestination()
    sync_instance = get_instance(name=SYNC_NAME, directory=str(EXAMPLES_DIR))
    assert sync_instance is not None

    with patch(
        "infrahub_sync.execution.read_saved_plan",
        side_effect=PlanArtifactUnreadableError("post-apply reread must not happen"),
    ):
        result = execute_run(
            sync_instance,
            operation="apply",
            confirm_writes=True,
            run_id=RUN_ID,
            _plan_applier_factory=_patched_open_existing(destination),
        )

    assert result.summary == {"create": 1, "update": 1, "delete": 0}
    assert destination.writes == [str(operation["operation_id"]) for operation in APPLY_PLAN]


def test_apply_factory_refusal_redacts_resolved_configuration_credentials(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Inline destination credentials cannot leak from the construction-only apply seam."""
    sentinel = "db004-apply-factory-config-secret"
    sync_instance = get_instance(name=SYNC_NAME, directory=str(EXAMPLES_DIR))
    assert sync_instance is not None
    sync_instance = sync_instance.model_copy(deep=True)
    assert sync_instance.destination.settings is not None
    sync_instance.destination.settings["api_token"] = sentinel
    _appliable_run(tmp_path, config_version=default_config_version(sync_instance))

    with (
        patch("infrahub_sync.cli.get_instance", return_value=sync_instance),
        patch("infrahub_sync.cli.PlanApplier.open_existing", side_effect=ValueError(f"adapter rejected {sentinel}")),
        caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"),
    ):
        result = _apply(RUN_ID)

    assert result.exit_code == 1
    reported = _operator_errors(caplog)
    assert sentinel not in reported
    assert "***" in reported
    assert "defect rather than a destination refusal" not in reported


def test_apply_plan_refusal_redacts_resolved_configuration_credentials(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Designed apply refusals use the resolved instance's redaction values."""
    sentinel = "db004-apply-refusal-config-secret"
    sync_instance = get_instance(name=SYNC_NAME, directory=str(EXAMPLES_DIR))
    assert sync_instance is not None
    sync_instance = sync_instance.model_copy(deep=True)
    assert sync_instance.destination.settings is not None
    sync_instance.destination.settings["api_token"] = sentinel
    _appliable_run(tmp_path, config_version=default_config_version(sync_instance))
    refusal = PlanVerificationError(f"plan refused credential {sentinel}")

    with (
        patch("infrahub_sync.cli.get_instance", return_value=sync_instance),
        patch("infrahub_sync.cli.execute_run", side_effect=refusal),
        caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"),
    ):
        result = _apply(RUN_ID)

    assert result.exit_code == 1
    reported = _operator_errors(caplog)
    assert sentinel not in reported
    assert "***" in reported


# ======================================================================================
# The reviewed generation is immutable, and approval names its bytes
# ======================================================================================


def _diff_into(run_id: str) -> Any:  # noqa: ANN401 — click's Result type is not exported for annotation
    """Invoke the **live** `diff` naming `run_id`, which is the re-plan-in-place attempt.

    Not review mode: no `--from-plan`. Adapter construction is refused for every case in this
    file, so a command that reaches the factory fails on the sentinel — which is what makes
    "refused before anything was constructed" observable from the construction log.
    """
    return runner.invoke(app, ["diff", "--name", SYNC_NAME, "--directory", str(EXAMPLES_DIR), "--run-id", run_id])


def test_a_second_diff_into_a_committed_run_id_is_refused_with_the_reviewed_plan_intact(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, adapter_construction_log: list[dict[str, Any]]
) -> None:
    """`diff --run-id R` no longer replaces the plan a human reviewed under R."""
    directory = _appliable_run(tmp_path)
    before = {path: path.read_bytes() for path in sorted(plan_dir(directory).rglob("*")) if path.is_file()} | {
        _apply_snapshot_path(directory): _apply_snapshot_path(directory).read_bytes()
    }

    with caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"):
        result = _diff_into(RUN_ID)

    assert result.exit_code != 0
    assert adapter_construction_log == [], "the refusal must precede adapter construction and extraction"
    message = _operator_errors(caplog)
    assert "already holds a committed plan generation" in message
    assert str(manifest_path(directory)) in message
    assert "Next action:" in message
    assert "without `--run-id`" in message
    assert {path: path.read_bytes() for path in before} == before
    assert not (directory / "run.json").exists()


def test_a_generation_committed_while_the_diff_waited_for_the_lock_is_left_intact(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, adapter_construction_log: list[dict[str, Any]]
) -> None:
    """The immutability guard has to hold at the point the lock stops serializing invocations.

    Two `diff --run-id R` invocations race: the first commits its generation while the second is
    blocked on the pipeline lock, so the second's pre-lock check saw a free run id and is wrong
    by the time it acquires the lock. Extraction from there rewrites the `A/` snapshots the
    committed plan binds itself to, which — with the source moved on — leaves the reviewed plan
    unappliable, and the writer's refusal comes too late to prevent it.

    The commit is staged inside the lock's `__enter__` because that is exactly the window: the
    invocation under test has already passed its pre-lock check and has not yet built anything.
    """
    _run_directory(tmp_path)
    committed: dict[Path, bytes] = {}

    @contextmanager
    def _commit_while_waiting(name: str, **_kwargs: object) -> Iterator[None]:
        with pipeline_lock(name):
            directory = _appliable_run(tmp_path)
            committed.update(
                {path: path.read_bytes() for path in sorted(plan_dir(directory).rglob("*")) if path.is_file()}
            )
            committed[_apply_snapshot_path(directory)] = _apply_snapshot_path(directory).read_bytes()
            yield

    with (
        caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"),
        patch("infrahub_sync.execution.pipeline_lock", _commit_while_waiting),
    ):
        result = _diff_into(RUN_ID)

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"the refusal escaped as a raw {type(result.exception).__name__} traceback"
    )
    assert adapter_construction_log == [], "the refusal must precede adapter construction and extraction"
    assert committed, "the fixture must have committed a generation inside the window"
    assert {path: path.read_bytes() for path in committed} == committed, (
        "the refused re-plan rewrote the snapshots the committed plan is bound to"
    )
    assert not (_cache_root(tmp_path) / RUN_ID / "run.json").exists()
    message = _operator_errors(caplog)
    assert "already holds a committed plan generation" in message
    assert "Next action:" in message


# The two states the failure injections leave behind: a run whose plan was never
# started, and a run whose operations file was published while its manifest never was. The
# writer's own crash injection is `tests/plan/test_writer.py`; what is decided here is what
# the two commands an operator reaches do with the state it leaves.
def _crashed_before_the_writer(tmp_path: Path) -> Path:
    """A run that extracted its source side and then failed before deriving the plan."""
    directory = _run_directory(tmp_path)
    _write_apply_snapshot(directory, APPLY_SNAPSHOT_ROWS)
    return directory


def _crashed_publishing_the_manifest(tmp_path: Path) -> Path:
    """A run whose `operations.jsonl` landed and whose manifest never did (AD014)."""
    directory = _appliable_run(tmp_path)
    manifest_path(directory).unlink()
    return directory


# One row per crash state: how it is built, what the review says, and what the apply says.
# The two verdicts differ in wording because they are reached by different components — the
# reader on review, the pre-apply verifier's format-version gate on apply — and asserting one
# phrase for both would hide a path that stopped refusing.
INCOMPLETE_GENERATIONS: dict[str, tuple[Callable[[Path], Path], tuple[str, str]]] = {
    "crashed_before_the_writer": (_crashed_before_the_writer, ("holds no plan artifact", "holds no plan artifact")),
    "crashed_publishing_the_manifest": (
        _crashed_publishing_the_manifest,
        ("manifest.json is absent", "no readable, parseable manifest"),
    ),
}


@pytest.mark.parametrize(
    ("build", "fragments"), list(INCOMPLETE_GENERATIONS.values()), ids=list(INCOMPLETE_GENERATIONS)
)
def test_an_incomplete_generation_is_refused_by_both_review_and_apply_with_no_destination_call(
    tmp_path: Path,
    destination_double: RecordingDestination,
    caplog: pytest.LogCaptureFixture,
    build: Callable[[Path], Path],
    fragments: tuple[str, str],
) -> None:
    """A half-written generation is applicable to nobody."""
    review_fragment, apply_fragment = fragments
    build(tmp_path)
    constructed: list[str] = []

    with caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"):
        review = _review("--from-plan", RUN_ID)
        assert review.exit_code != 0, review.output
        assert review_fragment in _operator_errors(caplog)
        caplog.clear()
        with patch(
            "infrahub_sync.cli.PlanApplier.open_existing",
            _patched_open_existing(destination_double, constructed=constructed),
        ):
            applied = _apply(RUN_ID)
        assert applied.exit_code != 0
        assert apply_fragment in _operator_errors(caplog)

    assert destination_double.writes == [], "an incomplete generation must reach the destination not at all"


@pytest.mark.parametrize(
    "build", [case[0] for case in INCOMPLETE_GENERATIONS.values()], ids=list(INCOMPLETE_GENERATIONS)
)
def test_an_incomplete_generation_stays_re_plannable_under_the_same_run_id(
    tmp_path: Path, adapter_construction_log: list[dict[str, Any]], build: Callable[[Path], Path]
) -> None:
    """The residual that immutability has to preserve: neither crash strands the run id."""
    build(tmp_path)

    result = _diff_into(RUN_ID)

    assert result.exit_code != 0, "the construction sentinel is expected to stop this run"
    assert adapter_construction_log, "the immutability guard refused a generation that was never committed"


def _stored_checksum(tmp_path: Path, run_id: str = RUN_ID) -> str:
    """The `plan_checksum` the stored manifest records, read from disk."""
    recorded = json.loads(manifest_path(_cache_root(tmp_path) / run_id).read_text(encoding="utf-8"))
    return str(recorded["plan_checksum"])


@pytest.mark.parametrize("options", [(), ("--detail",)], ids=["summary", "detail"])
def test_the_review_prints_the_full_plan_checksum_at_both_depths(tmp_path: Path, options: tuple[str, ...]) -> None:
    """The review shows the checksum, not only the verdict about it."""
    _store(tmp_path)

    result = _review("--from-plan", RUN_ID, *options)

    assert result.exit_code == 0, result.output
    assert f"plan checksum: {_stored_checksum(tmp_path)}" in result.output


def test_an_apply_naming_the_reviewed_checksum_applies(
    tmp_path: Path, destination_double: RecordingDestination
) -> None:
    """The positive half: the approved bytes are the stored bytes, so the apply proceeds."""
    _appliable_run(tmp_path)

    with patch("infrahub_sync.cli.PlanApplier.open_existing", _patched_open_existing(destination_double)):
        result = runner.invoke(
            app,
            [
                "apply",
                "--name",
                SYNC_NAME,
                "--directory",
                str(EXAMPLES_DIR),
                "--run-id",
                RUN_ID,
                "--expected-checksum",
                _stored_checksum(tmp_path),
            ],
        )

    assert result.exit_code == 0, result.output
    assert len(destination_double.writes) == len(APPLY_PLAN)


def test_an_apply_naming_another_generations_checksum_refuses_before_the_destination_exists(
    tmp_path: Path, destination_double: RecordingDestination, caplog: pytest.LogCaptureFixture
) -> None:
    """The whole point of the approval binding: a valid plan that is not the approved one is refused."""
    _appliable_run(tmp_path)
    approved = _stored_checksum(tmp_path)
    substituted = _appliable_run(tmp_path, [operation_record(identity={"name": "substituted"})])
    assert _stored_checksum(tmp_path) != approved, "the substituted plan must be a different generation"
    assert manifest_path(substituted).is_file()
    constructed: list[str] = []

    with (
        caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"),
        patch(
            "infrahub_sync.cli.PlanApplier.open_existing",
            _patched_open_existing(destination_double, constructed=constructed),
        ),
    ):
        result = runner.invoke(
            app,
            [
                "apply",
                "--name",
                SYNC_NAME,
                "--directory",
                str(EXAMPLES_DIR),
                "--run-id",
                RUN_ID,
                "--expected-checksum",
                approved,
            ],
        )

    assert result.exit_code != 0
    assert constructed == [], "the refusal must precede destination construction"
    assert destination_double.writes == []
    message = _operator_errors(caplog)
    assert "is not the plan this apply approved" in message
    assert approved in message
    assert _stored_checksum(tmp_path) in message
    assert "Next action:" in message
    assert message.count("Next action:") == 1
    assert not (_cache_root(tmp_path) / RUN_ID / "run.json").exists()


def test_an_expected_checksum_against_a_non_utf8_manifest_refuses_instead_of_tracing_back(
    tmp_path: Path, destination_double: RecordingDestination, caplog: pytest.LogCaptureFixture
) -> None:
    """The approval check's bytes-to-mapping step must not crash on undecodable bytes."""
    directory = _appliable_run(tmp_path)
    approved = _stored_checksum(tmp_path)
    manifest_path(directory).write_bytes(b'{"format_version": 2, "config_version": "\xff\xfe"}')

    with (
        caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"),
        patch("infrahub_sync.cli.PlanApplier.open_existing", _patched_open_existing(destination_double)),
    ):
        result = runner.invoke(
            app,
            [
                "apply",
                "--name",
                SYNC_NAME,
                "--directory",
                str(EXAMPLES_DIR),
                "--run-id",
                RUN_ID,
                "--expected-checksum",
                approved,
            ],
        )

    assert result.exit_code == 1, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"the refusal escaped as a raw {type(result.exception).__name__} traceback"
    )
    assert destination_double.writes == [], "nothing may be written for an unreadable manifest"
    message = _operator_errors(caplog)
    assert "could not be hashed" in message, message
    assert "Next action:" in message, message


def test_an_expected_checksum_against_an_unhashable_plan_refuses_rather_than_applying(
    tmp_path: Path, destination_double: RecordingDestination, caplog: pytest.LogCaptureFixture
) -> None:
    """The approval check fails closed: a plan that cannot be hashed matched nothing.

    An artifact too incomplete to hash cannot be compared against the operator's approved
    value, and the pre-apply verifier never makes that comparison — it tests the artifact's
    self-consistency. Passing the unhashable case through would therefore skip the control
    the operator asked for, not defer it, and the apply would reach the destination with a
    plan no approval ever named.
    """
    directory = _appliable_run(tmp_path)
    manifest_path(directory).unlink()
    constructed: list[str] = []

    with (
        caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"),
        patch(
            "infrahub_sync.cli.PlanApplier.open_existing",
            _patched_open_existing(destination_double, constructed=constructed),
        ),
    ):
        result = runner.invoke(
            app,
            [
                "apply",
                "--name",
                SYNC_NAME,
                "--directory",
                str(EXAMPLES_DIR),
                "--run-id",
                RUN_ID,
                "--expected-checksum",
                "0" * 64,
            ],
        )

    assert result.exit_code != 0, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"the refusal escaped as a raw {type(result.exception).__name__} traceback"
    )
    assert constructed == [], "an unverifiable approval must refuse before the destination is constructed"
    assert destination_double.writes == [], "an unverifiable approval must write nothing"
    message = _operator_errors(caplog)
    assert "could not be hashed" in message, message
    assert "Next action:" in message, message


def test_the_expected_checksum_comparison_ignores_hex_case_and_surrounding_space(
    tmp_path: Path, destination_double: RecordingDestination
) -> None:
    """A checksum copied out of a terminal or a ticket still matches."""
    _appliable_run(tmp_path)

    with patch("infrahub_sync.cli.PlanApplier.open_existing", _patched_open_existing(destination_double)):
        result = runner.invoke(
            app,
            [
                "apply",
                "--name",
                SYNC_NAME,
                "--directory",
                str(EXAMPLES_DIR),
                "--run-id",
                RUN_ID,
                "--expected-checksum",
                f"  {_stored_checksum(tmp_path).upper()} ",
            ],
        )

    assert result.exit_code == 0, result.output
    assert len(destination_double.writes) == len(APPLY_PLAN)


def test_a_plan_substituted_after_the_early_approval_check_is_never_applied(
    tmp_path: Path, destination_double: RecordingDestination, caplog: pytest.LogCaptureFixture
) -> None:
    """The approval has to answer about the bytes that are applied, not an earlier read of them.

    The command's own check runs before the destination is built, so it necessarily reads the
    artifact before the apply consumes it. A plan replaced in that window — by a concurrent
    re-plan, a restore, or a hand edit — is internally valid and passes verification, so nothing
    but an approval comparison against the applied bytes can catch it.
    """
    _appliable_run(tmp_path)
    approved = _stored_checksum(tmp_path)
    opener = _patched_open_existing(destination_double)

    def _substitute_then_open(sync_instance: Any, **kwargs: Any) -> PlanApplier:  # noqa: ANN401
        applier = opener(sync_instance, **kwargs)
        _appliable_run(tmp_path, [operation_record(identity={"name": "substituted"})])
        return applier

    with (
        caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"),
        patch("infrahub_sync.cli.PlanApplier.open_existing", _substitute_then_open),
    ):
        result = runner.invoke(
            app,
            [
                "apply",
                "--name",
                SYNC_NAME,
                "--directory",
                str(EXAMPLES_DIR),
                "--run-id",
                RUN_ID,
                "--expected-checksum",
                approved,
            ],
        )

    assert result.exit_code != 0, result.output
    assert destination_double.writes == [], "the substituted plan must not be dispatched"
    message = _operator_errors(caplog)
    assert "is not the plan this apply approved" in message, message
    assert _stored_checksum(tmp_path) in message, message
    assert "Next action:" in message, message
    assert _run_json(tmp_path)["status"] == "failed"


def test_a_broken_apply_invariant_records_what_was_written_not_an_empty_record(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """AD062's safety net must not zero the record it exists to protect."""
    _appliable_run(tmp_path)
    destination = RecordingDestination()
    applied_ids = [str(record["operation_id"]) for record in APPLY_PLAN]
    real_parse = parse_plan_artifact

    def _inflated_count(raw: Any, **kwargs: Any) -> Any:  # noqa: ANN401 — mirrors the parser
        loaded = real_parse(raw, **kwargs)
        loaded.manifest.operations_count += 1
        return loaded

    with (
        caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"),
        patch("infrahub_sync.plan.reader.parse_plan_artifact", _inflated_count),
    ):
        result = _run_apply(destination)

    assert result.exit_code != 0
    assert destination.writes == applied_ids
    assert "does not account for the plan" in _operator_errors(caplog)

    recorded = _run_json(tmp_path)
    assert recorded["status"] == "failed"
    assert recorded["summary"]["applied_operations"] == applied_ids
    assert recorded["summary"]["skipped_delete_count"] == 0
