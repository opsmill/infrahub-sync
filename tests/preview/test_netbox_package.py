"""The shipped `from-netbox` package against the running service: put it in Infrahub, validate.

The preview stack has no NetBox source, no schema library and no NetBox token, so this
module never plans or applies this package — and since only a run records a version, it
validates each configuration's document as it stands on the default branch. What it does
prove is the part that needs no source: the package a reader actually copies out of
`examples/` is accepted as a `SyncConfiguration` document verbatim, resolves its
credentials by reference rather than by value, and reports the same findings whichever
of the three interfaces asked.

The last row is the one the package's shape rests on. Default validation judges declared
content only — no schema read, no network — so the source URL is pointed at a listener
that answers anything and records what it was asked, and the recording has to stay empty.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import yaml

from infrahub_sync.client import SyncClient
from tasks.preview import REPO_ROOT
from tests.preview.evidence import canary_leaks
from tests.preview.test_cli_client import ANSI, run_cli, run_cli_command
from tests.preview.test_service_api import authenticated_client, put_smoke_configuration

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.preview

PACKAGE_FILE = REPO_ROOT / "examples" / "netbox_to_infrahub" / "package.yml"
# A destination kind the shipped package deliberately does not map, so declaring it omitted
# is accepted and reports exactly one warning. This is the copy that separates an interface
# which renders findings from one that renders none because there were none to render.
OMITTED_KIND = "DcimCable"
OMISSION_REASON = "NetBox cable terminations have no destination mapping yet"

_sink_requests: list[str] = []


class _SinkHandler(BaseHTTPRequestHandler):
    """Answer anything and record it. A default validation must never reach this."""

    def _record(self) -> None:
        _sink_requests.append(f"{self.command} {self.path}")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    do_GET = _record  # noqa: N815 -- the handler's fixed method-dispatch names
    do_POST = _record  # noqa: N815
    do_HEAD = _record  # noqa: N815

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, ANN401 -- the base signature
        """Stay silent: the recorded request list is this listener's only output."""


@pytest.fixture
def source_sink() -> Iterator[str]:
    """A live HTTP listener, and the URL a package can name as its source."""
    _sink_requests.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SinkHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def shipped_package() -> dict[str, Any]:
    """The package exactly as `examples/netbox_to_infrahub/package.yml` declares it."""
    return dict(yaml.safe_load(PACKAGE_FILE.read_text(encoding="utf-8")))


def _with_omission() -> dict[str, Any]:
    """The shipped package plus one declared omission, which is one warning finding."""
    return {**shipped_package(), "omissions": [{"kind": OMITTED_KIND, "reason": OMISSION_REASON}]}


def _rendered(code: str, severity: str, location: str, message: str) -> str:
    """One finding in the CLI's own line format, so the three renderings compare as bytes."""
    return f"finding: code={code} severity={severity} location={location} message={message}"


def _cli_findings(output: str) -> list[str]:
    return [line for line in ANSI.sub("", output).splitlines() if line.startswith("finding: ")]


def _http_findings(payload: dict[str, Any]) -> list[str]:
    return [
        _rendered(finding["code"], finding["severity"], finding["location"], finding["message"])
        for finding in payload["findings"]
    ]


def test_the_shipped_netbox_package_is_a_configuration_document_through_every_interface(
    preview_env: dict[str, Any], evidence_dir: Path
) -> None:
    """The file on disk is accepted verbatim, and checksums the same everywhere, YAML or JSON."""
    package = shipped_package()
    # The credential references are what makes the document admissible at all: a package
    # carrying a literal token is refused before any version is recorded.
    assert package["configuration"]["source"]["settings"]["token"] == {"$credential": "netbox-token"}
    assert package["configuration"]["destination"]["settings"]["token"] == {"$credential": "infrahub-token"}
    assert package["credentials"] == {
        "netbox-token": {"provider": "env", "identifier": "INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN"},
        "infrahub-token": {"provider": "env", "identifier": "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN"},
    }

    # The YAML text exactly as the reader copies it, and the same content as JSON: an
    # operator may paste either into the document attribute.
    from_yaml = put_smoke_configuration(preview_env, "preview-netbox-yaml", PACKAGE_FILE.read_text(encoding="utf-8"))
    from_json = put_smoke_configuration(preview_env, "preview-netbox-json", json.dumps(package))

    artifacts: dict[str, object] = {}
    from_cli = run_cli(
        preview_env,
        "configs",
        "validate",
        from_yaml,
        artifacts=artifacts,
        artifact_name="NetBox package validate",
    )
    assert from_cli["total_findings"] == "0", from_cli

    with SyncClient(preview_env["urls"]["sync_api"], preview_env["bearer_token"], timeout=30.0) as client:
        from_python = client.validate_config_on_branch(from_json)
        assert from_python.findings == ()

    transcript = evidence_dir / "netbox-package-document-http.jsonl"
    with authenticated_client(preview_env, transcript=transcript) as api:
        response = api.post(f"/configs/{from_yaml}/validate")
        assert response.status_code == 200, response.text
        from_http = response.json()

    # One declared content in two configurations and two notations, so the checksum a
    # version of it would carry has to be one value.
    assert from_cli["checksum"] == from_python.checksum == from_http["checksum"]
    captured = transcript.read_text(encoding="utf-8")
    exchanges = [
        (record["method"], record["path"], record["status"]) for record in map(json.loads, captured.splitlines())
    ]
    assert exchanges == [("POST", f"/configs/{from_yaml}/validate", 200)]
    artifacts.update({"NetBox Python validation resource": from_python, str(transcript): captured})
    assert canary_leaks(preview_env["infrahub_token"], artifacts) == []


def test_the_netbox_package_validates_identically_through_every_interface(
    preview_env: dict[str, Any], evidence_dir: Path
) -> None:
    """Findings are byte-identical across the interfaces, both when empty and when not."""
    transcript = evidence_dir / "netbox-package-validate-http.jsonl"
    artifacts: dict[str, object] = {}
    python_reports: list[object] = []
    http_bodies: list[bytes] = []
    clean = put_smoke_configuration(preview_env, "preview-netbox-clean", shipped_package())
    warned = put_smoke_configuration(preview_env, "preview-netbox-warned", _with_omission())
    expected = {
        clean: [],
        warned: [
            _rendered(
                "intentional-omission",
                "warning",
                "/omissions/0",
                f"declared content is intentionally omitted from synchronization: {OMISSION_REASON}",
            )
        ],
    }
    with authenticated_client(preview_env, transcript=transcript) as api:
        for index, (config_id, findings) in enumerate(expected.items(), start=1):
            cli = run_cli_command(
                preview_env,
                "configs",
                "validate",
                config_id,
                artifacts=artifacts,
                artifact_name=f"NetBox package validate {index}",
            )
            assert cli.returncode == 0, cli.stderr

            with SyncClient(preview_env["urls"]["sync_api"], preview_env["bearer_token"], timeout=30.0) as client:
                report = client.validate_config_on_branch(config_id)
            python_reports.append(report)
            body = api.post(f"/configs/{config_id}/validate")
            assert body.status_code == 200, body.text
            http_bodies.append(body.content)

            python_findings = [
                _rendered(item.code, item.severity, item.location, item.message) for item in report.findings
            ]
            assert _cli_findings(cli.stdout) == python_findings == _http_findings(body.json()) == findings
            assert report.total_findings == len(findings)
            assert body.json()["total_findings"] == len(findings)

    captured = transcript.read_text(encoding="utf-8")
    exchanges = [
        (record["method"], record["path"], record["status"]) for record in map(json.loads, captured.splitlines())
    ]
    assert exchanges == [
        ("POST", f"/configs/{clean}/validate", 200),
        ("POST", f"/configs/{warned}/validate", 200),
    ]
    artifacts[str(transcript)] = captured
    artifacts.update(
        {f"NetBox Python validation resource {index}": report for index, report in enumerate(python_reports)}
    )
    artifacts.update({f"NetBox HTTP validation body {index}": body for index, body in enumerate(http_bodies)})
    assert canary_leaks(preview_env["infrahub_token"], artifacts) == []


def test_validating_the_netbox_package_reads_no_source(
    preview_env: dict[str, Any], source_sink: str, evidence_dir: Path
) -> None:
    """Default validation judges declared content, so the source endpoint is never called."""
    package = shipped_package()
    package["configuration"]["source"]["settings"]["url"] = source_sink
    # Prove the listener records before relying on it recording nothing; an empty log from
    # a listener nothing could have reached would be evidence of the harness, not the route.
    assert httpx.get(source_sink, timeout=5).status_code == 200
    assert _sink_requests == ["GET /"]
    _sink_requests.clear()
    config_id = put_smoke_configuration(preview_env, "preview-netbox-sink", package)

    transcript = evidence_dir / "netbox-package-zero-source-http.jsonl"
    with authenticated_client(preview_env, transcript=transcript) as api:
        report = api.post(f"/configs/{config_id}/validate")
        assert report.status_code == 200, report.text
        assert report.json()["findings"] == [], report.text

    assert _sink_requests == []
    captured = transcript.read_text(encoding="utf-8")
    exchanges = [
        (record["method"], record["path"], record["status"]) for record in map(json.loads, captured.splitlines())
    ]
    assert exchanges == [("POST", f"/configs/{config_id}/validate", 200)]
    assert (
        canary_leaks(
            preview_env["infrahub_token"],
            {str(transcript): captured, "NetBox zero-source validation body": report.content},
        )
        == []
    )
