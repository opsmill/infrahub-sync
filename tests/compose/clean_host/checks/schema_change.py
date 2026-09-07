"""A compatible schema change needs no operator action; a drifted one refuses before any write.

Both halves are destination-side. The compatible half loads an additive change and
runs against it: the deployment picks it up with no container replaced. The
incompatible half changes the kind of an attribute the plan *consumes*, then
applies the retained plan — the fingerprint the plan recorded no longer matches
the one a live read produces, and `_require_planned_schema` refuses before any
adapter is constructed.

The attribute has to be one the plan consumes. Changing any other leaves the
fingerprint where it was, and the row would report success having drifted nothing.

The original kind is read from the running destination rather than assumed, and
the change is reversed afterwards, so a failure here cannot leave the destination
on a schema the rows after it do not expect.

Negative destination-state assertions are read from the destination, independently
of the deployment client -- and on the branch the plan is computed against. A
drift verified on `main` while the plan reads a branch says nothing about the
comparison the refusal makes.
"""

from __future__ import annotations

import contextlib
import os
import pathlib

import yaml
from kit import (
    PLANTED_OPERATIONS,
    deployment,
    follow,
    key,
    planned_branch,
    plant,
    recorded_failure,
    refuse,
    require_planned_work,
    run_request,
    sdk,
)

from infrahub_sync.client.errors import APIError
from infrahub_sync.client.models import ApplyRunRequest

# The plan maps this attribute, so its kind is part of the semantics the
# fingerprint covers. Both kinds hold the same string values, so the conversion is
# reversible in either direction and no object loses its value.
DRIFTED_ATTRIBUTE = "type"
REVERSIBLE_KINDS = {"Text": "TextArea", "TextArea": "Text"}
SCHEMA_FILE = os.environ["CLEAN_HOST_SCHEMA"]
SMOKE_KIND = "InfraDevice"
# The typed refusal the pre-write gate raises when a plan's consumed semantics moved.
REFUSAL = "PlanSchemaChangedError"


def attribute_kind(branch: str) -> str:
    """The destination's kind for the attribute the plan consumes, on one branch.

    On the branch the plan is computed against, and read with the call the
    worker's own schema read makes (`configuration/capabilities.py` ->
    `client.schema.all(branch=...)`). The apply compares a fingerprint taken from
    `effective_destination_branch(...)`, so a drift written or verified anywhere
    else is a statement about a schema the refusal never looks at -- and the row
    would report a passing gate having drifted nothing it compares.
    """
    node = sdk().schema.all(branch=branch, refresh=True).get(SMOKE_KIND)
    if node is None:
        refuse(f"the destination serves no {SMOKE_KIND} on {branch}")
    declared = [attribute.kind for attribute in node.attributes if attribute.name == DRIFTED_ATTRIBUTE]
    if not declared:
        refuse(f"the destination declares no {DRIFTED_ATTRIBUTE} attribute on {SMOKE_KIND} on {branch}")
    return str(declared[0])


def load_attribute_kind(kind: str, branch: str) -> None:
    """Load the seeded schema with one attribute kind changed, and prove it landed.

    Loaded onto the same branch, through the SDK the image ships, and waited on:
    Infrahub applies a schema asynchronously, so a plan taken before it converges
    reads the old semantics -- and this row would then report success having
    drifted nothing.
    """
    schema = yaml.safe_load(pathlib.Path(SCHEMA_FILE).read_text(encoding="utf-8"))
    for node in schema["nodes"]:
        for attribute in node["attributes"]:
            if attribute["name"] == DRIFTED_ATTRIBUTE:
                attribute["kind"] = kind
    sdk().schema.load(schemas=[schema], branch=branch, wait_until_converged=True)
    if attribute_kind(branch) != kind:
        refuse(f"the destination did not converge on {DRIFTED_ATTRIBUTE} kind {kind} on {branch}")


# The branch the plan reads and writes, which is the one whose schema the apply
# compares its recorded fingerprint against.
BRANCH = planned_branch()

with deployment() as client:
    original = attribute_kind(BRANCH)
    if original not in REVERSIBLE_KINDS:
        refuse(f"{DRIFTED_ATTRIBUTE} has unsupported live kind {original!r}")

    # The row before this one applied its plan and converged the two sides, so a
    # plan taken now would propose nothing -- and "refused before any write" is
    # satisfied by a run that had no write to be before. Its own difference, so a
    # failure says which row is being observed.
    plant("drift")

    # The compatible half: a run against the destination as it stands completes,
    # and the driver confirms separately that no container was replaced.
    follow(client, client.plan(run_request(client, "plan", "clean-host: compatible schema"), key("compatible")))

    planned = follow(client, client.plan(run_request(client, "plan", "clean-host: drift plan"), key("drift")))
    run_id = planned.run.run_id
    plan = client.get_plan(run_id)
    require_planned_work(client, run_id, expected=PLANTED_OPERATIONS)

    load_attribute_kind(REVERSIBLE_KINDS[original], BRANCH)
    try:
        # Precondition: the plan exists, the change landed above, and the apply is
        # actually attempted against the retained plan.
        with contextlib.suppress(APIError):
            client.apply(
                run_id,
                ApplyRunRequest(expected_checksum=plan.checksum, confirm_writes=True, reason="clean-host: drift"),
                key("drift-apply"),
            )
        # The reason, not merely a failure: an apply that failed for anything else
        # would satisfy a check that only required it to fail.
        # Through the kit rather than by stage name: which key holds the evidence
        # depends on the operation that failed, and a row reading one name observes
        # nothing at all about a run that failed in another.
        failure = recorded_failure(client, run_id)
        if failure.get("error_type") != REFUSAL:
            refuse(f"the apply reported {failure.get('error_type')!r} rather than {REFUSAL}")
        if failure.get("may_have_partially_written"):
            refuse("the refusal reports it may have written, which the gate runs before any adapter to prevent")
    finally:
        load_attribute_kind(original, BRANCH)
