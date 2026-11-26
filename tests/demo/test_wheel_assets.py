"""The wheel carries the NetBox demo's schema and package, byte-identical to their sources.

An editable install reads the repository paths instead (`tests/demo/test_assets.py`), so
only a built wheel proves the bundled path. No Docker is needed. The test needs `uv` on
`PATH` and skips without it, like `tests/testcontainers/test_packaging.py`.
"""

from __future__ import annotations

import shutil
import subprocess  # noqa: S404 -- runs the fixed `uv build` command line below
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_SCHEMA_DIR = REPO_ROOT / "tests" / "data" / "nightly_schema" / "traditional-infrastructure-sot"
SOURCE_PACKAGE = REPO_ROOT / "examples" / "netbox_to_infrahub" / "package.yml"
BUNDLED_ROOT = "infrahub_sync/demo/assets"
EXPECTED_SCHEMA_FILES = 16


@pytest.mark.timeout(120)
def test_the_wheel_carries_the_demo_schema_and_package(tmp_path: Path) -> None:
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

    sources = sorted(SOURCE_SCHEMA_DIR.glob("*.yml"))
    assert len(sources) == EXPECTED_SCHEMA_FILES
    with zipfile.ZipFile(wheel) as archive:
        bundled = sorted(name for name in archive.namelist() if name.startswith(f"{BUNDLED_ROOT}/"))
        expected = sorted(
            [
                *(f"{BUNDLED_ROOT}/schema/{source.name}" for source in sources),
                f"{BUNDLED_ROOT}/netbox/package.yml",
            ]
        )
        assert bundled == expected
        for source in sources:
            assert archive.read(f"{BUNDLED_ROOT}/schema/{source.name}") == source.read_bytes(), source.name
        assert archive.read(f"{BUNDLED_ROOT}/netbox/package.yml") == SOURCE_PACKAGE.read_bytes()
