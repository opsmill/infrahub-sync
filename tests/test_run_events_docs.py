"""Operator pages describe run events, run logs and Prefect's retention (spec 017, FR-011, FR-017)."""

from pathlib import Path

import pytest

DOCS_ROOT = Path(__file__).resolve().parents[1] / "docs" / "docs"


def _page(relative: str) -> str:
    return (DOCS_ROOT / relative).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "expected",
    ["`GET /events`", "`GET /runs/{run_id}/logs`", "`log-expired`", "`log-unavailable`", "`infrahub_sync.run.failed`"],
)
def test_the_http_reference_documents_the_events_and_logs_routes(expected: str) -> None:
    assert expected in _page("reference/sync-http-api.mdx")


@pytest.mark.parametrize(
    "expected",
    ["`PREFECT_SERVER_SERVICES_DB_VACUUM_ENABLED`", "`PREFECT_SERVER_SERVICES_DB_VACUUM_RETENTION_PERIOD`"],
)
def test_the_deployment_page_names_prefects_retention_settings(expected: str) -> None:
    assert expected in _page("compose-deployment.mdx")


def test_the_deployment_page_says_event_order_needs_the_containers_clocks_to_agree() -> None:
    text = " ".join(_page("compose-deployment.mdx").split()).lower()

    assert "clocks" in text
    assert "event order" in text or "order of events" in text


def test_the_orchestration_page_says_logs_are_read_through_the_sync_api() -> None:
    text = _page("orchestration.mdx")

    assert "`infrahub-sync runs logs <run_id>`" in text
    assert "`GET /runs/{run_id}/logs`" in text
