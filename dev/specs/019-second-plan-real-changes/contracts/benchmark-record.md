# Contract: benchmark record and results page

## Record fields

A v3 record of `uv run invoke bench.run` holds, in addition to the current fields:

```json
{
  "plan_parts": {"waiting": 2.1, "preparing": 6.4, "source_read": 41.0,
                 "destination_read": 38.2, "comparing": 12.5, "checking": 1.9, "saving": 3.3},
  "source_read_mode": "full",
  "destination_read_mode": "full",
  "plan_bytes": 1843207,
  "operations_digest": "9f2c…",
  "left_out": 0,
  "kept": 0,
  "problems": 0
}
```

- A part without both times is left out of `plan_parts`.
- `builtin_deletes` is removed.
- For a result that is not `ok`, the new fields are `null`.

## Checks

| Scenario | Check |
| --- | --- |
| `warm` (no change) | The plan holds 0 operations (`summary.total == 0`). No delete is excluded by name. |
| `changed` | The actions equal the expected changes, deletes included. |
| A run whose plan leaves records out | The record holds the counts of the plan report; the apply summary is compared only when an apply ran. |

## Results page

`docs/docs/development/guides/netbox-benchmark-results.md` holds:

- the commit measured, the Infrahub version and the machine description;
- one table per tier with, for each of the 3 scenarios and 3 repetitions: `plan_seconds`, each
  part of `plan_parts`, the read modes, `plan_bytes` and `operations_digest`;
- the medians, and the verdict of FR-034 for tier M.
