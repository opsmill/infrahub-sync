---
description: "Implementation report — saved plan artifact and apply-exactly-what-was-reviewed"
---

# Implementation report — BLOCKED

**Feature**: `001-plan-artifact-saved-apply-infp-653`
**Spec dir**: `/Users/blake/repos/opsmill/infrahub-sync-run3/dev/specs/001-plan-artifact-saved-apply`
**Base commit (resume point)**: `5c05414`
**Head commit**: `d02ca6a`
**Decision mode**: CHECKPOINT
**Run status**: **BLOCKED** — a new material decision surfaced in Phase E and, per the CHECKPOINT
contract, dependent work stopped rather than proceeding on an implementer's judgment call.

This run resumed after a client disconnect. T001–T023 were already `[X]` and committed; five further
chunks landed here, taking the tree from 23 to 50 of 90 tasks before the block.

## 1. Chunk ledger

| # | Chunk | Tasks | Outcome | Commit |
|---|---|---|---|---|
| 1 | Phase C tail — reader / verifier / review tests | T024–T027 (4) | 4 ✅ | `0879204` |
| 2 | Phase D — derivation and engine wiring | T028–T035 (8) | 8 ✅ | `1c8061b` |
| 3 | Phase D tests, part 1 | T036–T041 (6) | 5 ✅, 1 ⚠️ | `0825b0b` |
| 4 | Phase D tests, part 2 | T082–T085 (4) | 4 ✅ | `ac8c61d` |
| 5 | Phase E — write surface and peer resolution | T042–T046 (5) | 5 ✅ + escalation | `d02ca6a` |
| 6–15 | Phase E tests, F, G, H, project gate | 40 tasks | **not dispatched** — blocked | — |

Every commit SHA above was verified with `git log`; every checkbox transition was verified by
re-reading `tasks.md`; every headline test count was re-run by the orchestrator rather than taken
from a subagent's self-report.

## 2. The block

A Phase E worker returned `ESCALATION: NEW MATERIAL DECISION`. The orchestrator verified the claim
against the real SDK rather than relaying it, and it is **correct — and broader than reported**.

**The collision.** Two ratified pins cannot both hold:

- **AD075** pins the replace-set flush to a **plain `node.save()`**, and states the mechanism as
  "`_strip_unmodified` **keeps** the relationship because `has_update` is true (`:352`, `:362`)".
- **T046**, **T051**, `contracts/destination-write-surface.md:239` and
  `contracts/plan-artifact-format.md:96` all pin that **`peers: []` under `cardinality: "many"`
  means empty the set and the replace-set acts on it**, with the **issued destination write** as the
  observable.

**Why they collide.** `_strip_unmodified`'s `has_update` arm
(`.venv/…/infrahub_sdk/node/node.py:352`, `:362`) runs in the *first* loop and only decides the
not-updated case. A *second* loop (`:365-370`) then pops any key whose rendered value equals the
node's `_data` — and `_data` is the create payload, where `generate_payload_create` wrote `[]` for
the same field (`.venv/…/infrahub_sdk/schema/__init__.py:178-181`).

**Orchestrator's own falsification run** (real `InfrahubNodeSync` over a real `NodeSchemaAPI`, plain
save, `exclude_unmodified=True`):

```text
has_update: True   peer_ids after empty: []
UPDATE render data: {'data': {'id': 'node-1'}, ...}
'members' present in emptied update render: False
non-equal _data case, 'members' present: False None
```

The emptied relationship is absent from the flush in **both** the equal-`_data` case the worker
identified **and** the differing-`_data` case it did not. So emptying a peer set never reaches the
destination under a plain save, and `T051`'s empty-`peers` case cannot pass on the issued-write
observable as currently specified.

**The candidate resolution the worker declined to take unilaterally**, quoted verbatim:

> The one-line resolution I did **not** take, because it deviates from step 2/3's written pseudocode
> (`data[ref.field] = ids`): omit an empty cardinality-many reference from `data` before
> `generate_payload_create`, so `_data` carries no such key, the equality never fires, and the flush
> carries `members: []`. It is arguably the reading most consistent with step 7's own premise — the
> empty set is the replace-set's job, "enforced, not assumed of the upsert" — but it changes what the
> upsert payload contains, so it is yours to decide. As shipped, T051's empty-`peers` case will fail
> on the issued-write observable.

This changes what the upsert payload contains, or else changes a ratified flush pin. Either is
material, so under CHECKPOINT it returns to the decision owner. There is no second decision gate.

## 3. Tasks not completed

**40 of 90 remain `[ ]`**: T047, T048, T049, T050, T051, T052, T053, T054, T055, T056, T057, T058,
T059, T061, T062, T063, T064, T065, T066, T067, T068, T069, T070, T071, T072, T073, T074, T075,
T076, T077, T078, T079, T080, T081, T086, T087, T088, T089, T090, T091.

Reason for all forty: **not dispatched.** The block landed at the Phase E boundary and every one of
them is downstream of the unresolved write-surface question — the Phase E test chunks assert the
flush directly (T051, T081), and F, G and H build on the surface those tests are meant to pin.
Dispatching them would have meant a worker silently picking one side of the collision.

**One task is `[X]` but only partially satisfied — T037.** The worker reported `⚠️ partial` and
ticked it anyway. Its remaining clause asks the incremental run's apply to record
`summary["skipped_delete_count"] == 0`, which is a **Phase E** observable ordered into a Phase D
task: `Potenda.apply_plan` is still the pre-existing parquet dispatcher
(`infrahub_sync/potenda/__init__.py:443-472`) and `ApplyRecord` lands at T047. The worker asserted
the decidable precondition instead (`SavedPlan.summary().deletes_not_executed == 0`). This must be
closed once T047/T054/T065 exist; it should not be read as done.

T060 is `~~DROPPED~~` by AD063 and is correctly excluded from the 90.

## 4. Local-pass evidence

Every test added by chunks 1–4 was observed passing locally, and chunks 3 and 4 additionally
demonstrated **falsifiability** — each test shown to fail when the behaviour it guards was reverted,
with the production file restored afterwards. Full per-test node ids, commands, timestamps and
verbatim pass lines are in the chunk reports; the aggregate, re-run by the orchestrator:

| Test group | Type | Run command | Passed at | Environment | Verbatim pass line |
|---|---|---|---|---|---|
| `tests/plan/test_reader.py`, `test_verify.py`, `test_review.py` (71 cases, T024–T027) | unit | `uv run pytest -q` | 2026-07-27T20:50Z | macOS arm64, Python 3.12.2, pytest 9.0.2, `uv sync --extra dev` | `475 passed, 3 skipped, 3 warnings in 6.42s` |
| `tests/cache/test_incremental_engine.py::test_side_full_extract_answers_per_side_on_a_mixed_run` (T031) | unit | `uv run pytest -q` | 2026-07-27T21:09Z | as above | `476 passed, 3 skipped, 3 warnings in 5.58s` |
| `tests/test_potenda_plan_artifact.py` (20 cases, T036–T041) | unit | `uv run pytest -q` | 2026-07-27T21:26Z | as above | `496 passed, 3 skipped, 3 warnings in 5.60s` |
| `tests/test_potenda_plan_artifact.py` (21 further cases, T082–T085) | unit | `uv run pytest -q` | 2026-07-27T21:41Z | as above | `517 passed, 3 skipped, 3 warnings in 5.93s` |
| Whole suite, orchestrator re-run at the block | unit | `uv run pytest -q` | 2026-07-27T22:0xZ | as above | `517 passed, 3 skipped, 3 warnings in 6.13s` |

No `MISSING` rows. Chunk 5 added no repository tests by design (its assertions are T050–T057 and
T081, not yet dispatched); it verified its work through throwaway scripts outside the repo and
reported the observations, which is recorded here as implementation evidence, **not** as criterion
evidence.

**Integration-marked tests: NOT RUN.** `uv run pytest -q -m integration --collect-only` reports
`1/518 tests collected (517 deselected)` — the single collected item is the pre-existing integration
module, and it is deselected from the default run. Phase H, which authors the new `integration`
tests, was never dispatched. Per AD007/AD045 no live Infrahub is reachable and nothing was
substituted with a mock.

## 5. Gate status

- `uv run invoke format` — clean.
- `uv run invoke lint` — exits **30 at pylint**, entirely **pre-existing** findings in
  `infrahub_sync/` (all `C0415` in `potenda/__init__.py`). Score held at **9.71/10
  (previous run: 9.71, +0.00)** across all five chunks; zero findings in any file this run created.
  Ruff, yamllint and ty are each clean.
- `uv run ty check .` — **exit 0, 3 diagnostics**, exactly the baseline: three
  `unused-ignore-comment` warnings in `tests/adapters/test_nautobot_incremental.py`, all pre-existing
  on `main`.
- `pyproject.toml` — **no `[[tool.ty.overrides]]`** (verified: `grep -c` returns 0). None added.
- `uv run rumdl check .` — clean, 80 files.
- CLI sanity — `infrahub-sync --help` and `list --directory examples/` both exit 0.
- `tests/adapters/` passes **unchanged** (26 passed, 2 skipped), and `update_node` is
  **byte-identical** to `HEAD` (sha256 `552c6697…`, 81 lines, verified by AST extraction). The AD070
  tripwire was not tripped.

## 6. Material technical decisions recorded

| # | Decision | Origin |
|---|---|---|
| 1 | `tests/plan/artifact_fixtures.py` added as a shared non-test fixture module — several required shapes (`action: "purge"`, a `create` with no payload, a line count disagreeing with `operations_count`) cannot be built through `PlannedOperation` or `write_plan_artifact` and must be assembled at byte level | inherent |
| 2 | Plain `uv sync` removes the `dev` extra, after which `uv run pytest` dies on unrecognised `--cov-report`/`--dist` in `addopts`; `uv sync --extra dev` is what works. AGENTS.md prescribes the former. **Not repaired** — flagged only | governance |
| 3 | `write_plan_artifact` is invoked from `Potenda.write_plan` rather than from `cli.py` — the engine exposes no single "diff path" entry point and `write_plan` is exactly {diff cmd, serial sync cmd, no-tiers `sync_in_tiers`} | inherent |
| 4 | FR-024 component matching is first-segment presence (`site__name__value` → identity key `site`); the stronger nested form is AD051's write-path gate at T045, deliberately not duplicated | inherent |
| 5 | An identifier reference cycle raises `UnformableDestinationIdentityError` with an overridden next action rather than `RecursionError` | inherent |
| 6 | An **empty** many-peer set on a field with exactly one declared candidate takes the mapping-declared kind (`derive.py:270-274`). Verified by the orchestrator as **not** the AD050 fallback: a *present* peer is always probed, and this arm only fires when there is no peer to probe. With more than one candidate it raises instead | inherent |
| 7 | `SkippedDeleteOperation` lives in `infrahub_sync/plan/errors.py` deliberately **outside** `PlanArtifactError` — it is a control signal for a designed limitation and carries no `next_action` | brief-gap (placement only) |
| 8 | Two new taxonomy members, `UnaccountedIdentityComponentError` and `UnkeyedWriteRefusedError` — the contracts pin the raise and the message but name no class | brief-gap (local) |
| 9 | The resolver query passes `populate_store=False`; the SDK default `True` would read `client.store`, which FR-014 forbids | inherent |
| 10 | Where the peer kind's HFID supplies no usable component the query falls back to the identity's direct scalars; the `<rel>__ids` fallback is **documented, not implemented** (the task verb is "Document") | brief-gap (local) |
| 11 | The flush is issued only when the operation carries a cardinality-many reference | inherent |

## 7. Escalations

**One `ESCALATION: NEW MATERIAL DECISION`** — the empty-peer-set flush collision, quoted verbatim and
verified in §2. This is what blocks the run.

**No `NEEDS_INTAKE_REVISION`** was returned by any worker. No product ambiguity was hit.

## 8. A settled point that turned out wrong against the real code

The caller listed nine settled points and asked that any which proved wrong be quoted with anchors
rather than worked around. **One did, in part — settled point 3 / AD075.**

Its *conclusions* hold: the relationship manager genuinely has no `save`; `add`/`remove` genuinely
are local only; the flush genuinely must be a plain `node.save()` on the node whose manager was
reconciled, once after the loop; and the scoped-`client.get` variant is genuinely wrong. Chunk 5
implemented all of that and demonstrated it end to end — the flush renders as `TestThingUpdate`, not
`TestThingUpsert`, carrying the reconciled peer list.

Its *stated mechanism* is incomplete. AD075 and `tasks.md:403` both assert that

> `_strip_unmodified` **keeps** the relationship precisely because `has_update` is true (`:352`, the
> relationship arm at `:362`)

That is true for a **non-empty** reconciled set. It is false for an **emptied** one: the `has_update`
arm is in the first loop and only decides the not-updated case, while a second loop at
`.venv/…/infrahub_sdk/node/node.py:365-370` pops the key on equality with `_data` regardless. The
orchestrator's falsification run above shows `'members' present in emptied update render: False`.

The consequence is scoped and narrow — non-empty replace-sets are unaffected — but it is exactly the
class of defect AD065/AD075 exist to catch, and it was found by running the code rather than by
reading the artifacts.

## 9. Repository state

```text
$ git status --porcelain
(clean)

$ git log --oneline 5c05414..HEAD
d02ca6a feat: add the destination planned-write surface and apply-time peer resolution
ac8c61d test: pin the peer-kind probe, derivation failures, delete identity and the schema guard
0825b0b test: cover plan derivation, tier ordering and re-plan determinism
1c8061b feat: derive planned operations and write the plan artifact
0879204 test: cover the plan reader, verifier and review surface
```

Branch `001-plan-artifact-saved-apply-infp-653` throughout. Nothing pushed, no PR, no merge, no
branch created or switched.

## 10. Suggested next steps

1. **Resolve the §2 collision** — it is the only thing blocking the remaining 40 tasks. The
   candidate resolution is one line and is quoted verbatim above; deciding it needs the decision
   owner, not an implementer.
2. Re-run `speckit-opsmill-implement` from T047 once that is settled.
3. Close **T037**'s deferred apply-side assertion when T047/T054/T065 land; it is ticked `[X]` today
   on a precondition rather than on the observable its task text names.
4. Record the AD075 mechanism correction (§8) against the decision, so the artifacts and the code
   stop disagreeing.
5. Phase H remains authored-but-unsatisfied by design (AD045b): DBA-001, DBA-002, DBA-003, DBA-008
   and the live halves of DBA-007 and SC-016 will still need a live Infrahub after the code is
   complete.
