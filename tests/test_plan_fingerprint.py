"""Unit tests for the canonical plan fingerprint (DBA-009, SC-007).

These tests assert against `compute_plan_fingerprint` only — the algorithm is
never reimplemented here, so a change in the helper cannot be masked by a
matching change in a test-local copy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.cache.fingerprint import PLAN_FINGERPRINT_FIELDS, compute_plan_fingerprint
from infrahub_sync.cache.parquet_io import write_plan

if TYPE_CHECKING:
    from pathlib import Path


def _row(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401 — heterogeneous plan-row values
    """One plan row with every PLAN_SCHEMA column present."""
    row: dict[str, Any] = {
        "action": "create",
        "resource": "InfraDevice",
        "source_id": "core01",
        "dest_id": None,
        "attribute": None,
        "old_value": None,
        "new_value": '{"name":"core01"}',
        "owner": None,
        "skip_reason": None,
        "conflict_class": None,
    }
    row.update(overrides)
    return row


def _plan(run_dir: Path, rows: list[dict[str, Any]]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    write_plan(run_dir=run_dir, rows=rows)
    return run_dir


FIVE_CREATES = [
    _row(source_id="core01", new_value='{"name":"core01","type":"7280R"}'),
    _row(source_id="core02", new_value='{"name":"core02","type":"7280R"}'),
    _row(source_id="core03", new_value='{"name":"core03","type":"7280R"}'),
    _row(source_id="edge01", new_value='{"name":"edge01","type":"MX204"}'),
    _row(source_id="edge02", new_value='{"name":"edge02","type":"MX204"}'),
]


def test_fingerprint_fields_are_the_five_canonical_columns() -> None:
    assert PLAN_FINGERPRINT_FIELDS == ("action", "resource", "source_id", "attribute", "new_value")


def test_identical_plans_hash_equal(tmp_path: Path) -> None:
    a = _plan(tmp_path / "run-a", FIVE_CREATES)
    b = _plan(tmp_path / "run-b", FIVE_CREATES)
    assert compute_plan_fingerprint(a) == compute_plan_fingerprint(b)


def test_row_order_does_not_change_the_digest(tmp_path: Path) -> None:
    a = _plan(tmp_path / "run-a", FIVE_CREATES)
    b = _plan(tmp_path / "run-b", list(reversed(FIVE_CREATES)))
    assert compute_plan_fingerprint(a) == compute_plan_fingerprint(b)


def test_run_identity_and_directory_are_excluded(tmp_path: Path) -> None:
    """Two runs differing only in run id / directory hash equal (SC-007)."""
    a = _plan(tmp_path / "canary-example" / "20260731T1200-aaaaaaaa", FIVE_CREATES)
    b = _plan(tmp_path / "other-root" / "20991231T2359-ffffffff", FIVE_CREATES)
    assert compute_plan_fingerprint(a) == compute_plan_fingerprint(b)


def test_non_projected_columns_are_excluded(tmp_path: Path) -> None:
    """Columns outside PLAN_FINGERPRINT_FIELDS never reach the digest."""
    noisy = [
        _row(dest_id="17d0-abc", old_value='{"stale":true}', owner="alice", skip_reason="none", conflict_class="soft"),
    ]
    a = _plan(tmp_path / "run-a", [_row()])
    b = _plan(tmp_path / "run-b", noisy)
    assert compute_plan_fingerprint(a) == compute_plan_fingerprint(b)


@pytest.mark.parametrize(
    "override",
    [
        {"action": "update"},
        {"resource": "InfraInterface"},
        {"source_id": "core99"},
        {"attribute": "type"},
        {"new_value": '{"name":"core02"}'},
    ],
)
def test_each_projected_field_changes_the_digest(tmp_path: Path, override: dict[str, Any]) -> None:
    a = _plan(tmp_path / "run-a", [_row()])
    b = _plan(tmp_path / "run-b", [_row(**override)])
    assert compute_plan_fingerprint(a) != compute_plan_fingerprint(b)


def test_tie_breaker_orders_rows_with_identical_sort_keys(tmp_path: Path) -> None:
    """Rows tying on (resource, source_id, action, attribute) still sort totally."""
    tied = [
        _row(source_id="core01", attribute=None, new_value='{"b":2}'),
        _row(source_id="core01", attribute=None, new_value='{"a":1}'),
    ]
    a = _plan(tmp_path / "run-a", tied)
    b = _plan(tmp_path / "run-b", list(reversed(tied)))
    assert compute_plan_fingerprint(a) == compute_plan_fingerprint(b)


def test_null_bearing_sort_key_fields_do_not_break_the_sort(tmp_path: Path) -> None:
    """A `None` in a sort-key field must not raise TypeError (E12).

    The rows tie on the leading fields, so the comparison reaches `attribute`
    where one row carries `None` and the other a string — the exact pair that
    raises `TypeError: '<' not supported between 'NoneType' and 'str'` without
    null normalization.
    """
    mixed = [
        _row(source_id="core01", attribute=None, new_value='{"a":1}'),
        _row(source_id="core01", attribute="type", new_value='{"a":1}'),
        _row(source_id="core01", attribute=None, new_value='{"b":2}'),
    ]
    forward = _plan(tmp_path / "run-a", mixed)
    reverse = _plan(tmp_path / "run-b", list(reversed(mixed)))
    digest = compute_plan_fingerprint(forward)
    assert digest == compute_plan_fingerprint(reverse)
    assert len(digest) == 64


def test_fixed_vector_digest(tmp_path: Path) -> None:
    """Pin the digest of a known plan so the algorithm cannot drift silently."""
    run_dir = _plan(
        tmp_path / "fixed",
        [
            _row(action="create", resource="InfraDevice", source_id="core01", attribute=None, new_value='{"n":1}'),
            _row(action="update", resource="InfraDevice", source_id="core02", attribute="type", new_value='{"n":2}'),
            _row(action="delete", resource="InfraInterface", source_id="eth0", attribute=None, new_value=None),
        ],
    )
    assert compute_plan_fingerprint(run_dir) == "4dc89e08cf10765e81bef6626ca76cad98b90159bb2653d7765da20d56d5c098"


def test_empty_plan_has_a_stable_digest(tmp_path: Path) -> None:
    a = _plan(tmp_path / "run-a", [])
    b = _plan(tmp_path / "run-b", [])
    assert compute_plan_fingerprint(a) == compute_plan_fingerprint(b)
    assert compute_plan_fingerprint(a) != compute_plan_fingerprint(_plan(tmp_path / "run-c", [_row()]))
