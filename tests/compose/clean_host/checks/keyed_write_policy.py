"""A create that cannot be proven keyed is refused at plan time, against a live destination.

`CleanDevice`'s human-friendly ID is `[site__name__value, name__value]` while the
configuration's identifiers name `name` alone. `site` is resolvable from the
payload and still absent from the operation's identity, so the create cannot be
proven to carry every component the destination matches on -- and a payload
missing one component does not match the existing object: the server reports
`ok: true` and creates a second one. That is the case this row exists to keep
unreachable.

The refusal moved. It used to be a pre-write gate reading the SDK's private render,
which is retired: on newer SDKs that render reports an `hfid` the wire does not
carry, so it passed writes that were unkeyed where it mattered. The proof is now
made where the plan is built, so the run never reaches a write at all and settles
at the **plan** stage.

That makes this row's verdict sharper than the one it replaces. A refusal raised
before any dispatch leaves nothing to reconcile, so the run is `failed` rather than
`interrupted`/`ambiguous`, and `reconciliation_required` must be false. Row 8 asserts
the opposite for a genuine interruption; the two must not be satisfiable by the same
state.

The plan stage raises the refusal directly, so the run's own `error_type` names it.
There is no apply wrapper here and therefore no `cause_type` to read.

The precondition and the property are asserted apart. A negative assertion is
satisfied by a mechanism that never fired, so this first proves the configuration
really does propose this kind, and only then that nothing was written.

Negative destination-state assertions are read from the destination, on the branch
the run writes to, independently of the deployment client.
"""

from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING

import yaml
from kit import (
    KEYED_CREATE_REFUSAL,
    Operation,
    create_run,
    deployment,
    destination,
    key,
    recorded_failure,
    refuse,
    settle,
)

from infrahub_sync.client.models import ConfigMutationRequest, CreateRunRequest

if TYPE_CHECKING:
    from infrahub_sync.client import SyncClient

# The kind whose create cannot be proven keyed: its identity omits an HFID component.
UNKEYED_KIND = "CleanDevice"
CONFIGURATION = "/checks/unkeyed-configuration.yaml"


def declared_package() -> dict:
    """The whole declared package this row registers, credentials included."""
    return dict(yaml.safe_load(pathlib.Path(CONFIGURATION).read_text(encoding="utf-8")))


def unkeyed_objects(branch: str) -> int:
    """Count the kind's objects on one branch, read from the destination itself.

    On the branch the run writes to, never on `main`: a refused write leaves `main`
    unchanged whether it was refused or not, and the count would confirm the
    property without ever having been able to contradict it. Infrahub addresses a
    branch by path (`_graphql_url` in the SDK), so this is the same endpoint the
    deployment's own adapter reads.
    """
    with destination() as infrahub:
        answer = infrahub.post(f"/graphql/{branch}", json={"query": f"{{ {UNKEYED_KIND} {{ count }} }}"})
        if answer.status_code != 200:
            refuse(f"the destination did not answer for {UNKEYED_KIND}'s object count on {branch}")
        # A GraphQL refusal arrives as a 200 carrying `errors` and no `data`, and
        # indexing it raises where a sentence belongs -- the row would die with a
        # traceback about a key rather than say what it could not read.
        held = answer.json().get("data") or {}
        counted = held.get(UNKEYED_KIND, {}).get("count") if isinstance(held.get(UNKEYED_KIND), dict) else None
        if not isinstance(counted, int):
            refuse(f"the destination answered for {UNKEYED_KIND} on {branch} without a count")
        return counted


def registered_unkeyed(client: SyncClient) -> tuple[str, int]:
    """Register this row's own configuration and return the version to run.

    Its own, because the bundled configuration maps a kind whose identifier is
    direct and can never produce the operation under test.
    """
    package = declared_package()
    address = os.environ["INFRAHUB_DESTINATION_URL"]
    for side in ("source", "destination"):
        package["configuration"][side]["settings"]["url"] = address
    answer = client.register_config(
        ConfigMutationRequest(package=package, reason="clean-host: register the unkeyed-write configuration"),
        key("register-unkeyed"),
    )
    return answer.version.config_id, answer.version.registry_version


written_branch = declared_package()["configuration"]["destination"]["settings"]["branch"]

with deployment() as client:
    before = unkeyed_objects(written_branch)
    config_id, registry_version = registered_unkeyed(client)

    def request(operation: Operation, reason: str) -> CreateRunRequest:
        """This row's own configuration, through the kit's one run-request builder.

        Its identifiers are its own -- it registered the configuration itself --
        but the confirmation the client requires for each operation is not, and
        deriving it in a second place is how the two drift apart.
        """
        return create_run(operation, config_id=config_id, registry_version=registry_version, reason=reason)

    # Settled rather than followed: planning refuses this configuration, so awaiting
    # success could never pass. For this row the terminal failure is the expected
    # outcome and the verdict is what it reads.
    planned = settle(client, client.plan(request("plan", "clean-host: unkeyed plan"), key("unkeyed")))

    # Property: the run was refused for a create that cannot be proven keyed, it was
    # refused before anything was dispatched, and the branch it writes to is unchanged.
    failure = recorded_failure(client, planned.run.run_id)
    if failure.get("error_type") != KEYED_CREATE_REFUSAL:
        refuse(f"the run recorded {failure.get('error_type')!r}, rather than a {KEYED_CREATE_REFUSAL}")
    if planned.run.reconciliation_required:
        refuse("a plan-time refusal dispatched nothing, so it must not require reconciliation")
    after = unkeyed_objects(written_branch)
    if after != before:
        refuse(f"a refused unkeyed operation changed {written_branch} from {before} to {after} objects")
