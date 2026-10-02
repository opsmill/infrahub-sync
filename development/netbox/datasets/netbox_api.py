"""Bounded REST operations for disposable benchmark tiers; no credential output."""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog
from typing_extensions import Self

from development.netbox.datasets.tier_data import FOUNDATION_COUNTS, RELATIONS, SKIP_COUNTS, TIER_COUNTS, Row

BATCH_SIZE = 200
log = structlog.get_logger()


class NetboxAPI:
    """REST client whose failure boundary never exposes response bodies or credentials."""

    def __init__(self, url: str, token: str) -> None:
        """Initialize the bounded REST client."""
        self.client = httpx.Client(
            base_url=url.rstrip("/") + "/", headers={"Authorization": f"Bearer {token}"}, timeout=120
        )

    def __enter__(self) -> Self:
        """Return the client for a managed request session."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Close the HTTP client when its managed session ends."""
        self.client.close()

    def request(self, method: str, kind: str, payload: Any = None, suffix: str = "") -> Any:  # noqa: ANN401 -- REST JSON responses have endpoint-specific shapes
        """Send one bounded request, reporting only method, endpoint and status on failure."""
        try:
            response = self.client.request(method, f"api/{kind}/{suffix}", json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            msg = f"NetBox {method} {kind} failed: HTTP {exc.response.status_code}"
            raise RuntimeError(msg) from None
        except httpx.HTTPError:
            msg = f"NetBox {method} {kind} failed: transport error"
            raise RuntimeError(msg) from None
        return response.json() if response.content else None

    def all(self, kind: str) -> list[dict[str, Any]]:
        """Read every page with a bounded page size."""
        rows = []
        offset = 0
        while True:
            page = self.request("GET", kind, suffix=f"?limit={BATCH_SIZE}&offset={offset}")
            rows.extend(page["results"])
            if not page["next"]:
                return rows
            offset += BATCH_SIZE

    def count(self, kind: str) -> int:
        """Read the endpoint total without loading its records."""
        return self.request("GET", kind, suffix="?limit=1")["count"]


def environment_credentials() -> tuple[str, str]:
    """Require the URL and token in the environment, without printing their values."""
    try:
        return os.environ["NETBOX_URL"], os.environ["NETBOX_API_TOKEN"]
    except KeyError:
        msg = "set NETBOX_URL and NETBOX_API_TOKEN"
        raise ValueError(msg) from None


def relation_kind(kind: str, field: str) -> str | None:
    """Identify the target endpoint of an ordinal-valued API field."""
    if kind == "circuits/circuits" and field == "type":
        return "circuits/circuit-types"
    return RELATIONS.get(field)


def resolve_ordinals(kind: str, fields: dict[str, Any], ids: dict[str, dict[int, int]]) -> dict[str, Any]:
    """Replace endpoint-local ordinals with IDs returned by NetBox."""
    result = dict(fields)
    for field, value in fields.items():
        target = relation_kind(kind, field)
        if target and value is not None:
            result[field] = [ids[target][i] for i in value] if isinstance(value, list) else ids[target][value]
    return result


def seed_dataset(api: NetboxAPI, data: dict[str, list[Row]], tier: str) -> None:
    """Bulk-create a tier only into an empty instance and verify each final endpoint count."""
    for kind in data:
        if api.count(kind):
            msg = f"refusing to seed: {kind} is not empty"
            raise ValueError(msg)
    ids: dict[str, dict[int, int]] = {}
    for kind, rows in data.items():
        ids[kind] = {}
        for start in range(0, len(rows), BATCH_SIZE):
            batch = rows[start : start + BATCH_SIZE]
            payloads = [
                resolve_ordinals(kind, {k: v for k, v in row.fields.items() if k != "lag"}, ids) for row in batch
            ]
            created = api.request("POST", kind, payloads)
            if len(created) != len(batch):
                msg = f"NetBox returned an incomplete batch for {kind}"
                raise RuntimeError(msg)
            ids[kind].update({row.id: obj["id"] for row, obj in zip(batch, created, strict=True)})
            log.info("seed_progress", endpoint=kind, created=start + len(batch), total=len(rows))
        if kind == "dcim/interfaces":
            members = [
                {"id": ids[kind][row.id], "lag": ids[kind][row.fields["lag"]]} for row in rows if "lag" in row.fields
            ]
            for start in range(0, len(members), BATCH_SIZE):
                api.request("PATCH", kind, members[start : start + BATCH_SIZE])
    verify_counts(api, tier)


def verify_counts(api: NetboxAPI, tier: str) -> None:
    """Assert endpoint totals and mapped versus deliberately skipped row counts."""
    for kind, expected in (TIER_COUNTS[tier] | FOUNDATION_COUNTS).items():
        actual = api.count(kind)
        if actual != expected + SKIP_COUNTS[tier].get(kind, 0):
            msg = f"wrong final count for {kind}: {actual}"
            raise RuntimeError(msg)
    # Verify filtered counts separately, so skip cases cannot conceal missing syncable rows.
    for kind in ("dcim/devices", "dcim/interfaces", "ipam/vlans"):
        # Inspect fields locally instead of relying on version-specific filter syntax.
        rows = api.all(kind)
        actual = sum(
            bool(row.get("name"))
            if kind == "dcim/devices"
            else bool(row["device"].get("name"))
            if kind == "dcim/interfaces"
            else row.get("group") is not None
            for row in rows
        )
        if actual != TIER_COUNTS[tier][kind]:
            msg = f"wrong syncable count for {kind}: {actual}"
            raise RuntimeError(msg)
