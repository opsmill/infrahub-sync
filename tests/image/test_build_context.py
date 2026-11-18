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
    "examples",
    "frontend",
    "infrahub_sync",
    "opsmill_prefect_extras",
    "pyproject.toml",
    "tests",
    "uv.lock",
}

# `examples` and `tests` reach the context only for the NetBox demo's package and
# schema, which the wheel carries; nothing else under either tree may follow them.
DEMO_ASSET_SOURCES = (
    "examples/netbox_to_infrahub/package.yml",
    "tests/data/nightly_schema/traditional-infrastructure-sot",
)

AUDIT_TAG = "infrahub-sync-context-audit:test"


def demo_asset_files() -> set[str]:
    """The files of `DEMO_ASSET_SOURCES`, as the audit image lists them under `/context`."""
    files: set[str] = set()
    for name in DEMO_ASSET_SOURCES:
        path = REPO_ROOT / name
        found = [path] if path.is_file() else [child for child in path.rglob("*") if child.is_file()]
        files.update(f"/context/{child.relative_to(REPO_ROOT).as_posix()}" for child in found)
    return files


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
        found = docker(["run", "--rm", AUDIT_TAG, "find", "/context/examples", "/context/tests", "-type", "f"])
        assert found.returncode == 0, found.stderr
        assert set(found.stdout.split()) == demo_asset_files()
    finally:
        docker(["image", "rm", "--force", AUDIT_TAG])
