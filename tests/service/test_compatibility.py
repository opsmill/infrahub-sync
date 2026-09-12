"""Compatibility metadata boundary tests."""

from importlib.metadata import PackageNotFoundError

import pytest

from infrahub_sync.service import compatibility


@pytest.mark.parametrize(
    "failure",
    [PackageNotFoundError("infrahub-sync"), ValueError("corrupt metadata")],
)
def test_installed_server_version_translates_metadata_failures_without_cause(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    def fail_version(_distribution_name: str) -> str:
        raise failure

    monkeypatch.setattr(compatibility, "version", fail_version)

    with pytest.raises(RuntimeError, match=r"^service package metadata is unavailable$") as caught:
        compatibility.installed_server_version()

    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "metadata_value",
    [
        None,
        "",
        " ",
        "not-a-version",
        "1..0",
        "1.0+",
        "1.0+local..part",
        "1!1!2",
    ],
)
def test_installed_server_version_rejects_invalid_metadata_values(
    monkeypatch: pytest.MonkeyPatch,
    metadata_value: object,
) -> None:
    monkeypatch.setattr(compatibility, "version", lambda _distribution_name: metadata_value)

    with pytest.raises(RuntimeError, match=r"^service package metadata is unavailable$") as caught:
        compatibility.installed_server_version()

    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "metadata_value",
    [
        "0.0.0",
        "2.0",
        "02.0.1",
        "2.0.1.dev1",
        "2.0.1rc1",
        "2.0.1.post1",
        "2.0.1+linux_x86",
        "1!2.0.1",
        "v02.0.1-rc.1+LOCAL_7",
    ],
)
def test_installed_server_version_accepts_builtin_pep440_versions_without_normalizing(
    monkeypatch: pytest.MonkeyPatch,
    metadata_value: str,
) -> None:
    monkeypatch.setattr(compatibility, "version", lambda _distribution_name: metadata_value)

    assert compatibility.installed_server_version() == metadata_value
