"""The custom adapter example loads through the base class loop (spec 009 review M8).

`examples/custom_adapter` is the shape reference the adding-an-adapter guide points to. It
implements `list_records` and `record_to_data` only, so the admission step lists a record that
cannot be built instead of failing the load.
"""

from __future__ import annotations

import json
from pathlib import Path

from examples.custom_adapter.custom_adapter_src.custom_adapter import MockdbAdapter
from examples.custom_adapter.mockdb.sync_adapter import MockdbSync
from infrahub_sync import SyncAdapter
from infrahub_sync.utils import get_instance

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DATABASE = REPO_ROOT / "examples" / "custom_adapter" / "custom_adapter_src" / "mock_db.json"


def _load(database: Path) -> MockdbSync:
    instance = get_instance(name="custom-example", directory=str(REPO_ROOT / "examples"))
    assert instance is not None
    adapter = MockdbSync(
        target="source",
        adapter=SyncAdapter(name=instance.source.name, settings={"db_path": str(database)}),
        config=instance,
    )
    # Potenda sets the load order on both sides before it loads them.
    adapter.top_level = ["InfraDevice"]
    adapter.load()
    return adapter


def test_the_example_adapter_has_no_load_loop_of_its_own() -> None:
    own = set(vars(MockdbAdapter))
    assert {"list_records", "record_to_data"} <= own
    assert own.isdisjoint({"model_loader", "load_record", "load", "peer_unique_id"})


def test_the_example_adapter_loads_every_device_of_the_fixture() -> None:
    adapter = _load(EXAMPLE_DATABASE)

    devices = adapter.get_all("InfraDevice")
    assert sorted(device.local_id for device in devices) == ["core01", "core02", "core03", "edge01", "edge02"]
    assert adapter.load_report == []


def test_a_record_that_cannot_be_built_is_listed_and_the_load_continues(tmp_path: Path) -> None:
    fixture = json.loads(EXAMPLE_DATABASE.read_text(encoding="utf-8"))
    fixture["nodes"]["devices"].append({"name": "edge03", "device_type": ["not", "a", "string"]})
    database = tmp_path / "mock_db.json"
    database.write_text(json.dumps(fixture), encoding="utf-8")

    adapter = _load(database)

    assert len(adapter.get_all("InfraDevice")) == len(fixture["nodes"]["devices"]) - 1
    assert [(entry.kind, entry.source_id, entry.reason, entry.field) for entry in adapter.load_report] == [
        ("InfraDevice", "edge03", "wrong_value_type", "type")
    ]
