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

<!-- Extracted from dev/specs/archive/001-plan-artifact-saved-apply on 2026-07-28 -->

### `identifiers` is not the convergence key

`identifiers` is the **DiffSync natural key**: it decides which source object is "the same object" as
which destination object during the comparison. It is not what makes a write converge. A convergent
write against Infrahub is keyed on the **destination kind's `human_friendly_id`**, read from the
destination schema — the upsert mutation carries `data["id"]` if known, else `data["hfid"]`.

The two answer different questions and routinely give different answers, so read keying behaviour off
the destination schema and never off `config.yml`. On `examples/netbox_to_infrahub/config.yml`, ten
mapping entries carry a `reference` inside their `identifiers`, while the number of destination kinds
whose *convergence key* crosses a relationship is five. Taking the configuration-side figure for the
keying figure overstated a keying risk by a factor of two, and the error survived several rounds of
review because both numbers are real counts of something.

Two practical consequences:

- A kind whose HFID crosses a relationship may not be able to render a client-side `hfid` at all, in
  which case whether the write converges depends on a destination-side uniqueness constraint covering
  the components as sent.
- A kind that declares no HFID is unkeyed as a matter of schema, not as a defect.

Both are handled on the apply path rather than at mapping time; see
[Planned writes and apply](planned-write-and-apply.md).

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
- [Planned writes and apply](planned-write-and-apply.md) — how identities and references are
  resolved when a saved plan is applied.
- User-facing configuration reference under `docs/docs/`.
