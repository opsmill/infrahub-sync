"""Read the Prefect UI address the Sync API links flow runs to."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Mapping

PREFECT_UI_URL_ENV = "INFRAHUB_SYNC_PREFECT_UI_URL"
# The value is never quoted: an operator may paste an address that carries credentials.
_INVALID = f"{PREFECT_UI_URL_ENV} must be an absolute http or https URL without credentials, query or fragment"


def prefect_ui_url_from_environment(environ: Mapping[str, str] = os.environ) -> str | None:
    """Return the Prefect UI base address without its trailing `/`, or `None` when it is unset or empty.

    Raises:
        ValueError: The value is not an absolute http or https URL, or it carries user info, a query or
            a fragment. The message names the setting and does not contain the value.
    """
    value = environ.get(PREFECT_UI_URL_ENV, "").strip()
    if not value:
        return None
    # httpx drops an empty `?` or `#`, so the raw value is checked for both.
    if "?" in value or "#" in value:
        raise ValueError(_INVALID)
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL:
        raise ValueError(_INVALID) from None
    # httpx percent-encodes characters a host cannot hold, such as an unclosed `[`.
    authority_invalid = url.scheme not in {"http", "https"} or not url.host or "%" in url.host
    carries_extras = any((url.username, url.password, url.query, url.fragment))
    if authority_invalid or carries_extras:
        raise ValueError(_INVALID)
    return str(url).rstrip("/")


def flow_run_url(base: str, flow_run_id: str) -> str:
    """Return the Prefect UI page of one flow run under the validated base address."""
    return f"{base}/runs/flow-run/{flow_run_id}"
