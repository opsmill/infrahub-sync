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
import shlex
import tarfile
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

import yaml
from invoke import Context, task

from .release import DISTRIBUTION_FILE, ReleaseIdentity, identity_from, read_release_identity, record_gate
from .utils import ESCAPED_REPO_PATH, REPO_BASE

if TYPE_CHECKING:
    from pathlib import Path

NAMESPACE = "INFRAHUB-SYNC-IMAGE"

REPO_ROOT = REPO_BASE
DOCKERFILE = REPO_ROOT / "Dockerfile"
BUILD_DIR = REPO_ROOT / ".image"
LAYOUT_DIR = BUILD_DIR / "oci"
# Its own directory so the transfer step can be given write access to the
# archives without also being able to write into the layout it reads.
ARCHIVE_DIR = BUILD_DIR / "archives"
DIGESTS_FILE = BUILD_DIR / "digests.json"
WAIVER_FILE = REPO_ROOT / "vulnerability-waivers.yml"

DISTRIBUTION = "infrahub-sync"
LOCAL_REPOSITORY = "infrahub-sync-local"
BUILDER_NAME = "infrahub-sync-image"
PLATFORMS = ("linux/amd64", "linux/arm64")

# The digest pins the scanner executables, so which Syft produced a bill of
# materials and which Grype read it are both answerable later. It does not pin
# what Grype knows: the vulnerability database is fetched for each scan and is
# meant to be, because a gate judging today's image against a stored snapshot of
# last month's advisories would pass an image that is no longer safe. Two runs of
# one commit can therefore differ, and the later one is the one to believe.
SYFT_IMAGE = "anchore/syft:v1.33.0@sha256:f94e5d9fce1f2278491a8e3a63bd5f6ddb81fdfdbb8bf7a1637565c1d5344357"
GRYPE_IMAGE = "anchore/grype:v0.101.0@sha256:66a63cacdfeed19c7c9cbad9a841cd538b28055bb0e207013d27a12585a39063"

# Skopeo moves a built platform image out of the retained layout, so it stands
# between the one build and everything that judges its output. It is pinned by
# digest for the same reason the scanners are.
SKOPEO_IMAGE = "quay.io/skopeo/stable:v1.20.0@sha256:47853bb9fb24202af9110531ebd6e43c5f97701254ca290596640290d17942f4"

CANARY_ENV = "INFRAHUB_SYNC_IMAGE_CANARY"
SMOKE_COMMAND = "pytest -m docker tests/image"
# 2 added `index_name`: the reference annotation the exporter wrote beside the
# index descriptor. A record written under 1 carries no name, and the bundle's
# binding cannot be derived from one — which is a refusal there rather than a
# field this reader invents.
DIGESTS_SCHEMA_VERSION = 2

# The annotation an OCI exporter puts on the index descriptor it writes. It is
# the only name a layout carries: the export is a directory, not a repository,
# so nothing else in it says what the index would be loaded as.
REFERENCE_ANNOTATION = "org.opencontainers.image.ref.name"
WAIVER_SCHEMA_VERSION = 1
WAIVER_FIELDS = ("vulnerability", "owner", "reason", "expires")
BLOCKING_SEVERITIES = frozenset({"high", "critical"})
FIXED_STATE = "fixed"

# Attestation manifests are recorded against this placeholder platform. The build
# asks for none, so one appearing means the exporter added something the recorded
# digests would otherwise silently describe as an image.
_ATTESTATION_ARCHITECTURE = "unknown"


class ImageTaskError(RuntimeError):
    """Raised when an image build input or output does not meet the artifact contract."""


def build_command(
    identity: ReleaseIdentity,
    *,
    platforms: tuple[str, ...],
    destination: Path,
) -> tuple[str, ...]:
    """Return the fixed buildx argv for one OCI layout export.

    Only the three identity values are passed as build arguments. Nothing else
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
        f"VERSION={identity.version}",
        "--build-arg",
        f"REVISION={identity.revision}",
        "--build-arg",
        f"CREATED={identity.created}",
        "--output",
        f"type=oci,tar=false,dest={destination}",
        str(REPO_ROOT),
    )


def export_command(*, platform: str, archive: str, reference: str) -> tuple[str, ...]:
    """Return the fixed argv that copies one recorded platform out of the layout.

    The conversion runs in a pinned container with no network, reads the layout
    through a read-only mount, and can write only into the archive directory, so
    the step that transfers a candidate cannot alter the candidate. The platform
    is selected explicitly rather than left to the host's own architecture.
    """
    operating_system, _, architecture = platform.partition("/")
    return (
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--volume",
        f"{LAYOUT_DIR}:/layout:ro",
        "--volume",
        f"{ARCHIVE_DIR}:/archives",
        SKOPEO_IMAGE,
        "--override-os",
        operating_system,
        "--override-arch",
        architecture,
        "copy",
        "oci:/layout",
        f"docker-archive:/archives/{archive}:{reference}",
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
    """Return the index digest, its recorded name, and the per-platform digests.

    The layout is written by an external exporter, so its shape is checked rather
    than assumed: every digest below is read from a descriptor this function has
    confirmed is present and well formed.

    The reference annotation is read the same way and recorded as it was found.
    An export carrying none is still a readable layout — `image.inspect` has to
    keep working on one — so an absent name is an empty string here and a refusal
    wherever a name is actually required.
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
    annotations = entries[0].get("annotations")
    named = annotations.get(REFERENCE_ANNOTATION) if isinstance(annotations, dict) else None
    index_name = named if isinstance(named, str) else ""

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
    return {"index": index_digest, "index_name": index_name, "platforms": platforms}


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
    """Return where one platform image is exported for transfer and for the scanners."""
    return ARCHIVE_DIR / f"image-{platform_slug(platform)}.tar"


def archive_configuration(archive: Path) -> str:
    """Return the configuration digest a Docker-load archive names.

    That format carries no manifest digest, so this is what proves an export
    resolved the manifest the record holds: a manifest names exactly one
    configuration, and the configuration names every layer through its diff
    identifiers, which the copy verified on the way out.
    """
    try:
        with tarfile.open(archive) as opened:
            entry = opened.extractfile("manifest.json")
            manifest = json.loads(entry.read()) if entry is not None else None
    except (tarfile.TarError, KeyError, json.JSONDecodeError):
        msg = f"{archive} is not a readable Docker-load archive"
        raise ImageTaskError(msg) from None
    if not isinstance(manifest, list) or len(manifest) != 1:
        msg = f"{archive} must hold exactly one image"
        raise ImageTaskError(msg)
    configuration = _mapping(manifest[0], f"the {archive.name} manifest").get("Config")
    if not isinstance(configuration, str):
        msg = f"{archive} names no image configuration"
        raise ImageTaskError(msg)
    return f"sha256:{configuration.removesuffix('.json')}"


def recorded_identity(record: dict) -> ReleaseIdentity:
    """Return the release identity the build recorded beside its digests.

    Everything the scanners write is named from it, so a report downloaded on its
    own says which release and which platform it describes.
    """
    return identity_from(record.get("provenance"), str(DIGESTS_FILE))


def sbom_file(identity: ReleaseIdentity, platform: str) -> Path:
    """Return where one platform image's SPDX bill of materials is written."""
    return BUILD_DIR / f"{DISTRIBUTION_FILE}-{identity.version}-sbom-{platform_slug(platform)}.spdx.json"


def scan_file(identity: ReleaseIdentity, platform: str) -> Path:
    """Return where one platform image's vulnerability report is written."""
    return BUILD_DIR / f"{DISTRIBUTION_FILE}-{identity.version}-vulnerabilities-{platform_slug(platform)}.json"


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

    identity = read_release_identity(context)
    _ensure_builder(context)

    print(f" - [{NAMESPACE}] Building {', '.join(requested)} at revision {identity.revision}")
    # The whole build directory, not only the layout. An SBOM or scanner report
    # left behind describes the artifact of the previous build, which the digest
    # record about to be written no longer names, and `image.scan` would read it
    # as a statement about the new one.
    _remove_tree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    _run(context, build_command(identity, platforms=requested, destination=LAYOUT_DIR))

    layout = read_layout(LAYOUT_DIR)
    record = {
        "schema_version": DIGESTS_SCHEMA_VERSION,
        "provenance": {
            "version": identity.version,
            "revision": identity.revision,
            "created": identity.created,
        },
        "index_digest": layout["index"],
        # What the exporter called the index it wrote. The bundle's binding names
        # the candidate as `<name>@<index digest>`, so losing this here would
        # leave a release with no honest way to write that half.
        "index_name": layout["index_name"],
        "platforms": layout["platforms"],
        "canary_present": True,
    }
    DIGESTS_FILE.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f" - [{NAMESPACE}] OCI index {layout['index_name']}@{layout['index']}")
    for name, digests in sorted(layout["platforms"].items()):
        print(f" - [{NAMESPACE}]   {name} manifest {digests['manifest']}")
    print(f" - [{NAMESPACE}] Digests recorded in {DIGESTS_FILE}")


@task(name="inspect")
def inspect(context: Context) -> None:
    """Print the digests, labels, identity, and environment the built layout records."""
    del context
    record = read_digests()
    print(f" - [{NAMESPACE}] Index digest {record['index_digest']}")
    print(f" - [{NAMESPACE}] Index name   {record.get('index_name', '')}")
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


@task(name="freshness")
def freshness(context: Context) -> None:
    """Prove a second build on the warm builder installs the source it copied.

    Its own task rather than a case in the smoke suite: everything under that
    marker runs against an image the gate has already built and is asked not to
    build one, and this runs two builds because the builder's cache is what it
    is about.
    """
    _ensure_builder(context)
    print(f" - [{NAMESPACE}] Checking warm-builder freshness on {BUILDER_NAME}")
    with context.cd(ESCAPED_REPO_PATH):
        context.run("pytest tests/image -m builder", env={"INFRAHUB_SYNC_BUILDER": BUILDER_NAME}, pty=True)


def _requested_platforms(record: dict, platform: str) -> list[str]:
    requested = [platform] if platform else sorted(record["platforms"])
    missing = [name for name in requested if name not in record["platforms"]]
    if missing:
        msg = f"{', '.join(missing)} was not built; run `uv run invoke image.build` for it first"
        raise ImageTaskError(msg)
    return requested


def _export_platform(context: Context, record: dict, platform: str) -> Path:
    """Export one recorded platform out of the retained layout, and prove it is that image.

    Rebuilding to obtain transferable bytes would produce a second artifact and
    then let every later check describe it as the first, so the candidate is only
    ever copied. The archive is removed first because the destination format
    refuses to overwrite, and a stale one would otherwise be read as this export.
    """
    expected = record["platforms"][platform]["config"]
    archive = archive_file(platform)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive.unlink(missing_ok=True)
    print(f" - [{NAMESPACE}] Exporting {platform} from {LAYOUT_DIR}")
    _run(context, export_command(platform=platform, archive=archive.name, reference=local_reference(platform)))
    exported = archive_configuration(archive)
    if exported != expected:
        msg = f"{archive} holds {exported}, not the built {platform} image {expected}"
        raise ImageTaskError(msg)
    return archive


def _import_platform(context: Context, record: dict, platform: str) -> str:
    """Put one exported platform image in the local daemon and prove it is that image.

    The loaded image identifier is the configuration digest, so comparing it to
    the digest recorded from the OCI layout is what makes every later check —
    smoke, the lifecycle matrix — a statement about the artifact the index names.
    """
    archive = _export_platform(context, record, platform)
    reference = local_reference(platform)
    print(f" - [{NAMESPACE}] Loading {platform} as {reference}")
    _run(context, ("docker", "image", "load", "--input", str(archive)), hide=True)
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
        reference = _import_platform(context, record, name)
        print(f" - [{NAMESPACE}] Smoking {name}")
        with context.cd(ESCAPED_REPO_PATH):
            context.run(
                SMOKE_COMMAND,
                env={
                    "INFRAHUB_SYNC_IMAGE_REF": reference,
                    "INFRAHUB_SYNC_IMAGE_LAYOUT": str(LAYOUT_DIR),
                },
                pty=True,
            )
        record_gate("image-smoke", platform=name, image=record["platforms"][name]["config"], command=SMOKE_COMMAND)
    print(f" - [{NAMESPACE}] Smoked {', '.join(requested)}")


@task(name="sbom")
def sbom(context: Context, platform: str = "") -> None:
    """Write an SPDX JSON SBOM for each built platform image with the pinned Syft."""
    record = read_digests()
    identity = recorded_identity(record)
    for name in _requested_platforms(record, platform):
        archive = _export_platform(context, record, name)
        # The scanner reads the exported archive and writes nothing: its output
        # comes back on stdout and this task owns the file, so the bill of
        # materials describes the candidate's own bytes rather than a rebuild.
        document = _run(
            context,
            (
                "docker",
                "run",
                "--rm",
                "--network=none",
                "--volume",
                f"{ARCHIVE_DIR}:/work:ro",
                SYFT_IMAGE,
                f"docker-archive:/work/{archive.name}",
                "--output",
                "spdx-json",
            ),
            hide=True,
        )
        sbom_file(identity, name).write_text(document, encoding="utf-8")
        print(f" - [{NAMESPACE}] {name} SBOM written to {sbom_file(identity, name)}")


@task(name="scan")
def scan(context: Context, platform: str = "") -> None:
    """Fail on fixable high or critical vulnerabilities with the pinned Grype."""
    record = read_digests()
    identity = recorded_identity(record)
    waivers = read_waivers(today=date.today())  # noqa: DTZ011 -- a waiver expiry is a calendar date
    blocking: list[tuple[str, Finding]] = []

    for name in _requested_platforms(record, platform):
        document = sbom_file(identity, name)
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
        scan_file(identity, name).write_text(report, encoding="utf-8")
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
