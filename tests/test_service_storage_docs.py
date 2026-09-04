"""Operator-reference contract for the deployed service storage profile."""

from pathlib import Path

import pytest

REFERENCE_ROOT = Path(__file__).resolve().parents[1] / "docs" / "docs" / "reference"
SERVICE_STORAGE_SETTINGS = frozenset(
    {
        "INFRAHUB_SYNC_DATABASE_URL",
        "INFRAHUB_SYNC_S3_BUCKET",
        "INFRAHUB_SYNC_S3_PREFIX",
        "INFRAHUB_SYNC_S3_ENDPOINT_URL",
        "INFRAHUB_SYNC_S3_REGION",
    }
)


@pytest.mark.parametrize("name", ["durable-product-records.mdx", "sync-http-api.mdx"])
def test_service_storage_operator_references_state_the_complete_deployed_contract(name: str) -> None:
    """Every service-storage reference names one PostgreSQL/S3 deployment shape."""
    text = (REFERENCE_ROOT / name).read_text(encoding="utf-8")

    assert not {setting for setting in SERVICE_STORAGE_SETTINGS if f"`{setting}`" not in text}
    assert "standard credential-provider chain" in text
    assert "absolute `http` or `https` URL with no userinfo" in text
    assert "reaches Boto3 unchanged" in text
    assert "`INFRAHUB_SYNC_CACHE_DIR`" in text
    assert "PH-2" in text
    assert not [claim for claim in ("backup", "restore", "production hardening") if claim in text.lower()]


def test_the_documented_s3_client_protocol_matches_the_one_the_store_requires() -> None:
    """The reference names a provider contract, so it cannot drift from the protocol itself."""
    from re import findall

    from infrahub_sync.product_store.store import S3Client

    text = (REFERENCE_ROOT / "durable-product-records.mdx").read_text(encoding="utf-8")
    clause = text.split("`S3Client` protocol (", 1)[1].split(")", 1)[0]

    assert set(findall(r"`([a-z_]+)`", clause)) == {name for name in vars(S3Client) if not name.startswith("_")}


def test_durable_records_reference_limits_the_local_projection_to_the_injected_seam() -> None:
    """The local projection is not presented as a deployed service profile."""
    text = (REFERENCE_ROOT / "durable-product-records.mdx").read_text(encoding="utf-8")

    assert "injected test seam" in text
    assert "Sync HTTP API and its worker use the local profile" not in text
