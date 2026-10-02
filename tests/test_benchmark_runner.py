"""Benchmark contracts use mapped kinds and never require live services."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
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
    mapped_kinds,
    medians,
    parse_v2_summary,
    stage_seconds,
    validate_result,
)
from development.bench.runtime import BenchmarkError, docker_memory_mib, measured_process
from development.netbox.datasets.change_netbox import expected_text, plan_changes
from tasks import bench


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
    } <= rows[0].keys()
    assert rows[0]["wall_seconds"] == (3 if status == "ok" else None)
    assert rows[0]["plan_seconds"] == (1 if status == "ok" else None)
    assert rows[0]["apply_seconds"] == (2 if status == "ok" else None)


def test_warm_rejects_writes_even_when_counts_match(mapping) -> None:
    with pytest.raises(ValueError, match="nonzero"):
        validate_result("S", "warm", expected_counts("S", mapping), {"update": {"DcimDevice": 1}}, mapping)


def test_cold_rejects_missing_kind(mapping) -> None:
    counts = expected_counts("S", mapping)
    counts.pop("IpamVLAN")
    with pytest.raises(ValueError, match="counts differ"):
        validate_result("S", "cold", counts, {}, mapping)


def test_changed_requires_applied_actions_and_resulting_counts(mapping) -> None:
    actions = {"create": {"InterfacePhysical": 2}, "update": {"IpamIPAddress": 1}, "delete": {"InterfacePhysical": 1}}
    counts = expected_counts("S", mapping)
    counts["InterfacePhysical"] += 1
    validate_result("S", "changed", counts, actions, mapping, actions)
    with pytest.raises(ValueError, match="actions differ"):
        validate_result("S", "changed", counts, count_delta(expected_counts("S", mapping), counts), mapping, actions)


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
        measured_process([sys.executable, "-c", "print('secret-token'); raise SystemExit(1)"], tmp_path, {}, timeout=5)
    assert "secret-token" not in str(exc.value) + capsys.readouterr().out + capsys.readouterr().err


@pytest.mark.parametrize(
    ("line", "scenario", "variant"), [("v3", "cold", "parallel"), ("v2", "warm", "incremental"), ("v2", "cold", "full")]
)
def test_invalid_cell_options_fail_before_stack_mutation(line, scenario, variant) -> None:
    with pytest.raises(ValueError):
        bench.cell_options(line, "S", scenario, variant, 1, "")


def test_medians_exclude_invalid_samples(tmp_path) -> None:
    target = tmp_path / "results.jsonl"
    for status, seconds in [("ok", 2), ("ok", 4), ("failed", 100)]:
        ResultRecord(
            "v3", "3", "abc", "S", "cold", "full", 1, status=status, wall_seconds=seconds, peak_rss_mb=1
        ).append(target)
    assert medians(target)[0]["v3_seconds"] == 3
    assert medians(target)[0]["v2_seconds"] is None


@pytest.mark.parametrize("scenario", ["cold", "warm", "changed"])
def test_runner_repeats_from_fresh_state_and_cleans_up(monkeypatch, tmp_path, mapping, scenario) -> None:
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
            return {}, counts.copy()

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

    changes = tmp_path / "changes"
    changes.mkdir()
    (changes / "S.expected.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(bench, "STATE", tmp_path / "bench")
    monkeypatch.setattr(bench, "RESULTS", tmp_path / "results.jsonl")
    monkeypatch.setattr(bench, "cell_options", lambda *_args: None)
    monkeypatch.setattr(bench, "CellStack", Stack)
    monkeypatch.setenv("INFRAHUB_SYNC_API_TOKEN", "test-api-token")
    monkeypatch.setattr(bench, "SyncClient", Client)
    monkeypatch.setattr(bench, "v3_sync", sync)
    monkeypatch.setattr(bench, "expected_actions", lambda *_args: expected)
    monkeypatch.setattr(bench.netbox, "STATE_DIR", tmp_path)
    monkeypatch.setattr(bench.netbox, "change", lambda *_args, **_kwargs: events.append(("change",)))
    bench.run_cell.body(None, scenario=scenario, repetitions=2)
    rows = [json.loads(line) for line in bench.RESULTS.read_text(encoding="utf-8").splitlines()]
    status = "failed" if scenario == "changed" else "ok"
    assert [row["status"] for row in rows] == [status, status]
    if scenario == "changed":
        assert all(row["wall_seconds"] is None for row in rows)
        assert all("does not execute planned deletes" in row["error"] for row in rows)
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


def test_quiet_context_suppresses_success_and_failure_output(capsys) -> None:
    from development.bench.runtime import QuietContext

    context = QuietContext()
    context.run("printf secret-token")
    with pytest.raises(BenchmarkError):
        context.run("printf secret-token; exit 1")
    captured = capsys.readouterr()
    assert "secret-token" not in captured.out + captured.err


@pytest.mark.parametrize("applied_updates", [0, 1])
def test_v3_uses_api_plan_and_apply_evidence(monkeypatch, applied_updates) -> None:
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
                    SimpleNamespace(action="delete", kind="IpamNamespace", identity={"name": "default"}),
                    SimpleNamespace(action="update", kind="InterfaceVirtual", identity={"name": "test"}),
                ],
                summary=SimpleNamespace(deletes_not_executed=1),
            )

        @staticmethod
        def get_results(_run_id) -> SimpleNamespace:
            return SimpleNamespace(results={"summary": {"create": 0, "update": applied_updates, "delete": 1}})

    monkeypatch.setenv("INFRAHUB_SYNC_API_TOKEN", "test-api-token")
    monkeypatch.setattr(bench, "SyncClient", Client)
    record = ResultRecord("v3", "3", "abc", "S", "changed", "full", 1)
    stack = cast("bench.CellStack", SimpleNamespace(memory=lambda: 12))
    if applied_updates:
        bench.v3_sync(stack, ("config", 1), record, 10)
        assert record.actions == {"create": {}, "update": {"InterfaceVirtual": 1}, "delete": {}}
        assert record.skipped_deletes == {"IpamNamespace": 1}
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


def test_worker_memory_closes_the_docker_client_without_context_manager(monkeypatch) -> None:
    import docker

    calls = []

    def stats(container, **kwargs: object):
        calls.append((container, kwargs))
        return {"memory_stats": {"usage": 12 * 1024**2}}

    client = SimpleNamespace(api=SimpleNamespace(stats=stats), close=lambda: calls.append("closed"))
    monkeypatch.setattr(docker, "from_env", lambda **_kwargs: client)
    stack = bench.CellStack()
    stack.worker = "benchmark-worker"
    assert stack.memory() == 12
    assert calls == [("benchmark-worker", {"stream": False, "one_shot": True}), "closed"]


def test_v2_mapping_is_preserved_and_environment_targets_the_disposable_stacks(tmp_path, monkeypatch) -> None:
    stack = bench.CellStack()
    monkeypatch.setenv("NETBOX_ADDRESS", "unrelated-source")
    monkeypatch.setenv("INFRAHUB_ADDRESS", "unrelated-destination")
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
