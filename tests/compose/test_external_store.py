"""The operator file against an S3-compatible store the team runs outside the deployment.

Spec 021 user story 1. A Silo instance stands in for the team's store: it runs on its own
Docker network, with the bucket created before the deployment starts, and the deployment
leaves its bundled store out (`INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0`). A test-only override
connects the three Sync services that use the store to that network; the shipped file is not
changed. Both addressing styles run: Silo answers virtual-hosted requests for its domain.

The proxy case puts a forwarding proxy in front of Silo that answers a repeated create-only
write itself, the way a store that ignores `If-None-Match: *` would. The proxy forwards every
other request unchanged, `Host` included, so the requests' signatures stay valid.

The private CA cases (spec 021 user story 3, SC-004) serve the store, Infrahub and a NetBox
stand-in over TLS with certificates from a test CA. A TLS front on the store's network answers
as both `INFRAHUB_HOST`, which it forwards to the Infrahub fixture, and `NETBOX_HOST`, which it
answers itself. The same front checks each bundled adapter's HTTP client inside the image.
"""

from __future__ import annotations

import datetime
import ipaddress
import json
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tasks.smoke_dataset import SMOKE_BRANCH, SMOKE_KIND
from tests.compose.conftest import FIXTURE_INFRAHUB_PORT, PROJECT_PREFIX
from tests.compose.lifecycle import (
    FIXTURE_OVERRIDE,
    Deployment,
    api_client,
    await_phase,
    canary,
    container_reachable_host,
    docker,
    idempotency,
    operator_environment,
    register,
    smoke_package,
    wait_for,
    worker_state,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from tests.compose.redaction import Captured

pytestmark = pytest.mark.compose

SILO_IMAGE = (
    "pgsty/silo:RELEASE.2026-10-18T00-00-00Z@sha256:2636fc4155d9a7e41ff58cdb65ddd13ba9304e67184e13c25bc5cb34016269d7"
)
STORE_DOMAIN = "s3.external.test"
PROXY_HOST = "s3-proxy.external.test"
BUCKET = "external-bucket"
ACCESS_KEY = "compose-suite-access-key"
STORE_SERVICES = ("sync-bootstrap", "sync-api", "sync-worker")
# The TLS front's names, and the port it listens on as the image's non-root user.
INFRAHUB_HOST = "infrahub.external.test"
NETBOX_HOST = "netbox.external.test"
FRONT_PORT = 8443
NETBOX_TOKEN_SETTING = "INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN"  # noqa: S105 -- a setting name, not a value
# Where the Compose file mounts the CA directory in the three Sync services.
CA_MOUNT = "/etc/infrahub-sync/ca"
# The bundled adapters the image can build, each with its own HTTP client.
PROBED_ADAPTERS = ("aci", "genericrestapi", "infrahub", "nautobot", "netbox", "peeringmanager", "prometheus")
CHECK_PATH = "infrahub-sync/.infrahub-sync/bootstrap-check/"
# Not the other lifecycle modules' ports, so the modules can share a session.
API_PORTS = {"path": 8071, "virtual": 8072, "proxy": 8073, "tls": 8074, "untrusted": 8076}

# Forwards every request to Silo unchanged, and answers a second create-only write of one
# key itself with 200, the way a store that ignores the condition does.
PROXY = """
import http.client, http.server, sys
seen = set()
class Forward(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def _relay(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        if self.command == "PUT" and self.headers.get("If-None-Match") == "*":
            if self.path in seen:
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            seen.add(self.path)
        upstream = http.client.HTTPConnection(sys.argv[1], 9000, timeout=30)
        upstream.request(self.command, self.path, body=body, headers=dict(self.headers))
        answer = upstream.getresponse()
        payload = answer.read()
        self.send_response(answer.status)
        for name, value in answer.getheaders():
            if name.lower() not in {"transfer-encoding", "connection", "content-length"}:
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)
    do_GET = do_PUT = do_POST = do_DELETE = do_HEAD = _relay
http.server.ThreadingHTTPServer(("0.0.0.0", 9000), Forward).serve_forever()
"""

# Serves TLS on FRONT_PORT with the test CA's server certificate. A request for INFRAHUB_HOST
# goes to the Infrahub fixture over plain HTTP; any other request gets the stand-in's answer.
# Every request line is printed, so a test can read which host received what.
TLS_FRONT = """
import http.client, http.server, json, ssl, sys
UPSTREAM_HOST, UPSTREAM_PORT, INFRAHUB_HOST = sys.argv[1], int(sys.argv[2]), sys.argv[3]
# One answer the stand-in gives to every request: an empty NetBox or Nautobot list page, an
# APIC login, and an empty Prometheus query result, in one document.
STAND_IN = json.dumps({
    "count": 0, "next": None, "previous": None, "results": [],
    "imdata": [{"aaaLogin": {"attributes": {"refreshTimeoutSeconds": "600"}}}],
    "status": "success", "data": {"resultType": "vector", "result": []},
}).encode()
class Front(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def _answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        host = (self.headers.get("Host") or "").split(":")[0]
        print("FRONT", host, self.command, self.path, flush=True)
        if host == INFRAHUB_HOST:
            headers = {name: value for name, value in self.headers.items() if name.lower() != "host"}
            upstream = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=120)
            upstream.request(self.command, self.path, body=body, headers=headers)
            answer = upstream.getresponse()
            payload, status = answer.read(), answer.status
            pairs = [
                (name, value) for name, value in answer.getheaders()
                if name.lower() not in {"transfer-encoding", "connection", "content-length"}
            ]
        else:
            payload, status = STAND_IN, 200
            # pynautobot reads the API version from this header when it builds its client.
            pairs = [("Content-Type", "application/json"), ("API-Version", "2.4")]
        self.send_response(status)
        for name, value in pairs:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)
    def log_message(self, *arguments):
        pass
    do_GET = do_PUT = do_POST = do_PATCH = do_DELETE = do_HEAD = _answer
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain("/certs/public.crt", "/certs/private.key")
server = http.server.ThreadingHTTPServer(("0.0.0.0", int(sys.argv[4])), Front)
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
"""

# Builds each bundled adapter's client with the adapter's own code, after the same trust
# store step the Sync services run first, and sends one request through it. Prints one JSON
# object: per adapter, "trusted" when the request completed, "certificate-refused" when the
# client refused the server certificate, or the error class otherwise.
ADAPTER_PROBE = """
import importlib.util, json, os, ssl, sys
from infrahub_sync import SyncAdapter
from infrahub_sync.service.trust_store import install_custom_ca_bundle
install_custom_ca_bundle()
stand_in, infrahub = sys.argv[1], sys.argv[2]

def adapter(name, **settings):
    return SyncAdapter(name=name, settings=settings)

def infrahub_client():
    from infrahub_sync.adapters.infrahub import InfrahubAdapter
    # The constructor reads the destination schema through the SDK client.
    InfrahubAdapter(target="source", config=None, adapter=adapter(
        "infrahub", url=infrahub, token=os.environ["PROBE_INFRAHUB_TOKEN"], branch="main"))

def netbox_client():
    from infrahub_sync.adapters.netbox import NetboxAdapter
    NetboxAdapter(target="source", config=None, adapter=adapter("netbox", url=stand_in, token="probe-token")).client.status()

def nautobot_client():
    from infrahub_sync.adapters.nautobot import NautobotAdapter
    # The constructor reads the API version through the client.
    NautobotAdapter(target="source", config=None, adapter=adapter("nautobot", url=stand_in, token="probe-token"))

def aci_client():
    from infrahub_sync.adapters.aci import AciAdapter
    client = AciAdapter._create_aci_client(None, adapter("aci", url=stand_in, username="probe", password="probe-password"))
    assert client._login().ok

def prometheus_client():
    from infrahub_sync.adapters.prometheus import PrometheusAdapter
    PrometheusAdapter(target="source", config=None, adapter=adapter("prometheus", url=stand_in, mode="api")).client.instant_query("up")

def genericrestapi_client():
    from infrahub_sync.adapters.genericrestapi import GenericrestapiAdapter
    GenericrestapiAdapter(target="source", config=None, adapter=adapter(
        "genericrestapi", url=stand_in, auth_method="none")).client.get("devices")

def peeringmanager_client():
    from infrahub_sync.adapters.peeringmanager import PeeringmanagerAdapter
    PeeringmanagerAdapter(target="source", config=None, adapter=adapter(
        "peeringmanager", url=stand_in, token="probe-token")).client.get("devices")

def refused_certificate(error):
    while error is not None:
        if isinstance(error, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(error):
            return True
        error = error.__cause__ or error.__context__
    return False

results = {}
for name, check in sorted(globals().items()):
    if not name.endswith("_client"):
        continue
    try:
        check()
        results[name.removesuffix("_client")] = "trusted"
    except Exception as error:
        results[name.removesuffix("_client")] = "certificate-refused" if refused_certificate(error) else type(error).__name__
# The two adapters whose SDK the image does not install cannot be built here.
for name, module in (("ipfabricsync", "ipfabric"), ("slurpitsync", "slurpit")):
    results[name] = "not-installed" if importlib.util.find_spec(module) is None else "installed"
print(json.dumps(results, sort_keys=True))
"""

LIST_KEYS = """
import boto3, json, os, sys
from botocore.config import Config
client = boto3.client("s3", endpoint_url=sys.argv[1], region_name="us-east-1",
    aws_access_key_id=sys.argv[2], aws_secret_access_key=sys.argv[3],
    config=Config(s3={"addressing_style": "path"}), verify=os.environ.get("PROBE_CA") or True)
if sys.argv[4] == "create":
    try:
        client.create_bucket(Bucket=sys.argv[5])
    except client.exceptions.BucketAlreadyOwnedByYou:
        pass
    print(json.dumps([]))
else:
    pages = client.get_paginator("list_objects_v2").paginate(Bucket=sys.argv[5])
    print(json.dumps([item["Key"] for page in pages for item in page.get("Contents", [])]))
"""


class ExternalStore:
    """A Silo instance on its own Docker network, reached by the deployment as `STORE_DOMAIN`."""

    def __init__(self, *, image: str, secret_key: str, tls: Path | None = None) -> None:
        self.network = f"{PROJECT_PREFIX}-store-{uuid.uuid4().hex[:12]}"
        self.image = image
        self.secret_key = secret_key
        # A directory holding `certs/` (the server's certificate and key) and `ca/` (the CA).
        self.tls = tls
        self.containers: list[str] = []

    @property
    def scheme(self) -> str:
        return "https" if self.tls is not None else "http"

    def start(self) -> None:
        created = docker(["network", "create", self.network])
        assert created.returncode == 0, created.stderr
        aliases = [STORE_DOMAIN, f"{BUCKET}.{STORE_DOMAIN}"]
        command = ["run", "--detach", "--network", self.network]
        for alias in aliases:
            command += ["--network-alias", alias]
        if self.tls is not None:
            command += ["--volume", f"{self.tls / 'certs'}:/certs:ro"]
        command += [
            "--tmpfs",
            "/data:uid=65532,gid=65532",
            "--env",
            f"MINIO_ROOT_USER={ACCESS_KEY}",
            "--env",
            f"MINIO_ROOT_PASSWORD={self.secret_key}",
            "--env",
            f"MINIO_DOMAIN={STORE_DOMAIN}",
            "--env",
            "MINIO_BROWSER=off",
            SILO_IMAGE,
            "server",
            "/data",
            *(["--certs-dir", "/certs"] if self.tls is not None else []),
        ]
        started = docker(command)
        assert started.returncode == 0, started.stderr
        self.containers.append(started.stdout.strip())
        wait_for("the external store creating its bucket", lambda: self.listing(create=True) == [], timeout=120)

    def start_proxy(self) -> None:
        """Start the proxy that accepts a second create-only write, as `PROXY_HOST`."""
        started = docker(
            [
                "run",
                "--detach",
                "--network",
                self.network,
                "--network-alias",
                PROXY_HOST,
                "--entrypoint",
                "python",
                self.image,
                "-c",
                PROXY,
                STORE_DOMAIN,
            ]
        )
        assert started.returncode == 0, started.stderr
        self.containers.append(started.stdout.strip())

    def start_front(self, upstream_port: str) -> str:
        """Start the TLS front as `INFRAHUB_HOST` and `NETBOX_HOST`, and return its container."""
        assert self.tls is not None, "the TLS front needs the store's test certificates"
        started = docker(
            [
                "run",
                "--detach",
                "--network",
                self.network,
                "--network-alias",
                INFRAHUB_HOST,
                "--network-alias",
                NETBOX_HOST,
                # The Infrahub fixture is published on this host; the name resolves on Linux too.
                "--add-host",
                "host.docker.internal:host-gateway",
                "--volume",
                f"{self.tls / 'certs'}:/certs:ro",
                "--entrypoint",
                "python",
                self.image,
                "-c",
                TLS_FRONT,
                "host.docker.internal",
                upstream_port,
                INFRAHUB_HOST,
                str(FRONT_PORT),
            ]
        )
        assert started.returncode == 0, started.stderr
        container = started.stdout.strip()
        self.containers.append(container)
        return container

    def keys(self) -> list[str]:
        """Every key in the bucket. A failed listing fails the test.

        Read as an empty bucket, a failed listing would let a check that no key remains
        pass without listing anything.
        """
        keys = self.listing()
        assert keys is not None, "listing the bucket failed"
        return keys

    def listing(self, *, create: bool = False) -> list[str] | None:
        """Every key in the bucket, read with the Sync image on the store's network; None on failure."""
        listed = self.list_keys(create=create)
        if listed.returncode != 0:
            return None
        return json.loads(listed.stdout.strip().splitlines()[-1])

    def list_keys(self, *, create: bool = False, trust: bool = True) -> Captured:
        """Run the listing in the Sync image; with `trust` off, a TLS store's CA is not given to it."""
        mounts = (
            ["--volume", f"{self.tls / 'ca'}:/ca:ro", "--env", "PROBE_CA=/ca/ca.pem"]
            if self.tls is not None and trust
            else []
        )
        return docker(
            [
                "run",
                "--rm",
                "--network",
                self.network,
                *mounts,
                "--entrypoint",
                "python",
                self.image,
                "-c",
                LIST_KEYS,
                f"{self.scheme}://{STORE_DOMAIN}:9000",
                ACCESS_KEY,
                self.secret_key,
                "create" if create else "list",
                BUCKET,
            ],
            timeout=120,
        )

    def stop(self) -> None:
        for container in self.containers:
            docker(["rm", "--force", "--volumes", container])
        docker(["network", "rm", self.network])


def bootstrap_log(deployment: Deployment) -> str:
    """The one-off bootstrap container's whole log, redacted, read from the container itself."""
    return docker(["logs", deployment.container("sync-bootstrap")]).output


def store_override(directory: Path, network: str) -> Path:
    """A test-only override that connects the Sync services that use the store to its network."""
    lines = ["services:"]
    for name in STORE_SERVICES:
        lines += [f"  {name}:", "    networks:", "      default: {}", "      external-store: {}"]
    lines += ["networks:", "  external-store:", "    external: true", f"    name: {network}"]
    path = directory / "external-store.override.yml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def external_deployment(  # noqa: PLR0913 -- one deployment is named by each of these
    directory: Path,
    *,
    style: str,
    endpoint_host: str,
    store: ExternalStore,
    image: str,
    destination_token: str,
    canaries: dict[str, str],
    trust_ca: bool = True,
    destination: str | None = None,
    extra_settings: dict[str, str] | None = None,
) -> Deployment:
    """Write the `.env` of a deployment that uses the external store, and return it, not started.

    For a TLS store, `trust_ca` names the test CA directory in `.env`; off, the deployment runs
    without it. `destination` replaces the Infrahub fixture's plain HTTP address.
    """
    tls_kind = "tls" if trust_ca else "untrusted"
    kind = tls_kind if store.tls is not None else (style if endpoint_host == STORE_DOMAIN else "proxy")
    port = API_PORTS[kind]
    environment_file = operator_environment(
        directory,
        image=image,
        destination_token=destination_token,
        canaries=canaries,
        api_port=port,
    )
    with environment_file.open("a", encoding="utf-8") as settings:
        settings.write(f"INFRAHUB_SYNC_S3_ENDPOINT_URL={store.scheme}://{endpoint_host}:9000\n")
        if store.tls is not None and trust_ca:
            settings.write(f"INFRAHUB_SYNC_CA_CERTIFICATES={store.tls / 'ca'}\n")
        settings.write(f"INFRAHUB_SYNC_S3_ADDRESSING_STYLE={style}\n")
        settings.write(f"INFRAHUB_SYNC_S3_BUCKET={BUCKET}\n")
        settings.write("INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0\n")
        for name, value in (extra_settings or {}).items():
            settings.write(f"{name}={value}\n")
    return Deployment(
        instance=f"external{style}{uuid.uuid4().hex[:8]}",
        environment_file=environment_file,
        overrides=(FIXTURE_OVERRIDE, store_override(directory, store.network)),
        destination=destination or f"http://{container_reachable_host()}:{FIXTURE_INFRAHUB_PORT}",
        api_port=port,
    )


@pytest.fixture(scope="module")
def external_store(sync_image: str, canaries: dict[str, str]) -> Iterator[ExternalStore]:
    store = ExternalStore(image=sync_image, secret_key=canaries["object_store"])
    try:
        store.start()
        yield store
    finally:
        store.stop()


@pytest.fixture(scope="module", params=["path", "virtual"])
def started(  # noqa: PLR0913, PLR0917 -- pytest injects each fixture by name
    request: pytest.FixtureRequest,
    external_store: ExternalStore,
    sync_image: str,
    infrahub_fixture: dict[str, str],
    canaries: dict[str, str],
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Deployment]:
    deployment = external_deployment(
        tmp_path_factory.mktemp(f"external-{request.param}"),
        style=request.param,
        endpoint_host=STORE_DOMAIN,
        store=external_store,
        image=sync_image,
        destination_token=infrahub_fixture["token"],
        canaries=canaries,
    )
    launched = deployment.up()
    try:
        assert launched.returncode == 0, launched.stderr[-3000:] + bootstrap_log(deployment)[-3000:]
        wait_for("the deployment reporting a live worker", lambda: worker_state(deployment) in {"ready", "busy"})
        yield deployment
    finally:
        deployment.down(volumes=True)


def test_no_bundled_store_container_exists(started: Deployment) -> None:
    assert not {"object-store-init", "object-store"} & set(started.containers())


def test_bootstrap_found_the_bucket_and_checked_create_only_writes(
    started: Deployment, external_store: ExternalStore
) -> None:
    logs = bootstrap_log(started)

    assert "bucket present" in logs
    assert "object store refuses a second create-only write" in logs
    keys = external_store.keys()
    assert not [key for key in keys if key.startswith(CHECK_PATH)], keys


def test_a_plan_and_an_apply_keep_their_files_in_the_external_bucket(
    started: Deployment, external_store: ExternalStore
) -> None:
    before = set(external_store.keys())
    principal = json.loads(started.setting("INFRAHUB_SYNC_SERVICE_BEARER_TOKENS"))["compose-suite"]["token"]
    with api_client(started, principal) as client:
        config_id, registry_version = register(
            client, smoke_package(started.destination), "compose suite: external store configuration"
        )
        created = client.post(
            "/runs",
            headers=idempotency("compose-external-sync"),
            json={
                "operation": "sync",
                "config_id": config_id,
                "registry_version": registry_version,
                "branch": SMOKE_BRANCH,
                "reason": "compose suite: sync with the external store",
                "confirm_writes": True,
            },
        )
        assert created.status_code == 202, created.text
        run_id = created.json()["run"]["run_id"]
        await_phase(client, run_id, "applied")

    written = set(external_store.keys()) - before
    assert written, "the run wrote no object to the external bucket"


def test_a_store_that_accepts_a_second_create_only_write_stops_bootstrap(
    external_store: ExternalStore,
    sync_image: str,
    canaries: dict[str, str],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """FR-004: the deployment does not start, bootstrap names the reason, and no check key stays."""
    external_store.start_proxy()
    deployment = external_deployment(
        tmp_path_factory.mktemp("external-proxy"),
        style="path",
        endpoint_host=PROXY_HOST,
        store=external_store,
        image=sync_image,
        destination_token="",
        canaries=canaries,
    )
    try:
        # `sync-api` waits for bootstrap to complete, so `up` returns once bootstrap has ended.
        launched = deployment.up("sync-api")
        logs = bootstrap_log(deployment)
        assert "refused: object-store-unsupported" in logs, logs[-2000:]
        assert launched.returncode != 0
        keys = external_store.keys()
        assert not [key for key in keys if key.startswith(CHECK_PATH)], keys
    finally:
        deployment.down(volumes=True)


# ---------------------------------------------------------------------------
# A private certificate authority (spec 021 US3, SC-004)
# ---------------------------------------------------------------------------


def write_test_pki(directory: Path) -> Path:
    """A test CA in `ca/ca.pem`, and a server certificate it signed in `certs/` for the store and the front."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Spec 021 test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)  # noqa: FBT003 -- the constructor's positional flags
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    server_key = ec.generate_private_key(ec.SECP256R1())
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, STORE_DOMAIN)]))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName(STORE_DOMAIN),
                    x509.DNSName(f"*.{STORE_DOMAIN}"),
                    x509.DNSName(INFRAHUB_HOST),
                    x509.DNSName(NETBOX_HOST),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        # On Python 3.13, `ssl.create_default_context()` and urllib3 verify in strict X.509
        # mode, which refuses a certificate without an authority key identifier. botocore
        # builds its own context without that mode, so the store alone does not show it.
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    (directory / "ca").mkdir(parents=True)
    (directory / "certs").mkdir()
    (directory / "ca" / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (directory / "certs" / "public.crt").write_bytes(server.public_bytes(serialization.Encoding.PEM))
    key = directory / "certs" / "private.key"
    key.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    for path in (directory, directory / "ca", directory / "certs"):
        path.chmod(0o755)
    for path in (directory / "ca" / "ca.pem", directory / "certs" / "public.crt", key):
        path.chmod(0o644)
    return directory


@pytest.fixture(scope="module")
def tls_store(
    sync_image: str, canaries: dict[str, str], tmp_path_factory: pytest.TempPathFactory
) -> Iterator[ExternalStore]:
    pytest.importorskip("cryptography")
    store = ExternalStore(
        image=sync_image,
        secret_key=canaries["object_store"],
        tls=write_test_pki(tmp_path_factory.mktemp("pki") / "pki"),
    )
    try:
        store.start()
        store.start_front(FIXTURE_INFRAHUB_PORT)
        yield store
    finally:
        store.stop()


def front_log(store: ExternalStore) -> str:
    """The TLS front's request lines: one `FRONT <host> <method> <path>` per request."""
    assert store.containers, "the TLS store is not started"
    return docker(["logs", store.containers[-1]]).output


def netbox_package(netbox_url: str, infrahub_url: str) -> dict[str, Any]:
    """A NetBox to Infrahub package for the stand-in and the TLS front. No `verify_ssl`: checks stay on."""
    return {
        "format_version": 1,
        "configuration": {
            "name": "compose-suite-private-ca",
            "source": {
                "name": "netbox",
                "settings": {"url": netbox_url, "token": {"$credential": "netbox-token"}},
            },
            "destination": {
                "name": "infrahub",
                "settings": {
                    "url": infrahub_url,
                    "branch": SMOKE_BRANCH,
                    "token": {"$credential": "infrahub-token"},
                },
            },
            "schema_mapping": [
                {
                    "name": SMOKE_KIND,
                    "mapping": "dcim.devices",
                    "identifiers": ["name"],
                    "fields": [
                        {"name": "name", "mapping": "name"},
                        {"name": "type", "mapping": "device_type.model"},
                    ],
                }
            ],
        },
        "credentials": {
            "netbox-token": {"provider": "env", "identifier": NETBOX_TOKEN_SETTING},
            "infrahub-token": {"provider": "env", "identifier": "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN"},
        },
    }


def test_a_plan_reaches_netbox_infrahub_and_the_store_behind_a_private_ca(
    tls_store: ExternalStore,
    sync_image: str,
    infrahub_fixture: dict[str, str],
    canaries: dict[str, str],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """SC-004: the store, a NetBox stand-in and Infrahub present certificates from the test CA.

    `.env` names the CA directory and nothing turns certificate checks off. Bootstrap, the API
    and the worker each load the CA; the plan reads NetBox and Infrahub through the TLS front
    and writes its files to the TLS store.
    """
    deployment = external_deployment(
        tmp_path_factory.mktemp("external-tls"),
        style="path",
        endpoint_host=STORE_DOMAIN,
        store=tls_store,
        image=sync_image,
        destination_token=infrahub_fixture["token"],
        canaries=canaries,
        destination=f"https://{INFRAHUB_HOST}:{FRONT_PORT}",
        extra_settings={NETBOX_TOKEN_SETTING: canary("netbox-token")},
    )
    try:
        launched = deployment.up()
        logs = bootstrap_log(deployment)
        assert launched.returncode == 0, launched.stderr[-2000:] + logs[-2000:]
        wait_for("the deployment reporting a live worker", lambda: worker_state(deployment) in {"ready", "busy"})
        assert "loaded 1 CA certificate(s)" in logs
        for name in ("sync-api", "sync-worker"):
            assert "loaded 1 CA certificate(s)" in deployment.logs(name).output, name
        before = set(tls_store.keys())
        principal = json.loads(deployment.setting("INFRAHUB_SYNC_SERVICE_BEARER_TOKENS"))["compose-suite"]["token"]
        package = netbox_package(f"https://{NETBOX_HOST}:{FRONT_PORT}", deployment.destination)
        with api_client(deployment, principal) as client:
            config_id, registry_version = register(client, package, "compose suite: private CA configuration")
            created = client.post(
                "/runs",
                headers=idempotency("compose-tls-plan"),
                json={
                    "operation": "plan",
                    "config_id": config_id,
                    "registry_version": registry_version,
                    "branch": SMOKE_BRANCH,
                    "reason": "compose suite: plan against NetBox and Infrahub over TLS",
                },
            )
            assert created.status_code == 202, created.text
            await_phase(client, created.json()["run"]["run_id"], "planned")
        assert set(tls_store.keys()) - before, "the plan wrote no object to the TLS store"
        requests = front_log(tls_store)
        assert f"FRONT {NETBOX_HOST} GET /api/dcim/devices/" in requests, requests[-2000:]
        assert f"FRONT {INFRAHUB_HOST} POST /graphql" in requests, requests[-2000:]
    finally:
        deployment.down(volumes=True)


def test_without_the_ca_bootstrap_refuses_the_tls_store(
    tls_store: ExternalStore,
    sync_image: str,
    canaries: dict[str, str],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The same TLS store, with no CA named in `.env`: bootstrap stops and the deployment does not start.

    Bootstrap reports only the failure family, which a store that is down also gives. The
    listing run without the CA shows the cause, a certificate the client does not trust, and
    the listing with the CA shows the store is up.
    """
    deployment = external_deployment(
        tmp_path_factory.mktemp("external-untrusted"),
        style="path",
        endpoint_host=STORE_DOMAIN,
        store=tls_store,
        image=sync_image,
        destination_token="",
        canaries=canaries,
        trust_ca=False,
    )
    try:
        # `sync-api` waits for bootstrap to complete, so `up` returns once bootstrap has ended.
        launched = deployment.up("sync-api")
        logs = bootstrap_log(deployment)
        assert launched.returncode != 0
        assert "refused: object-store-unavailable" in logs, logs[-2000:]
        assert "loaded 1 CA certificate(s)" not in logs
    finally:
        deployment.down(volumes=True)
    untrusted = tls_store.list_keys(trust=False)
    assert untrusted.returncode != 0
    assert "CERTIFICATE_VERIFY_FAILED" in untrusted.output, untrusted.output[-2000:]
    tls_store.keys()


@pytest.mark.parametrize("trusted", [True, False], ids=["with-ca", "without-ca"])
def test_each_bundled_adapter_client_follows_the_ca_bundle(
    tls_store: ExternalStore, sync_image: str, infrahub_fixture: dict[str, str], *, trusted: bool
) -> None:
    """Each adapter's own client, built in the image, reaches the TLS front with the CA and is refused without it.

    The CA reaches the probe the way it reaches the Sync services: mounted at the same path,
    named by `INFRAHUB_SYNC_CA_DIRECTORY`, and loaded by `install_custom_ca_bundle()`. The IP
    Fabric and Slurp'it SDKs are not in the image, so those two adapters cannot run there.
    """
    assert tls_store.tls is not None
    trust = (
        ["--volume", f"{tls_store.tls / 'ca'}:{CA_MOUNT}:ro", "--env", f"INFRAHUB_SYNC_CA_DIRECTORY={CA_MOUNT}"]
        if trusted
        else []
    )
    probed = docker(
        [
            "run",
            "--rm",
            "--network",
            tls_store.network,
            *trust,
            "--env",
            f"PROBE_INFRAHUB_TOKEN={infrahub_fixture['token']}",
            "--entrypoint",
            "python",
            sync_image,
            "-c",
            ADAPTER_PROBE,
            f"https://{NETBOX_HOST}:{FRONT_PORT}",
            f"https://{INFRAHUB_HOST}:{FRONT_PORT}",
        ],
        timeout=300,
    )
    assert probed.returncode == 0, probed.output[-3000:]
    results = json.loads(probed.stdout.strip().splitlines()[-1])
    expected = "trusted" if trusted else "certificate-refused"
    assert results == {
        **dict.fromkeys(PROBED_ADAPTERS, expected),
        "ipfabricsync": "not-installed",
        "slurpitsync": "not-installed",
    }
