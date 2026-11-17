"""The Sync API reads the principal of a named actor without a bearer token."""

from __future__ import annotations

import json

import pytest

from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver, Principal

OPERATOR_TOKEN = "operator-fake-token-0001"  # noqa: S105 - a fixed test value, not a credential
ADMIN_TOKEN = "admin-fake-token-00000002"  # noqa: S105 - a fixed test value, not a credential


@pytest.fixture
def resolver(monkeypatch: pytest.MonkeyPatch) -> EnvironmentPrincipalResolver:
    """Return a resolver with one operator and one administrator."""
    monkeypatch.setenv(
        PRINCIPALS_ENV,
        json.dumps(
            {
                "operator": {"token": OPERATOR_TOKEN},
                " admin ": {"token": ADMIN_TOKEN, "administrator": True},
            }
        ),
    )
    return EnvironmentPrincipalResolver.from_environment()


@pytest.mark.parametrize(
    ("actor", "administrator"),
    [("operator", False), ("admin", True)],
    ids=["operator", "administrator"],
)
def test_principal_of_returns_the_principal_of_a_configured_actor(
    resolver: EnvironmentPrincipalResolver,
    actor: str,
    administrator: bool,  # noqa: FBT001 - one parametrized dimension of the configured actors.
) -> None:
    assert resolver.principal_of(actor) == Principal(actor=actor, administrator=administrator)


@pytest.mark.parametrize(
    "actor",
    ["nobody", "", "Operator", "operator ", " admin ", OPERATOR_TOKEN, ADMIN_TOKEN],
    ids=["unknown", "empty", "other-case", "trailing-space", "untrimmed-name", "operator-token", "admin-token"],
)
def test_principal_of_returns_none_for_a_name_that_is_not_a_configured_actor(
    resolver: EnvironmentPrincipalResolver, actor: str
) -> None:
    assert resolver.principal_of(actor) is None


@pytest.mark.parametrize(("actor", "token"), [("operator", OPERATOR_TOKEN), ("admin", ADMIN_TOKEN)])
def test_principal_of_matches_the_principal_that_the_token_resolves_to(
    resolver: EnvironmentPrincipalResolver, actor: str, token: str
) -> None:
    assert resolver.principal_of(actor) == resolver.resolve(token)


@pytest.mark.parametrize("actor", ["operator", "admin"])
def test_principal_of_never_returns_a_token(resolver: EnvironmentPrincipalResolver, actor: str) -> None:
    principal = resolver.principal_of(actor)

    assert principal is not None
    assert set(principal.model_dump()) == {"actor", "administrator"}
    for text in (repr(principal), str(principal), principal.model_dump_json()):
        assert OPERATOR_TOKEN not in text
        assert ADMIN_TOKEN not in text


def test_principal_of_writes_no_log_and_no_output(
    resolver: EnvironmentPrincipalResolver, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("DEBUG"):
        resolver.principal_of("operator")
        resolver.principal_of("nobody")

    captured = capsys.readouterr()
    assert (captured.out, captured.err, caplog.records) == ("", "", [])
