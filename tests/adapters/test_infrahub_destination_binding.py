"""The Infrahub adapter's effective-endpoint resolution.

The adapter connects with `INFRAHUB_ADDRESS`/`INFRAHUB_URL` **over** `settings["url"]` and
`settings["branch"]` over the `--branch` argument. `resolved_endpoint` is that resolution,
extracted so the plan's destination binding records the values the adapter actually uses —
the deployment the repo's own guidance recommends (credentials and addresses in environment
variables) is exactly the one where the config-version digest is blind to the destination.
"""

from __future__ import annotations

import pytest

from infrahub_sync.adapters.infrahub import resolved_endpoint
from infrahub_sync.plan.models import DestinationBindingRecord

SETTINGS = {"url": "http://settings.example:8000", "token": "settings-token", "branch": "settings-branch"}


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("INFRAHUB_ADDRESS", "INFRAHUB_URL", "INFRAHUB_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def test_infrahub_address_wins_over_settings_and_infrahub_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_ADDRESS", "http://address.example:8000")
    monkeypatch.setenv("INFRAHUB_URL", "http://url.example:8000")

    url, _branch = resolved_endpoint(SETTINGS, None)

    assert url == "http://address.example:8000"


def test_infrahub_url_wins_over_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_URL", "http://url.example:8000")

    url, _branch = resolved_endpoint(SETTINGS, None)

    assert url == "http://url.example:8000"


def test_settings_url_is_the_fallback() -> None:
    url, _branch = resolved_endpoint(SETTINGS, None)

    assert url == "http://settings.example:8000"


def test_settings_branch_wins_over_the_branch_argument() -> None:
    _url, branch = resolved_endpoint(SETTINGS, "argument-branch")

    assert branch == "settings-branch"


def test_the_branch_argument_is_the_fallback() -> None:
    _url, branch = resolved_endpoint({"url": "http://settings.example:8000"}, "argument-branch")

    assert branch == "argument-branch"


def test_no_url_anywhere_resolves_to_none() -> None:
    """`None` is the adapter's cue to raise its existing url-and-token refusal."""
    url, branch = resolved_endpoint({}, None)

    assert url is None
    assert branch is None


def test_the_resolved_values_build_a_binding_without_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The capture path end to end: resolved values in, a normalized token-free record out."""
    monkeypatch.setenv("INFRAHUB_ADDRESS", "HTTP://Address.Example:8000/")
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "environment-token")

    url, branch = resolved_endpoint(SETTINGS, None)
    record = DestinationBindingRecord(url=str(url), branch=branch or "main")

    assert record.url == "http://address.example:8000"
    assert record.branch == "settings-branch"
    assert "environment-token" not in record.model_dump_json()
    assert "settings-token" not in record.model_dump_json()
