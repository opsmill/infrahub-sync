# Quickstart: validating the saved plan artifact and apply path

**Feature**: `001-plan-artifact-saved-apply-infp-653`

How to prove this feature works, end to end. Two tracks: everything that runs on a laptop with no
servers, and the nine success criteria that need a live Infrahub. Details of the format live in
[contracts/plan-artifact-format.md](./contracts/plan-artifact-format.md); details of the reader in
[contracts/plan-reader-api.md](./contracts/plan-reader-api.md). This page is the run guide.

## Prerequisites

```bash
cd /path/to/infrahub-sync
uv sync
```

For the live track only:

```bash
export INFRAHUB_ADDRESS="http://localhost:8000"
export INFRAHUB_API_TOKEN="<token>"
export NETBOX_URL="https://demo.netbox.dev"
export NETBOX_TOKEN="<token>"
```

Per AD007 no live Infrahub is reachable in the development environment this feature was planned in,
so the live track is expected to run in CI or on a maintainer's machine, not during implementation.

## Track 1 — local, no servers

### The gate

```bash
uv run invoke format
uv run invoke lint          # ruff → pylint → yamllint → ty
uv run ty check .           # must exit 0, no [[tool.ty.overrides]] added
uv run pytest -q            # integration tests are skipped by default
```

### CLI sanity (from AGENTS.md)

```bash
uv run infrahub-sync --help
uv run infrahub-sync list --directory examples/
uv run infrahub-sync generate --name from-netbox --directory examples/
```

### SC-012 — no command group was added

```bash
git stash && uv run infrahub-sync --help > /tmp/help-before.txt && git stash pop
uv run infrahub-sync --help > /tmp/help-after.txt
diff /tmp/help-before.txt /tmp/help-after.txt      # expected: no difference at the command list
uv run infrahub-sync diff --help | grep -E 'from-plan|detail|kind'
```

Five commands before, five after, no group. The new flags appear only under `diff --help`.

### Unit and CLI suites

```bash
uv run pytest -q tests/plan                              # artifact, reader, verifier, review
uv run pytest -q tests/test_cli_plan_review.py           # review mode, error paths, SC-012
uv run pytest -q tests/test_potenda_plan_artifact.py     # engine wiring, tiers, delete derivation
uv run pytest -q tests/adapters/test_infrahub_planned_write.py   # write surface, mocked SDK
uv run pytest -q tests/cache                             # regression: existing cache behavior
```

### Criteria proven on this track

| SC | Test | What it asserts |
|---|---|---|
| SC-004 | `tests/plan/test_verify.py`, six parametrized cases | Checksum mismatch, config-version mismatch, snapshot-binding mismatch, absent operations, truncated snapshot, **absent snapshot** — each refuses, writes nothing, and records `failed` |
| SC-005 | `tests/plan/test_review.py` + `tests/adapters/test_infrahub_planned_write.py` | The identifier set from per-object review equals the FR-020 record from the apply |
| SC-006 | `tests/plan/test_writer.py` | Two derivations over identical input, same extraction mode: byte-identical `operations.jsonl`, and a manifest byte-identical after removing `run_id` and `created_at` from both sides |
| SC-007 (local half) | `tests/adapters/test_infrahub_planned_write.py` | Non-deletes applied, the delete never dispatched, run `failed`, message names the identifier and action |
| SC-009 | `tests/test_cli_plan_review.py` | Four cases — summary and detail, in-process and CLI — against a stored artifact read in a **new process** with source and destination unreachable |
| SC-010 | `tests/plan/test_writer.py` | Canary credential in `settings`; absent from the artifact files, the captured stdout, and the reader's returned data |
| SC-011 | `tests/plan/test_reader.py` | A run directory with `plan.parquet` and no `plan/` rejects with the re-plan message; zero writes |
| SC-013 | `tests/plan/test_config_version.py` | An opaque printable-ASCII value supplied verbatim round-trips through write and apply comparison, never parsed |
| SC-014 | `tests/plan/test_derive.py` | A kind with no `human_friendly_id`, and one whose identity misses a component, each warn naming the kind and component — and the plan run still succeeds |
| SC-015 | `tests/plan/test_verify.py` | A `plan/` directory copied between run directories refuses on the run-identifier check |
| SC-016 (local half) | `tests/adapters/test_infrahub_planned_write.py` | Zero-match names the peer kind, peer identity and referring operation id; multi-match names the peer kind, peer identity and match count |
| SC-017 | `tests/test_potenda_plan_artifact.py` | Full destination extract → deletes recorded and `delete_operations_computed: true`; incremental → no deletes and `false`, and the apply is not driven to `failed` by a phantom delete |
| SC-018 | `tests/plan/test_reader.py` | `format_version: 99` refuses naming version found and versions supported, textually distinct from the SC-011 message |

### Inspecting an artifact by hand

After any `diff` run:

```bash
RUN=.infrahub-sync-cache/from-netbox/<run-id>
python -m json.tool "$RUN/plan/manifest.json"
head -3 "$RUN/plan/operations.jsonl" | python -c 'import sys,json;[print(json.loads(l)) for l in sys.stdin]'
wc -l "$RUN/plan/operations.jsonl"    # must equal manifest.operations_count
```

Recompute the checksum independently:

```bash
uv run python - <<'PY'
import hashlib, json, pathlib, sys
run = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
m = json.loads((run / "plan/manifest.json").read_text())
recorded = m.pop("plan_checksum"); m.pop("run_id"); m.pop("created_at")
body = json.dumps(m, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
ops = (run / "plan/operations.jsonl").read_bytes()
print("recorded  ", recorded)
print("recomputed", hashlib.sha256(body + ops).hexdigest())
PY
```

## Track 2 — live destination (`integration` marker)

```bash
uv run pytest -m integration tests/integration/test_saved_plan_apply_integration.py
```

Skipped automatically when `INFRAHUB_ADDRESS` and `INFRAHUB_API_TOKEN` are unset, matching
`tests/integration/test_infrahub_node_to_diffsync_integration.py` and the marker declared at
`pyproject.toml:133-135`.

| SC | What the test does |
|---|---|
| SC-001 | Patches `Adapter.diff_from` and `Adapter.sync_from` to fail if called, applies a stored plan, asserts the apply completed and neither was invoked |
| SC-002 | Applies once, records per-kind counts and HFID identities, applies the identical plan again, compares — no duplicates |
| SC-003 | Per-class matrix over create / update / relationship-bearing, across apply-once, apply-twice, a crash injected **after** the destination write commits and before the loop advances, and one injected **before** the write is issued. Every class ends at clean-single-run counts. Relationship class measured by SC-008's peer-set comparison, since an object created with its peers unlinked leaves counts correct and relationships wrong |
| SC-007 (live half) | Applies a plan containing a delete; destination object counts before and after, scoped to the kinds in the plan; asserts the delete target is still present and the run is `failed` |
| SC-008 | Applies a relationship-bearing kind with no comparison store loaded; reads the destination peer sets back; compares against the plan's reference list as an unordered set of `(peer kind, peer identity)` pairs |
| SC-016 (live half) | Seeds a genuinely ambiguous peer; asserts the multi-match refusal names the real count |

### Manual walkthrough of the headline scenario

```bash
# 1. Produce a plan. Writes A/, B/, plan.parquet, and the new plan/ artifact.
uv run infrahub-sync diff --name from-netbox --directory examples/
#    → "Cached run 20260726T1804-9f3ac210 at .infrahub-sync-cache/from-netbox/..."

# 2. Review the summary — no adapter is constructed, nothing is extracted.
uv run infrahub-sync diff --name from-netbox --directory examples/ \
    --run-id 20260726T1804-9f3ac210 --from-plan

# 3. Expand one kind to per-object detail.
uv run infrahub-sync diff --name from-netbox --directory examples/ \
    --run-id 20260726T1804-9f3ac210 --from-plan --detail --kind LocationSite

# 4. Apply exactly what was reviewed, by run ID.
uv run infrahub-sync apply --name from-netbox --directory examples/ \
    --run-id 20260726T1804-9f3ac210

# 5. Apply again — converges, no duplicates.
uv run infrahub-sync apply --name from-netbox --directory examples/ \
    --run-id 20260726T1804-9f3ac210
```

Step 2 works with `INFRAHUB_ADDRESS` and `NETBOX_URL` unset and while another `sync` holds the
pipeline lock — that is the FR-008 obligation, and the local suite asserts both.

### Negative walkthrough

```bash
RUN=.infrahub-sync-cache/from-netbox/20260726T1804-9f3ac210

# Corrupt the checksum → refused, run recorded failed, zero writes.
python - <<PY
import json, pathlib
p = pathlib.Path("$RUN/plan/manifest.json"); m = json.loads(p.read_text())
m["plan_checksum"] = "0" * 64
p.write_text(json.dumps(m, sort_keys=True, separators=(",", ":")))
PY
uv run infrahub-sync apply --name from-netbox --directory examples/ --run-id 20260726T1804-9f3ac210
python -c "import json;print(json.load(open('$RUN/run.json'))['status'])"   # → failed

# A v1 plan → the re-plan message, distinct from the version message.
rm -rf "$RUN/plan"
uv run infrahub-sync apply --name from-netbox --directory examples/ --run-id 20260726T1804-9f3ac210
```

## Documentation check

```bash
uv run invoke docs.generate      # regenerates docs/docs/reference/cli.mdx with the new flags
uv run rumdl check .
```

`docs/docs/reference/cache-layout.mdx` must describe the `plan/` directory and both files;
`docs/docs/running-a-sync.mdx` must describe the review-then-apply workflow. Constitution
"Documentation" makes this part of the same change, not a follow-up.
</content>
