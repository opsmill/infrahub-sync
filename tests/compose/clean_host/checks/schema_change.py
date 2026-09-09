"""A compatible schema change needs no operator action; a drifted one refuses before any write.

Both halves are destination-side. The compatible half loads a new optional
attribute, proves the destination converged on it, and then runs against it: the
deployment picks the change up with no container replaced, which the driver
answers by comparing the row's container identities either side of this check. The
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
import sys

import yaml
from kit import (
    PLANTED_OPERATIONS,
    deployment,
    follow,
    key,
    planned_branch,
    plant,
    printable_type_name,
    recorded_failure,
    refuse,
    require_planned_work,
    run_request,
    sdk,
    settle,
)

from infrahub_sync.client.errors import APIError
from infrahub_sync.client.models import ApplyRunRequest

# The plan maps this attribute, so its kind is part of the semantics the
# fingerprint covers. Both kinds hold the same string values, so the conversion is
# reversible in either direction and no object loses its value.
DRIFTED_ATTRIBUTE = "type"
REVERSIBLE_KINDS = {"Text": "TextArea", "TextArea": "Text"}
# The compatible change: an attribute that is new, optional, and consumed by no
# configuration this gate registers. Additive on every axis the destination
# validates, so loading it is the change the contract says needs no operator
# action -- and it is loaded rather than assumed, so the compatible half below
# runs against a schema that actually moved.
ADDITIVE_ATTRIBUTE = "clean_host_compatible_note"
ADDITIVE_KIND = "Text"
SCHEMA_FILE = os.environ["CLEAN_HOST_SCHEMA"]
SMOKE_KIND = "InfraDevice"
# The typed refusal the pre-write gate raises when a plan's consumed semantics moved.
REFUSAL = "PlanSchemaChangedError"


def attribute_kind(branch: str, attribute: str = DRIFTED_ATTRIBUTE) -> str:
    """The destination's kind for one attribute of the smoke kind, on one branch.

    Defaulting to the attribute the plan consumes, because that is what both
    halves are about; the compatible half names the additive one instead.

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
    declared = [declaration.kind for declaration in node.attributes if declaration.name == attribute]
    if not declared:
        refuse(f"the destination declares no {attribute} attribute on {SMOKE_KIND} on {branch}")
    return str(declared[0])


def load_attribute_kind(kind: str, branch: str) -> None:
    """Load the seeded schema with one attribute kind set and the additive one added, and prove both landed.

    Loaded onto the same branch, through the SDK the image ships, and waited on:
    Infrahub applies a schema asynchronously, so a plan taken before it converges
    reads the old semantics -- and this row would then report success having
    drifted nothing.

    The additive attribute travels with every load, including the revert. A load
    states the node it declares, so one that omitted the attribute could take it
    back -- and the halves after it would then run against a schema this row had
    silently undone.
    """
    schema = yaml.safe_load(pathlib.Path(SCHEMA_FILE).read_text(encoding="utf-8"))
    for node in schema["nodes"]:
        for attribute in node["attributes"]:
            if attribute["name"] == DRIFTED_ATTRIBUTE:
                attribute["kind"] = kind
        node["attributes"].append({"name": ADDITIVE_ATTRIBUTE, "kind": ADDITIVE_KIND, "optional": True})
    sdk().schema.load(schemas=[schema], branch=branch, wait_until_converged=True)
    if attribute_kind(branch) != kind:
        refuse(f"the destination did not converge on {DRIFTED_ATTRIBUTE} kind {kind} on {branch}")
    if attribute_kind(branch, ADDITIVE_ATTRIBUTE) != ADDITIVE_KIND:
        refuse(f"the destination did not converge on the additive {ADDITIVE_ATTRIBUTE} on {branch}")


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

    # The compatible half: the additive change is loaded and proven to have
    # landed, and then a run goes against the destination as it now stands. A run
    # against an unchanged schema would exercise nothing this half is about.
    #
    # Whether anything was restarted to pick it up is the driver's to answer.
    # This check runs in a throwaway container on the deployment's network and
    # cannot see the engine, so the driver brackets the whole row with the
    # identities of the containers the deployment owns.
    load_attribute_kind(original, BRANCH)
    follow(client, client.plan(run_request(client, "plan", "clean-host: compatible schema"), key("compatible")))

    planned = follow(client, client.plan(run_request(client, "plan", "clean-host: drift plan"), key("drift")))
    run_id = planned.run.run_id
    plan = client.get_plan(run_id)
    require_planned_work(client, run_id, expected=PLANTED_OPERATIONS)

    load_attribute_kind(REVERSIBLE_KINDS[original], BRANCH)

    # Precondition: the change moved the semantics the retained plan recorded.
    #
    # `_require_planned_schema` compares the manifest's
    # `registered_schema_fingerprint` against the one this stage's live read
    # produces, and a plan resource exposes exactly that field -- so a second plan
    # taken now carries what `live` will be, and the two together are the
    # comparison the apply is about to make. Asserted rather than assumed because
    # the drift landing on the branch and the drift moving that fingerprint are
    # two different claims, and the row has already been wrong about which one it
    # was establishing. Both values are named, so a run that fails here says which
    # of them did not move.
    probe = follow(client, client.plan(run_request(client, "plan", "clean-host: drift probe"), key("drift-probe")))
    moved = client.get_plan(probe.run.run_id).schema_fingerprint
    if plan.schema_fingerprint is None or moved is None:
        refuse(f"a plan recorded no schema fingerprint to compare: {plan.schema_fingerprint!r} then {moved!r}")
    # Reported whether or not it refuses. A value only printed on failure is a
    # value nobody has ever seen, and three candidate explanations for this row
    # were argued from values none of us had looked at.
    print(f"clean-host: drift: retained {plan.schema_fingerprint} then {moved}", file=sys.stderr)
    if plan.schema_fingerprint == moved:
        refuse(
            f"changing {DRIFTED_ATTRIBUTE} from {original} to {REVERSIBLE_KINDS[original]} on {BRANCH}"
            f" left the consumed-semantics fingerprint at {moved}, so the apply below has nothing to refuse for"
        )

    try:
        # Precondition: the apply is actually attempted against the retained plan,
        # and it is waited for. `apply` returns on acceptance, and the refusal is
        # the worker's -- so reading the evidence here would read a verdict the run
        # has not reached, and report an unfinished run as the product declining to
        # refuse. The revert below would also land while the run was still queued.
        accepted = None
        with contextlib.suppress(APIError):
            accepted = client.apply(
                run_id,
                ApplyRunRequest(expected_checksum=plan.checksum, confirm_writes=True, reason="clean-host: drift"),
                key("drift-apply"),
            )
        if accepted is None:
            refuse("the API refused the apply outright, so the pre-write gate never ran to refuse it")
        settle(client, accepted)
        # The reason, not merely a failure: an apply that failed for anything else
        # would satisfy a check that only required it to fail.
        # Through the kit rather than by stage name: which key holds the evidence
        # depends on the operation that failed, and a row reading one name observes
        # nothing at all about a run that failed in another.
        failure = recorded_failure(client, run_id)
        # Stage, outcome and the error class, each bounded. Never the mapping: it
        # is read out of the deployment and carries whatever that stage recorded,
        # and this stream is the sentence the driver shows.
        print(
            "clean-host: drift: the settled apply recorded"
            f" {printable_type_name(failure.get('stage'))}"
            f"/{printable_type_name(failure.get('outcome'))}"
            f"/{printable_type_name(failure.get('error_type'))}"
            if failure
            else "clean-host: drift: the settled apply recorded nothing",
            file=sys.stderr,
        )
        if failure.get("error_type") != REFUSAL:
            refuse(f"the apply reported {failure.get('error_type')!r} rather than {REFUSAL}")
        if failure.get("may_have_partially_written"):
            refuse("the refusal reports it may have written, which the gate runs before any adapter to prevent")
    finally:
        load_attribute_kind(original, BRANCH)
