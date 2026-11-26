"""Load the demo schema into Infrahub, create the import branch, then register the NetBox package.

The steps run in that order, and a schema load or a branch creation that fails stops the
preparation before anything is registered. The branch step runs only when a branch is named.
The registration is idempotent: a package the configuration already holds answers `unchanged`,
with that version, and other addresses add a version. Nothing here plans, applies, approves or
reads the Sync API's approval setting.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Literal, Protocol

import httpx
import yaml
from pydantic import ValidationError

from infrahub_sync.client import ClientInputError, ConfigMutationRequest
from infrahub_sync.demo import assets
from infrahub_sync.demo.errors import DemoAddressError, DemoBranchError, DemoSchemaError
from infrahub_sync.demo.package import local_package_text, package_name

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from infrahub_sync.client import RegisteredConfigurationResource, RegisteredVersionResource
    from infrahub_sync.client.models import ConfigurationSummaryResource

SchemaState = Literal["loaded", "unchanged"]
ConfigurationState = Literal["registered", "versioned", "unchanged"]
BranchState = Literal["created", "exists"]
# Whether a branch exists and holds the schema of the default branch; `unknown` on an Infrahub
# older than 1.11, which does not report it.
BranchCheck = Literal["missing", "same-schema", "other-schema", "unknown"]

DEFAULT_SCHEMA_TIMEOUT = 120.0
_REDACTED = "<redacted>"
_REQUEST_ARGUMENT = "request"
# Branch requests go to the default branch: Infrahub answers 404 to a request sent to a branch
# that does not exist yet. These are the requests `infrahub_sync.service.branch_review` sends;
# the demo package does not import the service package, which the base install lacks.
_DEFAULT_BRANCH = "main"
_BRANCH_LIST = "query DemoBranchList { Branch { name } }"
# `schema_differs_from_default_branch` exists from Infrahub 1.11.0.
_BRANCH_SCHEMA_LIST = "query DemoBranchSchemaList { Branch { name schema_differs_from_default_branch } }"
_BRANCH_CREATE = (
    "mutation DemoBranchCreate($name: String!) "
    "{ BranchCreate(wait_until_completion: true, data: {name: $name, sync_with_git: false}) { ok } }"
)
# The `user:password@` part of any URL in a message, removed before the message is shown.
_URL_USERINFO = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)[^/\s@]+@")


class ConfigRegistry(Protocol):
    """The three Sync client calls the registration uses (`SyncClient` provides them)."""

    def iter_configs(self, name: str | None = None, limit: int | None = None) -> Iterator[ConfigurationSummaryResource]:
        """Yield every configuration, newest first, reading every page."""
        ...

    def register_config(self, request: ConfigMutationRequest, idempotency_key: str) -> RegisteredConfigurationResource:
        """Register a configuration package."""
        ...

    def create_config_version(
        self, config_id: str, request: ConfigMutationRequest, idempotency_key: str
    ) -> RegisteredVersionResource:
        """Create, or return the identical, version of a configuration."""
        ...


class SchemaLoader(Protocol):
    """Load schema files into one Infrahub and report whether its schema changed."""

    def __call__(self, address: str, token: str, files: Sequence[Path], timeout: float) -> SchemaState:
        """Load `files` into the Infrahub at `address`."""
        ...


class BranchManager(Protocol):
    """Check and create branches in one Infrahub."""

    def check(self, address: str, token: str, name: str) -> BranchCheck:
        """Whether the Infrahub at `address` holds the branch `name`, and with which schema."""
        ...

    def create(self, address: str, token: str, name: str) -> None:
        """Create the branch `name` from the default branch."""
        ...


@dataclass(frozen=True)
class Registration:
    """The configuration the package was registered or versioned as."""

    config_id: str
    version: int
    state: ConfigurationState
    # How many configurations carry the package name, across every page; above 1, the newest
    # was versioned.
    same_name: int


@dataclass(frozen=True)
class PreparationResult:
    """What the preparation did; it holds no token."""

    schema: SchemaState
    schema_files: int
    name: str
    config_id: str
    version: int
    configuration: ConfigurationState
    same_name: int
    netbox_url: str
    infrahub_url: str
    branch: str | None = None
    branch_state: BranchState | None = None

    def as_dict(self) -> dict[str, object]:
        """The result as a JSON-ready mapping."""
        return asdict(self)


def _redact(text: str, token: str) -> str:
    text = _URL_USERINFO.sub(r"\g<scheme>", text)
    return text.replace(token, _REDACTED) if token else text


def _has_userinfo(url: str) -> bool:
    # Without a scheme, the authority starts at the beginning of the value.
    authority = url.partition("://")[2] if "://" in url else url
    return "@" in authority.partition("/")[0]


def refused_address_option(netbox_url: str, infrahub_url: str, infrahub_address: str | None) -> str | None:
    """Return the first address option that carries `user:password@`, or None when none does.

    The package and the output hold these addresses, so a credential in one would leak.
    """
    for option, value in (
        ("--netbox-url", netbox_url),
        ("--infrahub-url", infrahub_url),
        ("--infrahub-address", infrahub_address),
    ):
        if value is not None and _has_userinfo(value):
            return option
    return None


def load_schema(address: str, token: str, files: Sequence[Path], timeout: float) -> SchemaState:
    """Load the schema files into Infrahub and wait up to `timeout` seconds for convergence.

    Returns `loaded` when Infrahub reports a new schema hash and `unchanged` otherwise.
    Raises `DemoSchemaError` with Infrahub's answer, never the token, when the load fails.
    """
    # Imported here so the Sync CLI does not load the Infrahub SDK for any other command.
    # pylint: disable-next=import-outside-toplevel
    from infrahub_sdk import Config, InfrahubClientSync

    # pylint: disable-next=import-outside-toplevel
    from infrahub_sdk.exceptions import Error as InfrahubSDKError

    try:
        schemas = [yaml.safe_load(path.read_text(encoding="utf-8")) for path in files]
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        msg = f"cannot read the demo schema: {error}"
        raise DemoSchemaError(msg) from error
    seconds = math.ceil(timeout)
    client = InfrahubClientSync(address, config=Config(api_token=token, schema_converge_timeout=seconds))
    try:
        response = client.schema.load(schemas=schemas, wait_until_converged=True)
        if response.errors:
            msg = _redact(f"Infrahub at {address} refused the demo schema: {response.errors}", token)
            raise DemoSchemaError(msg)
        # The SDK only logs a warning when the wait times out, so ask Infrahub once more.
        converged = client.schema.in_sync()
    except (InfrahubSDKError, httpx.HTTPError) as error:
        msg = _redact(f"cannot load the demo schema into {address}: {error}", token)
        raise DemoSchemaError(msg) from None
    if not converged:
        msg = _redact(
            f"Infrahub at {address}: the demo schema was loaded but not applied within {seconds} seconds; "
            "re-run with a larger --schema-timeout",
            token,
        )
        raise DemoSchemaError(msg)
    return "loaded" if response.schema_updated else "unchanged"


class InfrahubBranches:
    """Lists and creates branches with the Infrahub SDK, sending each request to `main`."""

    @staticmethod
    def _request(
        address: str,
        token: str,
        query: str,
        variables: dict[str, str] | None = None,
        *,
        raise_query_error: bool = False,
    ) -> dict:
        """Send one GraphQL request to `main`.

        With `raise_query_error`, a query Infrahub refuses raises the SDK's `GraphQLError`, so the
        caller can retry with a query an older Infrahub accepts; every other failure raises
        `DemoBranchError`.
        """
        # Imported here so the Sync CLI does not load the Infrahub SDK for any other command.
        # pylint: disable-next=import-outside-toplevel
        from infrahub_sdk import Config, InfrahubClientSync

        # pylint: disable-next=import-outside-toplevel
        from infrahub_sdk.exceptions import Error as InfrahubSDKError

        # pylint: disable-next=import-outside-toplevel
        from infrahub_sdk.exceptions import GraphQLError

        client = InfrahubClientSync(address, config=Config(api_token=token))
        try:
            return client.execute_graphql(query=query, variables=variables, branch_name=_DEFAULT_BRANCH)
        except GraphQLError:
            if raise_query_error:
                raise
            msg = _redact(f"Infrahub at {address} refused a branch request", token)
            raise DemoBranchError(msg) from None
        except (InfrahubSDKError, httpx.HTTPError) as error:
            msg = _redact(f"cannot reach the branches of Infrahub at {address}: {error}", token)
            raise DemoBranchError(msg) from None

    def check(self, address: str, token: str, name: str) -> BranchCheck:
        """Whether the Infrahub at `address` holds the branch `name`, and with which schema."""
        # pylint: disable-next=import-outside-toplevel
        from infrahub_sdk.exceptions import GraphQLError

        try:
            data = self._request(address, token, _BRANCH_SCHEMA_LIST, raise_query_error=True)
            reports_schema = True
        except GraphQLError as error:
            # Infrahub before 1.11 does not know `schema_differs_from_default_branch`; any other
            # refusal must not turn into an `unknown` answer.
            if not any(
                "schema_differs_from_default_branch" in str(item.get("message", "") if isinstance(item, dict) else item)
                for item in error.errors
            ):
                msg = _redact(f"Infrahub at {address} refused a branch request", token)
                raise DemoBranchError(msg) from None
            data = self._request(address, token, _BRANCH_LIST)
            reports_schema = False
        for item in data.get("Branch") or []:
            if isinstance(item, dict) and item.get("name") == name:
                if not reports_schema:
                    return "unknown"
                return "other-schema" if item.get("schema_differs_from_default_branch") else "same-schema"
        return "missing"

    def create(self, address: str, token: str, name: str) -> None:
        """Create the branch `name` from `main`, with no synchronization to Git."""
        data = self._request(address, token, _BRANCH_CREATE, {"name": name})
        if not (data.get("BranchCreate") or {}).get("ok"):
            msg = _redact(f"Infrahub at {address} did not create the branch {name}", token)
            raise DemoBranchError(msg)


def _sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _mutation_request(text: str, reason: str | None) -> ConfigMutationRequest:
    try:
        return ConfigMutationRequest(package=yaml.safe_load(text), reason=reason)
    except ValidationError:
        raise ClientInputError(_REQUEST_ARGUMENT) from None


def register_package(client: ConfigRegistry, text: str, reason: str | None = None) -> Registration:
    """Register the package, or version the newest configuration with its name.

    The idempotency key depends on the operation, its target, the package text and the reason,
    as the Sync API's request fingerprint does, so a retry after a lost answer replays, a run
    with another reason is not refused as a conflict, and no key is used for two routes
    (research R-3).

    The state does not come from the answer's `created`: a key used before replays its first
    answer. A version above the configuration's latest version before the call was added
    (`versioned`); any other version already held the package (`unchanged`).
    """
    name = package_name(text)
    request = _mutation_request(text, reason)
    digest = _sha12(f"{text}\0{reason or ''}")
    existing = list(client.iter_configs(name=name))
    if not existing:
        registered = client.register_config(request, f"demo-prepare-register-{digest}")
        return Registration(
            config_id=registered.configuration.config_id,
            version=registered.version.registry_version,
            state="registered",
            same_name=0,
        )
    newest = existing[0]
    versioned = client.create_config_version(
        newest.config_id, request, f"demo-prepare-version-{newest.config_id}-{digest}"
    )
    added = versioned.version.registry_version > (newest.latest_version or 0)
    return Registration(
        config_id=versioned.version.config_id,
        version=versioned.version.registry_version,
        state="versioned" if added else "unchanged",
        same_name=len(existing),
    )


def prepare(
    client: ConfigRegistry,
    *,
    netbox_url: str,
    infrahub_url: str,
    infrahub_address: str | None,
    token: str,
    schema_timeout: float = DEFAULT_SCHEMA_TIMEOUT,
    reason: str | None = None,
    schema_loader: SchemaLoader | None = None,
    branch: str | None = None,
    branches: BranchManager | None = None,
) -> PreparationResult:
    """Load the demo schema, create `branch` when named, then register the package.

    `infrahub_address` is where this process reaches Infrahub; it defaults to `infrahub_url`,
    the address the worker uses. An address with `user:password@` raises `DemoAddressError`,
    an invalid reason raises `ClientInputError`, and a schema load or branch failure raises,
    before any Sync API call. The address and reason checks also run before the schema load.

    A branch that exists is kept, unless its schema differs from the schema of `main`: Infrahub
    does not copy a schema change of `main` into a branch that exists, so a plan on it would not
    find the demo kinds. That case raises `DemoBranchError` before the registration. An Infrahub
    older than 1.11 does not report a branch's schema; there, an existing branch is refused when
    this run changed the schema.
    """
    option = refused_address_option(netbox_url, infrahub_url, infrahub_address)
    if option is not None:
        msg = f"{option} must not carry a user name or password (user:password@)"
        raise DemoAddressError(msg)
    files = assets.schema_files()
    text = local_package_text(assets.netbox_package_text(), netbox_url, infrahub_url)
    # Refuse an invalid reason here, so it never leaves a schema loaded and nothing registered.
    _mutation_request(text, reason)
    address = infrahub_address or infrahub_url
    manager = branches if branches is not None else InfrahubBranches()
    branch_existed = bool(branch) and manager.check(address, token, branch) != "missing"
    loader = schema_loader if schema_loader is not None else load_schema
    schema = loader(address, token, files, schema_timeout)
    branch_state: BranchState | None = None
    if branch:
        if branch_existed and branch != _DEFAULT_BRANCH:
            # Checked again after the load, which may have changed the schema of `main`.
            check = manager.check(address, token, branch)
            if check == "other-schema" or (check == "unknown" and schema == "loaded"):
                msg = (
                    f"the branch {branch} does not hold the schema of main, so a plan on it cannot use the "
                    "demo schema; name a new branch with --branch, or rebase the branch in Infrahub"
                )
                raise DemoBranchError(msg)
        if not branch_existed:
            manager.create(address, token, branch)
        branch_state = "exists" if branch_existed else "created"
    registration = register_package(client, text, reason)
    return PreparationResult(
        schema=schema,
        schema_files=len(files),
        name=package_name(text),
        config_id=registration.config_id,
        version=registration.version,
        configuration=registration.state,
        same_name=registration.same_name,
        netbox_url=netbox_url,
        infrahub_url=infrahub_url,
        branch=branch,
        branch_state=branch_state,
    )
