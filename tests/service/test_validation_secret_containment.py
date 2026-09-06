"""A collected secret never reaches a serialized configuration validation finding.

Diagnostics quote caller-declared keys, and the component bound cuts a key at 64 characters,
so whole-value matching at a later boundary cannot find a longer secret. These cases drive the
real HTTP surface — `build_app`, the router, and the serialized response — because that is the
only place the producer's redaction and the boundary's redaction are both in force.
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
from infrahub_sync.execution import MIN_SECRET_LENGTH
from infrahub_sync.product_store import configs, local_product_projection
from infrahub_sync.service import serve
from infrahub_sync.service.auth import PRINCIPALS_ENV

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

# Shapes a declared key cannot have, collected from the environment all the same, so the
# serialized response is asserted free of every collected value rather than of one shape.
# A declared credential path deep enough that the pointer it builds passes the finding text
# bound, with a secret as its last component. The bound then cuts through that component, and a
# whole-value match at the final boundary cannot find the prefix left behind.
NESTED_SECRET = "canary" + "n" * 58
NESTED_BRANCHES = tuple(f"deep{letter * 55}" for letter in "xyz")
NESTED_CREDENTIAL_PATH = ".".join((*NESTED_BRANCHES, NESTED_SECRET))

UNCOLLECTABLE_SHAPES = {
    "SYNC_UNICODE_TOKEN": "cänary-sécret-" + "d" * 60,
    "SYNC_QUOTED_TOKEN": "canary\"quoted'secret-" + "e" * 50,
    "SYNC_ESCAPED_TOKEN": "canary/slash~tilde-" + "f" * 50,
    "SYNC_USERINFO_URL": "https://canaryuser:canarypassword0002@example.invalid/api",
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
    for name, value in UNCOLLECTABLE_SHAPES.items():
        monkeypatch.setenv(name, value)


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


def test_no_collected_value_of_any_shape_reaches_the_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: None
) -> None:
    """Escaping, Unicode, quote and URL-userinfo shapes are absent in every rendering."""
    _ = environment
    projection = local_product_projection(tmp_path)
    version = _register_then_narrow(monkeypatch, projection, undeclared=LONG_SECRET, credential_path=BOUND_SECRET)

    body = json.dumps(_validate(monkeypatch, projection, version))

    for value in UNCOLLECTABLE_SHAPES.values():
        for rendering in (value, json.dumps(value)[1:-1], repr(value)[1:-1]):
            assert rendering not in body, f"{rendering!r} reached the response:\n{body}"


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


def test_the_stable_machine_fields_are_unchanged_by_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: None
) -> None:
    """A code, a severity and the report's own identifiers are never rewritten."""
    _ = environment
    projection = local_product_projection(tmp_path)
    version = _register_then_narrow(monkeypatch, projection, undeclared=LONG_SECRET, credential_path=BOUND_SECRET)

    report = _validate(monkeypatch, projection, version)

    assert report["config_id"] == version.config_id
    assert report["registry_version"] == version.registry_version
    assert report["package_checksum"] == version.package_checksum
    for finding in report["findings"]:
        assert finding["severity"] in {"error", "warning"}
        assert "*" not in finding["code"], f"a stable machine field was rewritten: {finding}"


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
