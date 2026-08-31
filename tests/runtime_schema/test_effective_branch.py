"""AR5: one branch rule feeds discovery, adapter construction, and destination binding."""

from __future__ import annotations

import pytest

from infrahub_sync.configuration.runtime import effective_destination_branch


@pytest.mark.parametrize(
    ("declared", "run_branch", "expected"),
    [
        ("staging", "review", "staging"),
        ("staging", None, "staging"),
        (None, "review", "review"),
        (None, None, "main"),
        ("", "review", "review"),
        (None, "", "main"),
    ],
)
def test_the_declared_branch_wins_then_the_run_request_then_main(
    declared: str | None, run_branch: str | None, expected: str
) -> None:
    settings = {} if declared is None else {"branch": declared}

    assert effective_destination_branch(settings, run_branch) == expected


def test_absent_settings_resolve_the_same_way() -> None:
    assert effective_destination_branch(None, "review") == "review"
    assert effective_destination_branch(None, None) == "main"
