# Saved plan format 4

Draft decision record: research.md R7 (ADR 0022). ADR 0001's rules apply unless stated here.

## Directory

```text
<run_dir>/plan/
├── operations.jsonl   written first (unchanged)
├── report.jsonl       written second (new)
└── manifest.json      written last; its presence is the commit point (unchanged)
```

Each file is written to a temporary name and then renamed, as today (`plan/writer.py`).

## Manifest

`format_version` is 4. New object `report`:

```json
{
  "report": {
    "digest": "<sha256 hex of report.jsonl>",
    "size": 18234,
    "problems": 7,
    "left_out": 12,
    "left_out_with_problem": 6,
    "left_out_dependent": 6,
    "kept": 1
  }
}
```

The checksum is `sha256(canonical(manifest without plan_checksum, run_id, created_at) +
operations bytes)`, unchanged. It covers the report through `report.digest`.

## Report entries

One canonical JSON object per line with `entry` set to `problem`, `left_out` or `kept`, and the
fields of [data-model.md](../data-model.md). An empty report is a zero-byte file; `report` is
still present in the manifest with zero counts.

## Read and apply rules

| Format | Read and review | Apply |
| --- | --- | --- |
| 2 | yes | refused (today) |
| 3 | yes, with empty report | refused: `PlanFormatApplyUnsupportedError`, "plan again" |
| 4 | yes | yes, after the left-out count check |

Verification refuses a format-4 plan whose `report.jsonl` is missing, whose digest or size does
not match, or whose line counts differ from the manifest.
