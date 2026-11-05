"""Resolve any Compose file of the repository the way the contract tests read it.

The contract tests for the operator deployment, the development stack and the
preview stack read the model `docker compose config --format json` resolves a
file to, with chosen profiles named, rather than the file's YAML. This helper
returns that model and the image references its services run.

It runs Compose through `tests.compose.conftest.compose`, so its output passes
the same redaction boundary, and with only `PATH`, `HOME` and `DOCKER_CONFIG`
inherited from the caller's environment: a value a developer exported cannot
change what a contract test reads. It needs the Compose CLI and no Docker daemon,
and it skips the calling test when the CLI is not installed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from tests.compose.conftest import compose, compose_cli_version

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


@dataclass(frozen=True)
class ResolvedStack:
    """One Compose file, or a file and its overrides, as Compose resolves them."""

    model: dict[str, Any]

    @property
    def images(self) -> list[str]:
        """Return the distinct image references the resolved services run, sorted."""
        return image_references(self.model)


def image_references(model: Mapping[str, Any]) -> list[str]:
    """Return the distinct `image` values of a resolved model's services, sorted.

    A service that only declares `build` and no `image` names no reference and is
    left out.
    """
    services: Mapping[str, Mapping[str, Any]] = model.get("services", {})
    return sorted({definition["image"] for definition in services.values() if definition.get("image")})


def resolve_stack(
    files: Sequence[Path],
    *,
    profiles: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
    env_files: Sequence[Path] = (),
) -> ResolvedStack:
    """Return the model Compose resolves `files` to with `profiles` named.

    Skips the calling test when the Compose CLI is not installed, and fails it,
    quoting Compose's redacted refusal, when Compose refuses the files.
    """
    if compose_cli_version() is None:
        pytest.skip("docker compose is not installed; resolving a Compose file needs the CLI, not a daemon")
    argv: list[str] = []
    for profile in profiles:
        argv += ["--profile", profile]
    result = compose(
        [*argv, "config", "--format", "json"],
        environment=environment,
        files=files,
        env_files=env_files,
        inherit_environment=False,
    )
    if result.returncode != 0:
        pytest.fail(f"docker compose config refused {[str(path) for path in files]}: {result.stderr.strip()}")
    return ResolvedStack(model=json.loads(result.stdout))
