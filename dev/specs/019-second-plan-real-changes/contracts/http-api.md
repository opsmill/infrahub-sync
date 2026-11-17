# Contract: Sync HTTP API changes

All changes are additive. The API stays `v3-unstable`.

## `GET /runs/{run_id}`

Each entry of `orchestration` for a `plan` or `sync` execution gains `plan_step`:

```json
"orchestration": [{
  "purpose": "plan",
  "submitted_at": "2026-11-16T10:00:00+00:00",
  "claimed_at": "2026-11-16T10:00:04+00:00",
  "reads": {"source": {"state": "succeeded", "started_at": "...", "ended_at": "...", "read_mode": "full"},
            "destination": {"state": "succeeded", "started_at": "...", "ended_at": "...", "read_mode": "full"}},
  "plan_step": {
    "running": null,
    "parts": {
      "waiting":   {"started_at": "2026-11-16T10:00:00+00:00", "ended_at": "2026-11-16T10:00:04+00:00"},
      "preparing": {"started_at": "2026-11-16T10:00:04+00:00", "ended_at": "2026-11-16T10:00:09+00:00"},
      "comparing": {"started_at": "...", "ended_at": "..."},
      "checking":  {"started_at": "...", "ended_at": "..."},
      "saving":    {"started_at": "...", "ended_at": "..."}
    }
  }
}]
```

| Case | `plan_step` |
| --- | --- |
| Execution created before this release, or with no document | `null` |
| Part that did not start | No entry |
| Part that runs | Entry with `started_at` only; `running` names it |
| Part that was running when the step failed, was cancelled or interrupted | Entry with `started_at` and `stopped_at` (the verdict time), or `started_at` only when no verdict exists yet |
| Execution completed, document still names a running part | No entry for that part |
| `claimed_at` before `submitted_at` (clock skew) | No `waiting` entry |

No entry holds a zero duration that was not measured. No field holds an error message or a data
value.

The API reference marks `plan_step` and each of its parts as subject to change in a later
release.

## `GET /runs/{run_id}/plan`

Unchanged in shape. An operation of a format 5 plan can hold a one-peer relationship with
`peers: []`.

## `GET /runs`

Unchanged. List items do not carry `plan_step`.
