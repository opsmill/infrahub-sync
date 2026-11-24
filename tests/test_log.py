"""The shared logging configuration: `structlog` events reach standard `logging` handlers.

These tests read records through `caplog` and through `RunLoggerBridge`, not through
`structlog.testing.capture_logs`, because `capture_logs` replaces the configured processors
and would not exercise the delivery to standard `logging`.
"""

from __future__ import annotations

import logging
import sys

import pytest
import structlog

import infrahub_sync  # noqa: F401  # importing the package applies the configuration
from infrahub_sync import log as log_module
from infrahub_sync.log import PACKAGE_LOGGER_NAME, configure_service_logging

LOGGER_NAME = "infrahub_sync.tests.log"
SECRET = "s3cr3t-token-value"  # noqa: S105  # a planted value, not a credential


class _RunLogger:
    def __init__(self) -> None:
        self.lines: list[tuple[int, str]] = []

    def log(self, level: int, msg: str, *args: object) -> None:
        self.lines.append((level, msg % args))

    def info(self, msg: str, *args: object) -> None:
        self.log(logging.INFO, msg, *args)


def test_importing_the_package_configures_structlog() -> None:
    assert structlog.is_configured()


def test_event_without_arguments_keeps_its_text(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=PACKAGE_LOGGER_NAME):
        structlog.get_logger(LOGGER_NAME).info("three devices read")

    (record,) = caplog.records
    assert (record.name, record.levelno, record.getMessage()) == (LOGGER_NAME, logging.INFO, "three devices read")


def test_positional_arguments_are_formatted_by_logging(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=PACKAGE_LOGGER_NAME):
        structlog.get_logger(LOGGER_NAME).warning("read %d devices from %s", 3, "netbox")

    (record,) = caplog.records
    assert record.args == (3, "netbox")
    assert record.getMessage() == "read 3 devices from netbox"


def test_key_values_are_written_into_the_message(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=PACKAGE_LOGGER_NAME):
        structlog.get_logger(LOGGER_NAME).info("devices read", source="netbox", count=3)

    (record,) = caplog.records
    assert record.getMessage() == "devices read source=netbox count=3"


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        pytest.param((), "usage usage=50%", id="no-positional-argument"),
        pytest.param(("disk",), "usage of disk usage=50%", id="with-positional-argument"),
    ],
)
def test_percent_sign_in_a_value_is_kept(
    caplog: pytest.LogCaptureFixture, arguments: tuple[str, ...], expected: str
) -> None:
    event = "usage of %s" if arguments else "usage"
    with caplog.at_level(logging.INFO, logger=PACKAGE_LOGGER_NAME):
        structlog.get_logger(LOGGER_NAME).info(event, *arguments, usage="50%")

    (record,) = caplog.records
    assert record.getMessage() == expected


def test_extra_mapping_becomes_record_attributes(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=PACKAGE_LOGGER_NAME):
        structlog.get_logger(LOGGER_NAME).info("plan refused", extra={"run_id": "run-1"})

    (record,) = caplog.records
    assert record.getMessage() == "plan refused"
    assert record.__dict__["run_id"] == "run-1"


def test_exception_keeps_its_traceback_on_the_record(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=PACKAGE_LOGGER_NAME):
        try:
            int("not a number")
        except ValueError:
            structlog.get_logger(LOGGER_NAME).exception("read failed")

    (record,) = caplog.records
    assert record.exc_info is not None
    assert record.exc_info[0] is ValueError


def test_level_of_the_logging_logger_filters_the_event(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=PACKAGE_LOGGER_NAME):
        logger = structlog.get_logger(LOGGER_NAME)
        logger.info("dropped", count=1)
        logger.warning("kept")

    assert [record.getMessage() for record in caplog.records] == ["kept"]


def test_bound_and_context_values_are_written_into_the_message(caplog: pytest.LogCaptureFixture) -> None:
    structlog.contextvars.bind_contextvars(run_id="run-1")
    try:
        with caplog.at_level(logging.INFO, logger=PACKAGE_LOGGER_NAME):
            structlog.get_logger(LOGGER_NAME).bind(stage="plan").info("stage started")
    finally:
        structlog.contextvars.unbind_contextvars("run_id")

    (record,) = caplog.records
    assert record.getMessage() == "stage started stage=plan run_id=run-1"


def test_run_log_receives_a_key_value_redacted() -> None:
    pytest.importorskip("prefect")
    from infrahub_sync.orchestration.flow import remote_log_bridge

    run_logger = _RunLogger()
    with remote_log_bridge(run_logger, secrets=[SECRET]):
        structlog.get_logger(LOGGER_NAME).info("request failed", header=f"Bearer {SECRET}")

    assert run_logger.lines == [(logging.INFO, f"{LOGGER_NAME} | request failed header=Bearer ***")]


def test_service_logging_reports_info_lines() -> None:
    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    package_logger.setLevel(logging.ERROR)

    configure_service_logging()

    assert package_logger.level == logging.INFO
    assert package_logger.hasHandlers()


def test_service_logging_keeps_a_lower_level() -> None:
    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    package_logger.setLevel(logging.DEBUG)

    configure_service_logging()

    assert package_logger.level == logging.DEBUG


def _fallback_only_package_logger(monkeypatch: pytest.MonkeyPatch) -> logging.Logger:
    """Leave the package logger with the fallback handler as the only handler that receives its lines."""
    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    monkeypatch.setattr(package_logger, "handlers", [])
    monkeypatch.setattr(package_logger, "propagate", False)
    configure_service_logging()
    return package_logger


def test_fallback_handler_prints_when_no_other_handler_receives_the_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fallback_only_package_logger(monkeypatch)
    monkeypatch.setattr(log_module._FALLBACK_HANDLER, "stream", sys.stderr)

    structlog.get_logger(LOGGER_NAME).info("bundle written", count=2)

    assert capsys.readouterr().err == "INFO | infrahub-sync | bundle written count=2\n"


def test_fallback_handler_prints_nothing_while_the_run_log_bridge_receives_the_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("prefect")
    from infrahub_sync.orchestration.flow import remote_log_bridge

    _fallback_only_package_logger(monkeypatch)
    monkeypatch.setattr(log_module._FALLBACK_HANDLER, "stream", sys.stderr)
    run_logger = _RunLogger()

    with remote_log_bridge(run_logger, secrets=[SECRET]):
        structlog.get_logger(LOGGER_NAME).info("request failed", header=f"Bearer {SECRET}")

    assert run_logger.lines == [(logging.INFO, f"{LOGGER_NAME} | request failed header=Bearer ***")]
    assert not capsys.readouterr().err
