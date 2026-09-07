"""An unkeyed convergent write is refused before it mutates the destination.

Two conditions together induce it, and neither alone does. The mapped field named
`id` renders as an empty attribute block, which defeats a gate testing only that a
key is present; the destination kind declaring no `human_friendly_id` is what stops
an `hfid` rendering and satisfying the check first.

The precondition and the property are asserted separately. A negative assertion is
satisfied by a mechanism that never fired, so this first proves the unkeyed
operation reached apply, and only then that it wrote nothing.

Negative destination-state assertions are read from the destination, independently
of the deployment client.
"""

from __future__ import annotations

import os
import pathlib

import yaml
from kit import deployment, destination, follow, key, refuse

from infrahub_sync.client.models import ConfigMutationRequest, CreateRunRequest

KEYLESS_KIND = "CleanKeyless"
# The typed refusal the adapter raises immediately before the SDK write.
REFUSAL = "UnkeyedWriteRefusedError"
CONFIGURATION = "/checks/keyless-configuration.yaml"


def keyless_objects() -> int:
    with destination() as infrahub:
        answer = infrahub.post("/graphql", json={"query": f"{{ {KEYLESS_KIND} {{ count }} }}"})
        if answer.status_code != 200:
            refuse("the destination did not answer for the keyless kind's object count")
        return int(answer.json()["data"][KEYLESS_KIND]["count"])


def registered_keyless(client: object) -> tuple[str, int]:
    """Register this row's own configuration and return the version to run.

    Its own, because the bundled configuration maps a kind with a renderable
    human-friendly ID and can never produce the operation under test.
    """
    package = yaml.safe_load(pathlib.Path(CONFIGURATION).read_text(encoding="utf-8"))
    address = os.environ["INFRAHUB_DESTINATION_URL"]
    for side in ("source", "destination"):
        package["configuration"][side]["settings"]["url"] = address
    answer = client.register_config(  # ty: ignore[unresolved-attribute]
        ConfigMutationRequest(package=package, reason="clean-host: register the keyed-write configuration"),
        key("register-keyless"),
    )
    return answer.version.config_id, answer.version.registry_version


with deployment() as client:
    before = keyless_objects()
    config_id, registry_version = registered_keyless(client)

    def request(operation: str, reason: str) -> CreateRunRequest:
        return CreateRunRequest(
            operation=operation,  # ty: ignore[invalid-argument-type] -- the model checks the literal
            config_id=config_id,
            registry_version=registry_version,
            reason=reason,
        )

    planned = follow(client, client.plan(request("plan", "clean-host: unkeyed plan"), key("unkeyed")))
    plan = client.get_plan(planned.run.run_id)

    # Precondition: the operation survived planning. Refused at plan time it would
    # never reach the render gate, and this row would be reporting the wrong check.
    kinds = {operation.kind for operation in plan.operations}
    if KEYLESS_KIND not in kinds:
        refuse(f"planning produced no {KEYLESS_KIND} operation, so the render gate was never reached")

    applied = follow(
        client,
        client.sync(request("sync", "clean-host: unkeyed apply"), key("unkeyed-apply")),
    )

    # Property: the run was refused for being unkeyed, and the destination is
    # unchanged. A run that failed for anything else would satisfy a check that
    # only required it to fail.
    failure = client.get_results(applied.run.run_id).results.get("apply_failure", {})
    if failure.get("error_type") != REFUSAL:
        refuse(f"the run reported {failure.get('error_type')!r} rather than {REFUSAL}")
    after = keyless_objects()
    if after != before:
        refuse(f"a refused unkeyed operation changed the destination from {before} to {after} objects")
