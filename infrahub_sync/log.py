"""Shared logging configuration: `structlog` events delivered through standard `logging`.

Every module creates its logger with `structlog.get_logger(__name__)`. The configuration
below hands each event to the standard `logging` logger of the same name, so one set of
levels and handlers applies to every line: the level the CLI sets, the handlers Prefect
configures, `RunLoggerBridge` (which redacts a line before it reaches the Prefect run log)
and pytest's `caplog`.

A key-value pair of an event is written into the message text, as `event key=value`,
because the handlers above read only the message. A value that must be redacted is therefore
redacted with the rest of the line.

`configure_structlog` runs when `infrahub_sync` is imported. `configure_service_logging` is
the standard `logging` half for the service processes; each of them calls it at start.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from structlog.typing import EventDict, Processor

PACKAGE_LOGGER_NAME = "infrahub_sync"
SERVICE_LOG_FORMAT = "%(levelname)s | infrahub-sync | %(message)s"

# Keyword arguments that `logging.Logger` methods take themselves; every other keyword
# argument of an event is a key-value pair.
_LOGGING_KEYWORDS = ("exc_info", "stack_info", "stacklevel")


def _other_handler_receives(record: logging.LogRecord) -> bool:
    """Return whether a handler other than the fallback receives `record`."""
    current: logging.Logger | None = logging.getLogger(record.name)
    while current is not None:
        if any(handler is not _FALLBACK_HANDLER for handler in current.handlers):
            return True
        current = current.parent if current.propagate else None
    return False


class _FallbackHandler(logging.StreamHandler):
    """Print a line on standard error only while no other handler receives it.

    The handler stays on the package logger for the life of the process. A handler added
    later, such as the ones Prefect configures or `RunLoggerBridge`, then takes over: the
    line is not printed twice, and a line the bridge redacts is not also printed here
    without redaction.
    """

    def emit(self, record: logging.LogRecord) -> None:
        if not _other_handler_receives(record):
            super().emit(record)


# Used only when no handler would print the package's lines: the Sync API process
# configures no logging before uvicorn starts.
_FALLBACK_HANDLER = _FallbackHandler()
_FALLBACK_HANDLER.setFormatter(logging.Formatter(SERVICE_LOG_FORMAT))


def _render_value(value: object) -> str:
    """Return the text of one value, with every string in it written as it is.

    `RunLoggerBridge` replaces a secret by a literal search of the message. `repr` would
    escape a quote, a backslash or a control character of a string, and the search would then
    miss a secret that holds one. A string is therefore written without quotes, also inside a
    mapping, a list, a tuple or a set. Any other value is written with `str`.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        items = ", ".join(f"{_render_value(key)}: {_render_value(item)}" for key, item in value.items())
        return f"{{{items}}}"
    if isinstance(value, (list, tuple, set, frozenset)):
        return f"[{', '.join(_render_value(item) for item in value)}]"
    return str(value)


def render_for_logging(
    _logger: object, _method_name: str, event_dict: EventDict
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Turn an event into the arguments of a standard `logging` call.

    The event and its positional arguments stay separate, so `logging` formats a
    `"%s"`-style message as before and the record keeps its `args`. The key-value pairs are
    appended to the message. An `extra` mapping becomes attributes of the record, as it
    does in a direct `logging` call; any other `extra` value is a key-value pair.
    """
    event = str(event_dict.pop("event", ""))
    positional = tuple(event_dict.pop("positional_args", ()))
    keywords: dict[str, Any] = {name: event_dict.pop(name) for name in _LOGGING_KEYWORDS if name in event_dict}
    # An `extra` that is not a mapping stays in the event and is written as a key-value pair.
    if isinstance(event_dict.get("extra"), Mapping):
        keywords["extra"] = dict(event_dict.pop("extra"))
    if event_dict:
        pairs = " ".join(f"{key}={_render_value(value)}" for key, value in event_dict.items())
        if positional:
            # `logging` applies `%` formatting to the whole message when it has arguments.
            pairs = pairs.replace("%", "%%")
        event = f"{event} {pairs}" if event else pairs
    return (event, *positional), keywords


def configure_structlog() -> None:
    """Deliver `structlog` events through standard `logging`, unless the process configured it.

    An application that imports `infrahub_sync` and has called `structlog.configure` keeps
    its own configuration.
    """
    if structlog.is_configured():
        return
    processors: list[Processor] = [
        structlog.stdlib.filter_by_level,
        structlog.contextvars.merge_contextvars,
        render_for_logging,
    ]
    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # A cached logger would keep these processors after `structlog.testing.capture_logs`
        # or a later `structlog.configure` replaced them.
        cache_logger_on_first_use=False,
    )


def configure_service_logging() -> None:
    """Report the package's INFO lines in a service process.

    Importing Prefect sets the root logger to a level that drops INFO before a service's
    `main` runs, so the package logger gets its own level. A level already at INFO or lower
    is kept. One standard-error handler is added when no handler would print the lines; it
    prints a line only while no other handler receives it.
    """
    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    if package_logger.getEffectiveLevel() > logging.INFO:
        package_logger.setLevel(logging.INFO)
    if not package_logger.hasHandlers():
        package_logger.addHandler(_FALLBACK_HANDLER)
