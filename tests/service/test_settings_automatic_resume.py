"""The automatic resume setting is off unless a site turns it on, and refuses a value it does not accept."""

from __future__ import annotations

import pytest

from infrahub_sync.service.settings import AUTOMATIC_RESUME_ENV, automatic_resume_enabled, validate_settings


def test_an_unset_setting_means_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AUTOMATIC_RESUME_ENV, raising=False)

    assert automatic_resume_enabled() is False


@pytest.mark.parametrize("value", ["true", "TRUE", "1", " yes ", "on"])
def test_a_true_value_turns_the_setting_on(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(AUTOMATIC_RESUME_ENV, value)

    assert automatic_resume_enabled() is True


@pytest.mark.parametrize("value", ["false", "False", "0", "no", "off"])
def test_a_false_value_turns_the_setting_off(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(AUTOMATIC_RESUME_ENV, value)

    assert automatic_resume_enabled() is False


@pytest.mark.parametrize("value", ["", "enabled", "maybe", "2"])
def test_an_unknown_value_is_refused_and_names_the_variable(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(AUTOMATIC_RESUME_ENV, value)

    with pytest.raises(ValueError, match=AUTOMATIC_RESUME_ENV) as error:
        automatic_resume_enabled()

    if value:
        assert value not in str(error.value)


def test_an_unknown_value_stops_the_service_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INFRAHUB_SYNC_REQUIRE_APPROVAL", raising=False)
    monkeypatch.delenv("INFRAHUB_SYNC_SESSION_IDLE_SECONDS", raising=False)
    monkeypatch.setenv(AUTOMATIC_RESUME_ENV, "enabled")

    with pytest.raises(ValueError, match=AUTOMATIC_RESUME_ENV):
        validate_settings()
