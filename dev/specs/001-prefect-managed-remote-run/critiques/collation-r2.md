# Critique collation — Round 2

Round 2 re-ran all three lenses (engineering-r2.md, ergonomics-r2.md, fidelity-r2.md).
Its two new decisions, **D012** and **D013**, were stamped into `plan.md`'s decision-ID
map and into the owning tasks and contracts during round-2 remediation, but their full
records were never written anywhere — unlike D009/D010/D011, whose records live in
`collation-r1.md`. This file exists to give D012 and D013 record parity with D009–D011,
in the same eight-field shape, so the checkpoint gate can read every decision at the same
depth. It adds no new decision IDs.

**Round-2 blocking closures resolved by the final remediation**: E16's remote half (the
`# noqa: BLE001` rule is mechanism-conditioned — required at `run_remote_request`, forbidden
at `execute_run` step 6) and V1 (T026's serial/parallel lock-and-engine structure pinned to
a single engine construction plus the `_lock_already_held` CLI seam).

## New decision records (round 2)

### D012 — Missing-credential env-var naming lands at the remote wrap, not in the adapter

**Question:** X5 asks that a missing-credential failure name the runner-environment
variables the operator must set (`INFRAHUB_ADDRESS`, `INFRAHUB_API_TOKEN`). Where does that
naming live, given DBR-009 requires the touched CLI lifecycles to stay byte-identical to
`9edc1bc`?
**Evidence:** fidelity-r2.md F8, root-confirmed: the adapter's missing-credential
`ValueError` message flows through the CLI's preserved prefixed abort ("Failed to
initialize the Sync Instance: Both url and token must be specified!") on the very
lifecycles DBR-009 protects, so editing `infrahub_sync/adapters/infrahub.py` would change
user-visible CLI failure output while T025 and contracts/execution-surface.md "Failure
semantics" simultaneously bind "exit codes and output identical to the current CLI at
`9edc1bc`". The round-1 collation routed X5 as "task note; adapter-message addition as
in-scope task text", which asserted in-scope-ness rather than recording a decision: the
brief has no adapter-repair allowance. The remote-side need (the `RunExecutionError`
"names the missing input") is fully satisfiable at the `run_remote_request` wrap point,
which is already THE sanitize-and-wrap boundary (D009) and already rewrites the message.
**Options:** A — name the variables in `run_remote_request`'s wrapped message only (by NAME
only, never a value); leave every adapter module untouched, so DBR-009's byte-identity
holds absolutely by construction and T011's assertion sits on the wrapper message. B — keep
the adapter edit under a ratified DBR-009 exception: a decision record plus amendments to
T025's and the contract's "output identical" sentences stating the one permitted
deviation, and a gate-packet entry for the human to ratify the CLI-visible wording change.
**Recommendation:** A.
**Rationale:** the ergonomic goal is met exactly where the operator needs it (the remote
failure surface), the CLI keeps today's wording with no exception to police, and DBR-009
needs no qualifier — identity by construction, consistent with D009's own reasoning. B
would trade a one-line ergonomic gain for a permanent asterisk on the run's strongest
preservation claim.
**Confidence:** High.
**Origin:** `inherent`.
**Status:** PROVISIONAL (CHECKPOINT).

### D013 — T033a's example-fixture diagnosability is a recorded gate item grounded in DBA-011 + DBR-012/DBA-004

**Question:** On what authority does T033a change a SHIPPED example file — a WARNING when
`MockDBClient`'s configured `db_path` does not exist, and replacing the example adapter's
`print()` narration with bridged `logging` — and does the change need ratification?
**Evidence:** fidelity-r2.md F9, root-confirmed: T033a cited the brief's §Assumptions
fixture-repair allowance ("The example must be repaired within this brief without changing
its five-device outcome"), but that allowance is the *impact-if-wrong* consequence of the
fixture-compatibility assumption, and the assumption HELD — R-3's smoke test produced the
expected five creates — so its trigger condition never occurred. The two halves have
independent standing authority: DBA-011 (a clean-context stranger must reproduce the
demonstration; the confirmed X1 hazard means a served fixture can silently produce an
empty plan — a wrong answer for the brief's own qualified demonstration), and
DBR-012/DBA-004 (adapter narration must reach the Prefect run log to be remotely
observable, which `print()` never does). Comparable-or-smaller discretionary scope in this
run was recorded as D007 and D011, so consistency requires a record here too.
**Options:** A — keep both halves, re-grounded on DBA-011 + DBR-012/DBA-004 and surfaced as
a gate item. B — keep the WARNING only (the diagnosability half), dropping the
`print()` → `logging` conversion as demo polish. C — drop both and leave the fixture
exactly as shipped.
**Recommendation:** A.
**Rationale:** the WARNING converts a silent wrong answer into a remotely visible
diagnosis, and the logging conversion is what makes the example's own advertised
checkpoint output ("Loading 5 InfraDevice nodes") appear in the Prefect run log the
example exists to demonstrate — B leaves the walkthrough teaching an observation surface
the fixture cannot reach, and C leaves the qualified demonstration able to fail silently.
Both halves are observability-only: the five-device outcome of the intact fixture is
UNCHANGED. Because the example's user-visible narration mechanism does change, the human
ratifies it like D007 and D011 rather than it riding in as an inherited allowance.
**Confidence:** High.
**Origin:** `inherent`.
**Status:** PROVISIONAL (CHECKPOINT).
