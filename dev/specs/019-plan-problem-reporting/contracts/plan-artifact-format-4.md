# Saved plan format 4

Format 4 is format 3 plus a report. Decision record: 0022 ([research R9](../research.md)).

## Files

| File | Format 3 | Format 4 |
| --- | --- | --- |
| `plan/manifest.json` | yes | yes, `format_version: 4`, plus `report` and, when the check could not run, `dropdown_values_checked: false` |
| `plan/operations.jsonl` (the operation file format 3 writes) | yes | unchanged; holds no operation of a left-out record and no relationship value of a kept record's dropped relationship |
| `plan/last_changes.jsonl` (spec 014) | yes | unchanged |
| `plan/report.jsonl` | no | new; canonical lines, see data-model.md "Plan report" |

The exact file names of format 3 are those in `infrahub_sync/plan/writer.py`; format 4 adds only
`report.jsonl`.

## Manifest `report` object

```json
{
  "digest": "<sha256 hex of report.jsonl>",
  "size": 18234,
  "problems": 9,
  "left_out": 15,
  "left_out_with_problem": 9,
  "left_out_dependent": 6,
  "kept": 2
}
```

The manifest also keeps every format 3 key, including `lineage` (spec 014) when present.

## Rules

- The plan checksum covers the manifest, so it covers `report.digest`.
- A reader refuses a format 4 plan whose report is missing, whose digest does not match, or whose
  `left_out` differs from the number of `left_out` lines: `PlanVerificationError`, before any
  write.
- A plan with no problems still writes an empty `report.jsonl` and a `report` object with zeros, so
  every format 4 plan has one shape.
- `SUPPORTED_FORMAT_VERSIONS` becomes `{2, 3, 4}`. Formats 2 and 3 stay readable for review. Apply
  refuses them with `PlanFormatApplyUnsupportedError`.
- A canonical plan fingerprint (ADR 0007) of a format 4 plan covers the report.
