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
    "worker_netbox_address": "http://netbox:8080",
    "worker_infrahub_address": "http://infrahub-server:8000",
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


UPDATED = ("DcimDevice", "name=dmi01-akron-rtr01")


def test_plan_summary_reads_the_total_and_whether_deletes_were_computed(runner: ModuleType) -> None:
    """The total and the delete flag come from the summary, not from the operation lines."""
    assert runner.plan_summary(SAMPLE.read_text(encoding="utf-8")) == {
        "total": 4,
        "delete_operations_computed": True,
    }


@pytest.mark.parametrize(
    "detail",
    [
        pytest.param("operations: 0\ndelete_operations_computed: false\nby_kind: \n", id="field-false"),
        pytest.param(
            "operations: 0\ndelete_operations_computed: true\nby_kind: \n"
            "Delete operations were NOT computed for this plan; the review may omit destination deletes.\n",
            id="disclosure-line",
        ),
        pytest.param("operations: 0\nby_kind: \n", id="field-absent"),
    ],
)
def test_plan_summary_reads_deletes_not_computed(runner: ModuleType, detail: str) -> None:
    """A plan that says deletes were not computed, or does not say, has no computed deletes."""
    assert runner.plan_summary(detail)["delete_operations_computed"] is False


def test_plan_summary_without_a_total_reads_none(runner: ModuleType) -> None:
    """A missing or unreadable `operations:` total is `None`, never 0."""
    assert runner.plan_summary("delete_operations_computed: true\nby_kind: \n")["total"] is None
    assert runner.plan_summary("operations: many\nby_kind: \n")["total"] is None


def test_compare_counts_and_records_each_unexpected_entry(runner: ModuleType, operations: list[dict[str, Any]]) -> None:
    """Creates, updates and unlisted deletes are findings; a listed delete is matched.

    An update field gives the value read from the destination; a field that was not read
    says so with the `<not read>` marker.
    """
    result = runner.compare_plan(
        operations,
        [EXPECTED_DELETE],
        total=4,
        deletes_computed=True,
        current={UPDATED: {"description": "core router"}},
        left_out=[],
        problems=[],
    )
    assert result["counts"] == {"create": 1, "update": 1, "delete": 2, "left_out": 0, "problems": 0}
    assert result["expected_deletes_matched"] == [EXPECTED_DELETE]
    assert result["unexpected"] == [
        {"finding": "create", "kind": "BuiltinTag", "identity": "name=Gold"},
        {
            "finding": "update",
            "kind": "DcimDevice",
            "identity": "name=dmi01-akron-rtr01",
            "fields": [
                {"name": "description", "v2": "core router", "v3": "edge router"},
                {"name": "serial_number", "v2": "<not read>", "v3": "SN-123"},
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
    result = runner.compare_plan(plan, expected, total=1, deletes_computed=True, left_out=[], problems=[])
    assert result["passed"] is True
    assert result["unexpected"] == []
    assert not runner.gate_failure(result)


def test_compare_records_left_out_records_and_problems(runner: ModuleType) -> None:
    """Records left out and problems that are not on the reviewed list are findings."""
    result = runner.compare_plan(
        [],
        [],
        total=0,
        deletes_computed=True,
        left_out=[{"type": "has_problem", "kind": "DcimDevice", "identity": "name=edge", "text": "has x"}],
        problems=[{"type": "wrong_value_type", "kind": "LocationSite", "identity": "name=MDF", "text": "why"}],
    )
    assert result["counts"]["left_out"] == 1
    assert result["counts"]["problems"] == 1
    assert [(item["finding"], item["type"], item["kind"], item["identity"]) for item in result["unexpected"]] == [
        ("left_out", "has_problem", "DcimDevice", "name=edge"),
        ("problem", "wrong_value_type", "LocationSite", "name=MDF"),
    ]
    assert result["passed"] is False


def test_listed_left_out_records_and_problems_pass(runner: ModuleType) -> None:
    """A record left out or a problem on the reviewed list is accepted, matched on list, type, kind and identity."""
    listed = [
        {
            "list": "left_out",
            "type": "has_problem",
            "kind": "DcimDevice",
            "identity": {"name": "PP:MDF"},
            "reason": "r",
        },
        {
            "list": "problem",
            "type": "destination_identity_collision",
            "kind": "DcimDevice",
            "identity": "name=PP:MDF",
            "reason": "r",
        },
    ]
    result = runner.compare_plan(
        [],
        [],
        total=0,
        deletes_computed=True,
        left_out=[{"type": "has_problem", "kind": "DcimDevice", "identity": "name=PP:MDF"}],
        problems=[{"type": "destination_identity_collision", "kind": "DcimDevice", "identity": "name=PP:MDF"}],
        expected_left_out=listed,
    )
    assert result["unexpected"] == []
    assert result["expected_left_out_matched"] == listed
    assert result["passed"] is True


def test_report_entries_read_the_four_columns(runner: ModuleType) -> None:
    output = "has_problem  DcimDevice  name=PP:MDF  has destination_identity_collision\n\n"
    assert runner.report_entries(output) == [
        {
            "type": "has_problem",
            "kind": "DcimDevice",
            "identity": "name=PP:MDF",
            "text": "has destination_identity_collision",
        }
    ]


def test_expected_left_out_list_holds_the_merged_kinds_with_reasons(runner: ModuleType) -> None:
    """The reviewed list names only records of the three kinds v2 merged, each with a reason."""
    entries = runner.expected_left_out()
    assert {entry["kind"] for entry in entries} == {"LocationRack", "IpamVLAN", "DcimDevice"}
    assert all(entry["reason"] for entry in entries)
    assert all(entry["type"] != runner.DEPENDS_ON_LEFT_OUT for entry in entries)


_RACK = {"list": "left_out", "type": "has_problem", "kind": "LocationRack", "identity": "name=CC site=a", "reason": "r"}


def _dependent(kind: str, identity: str, on_kind: str, on_identity: str) -> dict[str, str]:
    return {
        "type": "depends_on_left_out",
        "kind": kind,
        "identity": identity,
        "text": f"depends on {on_kind} {on_identity} through parent",
    }


def test_dependents_of_a_listed_record_are_accepted_through_several_steps(runner: ModuleType) -> None:
    left_out = [
        {"type": "has_problem", "kind": "LocationRack", "identity": "name=CC site=a"},
        _dependent("InterfacePhysical", "device=d1 name=eth0", "DcimDevice", "location=CC,a name=d1"),
        _dependent("DcimDevice", "location=CC,a name=d1", "LocationRack", "name=CC site=a"),
    ]
    deletes = [
        {"action": "delete", "kind": "DcimDevice", "identity": "location=CC,b name=d1"},
        {"action": "delete", "kind": "InterfacePhysical", "identity": "device=x name=eth0"},
    ]
    result = runner.compare_plan(
        deletes, [], total=2, deletes_computed=True, left_out=left_out, problems=[], expected_left_out=[_RACK]
    )
    assert result["unexpected"] == []
    assert result["left_out_dependents_accepted"] == 2
    assert result["deletes_of_accepted_kinds"] == 2
    assert result["passed"] is True


def test_dependent_of_an_unlisted_record_and_other_deletes_are_findings(runner: ModuleType) -> None:
    left_out = [
        {"type": "has_problem", "kind": "LocationRack", "identity": "name=CC site=a"},
        _dependent("DcimDevice", "location=X name=d2", "LocationRack", "name=X site=b"),
    ]
    deletes = [{"action": "delete", "kind": "LocationSite", "identity": "name=gone"}]
    result = runner.compare_plan(
        deletes, [], total=1, deletes_computed=True, left_out=left_out, problems=[], expected_left_out=[_RACK]
    )
    assert [(item["finding"], item["kind"]) for item in result["unexpected"]] == [
        ("left_out", "DcimDevice"),
        ("delete", "LocationSite"),
    ]
    assert result["passed"] is False


def test_depends_on_reads_the_kind_and_identity(runner: ModuleType) -> None:
    text = "depends on DcimDevice location=Comms closet,dm-akron name=r1 through device"
    assert runner.depends_on(text) == ("DcimDevice", "location=Comms closet,dm-akron name=r1")
    assert runner.depends_on("has destination_identity_collision") is None


def test_an_empty_plan_does_not_pass_until_left_out_and_problems_are_counted(runner: ModuleType) -> None:
    """Without counts for records left out and problems, the gate cannot report a pass."""
    result = runner.compare_plan([], [], total=0, deletes_computed=True)
    assert result["counts"]["left_out"] is None
    assert result["counts"]["problems"] is None
    assert result["passed"] is False
    assert runner.gate_failure(result) == runner.UNCOUNTED


def test_gate_failure_names_findings_and_the_uncounted_lists(
    runner: ModuleType, operations: list[dict[str, Any]]
) -> None:
    """The failure message counts each kind of finding before the uncounted lists."""
    result = runner.compare_plan(operations, [EXPECTED_DELETE], total=4, deletes_computed=True)
    assert runner.gate_failure(result) == (
        f"the first V3 plan holds unexpected entries: create=1, update=1, delete=1; {runner.UNCOUNTED}"
    )


@pytest.mark.parametrize(
    ("counted", "uncounted"),
    [
        pytest.param({"left_out": []}, "problems", id="only-left-out"),
        pytest.param({"problems": []}, "left_out", id="only-problems"),
    ],
)
def test_compare_does_not_pass_when_only_one_list_is_counted(
    runner: ModuleType, counted: dict[str, list[Any]], uncounted: str
) -> None:
    """Both records left out and problems must be counted for a pass."""
    result = runner.compare_plan([], [], total=0, deletes_computed=True, **counted)
    assert result["counts"][uncounted] is None
    assert result["passed"] is False
    assert runner.gate_failure(result) == runner.UNCOUNTED


def test_listed_identity_on_another_kind_is_an_unexpected_delete(runner: ModuleType) -> None:
    """A delete matches the list only on both the kind and the identity."""
    plan = [{"action": "delete", "kind": "LocationSite", "identity": "name=default"}]
    result = runner.compare_plan(plan, [EXPECTED_DELETE], total=1, deletes_computed=True, left_out=[], problems=[])
    assert result["unexpected"] == [{"finding": "delete", "kind": "LocationSite", "identity": "name=default"}]
    assert result["expected_deletes_matched"] == []
    assert result["passed"] is False


@pytest.mark.parametrize(
    ("total", "message"),
    [
        pytest.param(5, "the plan summary reports 5 operations but the detail lists 1", id="mismatch"),
        pytest.param(None, "the plan summary reports no operations total", id="missing"),
    ],
)
def test_compare_fails_when_the_detail_does_not_list_every_operation(
    runner: ModuleType, total: int | None, message: str
) -> None:
    """A plan detail that lists fewer or more operations than its total cannot pass."""
    plan = [{"action": "delete", "kind": "IpamNamespace", "identity": "name=default"}]
    result = runner.compare_plan(plan, [EXPECTED_DELETE], total=total, deletes_computed=True, left_out=[], problems=[])
    assert result["passed"] is False
    assert runner.gate_failure(result) == message


def test_compare_fails_when_deletes_were_not_computed(runner: ModuleType) -> None:
    """A plan without computed deletes cannot show that no unexpected delete exists."""
    result = runner.compare_plan([], [], total=0, deletes_computed=False, left_out=[], problems=[])
    assert result["passed"] is False
    assert runner.gate_failure(result) == runner.DELETES_NOT_COMPUTED


class FakeNode:
    """A destination node whose attributes hold a `value`, as `infrahub_sdk` returns them."""

    def __init__(self, **values: object) -> None:
        for name, value in values.items():
            setattr(self, name, type("Attribute", (), {"value": value})())


class FakeClient:
    """Return one node per destination id and record each read."""

    def __init__(self, nodes: dict[str, FakeNode]) -> None:
        self.nodes = nodes
        self.reads: list[dict[str, Any]] = []

    def get(self, **kwargs: str) -> FakeNode:
        """Return the node, or raise as `infrahub_sdk` does when it does not exist."""
        from infrahub_sdk.exceptions import NodeNotFoundError

        self.reads.append(kwargs)
        if kwargs["id"] not in self.nodes:
            raise NodeNotFoundError(identifier={"id": [kwargs["id"]]}, message="not found")
        return self.nodes[kwargs["id"]]


def test_current_values_reads_each_updated_node_by_its_destination_id(
    runner: ModuleType, operations: list[dict[str, Any]]
) -> None:
    """Only updates are read, by kind and destination id on the gate branch, for the changed fields."""
    client = FakeClient(
        {"18a0c1d2-0000-4000-8000-000000000002": FakeNode(description="core router", serial_number=None)}
    )
    assert runner.current_values(operations, client) == {UPDATED: {"description": "core router", "serial_number": None}}
    assert client.reads == [{"kind": "DcimDevice", "id": "18a0c1d2-0000-4000-8000-000000000002", "branch": "v2-gate"}]


def test_current_values_leaves_out_a_node_it_cannot_read(runner: ModuleType, operations: list[dict[str, Any]]) -> None:
    """A node that is not found, or an update without a destination id, is not in the mapping."""
    without_id = [{**operation, "destination_id": None} for operation in operations]
    assert runner.current_values(operations, FakeClient({})) == {}
    assert runner.current_values(without_id, FakeClient({})) == {}


def test_current_values_writes_a_value_that_is_not_json_as_text(runner: ModuleType) -> None:
    """A value such as an IP interface is kept as its text so the result stays JSON."""
    import ipaddress

    plan = [
        {
            "action": "update",
            "kind": "IpamIPAddress",
            "identity": "address=10.0.0.1/24",
            "destination_id": "node-1",
            "payload": {"address": "10.0.0.1/24", "absent": "x"},
        }
    ]
    client = FakeClient({"node-1": FakeNode(address=ipaddress.ip_interface("10.0.0.1/24"))})
    assert runner.current_values(plan, client) == {("IpamIPAddress", "address=10.0.0.1/24"): {"address": "10.0.0.1/24"}}


def test_expected_delete_list_holds_only_the_default_namespace(runner: ModuleType) -> None:
    """The shipped list holds the one delete that `--live-schema` causes, with its reason."""
    entries = runner.expected_deletes()
    assert [(entry["kind"], entry["identity"]) for entry in entries] == [("IpamNamespace", "name=default")]
    assert "--live-schema" in entries[0]["reason"]


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

    def __init__(self, plan: str, left_out: str = "", problems: str = "") -> None:
        self.plan = plan
        self.left_out = left_out
        self.problems = problems
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
        if "convert" in command:
            # The gate rewrites the package the conversion wrote to the worker's addresses.
            source = Path(command[command.index("convert") + 1])
            package = {
                "format_version": 1,
                "configuration": {
                    "source": {"name": "netbox", "settings": {"url": "http://localhost:8001"}},
                    "destination": {"name": "infrahub", "settings": {"url": "http://localhost:8080"}},
                },
            }
            (source.parent / "package.yml").write_text(yaml.safe_dump(package), encoding="utf-8")
        if "register" in command:
            registered = Path(command[command.index("register") + 1])
            self.read["registered"] = yaml.safe_load(registered.read_text(encoding="utf-8"))
        answers = {
            "register": "config_id: config-1\nregistry_version: 1\n",
            # `plan --config-id` creates the run; `runs plan` reads its detail.
            "--config-id": "run_id: gate-1\n",
            "show": "outcome: planned\n",
            "--left-out": self.left_out,
            "--problems": self.problems,
            "plan": self.plan,
        }
        return next((answer for word, answer in answers.items() if word in command), "")

    @property
    def commands(self) -> list[tuple[str, ...]]:
        """The commands in the order the gate ran them."""
        return [command for command, _env in self.calls]


GATE_ENV = {"NETBOX_TOKEN": NETBOX_TOKEN, "INFRAHUB_API_TOKEN": INFRAHUB_TOKEN, "INFRAHUB_SYNC_API_URL": "http://api"}


@pytest.fixture
def destination(runner: ModuleType, monkeypatch: pytest.MonkeyPatch) -> list[tuple[dict[str, str], dict[str, str]]]:
    """Answer the gate's destination reads with the sample's updated device; record each client built."""
    built: list[tuple[dict[str, str], dict[str, str]]] = []
    node = FakeNode(description="core router", serial_number="SN-000")

    def _client(env: dict[str, str], context: dict[str, str]) -> FakeClient:
        built.append((env, context))
        return FakeClient({"18a0c1d2-0000-4000-8000-000000000002": node})

    monkeypatch.setattr(runner, "gate_client", _client)
    return built


@pytest.mark.usefixtures("destination")
def test_v2_gate_runs_v2_from_pypi_on_the_gate_branch(runner: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """v2 2.0.1 from PyPI generates and syncs the example once, isolated from the repository's settings."""
    cli = FakeCli(SAMPLE.read_text(encoding="utf-8"))
    monkeypatch.setattr(runner, "run", cli)
    with pytest.raises(RuntimeError, match="create=1, update=1, delete=1"):
        runner.v2_gate(GATE_ENV, CONTEXT)
    commands = cli.commands
    assert commands[0] == ("uv", "run", "--no-sync", "infrahubctl", "branch", "create", "v2-gate")
    v2 = ("uvx", "--from", "infrahub-sync==2.0.1", "--with", "pynetbox==7.8.0", "infrahub-sync")
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
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    destination: list[tuple[dict[str, str], dict[str, str]]],
) -> None:
    """The conversion reads the addresses from the v2 variables, and the result names what was compared."""
    cli = FakeCli(SAMPLE.read_text(encoding="utf-8"))
    monkeypatch.setattr(runner, "run", cli)
    with pytest.raises(RuntimeError, match="create=1, update=1, delete=1"):
        runner.v2_gate(GATE_ENV, CONTEXT)
    convert, convert_env = cli.calls[3]
    assert convert[4:6] == ("configs", "convert")
    assert convert[-2:] == ("--live-schema", "--yes")
    assert convert_env["NETBOX_ADDRESS"] == CONTEXT["netbox_address"]
    assert convert_env["INFRAHUB_ADDRESS"] == CONTEXT["infrahub_address"]
    settings = cli.read["registered"]["configuration"]
    assert settings["source"]["settings"]["url"] == "http://netbox:8080"
    assert settings["destination"]["settings"]["url"] == "http://infrahub-server:8000"
    register, plan = cli.commands[4], cli.commands[5]
    assert register[4:6] == ("configs", "register")
    assert register[6].endswith("package.yml")
    assert plan[plan.index("--branch") + 1] == "v2-gate"
    assert [command[4:] for command in cli.commands[-3:]] == [
        ("runs", "plan", "gate-1", "--detail"),
        ("runs", "plan", "gate-1", "--left-out"),
        ("runs", "plan", "gate-1", "--problems"),
    ]
    result = json.loads((runner.REPORTS / "gate-result.json").read_text(encoding="utf-8"))
    assert result["v2_version"] == "2.0.1"
    assert result["infrahub_version"] == "1.10.6"
    assert result["dataset"] == {"name": "demo", "commit": "commit", "sha256": "digest"}
    assert result["counts"] == {"create": 1, "update": 1, "delete": 2, "left_out": 0, "problems": 0}
    assert result["passed"] is False
    assert destination == [(GATE_ENV, CONTEXT)]
    update = next(item for item in result["unexpected"] if item["finding"] == "update")
    assert update["fields"] == [
        {"name": "description", "v2": "core router", "v3": "edge router"},
        {"name": "serial_number", "v2": "SN-000", "v3": "SN-123"},
    ]


def test_v2_gate_passes_on_an_empty_plan(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    destination: list[tuple[dict[str, str], dict[str, str]]],
) -> None:
    """No operation, no record left out and no problem: the gate passes."""
    monkeypatch.setattr(
        runner,
        "run",
        FakeCli("run_id: gate-1\noperations: 0\ndelete_operations_computed: true\nby_action: \nby_kind: \n"),
    )
    runner.v2_gate(GATE_ENV, CONTEXT)
    result = json.loads((runner.REPORTS / "gate-result.json").read_text(encoding="utf-8"))
    assert result["passed"] is True
    assert result["counts"]["left_out"] == 0
    assert destination == []


def test_v2_gate_fails_on_an_unlisted_record_left_out(runner: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """A record left out that is not on the reviewed list fails the gate and is recorded."""
    monkeypatch.setattr(
        runner,
        "run",
        FakeCli(
            "run_id: gate-1\noperations: 0\ndelete_operations_computed: true\nby_action: \nby_kind: \n",
            left_out="has_problem  DcimDevice  name=PP:MDF  has destination_identity_collision\n",
        ),
    )
    with pytest.raises(RuntimeError, match="left_out=1"):
        runner.v2_gate(GATE_ENV, CONTEXT)
    result = json.loads((runner.REPORTS / "gate-result.json").read_text(encoding="utf-8"))
    assert result["unexpected"][0]["identity"] == "name=PP:MDF"


def test_v2_gate_removes_the_previous_result_when_an_early_step_fails(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that fails before the comparison leaves no result from an earlier run to publish."""
    runner.REPORTS.mkdir(parents=True)
    stale = runner.REPORTS / "gate-result.json"
    stale.write_text(json.dumps({"passed": True}), encoding="utf-8")

    def _fail(*_command: str, **_kwargs: object) -> str:
        msg = "branch create failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(runner, "run", _fail)
    with pytest.raises(RuntimeError, match="branch create failed"):
        runner.v2_gate(GATE_ENV, CONTEXT)
    assert not stale.exists()


def test_summary_includes_the_gate_result(runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The run summary lists the v2-gate suite and the gate's counts."""
    runner.REPORTS.mkdir(parents=True)
    result = runner.compare_plan([], [], total=0, deletes_computed=True)
    (runner.REPORTS / "gate-result.json").write_text(json.dumps({"v2_version": "2.0.1", **result}))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    runner.summarize("abc")
    contents = summary.read_text()
    assert "| v2-gate | 0 | 1 | 0 | not started |" in contents
    assert (
        "v2 2.0.1 to V3 plan gate: passed=false; create=0, update=0, delete=0, "
        "left_out=not counted, problems=not counted; expected deletes matched=0"
    ) in contents


def test_v2_gate_suite_seeds_demo_and_starts_a_fresh_stack(runner: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate prepares NetBox and Infrahub as the from-netbox suite does, then runs the gate."""
    events: list[str] = []
    monkeypatch.setattr(runner, "environment", dict)
    monkeypatch.setattr(runner, "start_stack", lambda _env: events.append("up"))
    monkeypatch.setattr(runner, "schema", lambda _env: events.append("schema"))
    monkeypatch.setattr(runner, "gate_context", lambda _env: CONTEXT)
    monkeypatch.setattr(runner, "v2_gate", lambda _env, context: events.append(f"gate {context['infrahub_address']}"))
    monkeypatch.setattr(runner, "run", lambda *command, **_kwargs: events.append(" ".join(command)))
    runner.suite("v2-gate")
    assert events[0].endswith("invoke dev.destroy")
    assert events[1].endswith("invoke netbox.seed --dataset demo")
    assert events[2:] == ["up", "schema", "gate http://localhost:8080"]
