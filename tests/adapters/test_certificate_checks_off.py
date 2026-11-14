"""A source set to `verify_ssl: false` keeps certificate checks off when a CA bundle variable is set."""

from __future__ import annotations

import pytest
import requests

from infrahub_sync.adapters.utils import keep_certificate_checks_off

BUNDLE = "/etc/infrahub-sync/bundle.pem"  # a path that is never read
CUSTOM = "/etc/custom.pem"


@pytest.mark.parametrize("variable", ["REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"])
def test_a_bundle_variable_does_not_turn_checks_back_on(variable: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(variable, BUNDLE)
    session = requests.Session()
    session.verify = False

    before = session.merge_environment_settings("https://netbox.example.test", {}, None, None, None)
    keep_certificate_checks_off(session)
    after = session.merge_environment_settings("https://netbox.example.test", {}, None, None, None)

    assert before["verify"] == BUNDLE
    assert after["verify"] is False


def test_a_session_that_checks_certificates_still_uses_the_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", BUNDLE)
    session = keep_certificate_checks_off(requests.Session())

    settings = session.merge_environment_settings("https://netbox.example.test", {}, None, None, None)

    assert settings["verify"] == BUNDLE


def test_a_request_level_setting_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", BUNDLE)
    session = requests.Session()
    session.verify = False
    keep_certificate_checks_off(session)

    settings = session.merge_environment_settings("https://netbox.example.test", {}, None, CUSTOM, None)

    assert settings["verify"] == CUSTOM
