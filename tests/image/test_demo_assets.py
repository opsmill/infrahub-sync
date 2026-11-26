"""The image runs `infrahub-sync demo prepare netbox` with no checkout (spec 024, User Story 2).

The image installs the project as a built wheel, so the demo's schema and package must
come from the bundled `infrahub_sync/demo/assets/` directory, not from the repository
paths an editable install falls back to. Both checks run the way the image is deployed:
read-only root, no network.
"""

from __future__ import annotations

import json

import pytest

from tests.image.conftest import run_in_image

pytestmark = pytest.mark.docker

EXPECTED_SCHEMA_FILES = 16

# Prints where the assets resolve from, how many schema files there are, and whether
# the package text can be read, so the assertions below can name what went wrong.
RESOLVE_PROBE = (
    "import json; "
    "from infrahub_sync.demo import assets; "
    "root = assets.bundled_root(); "
    "schema = assets.schema_files(); "
    "print(json.dumps({"
    "'root': str(root) if root else None, "
    "'schema_parents': sorted({str(path.parent) for path in schema}), "
    "'schema_count': len(schema), "
    "'package_bundled': bool(root) and (root / 'netbox' / 'package.yml').is_file(), "
    "'package_bytes': len(assets.netbox_package_text())}))"
)


def test_the_demo_prepare_command_answers_its_help(image_ref: str) -> None:
    result = run_in_image(image_ref, ["infrahub-sync", "demo", "prepare", "netbox", "--help"])

    assert result.returncode == 0, result.stderr


def test_the_demo_assets_resolve_from_the_bundled_path(image_ref: str) -> None:
    result = run_in_image(image_ref, ["python", "-c", RESOLVE_PROBE])

    assert result.returncode == 0, result.stderr
    resolved = json.loads(result.stdout.strip().splitlines()[-1])
    root = resolved["root"]
    assert root is not None, "the installed package has no bundled assets directory"
    assert root.startswith("/opt/infrahub-sync/venv/"), root
    assert resolved["schema_parents"] == [f"{root}/schema"]
    assert resolved["schema_count"] == EXPECTED_SCHEMA_FILES
    assert resolved["package_bundled"] is True
    assert resolved["package_bytes"] > 0
