"""A secret in the build environment reaches nothing the build leaves behind."""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

import pytest

from tasks.image import CANARY_ENV, DIGESTS_FILE
from tests.image.canary import canary_locations
from tests.image.conftest import docker

PLANTED = "c4n4ry-6b18f0d9a7e34c5-do-not-leak"


def test_the_canary_scan_finds_a_planted_value_in_plain_and_compressed_output(tmp_path: Path) -> None:
    """Without this, the containment result below would be indistinguishable from a broken search."""
    (tmp_path / "config.json").write_text(json.dumps({"Labels": {"leak": PLANTED}}), encoding="utf-8")
    layer = tmp_path / "blobs" / "sha256"
    layer.mkdir(parents=True)
    (layer / "planted").write_bytes(gzip.compress(b"x" * (1 << 20) + PLANTED.encode() + b"y" * 16))
    (tmp_path / "clean.json").write_text(json.dumps({"Labels": {}}), encoding="utf-8")

    found = canary_locations(PLANTED, [tmp_path])

    assert sorted(found) == sorted([str(tmp_path / "config.json"), str(layer / "planted")])


def test_the_canary_scan_reports_nothing_for_output_that_holds_no_value(tmp_path: Path) -> None:
    (tmp_path / "clean.json").write_text(json.dumps({"Labels": {}}), encoding="utf-8")
    (tmp_path / "clean.gz").write_bytes(gzip.compress(b"nothing to see"))

    assert canary_locations(PLANTED, [tmp_path]) == ()


@pytest.mark.docker
def test_the_build_ran_with_a_secret_in_its_environment(image_layout: Path) -> None:
    """The containment claim below is only meaningful about a build that had one."""
    del image_layout
    record = json.loads(DIGESTS_FILE.read_text(encoding="utf-8"))

    assert record["canary_present"] is True
    assert os.environ.get(CANARY_ENV, "").strip()


@pytest.mark.docker
def test_no_build_environment_secret_reaches_the_artifact_or_the_retained_output(image_layout: Path) -> None:
    """Covers layers, configuration, labels, history, the SBOM, and every saved file.

    All of those are files under the build directory, so the check is one sweep of
    it rather than a list of surfaces that a new output could quietly fall outside.
    """
    canary = os.environ[CANARY_ENV].strip()

    assert canary_locations(canary, [image_layout.parent]) == ()


@pytest.mark.docker
def test_no_build_environment_secret_reaches_the_image_configuration_or_history(image_ref: str) -> None:
    """Read back through Docker as well, so containment does not rest on one reader."""
    canary = os.environ[CANARY_ENV].strip()

    configuration = docker(["image", "inspect", image_ref])
    history = docker(["image", "history", "--no-trunc", image_ref])

    assert configuration.returncode == 0, configuration.stderr
    assert history.returncode == 0, history.stderr
    assert canary not in configuration.stdout
    assert canary not in history.stdout
