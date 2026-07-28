# Critique collation — round 3 (final round)

Three lenses re-ran at commit `5570270`. Reports: `engineering-r3.md` (369 lines),
`ergonomics-r3.md` (510), `fidelity-r3.md` (453).

**Round 3 result: 1 blocking finding.** Ergonomics and fidelity both returned **zero** blocking and
both answered the implement-or-not question with "implement". Engineering returned one Must-Address,
**S1**, on the same mechanism as its round 2 finding — not a repeat, but a new defect found by
executing the prescribed sequence against the library rather than reading it.

## What each round 2 blocker came to

| Round 2 | Verdict | Note |
|---|---|---|
| R1 re-read mechanism | **CLOSED** | Mechanism named in six places; both variants verified workable; the test observable now fails against an implementation that only reorders the call |
| R2 keyedness gate | **CLOSED as narrowed** | 44 "unkeyed" references across the artifacts and not one absolute survives |
| R3 conformance fixture | **CLOSED** | Strict expected-failure that becomes a suite failure when the hole closes |
| R4 vacuous assertion | **CLOSED** | Byte-identity replaces it |
| R5 run-record ownership | **CLOSED** | Correct on a completed apply, a mid-apply rejection, and a refusal |
| ERG-21 missing error classes | **CLOSED** | All three instrumentation points moved, not just the prose |
| ERG-22 quickstart false pass | **CLOSED** | Both kind claims verified against the qualified config |
| ERG-24 unbounded enumeration | **CLOSED** | Bound stated in five places with no drift |
| R2-F1 live-path scope leak | **CLOSED by withdrawal** | Fidelity enumerated all six surfaces touching existing code; each is authorized |
| R2-F2 miscited authority | **CLOSED** | The over-broad reading is now repudiated in the spec's own text |

## The blocking finding

### AD075 — the reconciled peer set is never flushed to the destination

**Verdict: CONFIRMED by root.**

**Evidence root checked directly:** `infrahub_sdk/node/relationship.py` — `RelationshipManagerSync`
has **no** `save` method; `add()` (`:322-332`) and `remove()` (`:339-357`) only mutate `self.peers`
and set `_has_update = True`. The peer set reaches the destination only on a subsequent `node.save()`.
That is exactly how the code this helper is modelled on works: `update_node` returns the node
**unsaved** (`adapters/infrahub.py:177`) and its caller flushes it (`:626`). The specified sequence
ends at step 7d and proceeds straight to remembering the node and returning.

**Why it is blocking rather than something implementation would catch.** Every specified observable is
satisfied without a flush: the unit test is mocked and asserts "peers removed", the conformance
assertion asserts "the surplus is removed", and the task's done-condition asserts manager state. The
only criterion that would catch it — SC-008 — is behind the `integration` marker and is not produced
at merge. And the task instructs the implementer to model the helper on `update_node`, whose flush
lives in its *caller*. So it ships green, with documentation claiming an enforced replace-set.

Worse, the failure is **co-extensive with the risk the step exists for**: if the server's upsert
replaces the set, there is nothing to flush and the omission is invisible; if it merges — the case
this whole mechanism was introduced to handle — the surplus is computed correctly and then discarded.

**Recommendation:** add the flush as an explicit step, specified as plain `node.save()` rather than an
upsert, because after the create step the node is already marked existing, so a plain save dispatches
an update and the unmodified-field stripping retains the relationship precisely because it has an
update flag. Then move the test observable onto **the issued destination write carrying the reconciled
peer list** — the same correction AD065 already made for the read side.

**Confidence:** High. **Origin:** `inherent`.

## Recommended, batched — applied in the same pass

Ordered by consequence, from all three reports:

- **AD076** — the keyedness gate is stricter than the claim for a kind declaring no human-friendly ID, which FR-024 explicitly permits, and the accessor named for the rendered mutation is one level short of the payload.
- **AD077** — the AD074 "second ground" is unsound and must be withdrawn: DBA-007 and the brief's Scenario 4 both call a recorded delete "the unsupported operation", so the brief owner's override was **necessary**, not merely the better-cited of two routes. The spec asserts the contrary once. This matters because a future reader would otherwise learn a false lesson about when a derived requirement can be re-derived without an override.
- **AD078** — the keyedness warning is unpinned, unspecified in content, **untested** (no task asserts its existence, level, or per-kind deduplication), and undocumented, while the sibling delete warning is pinned, tested and documented. One round ago this run ruled "operator-visible" insufficient and pinned the delete warning's level; the same phrase was then used here.
- **AD079** — a quickstart step under the "local, no servers" heading exits non-zero because it needs a server. Recommended rather than blocking because it fails **loudly**, unlike the three earlier quickstart defects, each of which was a false pass.
- **AD080** — a live-track quickstart line promises "apply again — converges, no duplicates" unconditionally, on the configuration whose ten relationship-crossing identities are exactly the population the narrowed gate excludes; the truth sits 190 lines above in the same file.
- **AD081** — the brief-side dependency repair: the "convergent write path — Satisfied" row should be scoped to kinds whose convergence key is all-direct, unverified where it crosses a relationship, with impact if wrong. Three later outcomes inherit the same partial satisfaction. `brief-gap`, `instance` — planner feedback, no code change.
- **AD082** — one error class is raised for an ambiguous-kind probe while its taxonomy row defines only the absent case and writes its remedy for that case.
- **AD083** — no task points adapter authors at the adding-an-adapter guide, so they learn the new write surface only from a pre-write refusal message. Three rounds old and never routed.
- **AD084** — the run has passed its decision gate, so the ~196 remaining `[PROVISIONAL ADnnn]` markers must be stripped and the six clarification sessions restated as ratified.
- Plus: two stale assertion numbers, the apply record's unnamed type, and the no-runs next action not naming the command its sibling row names.

## Round disposition and the round cap

The lens loop's cap is three rounds, and blocking residue **left open** after round 3 makes the run
`BLOCKED`. AD075 is therefore closed in this pass rather than carried, and its closure is verified by
a **narrow, single-finding check** — not a fourth full round, which the cap forbids and which nothing
here justifies: two of three lenses returned zero blocking and answered "implement".

That narrow check is not optional bookkeeping. Every unverified repair in this run turned out partly
wrong — twice a remediation agent reported a fix it had not made, and AD075 itself is a defect in the
repair of a defect in a repair. Verifying exactly the thing just changed, and nothing else, is the
proportionate response.
