"""The web directory setting names the built web interface, or nothing, and never stops the service."""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

import pytest
from structlog.testing import capture_logs

from infrahub_sync.service.settings import WEB_DIRECTORY_ENV, validate_settings, web_directory

if TYPE_CHECKING:
    from pathlib import Path


def test_an_unset_setting_means_no_web_interface(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WEB_DIRECTORY_ENV, raising=False)

    with capture_logs() as logs:
        assert web_directory() is None

    assert logs == []


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_setting_means_no_web_interface(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(WEB_DIRECTORY_ENV, value)

    with capture_logs() as logs:
        assert web_directory() is None

    assert logs == []


def test_a_missing_directory_is_logged_and_treated_as_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    monkeypatch.setenv(WEB_DIRECTORY_ENV, str(missing))

    with capture_logs() as logs:
        assert web_directory() is None

    assert len(logs) == 1
    assert logs[0]["log_level"] == "warning"
    assert logs[0]["path"] == str(missing.resolve())
    assert logs[0]["variable"] == WEB_DIRECTORY_ENV


def test_a_directory_without_index_html_is_logged_and_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "assets").mkdir()
    # A directory named index.html is not a regular file.
    (tmp_path / "index.html").mkdir()
    monkeypatch.setenv(WEB_DIRECTORY_ENV, str(tmp_path))

    with capture_logs() as logs:
        assert web_directory() is None

    assert len(logs) == 1
    assert logs[0]["log_level"] == "warning"
    assert logs[0]["path"] == str(tmp_path.resolve())


@pytest.mark.parametrize(
    ("method", "error"),
    [
        pytest.param("is_file", PermissionError(13, "Permission denied"), id="directory-not-enterable"),
        pytest.param("resolve", RuntimeError("Symlink loop"), id="symbolic-link-loop"),
    ],
)
def test_a_directory_that_cannot_be_checked_is_logged_and_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, method: str, error: Exception
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setenv(WEB_DIRECTORY_ENV, str(tmp_path))
    monkeypatch.setattr(pathlib.Path, method, refuse)

    with capture_logs() as logs:
        assert web_directory() is None

    assert len(logs) == 1
    assert logs[0]["log_level"] == "warning"
    assert logs[0]["error_type"] == type(error).__name__
    assert logs[0]["variable"] == WEB_DIRECTORY_ENV


def test_a_directory_with_index_html_is_returned_resolved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setenv(WEB_DIRECTORY_ENV, str(tmp_path))

    with capture_logs() as logs:
        assert web_directory() == tmp_path.resolve()

    assert logs == []


def test_the_setting_never_fails_settings_validation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("INFRAHUB_SYNC_REQUIRE_APPROVAL", raising=False)
    monkeypatch.setenv(WEB_DIRECTORY_ENV, str(tmp_path / "missing"))

    validate_settings()
