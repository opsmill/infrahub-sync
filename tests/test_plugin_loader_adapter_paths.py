"""Adapter-path resolution keeps an earlier class when a later path fails to import."""

from __future__ import annotations

from typing import TYPE_CHECKING

from infrahub_sync.plugin_loader import PluginLoader

if TYPE_CHECKING:
    from pathlib import Path


def test_later_failing_adapter_path_keeps_earlier_resolved_class(tmp_path: Path) -> None:
    working = tmp_path / "working"
    broken = tmp_path / "broken"
    working.mkdir()
    broken.mkdir()
    (working / "external_probe.py").write_text("class WorkingAdapter:\n    pass\n")
    (broken / "external_probe.py").write_text("import module_that_does_not_exist_anywhere\n")

    loader = PluginLoader(adapter_paths=[str(working), str(broken)])
    cls = loader._resolve_from_filesystem("external_probe", "WorkingAdapter", ())

    assert cls is not None
    assert cls.__name__ == "WorkingAdapter"
