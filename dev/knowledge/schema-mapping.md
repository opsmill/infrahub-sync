# Schema mapping

> Part of: `dev/knowledge/` | Related: [Adapter anatomy](adapter-anatomy.md), [Adding an adapter](../guides/adding-an-adapter.md)

The `schema_mapping` block in `config.yml` is the declarative contract between a source
system and a destination schema. It says which source resources become which destination
models, which fields carry over, how objects are uniquely identified, and which records to
skip or rewrite. The generator turns each entry into a DiffSync model class, and adapters
read the same entries at load time. The pydantic models behind it live in
`infrahub_sync/__init__.py`.

## A mapping entry

Each list item is a `SchemaMappingModel`:

```yaml
schema_mapping:
  - name: InfraDevice          # destination model / kind
    mapping: dcim.devices      # source resource path
    identifiers: ["name"]      # natural key
    filters:                   # optional: which records to keep
      - field: status.value
        operation: "=="
        value: active
    transforms:                # optional: rewrite fields
      - field: name
        expression: "{{ name | lower }}"
    fields:                    # field-by-field mapping
      - name: name
        mapping: name
```

- `name` — the destination model/kind. Must match a node in the destination schema.
- `mapping` — the source resource. Its meaning is adapter-specific: a pynetbox endpoint
  path like `dcim.devices`, a REST collection, a key in a JSON document.
- `identifiers` — the fields that uniquely identify the object. This is the DiffSync natural
  key; it must be stable across runs and present on both sides.
- `filters` — records must match **all** filters to be loaded (see below).
- `transforms` — applied after filtering, in order.
- `fields` — the field mappings (see below).

## Fields

Each field is a `SchemaMappingField` with four levers, checked in this order:

- `static` — assign a literal value, ignoring the source object entirely.
- `mapping` (without `reference`) — read a value from the source record by dot-notation
  path (`get_value` walks dicts and objects, e.g. `status.value`).
- `mapping` + `reference` — treat the value as a foreign key into another mapped model.
  The adapter resolves it to that peer's `unique_id`. If the destination field is a list,
  every referenced id is resolved and collected.
- `name` — always the destination field name.

References are how relationships are rebuilt on the destination. Because a reference points
at another model by its identifiers, the referenced model must also appear in
`schema_mapping`, and it must be written first — which is what `order` controls.

## Identifiers, references, and write order

`order` is the list of models in the sequence they are written to the destination. It is
**auto-computed** from the `reference` edges in `schema_mapping` (a model is written after
everything it references), so you normally omit it. Override it only when the computed order
is wrong — for example to break a cycle. The `local_id` carried on each loaded record is the
source primary key that reference resolution matches against.

## Filters

Filters drop records before they enter the store. A record is kept only if it passes every
filter. Each `SchemaMappingFilter` has a `field` (dot-notation path), an `operation`, and a
`value`. The supported operations are:

| Category | Operations |
|----------|------------|
| Equality | `==`, `!=` |
| Ordering (numeric) | `>`, `<`, `>=`, `<=` |
| Membership | `in`, `not in`, `contains`, `not contains` |
| Presence | `is_empty`, `is_not_empty` |
| Pattern | `regex` |
| Network | `is_ip_within` |

`is_empty` / `is_not_empty` ignore `value`; the ordering operations coerce both sides to
`int`. The full operator table is `FILTERS_OPERATIONS` in `infrahub_sync/__init__.py`.

## Transforms

Transforms rewrite a field with a Jinja2 expression after filtering. Each
`SchemaMappingTransform` names a `field` and an `expression`. Expressions run in a Jinja2
`NativeEnvironment`, so the result keeps its native Python type (`list`, `dict`, `bool`,
`int`, `str`) instead of being stringified, and `StrictUndefined` makes a missing key fail
fast rather than silently producing an empty string. The whole record is available as
template context:

```yaml
transforms:
  - field: name
    expression: "{{ name | upper }}"
  - field: tags
    expression: "{{ tags + ['synced'] }}"
```

## See also

- [Adapter anatomy](adapter-anatomy.md) — how an adapter reads these entries at load time.
- [Adding an adapter](../guides/adding-an-adapter.md) — writing a mapping for a new system.
- User-facing configuration reference under `docs/docs/`.
