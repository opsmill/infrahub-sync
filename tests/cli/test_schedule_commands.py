"""The `schedules` command group and the schedule options of `runs` and `events` (spec 021, contracts/cli.md)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from infrahub_sync.cli import app
from infrahub_sync.client import (
    APIError,
    ChangeScheduleRequest,
    ConfigurationSummaryResource,
    CreateScheduleRequest,
    EventDetailsResource,
    EventPrimaryResource,
    EventResource,
    PublicRunResource,
    RunPage,
    RunResource,
    ScheduleActionRequest,
    ScheduleOpenBranchResource,
    SchedulePage,
    ScheduleResource,
    ScheduleSettingChangeResource,
    SyncClient,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

NOW = datetime(2026, 11, 18, 9, tzinfo=timezone.utc)
SCHEDULE_ID = "s-0123456789abcdef"
NAME = "from-netbox-nightly"
RUNNER = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _schedule(**extra: object) -> ScheduleResource:
    values: dict[str, object] = {
        "schedule_id": SCHEDULE_ID,
        "name": NAME,
        "config_id": "from-netbox",
        "registry_version": 3,
        "cron": "0 2 * * *",
        "timezone": "Europe/Paris",
        "write_target": "branch-review",
        "write_when_left_out": False,
        "overlap_policy": "reject",
        "state": "active",
        "owner": "operator",
        "next_start_at": NOW,
        "prefect_synced": True,
        "consecutive_failures": 0,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(extra)
    return ScheduleResource.model_validate(values)


def _not_found() -> APIError:
    return APIError(404, "schedule-not-found")


@pytest.fixture
def client() -> MagicMock:
    injected = MagicMock(spec=SyncClient)
    injected.get_config.side_effect = lambda config_id: ConfigurationSummaryResource(
        config_id=config_id, created_at=NOW
    )
    injected.get_schedule.side_effect = lambda schedule_id: (
        _schedule() if schedule_id == SCHEDULE_ID else (_ for _ in ()).throw(_not_found())
    )
    injected.iter_schedules.side_effect = lambda **_: iter((_schedule(),))
    injected.list_schedules.return_value = SchedulePage(items=(_schedule(),))
    for method in (
        "create_schedule",
        "change_schedule",
        "pause_schedule",
        "resume_schedule",
        "delete_schedule",
        "take_over_schedule",
    ):
        getattr(injected, method).return_value = _schedule()
    injected.list_runs.return_value = RunPage(items=())
    injected.iter_events.return_value = iter(())
    return injected


@pytest.fixture(autouse=True)
def _restore_package_logging() -> Iterator[None]:
    """Undo the logger level and handler that `main` installs on every CLI invocation."""
    package_logger = logging.getLogger("infrahub_sync")
    level = package_logger.level
    handlers = list(package_logger.handlers)
    yield
    package_logger.setLevel(level)
    for handler in list(package_logger.handlers):
        if handler not in handlers:
            package_logger.removeHandler(handler)
            handler.close()


def _invoke(client: MagicMock, *args: str, stdin: str | None = None):  # type: ignore[no-untyped-def]
    return RUNNER.invoke(app, list(args), obj={"client": client}, input=stdin)


def test_create_sends_the_request_and_prints_the_schedule_with_its_next_start(client: MagicMock) -> None:
    result = _invoke(
        client,
        "schedules",
        "create",
        "--name",
        NAME,
        "--config",
        "from-netbox",
        "--version",
        "3",
        "--cron",
        "0 2 * * *",
        "--timezone",
        "Europe/Paris",
        "--reason",
        "nightly import",
        "--idempotency-key",
        "key-1",
    )

    assert result.exit_code == 0, result.output
    client.create_schedule.assert_called_once_with(
        CreateScheduleRequest(
            name=NAME,
            config_id="from-netbox",
            registry_version=3,
            cron="0 2 * * *",
            timezone="Europe/Paris",
            reason="nightly import",
        ),
        "key-1",
    )
    assert result.stdout.index("idempotency_key: key-1") < result.stdout.index(f"schedule_id: {SCHEDULE_ID}")
    assert f"next_start_at: {NOW.isoformat()}" in result.stdout


def test_create_defaults_the_time_zone_to_utc(client: MagicMock) -> None:
    result = _invoke(
        client,
        "schedules",
        "create",
        "--name",
        NAME,
        "--config",
        "from-netbox",
        "--version",
        "3",
        "--cron",
        "* * * * *",
    )

    assert result.exit_code == 0, result.output
    assert client.create_schedule.call_args.args[0].timezone == "UTC"


def test_create_offers_only_the_settings_the_sync_api_accepts() -> None:
    result = RUNNER.invoke(app, ["schedules", "create", "--help"], env={"NO_COLOR": "1", "COLUMNS": "200"})
    # Rich can force ANSI on, which splits option strings, so the escape codes are removed first.
    help_text = _ANSI.sub("", result.output)

    assert result.exit_code == 0, result.output
    for option in (
        "--name",
        "--config",
        "--version",
        "--cron",
        "--timezone",
        "--write-to-default-branch",
        "--write-when-left-out",
        "--overlap-policy",
        "--start-late-by-at-most",
        "--reason",
        "--idempotency-key",
    ):
        assert option in help_text
    # Without `--write-to-default-branch` the write target is branch review (contracts/cli.md).
    assert "--write-to-branch-review" not in help_text


def test_create_sends_the_default_branch_only_when_the_option_is_given(client: MagicMock) -> None:
    args = ("schedules", "create", "--name", NAME, "--config", "from-netbox", "--version", "3", "--cron", "* * * * *")

    to_default = _invoke(client, *args, "--write-to-default-branch")
    left_out = _invoke(client, *args)

    assert (to_default.exit_code, left_out.exit_code) == (0, 0), to_default.output
    sent = [call.args[0] for call in client.create_schedule.call_args_list]
    assert [request.write_target for request in sent] == ["default-branch", "branch-review"]
    assert "write_target" in sent[0].model_fields_set
    assert "write_target" not in sent[1].model_fields_set


@pytest.mark.parametrize(
    ("option", "value"),
    [("--write-to-default-branch", "default-branch"), ("--write-to-branch-review", "branch-review")],
)
def test_change_sends_the_write_target(
    client: MagicMock, option: str, value: Literal["default-branch", "branch-review"]
) -> None:
    result = _invoke(client, "schedules", "change", SCHEDULE_ID, option, "--idempotency-key", "key-4")

    assert result.exit_code == 0, result.output
    client.change_schedule.assert_called_once_with(SCHEDULE_ID, ChangeScheduleRequest(write_target=value), "key-4")


def test_a_default_branch_refused_while_approval_is_required_exits_1_with_the_code(client: MagicMock) -> None:
    client.create_schedule.side_effect = APIError(409, "approval-required-for-schedule")

    result = _invoke(
        client,
        "schedules",
        "create",
        "--name",
        NAME,
        "--config",
        "from-netbox",
        "--version",
        "3",
        "--cron",
        "* * * * *",
        "--write-to-default-branch",
    )

    assert result.exit_code == 1
    assert "code: approval-required-for-schedule" in result.stderr


def test_create_refusal_exits_1_with_the_code(client: MagicMock) -> None:
    client.create_schedule.side_effect = APIError(409, "schedule-name-in-use")

    result = _invoke(
        client,
        "schedules",
        "create",
        "--name",
        NAME,
        "--config",
        "from-netbox",
        "--version",
        "3",
        "--cron",
        "* * * * *",
    )

    assert result.exit_code == 1
    assert "code: schedule-name-in-use" in result.stderr


def test_list_prints_one_line_per_schedule(client: MagicMock) -> None:
    result = _invoke(client, "schedules", "list", "--config", "from-netbox", "--state", "active", "--limit", "5")

    assert result.exit_code == 0, result.output
    client.list_schedules.assert_called_once_with(config_id="from-netbox", state="active", limit=5)
    assert result.stdout.splitlines() == [
        f"{SCHEDULE_ID}\t{NAME}\tfrom-netbox@3\tactive\t0 2 * * *\tEurope/Paris\t{NOW.isoformat()}"
    ]


def test_list_all_reads_every_page(client: MagicMock) -> None:
    result = _invoke(client, "schedules", "list", "--all")

    assert result.exit_code == 0, result.output
    client.iter_schedules.assert_called_once_with(config_id=None, state=None, limit=None)
    client.list_schedules.assert_not_called()


def test_list_says_when_more_schedules_follow(client: MagicMock) -> None:
    client.list_schedules.return_value = SchedulePage(items=(_schedule(),), next_cursor="c1")

    result = _invoke(client, "schedules", "list")

    assert "more schedules" in result.stderr


@pytest.mark.parametrize(
    ("reason", "action"),
    [
        ("owner-not-found", "an administrator takes the schedule over"),
        ("approval-required", "schedules change SCHEDULE --write-to-branch-review"),
        ("repeated-failures", "check the last runs"),
    ],
)
def test_show_warns_about_a_stopped_schedule_with_the_action_its_reason_needs(
    client: MagicMock, reason: str, action: str
) -> None:
    client.get_schedule.side_effect = None
    client.get_schedule.return_value = _schedule(state="stopped", state_reason=reason, next_start_at=None)

    result = _invoke(client, "schedules", "show", SCHEDULE_ID)

    assert result.exit_code == 0, result.output
    assert f"warning: this schedule is stopped: {reason}" in result.stderr
    assert action in result.stderr
    assert "warning" not in result.stdout


def test_list_warns_about_a_schedule_not_yet_handed_to_the_task_manager(client: MagicMock) -> None:
    client.list_schedules.return_value = SchedulePage(items=(_schedule(prefect_synced=False),))

    result = _invoke(client, "schedules", "list")

    assert result.exit_code == 0, result.output
    assert "warning: this schedule is not yet handed to the task manager" in result.stderr
    assert SCHEDULE_ID in result.stderr


def test_show_prints_the_state_the_last_run_and_the_consecutive_failures(client: MagicMock) -> None:
    client.get_schedule.side_effect = None
    client.get_schedule.return_value = _schedule(last_run_id="run-9", consecutive_failures=2)

    result = _invoke(client, "schedules", "show", SCHEDULE_ID)

    assert result.exit_code == 0, result.output
    for line in ("state: active", "state_reason: <none>", "last_run_id: run-9", "consecutive_failures: 2"):
        assert line in result.stdout
    assert not result.stderr
    # A field that a Sync API without user story 3 does not return is not printed as a value.
    assert "newer_version" not in result.stdout


def test_show_prints_the_fields_of_user_story_3_when_the_sync_api_returns_them(client: MagicMock) -> None:
    client.get_schedule.side_effect = None
    client.get_schedule.return_value = _schedule(
        overlap_policy="queue", start_late_by_at_most_seconds=1800, waiting_start_at=NOW
    )

    result = _invoke(client, "schedules", "show", SCHEDULE_ID)

    assert result.exit_code == 0, result.output
    for line in (
        "overlap_policy: queue",
        "start_late_by_at_most_seconds: 1800",
        f"waiting_start_at: {NOW.isoformat()}",
    ):
        assert line in result.stdout


@pytest.mark.parametrize("command", ["show", "list"])
def test_a_newer_version_is_a_warning_on_standard_error(client: MagicMock, command: str) -> None:
    """Scenario 6: the warning does not change the exit code and is not part of the output."""
    newer = _schedule(newer_version=4)
    client.get_schedule.side_effect = None
    client.get_schedule.return_value = newer
    client.list_schedules.return_value = SchedulePage(items=(newer,))

    result = _invoke(client, "schedules", command, *((SCHEDULE_ID,) if command == "show" else ()))

    assert result.exit_code == 0, result.output
    assert "warning: a newer version of this configuration exists: from-netbox version 4" in result.stderr
    assert "warning" not in result.stdout


_CREATE = ("schedules", "create", "--name", NAME, "--config", "from-netbox", "--version", "3", "--cron", "0 2 * * *")


@pytest.mark.parametrize(
    ("duration", "seconds"),
    [("30m", 1800), ("2h", 7200), ("90s", 90), ("7d", 604800), ("1m", 60)],
)
def test_create_sends_queue_and_the_late_start_duration_in_seconds(
    client: MagicMock, duration: str, seconds: int
) -> None:
    result = _invoke(client, *_CREATE, "--overlap-policy", "queue", "--start-late-by-at-most", duration)

    assert result.exit_code == 0, result.output
    (request, _key) = client.create_schedule.call_args.args
    assert (request.overlap_policy, request.start_late_by_at_most_seconds) == ("queue", seconds)


def test_create_sends_neither_setting_when_neither_option_is_given(client: MagicMock) -> None:
    result = _invoke(client, *_CREATE)

    assert result.exit_code == 0, result.output
    (request, _key) = client.create_schedule.call_args.args
    assert {"overlap_policy", "start_late_by_at_most_seconds"}.isdisjoint(request.model_fields_set)


@pytest.mark.parametrize("duration", ["30", "59s", "8d", "2 hours", "1.5h", "-1h", "h"])
def test_a_duration_that_cannot_be_read_or_is_out_of_range_exits_2_before_any_request(
    client: MagicMock, duration: str
) -> None:
    result = _invoke(client, *_CREATE, "--start-late-by-at-most", duration)

    assert result.exit_code == 2
    assert "--start-late-by-at-most" in _ANSI.sub("", result.output)
    client.create_schedule.assert_not_called()


def test_an_unknown_overlap_policy_exits_2_before_any_request(client: MagicMock) -> None:
    result = _invoke(client, *_CREATE, "--overlap-policy", "wait")

    assert result.exit_code == 2
    client.create_schedule.assert_not_called()


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        (("--overlap-policy", "queue"), ChangeScheduleRequest(overlap_policy="queue")),
        (("--start-late-by-at-most", "2h"), ChangeScheduleRequest(start_late_by_at_most_seconds=7200)),
    ],
)
def test_change_sends_the_overlap_policy_or_the_late_start_duration_alone(
    client: MagicMock, options: tuple[str, str], expected: ChangeScheduleRequest
) -> None:
    result = _invoke(client, "schedules", "change", SCHEDULE_ID, *options, "--idempotency-key", "key-4")

    assert result.exit_code == 0, result.output
    client.change_schedule.assert_called_once_with(SCHEDULE_ID, expected, "key-4")


def test_take_over_sends_the_reason_and_prints_the_schedule(client: MagicMock) -> None:
    client.take_over_schedule.return_value = _schedule(owner="admin")

    result = _invoke(client, "schedules", "take-over", NAME, "--reason", "the owner left", "--idempotency-key", "k-5")

    assert result.exit_code == 0, result.output
    client.take_over_schedule.assert_called_once_with(
        SCHEDULE_ID, ScheduleActionRequest(reason="the owner left"), "k-5"
    )
    assert "idempotency_key: k-5" in result.stdout
    assert "owner: admin" in result.stdout


def test_take_over_refused_for_a_caller_who_is_not_an_administrator_exits_1(client: MagicMock) -> None:
    client.take_over_schedule.side_effect = APIError(403, "forbidden")

    result = _invoke(client, "schedules", "take-over", SCHEDULE_ID)

    assert result.exit_code == 1
    assert "code: forbidden" in result.stderr


def test_show_resolves_a_name_when_no_schedule_has_that_identifier(client: MagicMock) -> None:
    result = _invoke(client, "schedules", "show", NAME)

    assert result.exit_code == 0, result.output
    assert f"schedule_id: {SCHEDULE_ID}" in result.stdout


def test_a_schedule_that_cannot_be_resolved_exits_2(client: MagicMock) -> None:
    result = _invoke(client, "schedules", "pause", "unknown-schedule")

    assert result.exit_code == 2
    assert "no schedule has the id or name 'unknown-schedule'" in result.stderr
    client.pause_schedule.assert_not_called()


def test_change_sends_only_the_given_settings(client: MagicMock) -> None:
    result = _invoke(
        client, "schedules", "change", NAME, "--cron", "0 3 * * *", "--version", "4", "--idempotency-key", "key-2"
    )

    assert result.exit_code == 0, result.output
    client.change_schedule.assert_called_once_with(
        SCHEDULE_ID, ChangeScheduleRequest(cron="0 3 * * *", registry_version=4), "key-2"
    )
    assert "next_start_at:" in result.stdout


def test_create_sends_the_left_out_setting_only_when_it_is_given(client: MagicMock) -> None:
    args = ("schedules", "create", "--name", NAME, "--config", "from-netbox", "--version", "3", "--cron", "* * * * *")

    turned_on = _invoke(client, *args, "--write-when-left-out")
    turned_off = _invoke(client, *args, "--no-write-when-left-out")
    left_out = _invoke(client, *args)

    assert (turned_on.exit_code, turned_off.exit_code, left_out.exit_code) == (0, 0, 0), turned_on.output
    sent = [call.args[0] for call in client.create_schedule.call_args_list]
    assert [request.write_when_left_out for request in sent] == [True, False, False]
    assert "write_when_left_out" in sent[0].model_fields_set
    assert "write_when_left_out" not in sent[2].model_fields_set


@pytest.mark.parametrize(("option", "value"), [("--write-when-left-out", True), ("--no-write-when-left-out", False)])
def test_change_sends_the_left_out_setting(client: MagicMock, option: str, value: bool) -> None:  # noqa: FBT001
    result = _invoke(client, "schedules", "change", SCHEDULE_ID, option, "--idempotency-key", "key-3")

    assert result.exit_code == 0, result.output
    client.change_schedule.assert_called_once_with(
        SCHEDULE_ID, ChangeScheduleRequest(write_when_left_out=value), "key-3"
    )


def test_a_left_out_setting_refused_for_branch_review_exits_1_with_the_code(client: MagicMock) -> None:
    """The Sync API decides: the setting applies only to a schedule that writes to the default branch."""
    client.change_schedule.side_effect = APIError(422, "schedule-left-out-not-applicable")

    result = _invoke(client, "schedules", "change", SCHEDULE_ID, "--write-when-left-out")

    assert result.exit_code == 1
    assert "code: schedule-left-out-not-applicable" in result.stderr


def test_change_without_a_setting_exits_2_before_any_request(client: MagicMock) -> None:
    result = _invoke(client, "schedules", "change", SCHEDULE_ID)

    assert result.exit_code == 2
    client.change_schedule.assert_not_called()


@pytest.mark.parametrize(("command", "method"), [("pause", "pause_schedule"), ("resume", "resume_schedule")])
def test_pause_and_resume_send_the_reason_and_print_the_schedule(client: MagicMock, command: str, method: str) -> None:
    result = _invoke(client, "schedules", command, NAME, "--reason", "maintenance", "--idempotency-key", "key-3")

    assert result.exit_code == 0, result.output
    getattr(client, method).assert_called_once_with(SCHEDULE_ID, ScheduleActionRequest(reason="maintenance"), "key-3")
    assert "idempotency_key: key-3" in result.stdout
    assert f"schedule_id: {SCHEDULE_ID}" in result.stdout


def test_resume_refused_for_a_stopped_schedule_exits_1(client: MagicMock) -> None:
    client.resume_schedule.side_effect = APIError(409, "schedule-stopped")

    result = _invoke(client, "schedules", "resume", SCHEDULE_ID)

    assert result.exit_code == 1
    assert "code: schedule-stopped" in result.stderr


def test_delete_without_yes_refuses_when_not_interactive(client: MagicMock) -> None:
    result = _invoke(client, "schedules", "delete", SCHEDULE_ID)

    assert result.exit_code == 2
    assert "--yes" in result.stderr
    client.delete_schedule.assert_not_called()


@pytest.mark.parametrize(("answer", "deleted"), [("y\n", True), ("n\n", False)])
def test_delete_prompts_when_interactive(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch, answer: str, *, deleted: bool
) -> None:
    monkeypatch.setattr("infrahub_sync.cli._stdin_is_interactive", lambda: True)

    result = _invoke(client, "schedules", "delete", SCHEDULE_ID, stdin=answer)

    assert client.delete_schedule.called is deleted
    assert result.exit_code == (0 if deleted else 1), result.output


def test_delete_names_the_open_branch_and_proposed_change_that_stay_in_infrahub(client: MagicMock) -> None:
    client.delete_schedule.return_value = _schedule(
        state="deleted",
        next_start_at=None,
        open_branch=ScheduleOpenBranchResource(run_id="run-7", branch="sync/nightly-1", proposed_change_id="pc-1"),
    )

    result = _invoke(client, "schedules", "delete", SCHEDULE_ID, "--yes", "--idempotency-key", "key-4")

    assert result.exit_code == 0, result.output
    client.delete_schedule.assert_called_once_with(SCHEDULE_ID, ScheduleActionRequest(), "key-4")
    assert "state: deleted" in result.stdout
    assert "open_branch: sync/nightly-1" in result.stdout
    assert "open_proposed_change_id: pc-1" in result.stdout
    assert "the branch sync/nightly-1 and the proposed change pc-1 stay in Infrahub" in result.stdout


def _public_run(**extra: object) -> PublicRunResource:
    values: dict[str, object] = {
        "run_id": "run-1",
        "operation": "sync",
        "configuration_reference": "from-netbox@3",
        "config_id": "from-netbox",
        "registry_version": 3,
        "package_checksum": "a" * 64,
        "actor": "operator",
        "started_at": NOW,
        "phase": "applied",
        "destination_branch": "main",
    }
    values.update(extra)
    return PublicRunResource.model_validate(values)


def test_runs_list_filters_on_the_trigger_type_and_a_schedule_name(client: MagicMock) -> None:
    client.list_runs.return_value = RunPage(
        items=(_public_run(trigger_type="scheduled", schedule_id=SCHEDULE_ID, scheduled_start_at=NOW),)
    )

    result = _invoke(client, "runs", "list", "--trigger-type", "scheduled", "--schedule", NAME)

    assert result.exit_code == 0, result.output
    client.list_runs.assert_called_once_with(
        config_id=None, registry_version=None, limit=None, trigger_type="scheduled", schedule_id=SCHEDULE_ID
    )
    assert "  scheduled  " in result.stdout


def test_runs_list_refuses_an_unknown_trigger_type(client: MagicMock) -> None:
    result = _invoke(client, "runs", "list", "--trigger-type", "daily")

    assert result.exit_code == 2
    client.list_runs.assert_not_called()


def test_runs_show_prints_the_trigger_type_the_schedule_and_the_scheduled_start(client: MagicMock) -> None:
    run = _public_run(trigger_type="scheduled", schedule_id=SCHEDULE_ID, scheduled_start_at=NOW)
    client.get_run.return_value = RunResource(run=run, orchestration=())

    result = _invoke(client, "runs", "show", "run-1")

    assert result.exit_code == 0, result.output
    assert "trigger_type: scheduled" in result.stdout
    assert f"schedule_id: {SCHEDULE_ID}" in result.stdout
    assert f"scheduled_start_at: {NOW.isoformat()}" in result.stdout


@pytest.mark.parametrize(
    ("changes", "printed"),
    [
        (
            (
                ScheduleSettingChangeResource(field="cron", before="0 2 * * *", after="30 6 * * 1-5"),
                ScheduleSettingChangeResource(field="write_when_left_out", before=True, after=False),
            ),
            'cron="0 2 * * *"->"30 6 * * 1-5" write_when_left_out=true->false',
        ),
        ((), "changes=none"),
    ],
    ids=["changed", "nothing-changed"],
)
def test_events_prints_each_changed_setting_of_a_change_event(
    client: MagicMock, changes: tuple[ScheduleSettingChangeResource, ...], printed: str
) -> None:
    event = EventResource(
        event_id="e-1",
        event_type="infrahub_sync.schedule.changed",
        occurred_at=NOW,
        actor="operator",
        operation="schedule-change",
        outcome="accepted",
        primary_resource=EventPrimaryResource(kind="schedule", id=SCHEDULE_ID),
        details=EventDetailsResource(changes=changes),
    )
    client.iter_events.return_value = iter((event,))

    result = _invoke(client, "events", "--schedule", NAME)

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0].split("\t")[-1] == printed


def test_events_filters_on_a_schedule_and_prints_the_details(client: MagicMock) -> None:
    event = EventResource(
        event_id="e-1",
        event_type="infrahub_sync.schedule.start_skipped",
        occurred_at=NOW,
        actor="operator",
        operation="schedule",
        outcome="skipped",
        primary_resource=EventPrimaryResource(kind="schedule", id=SCHEDULE_ID),
        details=EventDetailsResource(start_at=NOW, cause="run-in-progress"),
    )
    client.iter_events.return_value = iter((event,))

    result = _invoke(client, "events", "--schedule", NAME)

    assert result.exit_code == 0, result.output
    client.iter_events.assert_called_once_with(
        run_id=None, config_id=None, event_types=(), related=(("schedule", SCHEDULE_ID),)
    )
    assert result.stdout.splitlines() == [
        "\t".join(
            (
                NOW.isoformat(),
                "infrahub_sync.schedule.start_skipped",
                f"schedule:{SCHEDULE_ID}",
                "operator",
                "skipped",
                f"start_at={NOW.isoformat()} cause=run-in-progress",
            )
        )
    ]
