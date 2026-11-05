"""The development tasks rebuild `infrahub-sync:dev` only when its build inputs change.

The image carries the SHA-256 of `Dockerfile`, `pyproject.toml` and `uv.lock` as the
label `org.opsmill.infrahub-sync.build-inputs`. The tasks compare that label with the
checkout: a missing image, a missing label or a different value means a build, an equal
value means a start without one. A change under `infrahub_sync/` changes none of the
three files, because the stack mounts the source over the installed copy (FR-018,
FR-019). `docker image inspect` is replaced by a fake runner; no daemon is needed.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from invoke import Context

from tasks import dev

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A copy of the three build inputs and one source file."""
    for name in dev.BUILD_INPUT_FILES:
        shutil.copyfile(REPO_ROOT / name, tmp_path / name)
    package = tmp_path / "infrahub_sync"
    package.mkdir()
    (package / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    return tmp_path


class _Result:
    def __init__(self, stdout: str, *, ok: bool) -> None:
        self.stdout = stdout
        self.ok = ok


class FakeDocker:
    """Answers `docker image inspect` with a fixed label, or as a missing image."""

    def __init__(self, label: str | None) -> None:
        self.label = label
        self.commands: list[str] = []

    def run(self, command: str, **_kwargs: object) -> _Result:
        self.commands.append(command)
        if self.label is None:
            return _Result("", ok=False)
        return _Result(f"{self.label}\n", ok=True)


def _context(fake: FakeDocker, monkeypatch: pytest.MonkeyPatch) -> Context:
    context = Context()
    monkeypatch.setattr(context, "run", fake.run)
    return context


def test_the_hash_is_the_sha256_of_the_three_files_in_order(checkout: Path) -> None:
    expected = hashlib.sha256(b"".join((checkout / name).read_bytes() for name in dev.BUILD_INPUT_FILES))

    assert dev.build_inputs_hash(checkout) == expected.hexdigest()
    assert dev.BUILD_INPUT_FILES == ("Dockerfile", "pyproject.toml", "uv.lock")


@pytest.mark.parametrize("name", ["Dockerfile", "pyproject.toml", "uv.lock"])
def test_the_hash_changes_when_a_build_input_changes(checkout: Path, name: str) -> None:
    before = dev.build_inputs_hash(checkout)
    with (checkout / name).open("a", encoding="utf-8") as handle:
        handle.write("\n# changed\n")

    assert dev.build_inputs_hash(checkout) != before


def test_the_hash_does_not_change_when_the_source_changes(checkout: Path) -> None:
    before = dev.build_inputs_hash(checkout)
    (checkout / "infrahub_sync" / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    (checkout / "infrahub_sync" / "added.py").write_text("ADDED = True\n", encoding="utf-8")

    assert dev.build_inputs_hash(checkout) == before


@pytest.mark.parametrize(
    "label",
    [
        pytest.param(None, id="missing-image"),
        pytest.param("", id="empty-label"),
        pytest.param("<no value>", id="missing-label"),
        pytest.param("0" * 64, id="different-label"),
    ],
)
def test_a_missing_or_stale_image_is_built(checkout: Path, label: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(FakeDocker(label), monkeypatch)

    assert dev.rebuild_decision(context, root=checkout) == "build"


def test_an_image_built_from_the_same_inputs_is_started(checkout: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(FakeDocker(dev.build_inputs_hash(checkout)), monkeypatch)

    assert dev.rebuild_decision(context, root=checkout) == "start"


def test_the_label_is_read_from_the_development_image(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDocker("abc")

    assert dev.image_build_inputs(_context(fake, monkeypatch)) == "abc"
    assert len(fake.commands) == 1
    command = fake.commands[0]
    assert command.startswith("docker image inspect --format ")
    assert '"org.opsmill.infrahub-sync.build-inputs"' in command
    assert command.endswith(" infrahub-sync:dev")
