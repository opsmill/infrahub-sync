"""Benchmark contracts use mapped kinds and never require live services."""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import signal
import subprocess  # noqa: S404 -- real benchmark subprocess regression
import sys
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from invoke import Context

from development.bench.records import (
    ResultRecord,
    count_delta,
    expected_actions,
    expected_counts,
    expected_source_counts,
    mapped_kinds,
    medians,
    parse_v2_summary,
    stage_seconds,
    validate_result,
    validate_source_counts,
)
from development.bench.runtime import BenchmarkError, docker_memory_mib, measured_process, output
from development.netbox.datasets.change_netbox import expected_text, plan_changes
from tasks import bench


@pytest.fixture(autouse=True)
def isolated_runner_lock(monkeypatch, tmp_path) -> None:
    """Keep unit callers separate from benchmarks running on the host."""
    monkeypatch.setattr(bench, "RUNNER_LOCK", tmp_path / "shared-runner.lock")


@pytest.fixture
def mapping() -> dict[str, list[str]]:
    package = yaml.safe_load(bench.netbox.SHIPPED_PACKAGE.read_text(encoding="utf-8"))
    return mapped_kinds(package["configuration"])


@pytest.mark.parametrize(("tier", "devices"), [("S", 40), ("M", 800), ("L", 7000)])
def test_mapped_counts_include_interface_splits_and_two_vrf_projections(mapping, tier, devices) -> None:
    counts = expected_counts(tier, mapping)
    assert counts["InterfacePhysical"] == devices * 6
    assert counts["InterfaceVirtual"] == counts["InterfaceLag"] == devices
    assert counts["IpamNamespace"] == counts["IpamVRF"] + 1
    assert len(counts) == 21
    assert "dcim/device-roles" not in mapping


def test_prefix_move_translates_to_delete_and_create(mapping) -> None:
    document = {
        "format_version": 1,
        "tier": "S",
        "changes": [
            {"kind": "ipam/prefixes", "action": "update", "identifier": "old", "fields": {"vrf": {"identifier": "new"}}}
        ],
    }
    actions = expected_actions(document, mapping)
    assert actions == {"create": {"IpamPrefix": 1}, "delete": {"IpamPrefix": 1}, "update": {}}


@pytest.mark.parametrize("tier", ["S", "M", "L"])
def test_real_change_file_maps_deterministically(mapping, tier) -> None:
    changes = plan_changes(tier)
    first = expected_actions(json.loads(expected_text(tier, changes)), mapping)
    assert first == expected_actions(json.loads(expected_text(tier, plan_changes(tier))), mapping)
    assert sum(first["create"].values()) >= 1
    assert sum(first["delete"].values()) >= 1
    assert first["update"]["InterfaceVirtual"] >= 1
    assert sum(first["create"].values()) - sum(first["delete"].values()) == sum(
        c["action"] == "create" for c in changes
    ) - sum(c["action"] == "delete" for c in changes)


@pytest.mark.parametrize("status", ["ok", "failed", "timed_out"])
def test_jsonl_contract_and_invalid_times(tmp_path, status) -> None:
    record = ResultRecord(
        "v3",
        "3",
        "abc",
        "S",
        "cold",
        "full",
        1,
        status=status,
        wall_seconds=3,
        plan_seconds=1,
        apply_seconds=2,
        peak_rss_mb=4,
    )
    target = tmp_path / "results.jsonl"
    record.append(target)
    record.append(target)
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert {
        "line",
        "version",
        "commit",
        "tier",
        "scenario",
        "variant",
        "repetition",
        "status",
        "wall_seconds",
        "plan_seconds",
        "apply_seconds",
        "peak_rss_mb",
        "netbox_counts",
        "infrahub_counts",
        "machine",
        "harness_commit",
        "mapping_sha256",
        "builtin_deletes",
        "skipped_deletes",
    } <= rows[0].keys()
    assert rows[0]["wall_seconds"] == (3 if status == "ok" else None)
    assert rows[0]["plan_seconds"] == (1 if status == "ok" else None)
    assert rows[0]["apply_seconds"] == (2 if status == "ok" else None)


def test_warm_rejects_writes_even_when_counts_match(mapping) -> None:
    with pytest.raises(BenchmarkError, match="nonzero"):
        validate_result("S", "warm", expected_counts("S", mapping), {"update": {"DcimDevice": 1}}, mapping)


def test_cold_rejects_missing_kind(mapping) -> None:
    counts = expected_counts("S", mapping)
    counts.pop("IpamVLAN")
    with pytest.raises(BenchmarkError, match="counts differ"):
        validate_result("S", "cold", counts, {}, mapping)


def test_changed_requires_applied_actions_and_resulting_counts(mapping) -> None:
    actions = {"create": {"InterfacePhysical": 2}, "update": {"IpamIPAddress": 1}, "delete": {"InterfacePhysical": 1}}
    counts = expected_counts("S", mapping)
    counts["InterfacePhysical"] += 1
    validate_result("S", "changed", counts, actions, mapping, actions)
    with pytest.raises(BenchmarkError, match="actions differ"):
        validate_result("S", "changed", counts, count_delta(expected_counts("S", mapping), counts), mapping, actions)


@pytest.mark.parametrize(
    "damage", ["none", "missing-delete", "extra-delete", "wrong-kind", "create", "update", "executed-delete", "counts"]
)
def test_v3_changed_requires_exact_skipped_deletes_and_executed_writes(mapping, damage) -> None:
    expected = expected_actions(json.loads(expected_text("S", plan_changes("S"))), mapping)
    actions = {**expected, "delete": {}}
    skipped = expected["delete"].copy()
    counts = expected_counts("S", mapping)
    for kind, count in expected["create"].items():
        counts[kind] += count
    if damage == "missing-delete":
        skipped = {}
    elif damage == "extra-delete":
        skipped["InterfacePhysical"] = 2
    elif damage == "wrong-kind":
        skipped = {"IpamIPAddress": 1}
    elif damage in {"create", "update"}:
        actions[damage] = {}
    elif damage == "executed-delete":
        actions["delete"] = skipped.copy()
    elif damage == "counts":
        counts["InterfacePhysical"] -= 1
    if damage == "none":
        validate_result("S", "changed", counts, actions, mapping, expected, skipped_deletes=skipped)
    else:
        with pytest.raises(BenchmarkError, match="differ"):
            validate_result("S", "changed", counts, actions, mapping, expected, skipped_deletes=skipped)


def test_parse_summary_requires_complete_kinds() -> None:
    text = 'DcimDevice {"create": 0, "update": 2, "delete": 0}\nIpamVRF create=1 update=0 delete=0'
    assert parse_v2_summary(text, {"DcimDevice", "IpamVRF"}) == {
        "create": {"IpamVRF": 1},
        "update": {"DcimDevice": 2},
        "delete": {},
    }
    assert parse_v2_summary(text, {"DcimDevice", "IpamVRF", "BuiltinTag"}) is None


@pytest.mark.parametrize("complete", [False, True])
def test_v2_printed_action_statuses_require_a_successful_complete_run(complete) -> None:
    text = (
        "Beginning sync\nCreated successfully action=create model=DcimDevice status=success\n"
        "Updated successfully action=update model=IpamPrefix status=success\nSync complete\n"
    )
    if complete:
        text += "INFO | infrahub_sync.cli | Sync run run-id at directory\n"
    actions = parse_v2_summary(text, {"DcimDevice", "IpamPrefix"})
    assert actions == ({"create": {"DcimDevice": 1}, "update": {"IpamPrefix": 1}, "delete": {}} if complete else None)


def test_v2_explicit_finished_no_difference_summary_establishes_zero_actions() -> None:
    text = "No difference found. Nothing to sync\nINFO | infrahub_sync.cli | Sync run run-id at directory\n"
    assert parse_v2_summary(text, {"DcimDevice"}) == {"create": {}, "update": {}, "delete": {}}


def test_v2_unsuccessful_object_status_is_not_valid_action_evidence() -> None:
    text = (
        "Beginning sync\nUnable to create action=create model=DcimDevice status=failure\nSync complete\n"
        "INFO | infrahub_sync.cli | Sync run run-id at directory\n"
    )
    assert parse_v2_summary(text, {"DcimDevice"}) is None


def test_v2_unrecognized_action_output_never_establishes_zero_warm_writes() -> None:
    text = (
        'Beginning sync\n{"action":"update","model":"DcimDevice","status":"success"}\nSync complete\n'
        "INFO | infrahub_sync.cli | Sync run run-id at directory\n"
    )
    assert parse_v2_summary(text, {"DcimDevice"}) is None


@pytest.mark.parametrize(
    ("cache", "expected"),
    [({"inactive_file": 3 * 1024**2}, 7), ({"total_inactive_file": 4 * 1024**2}, 6), ({}, 10)],
)
def test_docker_memory_matches_stats_for_both_cgroups(cache, expected) -> None:
    assert docker_memory_mib({"memory_stats": {"usage": 10 * 1024**2, "stats": cache}}) == expected


def test_recorded_stage_durations() -> None:
    start = datetime.now(timezone.utc)
    assert stage_seconds(start, start + timedelta(seconds=2), start + timedelta(seconds=5)) == (2, 3)


def test_timeout_kills_v2_process_session(tmp_path) -> None:
    with pytest.raises(TimeoutError):
        measured_process([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path, {}, timeout=0.1)


def test_failure_output_never_exposes_token(tmp_path, capsys) -> None:
    with pytest.raises(BenchmarkError) as exc:
        measured_process(
            [
                sys.executable,
                "-c",
                "import sys; print('secret-token'); print('secret-token', file=sys.stderr); sys.exit(1)",
            ],
            tmp_path,
            {},
            timeout=5,
        )
    captured = capsys.readouterr()
    assert "secret-token" not in str(exc.value) + captured.out + captured.err


@pytest.mark.parametrize(
    ("line", "scenario", "variant"), [("v3", "cold", "parallel"), ("v2", "warm", "incremental"), ("v2", "cold", "full")]
)
def test_invalid_cell_options_fail_before_stack_mutation(line, scenario, variant) -> None:
    with pytest.raises(ValueError):
        bench.cell_options(line, "S", scenario, variant, 1, "")


@pytest.mark.parametrize("entrypoint", ["v2_environment", "run_cell"])
def test_benchmark_rejects_unsupported_python_before_setup(monkeypatch, entrypoint) -> None:
    monkeypatch.setattr(bench.sys, "version_info", (3, 10, 0))
    if entrypoint == "v2_environment":
        with pytest.raises(BenchmarkError, match=r"requires Python 3\.11 to 3\.13"):
            bench.v2_environment("2.0.1")
    else:
        with pytest.raises(BenchmarkError, match=r"requires Python 3\.11 to 3\.13"):
            bench.run_cell.body(Context())


def test_medians_exclude_invalid_samples(tmp_path) -> None:
    target = tmp_path / "results.jsonl"
    for status, seconds in [("ok", 2), ("ok", 4), ("failed", 100)]:
        ResultRecord(
            "v3", "3", "abc", "S", "cold", "full", 1, status=status, wall_seconds=seconds, peak_rss_mb=1
        ).append(target)
    assert medians(target)[0]["v3_seconds"] == 3
    assert medians(target)[0]["v2_seconds"] is None


@pytest.mark.parametrize("line", ["v2", "v3"])
@pytest.mark.parametrize("scenario", ["cold", "warm", "changed"])
@pytest.mark.parametrize("delete_evidence", ["exact", "missing", "extra"])
@pytest.mark.skipif(sys.version_info < (3, 11), reason="benchmark cells require Python 3.11 to 3.13")
def test_runner_repeats_from_fresh_state_and_cleans_up(  # noqa: PLR0913, PLR0917, PLR0915 -- complete runner protocol
    monkeypatch, tmp_path, mapping, scenario, line, delete_evidence
) -> None:
    events = []
    counts = expected_counts("S", mapping)
    expected = {
        "create": {"InterfacePhysical": 1},
        "update": {"InterfaceVirtual": 1},
        "delete": {"InterfacePhysical": 1},
    }

    class Stack:  # test double for the stack protocol
        context = None

        @staticmethod
        def reset(tier) -> None:
            events.append(("reset", tier))
            counts.update(expected_counts("S", mapping))

        @staticmethod
        def destination_identity() -> tuple[str, str, str]:
            return "1.11.3", "sha256:image", "image@sha256:digest"

        @staticmethod
        def start_sync() -> None:
            events.append(("start",))

        @staticmethod
        def package(*, worker) -> dict[str, Any]:  # noqa: ARG004 -- match the stack protocol
            return {
                "configuration": {
                    "schema_mapping": [
                        {"mapping": endpoint.replace("/", ".", 1), "name": kind}
                        for endpoint, kinds in mapping.items()
                        for kind in kinds
                    ]
                }
            }

        @staticmethod
        def counts(_mapping) -> tuple[dict, dict]:
            return expected_source_counts("S"), counts.copy()

        @staticmethod
        def remaining() -> int:
            return 10

        @staticmethod
        def close() -> None:
            events.append(("close",))

    class Client:
        def __init__(self, *_args: object):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args: object):
            pass

        @staticmethod
        def register_config(*_args: object) -> SimpleNamespace:

            return SimpleNamespace(
                configuration=SimpleNamespace(config_id="config"), version=SimpleNamespace(registry_version=1)
            )

    def sync(_stack, _registered, record, _timeout):
        events.append(("sync", record.scenario))
        record.wall_seconds = 1
        record.peak_rss_mb = 2
        record.actions = (
            {**expected, "delete": {}}
            if record.scenario == "changed"
            else {action: {} for action in ("create", "update", "delete")}
        )
        record.builtin_deletes = {"IpamNamespace": 1}
        if record.scenario == "changed":
            counts["InterfacePhysical"] += 1
            record.skipped_deletes = {"InterfacePhysical": {"exact": 1, "missing": 0, "extra": 2}[delete_evidence]}

    changes = tmp_path / "changes"
    changes.mkdir()
    (changes / "S.expected.json").write_text('{"changes": []}', encoding="utf-8")
    monkeypatch.setattr(bench, "STATE", tmp_path / "bench")
    monkeypatch.setattr(bench, "RESULTS", tmp_path / "results.jsonl")
    monkeypatch.setattr(bench, "cell_options", lambda *_args: None)
    monkeypatch.setattr(bench, "CellStack", Stack)
    monkeypatch.setenv("INFRAHUB_SYNC_API_TOKEN", "test-api-token")
    monkeypatch.setattr(bench, "SyncClient", Client)
    monkeypatch.setattr(bench, "v3_sync", sync)
    monkeypatch.setattr(bench, "v2_environment", lambda _ref: (tmp_path, "2.0.1", "release-sha"))
    monkeypatch.setattr(bench, "prepare_v2", lambda *_args: None)
    monkeypatch.setattr(bench, "output", lambda argv, **_kwargs: "harness-sha" if "rev-parse" in argv else "")

    def legacy_sync(_stack, _directory, _variant, _timeout):
        measured = scenario if events.count(("sync", "cold")) > events.count(("close",)) else "cold"
        if scenario == "cold":
            measured = "cold"
        events.append(("sync", measured))
        actions = expected if measured == "changed" else {action: {} for action in ("create", "update", "delete")}
        if measured == "warm":
            # Release 2.0.1 parallel no-op: all tier logs and footer, without DiffSync boundaries.
            text = parallel_warm_output(set(counts))
        else:
            text = "\n".join(
                f"{kind} create={actions['create'].get(kind, 0)} update={actions['update'].get(kind, 0)} delete={actions['delete'].get(kind, 0)}"
                for kind in counts
            )
        return text, 1, 2

    monkeypatch.setattr(bench, "v2_sync", legacy_sync)
    monkeypatch.setattr(bench, "expected_actions", lambda *_args: expected)
    monkeypatch.setattr(bench.netbox, "STATE_DIR", tmp_path)
    monkeypatch.setattr(bench.netbox, "change", lambda *_args, **_kwargs: events.append(("change",)))
    bench.run_cell.body(
        None,
        line=line,
        v2_ref="2.0.1",
        variant="parallel" if line == "v2" else "full",
        scenario=scenario,
        repetitions=2,
    )
    rows = [json.loads(line) for line in bench.RESULTS.read_text(encoding="utf-8").splitlines()]
    status = "failed" if scenario == "changed" and line == "v3" and delete_evidence != "exact" else "ok"
    assert [row["status"] for row in rows] == [status, status]
    assert all(row["harness_commit"] == "harness-sha" for row in rows)
    assert all(row["commit"] == ("release-sha" if line == "v2" else "harness-sha") for row in rows)
    import hashlib

    configuration = yaml.safe_load(bench.netbox.SHIPPED_PACKAGE.read_text())["configuration"]
    expected_hash = hashlib.sha256(
        json.dumps(configuration["schema_mapping"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert all(row["mapping_sha256"] == expected_hash for row in rows)
    if status == "failed":
        assert all(row["wall_seconds"] is None for row in rows)
        assert all("recorded nonexecuted deletes differ" in row["error"] for row in rows)
    else:
        assert all(row["wall_seconds"] == 1 for row in rows)
    if scenario == "changed" and line == "v3":
        assert all(row["builtin_deletes"] == {"IpamNamespace": 1} for row in rows)
        assert all(
            row["skipped_deletes"]["InterfacePhysical"] == {"exact": 1, "missing": 0, "extra": 2}[delete_evidence]
            for row in rows
        )
    assert events.count(("reset", "S")) == events.count(("close",)) == 2
    assert events.count(("sync", "cold")) == 2
    if scenario != "cold":
        assert events.count(("sync", scenario)) == 2
    if scenario == "changed":
        assert events.count(("change",)) == 2


def test_report_compares_lines_without_pooling_versions(tmp_path) -> None:
    path = tmp_path / "results.jsonl"
    for line, version, variant, seconds in [
        ("v2", "2a", "parallel", 4),
        ("v2", "2b", "parallel", 6),
        ("v3", "3", "full", 2),
    ]:
        ResultRecord(
            line, version, "abc", "S", "cold", variant, 1, status="ok", wall_seconds=seconds, peak_rss_mb=1
        ).append(path)
    rows = medians(path)
    assert [row["v2_seconds"] for row in rows] == [4, 6]
    assert [row["v3_seconds"] for row in rows] == [2, 2]


def test_report_task_prints_side_by_side_medians(tmp_path, monkeypatch, capsys) -> None:
    path = tmp_path / "results.jsonl"
    for line, version, seconds in [("v2", "2", 4), ("v3", "3", 2)]:
        ResultRecord(
            line, version, "abc", "S", "cold", "full", 1, status="ok", wall_seconds=seconds, peak_rss_mb=1
        ).append(path)
    monkeypatch.setattr(bench, "RESULTS", path)
    monkeypatch.setenv("COLUMNS", "180")
    bench.report(Context())
    output = capsys.readouterr().out
    assert "4.000" in output
    assert "2.000" in output
    assert "2@abc" in output
    assert "3@abc" in output
    assert "v2 executes deletes; v3 records deletes without executing them" in output


def test_quiet_context_suppresses_success_and_failure_output(capsys) -> None:
    from development.bench.runtime import QuietContext

    context = QuietContext()
    context.run("printf secret-token")
    with pytest.raises(BenchmarkError):
        context.run("printf secret-token; exit 1")
    captured = capsys.readouterr()
    assert "secret-token" not in captured.out + captured.err


@pytest.mark.parametrize("applied_updates", [0, 1])
@pytest.mark.parametrize("namespace", ["default", "seed-vrf"])
def test_v3_uses_api_plan_and_apply_evidence(monkeypatch, applied_updates, namespace) -> None:
    from types import SimpleNamespace

    from infrahub_sync.client.models import ArtifactReferenceResource, PublicRunResource

    start = datetime.now(timezone.utc)
    plan_time = start + timedelta(seconds=2)
    digest = "a" * 64
    run = PublicRunResource(
        run_id="run",
        operation="sync",
        configuration_reference="config@1",
        phase="applied",
        outcome="applied",
        started_at=start,
        finished_at=start + timedelta(seconds=4),
        artifact_refs=(
            ArtifactReferenceResource(
                artifact_id="plan-review",
                run_id="run",
                kind="saved-plan-review",
                media_type="application/json",
                digest=digest,
                size=1,
                object_key=f"artifacts/{digest}/plan-review",
                manifest_key=f"manifests/{digest}/plan-review",
                created_at=plan_time,
            ),
        ),
    )
    requests = []

    class Client:
        def __init__(self, *_args: object) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        @staticmethod
        def sync(request, _key) -> SimpleNamespace:
            requests.append(request)
            return SimpleNamespace(run=run)

        @staticmethod
        def wait_for_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(run=run)

        @staticmethod
        def get_plan(_run_id) -> SimpleNamespace:
            return SimpleNamespace(
                operations=[
                    SimpleNamespace(action="delete", kind="IpamNamespace", identity={"name": namespace}),
                    SimpleNamespace(action="delete", kind="InterfacePhysical", identity={"name": "eth0"}),
                    SimpleNamespace(action="update", kind="InterfaceVirtual", identity={"name": "test"}),
                ],
                summary=SimpleNamespace(deletes_not_executed=2),
            )

        @staticmethod
        def get_results(_run_id) -> SimpleNamespace:
            return SimpleNamespace(results={"summary": {"create": 0, "update": applied_updates, "delete": 2}})

    monkeypatch.setenv("INFRAHUB_SYNC_API_TOKEN", "test-api-token")
    monkeypatch.setattr(bench, "SyncClient", Client)
    record = ResultRecord("v3", "3", "abc", "S", "changed", "full", 1)
    stack = cast("bench.CellStack", SimpleNamespace(memory=lambda: 12))
    if applied_updates:
        bench.v3_sync(stack, ("config", 1), record, 10)
        assert record.actions == {"create": {}, "update": {"InterfaceVirtual": 1}, "delete": {}}
        assert record.builtin_deletes == ({"IpamNamespace": 1} if namespace == "default" else {})
        assert record.skipped_deletes == (
            {"InterfacePhysical": 1} if namespace == "default" else {"InterfacePhysical": 1, "IpamNamespace": 1}
        )
        assert (record.plan_seconds, record.apply_seconds) == (2, 2)
        assert record.peak_rss_mb == 12
    else:
        with pytest.raises(BenchmarkError, match="apply summary differs"):
            bench.v3_sync(stack, ("config", 1), record, 10)
    assert requests[0].operation == "sync"
    assert requests[0].confirm_writes is True


@pytest.mark.parametrize("variant", ["full", "parallel", "incremental"])
def test_v2_flags_are_only_sent_to_the_isolated_release(tmp_path, variant) -> None:
    from development.bench.v2 import sync_command

    environment = tmp_path / "v2-release"
    command = sync_command(environment, environment / "mapping", variant)
    assert command[0] == str(environment / ".venv/bin/infrahub-sync")
    assert command[0] != str(bench.ROOT / ".venv/bin/infrahub-sync")
    assert ("--parallel" in command) == (variant == "parallel")
    assert ("--concurrent-load" in command) == (variant == "parallel")
    assert ("--no-full-extract" in command) == (variant == "incremental")


def test_destination_start_includes_the_schema_task_worker(monkeypatch) -> None:
    commands = []
    stack = bench.CellStack()
    monkeypatch.setattr(stack.context, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout=""))
    monkeypatch.setattr(bench.netbox, "restore", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(stack, "compose", commands.append)
    monkeypatch.setattr(bench, "output", lambda *_args, **_kwargs: "")
    stack.reset("S")
    assert commands[0] == "down --volumes --remove-orphans"
    assert "task-worker" in commands[1].split()
    assert "infrahub-server" in commands[1].split()
    assert "sync-prefect" not in commands[1].split()


def test_v2_generation_uses_the_release_environment(tmp_path) -> None:
    from development.bench.v2 import generation_command

    command = generation_command(tmp_path / "v2", tmp_path / "mapping")
    assert command[0] == str(tmp_path / "v2/.venv/bin/infrahub-sync")
    assert command[1] == "generate"
    assert command[-1] == str(tmp_path / "mapping")


@pytest.fixture
def docker_sdk(monkeypatch) -> SimpleNamespace:
    """Exercise the SDK boundary with a double, including when the SDK is not installed."""

    class DockerSDKError(RuntimeError):
        pass

    errors = SimpleNamespace(DockerException=DockerSDKError)
    sdk = SimpleNamespace(from_env=lambda **_kwargs: None, errors=errors)
    monkeypatch.setitem(sys.modules, "docker", sdk)
    monkeypatch.setitem(sys.modules, "docker.errors", errors)
    return sdk


def test_worker_memory_closes_the_docker_client_without_context_manager(monkeypatch, docker_sdk) -> None:

    calls = []

    def stats(container, **kwargs: object):
        calls.append((container, kwargs))
        return {"memory_stats": {"usage": 12 * 1024**2}}

    client = SimpleNamespace(api=SimpleNamespace(stats=stats), close=lambda: calls.append("closed"))
    monkeypatch.setattr(docker_sdk, "from_env", lambda **_kwargs: client)
    stack = bench.CellStack()
    stack.worker = "benchmark-worker"
    assert stack.memory() == 12
    assert calls == [("benchmark-worker", {"stream": False, "one_shot": True}), "closed"]


def test_v2_mapping_is_preserved_and_environment_targets_the_disposable_stacks(tmp_path, monkeypatch) -> None:
    stack = bench.CellStack()
    monkeypatch.setenv("NETBOX_ADDRESS", "unrelated-source")
    monkeypatch.setenv("INFRAHUB_ADDRESS", "unrelated-destination")
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "caller-cache"))
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
        monkeypatch.setenv(name, "caller-environment")
    directory, env = bench.v2_inputs(stack, tmp_path)
    copied = yaml.safe_load((directory / "config.yml").read_text())
    shipped = yaml.safe_load(bench.netbox.SHIPPED_PACKAGE.read_text())["configuration"]
    assert copied["schema_mapping"] == shipped["schema_mapping"]
    assert "token" not in copied["source"]["settings"]
    assert "token" not in copied["destination"]["settings"]
    assert env["NETBOX_ADDRESS"] == bench.netbox.netbox_url(stack.netbox_env)
    assert env["INFRAHUB_ADDRESS"] == stack.infrahub_url
    assert not {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"} & env.keys()
    assert env["UV_PROJECT_ENVIRONMENT"] == str(tmp_path / ".venv")
    assert env["INFRAHUB_SYNC_CACHE_DIR"] == str(tmp_path / ".infrahub-sync-cache")


@pytest.mark.parametrize("fails", [False, True])
def test_v2_generation_removes_the_private_credential_on_every_exit(tmp_path, monkeypatch, fails) -> None:
    stack = bench.CellStack()
    stack.infrahub_token = "private-generation-credential"  # noqa: S105 -- test credential
    configuration, _ = bench.v2_inputs(stack, tmp_path)
    original = (configuration / "config.yml").read_text()

    def generate(*_args: object, **_kwargs: object) -> str:
        path = configuration / "config.yml"
        assert path.stat().st_mode & 0o777 == 0o600
        assert yaml.safe_load(path.read_text())["destination"]["settings"]["token"] == stack.infrahub_token
        if fails:
            msg = "generation failed"
            raise BenchmarkError(msg)
        return ""

    monkeypatch.setattr(bench, "output", generate)
    if fails:
        with pytest.raises(BenchmarkError, match="v2 adapter generation failed"):
            bench.prepare_v2(stack, tmp_path)
    else:
        bench.prepare_v2(stack, tmp_path)
    assert (configuration / "config.yml").read_text() == original


def parallel_warm_output(kinds: set[str]) -> str:
    """Model the release logger format with successive tiers and its successful CLI footer."""
    ordered = sorted(kinds)
    return (
        "\n".join(
            f"2026-10-02 00:00:00 | INFO | infrahub_sync.potenda | Sync tier {index} ({len(members)}): {members!r}"
            for index, members in enumerate((ordered[::2], ordered[1::2]))
        )
        + "\n2026-10-02 00:00:01 | INFO | infrahub_sync.cli | Sync run run-id at directory\n"
    )


@pytest.mark.parametrize(
    "damage", ["none", "footer", "tier", "incomplete-sync", "failed-action", "footer-before-tiers"]
)
def test_release_parallel_warm_requires_finished_complete_tiers(damage) -> None:
    kinds = {"DcimDevice", "DcimDeviceType", "IpamVLAN", "IpamVLANGroup"}
    text = parallel_warm_output(kinds)
    if damage == "footer":
        text = text.rsplit("\n", 2)[0]
    elif damage == "tier":
        text = text.split("\n", 1)[1]
    elif damage == "incomplete-sync":
        text = "Beginning sync\n" + text
    elif damage == "failed-action":
        text = "Unable to update action=update model=DcimDevice status=failure\n" + text
    elif damage == "footer-before-tiers":
        lines = text.splitlines()
        text = "\n".join([lines[-1], *lines[:-1]])
    assert parse_v2_summary(text, kinds) == ({"create": {}, "update": {}, "delete": {}} if damage == "none" else None)


def test_complete_summary_distinguishes_overlapping_kind_names() -> None:
    text = (
        "DcimDevice create=40 update=0 delete=0\n"
        "DcimDeviceType create=4 update=0 delete=0\n"
        "IpamVLAN create=12 update=0 delete=0\n"
        "IpamVLANGroup create=2 update=0 delete=0\n"
    )
    assert parse_v2_summary(text, {"DcimDevice", "DcimDeviceType", "IpamVLAN", "IpamVLANGroup"}) == {
        "create": {"DcimDevice": 40, "DcimDeviceType": 4, "IpamVLAN": 12, "IpamVLANGroup": 2},
        "update": {},
        "delete": {},
    }
    assert parse_v2_summary("DcimDeviceType create=4 update=0 delete=0", {"DcimDevice"}) is None


def test_setup_command_timeout_is_a_safe_failure(tmp_path, capsys) -> None:
    with pytest.raises(BenchmarkError, match="setup command exceeded its command time limit") as exc:
        output(
            [sys.executable, "-c", "import sys,time; print('secret-token', file=sys.stderr); time.sleep(60)"],
            cwd=tmp_path,
            timeout=0.1,
        )
    captured = capsys.readouterr()
    assert "secret-token" not in str(exc.value) + captured.out + captured.err


def test_missing_api_token_fails_before_dump_validation(monkeypatch) -> None:
    monkeypatch.delenv("INFRAHUB_SYNC_API_TOKEN", raising=False)
    monkeypatch.setattr(bench.netbox, "verify_tier_dump", lambda _tier: pytest.fail("dump must not be read"))
    with pytest.raises(ValueError, match="set INFRAHUB_SYNC_API_TOKEN"):
        bench.cell_options("v3", "S", "cold", "full", 1, "")


def test_report_before_any_results(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(bench, "RESULTS", tmp_path / "missing.jsonl")
    bench.report(Context())
    assert "Valid benchmark medians" in capsys.readouterr().out


@pytest.mark.parametrize("version", ["1.11.3", "1.10.6"])
def test_destination_identity_checks_server_and_records_image(monkeypatch, version, docker_sdk) -> None:
    from infrahub_sdk import InfrahubClientSync

    monkeypatch.setenv("INFRAHUB_DOCKER_IMAGE", "local/unverified-infrahub")
    stack = bench.CellStack()
    assert stack.preview_env["INFRAHUB_DOCKER_IMAGE"] == bench.INFRAHUB_IMAGE
    assert stack.preview_env["VERSION"] == "1.11.3"
    image = SimpleNamespace(id="sha256:image", attrs={"RepoDigests": [bench.INFRAHUB_IMAGE + "@sha256:digest"]})
    filters = []
    engine = SimpleNamespace(
        containers=SimpleNamespace(list=lambda **kwargs: filters.append(kwargs) or [SimpleNamespace(image=image)]),
        close=lambda: None,
    )
    monkeypatch.setattr(docker_sdk, "from_env", lambda **_kwargs: engine)
    monkeypatch.setattr(InfrahubClientSync, "get_version", lambda _self: version)
    if version == "1.11.3":
        assert stack.destination_identity() == (version, "sha256:image", bench.INFRAHUB_IMAGE + "@sha256:digest")
        assert "com.docker.compose.project=infrahub-sync-benchmark" in filters[0]["filters"]["label"]
    else:
        with pytest.raises(BenchmarkError, match="version differs"):
            stack.destination_identity()


@pytest.mark.parametrize("timeout", ["setup", "command", "deadline"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
@pytest.mark.skipif(sys.version_info < (3, 11), reason="benchmark cells require Python 3.11 to 3.13")
def test_runner_distinguishes_setup_timeout_from_cell_deadline(monkeypatch, tmp_path, timeout, cleanup_fails) -> None:
    from development.bench.runtime import QuietContext

    events = []

    class Stack:
        @staticmethod
        def reset(_tier: str) -> None:
            if timeout != "setup":
                raise TimeoutError
            msg = "benchmark setup command exceeded its command time limit; provider output suppressed"
            raise BenchmarkError(msg)

        @staticmethod
        def remaining() -> int:
            if timeout == "deadline":
                raise TimeoutError
            return 10

        @staticmethod
        def close() -> None:
            events.append("closed")
            if cleanup_fails:
                msg = "cleanup failed"
                raise BenchmarkError(msg)

    monkeypatch.setattr(bench, "STATE", tmp_path / "bench")
    monkeypatch.setattr(bench, "RESULTS", tmp_path / "results.jsonl")
    monkeypatch.setattr(bench, "cell_options", lambda *_args: None)
    monkeypatch.setattr(bench, "CellStack", Stack)
    monkeypatch.setattr(bench, "output", lambda argv, **_kwargs: "abc" if "rev-parse" in argv else " M tasks/bench.py")
    bench.run_cell.body(QuietContext())
    row = json.loads(bench.RESULTS.read_text())
    assert row["status"] == ("timed_out" if timeout == "deadline" else "failed")
    assert row["commit"] == "abc-dirty"
    assert row["infrahub_version"] is None
    assert row["wall_seconds"] is None
    if cleanup_fails:
        assert "remove the disposable stacks manually" in row["error"]
        if timeout == "deadline":
            assert "cell exceeded its time limit" in row["error"]
    assert events == ["closed"]


def test_stopped_development_worker_requires_destroy(monkeypatch) -> None:
    stack = bench.CellStack()
    monkeypatch.setattr(stack.context, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout="stopped-worker"))
    monkeypatch.setattr(bench.dev, "build", lambda _context: pytest.fail("must not build over existing stack"))
    with pytest.raises(BenchmarkError, match=r"invoke destroy.*removes volumes"):
        stack.start_sync()
    assert stack.started_sync is False


@pytest.mark.parametrize(
    ("name", "value"), [("COMPOSE_PROJECT_NAME", "existing-development"), ("COMPOSE_FILE", "other-compose.yaml")]
)
def test_conflicting_compose_environment_is_refused_before_mutation(monkeypatch, name, value) -> None:
    stack = bench.CellStack()
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(stack.context, "run", lambda *_args, **_kwargs: pytest.fail("must refuse before Docker"))
    monkeypatch.setattr(bench.netbox, "restore", lambda *_args, **_kwargs: pytest.fail("must not reset NetBox"))
    with pytest.raises(BenchmarkError, match=f"unset conflicting {name}"):
        stack.reset("S")
    stack.close()
    assert not stack.started_sync
    assert not stack.started_netbox
    assert not stack.started_destination


@pytest.mark.parametrize("phase", ["build", "start-empty", "start-created", "ready"])
def test_sync_cleanup_only_owns_created_containers_and_pins_compose(monkeypatch, phase) -> None:
    stack = bench.CellStack()
    commands = []
    created = False
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", bench.netbox.DEV_STACK_PROJECT)
    monkeypatch.setenv("COMPOSE_FILE", str(bench.ROOT / "compose.yaml"))
    monkeypatch.setattr(bench.dev, "attach_dev_worker", lambda *_args: False)
    monkeypatch.setattr(bench.netbox, "dev_worker_containers", lambda _context: ["worker"])

    def docker(command, **_kwargs: object):
        nonlocal created
        commands.append(shlex.split(command))
        if "ps" in commands[-1]:
            return SimpleNamespace(stdout="container" if created else "")
        if "build" in commands[-1] and phase == "build":
            msg = "build failed"
            raise BenchmarkError(msg)
        if "up" in commands[-1]:
            created = phase != "start-empty"
            if phase != "ready":
                msg = "start failed"
                raise BenchmarkError(msg)
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(stack.context, "run", docker)
    if phase == "ready":
        stack.start_sync()
    else:
        with pytest.raises(BenchmarkError, match="failed"):
            stack.start_sync()
    stack.close()
    assert stack.started_sync == (phase in {"start-created", "ready"})
    lifecycle = [command for command in commands if command[:2] == ["docker", "compose"]]
    assert all(
        command[2:6] == ["--project-name", bench.netbox.DEV_STACK_PROJECT, "-f", str(bench.ROOT / "compose.yaml")]
        for command in lifecycle
    )
    assert any("down" in command for command in lifecycle) == stack.started_sync
    assert commands[0] == [
        "docker",
        "ps",
        "--all",
        "--quiet",
        "--filter",
        f"label=com.docker.compose.project={bench.netbox.DEV_STACK_PROJECT}",
    ]


@pytest.mark.parametrize("interrupted", [False, True])
@pytest.mark.skipif(sys.version_info < (3, 11), reason="benchmark cells require Python 3.11 to 3.13")
def test_runner_lock_excludes_other_checkouts_before_reset_and_releases(monkeypatch, tmp_path, interrupted) -> None:
    import fcntl

    roots = [tmp_path / "checkout-one", tmp_path / "checkout-two"]
    for root in roots:
        root.mkdir()
        (root / "pyproject.toml").write_text('[project]\nversion = "3"\n', encoding="utf-8")
    events = []

    class Stack:
        @staticmethod
        def reset(_tier) -> None:
            events.append(("reset", bench.ROOT))
            with monkeypatch.context() as other:
                other.setattr(bench, "ROOT", roots[1])
                other.setattr(bench, "STATE", roots[1] / ".netbox/bench")
                with pytest.raises(BenchmarkError, match="another benchmark cell is running"):
                    bench.run_cell.body(Context())
            if interrupted:
                raise KeyboardInterrupt
            msg = "setup failed"
            raise BenchmarkError(msg)

        @staticmethod
        def remaining() -> int:
            return 10

        @staticmethod
        def close() -> None:
            events.append(("close", bench.ROOT))
            # The lock must cover cleanup too, until Docker mutations finish.
            with bench.RUNNER_LOCK.open("a") as contender, pytest.raises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)

    monkeypatch.setattr(bench, "ROOT", roots[0])
    monkeypatch.setattr(bench, "STATE", roots[0] / ".netbox/bench")
    monkeypatch.setattr(bench, "RESULTS", tmp_path / "results.jsonl")
    monkeypatch.setattr(bench, "cell_options", lambda *_args: None)
    monkeypatch.setattr(bench, "output", lambda *_args, **_kwargs: "commit")
    monkeypatch.setattr(bench, "CellStack", Stack)
    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            bench.run_cell.body(Context())
    else:
        bench.run_cell.body(Context())
    assert events == [("reset", roots[0]), ("close", roots[0])]
    with bench.RUNNER_LOCK.open("a") as contender:
        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("phase", ["compose", "after-compose", "cleanup"])
def test_real_invoke_enforces_deadline_through_stack_lifecycle(monkeypatch, phase) -> None:
    stack = bench.CellStack()
    command = "exec " + shlex.join([sys.executable, "-c", "import time; time.sleep(5)"])
    monkeypatch.setattr(
        bench.preview,
        "_compose",
        lambda context, _arguments, _env: context.run(command if phase == "compose" else "true"),
    )
    stack.context.deadline = time.monotonic() + 0.2
    started = time.monotonic()

    def run_phase():
        if phase == "cleanup":
            stack.started_netbox = True

            def down(context):
                context.deadline = time.monotonic() + 0.2
                context.run(command)

            monkeypatch.setattr(bench.netbox, "down", down)
            stack.close()
        else:
            stack.compose("up")
            stack.context.run(command)

    with pytest.raises(TimeoutError):
        run_phase()
    assert time.monotonic() - started < 3
    assert "timeout" not in stack.context.config.run


def test_quiet_context_shorter_command_timeout_is_safe(capsys) -> None:
    from development.bench.runtime import QuietContext

    context = QuietContext()
    context.deadline = time.monotonic() + 10
    with pytest.raises(BenchmarkError, match="command time limit") as exc:
        context.run("printf secret-token; sleep 5", timeout=0.2)
    captured = capsys.readouterr()
    assert "secret-token" not in str(exc.value) + captured.out + captured.err


@pytest.mark.parametrize("shape", ["redirected", "uv-run"])
def test_real_invoke_deadline_kills_grandchild_in_command_group(monkeypatch, tmp_path, shape) -> None:
    from development.bench.runtime import QuietContext

    child_pid = tmp_path / "child.pid"
    script = tmp_path / "spawn.py"
    script.write_text(
        "import subprocess,sys,time\nfrom pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
        f"Path({str(child_pid)!r}).write_text(str(child.pid))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    original_popen = subprocess.Popen
    processes = []

    def popen(*args: Any, **kwargs: Any):  # noqa: ANN401 -- forward the real Popen protocol
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    command = shlex.join([sys.executable, str(script)])
    if shape == "redirected":
        source = tmp_path / "input.sql"
        source.write_text("", encoding="utf-8")
        command += f" < {shlex.quote(str(source))} > {shlex.quote(str(tmp_path / 'output'))}"
    else:
        command = shlex.join(["uv", "run", "--no-sync", "python", str(script)])
    context = QuietContext()
    context.deadline = time.monotonic() + 2
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            context.run(command, in_stream=False)
        assert time.monotonic() - started < 4
        assert child_pid.exists(), "the command must spawn its grandchild before the deadline"
        assert processes[0].returncode is not None
        stat = Path(f"/proc/{int(child_pid.read_text())}/stat")
        deadline = time.monotonic() + 1
        while stat.exists() and stat.read_text(encoding="utf-8").split()[2] != "Z" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not stat.exists() or stat.read_text(encoding="utf-8").split()[2] == "Z", "grandchild is still running"
    finally:
        for process in processes:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            # The pre-fix runner does not create a session; also remove the leaked descendant.
            if child_pid.exists():
                with contextlib.suppress(ProcessLookupError):
                    os.kill(int(child_pid.read_text()), signal.SIGKILL)
            process.wait()


@pytest.mark.parametrize("cleanup_fails", [False, True])
@pytest.mark.skipif(sys.version_info < (3, 11), reason="benchmark cells require Python 3.11 to 3.13")
def test_runner_records_keyboard_interrupt_before_propagating(monkeypatch, tmp_path, cleanup_fails) -> None:
    events = []

    class Stack:
        @staticmethod
        def reset(_tier: str) -> None:
            raise KeyboardInterrupt

        @staticmethod
        def close() -> None:
            events.append("closed")
            if cleanup_fails:
                msg = "cleanup failed"
                raise BenchmarkError(msg)

    monkeypatch.setattr(bench, "STATE", tmp_path / "bench")
    monkeypatch.setattr(bench, "RESULTS", tmp_path / "results.jsonl")
    monkeypatch.setattr(bench, "cell_options", lambda *_args: None)
    monkeypatch.setattr(bench, "CellStack", Stack)
    monkeypatch.setattr(bench, "output", lambda *_args, **_kwargs: "harness-sha")
    with pytest.raises(KeyboardInterrupt):
        bench.run_cell.body(Context(), repetitions=2)
    row = json.loads(bench.RESULTS.read_text())
    assert row["status"] == "failed"
    assert row["wall_seconds"] is None
    assert "interrupted by the user" in row["error"]
    assert events == ["closed"]


@pytest.mark.parametrize("line", ["v2", "v3"])
@pytest.mark.parametrize("container", ["running-worker", "stopped-worker", "api", "database"])
@pytest.mark.skipif(sys.version_info < (3, 11), reason="benchmark cells require Python 3.11 to 3.13")
def test_runner_rejects_existing_development_container_before_mutation(monkeypatch, tmp_path, line, container) -> None:
    monkeypatch.setattr(bench, "STATE", tmp_path / "bench")
    monkeypatch.setattr(bench, "RESULTS", tmp_path / "results.jsonl")
    monkeypatch.setattr(bench, "cell_options", lambda *_args: None)
    monkeypatch.setattr(bench, "output", lambda *_args, **_kwargs: "harness-sha")
    monkeypatch.setattr(bench, "v2_environment", lambda _ref: (tmp_path, "2.0.1", "release-sha"))
    commands = []

    def docker_ps(_context, command, **_kwargs: object):
        commands.append(command)
        return SimpleNamespace(stdout=container)

    monkeypatch.setattr(bench.QuietContext, "run", docker_ps)

    def destructive(*_args: object, **_kwargs: object):
        pytest.fail("rejected cell must not mutate or remove existing stacks")

    monkeypatch.setattr(bench.netbox, "restore", destructive)
    monkeypatch.setattr(bench.netbox, "down", destructive)
    monkeypatch.setattr(bench.preview, "_compose", destructive)
    monkeypatch.setattr(bench.dev, "build", destructive)
    monkeypatch.setattr(bench.dev, "destroy", destructive)
    bench.run_cell.body(Context(), line=line, v2_ref="2.0.1")
    row = json.loads(bench.RESULTS.read_text())
    assert row["status"] == "failed"
    assert "uv run invoke destroy" in row["error"]
    assert "--all" in commands[0]
    assert "com.docker.compose.project=infrahub-sync-dev" in commands[0]
    assert "com.docker.compose.service" not in commands[0]


@pytest.mark.parametrize("failure", ["source", "destination"])
def test_partial_setup_cleans_only_acquired_stacks(monkeypatch, failure) -> None:
    stack = bench.CellStack()
    events = []
    monkeypatch.setattr(stack.context, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout=""))

    def restore(*_args: object, **_kwargs: object):
        if failure == "source":
            msg = "restore failed"
            raise BenchmarkError(msg)

    def compose(_context, _arguments, _env):
        events.append("destination")
        if len(events) == 1:
            msg = "compose failed"
            raise BenchmarkError(msg)

    monkeypatch.setattr(bench.netbox, "restore", restore)
    monkeypatch.setattr(bench.preview, "_compose", compose)
    monkeypatch.setattr(bench.netbox, "down", lambda _context: events.append("source"))
    monkeypatch.setattr(bench.dev, "destroy", lambda _context: pytest.fail("unacquired Sync stack"))
    with pytest.raises(BenchmarkError):
        stack.reset("S")
    stack.close()
    assert events == (["source"] if failure == "source" else ["destination", "destination", "source"])


@pytest.fixture
def default_sigint_handler() -> Iterator[None]:
    """Isolate the interrupt probe from signal handlers installed by other tests."""
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


@pytest.mark.usefixtures("default_sigint_handler")
def test_interrupted_v2_sync_kills_session_and_reaps_process(monkeypatch, tmp_path) -> None:
    from development.bench import runtime

    child_pid = tmp_path / "child.pid"
    child_code = "import time; time.sleep(60)"
    code = (
        "import subprocess,sys,time; from pathlib import Path; "
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
        f"Path({str(child_pid)!r}).write_text(str(child.pid)); time.sleep(60)"
    )
    original_popen = subprocess.Popen
    processes = []

    def popen(*args: Any, **kwargs: Any):  # noqa: ANN401 -- forward the real Popen protocol
        process = original_popen(*args, **kwargs)
        processes.append(process)
        original_wait = process.wait
        interrupted = False

        def wait(*args: Any, **kwargs: Any):  # noqa: ANN401 -- forward the real wait protocol
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                deadline = time.monotonic() + 5
                while not child_pid.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert child_pid.exists(), "sync must spawn its descendant before cancellation"
                signal.raise_signal(signal.SIGINT)
            return original_wait(*args, **kwargs)

        monkeypatch.setattr(process, "wait", wait)
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    try:
        with pytest.raises(KeyboardInterrupt):
            measured_process([sys.executable, "-c", code], tmp_path, dict(os.environ), 10)
        process = processes[0]
        assert process.returncode == -signal.SIGKILL
        assert not (runtime.Path("/proc") / str(process.pid)).exists()
        pid = int(child_pid.read_text())
        deadline = time.monotonic() + 3
        stat = runtime.Path(f"/proc/{pid}/stat")
        while stat.exists() and stat.read_text().split()[2] != "Z" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not stat.exists() or stat.read_text().split()[2] == "Z"
    finally:
        for process in processes:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()


@pytest.mark.parametrize("different", ["harness", "mapping", "image-id", "image-digest"])
def test_report_never_pools_or_pairs_different_provenance(tmp_path, different) -> None:
    path = tmp_path / "results.jsonl"
    for line, harness, mapping_hash, seconds in [
        ("v2", "a", "map-a", 2),
        ("v2", "b" if different == "harness" else "a", "map-b" if different == "mapping" else "map-a", 20),
        ("v3", "a", "map-a", 1),
    ]:
        ResultRecord(
            line,
            "release",
            "same-sha",
            "S",
            "cold",
            "full",
            1,
            status="ok",
            wall_seconds=seconds,
            peak_rss_mb=1,
            harness_commit=harness,
            mapping_sha256=mapping_hash,
            infrahub_image_id="other-image" if line == "v2" and seconds == 20 and different == "image-id" else "image",
            infrahub_image_digest="other-digest"
            if line == "v2" and seconds == 20 and different == "image-digest"
            else "digest",
        ).append(path)
    rows = medians(path)
    assert len(rows) == 2
    paired = next(row for row in rows if row["v3_seconds"] is not None)
    unpaired = next(row for row in rows if row["v3_seconds"] is None)
    assert paired["v2_seconds"] == 2
    assert paired["v3_seconds"] == 1
    assert unpaired["v2_seconds"] == 20


@pytest.mark.parametrize("tier", ["S", "M", "L"])
@pytest.mark.parametrize("changed", [False, True])
def test_source_counts_include_skips_foundations_and_raw_changes(tier, changed) -> None:
    from development.netbox.datasets.tier_data import build_dataset

    document = json.loads(expected_text(tier, plan_changes(tier))) if changed else None
    counts = {kind: len(rows) for kind, rows in build_dataset(tier).items()}
    if document:
        for change in document["changes"]:
            counts[change["kind"]] += {"create": 1, "delete": -1, "update": 0}[change["action"]]
    validate_source_counts(tier, counts, document)
    assert counts == expected_source_counts(tier, document)


@pytest.mark.parametrize("kind", ["dcim/device-roles", "dcim/devices", "dcim/interfaces", "ipam/vlans"])
@pytest.mark.parametrize("delta", [-1, 1])
def test_source_counts_reject_missing_or_extra_foundation_and_skip_rows(kind, delta) -> None:
    counts = expected_source_counts("S")
    counts[kind] += delta
    with pytest.raises(BenchmarkError, match="NetBox counts differ"):
        validate_source_counts("S", counts)


@pytest.mark.usefixtures("default_sigint_handler")
@pytest.mark.parametrize("ignore_sigint", [False, True])
@pytest.mark.skipif(sys.version_info < (3, 11), reason="benchmark cells require Python 3.11 to 3.13")
def test_setup_interrupt_stops_sleeping_grandchild_and_records_reason(monkeypatch, tmp_path, ignore_sigint) -> None:
    from development.bench import runtime

    child_pid = tmp_path / "child.pid"
    script = tmp_path / "spawn.py"
    script.write_text(
        "import signal,subprocess,sys,time\nfrom pathlib import Path\n"
        + ("signal.signal(signal.SIGINT, signal.SIG_IGN)\n" if ignore_sigint else "")
        + "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
        f"Path({str(child_pid)!r}).write_text(str(child.pid))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    processes = []
    original_popen = subprocess.Popen

    def popen(*args: Any, **kwargs: Any):  # noqa: ANN401 -- real Popen protocol
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    original_wait = runtime.SessionLocal.wait

    def wait(runner):
        deadline = time.monotonic() + 3
        while not child_pid.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert child_pid.exists()
        os.kill(os.getpid(), signal.SIGINT)
        original_wait(runner)

    class Stack:
        context = runtime.QuietContext()

        def reset(self, _tier):
            self.context.run(shlex.join([sys.executable, str(script)]), in_stream=False)
            pytest.fail("interrupted setup must not continue")

        @staticmethod
        def close() -> None:
            stat = Path(f"/proc/{int(child_pid.read_text())}/stat")
            assert not stat.exists() or stat.read_text(encoding="utf-8").split()[2] == "Z", "cleanup raced a live child"
            assert processes[0].poll() is not None

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(runtime.SessionLocal, "wait", wait)
    monkeypatch.setattr(bench, "STATE", tmp_path / "bench")
    monkeypatch.setattr(bench, "RESULTS", tmp_path / "results.jsonl")
    monkeypatch.setattr(bench, "cell_options", lambda *_args: None)
    monkeypatch.setattr(bench, "CellStack", Stack)
    monkeypatch.setattr(bench, "output", lambda *_args, **_kwargs: "harness-sha")
    started = time.monotonic()
    try:
        with pytest.raises(KeyboardInterrupt):
            bench.run_cell.body(Context(), repetitions=2)
        assert time.monotonic() - started < 5
        row = json.loads(bench.RESULTS.read_text())
        assert row["status"] == "failed"
        assert row["wall_seconds"] is None
        assert "interrupted by the user" in row["error"]
        stat = Path(f"/proc/{int(child_pid.read_text())}/stat")
        deadline = time.monotonic() + 2
        while stat.exists() and stat.read_text(encoding="utf-8").split()[2] != "Z" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not stat.exists() or stat.read_text(encoding="utf-8").split()[2] == "Z"
    finally:
        for process in processes:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def test_invoke_tasks_load_without_toml(tmp_path) -> None:
    script = tmp_path / "without_toml.py"
    script.write_text(
        "import sys\nfrom importlib.abc import MetaPathFinder\n"
        "class WithoutToml(MetaPathFinder):\n"
        " def find_spec(self, fullname, path=None, target=None):\n"
        "  if fullname in {'toml', 'tomlkit', 'tomllib'}: raise ModuleNotFoundError(fullname)\n"
        "sys.meta_path.insert(0, WithoutToml())\n"
        "from invoke.program import Program\nProgram().run('invoke --list', exit=False)\n",
        encoding="utf-8",
    )
    result = subprocess.run(  # noqa: S603 -- fixed interpreter and local test script
        [sys.executable, str(script)],
        cwd=bench.ROOT,
        env=os.environ | {"PYTHONPATH": str(bench.ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "bench.run" in result.stdout
