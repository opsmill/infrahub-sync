"""The one place a release identity is read, validated, and turned into names.

Everything a release produces is named from here: the image's version label, the
two Python distributions, the Compose bundle, the tag, and the release itself. A
second reader would be a second answer, and the first time the two disagreed
would be after something had already been published under one of them.

The declared version has to be its own normalized form, so there is one string
rather than a declared form and a filename form that a reader has to reconcile.

Nothing here logs in, pushes, tags for a registry, publishes, or promotes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from shutil import rmtree

from invoke import Context, task
from packaging.version import InvalidVersion, Version

from .utils import ESCAPED_REPO_PATH, REPO_BASE

NAMESPACE = "INFRAHUB-SYNC-RELEASE"

REPO_ROOT = REPO_BASE
RECORD_DIR = REPO_ROOT / ".release"
RECORD_FILE = RECORD_DIR / "identity.json"
DIST_DIR = RECORD_DIR / "dist"

DISTRIBUTION = "infrahub-sync"
# PEP 427 replaces every run of `-`, `_`, or `.` in a distribution name with a
# single `_` for the file it writes, so the two forms are derived, not listed.
DISTRIBUTION_FILE = re.sub(r"[-_.]+", "_", DISTRIBUTION)
BUNDLE_STEM = "infrahub-sync-compose"
TAG_PREFIX = "v"
TITLE_STEM = "Infrahub Sync"
# The one wheel this project builds is pure Python and supports every
# interpreter its metadata claims, so its compatibility tags are fixed.
WHEEL_TAGS = "py3-none-any"

RECORD_SCHEMA_VERSION = 1

_REVISION = re.compile(r"[0-9a-f]{40}")


class ReleaseTaskError(RuntimeError):
    """Raised when a release identity is unreadable, invalid, or contradicted by an input."""


@dataclass(frozen=True)
class ReleaseIdentity:
    """One release, and every artifact name that follows from it."""

    version: str
    revision: str
    created: str

    @property
    def tag(self) -> str:
        """The Git tag this release is published under."""
        return f"{TAG_PREFIX}{self.version}"

    @property
    def title(self) -> str:
        """The title the published release carries."""
        return f"{TITLE_STEM} - {self.tag}"

    @property
    def wheel(self) -> str:
        """The built distribution's filename."""
        return f"{DISTRIBUTION_FILE}-{self.version}-{WHEEL_TAGS}.whl"

    @property
    def sdist(self) -> str:
        """The source distribution's filename."""
        return f"{DISTRIBUTION_FILE}-{self.version}.tar.gz"

    @property
    def bundle(self) -> str:
        """The Compose bundle archive's filename."""
        return f"{BUNDLE_STEM}-{self.version}.tar.gz"

    def record(self) -> dict[str, object]:
        """Return the identity as the document every later phase reads it from."""
        return {
            "schema_version": RECORD_SCHEMA_VERSION,
            "version": self.version,
            "revision": self.revision,
            "created": self.created,
            "tag": self.tag,
            "title": self.title,
            "wheel": self.wheel,
            "sdist": self.sdist,
            "bundle": self.bundle,
        }


def release_identity(*, version: str, revision: str, created: str) -> ReleaseIdentity:
    """Validate one release identity, and refuse anything that names no single release.

    An abbreviated revision or a local timestamp would leave artifacts nobody can
    trace back to one commit. An unnormalized version would leave two spellings
    of one release, and the artifacts named from each would not match.
    """
    try:
        parsed_version = Version(version)
    except InvalidVersion:
        msg = f"version {version!r} is not a release identifier"
        raise ReleaseTaskError(msg) from None
    if str(parsed_version) != version:
        msg = f"version {version!r} is not its own normalized form; declare {parsed_version} instead"
        raise ReleaseTaskError(msg)
    if not _REVISION.fullmatch(revision):
        msg = f"revision {revision!r} is not a full commit identifier"
        raise ReleaseTaskError(msg)
    # Git writes a terminal `Z` for a commit made at UTC, and `fromisoformat` does
    # not read it before Python 3.11. Rewriting that one designator is what lets
    # this run on every supported interpreter; it is done for parsing alone, so a
    # timestamp no commit carried cannot reach an artifact.
    parsable = f"{created[:-1]}+00:00" if created.endswith("Z") else created
    try:
        parsed = datetime.fromisoformat(parsable)
    except ValueError:
        msg = f"created {created!r} is not an ISO 8601 timestamp"
        raise ReleaseTaskError(msg) from None
    if parsed.utcoffset() is None:
        msg = f"created {created!r} has no UTC offset, so it names no absolute instant"
        raise ReleaseTaskError(msg)
    return ReleaseIdentity(version=version, revision=revision, created=created)


def read_release_identity(context: Context) -> ReleaseIdentity:
    """Derive the release identity from the installed distribution and the source commit.

    `created` comes from the commit, never the build clock, so two builds of one
    revision record the same creation time.
    """
    try:
        version = installed_version(DISTRIBUTION)
    except PackageNotFoundError:
        msg = f"{DISTRIBUTION} is not installed; run `uv sync --extra dev --extra prefect --extra service`"
        raise ReleaseTaskError(msg) from None
    return release_identity(
        version=version,
        revision=_git(context, "rev-parse HEAD"),
        created=_git(context, "show -s --format=%cI HEAD"),
    )


def match_declared(identity: ReleaseIdentity, declared: str) -> ReleaseIdentity:
    """Refuse a declared version that is not the one the source carries.

    Accepting it would let the caller name the artifacts rather than the source,
    which is the one thing a declared version must not be able to do.
    """
    if declared != identity.version:
        msg = f"the declared version {declared!r} is not the source's {identity.version!r}"
        raise ReleaseTaskError(msg)
    return identity


def _git(context: Context, arguments: str) -> str:
    with context.cd(ESCAPED_REPO_PATH):
        result = context.run(f"git {arguments}", hide=True, warn=True, pty=False)
    if result is None or result.exited != 0:
        msg = f"`git {arguments}` failed in {REPO_ROOT}"
        raise ReleaseTaskError(msg)
    return result.stdout.strip()


def read_recorded_identity() -> ReleaseIdentity:
    """Return the identity the validation step recorded, refusing a run without one.

    Only the three source values are read back. Every name beside them is derived
    again from the version, so a hand-edited record cannot rename an artifact.
    """
    if not RECORD_FILE.is_file():
        msg = f"{RECORD_FILE} is missing; run `uv run invoke release.identity --version <version>` first"
        raise ReleaseTaskError(msg)
    try:
        record = json.loads(RECORD_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        msg = f"{RECORD_FILE} is not JSON"
        raise ReleaseTaskError(msg) from None
    if not isinstance(record, dict):
        msg = f"{RECORD_FILE} must be a mapping"
        raise ReleaseTaskError(msg)
    values = {field: record.get(field) for field in ("version", "revision", "created")}
    if not all(isinstance(value, str) for value in values.values()):
        msg = f"{RECORD_FILE} does not record a version, a revision, and a creation time"
        raise ReleaseTaskError(msg)
    return release_identity(**values)


@task(name="identity")
def identity(context: Context, version: str = "") -> None:
    """Record the release identity the source declares, refusing a caller who renames it.

    The version is optional because the source is what declares it, and a
    candidate build has nothing of its own to declare. Passing one is how a caller
    that already carries a version — an operator, or an approval naming one — has
    the source contradict it rather than quietly go along with it.
    """
    declared = read_release_identity(context)
    if version:
        match_declared(declared, version)
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    record = declared.record()
    RECORD_FILE.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for name, value in sorted(record.items()):
        print(f" - [{NAMESPACE}] {name:<14} {value}")
    print(f" - [{NAMESPACE}] Identity recorded in {RECORD_FILE}")


@task(name="build")
def build(context: Context) -> None:
    """Build the two distributions this release publishes and refuse a name it did not derive.

    This is the whole package side of a run with publication disabled: it produces
    the artifacts a later approval would upload, and reaches nothing that could
    upload them. A build whose output is not what the recorded identity names
    fails here rather than at the upload it would otherwise be handed to.
    """
    identity = read_recorded_identity()
    rmtree(DIST_DIR, ignore_errors=True)
    with context.cd(ESCAPED_REPO_PATH):
        context.run(f"uv build --out-dir {DIST_DIR}", pty=True)
    # uv writes a `.gitignore` beside the distributions; the release is the rest.
    produced = sorted(path.name for path in DIST_DIR.iterdir() if not path.name.startswith("."))
    expected = sorted((identity.sdist, identity.wheel))
    if produced != expected:
        msg = f"the build produced {produced}, not the {identity.version} distributions {expected}"
        raise ReleaseTaskError(msg)
    for name in expected:
        print(f" - [{NAMESPACE}] Built {DIST_DIR / name}")
