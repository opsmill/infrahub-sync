"""Every development and preview stack publishes on the host's loopback address only.

These stacks ship published development credentials -- a PostgreSQL superuser, the
MinIO root keys, an Infrahub admin token, an unauthenticated Prefect -- so a port
reachable from another machine hands those credentials to it. Each publication is
read from the Compose files as shipped, with each `${NAME}` resolved the way Compose
resolves it for the tasks: from the shipped environment file first, then from the
file's own default. A long-syntax entry must name `127.0.0.1` as its `host_ip`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from tasks.preview import preview_urls

REPO_ROOT = Path(__file__).resolve().parents[2]
DEVELOPMENT = REPO_ROOT / "development"
LOOPBACK = "127.0.0.1"
# Each stack, and the environment file its tasks hand Compose.
STACKS = {
    DEVELOPMENT / "docker-compose.dev.yml": None,
    DEVELOPMENT / "docker-compose.infrahub.yml": DEVELOPMENT / "preview.env",
    DEVELOPMENT / "docker-compose.preview.yml": DEVELOPMENT / "preview.env",
    DEVELOPMENT / "netbox" / "docker-compose.netbox.yml": DEVELOPMENT / "netbox" / "netbox.env",
}
INTERPOLATION = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::?-(?P<default>[^}]*))?\}")


class _ComposeLoader(yaml.SafeLoader):
    """Read Compose's merge tags (`!override`, `!reset`) as the plain values they carry."""


def _tagged(loader: yaml.SafeLoader, _suffix: str, node: yaml.Node) -> Any:  # noqa: ANN401 - any YAML value.
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    assert isinstance(node, yaml.ScalarNode)
    return loader.construct_scalar(node)


_ComposeLoader.add_multi_constructor("!", _tagged)


def _environment(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def _resolve(text: str, environment: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        if environment.get(name):
            return environment[name]
        return match.group("default") or ""

    return INTERPOLATION.sub(replace, text)


def _publications() -> list[tuple[str, str, object]]:
    found: list[tuple[str, str, object]] = []
    for compose_file, env_file in STACKS.items():
        model = yaml.load(compose_file.read_text(encoding="utf-8"), Loader=_ComposeLoader)  # noqa: S506 - SafeLoader subclass.
        environment = _environment(env_file)
        for name, service in (model.get("services") or {}).items():
            for entry in (service or {}).get("ports") or []:
                label = f"{compose_file.relative_to(REPO_ROOT)}:{name}"
                if isinstance(entry, dict):
                    found.append((label, _resolve(str(entry.get("host_ip", "")), environment), entry))
                else:
                    resolved = _resolve(str(entry), environment)
                    parts = resolved.split(":")
                    found.append((label, parts[0] if len(parts) == 3 else "", entry))
    return found


def test_every_stack_has_publications_to_check() -> None:
    """A parser that found nothing would pass the check below for every file."""
    labels = {label.split(":", 1)[0] for label, _, _ in _publications()}

    assert labels == {str(path.relative_to(REPO_ROOT)) for path in STACKS}


@pytest.mark.parametrize(("label", "host_ip", "entry"), _publications(), ids=str)
def test_every_publication_binds_to_loopback(label: str, host_ip: str, entry: object) -> None:
    assert host_ip == LOOPBACK, f"{label} publishes {entry!r} on {host_ip or 'every interface'}"


def test_the_one_adjustable_address_ships_as_loopback() -> None:
    """Infrahub's address can change for a Linux worker container; its shipped value cannot be wider."""
    assert _environment(DEVELOPMENT / "preview.env")["PREVIEW_INFRAHUB_BIND_ADDRESS"] == LOOPBACK


@pytest.mark.parametrize(
    ("bind_address", "expected"),
    [
        ("127.0.0.1", "http://localhost:8080"),
        ("0.0.0.0", "http://localhost:8080"),  # noqa: S104 - a wildcard publication, which loopback answers.
        ("", "http://localhost:8080"),
        ("172.17.0.1", "http://172.17.0.1:8080"),
        ("fd00::1", "http://[fd00::1]:8080"),
    ],
)
def test_the_preview_tasks_reach_infrahub_where_it_is_published(bind_address: str, expected: str) -> None:
    """A bridge-gateway publication answers on that address only, so `localhost` is not used for it."""
    values = {
        "PREVIEW_INFRAHUB_BIND_ADDRESS": bind_address,
        "PREVIEW_INFRAHUB_PORT": "8080",
        "PREVIEW_PREFECT_PORT": "4300",
        "PREVIEW_SYNC_API_PORT": "8100",
    }

    assert preview_urls(values)["infrahub"] == expected
