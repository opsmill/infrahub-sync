---
description: "Implementation report — saved plan artifact and apply-exactly-what-was-reviewed"
---

# Implementation report — COMPLETE

**Feature**: `001-plan-artifact-saved-apply-infp-653`
**Spec dir**: `/Users/blake/repos/opsmill/infrahub-sync-run3/dev/specs/001-plan-artifact-saved-apply`
**Base commit (resume point)**: `92ce0dc`
**Head commit**: `3c7e233`
**Commits added**: 10 (8 implementation + 2 review remediation)
**Tasks**: **90 / 90 `[X]`** — none blocked, none deferred
**Suite**: **681 passed, 11 skipped, 1 xfailed** (from a 521 passed / 3 skipped base)
**Decision mode**: CHECKPOINT

This run resumed at T047 after the brief owner resolved the block recorded in the previous report
(AD085: the replace-set flush is `node.update(do_full_update=True)`, committed at `92ce0dc`). It
completed the Phase E test tail, Phase F (CLI review mode and apply rewiring), Phase G (docs,
fixtures, project gate) and Phase H (live evidence, `integration`-marked), then ran a six-lens review
and remediated its findings.

## 1. Chunk ledger

Chunks were dispatched one at a time — never in parallel — because they share files.

| # | Chunk | Tasks | Yes | Partial | Blocked | Commit |
|---|---|---|---|---|---|---|
| 1 | Phase E impl tail | T047, T048, T066, T049 | 4 | 0 | 0 | `cfa830e` |
| 2 | Phase E tests, part 1 | T050–T053 | 4 | 0 | 0 | `54eb2c4` |
| 3 | Phase E tests, part 2 | T054, T055, T056, T081, T057 | 5 | 0 | 0 | `b26b349` |
| 4 | Phase F implementation | T058, T059, T086, T088 | 4 | 0 | 0 | `573593f` |
| 5 | Phase F tests | T061–T065, T087, T089, T090 | 8 | 0 | 0 | `aa52a5a` |
| 6 | Phase G docs and fixtures | T067–T071, T091 | 6 | 0 | 0 | `b0fa785` |
| 7 | Phase H live evidence | T074–T080 | 7 | 0 | 0 | `fc8a312` |
| 8 | Phase G tail | T072, T073 | 2 | 0 | 0 | `d4e2718` |
| R1 | Review remediation — source | 4 findings | 4 | 0 | 0 | `b759ac4` |
| R2 | Review remediation — docs, test gaps | 6 findings | 6 | 0 | 0 | `3c7e233` |

Notable things chunks flagged upward:

- **Chunk 1** — `ApplyRecord` was given a home in `infrahub_sync/plan/models.py` (no artifact text
  assigned one), and a missing write surface surfaces as `PlanVerificationError` naming the adapter
  rather than a separate `NotImplementedError`. T055's own wording says the verifier produces that
  name, so this reconciles T048 with T055 rather than departing from either.
- **Chunk 3** — T081 assertion 2 is a **strict xfail**, which is what AD067 specifies. Confirmed to
  xfail at the keyedness assertion itself, not incidentally.
- **Chunk 4** — T088 was largely pre-delivered by Phases S and B–E; the task reads as more work than
  actually remained.
- **Chunk 5** — T065's blanket "all nine refusals assert `failed` from `run.json`" cannot hold for
  SC-011: the contract requires that case to create **no run directory**, so there is no sidecar to
  read. `tasks.md` and the contract disagree; the contract was followed.
- **Chunk 6** — the sweep found the five fixtures T067 names were **not** stale. The real stale
  content was two false source docstrings and an example config.

## 2. Tasks not completed

None. All 90 task IDs are `[X]` in `tasks.md`, re-read and confirmed after every chunk.

The Phase H tasks (T074–T080) are **authored, not satisfied**, exactly as AD045b prescribes — §3.

## 3. Local-pass evidence

Aggregated per file rather than per test: this run added roughly 200 tests, and a 200-row table would
obscure rather than evidence. Every row was executed on the final tree at the timestamp shown and
carries the runner's verbatim tail. No row is `MISSING`.

Environment for every row: macOS darwin 25.5.0, Python 3.12.2, pytest 9.0.2, dependencies via
`uv sync --extra dev`, repo `/Users/blake/repos/opsmill/infrahub-sync-run3`. Offline — no Infrahub,
NetBox or Nautobot reachable, and none required.

| Test file | Type | Run command | Passed at (UTC) | Verbatim tail |
|---|---|---|---|---|
| `tests/adapters/test_infrahub_planned_write.py` | unit | `uv run pytest <file> -q` | 2026-07-28T05:15:33Z | `29 passed in 0.32s` |
| `tests/test_cli_plan_review.py` | unit/CLI | `uv run pytest <file> -q` | 2026-07-28T05:15:33Z | `90 passed in 18.71s` |
| `tests/plan/test_apply_conformance.py` | unit | `uv run pytest <file> -q` | 2026-07-28T05:15:33Z | `11 passed, 1 xfailed in 0.31s` |
| `tests/plan/test_config_version.py` | unit | `uv run pytest <file> -q` | 2026-07-28T05:15:33Z | `41 passed in 0.18s` |
| `tests/test_sc010_credential_canary.py` | unit | `uv run pytest <file> -q` | 2026-07-28T05:15:33Z | `6 passed in 0.37s` |
| `tests/cache/test_apply_plan.py` | unit | `uv run pytest <file> -q` | 2026-07-28T05:15:33Z | `9 passed in 0.15s` |
| `tests/plan/test_models.py` | unit | `uv run pytest <file> -q` | 2026-07-28T05:15:33Z | `78 passed in 0.17s` |
| `tests/test_live_fixture_preconditions.py` | unit | `uv run pytest <file> -q` | 2026-07-28T05:15:33Z | `5 passed in 0.19s` |
| `tests/plan/test_review.py` | unit | `uv run pytest <file> -q` | 2026-07-28T05:15:33Z | `25 passed in 1.39s` |
| `tests/integration/test_saved_plan_apply_integration.py` | integration | `uv run pytest -m integration <file>` | offline: 2026-07-28T05:15:33Z. **Live: 2026-07-28, see §3a** | offline `8 skipped in 0.19s`; live **`7 passed, 1 error in 70.74s`** |

Full suite, three consecutive runs on the final tree:

```text
681 passed, 11 skipped, 1 xfailed, 3 warnings in 25.07s
681 passed, 11 skipped, 1 xfailed, 3 warnings in 25.06s
681 passed, 11 skipped, 1 xfailed, 3 warnings in 24.75s
```

### The one xfail and the eleven skips

- **1 xfailed** — `test_a_relationship_crossing_kind_renders_a_keyed_mutation`, a **strict** xfail
  mandated by AD067. It self-retires if the SDK ever renders that case keyed.
- **11 skipped** — 3 pre-existing, plus the 8 Phase H `integration` tests.

### Phase H is authored, not satisfied

*(Superseded for five of the six criteria by §3a, which records a live run. Kept as the record of the
offline tree, and still accurate for SC-016's live half.)*

`uv run pytest -m integration -q` reports `11 skipped, 682 deselected`. Per AD007 no live Infrahub is
reachable here, and per **AD045b** these tests produce no evidence in this environment. **SC-001,
SC-002, SC-003, SC-008 and the live halves of SC-007 and SC-016 remain without passing evidence, and
the brief's completion condition is unmet at merge.** This belongs in the merge notes.

Nothing was mocked to stand in for a live destination. A reviewer audited specifically for this and
confirmed the patches in that file (`Adapter.diff_from` / `sync_from` patched to **raise**) are
assertion mechanisms, not substitutes.

CI command to produce the missing evidence, against a **disposable** Infrahub:

```bash
export INFRAHUB_ADDRESS=... INFRAHUB_API_TOKEN=... NETBOX_URL=... NETBOX_TOKEN=...
uv sync --extra dev
uv run pytest -m integration tests/integration/test_saved_plan_apply_integration.py
```

## 3a. The live run — what actually happened (AD091)

The section above stands as the record of the offline tree. A live destination then became reachable and
the command above was run. **The paragraph "Phase H is authored, not satisfied" is superseded for five of
the six criteria and stands for the sixth.**

Environment: Infrahub at `http://localhost:8000`, branch `main`, schema from
`opsmill/schema-library@bgi-schema-library-v2`; source `https://demo.netbox.dev` (75 devices, 1761
interfaces). Verbatim summary line:

```text
7 passed, 1 error in 70.74s (0:01:10)
```

| Test | Outcome | What it establishes |
|---|---|---|
| `test_applying_a_stored_plan_runs_no_extraction` | **passed** | SC-001. `Adapter.diff_from`, `Adapter.sync_from` and `DiffSyncMixin.load` all patched to raise; none was called, and `applied ∪ skipped` covered every operation |
| `test_re_applying_an_identical_plan_converges` | **passed** | SC-002. No duplicate on any kind in the plan — **including `InterfacePhysical`, whose convergence key crosses a relationship**. Its docstring records that a duplicate there would be the test working correctly; none occurred |
| `test_the_write_class_conformance_matrix[create]` | **passed** | SC-003, create class, across apply-once, apply-twice and both crash windows |
| `test_the_write_class_conformance_matrix[update]` | **passed** | SC-003, update class, same four scenarios |
| `test_the_write_class_conformance_matrix[relationship]` | **passed** | SC-003, relationship class, same four scenarios, measured through peer sets |
| `test_a_delete_bearing_plan_applies_and_records_the_skipped_deletes` | **passed** | SC-007's live half. Run ended `applied`, the delete targets survived, the skipped-delete count and identifier set matched the plan, and a `WARNING`-level line named the count under `--quiet` |
| `test_relationship_peer_sets_match_the_plan` | **passed** | SC-008. Peer sets matched, **and** the resolver was observed issuing `client.filters(kind="InterfaceLag", device__name__value=…, name__value=…)` — PD-004's nested spelling, verified against a live destination for the first time |
| `test_an_ambiguous_peer_refuses_the_operation` | **error in setup — not a pass, and not satisfiable on this schema** | SC-016's live half. `_clone_node` was refused: `Violates uniqueness constraint 'device-name'`, HTTP 422, converted to `LivePlanPreconditionError` as that helper's docstring prescribes. Seeding a genuine ambiguity needs a referenced kind whose uniqueness constraints do not cover the components the resolver filters on, and **all 20 kinds this configuration touches declare one that does**. The offline half still passes |

**The keyedness gate, observed live.** Seventeen `WARNING` lines across the module, one per apply, all
naming `InterfacePhysical`: *"the mutation rendered for destination kind InterfacePhysical carries neither
'id' nor 'hfid' because the kind's convergence key crosses a relationship (device__name__value,
name__value) … The write was issued anyway."* So the render is unkeyed exactly as V39 predicted — and the
writes converged regardless, because the destination resolves the upsert on its own `device-name`
uniqueness constraint. The Material nested-HFID risk is confirmed as a **render** fact and **not**
realised as a duplicate on this schema. AD067's strict `xfail` did not xpass: it asserts the render, and
the render is unchanged; it is an offline harness against a committed fixture and is not parameterised by
the live slice.

**Two defects the live run found, neither of them on the apply path, both recorded in plan.md's Risks and
neither fixed here:** `LocationRack` is not convergent on the qualified path because the destination keys
racks on `name` alone while the configuration's identity is `name` + `site` and thirteen demo racks share
a name; and the destination **extract** path cannot rebuild a peer whose own identifiers include a
relationship, so `diff` fails once `InterfacePhysical` exists at the destination. The second one bounds
what the seven passes mean: they were produced against a destination holding no `InterfacePhysical`, and
reproducing them requires clearing that kind first.

Nothing was mocked, stubbed or skipped to manufacture any of this, and no assertion or precondition was
weakened. The one change to the module was widening `KINDS_UNDER_TEST` so its own
`_require_preexisting_peer` precondition could be met (AD091).

## 4. Review findings

Six lenses ran over `92ce0dc..HEAD` (code, errors, tests, types, comments; simplify folded in).

| Severity | Area | Finding | Disposition |
|---|---|---|---|
| High | `cli.py` apply arms | An interrupt mid-apply left `run.json` at `status="running"`, `summary={}` while writes had already landed — `BaseException` passed through both `except Exception` guards | Fixed `b759ac4` |
| High | `cli.py` refusals | Every designed apply refusal reached the operator as a raw traceback instead of `print_error_and_abort` | Fixed `b759ac4` |
| High | `potenda` dispatch | The write boundary is dispatched via `getattr` and typed `Any`, so `ty` checks nothing about the call | **Escalated** — material design decision |
| High | `potenda` cast | `cast("InfrahubAdapter", ...)` narrows on a `hasattr` gate, so a duck-typed destination passes the pre-write refusal and dies mid-apply | **Escalated** — material design decision |
| Critical (docs) | `cache-layout.mdx` | Documented an `apply` refusal on schema-sub-hash drift that never happens | Fixed `3c7e233` |
| High (docs) | 5 sites | "The engine collects these" misdescribes the delete-decline mechanism — the engine pre-filters and never dispatches a delete | Fixed `3c7e233` |
| Medium | `cli.py` invariant arm | `ApplyRecordInvariantError` merged an **empty** record on a run that had written everything | Fixed `b759ac4` |
| Medium | `models.py` | `ApplyRecord` allowed `skipped_delete_count` to contradict its own list, and negative counts | Fixed `b759ac4` |
| Medium | `cli.py` renderer | The AD043 nested-peer identity renderer was entirely untested | Fixed `3c7e233` |
| Medium | integration fixture | `assert_convergence_key_is_supplied` claimed to be exercised offline and was not | Fixed `3c7e233` |
| Medium | `potenda` config version | `_apply_config_version`'s no-config refusal was untested | Fixed `3c7e233` |
| Medium | `potenda` broad except | Erases `PeerNotFoundError`'s class and emits two competing next actions | Deferred — §5 |
| Medium | `errors.py` | `next_action` is enforced at construction, not class definition, so a forgetful subclass trades a missing remedy for a lost error | Deferred |
| Low | 6 citations | Stale `file:line` references, one authored by this change | Fixed `3c7e233` |
| Low | `cli.py` dead block | Inert schema-sub-hash check | **Left alone — AD063 ratified this** |
| Low | misc | Untested completion line and empty-plan `--kind` arm; a layout-coupled assertion; a missing failure message on the SC-012 byte compare | Deferred |

### A measurement artifact worth recording

Two reviewers initially reported the suite as flaky. It is not. The reviewers were mutating source in
place to prove test resilience — as instructed — **concurrently with each other's test runs**. The
contamination was reproduced directly (`git status` showing a modified source file mid-run), and one
contaminated run surfaced an error string that exists nowhere in the repository. On a quiet tree the
suite is deterministic: six consecutive runs, identical results. No action needed, but a future
parallel review pass should give each mutating reviewer its own worktree.

## 5. Autonomous decisions

Recorded with origin, per the CHECKPOINT protocol.

1. **Chunk ordering — Phase H before Phase G's tail** *(governance)*. `tasks.md` orders G then H, but
   T073 *is* the full project gate. Running it before Phase H added files would have made its result
   stale on arrival. Order run: G's docs, then H, then T072/T073 — so the gate genuinely ran last.
   Task order in `tasks.md` is otherwise unchanged.
2. **Two high type findings escalated rather than fixed** *(governance)*. Replacing the `getattr`
   dispatch with a `runtime_checkable` Protocol, and removing the `cast("InfrahubAdapter", ...)` by
   moving `PeerResolver` ownership to the adapter, are design changes to the feature's central write
   boundary — not low-impact reversible choices. Under CHECKPOINT they return to the brief owner.
   Both are real: the second means a duck-typed non-Infrahub destination passes the pre-write
   refusal FR-023 and AD058 exist to enforce, and fails mid-apply after writes have landed.
3. **A published release note was edited** *(governance — flagged for reversal if unwanted)*.
   `docs/docs/release-notes/infrahub-sync/release-2_0_0.mdx` carried the same false schema-sub-hash
   refusal claim. One sentence was deleted. Leaving a falsehood in live user-facing docs seemed worse
   than amending a historical note, but this is a call the brief owner may want to make differently;
   it is one line and trivially revertible.
4. **The refusal/defect split** *(inherent)*. "Designed refusal" was defined as *membership of the
   `PlanArtifactError` taxonomy* rather than a hand-picked list, because that base class already
   enforces a non-empty `next_action` (AD059) — membership already is the predicate. Everything
   outside the taxonomy keeps its traceback.
5. **`ApplyRecordInvariantError` gained a required `apply_record`** *(inherent)*, mirroring
   `OperationApplyFailedError`; merging the real counts is not implementable without it.
6. **One new `# ty: ignore[invalid-assignment]`** *(governance)* for attaching a record to a foreign
   `BaseException`. It follows 13 pre-existing ignores in the same module and is *used*, so the
   3-warning baseline is unchanged.
7. **T091's "nine adapters other than Infrahub" corrected to eight** *(brief-gap)* — nine total,
   eight others, corroborated by an existing assertion in the suite.
8. **Deferred deliberately**: the broad-except class erasure in `potenda` and definition-time
   `next_action` enforcement. Both are real, neither is load-bearing for this outcome, and both touch
   error-taxonomy design that AD059 already ratified a shape for.

Nothing was returned as `NEEDS_INTAKE_REVISION`. No product scope was added, expanded, removed,
softened or reassigned. Every out-of-scope item stayed out: no delete reaches the destination, no new
CLI command group, no durable apply ledger, no configuration-version registry, no load-path scan
replacement, no destination freshness or conflict policy, no branch review mode.

## 6. Gate results

Re-run by the orchestrator, not taken on a worker's report.

| Gate | Result |
|---|---|
| `uv run pytest -q` | **681 passed, 11 skipped, 1 xfailed** — deterministic over 3 consecutive runs |
| `uv run pytest -m integration -q` | `11 skipped, 682 deselected` — clean skip, no destination |
| `uv run ty check .` | exit **0**, exactly **3** pre-existing `unused-ignore-comment` warnings |
| `grep -c "tool.ty.overrides" pyproject.toml` | **0** |
| pylint | **9.73/10** (baseline 9.73) |
| `uv run rumdl check .` | `Success: No issues found in 81 files` |
| `uv run infrahub-sync --help` | exit 0, **byte-identical** to the committed SC-012 baseline |
| `uv run infrahub-sync list --directory examples/` | exit 0, 14 syncs |
| `uv run infrahub-sync diff --help` | the three review options present with contract-exact help text |
| `generate` | exit 1, `ServerNotReachableError` — **excluded from the offline gate by AD079** |

`uv run invoke lint` exits **30**. This is pre-existing and not caused by this feature: pylint emits
one `E0213` (`no-self-argument`) at `infrahub_sync/__init__.py:98`, a file this branch never touches
(last changed by `b25942a`, before the branch base). Pylint's exit code is a bitmask. The criterion
that binds — the score — is met and unmoved. Deliberately not fixed: unrelated pre-existing lint is
out of scope.

### AD070 tripwire

Verified at **every chunk boundary** and on the final tree, by AST extraction of the module-level
`update_node` in `infrahub_sync/adapters/infrahub.py`:

```text
lines: 109 - 189
byte_sha256: 552c6697ca78be62bd7152061d48e48baa5d289131c2cc571fd5940d4b78bb92
ast_sha256 : 81ae3d95cabed698a4d72de72a2e7e379fe171676025ab64ed05b35069179553
```

Identical to baseline at all ten checks. The second clause also holds: the only file added under
`tests/adapters/` is the new planned-write suite, and the pre-existing set still reports exactly its
original **30 passed, 2 skipped**.

`tests/data/cli_help_baseline.txt` is unchanged across the whole range
(`fbbe0f9397cadee39970aea1b41b85baa83647db691dc43621698e6fe1e80401`), and a reviewer independently
confirmed it was committed in `368e960` *before* the CLI change — a genuine pre-change capture, not a
self-comparison.

## 7. Suggested next steps

1. **Record the AD045b deferral in the merge notes.** Six criteria — SC-001, SC-002, SC-003, SC-008
   and the live halves of SC-007 and SC-016 — have no passing evidence, and the brief's completion
   condition is unmet at merge. Expected and ratified, but it must be said out loud.
2. **Run the `integration` suite** wherever a disposable Infrahub and a NetBox are reachable, using
   the command in §3, to close those six.
3. **Decide the two escalated type findings** — the `getattr` write-boundary dispatch and the `cast`
   on a `hasattr` gate. The second is the more consequential: it lets a duck-typed destination past a
   pre-write refusal.
4. **Confirm or revert the release-note edit** (§5.3).
5. Consider the deferred medium findings: the broad-except class erasure (which also produces a
   doubled period in the operator message) and definition-time `next_action` enforcement.
6. Give each reviewer its own worktree on any future parallel review pass (§4).
