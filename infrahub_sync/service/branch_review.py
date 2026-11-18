"""Create and delete the branch of a scheduled run, and open, read and close its proposed change.

The five operations of research R15 and contracts/branch-review.md of spec 021, plus the check
that the destination's default branch is `main`. The worker of a scheduled run with branch review
calls them, with an Infrahub client that `build_client` makes from the destination settings, with
the run's branch as its default branch. Building the client reads nothing from Infrahub, so it
can be built before the run's branch exists.

Where each request goes:

- The branch requests (the branch list, create and delete) are sent to `main`. Infrahub answers
  404 to a request sent to a branch that does not exist (`graphql/app.py:133-138` in the
  Infrahub source), and the run's branch does not exist when it is created. `BranchCreate` and
  `BranchDelete` are exempt from the global permission `edit_default_branch`
  (`default_branch_checker.py:20-29`).
- The requests about a proposed change are sent to the client's default branch, the run's
  branch, so the account needs the object permissions of other branches only (research,
  "Checked since, for R11 and R15"). `read_proposed_change` takes another branch for a read made
  before the run's branch exists.

Every failure is raised as a `BranchReviewError` subclass with a fixed message, `from` the SDK
error, and `failure_cause` maps each one to a failure class (ADR 0021). No text from Infrahub is
put into an error message here: the SDK error stays the cause, and the failure record reads only
its class.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast, get_args

import httpx
import structlog
from infrahub_sdk import Config, InfrahubClientSync
from infrahub_sdk.exceptions import Error as InfrahubSDKError
from infrahub_sdk.exceptions import URLNotFoundError

from infrahub_sync.adapters.infrahub import infrahub_sdk_config, resolved_endpoint
from infrahub_sync.configuration.credentials import CredentialConfigurationError, select_runtime_credential

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = structlog.get_logger(__name__)

# Infrahub accepts a proposed change only to this branch (`graphql/mutations/proposed_change.py:89-129`).
DEFAULT_BRANCH = "main"

ProposedChangeState = Literal["open", "approved", "merging", "merged", "closed", "canceled", "missing"]
"""What `read_proposed_change` returns: Infrahub's state, `approved` for an open one with an
approver, or `missing` when Infrahub holds no proposed change with the identifier (a person
deleted it)."""

PROPOSED_CHANGE_STATES: tuple[ProposedChangeState, ...] = get_args(ProposedChangeState)
# The states Infrahub stores (`proposed_change/constants.py:14-19`); `approved` and `missing` are derived.
_INFRAHUB_STATES = frozenset({"open", "merging", "merged", "closed", "canceled"})
# A proposed change in one of these states needs no close (contracts/branch-review.md, rule 4).
_DONE_STATES: frozenset[str] = frozenset({"closed", "canceled", "merged", "missing"})

_BRANCH_LIST = "query SyncBranchList { Branch { name is_default } }"
_BRANCH_CREATE = (
    "mutation SyncBranchCreate($name: String!) "
    "{ BranchCreate(wait_until_completion: true, data: {name: $name, sync_with_git: false}) { ok } }"
)
_BRANCH_DELETE = "mutation SyncBranchDelete($name: String!) { BranchDelete(wait_until_completion: true, data: {name: $name}) { ok } }"
_PROPOSED_CHANGE_CREATE = (
    "mutation SyncProposedChangeCreate("
    "$name: String!, $description: String!, $source_branch: String!, $destination_branch: String!) "
    "{ CoreProposedChangeCreate(data: {name: {value: $name}, description: {value: $description}, "
    "source_branch: {value: $source_branch}, destination_branch: {value: $destination_branch}}) "
    "{ ok object { id } } }"
)
_PROPOSED_CHANGE_READ = (
    "query SyncProposedChangeRead($id: ID!) { CoreProposedChange(ids: [$id]) "
    "{ edges { node { id state { value } approved_by { edges { node { id } } } } } } }"
)
_CHANGE_COMMENT_CREATE = (
    "mutation SyncChangeCommentCreate($text: String!, $change: String!) "
    "{ CoreChangeCommentCreate(data: {text: {value: $text}, change: {id: $change}}) { ok } }"
)
# Closing is an update of the state: Infrahub has no close mutation (`proposed_change.py:147-227`).
_PROPOSED_CHANGE_CLOSE = (
    "mutation SyncProposedChangeClose($id: String!) "
    '{ CoreProposedChangeUpdate(data: {id: $id, state: {value: "closed"}}) { ok } }'
)
# What a branch request raises when it fails: an SDK error, or an HTTP status error that the SDK
# raises as it is for a status other than 401, 403 and 404 (a 502 of a proxy, for example). The
# branch list then decides whether the branch exists (rule 4).
_REQUEST_ERRORS: tuple[type[Exception], ...] = (InfrahubSDKError, httpx.HTTPError)


class BranchReviewError(Exception):
    """A branch review operation failed. The message is fixed; the SDK error is the cause."""

    message: ClassVar[str] = "A branch review operation failed in Infrahub"

    def __init__(self) -> None:
        super().__init__(self.message)


class DestinationDefaultBranchNotMainError(BranchReviewError):
    """The destination's default branch is not `main`, so it cannot receive a proposed change."""

    message = "The default branch of the destination is not main"


class BranchListFailedError(BranchReviewError):
    """Infrahub answered the branch list with data that does not have the shape of a branch list."""

    message = "Infrahub answered the branch list in a form that cannot be read"


class BranchCreateFailedError(BranchReviewError):
    """Infrahub did not create the run's branch, and the branch does not exist."""

    message = "Infrahub did not create the branch of the scheduled run"


class BranchDeleteFailedError(BranchReviewError):
    """Infrahub did not delete a branch of the schedule, and the branch still exists."""

    message = "Infrahub did not delete a branch of the schedule"


class ProposedChangeOpenFailedError(BranchReviewError):
    """Infrahub did not open the proposed change of the run."""

    message = "Infrahub did not open the proposed change of the scheduled run"


class ProposedChangeReadFailedError(BranchReviewError):
    """The state of a proposed change of the schedule could not be read."""

    message = "The state of a proposed change of the schedule could not be read from Infrahub"


class ProposedChangeCloseFailedError(BranchReviewError):
    """Infrahub did not close a proposed change of the schedule."""

    message = "Infrahub did not close a proposed change of the schedule"


@dataclass(frozen=True, slots=True)
class CloseResult:
    """What `close_proposed_change` found and did.

    `state` is the state read before acting. `closed_now` is true when this call closed it. The
    data model's `branch_review.kept` cause comes from `state` when `closed_now` is false:
    `approved`, `merged` (also for `merging`) or `closed`.
    """

    state: ProposedChangeState
    closed_now: bool

    @property
    def done(self) -> bool:
        """The proposed change is closed, canceled or merged, by this call or before it."""
        return self.closed_now or self.state in _DONE_STATES

    @property
    def left_in_place(self) -> bool:
        """The proposed change stays: a person approved it, or Infrahub is merging it."""
        return not self.done


def build_client(settings: Mapping[str, Any], branch: str) -> InfrahubClientSync:
    """An Infrahub client of the destination, with `branch` as its default branch.

    It reads the address and the token as the destination adapter does, and reads nothing from
    Infrahub, so `branch` need not exist yet. A missing address or token raises
    `CredentialConfigurationError`, which the failure record classes as `credential_missing`.
    """
    url, _declared_branch = resolved_endpoint(settings, branch)
    token = select_runtime_credential(settings, "token", ("INFRAHUB_API_TOKEN",))
    if not url or not token:
        msg = "The address or the token of the Infrahub destination is not set"
        raise CredentialConfigurationError(msg)
    sdk_config = infrahub_sdk_config(settings, url=url, token=token, branch=branch)
    return InfrahubClientSync(address=url, config=Config(**sdk_config))


def _branches(client: InfrahubClientSync) -> dict[str, bool]:
    """Each branch of the destination, by name, and whether it is the default branch.

    An answer that does not have the shape of a branch list raises `BranchListFailedError`.
    """
    data = client.execute_graphql(
        query=_BRANCH_LIST, branch_name=DEFAULT_BRANCH, tracker="sync-branch-list", operation_name="SyncBranchList"
    )
    try:
        return {str(branch["name"]): bool(branch.get("is_default")) for branch in data["Branch"]}
    except (KeyError, TypeError, AttributeError):
        raise BranchListFailedError from None


def check_default_branch_is_main(client: InfrahubClientSync) -> None:
    """Raise `DestinationDefaultBranchNotMainError` unless the destination's default branch is `main`.

    A run with branch review calls it before it creates its branch (contracts/branch-review.md,
    rule 6). A 404 to the branch list sent to `main` means that `main` does not exist. Any other
    failure of the request is raised as the SDK raised it, for the transport classification.
    """
    try:
        branches = _branches(client)
    except URLNotFoundError as exc:
        raise DestinationDefaultBranchNotMainError from exc
    if not branches.get(DEFAULT_BRANCH, False):
        raise DestinationDefaultBranchNotMainError


def _mutation_ok(data: object, mutation: str) -> bool:
    """Whether the answer to a mutation reports `ok: true`.

    Infrahub can answer a mutation it did not carry out with HTTP success and `ok: false`, so
    only a payload whose `ok` is `true` counts as done.
    """
    if not isinstance(data, dict):
        return False
    payload = cast("dict[str, Any]", data).get(mutation)
    return isinstance(payload, dict) and payload.get("ok") is True


def _branch_exists(client: InfrahubClientSync, name: str) -> bool | None:
    """Whether the branch exists, or `None` when Infrahub cannot tell."""
    try:
        return name in _branches(client)
    except (*_REQUEST_ERRORS, BranchListFailedError):
        return None


def create_branch(client: InfrahubClientSync, name: str) -> None:
    """Create the branch `name` from the default branch, with no synchronization to Git.

    A branch of that name that exists counts as created (rule 4): when Infrahub refuses the
    create, the branch list decides, so no message of Infrahub is read.
    """
    variables = {"name": name}
    failure: Exception | None = None
    try:
        data = client.execute_graphql(
            query=_BRANCH_CREATE, variables=variables, branch_name=DEFAULT_BRANCH, tracker="sync-branch-create"
        )
    except _REQUEST_ERRORS as exc:
        failure = exc
    else:
        if _mutation_ok(data, "BranchCreate"):
            logger.info("branch_review.branch_created", branch=name)
            return
    if _branch_exists(client, name):
        logger.info("branch_review.branch_exists", branch=name)
        return
    raise BranchCreateFailedError from failure


def delete_branch(client: InfrahubClientSync, name: str) -> None:
    """Delete the branch `name`. A branch that no longer exists counts as deleted (rule 4).

    Deleting a branch sets its proposed changes to `canceled`, so the caller closes a proposed
    change before it deletes the branch (rule 7).
    """
    variables = {"name": name}
    failure: Exception | None = None
    try:
        data = client.execute_graphql(
            query=_BRANCH_DELETE, variables=variables, branch_name=DEFAULT_BRANCH, tracker="sync-branch-delete"
        )
    except _REQUEST_ERRORS as exc:
        failure = exc
    else:
        if _mutation_ok(data, "BranchDelete"):
            logger.info("branch_review.branch_deleted", branch=name)
            return
    if _branch_exists(client, name) is False:
        logger.info("branch_review.branch_missing", branch=name)
        return
    raise BranchDeleteFailedError from failure


def open_proposed_change(client: InfrahubClientSync, *, branch: str, title: str, description: str) -> str:
    """Open a proposed change from `branch` to `main`, and return its identifier."""
    variables = {
        "name": title,
        "description": description,
        "source_branch": branch,
        "destination_branch": DEFAULT_BRANCH,
    }
    try:
        data = client.execute_graphql(
            query=_PROPOSED_CHANGE_CREATE, variables=variables, tracker="sync-proposed-change-create"
        )
    except InfrahubSDKError as exc:
        raise ProposedChangeOpenFailedError from exc
    if not _mutation_ok(data, "CoreProposedChangeCreate"):
        raise ProposedChangeOpenFailedError
    created = data["CoreProposedChangeCreate"].get("object")
    identifier = created.get("id") if isinstance(created, dict) else None
    if not isinstance(identifier, str) or not identifier:
        raise ProposedChangeOpenFailedError
    logger.info("branch_review.proposed_change_opened", branch=branch, proposed_change_id=identifier)
    return identifier


def read_proposed_change(
    client: InfrahubClientSync, proposed_change_id: str, *, branch: str | None = None
) -> ProposedChangeState:
    """The state of a proposed change: `approved` when it is open and an account approved it.

    The request goes to `branch`, else to the client's default branch. A proposed change that
    does not exist reads as `missing`: a person deleted it, and the schedule leaves its branch
    alone. Only an answer whose `edges` is an empty list means that; a failed request, an answer
    that does not have the shape of the query, or a state that Infrahub does not define, raises
    `ProposedChangeReadFailedError`.
    """
    try:
        data = client.execute_graphql(
            query=_PROPOSED_CHANGE_READ,
            variables={"id": proposed_change_id},
            branch_name=branch,
            tracker="sync-proposed-change-read",
            operation_name="SyncProposedChangeRead",
        )
    except InfrahubSDKError as exc:
        raise ProposedChangeReadFailedError from exc
    try:
        edges = data["CoreProposedChange"]["edges"]
        if not isinstance(edges, list):
            raise ProposedChangeReadFailedError
        if not edges:
            return "missing"
        node = edges[0]["node"]
        state = node["state"]["value"]
        approvers = node["approved_by"]["edges"]
    except (KeyError, TypeError, AttributeError):
        raise ProposedChangeReadFailedError from None
    if not isinstance(approvers, list):
        # An unreadable list of approvers must not read as none: the change would then be closed.
        raise ProposedChangeReadFailedError
    if state not in _INFRAHUB_STATES:
        raise ProposedChangeReadFailedError
    if state == "open" and approvers:
        return "approved"
    return state


def close_proposed_change(client: InfrahubClientSync, proposed_change_id: str, comment: str) -> CloseResult:
    """Close an open proposed change with a comment; leave any other one as it is.

    It reads the state first, because Infrahub refuses an update of a proposed change that is not
    open. Only an open proposed change with no approver gets the comment and then the state
    `closed`. `closed`, `canceled` and `merged` count as done. `merging`, and `approved` (a person
    approved it while the run was writing), are kept (FR-021).
    """
    try:
        state = read_proposed_change(client, proposed_change_id)
    except ProposedChangeReadFailedError as exc:
        raise ProposedChangeCloseFailedError from exc
    if state != "open":
        logger.info("branch_review.proposed_change_not_closed", proposed_change_id=proposed_change_id, state=state)
        return CloseResult(state=state, closed_now=False)
    try:
        commented = client.execute_graphql(
            query=_CHANGE_COMMENT_CREATE,
            variables={"text": comment, "change": proposed_change_id},
            tracker="sync-change-comment-create",
        )
        if not _mutation_ok(commented, "CoreChangeCommentCreate"):
            raise ProposedChangeCloseFailedError
        closed = client.execute_graphql(
            query=_PROPOSED_CHANGE_CLOSE, variables={"id": proposed_change_id}, tracker="sync-proposed-change-close"
        )
    except InfrahubSDKError as exc:
        raise ProposedChangeCloseFailedError from exc
    if not _mutation_ok(closed, "CoreProposedChangeUpdate"):
        raise ProposedChangeCloseFailedError
    logger.info("branch_review.proposed_change_closed", proposed_change_id=proposed_change_id)
    return CloseResult(state=state, closed_now=True)
