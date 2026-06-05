"""Potenda receives the auto-computed top_level when order: is omitted."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from infrahub_sync import SyncInstance

from infrahub_sync.utils import get_instance, get_potenda_from_instance

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def netbox_instance(tmp_path: Path) -> SyncInstance:
    """Load the netbox_to_infrahub config but blank out `order:` to force
    the auto-tier path."""
    src = EXAMPLES_DIR / "netbox_to_infrahub" / "config.yml"
    dst = tmp_path / "config.yml"
    dst.write_text(src.read_text())
    inst = get_instance(config_file=str(dst))
    assert inst is not None
    inst.order = []  # force fallback to compute_order()
    return inst


def test_potenda_top_level_comes_from_compute_order(netbox_instance: SyncInstance) -> None:
    """If order: is empty, Potenda is built with the flattened tier order."""
    with patch("infrahub_sync.utils.import_adapter") as fake_import:
        fake_import.return_value = MagicMock()
        ptd = get_potenda_from_instance(sync_instance=netbox_instance)
    expected = netbox_instance.compute_order()
    assert ptd.top_level == expected
    assert "BuiltinTag" in ptd.top_level
