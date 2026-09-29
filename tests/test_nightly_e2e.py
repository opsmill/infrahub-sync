"""Offline checks for the nightly runner's reports and suite ordering."""

from __future__ import annotations

import importlib.util
import json
import xml.etree.ElementTree as ET  # noqa: S405 -- generated local reports only
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "nightly_e2e.py"


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load the script with a report path whose parent does not exist."""
    spec = importlib.util.spec_from_file_location("nightly_e2e", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REPORTS", tmp_path / "missing" / "nightly-e2e")
    return module


def test_suite_writes_failure_report_from_clean_checkout(runner: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """A setup failure still records a failed suite on a fresh runner."""

    def fail_setup() -> None:
        msg = "setup failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(runner, "environment", fail_setup)
    monkeypatch.setattr(runner, "collect_logs", lambda _name: None)
    with pytest.raises(RuntimeError, match="integration: setup failed"):
        runner.suite("integration")
    report = ET.parse(runner.REPORTS / "integration.xml").getroot()  # noqa: S314 -- generated local report
    failure = report.find("testcase/failure")
    assert failure is not None
    assert failure.get("message") == "setup failed"
    assert json.loads((runner.REPORTS / "integration.json").read_text())["failure"] == "setup failed"


def test_logs_writes_timeout_report_without_preview_directory(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recovery step can make JUnit evidence before any suite starts."""
    monkeypatch.setattr(runner, "collect_logs", lambda _name: None)
    runner.logs("preview")
    report = ET.parse(runner.REPORTS / "preview.xml").getroot()  # noqa: S314 -- generated local report
    failure = report.find("testcase/failure")
    assert failure is not None
    assert failure.get("message") == "suite step failed or timed out"


def test_summary_counts_tests_and_setup_failure(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The summary counts JUnit cases and marks a failed post-test setup."""
    runner.REPORTS.mkdir(parents=True)
    (runner.REPORTS / "integration.xml").write_text(
        '<testsuite><testcase name="pass"/><testcase name="fail"><failure/></testcase>'
        '<testcase name="skip"><skipped/></testcase></testsuite>'
    )
    (runner.REPORTS / "integration.json").write_text(json.dumps({"duration": 2.5, "failure": ""}))
    (runner.REPORTS / "preview.xml").write_text('<testsuite><testcase name="pass"/></testsuite>')
    (runner.REPORTS / "preview.json").write_text(json.dumps({"duration": 1.0, "failure": "seed failed"}))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    runner.summarize("abc")
    contents = summary.read_text()
    assert "| integration | 1 | 1 | 1 | 2.5s |" in contents
    assert "| preview | 0 | 1 | 0 | 1.0s |" in contents
    assert "| saved-plan | 0 | 1 | 0 | not started |" in contents


def test_preview_resets_and_starts_its_own_stack(runner: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Preview starts from a fresh stack even after the integration suite."""
    events: list[str] = []
    monkeypatch.setattr(runner, "environment", dict)
    monkeypatch.setattr(runner, "start_preview", lambda _env: events.append("up"))

    def record(*command: str, **_kwargs: object) -> str:
        events.append(" ".join(command))
        return ""

    monkeypatch.setattr(runner, "run", record)
    runner.suite("preview")
    assert events[0].endswith("invoke preview.down --volumes")
    assert events[1] == "up"
    assert events[2].endswith("invoke preview.seed")
    assert "pytest -m preview" in events[3]


def test_planned_counts_ignore_unexecuted_deletes(runner: ModuleType) -> None:
    """The branch count comparison uses only creates from plan details."""
    output = "by_kind: DcimDevice=2, LocationSite=1\na create DcimDevice name=one\nb create DcimDevice name=two\nc delete LocationSite name=old\n"
    assert runner.planned_counts(output) == {"DcimDevice": 2}
