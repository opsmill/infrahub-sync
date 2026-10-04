"""Offline checks for the nightly runner's reports and suite ordering."""

from __future__ import annotations

import importlib.util
import json
import sys
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


def test_integration_seeds_the_live_branch(runner: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """The integration suite seeds the branch its live write tests require."""
    events: list[str] = []
    monkeypatch.setattr(runner, "environment", lambda: {"NETBOX_URL": "unused", "NETBOX_TOKEN": "unused"})
    monkeypatch.setattr(runner, "start_preview", lambda _env: events.append("up"))

    def record(*command: str, **_kwargs: object) -> str:
        events.append(" ".join(command))
        return ""

    monkeypatch.setattr(runner, "run", record)
    runner.suite("integration")
    assert events[0] == "up"
    assert events[1].endswith("invoke preview.seed")
    assert "pytest -m integration" in events[2]


def test_planned_counts_include_unexecuted_deletes(runner: ModuleType) -> None:
    """The branch count includes a built-in object whose delete is not executed."""
    output = (
        "operations: 3\nby_action: create=2, delete=1\nby_kind: IpamNamespace=2, DcimDevice=1\n"
        "1 delete operation(s) are recorded and NONE will be executed by apply.\n"
    )
    assert runner.planned_counts(output) == {"IpamNamespace": 2, "DcimDevice": 1}


def test_from_netbox_accepts_cli_terminal_outcomes(runner: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """A completed diff is planned and a completed sync is applied."""
    checked: list[str] = []

    def cli(*command: str, **_kwargs: object) -> str:
        if "netbox.demo-package" in command or "branch" in command:
            return ""
        if "diff" in command:
            return "run_id: diff-1\noperations: 1688\n"
        if "sync" in command:
            return "run_id: sync-1\n"
        if "show" in command:
            return "outcome: planned\n" if "diff-1" in command else "outcome: applied\n"
        pytest.fail(f"unexpected command: {command}")

    monkeypatch.setattr(runner, "run", cli)
    monkeypatch.setattr(runner, "put_netbox_configuration", lambda _env: "nightly-from-netbox")
    monkeypatch.setattr(runner, "check_import_counts", lambda run_id, _env: checked.append(run_id))
    runner.from_netbox({})
    assert checked == ["diff-1"]


def test_import_counts_match_planned_operations(runner: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """The existing default namespace accounts for the unexecuted delete."""
    plan = (
        "operations: 1688\nby_action: create=1687, delete=1\nby_kind: DcimDevice=1681, IpamNamespace=7\n"
        "1 delete operation(s) are recorded and NONE will be executed by apply.\n"
    )
    monkeypatch.setattr(runner, "run", lambda *_args, **_kwargs: plan)
    sdk = ModuleType("infrahub_sdk")
    monkeypatch.setattr(sdk, "Config", lambda **_kwargs: object(), raising=False)

    class Client:
        """Return the branch counts observed after the demo import."""

        def __init__(self, **_kwargs: object) -> None:
            pass

        @staticmethod
        def count(*, kind: str, branch: str) -> int:
            assert branch == "netbox-import"
            return {"DcimDevice": 1681, "IpamNamespace": 7}[kind]

    monkeypatch.setattr(sdk, "InfrahubClientSync", Client, raising=False)
    monkeypatch.setitem(sys.modules, "infrahub_sdk", sdk)
    runner.check_import_counts("diff-1", {"INFRAHUB_ADDRESS": "unused", "INFRAHUB_API_TOKEN": "unused"})


def test_schema_loads_the_pinned_bundle(runner: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both NetBox suites use the bundled schema without a Marketplace request."""
    path = runner.ROOT / "tests" / "data" / "nightly_schema"
    assert len(list(path.rglob("*.yml"))) == 16
    assert runner.schema_digest(path) == runner.SCHEMA_SHA256
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(runner, "run", lambda *command, **_kwargs: commands.append(command))
    runner.schema({})
    assert commands == [("uv", "run", "--no-sync", "infrahubctl", "schema", "load", str(path), "--wait", "120")]


def test_summary_names_sha_refusal(runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The summary identifies why all four suites were left unstarted."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    runner.summarize("bad-sha", sha_guard="failure")
    assert "Requested commit refused by the merged-commit SHA guard." in summary.read_text()
