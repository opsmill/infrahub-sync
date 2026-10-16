"""A deployment trusts a team's own CA for a source only through `INFRAHUB_SYNC_CA_CERTIFICATES`.

US5, FR-024, SC-009. The source is a small HTTPS server that answers the one
endpoint a `genericrestapi` package reads, started from the Sync image outside
the deployment, with a server certificate from a test CA. `sync-worker` reaches
it on a network it joins through a test-only override; no other service joins.
The destination is the suite's Infrahub fixture, over HTTP.

One deployment walks the cases in order. Started without the setting, every
service starts as before and a plan run fails, because the worker cannot verify
the source's certificate; the run's cause has the failure type
`tls_certificate_rejected` (spec 009).
Started again with the setting, the same package plans. Then a CA directory that
also holds a private key stops `sync-bootstrap`, `sync-api` and `sync-worker`,
and their output names the file and holds none of its content.
"""

from __future__ import annotations

import datetime
import secrets
import uuid
from typing import TYPE_CHECKING, Any, cast

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from tasks.preview import SMOKE_BRANCH, SMOKE_KIND
from tests.compose.conftest import FIXTURE_INFRAHUB_PORT
from tests.compose.lifecycle import (
    FIXTURE_OVERRIDE,
    RUN_TIMEOUT_SECONDS,
    Deployment,
    api_client,
    container_reachable_host,
    docker,
    idempotency,
    operator_environment,
    register,
    wait_for,
    worker_state,
)
from tests.compose.redaction import SECRETS

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import httpx

pytestmark = pytest.mark.compose

API_PORT = 8076
SOURCE_PORT = 8443
READY_SECONDS = 420
SOURCE_READY_SECONDS = 120
CA_SERVICES = ("sync-bootstrap", "sync-api", "sync-worker")
CA_SETTING = "INFRAHUB_SYNC_CA_CERTIFICATES"
SOURCE_TOKEN_SETTING = "INFRAHUB_SYNC_CREDENTIAL_GENERICRESTAPI_TOKEN"  # noqa: S105 -- a variable name, not a value
INSTALLED = "custom CA bundle installed"
NO_CERTIFICATE = "custom CA directory holds no certificate file"
KEY_FILE = "leaked-server-key.pem"

# The source: one endpoint, `/api/v0/devices`, answering one device in the
# shape `genericrestapi` reads. It checks the token the package declares, so a
# request without it is refused like a real API would refuse it.
SOURCE_SERVER = """
import http.server, json, ssl, sys

name, value, token = sys.argv[1], sys.argv[2], sys.argv[3]
body = json.dumps({"devices": [{"id": "1", "name": name, "type": value}]}).encode()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0].rstrip("/") != "/api/v0/devices":
            self.send_response(404)
            self.end_headers()
            return
        if self.headers.get("Authorization") != "Token " + token:
            self.send_response(401)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("0.0.0.0", 8443), Handler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain("/certs/server.crt", "/certs/server.key")
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
"""
LISTENING = "import socket; socket.create_connection(('127.0.0.1', 8443), 2).close()"


class HttpsSource:
    """One HTTPS source outside the deployment, and the CA directories a deployment may name."""

    def __init__(self, directory: Path) -> None:
        suffix = secrets.token_hex(6)
        self.container = f"infrahub-sync-ca-source-{suffix}"
        self.network = f"infrahub-sync-ca-source-{suffix}"
        self.host = f"source-{suffix}.ca.test"
        self.url = f"https://{self.host}:{SOURCE_PORT}"
        self.token = f"ca-source-token-{secrets.token_hex(12)}"
        SECRETS.register(self.token)
        # The value the source reports, so a plan has one difference to find.
        self.device_type = f"ca-suite-{uuid.uuid4().hex[:12]}"
        # Given to the deployment: the CA certificate only.
        self.ca_directory = directory / "ca"
        # Given to the deployment in the refusal case: the CA and a private key.
        self.ca_with_key_directory = directory / "ca-with-key"
        # The server's own certificate and key: never given to the deployment.
        self.certificate_directory = directory / "source-certificates"
        self.key_pem = b""


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def write_certificates(source: HttpsSource) -> None:
    """Write a test CA, and a server certificate it signs for the source's host name."""
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = _name("infrahub-sync custom CA suite")
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    server_key = ec.generate_private_key(ec.SECP256R1())
    server = (
        x509.CertificateBuilder()
        .subject_name(_name(source.host))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(source.host)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    ca_pem = ca.public_bytes(serialization.Encoding.PEM)
    source.key_pem = server_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    files = {
        source.ca_directory / "team-ca.crt": ca_pem,
        source.ca_with_key_directory / "team-ca.crt": ca_pem,
        source.ca_with_key_directory / KEY_FILE: source.key_pem,
        source.certificate_directory / "server.crt": server.public_bytes(serialization.Encoding.PEM),
        source.certificate_directory / "server.key": source.key_pem,
    }
    for directory in (source.ca_directory, source.ca_with_key_directory, source.certificate_directory):
        directory.mkdir(parents=True)
        directory.chmod(0o755)
    for path, content in files.items():
        path.write_bytes(content)
        # The services and the source run as non-root users and read these through bind mounts.
        path.chmod(0o644)


def start_source(source: HttpsSource, image: str) -> None:
    """Start the source from the Sync image on its own network, and wait until it listens."""
    created = docker(["network", "create", source.network])
    assert created.returncode == 0, created.output
    started = docker(
        [
            "run",
            "--detach",
            "--name",
            source.container,
            "--network",
            source.network,
            "--network-alias",
            source.host,
            "--volume",
            f"{source.certificate_directory}:/certs:ro",
            "--entrypoint",
            "python",
            image,
            "-c",
            SOURCE_SERVER,
            "core01",
            source.device_type,
            source.token,
        ]
    )
    assert started.returncode == 0, started.output
    wait_for(
        "the HTTPS source listening",
        lambda: docker(["exec", source.container, "python", "-c", LISTENING], timeout=60).returncode == 0,
        timeout=SOURCE_READY_SECONDS,
    )


def stop_source(source: HttpsSource) -> None:
    """Remove the source container and its network."""
    docker(["rm", "--force", source.container])
    docker(["network", "rm", source.network])


@pytest.fixture
def https_source(sync_image: str, tmp_path: Path) -> Iterator[HttpsSource]:
    """One HTTPS source signed by a test CA, removed afterwards."""
    source = HttpsSource(tmp_path / "custom-ca")
    write_certificates(source)
    try:
        start_source(source, sync_image)
        yield source
    finally:
        stop_source(source)


def source_override(directory: Path, source: HttpsSource) -> Path:
    """A test-only override that joins `sync-worker`, the one service that reads a source, to its network."""
    path = directory / "ca-source-override.yaml"
    path.write_text(
        "---\nservices:\n  sync-worker:\n    networks: [default, ca-source]\n"
        f"networks:\n  ca-source:\n    external: true\n    name: {source.network}\n",
        encoding="utf-8",
    )
    return path


def write_environment(base: dict[str, str], path: Path, ca_directory: Path | None) -> None:
    """Write the deployment's `.env`, with or without the CA setting."""
    values = dict(base)
    values.pop(CA_SETTING, None)
    if ca_directory is not None:
        values[CA_SETTING] = str(ca_directory)
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    path.chmod(0o600)


def source_package(source: HttpsSource, destination_url: str) -> dict[str, Any]:
    """A `genericrestapi` package that reads the HTTPS source into the smoke branch."""
    return {
        "format_version": 1,
        "configuration": {
            "name": "compose-suite-custom-ca",
            "source": {
                "name": "genericrestapi",
                "settings": {
                    "url": source.url,
                    "api_endpoint": "/api/v0",
                    "auth_method": "token",
                    "token": {"$credential": "source-token"},
                },
            },
            "destination": {
                "name": "infrahub",
                "settings": {
                    "url": destination_url,
                    "branch": SMOKE_BRANCH,
                    "token": {"$credential": "infrahub-token"},
                },
            },
            "schema_mapping": [
                {
                    "name": SMOKE_KIND,
                    "mapping": "devices",
                    "identifiers": ["name"],
                    "fields": [{"name": "name", "mapping": "name"}, {"name": "type", "mapping": "type"}],
                }
            ],
        },
        "credentials": {
            "source-token": {"provider": "env", "identifier": SOURCE_TOKEN_SETTING},
            "infrahub-token": {"provider": "env", "identifier": "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN"},
        },
    }


def plan_outcome(client: httpx.Client, config_id: str, registry_version: int) -> tuple[str, dict[str, Any]]:
    """Submit one plan run and return the phase it ends in, `planned` or a failed phase, with the run."""
    created = client.post(
        "/runs",
        headers=idempotency("custom-ca-plan"),
        json={
            "operation": "plan",
            "config_id": config_id,
            "registry_version": registry_version,
            "branch": SMOKE_BRANCH,
            "reason": "custom CA suite: plan",
        },
    )
    assert created.status_code == 202, created.text
    run_id = str(created.json()["run"]["run_id"])

    def settled() -> dict[str, Any] | None:
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()["run"]
        return run if run["phase"] == "planned" or "failed" in run["phase"] else None

    run = cast("dict[str, Any]", wait_for(f"run {run_id} settling", settled, timeout=RUN_TIMEOUT_SECONDS))
    return str(run["phase"]), run


def await_live_worker(deployment: Deployment) -> None:
    wait_for(
        "the deployment reporting a live worker",
        lambda: worker_state(deployment) in {"ready", "busy"},
        timeout=READY_SECONDS,
    )


def test_a_custom_ca_is_trusted_only_when_named_and_a_private_key_stops_the_services(  # noqa: PLR0914 -- one deployment walks the cases in order
    https_source: HttpsSource,
    sync_image: str,
    infrahub_fixture: dict[str, str],
    canaries: dict[str, str],
    tmp_path: Path,
) -> None:
    source = https_source
    environment_file = operator_environment(
        tmp_path / "deployment",
        image=sync_image,
        destination_token=infrahub_fixture["token"],
        canaries=canaries,
        api_port=API_PORT,
    )
    base = dict(line.split("=", 1) for line in environment_file.read_text(encoding="utf-8").splitlines() if line)
    base[SOURCE_TOKEN_SETTING] = source.token
    write_environment(base, environment_file, None)

    deployment = Deployment(
        instance=f"customca{uuid.uuid4().hex[:12]}",
        environment_file=environment_file,
        overrides=(FIXTURE_OVERRIDE, source_override(tmp_path, source)),
        destination=f"http://{container_reachable_host()}:{FIXTURE_INFRAHUB_PORT}",
        api_port=API_PORT,
    )
    try:
        # No setting: every service starts as before, and none installs a bundle.
        started = deployment.up()
        assert started.returncode == 0, started.output[-3000:]
        await_live_worker(deployment)
        before = deployment.logs(*CA_SERVICES, tail=1000)
        assert INSTALLED not in before.stdout, "a service installed a CA bundle with no CA setting"
        for service in CA_SERVICES:
            log = deployment.logs(service, tail=1000)
            assert NO_CERTIFICATE in log.stdout, (
                f"{service} did not report the empty CA directory: {log.output[-2000:]}"
            )

        with api_client(deployment, canaries["principal"]) as client:
            config_id, registry_version = register(
                client, source_package(source, deployment.destination), "custom CA suite: configuration"
            )
            phase, run = plan_outcome(client, config_id, registry_version)
        assert "failed" in phase, f"the run reached {phase!r} without the CA: {run}"
        failure = run["failure"]
        assert failure is not None, f"the failed run has no cause: {run}"
        assert failure["failure_type"] == "tls_certificate_rejected", failure

        # With the setting: the three services trust the CA, and the same package plans.
        write_environment(base, environment_file, source.ca_directory)
        restarted = deployment.up()
        assert restarted.returncode == 0, restarted.output[-3000:]
        await_live_worker(deployment)
        for service in CA_SERVICES:
            log = deployment.logs(service, tail=1000)
            installed = [line for line in log.stdout.splitlines() if INSTALLED in line]
            assert installed, f"{service} did not report the CA bundle: {log.output[-2000:]}"
            assert "certificates=1" in installed[-1] or '"certificates": 1' in installed[-1], installed[-1]

        with api_client(deployment, canaries["principal"]) as client:
            phase, run = plan_outcome(client, config_id, registry_version)
        assert phase == "planned", f"the run reached {phase!r} with the CA: {run}"

        # A private key in the CA directory: each of the three services stops at start.
        write_environment(base, environment_file, source.ca_with_key_directory)
        key_lines = [line for line in source.key_pem.decode().splitlines() if line and not line.startswith("-----")]
        for service in CA_SERVICES:
            refused = deployment.compose(["run", "--rm", "--no-deps", "-T", service], timeout=300)
            raw = refused.unredacted()
            assert refused.returncode != 0, f"{service} started with a private key in the CA directory"
            assert f"CA certificate file {KEY_FILE} holds a private key" in raw, refused.output[-2000:]
            holds_pem = "PRIVATE KEY" in raw or any(line in raw for line in key_lines)
            assert not holds_pem, f"{service}'s output holds content of {KEY_FILE}"
    finally:
        deployment.down(volumes=True)
