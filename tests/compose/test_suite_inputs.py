"""The inputs of the Docker-backed suites, checked without Docker (spec 027).

The `compose` and `docker` cases need a daemon and a built image, so a mistake in a package
they register or in the `.env` they write would show only where those suites run. These cases
run in the unit tier: each package the suites register parses as a package that names stored
credentials, and the `.env` of a suite deployment holds settings of stored credentials that
the Sync API and the worker accept.
"""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.configuration import parse_configuration_package
from tests.compose.lifecycle import SMOKE_CREDENTIAL, Deployment, operator_environment, smoke_package

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

REMOVED_PREFIX = "INFRAHUB_SYNC_CREDENTIAL_"
# The text of the package format that spec 027 removed.
REMOVED_FORMAT = ('"credentials"', '"provider"', "$credential", REMOVED_PREFIX)


def _external_store_package() -> dict[str, Any]:
    module = importlib.import_module("tests.compose.test_external_store")
    return module.netbox_package("https://netbox.invalid:9443", "https://infrahub.invalid:9443")


def _startup_package() -> dict[str, Any]:
    return importlib.import_module("tests.compose.test_configuration_free_startup").declared_package()


def _testcontainers_package() -> dict[str, Any]:
    pytest.importorskip("infrahub_testcontainers", reason="needs the `testcontainers` extra")
    return importlib.import_module("tests.compose.test_testcontainers_fixture").tag_package("suite-inputs")


# Each package a Docker-backed suite registers, with the stored credentials its blocks name.
SUITE_PACKAGES: dict[str, tuple[Callable[[], dict[str, Any]], dict[str, str | None]]] = {
    "smoke": (
        lambda: smoke_package("http://infrahub.invalid:8000"),
        {"source": SMOKE_CREDENTIAL, "destination": SMOKE_CREDENTIAL},
    ),
    "external-store": (_external_store_package, {"source": "netbox-token", "destination": SMOKE_CREDENTIAL}),
    "configuration-free-startup": (_startup_package, {"source": None, "destination": None}),
    "testcontainers": (_testcontainers_package, {"source": "infrahub-token", "destination": "infrahub-token"}),
}


@pytest.mark.parametrize("suite", sorted(SUITE_PACKAGES))
def test_a_suite_package_parses_and_names_its_stored_credentials(suite: str) -> None:
    """The Sync API of a suite deployment admits the package, once the named credentials are stored."""
    build, expected = SUITE_PACKAGES[suite]
    content = build()

    package = parse_configuration_package(content)

    named = {block: getattr(package.configuration, block).credential for block in ("source", "destination")}
    assert named == expected
    rendered = json.dumps(content)
    assert [text for text in REMOVED_FORMAT if text in rendered] == []


@pytest.mark.parametrize("suite", sorted(SUITE_PACKAGES))
def test_the_sync_api_registers_a_suite_package_once_its_credentials_are_stored(
    suite: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route the suites call admits the package: `POST /configs` answers 201 on the in-process Sync API."""
    pytest.importorskip("fastapi")
    pytest.importorskip("prefect")
    pytest.importorskip("cryptography")
    # The Sync service needs the `service` extra, which Python 3.10 does not install.
    pytest.importorskip("psycopg")
    account_api = importlib.import_module("tests.service.account_api")
    stored = importlib.import_module("tests.helpers.stored_credentials")
    build, expected = SUITE_PACKAGES[suite]
    content = build()
    api = account_api.build_account_api(tmp_path, monkeypatch, credential_key=stored.TEST_ENCRYPTION_KEY)
    stored.create_stored_credential_names(api.store, *sorted({name for name in expected.values() if name}))

    answer = api.client.post(
        "/configs",
        headers={**account_api.bearer(account_api.ADMIN_TOKEN), "Idempotency-Key": f"suite-inputs-{suite}"},
        json={"package": content, "reason": "suite inputs: register"},
    )

    code = answer.json().get("error", {}).get("code") if answer.status_code != 201 else None
    assert answer.status_code == 201, f"the Sync API refused the package of {suite}: {answer.status_code} {code}"


@pytest.fixture
def suite_canaries() -> dict[str, str]:
    """The values the `canaries` fixture of the suites gives a deployment, made the same way."""
    from tests.compose.lifecycle import canary, encryption_key

    kinds = ("administrator", "product", "prefect", "prefect_auth", "object_store", "admin_account", "principal")
    return {**{kind: canary(kind) for kind in (*kinds, "worker")}, "encryption_key": encryption_key()}


def test_a_suite_deployment_is_given_settings_of_stored_credentials_that_the_services_accept(
    tmp_path: Path, suite_canaries: dict[str, str]
) -> None:
    """The `.env` a suite writes holds a key and a worker token that pass both start-up checks.

    Compared privately: a failure names the setting or the rule, never a value.
    """
    settings = pytest.importorskip("infrahub_sync.service.settings", reason="needs the `service` extra")
    environment_file = operator_environment(tmp_path, image="infrahub-sync:suite-inputs", canaries=suite_canaries)
    deployment = Deployment(instance="suiteinputs", environment_file=environment_file)
    names = (
        "INFRAHUB_SYNC_ENCRYPTION_KEY",
        "INFRAHUB_SYNC_WORKER_TOKEN",
        "INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN",
    )
    environment = {name: deployment.setting(name) for name in names}

    api = settings.credential_settings(environment)
    worker = settings.worker_settings({**environment, "INFRAHUB_SYNC_API_URL": "http://sync-api:8000"})

    assert len(api.encryption_key) == settings.ENCRYPTION_KEY_BYTES
    key_is_the_generated_one = environment["INFRAHUB_SYNC_ENCRYPTION_KEY"] == suite_canaries["encryption_key"]
    token_is_the_generated_one = worker.token == suite_canaries["worker"]
    assert key_is_the_generated_one, "the `.env` does not hold the generated key"
    assert token_is_the_generated_one, "the `.env` does not hold the generated worker token"
    written = environment_file.read_text(encoding="utf-8")
    assert [line.split("=", 1)[0] for line in written.splitlines() if line.startswith(REMOVED_PREFIX)] == []
