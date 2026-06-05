"""Potenda stores the computed tiers and logs them on diff."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    import pytest

from infrahub_sync.potenda import Potenda


class _FakeAdapter:
    def __str__(self) -> str:
        return "fake"

    top_level: ClassVar[list[str]] = []


def test_potenda_accepts_tiers_kwarg() -> None:
    ptd = Potenda(
        source=_FakeAdapter(),  # ty: ignore[invalid-argument-type]
        destination=_FakeAdapter(),  # ty: ignore[invalid-argument-type]
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=["A", "B", "C"],
        tiers=[{"A"}, {"B", "C"}],
    )
    assert ptd.tiers == [{"A"}, {"B", "C"}]


def test_potenda_logs_tiers_on_construction(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="infrahub_sync.potenda")
    Potenda(
        source=_FakeAdapter(),  # ty: ignore[invalid-argument-type]
        destination=_FakeAdapter(),  # ty: ignore[invalid-argument-type]
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=["A", "B"],
        tiers=[{"A"}, {"B"}],
    )
    msgs = [r.getMessage() for r in caplog.records]
    assert any("tier 0" in m for m in msgs)
    assert any("tier 1" in m for m in msgs)
