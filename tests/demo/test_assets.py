"""The demo assets resolve from the bundled directory first, then from the source tree."""

from __future__ import annotations

from pathlib import Path

import pytest

from infrahub_sync.demo import assets
from infrahub_sync.demo.errors import DemoAssetError

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_SCHEMA = REPO_ROOT / "tests" / "data" / "nightly_schema" / "traditional-infrastructure-sot"
SOURCE_PACKAGE = REPO_ROOT / "examples" / "netbox_to_infrahub" / "package.yml"


@pytest.fixture
def no_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the bundled root at an empty directory, as in a source-tree run."""
    monkeypatch.setattr(assets, "bundled_root", lambda: tmp_path / "missing-assets")


@pytest.mark.usefixtures("no_bundle")
def test_schema_files_from_the_source_tree_are_the_sixteen_yaml_files_sorted_by_name() -> None:
    files = assets.schema_files()

    assert len(files) == 16
    assert [file.name for file in files] == sorted(file.name for file in SOURCE_SCHEMA.glob("*.yml"))
    assert all(file.parent == SOURCE_SCHEMA for file in files)


@pytest.mark.usefixtures("no_bundle")
def test_netbox_package_text_from_the_source_tree_is_the_shipped_example() -> None:
    assert assets.netbox_package_text() == SOURCE_PACKAGE.read_text(encoding="utf-8")


def test_the_bundled_directory_is_preferred(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bundle = tmp_path / "assets"
    (bundle / "schema").mkdir(parents=True)
    (bundle / "netbox").mkdir()
    (bundle / "schema" / "b.yml").write_text("version: '1.0'\n", encoding="utf-8")
    (bundle / "schema" / "a.yml").write_text("version: '1.0'\n", encoding="utf-8")
    (bundle / "schema" / "README.md").write_text("not a schema\n", encoding="utf-8")
    (bundle / "netbox" / "package.yml").write_text("bundled: true\n", encoding="utf-8")
    monkeypatch.setattr(assets, "bundled_root", lambda: bundle)

    assert assets.schema_files() == [bundle / "schema" / "a.yml", bundle / "schema" / "b.yml"]
    assert assets.netbox_package_text() == "bundled: true\n"


def test_a_missing_schema_directory_names_the_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(assets, "bundled_root", lambda: tmp_path / "missing-assets")
    monkeypatch.setattr(assets, "SOURCE_SCHEMA_DIR", tmp_path / "no-schema")

    with pytest.raises(DemoAssetError, match="no-schema"):
        assets.schema_files()


def test_an_empty_schema_directory_names_the_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    empty = tmp_path / "empty-schema"
    empty.mkdir()
    monkeypatch.setattr(assets, "bundled_root", lambda: tmp_path / "missing-assets")
    monkeypatch.setattr(assets, "SOURCE_SCHEMA_DIR", empty)

    with pytest.raises(DemoAssetError, match="empty-schema"):
        assets.schema_files()


def test_a_missing_package_names_the_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(assets, "bundled_root", lambda: tmp_path / "missing-assets")
    monkeypatch.setattr(assets, "SOURCE_PACKAGE", tmp_path / "no-package.yml")

    with pytest.raises(DemoAssetError, match=r"no-package\.yml"):
        assets.netbox_package_text()
