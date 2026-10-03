---
title: "Run a NetBox benchmark cell"
---

## Run a NetBox benchmark cell

> Part of: Develop > Guides | Related: [NetBox benchmark tiers](netbox-benchmark-tiers.md)

The manual benchmark compares v2 and v3 against the same local NetBox tier and a fresh
Infrahub 1.11.3 destination. It runs outside CI. Tier L contains 87,815 mapped source
objects; seeding and benchmark runs can take hours. Each repetition has a six-hour
limit for preparation and sync. Cleanup has a separate five-minute limit. A timeout or validation failure produces an invalid result with no valid time.

### Prepare the tier

Install the development environment with the `dev`, `prefect`, and `service` extras.
Use a Linux host with Docker running; v2 memory sampling reads `/proc`.
Follow [NetBox benchmark tiers](netbox-benchmark-tiers.md) to
seed and save the source database:

```bash
uv run invoke netbox.seed --tier S
uv run invoke netbox.dump --tier S
```

Use `M` or `L` for a larger tier. The runner restores the verified dump; it never seeds
instead of restoring. Dumps remain under `.netbox/dumps/` after a run. Keep generated
database files private.

### Run one cell

Remove the local development Sync stack before a benchmark:

```bash
uv run invoke destroy
```

This command removes the development containers and their volumes. Stopped worker
containers also block the runner. The benchmark requires exclusive ownership of the
fixed development stack; it checks for existing workers before restoring NetBox or
rebuilding Infrahub. It does not reuse an existing API or worker. The runner builds the Sync
image from this checkout and uses the existing start tasks for its API and worker. It
requires `INFRAHUB_SYNC_API_TOKEN` in your environment, matching the local stack's API
principal. It creates an isolated destination stack from the preview Compose files, pinned to
Infrahub 1.11.3. It uses the preview connection settings and local NetBox settings,
including personal connection overrides. The runner enforces the OpsMill image repository
and version, ignoring preview image overrides. Keep the preview stack stopped so its ports are available.

```bash
uv run invoke bench.run --line v3 --tier S --scenario cold --repetitions 3
uv run invoke bench.run --line v3 --tier S --scenario warm --repetitions 3
uv run invoke bench.run --line v3 --tier S --scenario changed --repetitions 3
```

Every repetition restores NetBox and rebuilds Infrahub empty, then loads the schema
library shipped with this checkout. A `cold` cell measures the first sync. A `warm` cell
first completes and checks an unmeasured cold sync, then measures a second sync without
source changes. A `changed` cell starts from the same checked cold state, applies the
fixed 1% changes, then measures a sync. Repetitions do not inherit earlier mutations.
The runner removes its containers and volumes when each repetition finishes, including
on failure. It retains the source dumps, v2 environments, and results.

For v2, choose the latest released tag on `main` when starting the benchmark and pass it
explicitly. The runner fetches refs, verifies that the release or commit belongs to
`main`, and builds an isolated environment under `.netbox/bench/v2-<ref>/`.

```bash
uv run invoke bench.run --line v2 --v2-ref <released-tag> --tier S --scenario cold --variant full
uv run invoke bench.run --line v2 --v2-ref <released-tag> --tier S --scenario warm --variant parallel
uv run invoke bench.run --line v2 --v2-ref <released-tag> --tier S --scenario changed --variant incremental
```

The v2 variants are `full` (full extraction, serial loading and writes), `parallel`
(full extraction with the release's parallel option and concurrent reads), and `incremental`
(incremental extraction, only for `changed`). The incremental baseline uses a full read.
v3 always uses `full` and submits a confirmed sync through the Sync API. The v2 copy
uses the current checkout's mapping entries without adaptation and generates the
release's adapter classes before measurement. Only URLs and
credential settings are supplied for the local services. If a release cannot run the
mapping or flags, the cell fails; the runner never changes sync behavior to obtain a
benchmark result.

Credentials come from the existing local task environment settings and are passed to
processes or containers through their environment. The registered package contains
credential references. The runner suppresses provider output and legacy token banners;
result errors describe the failed operation without exposing credentials.
The legacy v2 adapter generator needs the destination credential in configuration
settings. The runner supplies it in a file readable only by your user during generation,
then removes it before measuring sync, including when generation fails.

### Read results

Each measured repetition appends one JSON object to the git-ignored
`.netbox/benchmarks/results.jsonl`. The fields include:

- `line`, `version`, `commit`, `tier`, `scenario`, `variant`, and `repetition` identify
  the input and installed line. A current checkout identity ending in `-dirty` indicates
  uncommitted changes. `harness_commit` records the current checkout for both lines,
  separately from the installed release commit. `mapping_sha256` hashes the canonical
  mapping entries, including uncommitted edits. `infrahub_version` is read from the running server and
  checked against 1.11.3. `infrahub_image_id` records its immutable Docker image ID;
  `infrahub_image_digest` records the OpsMill repository digest when available.
  The Infrahub identity fields remain null if setup fails before verification.
- `status` is `ok`, `failed`, or `timed_out`. `wall_seconds` is valid only for `ok`.
  `wall_seconds` measures submission/process start through a finished sync, excluding
  preparation, validation, and cleanup. Only reaching the six-hour cell limit produces
  `timed_out`; earlier setup command timeouts produce `failed` with a separate error.
- v3 `plan_seconds` and `apply_seconds` split the recorded run interval at the published
  plan-review artifact timestamp. These intervals include queue/load and verification or
  checkpoint overhead; they are not CPU timings of the diff and write loops.
- `peak_rss_mb` is the maximum MiB observed once per second: the v2 sync process's
  resident memory or the v3 worker container's `docker stats` memory usage.
- `netbox_counts` includes every tier endpoint plus its foundation kinds and skip
  cases. `infrahub_counts` includes every mapped destination kind, with physical,
  virtual, and LAG interfaces split and both VRF projections counted.
- `machine` records CPU model/count, physical memory, OS, and architecture.
  `actions`, `action_evidence`, `run_id`, and `error` explain validation evidence.
  `skipped_deletes` records v3 plan deletions that apply did not dispatch.

Cold and warm counts must match the mapped tier, including Infrahub's preserved default
IP namespace. A warm cell must also show zero applied actions. Changed cells compare
per-kind applied actions against `.netbox/changes/<tier>.expected.json`, translating
prefix VRF moves into deletes and creates. v3 checks its saved plan against the
apply summary and excludes deletions disclosed as not executed. The current v3 engine
does not execute planned deletes, so a changed cell requiring deletes remains invalid;
the runner does not change this behavior. All current v3 changed cells require deletes
and therefore fail. A warm v3 plan can contain a default IP namespace deletion that is
not dispatched; the runner records it in `skipped_deletes` and checks zero executed actions.
v2 parses complete per-kind summaries or successful per-object action
status lines from a finished sync. For a parallel no-op, all mapped tier logs and the
completed release footer establish zero writes. The runner otherwise records count
deltas. Deltas cannot prove updates or offsetting writes, so a warm or changed cell
without a complete summary remains invalid. A failed or timed-out result clears all
reported timings.

```bash
uv run invoke bench.report
```

The table compares medians from valid samples only. It keeps versions, commits, and v2
variants separate. It pools repetitions and pairs lines only when `harness_commit` and
`mapping_sha256` both match. Older records without provenance remain in a separate
unknown group. A missing valid line appears as an empty comparison cell. An invalid
result is evidence of a failed benchmark cell, never a performance measurement.
