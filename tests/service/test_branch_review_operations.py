"""The branch and proposed change operations of a scheduled run, against a fake Infrahub client.

contracts/branch-review.md and research R15 of spec 021. The fake records the GraphQL operation
and the branch of every request, so the tests also check where each request is sent: the branch
requests go to `main`, because a request to a branch that does not exist answers 404 in Infrahub
(`graphql/app.py:133-138`), and the requests about a proposed change go to the client's default
branch, the run's branch.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pytest
from infrahub_sdk.exceptions import (
    AuthenticationError,
    GraphQLError,
    ServerNotReachableError,
    ServerNotResponsiveError,
    URLNotFoundError,
)

from infrahub_sync.client.models import FAILURE_TYPES
from infrahub_sync.configuration.credentials import CredentialConfigurationError
from infrahub_sync.service import branch_review
from infrahub_sync.service.branch_review import (
    BranchCreateFailedError,
    BranchDeleteFailedError,
    CloseResult,
    DestinationDefaultBranchNotMainError,
    ProposedChangeCloseFailedError,
    ProposedChangeOpenFailedError,
    ProposedChangeReadFailedError,
    build_client,
    check_default_branch_is_main,
    close_proposed_change,
    create_branch,
    delete_branch,
    open_proposed_change,
    read_proposed_change,
)
from infrahub_sync.service.failure_cause import failure_class, failure_record

if TYPE_CHECKING:
    from collections.abc import Callable

    from infrahub_sdk import InfrahubClientSync

RUN_BRANCH = "sync/nightly-20261118-a1b2c3"
PROPOSED_CHANGE_ID = "18a7c3e0-0000-0000-0000-000000000001"
# Text that only Infrahub would send. It must never reach a failure record or a typed error.
INFRAHUB_TEXT = "infrahub-said-this-7f3a"
TOKEN = "token-must-not-leak-91c2"  # noqa: S105

_OPERATION = re.compile(r"^\s*(?:query|mutation)\s+(\w+)")


@dataclass
class Call:
    """One request the fake received."""

    operation: str
    branch: str
    variables: dict[str, Any]


@dataclass
class FakeInfrahub:
    """A fake `InfrahubClientSync` that answers `execute_graphql` from one handler per operation."""

    default_branch: str = RUN_BRANCH
    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)

    def execute_graphql(
        self,
        query: str,
        variables: dict | None = None,
        branch_name: str | None = None,
        **_: str | None,
    ) -> dict[str, Any]:
        match = _OPERATION.match(query)
        assert match is not None, query
        operation = match.group(1)
        self.calls.append(Call(operation, branch_name or self.default_branch, dict(variables or {})))
        return self.handlers[operation](dict(variables or {}))

    def operations(self) -> list[str]:
        return [call.operation for call in self.calls]


def _sdk(fake: FakeInfrahub) -> InfrahubClientSync:
    """The fake, typed as the client the operations take: they call only `execute_graphql`."""
    return cast("InfrahubClientSync", fake)


def _graphql_error() -> GraphQLError:
    return GraphQLError(
        errors=[{"message": f"{INFRAHUB_TEXT} {TOKEN}"}],
        query=f"mutation {{ x(token: {TOKEN!r}) }}",
        variables={"token": TOKEN},
    )


def _raise(exc: BaseException) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def handler(_variables: dict[str, Any]) -> dict[str, Any]:
        raise exc

    return handler


def _branches(*names: str, default: str = "main") -> Callable[[dict[str, Any]], dict[str, Any]]:
    def handler(_variables: dict[str, Any]) -> dict[str, Any]:
        return {"Branch": [{"name": name, "is_default": name == default} for name in names]}

    return handler


def _proposed_change(state: str, approvers: int = 0) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def handler(variables: dict[str, Any]) -> dict[str, Any]:
        return {
            "CoreProposedChange": {
                "edges": [
                    {
                        "node": {
                            "id": variables["id"],
                            "state": {"value": state},
                            "approved_by": {"edges": [{"node": {"id": f"account-{n}"}} for n in range(approvers)]},
                        }
                    }
                ]
            }
        }

    return handler


def _ok(key: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda _variables: {key: {"ok": True}}


def _assert_no_infrahub_text(exc: BaseException) -> None:
    """The typed error and the failure record hold no text from Infrahub and no token."""
    record = failure_record("sync", exc, secrets=())
    rendered = json.dumps(record) + str(exc) + repr(exc)
    assert INFRAHUB_TEXT not in rendered
    assert TOKEN not in rendered


# --- Building the client ---------------------------------------------------------------------


def test_build_client_uses_the_run_branch_and_reads_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INFRAHUB_API_TOKEN", raising=False)
    monkeypatch.delenv("INFRAHUB_ADDRESS", raising=False)
    monkeypatch.delenv("INFRAHUB_URL", raising=False)
    # An address that cannot resolve: any request made while building would raise.
    settings = {"url": "http://infrahub.invalid:8000", "token": TOKEN, "verify_ssl": False}

    client = build_client(settings, RUN_BRANCH)

    assert client.default_branch == RUN_BRANCH
    assert client.config.address == "http://infrahub.invalid:8000"
    assert client.config.api_token == TOKEN
    assert client.config.tls_insecure is True
    assert client.config.default_branch_from_git is False


def test_build_client_of_a_registered_package_reads_no_worker_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "worker-environment-token")
    monkeypatch.setenv("INFRAHUB_ADDRESS", "http://worker-environment.invalid")
    settings = {
        "url": "http://infrahub.invalid:8000",
        "token": TOKEN,
        "_infrahub_sync_registered_context": True,
    }

    client = build_client(settings, RUN_BRANCH)

    assert client.config.address == "http://infrahub.invalid:8000"
    assert client.config.api_token == TOKEN
    assert client.config.tls_insecure is False


def test_build_client_without_a_token_is_a_missing_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INFRAHUB_API_TOKEN", raising=False)
    settings = {"url": "http://infrahub.invalid:8000", "_infrahub_sync_registered_context": True}

    with pytest.raises(CredentialConfigurationError) as caught:
        build_client(settings, RUN_BRANCH)

    assert failure_class(caught.value) == "credential_missing"


# --- The default branch of the destination ---------------------------------------------------


def test_default_branch_main_passes_and_is_read_on_main() -> None:
    client = FakeInfrahub(handlers={"SyncBranchList": _branches("main", "other")})

    check_default_branch_is_main(_sdk(client))

    assert [(call.operation, call.branch) for call in client.calls] == [("SyncBranchList", "main")]


def test_default_branch_other_than_main_fails_with_its_own_class() -> None:
    client = FakeInfrahub(handlers={"SyncBranchList": _branches("main", "trunk", default="trunk")})

    with pytest.raises(DestinationDefaultBranchNotMainError) as caught:
        check_default_branch_is_main(_sdk(client))

    assert failure_class(caught.value) == "destination_default_branch_not_main"
    _assert_no_infrahub_text(caught.value)


def test_destination_without_a_main_branch_fails_with_the_same_class() -> None:
    # Infrahub answers 404 to a request sent to a branch that does not exist.
    client = FakeInfrahub(handlers={"SyncBranchList": _raise(URLNotFoundError(url=f"http://x/graphql/main?{TOKEN}"))})

    with pytest.raises(DestinationDefaultBranchNotMainError) as caught:
        check_default_branch_is_main(_sdk(client))

    assert failure_class(caught.value) == "destination_default_branch_not_main"
    _assert_no_infrahub_text(caught.value)


def test_default_branch_check_that_cannot_reach_infrahub_is_a_transport_failure() -> None:
    client = FakeInfrahub(handlers={"SyncBranchList": _raise(ServerNotReachableError(address="http://x"))})

    with pytest.raises(ServerNotReachableError) as caught:
        check_default_branch_is_main(_sdk(client))

    assert failure_class(caught.value) == "system_unreachable"


# --- Create a branch -------------------------------------------------------------------------


def test_create_branch_sends_the_mutation_to_main() -> None:
    client = FakeInfrahub(handlers={"SyncBranchCreate": lambda _v: {"BranchCreate": {"ok": True}}})

    create_branch(_sdk(client), RUN_BRANCH)

    assert [(call.operation, call.branch, call.variables) for call in client.calls] == [
        ("SyncBranchCreate", "main", {"name": RUN_BRANCH}),
    ]


def test_create_branch_that_exists_counts_as_created() -> None:
    client = FakeInfrahub(
        handlers={"SyncBranchCreate": _raise(_graphql_error()), "SyncBranchList": _branches("main", RUN_BRANCH)}
    )

    create_branch(_sdk(client), RUN_BRANCH)

    assert client.operations() == ["SyncBranchCreate", "SyncBranchList"]
    assert all(call.branch == "main" for call in client.calls)


def test_create_branch_refused_maps_to_its_class() -> None:
    client = FakeInfrahub(handlers={"SyncBranchCreate": _raise(_graphql_error()), "SyncBranchList": _branches("main")})

    with pytest.raises(BranchCreateFailedError) as caught:
        create_branch(_sdk(client), RUN_BRANCH)

    assert failure_class(caught.value) == "branch_create_failed"
    _assert_no_infrahub_text(caught.value)


# --- Delete a branch -------------------------------------------------------------------------


def test_delete_branch_sends_the_mutation_to_main() -> None:
    client = FakeInfrahub(handlers={"SyncBranchDelete": _ok("BranchDelete")})

    delete_branch(_sdk(client), RUN_BRANCH)

    assert [(call.operation, call.branch, call.variables) for call in client.calls] == [
        ("SyncBranchDelete", "main", {"name": RUN_BRANCH}),
    ]


def test_delete_branch_that_is_missing_counts_as_deleted() -> None:
    client = FakeInfrahub(handlers={"SyncBranchDelete": _raise(_graphql_error()), "SyncBranchList": _branches("main")})

    delete_branch(_sdk(client), RUN_BRANCH)

    assert client.operations() == ["SyncBranchDelete", "SyncBranchList"]


def test_delete_branch_refused_maps_to_its_class() -> None:
    client = FakeInfrahub(
        handlers={"SyncBranchDelete": _raise(_graphql_error()), "SyncBranchList": _branches("main", RUN_BRANCH)}
    )

    with pytest.raises(BranchDeleteFailedError) as caught:
        delete_branch(_sdk(client), RUN_BRANCH)

    assert failure_class(caught.value) == "branch_delete_failed"
    _assert_no_infrahub_text(caught.value)


# --- Open a proposed change ------------------------------------------------------------------


def test_open_proposed_change_targets_main_from_the_run_branch() -> None:
    def created(_variables: dict[str, Any]) -> dict[str, Any]:
        return {"CoreProposedChangeCreate": {"ok": True, "object": {"id": PROPOSED_CHANGE_ID}}}

    client = FakeInfrahub(handlers={"SyncProposedChangeCreate": created})

    identifier = open_proposed_change(
        _sdk(client), branch=RUN_BRANCH, title="Scheduled sync nightly 2026-11-18T02:00:00Z", description="run r-1"
    )

    assert identifier == PROPOSED_CHANGE_ID
    [call] = client.calls
    assert call.branch == RUN_BRANCH
    assert call.variables == {
        "name": "Scheduled sync nightly 2026-11-18T02:00:00Z",
        "description": "run r-1",
        "source_branch": RUN_BRANCH,
        "destination_branch": "main",
    }


def test_open_proposed_change_refused_maps_to_its_class() -> None:
    client = FakeInfrahub(handlers={"SyncProposedChangeCreate": _raise(_graphql_error())})

    with pytest.raises(ProposedChangeOpenFailedError) as caught:
        open_proposed_change(_sdk(client), branch=RUN_BRANCH, title="t", description="d")

    assert failure_class(caught.value) == "proposed_change_open_failed"
    _assert_no_infrahub_text(caught.value)


def test_open_proposed_change_without_an_identifier_fails() -> None:
    client = FakeInfrahub(
        handlers={"SyncProposedChangeCreate": lambda _v: {"CoreProposedChangeCreate": {"ok": True, "object": None}}}
    )

    with pytest.raises(ProposedChangeOpenFailedError):
        open_proposed_change(_sdk(client), branch=RUN_BRANCH, title="t", description="d")


# --- Read a proposed change ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "approvers", "expected"),
    [
        ("open", 0, "open"),
        ("open", 1, "approved"),
        ("open", 2, "approved"),
        ("merging", 1, "merging"),
        ("merged", 1, "merged"),
        ("closed", 1, "closed"),
        ("canceled", 0, "canceled"),
    ],
)
def test_read_proposed_change(state: str, approvers: int, expected: str) -> None:
    client = FakeInfrahub(handlers={"SyncProposedChangeRead": _proposed_change(state, approvers)})

    assert read_proposed_change(_sdk(client), PROPOSED_CHANGE_ID) == expected
    [call] = client.calls
    assert call.branch == RUN_BRANCH
    assert call.variables == {"id": PROPOSED_CHANGE_ID}


def test_read_proposed_change_on_a_named_branch() -> None:
    client = FakeInfrahub(handlers={"SyncProposedChangeRead": _proposed_change("open")})

    read_proposed_change(_sdk(client), PROPOSED_CHANGE_ID, branch="main")

    assert client.calls[0].branch == "main"


@pytest.mark.parametrize(
    "handler",
    [
        _raise(_graphql_error()),
        lambda _v: {"CoreProposedChange": {"edges": []}},
        _proposed_change(INFRAHUB_TEXT),
    ],
    ids=["refused", "not-found", "unknown-state"],
)
def test_read_proposed_change_failure_maps_to_its_class(handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    client = FakeInfrahub(handlers={"SyncProposedChangeRead": handler})

    with pytest.raises(ProposedChangeReadFailedError) as caught:
        read_proposed_change(_sdk(client), PROPOSED_CHANGE_ID)

    assert failure_class(caught.value) == "proposed_change_read_failed"
    _assert_no_infrahub_text(caught.value)


# --- Close a proposed change -----------------------------------------------------------------


def test_close_open_proposed_change_comments_then_closes() -> None:
    client = FakeInfrahub(
        handlers={
            "SyncProposedChangeRead": _proposed_change("open"),
            "SyncChangeCommentCreate": _ok("CoreChangeCommentCreate"),
            "SyncProposedChangeClose": _ok("CoreProposedChangeUpdate"),
        }
    )

    result = close_proposed_change(_sdk(client), PROPOSED_CHANGE_ID, "Replaced by pc-2 (run r-2)")

    assert result == CloseResult(state="open", closed_now=True)
    assert result.done
    assert not result.kept
    assert [(call.operation, call.variables) for call in client.calls] == [
        ("SyncProposedChangeRead", {"id": PROPOSED_CHANGE_ID}),
        ("SyncChangeCommentCreate", {"text": "Replaced by pc-2 (run r-2)", "change": PROPOSED_CHANGE_ID}),
        ("SyncProposedChangeClose", {"id": PROPOSED_CHANGE_ID}),
    ]
    assert all(call.branch == RUN_BRANCH for call in client.calls)


@pytest.mark.parametrize(
    ("state", "approvers", "done", "kept"),
    [
        ("closed", 0, True, False),
        ("canceled", 0, True, False),
        ("merged", 0, True, False),
        ("merging", 0, False, True),
        ("open", 1, False, True),
    ],
    ids=["closed", "canceled", "merged", "merging", "approved"],
)
def test_close_acts_only_on_an_open_proposed_change(state: str, approvers: int, *, done: bool, kept: bool) -> None:
    client = FakeInfrahub(handlers={"SyncProposedChangeRead": _proposed_change(state, approvers)})

    result = close_proposed_change(_sdk(client), PROPOSED_CHANGE_ID, "Replaced by pc-2 (run r-2)")

    assert not result.closed_now
    assert result.done is done
    assert result.kept is kept
    assert client.operations() == ["SyncProposedChangeRead"]


@pytest.mark.parametrize("failing", ["SyncProposedChangeRead", "SyncChangeCommentCreate", "SyncProposedChangeClose"])
def test_close_failure_maps_to_its_class(failing: str) -> None:
    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
        "SyncProposedChangeRead": _proposed_change("open"),
        "SyncChangeCommentCreate": _ok("CoreChangeCommentCreate"),
        "SyncProposedChangeClose": _ok("CoreProposedChangeUpdate"),
    }
    handlers[failing] = _raise(_graphql_error())
    client = FakeInfrahub(handlers=handlers)

    with pytest.raises(ProposedChangeCloseFailedError) as caught:
        close_proposed_change(_sdk(client), PROPOSED_CHANGE_ID, "Replaced by pc-2 (run r-2)")

    assert failure_class(caught.value) == "proposed_change_close_failed"
    _assert_no_infrahub_text(caught.value)


# --- Transport failures win over the operation's class ---------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ServerNotReachableError(address=f"http://x?{TOKEN}"), "system_unreachable"),
        (ServerNotResponsiveError(url=f"http://x?{TOKEN}", timeout=60), "system_timeout"),
        (AuthenticationError(INFRAHUB_TEXT), "token_rejected"),
    ],
    ids=["unreachable", "timeout", "token"],
)
def test_transport_failure_of_an_operation_keeps_the_transport_class(error: BaseException, expected: str) -> None:
    client = FakeInfrahub(handlers={"SyncBranchCreate": _raise(error), "SyncBranchList": _raise(error)})

    with pytest.raises(BranchCreateFailedError) as caught:
        create_branch(_sdk(client), RUN_BRANCH)

    assert failure_class(caught.value) == expected


def test_every_operation_class_is_a_failure_type() -> None:
    for name in (
        "destination_default_branch_not_main",
        "branch_create_failed",
        "branch_delete_failed",
        "proposed_change_open_failed",
        "proposed_change_read_failed",
        "proposed_change_close_failed",
    ):
        assert name in FAILURE_TYPES


def test_failure_record_of_an_operation_names_the_destination_and_a_template() -> None:
    client = FakeInfrahub(handlers={"SyncBranchCreate": _raise(_graphql_error()), "SyncBranchList": _branches("main")})
    with pytest.raises(BranchCreateFailedError) as caught:
        create_branch(_sdk(client), RUN_BRANCH)

    record = failure_record("sync", caught.value)

    assert record["failure_type"] == "branch_create_failed"
    assert record["system"] == {"role": "destination", "adapter": "infrahub"}
    assert record["cause"]
    assert record["next_action"]
    assert record["infrahub_message"] is None


def test_module_exposes_no_infrahub_text_in_its_fixed_messages() -> None:
    for error_class in (
        DestinationDefaultBranchNotMainError,
        BranchCreateFailedError,
        BranchDeleteFailedError,
        ProposedChangeOpenFailedError,
        ProposedChangeReadFailedError,
        ProposedChangeCloseFailedError,
    ):
        assert issubclass(error_class, branch_review.BranchReviewError)
        assert str(error_class()) == error_class.message
