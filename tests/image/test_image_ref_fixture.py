"""The smoke suite's image settings skip on a workstation and fail on a CI runner."""

from __future__ import annotations

import pytest

from tests.docker_image import IMAGE_REPOSITORY_ENV, IMAGE_VERSION_ENV
from tests.image import conftest


@pytest.mark.parametrize("unset", [IMAGE_REPOSITORY_ENV, IMAGE_VERSION_ENV])
def test_an_unset_image_skips_outside_ci(monkeypatch: pytest.MonkeyPatch, unset: str) -> None:
    monkeypatch.setenv(IMAGE_REPOSITORY_ENV, "infrahub-sync")
    monkeypatch.setenv(IMAGE_VERSION_ENV, "smoke")
    monkeypatch.delenv(unset)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    with pytest.raises(pytest.skip.Exception, match=unset):
        conftest.require_image_ref()


@pytest.mark.parametrize("unset", [IMAGE_REPOSITORY_ENV, IMAGE_VERSION_ENV])
def test_an_unset_image_fails_on_github_actions(monkeypatch: pytest.MonkeyPatch, unset: str) -> None:
    """Otherwise a CI smoke run with no image reports green with every case skipped."""
    monkeypatch.setenv(IMAGE_REPOSITORY_ENV, "infrahub-sync")
    monkeypatch.setenv(IMAGE_VERSION_ENV, "smoke")
    monkeypatch.delenv(unset)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    with pytest.raises(pytest.fail.Exception, match=unset):
        conftest.require_image_ref()


def test_a_named_image_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(IMAGE_REPOSITORY_ENV, "infrahub-sync")
    monkeypatch.setenv(IMAGE_VERSION_ENV, "smoke")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    assert conftest.require_image_ref() == "infrahub-sync:smoke"
