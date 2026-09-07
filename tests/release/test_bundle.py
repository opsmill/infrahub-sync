"""What the deployment bundle archive holds, and what makes its bytes reproducible.

A checksum in a release record names one archive. That is only worth recording
while two runs from one tree produce the same bytes, so each field a tar entry or
a gzip stream carries independently of content is asserted on its own here rather
than left to a comparison of two runs — which would agree just as happily if
every one of those fields were floating together.

Which files ship is decided by what they are, not by what they are called.
`configuration/qualification.yaml` is the declared configuration the bootstrap
job registers and the one filesystem input `compose.yaml` binds, so it ships
despite reading as a test artifact. What never ships is what a deployment
generates on its host: the operator environment, the mounted credential, and the
instance identity.
"""

from __future__ import annotations

import gzip
import shutil
import tarfile
from pathlib import Path

import pytest
from invoke import Context

from tasks import release

VERSION = "3.0.0a1"
COMMIT = "708a8fca4b3fe300ae33242ddcd791a181926eeb"
COMMIT_TIME = "2026-09-04T10:15:00+02:00"
# The instant that timestamp names — 08:15 UTC — which every entry and the gzip
# header carry. Written out rather than computed, so a change to how the module
# reads a commit time fails here instead of agreeing with itself.
COMMIT_SECONDS = 1788509700

ROOT = f"infrahub-sync-compose-{VERSION}"

# Everything the bundle ships, as an equality: a file newly reaching it fails
# here rather than shipping unnoticed.
SHIPPED = {
    "compose.yaml",
    "infrahub-sync-compose",
    "defaults.conf",
    "configuration/qualification.yaml",
    "bootstrap/databases.sh",
}
# What a deployment writes beside them on its own host. None is the repository's
# to ship, and one of them is a credential.
GENERATED = ("operator.env", ".instance", "secrets/postgres-admin-password")

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
def archive(identity: release.ReleaseIdentity, tracked: dict[str, int], tmp_path: Path) -> Path:
    return release.write_bundle(identity, tracked, tmp_path / "out")


def members(archive: Path) -> list[tarfile.TarInfo]:
    with tarfile.open(archive) as opened:
        return opened.getmembers()


def gzip_header(archive: Path) -> bytes:
    return archive.read_bytes()[:10]


def test_the_bundle_ships_exactly_the_files_the_deployment_needs(tracked: dict[str, int]) -> None:
    """Read from Git, so an untracked file beside them cannot become bundle content."""
    assert set(tracked) == SHIPPED


def test_the_archive_holds_the_shipped_files_and_nothing_a_deployment_generates(archive: Path) -> None:
    """The credential, the operator environment, and the instance identity stay on the host."""
    held = {member.name for member in members(archive) if member.isfile()}

    assert held == {f"{ROOT}/{name}" for name in SHIPPED}


def test_two_runs_from_one_tree_produce_the_same_bytes(
    identity: release.ReleaseIdentity, tracked: dict[str, int], tmp_path: Path
) -> None:
    """The claim a recorded checksum rests on."""
    first = release.write_bundle(identity, tracked, tmp_path / "first")
    second = release.write_bundle(identity, tracked, tmp_path / "second")

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
