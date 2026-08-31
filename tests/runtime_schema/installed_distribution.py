"""Simulate an installed distribution: real files on the import path, real metadata.

Registered admission binds a dotted target's origin to the files a distribution actually
ships, so a test that only claims ownership proves nothing. These helpers lay a module
out the way an install does — inside a site-packages-shaped directory that is on
``sys.path`` — and publish metadata whose file list locates it there.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

ADAPTER_SOURCE = '''
from diffsync import Adapter, DiffSyncModel

from infrahub_sync import DiffSyncMixin, DiffSyncModelMixin


class WheelModel(DiffSyncModelMixin, DiffSyncModel):
    """The model base a runtime-built class derives from."""


class WheelAdapter(DiffSyncMixin, Adapter):
    """The adapter class registered resolution loads."""

    type = "Wheel"
'''


@dataclass(frozen=True, slots=True)
class _FakeDistribution:
    """The two parts of distribution metadata provenance reads."""

    root: Path
    relative_files: tuple[str, ...]

    @property
    def files(self) -> tuple[Path, ...]:
        return tuple(Path(name) for name in self.relative_files)

    def locate_file(self, path: str | Path) -> Path:
        return self.root / path


def install_distribution(  # noqa: PLR0913 - one call describes a whole install layout
    monkeypatch: pytest.MonkeyPatch,
    *,
    site_packages: Path,
    package: str,
    module: str,
    source: str = ADAPTER_SOURCE,
    on_import_path: bool = True,
    claimed_root: Path | None = None,
) -> str:
    """Lay `package/module.py` out under `site_packages` and publish its metadata.

    Returns the dotted name. `claimed_root` overrides where the metadata says the files
    live, which is how a shadowing case is built: the claim points at the installed copy
    while `sys.path` reaches a different one first.
    """
    import sys

    directory = site_packages / package
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "__init__.py").write_text("", encoding="utf-8")
    (directory / f"{module}.py").write_text(source, encoding="utf-8")
    relative = (f"{package}/__init__.py", f"{package}/{module}.py")
    distribution = _FakeDistribution(root=claimed_root or site_packages, relative_files=relative)
    monkeypatch.setattr("infrahub_sync.plugin_loader.distributions", lambda: [distribution])
    if on_import_path:
        monkeypatch.syspath_prepend(str(site_packages))
    for name in list(sys.modules):
        if name == package or name.startswith(f"{package}."):
            monkeypatch.delitem(sys.modules, name)
    return f"{package}.{module}"
