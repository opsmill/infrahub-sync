"""The Sync API's Prefect UI address: how it is read from the environment and turned into a link."""

from __future__ import annotations

import pytest

from infrahub_sync.service.prefect_ui import PREFECT_UI_URL_ENV, flow_run_url, prefect_ui_url_from_environment


@pytest.mark.parametrize("environ", [{}, {PREFECT_UI_URL_ENV: ""}], ids=("missing", "empty"))
def test_a_missing_or_empty_setting_turns_the_link_off(environ: dict[str, str]) -> None:
    """An operator who does not run the Prefect UI next to the Sync API gets no link."""
    assert prefect_ui_url_from_environment(environ) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://127.0.0.1:4230/", "http://127.0.0.1:4230"),
        ("https://example.com/prefect", "https://example.com/prefect"),
        (" http://h ", "http://h"),
    ],
)
def test_a_valid_setting_is_returned_without_its_trailing_slash(value: str, expected: str) -> None:
    """A path prefix is kept, so a Prefect UI behind a reverse proxy still links correctly."""
    assert prefect_ui_url_from_environment({PREFECT_UI_URL_ENV: value}) == expected


@pytest.mark.parametrize(
    "value",
    [
        "ftp://h",
        "127.0.0.1:4230",
        "http://u:p@h",
        "http://h/?q=1",
        "http://h/#f",
        "http://h/?",
        "http://h/#",
        "http://[bad",
        "http://h:x",
    ],
    ids=(
        "scheme",
        "relative",
        "credentials",
        "query",
        "fragment",
        "empty-query",
        "empty-fragment",
        "unclosed-bracket",
        "bad-port",
    ),
)
def test_an_invalid_setting_is_refused_without_printing_its_value(value: str) -> None:
    """The refusal names the setting but never echoes a value that may carry credentials."""
    with pytest.raises(ValueError, match=PREFECT_UI_URL_ENV) as refused:
        prefect_ui_url_from_environment({PREFECT_UI_URL_ENV: value})

    assert value not in str(refused.value)
    assert str(refused.value) == (
        "INFRAHUB_SYNC_PREFECT_UI_URL must be an absolute http or https URL without credentials, query or fragment"
    )


def test_a_flow_run_link_appends_the_prefect_flow_run_path() -> None:
    """The link opens the flow run's page in the Prefect UI."""
    assert flow_run_url("http://h/prefect", "abc") == "http://h/prefect/runs/flow-run/abc"
