"""The pinned Infrahub fixture must not start anything the image prerequisite would skip.

`infrahub_fixture` starts and seeds a real Infrahub stack, and `sync_image` holds
the skip that decides the Docker-backed cases do not run at all. Only a
dependency edge orders them: a session fixture is created at its first use, so
the order follows whichever case reaches it first, and a `sync_image` skip
already cached for one case does not stop a later case that reaches
`infrahub_fixture` on its own.

The negative cases run real pytest sessions, because that scheduling is the
property under test and because a skip is cached for the rest of the session it
happens in — asserting it here would silence the suite that runs the deployment.
The positive case needs no session: it drives the two fixture bodies directly.
"""

from __future__ import annotations

import inspect
import os
import subprocess  # noqa: S404 -- the inner pytest sessions this proof is built on, and a stubbed result
import sys
from pathlib import Path

import pytest

from tasks import preview
from tests.compose import conftest
from tests.compose.conftest import FIXTURE_INFRAHUB_PORT, FIXTURE_PROJECT, IMAGE_REFERENCE_ENV
from tests.compose.redaction import Captured

REPO_ROOT = Path(__file__).resolve().parents[2]

# The case whose own request order put the side effect ahead of the prerequisite.
INCIDENT = "tests/compose/test_lifecycle.py::test_a_plan_apply_and_separate_sync_run_through_the_replacement_worker"
# A case that reaches `sync_image` first, used to cache its skip before the one above.
EARLIER_SKIP = "tests/compose/test_bootstrap_idempotence.py::test_the_first_bootstrap_created_the_two_databases_and_their_owner_roles"

# Answers the daemon probe and records every call to the one subprocess boundary
# the compose fixtures reach Docker, Compose and infrahubctl through. Nothing the
# fixtures do can start a container while this is loaded.
STUB = """
import os, pathlib, sys

MARKER = pathlib.Path(os.environ["FIXTURE_SIDE_EFFECT_MARKER"])


def _module(suffix):
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if path and path.endswith(suffix):
            return module
    raise AssertionError(suffix + " was never imported")


def pytest_collection_finish(session):
    del session
    _module("tests/compose/lifecycle.py").daemon_available = lambda: True

    def refuse(argv, **kwargs):
        del kwargs
        rendered = " ".join(str(part) for part in argv)
        with MARKER.open("a", encoding="utf-8") as handle:
            handle.write(rendered + "\\n")
        raise AssertionError("side effect reached: " + rendered)

    _module("tests/compose/conftest.py").capture = refuse
"""


def run_fixture_setup(tmp_path: Path, selection: list[str]) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Set up the real fixtures for `selection` with no image named, recording any side effect.

    Two separate things keep this proof harmless, and they cover different halves.
    The stub replaces `capture` -- the one subprocess boundary the fixtures reach
    Docker, Compose and infrahubctl through -- so no fixture setup can start
    anything. That is the half that matters here, because the defect is in setup.
    `--setup-only` covers the other half: it stops the test bodies that follow.
    """
    (tmp_path / "fixture_side_effect_stub.py").write_text(STUB, encoding="utf-8")
    marker = tmp_path / "side-effects.txt"
    environment = {key: value for key, value in os.environ.items() if key not in {IMAGE_REFERENCE_ENV, "PYTHONPATH"}}
    environment["PYTHONPATH"] = str(tmp_path)
    environment["FIXTURE_SIDE_EFFECT_MARKER"] = str(marker)
    result = subprocess.run(  # noqa: S603 -- fixed argv, this interpreter
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-rs",
            "-o",
            "addopts=",
            "-p",
            "fixture_side_effect_stub",
            "--setup-only",
            *selection,
        ],
        capture_output=True,
        check=False,
        text=True,
        cwd=REPO_ROOT,
        env=environment,
        timeout=300,
    )
    recorded = marker.read_text(encoding="utf-8").splitlines() if marker.exists() else []
    return result, recorded


@pytest.mark.parametrize(
    "selection",
    [
        pytest.param([INCIDENT], id="reached-first-in-a-session"),
        pytest.param([EARLIER_SKIP, INCIDENT], id="reached-after-another-case-already-skipped"),
    ],
)
def test_no_image_means_the_infrahub_fixture_starts_nothing(tmp_path: Path, selection: list[str]) -> None:
    """With no image named, every case skips on the prerequisite and nothing is started."""
    result, recorded = run_fixture_setup(tmp_path, selection)
    assert not recorded, f"the fixture ran {len(recorded)} command(s) before the image skip: {recorded}"
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    assert f"{len(selection)} skipped" in result.stdout, result.stdout[-3000:]
    assert f"{IMAGE_REFERENCE_ENV} is unset" in result.stdout, result.stdout[-3000:]


def test_a_named_image_runs_the_fixture_through_start_seed_and_teardown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Supplying the prerequisite preserves the fixture: it starts, seeds, yields, and tears down.

    The two real fixture bodies are driven directly, so this says what the
    fixture does once the prerequisite passes without scheduling a session.
    """
    image = "sha256:" + "4" * 64
    monkeypatch.setenv(IMAGE_REFERENCE_ENV, image)
    reference = inspect.unwrap(conftest.sync_image)(None)
    assert reference == image

    calls: list[str] = []

    def record(argv: list[str], **kwargs: object) -> Captured:
        del kwargs
        calls.append(" ".join(str(part) for part in argv))
        return Captured(subprocess.CompletedProcess(argv, 0, "", ""))

    monkeypatch.setattr(conftest, "capture", record)
    monkeypatch.setattr(preview, "ensure_smoke_branch", lambda env: calls.append(f"seed {sorted(env)}"))

    fixture = inspect.unwrap(conftest.infrahub_fixture)(reference)
    provided = next(fixture)
    assert provided["address"] == f"http://127.0.0.1:{FIXTURE_INFRAHUB_PORT}"
    assert provided["token"]
    started, loaded, seeded = calls
    assert f"--project-name {FIXTURE_PROJECT}" in started
    assert "up --detach --wait" in started
    assert "infrahubctl schema load --wait" in loaded
    assert seeded.startswith("seed ")

    with pytest.raises(StopIteration):
        next(fixture)
    assert "down --volumes --remove-orphans" in calls[-1]
