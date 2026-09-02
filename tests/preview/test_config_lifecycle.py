"""Configuration and discovery parity: every registry route through every interface.

The lifecycle smokes register once and move straight to a run. The registry has seven
resources and the service has two unauthenticated ones, and a client can interpret any of
them wrongly on its own — so this module drives all nine through the CLI, the typed
`SyncClient`, and raw HTTP against the same running service, and compares what each one
returns with what the service recorded.

Nothing here admits a run, so the module is not one of the Prefect surface's creators. It
registers its own configurations rather than reusing the lifecycle smokes': registration
is one of the rows, replay has to be observed on a key nothing else has used, and a second
version must be the second version of a configuration this module owns.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.client import SyncClient
from infrahub_sync.client.models import ConfigMutationRequest
from tests.preview.evidence import canary_leaks
from tests.preview.test_cli_client import ANSI, package_file, run_cli, run_cli_command
from tests.preview.test_service_api import authenticated_client, register_request, smoke_package

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.preview

REASON = "preview qualification: exercise the configuration registry"


def _key() -> str:
    """A fresh key per mutation, so a re-run never replays an earlier session's response."""
    return f"preview-config-{uuid.uuid4()}"


def revised_package(infrahub_url: str) -> dict[str, Any]:
    """The smoke package with one declared setting added, so its checksum differs.

    `create_version` answers 200 with the stored version for a package the configuration
    already holds and 201 for a new one, so proving both needs two packages that differ in
    declared content and nothing else. `verify_ssl` is a declared `infrahub` setting and is
    inert against the preview's plain-HTTP endpoint.
    """
    package = smoke_package(infrahub_url)
    package["configuration"]["destination"]["settings"]["verify_ssl"] = True
    return package


def test_the_cli_drives_the_registry_and_checks_compatibility_first(
    preview_env: dict[str, Any], tmp_path: Path
) -> None:
    """register, replay, version 200/201, list, show, versions, get-version, validate."""
    first = package_file(preview_env, tmp_path)
    second = tmp_path / "revised-package.json"
    second.write_text(json.dumps(revised_package(preview_env["urls"]["infrahub"])), encoding="utf-8")
    key = _key()

    registered = run_cli(preview_env, "configs", "register", str(first), "--reason", REASON, "--idempotency-key", key)
    config_id = registered["config_id"]
    replayed = run_cli(preview_env, "configs", "register", str(first), "--reason", REASON, "--idempotency-key", key)
    # A replayed key returns the stored response, so every field — the created timestamp
    # above all — is the first one, not a second registration that happens to look alike.
    assert replayed == registered

    identical = run_cli(preview_env, "configs", "version", config_id, str(first), "--reason", REASON)
    assert identical["created"] == "false"
    assert identical["registry_version"] == "1"
    assert identical["package_checksum"] == registered["package_checksum"]
    revised = run_cli(preview_env, "configs", "version", config_id, str(second), "--reason", REASON)
    assert revised["created"] == "true"
    assert revised["registry_version"] == "2"

    listed = run_cli_command(preview_env, "configs", "list")
    assert listed.returncode == 0, listed.stderr
    assert f"config_id: {config_id}" in ANSI.sub("", listed.stdout)

    assert run_cli(preview_env, "configs", "show", config_id)["config_id"] == config_id
    shown_version = run_cli(preview_env, "configs", "show", config_id, "--version", "1")
    assert shown_version["package_checksum"] == registered["package_checksum"]

    versions = run_cli_command(preview_env, "configs", "versions", config_id)
    assert versions.returncode == 0, versions.stderr
    # The replay must not have added a row: exactly the two versions created above.
    assert ANSI.sub("", versions.stdout).count("registry_version: ") == 2

    validated = run_cli(preview_env, "configs", "validate", config_id, "1")
    assert validated["total_findings"] == "0"
    assert validated["destination_schema_fingerprint"] == "<none>"

    # The client checks `/version` before any operation, so a base URL that is not the
    # Sync API is refused as an incompatible service rather than as a missing resource.
    misdirected = run_cli_command(
        preview_env,
        "--api-url",
        f"{preview_env['urls']['sync_api']}/not-the-sync-api",
        "configs",
        "list",
    )
    assert misdirected.returncode == 1
    assert "error: compatibility" in ANSI.sub("", misdirected.stderr)

    assert (
        canary_leaks(
            preview_env["infrahub_token"],
            {
                "configs register stdout": json.dumps(registered),
                "configs version stdout": json.dumps(revised),
                "configs list stdout": listed.stdout,
                "configs show --version stdout": json.dumps(shown_version),
                "configs versions stdout": versions.stdout,
                "configs validate stdout": json.dumps(validated),
                "compatibility refusal stderr": misdirected.stderr,
            },
        )
        == []
    )


def test_the_python_client_drives_the_registry_and_both_public_resources(preview_env: dict[str, Any]) -> None:
    """`/version`, `/status`, and all seven registry methods through the typed client."""
    package = smoke_package(preview_env["urls"]["infrahub"])
    revised = revised_package(preview_env["urls"]["infrahub"])
    key = _key()

    with SyncClient(preview_env["urls"]["sync_api"], preview_env["bearer_token"], timeout=30.0) as client:
        version = client.get_version()
        assert "v3-unstable" in version.api_versions

        status = client.get_status()
        assert status.service == "ready"
        # The preview starts a worker, so an absent one is a broken environment, not a
        # tolerable state: only the two live states are admitted here.
        assert status.worker.state in {"ready", "busy"}, status.worker

        request = ConfigMutationRequest(package=package, reason=REASON)
        registered = client.register_config(request, key)
        assert client.register_config(request, key) == registered
        config_id = registered.version.config_id

        identical = client.create_config_version(
            config_id, ConfigMutationRequest(package=package, reason=REASON), _key()
        )
        assert identical.created is False
        assert identical.version.registry_version == 1
        created = client.create_config_version(config_id, ConfigMutationRequest(package=revised, reason=REASON), _key())
        assert created.created is True
        assert created.version.registry_version == 2

        assert config_id in {summary.config_id for summary in client.list_configs()}
        assert client.get_config(config_id).config_id == config_id
        assert [entry.registry_version for entry in client.list_config_versions(config_id)] == [1, 2]
        fetched = client.get_config_version(config_id, 1)
        assert fetched == registered.version

        report = client.validate_config(config_id, 1)
        assert report.findings == ()
        assert report.total_findings == 0
        assert report.next_offset is None
        # No shipped interface offers the destination-schema opt-in, so the fingerprint the
        # report carries is always absent. Pinned here so its arrival is a deliberate change.
        assert report.destination_schema_fingerprint is None

    assert (
        canary_leaks(
            preview_env["infrahub_token"],
            {
                "get_version resource": version,
                "get_status resource": status,
                "register_config resource": registered,
                "create_config_version resource": created,
                "get_config_version resource": fetched,
                "validate_config resource": report,
            },
        )
        == []
    )


def test_raw_http_drives_the_registry_and_records_the_transcript(
    preview_env: dict[str, Any], evidence_dir: Path
) -> None:
    """Every registry and public route over the wire, with the exchange captured."""
    transcript = evidence_dir / "config-lifecycle-http.jsonl"
    package = smoke_package(preview_env["urls"]["infrahub"])
    body = register_request(preview_env["urls"]["infrahub"])
    key = {"Idempotency-Key": _key()}

    with authenticated_client(preview_env, transcript=transcript) as client:
        assert client.get("/version").status_code == 200
        status = client.get("/status")
        assert status.status_code == 200, status.text
        assert status.json()["service"] == "ready"

        registered = client.post("/configs", headers=key, json=body)
        assert registered.status_code == 201, registered.text
        config_id = registered.json()["version"]["config_id"]
        replayed = client.post("/configs", headers=key, json=body)
        assert replayed.status_code == 201, replayed.text
        assert replayed.json() == registered.json()

        identical = client.post(
            f"/configs/{config_id}/versions",
            headers={"Idempotency-Key": _key()},
            json={"package": package, "reason": REASON},
        )
        assert identical.status_code == 200, identical.text
        assert identical.json()["created"] is False
        revised = client.post(
            f"/configs/{config_id}/versions",
            headers={"Idempotency-Key": _key()},
            json={"package": revised_package(preview_env["urls"]["infrahub"]), "reason": REASON},
        )
        assert revised.status_code == 201, revised.text
        assert revised.json()["version"]["registry_version"] == 2

        assert config_id in {entry["config_id"] for entry in client.get("/configs").json()}
        assert client.get(f"/configs/{config_id}").json()["config_id"] == config_id
        versions = client.get(f"/configs/{config_id}/versions").json()
        assert [entry["registry_version"] for entry in versions] == [1, 2]
        assert client.get(f"/configs/{config_id}/versions/1").json() == registered.json()["version"]

        validated = client.post(f"/configs/{config_id}/versions/1/validate")
        assert validated.status_code == 200, validated.text
        assert validated.json()["findings"] == []
        assert validated.json()["destination_schema_fingerprint"] is None

    records = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    assert [(record["method"], record["path"], record["status"]) for record in records] == [
        ("GET", "/version", 200),
        ("GET", "/status", 200),
        ("POST", "/configs", 201),
        ("POST", "/configs", 201),
        ("POST", f"/configs/{config_id}/versions", 200),
        ("POST", f"/configs/{config_id}/versions", 201),
        ("GET", "/configs", 200),
        ("GET", f"/configs/{config_id}", 200),
        ("GET", f"/configs/{config_id}/versions", 200),
        ("GET", f"/configs/{config_id}/versions/1", 200),
        ("POST", f"/configs/{config_id}/versions/1/validate", 200),
    ]
    assert {record["request_headers"]["authorization"] for record in records} == {"<redacted>"}
    assert canary_leaks(preview_env["infrahub_token"], {str(transcript): transcript.read_text(encoding="utf-8")}) == []
