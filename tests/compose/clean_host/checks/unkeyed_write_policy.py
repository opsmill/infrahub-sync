"""A convergent write whose key cannot be rendered is refused before it mutates anything.

The mechanism is ported from `tests/integration/test_infrahub_unkeyed_refusal_integration.py`,
which proves this shape against a live Infrahub, rather than from the offline
adapter suite: that drives a recording client with a locally constructed schema
whose `human_friendly_id` stays unset, and a live destination reports whatever it
computed instead. A mechanism taken from the offline suite does not survive
contact with a real destination -- this row's previous one did not.

`CleanDevice`'s identifier crosses the `site` relationship. At apply the peer is a
resolved node id, `get_path_value` cannot walk to `site__name__value`, so the
component resolves to None and no `hfid` renders; a create carries no `id` either,
and the pre-write gate refuses with nothing sent.

The precondition and the property are asserted apart. A negative assertion is
satisfied by a mechanism that never fired, so this first proves the operation
reached apply, and only then that it wrote nothing. That split is what told us the
previous mechanism had died rather than the property failing.

Negative destination-state assertions are read from the destination, on the branch
the run writes to, independently of the deployment client.
"""

from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING

import yaml
from kit import Operation, create_run, deployment, destination, follow, key, refuse

from infrahub_sync.client.models import ConfigMutationRequest, CreateRunRequest

if TYPE_CHECKING:
    from infrahub_sync.client import SyncClient

# The kind whose convergent write cannot be keyed, and the typed refusal the
# adapter raises immediately before the SDK write.
UNKEYED_KIND = "CleanDevice"
REFUSAL = "UnkeyedWriteRefusedError"
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
        return int(answer.json()["data"][UNKEYED_KIND]["count"])


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

    planned = follow(client, client.plan(request("plan", "clean-host: unkeyed plan"), key("unkeyed")))
    plan = client.get_plan(planned.run.run_id)

    # Precondition: the operation survived planning and is a create. Refused at
    # plan time it would never reach the render gate; proposed as an update it
    # would carry the destination node's own id and be keyed by it, so the gate
    # would pass for a reason that says nothing about this row.
    proposed = [operation for operation in plan.operations if operation.kind == UNKEYED_KIND]
    if not proposed:
        refuse(f"planning produced no {UNKEYED_KIND} operation, so the render gate was never reached")
    if {operation.action for operation in proposed} != {"create"}:
        refuse(f"planning proposed {sorted({operation.action for operation in proposed})} rather than a create")

    applied = follow(
        client,
        client.sync(request("sync", "clean-host: unkeyed apply"), key("unkeyed-apply")),
    )

    # Property: the run was refused for being unkeyed, and the branch it writes to
    # is unchanged. A run that failed for anything else would satisfy a check that
    # only required it to fail.
    failure = client.get_results(applied.run.run_id).results.get("apply_failure", {})
    if failure.get("error_type") != REFUSAL:
        refuse(f"the run reported {failure.get('error_type')!r} rather than {REFUSAL}")
    after = unkeyed_objects(written_branch)
    if after != before:
        refuse(f"a refused unkeyed operation changed {written_branch} from {before} to {after} objects")
