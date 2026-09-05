"""Build, inspect, and smoke the Infrahub Sync container image locally.

The build writes an OCI layout rather than a tagged image, because the layout is
what later units consume: it carries the index digest and one manifest digest per
platform, and those digests — not a tag — are how a candidate is identified,
scanned, and eventually promoted without being rebuilt.

Nothing here logs in, pushes, tags for a registry, or promotes anything.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass
from datetime import date, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from typing import TYPE_CHECKING, Any

import yaml
from invoke import Context, task

from .utils import ESCAPED_REPO_PATH, REPO_BASE

if TYPE_CHECKING:
    from pathlib import Path

NAMESPACE = "INFRAHUB-SYNC-IMAGE"

REPO_ROOT = REPO_BASE
DOCKERFILE = REPO_ROOT / "Dockerfile"
BUILD_DIR = REPO_ROOT / ".image"
LAYOUT_DIR = BUILD_DIR / "oci"
DIGESTS_FILE = BUILD_DIR / "digests.json"
WAIVER_FILE = REPO_ROOT / "vulnerability-waivers.yml"

DISTRIBUTION = "infrahub-sync"
LOCAL_REPOSITORY = "infrahub-sync-local"
BUILDER_NAME = "infrahub-sync-image"
PLATFORMS = ("linux/amd64", "linux/arm64")

# Both scanners are pinned by digest for the same reason the image bases are: a
# scan that a re-pointed tag can change is not a gate anybody can reproduce.
SYFT_IMAGE = "anchore/syft:v1.33.0@sha256:f94e5d9fce1f2278491a8e3a63bd5f6ddb81fdfdbb8bf7a1637565c1d5344357"
GRYPE_IMAGE = "anchore/grype:v0.101.0@sha256:66a63cacdfeed19c7c9cbad9a841cd538b28055bb0e207013d27a12585a39063"

CANARY_ENV = "INFRAHUB_SYNC_IMAGE_CANARY"
DIGESTS_SCHEMA_VERSION = 1
WAIVER_SCHEMA_VERSION = 1
WAIVER_FIELDS = ("vulnerability", "owner", "reason", "expires")
BLOCKING_SEVERITIES = frozenset({"high", "critical"})
FIXED_STATE = "fixed"

_REVISION = re.compile(r"[0-9a-f]{40}")
_VERSION = re.compile(r"[0-9][0-9A-Za-z.!+-]*")
# Attestation manifests are recorded against this placeholder platform. The build
# asks for none, so one appearing means the exporter added something the recorded
# digests would otherwise silently describe as an image.
_ATTESTATION_ARCHITECTURE = "unknown"


class ImageTaskError(RuntimeError):
    """Raised when an image build input or output does not meet the artifact contract."""


@dataclass(frozen=True)
class SourceProvenance:
    """The three provenance values a build is allowed to take from its source."""

    version: str
    revision: str
    created: str


def source_provenance(*, version: str, revision: str, created: str) -> SourceProvenance:
    """Validate the release identity a build may record, and refuse anything else.

    These three values are the only build inputs that reach image metadata, so
    they are checked here rather than trusted: an abbreviated revision or a local
    timestamp would leave an image nobody can trace back to one commit.
    """
    if not _VERSION.fullmatch(version):
        msg = f"version {version!r} is not a release identifier"
        raise ImageTaskError(msg)
    if not _REVISION.fullmatch(revision):
        msg = f"revision {revision!r} is not a full commit identifier"
        raise ImageTaskError(msg)
    # Git writes a terminal `Z` for a commit made at UTC, and `fromisoformat` does
    # not read it before Python 3.11. Rewriting that one designator is what lets
    # this run on every supported interpreter; it is done for parsing alone, so a
    # timestamp no commit carried cannot reach image metadata.
    parsable = f"{created[:-1]}+00:00" if created.endswith("Z") else created
    try:
        parsed = datetime.fromisoformat(parsable)
    except ValueError:
        msg = f"created {created!r} is not an ISO 8601 timestamp"
        raise ImageTaskError(msg) from None
    if parsed.utcoffset() is None:
        msg = f"created {created!r} has no UTC offset, so it names no absolute instant"
        raise ImageTaskError(msg)
    return SourceProvenance(version=version, revision=revision, created=created)


def read_source_provenance(context: Context) -> SourceProvenance:
    """Derive the release identity from the installed distribution and the source commit.

    `created` comes from the commit, never the build clock, so two builds of one
    revision record the same creation time.
    """
    try:
        version = installed_version(DISTRIBUTION)
    except PackageNotFoundError:
        msg = f"{DISTRIBUTION} is not installed; run `uv sync --extra dev --extra prefect --extra service`"
        raise ImageTaskError(msg) from None
    return source_provenance(
        version=version,
        revision=_git(context, "rev-parse HEAD"),
        created=_git(context, "show -s --format=%cI HEAD"),
    )


def build_command(
    provenance: SourceProvenance,
    *,
    platforms: tuple[str, ...],
    destination: Path,
) -> tuple[str, ...]:
    """Return the fixed buildx argv for one OCI layout export.

    Only the three provenance values are passed as build arguments. Nothing else
    from the caller's environment or command line reaches the image, so image
    history cannot become a place a secret is accidentally recorded.
    """
    return (
        "docker",
        "buildx",
        "build",
        "--builder",
        BUILDER_NAME,
        "--file",
        str(DOCKERFILE),
        "--platform",
        ",".join(platforms),
        "--provenance=false",
        "--sbom=false",
        "--build-arg",
        f"VERSION={provenance.version}",
        "--build-arg",
        f"REVISION={provenance.revision}",
        "--build-arg",
        f"CREATED={provenance.created}",
        "--output",
        f"type=oci,tar=false,dest={destination}",
        str(REPO_ROOT),
    )


def load_command(provenance: SourceProvenance, *, platform: str, reference: str) -> tuple[str, ...]:
    """Return the fixed buildx argv that puts one platform image in the local daemon."""
    return (
        "docker",
        "buildx",
        "build",
        "--builder",
        BUILDER_NAME,
        "--file",
        str(DOCKERFILE),
        "--platform",
        platform,
        "--provenance=false",
        "--sbom=false",
        "--build-arg",
        f"VERSION={provenance.version}",
        "--build-arg",
        f"REVISION={provenance.revision}",
        "--build-arg",
        f"CREATED={provenance.created}",
        "--tag",
        reference,
        "--load",
        str(REPO_ROOT),
    )


def local_reference(platform: str) -> str:
    """Return the local-only tag one platform image is loaded under."""
    return f"{LOCAL_REPOSITORY}:{platform.replace('/', '-')}"


def read_blob(layout: Path, digest: str) -> dict:
    """Return one JSON blob from an OCI layout, refusing a descriptor it cannot resolve."""
    algorithm, _, encoded = digest.partition(":")
    blob = layout / "blobs" / algorithm / encoded
    if not blob.is_file():
        msg = f"{layout} does not hold the blob {digest}"
        raise ImageTaskError(msg)
    try:
        return json.loads(blob.read_bytes())
    except json.JSONDecodeError:
        msg = f"blob {digest} in {layout} is not JSON"
        raise ImageTaskError(msg) from None


def read_layout(layout: Path) -> dict:
    """Return the index digest and the per-platform digests an OCI layout records.

    The layout is written by an external exporter, so its shape is checked rather
    than assumed: every digest below is read from a descriptor this function has
    confirmed is present and well formed.
    """
    index_file = layout / "index.json"
    if not index_file.is_file():
        msg = f"{layout} is not an OCI layout; run `invoke image.build` first"
        raise ImageTaskError(msg)
    try:
        entries = json.loads(index_file.read_bytes()).get("manifests")
    except json.JSONDecodeError:
        msg = f"{index_file} is not JSON"
        raise ImageTaskError(msg) from None
    if not isinstance(entries, list) or len(entries) != 1:
        msg = f"{index_file} must reference exactly one image index"
        raise ImageTaskError(msg)
    index_digest = entries[0].get("digest")
    if not isinstance(index_digest, str):
        msg = f"{index_file} records no index digest"
        raise ImageTaskError(msg)

    # A multi-platform export names an index that lists one manifest per platform,
    # and a single-platform export names that one manifest directly. Both are
    # valid layouts, and the platform is read from the descriptor when there is
    # one and from the image configuration when there is not.
    root = read_blob(layout, index_digest)
    described = root.get("manifests")
    descriptors = (
        [(entry.get("platform"), entry.get("digest")) for entry in described]
        if isinstance(described, list)
        else [(None, index_digest)]
    )

    platforms: dict[str, dict[str, str]] = {}
    for platform, manifest_digest in descriptors:
        if not isinstance(manifest_digest, str):
            msg = f"{layout} holds a manifest descriptor without a digest"
            raise ImageTaskError(msg)
        config_digest = read_blob(layout, manifest_digest).get("config", {}).get("digest")
        if not isinstance(config_digest, str):
            msg = f"{layout} records no configuration digest for manifest {manifest_digest}"
            raise ImageTaskError(msg)
        described_platform = platform if isinstance(platform, dict) else read_blob(layout, config_digest)
        architecture = described_platform.get("architecture")
        if architecture == _ATTESTATION_ARCHITECTURE:
            msg = f"{layout} holds an attestation manifest; the build asked for none"
            raise ImageTaskError(msg)
        name = f"{described_platform.get('os')}/{architecture}"
        platforms[name] = {"manifest": manifest_digest, "config": config_digest}
    if not platforms:
        msg = f"{layout} holds no platform images"
        raise ImageTaskError(msg)
    return {"index": index_digest, "platforms": platforms}


def read_digests() -> dict:
    """Return the digest record the last build wrote."""
    if not DIGESTS_FILE.is_file():
        msg = f"{DIGESTS_FILE} is missing; run `uv run invoke image.build` first"
        raise ImageTaskError(msg)
    return json.loads(DIGESTS_FILE.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Waiver:
    """One approved exception to the vulnerability policy."""

    vulnerability: str
    owner: str
    reason: str
    expires: date


@dataclass(frozen=True)
class Finding:
    """One scanner match the policy refuses to ship."""

    vulnerability: str
    severity: str
    package: str
    fixed_in: str


def _mapping(value: object, description: str) -> dict[Any, Any]:
    """Return one mapping read from outside this module, refusing anything else.

    Every external document below — the waiver file and the scanner report —
    passes through here, so a shape this code cannot read becomes a refusal at
    the boundary rather than an attribute error somewhere further in.
    """
    if not isinstance(value, dict):
        msg = f"{description} must be a mapping; found {type(value).__name__}"
        raise ImageTaskError(msg)
    return value


def parse_waivers(document: object, *, today: date) -> tuple[Waiver, ...]:
    """Validate the waiver file and return the exceptions still in force.

    A waiver is a decision to ship a known fixable vulnerability, so every entry
    has to name who owns it, why, and when the decision lapses. An entry past its
    expiry is an error rather than a silent no-op: the point of an expiry is that
    somebody has to look again.
    """
    waiver_document = _mapping(document, str(WAIVER_FILE))
    if waiver_document.get("schema_version") != WAIVER_SCHEMA_VERSION:
        msg = f"{WAIVER_FILE} must declare schema_version {WAIVER_SCHEMA_VERSION}"
        raise ImageTaskError(msg)
    entries = waiver_document.get("waivers")
    if not isinstance(entries, list):
        msg = f"{WAIVER_FILE} must contain a waivers list"
        raise ImageTaskError(msg)

    waivers = []
    for candidate in entries:
        if not isinstance(candidate, dict) or set(candidate) != set(WAIVER_FIELDS):
            msg = f"each waiver must define exactly {list(WAIVER_FIELDS)}; found {candidate!r}"
            raise ImageTaskError(msg)
        entry = _mapping(candidate, "a waiver")
        values = {field: entry[field] for field in WAIVER_FIELDS[:3]}
        for field, value in values.items():
            if not isinstance(value, str) or not value.strip():
                msg = f"waiver {field} must be a non-empty string; found {value!r}"
                raise ImageTaskError(msg)
        expires = entry["expires"]
        if isinstance(expires, str):
            try:
                expires = date.fromisoformat(expires)
            except ValueError:
                msg = f"waiver expires must be an ISO 8601 date; found {expires!r}"
                raise ImageTaskError(msg) from None
        if not isinstance(expires, date):
            msg = f"waiver expires must be an ISO 8601 date; found {expires!r}"
            raise ImageTaskError(msg)
        if expires < today:
            msg = f"waiver for {values['vulnerability']} expired on {expires.isoformat()}; renew or remove it"
            raise ImageTaskError(msg)
        waivers.append(Waiver(**values, expires=expires))
    return tuple(waivers)


def read_waivers(*, today: date) -> tuple[Waiver, ...]:
    """Return the waivers this repository ships."""
    if not WAIVER_FILE.is_file():
        msg = f"{WAIVER_FILE} is missing"
        raise ImageTaskError(msg)
    return parse_waivers(yaml.safe_load(WAIVER_FILE.read_text(encoding="utf-8")), today=today)


def blocking_findings(report: object, *, waivers: tuple[Waiver, ...] = ()) -> tuple[Finding, ...]:
    """Return the scanner matches that fail the gate.

    The accepted policy is narrow on purpose: a high or critical finding blocks
    only when the scanner knows of a fix, because a finding nobody can act on
    would turn the gate into something teams route around. The scanner report is
    external input, so a match this cannot read is an error, never a pass.
    """
    matches = _mapping(report, "the vulnerability report").get("matches")
    if not isinstance(matches, list):
        msg = "the vulnerability report must contain a matches list"
        raise ImageTaskError(msg)
    waived = {waiver.vulnerability for waiver in waivers}

    findings = []
    for candidate in matches:
        match = _mapping(candidate, "a vulnerability report match")
        vulnerability = match.get("vulnerability")
        if not isinstance(vulnerability, dict):
            msg = f"the vulnerability report holds a match without a vulnerability: {candidate!r}"
            raise ImageTaskError(msg)
        details = _mapping(vulnerability, "a reported vulnerability")
        identifier = details.get("id")
        severity = details.get("severity")
        fix = details.get("fix")
        if not isinstance(identifier, str) or not isinstance(severity, str) or not isinstance(fix, dict):
            msg = f"the vulnerability report holds an unreadable match: {vulnerability!r}"
            raise ImageTaskError(msg)
        remedy = _mapping(fix, "a reported fix")
        state = remedy.get("state")
        if not isinstance(state, str):
            msg = f"the vulnerability report holds {identifier} without a fix state"
            raise ImageTaskError(msg)
        if severity.lower() not in BLOCKING_SEVERITIES or state != FIXED_STATE or identifier in waived:
            continue
        artifact = match.get("artifact")
        named = artifact if isinstance(artifact, dict) else {}
        findings.append(
            Finding(
                vulnerability=identifier,
                severity=severity,
                package=f"{named.get('name')}@{named.get('version')}",
                fixed_in=", ".join(str(version) for version in remedy.get("versions") or []),
            )
        )
    return tuple(findings)


def platform_slug(platform: str) -> str:
    """Return the filename-safe form of a platform name."""
    return platform.replace("/", "-")


def archive_file(platform: str) -> Path:
    return BUILD_DIR / f"image-{platform_slug(platform)}.tar"


def sbom_file(platform: str) -> Path:
    return BUILD_DIR / f"sbom-{platform_slug(platform)}.spdx.json"


def scan_file(platform: str) -> Path:
    return BUILD_DIR / f"vulnerabilities-{platform_slug(platform)}.json"


def _git(context: Context, arguments: str) -> str:
    with context.cd(ESCAPED_REPO_PATH):
        result = context.run(f"git {arguments}", hide=True, warn=True, pty=False)
    if result is None or result.exited != 0:
        msg = f"`git {arguments}` failed in {REPO_ROOT}"
        raise ImageTaskError(msg)
    return result.stdout.strip()


def _run(context: Context, argv: tuple[str, ...], *, hide: bool = False) -> str:
    """Run one fixed argv, quoting every word so no value can become shell syntax."""
    result = context.run(" ".join(shlex.quote(word) for word in argv), hide=hide, pty=False)
    return (result.stdout or "") if result is not None else ""


def _ensure_builder(context: Context) -> None:
    """Create the container builder once; the default driver exports no OCI layout."""
    probe = context.run(f"docker buildx inspect {shlex.quote(BUILDER_NAME)}", hide=True, warn=True, pty=False)
    if probe is not None and probe.exited == 0:
        return
    print(f" - [{NAMESPACE}] Creating the {BUILDER_NAME} buildx builder")
    _run(
        context,
        ("docker", "buildx", "create", "--name", BUILDER_NAME, "--driver", "docker-container", "--bootstrap"),
    )


@task(name="build")
def build(context: Context, platforms: str = ",".join(PLATFORMS)) -> None:
    """Build the image for every requested platform into a local OCI layout."""
    requested = tuple(entry.strip() for entry in platforms.split(",") if entry.strip())
    if not requested:
        msg = "at least one platform is required"
        raise ImageTaskError(msg)

    # The build runs with a secret canary in its environment and passes it to
    # nothing. That is the only way the containment check afterwards can mean
    # anything: an environment holding no secret proves nothing about an image.
    if not os.environ.get(CANARY_ENV, "").strip():
        msg = f"{CANARY_ENV} must hold a throwaway secret value so the build proves it leaks none"
        raise ImageTaskError(msg)

    provenance = read_source_provenance(context)
    _ensure_builder(context)

    print(f" - [{NAMESPACE}] Building {', '.join(requested)} at revision {provenance.revision}")
    # The whole build directory, not only the layout. An SBOM or scanner report
    # left behind describes the artifact of the previous build, which the digest
    # record about to be written no longer names, and `image.scan` would read it
    # as a statement about the new one.
    _remove_tree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    _run(context, build_command(provenance, platforms=requested, destination=LAYOUT_DIR))

    layout = read_layout(LAYOUT_DIR)
    record = {
        "schema_version": DIGESTS_SCHEMA_VERSION,
        "provenance": {
            "version": provenance.version,
            "revision": provenance.revision,
            "created": provenance.created,
        },
        "index_digest": layout["index"],
        "platforms": layout["platforms"],
        "canary_present": True,
    }
    DIGESTS_FILE.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f" - [{NAMESPACE}] OCI index {layout['index']}")
    for name, digests in sorted(layout["platforms"].items()):
        print(f" - [{NAMESPACE}]   {name} manifest {digests['manifest']}")
    print(f" - [{NAMESPACE}] Digests recorded in {DIGESTS_FILE}")


@task(name="inspect")
def inspect(context: Context) -> None:
    """Print the digests, labels, identity, and environment the built layout records."""
    del context
    record = read_digests()
    print(f" - [{NAMESPACE}] Index digest {record['index_digest']}")
    for name, digests in sorted(record["platforms"].items()):
        configuration = read_blob(LAYOUT_DIR, digests["config"]).get("config", {})
        print(f" - [{NAMESPACE}] {name}")
        print(f" - [{NAMESPACE}]   manifest {digests['manifest']}")
        print(f" - [{NAMESPACE}]   config   {digests['config']}")
        print(f" - [{NAMESPACE}]   user     {configuration.get('User')}")
        print(f" - [{NAMESPACE}]   command  {configuration.get('Cmd')}")
        for key, value in sorted((configuration.get("Labels") or {}).items()):
            print(f" - [{NAMESPACE}]   label    {key}={value}")
        for value in sorted(configuration.get("Env") or []):
            print(f" - [{NAMESPACE}]   env      {value}")


def _requested_platforms(record: dict, platform: str) -> list[str]:
    requested = [platform] if platform else sorted(record["platforms"])
    missing = [name for name in requested if name not in record["platforms"]]
    if missing:
        msg = f"{', '.join(missing)} was not built; run `uv run invoke image.build` for it first"
        raise ImageTaskError(msg)
    return requested


def _load_platform(context: Context, record: dict, platform: str) -> str:
    """Put one built platform image in the local daemon and prove it is that image.

    The loaded image identifier is the configuration digest, so comparing it to
    the digest recorded from the OCI layout is what makes every later check —
    smoke, SBOM, scan — a statement about the artifact the index names.
    """
    provenance = source_provenance(**record["provenance"])
    reference = local_reference(platform)
    print(f" - [{NAMESPACE}] Loading {platform} as {reference}")
    _run(context, load_command(provenance, platform=platform, reference=reference))
    loaded = _run(context, ("docker", "image", "inspect", "--format", "{{.Id}}", reference), hide=True).strip()
    expected = record["platforms"][platform]["config"]
    if loaded != expected:
        msg = f"{reference} is {loaded}, not the built {platform} image {expected}"
        raise ImageTaskError(msg)
    return reference


@task(name="smoke")
def smoke(context: Context, platform: str = "") -> None:
    """Run the Docker-backed image suite against every built platform image."""
    record = read_digests()
    if not os.environ.get(CANARY_ENV, "").strip():
        msg = f"{CANARY_ENV} must hold the same value the build ran with, so containment can be checked"
        raise ImageTaskError(msg)

    requested = _requested_platforms(record, platform)
    for name in requested:
        reference = _load_platform(context, record, name)
        print(f" - [{NAMESPACE}] Smoking {name}")
        with context.cd(ESCAPED_REPO_PATH):
            context.run(
                "pytest -m docker tests/image",
                env={
                    "INFRAHUB_SYNC_IMAGE_REF": reference,
                    "INFRAHUB_SYNC_IMAGE_LAYOUT": str(LAYOUT_DIR),
                },
                pty=True,
            )
    print(f" - [{NAMESPACE}] Smoked {', '.join(requested)}")


@task(name="sbom")
def sbom(context: Context, platform: str = "") -> None:
    """Write an SPDX JSON SBOM for each built platform image with the pinned Syft."""
    record = read_digests()
    for name in _requested_platforms(record, platform):
        reference = _load_platform(context, record, name)
        archive = archive_file(name)
        _run(context, ("docker", "image", "save", "--output", str(archive), reference))
        # The scanner reads the archive and writes nothing: its output comes back
        # on stdout and this task owns the file, so no container writes into the
        # build directory as root.
        document = _run(
            context,
            (
                "docker",
                "run",
                "--rm",
                "--network=none",
                "--volume",
                f"{BUILD_DIR}:/work:ro",
                SYFT_IMAGE,
                f"docker-archive:/work/{archive.name}",
                "--output",
                "spdx-json",
            ),
            hide=True,
        )
        sbom_file(name).write_text(document, encoding="utf-8")
        print(f" - [{NAMESPACE}] {name} SBOM written to {sbom_file(name)}")


@task(name="scan")
def scan(context: Context, platform: str = "") -> None:
    """Fail on fixable high or critical vulnerabilities with the pinned Grype."""
    record = read_digests()
    waivers = read_waivers(today=date.today())  # noqa: DTZ011 -- a waiver expiry is a calendar date
    blocking: list[tuple[str, Finding]] = []

    for name in _requested_platforms(record, platform):
        document = sbom_file(name)
        if not document.is_file():
            msg = f"{document} is missing; run `uv run invoke image.sbom` first"
            raise ImageTaskError(msg)
        report = _run(
            context,
            (
                "docker",
                "run",
                "--rm",
                "--volume",
                f"{BUILD_DIR}:/work:ro",
                GRYPE_IMAGE,
                f"sbom:/work/{document.name}",
                "--output",
                "json",
            ),
            hide=True,
        )
        scan_file(name).write_text(report, encoding="utf-8")
        findings = blocking_findings(json.loads(report), waivers=waivers)
        blocking.extend((name, finding) for finding in findings)
        print(f" - [{NAMESPACE}] {name}: {len(findings)} fixable high or critical findings")

    if blocking:
        for name, finding in blocking:
            print(
                f" - [{NAMESPACE}] {name} {finding.severity} {finding.vulnerability} "
                f"in {finding.package}, fixed in {finding.fixed_in}"
            )
        msg = (
            f"{len(blocking)} fixable high or critical vulnerabilities block this image. "
            f"Update the owned dependency or base, or take an approved waiver to {WAIVER_FILE}."
        )
        raise ImageTaskError(msg)
    print(f" - [{NAMESPACE}] Vulnerability policy passed with {len(waivers)} waivers in force")


@task(name="clean")
def clean(context: Context) -> None:
    """Remove the local build outputs, the loaded images, and the builder."""
    for name in PLATFORMS:
        context.run(
            f"docker image rm --force {shlex.quote(local_reference(name))}",
            hide=True,
            warn=True,
            pty=False,
        )
    context.run(f"docker buildx rm {shlex.quote(BUILDER_NAME)}", hide=True, warn=True, pty=False)
    # The build directory holds the saved image archives and the scanner reports;
    # removing the whole tree is what keeps a canary out of retained output.
    _remove_tree(BUILD_DIR)
    print(f" - [{NAMESPACE}] Removed {BUILD_DIR}, the loaded images, and the {BUILDER_NAME} builder")


def _remove_tree(path: Path) -> None:
    from shutil import rmtree  # noqa: PLC0415 -- keep Invoke task imports lightweight

    rmtree(path, ignore_errors=True)
