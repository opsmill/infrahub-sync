"""Prepare the destination the managed rows plan against: schema, object, branch, difference.

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

import pathlib
import subprocess  # noqa: S404 -- fixed argv, the product CLI the image ships
import sys
import uuid

import yaml
from infrahub_sdk.node import Attribute
from kit import destination, refuse, sdk

SCHEMAS = ("/checks/infra_device.yml", "/checks/keyless.yml")
# The declared configuration the deployment's own bootstrap registers, mounted
# from the extracted bundle. The branch is read from it rather than named here:
# a plan runs against the branch that document names, and nothing else.
CONFIGURATION = "/configuration/qualification.yaml"

SEEDED_KIND = "InfraDevice"
SEEDED_DEVICE = "clean-host-device"
CREATE_DEVICE = 'mutation { InfraDeviceCreate(data: {name: {value: "NAME"}, type: {value: "seed"}}) { ok } }'
# The attribute the declared configuration maps, so a change to it is a change
# the plan sees. Anything else would leave the two sides equal as far as a plan
# is concerned.


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


def planned_branch() -> str:
    """Return the branch the declared configuration writes to, refusing a vacuous pair.

    Two sides naming one branch read identically, so every plan against them is
    empty -- and a row asserting that its plan proposed something would refuse for
    that instead of for what it means to test.
    """
    document = yaml.safe_load(pathlib.Path(CONFIGURATION).read_text(encoding="utf-8"))
    declared = document["configuration"]
    source = declared["source"]["settings"]["branch"]
    branch = declared["destination"]["settings"]["branch"]
    if branch == source:
        refuse(f"the declared configuration reads and writes {branch}, so no plan against it can propose anything")
    return str(branch)


def seed_object() -> None:
    """Create the one object both sides will hold, on `main`."""
    with destination() as infrahub:
        created = infrahub.post("/graphql", json={"query": CREATE_DEVICE.replace("NAME", SEEDED_DEVICE)})
        if created.status_code != 200:
            refuse("the destination refused the object this gate seeds")


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


def planned_attribute(device: object) -> Attribute:
    """Return the object's `type`, refusing anything the SDK does not model as an attribute.

    A fetched node types every member as an attribute or one of two relationship
    shapes, so which one this is has to be established rather than assumed. Doing
    it here rather than suppressing the union turns a typing gap into a refusal
    that says what the destination actually declared.
    """
    attribute = device.type  # ty: ignore[unresolved-attribute] - TODO: a fetched node is typed by its schema
    if not isinstance(attribute, Attribute):
        refuse(f"the destination models {SEEDED_KIND} type as {type(attribute).__name__}, not as an attribute")
    return attribute


def plant_pending_update() -> str:
    """Change the seeded object on `main` alone, and return the value written.

    This is the one difference every managed plan proposes. `type` is the
    attribute because the declared configuration maps it, so a change to it is a
    change a plan sees; a change to anything else leaves the two sides equal as
    far as a plan is concerned.

    Written against `main`, so the branch keeps the value it inherited. Read back
    afterwards, because a save that persisted nothing would leave a plan with
    nothing to propose and the row would report that instead.
    """
    value = f"clean-host-{uuid.uuid4().hex[:12]}"
    device = sdk().get(kind=SEEDED_KIND, branch="main", name__value=SEEDED_DEVICE)
    planned_attribute(device).value = value
    device.save()

    written = sdk().get(kind=SEEDED_KIND, branch="main", name__value=SEEDED_DEVICE)
    if planned_attribute(written).value != value:
        refuse("the destination did not keep the change that gives a plan something to propose")
    return value


for schema in SCHEMAS:
    load(schema)

seed_object()
ensure_branch(planned_branch())
planted = plant_pending_update()

print(f"{SEEDED_DEVICE} {planted}", file=sys.stderr)
