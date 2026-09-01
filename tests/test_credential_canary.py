"""Non-CLI credential-canary checks for saved plan data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from infrahub_sync.plan.config_version import default_config_version
from infrahub_sync.plan.review import read_saved_plan
from infrahub_sync.plan.writer import PLAN_DIR_NAME
from infrahub_sync.utils import get_instance
from tests.plan.artifact_fixtures import RUN_ID
from tests.test_potenda_plan_artifact import (
    KINDS,
    _FakeAdapter,
    _FakeRecord,
    build_potenda,
    qualified_mapping,
    run_plan,
)

CANARY = "c4n4ry-7f3e9a1d2b48605-do-not-leak"
SYNC_NAME = "canary-sync"


def _write_configuration(projects_root: Path) -> Path:
    project = projects_root / "canary-project"
    project.mkdir(parents=True)
    settings = {"url": "https://example.invalid", "token": CANARY, "password": CANARY}
    document = {
        "name": SYNC_NAME,
        "source": {"name": "netbox", "settings": settings},
        "destination": {"name": "infrahub", "settings": settings},
        "order": list(KINDS),
        "schema_mapping": [entry.model_dump() for entry in qualified_mapping()],
    }
    (project / "config.yml").write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return project


def _plan_surfaces(projects_root: Path, *, plant_payload: bool) -> tuple[str, str]:
    project = _write_configuration(projects_root)
    instance = get_instance(name=SYNC_NAME, directory=str(project.parent))
    assert instance is not None
    assert instance.source.settings is not None
    assert CANARY in instance.source.settings.values()

    description = CANARY if plant_payload else "production"
    source = _FakeAdapter(
        "source",
        [
            _FakeRecord("BuiltinTag", {"name": "prod"}, {"description": description, "slug": "prod"}),
            _FakeRecord("LocationSite", {"name": "hq"}, {}),
            _FakeRecord("LocationRack", {"name": "r1", "site": "hq"}, {}),
            _FakeRecord("DcimDevice", {"name": "d1", "rack": "r1__hq"}, {"model": "c9300", "tags": []}),
        ],
    )
    destination = _FakeAdapter("destination", [])
    potenda = build_potenda(
        config=instance,
        source=source,
        destination=destination,
        run_id=RUN_ID,
        top_level=list(KINDS),
    )
    run_plan(potenda)

    assert potenda.run_dir is not None
    plan_directory = potenda.run_dir / PLAN_DIR_NAME
    artifact_text = "".join(
        path.read_bytes().decode("utf-8", errors="replace")
        for path in sorted(plan_directory.rglob("*"))
        if path.is_file()
    )
    plan = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, config=instance)
    reader_text = json.dumps(
        {
            "manifest": plan.manifest.model_dump(mode="json"),
            "summary": plan.summary().model_dump(mode="json"),
            "operations": [operation.model_dump(mode="json") for operation in plan.operations()],
        },
        sort_keys=True,
    )
    return artifact_text, reader_text


@pytest.fixture(name="projects_root")
def fixture_projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "projects"
    root.mkdir()
    return root


def test_settings_credentials_reach_neither_artifact_nor_reader_data(projects_root: Path) -> None:
    artifact_text, reader_text = _plan_surfaces(projects_root, plant_payload=False)

    assert CANARY not in artifact_text
    assert CANARY not in reader_text


def test_canary_scans_detect_a_planted_plan_value(projects_root: Path) -> None:
    artifact_text, reader_text = _plan_surfaces(projects_root, plant_payload=True)

    assert CANARY in artifact_text
    assert CANARY in reader_text


def test_configuration_digest_binds_settings_without_disclosing_them(projects_root: Path) -> None:
    _write_configuration(projects_root)
    instance = get_instance(name=SYNC_NAME, directory=str(projects_root))
    assert instance is not None
    changed = instance.model_copy(deep=True)
    assert changed.source.settings is not None
    changed.source.settings["token"] = "another-synthetic-value"  # noqa: S105

    digest = default_config_version(instance)

    assert CANARY not in digest
    assert digest != default_config_version(changed)
