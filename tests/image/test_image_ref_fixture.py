"""The smoke suite's image setting skips on a workstation and fails on a CI runner."""

from __future__ import annotations

import pytest

from tests.image import conftest


def test_an_unset_image_skips_outside_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(conftest.IMAGE_REFERENCE_ENV, raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    with pytest.raises(pytest.skip.Exception, match=conftest.IMAGE_REFERENCE_ENV):
        conftest.require_image_ref()


def test_an_unset_image_fails_on_github_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Otherwise a CI smoke run with no image reports green with every case skipped."""
    monkeypatch.delenv(conftest.IMAGE_REFERENCE_ENV, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    with pytest.raises(pytest.fail.Exception, match=conftest.IMAGE_REFERENCE_ENV):
        conftest.require_image_ref()


def test_a_named_image_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(conftest.IMAGE_REFERENCE_ENV, "infrahub-sync:smoke")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    assert conftest.require_image_ref() == "infrahub-sync:smoke"
