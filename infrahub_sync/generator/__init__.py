from __future__ import annotations

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

ATTRIBUTE_KIND_MAP = {
    "Text": "str",
    "String": "str",
    "TextArea": "str",
    "DateTime": "str",
    "HashedPassword": "str",
    "Number": "int",
    "Integer": "int",
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


def list_to_str(items: list[str]) -> str:
    """Convert a list into a string separated with comma"""
    return ", ".join(items)


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

    identifiers = [
        attr.name for attr in node.attributes if attr.unique and has_field(config, name=node.kind, field=attr.name)
    ]

    if not identifiers:
        return None

    return identifiers


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


def get_children(node: NodeSchema, config: SyncConfig) -> str | None:
    # rel.peer.lower() might now work in all cases we should have a better function to convert that
    children = {
        rel.peer.lower(): rel.name
        for rel in node.relationships
        if rel.cardinality == "many"
        and rel.kind == RelationshipKind.COMPONENT
        and has_field(config, name=node.kind, field=rel.name)
    }

    if not children:
        return None

    children_list = [f'"{key}": "{value}"' for key, value in children.items()]
    return "{" + ", ".join(children_list) + "}"


class _AttributeLike(Protocol):
    """Structural shape get_attribute_type_annotation() needs from an attribute-schema object."""

    kind: Any
    optional: bool
    default_value: Any


class _RelationshipLike(Protocol):
    """Structural shape get_relationship_type_annotation() needs from a relationship-schema object."""

    cardinality: str
    optional: bool


def get_attribute_type_annotation(item: _AttributeLike) -> str:
    """Return type annotation of schema attribute for Diffsync model."""
    annotation = ATTRIBUTE_KIND_MAP.get(item.kind, "str")
    if item.optional:
        annotation = f"{annotation} | None"
        if item.default_value is not None:
            # Format the default value based on its type
            if isinstance(item.default_value, str):
                annotation += f' = "{item.default_value}"'
            elif isinstance(item.default_value, (int, float, bool)):
                annotation += f" = {item.default_value}"
            else:
                annotation += f" = {item.default_value!r}"
        else:
            annotation += " = None"

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


def has_children(node: NodeSchema, config: SyncConfig) -> bool:
    return bool(get_children(config=config, node=node))


def render_template(template_file: Path, output_dir: Path, output_file: Path, context: dict[str, Any]) -> None:
    template_loader = jinja2.PackageLoader("infrahub_sync", "generator/templates")
    template_env = jinja2.Environment(
        loader=template_loader,
    )
    # Add custom filters to Jinja2
    template_env.filters["get_identifiers"] = get_identifiers
    template_env.filters["get_attributes"] = get_attributes
    template_env.filters["get_children"] = get_children
    template_env.filters["list_to_set"] = list_to_set
    template_env.filters["list_to_str"] = list_to_str
    template_env.filters["has_node"] = has_node
    template_env.filters["has_field"] = has_field
    template_env.filters["has_children"] = has_children
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
