"""The configuration-version value a saved plan is bound to (FR-011, AD041, PD-003).

The value is **opaque**: it is compared for equality at apply time and never parsed
(AD013). Two ways of obtaining one:

- the default rule — `sha256` over the canonical JSON of the parsed configuration with
  `directory` excluded and `settings` included (PD-003). `directory` is an absolute
  filesystem path, so including it would make the value machine-dependent and a plan
  produced in CI could never be applied from a developer's checkout. `settings` is
  included because a changed destination address is a changed configuration; only the
  one-way digest is written, so no credential is disclosed (FR-018). The stated
  consequence: **rotating a credential invalidates every saved plan for that
  configuration** (AD041).
- a caller-supplied value, validated as non-empty printable ASCII and stored verbatim.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

from infrahub_sync.plan.canonical import canonical_json_bytes

if TYPE_CHECKING:
    from infrahub_sync import SyncConfig

# Non-empty printable ASCII (AD035). Anchored for the model field; `re.fullmatch` is used
# here because `$` in Python also matches before a trailing newline.
CONFIG_VERSION_PATTERN = r"^[\x20-\x7e]+$"
_CONFIG_VERSION_BODY = r"[\x20-\x7e]+"

# `directory` is location, not configuration (PD-003).
CONFIG_VERSION_EXCLUDED_FIELDS = frozenset({"directory"})


def default_config_version(config: SyncConfig) -> str:
    """Compute the default configuration version for `config` (AD041, PD-003).

    `sha256` over the canonical JSON of `config.model_dump(mode="json")` with `directory`
    excluded, lowercase hex. Parsing before hashing makes the value insensitive to YAML
    comments, key order and whitespace, which is the strongest stability available without
    a version registry.
    """
    payload = config.model_dump(mode="json", exclude=set(CONFIG_VERSION_EXCLUDED_FIELDS))
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def validate_config_version(value: str) -> str:
    """Validate a caller-supplied configuration version and return it **verbatim**.

    The value is never parsed or interpreted — only checked to be a non-empty printable
    ASCII string, so it survives a JSON round trip in the manifest and reads back byte for
    byte (FR-011, AD013).
    """
    if not isinstance(value, str) or not re.fullmatch(_CONFIG_VERSION_BODY, value):
        msg = (
            f"A caller-supplied configuration version must be non-empty printable ASCII "
            f"(matching {CONFIG_VERSION_PATTERN!r}), got {value!r}."
        )
        raise ValueError(msg)
    return value


def resolve_config_version(config: SyncConfig, supplied: str | None = None) -> str:
    """Return the caller-supplied configuration version, or the default rule's value.

    The one place the two paths meet, so a caller never has to remember that a supplied
    value is validated and a computed one is not.
    """
    if supplied is None:
        return default_config_version(config)
    return validate_config_version(supplied)
