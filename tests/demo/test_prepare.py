"""Preparing the NetBox demo: schema first, then an idempotent registration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import infrahub_sdk
import pytest
from infrahub_sdk.exceptions import GraphQLError, ServerNotReachableError
from infrahub_sdk.schema import SchemaLoadResponse

from infrahub_sync.client import ClientInputError
from infrahub_sync.demo import assets, prepare
from infrahub_sync.demo.errors import DemoAddressError, DemoBranchError, DemoSchemaError
from infrahub_sync.demo.package import local_package_text
from tests.demo.fakes import FakeBranches, FakeSchemaLoader, FakeSyncClient

NETBOX_URL = "http://netbox:8080"
INFRAHUB_URL = "http://infrahub-server:8000"
INFRAHUB_TOKEN = "canary-infrahub-token-7f3a91"  # noqa: S105 -- test canary, not a secret
DEMO_MODULES = sorted((Path(prepare.__file__).parent).glob("*.py"))


def _sha12(netbox_url: str = NETBOX_URL, infrahub_url: str = INFRAHUB_URL, reason: str = "") -> str:
    text = local_package_text(assets.netbox_package_text(), netbox_url, infrahub_url)
    return hashlib.sha256(f"{text}\0{reason}".encode()).hexdigest()[:12]


def _prepare(
    client: FakeSyncClient,
    loader: FakeSchemaLoader,
    netbox_url: str = NETBOX_URL,
    reason: str | None = None,
) -> prepare.PreparationResult:
    return prepare.prepare(
        client,
        netbox_url=netbox_url,
        infrahub_url=INFRAHUB_URL,
        infrahub_address=None,
        token=INFRAHUB_TOKEN,
        schema_loader=loader,
        reason=reason,
    )


def test_an_empty_infrahub_and_no_configuration_load_the_schema_and_register_version_1() -> None:
    client, loader = FakeSyncClient(), FakeSchemaLoader()

    result = _prepare(client, loader)

    assert (result.schema, result.schema_files) == ("loaded", 16)
    assert (result.configuration, result.config_id, result.version) == ("registered", "cfg-1", 1)
    assert (result.name, result.netbox_url, result.infrahub_url) == ("from-netbox", NETBOX_URL, INFRAHUB_URL)
    assert client.calls[-1] == ("register_config", "cfg-1", f"demo-prepare-register-{_sha12()}")
    assert loader.calls == [(INFRAHUB_URL, INFRAHUB_TOKEN, 16, prepare.DEFAULT_SCHEMA_TIMEOUT)]


def test_a_second_run_with_the_same_addresses_changes_nothing() -> None:
    client, loader = FakeSyncClient(), FakeSchemaLoader()
    _prepare(client, loader)

    result = _prepare(client, loader)

    assert (result.schema, result.configuration, result.config_id, result.version) == (
        "unchanged",
        "unchanged",
        "cfg-1",
        1,
    )
    assert client.calls[-1] == ("create_config_version", "cfg-1", f"demo-prepare-version-cfg-1-{_sha12()}")


def test_another_netbox_address_adds_version_2_under_its_own_key() -> None:
    client, loader = FakeSyncClient(), FakeSchemaLoader()
    _prepare(client, loader)

    result = _prepare(client, loader, netbox_url="http://netbox.example:8080")

    assert (result.configuration, result.config_id, result.version) == ("versioned", "cfg-1", 2)
    expected_key = f"demo-prepare-version-cfg-1-{_sha12(netbox_url='http://netbox.example:8080')}"
    assert client.calls[-1] == ("create_config_version", "cfg-1", expected_key)
    assert expected_key != f"demo-prepare-version-cfg-1-{_sha12()}"


def test_two_configurations_with_the_package_name_version_the_newest_and_name_it() -> None:
    client, loader = FakeSyncClient(), FakeSchemaLoader()
    package: dict[str, Any] = {"format_version": 1, "configuration": {"name": "from-netbox"}}
    client.add("cfg-old", package)
    client.add("cfg-new", package)

    result = _prepare(client, loader)

    assert (result.config_id, result.configuration, result.version, result.same_name) == ("cfg-new", "versioned", 2, 2)
    assert client.calls[-1][:2] == ("create_config_version", "cfg-new")


def test_a_different_reason_on_a_later_run_reports_unchanged_without_an_idempotency_conflict() -> None:
    client, loader = FakeSyncClient(), FakeSchemaLoader()
    _prepare(client, loader)
    _prepare(client, loader, reason="first demo")

    result = _prepare(client, loader, reason="second demo")

    assert (result.configuration, result.config_id, result.version) == ("unchanged", "cfg-1", 1)
    assert client.calls[-1] == (
        "create_config_version",
        "cfg-1",
        f"demo-prepare-version-cfg-1-{_sha12(reason='second demo')}",
    )


def test_switching_between_two_netbox_addresses_reports_versioned_only_when_a_version_is_added() -> None:
    client, loader = FakeSyncClient(), FakeSchemaLoader()
    other = "http://netbox.example:8080"

    results = [_prepare(client, loader, netbox_url=url) for url in (NETBOX_URL, other, NETBOX_URL, other)]

    assert [(result.configuration, result.version) for result in results] == [
        ("registered", 1),
        ("versioned", 2),
        ("unchanged", 1),
        ("unchanged", 2),
    ]


def test_same_name_counts_the_configurations_on_every_page() -> None:
    client, loader = FakeSyncClient(page_size=1), FakeSchemaLoader()
    package: dict[str, Any] = {"format_version": 1, "configuration": {"name": "from-netbox"}}
    for config_id in ("cfg-a", "cfg-b", "cfg-c"):
        client.add(config_id, package)

    result = _prepare(client, loader)

    assert (result.config_id, result.same_name) == ("cfg-c", 3)


@pytest.mark.parametrize(
    "option", ["netbox_url", "infrahub_url", "infrahub_address"], ids=["netbox", "infrahub", "address"]
)
def test_an_address_with_userinfo_is_refused_before_any_call(option: str) -> None:
    client, loader = FakeSyncClient(), FakeSchemaLoader()
    addresses: dict[str, str | None] = {
        "netbox_url": NETBOX_URL,
        "infrahub_url": INFRAHUB_URL,
        "infrahub_address": None,
        option: "http://admin:userinfo-canary@host:8000",
    }

    with pytest.raises(DemoAddressError) as raised:
        prepare.prepare(
            client,
            netbox_url=addresses["netbox_url"] or "",
            infrahub_url=addresses["infrahub_url"] or "",
            infrahub_address=addresses["infrahub_address"],
            token=INFRAHUB_TOKEN,
            schema_loader=loader,
        )

    assert f"--{option.replace('_', '-')}" in str(raised.value)
    assert "userinfo-canary" not in str(raised.value)
    assert (loader.calls, client.calls) == ([], [])


@pytest.mark.parametrize("reason", ["", " padded ", "line\nbreak"], ids=["empty", "padded", "control"])
def test_an_invalid_reason_is_refused_before_the_schema_load(reason: str) -> None:
    client, loader = FakeSyncClient(), FakeSchemaLoader()

    with pytest.raises(ClientInputError):
        _prepare(client, loader, reason=reason)

    assert (loader.calls, client.calls) == ([], [])


def test_a_schema_load_error_stops_before_any_sync_client_call() -> None:
    client = FakeSyncClient()
    loader = FakeSchemaLoader(error=DemoSchemaError("Infrahub refused the demo schema"))

    with pytest.raises(DemoSchemaError, match="refused the demo schema"):
        _prepare(client, loader)

    assert client.calls == []


def test_the_result_never_holds_the_token() -> None:
    result = _prepare(FakeSyncClient(), FakeSchemaLoader())

    assert INFRAHUB_TOKEN not in json.dumps(result.as_dict())


def test_nothing_in_the_demo_package_touches_the_approval_setting() -> None:
    assert DEMO_MODULES
    for module in DEMO_MODULES:
        assert "INFRAHUB_SYNC_REQUIRE_APPROVAL" not in module.read_text(encoding="utf-8"), module


class _FakeInfrahub:
    """Stands in for `InfrahubClientSync`: records its settings, answers one schema load."""

    answer: SchemaLoadResponse | Exception = SchemaLoadResponse()
    converged: bool | Exception = True
    seen: dict[str, Any] = {}  # noqa: RUF012 -- one test-scoped record, reset by the fixture

    def __init__(self, address: str, config: infrahub_sdk.Config) -> None:
        _FakeInfrahub.seen = {"address": address, "config": config}
        self.schema = SimpleNamespace(load=self._load, in_sync=self._in_sync)

    def _in_sync(self) -> bool:
        if isinstance(self.converged, Exception):
            raise self.converged
        return self.converged

    def _load(self, schemas: list[dict[str, Any]], wait_until_converged: bool) -> SchemaLoadResponse:  # noqa: FBT001 -- SDK keyword
        _FakeInfrahub.seen.update(schemas=schemas, wait=wait_until_converged)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def fake_infrahub(monkeypatch: pytest.MonkeyPatch) -> type[_FakeInfrahub]:
    monkeypatch.setattr(infrahub_sdk, "InfrahubClientSync", _FakeInfrahub)
    _FakeInfrahub.seen = {}
    return _FakeInfrahub


@pytest.mark.parametrize(
    ("answer", "state"),
    [
        pytest.param(SchemaLoadResponse(hash="b", previous_hash="a"), "loaded", id="new-hash"),
        pytest.param(SchemaLoadResponse(hash="a", previous_hash="a"), "unchanged", id="same-hash"),
    ],
)
def test_load_schema_reports_whether_the_schema_hash_changed(
    monkeypatch: pytest.MonkeyPatch, fake_infrahub: type[_FakeInfrahub], answer: SchemaLoadResponse, state: str
) -> None:
    monkeypatch.setattr(fake_infrahub, "answer", answer)
    files = assets.schema_files()

    assert prepare.load_schema("http://127.0.0.1:8000", INFRAHUB_TOKEN, files, 45) == state
    assert fake_infrahub.seen["address"] == "http://127.0.0.1:8000"
    assert fake_infrahub.seen["config"].api_token == INFRAHUB_TOKEN
    assert fake_infrahub.seen["config"].schema_converge_timeout == 45
    assert fake_infrahub.seen["wait"] is True
    assert len(fake_infrahub.seen["schemas"]) == 16


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(
            SchemaLoadResponse(errors={"errors": [{"message": f"token {INFRAHUB_TOKEN} denied"}]}), id="errors"
        ),
        pytest.param(ServerNotReachableError(address=f"http://x?token={INFRAHUB_TOKEN}"), id="unreachable"),
    ],
)
def test_load_schema_errors_raise_without_the_token(
    monkeypatch: pytest.MonkeyPatch, fake_infrahub: type[_FakeInfrahub], answer: object
) -> None:
    monkeypatch.setattr(fake_infrahub, "answer", answer)

    with pytest.raises(DemoSchemaError) as raised:
        prepare.load_schema("http://127.0.0.1:8000", INFRAHUB_TOKEN, assets.schema_files(), 5)

    assert "http://127.0.0.1:8000" in str(raised.value)
    assert INFRAHUB_TOKEN not in str(raised.value)
    assert "<redacted>" in str(raised.value)


def test_load_schema_rounds_the_timeout_up(fake_infrahub: type[_FakeInfrahub]) -> None:
    prepare.load_schema("http://127.0.0.1:8000", INFRAHUB_TOKEN, assets.schema_files(), 45.2)

    assert fake_infrahub.seen["config"].schema_converge_timeout == 46


def test_load_schema_raises_when_the_schema_has_not_converged(
    monkeypatch: pytest.MonkeyPatch, fake_infrahub: type[_FakeInfrahub]
) -> None:
    monkeypatch.setattr(fake_infrahub, "converged", False)

    with pytest.raises(DemoSchemaError) as raised:
        prepare.load_schema("http://127.0.0.1:8000", INFRAHUB_TOKEN, assets.schema_files(), 45.2)

    assert "loaded but not applied within 46 seconds" in str(raised.value)
    assert "--schema-timeout" in str(raised.value)


def test_load_schema_raises_without_the_token_when_the_convergence_check_fails(
    monkeypatch: pytest.MonkeyPatch, fake_infrahub: type[_FakeInfrahub]
) -> None:
    monkeypatch.setattr(fake_infrahub, "converged", ServerNotReachableError(address=f"http://x?token={INFRAHUB_TOKEN}"))

    with pytest.raises(DemoSchemaError) as raised:
        prepare.load_schema("http://127.0.0.1:8000", INFRAHUB_TOKEN, assets.schema_files(), 5)

    assert INFRAHUB_TOKEN not in str(raised.value)
    assert "<redacted>" in str(raised.value)


def test_load_schema_errors_never_show_url_userinfo(
    monkeypatch: pytest.MonkeyPatch, fake_infrahub: type[_FakeInfrahub]
) -> None:
    monkeypatch.setattr(fake_infrahub, "answer", ServerNotReachableError(address="http://admin:userinfo-canary@x:8000"))

    with pytest.raises(DemoSchemaError) as raised:
        prepare.load_schema("http://admin:userinfo-canary@x:8000", INFRAHUB_TOKEN, assets.schema_files(), 5)

    assert "userinfo-canary" not in str(raised.value)
    assert "http://x:8000" in str(raised.value)


def _prepare_branch(
    client: FakeSyncClient, loader: FakeSchemaLoader, branches: FakeBranches, branch: str = "netbox-demo-1"
) -> prepare.PreparationResult:
    return prepare.prepare(
        client,
        netbox_url=NETBOX_URL,
        infrahub_url=INFRAHUB_URL,
        infrahub_address="http://127.0.0.1:8000",
        token=INFRAHUB_TOKEN,
        schema_loader=loader,
        branch=branch,
        branches=branches,
    )


def test_without_a_branch_no_branch_request_is_sent() -> None:
    branches = FakeBranches()

    result = prepare.prepare(
        FakeSyncClient(),
        netbox_url=NETBOX_URL,
        infrahub_url=INFRAHUB_URL,
        infrahub_address=None,
        token=INFRAHUB_TOKEN,
        schema_loader=FakeSchemaLoader(),
        branches=branches,
    )

    assert branches.calls == []
    assert (result.branch, result.branch_state) == (None, None)


def test_a_missing_branch_is_created_at_the_address_this_machine_uses() -> None:
    client, branches = FakeSyncClient(), FakeBranches()

    result = _prepare_branch(client, FakeSchemaLoader(), branches)

    assert branches.calls == [
        ("check", "http://127.0.0.1:8000", INFRAHUB_TOKEN, "netbox-demo-1"),
        ("create", "http://127.0.0.1:8000", INFRAHUB_TOKEN, "netbox-demo-1"),
    ]
    assert (result.branch, result.branch_state, result.configuration) == ("netbox-demo-1", "created", "registered")


def test_a_branch_that_holds_the_schema_of_main_is_kept() -> None:
    loader, branches = FakeSchemaLoader(), FakeBranches()
    _prepare_branch(FakeSyncClient(), loader, branches)

    result = _prepare_branch(FakeSyncClient(), loader, branches)

    assert (result.schema, result.branch_state) == ("unchanged", "exists")
    assert [call[0] for call in branches.calls] == ["check", "create", "check", "check"]


@pytest.mark.parametrize("earlier_load", [False, True], ids=["schema-loaded", "schema-unchanged"])
def test_a_branch_with_another_schema_than_main_stops_before_the_registration(*, earlier_load: bool) -> None:
    client, loader = FakeSyncClient(), FakeSchemaLoader()
    if earlier_load:
        loader.calls.append(("earlier run", "", 0, 0.0))  # the next load answers `unchanged`
    branches = FakeBranches(names={"main": "same-schema", "netbox-demo-1": "other-schema"})

    with pytest.raises(DemoBranchError) as raised:
        _prepare_branch(client, loader, branches)

    assert "netbox-demo-1 does not hold the schema of main" in str(raised.value)
    assert "--branch" in str(raised.value)
    assert client.calls == []
    assert [call[0] for call in branches.calls] == ["check", "check"]


@pytest.mark.parametrize(
    ("earlier_load", "refused"), [(False, True), (True, False)], ids=["schema-loaded", "schema-unchanged"]
)
def test_before_infrahub_1_11_an_existing_branch_is_refused_only_when_this_run_changed_the_schema(
    *, earlier_load: bool, refused: bool
) -> None:
    client, loader = FakeSyncClient(), FakeSchemaLoader()
    if earlier_load:
        loader.calls.append(("earlier run", "", 0, 0.0))  # the next load answers `unchanged`
    branches = FakeBranches(names={"main": "same-schema", "netbox-demo-1": "same-schema"}, reports_schema=False)

    if refused:
        with pytest.raises(DemoBranchError):
            _prepare_branch(client, loader, branches)
        assert client.calls == []
    else:
        assert _prepare_branch(client, loader, branches).branch_state == "exists"


def test_the_default_branch_holds_a_schema_change_and_is_kept() -> None:
    branches = FakeBranches(reports_schema=False)

    result = _prepare_branch(FakeSyncClient(), FakeSchemaLoader(), branches, branch="main")

    assert (result.schema, result.branch_state) == ("loaded", "exists")
    assert [call[0] for call in branches.calls] == ["check"]


def test_a_branch_error_stops_before_the_schema_load_and_the_registration() -> None:
    client, loader = FakeSyncClient(), FakeSchemaLoader()

    with pytest.raises(DemoBranchError):
        _prepare_branch(client, loader, FakeBranches(error=DemoBranchError("cannot reach the branches")))

    assert (loader.calls, client.calls) == ([], [])


class _FakeGraphQL:
    """Stands in for `InfrahubClientSync` for the branch requests: records them, answers in order."""

    answers: list[dict[str, Any] | Exception] = []  # noqa: RUF012 -- test-scoped answers, reset by the fixture
    requests: list[dict[str, Any]] = []  # noqa: RUF012 -- one test-scoped record, reset by the fixture

    def __init__(self, address: str, config: infrahub_sdk.Config) -> None:
        self.address, self.config = address, config

    def execute_graphql(self, query: str, variables: dict[str, str] | None, branch_name: str) -> dict[str, Any]:
        _FakeGraphQL.requests.append(
            {
                "address": self.address,
                "token": self.config.api_token,
                "query": query,
                "variables": variables,
                "branch": branch_name,
            }
        )
        answer = _FakeGraphQL.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def fake_graphql(monkeypatch: pytest.MonkeyPatch) -> type[_FakeGraphQL]:
    monkeypatch.setattr(infrahub_sdk, "InfrahubClientSync", _FakeGraphQL)
    _FakeGraphQL.answers, _FakeGraphQL.requests = [], []
    return _FakeGraphQL


BRANCHES = {
    "Branch": [
        {"name": "main", "schema_differs_from_default_branch": False},
        {"name": "netbox-demo-1", "schema_differs_from_default_branch": False},
        {"name": "preview-smoke", "schema_differs_from_default_branch": True},
    ]
}


@pytest.mark.parametrize(
    ("name", "check"),
    [("netbox-demo-1", "same-schema"), ("preview-smoke", "other-schema"), ("other", "missing")],
)
def test_infrahub_branches_reads_each_branch_schema_check_on_main(
    fake_graphql: type[_FakeGraphQL], name: str, check: str
) -> None:
    fake_graphql.answers = [BRANCHES]

    assert prepare.InfrahubBranches().check("http://127.0.0.1:8000", INFRAHUB_TOKEN, name) == check
    assert fake_graphql.requests[0]["branch"] == "main"
    assert fake_graphql.requests[0]["token"] == INFRAHUB_TOKEN
    assert "schema_differs_from_default_branch" in fake_graphql.requests[0]["query"]


@pytest.mark.parametrize(("name", "check"), [("netbox-demo-1", "unknown"), ("other", "missing")])
def test_before_infrahub_1_11_infrahub_branches_lists_names_only(
    fake_graphql: type[_FakeGraphQL], name: str, check: str
) -> None:
    fake_graphql.answers = [
        GraphQLError(errors=[{"message": "Cannot query field 'schema_differs_from_default_branch'"}]),
        {"Branch": [{"name": "main"}, {"name": "netbox-demo-1"}]},
    ]

    assert prepare.InfrahubBranches().check("http://127.0.0.1:8000", INFRAHUB_TOKEN, name) == check
    assert "schema_differs_from_default_branch" not in fake_graphql.requests[1]["query"]


def test_infrahub_branches_check_raises_when_infrahub_refuses_for_another_reason(
    fake_graphql: type[_FakeGraphQL],
) -> None:
    fake_graphql.answers = [GraphQLError(errors=[{"message": f"token {INFRAHUB_TOKEN} permission denied"}])]

    with pytest.raises(DemoBranchError) as raised:
        prepare.InfrahubBranches().check("http://127.0.0.1:8000", INFRAHUB_TOKEN, "netbox-demo-1")

    assert "http://127.0.0.1:8000" in str(raised.value)
    assert INFRAHUB_TOKEN not in str(raised.value)
    assert len(fake_graphql.requests) == 1


def test_infrahub_branches_creates_the_branch_from_main_without_git(fake_graphql: type[_FakeGraphQL]) -> None:
    fake_graphql.answers = [{"BranchCreate": {"ok": True}}]

    prepare.InfrahubBranches().create("http://127.0.0.1:8000", INFRAHUB_TOKEN, "netbox-demo-1")

    request = fake_graphql.requests[0]
    assert (request["branch"], request["variables"]) == ("main", {"name": "netbox-demo-1"})
    assert "BranchCreate" in request["query"]
    assert "sync_with_git: false" in request["query"]


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param({"BranchCreate": {"ok": False}}, id="not-ok"),
        pytest.param(ServerNotReachableError(address=f"http://x?token={INFRAHUB_TOKEN}"), id="unreachable"),
        pytest.param(GraphQLError(errors=[{"message": f"token {INFRAHUB_TOKEN} denied"}]), id="refused"),
    ],
)
def test_infrahub_branches_create_errors_raise_without_the_token(
    fake_graphql: type[_FakeGraphQL], answer: dict[str, Any] | Exception
) -> None:
    fake_graphql.answers = [answer]

    with pytest.raises(DemoBranchError) as raised:
        prepare.InfrahubBranches().create("http://127.0.0.1:8000", INFRAHUB_TOKEN, "netbox-demo-1")

    assert "http://127.0.0.1:8000" in str(raised.value)
    assert INFRAHUB_TOKEN not in str(raised.value)
