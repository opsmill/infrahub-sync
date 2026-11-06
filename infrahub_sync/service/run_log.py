"""Read a run's own log lines out of its flow runs' logs (spec 017, FR-004, FR-005; research R3, R9).

A Sync line is a log line whose message starts with an ``infrahub_sync`` logger name and
`` | ``: ``RunLoggerBridge`` writes every bridged worker line that way and the service flow
writes its direct lines with the same prefix. Lines the orchestration engine writes start
with fixed English text instead, so they are left out.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from operator import itemgetter
from typing import TYPE_CHECKING, Final, Protocol

from infrahub_sync.client.models import RunLogLine

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from .orchestration import FlowRunLog

SYNC_LINE_PATTERN: Final = re.compile(r"^(infrahub_sync(?:\.[A-Za-z0-9_]+)*) \| ")
# A log line is input Sync did not validate when it was stored, so its served length is capped.
MAX_MESSAGE_CHARACTERS: Final = 4096
# Line breaks and tabs keep a multi-line message readable; every other control character is removed.
_KEPT_CONTROL_CHARACTERS: Final = frozenset("\n\t")
_LEVEL_NAMES: Final = (
    (logging.CRITICAL, "CRITICAL"),
    (logging.ERROR, "ERROR"),
    (logging.WARNING, "WARNING"),
    (logging.INFO, "INFO"),
)


class LogEntry(Protocol):
    """The orchestration entry of a run that a log line is tagged from."""

    @property
    def flow_run_id(self) -> str: ...

    @property
    def purpose(self) -> str: ...

    @property
    def attempt(self) -> int: ...


def sync_lines(entries: Sequence[LogEntry], logs: Iterable[FlowRunLog]) -> tuple[RunLogLine, ...]:
    """Return the Sync lines of ``logs``, tagged with the stage and attempt of their entry.

    Lines follow the order of ``entries`` (the run record's order, which is attempt order
    within each stage), then their timestamp, then their log ID. A line of a flow run that no
    entry names is left out.
    """
    positions = {entry.flow_run_id: position for position, entry in enumerate(entries)}
    kept: list[tuple[tuple[int, object, str], RunLogLine]] = []
    for log in logs:
        position = positions.get(log.flow_run_id)
        if position is None:
            continue
        match = SYNC_LINE_PATTERN.match(log.message)
        if match is None:
            continue
        entry = entries[position]
        line = RunLogLine(
            stage=entry.purpose,
            attempt=entry.attempt,
            timestamp=log.timestamp,
            level=level_name(log.level),
            source=match.group(1),
            message=_clean(log.message[match.end() :]),
        )
        kept.append(((position, log.timestamp, log.log_id), line))
    kept.sort(key=itemgetter(0))
    return tuple(line for _key, line in kept)


def page(lines: Sequence[RunLogLine], offset: int, limit: int) -> tuple[tuple[RunLogLine, ...], bool]:
    """Return the lines after ``offset``, at most ``limit`` of them, and whether more follow."""
    return tuple(lines[offset : offset + limit]), len(lines) > offset + limit


def level_name(level: int) -> str:
    """Return the name of the highest standard level at or below ``level``; ``DEBUG`` below ``INFO``."""
    for threshold, name in _LEVEL_NAMES:
        if level >= threshold:
            return name
    return "DEBUG"


def _clean(message: str) -> str:
    """Remove control characters other than line breaks and tabs, then cap the length."""
    cleaned = "".join(
        character
        for character in message
        if character in _KEPT_CONTROL_CHARACTERS or unicodedata.category(character) != "Cc"
    )
    return cleaned[:MAX_MESSAGE_CHARACTERS]
