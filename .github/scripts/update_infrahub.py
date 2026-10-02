"""Move the pinned Infrahub release, and its matching image digest, to a new version.

The development and preview stacks pin Infrahub by tag and by the tag's top-level
index digest (see the header of `development/docker-compose.infrahub.yml`). A tag
without its digest would still pull the old image, so both move together. The
current pin is read from `development/preview.env`, the one file that states it
as plain values, and every occurrence in the pinned files is rewritten.

Pages that report a measured run (ADRs, tutorials, platform limits) name the
version they were measured on and are left alone.

Usage: python .github/scripts/update_infrahub.py 1.11.0
"""

# ruff: noqa: INP001 -- GitHub Actions runs this script directly.

from __future__ import annotations

import json
import re
import subprocess  # noqa: S404 -- one fixed docker command inspects a public registry tag
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "registry.opsmill.io/opsmill/infrahub"
PREVIEW_ENV = Path("development/preview.env")
# Every file that carries the pinned version; the first two also carry the digest.
PINNED_FILES = (
    Path("development/docker-compose.infrahub.yml"),
    PREVIEW_ENV,
    Path("development/docker-compose.preview.yml"),
    Path("tests/compose/conftest.py"),
    Path("tests/compose/fixture-override.yaml"),
    Path("tests/compose/test_bundle_contract.py"),
    Path("tests/preview/test_preview_configuration.py"),
)
DIGEST_FILES = (Path("development/docker-compose.infrahub.yml"), PREVIEW_ENV)
REQUIRED_PLATFORMS = frozenset({"linux/amd64", "linux/arm64"})
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[a-z0-9.-]*[a-z0-9])?")
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


class UpdateError(RuntimeError):
    """The requested version cannot be pinned safely."""


def current_pin(root: Path) -> tuple[str, str]:
    """Return the version and bare digest (`sha256:...`) the preview env file pins."""
    values: dict[str, str] = {}
    for line in (root / PREVIEW_ENV).read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#"):
            values[key.strip()] = value.strip()
    version = values.get("VERSION", "")
    digest = values.get("INFRAHUB_DOCKER_IMAGE_DIGEST", "").removeprefix("@")
    if not VERSION_PATTERN.fullmatch(version) or not DIGEST_PATTERN.fullmatch(digest):
        msg = f"{PREVIEW_ENV} does not pin a version and digest: VERSION={version!r}"
        raise UpdateError(msg)
    return version, digest


def resolve_digest(version: str) -> str:
    """Return the top-level index digest of the tag, after checking its platforms."""
    result = subprocess.run(  # noqa: S603 -- argv is fixed apart from the validated version
        ["docker", "buildx", "imagetools", "inspect", f"{IMAGE}:{version}", "--format", "{{json .Manifest}}"],  # noqa: S607
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        msg = f"cannot inspect {IMAGE}:{version}: {result.stderr.strip()}"
        raise UpdateError(msg)
    manifest = json.loads(result.stdout)
    platforms = {
        f"{entry['platform']['os']}/{entry['platform']['architecture']}"
        for entry in manifest.get("manifests", [])
        if "platform" in entry
    }
    missing = REQUIRED_PLATFORMS - platforms
    if missing:
        msg = f"{IMAGE}:{version} is not an index for {sorted(missing)}"
        raise UpdateError(msg)
    digest = str(manifest.get("digest", ""))
    if not DIGEST_PATTERN.fullmatch(digest):
        msg = f"{IMAGE}:{version} returned no index digest"
        raise UpdateError(msg)
    return digest


def version_occurrences(version: str) -> re.Pattern[str]:
    """Match the version as a whole token, so 1.10.6 never matches inside 1.10.60."""
    return re.compile(rf"(?<![0-9.]){re.escape(version)}(?![0-9]|\.[0-9])")


def update(root: Path, version: str, resolve: Callable[[str], str] = resolve_digest) -> list[Path]:
    """Rewrite every pinned file to `version` and return the files that changed."""
    if not VERSION_PATTERN.fullmatch(version):
        msg = f"not a release version: {version!r}"
        raise UpdateError(msg)
    old_version, old_digest = current_pin(root)
    if version == old_version:
        return []
    new_digest = resolve(version)
    pattern = version_occurrences(old_version)
    # Rewrite in memory first, so a file that lost its pin leaves the tree untouched.
    rewritten: dict[Path, str] = {}
    for relative in PINNED_FILES:
        before = (root / relative).read_text(encoding="utf-8")
        after = pattern.sub(version, before)
        if relative in DIGEST_FILES:
            if old_digest not in after:
                msg = f"{relative} does not carry the pinned digest {old_digest}"
                raise UpdateError(msg)
            after = after.replace(old_digest, new_digest)
        if after == before:
            msg = f"{relative} does not carry the pinned version {old_version}"
            raise UpdateError(msg)
        rewritten[relative] = after
    for relative, content in rewritten.items():
        (root / relative).write_text(content, encoding="utf-8")
    return list(rewritten)


def main(argv: list[str]) -> int:
    """Update the pin to the version named on the command line."""
    if len(argv) != 1:
        sys.stderr.write("usage: update_infrahub.py VERSION\n")
        return 2
    try:
        changed = update(ROOT, argv[0])
    except UpdateError as error:
        sys.stderr.write(f"error: {error}\n")
        return 1
    for path in changed:
        sys.stdout.write(f"updated {path}\n")
    if not changed:
        sys.stdout.write(f"Infrahub is already pinned to {argv[0]}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
