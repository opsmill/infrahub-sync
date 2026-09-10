"""Register the extracted bundle's declared configuration, once, through the API.

A started deployment holds an empty registry: nothing in the bundle registers a
package, so the rows below row 2 have nothing to run against until an operator
does it. This is that operator step, made by the same client an operator has.

The package is the one mounted read-only beside the checks, which is the
document whose branches and mapped attribute every managed row depends on.
"""

from __future__ import annotations

import pathlib

import yaml
from kit import BUNDLED_CONFIGURATION, CONFIGURATION, bundled, deployment, key, refuse

from infrahub_sync.client.models import ConfigMutationRequest

package = yaml.safe_load(pathlib.Path(CONFIGURATION).read_text(encoding="utf-8"))
declared = package.get("configuration", {}).get("name")
if declared != BUNDLED_CONFIGURATION:
    refuse(f"the mounted declared configuration names {declared!r} rather than {BUNDLED_CONFIGURATION!r}")

with deployment() as client:
    if client.list_configs():
        refuse("this deployment already holds a registered configuration, so a cold start registered one")
    registered = client.register_config(
        ConfigMutationRequest(package=package, reason="clean-host: register the qualification configuration"),
        key("register-configuration"),
    )
    # Read back through the lookup every managed row uses, so what those rows
    # resolve is what this registration created rather than something adjacent.
    config_id, registry_version = bundled(client)
    if (config_id, registry_version) != (registered.version.config_id, registered.version.registry_version):
        refuse("the registered configuration is not the one the managed rows resolve")
