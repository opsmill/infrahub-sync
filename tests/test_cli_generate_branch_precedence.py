"""Verify that an explicit generate branch wins over the environment default."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import yaml
from typer.testing import CliRunner

from infrahub_sync.cli import app

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_cli_branch_overrides_environment_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use --branch when both it and INFRAHUB_DEFAULT_BRANCH are set."""
    for name in [key for key in os.environ if key.startswith("INFRAHUB_")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("INFRAHUB_DEFAULT_BRANCH", "environment-branch")

    config_path = tmp_path / "config.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "name": "test-sync",
                "source": {"name": "netbox"},
                "destination": {"name": "infrahub", "settings": {"url": "http://infrahub.example.com"}},
                "schema_mapping": [],
            }
        ),
        encoding="UTF-8",
    )
    captured: dict[str, Any] = {}

    class FakeSchema:
        @staticmethod
        def all() -> dict[str, Any]:
            return {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.schema = FakeSchema()

    monkeypatch.setattr("infrahub_sync.cli.InfrahubClientSync", FakeClient)
    monkeypatch.setattr("infrahub_sync.cli.render_adapter", lambda **_kwargs: [])

    result = CliRunner().invoke(app, ["generate", "--config-file", str(config_path), "--branch", "cli-branch"])

    assert result.exit_code == 0, f"exit={result.exit_code} exception={result.exception!r}\n{result.output}"
    assert captured["config"].default_branch == "cli-branch"
