"""Prepare the destination every managed row plans against.

Run inside the candidate image, because the host this gate qualifies on has no
interpreter and no product CLI of its own. The schemas are the gate's own
evidence: the example schema an operator follows is loaded unchanged, and the
keyless kind beside it exists only so the keyed-write row has a destination kind
whose convergent write cannot be keyed.

The order is the whole content of this file, and it is the Compose fixture's own
(`ensure_smoke_branch` and `plant_pending_update`, from `tasks/preview.py` and
`tests/compose/lifecycle.py`, called from `tests/compose/conftest.py`):

1. the schemas onto `main`, waited on, because a branch carries the schema `main`
   held when it forked;
2. the object on `main`;
3. then the branch the declared configuration names, forked from `main` and
   therefore holding that object;
4. then a change to the object on `main`, so the two sides differ and a plan has
   an update to propose.

Every position is load-bearing, and three of them were learned the hard way. A
branch forked before the schema carries no schema. A branch forked before the
object does not hold it -- while the object's human-friendly ID is registered
across the whole destination, so a convergent upsert can then neither find the
node on this branch nor create one under an identifier already taken:

    Node … / InfraDevice uses this human-friendly ID, but does not exist on this
    branch. Please rebase this branch to access … / InfraDevice

And with both sides holding an identical object, a plan proposes nothing at all,
which is what step 4 exists for. The changed value is fresh on every run because
a fixed one converges: the first apply writes it to the branch, and the next
run's plan would be empty again.
"""

from __future__ import annotations

import subprocess  # noqa: S404 -- fixed argv, the product CLI the image ships
import sys
import time

from kit import SEEDED_DEVICE, destination, planned_branch, plant, refuse, sdk

# The bound on a client's view catching up with a load it has already accepted.
SCHEMA_TIMEOUT_SECONDS = 120.0

SCHEMAS = ("/checks/infra_device.yml", "/checks/unkeyed.yml")
CREATE_DEVICE = 'mutation { InfraDeviceCreate(data: {name: {value: "NAME"}, type: {value: "seed"}}) { ok } }'

# The unkeyed-write row's own configuration, which the kit carries beside the
# checks. Its two kinds are seeded on either side of its fork, for reasons the
# document itself records.
UNKEYED_CONFIGURATION = "/checks/unkeyed-configuration.yaml"
UNKEYED_KINDS = ("CleanSite", "CleanDevice")
SEEDED_SITE = "clean-host-site"
SEEDED_UNKEYED = "clean-host-unkeyed-device"


def load(schema: str) -> None:
    result = subprocess.run(  # noqa: S603
        ["infrahubctl", "schema", "load", schema, "--branch", "main", "--wait", "60"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    if result.returncode != 0:
        refuse(f"the destination refused the schema {schema}")


def seed_object(mutation: str, named: str) -> None:
    """Create one object on `main`, which is the branch `/graphql` addresses."""
    with destination() as infrahub:
        created = infrahub.post("/graphql", json={"query": mutation.replace("NAME", named)})
        if created.status_code != 200:
            refuse(f"the destination refused the object {named} this gate seeds")


def ensure_branch(name: str) -> None:
    """Fork the branch from `main` unless it is already there.

    `branch.create` waits for the branch to exist before it returns, so nothing
    after this has to wait again. Skipped when present, because re-forking would
    discard whatever the rows before it wrote.
    """
    client = sdk()
    if name in client.branch.all():
        return
    client.branch.create(name, sync_with_git=False)
    if name not in client.branch.all():
        refuse(f"the destination did not create the branch {name} the configuration names")


def await_kinds(kinds: tuple[str, ...], branch: str = "main") -> None:
    """Block until this client can resolve every one of `kinds` on `branch`.

    A schema load returns once the payload is accepted, not once the kinds it
    declares are resolvable, and a write issued in that window fails as a missing
    schema rather than as whatever the row is testing. The load above settles the
    server; this settles the view a client actually reads, which is the one the
    deployment's own worker will read too.
    """
    deadline = time.monotonic() + SCHEMA_TIMEOUT_SECONDS
    client = sdk()
    while True:
        missing = set(kinds) - set(client.schema.all(branch=branch, refresh=True))
        if not missing:
            return
        if time.monotonic() >= deadline:
            refuse(f"the destination did not serve {sorted(missing)} within {SCHEMA_TIMEOUT_SECONDS:.0f}s of a load")
        time.sleep(1.0)


def seed_unkeyed_peer() -> str:
    """Create the peer the unkeyed row's kind references, on `main`, and return its id.

    Through the SDK rather than through a planned apply, and the same is true of
    the subject below: the write surface refuses the very operation this row
    exists to observe, so a planned apply could not establish either object.
    """
    site = sdk().create(kind="CleanSite", branch="main", data={"name": SEEDED_SITE})
    site.save()
    return str(site.id)


def seed_unkeyed_subject(site_id: str) -> None:
    """Create the crossing kind on `main` alone, referencing the peer by its node id."""
    device = sdk().create(kind="CleanDevice", branch="main", data={"name": SEEDED_UNKEYED, "site": site_id})
    device.save()


for schema in SCHEMAS:
    load(schema)
await_kinds(UNKEYED_KINDS)

# The managed rows: the object first, then the branch that inherits it, then the
# difference. Every position is explained above.
seed_object(CREATE_DEVICE, SEEDED_DEVICE)
ensure_branch(planned_branch())

# The unkeyed-write row, whose two kinds sit on either side of its own fork. The
# peer is seeded first so the branch inherits it and the reference resolves at the
# destination; without a resolvable peer the row's run fails at peer resolution and
# reports the wrong refusal. The subject is seeded after, on `main` alone, so the
# plan proposes a create -- the action whose key cannot be rendered.
site_id = seed_unkeyed_peer()
ensure_branch(planned_branch(UNKEYED_CONFIGURATION))
seed_unkeyed_subject(site_id)

planted = plant("managed")

print(f"{SEEDED_DEVICE} {SEEDED_SITE} {SEEDED_UNKEYED} {planted}", file=sys.stderr)
