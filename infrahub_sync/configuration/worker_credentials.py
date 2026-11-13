"""The credential variable names a worker holds and a package references (spec 020 FR-035).

`configs validate` runs in the Sync API, which does not hold the variables a run reads: Compose
gives the worker and the API different environments. So each worker publishes the NAMES of its
set, non-empty variables of the credential namespace (`held_credential_names`), and validation
compares the variables a package references (`referenced_credential_variables`) with the names
the live workers published. No function here reads or returns a value.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, cast

from .credentials import ENV_CREDENTIAL_PREFIX

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from .models import ConfigurationPackage

# The names a worker publishes: the credential namespace only, never another setting.
_CREDENTIAL_NAME = re.compile(rf"^{ENV_CREDENTIAL_PREFIX}[A-Za-z0-9_]+$")


def held_credential_names(environment: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Return the sorted names of the set, non-empty credential variables of this process.

    Compose passes an unset variable as an empty string, so an empty value counts as unset,
    as the `env` credential provider counts it. Only names are returned.
    """
    values = os.environ if environment is None else environment
    return tuple(sorted(name for name, value in values.items() if _CREDENTIAL_NAME.fullmatch(name) and value))


def is_credential_name(name: object) -> bool:
    """Whether `name` is a variable name of the credential namespace."""
    return isinstance(name, str) and _CREDENTIAL_NAME.fullmatch(name) is not None


def _reference_names(value: object) -> Iterable[str]:
    """Every `$credential` reference name in declared settings, depth first."""
    if isinstance(value, dict):
        mapping = cast("dict[str, object]", value)
        reference = mapping.get("$credential")
        if set(mapping) == {"$credential"} and isinstance(reference, str):
            yield reference
            return
        for child in mapping.values():
            yield from _reference_names(child)
    elif isinstance(value, list):
        for child in cast("list[object]", value):
            yield from _reference_names(child)


def referenced_credential_variables(package: ConfigurationPackage) -> tuple[tuple[str, str], ...]:
    """Return `(reference name, variable name)` for each `$credential` the configuration uses.

    Only references the package declares with the `env` provider name a worker variable; an
    undeclared reference or another provider is the core validation's finding. Sorted by
    reference name, each once.
    """
    declared = package.configuration.model_dump(mode="json", by_alias=True)
    found: dict[str, str] = {}
    for reference_name in _reference_names(declared):
        reference = package.credentials.get(reference_name)
        if reference is not None and reference.provider == "env":
            found[reference_name] = reference.identifier
    return tuple(sorted(found.items()))
