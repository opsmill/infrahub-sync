"""The review surface (FR-006, FR-007, FR-017, FR-022, FR-029, SC-009).

`read_saved_plan` in-process — the counts, the per-object field set, the `kind` filter, the
empty plan, the plan that would fail verification and is rendered anyway, AD056's two
disclosure fields — plus SC-009's in-process half run in a **subprocess** against a stored
artifact with neither source nor destination reachable, which is what evidences that the
producing process need not be alive and that no adapter is constructed.

Two pairings here are deliberate and **must not be split**: a declared kind with no
operations returns `[]` while an undeclared kind raises (a test that raised for both would
pass against the behaviour AD058 corrected), and a failing **checksum** renders while an
**unrecognized action** refuses (which stops "review never refuses to show" being read as
absolute).
"""

from __future__ import annotations

import json
import os
import subprocess  # noqa: S404 — a new process is the point: SC-009 measures reading after the producer exited
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync import SchemaMappingModel, SyncAdapter, SyncConfig
from infrahub_sync.cache.parquet_io import write_resource_side
from infrahub_sync.plan.checksum import source_snapshot_records
from infrahub_sync.plan.errors import UnknownPlanKindError, UnsupportedOperationActionError
from infrahub_sync.plan.models import PlannedOperation
from infrahub_sync.plan.review import read_saved_plan
from tests.plan.artifact_fixtures import (
    OTHER_RUN_ID,
    RUN_ID,
    SYNC_NAME,
    operation_record,
    tamper_with_operations,
    tamperable_operation,
    write_artifact,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]

# The minimum per-object field set FR-006 and AD020 fix: the review-side source SC-005
# compares against the apply result.
DETAIL_FIELDS = ("operation_id", "action", "kind", "identity")

# Adapter credentials and endpoints. Unset in the subprocess, so "no adapter is reachable" is
# a property of the environment the review ran in and not only of the code path taken.
ADAPTER_ENV_VARS = (
    "INFRAHUB_ADDRESS",
    "INFRAHUB_API_TOKEN",
    "INFRAHUB_TOKEN",
    "NETBOX_ADDRESS",
    "NETBOX_TOKEN",
    "NAUTOBOT_ADDRESS",
    "NAUTOBOT_TOKEN",
)

# Made unimportable in the subprocess. `infrahub_sync.adapters.utils` is deliberately absent:
# it is a pure mapping helper the package imports at module scope, not a connection surface,
# so blocking it would prove nothing and stop `infrahub_sync` importing at all.
BLOCKED_MODULES = (
    "infrahub_sync.adapters.infrahub",
    "infrahub_sync.adapters.netbox",
    "infrahub_sync.adapters.nautobot",
    "infrahub_sdk",
    "pynetbox",
    "pynautobot",
)

MIXED_PLAN: tuple[dict[str, Any], ...] = (
    operation_record(identity={"name": "prod"}),
    operation_record(identity={"name": "staging"}),
    operation_record(action="update", kind="LocationSite", identity={"name": "dc1"}, tier=1),
    operation_record(action="delete", identity={"name": "retired"}),
)


def _config(*kinds: str) -> SyncConfig:
    """A configuration declaring `kinds`, which is all review uses `config` for (FR-006)."""
    return SyncConfig(
        name=SYNC_NAME,
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[SchemaMappingModel(name=kind) for kind in kinds],
    )


def _store(
    tmp_path: Path,
    records: Sequence[Mapping[str, Any]] = MIXED_PLAN,
    *,
    run_id: str = RUN_ID,
    deletes_computed: bool = True,
) -> Path:
    """Store an artifact where `read_saved_plan` looks for it, and return its run directory.

    `cache_root_for` honours `INFRAHUB_SYNC_CACHE_DIR`, which the autouse fixture below points
    at `tmp_path`, so the run lands at `tmp_path/<sync name>/<run id>` — the real layout,
    reached through the real path resolver rather than by handing the reader a directory.
    """
    directory = tmp_path / SYNC_NAME / run_id
    directory.mkdir(parents=True, exist_ok=True)
    write_artifact(directory, list(records), run_id=run_id, deletes_computed=deletes_computed)
    return directory


@pytest.fixture(autouse=True)
def _isolated_cache_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep every case inside `tmp_path`, so no test reads a real developer cache."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))


def _tree(directory: Path) -> set[str]:
    """Every path under `directory`, relative and POSIX, for a before/after comparison."""
    return {path.relative_to(directory).as_posix() for path in directory.rglob("*")}


# ======================================================================================
# The summary
# ======================================================================================


def test_the_summary_reports_a_count_per_action(tmp_path: Path) -> None:
    """A count per action, which is half of what FR-006's summary depth presents."""
    _store(tmp_path)

    summary = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID).summary()

    assert summary.by_action == {"create": 2, "delete": 1, "update": 1}


def test_the_summary_reports_a_count_per_kind(tmp_path: Path) -> None:
    """And a count per destination kind, which is the other half."""
    _store(tmp_path)

    summary = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID).summary()

    assert summary.by_kind == {"BuiltinTag": 3, "LocationSite": 1}


def test_the_summary_total_is_the_operation_count(tmp_path: Path) -> None:
    """The total agrees with both breakdowns, so a miscount cannot hide in one of them."""
    _store(tmp_path)

    summary = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID).summary()

    assert summary.total == len(MIXED_PLAN)
    assert sum(summary.by_action.values()) == summary.total
    assert sum(summary.by_kind.values()) == summary.total


# ======================================================================================
# Per-object detail
# ======================================================================================


def test_per_object_detail_carries_the_identifier_action_kind_and_identity(tmp_path: Path) -> None:
    """The AD020 minimum field set, present and populated on every operation."""
    _store(tmp_path)

    operations = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID).operations()

    assert len(operations) == len(MIXED_PLAN)
    for operation in operations:
        for name in DETAIL_FIELDS:
            assert getattr(operation, name), f"{name} is empty on {operation.operation_id!r}"


def test_the_detail_record_declares_the_minimum_field_set() -> None:
    """Asserted over the model's fields too, so a later rename fails here (AD020)."""
    assert set(DETAIL_FIELDS) <= set(PlannedOperation.model_fields)


def test_the_reviewed_identifiers_are_the_artifacts_own(tmp_path: Path) -> None:
    """The identifiers shown are the recorded ones, not re-derived on the way out (SC-005)."""
    _store(tmp_path)

    operations = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID).operations()

    assert {operation.operation_id for operation in operations} == {record["operation_id"] for record in MIXED_PLAN}


def test_the_reviewed_identifiers_are_returned_in_stored_order(tmp_path: Path) -> None:
    """T056/SC-005's review half: the review side is an ordered sequence, not a set."""
    _store(tmp_path)
    stored_order = [str(record["operation_id"]) for record in MIXED_PLAN]
    assert stored_order != sorted(stored_order), "the fixture must not already be in sorted order"

    operations = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID).operations()

    assert [operation.operation_id for operation in operations] == stored_order


# ======================================================================================
# The `kind` filter, and AD058's split
# ======================================================================================


def test_kind_narrows_the_operations_returned(tmp_path: Path) -> None:
    """The filter is a narrowing, not a re-read: everything returned is of that kind."""
    _store(tmp_path)

    operations = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID).operations(kind="LocationSite")

    assert [operation.kind for operation in operations] == ["LocationSite"]


def test_a_declared_kind_with_no_operations_returns_an_empty_list(tmp_path: Path) -> None:
    """It does **not** raise (AD058)."""
    _store(tmp_path)
    config = _config("BuiltinTag", "LocationSite", "BuiltinRole")

    plan = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, config=config)

    assert plan.operations(kind="BuiltinRole") == []


def test_an_undeclared_kind_raises_naming_the_kinds_the_plan_holds(tmp_path: Path) -> None:
    """The other half of AD058's split — and it must be the *other* half."""
    _store(tmp_path)
    config = _config("BuiltinTag", "LocationSite", "BuiltinRole")

    with pytest.raises(UnknownPlanKindError) as raised:
        read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, config=config).operations(kind="NotDeclared")

    message = str(raised.value)
    assert "NotDeclared" in message
    assert "BuiltinTag" in message
    assert "LocationSite" in message
    assert raised.value.next_action


def test_a_kind_the_plan_holds_but_the_configuration_omits_raises(tmp_path: Path) -> None:
    """The case that distinguishes the specified guard from a conjunction (T104)."""
    _store(tmp_path)
    # `MIXED_PLAN` holds `BuiltinTag` and `LocationSite`; the configuration declares only the
    # first, so `LocationSite` is held-but-undeclared.
    config = _config("BuiltinTag")
    plan = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, config=config)
    assert any(operation.kind == "LocationSite" for operation in plan.operations()), (
        "Precondition: the plan must hold operations for the kind, or this is the intersection case again."
    )

    with pytest.raises(UnknownPlanKindError) as raised:
        plan.operations(kind="LocationSite")

    assert "LocationSite" in str(raised.value)
    assert raised.value.next_action


def test_with_no_configuration_the_plans_own_kinds_are_the_vocabulary(tmp_path: Path) -> None:
    """`config` is optional, so declaration is unknowable without it (FR-029)."""
    _store(tmp_path)
    plan = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID)

    assert plan.operations(kind="BuiltinTag")
    with pytest.raises(UnknownPlanKindError):
        plan.operations(kind="BuiltinRole")


# ======================================================================================
# The empty plan
# ======================================================================================


def test_a_zero_operation_plan_summarizes_as_having_no_operations(tmp_path: Path) -> None:
    """The counts state emptiness rather than being absent, so the renderer can say so."""
    _store(tmp_path, [])

    plan = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID)
    summary = plan.summary()

    assert summary.total == 0
    assert summary.by_action == {}
    assert summary.by_kind == {}
    assert plan.operations() == []


def test_a_zero_operation_plan_still_carries_its_manifest(tmp_path: Path) -> None:
    """Empty is a complete artifact, so review reads it like any other (FR-022)."""
    _store(tmp_path, [])

    plan = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID)

    assert plan.manifest.operations_count == 0
    assert plan.checksum_ok


# ======================================================================================
# AD056's two disclosure fields
# ======================================================================================


def test_the_summary_discloses_that_deletes_were_computed_and_how_many(tmp_path: Path) -> None:
    """The `true` / non-zero fixture: computed, and one delete that will not be executed."""
    _store(tmp_path, MIXED_PLAN, deletes_computed=True)

    summary = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID).summary()

    assert summary.delete_operations_computed is True
    assert summary.deletes_not_executed == 1


def test_the_summary_discloses_that_deletes_were_not_computed(tmp_path: Path) -> None:
    """The `false` / zero fixture (AD056)."""
    _store(tmp_path, [operation_record()], deletes_computed=False)

    summary = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID).summary()

    assert summary.delete_operations_computed is False
    assert summary.deletes_not_executed == 0


def test_the_disclosure_matches_the_manifest_rather_than_the_operation_set(tmp_path: Path) -> None:
    """Read up from the manifest: a plan with no deletes may still have computed them."""
    _store(tmp_path, [operation_record()], deletes_computed=True)

    summary = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID).summary()

    assert summary.delete_operations_computed is True
    assert summary.deletes_not_executed == 0


# ======================================================================================
# A verification failure renders; an unrecognized action does not
# ======================================================================================


def test_a_plan_whose_checksum_fails_is_still_rendered_with_a_note(tmp_path: Path) -> None:
    """Review renders a plan that would fail apply verification (AD031)."""
    directory = _store(tmp_path, [tamperable_operation()])
    tamper_with_operations(directory)

    plan = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID)

    assert plan.checksum_ok is False
    assert plan.verification_notes
    assert any("checksum" in note for note in plan.verification_notes)
    assert plan.summary().total == 1
    assert plan.operations()


def test_a_verification_failure_renders_but_an_unrecognized_action_refuses(tmp_path: Path) -> None:
    """The pairing AD055 and AD031 draw between them, asserted in one place."""
    tampered = _store(tmp_path, [tamperable_operation()], run_id=RUN_ID)
    tamper_with_operations(tampered)
    renders = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID)
    assert renders.checksum_ok is False

    _store(tmp_path, [operation_record(action="purge")], run_id=OTHER_RUN_ID)
    with pytest.raises(UnsupportedOperationActionError):
        read_saved_plan(sync_name=SYNC_NAME, run_id=OTHER_RUN_ID)


# ======================================================================================
# Review writes nothing
# ======================================================================================


def test_no_review_call_writes_a_run_file(tmp_path: Path) -> None:
    """No `run.json`, and nothing else either: the run directory is untouched."""
    directory = _store(tmp_path)
    before = _tree(directory)

    plan = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID)
    plan.summary()
    plan.operations()
    plan.operations(kind="BuiltinTag")

    assert _tree(directory) == before
    assert "run.json" not in before


def test_review_writes_nothing_even_when_the_plan_fails_verification(tmp_path: Path) -> None:
    """The rendering path for a failing plan is still a read-only path (AD031)."""
    directory = _store(tmp_path, [tamperable_operation()])
    tamper_with_operations(directory)
    before = _tree(directory)

    read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID).summary()

    assert _tree(directory) == before


# ======================================================================================
# FR-010's source-snapshot binding, on the review path
# ======================================================================================

_EXTRACT_TS = datetime(2026, 7, 26, 18, 4, 11, tzinfo=timezone.utc)

SNAPSHOT_ROWS: list[dict[str, Any]] = [
    {"name": "prod", "description": "production"},
    {"name": "staging", "description": "staging"},
]


def _store_with_snapshot(
    tmp_path: Path,
    rows: Sequence[Mapping[str, Any]] = SNAPSHOT_ROWS,
    *,
    run_id: str = RUN_ID,
) -> Path:
    """Store an artifact bound to a real source snapshot, and return its run directory.

    The snapshot is written through the engine's own writer, because the binding digests the
    table's **logical rows** with `_extract_ts` dropped (AD037): a digest over a hand-built
    table missing the injected columns would not be the digest either path computes.
    """
    directory = tmp_path / SYNC_NAME / run_id
    directory.mkdir(parents=True, exist_ok=True)
    write_resource_side(
        run_dir=directory,
        side="A",
        resource="BuiltinTag",
        rows=[dict(row) for row in rows],
        source_ids=[str(row["name"]) for row in rows],
        extract_ts=_EXTRACT_TS,
        tombstones=None,
    )
    write_artifact(
        directory,
        [operation_record()],
        run_id=run_id,
        source_snapshot=source_snapshot_records(directory),
    )
    return directory


def _snapshot_path(run_directory: Path) -> Path:
    return run_directory / "A" / "BuiltinTag.parquet"


def test_a_plan_bound_to_an_intact_snapshot_carries_no_note(tmp_path: Path) -> None:
    """The precondition. Without it the two cases below could pass vacuously."""
    _store_with_snapshot(tmp_path)

    plan = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID)

    assert plan.checksum_ok is True
    assert plan.verification_notes == []


def test_a_review_of_a_run_whose_snapshot_is_absent_notes_the_tear(tmp_path: Path) -> None:
    """FR-010 on the review path: the torn binding is named, not silently rendered clean."""
    directory = _store_with_snapshot(tmp_path)
    _snapshot_path(directory).unlink()

    plan = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID)

    assert plan.checksum_ok is True, "the checksum is intact, so the note is the only signal"
    assert plan.verification_notes, "a run whose recorded snapshot is gone rendered with no note"
    note = " ".join(plan.verification_notes)
    # Which part is torn, expected and found, and the next action (FR-010).
    assert "A/BuiltinTag.parquet" in note
    assert "absent" in note
    assert "Re-run `diff`" in note
    # And it is still rendered rather than refused (AD031).
    assert plan.summary().total == 1
    assert plan.operations()


def test_a_review_of_a_run_whose_snapshot_was_truncated_notes_both_values(tmp_path: Path) -> None:
    """The truncation arm, whose expected and found values are both readable (FR-010)."""
    directory = _store_with_snapshot(tmp_path)
    write_resource_side(
        run_dir=directory,
        side="A",
        resource="BuiltinTag",
        rows=[dict(SNAPSHOT_ROWS[0])],
        source_ids=[str(SNAPSHOT_ROWS[0]["name"])],
        extract_ts=_EXTRACT_TS,
        tombstones=None,
    )

    plan = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID)

    assert plan.checksum_ok is True
    note = " ".join(plan.verification_notes)
    assert "A/BuiltinTag.parquet" in note
    assert f"{len(SNAPSHOT_ROWS)} row(s)" in note, f"the recorded row count is missing from: {note}"
    assert "1 row(s)" in note, f"the found row count is missing from: {note}"
    assert "Re-run `diff`" in note


def test_the_review_note_and_the_apply_refusal_report_the_same_binding(tmp_path: Path) -> None:
    """One check, two paths (FR-010)."""
    directory = _store_with_snapshot(tmp_path)
    _snapshot_path(directory).unlink()
    from infrahub_sync.plan.reader import read_plan_artifact_bytes
    from infrahub_sync.plan.verify import verify_plan
    from tests.plan.artifact_fixtures import CONFIG_VERSION

    failures = verify_plan(artifact=read_plan_artifact_bytes(directory), run_id=RUN_ID, config_version=CONFIG_VERSION)
    plan = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID)

    assert [failure.check for failure in failures] == ["source_snapshot"]
    assert len(plan.verification_notes) == 1
    reported = plan.verification_notes[0]
    assert str(failures[0].expected) in reported
    assert str(failures[0].found) in reported


def test_a_review_writes_nothing_while_reading_the_snapshot_binding(tmp_path: Path) -> None:
    """The new read is a read: recomputing the digest creates nothing (FR-008, AD021)."""
    directory = _store_with_snapshot(tmp_path)
    before = _tree(directory)

    read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID).summary()

    assert _tree(directory) == before


# ======================================================================================
# SC-009's in-process half, in a new process with nothing reachable
# ======================================================================================

READER_SCRIPT = '''\
"""Read a stored plan with every adapter surface made unimportable.

Run by T027 as a subprocess. Any adapter construction — or any import of an adapter module or
client library — raises here, so a successful run is evidence that the review path constructs
no adapter and reaches no source or destination.
"""

import json
import os
import sys

BLOCKED = tuple(json.loads(sys.argv[3]))
ADAPTER_ENV_VARS = tuple(json.loads(sys.argv[4]))


class RefuseAdapterImports:
    """A meta-path finder that raises for any blocked module, before it can be located."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(BLOCKED):
            raise ImportError(f"adapter import refused in this process: {fullname}")
        return None


sys.meta_path.insert(0, RefuseAdapterImports())

from infrahub_sync.plan import read_saved_plan

plan = read_saved_plan(sync_name=sys.argv[1], run_id=sys.argv[2])

try:
    __import__("infrahub_sync.adapters.infrahub")
except ImportError:
    guard_holds = True
else:  # pragma: no cover - only reached if the guard is ineffective
    guard_holds = False

print(
    json.dumps(
        {
            "summary": plan.summary().model_dump(),
            "detail": [
                {
                    "operation_id": operation.operation_id,
                    "action": operation.action,
                    "kind": operation.kind,
                    "identity": operation.identity,
                }
                for operation in plan.operations()
            ],
            "checksum_ok": plan.checksum_ok,
            "adapter_env_present": {name: name in os.environ for name in ADAPTER_ENV_VARS},
            "guard_holds": guard_holds,
        }
    )
)
'''


def _review_in_a_new_process(tmp_path: Path) -> dict[str, Any]:
    """Read the stored plan in a subprocess and return what it reported.

    The producing process is gone by construction — this one never wrote the artifact — and
    the adapter environment variables are stripped from the child's environment, so neither
    source nor destination is reachable from it.
    """
    script = tmp_path / "review_in_new_process.py"
    script.write_text(READER_SCRIPT, encoding="utf-8")

    environment = {name: value for name, value in os.environ.items() if name not in ADAPTER_ENV_VARS}
    environment["INFRAHUB_SYNC_CACHE_DIR"] = str(tmp_path)
    environment["PYTHONPATH"] = str(REPO_ROOT)

    completed = subprocess.run(  # noqa: S603 — a fixed argument vector run through sys.executable
        [
            sys.executable,
            str(script),
            SYNC_NAME,
            RUN_ID,
            json.dumps(list(BLOCKED_MODULES)),
            json.dumps(list(ADAPTER_ENV_VARS)),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(tmp_path),
    )
    assert completed.returncode == 0, f"the subprocess failed:\n{completed.stderr}"
    return json.loads(completed.stdout)


def test_a_summary_is_produced_in_a_new_process_with_nothing_reachable(tmp_path: Path) -> None:
    """SC-009, summary depth: the producing process need not be alive (FR-007)."""
    _store(tmp_path)

    reported = _review_in_a_new_process(tmp_path)

    assert reported["summary"]["by_action"] == {"create": 2, "delete": 1, "update": 1}
    assert reported["summary"]["by_kind"] == {"BuiltinTag": 3, "LocationSite": 1}
    assert reported["summary"]["total"] == len(MIXED_PLAN)


def test_per_object_detail_is_produced_in_a_new_process_with_nothing_reachable(tmp_path: Path) -> None:
    """SC-009, detail depth: one record per operation, each carrying the AD020 field set."""
    _store(tmp_path)

    reported = _review_in_a_new_process(tmp_path)

    assert len(reported["detail"]) == len(MIXED_PLAN)
    for record in reported["detail"]:
        assert set(DETAIL_FIELDS) <= set(record)
        for name in DETAIL_FIELDS:
            assert record[name]
    assert {record["operation_id"] for record in reported["detail"]} == {
        record["operation_id"] for record in MIXED_PLAN
    }


def test_the_new_process_had_no_adapter_environment_and_no_importable_adapter(tmp_path: Path) -> None:
    """The precondition, reported by the child rather than assumed by the parent.

    Both halves matter. Unset environment variables alone would not prove no adapter was
    constructed, and an import guard whose finder never fired would prove nothing either — so
    the child also reports that a blocked import really does raise.
    """
    _store(tmp_path)

    reported = _review_in_a_new_process(tmp_path)

    assert reported["adapter_env_present"] == dict.fromkeys(ADAPTER_ENV_VARS, False)
    assert reported["guard_holds"] is True


def test_the_new_process_reads_the_not_computed_disclosure(tmp_path: Path) -> None:
    """AD056's fields cross the process boundary, on the plan SC-009 names."""
    _store(tmp_path, [operation_record()], deletes_computed=False)

    reported = _review_in_a_new_process(tmp_path)

    assert reported["summary"]["delete_operations_computed"] is False
    assert reported["summary"]["deletes_not_executed"] == 0
    assert reported["summary"]["total"] == 1
    assert reported["checksum_ok"] is True


# ======================================================================================
# FIX-003 (spec 002) — a byte-corrupt snapshot renders a note, never a traceback
# ======================================================================================


def test_a_review_of_a_run_whose_snapshot_bytes_are_corrupt_notes_it(tmp_path: Path) -> None:
    """Garbage bytes at the recorded snapshot path are a rendered note (AD031, FIX-003)."""
    directory = _store_with_snapshot(tmp_path)
    _snapshot_path(directory).write_bytes(b"these bytes are not a Parquet table")

    plan = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID)

    assert plan.checksum_ok is True, "the checksum is intact, so the note is the only signal"
    assert plan.verification_notes, "a run whose snapshot bytes are corrupt rendered with no note"
    note = " ".join(plan.verification_notes)
    assert "A/BuiltinTag.parquet" in note
    assert "not a readable Parquet snapshot" in note
    assert "Re-run `diff`" in note
    # And it is still rendered rather than refused (AD031).
    assert plan.summary().total == 1
    assert plan.operations()
