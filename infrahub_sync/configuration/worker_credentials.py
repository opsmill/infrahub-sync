"""Compare a package's credential declarations with the variables a live worker holds (FR-033).

`configs validate --branch` runs in `sync-api`, which does not hold the source credentials a
run reads. Each worker publishes the names of its set credential variables to the product
store; this module compares a package's `env` credential declarations with the most recent
publication of a live worker. It reads names only, so a finding never carries a value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import ValidationFinding, safe_pointer_component, sort_findings
from .validation import _finding_message
from .warnings import worker_environment_unknown

if TYPE_CHECKING:
    from collections.abc import Collection

    from .models import ConfigurationPackage

_CODE_CREDENTIAL_NOT_SET_IN_WORKER = "credential-not-set-in-worker"

_ENVIRONMENT_PROVIDER = "env"
_CREDENTIALS_LOCATION = "/credentials"


def collect_worker_credential_findings(
    package: ConfigurationPackage, worker_credential_names: Collection[str] | None
) -> tuple[ValidationFinding, ...]:
    """Report each declared credential variable the worker does not hold, in contract order.

    ``worker_credential_names`` is the published list of a live worker, or ``None`` when no
    live worker has published one: the credentials cannot be checked then, which is a
    warning rather than an error.
    """
    if worker_credential_names is None:
        return (worker_environment_unknown(),)
    held = frozenset(worker_credential_names)
    findings = [
        ValidationFinding(
            code=_CODE_CREDENTIAL_NOT_SET_IN_WORKER,
            severity="error",
            location=f"{_CREDENTIALS_LOCATION}/{safe_pointer_component(name)}",
            message=_finding_message(f"credential variable {reference.identifier} is not set in the worker"),
        )
        for name, reference in package.credentials.items()
        if reference.provider == _ENVIRONMENT_PROVIDER and reference.identifier not in held
    ]
    return sort_findings(findings)
