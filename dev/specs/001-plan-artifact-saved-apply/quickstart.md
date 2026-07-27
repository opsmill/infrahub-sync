# Quickstart: validating the saved plan artifact and apply path

**Feature**: `001-plan-artifact-saved-apply-infp-653`

How to prove this feature works, end to end. Two tracks: everything that runs on a laptop with no
servers, and the **six** criteria and half-criteria that need a live Infrahub. Details of the format
live in [contracts/plan-artifact-format.md](./contracts/plan-artifact-format.md); details of the
reader in [contracts/plan-reader-api.md](./contracts/plan-reader-api.md). This page is the run guide.

**Track 2 is deferred evidence, not produced evidence (AD045b).** No Infrahub is reachable in the
development environment, so SC-001, SC-002, SC-003 and SC-008, and the live halves of SC-007 and
SC-016 — brief criteria DBA-001, DBA-002, DBA-003, DBA-008 and the live halves of DBA-007 and SC-016 —
have no passing evidence at merge time, and the brief's completion condition is **not met**. Track 1
includes an offline mutation-payload conformance harness that catches the class of defect those
criteria were the only other check on, but it does not substitute for them.

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
uv run pytest -q tests/plan                              # artifact, reader, verifier, review, conformance
uv run pytest -q tests/plan/test_apply_conformance.py    # mutation-payload conformance (AD045a)
uv run pytest -q tests/test_cli_plan_review.py           # review mode, error paths, apply refusals, SC-012
uv run pytest -q tests/test_potenda_plan_artifact.py     # engine wiring, tiers, delete derivation
uv run pytest -q tests/adapters/test_infrahub_planned_write.py   # write surface, mocked SDK
uv run pytest -q tests/cache                             # regression: existing cache behavior
```

### Criteria proven on this track

Attributions match `tasks.md`'s [success-criteria evidence](./tasks.md#success-criteria-evidence)
table task for task; the two must not drift.

| SC | Test (task) | What it asserts |
|---|---|---|
| SC-004 | `tests/plan/test_verify.py` (T025) + `tests/test_cli_plan_review.py` (T065) | Six cases — checksum mismatch, config-version mismatch, snapshot-binding mismatch, absent operations, truncated snapshot, **absent snapshot**. T025 asserts the verifier's verdict per case; T065 asserts each one on the CLI apply path with zero destination writes and `failed` in `run.json`, which a Phase C unit test cannot, because no apply exists in that phase |
| SC-005 | `tests/plan/test_review.py` + `tests/adapters/test_infrahub_planned_write.py` (T056) | The identifier set from per-object review equals the FR-020 record from the apply, in order. Fixture is delete-free: a delete is reviewed but never applied, so it would break the comparison for an unrelated reason |
| SC-006 | `tests/test_potenda_plan_artifact.py` (T041); `tests/plan/test_writer.py` (T018) supports at writer level | Two derivations over identical input, same extraction mode pinned and asserted: byte-identical `operations.jsonl`, and a manifest byte-identical after removing `run_id` and `created_at` from both sides |
| SC-007 (local half) | `tests/adapters/test_infrahub_planned_write.py` (T054) | Non-deletes applied, the delete never dispatched, run `failed`, message names the identifier and action |
| SC-009 | `tests/plan/test_review.py` (T027, in-process) + `tests/test_cli_plan_review.py` (T061, CLI) | Four cases — summary and detail, in-process and CLI — against a stored artifact read in a **new process** with source and destination unreachable |
| SC-010 | `tests/plan/test_canary.py` or equivalent (T072) | Canary credential in `settings`; absent from the artifact files, the captured stdout, and the reader's returned data — and the test fails if the canary is planted into a payload |
| SC-011 | `tests/plan/test_reader.py` (T024) + `tests/test_cli_plan_review.py` (T065) | T024: a run directory with `plan.parquet` and no `plan/` raises with the re-plan message. T065: the same case on the apply path, with zero writes and `failed` |
| SC-013 | `tests/plan/test_config_version.py` (T014 plan side, T057 apply side) | An opaque printable-ASCII value supplied verbatim round-trips through write and apply comparison, never parsed |
| SC-014 | `tests/test_potenda_plan_artifact.py` (T039) | **Three** cases: no `human_friendly_id`; an identity missing an HFID component; and a complete HFID with **no uniqueness constraint** over the plan's identity attributes. Each warns naming the kind and what is missing — and the plan run still succeeds |
| SC-015 | `tests/plan/test_verify.py` (T025) + `tests/test_cli_plan_review.py` (T065) | A `plan/` directory copied between run directories yields a `run_binding` failure (T025) and is refused on the apply path with zero writes and `failed` (T065) |
| SC-016 (local half) | `tests/adapters/test_infrahub_planned_write.py` (T053) | Zero-match names the peer kind, peer identity and referring operation id; multi-match names the peer kind, peer identity and match count; and the live `sync` path's warn-and-continue is asserted **unchanged** (AD048) |
| SC-017 | `tests/test_potenda_plan_artifact.py` (T037) | Full destination extract → deletes recorded and `delete_operations_computed: true`; incremental → no deletes and `false`, and the apply is not driven to `failed` by a phantom delete |
| SC-018 | `tests/plan/test_reader.py` (T024) + `tests/test_cli_plan_review.py` (T065) | T024: `format_version: 99` raises naming version found and versions supported, textually distinct from the SC-011 message. T065: the same case refused on the apply path with zero writes and `failed` |
| *(no SC)* | `tests/plan/test_apply_conformance.py` (T081) | Offline mutation-payload conformance: every HFID component present in each `client.create` call's data; the replace-set reconciliation issued for every cardinality-many relationship; a repeated operation producing no second create |
| *(no SC)* | `tests/test_potenda_plan_artifact.py` (T082, T083) | A kind declared by two schema-mapping entries resolves each peer's kind from the source store, not the mapping (AD046); and each plan-derivation failure fails `diff` with a named error rather than degrading to a warning (FR-030, AD047) |

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

**Everything in this table is deferred.** These tests are authored, not run, in the environment this
feature was built in. Until someone runs them against a live Infrahub, DBA-001, DBA-002, DBA-003 and
DBA-008 and the live halves of DBA-007 and SC-016 have no passing evidence, and the brief's completion
condition is unmet. Do not report them as covered on the strength of the offline harness.

| SC | What the test does |
|---|---|
| SC-001 | Patches `Adapter.diff_from` and `Adapter.sync_from` to fail if called, applies a stored plan, asserts the apply completed and neither was invoked |
| SC-002 | Applies once, records per-kind counts and HFID identities, applies the identical plan again, compares — no duplicates |
| SC-003 | Per-class matrix over create / update / relationship-bearing, across apply-once, apply-twice, a crash injected **after** the destination write commits and before the loop advances, and one injected **before** the write is issued. Every class ends at clean-single-run counts. Relationship class measured by SC-008's peer-set comparison, since an object created with its peers unlinked leaves counts correct and relationships wrong |
| SC-007 (live half) | Applies a plan containing a delete; destination object counts before and after, scoped to the kinds in the plan; asserts the delete target is still present and the run is `failed` |
| SC-008 | Applies a relationship-bearing kind with no comparison store loaded; reads the destination peer sets back; compares against the plan's reference list as an unordered set of `(peer kind, peer identity)` pairs. **At least one referenced peer pre-exists at the destination and is absent from the plan**, and the test asserts the destination-query path was taken for it — otherwise tier ordering fills the resolver's memo, the query path never runs, and the test passes while apply-time peer resolution is broken |
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
