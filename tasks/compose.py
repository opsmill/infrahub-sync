"""Run the Compose bundle's contract and lifecycle suites locally.

Neither task builds an image. The lifecycle suite consumes the candidate the
image gate already built and loaded, addressed by the configuration digest that
build recorded — so what it qualifies is that artifact, not a rebuild of the
same source that would carry a different digest.

Nothing here logs in, pushes, tags for a registry, or promotes anything.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping

from invoke import Context, task

from .image import ARCHIVE_DIR, ImageTaskError, read_digests
from .utils import ESCAPED_REPO_PATH

NAMESPACE = "INFRAHUB-SYNC-COMPOSE"

SUITE = "tests/compose"
# The lifecycle suite shares one session-scoped stack and one pinned Infrahub
# fixture, so it runs single-process. The repository's default distribution
# would split that stack's fixtures across workers.
SINGLE_PROCESS = "-n 0"
# The qualified architecture. The image gate builds and smokes arm64 as well;
# the lifecycle claim is amd64.
QUALIFIED_PLATFORM = "linux/amd64"
# Passed only by the qualification command below. Every row of the matrix is
# required, so under it a skipped `compose`-marked case is a failed one. Running
# the suite directly keeps its ordinary Docker and platform skips.
ZERO_SKIP_OPTION = "--compose-zero-skip"


def qualification_command() -> str:
    """The pytest invocation the qualification claim is made with."""
    return f"pytest {SUITE} -m compose {SINGLE_PROCESS} {ZERO_SKIP_OPTION}"


def candidate_reference(platform: str = QUALIFIED_PLATFORM) -> str:
    """Return the loaded candidate image's immutable local reference.

    The Docker image ID is the OCI configuration digest of the same build, which
    is why this can name an artifact no registry holds.
    """
    record = read_digests()
    if not isinstance(record, Mapping):
        msg = "the digest record is stale or incomplete; run `uv run invoke image.build` to rebuild it"
        raise ImageTaskError(msg)
    platforms = record.get("platforms", {})
    if not isinstance(platforms, Mapping) or platform not in platforms:
        msg = f"{platform} was not built; run `uv run invoke image.build` for it first"
        raise ImageTaskError(msg)
    entry = platforms[platform]
    if not isinstance(entry, Mapping) or not isinstance(entry.get("config"), str):
        msg = f"{platform} has a stale or incomplete digest record; run `uv run invoke image.build` to rebuild it"
        raise ImageTaskError(msg)
    return entry["config"]


def require_loaded(context: Context, reference: str) -> None:
    """Refuse to run against an image the daemon does not hold.

    Building one here would qualify a different artifact: a build records its
    source revision, so a rebuild at this head is a new digest even when the
    application bytes are identical.
    """
    probe = context.run(f"docker image inspect {shlex.quote(reference)}", hide=True, warn=True, pty=False)
    if probe is None or probe.exited != 0:
        msg = (
            f"{reference} is not loaded in this daemon. Run `uv run invoke image.smoke` first; "
            f"it loads the built platform image and proves it is the one the layout records."
        )
        raise ImageTaskError(msg)


@task(name="contract")
def contract(context: Context) -> None:
    """Read the resolved Compose model and check the bundle's structural properties.

    Needs the Compose CLI and no daemon, so it belongs in the ordinary suite.
    """
    print(f" - [{NAMESPACE}] Checking the resolved bundle")
    with context.cd(ESCAPED_REPO_PATH):
        context.run(f"pytest {SUITE} -m 'not compose'", pty=True)


@task(name="lifecycle")
def lifecycle(context: Context, platform: str = QUALIFIED_PLATFORM) -> None:
    """Run the Docker-backed lifecycle matrix against the already-built candidate.

    This is the qualification claim, so it makes every case mandatory: a skip
    here is the claim not being made, and it would otherwise exit zero.
    """
    reference = candidate_reference(platform)
    require_loaded(context, reference)
    print(f" - [{NAMESPACE}] Qualifying {platform} candidate {reference}")
    with context.cd(ESCAPED_REPO_PATH):
        context.run(
            qualification_command(),
            env={"INFRAHUB_SYNC_IMAGE": reference},
            pty=True,
        )
    print(f" - [{NAMESPACE}] Lifecycle matrix passed against {reference}")


@task(name="reclaim")
def reclaim(context: Context) -> None:
    """Remove the exported image archives, keeping the layout and digests.

    Each archive is one platform image copied out of the retained layout, and
    those copies are the largest thing a build leaves behind. They have no reader
    once the image has been loaded and scanned, and the lifecycle matrix that
    follows starts a second container stack beside the first. The layout they
    came from stays, so any of them can be exported again.
    """
    del context
    removed = 0
    for archive in sorted(ARCHIVE_DIR.glob("image-*.tar")):
        archive.unlink()
        print(f" - [{NAMESPACE}] Removed {archive}")
        removed += 1
    print(f" - [{NAMESPACE}] Reclaimed {removed} exported archive(s); the OCI layout is untouched")
