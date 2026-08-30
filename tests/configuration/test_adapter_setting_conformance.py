"""Static drift guards for bundled adapter setting declarations."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from infrahub_sync.configuration import BUILTIN_ADAPTER_CAPABILITIES

_ADAPTERS_DIRECTORY = Path(__file__).parents[2] / "infrahub_sync" / "adapters"

# PeeringManager extends GenericREST, so both modules form its effective runtime surface.
_RUNTIME_MODULES = {adapter_name: (adapter_name,) for adapter_name in BUILTIN_ADAPTER_CAPABILITIES} | {
    "peeringmanager": ("genericrestapi", "peeringmanager")
}

# These runtime knobs are intentionally unavailable to registered packages. Keep each
# refusal with the module that reads it so inherited reads remain accounted for.
_DELIBERATELY_REFUSED_BY_MODULE = {
    "genericrestapi": frozenset(
        {
            "params",
            "password_env_vars",
            "token_env_vars",
            "url_env_vars",
            "username_env_vars",
        }
    ),
    "prometheus": frozenset({"headers", "params"}),
}

# These adapters forward the closed registered surface to optional clients. This proves
# only that the local boundary remains a **settings call; upstream signature conformance
# is deliberately deferred to INFP-654.
_DYNAMIC_FORWARDING_BOUNDARIES = {
    "ipfabricsync": ("IPFClient", frozenset({"auth", "base_url", "verify_ssl"})),
    "slurpitsync": ("slurpit.api", frozenset({"api_key", "token", "url", "verify_ssl"})),
}


def _is_settings_reference(node: ast.AST) -> bool:
    return (isinstance(node, ast.Name) and node.id == "settings") or (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and node.attr == "settings"
    )


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_setting_method_access(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr not in {"get", "setdefault"} or not _is_settings_reference(node.func.value):
        return None
    if not node.args:
        return None
    return _literal_string(node.args[0])


def _registered_credential_access(node: ast.AST) -> str | None:
    """Return the setting consumed through the shared credential boundary."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        return None
    if node.func.id != "select_runtime_credential" or len(node.args) < 2:
        return None
    if not _is_settings_reference(node.args[0]):
        return None
    return _literal_string(node.args[1])


def _literal_setting_accesses(module_name: str) -> frozenset[str]:
    tree = ast.parse((_ADAPTERS_DIRECTORY / f"{module_name}.py").read_text(encoding="utf-8"))
    setting_names: set[str] = set()

    for node in ast.walk(tree):
        if (setting_name := _literal_setting_method_access(node)) is not None:
            setting_names.add(setting_name)

        if (
            isinstance(node, ast.Subscript)
            and _is_settings_reference(node.value)
            and (setting_name := _literal_string(node.slice)) is not None
        ):
            setting_names.add(setting_name)

        if isinstance(node, ast.Compare) and any(isinstance(operator, (ast.In, ast.NotIn)) for operator in node.ops):
            operands = (node.left, *node.comparators)
            if any(_is_settings_reference(operand) for operand in operands):
                setting_names.update(
                    setting_name for operand in operands if (setting_name := _literal_string(operand)) is not None
                )

        # Credential selection is a shared authority boundary.  Its setting name is
        # still a real adapter consumer, even though the helper (rather than the
        # adapter) performs the Mapping lookup.
        if (setting_name := _registered_credential_access(node)) is not None:
            setting_names.add(setting_name)

    return frozenset(setting_names)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _settings_forwarding_targets(module_name: str) -> frozenset[str]:
    tree = ast.parse((_ADAPTERS_DIRECTORY / f"{module_name}.py").read_text(encoding="utf-8"))
    return frozenset(
        call_name
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and any(keyword.arg is None and _is_settings_reference(keyword.value) for keyword in node.keywords)
        and (call_name := _call_name(node.func)) is not None
    )


@pytest.mark.parametrize("adapter_name", BUILTIN_ADAPTER_CAPABILITIES)
def test_bundled_literal_setting_accesses_are_declared_or_deliberately_refused(adapter_name: str) -> None:
    capability = BUILTIN_ADAPTER_CAPABILITIES[adapter_name]
    runtime_modules = _RUNTIME_MODULES[adapter_name]
    runtime_settings = frozenset().union(*(_literal_setting_accesses(name) for name in runtime_modules))
    deliberately_refused = frozenset().union(
        *(_DELIBERATELY_REFUSED_BY_MODULE.get(name, frozenset()) for name in runtime_modules)
    )

    if adapter_name not in _DYNAMIC_FORWARDING_BOUNDARIES:
        assert runtime_settings == capability.allowed_settings | deliberately_refused
        return

    expected_target, closed_surface = _DYNAMIC_FORWARDING_BOUNDARIES[adapter_name]
    assert runtime_settings <= capability.allowed_settings | deliberately_refused
    assert capability.allowed_settings == closed_surface
    assert _settings_forwarding_targets(adapter_name) == {expected_target}


# Implementing incremental extraction means overriding both of these; the base class raises
# unless cursor_tier_for stays NONE.
_INCREMENTAL_OVERRIDES = frozenset({"cursor_tier_for", "list_changed_since"})


def _defined_function_names(module_name: str) -> frozenset[str]:
    tree = ast.parse((_ADAPTERS_DIRECTORY / f"{module_name}.py").read_text(encoding="utf-8"))
    return frozenset(node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))


@pytest.mark.parametrize("adapter_name", BUILTIN_ADAPTER_CAPABILITIES)
def test_incremental_qualification_matches_the_runtime_overrides(adapter_name: str) -> None:
    # `incremental_extraction=True` must co-occur with the incremental-extraction overrides,
    # so a new incremental adapter cannot keep the unqualified-optional-feature warning
    # firing forever, and a declaration cannot qualify a feature its runtime never reads.
    capability = BUILTIN_ADAPTER_CAPABILITIES[adapter_name]
    runtime_modules = _RUNTIME_MODULES[adapter_name]
    defined = frozenset().union(*(_defined_function_names(name) for name in runtime_modules))

    assert capability.incremental_extraction == (defined >= _INCREMENTAL_OVERRIDES)


@pytest.mark.parametrize("adapter_name", BUILTIN_ADAPTER_CAPABILITIES)
def test_dynamic_settings_forwarding_is_explicit(adapter_name: str) -> None:
    boundary = _DYNAMIC_FORWARDING_BOUNDARIES.get(adapter_name)
    expected_targets = frozenset() if boundary is None else frozenset({boundary[0]})

    targets = _settings_forwarding_targets(adapter_name)

    assert targets == expected_targets
