"""V3 refuses configured sync stores before construction or data reads."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

import pytest
import yaml

from infrahub_sync import SyncInstance
from infrahub_sync.configuration.storage import UNSUPPORTED_STORE_MESSAGE
from infrahub_sync.execution import RunValidationError, execute_run, run_remote_request
from infrahub_sync.product_store import configs, local_product_projection
from tests.configuration.validation_packages import package_data

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "store",
    [
        {"type": "redis"},
        {"type": "redis", "settings": {"password": "store-secret-canary"}},
        {"type": "local", "settings": {}},
        {"type": "unknown", "settings": {}},
        {},
    ],
)
def test_registration_refuses_every_store_block_without_persisting(tmp_path: Path, store: dict[str, Any]) -> None:
    content = package_data()
    content["configuration"]["store"] = store
    projection = local_product_projection(tmp_path)

    with pytest.raises(configs.ConfigsValidationError) as raised:
        configs.register(package=content, projection=projection)

    assert str(raised.value) == UNSUPPORTED_STORE_MESSAGE
    assert [(f.code, f.location, f.message) for f in raised.value.findings] == [
        ("unsupported-sync-store", "/configuration/store", UNSUPPORTED_STORE_MESSAGE)
    ]
    assert projection.list_configurations() == ()


def test_creating_a_version_refuses_a_store_block(tmp_path: Path) -> None:
    projection = local_product_projection(tmp_path)
    content = package_data()
    registered = configs.register(package=content, projection=projection)
    content["configuration"]["store"] = {"type": "redis"}

    with pytest.raises(configs.ConfigsValidationError) as raised:
        configs.create_version(config_id=registered.configuration.config_id, package=content, projection=projection)

    assert str(raised.value) == UNSUPPORTED_STORE_MESSAGE
    assert len(projection.list_configuration_versions(registered.configuration.config_id)) == 1


@pytest.mark.parametrize("operation", ["plan", "verify", "apply"])
def test_execution_refuses_a_store_before_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: Literal["plan", "verify", "apply"]
) -> None:
    instance = SyncInstance.model_validate(
        {
            "name": "store-policy",
            "directory": str(tmp_path),
            "source": {"name": "netbox"},
            "destination": {"name": "infrahub"},
            "store": {"type": "redis"},
        }
    )
    monkeypatch.setattr(
        "infrahub_sync.execution.get_potenda_from_instance", lambda **_kwargs: pytest.fail("adapter built")
    )
    monkeypatch.setattr("infrahub_sync.execution._execute_verify_operation", lambda **_kwargs: pytest.fail("data read"))
    monkeypatch.setattr("infrahub_sync.execution._execute_apply_operation", lambda **_kwargs: pytest.fail("data read"))

    kwargs: dict[str, Any] = {}
    if operation in {"verify", "apply"}:
        kwargs["run_id"] = "store-policy-run"
    if operation == "apply":
        kwargs.update(confirm_writes=True, ownership=object(), record_applied=lambda _record: None)

    with pytest.raises(RunValidationError) as raised:
        execute_run(instance, operation=cast("Any", operation), **kwargs)

    assert str(raised.value) == UNSUPPORTED_STORE_MESSAGE


def test_direct_prefect_request_refuses_a_store_before_adapter_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "store-policy"
    directory.mkdir()
    content = {
        "name": "store-policy",
        "source": {"name": "netbox"},
        "destination": {"name": "infrahub"},
        "store": {"type": "redis"},
    }
    (directory / "config.yml").write_text(yaml.safe_dump(content), encoding="utf-8")
    monkeypatch.setattr(
        "infrahub_sync.execution.get_potenda_from_instance", lambda **_kwargs: pytest.fail("adapter built")
    )

    with pytest.raises(RunValidationError) as raised:
        run_remote_request("store-policy", config_directory=str(tmp_path))

    assert str(raised.value) == UNSUPPORTED_STORE_MESSAGE
