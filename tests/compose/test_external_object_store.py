"""A deployment keeps its plans and run files in a team's own S3-compatible store.

US3, FR-028 to FR-031, SC-012. The store is a MinIO instance from the bundled
build's image, started outside the deployment's services, over HTTPS with a
certificate from a test CA. The deployment trusts that CA only through
`INFRAHUB_SYNC_CA_CERTIFICATES`, and reaches the store on a network it joins
through a test-only override. The bucket and a credential limited to that
bucket's objects exist before the deployment starts, the way a team's own store
would hold them.

Each run checks one addressing style. The store answers `<bucket>.<host>` as
well as `<host>`, so the `virtual` style resolves inside the deployment's
network too. The negative case sets the endpoint and leaves the bundled store
in, which `sync-bootstrap` must refuse.

The endpoint, the bucket and the credential are registered as secrets of the
session, so no retained output renders them, and the log sweep reports the
names it finds, never the values.
"""

from __future__ import annotations

import datetime
import json
import secrets
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from tasks.preview import SMOKE_BRANCH
from tests.compose.conftest import FIXTURE_INFRAHUB_PORT
from tests.compose.lifecycle import (
    FIXTURE_OVERRIDE,
    Deployment,
    api_client,
    await_phase,
    await_verification,
    container_reachable_host,
    docker,
    idempotency,
    operator_environment,
    plant_pending_update,
    register,
    smoke_package,
    wait_for,
    worker_state,
)
from tests.compose.redaction import SECRETS

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from tests.compose.redaction import Captured

pytestmark = pytest.mark.compose

# The bundled build, so the external store is the one OpsMill names as tested.
MINIO_IMAGE = (
    "cgr.dev/chainguard/minio:latest-dev@sha256:d7c906993247627c19f37fc1fa302c34cf2d209ae0e7dc7d52fb0be6ac2849ba"
)
STORE_PORT = 9000
MINIO_USER = "65532"
REGION = "us-east-1"
READY_SECONDS = 420
STORE_READY_SECONDS = 120
API_PORTS = {"path": 8073, "virtual": 8074, "refused": 8075}

# The services that reach the object store. The `cli` service reaches only the API.
STORE_CLIENTS = ("sync-bootstrap", "sync-api", "sync-worker")
BUNDLED_SERVICES = ("object-store-init", "object-store")
NOT_LEFT_OUT = "bundled-object-store-not-left-out"


class ExternalStore:
    """One MinIO instance outside the deployment, and what a deployment needs to use it."""

    def __init__(self, directory: Path) -> None:
        suffix = secrets.token_hex(6)
        self.container = f"infrahub-sync-external-store-{suffix}"
        self.network = f"infrahub-sync-external-store-{suffix}"
        self.host = f"store-{suffix}.s3.test"
        self.bucket = f"sync-bucket-{suffix}"
        self.endpoint = f"https://{self.host}:{STORE_PORT}"
        self.root_user = f"root-{secrets.token_hex(8)}"
        self.root_password = secrets.token_hex(20)
        self.access_key = f"sync-{secrets.token_hex(8)}"
        self.secret_key = secrets.token_hex(20)
        SECRETS.register(self.host, self.bucket, self.root_user, self.root_password, self.access_key, self.secret_key)
        # The deployment is given this directory, and it holds the CA certificate only.
        self.ca_directory = directory / "ca"
        # The store's own certificate and key, and its setup files: never given to the deployment.
        self.certificate_directory = directory / "store-certificates"
        self.setup_directory = directory / "store-setup"

    @property
    def values(self) -> dict[str, str]:
        """Every value whose appearance in a log is a leak, by the setting it belongs to."""
        return {
            "INFRAHUB_SYNC_S3_ENDPOINT_URL": self.endpoint,
            "endpoint host": self.host,
            "INFRAHUB_SYNC_S3_BUCKET": self.bucket,
            "INFRAHUB_SYNC_S3_ACCESS_KEY": self.access_key,
            "INFRAHUB_SYNC_S3_SECRET_KEY": self.secret_key,
        }

    def mc(self, *arguments: str) -> Captured:
        """Run `mc` inside the store's own container, as its root user, trusting the test CA."""
        alias = f"https://{self.root_user}:{self.root_password}@{self.host}:{STORE_PORT}"
        return docker(
            [
                "exec",
                "--env",
                f"MC_HOST_store={alias}",
                "--env",
                "SSL_CERT_FILE=/ca/ca.crt",
                "--env",
                "MC_CONFIG_DIR=/tmp/mc",
                self.container,
                "mc",
                "--config-dir",
                "/tmp/mc",  # noqa: S108 -- inside the throwaway store container
                *arguments,
            ],
            timeout=120,
        )


def write_certificates(store: ExternalStore) -> None:
    """Write a test CA, and a server certificate it signs for the host and `<bucket>.<host>`."""
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "infrahub-sync compose test CA")])
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
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, store.host)]))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(store.host), x509.DNSName(f"{store.bucket}.{store.host}")]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    for directory in (store.ca_directory, store.certificate_directory, store.setup_directory):
        directory.mkdir(parents=True)
        directory.chmod(0o755)
    ca_pem = ca.public_bytes(serialization.Encoding.PEM)
    files = {
        store.ca_directory / "ca.crt": ca_pem,
        store.certificate_directory / "public.crt": server.public_bytes(serialization.Encoding.PEM),
        store.certificate_directory / "private.key": server_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
        store.certificate_directory / "CAs" / "ca.crt": ca_pem,
    }
    (store.certificate_directory / "CAs").mkdir()
    (store.certificate_directory / "CAs").chmod(0o755)
    for path, content in files.items():
        path.write_bytes(content)
        # The store runs as its own non-root user and reads these through a bind mount.
        path.chmod(0o644)


def object_policy(bucket: str) -> dict[str, Any]:
    """Object operations on the one bucket, and the bucket check bootstrap makes first."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": [f"arn:aws:s3:::{bucket}/*"],
            },
            {
                # `HeadBucket` is authorized as `s3:ListBucket`.
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{bucket}"],
            },
        ],
    }


def start_store(store: ExternalStore) -> None:
    """Start the store on its own network, then create the bucket and the limited credential."""
    created = docker(["network", "create", store.network])
    assert created.returncode == 0, created.output
    policy = store.setup_directory / "policy.json"
    policy.write_text(json.dumps(object_policy(store.bucket)), encoding="utf-8")
    policy.chmod(0o644)
    started = docker(
        [
            "run",
            "--detach",
            "--name",
            store.container,
            "--network",
            store.network,
            "--network-alias",
            store.host,
            "--network-alias",
            f"{store.bucket}.{store.host}",
            "--user",
            f"{MINIO_USER}:{MINIO_USER}",
            "--tmpfs",
            f"/data:uid={MINIO_USER},gid={MINIO_USER},mode=0700",
            "--volume",
            f"{store.certificate_directory}:/certs:ro",
            "--volume",
            f"{store.ca_directory}:/ca:ro",
            "--volume",
            f"{store.setup_directory}:/setup:ro",
            "--env",
            f"MINIO_ROOT_USER={store.root_user}",
            "--env",
            f"MINIO_ROOT_PASSWORD={store.root_password}",
            "--env",
            f"MINIO_DOMAIN={store.host}",
            "--env",
            "MINIO_BROWSER=off",
            MINIO_IMAGE,
            "server",
            "/data",
            "--certs-dir",
            "/certs",
            "--address",
            f":{STORE_PORT}",
        ]
    )
    assert started.returncode == 0, started.output
    wait_for(
        "the external store answering over HTTPS",
        lambda: store.mc("ready", "store").returncode == 0,
        timeout=STORE_READY_SECONDS,
    )
    for step in (
        ("mb", f"store/{store.bucket}"),
        ("admin", "policy", "create", "store", "sync-objects", "/setup/policy.json"),
        ("admin", "user", "add", "store", store.access_key, store.secret_key),
        ("admin", "policy", "attach", "store", "sync-objects", "--user", store.access_key),
    ):
        result = store.mc(*step)
        assert result.returncode == 0, f"mc {step[0]} {step[1]}: {result.output}"


def stop_store(store: ExternalStore) -> None:
    """Remove the store container and its network. Its data is tmpfs and goes with it."""
    docker(["rm", "--force", "--volumes", store.container])
    docker(["network", "rm", store.network])


@pytest.fixture(scope="module")
def external_store(docker_daemon: None, tmp_path_factory: pytest.TempPathFactory) -> Iterator[ExternalStore]:
    """One external store shared by every case of this module, removed afterwards."""
    del docker_daemon
    store = ExternalStore(tmp_path_factory.mktemp("external-store"))
    write_certificates(store)
    try:
        start_store(store)
        yield store
    finally:
        stop_store(store)


def store_override(directory: Path, store: ExternalStore) -> Path:
    """A test-only override that joins the store clients to the store's network."""
    services = "".join(f"  {name}:\n    networks: [default, external-store]\n" for name in STORE_CLIENTS)
    path = directory / "external-store-override.yaml"
    path.write_text(
        f"---\nservices:\n{services}networks:\n  external-store:\n    external: true\n    name: {store.network}\n",
        encoding="utf-8",
    )
    return path


def store_environment(  # noqa: PLR0913 -- one `.env` per case, each input independent
    directory: Path,
    store: ExternalStore,
    *,
    image: str,
    infrahub_fixture: Mapping[str, str],
    canaries: Mapping[str, str],
    api_port: int,
    prefix: str,
    addressing_style: str,
    leave_out_bundled: bool,
) -> Path:
    """Write the `.env` of a deployment that uses the external store."""
    path = operator_environment(
        directory,
        image=image,
        destination_token=infrahub_fixture["token"],
        canaries=canaries,
        api_port=api_port,
    )
    values = dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if line)
    values.update(
        {
            "INFRAHUB_SYNC_S3_ENDPOINT_URL": store.endpoint,
            "INFRAHUB_SYNC_S3_BUCKET": store.bucket,
            "INFRAHUB_SYNC_S3_PREFIX": prefix,
            "INFRAHUB_SYNC_S3_REGION": REGION,
            "INFRAHUB_SYNC_S3_ACCESS_KEY": store.access_key,
            "INFRAHUB_SYNC_S3_SECRET_KEY": store.secret_key,
            "INFRAHUB_SYNC_S3_ADDRESSING_STYLE": addressing_style,
            "INFRAHUB_SYNC_CA_CERTIFICATES": str(store.ca_directory),
        }
    )
    if leave_out_bundled:
        values["INFRAHUB_SYNC_BUNDLED_OBJECT_STORE"] = "0"
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    return path


def deployment_for(
    store: ExternalStore,
    directory: Path,
    environment_file: Path,
    api_port: int,
) -> Deployment:
    return Deployment(
        instance=f"extstore{uuid.uuid4().hex[:12]}",
        environment_file=environment_file,
        overrides=(FIXTURE_OVERRIDE, store_override(directory, store)),
        destination=f"http://{container_reachable_host()}:{FIXTURE_INFRAHUB_PORT}",
        api_port=api_port,
    )


def plan_and_apply(deployment: Deployment, principal: str, infrahub_fixture: Mapping[str, str]) -> str:
    """Register the example package, then plan, verify and apply one run; return its identifier."""
    plant_pending_update(infrahub_fixture)
    with api_client(deployment, principal) as client:
        config_id, registry_version = register(
            client, smoke_package(deployment.destination), "external store suite: configuration"
        )
        created = client.post(
            "/runs",
            headers=idempotency("external-store-plan"),
            json={
                "operation": "plan",
                "config_id": config_id,
                "registry_version": registry_version,
                "branch": SMOKE_BRANCH,
                "reason": "external store suite: plan",
            },
        )
        assert created.status_code == 202, created.text
        run_id = str(created.json()["run"]["run_id"])
        await_phase(client, run_id, "planned")
        plan = client.get(f"/runs/{run_id}/plan")
        assert plan.status_code == 200, plan.text
        verified = client.post(
            f"/runs/{run_id}/verify",
            headers=idempotency("external-store-verify"),
            json={"reason": "external store suite: verify the plan"},
        )
        assert verified.status_code == 202, verified.text
        assert await_verification(client, run_id)["outcome"] == "verified"
        applied = client.post(
            f"/runs/{run_id}/apply",
            headers=idempotency("external-store-apply"),
            json={
                "expected_checksum": plan.json()["checksum"],
                "confirm_writes": True,
                "branch": SMOKE_BRANCH,
                "reason": "external store suite: apply",
            },
        )
        assert applied.status_code == 202, applied.text
        await_phase(client, run_id, "applied")
    return run_id


def bucket_keys(store: ExternalStore) -> list[str]:
    """Every key in the external bucket, read with the store's root credential."""
    listed = store.mc("ls", "--recursive", "--json", f"store/{store.bucket}")
    assert listed.returncode == 0, listed.output
    keys = []
    for line in listed.unredacted().splitlines():
        if line.strip().startswith("{"):
            entry = json.loads(line)
            if entry.get("type") == "file":
                keys.append(str(entry["key"]))
    return keys


@pytest.mark.parametrize("addressing_style", ["path", "virtual"])
def test_a_run_keeps_its_files_in_the_external_store(  # noqa: PLR0913, PLR0917 -- pytest passes one fixture per input
    addressing_style: str,
    external_store: ExternalStore,
    sync_image: str,
    infrahub_fixture: dict[str, str],
    canaries: dict[str, str],
    tmp_path: Path,
) -> None:
    prefix = f"team-runs/{addressing_style}"
    environment_file = store_environment(
        tmp_path,
        external_store,
        image=sync_image,
        infrahub_fixture=infrahub_fixture,
        canaries=canaries,
        api_port=API_PORTS[addressing_style],
        prefix=prefix,
        addressing_style=addressing_style,
        leave_out_bundled=True,
    )
    deployment = deployment_for(external_store, tmp_path, environment_file, API_PORTS[addressing_style])
    try:
        started = deployment.up()
        detail = deployment.logs("sync-bootstrap", tail=80)
        assert started.returncode == 0, f"{started.stderr[-2000:]}\n{detail.output[-3000:]}"
        wait_for(
            "the deployment reporting a live worker",
            lambda: worker_state(deployment) in {"ready", "busy"},
            timeout=READY_SECONDS,
        )

        held = deployment.containers()
        assert not set(BUNDLED_SERVICES) & set(held), sorted(held)

        run_id = plan_and_apply(deployment, canaries["principal"], infrahub_fixture)

        keys = bucket_keys(external_store)
        run_keys = [key for key in keys if key.startswith(f"{prefix}/runs/{run_id}/")]
        assert run_keys, f"no object of run {run_id} under the prefix {prefix}; {len(keys)} keys in the bucket"
        assert all(key.startswith("team-runs/") for key in keys), "an object was written outside the prefixes"

        bootstrap_log = deployment.logs("sync-bootstrap", tail=1000)
        assert bootstrap_log.returncode == 0, bootstrap_log.output
        leaked = SECRETS.leaked(bootstrap_log.unredacted(), external_store.values)
        assert leaked == [], f"sync-bootstrap's log holds the value of {', '.join(leaked)}"
    finally:
        deployment.down(volumes=True)


def test_bootstrap_refuses_an_external_endpoint_while_the_bundled_store_runs(
    external_store: ExternalStore,
    sync_image: str,
    infrahub_fixture: dict[str, str],
    canaries: dict[str, str],
    tmp_path: Path,
) -> None:
    environment_file = store_environment(
        tmp_path,
        external_store,
        image=sync_image,
        infrahub_fixture=infrahub_fixture,
        canaries=canaries,
        api_port=API_PORTS["refused"],
        prefix="team-runs/refused",
        addressing_style="path",
        leave_out_bundled=False,
    )
    deployment = deployment_for(external_store, tmp_path, environment_file, API_PORTS["refused"])
    try:
        started = deployment.up()
        assert started.returncode != 0, "the deployment started with the bundled store left in"

        bootstrap = deployment.container("sync-bootstrap")
        state = docker(["inspect", "--format", "{{.State.ExitCode}}", bootstrap])
        assert state.stdout.strip() == "1", state.output

        bootstrap_log = deployment.logs("sync-bootstrap", tail=1000)
        assert NOT_LEFT_OUT in bootstrap_log.stdout, bootstrap_log.output
        leaked = SECRETS.leaked(bootstrap_log.unredacted(), external_store.values)
        assert leaked == [], f"sync-bootstrap's log holds the value of {', '.join(leaked)}"
        # Refused before any object store request: not even the create-only check key was written.
        assert [key for key in bucket_keys(external_store) if key.startswith("team-runs/refused/")] == []
        # Nothing that waits for bootstrap runs.
        running = deployment.compose(["ps", "--quiet", "sync-api", "sync-worker"])
        assert running.returncode == 0, running.output
        assert running.stdout.split() == [], running.output
    finally:
        deployment.down(volumes=True)
