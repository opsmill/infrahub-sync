"""What the deployment bundle archive holds, and what makes its bytes reproducible.

A checksum in a release record names one archive. That is only worth recording
while two runs from one tree produce the same bytes, so each field a tar entry or
a gzip stream carries independently of content is asserted on its own here rather
than left to a comparison of two runs — which would agree just as happily if
every one of those fields were floating together.

Which files ship is decided by what they are, not by what they are called.
`configuration/qualification.yaml` is the declared configuration the bootstrap
job registers and the one filesystem input `compose.yaml` binds, so it ships
despite reading as a test artifact. `OPERATING.md` ships because a host with only
the archive still has to be told what to do with it. What never ships is what a
deployment generates on its host: the operator environment, the mounted
credential, and the instance identity.
"""

from __future__ import annotations

import gzip
import json
import re
import shutil
import subprocess  # noqa: S404 -- this test runs the shipped guide's own shell snippet
import tarfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml
from invoke import Context

from tasks import image, release

VERSION = "3.0.0a1"
COMMIT = "708a8fca4b3fe300ae33242ddcd791a181926eeb"
COMMIT_TIME = "2026-09-04T10:15:00+02:00"
# The instant that timestamp names — 08:15 UTC — which every entry and the gzip
# header carry. Written out rather than computed, so a change to how the module
# reads a commit time fails here instead of agreeing with itself.
COMMIT_SECONDS = 1788509700

ROOT = f"infrahub-sync-compose-{VERSION}"

# The real bundle directory, held before the `source` fixture points the module
# at a copy. The content comparison below is against what the repository ships.
BUNDLE_SOURCE = release.BUNDLE_SOURCE

# Everything the bundle ships *from the repository*, as an equality: a file newly
# reaching it fails here rather than shipping unnoticed. The binding member below
# is not one of these — nothing in the source tree holds it.
SHIPPED = {
    "compose.yaml",
    "infrahub-sync-compose",
    "defaults.conf",
    "configuration/qualification.yaml",
    "bootstrap/databases.sh",
    # The operator's own copy of the procedure. A host that has the archive and
    # nothing else has to be able to read how to deploy it.
    "OPERATING.md",
    "skills/README.md",
    "skills/infrahub-sync-configuration/SKILL.md",
    "skills/infrahub-sync-deployment/SKILL.md",
}
SKILL_NAMES = ("infrahub-sync-configuration", "infrahub-sync-deployment")
SKILL_ENTRYPOINTS = {name: f"skills/{name}/SKILL.md" for name in SKILL_NAMES}
PINNED_DOC_REVISION = "38399ee12280c412755b96356316b3b529900395"
PINNED_DOC_PATH = f"/opsmill/infrahub-sync/blob/{PINNED_DOC_REVISION}/"
MARKDOWN_LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")
# What a deployment writes beside them on its own host. None is the repository's
# to ship, and one of them is a credential.
GENERATED = ("operator.env", ".instance", "secrets/postgres-admin-password")

# The one member the release generates rather than copies. It ships, which is
# the opposite of `GENERATED` above: those are an operator host's own files and
# never leave it, and this one is the package naming the image it was built for.
BINDING = release.BINDING_MEMBER

# The digest record one candidate build leaves, as the binding is derived from it.
INDEX_NAME = "latest"
INDEX_DIGEST = "sha256:" + "1" * 64
AMD64_CONFIG = "sha256:" + "2" * 64
ARM64_CONFIG = "sha256:" + "3" * 64

# The identity a containerd image store gives the exported amd64 archive once it
# is loaded. It is derived from that archive's own bytes rather than from the
# digest record, so it reaches the writer as an argument.
LOADED_MANIFEST = "sha256:" + "6" * 64


def digests(**overrides: object) -> dict[str, object]:
    """The digest record `image.build` writes for one two-platform candidate."""
    record: dict[str, object] = {
        "schema_version": image.DIGESTS_SCHEMA_VERSION,
        "provenance": {"version": VERSION, "revision": COMMIT, "created": COMMIT_TIME},
        "index_digest": INDEX_DIGEST,
        "index_name": INDEX_NAME,
        "platforms": {
            "linux/amd64": {"manifest": "sha256:" + "4" * 64, "config": AMD64_CONFIG},
            "linux/arm64": {"manifest": "sha256:" + "5" * 64, "config": ARM64_CONFIG},
        },
    }
    record.update(overrides)
    return record


# The lifecycle entry point is the only file a host executes.
EXECUTABLE = "infrahub-sync-compose"

# The gzip header's flag bit that says an original filename follows it, and the
# extra-flags byte that records which end of the speed/size range was asked for.
GZIP_NAME_FLAG = 0x08
GZIP_BEST_COMPRESSION = 2

# What a USTAR header writes at offset 257. A different tar format moves every
# field after it, so the format is part of what a recorded checksum names.
USTAR_MAGIC = b"ustar\x0000"


@pytest.fixture(scope="module")
def identity() -> release.ReleaseIdentity:
    """The one release every archive in this module is built for and named after."""
    return release.release_identity(version=VERSION, revision=COMMIT, created=COMMIT_TIME)


@pytest.fixture
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Copy the bundle, then write beside it everything a deployment would generate."""
    root = tmp_path / "compose"
    for name in release.bundle_paths(Context()):
        held = root / name
        held.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(release.BUNDLE_SOURCE / name, held)
    for name in GENERATED:
        generated = root / name
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text("a value no archive may carry\n", encoding="utf-8")
    # A checkout that lost the executable bit — to a umask, a filesystem that
    # carries no modes, or an unpacked archive. What the bundle ships has to be
    # decided by Git rather than by whatever this copy happens to show.
    (root / EXECUTABLE).chmod(0o644)
    monkeypatch.setattr(release, "BUNDLE_SOURCE", root)
    return root


@pytest.fixture
def tracked(source: Path) -> dict[str, int]:
    """Return the shipped paths and modes, read once the generated files are in place.

    Read here rather than before them, so a reader that walked the directory
    instead of asking Git would pick them up and be seen doing it.
    """
    del source
    return release.bundle_paths(Context())


@pytest.fixture
def binding(identity: release.ReleaseIdentity) -> bytes:
    """The record `release.kit` derives from one candidate's digests and its archive."""
    return release.image_binding(digests(), identity, loaded_manifest=LOADED_MANIFEST)


@pytest.fixture
def archive(identity: release.ReleaseIdentity, tracked: dict[str, int], binding: bytes, tmp_path: Path) -> Path:
    """One built archive, from the copied tree and the modes Git records for it."""
    return release.write_bundle(identity, tracked, tmp_path / "out", generated={BINDING: binding})


def members(archive: Path) -> list[tarfile.TarInfo]:
    """Return every entry of one archive, headers included.

    The headers are the subject: sorted order, the commit's time, an unnamed
    numeric owner and the mode Git records are each read from a `TarInfo` here
    rather than from an extracted tree, which would have lost all four.
    """
    with tarfile.open(archive) as opened:
        return opened.getmembers()


def gzip_header(archive: Path) -> bytes:
    """Return the ten bytes of one archive's gzip header.

    Read raw rather than through `gzip`, because what is asserted about them --
    the stamped time, the absent original filename, the compression level -- is
    exactly what decompressing discards.
    """
    return archive.read_bytes()[:10]


def contents(archive: Path) -> dict[str, bytes]:
    """Return each regular entry's bytes, keyed by its path inside the bundle."""
    held: dict[str, bytes] = {}
    with tarfile.open(archive) as opened:
        for member in opened.getmembers():
            if not member.isfile():
                continue
            stream = opened.extractfile(member)
            if stream is None:
                msg = f"{member.name} is a regular entry that would not open"
                raise AssertionError(msg)
            held[member.name.removeprefix(f"{ROOT}/")] = stream.read()
    return held


def skill_document(path: Path) -> tuple[dict[str, str], str]:
    """Return one skill's frontmatter and body."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == "---", path
    boundary = lines.index("---", 1)
    loaded = yaml.safe_load("\n".join(lines[1:boundary]))
    assert isinstance(loaded, dict), path
    return {str(key): str(value) for key, value in loaded.items()}, "\n".join(lines[boundary + 1 :])


def extract_bundle(archive: Path, destination: Path) -> Path:
    """Extract regular files from the bundle archive into an isolated directory."""
    with tarfile.open(archive) as opened:
        for member in opened.getmembers():
            if not member.isfile():
                continue
            stream = opened.extractfile(member)
            assert stream is not None, member.name
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(stream.read())
    return destination / ROOT


def install_snippet(guide: Path) -> str:
    """Return the guide's one documented shell installation block."""
    blocks = re.findall(r"```sh\n(.*?)\n```", guide.read_text(encoding="utf-8"), flags=re.DOTALL)
    assert len(blocks) == 1
    return blocks[0]


def test_the_bundle_ships_exactly_the_files_the_deployment_needs(tracked: dict[str, int]) -> None:
    """Read from Git, so an untracked file beside them cannot become bundle content."""
    assert set(tracked) == SHIPPED


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_each_skill_has_a_discriminating_portable_entrypoint(skill_name: str) -> None:
    """Discovery metadata and positive/negative boundaries travel in the entrypoint."""
    path = BUNDLE_SOURCE / SKILL_ENTRYPOINTS[skill_name]
    metadata, body = skill_document(path)

    assert metadata.keys() == {"name", "description"}
    assert metadata["name"] == path.parent.name == skill_name
    assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill_name)
    assert len(metadata["description"]) <= 1024
    assert "## Use this skill when" in body
    assert "## Do not use this skill when" in body
    for host_specific in ("/Users/", "bb thread", "BB_THREAD_STORAGE", "Codex"):
        assert host_specific not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_each_skill_routes_to_existing_docs_at_the_immutable_source_revision(skill_name: str) -> None:
    """A copied skill must not depend on its former checkout for human procedures."""
    path = BUNDLE_SOURCE / SKILL_ENTRYPOINTS[skill_name]
    links = MARKDOWN_LINK.findall(path.read_text(encoding="utf-8"))

    assert len(links) >= 4
    for link in links:
        parsed = urlsplit(link)
        assert parsed.scheme == "https"
        assert parsed.netloc == "github.com"
        assert parsed.path.startswith(PINNED_DOC_PATH)
        source_path = parsed.path.removeprefix(PINNED_DOC_PATH)
        assert (release.REPO_ROOT / source_path).is_file(), link


def test_skills_and_install_guide_survive_archive_extraction_and_copy(archive: Path, tmp_path: Path) -> None:
    """Exercise the real archive, then copy each whole skill without a checkout."""
    bundle = extract_bundle(archive, tmp_path / "extracted")
    installed = tmp_path / "agent-skills"
    for skill_name in SKILL_NAMES:
        copied = installed / skill_name
        shutil.copytree(bundle / "skills" / skill_name, copied)
        metadata, _body = skill_document(copied / "SKILL.md")
        assert metadata["name"] == skill_name

    guide = bundle / "skills" / "README.md"
    relative_links = [link for link in MARKDOWN_LINK.findall(guide.read_text(encoding="utf-8")) if "://" not in link]
    assert relative_links
    for link in relative_links:
        target = (guide.parent / link).resolve()
        assert target.is_relative_to(bundle.resolve())
        assert target.is_file(), link


def test_documented_install_is_all_or_nothing_and_does_not_overwrite(archive: Path, tmp_path: Path) -> None:
    """Run the shipped snippet against existing and empty agent skill directories."""
    bundle = extract_bundle(archive, tmp_path / "install-bundle")
    snippet = install_snippet(bundle / "skills" / "README.md")

    occupied = tmp_path / "occupied"
    sentinel = occupied / SKILL_NAMES[0] / "SKILL.md"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("user-owned sentinel\n", encoding="utf-8")
    refused = subprocess.run(  # noqa: S603 -- fixed /bin/sh argv runs the shipped guide's snippet
        ["/bin/sh", "-c", snippet],
        cwd=bundle,
        env={"AGENT_SKILLS_DIR": str(occupied)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert refused.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "user-owned sentinel\n"
    assert not (occupied / SKILL_NAMES[1]).exists()

    empty = tmp_path / "empty"
    empty.mkdir()
    installed = subprocess.run(  # noqa: S603 -- fixed /bin/sh argv runs the shipped guide's snippet
        ["/bin/sh", "-c", snippet],
        cwd=bundle,
        env={"AGENT_SKILLS_DIR": str(empty)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert installed.returncode == 0, installed.stderr
    for skill_name in SKILL_NAMES:
        assert (empty / skill_name / "SKILL.md").read_bytes() == (
            bundle / "skills" / skill_name / "SKILL.md"
        ).read_bytes()


@pytest.mark.parametrize("destination_kind", ["unset", "empty", "missing"])
def test_documented_install_refuses_an_unsafe_destination(archive: Path, tmp_path: Path, destination_kind: str) -> None:
    """Unset, empty, and nonexistent destinations stop before the copy command."""
    bundle = extract_bundle(archive, tmp_path / f"unsafe-{destination_kind}")
    snippet = install_snippet(bundle / "skills" / "README.md")
    marker = tmp_path / f"copy-called-{destination_kind}"
    fake_bin = tmp_path / f"bin-{destination_kind}"
    fake_bin.mkdir()
    fake_cp = fake_bin / "cp"
    fake_cp.write_text(f"#!/bin/sh\n: > '{marker}'\nexit 99\n", encoding="utf-8")
    fake_cp.chmod(0o755)
    environment = {"PATH": str(fake_bin)}
    if destination_kind == "empty":
        environment["AGENT_SKILLS_DIR"] = ""
    elif destination_kind == "missing":
        environment["AGENT_SKILLS_DIR"] = str(tmp_path / "does-not-exist")

    refused = subprocess.run(  # noqa: S603 -- fixed /bin/sh argv runs the shipped guide's snippet
        ["/bin/sh", "-c", snippet],
        cwd=bundle,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert refused.returncode != 0
    assert not marker.exists()


def test_the_archive_holds_the_shipped_files_and_nothing_a_deployment_generates(archive: Path) -> None:
    """The credential, the operator environment, and the instance identity stay on the host."""
    held = {member.name for member in members(archive) if member.isfile()}

    assert held == {f"{ROOT}/{name}" for name in (*SHIPPED, BINDING)}


def test_every_committed_file_in_the_archive_carries_the_bytes_of_its_source(
    archive: Path, tracked: dict[str, int]
) -> None:
    """Every property above holds just as well over an archive of empty entries.

    Names, modes, order, owners and stamps are each asserted apart from content,
    and two runs agreeing on their bytes agrees just as readily on the wrong
    ones. This is the one case that reads what a deployment would actually run.

    Only the committed half: the generated member has no file behind it, so
    comparing the whole archive against the source tree would require inventing
    one there — which is exactly the untracked artifact the release must not
    leave in `deploy/compose`.
    """
    held = contents(archive)

    assert {name: held[name] for name in tracked} == {name: (BUNDLE_SOURCE / name).read_bytes() for name in tracked}


def test_the_generated_binding_ships_as_its_own_member_with_the_derived_bytes(archive: Path, binding: bytes) -> None:
    """The bundle selects its image, so the record has to arrive inside the archive.

    Asserted apart from the committed members above, because the two are
    different claims: those carry a source file's bytes, and this one carries
    bytes no source file holds.
    """
    assert contents(archive)[BINDING] == binding


def test_exactly_one_binding_member_is_written(archive: Path) -> None:
    """A second entry under one name is what a tar reader resolves by luck."""
    named = [member.name for member in members(archive) if member.name == f"{ROOT}/{BINDING}"]

    assert named == [f"{ROOT}/{BINDING}"]


def test_the_generated_member_arrives_as_a_regular_file_a_host_can_read(archive: Path) -> None:
    """A directory sentinel and a generated byte member are different entries.

    They travel the same code path, and the one thing that tells them apart is
    what the entry says it is. A binding written as a directory extracts as one,
    and the wrapper's first read of it fails on a clean host rather than here.
    """
    entry = next(member for member in members(archive) if member.name == f"{ROOT}/{BINDING}")

    assert entry.isfile()
    assert entry.mode == 0o644
    assert entry.size == len(contents(archive)[BINDING])


def test_two_runs_from_one_tree_produce_the_same_bytes(
    identity: release.ReleaseIdentity, tracked: dict[str, int], binding: bytes, tmp_path: Path
) -> None:
    """The claim a recorded checksum rests on."""
    first = release.write_bundle(identity, tracked, tmp_path / "first", generated={BINDING: binding})
    second = release.write_bundle(identity, tracked, tmp_path / "second", generated={BINDING: binding})

    assert first.read_bytes() == second.read_bytes()


def test_every_entry_is_written_in_sorted_order(archive: Path) -> None:
    """Directory listing order differs between filesystems; the archive's must not."""
    names = [member.name for member in members(archive)]

    assert names == sorted(names)


def test_every_entry_carries_the_source_commits_time(archive: Path) -> None:
    """The build clock would put a different number in every run's archive."""
    stamps = {member.name: member.mtime for member in members(archive)}

    assert set(stamps.values()) == {COMMIT_SECONDS}


def test_no_entry_names_the_user_that_built_it(archive: Path) -> None:
    """A named owner is whoever ran the build, which differs on every machine."""
    owners = {(member.uid, member.gid, member.uname, member.gname) for member in members(archive)}

    assert owners == {(0, 0, "", "")}


def test_the_entry_point_arrives_executable_and_nothing_else_does(archive: Path) -> None:
    """A host runs that one file, and the copy this was built from cannot execute it.

    The mode is Git's, so a checkout that lost the bit still produces a bundle a
    clean host can run.
    """
    modes = {member.name: member.mode for member in members(archive) if member.isfile()}

    assert modes.pop(f"{ROOT}/{EXECUTABLE}") == 0o755
    assert set(modes.values()) == {0o644}


def test_the_compressed_stream_carries_the_commit_time_and_not_its_own_filename(archive: Path) -> None:
    """gzip stamps the clock and embeds the output's name unless it is told not to."""
    header = gzip_header(archive)

    assert int.from_bytes(header[4:8], "little") == COMMIT_SECONDS
    assert header[3] & GZIP_NAME_FLAG == 0
    assert header[8] == GZIP_BEST_COMPRESSION


def test_the_archive_is_written_in_the_pinned_tar_format(archive: Path) -> None:
    """Another format writes different headers for the same files."""
    with gzip.open(archive) as stream:
        assert stream.read(265)[257:] == USTAR_MAGIC


def test_the_checksum_names_the_archive_in_the_form_a_clean_host_reads(
    archive: Path, identity: release.ReleaseIdentity
) -> None:
    """A clean host has `sha256sum`, and it reads exactly this two-space form."""
    checksum = release.write_checksum(archive)
    digest, _, named = checksum.read_text(encoding="utf-8").strip().partition("  ")

    assert named == identity.bundle
    assert len(digest) == 64


def test_the_candidate_input_contains_only_what_clean_host_needs(
    archive: Path, identity: release.ReleaseIdentity
) -> None:
    """The preliminary manifest is input to qualification, never a qualification result."""
    document = release.candidate_input_document(digests(), identity, archive, archive)

    assert document == {
        "bundle": {"name": identity.bundle, "sha256": release._digest(archive)},
        "example": {"name": "example-package.yml", "sha256": release._digest(release.EXAMPLE_PACKAGE)},
        "identity": {"tag": identity.tag, "version": identity.version},
        "image": {"platforms": {"linux/amd64": {"config": AMD64_CONFIG, "sha256": release._digest(archive)}}},
    }
    assert "tests" not in json.dumps(document)


def test_release_kit_writes_the_candidate_input_beside_the_bundle(
    source: Path,
    tracked: dict[str, int],
    identity: release.ReleaseIdentity,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both workflow routes upload this directory, so the task owns the manifest."""
    del source
    record_dir = tmp_path / ".release"
    record_dir.mkdir()
    record_file = record_dir / "identity.json"
    record_file.write_text(json.dumps(identity.record()), encoding="utf-8")
    monkeypatch.setattr(release, "RECORD_FILE", record_file)
    monkeypatch.setattr(release, "BUNDLE_DIR", record_dir / "bundle")
    monkeypatch.setattr(release, "require_archivable_bundle", lambda _context: None)
    monkeypatch.setattr(release, "bundle_paths", lambda _context: tracked)
    monkeypatch.setattr(release, "build_qualification_kit", lambda: None)
    monkeypatch.setattr(image, "read_digests", digests)
    monkeypatch.setattr(image, "transferable_archive", lambda *_args: tmp_path / "image.tar")
    monkeypatch.setattr(image, "archive_manifest", lambda _archive: LOADED_MANIFEST)
    (tmp_path / "image.tar").write_bytes(b"image archive")

    release.kit(Context())

    candidate_input = release.BUNDLE_DIR / release.CANDIDATE_INPUT_NAME
    assert json.loads(candidate_input.read_text(encoding="utf-8")) == release.candidate_input_document(
        digests(), identity, release.BUNDLE_DIR / identity.bundle, tmp_path / "image.tar"
    )


# ---------------------------------------------------------------------------
# The binding record the package generates
# ---------------------------------------------------------------------------
# Four settings, derived from the candidate's own digests and its exported
# archive. An operator never writes one and never copies a digest into one: the
# whole point is that the archive already names the image it was qualified
# against, in every form a host can hold it under.


def parsed(record: bytes) -> dict[str, str]:
    """Return the binding as the wrapper's own `KEY=VALUE` reader sees it."""
    return dict(line.split("=", 1) for line in record.decode("utf-8").splitlines() if line and not line.startswith("#"))


def test_the_binding_names_the_qualified_platform_and_all_three_of_its_encodings(
    identity: release.ReleaseIdentity,
) -> None:
    """One image, in every immutable form a host can end up holding it under.

    The order is fixed and is the order the deployment tries them in: the index
    for a host that kept the original export, the synthesized manifest a
    containerd image store assigns a loaded archive, and the configuration
    digest the classic store leaves behind.
    """
    written = release.image_binding(digests(), identity, loaded_manifest=LOADED_MANIFEST)

    assert list(parsed(written).items()) == [
        (release.BINDING_PLATFORM_KEY, "linux/amd64"),
        (release.BINDING_INDEX_KEY, f"{INDEX_NAME}@{INDEX_DIGEST}"),
        (release.BINDING_MANIFEST_KEY, LOADED_MANIFEST),
        (release.BINDING_CONFIG_KEY, AMD64_CONFIG),
    ]


def test_the_binding_carries_no_pull_policy_of_its_own(identity: release.ReleaseIdentity) -> None:
    """An already-loaded private image resolves under the shipped `missing` policy.

    A further setting here would be a second image channel an operator could
    edit, which is the thing the record exists to remove.
    """
    assert set(parsed(release.image_binding(digests(), identity, loaded_manifest=LOADED_MANIFEST))) == {
        release.BINDING_PLATFORM_KEY,
        release.BINDING_INDEX_KEY,
        release.BINDING_MANIFEST_KEY,
        release.BINDING_CONFIG_KEY,
    }


def test_two_derivations_of_one_candidate_produce_the_same_bytes(identity: release.ReleaseIdentity) -> None:
    """The member is inside a checksummed archive, so its order cannot float."""
    assert release.image_binding(digests(), identity, loaded_manifest=LOADED_MANIFEST) == release.image_binding(
        digests(), identity, loaded_manifest=LOADED_MANIFEST
    )


@pytest.mark.parametrize("value", ["sha256:short", "sha256:" + "z" * 64, "infrahub-sync:latest", ""])
def test_a_malformed_loaded_manifest_is_refused(identity: release.ReleaseIdentity, value: str) -> None:
    """It is derived, so anything but a digest means the derivation went wrong."""
    with pytest.raises(release.ReleaseTaskError, match="immutable sha256 reference"):
        release.image_binding(digests(), identity, loaded_manifest=value)


@pytest.mark.parametrize("value", ["sha256:" + "6" * 64 + "\n", " sha256:" + "6" * 64, "sha256: " + "6" * 63])
def test_a_loaded_manifest_carrying_whitespace_is_refused(identity: release.ReleaseIdentity, value: str) -> None:
    """The wrapper reads this file line by line, and the manifest is one of its lines."""
    with pytest.raises(release.ReleaseTaskError, match="whitespace"):
        release.image_binding(digests(), identity, loaded_manifest=value)


def test_a_record_without_a_retained_index_name_is_refused(identity: release.ReleaseIdentity) -> None:
    """Half a reference is not a reference; a bundle would ship an unresolvable one."""
    with pytest.raises(release.ReleaseTaskError, match="index name"):
        release.image_binding(digests(index_name=""), identity, loaded_manifest=LOADED_MANIFEST)


def test_a_record_missing_the_qualified_platform_is_refused(identity: release.ReleaseIdentity) -> None:
    """linux/amd64 is what the lifecycle claim is made on; arm64 alone qualifies nothing."""
    arm64_only = {"linux/arm64": {"manifest": "sha256:" + "5" * 64, "config": ARM64_CONFIG}}

    with pytest.raises(release.ReleaseTaskError, match="linux/amd64"):
        release.image_binding(digests(platforms=arm64_only), identity, loaded_manifest=LOADED_MANIFEST)


def test_a_record_left_by_another_release_is_refused(identity: release.ReleaseIdentity) -> None:
    """A stale digest record would bind this bundle to somebody else's image."""
    foreign = digests(provenance={"version": "3.0.0a2", "revision": "b" * 40, "created": COMMIT_TIME})

    with pytest.raises(release.ReleaseTaskError, match="different release"):
        release.image_binding(foreign, identity, loaded_manifest=LOADED_MANIFEST)


@pytest.mark.parametrize("value", ["latest name", "latest\n", "\tlatest"])
def test_a_reference_value_carrying_whitespace_is_refused(identity: release.ReleaseIdentity, value: str) -> None:
    """The wrapper reads this file line by line; a value with a newline is two settings."""
    with pytest.raises(release.ReleaseTaskError, match="whitespace"):
        release.image_binding(digests(index_name=value), identity, loaded_manifest=LOADED_MANIFEST)
