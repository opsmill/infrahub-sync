"""Phase F — the command-line review mode, its errors, its isolation, and the apply refusals.

Eight tasks share this file because they share one surface: `diff --from-plan <run-id>`
(AD057) and the `apply` it exists to make safe. Splitting them would duplicate the fixtures
and, worse, let the review assertions and the apply assertions drift apart — which is
exactly the divergence between "what was reviewed" and "what was applied" the whole outcome
exists to close.

- **T061** — SC-009's CLI half: the summary and the per-object detail, with and without
  `--kind`, each against a stored artifact **written by a process that has since exited**,
  with neither source nor destination reachable. Every case also asserts AD056's
  delete-computation disclosure at that depth, and two of the four run against a plan whose
  destination side was loaded incrementally, so the "deletes were NOT computed" wording is
  asserted reachable rather than assumed.
- **T062** — the error paths, including AD073's bounded run-identifier enumeration and its
  no-runs arm, the `--run-id`-alongside-`--from-plan` warning, and the one case where review
  **refuses** rather than renders.
- **T063** — isolation: no adapter, no directory under the cache root, no `run.json`, and no
  pipeline lock (asserted while the lock is held by another holder).
- **T064** — SC-012: the top-level command list compared as text against the **committed**
  T002 baseline fixture, never one recovered by reverting the tree at test time (AD060).
- **T065** — the apply path: SC-004's six refusals individually, plus SC-011, SC-015, SC-018
  and AD055's unrecognized action, each with zero destination writes and the run state read
  back from `run.json`; plus the delete-bearing apply that **succeeds**, which is where
  AD069's merge is asserted by name.
- **T087** — the four delete-disclosure cases, each of which fails if its rendering is
  removed.
- **T089** — the next-action obligation across the whole taxonomy (AD059, AD071, AD073,
  AD082).
- **T090** — the help strings, which are fixed in `contracts/cli-review-mode.md` rather than
  discovered, because `docs.generate` renders them verbatim into the reference documentation
  (AD061).

Two properties of this file are deliberate and must not be "tidied". The review cases assert
against **stdout** (`typer.echo`, AD032) while every error case asserts against the
**logger**, because `print_error_and_abort` reports there — a test that read both from one
stream would pass against an implementation that merged them and broke FR-008's channel
split. And the apply cases run against a real `Potenda` over a recording destination rather
than a `MagicMock`: a mock answers `hasattr` for every name, so the missing-write-surface
refusal cannot be expressed against one.
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
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from filelock import FileLock, Timeout
from typer.testing import CliRunner

from infrahub_sync.cache.locks import pipeline_lock
from infrahub_sync.cache.parquet_io import write_resource_side
from infrahub_sync.cli import app
from infrahub_sync.plan.checksum import source_snapshot_records
from infrahub_sync.plan.config_version import default_config_version
from infrahub_sync.plan.errors import (
    ApplyRecordInvariantError,
    DuplicateOperationIdError,
    OperationApplyFailedError,
    PeerAmbiguousError,
    PeerNotFoundError,
    PlanArtifactError,
    PlanArtifactTornError,
    PlanArtifactUnreadableError,
    PlanFormatV1Error,
    PlanFormatVersionError,
    PlanVerificationError,
    SourcePeerUnresolvedError,
    UnaccountedIdentityComponentError,
    UnformableDestinationIdentityError,
    UnkeyedWriteRefusedError,
    UnknownPlanKindError,
    UnknownRunIdentifierError,
    UnserializablePayloadValueError,
    UnsupportedOperationActionError,
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
    from collections.abc import Callable, Mapping, Sequence

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
    both adapters through (`infrahub_sync/utils.py:183-184`), so refusing here refuses both.

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
    `logging.WARNING` (`infrahub_sync/cli.py:48`, `:78-79`) — the invocation SC-007's warning
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
# T061 — SC-009's CLI half (FR-006, FR-007, FR-008, AD056, AD057)
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
    """Each of SC-009's CLI cases renders, and states its delete-computation record (AD056).

    The disclosure assertion is inside every case on purpose: SC-009's pass condition is not
    "the counts are right", it is "the counts are right **and** both depths state the
    delete-computation record". A case that only checked the counts would pass against a
    renderer that dropped the disclosure entirely.
    """
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
    """AD043's identity shapes, as `--detail` renders them (FR-006, AD020).

    The identity of a relationship-bearing operation is not flat: a component that crosses a
    reference is a `{peer_kind, identity}` pair, recursively. SC-005 compares the apply
    against what this listing showed, so an identity rendered as `{...}` — or with its
    nesting flattened away — is an operator approving an object they cannot name.
    """
    _store(tmp_path, NESTED_PEER_PLAN, run_id=OTHER_RUN_ID)

    result = _review("--from-plan", OTHER_RUN_ID, "--detail")

    assert result.exit_code == 0, result.output
    lines = [line for line in _strip_ansi(result.output).splitlines() if line.startswith("op_")]
    assert len(lines) == len(NESTED_PEER_PLAN)
    for rendered, line in zip(NESTED_PEER_DETAIL, lines, strict=True):
        assert line.endswith(rendered), f"expected identity {rendered!r} at the end of {line!r}"


# ======================================================================================
# T087 — the four delete-disclosure cases (FR-006, FR-015, SC-009, AD024, AD056)
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


def test_a_computed_plan_without_deletes_carries_no_not_executed_annotation(tmp_path: Path) -> None:
    """(b) The annotation is conditional, not unconditional noise.

    Paired with (a) on purpose: an implementation that printed the annotation always would
    pass (a) and fail here, which is the only way to tell "disclosed" from "boilerplate".
    """
    _store(tmp_path, DELETELESS_PLAN, deletes_computed=True)

    summary, detail = _both_depths(RUN_ID)

    for output in (summary, detail):
        assert "deletes computed: yes" in output
        assert "will be executed" not in output
        assert "NONE will be executed" not in output
        assert "not executed" not in output


def test_a_plan_whose_deletes_were_not_computed_says_so_in_words_at_both_depths(tmp_path: Path) -> None:
    """(c) The not-computed wording, and its distinctness from (b)'s output.

    The whole point of AD056 is that "this plan has no deletes" and "nobody looked for
    deletes" must not read alike, so the two renderings are compared against each other
    rather than each being checked in isolation.
    """
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
    """(d) The CLI and the in-process reader disclose from **one** source (FR-029, AD056).

    Without this the two surfaces could disagree, and SC-009's four cases would be measuring
    two implementations rather than one.
    """
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
# T062 — the error paths (FR-006, FR-008, FR-010, FR-017, FR-019, FR-027, AD057-AD059, AD073)
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
    """The refusal is a refusal, not an empty rendering (AD021).

    Its own test because the failure mode is silence: a mode that created the run directory
    before checking would render a mistyped identifier as a valid plan with nothing in it,
    and every assertion about the message above would still hold on the *other* cases.
    """
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
    """AD073's first-run arm: a stated message, not a `FileNotFoundError` traceback.

    `cache_root_for` computes a path and neither creates nor checks it, so an unguarded
    listing raises on a sync that has never run — which is precisely the operator most likely
    to reach for `--from-plan` before ever having produced a plan.
    """
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
    """FR-019: a run that predates the artifact format has nothing to review."""
    directory = _run_directory(tmp_path)
    (directory / "plan.parquet").write_bytes(b"pre-existing row format")

    message = _failed_review(caplog, "--from-plan", RUN_ID)

    assert f"Run '{RUN_ID}' holds no plan artifact" in message
    assert str(directory / "plan") in message
    assert "Re-plan: re-run `diff` for this sync" in message


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
    assert "Re-run `diff` to rebuild the plan artifact" in message


def test_an_unrecognized_format_version_lists_the_versions_supported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """FR-027 and SC-018: the version found, the versions supported, and the next action."""
    _store(tmp_path, format_version=UNSUPPORTED_FORMAT_VERSION)

    message = _failed_review(caplog, "--from-plan", RUN_ID)

    assert f"declares format version {UNSUPPORTED_FORMAT_VERSION}" in message
    for version in sorted(SUPPORTED_FORMAT_VERSIONS):
        assert f"Supported plan format versions: {version}" in message
    assert "re-plan with this version, or apply with the version that wrote it" in message


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
    """AD058's reader arm. Paired with the case above so an implementation that raised for
    both — the behaviour AD058 corrected — cannot satisfy the pair."""
    _store(tmp_path)

    message = _failed_review(caplog, "--from-plan", RUN_ID, "--detail", "--kind", UNDECLARED_KIND)

    assert f"No destination kind '{UNDECLARED_KIND}' is declared for this synchronization" in message
    assert "The plan holds operations for: BuiltinTag, LocationSite." in message
    assert "declares that kind" not in message


def test_a_kind_the_plan_holds_but_the_configuration_omits_errors_at_the_cli(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """T104's CLI arm: the case the two above cannot reach.

    Both cases above use a kind the plan does not hold, so neither can distinguish the
    specified single condition — "the configuration does not declare it" — from a conjunction
    that also requires the plan not to hold it. Here the plan *does* hold operations for the
    undeclared kind, which is the state a review is designed to render: review deliberately
    does not verify the configuration version (AD031), so a plan written before an entry was
    dropped from `schema_mapping` is read against a configuration that no longer declares it.

    The renderer cannot recover the obligation on its own — `_select_review_records` turns only
    an **empty** reader result into FR-006's error, so under the conjunction these operations
    would print as ordinary per-object detail for a kind this synchronization does not have.
    """
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
    """The one ignored option this mode warns about, because it is the only other one that
    names a run (AD057). The run actually read is asserted, not only the warning."""
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
    """The one bound on "review renders rather than refuses" (AD055).

    Asserted here as well as at T026 because it is a *bound*, and an untested bound is one a
    later tidy-up removes: a plan whose operation vocabulary this release cannot interpret
    cannot be honestly summarized either.
    """
    _store(tmp_path, [operation_record(action="purge", identity={"name": "prod"})])

    message = _failed_review(caplog, "--from-plan", RUN_ID)

    assert "declares action 'purge'" in message
    assert f"Recognized actions: {', '.join(ACTIONS)}." in message
    assert "re-plan with this version" in message


def test_a_plan_that_would_fail_verification_is_still_rendered(tmp_path: Path) -> None:
    """AD031's other half, kept next to the refusal above so the pair cannot be collapsed.

    A checksum that does not verify is a *verification* failure, which review reports and
    renders around; an unrecognized action is not, and review refuses it.
    """
    directory = _store(tmp_path, [tamperable_operation()])
    tamper_with_operations(directory)

    result = _review("--from-plan", RUN_ID)

    assert result.exit_code == 0, result.output
    output = _flat(result.output)
    assert "checksum: FAILED" in output
    assert "The plan checksum does not match" in output
    assert "operations: 1" in output


# ======================================================================================
# T063 — isolation (FR-008, AD021, AD031)
# ======================================================================================

# Generous by two orders of magnitude against the 60-second lock timeout: the claim is "does
# not wait for the lock", and a bound tight enough to flake on a loaded CI box would test the
# runner rather than the code.
LOCK_FREE_SECONDS = 20.0


def test_the_review_path_constructs_no_adapter(tmp_path: Path, adapter_construction_log: list[dict[str, Any]]) -> None:
    """FR-008 and SC-009: the mode branches above `get_potenda_from_instance`.

    The sentinel raises if it is reached, so this asserts the call log is empty **and** that
    the command succeeded — a review that constructed an adapter would fail rather than pass
    quietly, and a review that succeeded without touching the sentinel is the positive half.
    """
    _store(tmp_path)

    result = _review("--from-plan", RUN_ID, "--detail")

    assert result.exit_code == 0, result.output
    assert adapter_construction_log == []


def test_the_review_path_creates_nothing_under_the_cache_root(tmp_path: Path) -> None:
    """No run directory, no `run.json`, no cached rendering — the whole subtree is unchanged.

    Asserted over the subtree rather than over `run.json` alone: branching above
    `get_potenda_from_instance` is what stops a typo'd run id rendering as a valid
    zero-operation plan, and that guarantee is about the directory, not about one file.
    """
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
    """AD021: review neither blocks nor is blocked by a running sync.

    The lock is genuinely held for the duration — proven by a second acquisition attempt
    timing out — so a review that took it would sit for the full 60-second timeout and fail
    the bound below instead of passing on an unlocked cache.
    """
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

    with patch("infrahub_sync.cli.pipeline_lock", _record):
        result = _review("--from-plan", RUN_ID)

    assert result.exit_code == 0, result.output
    assert taken == []


# ======================================================================================
# T064 — SC-012, against the committed baseline fixture (AD060)
# ======================================================================================

BASELINE_FIXTURE = REPO_ROOT / "tests" / "data" / "cli_help_baseline.txt"

# The rendering environment the committed fixture was captured in. Fixed here so the
# comparison is against the baseline's content and not against the terminal the suite
# happens to run in.
HELP_ENVIRONMENT = {"COLUMNS": "80", "TERM": "dumb", "NO_COLOR": "1"}
WIDE_HELP_ENVIRONMENT = {"COLUMNS": "400", "TERM": "dumb", "NO_COLOR": "1"}

EXPECTED_COMMANDS = ("list", "diff", "sync", "apply", "generate")
NEW_REVIEW_OPTIONS = ("--from-plan", "--detail", "--kind")


def _help_text(*args: str, wide: bool = False) -> str:
    """Capture `--help` for `args` in the fixture's own rendering environment."""
    result = runner.invoke(
        app,
        [*args, "--help"],
        prog_name="infrahub-sync",
        env=WIDE_HELP_ENVIRONMENT if wide else HELP_ENVIRONMENT,
    )
    assert result.exit_code == 0, result.output
    return _strip_ansi(result.output)


def _command_names(help_text: str) -> list[str]:
    """The command names listed in a help output's Commands panel, in order.

    Parsed rather than regex-matched against the whole text so a command name appearing
    inside a description cannot be counted as a command.
    """
    names: list[str] = []
    inside = False
    for line in help_text.splitlines():
        if "─ Commands ─" in line:
            inside = True
            continue
        if inside and line.startswith("╰"):
            break
        if inside and line.startswith("│"):
            body = line[1:]
            if body[:1] == " " and body[1:2] not in {"", " "}:
                names.append(body.split()[0])
    return names


def test_the_committed_baseline_fixture_exists() -> None:
    """AD060: an absent baseline is a failure, never a regeneration.

    Regenerating it here would turn the comparison below into the post-change listing diffed
    against itself — a test that passes with no baseline at all, which is the exact
    degradation AD060 exists to prevent.
    """
    assert BASELINE_FIXTURE.is_file(), (
        f"the committed SC-012 baseline is missing at {BASELINE_FIXTURE}. Restore it from git; "
        "do not regenerate it, or the comparison becomes a self-comparison."
    )


def test_the_top_level_help_is_unchanged_against_the_committed_baseline() -> None:
    """SC-012, compared as text against the fixture captured before any CLI change."""
    baseline = BASELINE_FIXTURE.read_text(encoding="utf-8")

    live = _help_text()

    assert live == baseline


def test_the_command_list_is_the_same_five_commands_with_no_group_added() -> None:
    """The bar itself: no command added, none removed, and no `add_typer` group (AD019)."""
    baseline_commands = _command_names(BASELINE_FIXTURE.read_text(encoding="utf-8"))
    live_commands = _command_names(_help_text())

    assert baseline_commands == list(EXPECTED_COMMANDS)
    assert live_commands == baseline_commands
    assert len(live_commands) == 5
    assert set(live_commands) - set(baseline_commands) == set()
    assert set(baseline_commands) - set(live_commands) == set()


def test_no_add_typer_call_exists_in_the_cli_module() -> None:
    """The group bar, asserted at the source rather than inferred from the rendering."""
    source = (REPO_ROOT / "infrahub_sync" / "cli.py").read_text(encoding="utf-8")

    assert "add_typer" not in source
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
# T090 — the help strings, fixed by contract rather than discovered (AD057, AD061)
# ======================================================================================

# Verbatim from `contracts/cli-review-mode.md`, "Help text, specified rather than discovered".
CONTRACT_HELP_STRINGS: dict[str, str] = {
    "--from-plan": (
        "Review the saved plan artifact for this run id instead of comparing live systems. "
        "Constructs no adapter, extracts nothing, and takes no lock."
    ),
    "--detail": "Expand the plan summary to one record per operation. Requires --from-plan.",
    "--kind": "Narrow --detail to a single destination kind. Requires --from-plan and --detail.",
    "--run-id": (
        "Re-use a specific cache run id for the live comparison. To review a saved plan "
        "instead, pass --from-plan <run-id>."
    ),
}


@pytest.mark.parametrize(("option", "expected"), list(CONTRACT_HELP_STRINGS.items()))
def test_the_help_string_matches_the_contract(option: str, expected: str) -> None:
    """`docs.generate` renders these verbatim into `docs/docs/reference/cli.mdx` (T070), so
    text left to the implementer ships as reviewed documentation having never been reviewed."""
    _ = option
    assert _flat(expected) in _flat(_help_text("diff", wide=True))


def test_the_run_id_help_carries_the_cross_reference_to_from_plan() -> None:
    """The correction is load-bearing, not cosmetic (AD057).

    `--run-id` no longer describes everything an operator needs in order to select a run, and
    an operator reading only its old text had no way to learn that reviewing a stored plan is
    a different option.
    """
    line = _option_help_line("--run-id")

    assert "--from-plan <run-id>" in line
    assert "To review a saved plan instead" in line


def _option_help_line(option: str) -> str:
    """The `diff --help` row for `option`, flattened.

    Row-scoped rather than whole-output: "`--from-plan` carries a metavar" is a claim about
    its own row, and a whole-output search would be satisfied by any other option's metavar.
    """
    rows = _help_text("diff", wide=True).splitlines()
    matching = [row for row in rows if row.lstrip("│ ").startswith(option)]
    assert len(matching) == 1, f"expected exactly one help row for {option}, got {len(matching)}"
    return _flat(matching[0].strip("│ "))


def test_from_plan_is_documented_as_taking_a_run_identifier_and_not_as_a_flag() -> None:
    """AD057: the option's value *is* the run identifier, so the help must show a value."""
    from_plan_row = _option_help_line("--from-plan")
    detail_row = _option_help_line("--detail")

    assert "TEXT" in from_plan_row
    # Paired with a real flag, so "every row says TEXT" cannot satisfy the assertion above.
    assert "TEXT" not in detail_row
    assert "for this run id" in from_plan_row


# ======================================================================================
# T089 — the next-action obligation across the taxonomy (AD059, AD071, AD073, AD082)
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
    "source_peer_absent": lambda: SourcePeerUnresolvedError.absent("Peer 'p' is absent from the source store."),
    "source_peer_ambiguous": lambda: SourcePeerUnresolvedError.ambiguous("Peer 'p' resolved in two kinds."),
    "unsupported_action": lambda: UnsupportedOperationActionError("Operation 'o' declares action 'purge'."),
    "duplicate_operation_id": lambda: DuplicateOperationIdError("Two operations share identifier 'o'."),
    "unserializable_payload": lambda: UnserializablePayloadValueError("Field 'f' of kind 'K' holds a set."),
    "peer_not_found": lambda: PeerNotFoundError("Peer 'p' of kind 'K' matches no destination object."),
    "peer_ambiguous": lambda: PeerAmbiguousError("Peer 'p' of kind 'K' matches 2 destination objects."),
    "unaccounted_identity_component": lambda: UnaccountedIdentityComponentError("Kind 'K' omits component 'c'."),
    "unkeyed_write_refused": lambda: UnkeyedWriteRefusedError("The rendered mutation for 'K' carries no key."),
    "plan_verification": lambda: PlanVerificationError("The plan artifact of run 'r' cannot be applied."),
    "operation_apply_failed": lambda: OperationApplyFailedError(
        "Applying operation 'o' failed.", apply_record=ApplyRecord()
    ),
    "apply_record_invariant": lambda: ApplyRecordInvariantError(
        "The apply record does not account for the plan.", apply_record=ApplyRecord()
    ),
}


@pytest.mark.parametrize("build", list(TAXONOMY_CASES.values()), ids=list(TAXONOMY_CASES))
def test_every_taxonomy_entry_names_a_next_action(build: Callable[[], PlanArtifactError]) -> None:
    """AD059 over the declared taxonomy, message included.

    The containment half matters as much as the presence half: the CLI renders `str(exc)`
    (`print_error_and_abort(str(exc))`), so a `next_action` attribute the message did not
    carry would satisfy a presence-only assertion and still reach the operator as a dead end.
    """
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
    """AD082: one class, two conditions, two remedies.

    The absent-case remedy is *wrong* for the ambiguous case — nothing is missing there, the
    same unique-id resolved in two buckets — so routing an operator at a condition they do
    not have is the dead end AD059 exists to remove.
    """
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


def test_the_unrecognized_action_refusal_lists_the_recognized_actions(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Enumeration 4."""
    _store(tmp_path, [operation_record(action="purge", identity={"name": "prod"})])

    message = _failed_review(caplog, "--from-plan", RUN_ID)

    assert f"Recognized actions: {', '.join(ACTIONS)}." in message


# ======================================================================================
# T065 — the apply path (SC-004, SC-007, SC-011, SC-015, SC-018, AD055, AD062, AD069)
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

    # `None` — no captured binding — skips FIX-005's destination comparison, so every case
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
    """A destination that accepts the first operation and rejects the next (AD027)."""

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        if self.writes:
            msg = f"the destination rejected {operation.operation_id!r}"
            raise RuntimeError(msg)
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
    from its own instance with no merge (`infrahub_sync/cache/sidecars.py:88-90`), so the
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
    """Every refusal: named cause, its next action, zero writes, and `failed` on disk.

    The zero-writes and run-state halves of SC-004, SC-011, SC-015 and SC-018 live here
    rather than with the Phase C unit tests, because no apply exists in that phase and a
    verifier's return value cannot evidence a destination that was never touched.

    The recorded fields are read back **as present and empty** rather than checked for
    absence: "nothing was applied" must be readable from the run, not inferred from a missing
    key (AD062).

    The message is read from the **operator-facing channel** rather than from the exception,
    because how a designed refusal arrives is part of what is being asserted: each of these
    is a decision the tool made on purpose and names its own remedy for, so it reaches the
    operator as that one line and not as a stack trace out of `apply_plan` (AD059).
    """
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
    """FIX-005 (spec 002): the plan is bound to its effective destination, and a mismatch refuses.

    The manifest records the resolved endpoint the plan was computed against; the live
    destination exposes a different one, so the apply refuses before any write, names both
    values, and names the override — the same operator-facing channel and run-state
    discipline as every other designed refusal above (AD059, AD062).
    """
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
    """FIX-005's override: an explicit flag for the deliberate cross-environment apply."""
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
    """FIX-005's absent-field skip: plans older than the field are not refused."""
    _appliable_run(tmp_path)
    destination_double.destination_binding = LIVE_BINDING

    result = _run_apply(destination_double)

    assert result.exit_code == 0, result.output
    assert len(destination_double.writes) == len(APPLY_PLAN)
    assert _run_json(tmp_path)["status"] == "applied"


def test_a_v1_plan_is_refused_before_anything_is_constructed(
    tmp_path: Path, destination_double: RecordingDestination
) -> None:
    """SC-011, under AD026's rule that this refusal creates nothing.

    The run-state half of the other refusals deliberately does **not** apply here: the
    contract's `apply` table requires this case to create **no run directory**, so there is
    no sidecar to record `failed` in — and a test that demanded one would be demanding the
    directory AD026 forbids.
    """
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
    """AD026, AD059 and AD073 on the apply path, guarded exactly as review's are.

    Both verdicts come from the same functions the review path reaches, so the enumeration
    and the next action cannot drift between the two commands an operator meets them from.
    """
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
    """The real assembly seam constructs the destination only.

    No seam is patched here: the command runs through the real `PlanApplier.open_existing`,
    and `import_adapter` **raises if asked for the source** while yielding the recording
    destination — so this apply succeeding is the proof that a host with destination
    credentials but no source dependency or token can apply a reviewed plan. The shared
    diff/sync factory imports and constructs both adapters, so routing apply through it
    fails this case before verification is even reached.
    """
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
    """The stored run's sidecars are immutable extraction provenance.

    Asserted through **both** outcomes — a designed refusal and a successful apply — because
    the clobbering happened at assembly time, before either outcome was decided: the shared
    diff/sync factory recomputed `schema-sub-hash.txt` from the live destination and wrote
    it into the stored run before anything compared it, erasing the recorded provenance a
    stored-vs-live comparison would need.
    """
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
    """SC-007's **local half** and AD055 on the CLI path — and AD069's merge, from `run.json`.

    Scoped, because SC-007 is evidenced against a live destination: object counts before and
    after, and the direct assertion that each delete's target object is still present. Neither
    is expressible against the in-memory double below. What this case carries is the rest of the
    criterion's evidence list — the recorded run state, the recorded skipped-delete count and
    identifiers, the closure of applied plus skipped over the plan, the warning at a level
    `--quiet` does not suppress, and the command's own completion line. The live half is
    `tests/integration/test_saved_plan_apply_integration.py`.

    This is the case that would have caught the collision. `apply_plan` returns the record
    and writes no run file; `RunFile.save()` writes the whole payload from an instance whose
    `summary` is the empty one the command built, and it runs *after* `apply_plan` returns.
    A CLI that saved without merging would leave `applied_operations: []` and
    `skipped_delete_count: 0` on a run that applied two operations and skipped one — and
    every other assertion in this test would still pass. So the three keys are read back
    **from the file, by name**, and a later relocation of any of them trips here.

    The exit code is asserted at the CLI because a delete-bearing apply must not translate a
    skipped delete into a non-zero exit, and an in-process assertion cannot see an exit code.
    """
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
    """T098 / SC-007: the command's own completion line is required evidence, so it must arrive.

    `--quiet` is the invocation this clause is about. It floors the package logger at
    `logging.WARNING`, so a completion line emitted at `INFO` satisfies every prose
    description of the obligation — "the command's own completion line naming the skipped
    count" — and is then dropped for exactly the scripted runs where it is the only signal the
    operator gets. The level is asserted through the real `--quiet` invocation rather than by
    reading the call site, because the level and the floor only interact at run time (AD089).
    """
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
    """The negative control for the case above (AD089).

    The level rises with the count it reports and not with the command: an apply that skipped
    nothing has nothing to disclose, and raising *that* line too would make `--quiet` noisy on
    every clean apply. Without this case, "emit the completion line at WARNING" could be
    satisfied by shouting on every run.
    """
    _appliable_run(tmp_path)
    destination = RecordingDestination()

    with caplog.at_level(logging.DEBUG, logger="infrahub_sync"):
        result = _run_apply(destination, quiet=True)

    assert result.exit_code == 0, result.output
    assert [entry.getMessage() for entry in caplog.records if entry.name == "infrahub_sync.cli"] == []


def test_a_clean_apply_records_the_applied_operations_it_actually_performed(tmp_path: Path) -> None:
    """The merge again, without a delete in the plan, so the two halves are separable.

    A CLI that merged only on the delete-bearing path would pass the case above and fail
    here, which is what keeps AD069 a property of the command rather than of one branch.
    """
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
    """FR-025's last-applied pointer survives a partial apply (AD027, AD069).

    The rejection carries the partial record, and the CLI merges *that* before recording
    `failed`. Without the merge the run would say nothing was applied while the first
    operation stayed written at the destination — the one state an operator cannot recover
    from by reading the run.

    The rejection is also a member of the taxonomy, so it reaches the operator as its own
    message — naming what stays written, and what to do next — rather than as a traceback
    out of the apply loop (AD059).
    """
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
    """T100 / SC-016: the refusal must **fail the run**, not merely leave the write surface.

    The write-surface half — the raise, and zero dispatch — is asserted against the adapter in
    `tests/adapters/test_infrahub_planned_write.py`. Neither half implies the other: an engine
    that swallowed `PeerNotFoundError` and carried on, or a command that recorded `applied`
    anyway, would satisfy the adapter case in full while leaving an operator with a run that
    says it succeeded and a destination missing the object. Unlike the multi-match arm there is
    no live counterpart, so the run-state half is asserted here, offline.
    """
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
    """Ctrl-C between two operations: the run says what was written, and still exits 130.

    An interrupt is the one stop an operator causes deliberately on a long apply, and it is
    the case where "nothing was applied must be readable from the run" is least inferable —
    the writes have landed and the operator has no return value to read. A command whose
    guards only catch `Exception` leaves the sidecar exactly as it was saved before the loop:
    `running`, with an empty summary, on a run that wrote to the destination (AD062).

    The interrupt itself is asserted to survive, because recording it must not swallow it:
    the exit code stays 130 and the exception is still a `KeyboardInterrupt`.
    """
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


def test_a_broken_apply_invariant_records_what_was_written_not_an_empty_record(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """AD062's safety net must not zero the record it exists to protect.

    The invariant is checked *after* the loop wrote every non-delete operation, so a command
    that merged an empty record here would report a run that wrote everything as having
    applied nothing — and an operator reading that would re-apply against a populated
    destination. The manifest's count is inflated because that is the only clause of the
    invariant a well-formed artifact can violate.
    """
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
