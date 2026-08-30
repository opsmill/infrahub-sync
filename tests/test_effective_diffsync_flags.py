"""Effective DiffSync flag resolution — the SYNC-78 deletion-safety rule.

Under the supported live-sync profile, ``SKIP_UNMATCHED_DST`` is invariant: a
destination-only object is never turned into a delete action, no matter which
unrelated flags a configuration declares. These tests pin the engine behavior
(Potenda) and the one centralized rule the engine consumes.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from diffsync import Adapter, DiffSyncModel
from diffsync.enum import DiffSyncFlags

from infrahub_sync import (
    SyncAdapter,
    SyncInstance,
    requested_destination_write_operations,
    resolve_effective_diffsync_flags,
)
from infrahub_sync.potenda import Potenda

_DST = DiffSyncFlags.SKIP_UNMATCHED_DST


# --- The one centralized flag-aggregation rule (SYNC-78) ---------------------


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        # Preservation: no-flags default resolves byte-identically to today.
        pytest.param(None, _DST, id="none"),
        pytest.param([], _DST, id="empty"),
        # Preservation: explicit sets that include SKIP_UNMATCHED_DST.
        pytest.param(["SKIP_UNMATCHED_DST"], _DST, id="dst-only"),
        pytest.param(
            ["SKIP_UNMATCHED_SRC", "SKIP_UNMATCHED_DST"],
            DiffSyncFlags.SKIP_UNMATCHED_SRC | _DST,
            id="src-and-dst",
        ),
        pytest.param(
            [DiffSyncFlags.CONTINUE_ON_FAILURE, DiffSyncFlags.SKIP_UNMATCHED_DST],
            DiffSyncFlags.CONTINUE_ON_FAILURE | _DST,
            id="continue-and-dst-enum",
        ),
        # Defect closure: every unrelated nonzero flag, alone and combined,
        # keeps the SKIP_UNMATCHED_DST invariant instead of replacing it.
        pytest.param(["SKIP_UNMATCHED_SRC"], DiffSyncFlags.SKIP_UNMATCHED_SRC | _DST, id="src-only"),
        pytest.param(["CONTINUE_ON_FAILURE"], DiffSyncFlags.CONTINUE_ON_FAILURE | _DST, id="continue-only"),
        pytest.param(["LOG_UNCHANGED_RECORDS"], DiffSyncFlags.LOG_UNCHANGED_RECORDS | _DST, id="log-only"),
        pytest.param(
            ["SKIP_UNMATCHED_SRC", "CONTINUE_ON_FAILURE"],
            DiffSyncFlags.SKIP_UNMATCHED_SRC | DiffSyncFlags.CONTINUE_ON_FAILURE | _DST,
            id="src-and-continue",
        ),
    ],
)
def test_rule_resolution(configured: list[str | DiffSyncFlags] | None, expected: DiffSyncFlags) -> None:
    assert resolve_effective_diffsync_flags(configured) == expected


def test_rule_invariant_holds_for_every_single_named_flag() -> None:
    for flag in DiffSyncFlags:
        if flag == DiffSyncFlags.NONE:
            continue
        assert resolve_effective_diffsync_flags([flag]) & _DST, flag


def test_rule_unknown_flag_name_raises_key_error() -> None:
    # Same failure mode as the engine's previous inline coercion.
    with pytest.raises(KeyError):
        resolve_effective_diffsync_flags(["NOT_A_FLAG"])


# --- Engine fixtures ----------------------------------------------------------


def _instance(flags: list[str | DiffSyncFlags] | None) -> SyncInstance | None:
    """Build a SyncInstance declaring `flags`; `None` means no configuration at all."""
    if flags is None:
        return None
    return SyncInstance(
        name="flags-under-test",
        source=SyncAdapter(name="source"),
        destination=SyncAdapter(name="destination"),
        diffsync_flags=flags,
        directory=".",
    )


class _Widget(DiffSyncModel):
    """Minimal identifier-only model for in-memory sync fixtures."""

    _modelname = "widget"
    _identifiers = ("name",)
    _attributes = ()

    name: str


class _SpiedWidget(_Widget):
    """Destination model whose custom ``delete`` records every invocation."""

    delete_calls: ClassVar[list[str]] = []

    def delete(self) -> _SpiedWidget | None:
        type(self).delete_calls.append(self.name)
        return super().delete()


class _SourceAdapter(Adapter):
    widget = _Widget
    top_level: ClassVar[list[str]] = ["widget"]


class _DestinationAdapter(Adapter):
    widget = _SpiedWidget
    top_level: ClassVar[list[str]] = ["widget"]


def _engine_with_destination_only_object(
    flags: list[str | DiffSyncFlags] | None,
) -> tuple[Potenda, _DestinationAdapter]:
    """Build a Potenda over one empty source and one destination-only widget."""
    _SpiedWidget.delete_calls.clear()
    source = _SourceAdapter(name="source")
    destination = _DestinationAdapter(name="destination")
    destination.add(_SpiedWidget(name="stale"))
    engine = Potenda(
        source=source,
        destination=destination,
        config=_instance(flags),  # ty: ignore[invalid-argument-type]  # engine guards config=None
        top_level=["widget"],
        show_progress=False,
    )
    return engine, destination


# --- Potenda resolves configured flags through the shared SYNC-78 rule --------


def test_engine_no_config_defaults_to_skip_unmatched_dst() -> None:
    # Preservation guard (green before the fix): no-flags default unchanged.
    engine, _ = _engine_with_destination_only_object(None)
    assert engine.flags == DiffSyncFlags.SKIP_UNMATCHED_DST


def test_engine_empty_flag_list_defaults_to_skip_unmatched_dst() -> None:
    # Preservation guard (green before the fix).
    engine, _ = _engine_with_destination_only_object([])
    assert engine.flags == DiffSyncFlags.SKIP_UNMATCHED_DST


def test_engine_explicit_set_including_skip_unmatched_dst_is_unchanged() -> None:
    # Preservation guard (green before the fix): explicit sets that already
    # carry SKIP_UNMATCHED_DST resolve byte-identically to the old rule.
    engine, _ = _engine_with_destination_only_object(["SKIP_UNMATCHED_SRC", "SKIP_UNMATCHED_DST"])
    assert engine.flags == DiffSyncFlags.SKIP_UNMATCHED_SRC | DiffSyncFlags.SKIP_UNMATCHED_DST


def test_engine_nonzero_set_lacking_skip_unmatched_dst_keeps_the_invariant() -> None:
    # SYNC-78 defect: a nonzero flag set lacking SKIP_UNMATCHED_DST used to
    # replace the safe default entirely, silently enabling delete actions.
    engine, _ = _engine_with_destination_only_object(["SKIP_UNMATCHED_SRC"])
    assert engine.flags == DiffSyncFlags.SKIP_UNMATCHED_SRC | DiffSyncFlags.SKIP_UNMATCHED_DST


# --- SYNC-78 reproduction row 2: SKIP_UNMATCHED_SRC alone must not delete -----


def test_diff_requests_no_delete_for_destination_only_object() -> None:
    engine, _ = _engine_with_destination_only_object(["SKIP_UNMATCHED_SRC"])
    diff = engine.diff()
    assert not diff.has_diffs()


def test_custom_delete_implementation_is_never_invoked() -> None:
    engine, _ = _engine_with_destination_only_object(["SKIP_UNMATCHED_SRC"])
    engine.sync()
    assert _SpiedWidget.delete_calls == []


def test_post_sync_destination_view_is_complete() -> None:
    # The post-sync snapshot is written from the in-memory destination
    # store; the destination-only object must survive the sync.
    engine, destination = _engine_with_destination_only_object(["SKIP_UNMATCHED_SRC"])
    engine.sync()
    assert [widget.get_unique_id() for widget in destination.get_all("widget")] == ["stale"]


# --- The operations-level sibling: requested destination write operations (AR5) ---


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        # The supported live-sync default: matched objects update, source-only objects create.
        pytest.param(None, frozenset({"create", "update"}), id="none"),
        pytest.param([], frozenset({"create", "update"}), id="empty"),
        pytest.param(["SKIP_UNMATCHED_DST"], frozenset({"create", "update"}), id="dst-only"),
        # AR5 discriminating fixture: a nonzero flag set lacking SKIP_UNMATCHED_DST does not
        # yield delete as a requested operation (SYNC-78's settled deletion rule).
        pytest.param(["SKIP_UNMATCHED_SRC"], frozenset({"update"}), id="src-only"),
        pytest.param(["CONTINUE_ON_FAILURE"], frozenset({"create", "update"}), id="continue-only"),
        pytest.param(["SKIP_UNMATCHED_SRC", "SKIP_UNMATCHED_DST"], frozenset({"update"}), id="src-and-dst"),
        pytest.param([DiffSyncFlags.CONTINUE_ON_FAILURE], frozenset({"create", "update"}), id="enum-member"),
    ],
)
def test_requested_destination_write_operations(
    configured: list[str | DiffSyncFlags] | None, expected: frozenset[str]
) -> None:
    assert requested_destination_write_operations(configured) == expected


def test_requested_operations_never_include_delete_for_any_single_named_flag() -> None:
    # The invariant restated at the operations level: no configured flag shape under the
    # supported live-sync profile turns a destination-only object into a delete request.
    for flag in DiffSyncFlags:
        configured = None if flag == DiffSyncFlags.NONE else [flag]
        assert "delete" not in requested_destination_write_operations(configured), flag


def test_requested_operations_unknown_flag_name_raises_key_error() -> None:
    # Same failure mode as the flags-level rule it consumes.
    with pytest.raises(KeyError):
        requested_destination_write_operations(["NOT_A_FLAG"])
