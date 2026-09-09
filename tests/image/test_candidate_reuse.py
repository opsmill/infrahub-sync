"""No image build command may run once the candidate layout exists.

Every consumer of a built candidate — the bill of materials, the vulnerability
scan, the image smoke, the Compose lifecycle matrix — has to read the retained
OCI layout. A build command reached through any of them, or through anything
they call, would produce a second artifact and then describe it as the first.

So the recorder below stands in for the whole Docker command surface rather than
for one helper: the property is about the commands that actually run, not about
the argv any single function returns.

Only the tasks and the helpers they call are spanned. A build a suite launched by
one of those tasks runs inside its own process is not: `test_build_context.py`
builds a throwaway audit image to read the build context back out, and that image
carries no release identity, is never written to the layout or the digest record,
and is removed as the test ends, so it can neither become a candidate nor be
mistaken for one.
"""

from __future__ import annotations

import ast
import io
import json
import shlex
import tarfile
from pathlib import Path

import pytest
from invoke import Context, Result

from tasks import compose, image, release
from tasks.release import release_identity

# The build-command path, and who is allowed to reach each part of it. Creating a
# builder is not building: the freshness gate needs one because it drives two real
# builds of its own, through a suite rather than through the argv below.
BUILD_PATH = {
    "build_command": {"image.py:build"},
    "_ensure_builder": {"image.py:build", "image.py:freshness"},
}
TASK_TREE = Path(image.__file__).resolve().parent


PLATFORMS = ("linux/amd64", "linux/arm64")
INDEX_DIGEST = "sha256:" + "1" * 64
CONFIGURATIONS = {
    "linux/amd64": "sha256:" + "a" * 64,
    "linux/arm64": "sha256:" + "b" * 64,
}
MANIFESTS = {
    "linux/amd64": "sha256:" + "c" * 64,
    "linux/arm64": "sha256:" + "d" * 64,
}
REVISION = "9" * 40
CREATED = "2026-09-06T00:00:00+00:00"
VERSION = "3.0.0a1"

CLEAN_SCAN = json.dumps({"matches": []})


def build_commands(commands: list[str]) -> list[str]:
    """Return the recorded commands that build an image.

    `docker buildx rm` and `docker buildx inspect` share a prefix with the build
    command and are not builds, so this reads the subcommand rather than looking
    for the word anywhere in the line.
    """
    found = []
    for command in commands:
        words = shlex.split(command)
        if words[:1] == ["docker"] and "build" in words[1:3]:
            found.append(command)
    return found


class Recorder(Context):
    """An Invoke context that records every command instead of running it.

    It answers only the questions the tasks ask of Docker: what a loaded image's
    identifier is, and what the scanners printed. Everything else is recorded and
    discarded, which is the point — a task that reached a build command would
    have to run it through here.

    It subclasses the real context because Invoke refuses to call a task with
    anything else, and because `cd` then behaves as the tasks expect.
    """

    def __init__(self, archives: Path) -> None:
        super().__init__()
        self.commands: list[str] = []
        # What the transfer step will be made to produce. A test reassigns this
        # to stand up an export that resolved something other than the record.
        self.exports = dict(CONFIGURATIONS)
        self._archives = archives

    def run(self, command: str, **kwargs: object) -> Result:
        del kwargs
        self.commands.append(command)
        words = shlex.split(command)
        if "skopeo" in command:
            self._write_archive(words)
        if "{{.Id}}" in command:
            return Result(stdout=CONFIGURATIONS[_platform_of(words[-1])] + "\n", exited=0)
        if "grype" in command:
            return Result(stdout=CLEAN_SCAN, exited=0)
        return Result(stdout="", exited=0)

    def _write_archive(self, words: list[str]) -> None:
        """Write what a Docker-load archive of the requested platform looks like.

        Skopeo is the tool under contract here, so the recorder produces what it
        would: an archive naming the configuration of the platform the command
        selected.
        """
        destination = next(word for word in words if word.startswith("docker-archive:"))
        _, path, reference = destination.split(":", 2)
        write_archive(self._archives / Path(path).name, self.exports[_platform_of(reference)])


def _platform_of(reference: str) -> str:
    """Return the platform a local reference names, as `local_reference` writes it."""
    return reference.rsplit(":", 1)[-1].replace("-", "/")


def write_archive(path: Path, configuration: str) -> None:
    """Write a Docker-load archive whose manifest names one configuration."""
    _, _, encoded = configuration.partition(":")
    manifest = json.dumps([{"Config": f"{encoded}.json", "RepoTags": [], "Layers": []}]).encode()
    with tarfile.open(path, "w") as archive:
        entry = tarfile.TarInfo("manifest.json")
        entry.size = len(manifest)
        archive.addfile(entry, io.BytesIO(manifest))


@pytest.fixture
def candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Recorder:
    """Stand up a built candidate's recorded outputs and the recorder that reads them."""
    build_dir = tmp_path / ".image"
    archives = build_dir / "archives"
    layout = build_dir / "oci"
    archives.mkdir(parents=True)
    layout.mkdir(parents=True)

    digests = build_dir / "digests.json"
    digests.write_text(
        json.dumps(
            {
                "schema_version": image.DIGESTS_SCHEMA_VERSION,
                "provenance": {"version": VERSION, "revision": REVISION, "created": CREATED},
                "index_digest": INDEX_DIGEST,
                "platforms": {
                    name: {"manifest": MANIFESTS[name], "config": CONFIGURATIONS[name]} for name in PLATFORMS
                },
                "canary_present": True,
            }
        ),
        encoding="utf-8",
    )
    identity = release_identity(version=VERSION, revision=REVISION, created=CREATED)
    for name in PLATFORMS:
        (build_dir / image.sbom_file(identity, name).name).write_text("{}", encoding="utf-8")
        # `image.inspect` reads each platform's configuration out of the layout.
        blob = layout / "blobs" / "sha256" / CONFIGURATIONS[name].removeprefix("sha256:")
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_text(json.dumps({"config": {"User": "10001:10001", "Cmd": ["python"]}}), encoding="utf-8")

    monkeypatch.setattr(image, "BUILD_DIR", build_dir)
    monkeypatch.setattr(image, "ARCHIVE_DIR", archives)
    monkeypatch.setattr(image, "LAYOUT_DIR", layout)
    monkeypatch.setattr(image, "DIGESTS_FILE", digests)
    monkeypatch.setattr(compose, "ARCHIVE_DIR", archives)
    # The gates record what they ran against. Without this they would write that
    # into the repository, where the qualification record reads it.
    monkeypatch.setattr(release, "RESULTS_DIR", build_dir / "results")
    monkeypatch.setenv(image.CANARY_ENV, "recorder-canary")

    return Recorder(archives)


CONSUMERS = {
    "image.inspect": image.inspect,
    "image.sbom": image.sbom,
    "image.scan": image.scan,
    "image.smoke": image.smoke,
    "compose.contract": compose.contract,
    "compose.lifecycle": compose.lifecycle,
    "compose.reclaim": compose.reclaim,
}


@pytest.mark.parametrize("name", sorted(CONSUMERS))
def test_no_candidate_consumer_runs_a_build_command(name: str, candidate: Recorder) -> None:
    """Each public task, and everything it calls, reads the layout instead of rebuilding."""
    CONSUMERS[name](candidate)

    assert build_commands(candidate.commands) == []


def test_the_recorder_names_a_build_command_when_one_runs() -> None:
    """Without this the assertion above would pass on a recorder that sees nothing."""
    identity = release_identity(version=VERSION, revision=REVISION, created=CREATED)
    build = " ".join(
        shlex.quote(word) for word in image.build_command(identity, platforms=PLATFORMS, destination=Path("/layout"))
    )

    assert build_commands(["docker buildx rm infrahub-sync-image", build, "docker image load --input a.tar"]) == [build]


def test_the_export_is_isolated_from_the_network_and_from_the_layout_it_reads() -> None:
    """The step that transfers a candidate must not be able to change it."""
    command = image.export_command(platform="linux/amd64", archive="image-linux-amd64.tar", reference="local:amd64")

    assert command[:4] == ("docker", "run", "--rm", "--network=none")
    assert image.SKOPEO_IMAGE in command
    assert f"{image.LAYOUT_DIR}:/layout:ro" in command
    assert f"{image.ARCHIVE_DIR}:/archives" in command
    assert command[command.index("--override-os") + 1] == "linux"
    assert command[command.index("--override-arch") + 1] == "amd64"
    assert command[-2:] == ("oci:/layout", "docker-archive:/archives/image-linux-amd64.tar:local:amd64")


def test_an_export_that_holds_another_platform_is_refused(candidate: Recorder) -> None:
    """A transfer that resolved the wrong image must not pass as the recorded one."""
    candidate.exports = dict.fromkeys(PLATFORMS, CONFIGURATIONS["linux/arm64"])

    with pytest.raises(image.ImageTaskError, match="not the built linux/amd64 image"):
        image.sbom(candidate, platform="linux/amd64")


def test_an_export_that_is_not_a_readable_archive_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "image-linux-amd64.tar"
    broken.write_bytes(b"not an archive")

    with pytest.raises(image.ImageTaskError, match="readable Docker-load archive"):
        image.archive_configuration(broken)


def test_an_export_holding_more_than_one_image_is_refused(tmp_path: Path) -> None:
    """Two images in one archive leave no single artifact for the record to name."""
    archive = tmp_path / "image-linux-amd64.tar"
    manifest = json.dumps([{"Config": "a.json"}, {"Config": "b.json"}]).encode()
    with tarfile.open(archive, "w") as opened:
        entry = tarfile.TarInfo("manifest.json")
        entry.size = len(manifest)
        opened.addfile(entry, io.BytesIO(manifest))

    with pytest.raises(image.ImageTaskError, match="exactly one image"):
        image.archive_configuration(archive)


def _references(node: ast.AST, module: str, scope: str, found: dict[str, set[str]]) -> None:
    """Record every place beneath one node that names part of the build-command path."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _references(child, module, child.name, found)
            continue
        named = child.id if isinstance(child, ast.Name) else child.attr if isinstance(child, ast.Attribute) else ""
        if named in BUILD_PATH:
            found.setdefault(named, set()).add(f"{module}:{scope}")
        _references(child, module, scope, found)


def build_path_references() -> dict[str, set[str]]:
    """Return who names each part of the build-command path, across the task tree."""
    found: dict[str, set[str]] = {}
    for module in sorted(TASK_TREE.glob("*.py")):
        _references(ast.parse(module.read_text(encoding="utf-8")), module.name, "<module>", found)
    return found


@pytest.mark.parametrize("name", sorted(BUILD_PATH))
def test_the_build_command_path_is_reached_only_from_where_it_is_meant_to_be(name: str) -> None:
    """One build means one caller, and this holds for a caller nobody has written yet.

    The recorder above sees only the tasks it is given. This reads the tree, so a
    task added later that reaches for a build — to replace a missing layout, say —
    fails here without anyone having remembered to name it.
    """
    assert build_path_references().get(name, set()) == BUILD_PATH[name]
