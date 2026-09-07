"""What every clean-host check is given, and the two instruments it uses.

These run inside the candidate image, on the deployment's own network, in a
throwaway container. That is the only interpreter this gate has: the host it
qualifies on carries none, which is the property being demonstrated.

Two instruments, deliberately. The deployment is reached through the client the
product ships, because that is the surface an operator has and the shapes are
its own. Anything a check claims did *not* happen, or survived, is read from the
thing itself instead — the destination's own API, PostgreSQL, the object store.
A client that misread a response would otherwise confirm a negative in the same
direction as the bug that caused it.

A check reports by exit status. What it prints on the error stream is the
sentence the driver shows, so no value read out of the deployment is printed:
some of them are credentials.
"""

from __future__ import annotations

import os
import pathlib
import sys
import uuid
from typing import Literal, NoReturn

import httpx
import yaml
from infrahub_sdk import Config, InfrahubClientSync
from infrahub_sdk.node import Attribute

from infrahub_sync.client import RunTerminalError, RunWaitTimeoutError, SyncClient, SyncClientError
from infrahub_sync.client.models import CreateRunRequest, PublicRunResource, RunResource

# The two operations a run request may name, as the model declares them.
Operation = Literal["plan", "sync"]

# A run crosses two services and a queue, so the bound is generous. It is a
# bound rather than a wait: a run that never arrives is a failure with a name.
RUN_TIMEOUT_SECONDS = float(os.environ.get("CLEAN_HOST_RUN_TIMEOUT_SECONDS", "600"))
POLL_SECONDS = 3.0


def refuse(sentence: str) -> NoReturn:
    """End this check with the sentence the driver will report."""
    print(sentence, file=sys.stderr)
    raise SystemExit(1)


def deployment() -> SyncClient:
    """The deployment, through the client an operator uses."""
    return SyncClient.from_environment(timeout=60.0)


def destination() -> httpx.Client:
    """The destination itself, reached without going through the deployment."""
    return httpx.Client(
        base_url=os.environ["INFRAHUB_DESTINATION_URL"],
        headers={"X-INFRAHUB-KEY": os.environ["INFRAHUB_DESTINATION_TOKEN"]},
        timeout=30,
    )


def sdk() -> InfrahubClientSync:
    """The destination through the SDK the candidate image ships."""
    return InfrahubClientSync(
        address=os.environ["INFRAHUB_DESTINATION_URL"],
        config=Config(api_token=os.environ["INFRAHUB_DESTINATION_TOKEN"], timeout=60),
    )


BUNDLED_CONFIGURATION = "infrahub-sync-qualification"


def bundled(client: SyncClient) -> tuple[str, int]:
    """Return the configuration this deployment's own bootstrap registered.

    Identified by the name it declares, because rows that need a package of
    their own register one, and a run against the wrong configuration would not
    be a statement about what the bundle deploys.
    """
    for summary in client.list_configs():
        versions = client.list_config_versions(summary.config_id)
        if versions and versions[-1].declared_content.get("configuration", {}).get("name") == BUNDLED_CONFIGURATION:
            return summary.config_id, versions[-1].registry_version
    refuse(f"this deployment has no configuration registered as {BUNDLED_CONFIGURATION}")


def run_request(client: SyncClient, operation: Operation, reason: str) -> CreateRunRequest:
    """Return a request for one run against the configuration bootstrap registered."""
    config_id, registry_version = bundled(client)
    return CreateRunRequest(
        operation=operation,
        config_id=config_id,
        registry_version=registry_version,
        reason=reason,
    )


# The result key each stage writes its own failure evidence under. Only three of
# that evidence's fields may be printed -- the stage, its outcome, and the name of
# the class the worker raised. The rest of it describes what a run touched in the
# destination, which is not this gate's to report.
FAILURE_STAGES = ("plan", "verify", "apply", "sync")

# The bound the CLI already applies to the one recorded field that is free-form
# in principle: a class name, or nothing.
MAX_TYPE_NAME = 128


def printable_type_name(value: object) -> str:
    """Return a recorded error type only while it is one, so no free text prints."""
    if isinstance(value, str) and len(value) <= MAX_TYPE_NAME and value.isascii() and value.isidentifier():
        return value
    return "an unprintable error type"


def reported_reason(client: SyncClient, run_id: str) -> str:
    """Return what the deployment says it recorded about a failure, or that it says nothing.

    This is the deployment's own account, through the same client that reported
    the verdict. What the store holds is read separately, by the driver, before
    teardown: if the two disagree, that disagreement is the finding.
    """
    try:
        results = client.get_results(run_id).results
    except SyncClientError:
        return "the deployment could not be asked which stage failed"
    for stage in FAILURE_STAGES:
        evidence = results.get(f"{stage}_failure")
        if isinstance(evidence, dict):
            return f"{stage} failed with {printable_type_name(evidence.get('error_type'))}"
    return "the deployment recorded no stage failure"


def follow(client: SyncClient, accepted: RunResource) -> RunResource:
    """Follow one accepted run to its verdict, with the client's own waiting.

    The client's error taxonomy is closed on purpose: each error carries its
    discriminating fields as attributes and leaves the rendering to whichever
    boundary catches it, the way the CLI renders them. This is that boundary for
    a check. Left uncaught, the traceback prints the taxonomy's constant sentence
    and discards the terminal state -- a report that a run ended without success
    and nothing at all about how.
    """
    try:
        return client.wait_for_run(accepted, timeout=RUN_TIMEOUT_SECONDS, poll_interval=POLL_SECONDS)
    except RunTerminalError as error:
        refuse(
            f"run {error.run_id} ended {error.terminal_state}/{error.terminal_outcome}"
            f" at phase {error.phase} with outcome {error.outcome};"
            f" {reported_reason(client, error.run_id)}"
        )
    except RunWaitTimeoutError as error:
        refuse(
            f"run {error.run_id} did not finish within {RUN_TIMEOUT_SECONDS:.0f}s"
            f" at phase {error.phase} with outcome {error.outcome},"
            f" its execution last seen {error.execution_state}"
        )


def key(purpose: str) -> str:
    """A mutation key unique to one purpose in one run of this gate."""
    return f"clean-host-{purpose}-{os.environ.get('CLEAN_HOST_RUN_KEY', 'run')}"


# ---------------------------------------------------------------------------
# The destination state a managed row needs before it means anything
# ---------------------------------------------------------------------------
# The declared configuration the deployment's own bootstrap registers, mounted
# read-only from the extracted bundle. Which branches a run reads and writes is
# named there and nowhere else.
CONFIGURATION = "/configuration/qualification.yaml"

SEEDED_KIND = "InfraDevice"
SEEDED_DEVICE = "clean-host-device"
# The attribute the declared configuration maps, so a change to it is a change a
# plan sees. A change to anything else leaves the two sides equal as far as a
# plan is concerned.
PLANTED_ATTRIBUTE = "type"

# What a completed run reports having written, as the execution surface names it.
ACTIONS = ("create", "update", "delete")


def declared(configuration: str = CONFIGURATION) -> dict:
    """Return one declared configuration's `configuration` section."""
    return dict(yaml.safe_load(pathlib.Path(configuration).read_text(encoding="utf-8"))["configuration"])


def planned_branch(configuration: str = CONFIGURATION) -> str:
    """Return the branch one configuration writes to, refusing a vacuous pair.

    Two sides naming one branch read identically, so every plan against them is
    empty -- and a row asserting that its plan proposed something would refuse for
    that instead of for what it means to test.
    """
    sides = declared(configuration)
    source = sides["source"]["settings"]["branch"]
    branch = sides["destination"]["settings"]["branch"]
    if branch == source:
        refuse(f"the declared configuration reads and writes {branch}, so no plan against it can propose anything")
    return str(branch)


def source_branch(configuration: str = CONFIGURATION) -> str:
    """Return the branch one configuration reads from, which is where a difference goes."""
    return str(declared(configuration)["source"]["settings"]["branch"])


def planted_attribute(node: object) -> Attribute:
    """Return the mapped attribute, refusing anything the SDK does not model as one.

    A fetched node types every member as an attribute or one of two relationship
    shapes, so which one this is has to be established rather than assumed. Doing
    it here rather than suppressing the union turns a typing gap into a refusal
    that says what the destination actually declared.
    """
    attribute = node.type  # ty: ignore[unresolved-attribute] - TODO: a fetched node is typed by its schema
    if not isinstance(attribute, Attribute):
        refuse(f"the destination models {SEEDED_KIND} {PLANTED_ATTRIBUTE} as {type(attribute).__name__}")
    return attribute


def plant(purpose: str) -> str:
    """Change the seeded object on the source branch, and return the value written.

    Every managed row needs the two sides to differ, and an apply converges them:
    the row before this one leaves nothing to propose. So each row that plans
    plants its own difference, labelled with its purpose, because a failure has to
    say which of them is being observed.

    Written to the source branch, so the destination keeps the value it inherited.
    Read back, because a save that persisted nothing would leave an empty plan and
    the row would report that instead.
    """
    value = f"clean-host-{purpose}-{uuid.uuid4().hex[:8]}"
    branch = source_branch()
    device = sdk().get(kind=SEEDED_KIND, branch=branch, name__value=SEEDED_DEVICE)
    planted_attribute(device).value = value
    device.save()

    written = sdk().get(kind=SEEDED_KIND, branch=branch, name__value=SEEDED_DEVICE)
    if planted_attribute(written).value != value:
        refuse(f"the destination did not keep the difference planted for {purpose}")
    return value


def require_planned_work(client: SyncClient, run_id: str) -> int:
    """Refuse a plan with nothing in it, and return how much it proposed.

    Every negative claim below a plan -- refused before any write, interrupted
    mid-write, wrote nothing it should not have -- is satisfied by a plan that
    proposed nothing. So no row reads its own plan without this.
    """
    total = client.get_plan(run_id).summary.total
    if total < 1:
        refuse("the plan proposed nothing, so anything asserted about applying it would mean nothing")
    return total


def wrote(run: PublicRunResource) -> int:
    """Return how many objects a completed run reports having written."""
    return sum(int(run.summary.get(action, 0)) for action in ACTIONS)
