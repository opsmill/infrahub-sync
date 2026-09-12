"""The one release identity, the names every artifact takes from it, and its refusals.

Everything a release produces is named from package metadata read in one place.
These cases fix what that place accepts, what it derives, and what it refuses —
because an identity that could be renamed by a caller, or spelled two ways, would
put two names on one release and only disagree after something was published.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from invoke import Context, Result

from tasks import release

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"

# The selected V3 MVP identity. It appears here and in the package metadata; every
# artifact name below is derived rather than written down a second time.
VERSION = "3.0.0a2"

COMMIT = "708a8fca4b3fe300ae33242ddcd791a181926eeb"
# The two forms Git writes for a commit's own timestamp: a numeric offset, and
# the terminal `Z` it uses when the commit was made at UTC.
COMMIT_TIME = "2026-09-04T10:15:00+02:00"
COMMIT_TIME_UTC = "2026-09-05T01:29:37Z"


def identity(version: str = VERSION) -> release.ReleaseIdentity:
    return release.release_identity(version=version, revision=COMMIT, created=COMMIT_TIME)


class StubContext(Context):
    """A context whose Git answers are fixed, so no test depends on the checkout.

    `builds` names what a `uv build` in this context leaves behind, so the check
    on what a build produced can be exercised without running one.
    """

    def __init__(self, revision: str = COMMIT, created: str = COMMIT_TIME, builds: tuple[str, ...] = ()) -> None:
        super().__init__()
        self._answers = {"rev-parse": revision, "show": created}
        self._builds = builds

    def run(self, command: str, **kwargs: object) -> Result:  # type: ignore[override]
        del kwargs
        if "uv build" in command:
            release.DIST_DIR.mkdir(parents=True, exist_ok=True)
            for name in self._builds:
                (release.DIST_DIR / name).write_bytes(b"")
            return Result(stdout="", exited=0)
        answer = next((value for key, value in self._answers.items() if key in command), "")
        return Result(stdout=f"{answer}\n", exited=0)


def test_the_project_declares_the_selected_release_identity() -> None:
    """Package metadata is what every artifact name is read from."""
    assert f'version = "{VERSION}"' in PYPROJECT.read_text(encoding="utf-8")


def test_every_artifact_name_derives_from_the_declared_version() -> None:
    """One version, one spelling of it, in every name a release carries."""
    derived = identity()

    assert derived.tag == f"v{VERSION}"
    assert derived.title == f"Infrahub Sync - v{VERSION}"
    assert derived.wheel == f"infrahub_sync-{VERSION}-py3-none-any.whl"
    assert derived.sdist == f"infrahub_sync-{VERSION}.tar.gz"
    assert derived.bundle == f"infrahub-sync-compose-{VERSION}.tar.gz"


@pytest.mark.parametrize(
    "revision",
    ["", "708a8fc", "708a8fca4b3fe300ae33242ddcd791a181926eez", COMMIT + "0", "HEAD"],
)
def test_an_identity_refuses_a_revision_that_is_not_a_commit_identifier(revision: str) -> None:
    with pytest.raises(release.ReleaseTaskError):
        release.release_identity(version=VERSION, revision=revision, created=COMMIT_TIME)


@pytest.mark.parametrize(
    "created",
    ["", "2026-09-04", "2026-09-04T10:15:00", "yesterday", "2026-13-04T10:15:00+02:00"],
)
def test_an_identity_refuses_a_creation_time_that_is_not_an_absolute_instant(created: str) -> None:
    """`created` has to be a timestamp a reader can resolve, not a local wall clock."""
    with pytest.raises(release.ReleaseTaskError):
        release.release_identity(version=VERSION, revision=COMMIT, created=created)


@pytest.mark.parametrize("version", ["", "  ", "3.0.0a1 dirty", "not-a-version"])
def test_an_identity_refuses_a_version_that_is_not_a_release_identifier(version: str) -> None:
    with pytest.raises(release.ReleaseTaskError, match="release identifier"):
        release.release_identity(version=version, revision=COMMIT, created=COMMIT_TIME)


@pytest.mark.parametrize("version", ["3.0.0-a1", "3.0.0.a1", "3.0.0alpha1", "3.0.0A1", "03.0.0a1", "v3.0.0a1\n"])
def test_an_identity_refuses_a_version_that_is_not_its_own_normalized_form(version: str) -> None:
    """Each of these parses as `3.0.0a1`, so accepting it puts two names on one release.

    PEP 440 reads a leading `v` and surrounding whitespace, which is exactly how a
    tag or a captured command output reaches here looking like a version.
    """
    with pytest.raises(release.ReleaseTaskError, match="normalized form"):
        release.release_identity(version=version, revision=COMMIT, created=COMMIT_TIME)


@pytest.mark.parametrize("created", [COMMIT_TIME, COMMIT_TIME_UTC])
def test_an_identity_accepts_the_recorded_release_values(created: str) -> None:
    """Both offset forms are accepted, and the label keeps the commit's own text.

    Rewriting `Z` into the recorded value would put a timestamp in image metadata
    that the commit it names never carried.
    """
    derived = release.release_identity(version=VERSION, revision=COMMIT, created=created)

    assert (derived.version, derived.revision, derived.created) == (VERSION, COMMIT, created)


def test_the_declared_identity_is_read_from_the_installed_distribution() -> None:
    """The source is what names a release, so the version comes from its metadata."""
    derived = release.read_release_identity(StubContext())

    assert derived.version == VERSION
    assert (derived.revision, derived.created) == (COMMIT, COMMIT_TIME)


@pytest.mark.parametrize("declared", ["2.0.1", "3.0.0", "v3.0.0a2", "3.0.0a1", " 3.0.0a2", ""])
def test_a_declared_version_that_contradicts_the_source_is_refused(declared: str) -> None:
    """An accepted mismatch would let the caller, not the source, name the artifacts."""
    with pytest.raises(release.ReleaseTaskError, match="is not the source's"):
        release.match_declared(identity(), declared)


def test_the_validation_step_records_what_every_later_phase_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recorded document is how a job with no checkout learns the identity."""
    monkeypatch.setattr(release, "RECORD_DIR", tmp_path / ".release")
    monkeypatch.setattr(release, "RECORD_FILE", tmp_path / ".release" / "identity.json")

    release.identity(StubContext(), version=VERSION)

    assert json.loads(release.RECORD_FILE.read_text(encoding="utf-8")) == identity().record()


def test_the_validation_step_refuses_before_recording_anything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused run must leave no record a later phase could read as approval."""
    monkeypatch.setattr(release, "RECORD_DIR", tmp_path / ".release")
    monkeypatch.setattr(release, "RECORD_FILE", tmp_path / ".release" / "identity.json")

    with pytest.raises(release.ReleaseTaskError):
        release.identity(StubContext(), version="3.0.0")

    assert not release.RECORD_FILE.exists()


def test_a_recorded_identity_is_read_back_through_the_same_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the source values are read back; the names are derived again from them."""
    record = tmp_path / "identity.json"
    written = identity().record()
    record.write_text(json.dumps({**written, "bundle": "renamed.tar.gz", "tag": "v9.9.9"}), encoding="utf-8")
    monkeypatch.setattr(release, "RECORD_FILE", record)

    read = release.read_recorded_identity()

    assert (read.bundle, read.tag) == (written["bundle"], written["tag"])


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ("not json", "is not JSON"),
        ('["a"]', "must record an identity as a mapping"),
        ('{"version": "3.0.0a1"}', "does not record a version"),
    ],
)
def test_a_record_that_names_no_identity_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, document: str, expected: str
) -> None:
    record = tmp_path / "identity.json"
    record.write_text(document, encoding="utf-8")
    monkeypatch.setattr(release, "RECORD_FILE", record)

    with pytest.raises(release.ReleaseTaskError, match=expected):
        release.read_recorded_identity()


def test_a_run_without_a_recorded_identity_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing downstream may assume an identity the validation step never recorded."""
    monkeypatch.setattr(release, "RECORD_FILE", tmp_path / "identity.json")

    with pytest.raises(release.ReleaseTaskError, match=r"release\.identity"):
        release.read_recorded_identity()


def _recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the release outputs at a temporary tree holding one recorded identity."""
    monkeypatch.setattr(release, "RECORD_DIR", tmp_path / ".release")
    monkeypatch.setattr(release, "RECORD_FILE", tmp_path / ".release" / "identity.json")
    monkeypatch.setattr(release, "DIST_DIR", tmp_path / ".release" / "dist")
    release.RECORD_DIR.mkdir(parents=True)
    release.RECORD_FILE.write_text(json.dumps(identity().record()), encoding="utf-8")


def test_the_dry_run_accepts_the_distributions_the_identity_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This is the whole package side of a run with publication disabled."""
    _recorded(tmp_path, monkeypatch)
    derived = identity()

    release.build(StubContext(builds=(derived.wheel, derived.sdist)))

    assert sorted(path.name for path in release.DIST_DIR.iterdir()) == sorted((derived.sdist, derived.wheel))


@pytest.mark.parametrize(
    "produced",
    [
        ("infrahub_sync-3.0.0a1-py3-none-any.whl", "infrahub_sync-3.0.0a1.tar.gz"),
        ("infrahub_sync-3.0.0a2-py3-none-any.whl",),
        ("infrahub-sync-3.0.0a2-py3-none-any.whl", "infrahub_sync-3.0.0a2.tar.gz"),
    ],
)
def test_the_dry_run_refuses_a_distribution_the_identity_does_not_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, produced: tuple[str, ...]
) -> None:
    """A distribution named something else is one an upload would publish as this release."""
    _recorded(tmp_path, monkeypatch)

    with pytest.raises(release.ReleaseTaskError, match=r"not the 3\.0\.0a2 distributions"):
        release.build(StubContext(builds=produced))


def test_the_dry_run_ignores_what_the_builder_writes_beside_the_distributions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uv drops a `.gitignore` into its output directory, and it is not a release artifact."""
    _recorded(tmp_path, monkeypatch)
    derived = identity()

    release.build(StubContext(builds=(derived.wheel, derived.sdist, ".gitignore")))
