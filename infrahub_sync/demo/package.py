"""Write the NetBox and Infrahub addresses into the shipped `from-netbox` package."""

from __future__ import annotations

import json

import yaml

from infrahub_sync.demo.errors import DemoPackageError

# The shipped `from-netbox` package names the public NetBox demo and a default Infrahub.
# The rewrite replaces exactly these two lines and leaves the rest byte for byte.
SHIPPED_NETBOX_URL = "https://demo.netbox.dev"
SHIPPED_INFRAHUB_URL = "http://localhost:8000"


def local_package_text(shipped: str, netbox: str, infrahub: str, *, source: str = "the package") -> str:
    """The shipped package text with its NetBox and Infrahub URLs replaced.

    Each shipped URL must appear exactly once as a `url:` setting; anything else means the
    shipped package changed shape, and a guessed replacement could point the copy at the
    public demo. `source` names the package in that refusal.
    """
    replacements = {
        f"url: {json.dumps(SHIPPED_NETBOX_URL)}": f"url: {json.dumps(netbox)}",
        f"url: {json.dumps(SHIPPED_INFRAHUB_URL)}": f"url: {json.dumps(infrahub)}",
    }
    # Count both lines in the shipped text before replacing either: a replacement URL can
    # equal the other shipped URL (a local NetBox on port 8000), and counting after the first
    # replacement would then blame the shipped package.
    for line in replacements:
        count = shipped.count(line)
        if count != 1:
            msg = f"{source} must contain {line!r} exactly once, found {count}"
            raise DemoPackageError(msg)
    lines = shipped.splitlines(keepends=True)
    for index, line in enumerate(lines):
        for current, replacement in replacements.items():
            if current in line:
                lines[index] = line.replace(current, replacement)
                break
    return "".join(lines)


def package_name(text: str) -> str:
    """The package's `configuration.name`, the name the Sync API lists it under."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        msg = "the package is not valid YAML, so its configuration.name cannot be read"
        raise DemoPackageError(msg) from error
    configuration = document.get("configuration") if isinstance(document, dict) else None
    name = configuration.get("name") if isinstance(configuration, dict) else None
    if not isinstance(name, str) or not name:
        msg = "the package has no configuration.name"
        raise DemoPackageError(msg)
    return name
