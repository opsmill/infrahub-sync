from __future__ import annotations

import logging
import subprocess  # noqa: S404 -- fixed argv (sys.executable -m ruff), no shell, no user input
import sys
from typing import TYPE_CHECKING, Any, Protocol

import jinja2
from infrahub_sdk.schema import (
    NodeSchema,
    RelationshipKind,
)

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync import SyncConfig

logger = logging.getLogger(__name__)

ATTRIBUTE_KIND_MAP = {
    "Text": "str",
    "TextArea": "str",
    "DateTime": "str",
    "HashedPassword": "str",
    "Number": "int",
    "Boolean": "bool",
    "Checkbox": "bool",
    "List": "list[Any]",
}


def list_to_set(items: list[str]) -> str:
    """Convert a list in a string representation of a Set."""
    if not items:
        return "()"

    response = '"' + '", "'.join(items) + '"'
    if len(items) == 1:
        response += ","

    return "(" + response + ")"


def has_node(config: SyncConfig, name: str) -> bool:
    return any(item.name == name for item in config.schema_mapping)


def has_field(config: SyncConfig, name: str, field: str) -> bool:
    for item in config.schema_mapping:
        if item.name == name:
            for subitem in item.fields or []:
                if subitem.name == field:
                    return True
    return False


def get_identifiers(node: NodeSchema, config: SyncConfig) -> list[str] | None:
    """Return the identifiers that should be used by DiffSync."""

    config_identifiers = [
        item.identifiers for item in config.schema_mapping if item.name == node.kind and item.identifiers
    ]

    if config_identifiers:
        return config_identifiers[0]

    identifier_attributes = [
        attr for attr in node.attributes if attr.unique and has_field(config, name=node.kind, field=attr.name)
    ]
    identifiers = [attr.name for attr in identifier_attributes]

    if not identifiers:
        return None

    return identifiers


def warn_optional_auto_selected_identifiers(node: NodeSchema, config: SyncConfig) -> None:
    """Warn once when generation auto-selects optional identifier attributes."""
    configured_identifiers = any(
        item.identifiers for item in config.schema_mapping if item.name == node.kind and item.identifiers
    )
    if configured_identifiers:
        return

    optional_identifiers = [
        attr.name
        for attr in node.attributes
        if attr.unique and attr.optional and has_field(config, name=node.kind, field=attr.name)
    ]
    if optional_identifiers:
        logger.warning(
            "Auto-selected optional attribute(s) %s as identifiers for %s; "
            "configure identifiers explicitly for this node",
            optional_identifiers,
            node.kind,
        )


def get_attributes(node: NodeSchema, config: SyncConfig) -> list[str] | None:
    """Return the attributes that should be used by DiffSync."""
    attrs_attributes = [attr.name for attr in node.attributes if has_field(config, name=node.kind, field=attr.name)]
    rels_identifiers = [
        rel.name
        for rel in node.relationships
        if rel.kind != RelationshipKind.COMPONENT and has_field(config, name=node.kind, field=rel.name)
    ]

    identifiers = get_identifiers(node=node, config=config)
    if not identifiers:
        return None

    attributes = [item for item in rels_identifiers + attrs_attributes if item not in identifiers]

    if not attributes:
        return None

    return attributes


class _AttributeLike(Protocol):
    """Structural shape get_attribute_type_annotation() needs from an attribute-schema object."""

    kind: Any
    optional: bool
    default_value: Any


class _RelationshipLike(Protocol):
    """Structural shape get_relationship_type_annotation() needs from a relationship-schema object."""

    @property
    def cardinality(self) -> str: ...

    @property
    def optional(self) -> bool: ...


def get_attribute_type_annotation(item: _AttributeLike) -> str:
    """Return type annotation of schema attribute for Diffsync model."""
    annotation = ATTRIBUTE_KIND_MAP.get(item.kind, "str")
    if item.optional:
        # repr() emits a Python literal that reproduces the schema value exactly, so string
        # defaults containing quotes, newlines or backslashes stay valid and unchanged.
        annotation = f"{annotation} | None = {item.default_value!r}"

    return annotation


def get_relationship_type_annotation(item: _RelationshipLike) -> str:
    """Return type annotation of schema relationship for Diffsync model."""
    annotation = "str"
    if item.cardinality == "one":
        if item.optional:
            annotation = f"{annotation} | None = None"

    elif item.cardinality == "many":
        annotation = "list[str]"
        if item.optional:
            annotation = f"{annotation} | None"
        annotation += " = []"

    return annotation


def render_template(
    template_file: Path,
    output_dir: Path,
    output_file: Path,
    context: dict[str, Any],
    *,
    warn_optional_identifiers: bool = True,
) -> None:
    """Render one generator template, warning once per affected model render."""
    if warn_optional_identifiers and template_file.name == "diffsync_models.j2":
        for node in context["schema"].values():
            warn_optional_auto_selected_identifiers(node=node, config=context["config"])

    template_loader = jinja2.PackageLoader("infrahub_sync", "generator/templates")
    template_env = jinja2.Environment(
        loader=template_loader,
    )
    # Add custom filters to Jinja2
    template_env.filters["get_identifiers"] = get_identifiers
    template_env.filters["get_attributes"] = get_attributes
    template_env.filters["list_to_set"] = list_to_set
    template_env.filters["has_node"] = has_node
    template_env.filters["has_field"] = has_field
    template_env.filters["get_attribute_type_annotation"] = get_attribute_type_annotation
    template_env.filters["get_relationship_type_annotation"] = get_relationship_type_annotation

    template = template_env.get_template(str(template_file))

    rendered_tpl = template.render(**context)
    if output_file.suffix == ".py":
        rendered_tpl = format_generated_python(source=rendered_tpl, filename=str(output_file))
    output_filename = output_dir / output_file
    output_filename.write_text(rendered_tpl, encoding="utf-8")


class GeneratedCodeFormattingError(RuntimeError):
    """Raised when Ruff cannot format a generated Python file."""


def format_generated_python(source: str, filename: str) -> str:
    """Format generated Python with Ruff so identical schema input yields identical bytes.

    Runs ``ruff format`` in isolated mode with a fixed line length, so the output does not
    depend on any configuration file present in (or absent from) the caller's project.
    """
    command = [
        sys.executable,
        "-m",
        "ruff",
        "format",
        "--isolated",
        "--line-length",
        "120",
        "--stdin-filename",
        filename,
        "-",
    ]
    try:
        result = subprocess.run(  # noqa: S603
            command,
            input=source,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except OSError as exc:
        msg = f"Unable to run Ruff to format generated file {filename}: {exc}"
        raise GeneratedCodeFormattingError(msg) from exc
    except subprocess.TimeoutExpired as exc:
        msg = f"Ruff timed out formatting generated file {filename}"
        raise GeneratedCodeFormattingError(msg) from exc
    if result.returncode != 0:
        msg = f"Ruff failed to format generated file {filename}: {result.stderr.strip()}"
        raise GeneratedCodeFormattingError(msg)
    return result.stdout
