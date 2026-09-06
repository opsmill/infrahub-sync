"""A collected secret never reaches a serialized configuration validation finding.

Diagnostics quote caller-declared keys, and the length bounds cut one before a later
whole-value match could find it. Every case drives the real router and the serialized response,
because that is the only place the producer's redaction and the boundary's redaction are both
in force.

Two ways in, for two different reasons. A secret shaped like a declared name reaches the
findings through the real producer, so those cases go through `build_app`. A secret that the
declared-name grammar cannot express is injected as a `ValidationFinding` behind the route
instead: the route's redaction of a report it did not produce is the thing under test, and each
such case first asserts the value is really collected and really present before the boundary.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")

from fastapi.testclient import TestClient

from infrahub_sync.configuration import capabilities as capabilities_module
from infrahub_sync.configuration import validation as validation_module
from infrahub_sync.configuration.models import ValidationFinding, safe_pointer_component
from infrahub_sync.execution import MIN_SECRET_LENGTH, collect_secret_values
from infrahub_sync.product_store import configs, local_product_projection
from infrahub_sync.product_store.configs import ValidationReport
from infrahub_sync.service import serve
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.config_routes import ConfigurationRoutes
from infrahub_sync.service.orchestration import Submission
from infrahub_sync.service.service import RunService

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.product_store import ProductProjection
    from infrahub_sync.product_store.models import ConfigurationVersion

BEARER = "administrator-bearer-token-0001"
SOURCE_SETTINGS = "/configuration/source/settings"

# The declared setting name grammar is `[a-z][a-z0-9_]*`, so a secret only reaches these
# findings as a key when it has that shape — which a hex or base32 token does.
SHORT_SECRET = "canary" + "a" * 4
BOUND_SECRET = "canary" + "b" * 58
LONG_SECRET = "canary" + "c" * 94

# A declared credential path deep enough that the pointer it builds passes the finding text
# bound, with a secret as its last component. The bound then cuts through that component, and a
# whole-value match at the final boundary cannot find the prefix left behind.
NESTED_SECRET = "canary" + "n" * 58
NESTED_BRANCHES = tuple(f"deep{letter * 55}" for letter in "xyz")
NESTED_CREDENTIAL_PATH = ".".join((*NESTED_BRANCHES, NESTED_SECRET))

# A declared adapter name longer than the finding text bound. The name is caller content and
# reaches the missing-adapter message whole, so the bound cuts through it unless it is redacted
# first. The grammar an adapter name must satisfy is `[a-z][a-z0-9_-]*`, with no length cap.
ADAPTER_NAME_SECRET = "canary" + "m" * 294

# Collected values whose shape a declared key cannot have. Each is exactly what
# `collect_secret_values` collects — for the URL that is the userinfo rather than the whole
# value — so a case here asserts the removal of a value the boundary really holds.
SHAPED_SECRETS = {
    "unicode": "cänary-sécret-" + "d" * 60,
    "quoted": "canary\"quoted'secret-" + "e" * 50,
    "escaped": "canary/slash~tilde-" + "f" * 50,
    "url-userinfo": "canaryuser:canarypassword0002",
}


def _package(*, undeclared: str, credential_path: str) -> dict[str, Any]:
    """One valid package whose two secret-named settings are declared at registration."""
    return copy.deepcopy(
        {
            "format_version": 1,
            "configuration": {
                "name": "secret-containment",
                "source": {
                    "name": "netbox",
                    "settings": {
                        "url": "https://demo.netbox.dev",
                        "token": {"$credential": "netbox-token"},
                        undeclared: "declared at registration",
                        credential_path: {"$credential": "netbox-token"},
                    },
                },
                "destination": {
                    "name": "infrahub",
                    "settings": {"url": "http://localhost:8000", "token": {"$credential": "infrahub-token"}},
                },
            },
            "credentials": {
                "netbox-token": {"provider": "env", "identifier": "NETBOX_TOKEN"},
                "infrahub-token": {"provider": "env", "identifier": "INFRAHUB_API_TOKEN"},
            },
        }
    )


def _nested_package() -> dict[str, Any]:
    """A valid package carrying a plain value at the deep path, so registration accepts it."""
    package = _package(undeclared="canaryplain", credential_path="canarypath")
    settings = package["configuration"]["source"]["settings"]
    del settings["canaryplain"], settings["canarypath"]
    leaf: dict[str, Any] = {NESTED_SECRET: "declared at registration"}
    for branch in reversed(NESTED_BRANCHES[1:]):
        leaf = {branch: leaf}
    settings[NESTED_BRANCHES[0]] = leaf
    return package


def _renderings(secret: str) -> set[str]:
    """Every known rendering a boundary must remove: raw, pointer-escaped, JSON and repr."""
    escaped = safe_pointer_component(secret)
    return {secret, escaped, json.dumps(escaped, ensure_ascii=True)[1:-1], repr(secret)[1:-1]}


def _injected_report(secret: str) -> ValidationReport:
    """One report carrying `secret` where a producer would put caller content.

    The pointer grammar refuses "/" and "~" outside an escape, so the location carries the
    escaped form and the message carries the raw one — the two shapes a real finding has.
    """
    return ValidationReport(
        config_id="injected",
        registry_version=1,
        package_checksum="c" * 64,
        findings=(
            ValidationFinding(
                code="undeclared-setting",
                severity="error",
                location=f"/configuration/source/settings/{safe_pointer_component(secret)}",
                message=f"setting {secret} is undeclared",
            ),
        ),
        destination_schema_fingerprint=None,
    )


class _Orchestration:  # pylint: disable=too-few-public-methods
    """The run service's collaborator, which no case here calls."""

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:  # noqa: ARG002, PLR6301
        return Submission(flow_run_id="unused", state="pending")


def _validate_injected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, report: ValidationReport) -> dict[str, Any]:
    """Serialize `report` through the real route, router and HTTP response.

    Only the application service behind the route is replaced. The route's own secret set, its
    redaction of the selected page, and the response model are the production ones.
    """
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"admin": {"token": BEARER, "administrator": True}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    secrets = tuple(dict.fromkeys((*collect_secret_values(), *resolver.secret_values)))
    projection = local_product_projection(tmp_path)

    class _Service:
        ConfigsError = configs.ConfigsError
        ConfigsRequestError = configs.ConfigsRequestError
        ConfigsValidationError = configs.ConfigsValidationError
        ConfigsNotFoundError = configs.ConfigsNotFoundError
        ConfigsStorageError = configs.ConfigsStorageError
        ConfigsInternalError = configs.ConfigsInternalError

        @staticmethod
        def validate(**_kwargs: object) -> ValidationReport:
            return report

    routes = ConfigurationRoutes(product_projection=projection, service=_Service(), secrets=secrets)
    application = create_app(RunService(projection, _Orchestration(), secrets=secrets), resolver, routes)  # ty: ignore[invalid-argument-type]
    response = TestClient(application).post(
        f"/configs/{report.config_id}/versions/{report.registry_version}/validate",
        headers={"Authorization": f"Bearer {BEARER}"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _longest_exposed_prefix(secret: str, body: str) -> int:
    """The length of the longest leading run of `secret` that `body` still contains.

    Redaction and the length bounds are both lossy, so "the whole value is absent" is too weak
    a property: a bound that cuts through an unredacted component leaves a prefix, and whole-value
    matching at any later boundary cannot find one. Zero means nothing of the value survived.
    """
    return next((cut for cut in range(len(secret), 0, -1) if secret[:cut] in body), 0)


def _capabilities(*, allowed: tuple[str, ...], credential_paths: tuple[str, ...]) -> dict[str, Any]:
    """The builtin table with netbox's source surface widened by the named settings."""
    table = dict(capabilities_module.BUILTIN_ADAPTER_CAPABILITIES)
    netbox = table["netbox"]
    table["netbox"] = replace(
        netbox,
        allowed_settings=netbox.allowed_settings | set(allowed),
        credential_setting_paths=(*netbox.credential_setting_paths, *credential_paths),
    )
    return table


def _named_source_package(adapter_name: str) -> dict[str, Any]:
    """A valid package whose source role names `adapter_name`."""
    package = _package(undeclared="canaryplain", credential_path="canarypath")
    settings = package["configuration"]["source"]["settings"]
    del settings["canaryplain"], settings["canarypath"]
    package["configuration"]["source"]["name"] = adapter_name
    return package


def _with_source_adapter(adapter_name: str) -> dict[str, Any]:
    """The builtin table plus a source adapter declared under `adapter_name`."""
    table = dict(capabilities_module.BUILTIN_ADAPTER_CAPABILITIES)
    table[adapter_name] = replace(table["netbox"], adapter_name=adapter_name)
    return table


def _install(monkeypatch: pytest.MonkeyPatch, table: dict[str, Any]) -> None:
    """Point the finding producer at one adapter declaration table."""
    monkeypatch.setattr(validation_module, "BUILTIN_ADAPTER_CAPABILITIES", table)


def _register_then_narrow(
    monkeypatch: pytest.MonkeyPatch,
    projection: ProductProjection,
    *,
    undeclared: str,
    credential_path: str,
) -> ConfigurationVersion:
    """Register while the declaration accepts both settings, then withdraw both from it.

    Revalidating a persisted version against the *current* declaration is the product's own
    contract, and it is the only way a registered package reports these two families.
    """
    _install(monkeypatch, _capabilities(allowed=(undeclared, credential_path), credential_paths=(credential_path,)))
    registered = configs.register(
        package=_package(undeclared=undeclared, credential_path=credential_path), projection=projection
    )
    _install(monkeypatch, _capabilities(allowed=(credential_path,), credential_paths=()))
    return registered.version


def _validate(
    monkeypatch: pytest.MonkeyPatch, projection: ProductProjection, version: ConfigurationVersion
) -> dict[str, Any]:
    """Drive the real serialized validation response for the one registered version."""
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"admin": {"token": BEARER, "administrator": True}}))
    app = serve.build_app(projection_factory=lambda: projection)
    response = TestClient(app).post(
        f"/configs/{version.config_id}/versions/{version.registry_version}/validate",
        headers={"Authorization": f"Bearer {BEARER}"},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every canary in place before `build_app` collects them."""
    monkeypatch.setenv("NETBOX_TOKEN", "netbox-token-value-0003")
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "infrahub-token-value-0004")
    monkeypatch.setenv("SYNC_SHORT_TOKEN", SHORT_SECRET)
    monkeypatch.setenv("SYNC_BOUND_TOKEN", BOUND_SECRET)
    monkeypatch.setenv("SYNC_LONG_TOKEN", LONG_SECRET)
    monkeypatch.setenv("SYNC_NESTED_TOKEN", NESTED_SECRET)
    monkeypatch.setenv("SYNC_ADAPTER_TOKEN", ADAPTER_NAME_SECRET)
    monkeypatch.setenv("SYNC_UNICODE_TOKEN", SHAPED_SECRETS["unicode"])
    monkeypatch.setenv("SYNC_QUOTED_TOKEN", SHAPED_SECRETS["quoted"])
    monkeypatch.setenv("SYNC_ESCAPED_TOKEN", SHAPED_SECRETS["escaped"])
    # Name-blind: the userinfo of any URL-shaped value is collected whatever the variable is called.
    monkeypatch.setenv("SYNC_ENDPOINT", f"https://{SHAPED_SECRETS['url-userinfo']}@example.invalid/api")


@pytest.mark.parametrize(
    ("description", "secret"),
    [("shorter than the bound", SHORT_SECRET), ("exactly the bound", BOUND_SECRET), ("longer than it", LONG_SECRET)],
)
def test_a_secret_named_setting_is_absent_from_the_serialized_findings(
    description: str,
    secret: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment: None,
) -> None:
    """A declared key carrying a collected value never reaches the response, at any length."""
    _ = description, environment
    projection = local_product_projection(tmp_path)
    version = _register_then_narrow(monkeypatch, projection, undeclared=secret, credential_path="canarypath")

    body = json.dumps(_validate(monkeypatch, projection, version))

    assert secret not in body, f"the whole secret reached the response:\n{body}"
    assert secret[:64] not in body, f"a bounded prefix of the secret reached the response:\n{body}"


def test_both_reproduced_finding_families_are_reported_and_carry_no_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: None
) -> None:
    """The two families the defect was reproduced in are still produced, and are safe."""
    _ = environment
    projection = local_product_projection(tmp_path)
    version = _register_then_narrow(monkeypatch, projection, undeclared=LONG_SECRET, credential_path=BOUND_SECRET)

    report = _validate(monkeypatch, projection, version)
    codes = {finding["code"] for finding in report["findings"]}
    body = json.dumps(report)

    assert "undeclared-setting" in codes, report
    assert "credential-path-not-declared" in codes, report
    for secret in (LONG_SECRET, BOUND_SECRET):
        assert secret not in body
        assert secret[:64] not in body


@pytest.mark.parametrize("shape", sorted(SHAPED_SECRETS), ids=sorted(SHAPED_SECRETS))
def test_a_collected_value_of_any_shape_is_removed_from_the_serialized_findings(
    shape: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: None
) -> None:
    """Escaping, Unicode, quote and URL-userinfo shapes leave the boundary in no rendering.

    The declared-name grammar is `[a-z][a-z0-9_]*`, so none of these shapes can reach a finding
    as a declared key. The finding is therefore injected: what is under test is the route's own
    redaction of a report it did not produce. Two guards keep the case honest — the value is
    asserted to be one the environment scan really collects, and the pre-redaction report is
    asserted to carry the renderings the response is then asserted not to.
    """
    _ = environment
    secret = SHAPED_SECRETS[shape]
    assert secret in collect_secret_values(), f"{secret!r} is not collected, so nothing would redact it"

    pre_redaction = _injected_report(secret)
    serialized_before = json.dumps([finding.model_dump(mode="json") for finding in pre_redaction.findings])
    exposed_before = {rendering for rendering in _renderings(secret) if rendering in serialized_before}
    assert exposed_before, f"the injected report carries no rendering of {secret!r}, so the case is vacuous"

    body = json.dumps(_validate_injected(monkeypatch, tmp_path, pre_redaction))

    for rendering in exposed_before:
        assert rendering not in body, f"{rendering!r} survived the boundary:\n{body}"


def test_two_secret_named_settings_stay_distinguishable_after_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: None
) -> None:
    """Redaction is lossy, so two pointers it maps together keep the existing location tag."""
    _ = environment
    projection = local_product_projection(tmp_path)
    version = _register_then_narrow(monkeypatch, projection, undeclared=LONG_SECRET, credential_path=BOUND_SECRET)

    report = _validate(monkeypatch, projection, version)
    redacted = [
        finding["location"] for finding in report["findings"] if finding["location"].startswith(SOURCE_SETTINGS)
    ]

    assert len(redacted) == len(set(redacted)), f"two redacted pointers collapsed onto one: {redacted}"
    assert all(location != SOURCE_SETTINGS for location in redacted)


def test_the_stable_machine_fields_survive_redaction_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: None
) -> None:
    """Every closed-vocabulary field leaves the boundary as the exact value that entered it.

    Compared against the pre-redaction report rather than against a set of permitted values: an
    environment holding a collected value equal to a code or a checksum would rewrite one of
    these into something an allowed-set check still accepts.
    """
    _ = environment
    pre_redaction = _injected_report(SHAPED_SECRETS["quoted"])

    report = _validate_injected(monkeypatch, tmp_path, pre_redaction)

    assert report["config_id"] == pre_redaction.config_id
    assert report["registry_version"] == pre_redaction.registry_version
    assert report["package_checksum"] == pre_redaction.package_checksum
    assert report["destination_schema_fingerprint"] == pre_redaction.destination_schema_fingerprint
    assert [finding["code"] for finding in report["findings"]] == [finding.code for finding in pre_redaction.findings]
    assert [finding["severity"] for finding in report["findings"]] == [
        finding.severity for finding in pre_redaction.findings
    ]


def test_a_withdrawn_adapter_declaration_never_exposes_an_adapter_name_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: None
) -> None:
    """Declaration drift reports the adapter name, and the message bound must not cut it.

    Registering against a declared adapter and revalidating after that declaration is withdrawn
    is the product's own current-declaration contract. The name is caller content, so a name
    longer than the finding text bound leaves a prefix unless it is redacted before bounding.
    """
    _ = environment
    projection = local_product_projection(tmp_path)
    _install(monkeypatch, _with_source_adapter(ADAPTER_NAME_SECRET))
    registered = configs.register(package=_named_source_package(ADAPTER_NAME_SECRET), projection=projection)
    _install(monkeypatch, dict(capabilities_module.BUILTIN_ADAPTER_CAPABILITIES))

    report = _validate(monkeypatch, projection, registered.version)
    body = json.dumps(report)

    assert any(finding["code"] == "missing-adapter" for finding in report["findings"]), report
    assert _longest_exposed_prefix(ADAPTER_NAME_SECRET, body) < MIN_SECRET_LENGTH, (
        f"a prefix of the declared adapter name survived the message bound:\n{body}"
    )


def test_a_deep_declared_credential_path_never_exposes_a_secret_component_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: None
) -> None:
    """The pointer bound must never cut through an unredacted declared component.

    The path is declared by the adapter capability rather than by the package, so it does not
    reach the finding through the declared-content walk. It is bounded all the same, and a cut
    through a secret component leaves a prefix no whole-value match can remove.
    """
    _ = environment
    projection = local_product_projection(tmp_path)
    _install(monkeypatch, _capabilities(allowed=(NESTED_BRANCHES[0],), credential_paths=()))
    registered = configs.register(package=_nested_package(), projection=projection)
    _install(
        monkeypatch,
        _capabilities(allowed=(NESTED_BRANCHES[0],), credential_paths=(NESTED_CREDENTIAL_PATH,)),
    )

    report = _validate(monkeypatch, projection, registered.version)
    body = json.dumps(report)

    assert any(finding["code"] == "inline-credential-value" for finding in report["findings"]), report
    assert _longest_exposed_prefix(NESTED_SECRET, body) < MIN_SECRET_LENGTH, (
        f"a prefix of the declared secret component survived the pointer bound:\n{body}"
    )
