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
    """The adapter class registered resolution loads.

    Takes the keyword arguments engine assembly passes, so this stands in for a real
    adapter all the way through construction and not only through admission.
    """

    type = "Wheel"

    def __init__(self, target, adapter, config, **kwargs):
        super().__init__(**kwargs)
        self.target = target
        self.config = config

    def model_loader(self, model_name, model):
        return None
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


def install_namespace_distribution(
    monkeypatch: pytest.MonkeyPatch,
    *,
    portions: dict[Path, dict[str, str]],
    dotted: str,
    installed_root: Path,
    on_import_path: bool = True,
) -> str:
    """Lay a PEP 420 namespace distribution out across one or more portions.

    `portions` maps each site-packages-shaped root to the `{module: source}` it supplies
    under the dotted name's parent packages, and no ``__init__.py`` is written anywhere —
    that absence is the point. Metadata is published for `installed_root`'s files only, so
    a module supplied by another portion is real, importable, and unowned.

    Roots are prepended in the order given, so the LAST one listed ends up earliest on
    ``sys.path``.
    """
    import sys

    parents = dotted.split(".")[:-1]
    owned: list[str] = []
    for root, modules in portions.items():
        directory = root.joinpath(*parents)
        directory.mkdir(parents=True, exist_ok=True)
        for module, source in modules.items():
            (directory / f"{module}.py").write_text(source, encoding="utf-8")
            if root == installed_root:
                owned.append("/".join([*parents, f"{module}.py"]))
    distribution = _FakeDistribution(root=installed_root, relative_files=tuple(owned))
    monkeypatch.setattr("infrahub_sync.plugin_loader.distributions", lambda: [distribution])
    if on_import_path:
        for root in portions:
            monkeypatch.syspath_prepend(str(root))
    top_level = parents[0]
    for name in list(sys.modules):
        if name == top_level or name.startswith(f"{top_level}."):
            monkeypatch.delitem(sys.modules, name)
    return dotted
