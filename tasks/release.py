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

import gzip
import json
import re
import tarfile
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from shutil import copyfile, rmtree
from typing import TYPE_CHECKING, Any, cast

from invoke import Context, task
from packaging.version import InvalidVersion, Version

from .utils import ESCAPED_REPO_PATH, REPO_BASE

if TYPE_CHECKING:
    from pathlib import Path

NAMESPACE = "INFRAHUB-SYNC-RELEASE"

REPO_ROOT = REPO_BASE
RECORD_DIR = REPO_ROOT / ".release"
RECORD_FILE = RECORD_DIR / "identity.json"
DIST_DIR = RECORD_DIR / "dist"
BUNDLE_DIR = RECORD_DIR / "bundle"
QUALIFICATION_DIR = RECORD_DIR / "qualification"
QUALIFICATION_FILE = RECORD_DIR / "qualification.json"
RESULTS_DIR = RECORD_DIR / "results"
ARTIFACTS_FILE = RECORD_DIR / "artifacts.json"

# The clean-host gate's own inputs: the driver a bare host runs, the checks it
# executes inside the candidate image, the schemas those checks load, and the
# pinned destination they qualify against. All evidence. None of it ships in the
# deployment bundle, and none of it is the example content an operator follows.
QUALIFICATION_SOURCE = REPO_ROOT / "tests" / "compose" / "clean_host"
DESTINATION_SOURCE = REPO_ROOT / "development"
DESTINATION_FILES = ("docker-compose.infrahub.yml", "docker-compose.preview.yml", "preview.env")
# The example schema the gate loads unchanged, so what it qualifies against is
# what the documentation tells an operator to load.
EXAMPLE_SCHEMA = REPO_ROOT / "examples" / "prefect_remote_run" / "schemas" / "infra_device.yml"

# The deployment bundle, as the repository holds it. Which of its files ship is
# decided by what they are, not by what they are called: `configuration/
# qualification.yaml` is the declared configuration the bootstrap job registers
# and the one filesystem input `compose.yaml` binds, so it ships despite its
# name. What never ships is what a deployment generates on its host —
# `operator.env`, `secrets/`, and `.instance` — none of which Git tracks.
BUNDLE_SOURCE = REPO_ROOT / "deploy" / "compose"
BUNDLE_TREE = "deploy/compose"
CHECKSUM_SUFFIX = ".sha256"

# Everything a tar entry or a gzip stream carries beside the file's content.
# Each one is fixed because each one otherwise differs between two runs, two
# hosts, or two checkouts of one commit — and a bundle whose bytes move cannot
# be the thing a checksum in a release record names.
ARCHIVE_FORMAT = tarfile.USTAR_FORMAT
ARCHIVE_OWNER = 0
# Stated rather than left to the archive module's defaults. Reading an entry off
# the filesystem instead would carry whoever ran the build, which is the
# difference between a developer's machine and a runner.
ARCHIVE_OWNER_NAME = ""
DIRECTORY_MODE = 0o755
GZIP_LEVEL = 9

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
QUALIFICATION_SCHEMA_VERSION = 1

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

    @property
    def timestamp(self) -> int:
        """The source commit's own time, as the seconds a tar entry and a gzip header hold."""
        return int(_instant(self.created).timestamp())

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
    try:
        parsed = _instant(created)
    except ValueError:
        msg = f"created {created!r} is not an ISO 8601 timestamp"
        raise ReleaseTaskError(msg) from None
    if parsed.utcoffset() is None:
        msg = f"created {created!r} has no UTC offset, so it names no absolute instant"
        raise ReleaseTaskError(msg)
    return ReleaseIdentity(version=version, revision=revision, created=created)


def _instant(created: str) -> datetime:
    """Return the instant a commit timestamp names.

    Git writes a terminal `Z` for a commit made at UTC, and `fromisoformat` does
    not read it before Python 3.11. Rewriting that one designator is what lets
    this run on every supported interpreter; it is done for parsing alone, so a
    timestamp no commit carried cannot reach an artifact.
    """
    return datetime.fromisoformat(f"{created[:-1]}+00:00" if created.endswith("Z") else created)


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


def identity_from(recorded: object, source: str) -> ReleaseIdentity:
    """Return the identity one recorded document names, refusing a document naming none.

    Only the three source values are read back. Every name beside them is derived
    again from the version, so a hand-edited record cannot rename an artifact.
    """
    if not isinstance(recorded, dict):
        msg = f"{source} must record an identity as a mapping"
        raise ReleaseTaskError(msg)
    document = cast("dict[str, object]", recorded)
    version = document.get("version")
    revision = document.get("revision")
    created = document.get("created")
    if not (isinstance(version, str) and isinstance(revision, str) and isinstance(created, str)):
        msg = f"{source} does not record a version, a revision, and a creation time"
        raise ReleaseTaskError(msg)
    return release_identity(version=version, revision=revision, created=created)


def read_recorded_identity() -> ReleaseIdentity:
    """Return the identity the validation step recorded, refusing a run without one."""
    if not RECORD_FILE.is_file():
        msg = f"{RECORD_FILE} is missing; run `uv run invoke release.identity --version <version>` first"
        raise ReleaseTaskError(msg)
    try:
        record = json.loads(RECORD_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        msg = f"{RECORD_FILE} is not JSON"
        raise ReleaseTaskError(msg) from None
    return identity_from(record, str(RECORD_FILE))


def bundle_paths(context: Context) -> dict[str, int]:
    """Return every path the deployment bundle ships, with the mode Git records for it.

    The mode comes from the index rather than from the filesystem: a checkout's
    umask decides what the working tree shows, and the lifecycle entry point has
    to arrive executable on a host that has never seen this repository.
    """
    paths = {}
    for line in _git(context, f"ls-files --stage -- {BUNDLE_TREE}").splitlines():
        metadata, _, tracked = line.partition("\t")
        paths[tracked.removeprefix(f"{BUNDLE_TREE}/")] = int(metadata.split()[0], 8) & 0o777
    if not paths:
        msg = f"{BUNDLE_TREE} tracks no files, so there is no bundle to archive"
        raise ReleaseTaskError(msg)
    return paths


def require_archivable_bundle(context: Context) -> None:
    """Refuse to archive a bundle whose files are not the ones the commit holds.

    The archive takes its content from the working tree and its revision from
    `HEAD`. Those are the same bytes only while these paths are clean, so without
    this a record could bind a bundle checksum to a revision whose content was
    never archived — a statement that reads as a fact and is not one.
    """
    reported = _git(context, f"status --porcelain -- {BUNDLE_TREE}")
    if reported:
        differing = ", ".join(sorted(line[3:] for line in reported.splitlines()))
        msg = f"{differing} differ from HEAD; commit or restore them before building the candidate kit"
        raise ReleaseTaskError(msg)


def bundle_root(identity: ReleaseIdentity) -> str:
    """Return the one directory an extracted bundle unpacks into."""
    return f"{BUNDLE_STEM}-{identity.version}"


def bundle_members(identity: ReleaseIdentity, tracked: dict[str, int]) -> list[tuple[str, str, int]]:
    """Return every entry the archive holds, as (name in it, path in the source, mode).

    Directories are written rather than left for extraction to invent, so what a
    clean host ends up with is decided here instead of by its umask. Sorting the
    whole list is what fixes the order, and it puts each directory before what it
    holds because a name is a prefix of everything beneath it.
    """
    root = bundle_root(identity)
    directories = {root}
    for name in tracked:
        parts = name.split("/")[:-1]
        directories.update(f"{root}/{'/'.join(parts[:depth])}" for depth in range(1, len(parts) + 1))
    entries = [(name, "", DIRECTORY_MODE) for name in directories]
    entries += [(f"{root}/{name}", name, mode) for name, mode in tracked.items()]
    return sorted(entries)


def write_bundle(identity: ReleaseIdentity, tracked: dict[str, int], destination: Path) -> Path:
    """Write the deployment bundle archive, fixing everything about it except content.

    Two runs from one tree have to produce the same bytes, so every field a tar
    entry or a gzip stream carries independently of content is pinned here. The
    gzip layer is opened directly because the archive module offers no way to
    set the header's timestamp or to keep the output file's own name out of it.
    """
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / identity.bundle
    modified = identity.timestamp
    with (
        archive.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=GZIP_LEVEL, filename="", mtime=modified) as compressed,
        tarfile.open(fileobj=compressed, mode="w", format=ARCHIVE_FORMAT) as bundle,
    ):
        for name, source, mode in bundle_members(identity, tracked):
            entry = tarfile.TarInfo(name)
            entry.mtime = modified
            entry.mode = mode
            entry.uid = entry.gid = ARCHIVE_OWNER
            entry.uname = entry.gname = ARCHIVE_OWNER_NAME
            if not source:
                entry.type = tarfile.DIRTYPE
                bundle.addfile(entry)
                continue
            held = BUNDLE_SOURCE / source
            entry.size = held.stat().st_size
            with held.open("rb") as content:
                bundle.addfile(entry, content)
    return archive


def write_checksum(archive: Path) -> Path:
    """Write the archive's SHA-256 in the two-space form a clean host's tools read."""
    checksum = archive.with_name(archive.name + CHECKSUM_SUFFIX)
    checksum.write_text(f"{sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n", encoding="utf-8")
    return checksum


# The whole of what a result document says, in the order `record_gate` writes it.
# Named once because the reader checks each field and returns nothing else: the
# directory is persisted input, and a document is only usable if it says all four.
RESULT_FIELDS = ("gate", "platform", "image", "command")


def record_gate(gate: str, *, platform: str, image: str, command: str) -> Path:
    """Record that one qualification gate ran, against which bytes, and how.

    The candidate record links its test results to the artifact they ran against,
    so each gate leaves this behind. A gate that left nothing would be one the
    record could only claim had passed.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result = RESULTS_DIR / f"{gate}-{platform.replace('/', '-')}.json"
    document = {"gate": gate, "platform": platform, "image": image, "command": command}
    result.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def read_results() -> dict[tuple[str, str], dict[str, str]]:
    """Return every gate result this candidate's runs left behind, keyed by gate and platform.

    This directory is persisted input rather than something one run hands the
    next, so what a document says is settled here instead of wherever a field is
    read. A document that does not say all four things `record_gate` writes -- an
    earlier schema, an interrupted write, a file this module did not write -- is
    the module's own refusal, not a `KeyError` from whichever line reached for the
    field first. Only the four are returned, so nothing else a document carries
    reaches the record.
    """
    results = {}
    for path in sorted(RESULTS_DIR.glob("*.json")) if RESULTS_DIR.is_dir() else ():
        recorded = identity_document(path)
        described: dict[str, str] = {}
        for field in RESULT_FIELDS:
            value = recorded.get(field)
            if not isinstance(value, str) or not value:
                msg = f"{path} records no {field}, so it describes no gate that ran"
                raise ReleaseTaskError(msg)
            described[field] = value
        results[described["gate"], described["platform"]] = described
    return results


def read_artifacts(candidate: ReleaseIdentity) -> dict[str, Any]:
    """Return the identifiers the service gave this candidate's uploaded artifacts.

    An approval is bound to bytes a service still holds, so the record carries
    what names them there and how long they last. A missing or partial entry is
    a refusal: promotion has nothing to check an artifact against without it.

    The document names the candidate whose uploads it describes, and one naming
    another candidate is refused rather than reused. Nothing rewrites this file
    when a second candidate starts in the same workspace, so the identifiers the
    first one uploaded stay readable — and merging those would bind an approval
    of these bytes to bytes a service is holding under another release.
    """
    if not ARTIFACTS_FILE.is_file():
        msg = f"{ARTIFACTS_FILE} is missing; the candidate workflow writes it from what each upload returned"
        raise ReleaseTaskError(msg)
    document = identity_document(ARTIFACTS_FILE)
    uploaded_for = identity_from(document.get("identity"), f"the identity in {ARTIFACTS_FILE}")
    if uploaded_for != candidate:
        msg = (
            f"{ARTIFACTS_FILE} describes uploads of {uploaded_for.version} at {uploaded_for.revision}, "
            f"not of this candidate; upload this candidate's artifacts and record what the service returned"
        )
        raise ReleaseTaskError(msg)
    retention = document.get("retention_days")
    if not isinstance(retention, int) or retention <= 0:
        msg = f"{ARTIFACTS_FILE} must record a positive retention_days covering the approval window"
        raise ReleaseTaskError(msg)
    uploaded = document.get("artifacts")
    if not isinstance(uploaded, dict) or not uploaded:
        msg = f"{ARTIFACTS_FILE} must record every uploaded artifact"
        raise ReleaseTaskError(msg)
    for name, entry in sorted(cast("dict[str, Any]", uploaded).items()):
        described = cast("dict[str, Any]", entry) if isinstance(entry, dict) else {}
        if not described.get("id") or not described.get("digest"):
            msg = f"{ARTIFACTS_FILE} records {name} without both an identifier and a digest"
            raise ReleaseTaskError(msg)
    return {"retention_days": retention, "artifacts": uploaded}


def identity_document(path: Path) -> dict[str, Any]:
    """Return one JSON mapping this repository wrote, refusing anything else."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        msg = f"{path} is not JSON"
        raise ReleaseTaskError(msg) from None
    if not isinstance(document, dict):
        msg = f"{path} must be a mapping"
        raise ReleaseTaskError(msg)
    return cast("dict[str, Any]", document)


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


def build_qualification_kit() -> None:
    """Assemble what a bare host needs to run the clean-host gate.

    The driver and its checks, the schemas the checks load, and the pinned
    destination they qualify against. The example schema is copied beside the
    checks so the gate loads the same file the documentation names.
    """
    rmtree(QUALIFICATION_DIR, ignore_errors=True)
    checks = QUALIFICATION_DIR / "checks"
    checks.mkdir(parents=True)
    copyfile(QUALIFICATION_SOURCE / "clean-host.sh", QUALIFICATION_DIR / "clean-host.sh")
    (QUALIFICATION_DIR / "clean-host.sh").chmod(0o755)
    for module in sorted((QUALIFICATION_SOURCE / "checks").glob("*.py")):
        copyfile(module, checks / module.name)
    # Both suffixes: the kit carries destination schemas and a declared
    # configuration package, and the two conventions differ in this repository.
    for declared in sorted((QUALIFICATION_SOURCE / "destination").iterdir()):
        if declared.suffix in {".yml", ".yaml"}:
            copyfile(declared, checks / declared.name)
    copyfile(EXAMPLE_SCHEMA, checks / EXAMPLE_SCHEMA.name)
    destination = QUALIFICATION_DIR / "destination"
    destination.mkdir()
    for name in DESTINATION_FILES:
        copyfile(DESTINATION_SOURCE / name, destination / name)


@task(name="kit")
def kit(context: Context) -> None:
    """Produce the deterministic deployment bundle and the separate qualification kit.

    Two archives with different audiences. The bundle is what an operator
    deploys; the qualification kit holds what the clean-host gate needs and an
    operator does not.
    """
    identity = read_recorded_identity()
    require_archivable_bundle(context)
    archive = write_bundle(identity, bundle_paths(context), BUNDLE_DIR)
    checksum = write_checksum(archive)
    build_qualification_kit()
    print(f" - [{NAMESPACE}] Bundle    {archive}")
    print(f" - [{NAMESPACE}] Checksum  {checksum.read_text(encoding='utf-8').strip()}")
    print(f" - [{NAMESPACE}] Qualification kit in {QUALIFICATION_DIR}")


@task(name="qualify")
def qualify(context: Context) -> None:
    """Record what this candidate is, what it is made of, and what passed against it.

    Imported here rather than at the top because the image tasks read this
    module's identity: the record is what ties the two together, so it is the one
    place that has to see both.
    """
    del context
    from datetime import date  # noqa: PLC0415 -- see the docstring

    from .image import (  # noqa: PLC0415 -- see the docstring
        blocking_findings,
        read_digests,
        read_waivers,
        recorded_identity,
        sbom_file,
        scan_file,
    )

    digests = read_digests()
    identity = recorded_identity(digests)
    if identity != read_recorded_identity():
        msg = "the built image and the recorded identity name different releases; rebuild the candidate"
        raise ReleaseTaskError(msg)

    archive = BUNDLE_DIR / identity.bundle
    if not archive.is_file():
        msg = f"{archive} is missing; run `uv run invoke release.kit` first"
        raise ReleaseTaskError(msg)

    waivers = read_waivers(today=date.today())  # noqa: DTZ011 -- a waiver expiry is a calendar date
    platforms = sorted(digests["platforms"])
    # A gate result names the configuration digest it ran against, so a result
    # left by an earlier candidate satisfies nothing here: the record has to link
    # what passed to the bytes this candidate is made of.
    configurations = {name: digests["platforms"][name]["config"] for name in platforms}
    results = read_results()
    missing = [
        ("image-smoke", name)
        for name in platforms
        if results.get(("image-smoke", name), {}).get("image") != configurations[name]
    ]
    qualified = set(configurations.values())
    if not any(gate == "compose-lifecycle" and result["image"] in qualified for (gate, _), result in results.items()):
        missing.append(("compose-lifecycle", "the qualified platform"))
    if missing:
        listed = ", ".join(f"{gate} on {platform}" for gate, platform in missing)
        msg = f"{listed} left no result naming this candidate's own bytes, so it qualified nothing"
        raise ReleaseTaskError(msg)

    scanned = {}
    for name in platforms:
        report = scan_file(identity, name)
        if not report.is_file():
            msg = f"{report} is missing; run `uv run invoke image.scan` first"
            raise ReleaseTaskError(msg)
        findings = blocking_findings(json.loads(report.read_text(encoding="utf-8")), waivers=waivers)
        scanned[name] = {"report": report.name, "sha256": _digest(report), "blocking": len(findings)}

    record = {
        "schema_version": QUALIFICATION_SCHEMA_VERSION,
        "identity": identity.record(),
        "image": {"index": digests["index_digest"], "platforms": digests["platforms"]},
        "bundle": {"name": archive.name, "sha256": _digest(archive)},
        "sboms": {
            name: {"document": sbom_file(identity, name).name, "sha256": _digest(sbom_file(identity, name))}
            for name in platforms
        },
        "scan": {"waivers_in_force": len(waivers), "platforms": scanned},
        # The refusal above reads only the results a required gate leaves. This is
        # every result in the directory, so one on a key nothing required reads --
        # a platform this candidate did not build, a second lifecycle run -- is
        # dropped rather than recorded as a gate that faced these bytes. By gate
        # and platform, so a record does not depend on directory order.
        "tests": [result for _, result in sorted(results.items()) if result["image"] in qualified],
        **read_artifacts(identity),
    }
    QUALIFICATION_FILE.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f" - [{NAMESPACE}] Qualification record written to {QUALIFICATION_FILE}")


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()
