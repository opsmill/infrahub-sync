"""Prepare the destination the managed rows plan against: schema, branch, one object.

Run inside the candidate image, because the host this gate qualifies on has no
interpreter and no product CLI of its own. The schemas are the gate's own
evidence: the example schema an operator follows is loaded unchanged, and the
keyless kind beside it exists only so the keyed-write row has a destination kind
whose convergent write cannot be keyed.

The order is the point, and two thirds of it is the Compose fixture's own
(`ensure_smoke_branch` in `tasks/preview.py`, called from `tests/compose/conftest.py`):

1. the schemas onto `main`, waited on, because a branch carries the schema `main`
   held when it forked;
2. then the branch the declared configuration names as its destination, forked
   from `main` and therefore carrying that schema;
3. then one object on `main` alone.

Step 3 is where this differs from the fixture, which seeds before forking so both
sides hold the object and a plan proposes an update. That harness then plants a
fresh difference for every run. This gate has no such step, so it forks first and
the object reaches `main` alone -- which is what leaves the first plan with one
create to propose instead of nothing.
"""

from __future__ import annotations

import pathlib
import subprocess  # noqa: S404 -- fixed argv, the product CLI the image ships
import sys

import yaml
from kit import destination, refuse, sdk

SCHEMAS = ("/checks/infra_device.yml", "/checks/keyless.yml")
# The declared configuration the deployment's own bootstrap registers, mounted
# from the extracted bundle. The branch is read from it rather than named here:
# a plan runs against the branch that document names, and nothing else.
CONFIGURATION = "/configuration/qualification.yaml"
SEEDED_DEVICE = "clean-host-device"
CREATE_DEVICE = 'mutation { InfraDeviceCreate(data: {name: {value: "NAME"}, type: {value: "seed"}}) { ok } }'


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


for schema in SCHEMAS:
    load(schema)

ensure_branch(planned_branch())

with destination() as infrahub:
    created = infrahub.post(
        "/graphql",
        json={"query": CREATE_DEVICE.replace("NAME", SEEDED_DEVICE)},
    )
    if created.status_code != 200:
        refuse("the destination refused the object this gate seeds")
print(SEEDED_DEVICE, file=sys.stderr)
