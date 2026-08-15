from pathlib import Path

import pytest
import yaml

from infrahub_sync import DiffSyncModelMixin, SyncConfig
from infrahub_sync.adapters.utils import get_value


@pytest.mark.parametrize("interface_name", ["InterfacePhysical", "InterfaceVirtual", "InterfaceLag"])
@pytest.mark.parametrize(
    ("netbox_mode", "expected_l2_mode"),
    [("access", "access"), ("tagged", "trunk"), ("tagged-all", "trunk_all"), (None, None)],
)
def test_netbox_example_translates_interface_l2_mode(
    interface_name: str,
    netbox_mode: str | None,
    expected_l2_mode: str | None,
) -> None:
    config_path = Path(__file__).resolve().parent.parent / "examples" / "netbox_to_infrahub" / "config.yml"
    with config_path.open() as config_file:
        config = SyncConfig(**yaml.safe_load(config_file))

    interface_mapping = next(mapping for mapping in config.schema_mapping if mapping.name == interface_name)
    l2_mode_field = next(field for field in interface_mapping.fields if field.name == "l2_mode")
    l2_mode_mapping = l2_mode_field.mapping
    assert l2_mode_mapping is not None
    record = {
        "name": "PortChannel1",
        "mode": {"value": netbox_mode} if netbox_mode is not None else None,
    }

    transformed_record = DiffSyncModelMixin.transform_records(
        records=[record],
        schema_mapping=interface_mapping,
    )[0]

    assert get_value(transformed_record, l2_mode_mapping) == expected_l2_mode


@pytest.mark.parametrize("interface_name", ["InterfacePhysical", "InterfaceVirtual", "InterfaceLag"])
@pytest.mark.parametrize("netbox_mode", [{}, {"value": "unsupported"}])
def test_netbox_example_rejects_invalid_interface_l2_mode(
    interface_name: str,
    netbox_mode: object,
) -> None:
    config_path = Path(__file__).resolve().parent.parent / "examples" / "netbox_to_infrahub" / "config.yml"
    with config_path.open() as config_file:
        config = SyncConfig(**yaml.safe_load(config_file))

    interface_mapping = next(mapping for mapping in config.schema_mapping if mapping.name == interface_name)
    record = {"name": "PortChannel1", "mode": netbox_mode}

    with pytest.raises(ValueError, match="Failed to transform 'l2_mode'"):
        DiffSyncModelMixin.transform_records(
            records=[record],
            schema_mapping=interface_mapping,
        )


@pytest.mark.parametrize("interface_name", ["InterfacePhysical", "InterfaceVirtual", "InterfaceLag"])
def test_netbox_example_refuses_q_in_q_without_destination_schema_support(interface_name: str) -> None:
    config_path = Path(__file__).resolve().parent.parent / "examples" / "netbox_to_infrahub" / "config.yml"
    with config_path.open() as config_file:
        config = SyncConfig(**yaml.safe_load(config_file))

    interface_mapping = next(mapping for mapping in config.schema_mapping if mapping.name == interface_name)
    record = {"name": "PortChannel1", "mode": {"value": "q-in-q"}}

    with pytest.raises(ValueError, match="q_in_q_requires_destination_schema_support"):
        DiffSyncModelMixin.transform_records(
            records=[record],
            schema_mapping=interface_mapping,
        )
