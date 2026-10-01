"""Changed rows must persist through stores that hand back copies."""

from __future__ import annotations

from typing import Any, ClassVar

from diffsync import Adapter, DiffSyncModel
from diffsync.store import BaseStore

from infrahub_sync.cache.cursors import CursorState, CursorTier
from infrahub_sync.cache.incremental import apply_changed_rows


class Rack(DiffSyncModel):
    """A rack distinguished by its site and name."""

    _modelname: ClassVar[str] = "TestRack"
    _identifiers: ClassVar[tuple[str, ...]] = ("name", "site")
    _attributes: ClassVar[tuple[str, ...]] = ("description",)
    name: str
    site: str | None
    description: str = "desired"
    local_id: str | None = None


class Population(Adapter):
    """In-memory adapter with a rack model."""

    TestRack = Rack
    top_level: ClassVar[list[str]] = ["TestRack"]

    @staticmethod
    def list_changed_since(_kind: str, _cursor: CursorState) -> list[dict[str, str]]:
        """Return one changed rack that overlaps the stored copy."""
        return [{"name": "same", "site": "site-a", "description": "new", "local_id": "new-id"}]


class DetachedStore(BaseStore):
    """Store that hands back copies, like a serialising store, so edits persist only through update()."""

    def __init__(self, rack: Rack) -> None:
        super().__init__(name="detached")
        self.saved = rack.model_copy()

    def get(self, *, model: Any, identifier: Any) -> Rack:  # noqa: ANN401, ARG002
        return self.saved.model_copy()

    def update(self, *, obj: Any) -> None:  # noqa: ANN401
        self.saved = obj.model_copy()


def test_overlapping_delta_update_and_local_id_are_persisted() -> None:
    """A detached stored copy keeps the delta's attributes and local_id after the load."""
    adapter = Population(name="destination")
    store = DetachedStore(Rack(name="same", site="site-a", description="old", local_id="old-id"))
    adapter.store = store
    cursor = CursorState(CursorTier.TIMESTAMP, "2026-09-29T00:00:00Z", safe=True)
    apply_changed_rows(adapter, "TestRack", Rack, cursor)
    assert (store.saved.description, store.saved.local_id) == ("new", "new-id")
