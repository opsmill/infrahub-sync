"""The NetBox example's Infrahub object file carries `package.yml` exactly."""

from __future__ import annotations

from pathlib import Path

import yaml

EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "netbox_to_infrahub"
OBJECT_FILE = EXAMPLE_DIR / "sync-configuration.yml"
PACKAGE_FILE = EXAMPLE_DIR / "package.yml"


def _object() -> dict:
    return yaml.safe_load(OBJECT_FILE.read_text(encoding="utf-8"))


def test_the_object_file_is_an_infrahub_object_of_the_sync_configuration_kind() -> None:
    loaded = _object()

    assert loaded["apiVersion"] == "infrahub.app/v1"
    assert loaded["kind"] == "Object"
    assert loaded["spec"]["kind"] == "SyncConfiguration"


def test_the_document_is_package_yml_verbatim() -> None:
    (entry,) = _object()["spec"]["data"]

    assert entry["document"] == PACKAGE_FILE.read_text(encoding="utf-8")


def test_the_configuration_name_matches_the_package() -> None:
    (entry,) = _object()["spec"]["data"]

    assert entry["name"] == yaml.safe_load(PACKAGE_FILE.read_text(encoding="utf-8"))["configuration"]["name"]
