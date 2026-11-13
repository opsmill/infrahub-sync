"""A reference value that starts like a not-in-source placeholder still names a stored peer."""

from __future__ import annotations

from diffsync import Adapter, DiffSyncModel

from infrahub_sync import NOT_IN_SOURCE_PLACEHOLDER_PREFIX, not_in_source_placeholder
from infrahub_sync.plan.derive import _peer_pair  # noqa: PLC2701  # the step under test
from infrahub_sync.plan.models import not_in_source_peer_pair


class _Tag(DiffSyncModel):
    _modelname = "BuiltinTag"
    _identifiers = ("name",)
    name: str


class _Source(Adapter):
    BuiltinTag = _Tag
    top_level = ["BuiltinTag"]  # noqa: RUF012  # the diffsync class attribute


def _pair(source: Adapter, unique_id: str) -> dict:
    return _peer_pair(
        store=source.store,
        config=None,
        owning_kind="DcimDevice",
        field="tags",
        candidates=("BuiltinTag",),
        unique_id=unique_id,
        chain=(),
        peers=None,
    )


def test_a_stored_peer_whose_unique_id_starts_like_a_placeholder_is_that_peer() -> None:
    """Tag `infrahub-sync:not-in-source:BuiltinTag:1` is stored: the reference names it, not a missing peer."""
    name = not_in_source_placeholder("BuiltinTag", "1")
    assert name.startswith(NOT_IN_SOURCE_PLACEHOLDER_PREFIX)
    source = _Source()
    source.add(_Tag(name=name))

    assert _pair(source, name) == {"peer_kind": "BuiltinTag", "identity": {"name": name}}


def test_a_placeholder_with_no_stored_peer_is_still_a_missing_peer() -> None:
    """With no such tag stored, the placeholder names a peer the source does not return."""
    placeholder = not_in_source_placeholder("BuiltinTag", "1")

    assert _pair(_Source(), placeholder) == not_in_source_peer_pair(placeholder)
