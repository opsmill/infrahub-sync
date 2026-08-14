"""Regenerating the committed example code must be a byte-level no-op.

Each snapshot in ``tests/data/generator_schema_snapshots/`` records the destination
schema kinds an example's ``schema_mapping`` names, captured from a live Infrahub
instance loaded with the schema revision its documentation pins. Rendering the
example's generated files from that snapshot must reproduce the committed bytes
exactly; otherwise `infrahub-sync generate` would rewrite checked-in files and the
generator's determinism claim would not cover what the repository ships.

To refresh a snapshot after an intentional template or schema change: load the
example's documented schema revision into a disposable Infrahub instance or branch,
capture ``client.schema.all()`` for the mapped kinds with ``model_dump(mode="json")``,
and rerun ``infrahub-sync generate`` for the example so the committed files and the
snapshot move together.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from infrahub_sync.utils import get_instance, render_adapter

if TYPE_CHECKING:
    from infrahub_sdk.schema import GenericSchema, NodeSchema

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = Path(__file__).resolve().parent / "data" / "generator_schema_snapshots"
GENERATED_FILES = ("sync_models.py", "sync_adapter.py")


def _load_snapshot(snapshot_name: str) -> dict[str, NodeSchema | GenericSchema]:
    entries = json.loads((SNAPSHOT_DIR / snapshot_name).read_text(encoding="utf-8"))
    schema = {}
    for kind, entry in entries.items():
        cls = getattr(importlib.import_module(entry["class_module"]), entry["class_name"])
        schema[kind] = cls.model_validate(entry["data"])
    return schema


@pytest.mark.parametrize(
    ("example_name", "example_dir", "snapshot_name"),
    [
        ("from-netbox", "netbox_to_infrahub", "netbox_example_schema.json"),
        ("custom-example", "custom_adapter", "custom_example_schema.json"),
    ],
)
def test_regenerating_the_committed_example_is_a_no_op(
    tmp_path: Path,
    example_name: str,
    example_dir: str,
    snapshot_name: str,
) -> None:
    instance = get_instance(name=example_name, directory=str(REPO_ROOT / "examples"))
    assert instance is not None

    schema = _load_snapshot(snapshot_name)
    mapped = {item.name for item in instance.schema_mapping}
    assert mapped == set(schema), "snapshot kinds must match the example's schema_mapping"

    instance.directory = str(tmp_path)
    render_adapter(sync_instance=instance, schema=schema)

    committed_root = REPO_ROOT / "examples" / example_dir
    for adapter_name in (instance.source.name, instance.destination.name):
        for file_name in GENERATED_FILES:
            rendered = (tmp_path / adapter_name / file_name).read_bytes()
            committed = (committed_root / adapter_name / file_name).read_bytes()
            assert rendered == committed, (
                f"{example_dir}/{adapter_name}/{file_name} drifted from generator output; "
                "rerun `infrahub-sync generate` for this example and commit the result"
            )
