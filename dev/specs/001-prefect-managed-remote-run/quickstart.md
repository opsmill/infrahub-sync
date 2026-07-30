# Quickstart: Validating the Prefect-Managed Remote Run (Developer Preview)

Runnable validation scenarios proving the feature end-to-end. Contracts:
[`contracts/execution-surface.md`](contracts/execution-surface.md),
[`contracts/prefect-flow.md`](contracts/prefect-flow.md),
[`contracts/run-result-and-errors.md`](contracts/run-result-and-errors.md). Entity
semantics: [`data-model.md`](data-model.md).

> Trusted development environment only. The default self-hosted Prefect Server must
> never be exposed to the public internet (spec constraint; the example README owns the
> user-facing caveat).

## Prerequisites

- Python 3.10–3.13 (probes ran on 3.12) and `uv`.
- A reachable Infrahub with the `InfraDevice(name, type)` schema loaded and **zero**
  `InfraDevice` objects (the lab instance at `http://localhost:8000` per R-3, or any
  disposable instance loaded with `examples/prefect_remote_run/schemas/infra_device.yml`).
- Credentials in the runner environment only: `INFRAHUB_ADDRESS`, `INFRAHUB_API_TOKEN`
  (never in files, parameters, or results — DBR-006).
- Port 4200 free for the Prefect server.

## Setup

```bash
# R-1: plain `uv sync` does not install dev tooling
uv sync --extra dev

# dev/test environment for the optional integration (D005 + D006 pins come from the extra)
uv sync --extra dev --extra prefect
```

## Scenario 0 — Base install stays Prefect-free (DBA-001, SC-006)

In a clean venv **without** the extra:

```bash
uv venv /tmp/base-venv && uv pip install -p /tmp/base-venv/bin/python .
/tmp/base-venv/bin/python - <<'EOF'
import sys
import infrahub_sync, infrahub_sync.cli, infrahub_sync.execution
assert not any(m == "prefect" or m.startswith("prefect.") for m in sys.modules), "prefect leaked"
print("OK: no prefect modules loaded")
EOF
/tmp/base-venv/bin/infrahub-sync --help
/tmp/base-venv/bin/infrahub-sync list --directory examples/
```

**Expected**: all commands succeed; `prefect` is not importable in that venv (any
import would fail loudly); the assertion passes.

## Scenario 1 — Serve and run a remote plan (DBA-002, DBA-003, DBA-004; US1)

Terminal A — server (default local database, built-in UI):

```bash
uv run prefect server start            # serves http://127.0.0.1:4200
```

Terminal B — served deployment:

```bash
export PREFECT_API_URL="http://127.0.0.1:4200/api"
export INFRAHUB_SYNC_CONFIG_DIRECTORY="$PWD/examples"
export INFRAHUB_ADDRESS="http://localhost:8000"
export INFRAHUB_API_TOKEN="<runner-env token>"
uv run python -m infrahub_sync.orchestration.serve
```

**Expected**: serve validates the config directory at startup (unset it to see the
serve-start failure naming `INFRAHUB_SYNC_CONFIG_DIRECTORY`);
`GET $PREFECT_API_URL/deployments/name/infrahub-sync/infrahub-sync` returns
`status: "READY"` with `enforce_parameter_schema: true`.

Terminal C — remote caller (pure REST):

```bash
DEP_ID=$(curl -s "$PREFECT_API_URL/deployments/name/infrahub-sync/infrahub-sync" | jq -r .id)
RUN_ID=$(curl -s -X POST "$PREFECT_API_URL/deployments/$DEP_ID/create_flow_run" \
  -H "Content-Type: application/json" \
  -d '{"parameters": {"sync_name": "custom-example", "operation": "plan"}}' | jq -r .id)
echo "flow run: $RUN_ID"                      # SC-001: id returned synchronously
watch -n1 "curl -s $PREFECT_API_URL/flow_runs/$RUN_ID | jq -r .state.type"
curl -s -X POST "$PREFECT_API_URL/logs/filter" -H "Content-Type: application/json" \
  -d "{\"logs\": {\"flow_run_id\": {\"any_\": [\"$RUN_ID\"]}}}" | jq -r '.[].message'
```

**Expected**: state reaches `COMPLETED`; logs contain the bridged `infrahub_sync`
lifecycle lines (load/diff/plan) and the result summary line reporting
`create: 5, update: 0, delete: 0`; the runner-local run directory
(`.infrahub-sync-cache/custom-example/<run_id>/`) contains `run.json`
(`status: dry-run`, `mode: diff`) and `plan.parquet` with five creates. Same lifecycle
and logs visible in the UI at `http://127.0.0.1:4200`.

## Scenario 2 — Confirmed write, then converged no-change plan (DBA-005; US2)

Destination must be at zero `InfraDevice` objects (reset if needed).

```bash
curl -s -X POST "$PREFECT_API_URL/deployments/$DEP_ID/create_flow_run" \
  -H "Content-Type: application/json" \
  -d '{"parameters": {"sync_name": "custom-example", "operation": "sync", "confirm_writes": true}}'
# wait for COMPLETED, then verify exactly five InfraDevice objects at the destination,
# then submit a follow-up plan (Scenario 1 call) — strictly sequentially
```

**Expected**: sync run `COMPLETED`; five devices (`core01..03`, `edge01..02`) exist;
the follow-up plan reports `status: no-change`, `changed: false`, all-zero summary
(Constitution II idempotency).

## Scenario 3 — Safety refusals (DBA-006, DBA-007; US3)

```bash
# 3a: unconfirmed sync → flow FAILS before any adapter loads; destination unchanged
... -d '{"parameters": {"sync_name": "custom-example", "operation": "sync"}}'
# expected: state FAILED; state.message explains confirm_writes=true is required

# 3b: invalid operation → rejected AT CREATION, no flow run exists (probe d₁)
... -d '{"parameters": {"sync_name": "custom-example", "operation": "apply"}}'
# expected: HTTP 409 {"detail": "... 'apply' is not one of ['plan', 'sync']"}

# 3c: unknown / path-like / command-like sync_name → RunValidationError, run FAILED
for BAD in "nope" "../custom-example" "/etc/passwd" "a/b" "--help" '$(touch /tmp/pwned)'; do
  ... -d "{\"parameters\": {\"sync_name\": \"$BAD\"}}"
done
# expected: every run FAILED with a message naming the logical name; no out-of-directory
# read, no subprocess (asserted with spies in the automated tests), /tmp/pwned absent
```

## Scenario 4 — CLI ≡ remote parity (DBA-009, SC-007; US4)

On reset copies of the fixture (destination emptied before each compared run, fresh
run ids, MockDB unmodified):

```bash
uv run infrahub-sync diff --name custom-example --directory examples/   # CLI side
# remote side: Scenario 1 plan run
uv run python - <<'EOF'
from pathlib import Path
from infrahub_sync.cache.fingerprint import compute_plan_fingerprint
cli_run, remote_run = Path("<cli run dir>"), Path("<remote run dir>")
assert compute_plan_fingerprint(cli_run) == compute_plan_fingerprint(remote_run)
print("fingerprints equal")
EOF
```

**Expected**: identical status, changed flag, per-action summary, and canonical
fingerprint. Existing targeted CLI tests pass unmodified:

```bash
uv run pytest -q tests/test_cli_full_extract.py tests/test_cli_parallel.py \
  tests/cache/test_cli_sync_cache.py tests/test_logging.py
```

## Scenario 5 — Full gates (R-4 baseline: 110 passed / 3 skipped at 9edc1bc)

```bash
uv run invoke format          # no diffs
uv run invoke lint            # exit 0 (pre-existing pylint import-outside-toplevel warnings in potenda are inherited)
uv run pytest -q              # no regression vs baseline
uv run infrahub-sync --help
uv run infrahub-sync list --directory examples/
uv run infrahub-sync generate --name from-netbox --directory examples/   # leaves tree clean (R-2)
```

## Scenario 6 — Example walkthrough (DBA-011; US5)

A clean-context developer follows only `examples/prefect_remote_run/README.md`
(prerequisites incl. Python range and the pinned Prefect version; schema loading;
serve; the REST request corpus; the trusted-environment caveat; obviously fake
placeholder credentials) and reproduces Scenario 1 without reading package source.
