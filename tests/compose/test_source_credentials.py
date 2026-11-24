"""What a worker does with a declared source credential, at the two boundaries that carry one.

The bundled NetBox and Nautobot adapters are the first source adapters whose SDK
the image installs, so this suite proves the thing that installation is for: a
registered package's stored credential, as the Sync API releases it to a run,
reaches the real adapter client, and nothing ambient does.

Two boundaries, deliberately separate.

* In-process, here, against the real parser, the real resolver, the real
  installed SDK and the real bundled adapter. Nothing is stubbed except the one
  transport call Nautobot's constructor makes, and every socket is denied, so a
  pass cannot come from a network the test did not intend.
* In the container, under the `docker` marker, through the shipped image. That
  one proves the same property survives the image and the Compose model.

Neither is an extraction claim and neither qualifies a source version. No source
server is reached, imitated, or described: the only external response in this
file is the fixed version document Nautobot's constructor demands before it will
return an object at all.

Canary values never reach retained output. Comparisons happen privately and a
failure reports names.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess  # noqa: S404 -- the probes of this module, run with this interpreter
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import requests

from infrahub_sync.configuration import collect_findings, parse_configuration_package
from infrahub_sync.configuration.credentials import CredentialConfigurationError, select_runtime_credential
from infrahub_sync.configuration.runtime import resolve_runtime_instance
from tests.compose.conftest import CONTRACT_ENVIRONMENT, compose
from tests.compose.redaction import SECRETS
from tests.docker_image import IMAGE_REPOSITORY_ENV, IMAGE_VERSION_ENV, image_settings

if TYPE_CHECKING:
    from collections.abc import Mapping

    from infrahub_sync import SyncInstance

# Long enough that a substring match means the value itself, and unmistakably a
# test value. These stand in for operator secrets; none is a real credential.
DECLARED_SOURCE_TOKEN = "pkg-r1-declared-source-token-9d41c7"  # noqa: S105 -- a test stand-in, not a credential
DECLARED_DESTINATION_TOKEN = "pkg-r1-declared-destination-token-4b82ae"  # noqa: S105 -- a test stand-in, not a credential
AMBIENT_SOURCE_TOKEN = "pkg-r1-ambient-source-token-must-lose-1f60d3"  # noqa: S105 -- a test stand-in, not a credential

# Reserved by RFC 6761: resolvable by nothing, so a leaked request cannot reach a
# real host even if a deny were somehow bypassed.
NETBOX_URL = "https://netbox.invalid"
NAUTOBOT_URL = "https://nautobot.invalid"
AMBIENT_URL = "https://ambient-source.invalid"

# The one external answer this file supplies. Nautobot's client asks for the API
# version during construction and refuses to return an object without it; every
# other request is rejected.
NAUTOBOT_VERSION_URL = f"{NAUTOBOT_URL}/api/"
NAUTOBOT_API_VERSION = "2.4"

# Both SDKs are declared `python_version >= '3.11'` in the service extra, so the
# Python 3.10 profile does not install them and cannot construct either client.
# Keyed to the interpreter rather than to whether the import happens to work: on
# every supported service Python these two cases always run, and a missing
# package fails them.
NEEDS_SERVICE_PROFILE = pytest.mark.skipif(
    sys.version_info < (3, 11),
    reason="the service extra declares both source SDKs python_version >= '3.11'",
)


@dataclass(frozen=True)
class Source:
    """One bundled source adapter's declared shape, as a package names it."""

    name: str
    url: str
    reference: str
    ambient_url_names: tuple[str, ...]
    ambient_token_name: str
    mapping: str


SOURCES = {
    source.name: source
    for source in (
        Source(
            name="netbox",
            url=NETBOX_URL,
            reference="netbox-token",
            ambient_url_names=("NETBOX_ADDRESS", "NETBOX_URL"),
            ambient_token_name="NETBOX_TOKEN",  # noqa: S106 - the name of a variable, not a value
            mapping="extras.tags",
        ),
        Source(
            name="nautobot",
            url=NAUTOBOT_URL,
            reference="nautobot-token",
            ambient_url_names=("NAUTOBOT_ADDRESS", "NAUTOBOT_URL"),
            ambient_token_name="NAUTOBOT_TOKEN",  # noqa: S106 - the name of a variable, not a value
            mapping="extras.tags",
        ),
    )
}

# Every environment name any of this touches, cleared before each case so an
# ambient value on the developer's machine cannot decide an outcome.
MANAGED_ENVIRONMENT = (
    "NETBOX_TOKEN",
    "NETBOX_ADDRESS",
    "NETBOX_URL",
    "NAUTOBOT_TOKEN",
    "NAUTOBOT_ADDRESS",
    "NAUTOBOT_URL",
    "INFRAHUB_API_TOKEN",
    "INFRAHUB_ADDRESS",
)


def package_content(source: str) -> dict[str, Any]:
    """One minimal single-kind package declaring `source` against Infrahub.

    Sanitized and illustrative. It is the smallest package whose source names a
    stored credential, which is the only property this file reads from it.
    """
    profile = SOURCES[source]
    return {
        "format_version": 1,
        "configuration": {
            "name": f"pkg-r1-{source}",
            "source": {
                "name": source,
                "credential": profile.reference,
                "settings": {"url": profile.url},
            },
            "destination": {
                "name": "infrahub",
                "credential": "infrahub-token",
                "settings": {"url": "http://infrahub.example.net:8000"},
            },
            "schema_mapping": [
                {
                    "name": "BuiltinTag",
                    "mapping": profile.mapping,
                    "identifiers": ["name"],
                    "fields": [{"name": "name", "mapping": "name"}],
                }
            ],
        },
    }


@pytest.fixture(autouse=True)
def _closed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every case from an environment that names no source credential."""
    for name in MANAGED_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse every socket for the rest of the case.

    Installed after imports, so it constrains what the code under test does
    rather than what importing it did. A construction that reaches the network
    fails here instead of depending on what a host happens to resolve.
    """

    def refuse(*_args: object, **_kwargs: object) -> object:
        message = "this case denies every network call"
        raise AssertionError(message)

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


RELEASED_SOURCE = {"token": DECLARED_SOURCE_TOKEN}
RELEASED_DESTINATION = {"token": DECLARED_DESTINATION_TOKEN}


def runtime_instance(
    source: str,
    *,
    resolve_source_credentials: bool = True,
    released: Mapping[str, Mapping[str, str]] | None = None,
) -> SyncInstance:
    """Parse and resolve one package exactly as a registered worker run does.

    ``released`` holds, for each block, the fields the Sync API released to the run. The default
    is the values of both blocks.
    """
    package = parse_configuration_package(package_content(source))
    return resolve_runtime_instance(
        package,
        directory="/nonexistent/pkg-r1",
        resolve_source_credentials=resolve_source_credentials,
        released={"source": RELEASED_SOURCE, "destination": RELEASED_DESTINATION} if released is None else released,
    )


# ---------------------------------------------------------------------------
# The resolver's refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_an_absent_selected_source_token_is_refused(source: str) -> None:
    """A run that needs a source credential does not start without one."""
    with pytest.raises(CredentialConfigurationError) as raised:
        runtime_instance(source, released={"destination": RELEASED_DESTINATION})

    assert "source block" in str(raised.value)


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_an_empty_selected_source_token_is_refused(source: str) -> None:
    """An empty value is an unset one; a blank released field must not start a run."""
    with pytest.raises(CredentialConfigurationError) as raised:
        runtime_instance(source, released={"source": {"token": ""}, "destination": RELEASED_DESTINATION})

    assert "source block" in str(raised.value)
    assert "token" in str(raised.value)


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_a_refusal_names_the_block_and_never_a_value(source: str) -> None:
    """The diagnosis an operator needs is the block; no released value must be in it."""
    with pytest.raises(CredentialConfigurationError) as raised:
        runtime_instance(source, released={"destination": RELEASED_DESTINATION})

    rendered = str(raised.value)
    value_shown = DECLARED_DESTINATION_TOKEN in rendered
    assert "source block" in rendered
    assert not value_shown


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_the_source_a_package_does_not_select_may_be_absent(source: str) -> None:
    """One deployment serves packages of both kinds; only the credential the package names is needed."""
    instance = runtime_instance(source)

    token_is_released = (instance.source.settings or {})["token"] == DECLARED_SOURCE_TOKEN
    assert token_is_released


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_declared_validation_resolves_no_source_credential(source: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Registration judges declared content, so it must not reach for the secret.

    Finding nothing wrong would also be true of a validation that filled the
    credential and happened to succeed, so the function that fills released
    values is made to fail the case if it is called at all.
    """

    def refuse(*_args: object, **_kwargs: object) -> None:
        message = "declared validation filled a credential setting"
        raise AssertionError(message)

    monkeypatch.setattr("infrahub_sync.configuration.runtime._fill_released_values", refuse)
    package = parse_configuration_package(package_content(source))

    assert collect_findings(package) == ()


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_a_saved_plan_apply_needs_no_source_token(source: str) -> None:
    """An apply host constructs the destination only, so it holds no source secret."""
    instance = runtime_instance(
        source, resolve_source_credentials=False, released={"destination": RELEASED_DESTINATION}
    )

    destination_token_is_released = (instance.destination.settings or {})["token"] == DECLARED_DESTINATION_TOKEN
    assert "token" not in (instance.source.settings or {})
    assert destination_token_is_released


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_the_registered_url_and_token_beat_a_conflicting_ambient_pair(
    source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A registered run reads its declared settings and its released values; the shell has no vote.

    Both halves matter. An ambient URL that won would send a stored credential
    somewhere the package never named, and an ambient token that won would send
    a credential the operator did not store.
    """
    profile = SOURCES[source]
    instance = runtime_instance(source)
    settings = instance.source.settings or {}

    for name in profile.ambient_url_names:
        monkeypatch.setenv(name, AMBIENT_URL)
    monkeypatch.setenv(profile.ambient_token_name, AMBIENT_SOURCE_TOKEN)

    token_is_released = (
        select_runtime_credential(settings, "token", (profile.ambient_token_name,)) == DECLARED_SOURCE_TOKEN
    )
    assert select_runtime_credential(settings, "url", profile.ambient_url_names) == profile.url
    assert token_is_released


# ---------------------------------------------------------------------------
# The real adapter clients
# ---------------------------------------------------------------------------
# The point of installing the two SDKs. A resolver that produced the right value
# proves nothing if the adapter then goes and reads the environment itself.


@NEEDS_SERVICE_PROFILE
def test_the_netbox_adapter_client_carries_the_registered_credential(
    monkeypatch: pytest.MonkeyPatch, deny_network: None
) -> None:
    """The real NetBox client, built by the real adapter, offline.

    `pynetbox.api()` issues no request, so the whole construction happens under
    the network deny and any read of the environment would have to come from the
    adapter itself.
    """
    del deny_network
    from infrahub_sync.adapters.netbox import NetboxAdapter

    instance = runtime_instance("netbox")
    for name in SOURCES["netbox"].ambient_url_names:
        monkeypatch.setenv(name, AMBIENT_URL)
    monkeypatch.setenv("NETBOX_TOKEN", AMBIENT_SOURCE_TOKEN)

    adapter = NetboxAdapter(target="netbox", adapter=instance.source, config=instance)

    base_url_is_declared = adapter.client.base_url == f"{NETBOX_URL}/api"
    token_is_declared = adapter.client.token == DECLARED_SOURCE_TOKEN

    assert base_url_is_declared, "the client was not built against the declared url"
    assert token_is_declared, "the client does not carry the declared token"


@NEEDS_SERVICE_PROFILE
def test_the_nautobot_adapter_client_carries_the_registered_credential(
    monkeypatch: pytest.MonkeyPatch, deny_network: None
) -> None:
    """The real Nautobot client, built by the real adapter, with one stubbed response.

    Nautobot's constructor performs a version-discovery GET and returns no object
    without it, so exactly that one request is answered with a fixed document.
    The SDK constructor, its version validation, the resolver and the adapter are
    all the real ones; any second request, or any request to another URL, fails
    the case. Answering it proves what the adapter sends, not what a server is.
    """
    del deny_network
    from infrahub_sync.adapters.nautobot import NautobotAdapter

    seen: list[tuple[str, str | None]] = []

    def version_only_get(
        session: requests.Session,
        url: str,
        *args: object,
        headers: Mapping[str, str] | None = None,
        **kwargs: object,
    ) -> requests.Response:
        del args, kwargs
        sent = {**dict(session.headers), **dict(headers or {})}
        seen.append((url, sent.get("Authorization")))
        if url != NAUTOBOT_VERSION_URL:
            message = "the adapter requested something other than the declared version document"
            raise AssertionError(message)
        answer = requests.Response()
        answer.status_code = 200
        answer.url = url
        answer.headers["API-Version"] = NAUTOBOT_API_VERSION
        answer._content = b"{}"
        return answer

    monkeypatch.setattr(requests.Session, "get", version_only_get)
    instance = runtime_instance("nautobot")
    for name in SOURCES["nautobot"].ambient_url_names:
        monkeypatch.setenv(name, AMBIENT_URL)
    monkeypatch.setenv("NAUTOBOT_TOKEN", AMBIENT_SOURCE_TOKEN)

    adapter = NautobotAdapter(target="nautobot", adapter=instance.source, config=instance)

    # Reduced to verdicts before the assert: pytest renders an assertion's operands
    # on failure, and these carry the declared credential.
    base_url_is_declared = adapter.client.base_url == f"{NAUTOBOT_URL}/api"
    token_is_declared = adapter.client.token == DECLARED_SOURCE_TOKEN
    exactly_one_version_get = len(seen) == 1
    version_get_url_is_declared = bool(seen) and seen[0][0] == NAUTOBOT_VERSION_URL
    version_get_carries_declared_token = bool(seen) and seen[0][1] == f"Token {DECLARED_SOURCE_TOKEN}"

    assert base_url_is_declared, "the client was not built against the declared url"
    assert token_is_declared, "the client does not carry the declared token"
    assert exactly_one_version_get, "the constructor did not make exactly one version request"
    assert version_get_url_is_declared, "the version request did not go to the declared version document"
    assert version_get_carries_declared_token, "the version request did not carry the declared credential"


# ---------------------------------------------------------------------------
# The container boundary
# ---------------------------------------------------------------------------
# The same property, proved where it has to hold: inside the shipped image, on
# the Compose model an operator actually runs. Opt-in under `docker`, and driven
# through the suite's capture boundary so a canary cannot reach retained output.
#
# A worker container is given no source or destination credential (spec 027). For a
# run it reads the fields of the stored credentials from the Sync API and passes them
# to the resolver as `released`. The probes below stand at that point: each generates
# the released values inside the container, so no value crosses the container
# boundary in either direction, and prints verdicts only.

REMOVED_PREFIX = "INFRAHUB_SYNC_CREDENTIAL_"

CONTAINER_PROBE = """
import json, os, secrets, socket, sys

from infrahub_sync.configuration import parse_configuration_package
from infrahub_sync.configuration.credentials import CredentialConfigurationError
from infrahub_sync.configuration.runtime import resolve_runtime_instance

package_content = json.loads(sys.argv[1])
source = package_content["configuration"]["source"]["name"]
declared_url = package_content["configuration"]["source"]["settings"]["url"]
release_source = sys.argv[2] == "release-source"

# The fields the Sync API would release to this run, generated here: they exist in this
# process only, and nothing below prints one.
released_token = secrets.token_hex(24)
released = {"destination": {"token": secrets.token_hex(24)}}
if release_source:
    released["source"] = {"token": released_token}

package = parse_configuration_package(package_content)
try:
    instance = resolve_runtime_instance(package, directory="/tmp/infrahub-sync/pkg-r1", released=released)
except CredentialConfigurationError as refusal:
    rendered = str(refusal)
    shown = [fields["token"] for fields in released.values() if fields["token"] in rendered]
    print("PKG-R1-CONTAINER REFUSED source-block" if "source block" in rendered and not shown else "PKG-R1-CONTAINER FAIL")
    sys.exit(3)

# The one external answer this probe supplies. Nautobot's client asks for the API
# version while constructing and returns no object without it. Everything about
# that request is recorded and judged: the URL, the effective Authorization header
# the session would actually send, and how many times it happens. Any other URL,
# any second call, or a header that does not carry the released credential leaves
# the verdicts below false. Nothing raw is printed.
seen = []

if source == "nautobot":
    import requests

    expected_version_url = declared_url.rstrip("/") + "/api/"

    def version_only_get(session, url, *args, headers=None, **kwargs):
        effective = dict(session.headers)
        effective.update(dict(headers or {}))
        seen.append((url, effective.get("Authorization")))
        if url != expected_version_url:
            raise AssertionError("the probe rejects every request but the declared version document")
        answer = requests.Response()
        answer.status_code = 200
        answer.url = url
        answer.headers["API-Version"] = "2.4"
        answer._content = b"{}"
        return answer

    requests.Session.get = version_only_get

def refuse(*args, **kwargs):
    raise AssertionError("the container probe denies every network call")

socket.socket.connect = refuse
socket.socket.connect_ex = refuse
socket.create_connection = refuse

if source == "netbox":
    from infrahub_sync.adapters.netbox import NetboxAdapter as Adapter
else:
    from infrahub_sync.adapters.nautobot import NautobotAdapter as Adapter

adapter = Adapter(target=source, adapter=instance.source, config=instance)

# Compared here, inside the container. Only the verdicts cross the boundary:
# a rendered value would put the credential into whatever retained the output.
checks = {
    "url_is_declared": adapter.client.base_url == declared_url.rstrip("/") + "/api",
    "token_is_released": adapter.client.token == released_token,
    "ambient_url_ignored": "ambient" not in adapter.client.base_url,
    "ambient_token_ignored": adapter.client.token not in set(os.environ.values()),
    "no_credential_variable": not [name for name in os.environ if name.startswith("INFRAHUB_SYNC_CREDENTIAL_")],
}
if source == "nautobot":
    checks["exactly_one_version_get"] = len(seen) == 1
    checks["version_get_url_is_declared"] = bool(seen) and seen[0][0] == expected_version_url
    checks["version_get_carries_released_token"] = bool(seen) and seen[0][1] == "Token " + released_token
else:
    checks["no_transport_call_during_construction"] = seen == []
failed = sorted(name for name, passed in checks.items() if not passed)
print("PKG-R1-CONTAINER PASS" if not failed else "PKG-R1-CONTAINER FAIL " + ",".join(failed))
"""


def container_environment() -> dict[str, str]:
    """Every operator input the file needs, with the image under test named."""
    image = image_settings()
    if not all(image.values()):
        pytest.skip(f"{IMAGE_REPOSITORY_ENV} and {IMAGE_VERSION_ENV} name no built image to run")
    return {**CONTRACT_ENVIRONMENT, **image}


def operator_file(tmp_path: Path, **settings: str) -> Path:
    """Write one operator `.env` holding exactly these settings."""
    path = tmp_path / ".env"
    path.write_text("".join(f"{name}={value}\n" for name, value in settings.items()), encoding="utf-8")
    path.chmod(0o600)
    return path


CONTAINER_DECLARED_URL = {"netbox": "http://netbox.invalid:8080", "nautobot": "http://nautobot.invalid:8080"}
CONTAINER_AMBIENT_URL = "http://ambient.invalid:9999"
# What an operator's `.env` of the earlier format still holds. Distinct per source, so a
# leak sweep can name which one escaped. The worker container must receive neither.
CONTAINER_TOKENS = {
    "netbox": "pkg-r1-container-netbox-canary-a71f3c",
    "nautobot": "pkg-r1-container-nautobot-canary-c93e05",
}


def container_package(source: str) -> str:
    """The package of `source` as the JSON text a probe takes, with the container's own source address."""
    content = package_content(source)
    content["configuration"]["source"]["settings"]["url"] = CONTAINER_DECLARED_URL[source]
    return json.dumps(content)


def earlier_format_variable(source: str) -> str:
    """The variable that carried the token of `source` to the worker before stored credentials."""
    return f"{REMOVED_PREFIX}{source.upper()}_TOKEN"


@NEEDS_SERVICE_PROFILE
@pytest.mark.parametrize("source", sorted(SOURCES))
@pytest.mark.parametrize("release", ["release-source", "withhold-source"])
def test_the_container_probe_gives_its_verdict_in_this_interpreter(source: str, release: str) -> None:
    """The probe itself is checked on the unit tier, so the `docker` cases cannot fail on its own text.

    A child process, because the probe replaces `socket.connect` for the whole interpreter.
    """
    result = subprocess.run(  # noqa: S603 -- this interpreter, with the fixed probe text of this module
        [sys.executable, "-c", CONTAINER_PROBE, container_package(source), release],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={name: value for name, value in os.environ.items() if not name.startswith(REMOVED_PREFIX)},
    )

    if release == "release-source":
        assert result.returncode == 0, result.stderr[-2000:]
        assert result.stdout.strip() == "PKG-R1-CONTAINER PASS"
    else:
        assert result.returncode == 3, result.stderr[-2000:]
        assert result.stdout.strip() == "PKG-R1-CONTAINER REFUSED source-block"


@pytest.mark.docker
@pytest.mark.parametrize("source", sorted(SOURCES))
def test_the_worker_container_gives_the_released_credential_to_the_real_adapter(
    docker_daemon: None, source: str, tmp_path: Path
) -> None:
    """One `compose run` in the image under test, resolving and constructing for real.

    The probe passes the resolver the fields the Sync API releases to a run, and the real
    adapter client ends up holding that token. The operator's `.env` still sets the variable
    that carried the token before stored credentials, and a hostile ambient URL is exported
    into the same container: the probe's verdict is PASS only when the container holds no
    `INFRAHUB_SYNC_CREDENTIAL_*` variable and the client ignores the ambient URL. Everything is
    compared inside the container and only verdicts are printed, so no value reaches anything
    this suite retains.
    """
    del docker_daemon
    profile = SOURCES[source]
    token = CONTAINER_TOKENS[source]
    SECRETS.register(token)
    settings = operator_file(tmp_path, **{earlier_format_variable(source): token})

    result = compose(
        [
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--env",
            f"{profile.ambient_url_names[0]}={CONTAINER_AMBIENT_URL}",
            "--env",
            f"{profile.ambient_url_names[1]}={CONTAINER_AMBIENT_URL}",
            "sync-worker",
            "python",
            "-c",
            CONTAINER_PROBE,
            container_package(source),
            "release-source",
        ],
        environment=container_environment(),
        env_files=(settings,),
    )

    assert result.returncode == 0, result.output
    assert "PKG-R1-CONTAINER PASS" in result.stdout, result.output
    assert SECRETS.leaked(result.unredacted(), {earlier_format_variable(source): token}) == []


@pytest.mark.docker
def test_a_source_credential_that_is_not_released_fails_the_run_inside_the_container(
    docker_daemon: None, tmp_path: Path
) -> None:
    """The refusal names the block, and no variable of the container can stand in for the stored credential.

    The operator's `.env` sets the variable that carried the NetBox token before stored
    credentials. The run is still refused, because the worker reads a credential from the
    Sync API only.
    """
    del docker_daemon
    token = CONTAINER_TOKENS["netbox"]
    SECRETS.register(token)
    settings = operator_file(tmp_path, **{earlier_format_variable("netbox"): token})

    result = compose(
        [
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "sync-worker",
            "python",
            "-c",
            CONTAINER_PROBE,
            container_package("netbox"),
            "withhold-source",
        ],
        environment=container_environment(),
        env_files=(settings,),
    )

    assert result.returncode != 0
    assert "PKG-R1-CONTAINER REFUSED source-block" in result.stdout, result.output
    assert "PKG-R1-CONTAINER PASS" not in result.stdout
    assert SECRETS.leaked(result.unredacted(), {earlier_format_variable("netbox"): token}) == []


# ---------------------------------------------------------------------------
# A credential with a user name and a password reaches the adapter settings (spec 015, SC-003)
# ---------------------------------------------------------------------------

ACI_PACKAGE = Path(__file__).resolve().parents[2] / "examples" / "aci_to_infrahub" / "package.yml"

# Resolves the shipped ACI package with the real resolver, inside the worker container, from
# the fields the Sync API releases for a credential of the user name and password kind. The
# values are generated in the container, and only a verdict is printed.
ACI_PROBE = """
import json, os, secrets, sys

from infrahub_sync.configuration import parse_configuration_package
from infrahub_sync.configuration.runtime import resolve_runtime_instance

released = {
    "source": {"username": secrets.token_hex(12), "password": secrets.token_hex(24)},
    "destination": {"token": secrets.token_hex(24)},
}
package = parse_configuration_package(json.loads(sys.argv[1]))
instance = resolve_runtime_instance(package, directory="/tmp/infrahub-sync/spec021-aci", released=released)
settings = instance.source.settings or {}
checks = {
    "username": settings.get("username") == released["source"]["username"],
    "password": settings.get("password") == released["source"]["password"],
    "destination": (instance.destination.settings or {}).get("token") == released["destination"]["token"],
    "no_credential_variable": not [name for name in os.environ if name.startswith("INFRAHUB_SYNC_CREDENTIAL_")],
}
print("SPEC021-ACI PASS" if all(checks.values()) else "SPEC021-ACI FAIL " + ",".join(k for k, v in checks.items() if not v))
"""


def aci_package() -> str:
    """The shipped ACI package as the JSON text the probe takes."""
    import yaml

    return json.dumps(yaml.safe_load(ACI_PACKAGE.read_text(encoding="utf-8")))


def test_the_aci_probe_passes_in_this_interpreter() -> None:
    """The probe and the shipped package are checked on the unit tier; the `docker` case adds the image."""
    result = subprocess.run(  # noqa: S603 -- this interpreter, with the fixed probe text of this module
        [sys.executable, "-c", ACI_PROBE, aci_package()],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={name: value for name, value in os.environ.items() if not name.startswith(REMOVED_PREFIX)},
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "SPEC021-ACI PASS"


@pytest.mark.docker
def test_the_worker_container_resolves_the_shipped_aci_package_credentials(docker_daemon: None, tmp_path: Path) -> None:
    """The released user name and password reach the ACI settings in the image, and no variable carries one."""
    del docker_daemon

    result = compose(
        ["run", "--rm", "--no-deps", "-T", "sync-worker", "python", "-c", ACI_PROBE, aci_package()],
        environment=container_environment(),
        env_files=(operator_file(tmp_path),),
    )

    assert result.returncode == 0, result.output
    assert "SPEC021-ACI PASS" in result.stdout, result.output
