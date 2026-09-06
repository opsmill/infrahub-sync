"""The destination probe answers in families, and carries nothing out with them.

It runs in the Sync image, where the declared package and the credential
resolver are, so it is the one part of preflight that sees a URL and a
credential. Everything it reports is a fixed family name: an HTTP client renders
both of those into its own exception text, and that text must not cross this
boundary.
"""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import yaml
from typing_extensions import Self  # ty targets 3.10, where typing.Self does not exist

from infrahub_sync.configuration.models import parse_configuration_package
from infrahub_sync.service.bootstrap import CONFIGURATION_PATH_ENV, BootstrapError
from infrahub_sync.service.preflight import (
    CREDENTIAL_UNRESOLVED,
    DESTINATION_UNREACHABLE,
    declared_destination_url,
    main,
    probe,
    resolve_destination_credentials,
)
from tests.configuration.validation_packages import package_data

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.configuration import ConfigurationPackage

# The URL and the credential the fakes below render into their own failure text.
DESTINATION_CANARY = "http://destination.internal:8000"
CREDENTIAL_CANARY = "preflight-destination-canary"
# An address the client refuses to parse rather than fails to reach. The port is
# the smallest way to say that; what matters is which exception it produces.
UNPARSEABLE_DESTINATION = "http://destination.internal:not-a-port"


def _package(url: str = DESTINATION_CANARY) -> ConfigurationPackage:
    content = copy.deepcopy(package_data())
    content["configuration"]["destination"]["settings"]["url"] = url
    return parse_configuration_package(content)


class _Transport:
    """An httpx.Client stand-in that answers with one fixed outcome."""

    def __init__(self, *, status: int | None = None, failure: Exception | None = None) -> None:
        self._status = status
        self._failure = failure
        self.requested: list[str] = []

    def __call__(self, **_settings: Any) -> Self:  # noqa: ANN401 -- the client's own keyword surface
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_arguments: object) -> None:
        return None

    def get(self, url: str) -> httpx.Response:
        self.requested.append(url)
        if self._failure is not None:
            raise self._failure
        assert self._status is not None
        return httpx.Response(self._status, request=httpx.Request("GET", url))


def test_the_declared_destination_url_is_the_one_probed() -> None:
    """The probe asks the address the package declares, not one it was told separately."""
    transport = _Transport(status=200)

    probe(declared_destination_url(_package()), client_factory=transport)  # ty: ignore[invalid-argument-type]

    assert transport.requested == [DESTINATION_CANARY]


@pytest.mark.parametrize("status", [200, 204, 404, 500])
def test_a_destination_that_answers_is_accepted(status: int) -> None:
    """Reachability is what this checks; what the root path serves is not its business."""
    probe(DESTINATION_CANARY, client_factory=_Transport(status=status))  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize("status", [401, 403])
def test_an_authentication_challenge_establishes_anonymous_reachability(status: int) -> None:
    """The generic probe resolves credentials but does not claim to validate one."""
    probe(DESTINATION_CANARY, client_factory=_Transport(status=status))  # ty: ignore[invalid-argument-type]


def test_an_unreachable_destination_carries_no_url_out_of_the_probe() -> None:
    """A connection error renders the address it failed to reach, and often the userinfo."""
    failure = httpx.ConnectError(f"failed to connect to {DESTINATION_CANARY} as {CREDENTIAL_CANARY}")

    with pytest.raises(BootstrapError) as refusal:
        probe(DESTINATION_CANARY, client_factory=_Transport(failure=failure))  # ty: ignore[invalid-argument-type]

    assert refusal.value.family == DESTINATION_UNREACHABLE
    rendered = str(refusal.value) + repr(refusal.value)
    assert DESTINATION_CANARY not in rendered
    assert CREDENTIAL_CANARY not in rendered


def test_a_destination_url_the_client_cannot_parse_refuses_in_the_fixed_family(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The real client, the real entry point, and the address it refused to parse.

    `main` catches the family and nothing else, so anything outside it arrives at
    the operator as a stack trace carrying the address.
    """
    content = copy.deepcopy(package_data())
    content["configuration"]["destination"]["settings"]["url"] = UNPARSEABLE_DESTINATION
    package = tmp_path / "configuration.yaml"
    package.write_text(yaml.safe_dump(content), encoding="utf-8")
    monkeypatch.setenv(CONFIGURATION_PATH_ENV, str(package))
    monkeypatch.setenv("INFRAHUB_API_TOKEN", CREDENTIAL_CANARY)

    with caplog.at_level(logging.ERROR):
        result = main()

    assert result == 1
    assert DESTINATION_UNREACHABLE in caplog.text
    assert UNPARSEABLE_DESTINATION not in caplog.text
    assert CREDENTIAL_CANARY not in caplog.text


def test_a_destination_credential_that_resolves_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proving it resolves now is what keeps the first real run from discovering it does not."""
    monkeypatch.setenv("INFRAHUB_API_TOKEN", CREDENTIAL_CANARY)

    resolve_destination_credentials(_package())


def test_a_destination_credential_that_does_not_resolve_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unresolvable reference fails at the first run otherwise, long after start."""
    monkeypatch.delenv("INFRAHUB_API_TOKEN", raising=False)

    with pytest.raises(BootstrapError) as refusal:
        resolve_destination_credentials(_package())

    assert refusal.value.family == CREDENTIAL_UNRESOLVED


def test_a_resolved_credential_is_never_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolution is the whole result; the value has no reason to leave this function."""
    monkeypatch.setenv("INFRAHUB_API_TOKEN", CREDENTIAL_CANARY)

    assert resolve_destination_credentials(_package()) is None
