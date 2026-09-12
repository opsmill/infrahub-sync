"""What the build context is allowed to contain, measured by building with it."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.image.conftest import REPO_ROOT, docker, external_image_references

pytestmark = pytest.mark.docker

DOCKERFILE = REPO_ROOT / "Dockerfile"

# Everything the image build needs, and nothing else. Checked as an equality so a
# file newly reaching the context fails here rather than shipping unnoticed.
ALLOWED_CONTEXT = {
    "LICENSE.txt",
    "README.md",
    "infrahub_sync",
    "opsmill_prefect_extras",
    "pyproject.toml",
    "uv.lock",
}

AUDIT_TAG = "infrahub-sync-context-audit:test"


def test_the_build_context_holds_only_what_the_image_needs(tmp_path: Path) -> None:
    """Credentials, local overrides, VCS internals, and caches never leave the host."""
    base = external_image_references(DOCKERFILE.read_text(encoding="utf-8"))[0]
    audit = tmp_path / "Dockerfile.audit"
    audit.write_text(f"FROM {base}\nCOPY . /context\n", encoding="utf-8")

    built = docker(
        ["build", "--file", str(audit), "--tag", AUDIT_TAG, "--load", str(REPO_ROOT)],
        timeout=600,
    )
    try:
        assert built.returncode == 0, built.stderr
        listed = docker(["run", "--rm", AUDIT_TAG, "ls", "-A", "/context"])
        assert listed.returncode == 0, listed.stderr
        assert set(listed.stdout.split()) == ALLOWED_CONTEXT
    finally:
        docker(["image", "rm", "--force", AUDIT_TAG])
