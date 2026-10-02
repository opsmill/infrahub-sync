"""The tester packet's recorded inputs, layout, and installation instructions."""

from __future__ import annotations

import json
import tarfile
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from invoke import Context

from infrahub_sync.configuration import parse_configuration_package
from tasks import image, release

VERSION = "3.0.0a1"
COMMIT = "708a8fca4b3fe300ae33242ddcd791a181926eeb"
CONFIG = "sha256:" + "a" * 64


def test_example_package_is_valid_and_contains_no_endpoint_or_credential_value() -> None:
    """The shipped package is declared content, ready for an operator to edit."""
    source = release.REPO_ROOT / "examples" / "tester_packet" / "example-package.yml"
    text = source.read_text(encoding="utf-8")
    package = parse_configuration_package(yaml.safe_load(text))

    assert package.configuration.name == "netbox-evaluation"
    assert "REPLACE_WITH_NETBOX_URL" in text
    assert "REPLACE_WITH_INFRAHUB_URL" in text
    assert "http://" not in text
    assert "https://" not in text
    assert "password:" not in text
    assert "token: {$credential:" in text


@pytest.fixture
def candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[release.ReleaseIdentity, Path, Path, Path]:
    """Create small build records and archives without Docker or a service."""
    identity = release.release_identity(version=VERSION, revision=COMMIT, created="2026-09-04T10:15:00+02:00")
    records = tmp_path / ".release"
    bundle_dir = records / "bundle"
    bundle_dir.mkdir(parents=True)
    archive = tmp_path / "image-linux-amd64.tar"
    archive.write_bytes(b"qualified image archive")
    bundle = bundle_dir / identity.bundle
    bundle.write_bytes(b"qualified compose bundle")
    example = tmp_path / "example-package.yml"
    example.write_bytes((release.REPO_ROOT / "examples" / "tester_packet" / "example-package.yml").read_bytes())
    monkeypatch.setattr(release, "RECORD_FILE", records / "identity.json")
    monkeypatch.setattr(release, "BUNDLE_DIR", bundle_dir)
    monkeypatch.setattr(release, "QUALIFICATION_FILE", records / "qualification.json")
    monkeypatch.setattr(release, "EXAMPLE_PACKAGE", example)
    monkeypatch.setattr(release, "PACKET_DIR", records / "packet")
    monkeypatch.setattr(image, "archive_file", lambda _platform: archive)
    monkeypatch.setattr(image, "archive_configuration", lambda _archive: CONFIG)
    release.RECORD_FILE.write_text(json.dumps(identity.record()), encoding="utf-8")
    digests = {"provenance": identity.record(), "platforms": {"linux/amd64": {"config": CONFIG}}}
    candidate_input = release.candidate_input_document(digests, identity, bundle, archive)
    (bundle_dir / release.CANDIDATE_INPUT_NAME).write_text(json.dumps(candidate_input), encoding="utf-8")
    release.QUALIFICATION_FILE.write_text(
        json.dumps(
            {
                "identity": identity.record(),
                "bundle": candidate_input["bundle"],
                "image": {"platforms": {"linux/amd64": {"config": CONFIG}}},
                "tests": [
                    {"gate": gate, "platform": "linux/amd64", "image": CONFIG}
                    for gate in ("image-smoke", "compose-lifecycle")
                ],
            }
        ),
        encoding="utf-8",
    )
    return identity, archive, bundle, example


def test_packet_contains_the_tester_layout_and_checksum_files(
    candidate: tuple[release.ReleaseIdentity, Path, Path, Path],
) -> None:
    """The download and its archive both name exactly the requested files."""
    identity, image_archive, bundle, example = candidate
    release.packet(Context())
    root = f"private-candidate-{VERSION}-{COMMIT[:7]}"
    archive = release.PACKET_DIR / f"{root}.tar.gz"
    assert archive.is_file()
    assert archive.with_name(f"{archive.name}.sha256").read_text(encoding="utf-8") == (
        f"{release._digest(archive)}  {archive.name}\n"
    )
    assert {file.name for file in release.PACKET_DIR.iterdir()} == {archive.name, f"{archive.name}.sha256"}
    expected = {
        "SHA256SUMS",
        "README.md",
        "example-package.yml",
        "image-linux-amd64.tar",
        identity.bundle,
    }
    with tarfile.open(archive) as opened:
        contents = {}
        for member in opened.getmembers():
            if member.isfile():
                stream = opened.extractfile(member)
                assert stream is not None
                contents[member.name.removeprefix(f"{root}/linux-amd64/")] = stream.read()
    assert set(contents) == expected
    assert contents["image-linux-amd64.tar"] == image_archive.read_bytes()
    assert contents[identity.bundle] == bundle.read_bytes()
    assert contents["example-package.yml"] == example.read_bytes()
    for line in contents["SHA256SUMS"].decode().splitlines():
        digest, name = line.split("  ")
        assert digest == sha256(contents[name]).hexdigest()


@pytest.mark.parametrize("input_name", ["image", "bundle", "example"])
def test_packet_refuses_input_changed_since_the_build(
    candidate: tuple[release.ReleaseIdentity, Path, Path, Path], input_name: str
) -> None:
    """Any one altered input stops the task before an output is written."""
    _, image_archive, bundle, example = candidate
    path = {"image": image_archive, "bundle": bundle, "example": example}[input_name]
    path.write_bytes(path.read_bytes() + b"changed")

    with pytest.raises(release.ReleaseTaskError, match="checksum does not match"):
        release.packet(Context())
    assert not release.PACKET_DIR.exists()


def test_packet_readme_names_the_exact_build_and_install_path(
    candidate: tuple[release.ReleaseIdentity, Path, Path, Path],
) -> None:
    """The README needs no checkout and links guides at the built commit."""
    identity, _, _, _ = candidate
    release.packet(Context())
    archive = release.PACKET_DIR / f"private-candidate-{VERSION}-{COMMIT[:7]}.tar.gz"
    with tarfile.open(archive) as opened:
        member = opened.extractfile(f"private-candidate-{VERSION}-{COMMIT[:7]}/linux-amd64/README.md")
        assert member is not None
        readme = member.read().decode()
    assert f"Version: {VERSION}" in readme
    assert f"Commit: {COMMIT}" in readme
    for guide in (
        "quickstart-compose.mdx",
        "compose-deployment.mdx",
        "tutorials/netbox-to-existing-infrahub.mdx",
        "tutorials/nautobot-to-existing-infrahub.mdx",
    ):
        assert f"/blob/{COMMIT}/docs/docs/{guide}" in readme
    for command in (
        "set -eu",
        "sha256sum -c",
        "docker load",
        "tar -xzf",
        "./infrahub-sync-compose init",
        "./infrahub-sync-compose start",
        "./infrahub-sync-compose status",
    ):
        assert command in readme
    assert identity.bundle in readme
    assert "PostgreSQL, Prefect, and object-store images" in readme
