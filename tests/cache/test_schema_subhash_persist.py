"""When a schema is available, get_potenda_from_instance persists the
sub-hash so `apply` can later reject mismatched runs."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from infrahub_sync import (
    SchemaMappingField,
    SchemaMappingModel,
    SyncAdapter,
    SyncInstance,
)
from infrahub_sync.cache.sidecars import SchemaHashFile
from infrahub_sync.utils import get_potenda_from_instance


def test_schema_subhash_persisted_when_cached_schema_present(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))

    inst = SyncInstance(
        name="hashtest",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        directory=str(tmp_path),
        schema_mapping=[SchemaMappingModel(name="Tag", fields=[SchemaMappingField(name="name")])],
    )
    inst._cached_schema = {  # ty: ignore[unresolved-attribute]
        "Tag": SimpleNamespace(kind="Tag", attributes=[], relationships=[]),
    }

    with patch("infrahub_sync.utils.import_adapter") as fake_import:
        fake_import.return_value = MagicMock()
        ptd = get_potenda_from_instance(sync_instance=inst)

    hash_path = ptd.run_dir / "schema-sub-hash.txt"  # ty: ignore[unsupported-operator]
    assert hash_path.exists()
    assert len(SchemaHashFile.load(hash_path).value) == 12
