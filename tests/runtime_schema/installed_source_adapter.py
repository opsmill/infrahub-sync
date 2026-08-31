"""One installed, non-bundled source adapter, importable by dotted path.

Registered admission accepts a source that names an installed import target or an entry
point. This module stands in for such a distribution: it is real installed code with no
optional driver and no network, so the admitted-profile tests exercise resolution rather
than describing it.
"""

from __future__ import annotations

from diffsync import Adapter, DiffSyncModel

from infrahub_sync import DiffSyncMixin, DiffSyncModelMixin


class InstalledSourceModel(DiffSyncModelMixin, DiffSyncModel):
    """The model base a runtime-built source class derives from."""


class InstalledSourceAdapter(DiffSyncMixin, Adapter):
    """The adapter class registered resolution loads for this source."""

    type = "InstalledSource"
