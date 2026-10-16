"""Shared isolation for the service tests."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from infrahub_sync.service.trust_store import DIRECTORY_SETTING, TRUST_VARIABLES

if TYPE_CHECKING:
    from collections.abc import Iterator

# The CA directory setting and every variable the trust store installer sets.
TRUST_ENVIRONMENT = (DIRECTORY_SETTING, *TRUST_VARIABLES)


@pytest.fixture(autouse=True)
def _isolated_trust_environment() -> Iterator[None]:
    """Run each case without the shell's CA settings, and undo what an entry point installs.

    The service entry points call the real trust store installer against `os.environ`.
    A CA directory exported in the developer's shell would make a case build a bundle,
    and the variables the installer sets would reach every later case. `monkeypatch.delenv`
    alone does not cover the second: it records nothing for a name that was absent.
    """
    saved = {name: os.environ.pop(name) for name in TRUST_ENVIRONMENT if name in os.environ}
    try:
        yield
    finally:
        for name in TRUST_ENVIRONMENT:
            os.environ.pop(name, None)
        os.environ.update(saved)
