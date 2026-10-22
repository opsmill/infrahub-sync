# Contract: Who last changed a value, in the stored plan and the review

Field rules are in [data-model.md](../data-model.md). Format rules follow
`dev/adr/0001-saved-plan-artifact-format.md:84-112`.

## Stored plan directory

```text
plan/
├── operations.jsonl     # unchanged, format 3
├── last_changes.jsonl   # new
└── manifest.json        # gains `lineage` and `last_changes`
```

## `manifest.json` additions

```json
{
  "lineage": {
    "import_account": {"name": "netbox", "id": "<account id>"},
    "source": {"kind": "CoreAccount", "name": "netbox"},
    "owner": {"kind": "CoreAccountGroup", "name": "network-team"},
    "protect": false
  },
  "last_changes": {
    "path": "last_changes.jsonl",
    "sha256": "<hex digest>",
    "count": 1,
    "metadata_read": true
  }
}
```

## `last_changes.jsonl` record

```json
{"operation_id": "<id>", "field": "description", "field_type": "attribute",
 "updated_by": {"id": "<account id>", "display_label": "Jane Doe"},
 "updated_at": "2026-10-21T09:14:03Z"}
```

A record with `"updated_by": null` or `"updated_at": null` is displayed as "not recorded".

## Compatibility

- `format_version` stays 3. A plan without the two manifest fields is a plan made before this
  feature; review shows no last-change column and apply is unchanged.
- The checksum covers both manifest fields directly and the sidecar through `last_changes.sha256`.
  Review and apply refuse a plan whose sidecar digest does not match.

## Review display (`runs plan` and `PlanResource`)

- For each update, each changed field shows `updated_by` (display label) and `updated_at`, or
  "not recorded".
- When `lineage.owner` is not `null`, the summary states: "The owner setting applies only to
  values this run creates."
