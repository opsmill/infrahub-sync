"""Load the qualification schemas into the pinned destination and seed one object.

Run inside the candidate image, because the host this gate qualifies on has no
interpreter and no product CLI of its own. The schemas are the gate's own
evidence: the example schema an operator follows is loaded unchanged, and the
keyless kind beside it exists only so the keyed-write row has a destination kind
whose convergent write cannot be keyed.
"""

from __future__ import annotations

import subprocess  # noqa: S404 -- fixed argv, the product CLI the image ships
import sys

from kit import destination, refuse

SCHEMAS = ("/checks/infra_device.yml", "/checks/keyless.yml")
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


for schema in SCHEMAS:
    load(schema)

with destination() as infrahub:
    created = infrahub.post(
        "/graphql",
        json={"query": CREATE_DEVICE.replace("NAME", SEEDED_DEVICE)},
    )
    if created.status_code != 200:
        refuse("the destination refused the object this gate seeds")
print(SEEDED_DEVICE, file=sys.stderr)
