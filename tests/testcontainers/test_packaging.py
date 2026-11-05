"""The package ships the root Compose file, byte-equal, and refuses to load without its extra.

No Docker is needed. The wheel test needs `uv` on `PATH` and skips without it.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess  # noqa: S404 -- runs the fixed `uv build` command line below
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT_COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
PACKAGED_COMPOSE_FILE = REPO_ROOT / "infrahub_sync" / "testcontainers" / "docker-compose.yml"
WHEEL_MEMBER = "infrahub_sync/testcontainers/docker-compose.yml"


def test_the_packaged_compose_file_is_byte_equal_to_the_root_file() -> None:
    """`release.update-docker-compose` rewrites both; any other edit to one must reach the other."""
    assert PACKAGED_COMPOSE_FILE.read_bytes() == ROOT_COMPOSE_FILE.read_bytes(), (
        "infrahub_sync/testcontainers/docker-compose.yml differs from docker-compose.yml; copy the root file over it"
    )


@pytest.mark.timeout(120)
def test_the_wheel_ships_the_root_compose_file(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH; the wheel build needs it")

    result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [uv, "build", "--wheel", "--quiet", "--out-dir", str(tmp_path), str(REPO_ROOT)],
        capture_output=True,
        text=True,
        check=False,
        timeout=110,
    )
    assert result.returncode == 0, result.stderr
    (wheel,) = tmp_path.glob("infrahub_sync-*.whl")

    with zipfile.ZipFile(wheel) as archive:
        assert archive.read(WHEEL_MEMBER) == ROOT_COMPOSE_FILE.read_bytes()


def test_loading_the_package_without_the_extra_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    # A None entry in `sys.modules` makes the next import of that name raise ImportError.
    monkeypatch.setitem(sys.modules, "infrahub_testcontainers", None)
    monkeypatch.delitem(sys.modules, "infrahub_sync.testcontainers", raising=False)
    monkeypatch.delitem(sys.modules, "infrahub_sync.testcontainers.helpers", raising=False)

    with pytest.raises(ImportError, match=r"`testcontainers` extra"):
        importlib.import_module("infrahub_sync.testcontainers")
