"""Offline checks for the nightly v2-to-V3 plan gate: plan reading, comparison and orchestration."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "nightly_e2e.py"
SAMPLE = ROOT / "tests" / "data" / "v2_gate" / "plan_detail_sample.txt"
CONTEXT = {
    "netbox_address": "http://localhost:8001",
    "infrahub_address": "http://localhost:8080",
    "infrahub_version": "1.10.6",
    "dataset_commit": "commit",
    "dataset_sha256": "digest",
}
NETBOX_TOKEN = "nb-token"  # noqa: S105 -- placeholder, not a credential
INFRAHUB_TOKEN = "ih-token"  # noqa: S105 -- placeholder, not a credential
EXPECTED_DELETE = {"kind": "IpamNamespace", "identity": "name=default", "reason": "built-in namespace"}


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load the script with a report path inside the test's directory."""
    spec = importlib.util.spec_from_file_location("nightly_e2e_v2_gate", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REPORTS", tmp_path / "nightly-e2e")
    return module


@pytest.fixture
def operations(runner: ModuleType) -> list[dict[str, Any]]:
    """The operations of the recorded plan detail."""
    return runner.plan_operations(SAMPLE.read_text(encoding="utf-8"))


def test_plan_operations_read_every_operation_and_its_values(operations: list[dict[str, Any]]) -> None:
    """Each printed operation keeps its kind, its identity as printed, and the V3 values."""
    assert [(item["action"], item["kind"], item["identity"]) for item in operations] == [
        ("create", "BuiltinTag", "name=Gold"),
        ("update", "DcimDevice", "name=dmi01-akron-rtr01"),
        ("delete", "IpamNamespace", "name=default"),
        ("delete", "LocationSite", "name=MDF Old"),
    ]
    update = operations[1]
    assert update["payload"] == {"description": "edge router", "serial_number": "SN-123"}
    assert update["relationships"] == [{"name": "role", "peer": {"name": "router"}}]
    assert update["destination_id"] == "18a0c1d2-0000-4000-8000-000000000002"


def test_compare_counts_and_records_each_unexpected_entry(runner: ModuleType, operations: list[dict[str, Any]]) -> None:
    """Creates, updates and unlisted deletes are findings; a listed delete is matched."""
    result = runner.compare_plan(operations, [EXPECTED_DELETE], left_out=[], problems=[])
    assert result["counts"] == {"create": 1, "update": 1, "delete": 2, "left_out": 0, "problems": 0}
    assert result["expected_deletes_matched"] == [EXPECTED_DELETE]
    assert result["unexpected"] == [
        {"finding": "create", "kind": "BuiltinTag", "identity": "name=Gold"},
        {
            "finding": "update",
            "kind": "DcimDevice",
            "identity": "name=dmi01-akron-rtr01",
            "fields": [
                {"name": "description", "v2": None, "v3": "edge router"},
                {"name": "serial_number", "v2": None, "v3": "SN-123"},
            ],
            "relationships": [{"name": "role", "peer": {"name": "router"}}],
        },
        {"finding": "delete", "kind": "LocationSite", "identity": "name=MDF Old"},
    ]
    assert result["passed"] is False


@pytest.mark.parametrize(
    "identity",
    [pytest.param("name=default", id="printed-text"), pytest.param({"name": "default"}, id="mapping")],
)
def test_compare_passes_when_only_listed_deletes_remain(runner: ModuleType, identity: object) -> None:
    """A plan of listed deletes passes once records left out and problems are counted at 0."""
    plan = [{"action": "delete", "kind": "IpamNamespace", "identity": "name=default"}]
    expected = [{"kind": "IpamNamespace", "identity": identity, "reason": "built-in namespace"}]
    result = runner.compare_plan(plan, expected, left_out=[], problems=[])
    assert result["passed"] is True
    assert result["unexpected"] == []
    assert not runner.gate_failure(result)


def test_compare_records_left_out_records_and_problems(runner: ModuleType) -> None:
    """Records left out and problems, once the plan reports them, are findings."""
    result = runner.compare_plan(
        [],
        [],
        left_out=[{"kind": "DcimDevice", "identity": {"name": "edge"}}],
        problems=[{"kind": "LocationSite", "identity": "name=MDF"}],
    )
    assert result["counts"]["left_out"] == 1
    assert result["counts"]["problems"] == 1
    assert result["unexpected"] == [
        {"finding": "left_out", "kind": "DcimDevice", "identity": "name=edge"},
        {"finding": "problem", "kind": "LocationSite", "identity": "name=MDF"},
    ]
    assert result["passed"] is False


def test_an_empty_plan_does_not_pass_until_left_out_and_problems_are_counted(runner: ModuleType) -> None:
    """Without counts for records left out and problems, the gate cannot report a pass."""
    result = runner.compare_plan([], [])
    assert result["counts"]["left_out"] is None
    assert result["counts"]["problems"] is None
    assert result["passed"] is False
    assert runner.gate_failure(result) == runner.UNCOUNTED


def test_gate_failure_names_findings_and_the_uncounted_lists(
    runner: ModuleType, operations: list[dict[str, Any]]
) -> None:
    """The failure message counts each kind of finding before the uncounted lists."""
    result = runner.compare_plan(operations, [EXPECTED_DELETE])
    assert runner.gate_failure(result) == (
        f"the first V3 plan holds unexpected entries: create=1, update=1, delete=1; {runner.UNCOUNTED}"
    )


def test_expected_delete_list_is_reviewed_and_starts_empty(runner: ModuleType) -> None:
    """The shipped list holds no delete, so every delete fails the gate until a reviewer adds one."""
    assert runner.expected_deletes() == []


def test_expected_delete_entry_needs_a_reason(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An entry without a reason is refused."""
    path = tmp_path / "expected_deletes.yml"
    path.write_text(yaml.safe_dump([{"kind": "IpamNamespace", "identity": "name=default"}]), encoding="utf-8")
    monkeypatch.setattr(runner, "EXPECTED_DELETES", path)
    with pytest.raises(ValueError, match="needs a kind, an identity and a reason"):
        runner.expected_deletes()


class FakeCli:
    """Answer the gate's commands as the CLIs would, and keep what each v2 run read."""

    def __init__(self, plan: str) -> None:
        self.plan = plan
        self.calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
        self.read: dict[str, Any] = {}

    def __call__(self, *command: str, env: dict[str, str] | None = None, **_kwargs: object) -> str:
        """Record one command and return the output the gate parses."""
        self.calls.append((command, dict(env or {})))
        if command[:1] == ("uvx",):
            directory = Path(command[command.index("--directory") + 1])
            step = "generate" if "generate" in command else "sync"
            self.read[step] = yaml.safe_load((directory / "config.yml").read_text(encoding="utf-8"))
            if step == "generate":
                for adapter in ("netbox", "infrahub"):
                    (directory / adapter).mkdir()
            else:
                self.read["adapters"] = sorted(path.name for path in directory.iterdir() if path.is_dir())
        answers = {
            "register": "config_id: config-1\nregistry_version: 1\n",
            "diff": "run_id: gate-1\n",
            "show": "outcome: planned\n",
            "plan": self.plan,
        }
        return next((answer for word, answer in answers.items() if word in command), "")

    @property
    def commands(self) -> list[tuple[str, ...]]:
        """The commands in the order the gate ran them."""
        return [command for command, _env in self.calls]


GATE_ENV = {"NETBOX_TOKEN": NETBOX_TOKEN, "INFRAHUB_API_TOKEN": INFRAHUB_TOKEN, "INFRAHUB_SYNC_API_URL": "http://api"}


def test_v2_gate_runs_v2_from_pypi_on_the_gate_branch(runner: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """v2 2.0.2 from PyPI generates and syncs the example once, isolated from the repository's settings."""
    cli = FakeCli(SAMPLE.read_text(encoding="utf-8"))
    monkeypatch.setattr(runner, "run", cli)
    with pytest.raises(RuntimeError, match="create=1, update=1, delete=2"):
        runner.v2_gate(GATE_ENV, CONTEXT)
    commands = cli.commands
    assert commands[0] == ("uv", "run", "--no-sync", "infrahubctl", "branch", "create", "v2-gate")
    v2 = ("uvx", "--from", "infrahub-sync==2.0.2", "--with", "pynetbox==7.8.0", "infrahub-sync")
    assert commands[1][:9] == (*v2, "generate", "--name", "from-netbox")
    assert commands[2][:9] == (*v2, "sync", "--name", "from-netbox")
    v2_env = cli.calls[2][1]
    assert v2_env["NETBOX_ADDRESS"] == CONTEXT["netbox_address"]
    assert v2_env["INFRAHUB_ADDRESS"] == CONTEXT["infrahub_address"]
    assert v2_env["NETBOX_TOKEN"] == NETBOX_TOKEN
    assert v2_env["INFRAHUB_API_TOKEN"] == INFRAHUB_TOKEN
    assert "INFRAHUB_SYNC_API_URL" not in v2_env
    assert cli.read["sync"]["destination"]["settings"] == {"url": "http://localhost:8000", "branch": "v2-gate"}
    assert cli.read["generate"]["destination"]["settings"]["url"] == CONTEXT["infrahub_address"]
    assert cli.read["adapters"] == ["infrahub", "netbox"]


def test_v2_gate_plans_the_converted_package_and_keeps_the_result(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conversion reads the addresses from the v2 variables, and the result names what was compared."""
    cli = FakeCli(SAMPLE.read_text(encoding="utf-8"))
    monkeypatch.setattr(runner, "run", cli)
    with pytest.raises(RuntimeError, match="create=1, update=1, delete=2"):
        runner.v2_gate(GATE_ENV, CONTEXT)
    convert, convert_env = cli.calls[3]
    assert convert[4:6] == ("configs", "convert")
    assert convert[-1] == "--yes"
    assert convert_env["NETBOX_ADDRESS"] == CONTEXT["netbox_address"]
    assert convert_env["INFRAHUB_ADDRESS"] == CONTEXT["infrahub_address"]
    register, diff = cli.commands[4], cli.commands[5]
    assert register[4:6] == ("configs", "register")
    assert register[6].endswith("package.yml")
    assert diff[diff.index("--branch") + 1] == "v2-gate"
    assert cli.commands[-1][4:] == ("runs", "plan", "gate-1", "--detail")
    result = json.loads((runner.REPORTS / "gate-result.json").read_text(encoding="utf-8"))
    assert result["v2_version"] == "2.0.2"
    assert result["infrahub_version"] == "1.10.6"
    assert result["dataset"] == {"name": "demo", "commit": "commit", "sha256": "digest"}
    assert result["counts"] == {"create": 1, "update": 1, "delete": 2, "left_out": None, "problems": None}
    assert result["passed"] is False


def test_v2_gate_stops_on_an_empty_plan_because_left_out_and_problems_are_not_counted(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no differences found, the gate still stops rather than report a pass it did not check."""
    monkeypatch.setattr(runner, "run", FakeCli("run_id: gate-1\noperations: 0\nby_action: \nby_kind: \n"))
    with pytest.raises(RuntimeError, match=f"^{runner.UNCOUNTED}$"):
        runner.v2_gate(GATE_ENV, CONTEXT)
    result = json.loads((runner.REPORTS / "gate-result.json").read_text(encoding="utf-8"))
    assert result["passed"] is False
    assert result["unexpected"] == []


def test_summary_includes_the_gate_result(runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The run summary lists the v2-gate suite and the gate's counts."""
    runner.REPORTS.mkdir(parents=True)
    result = runner.compare_plan([], [])
    (runner.REPORTS / "gate-result.json").write_text(json.dumps({"v2_version": "2.0.2", **result}))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    runner.summarize("abc")
    contents = summary.read_text()
    assert "| v2-gate | 0 | 1 | 0 | not started |" in contents
    assert (
        "v2 2.0.2 to V3 plan gate: passed=false; create=0, update=0, delete=0, "
        "left_out=not counted, problems=not counted; expected deletes matched=0"
    ) in contents


def test_v2_gate_suite_seeds_demo_and_starts_a_fresh_stack(runner: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate prepares NetBox and Infrahub as the from-netbox suite does, then runs the gate."""
    events: list[str] = []
    monkeypatch.setattr(runner, "environment", dict)
    monkeypatch.setattr(runner, "start_preview", lambda _env: events.append("up"))
    monkeypatch.setattr(runner, "schema", lambda _env: events.append("schema"))
    monkeypatch.setattr(runner, "gate_context", lambda: CONTEXT)
    monkeypatch.setattr(runner, "v2_gate", lambda _env, context: events.append(f"gate {context['infrahub_address']}"))
    monkeypatch.setattr(runner, "run", lambda *command, **_kwargs: events.append(" ".join(command)))
    runner.suite("v2-gate")
    assert events[0].endswith("invoke preview.down --volumes")
    assert events[1].endswith("invoke netbox.seed --dataset demo")
    assert events[2:] == ["up", "schema", "gate http://localhost:8080"]
