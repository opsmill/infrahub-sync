# Quickstart: validating the saved plan artifact and apply path

**Feature**: `001-plan-artifact-saved-apply-infp-653`

How to prove this feature works, end to end. Two tracks: everything that runs on a laptop with no
servers, and the **six** criteria and half-criteria that need a live Infrahub. Details of the format
live in [contracts/plan-artifact-format.md](./contracts/plan-artifact-format.md); details of the
reader in [contracts/plan-reader-api.md](./contracts/plan-reader-api.md). This page is the run guide.

**Track 2 is deferred evidence, not produced evidence (AD045b).** No Infrahub is reachable in the
development environment, so SC-001, SC-002, SC-003 and SC-008, and the live halves of SC-007 and
SC-016, have no passing evidence at merge time. **Five** of those six are the brief's own criteria —
DBA-001, DBA-002, DBA-003 and DBA-008 in full, plus the live half of DBA-007; the sixth, SC-016's live
half, this specification derived rather than took from the brief. The brief's completion condition is
therefore **not met**. Track 1
includes an offline **rendered-mutation** conformance harness that catches the class of defect those
criteria were the only other check on, but it does not substitute for them. It only narrows anything in the
form AD054 rebuilds it — asserting the mutation the SDK renders, against a committed schema fixture. Its
earlier form asserted the assembled payload against a wholly mocked SDK, which cannot fail for the right
reason and therefore narrowed nothing. And how much it narrows is bounded: for a destination kind whose
convergence key crosses a relationship it can only record that the render is unkeyed today, as a strict
expected failure, so that class rests entirely on the deferred live evidence (AD067).

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
```

**`generate` is not on this track (AD079).** `AGENTS.md` lists
`uv run infrahub-sync generate --name from-netbox --directory examples/` under its post-change sanity
checks, and this file copied it here — but the same file's Known Issues section records that `generate`
needs a running server, and it does: run offline it exits **1** with
`ServerNotReachableError: Unable to connect to 'http://localhost:8000'` and a full traceback, because it
reaches Infrahub for the destination schema. Verified by execution. It is listed under
[Track 2](#track-2--live-destination-integration-marker) instead, so a maintainer working this guide
offline does not meet a red traceback on a command this outcome does not touch and have to work out
whether the feature or the step is at fault.

### SC-012 — no command group was added (AD060)

```bash
uv run infrahub-sync --help > /tmp/help-after.txt
diff tests/data/cli_help_baseline.txt /tmp/help-after.txt   # expected: no difference at the command list
uv run infrahub-sync diff --help | grep -E 'from-plan|detail|kind'
```

Five commands before, five after, no group. The new options appear only under `diff --help`.

The "before" listing is the **committed** baseline `tests/data/cli_help_baseline.txt`, captured by T002 in
the setup phase before any CLI change. Do **not** try to recover it at comparison time by stashing:
`git stash` on a committed tree stashes nothing and exits 0, so the "before" capture that follows it runs
against the *post-change* binary, the comparison diffs a file against itself, and the step passes without
a baseline at all — while the trailing `git stash pop` fails in a way that reads as unrelated noise. This
was reproduced, not theorized.

### Unit and CLI suites

```bash
uv run pytest -q tests/plan                              # artifact, reader, verifier, review, conformance
uv run pytest -q tests/plan/test_apply_conformance.py    # mutation-payload conformance (AD045a)
uv run pytest -q tests/test_cli_plan_review.py           # review mode, error paths, apply refusals, SC-012
uv run pytest -q tests/test_potenda_plan_artifact.py     # engine wiring, tiers, delete derivation
uv run pytest -q tests/test_sc010_credential_canary.py    # SC-010 canary scan, with its positive controls
uv run pytest -q tests/adapters/test_infrahub_planned_write.py   # write surface, mocked SDK
uv run pytest -q tests/cache                             # regression: existing cache behavior
```

### Criteria proven on this track

Attributions match `tasks.md`'s [success-criteria evidence](./tasks.md#success-criteria-evidence)
table task for task; the two must not drift.

| SC | Test (task) | What it asserts |
|---|---|---|
| SC-004 | `tests/plan/test_verify.py` (T025) + `tests/test_cli_plan_review.py` (T065) | Six cases — checksum mismatch, config-version mismatch, snapshot-binding mismatch, absent operations, truncated snapshot, **absent snapshot**. T025 asserts the verifier's verdict per case; T065 asserts each one on the CLI apply path with zero destination writes and `failed` in `run.json`, which a Phase C unit test cannot, because no apply exists in that phase |
| SC-005 | `tests/plan/test_review.py` + `tests/adapters/test_infrahub_planned_write.py` (T056) | The identifier set from per-object review equals the FR-020 record (`summary["applied_operations"]`) from the apply, in order. Fixture is delete-free: a delete is reviewed but never applied, so it lands in `summary["skipped_delete_operations"]` instead and would break an order-sensitive equality for a reason unrelated to SC-005. T054 is where a delete-bearing plan is exercised |
| SC-006 | `tests/test_potenda_plan_artifact.py` (T041); `tests/plan/test_writer.py` (T018) supports at writer level | Two derivations over identical input, same extraction mode pinned and asserted: byte-identical `operations.jsonl`, and a manifest byte-identical after removing `run_id` and `created_at` from both sides |
| SC-007 (local half) | `tests/adapters/test_infrahub_planned_write.py` (T054) | Non-deletes applied, the delete never dispatched, run **`applied`**, `skipped_delete_count` non-zero with the identifiers recorded, and a captured warning naming the count. `applied` ∪ `skipped` covers the plan's whole identifier set. A run state of `failed` fails this test (AD055) |
| SC-009 | `tests/plan/test_review.py` (T027, in-process) + `tests/test_cli_plan_review.py` (T061, CLI) + `tests/test_cli_plan_review.py` (T087, disclosure) | Four cases — summary and detail, in-process and CLI — against a stored artifact read in a **new process** with source and destination unreachable. Both depths also state the delete-computation record and annotate a non-zero delete count; two cases run against an incrementally-loaded plan so the not-computed wording is asserted reachable (AD056) |
| SC-010 | `tests/test_sc010_credential_canary.py` (T072) | Canary credential in `settings`; absent from the artifact files, the captured stdout, and the reader's returned data — and the test fails if the canary is planted into a payload |
| SC-011 | `tests/plan/test_reader.py` (T024) + `tests/test_cli_plan_review.py` (T065) | T024: a run directory with `plan.parquet` and no `plan/` raises with the re-plan message. T065: the same case on the apply path, with zero writes and `failed` |
| SC-013 | `tests/plan/test_config_version.py` (T014 plan side, T057 apply side) | An opaque printable-ASCII value supplied verbatim round-trips through write and apply comparison, never parsed |
| SC-014 | `tests/test_potenda_plan_artifact.py` (T039, T085) | **Four** cases: no `human_friendly_id`; an identity missing an HFID component; a complete HFID with **no uniqueness constraint** over the plan's identity attributes; and a destination exposing **no schema at all**. The first three warn naming the kind and what is missing; the fourth skips the warning without erroring (AD052). The plan run succeeds in all four, and T085 asserts the same for a full `diff` against a non-Infrahub destination |
| SC-015 | `tests/plan/test_verify.py` (T025) + `tests/test_cli_plan_review.py` (T065) | A `plan/` directory copied between run directories yields a `run_binding` failure (T025) and is refused on the apply path with zero writes and `failed` (T065) |
| SC-016 (local half) | `tests/adapters/test_infrahub_planned_write.py` (T053) | Zero-match names the peer kind, peer identity and referring operation id; multi-match names the peer kind, peer identity and match count; and the live `sync` path's warn-and-continue is asserted **unchanged** (AD048) |
| SC-017 | `tests/test_potenda_plan_artifact.py` (T037) | Full destination extract → deletes recorded and `delete_operations_computed: true`; incremental → no deletes and `false`, the apply records a `skipped_delete_count` of **zero** so no phantom delete inflates it, and both review depths state that deletes were not computed (AD055, AD056) |
| SC-018 | `tests/plan/test_reader.py` (T024) + `tests/test_cli_plan_review.py` (T065) | T024: `format_version: 99` raises naming version found and versions supported, textually distinct from the SC-011 message. T065: the same case refused on the apply path with zero writes and `failed` |
| *(no SC)* | `tests/plan/test_apply_conformance.py` (T081) | Offline **rendered-mutation** conformance (AD054): the mutation input the SDK renders carries `id` or `hfid`, built against a committed `NodeSchemaAPI` fixture rather than a mock — required for an all-direct HFID, and a `xfail(strict=True)` for a relationship-crossing one, which cannot render keyed today (AD067); the replace-set reconciliation **issued a destination read** for the relationship before reading the peer set it compares against (AD065); the reconciled peer set was **issued to the destination** by the flush — the targeted relationship write on the reconciled node, rendering as an **update** carrying that peer list rather than a second upsert, and carrying it for an **emptied** set as well (AD075, AD085); the flush names **no destination field the operation did not map**, against a fixture kind carrying an unmapped optional cardinality-one relationship (AD088); two applies of one operation render **byte-identical** mutation inputs (AD068) |
| *(no SC)* | `tests/test_cli_plan_review.py` (T089) | Every error in the plan taxonomy names a next action; the unknown-kind and unknown-run-id messages list the values that exist (AD059) |
| *(no SC)* | `tests/test_cli_plan_review.py` (T090) | Each new option's help text matches the CLI contract, and the `--run-id` help text carries its corrected cross-reference (AD061) |
| *(no SC)* | `tests/test_potenda_plan_artifact.py` (T082, T083) | A kind declared by two schema-mapping entries resolves each peer's kind from the source store, not the mapping (AD046); and each plan-derivation failure fails `diff` with a named error rather than degrading to a warning (FR-030, AD047) |

### Inspecting an artifact by hand

After any `diff` run:

```bash
RUN=.infrahub-sync-cache/from-netbox/<run-id>
python -m json.tool "$RUN/plan/manifest.json"
head -3 "$RUN/plan/operations.jsonl" | python -c 'import sys,json;[print(json.loads(l)) for l in sys.stdin]'
wc -l "$RUN/plan/operations.jsonl"    # must equal manifest.operations_count
```

Recompute the checksum independently. **Pass `$RUN` as an argument (AD060)** — the earlier form read
`sys.argv[1]`, which `python -` never receives from a bare heredoc, so it silently fell back to `.` and
looked for `./plan/manifest.json` at the repository root. It could not succeed:

```bash
uv run python - "$RUN" <<'PY'
import hashlib, json, pathlib, sys
run = pathlib.Path(sys.argv[1])
m = json.loads((run / "plan/manifest.json").read_text())
recorded = m.pop("plan_checksum"); m.pop("run_id"); m.pop("created_at")
body = json.dumps(m, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
ops = (run / "plan/operations.jsonl").read_bytes()
print("recorded  ", recorded)
print("recomputed", hashlib.sha256(body + ops).hexdigest())
PY
```

`sys.argv[1]` is now required rather than defaulted, so a missing argument fails loudly instead of
reporting a checksum for a path that does not exist.

## Track 2 — live destination (`integration` marker)

```bash
uv run pytest -m integration tests/integration/test_saved_plan_apply_integration.py
uv run infrahub-sync generate --name from-netbox --directory examples/   # needs a reachable Infrahub (AD079)
```

Skipped automatically when `INFRAHUB_ADDRESS` and `INFRAHUB_API_TOKEN` are unset, matching
`tests/integration/test_infrahub_node_to_diffsync_integration.py` and the marker declared at
`pyproject.toml:133-135`.

**This table was run. Five of its six rows pass; the sixth cannot be satisfied on this destination
schema.** Verbatim: `7 passed, 1 error in 70.74s`, against Infrahub on `main` with the schema library
`opsmill/schema-library@bgi-schema-library-v2` and source `https://demo.netbox.dev` (AD091). So SC-001,
SC-002, SC-003, SC-007's live half and SC-008 have inspectable passing evidence, and with them DBA-001,
DBA-002, DBA-003, DBA-008 and DBA-007's live half.

**Superseded, kept so the change of state is legible**: this section previously read "Everything in this
table is deferred … authored, not run". That claim was **too weak in a way only execution could expose**
(AD090): the tests were not merely unrun, they were **non-functional** — the fixture's own
`_require_preexisting_peer` precondition refused every one of them, because with `DcimDevice` as the
widest kind every referenced kind was all-direct and SC-008's nested identity walk would have passed
vacuously. A test that has never run is not evidence of anything, including of its own validity.

**Two bounds travel with the passing rows and must not be dropped when they are quoted.**

1. **The slice is bounded.** Ten of the configuration's schema-mapping entries, four of them narrowed by
   a source-side filter (`ADDED_FILTERS`). Two filters are for size; the `LocationRack` one is there
   **because `LocationRack` is not convergent on the qualified path at all** — the destination keys it on
   the rack name alone while the plan identity is name-plus-site, so thirteen identically named demo
   racks collapse onto one object. SC-002 and SC-003 therefore establish convergence **on a slice from
   which the known non-convergent kind was filtered out** (AD080's precedent: this caveat travels with
   the claim). See plan.md's Risks.
2. **SC-016's live half is not deferred — it is unsatisfiable here.**
   `test_an_ambiguous_peer_refuses_the_operation` **errors in fixture setup**: seeding a genuine peer
   ambiguity needs a referenced kind whose uniqueness constraints do not cover the components the
   resolver filters on, and every one of the 20 kinds this configuration touches declares one that does.
   The destination answered the clone with `Violates uniqueness constraint 'device-name'`, HTTP 422. It
   is left erroring rather than skipped, weakened or mocked. Its offline half (T053) passes.

### Reset the destination before re-running Track 2

**Required, not optional — the run will fail in fixture setup without it.** Clear
`InterfacePhysical`, `InterfaceVirtual` and `InterfaceLag` from the destination first:

```bash
# With the destination reachable, delete every object of these three kinds before re-running.
# The order matters: InterfacePhysical references InterfaceLag through `bundle`.
uv run pytest -m integration tests/integration/test_saved_plan_apply_integration.py   # only after the reset
```

Why: the destination **extract** cannot rebuild a peer whose own identifiers include a relationship.
`resolve_peer_node` re-fetches a peer only when `_node_has_complete_attributes` is false, and that
predicate walks **attributes** only, so an attribute-complete peer carrying no relationship data is never
re-fetched; `infrahub_node_to_diffsync` then skips its relationship for want of `rel.id` and
`_resolve_peer_unique_id` raises `PeerIdentifierError`. Re-running against a destination this module has
already written to therefore fails inside the fixture's own `_plan_run` with
`Cannot build unique_id for peer InterfaceLag[…] (relationship InterfacePhysical.bundle …): missing
identifier key(s) ['device']`. The defect is **pre-existing** — the predicate and its gate are
byte-identical to `main` — lives on the destination-extract path shared with the live `sync` command,
which AD070 puts off limits for this delivery, and is **not** on the apply path, since a saved-plan apply
re-extracts nothing (FR-012). Recorded in plan.md's Risks and in `planner-feedback-additions.md` for a
later outcome to own.

| SC | What the test does |
|---|---|
| SC-001 | Patches `Adapter.diff_from` and `Adapter.sync_from` to fail if called, applies a stored plan, asserts the apply completed and neither was invoked |
| SC-002 | Applies once, records per-kind counts and HFID identities, applies the identical plan again, compares — no duplicates. **This criterion measures convergence rather than asserting it (AD080)**: for a destination kind whose convergence key crosses a relationship the render is unkeyed today and a duplicate here is the recorded AD066/AD067 limitation, which the offline harness carries as a strict expected failure — so a failure on one of those kinds is the criterion doing its job, not a regression |
| SC-003 | Per-class matrix over create / update / relationship-bearing, across apply-once, apply-twice, a crash injected **after** the destination write commits and before the loop advances, and one injected **before** the write is issued. Every class ends at clean-single-run counts. **Same caveat as SC-002 for relationship-crossing convergence keys (AD080)** — the relationship-bearing class is exactly the population the narrowed keyedness guarantee excludes. Relationship class measured by SC-008's peer-set comparison, since an object created with its peers unlinked leaves counts correct and relationships wrong |
| SC-007 (live half) | Applies a plan containing a delete; destination object counts before and after, scoped to the kinds in the plan; asserts the delete target is still present, the run is **`applied`**, the recorded `skipped_delete_count` equals the plan's delete count, and the warning names it (AD055) |
| SC-008 | Applies a relationship-bearing kind with no comparison store loaded; reads the destination peer sets back; compares against the plan's reference list as an unordered set of `(peer kind, peer identity)` pairs. **At least one referenced peer pre-exists at the destination and is absent from the plan**, and the test asserts the destination-query path was taken for it — otherwise tier ordering fills the resolver's memo, the query path never runs, and the test passes while apply-time peer resolution is broken |
| SC-016 (live half) | Seeds a genuinely ambiguous peer; asserts the multi-match refusal names the real count. **Errors in fixture setup and cannot pass on this destination schema** — see bound 2 above |

### Manual walkthrough of the headline scenario

```bash
RUN_ID=20260726T1804-9f3ac210

# 1. Produce a plan. Writes A/, B/, plan.parquet, and the new plan/ artifact.
uv run infrahub-sync diff --name from-netbox --directory examples/
#    → "Cached run 20260726T1804-9f3ac210 at .infrahub-sync-cache/from-netbox/..."

# 2. Review the summary — no adapter is constructed, nothing is extracted.
#    --from-plan TAKES the run id (AD057): one option, one meaning.
uv run infrahub-sync diff --name from-netbox --directory examples/ --from-plan "$RUN_ID"
#    → "deletes computed: yes" and, when the plan carries deletes, the NOTE naming how many
#      will NOT be executed (AD056).

# 3. Expand one kind to per-object detail.
uv run infrahub-sync diff --name from-netbox --directory examples/ \
    --from-plan "$RUN_ID" --detail --kind LocationSite

# 4. Apply exactly what was reviewed, by run ID.
uv run infrahub-sync apply --name from-netbox --directory examples/ --run-id "$RUN_ID"
#    → exits 0. On a destination holding objects absent from the source the plan carries
#      deletes; none is executed, and the apply warns naming the skipped count at WARNING
#      level — the level --quiet floors at, so it survives every verbosity (AD055).
#      The completion line names the counts too:
#      "Applied run 20260726T1804-9f3ac210: 33 operations applied, 4 deletes skipped".

# 5. Check what the run recorded.
python -c "import json;s=json.load(open('.infrahub-sync-cache/from-netbox/$RUN_ID/run.json'));\
print(s['status'], s['summary']['skipped_delete_count'], len(s['summary']['applied_operations']))"
#    → "applied 4 33"   — status applied, 4 deletes skipped, 33 operations applied.
#      33 + 4 == manifest.operations_count, which is what makes the applied set
#      knowable against the reviewed set as a value rather than a guess.

# 6. Apply again. Converges — with one caveat, below (AD080).
uv run infrahub-sync apply --name from-netbox --directory examples/ --run-id "$RUN_ID"
```

**Step 6's convergence is not unconditional (AD066, AD067, AD080).** For a destination kind whose
convergence key is composed of its own direct attributes, this converges and a duplicate is a defect. For a
destination kind whose key **crosses a relationship** — which on this configuration is **five kinds, five
mapping entries**: `InterfacePhysical`, `InterfaceVirtual`, `InterfaceLag`, `IpamPrefix` and
`IpamIPAddress` (AD091; the earlier "ten entries across nine kinds" counted plan identities containing a
reference, which is a fact about the configuration, not about the destination key, and `DcimDevice`,
`IpamVLAN`, `LocationRack` and `DcimDeviceType` are all-direct at the destination and converge normally) —
the rendered mutation carries neither identifier today, the apply
warns once per kind that it issued the write anyway, and whether the destination keys it server-side is
exactly what SC-002 and SC-003 **measure**. **They have now measured it (AD091)**: against
`opsmill/schema-library@bgi-schema-library-v2` the destination *does* key it server-side — thirteen
`InterfacePhysical` upserts rendered unkeyed, were issued with the warning, and a second apply of the
identical plan produced no duplicate, because the destination declares a `device-name` uniqueness
constraint and resolves the upsert on it. That is one destination's answer, not the general one: a
destination kind with a relationship-crossing key and **no** covering uniqueness constraint would still
duplicate. So on this walkthrough a duplicate of one of those kinds is the
**recorded AD066/AD067 limitation**, not a regression the maintainer just introduced. The preamble at the
top of this file says the same thing; this note exists so the two agree at the point of use.

**Step 4 exits 0 and records `applied`, not `failed` (AD055).** Not executing a delete is a designed
limitation of this release — DBR-010 puts applying deletes out of scope — so the apply reports it rather
than failing on it. Under the comparison engine's fallback flag set
(`infrahub_sync/potenda/__init__.py:92-93`) any destination holding mapped objects absent from the source
yields deletes, so this is the ordinary case on a non-pristine destination, not an exception. What makes it
not a silent skip is step 5: the count and the identifiers are recorded, and the warning names the count.

Step 2 works with `INFRAHUB_ADDRESS` and `NETBOX_URL` unset and while another `sync` holds the
pipeline lock — that is the FR-008 obligation, and the local suite asserts both.

### Negative walkthrough

**Order matters here, and it did not before (AD072).** Every case that needs to *read* the plan runs first;
the two that destroy something run last. An earlier version of this section deleted `$RUN/plan` in the middle
and then ran two review cases against that same run, so the last case raised the pre-existing-format error
rather than the unknown-kind error it claimed to demonstrate — and because it errored, it looked like it had
passed. Each case below also **says which branch it exercises**, because AD058 deliberately split two that
produce similar-looking messages.

```bash
RUN=.infrahub-sync-cache/from-netbox/"$RUN_ID"

# --- Read-only cases first: the plan must still be intact for these. ---

# 1. An unknown run id → names the identifier, the expected path, AND the run ids that exist,
#    bounded to the most recent 20 with the total when it truncates (AD059, AD073).
uv run infrahub-sync diff --name from-netbox --directory examples/ --from-plan not-a-run-id

# 2. READER branch — a kind the CONFIGURATION does not declare → UnknownPlanKindError.
#    CoreStandardGroup appears in examples/netbox_to_infrahub/config.yml only as a commented-out
#    `order:` example (:43), so it is undeclared and the reader raises (AD058).
uv run infrahub-sync diff --name from-netbox --directory examples/ \
    --from-plan "$RUN_ID" --detail --kind CoreStandardGroup

# 3. RENDERER branch — a kind the configuration DOES declare that the plan holds no operation for
#    → the never-empty rule, raised by the renderer, not the reader (AD058).
#    Pick any kind the summary in step 2 of the walkthrough above did NOT list a count for;
#    IpamRouteTarget (config.yml:469) is declared and is usually empty against demo data.
uv run infrahub-sync diff --name from-netbox --directory examples/ \
    --from-plan "$RUN_ID" --detail --kind IpamRouteTarget

# --- Destructive cases last: each of these leaves the run unusable for the cases above. ---

# 4. Corrupt the checksum → refused, run recorded failed, zero writes.
uv run python - "$RUN" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1]) / "plan/manifest.json"; m = json.loads(p.read_text())
m["plan_checksum"] = "0" * 64
p.write_text(json.dumps(m, sort_keys=True, separators=(",", ":")))
PY
uv run infrahub-sync apply --name from-netbox --directory examples/ --run-id "$RUN_ID"
uv run python -c "import json,sys;print(json.load(open(sys.argv[1]+'/run.json'))['status'])" "$RUN"  # → failed

# 5. A v1 plan → the re-plan message, distinct from the version message. Removes plan/ for good;
#    re-run `diff` if you want a usable run again.
rm -rf "$RUN/plan"
uv run infrahub-sync apply --name from-netbox --directory examples/ --run-id "$RUN_ID"
```

Cases 2 and 3 are the pair worth reading carefully: they must produce **different** messages from
**different** layers — the reader refusing a kind the configuration never declared, and the renderer
refusing to print empty detail for one it did. If both come back identical, AD058's split has not been
implemented and the walkthrough has caught it.

Every one of these names the operator's next action, not only the cause — that is AD059's obligation across
the whole taxonomy, not just the pre-apply refusals.

## Documentation check

```bash
uv run invoke docs.generate      # regenerates docs/docs/reference/cli.mdx with the new flags
uv run rumdl check .
```

`docs/docs/reference/cache-layout.mdx` must describe the `plan/` directory and both files;
`docs/docs/running-a-sync.mdx` must describe the review-then-apply workflow. Constitution
"Documentation" makes this part of the same change, not a follow-up.
