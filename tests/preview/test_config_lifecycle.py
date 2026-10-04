"""Configuration and discovery parity: every configuration route through every interface.

Configurations live in Infrahub, so the Sync API no longer registers them: it refuses
`POST /configs` and `POST /configs/{id}/versions` with 410, validates a configuration's
document as it stands on a branch, and records a version the first time a run uses
content no version holds. A client can interpret any of those routes, and the two
unauthenticated ones, wrongly on its own — so this module drives them through the CLI,
the typed `SyncClient`, and raw HTTP against the same running service, and compares what
each one returns with what the service recorded.

Versions are recorded only by runs, so each test admits plan runs without a version.
It writes its own configuration under a fresh name rather than reusing the lifecycle
smokes': the version numbers it asserts must be those of a configuration it owns.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.client import APIError, SyncClient
from infrahub_sync.client.models import ConfigMutationRequest, CreateRunRequest, RunResource
from tasks.preview import SMOKE_BRANCH
from tests.infrahub_records import put_configuration
from tests.preview.evidence import canary_leaks
from tests.preview.test_cli_client import ANSI, package_file, run_cli, run_cli_command
from tests.preview.test_service_api import (
    authenticated_client,
    create_run_request,
    idempotency_headers,
    infrahub_client,
    put_smoke_configuration,
    register_request,
    smoke_package,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.preview

REASON = "preview qualification: exercise the configuration routes"
IN_INFRAHUB = "configurations-in-infrahub"


def revised_package(infrahub_url: str) -> dict[str, Any]:
    """The smoke package with one declared setting added, so its checksum differs.

    A run reuses the version that holds its configuration's current content and records
    a new one otherwise, so proving both needs two packages that differ in declared
    content and nothing else. `verify_ssl` is a declared `infrahub` setting and is inert
    against the preview's plain-HTTP endpoint.
    """
    package = smoke_package(infrahub_url)
    package["configuration"]["destination"]["settings"]["verify_ssl"] = True
    return package


def _plan_without_version(
    preview_env: dict[str, Any], config_id: str, artifacts: dict[str, object], artifact_name: str
) -> dict[str, str]:
    """`diff` with no `--version` and no wait: the admitted run names the version it recorded."""
    return run_cli(
        preview_env,
        "diff",
        "--config-id",
        config_id,
        "--branch",
        SMOKE_BRANCH,
        "--reason",
        REASON,
        "--idempotency-key",
        f"preview-config-{uuid.uuid4()}",
        "--no-wait",
        artifacts=artifacts,
        artifact_name=artifact_name,
    )


def test_the_cli_refuses_registration_and_drives_the_configuration_routes(
    preview_env: dict[str, Any], tmp_path: Path
) -> None:
    """register and version 410, validate on a branch, lazy versions, list, show, versions."""
    artifacts: dict[str, object] = {}
    package = package_file(preview_env, tmp_path)

    registered = run_cli_command(
        preview_env,
        "configs",
        "register",
        str(package),
        "--reason",
        REASON,
        artifacts=artifacts,
        artifact_name="config lifecycle register refusal",
    )
    assert registered.returncode == 1, registered.stdout
    assert IN_INFRAHUB in ANSI.sub("", registered.stderr), registered.stderr

    config_id = put_smoke_configuration(preview_env, "preview-config-cli")
    versioned = run_cli_command(
        preview_env,
        "configs",
        "version",
        config_id,
        str(package),
        "--reason",
        REASON,
        artifacts=artifacts,
        artifact_name="config lifecycle version refusal",
    )
    assert versioned.returncode == 1, versioned.stdout
    assert IN_INFRAHUB in ANSI.sub("", versioned.stderr), versioned.stderr

    validated = run_cli(
        preview_env,
        "configs",
        "validate",
        config_id,
        artifacts=artifacts,
        artifact_name="config lifecycle validate document",
    )
    assert validated["total_findings"] == "0", validated
    assert validated["config_id"] == config_id

    first = _plan_without_version(preview_env, config_id, artifacts, "config lifecycle first plan")
    assert first["registry_version"] == "1", first
    assert first["package_checksum"] == validated["checksum"]
    again = _plan_without_version(preview_env, config_id, artifacts, "config lifecycle unchanged plan")
    # Unchanged content reuses the version that already holds it.
    assert again["registry_version"] == "1", again

    put_configuration(infrahub_client(preview_env), config_id, revised_package(preview_env["urls"]["infrahub"]))
    revised = _plan_without_version(preview_env, config_id, artifacts, "config lifecycle revised plan")
    assert revised["registry_version"] == "2", revised
    assert revised["package_checksum"] != first["package_checksum"]

    listed = run_cli_command(
        preview_env,
        "configs",
        "list",
        artifacts=artifacts,
        artifact_name="config lifecycle list",
    )
    assert listed.returncode == 0, listed.stderr
    assert f"config_id: {config_id}" in ANSI.sub("", listed.stdout)

    shown = run_cli(
        preview_env,
        "configs",
        "show",
        config_id,
        artifacts=artifacts,
        artifact_name="config lifecycle show",
    )
    assert shown["config_id"] == config_id
    shown_version = run_cli(
        preview_env,
        "configs",
        "show",
        config_id,
        "--version",
        "1",
        artifacts=artifacts,
        artifact_name="config lifecycle show version",
    )
    assert shown_version["package_checksum"] == first["package_checksum"]

    versions = run_cli_command(
        preview_env,
        "configs",
        "versions",
        config_id,
        artifacts=artifacts,
        artifact_name="config lifecycle versions",
    )
    assert versions.returncode == 0, versions.stderr
    # The unchanged run must not have added a row: exactly the two contents planned above.
    assert ANSI.sub("", versions.stdout).count("registry_version: ") == 2

    validated_version = run_cli(
        preview_env,
        "configs",
        "validate",
        config_id,
        "--version",
        "1",
        artifacts=artifacts,
        artifact_name="config lifecycle validate version",
    )
    assert validated_version["total_findings"] == "0"
    assert validated_version["destination_schema_fingerprint"] == "<none>"

    # The client checks `/version` before any operation, so a base URL that is not the
    # Sync API is refused as an incompatible service rather than as a missing resource.
    misdirected = run_cli_command(
        preview_env,
        "--api-url",
        f"{preview_env['urls']['sync_api']}/not-the-sync-api",
        "configs",
        "list",
        artifacts=artifacts,
        artifact_name="config lifecycle compatibility refusal",
    )
    assert misdirected.returncode == 1
    assert "error: compatibility" in ANSI.sub("", misdirected.stderr)

    assert canary_leaks(preview_env["infrahub_token"], artifacts) == []


def test_the_python_client_drives_the_configuration_routes_and_both_public_resources(
    preview_env: dict[str, Any],
) -> None:
    """`/version`, `/status`, the 410 refusals, branch validation, and the registry reads."""
    package = smoke_package(preview_env["urls"]["infrahub"])
    config_id = put_smoke_configuration(preview_env, "preview-config-python")

    def plan(client: SyncClient) -> RunResource:
        return client.plan(
            CreateRunRequest(operation="plan", config_id=config_id, branch=SMOKE_BRANCH, reason=REASON),
            idempotency_headers("preview-config")["Idempotency-Key"],
        )

    with SyncClient(preview_env["urls"]["sync_api"], preview_env["bearer_token"], timeout=30.0) as client:
        version = client.get_version()
        assert "v3-unstable" in version.api_versions

        status = client.get_status()
        assert status.service == "ready"
        # The preview starts a worker, so an absent one is a broken environment, not a
        # tolerable state: only the two live states are admitted here.
        assert status.worker.state in {"ready", "busy"}, status.worker

        request = ConfigMutationRequest(package=package, reason=REASON)
        with pytest.raises(APIError) as refused_register:
            client.register_config(request, idempotency_headers("preview-config")["Idempotency-Key"])
        assert refused_register.value.code == IN_INFRAHUB
        with pytest.raises(APIError) as refused_version:
            client.create_config_version(config_id, request, idempotency_headers("preview-config")["Idempotency-Key"])
        assert refused_version.value.code == IN_INFRAHUB

        checked = client.validate_config_on_branch(config_id)
        assert checked.findings == ()
        assert checked.branch is None

        first = plan(client)
        assert first.run.registry_version == 1, first.run
        assert first.run.package_checksum == checked.checksum
        again = plan(client)
        assert again.run.registry_version == 1, again.run
        put_configuration(infrahub_client(preview_env), config_id, revised_package(preview_env["urls"]["infrahub"]))
        revised = plan(client)
        assert revised.run.registry_version == 2, revised.run

        listed = client.list_configs()
        assert config_id in {summary.config_id for summary in listed}
        shown = client.get_config(config_id)
        assert shown.config_id == config_id
        versions = client.list_config_versions(config_id)
        assert [entry.registry_version for entry in versions] == [1, 2]
        fetched = client.get_config_version(config_id, 1)
        assert fetched.package_checksum == first.run.package_checksum

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
                "validate_config_on_branch resource": checked,
                "first plan resource": first,
                "unchanged plan resource": again,
                "revised plan resource": revised,
                "list_configs resource": listed,
                "get_config resource": shown,
                "list_config_versions resource": versions,
                "get_config_version resource": fetched,
                "validate_config resource": report,
            },
        )
        == []
    )


def test_raw_http_drives_the_configuration_routes_and_records_the_transcript(
    preview_env: dict[str, Any], evidence_dir: Path
) -> None:
    """Every configuration and public route over the wire, with the exchange captured."""
    transcript = evidence_dir / "config-lifecycle-http.jsonl"
    package = smoke_package(preview_env["urls"]["infrahub"])
    config_id = put_smoke_configuration(preview_env, "preview-config-http")

    with authenticated_client(preview_env, transcript=transcript) as client:
        assert client.get("/version").status_code == 200
        status = client.get("/status")
        assert status.status_code == 200, status.text
        assert status.json()["service"] == "ready"

        registered = client.post(
            "/configs",
            headers=idempotency_headers("preview-config"),
            json=register_request(preview_env["urls"]["infrahub"]),
        )
        assert registered.status_code == 410, registered.text
        assert registered.json()["error"]["code"] == IN_INFRAHUB
        versioned = client.post(
            f"/configs/{config_id}/versions",
            headers=idempotency_headers("preview-config"),
            json={"package": package, "reason": REASON},
        )
        assert versioned.status_code == 410, versioned.text
        assert versioned.json()["error"]["code"] == IN_INFRAHUB

        checked = client.post(f"/configs/{config_id}/validate")
        assert checked.status_code == 200, checked.text
        assert checked.json()["findings"] == []

        recorded = []
        for revise in (False, False, True):
            if revise:
                put_configuration(
                    infrahub_client(preview_env), config_id, revised_package(preview_env["urls"]["infrahub"])
                )
            created = client.post(
                "/runs", headers=idempotency_headers("preview-config"), json=create_run_request(config_id)
            )
            assert created.status_code == 202, created.text
            recorded.append(created.json()["run"]["registry_version"])
        assert recorded == [1, 1, 2]

        assert config_id in {entry["config_id"] for entry in client.get("/configs").json()}
        assert client.get(f"/configs/{config_id}").json()["config_id"] == config_id
        versions = client.get(f"/configs/{config_id}/versions").json()
        assert [entry["registry_version"] for entry in versions] == [1, 2]
        first_version = client.get(f"/configs/{config_id}/versions/1").json()
        assert first_version["package_checksum"] == checked.json()["checksum"]

        validated = client.post(f"/configs/{config_id}/versions/1/validate")
        assert validated.status_code == 200, validated.text
        assert validated.json()["findings"] == []
        assert validated.json()["destination_schema_fingerprint"] is None

    records = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    assert [(record["method"], record["path"], record["status"]) for record in records] == [
        ("GET", "/version", 200),
        ("GET", "/status", 200),
        ("POST", "/configs", 410),
        ("POST", f"/configs/{config_id}/versions", 410),
        ("POST", f"/configs/{config_id}/validate", 200),
        ("POST", "/runs", 202),
        ("POST", "/runs", 202),
        ("POST", "/runs", 202),
        ("GET", "/configs", 200),
        ("GET", f"/configs/{config_id}", 200),
        ("GET", f"/configs/{config_id}/versions", 200),
        ("GET", f"/configs/{config_id}/versions/1", 200),
        ("POST", f"/configs/{config_id}/versions/1/validate", 200),
    ]
    assert {record["request_headers"]["x-infrahub-key"] for record in records} == {"<redacted>"}
    assert canary_leaks(preview_env["infrahub_token"], {str(transcript): transcript.read_text(encoding="utf-8")}) == []
