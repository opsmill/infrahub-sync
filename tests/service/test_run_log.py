"""The run log reader keeps Sync's own lines, tags their stage and pages them (spec 017, FR-004, FR-005)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("prefect")

from infrahub_sync.service.orchestration import FlowRunLog
from infrahub_sync.service.run_log import MAX_MESSAGE_CHARACTERS, page, sync_lines

T0 = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)
PLAN_RUN = "11111111-1111-4111-8111-111111111111"
VERIFY_RUN = "22222222-2222-4222-8222-222222222222"


@dataclass(frozen=True)
class _Entry:
    flow_run_id: str
    purpose: str
    attempt: int


def _log(log_id: str, flow_run_id: str, seconds: int, message: str, level: int = 20) -> FlowRunLog:
    return FlowRunLog(
        log_id=log_id,
        flow_run_id=flow_run_id,
        timestamp=T0 + timedelta(seconds=seconds),
        level=level,
        message=message,
    )


def test_only_sync_lines_are_kept_with_source_and_message_split() -> None:
    logs = [
        _log("a", PLAN_RUN, 0, "Beginning flow run 'x' for flow 'infrahub-sync-service'"),
        _log("b", PLAN_RUN, 1, "infrahub_sync.service.flow | Plan started"),
        _log("c", PLAN_RUN, 2, "infrahub_sync | Loaded 3 nodes"),
        _log("d", PLAN_RUN, 3, "infrahub_syncx | not Sync"),
        _log("e", PLAN_RUN, 4, "infrahub_sync.service.flow|missing spaces"),
        _log("f", PLAN_RUN, 5, "Finished in state Completed()"),
        _log("g", PLAN_RUN, 6, " infrahub_sync | leading space"),
    ]

    lines = sync_lines([_Entry(PLAN_RUN, "plan", 1)], logs)

    assert [(line.source, line.message) for line in lines] == [
        ("infrahub_sync.service.flow", "Plan started"),
        ("infrahub_sync", "Loaded 3 nodes"),
    ]


def test_each_line_carries_its_stage_attempt_level_and_time() -> None:
    logs = [
        _log("a", VERIFY_RUN, 0, "infrahub_sync.service.flow | Verify failed", level=40),
        _log("b", PLAN_RUN, 1, "infrahub_sync.service.flow | Plan done", level=25),
    ]

    lines = sync_lines([_Entry(PLAN_RUN, "plan", 1), _Entry(VERIFY_RUN, "verify", 1)], logs)

    assert [(line.stage, line.attempt, line.level, line.timestamp) for line in lines] == [
        ("plan", 1, "INFO", T0 + timedelta(seconds=1)),
        ("verify", 1, "ERROR", T0),
    ]


def test_lines_are_ordered_by_entry_then_time_then_log_id() -> None:
    first_attempt = PLAN_RUN
    second_attempt = "33333333-3333-4333-8333-333333333333"
    logs = [
        _log("z", second_attempt, 0, "infrahub_sync | second attempt"),
        _log("b", first_attempt, 5, "infrahub_sync | first attempt, tie b"),
        _log("a", first_attempt, 5, "infrahub_sync | first attempt, tie a"),
        _log("c", first_attempt, 1, "infrahub_sync | first attempt, earliest"),
    ]

    lines = sync_lines([_Entry(first_attempt, "plan", 1), _Entry(second_attempt, "plan", 2)], logs)

    assert [line.message for line in lines] == [
        "first attempt, earliest",
        "first attempt, tie a",
        "first attempt, tie b",
        "second attempt",
    ]
    assert [line.attempt for line in lines] == [1, 1, 1, 2]


def test_a_line_of_a_flow_run_the_entries_do_not_name_is_left_out() -> None:
    logs = [_log("a", VERIFY_RUN, 0, "infrahub_sync | another stage")]

    assert sync_lines([_Entry(PLAN_RUN, "plan", 1)], logs) == ()


def test_control_characters_are_removed_and_the_message_is_capped() -> None:
    logs = [
        _log("a", PLAN_RUN, 0, "infrahub_sync | red\x1b[31m text\x00 bell\x07 del\x7f c1\x9b end"),
        _log("b", PLAN_RUN, 1, "infrahub_sync | line one\nline two\tafter tab"),
        _log("c", PLAN_RUN, 2, "infrahub_sync | " + "x" * (MAX_MESSAGE_CHARACTERS + 50)),
    ]

    escaped, multiline, long = sync_lines([_Entry(PLAN_RUN, "plan", 1)], logs)

    assert escaped.message == "red[31m text bell del c1 end"
    assert multiline.message == "line one\nline two\tafter tab"
    assert len(long.message) == MAX_MESSAGE_CHARACTERS


def test_a_secret_across_the_cap_is_redacted_before_the_message_is_capped() -> None:
    """Capping first would leave the secret's first characters, which redaction no longer matches."""
    secret = "s3cr3t-token-value-crossing-the-cap"  # noqa: S105 - a fake secret the redaction must remove.
    prefix = "x" * (MAX_MESSAGE_CHARACTERS - 10)
    logs = [_log("a", PLAN_RUN, 0, f"infrahub_sync | {prefix}{secret} tail")]

    (line,) = sync_lines([_Entry(PLAN_RUN, "plan", 1)], logs, secrets=(secret,))

    assert len(line.message) <= MAX_MESSAGE_CHARACTERS
    assert secret[:10] not in line.message
    assert line.message.startswith(prefix)


def test_page_with_offset_equal_to_lines_read_returns_only_newer_lines() -> None:
    entries = [_Entry(PLAN_RUN, "plan", 1)]
    first = [_log(f"l{index}", PLAN_RUN, index, f"infrahub_sync | line {index}") for index in range(5)]

    read, has_more = page(sync_lines(entries, first), offset=0, limit=200)
    assert len(read) == 5
    assert has_more is False

    added = [_log(f"l{index}", PLAN_RUN, index, f"infrahub_sync | line {index}") for index in range(5, 8)]
    newer, has_more = page(sync_lines(entries, [*first, *added]), offset=5, limit=200)

    assert [line.message for line in newer] == ["line 5", "line 6", "line 7"]
    assert has_more is False


def test_page_reports_has_more_when_lines_follow() -> None:
    entries = [_Entry(PLAN_RUN, "plan", 1)]
    lines = sync_lines(entries, [_log(f"l{i}", PLAN_RUN, i, f"infrahub_sync | {i}") for i in range(5)])

    items, has_more = page(lines, offset=1, limit=3)

    assert [line.message for line in items] == ["1", "2", "3"]
    assert has_more is True
    assert page(lines, offset=9, limit=3) == ((), False)
