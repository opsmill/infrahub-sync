"""Potenda stores the computed tiers without logging them again."""

from __future__ import annotations

import logging
import re
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


def test_potenda_does_not_repeat_the_tier_layout_it_is_given(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="infrahub_sync")
    Potenda(
        source=_FakeAdapter(),  # ty: ignore[invalid-argument-type]
        destination=_FakeAdapter(),  # ty: ignore[invalid-argument-type]
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=["A", "B"],
        tiers=[{"A"}, {"B"}],
    )
    assert not [r.getMessage() for r in caplog.records if re.search(r"\btier \d+ \(", r.getMessage())]
