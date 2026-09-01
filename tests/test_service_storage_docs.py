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


@pytest.mark.parametrize("name", ["durable-product-records.mdx", "managed-http-api.mdx"])
def test_managed_storage_operator_references_state_the_complete_deployed_contract(name: str) -> None:
    """Every service-storage reference names one PostgreSQL/S3 deployment shape."""
    text = (REFERENCE_ROOT / name).read_text(encoding="utf-8")

    assert not {setting for setting in SERVICE_STORAGE_SETTINGS if f"`{setting}`" not in text}
    assert "standard credential-provider chain" in text
    assert "absolute `http` or `https` URL with no userinfo" in text
    assert "reaches Boto3 unchanged" in text
    assert "`INFRAHUB_SYNC_CACHE_DIR`" in text
    assert "PH-2" in text
    assert not [claim for claim in ("backup", "restore", "production hardening") if claim in text.lower()]


def test_durable_records_reference_limits_the_local_projection_to_the_injected_seam() -> None:
    """The local projection is not presented as a deployed service profile."""
    text = (REFERENCE_ROOT / "durable-product-records.mdx").read_text(encoding="utf-8")

    assert "injected standalone/test seam" in text
    assert "managed Sync HTTP API and its worker use the local profile" not in text
