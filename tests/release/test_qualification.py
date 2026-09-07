"""What a candidate has to be made of before a record may say it qualified.

The record is the only thing a later approval reads. Everything it claims is
therefore checked here against the artifact that produced the claim, and each
piece it cannot find is a refusal rather than an omission — a record that quietly
left out a gate would read exactly like one whose gate had passed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from invoke import Context, Result

from tasks import image, release

VERSION = "3.0.0a1"
COMMIT = "708a8fca4b3fe300ae33242ddcd791a181926eeb"
COMMIT_TIME = "2026-09-04T10:15:00+02:00"

PLATFORMS = ("linux/amd64", "linux/arm64")
INDEX = "sha256:" + "1" * 64
DIGESTS = {
    "linux/amd64": {"manifest": "sha256:" + "c" * 64, "config": "sha256:" + "a" * 64},
    "linux/arm64": {"manifest": "sha256:" + "d" * 64, "config": "sha256:" + "b" * 64},
}

ARTIFACT_NAMES = ("infrahub-sync-candidate-bundle", "infrahub-sync-candidate-distributions")
RETENTION_DAYS = 90


def identity() -> release.ReleaseIdentity:
    return release.release_identity(version=VERSION, revision=COMMIT, created=COMMIT_TIME)


def artifacts_document() -> dict[str, object]:
    return {
        "retention_days": RETENTION_DAYS,
        "artifacts": {
            name: {"id": str(1000 + offset), "digest": f"sha256:{offset}"} for offset, name in enumerate(ARTIFACT_NAMES)
        },
    }


@pytest.fixture
def candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand up everything a complete candidate leaves behind, and nothing more."""
    record_dir = tmp_path / ".release"
    build_dir = tmp_path / ".image"
    for directory in (record_dir, build_dir):
        directory.mkdir()

    monkeypatch.setattr(release, "RECORD_DIR", record_dir)
    monkeypatch.setattr(release, "RECORD_FILE", record_dir / "identity.json")
    monkeypatch.setattr(release, "BUNDLE_DIR", record_dir / "bundle")
    monkeypatch.setattr(release, "QUALIFICATION_DIR", record_dir / "qualification")
    monkeypatch.setattr(release, "QUALIFICATION_FILE", record_dir / "qualification.json")
    monkeypatch.setattr(release, "RESULTS_DIR", record_dir / "results")
    monkeypatch.setattr(release, "ARTIFACTS_FILE", record_dir / "artifacts.json")
    monkeypatch.setattr(image, "BUILD_DIR", build_dir)
    monkeypatch.setattr(image, "DIGESTS_FILE", build_dir / "digests.json")

    derived = identity()
    release.RECORD_FILE.write_text(json.dumps(derived.record()), encoding="utf-8")
    image.DIGESTS_FILE.write_text(
        json.dumps(
            {
                "schema_version": image.DIGESTS_SCHEMA_VERSION,
                "provenance": {"version": VERSION, "revision": COMMIT, "created": COMMIT_TIME},
                "index_digest": INDEX,
                "platforms": DIGESTS,
                "canary_present": True,
            }
        ),
        encoding="utf-8",
    )
    release.write_bundle(derived, release.bundle_paths(Context()), release.BUNDLE_DIR)
    for name in PLATFORMS:
        image.sbom_file(derived, name).write_text('{"packages": []}', encoding="utf-8")
        image.scan_file(derived, name).write_text('{"matches": []}', encoding="utf-8")
        release.record_gate("image-smoke", platform=name, image=DIGESTS[name]["config"], command="pytest")
    release.record_gate(
        "compose-lifecycle", platform="linux/amd64", image=DIGESTS["linux/amd64"]["config"], command="pytest"
    )
    release.ARTIFACTS_FILE.write_text(json.dumps(artifacts_document()), encoding="utf-8")
    return tmp_path


def written() -> dict:
    return json.loads(release.QUALIFICATION_FILE.read_text(encoding="utf-8"))


def test_the_record_links_the_candidate_to_everything_it_is_made_of(candidate: Path) -> None:
    """One document, from the version down to the bytes a service is holding."""
    del candidate
    release.qualify(Context())
    record = written()

    assert record["identity"] == identity().record()
    assert record["image"] == {"index": INDEX, "platforms": DIGESTS}
    assert record["bundle"]["name"] == identity().bundle
    assert len(record["bundle"]["sha256"]) == 64
    assert sorted(record["sboms"]) == sorted(PLATFORMS)
    assert record["scan"]["platforms"]["linux/amd64"]["blocking"] == 0
    assert {result["gate"] for result in record["tests"]} == {"image-smoke", "compose-lifecycle"}
    assert record["retention_days"] == RETENTION_DAYS
    assert sorted(record["artifacts"]) == sorted(ARTIFACT_NAMES)


def test_every_recorded_test_result_names_the_bytes_it_ran_against(candidate: Path) -> None:
    """A result that named no artifact would be a claim about nothing in particular."""
    del candidate
    release.qualify(Context())

    assert {result["image"] for result in written()["tests"]} <= {entry["config"] for entry in DIGESTS.values()}


def test_a_candidate_whose_image_names_another_release_is_refused(candidate: Path) -> None:
    """The bundle and the image have to be two parts of one release, not two releases."""
    del candidate
    document = json.loads(image.DIGESTS_FILE.read_text(encoding="utf-8"))
    document["provenance"]["version"] = "3.0.0a2"
    image.DIGESTS_FILE.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(release.ReleaseTaskError, match="different releases"):
        release.qualify(Context())


def test_a_candidate_missing_its_bundle_is_refused(candidate: Path) -> None:
    del candidate
    (release.BUNDLE_DIR / identity().bundle).unlink()

    with pytest.raises(release.ReleaseTaskError, match=r"release\.kit"):
        release.qualify(Context())


@pytest.mark.parametrize("gate", ["image-smoke-linux-arm64.json", "compose-lifecycle-linux-amd64.json"])
def test_a_candidate_a_gate_never_ran_against_is_refused(candidate: Path, gate: str) -> None:
    """A record that could be written without the gate would say nothing about it."""
    del candidate
    (release.RESULTS_DIR / gate).unlink()

    with pytest.raises(release.ReleaseTaskError, match="qualified nothing"):
        release.qualify(Context())


@pytest.mark.parametrize("gate", ["image-smoke-linux-amd64.json", "compose-lifecycle-linux-amd64.json"])
def test_a_gate_result_from_other_bytes_is_refused(candidate: Path, gate: str) -> None:
    """A result naming another image is a gate that ran, but not against this candidate.

    A stale result left by an earlier run — or by anything else that writes one —
    would otherwise satisfy the record on behalf of a gate this candidate never
    faced.
    """
    del candidate
    result = release.RESULTS_DIR / gate
    recorded = json.loads(result.read_text(encoding="utf-8"))
    recorded["image"] = "sha256:" + "e" * 64
    result.write_text(json.dumps(recorded), encoding="utf-8")

    with pytest.raises(release.ReleaseTaskError, match="qualified nothing"):
        release.qualify(Context())


def test_a_candidate_without_a_vulnerability_report_is_refused(candidate: Path) -> None:
    del candidate
    image.scan_file(identity(), "linux/amd64").unlink()

    with pytest.raises(release.ReleaseTaskError, match=r"image\.scan"):
        release.qualify(Context())


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ({"artifacts": {}}, "positive retention_days"),
        ({"retention_days": 0, "artifacts": {"a": {"id": "1", "digest": "sha256:1"}}}, "positive retention_days"),
        ({"retention_days": 90}, "every uploaded artifact"),
        ({"retention_days": 90, "artifacts": {"a": {"id": "1"}}}, "without both an identifier and a digest"),
        ({"retention_days": 90, "artifacts": {"a": {"digest": "sha256:1"}}}, "without both an identifier and a digest"),
    ],
)
def test_a_record_that_cannot_name_the_uploaded_bytes_is_refused(
    candidate: Path, document: dict, expected: str
) -> None:
    """Promotion has nothing to check an artifact against without an identifier and a digest."""
    del candidate
    release.ARTIFACTS_FILE.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(release.ReleaseTaskError, match=expected):
        release.qualify(Context())


def test_a_record_written_before_the_uploads_it_names_is_refused(candidate: Path) -> None:
    """The identifiers exist only once the service has taken the bytes."""
    del candidate
    release.ARTIFACTS_FILE.unlink()

    with pytest.raises(release.ReleaseTaskError, match="the candidate workflow writes it"):
        release.qualify(Context())


def test_a_bundle_that_is_not_the_commits_content_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Archiving a dirty tree would bind a checksum to a revision it never came from."""
    monkeypatch.setattr(release, "BUNDLE_TREE", "tests/release")

    with pytest.raises(release.ReleaseTaskError, match="differ from HEAD"):
        release.require_archivable_bundle(_ReportingContext(f" M tests/release/{tmp_path.name}.py"))


class _ReportingContext(Context):
    """A context whose `git status` answer is fixed, so no test depends on the checkout."""

    def __init__(self, reported: str) -> None:
        super().__init__()
        self._reported = reported

    def run(self, command: str, **kwargs: object) -> Result:  # type: ignore[override]
        del kwargs
        return Result(stdout=f"{self._reported}\n" if "status" in command else "", exited=0)
