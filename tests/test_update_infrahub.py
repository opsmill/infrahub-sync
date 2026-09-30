"""Offline checks for the Infrahub version bump script."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "update_infrahub.py"
NEW_DIGEST = "sha256:" + "c" * 64


@pytest.fixture
def bump() -> ModuleType:
    """Load the script as a module."""
    spec = importlib.util.spec_from_file_location("update_infrahub", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tree(bump: ModuleType, tmp_path: Path) -> Path:
    """Copy the real pinned files into a scratch tree."""
    for relative in bump.PINNED_FILES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return tmp_path


def test_the_shipped_files_carry_the_pin_the_script_reads(bump: ModuleType, tree: Path) -> None:
    """Every pinned file still names the version, so a bump reaches all of them."""
    old_version, old_digest = bump.current_pin(tree)
    pattern = bump.version_occurrences(old_version)
    for relative in bump.PINNED_FILES:
        text = (tree / relative).read_text(encoding="utf-8")
        assert pattern.search(text), relative
    for relative in bump.DIGEST_FILES:
        assert old_digest in (tree / relative).read_text(encoding="utf-8"), relative


def test_a_bump_moves_the_version_and_digest_together(bump: ModuleType, tree: Path) -> None:
    """No pinned file keeps the old version or digest after a bump."""
    old_version, old_digest = bump.current_pin(tree)

    changed = bump.update(tree, "99.1.0", resolve=lambda _version: NEW_DIGEST)

    assert changed == list(bump.PINNED_FILES)
    assert bump.current_pin(tree) == ("99.1.0", NEW_DIGEST)
    pattern = bump.version_occurrences(old_version)
    for relative in bump.PINNED_FILES:
        text = (tree / relative).read_text(encoding="utf-8")
        assert not pattern.search(text), relative
        assert old_digest not in text, relative
    compose = (tree / "development/docker-compose.infrahub.yml").read_text(encoding="utf-8")
    assert f"99.1.0@{NEW_DIGEST}" in compose


def test_the_current_version_is_a_no_op(bump: ModuleType, tree: Path) -> None:
    """Re-running against the pinned version changes nothing and resolves nothing."""
    old_version, _ = bump.current_pin(tree)

    def fail(_version: str) -> str:
        pytest.fail("the digest must not be resolved")

    assert bump.update(tree, old_version, resolve=fail) == []


def test_a_file_that_lost_its_pin_leaves_the_tree_untouched(bump: ModuleType, tree: Path) -> None:
    """A pinned file without the version stops the bump before anything is written."""
    old_version, _ = bump.current_pin(tree)
    lost = tree / "tests/compose/fixture-override.yaml"
    lost.write_text(lost.read_text(encoding="utf-8").replace(old_version, "unpinned"), encoding="utf-8")
    before = {relative: (tree / relative).read_text(encoding="utf-8") for relative in bump.PINNED_FILES}

    with pytest.raises(bump.UpdateError, match="fixture-override"):
        bump.update(tree, "99.1.0", resolve=lambda _version: NEW_DIGEST)

    assert {relative: (tree / relative).read_text(encoding="utf-8") for relative in bump.PINNED_FILES} == before


@pytest.mark.parametrize("version", ["", "latest", "1.11", "1.11.0; rm -rf /", "v1.11.0"])
def test_a_malformed_version_is_refused(bump: ModuleType, tree: Path, version: str) -> None:
    """Dispatch payloads are untrusted, so only a release version is accepted."""
    with pytest.raises(bump.UpdateError, match="not a release version"):
        bump.update(tree, version, resolve=lambda _version: NEW_DIGEST)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1.10.6", True), ("1.10.6.", True), ("1.10.60", False), ("11.10.6", False), ("1.10.6.1", False)],
)
def test_the_version_matches_only_as_a_whole_token(bump: ModuleType, text: str, *, expected: bool) -> None:
    """A longer version that merely contains the pinned one is not rewritten."""
    assert bool(bump.version_occurrences("1.10.6").search(text)) is expected
