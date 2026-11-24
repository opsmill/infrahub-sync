# Feature Specification: Automatic resume of an interrupted apply

**Feature Branch**: `030-automatic-resume`

**Created**: 2026-11-23

**Status**: Draft

**Input**: The idea brief "Automatic resume of an interrupted apply", grilled with the user on 2026-11-23. Every
decision in the brief was confirmed by the user. The brief continues the item "automatic resume when a worker
restarts" of [INFP-650](https://opsmill.atlassian.net/browse/INFP-650), which
[spec 016](../archive/016-resume-interrupted-apply/spec.md) left out of scope. The brief is compared with this
specification in [`alignment-check.md`](./alignment-check.md).

## Background

Spec 016 and ADR 0027 let an operator resume an interrupted apply from its saved progress marker. The operator runs
`runs show`, `verify`, `approve` when the site requires approval, and `runs resume`. Nothing happens until a person
does this.

A run is often interrupted for a reason that has nothing to do with its data: a deployment restarts the worker,
Kubernetes moves the worker's pod to another node, the system kills the worker for using too much memory, or a node
restarts. The liveness monitor of the Sync API then ends the write execution with the phase `interrupted` and sets
`reconciliation_required`. A large apply started in the evening waits until the next morning. A scheduled run that
stays interrupted counts as a failure of its schedule (spec 021, FR-032).

The liveness monitor already tells these cases apart from the others. It ends an execution under one of these rules
(`infrahub_sync/service/liveness.py`):

- **Rule 2**: the operator asked to cancel, the worker did not confirm, and the deadline passed.
- **Rule 6**: the execution is claimed and Prefect reports its flow run as ended. Only the Prefect state `crashed`
  means that the worker's process ended. The state `failed` means the flow raised an error that the worker could not
  record, `cancelled` means a person cancelled it in Prefect, and `completed` means the flow ended without recording
  its end.
- **Rule 8**: the execution is claimed and its worker is no longer fresh: the worker pool does not report it online
  with a heartbeat inside `max(3 * heartbeat interval, 30)` seconds.

A worker that is still running and reports a failure itself ends the run through the failure recording of the flow
(`infrahub_sync/service/flow.py`), not through the liveness monitor.

Today the liveness monitor states that "an execution is never replayed after any verdict". This specification makes
one exception to that rule, for rules 6 and 8 only.

### Terms used in this specification

- **Worker disappeared**: the liveness monitor ended a run's write execution under rule 6 with the Prefect state
  `crashed`, or under rule 8.
- **Automatic resume**: a resume execution of a run that Sync starts itself, with no account, after the worker
  disappeared. It applies the run's saved plan from the progress marker, as a resume started with `runs resume` does.
- **Automatic check**: the check that Sync runs before an automatic resume. It is the same check as `verify` on an
  interrupted run (spec 016, FR-005 to FR-008): every pre-write check, and a report of each uncertain operation as
  "already matches the plan" (`matches`), "differs from the plan" (`differs`) or "missing at the destination"
  (`missing`).
- **Live worker**: a worker that the worker pool reports online with a heartbeat inside
  `max(3 * heartbeat interval, 30)` seconds. This is the rule the liveness monitor and `/status` apply today, and the
  rule that the worker health work for `/api/health` reuses
  ([INFP-663](https://opsmill.atlassian.net/browse/INFP-663), requirement 3).
- **Decision**: what Sync records for an interrupted run once it has considered an automatic resume: either "resume"
  or "no resume", with the reason.

## Clarifications

### Session 2026-11-23 (grilling of the idea brief, decided by the user)

- Q: Which interruptions start an automatic resume? → A: Only "worker disappeared" (rules 6 and 8). Not a
  cancellation (rule 2), and not a failure the worker reported itself, because a person needs to read that failure.
- Q: What replaces the operator's `verify`? → A: Sync runs the same check itself. A check that fails, or that reports
  at least one `differs`, stops the automatic resume. When the destination changed after the plan, a person decides,
  usually by creating a new plan. Sync never forces the plan over a changed destination without a person.
- Q: What approval does an automatic resume need? → A: The same approval check as `runs resume`, with nothing added.
  Plan approval is to be partly deprecated, so this feature adds nothing to it.
- Q: How is automatic resume turned on? → A: One site-wide setting of the Sync API, off by default.
- Q: How many automatic resumes can a run get? → A: None after an automatic resume that did not move the progress
  marker forward, and at most 3 in all.
- Q: Who is recorded as the actor? → A: No account. A marker that Sync started the resume, and a fixed reason built
  from the cause. The CLI and the web interface show "Sync (automatic resume)".
- Q: How do scheduled runs behave? → A: The same resume rules apply. A run counts toward the schedule's failures by
  its final result, and a run waiting for or running an automatic resume counts as in progress for the overlap
  policy.
- Q: Does the constitution allow a write that nobody started? → A: Not today. Principle I is amended with a second
  named case, automatic resume.
- Q: What does the check do with the 2,000-operation limit of `verify`? → A: It uses the same limit. An existing issue
  covers that limit.
- Q: What happens when the Sync API restarts? → A: Each automatic resume starts exactly once.

### Session 2026-11-23 (decided during specification, from the code and the brief)

- Q: Does an automatic resume need the left-out confirmation of spec 007 again? → A: It reuses the confirmation that
  admitted the interrupted write. That write could not start without it, and the plan cannot change.
- Q: Does the automatic check record a `verify`? → A: Yes. It is recorded as a `verify` of the run with no account,
  so the existing rule "a resume needs a passing `verify` newer than the latest write execution" (ADR 0027) holds
  unchanged.
- Q: Does the setting apply to runs interrupted before it was turned on? → A: No. Sync decides once, when the liveness
  monitor ends the execution, using the setting at that moment. While it is off, Sync records nothing.
- Q: Which Prefect states of rule 6 count? → A: Only `crashed`. `failed` is an error the worker raised (critique E1).
- Q: What happens when a person acts while Sync waits for a worker? → A: The person takes over (FR-019).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A stopped worker is replaced and the apply finishes with no one acting (Priority: P1)

An operator applies a large plan in the evening and leaves. The worker is killed part way through. Sync sees that
the worker disappeared, runs the automatic check on the uncertain operations, waits for a live worker, and resumes
the apply from the saved progress marker. In the morning the run is applied. Its detail shows one automatic resume,
its cause and the result of the automatic check.

**Why this priority**: It removes the person from the most common interruption, which is unrelated to the data. Without
it, a run waits for a person for as long as nobody looks.

**Independent Test**: With automatic resume turned on, apply a plan of at least 10,000 operations, kill the worker
during the apply, start a new worker, and wait. Check that the run ends applied with no command run by a person, and
that a new plan of the same configuration version shows 0 creates and 0 updates.

**Acceptance Scenarios**:

1. **Given** a site with automatic resume turned on and a run whose apply saved its progress marker at 6,000 of
   10,000 operations, **When** the worker is killed and the liveness monitor ends the execution under rule 8, and a
   new worker is live, **Then** Sync runs the automatic check, the check reports no `differs`, and Sync starts one
   resume execution that applies operations 6,001 to 10,000. The run ends applied and `reconciliation_required` is
   cleared.
2. **Given** the same run, **When** the operator runs `runs show` or opens the run page after it ends, **Then** the
   resume is shown with "Sync (automatic resume)" in place of an account, with its cause ("the worker
   `infrahub-sync-service-…` stopped sending heartbeats"), with the result of the automatic check, and with the number
   of automatic resumes of the run.
3. **Given** a run interrupted under rule 6 (Prefect reports the flow run as `crashed`), **When** the automatic check
   passes, **Then** Sync starts an automatic resume in the same way as under rule 8.
4. **Given** a site with automatic resume turned on and no live worker, **When** a run is interrupted under rule 8,
   **Then** Sync submits no resume while no worker is live, and submits it once a worker is live. The waiting resume
   is not abandoned by the admission time limit.

---

### User Story 2 - An automatic resume that does not start, and why (Priority: P2)

The worker disappears, but the automatic check finds a node that a person changed at the destination after the plan
was made. Sync does not resume the run. The run page states that the automatic resume did not start because of one
`differs`, and names the operation. The operator decides: a new plan, or a `runs resume` after reading the change.

**Why this priority**: The automatic resume is safe only because it stops where a person must decide. Each refusal must
say why, or the operator cannot act on it.

**Independent Test**: For each reason in the acceptance scenarios, set up the condition, interrupt the write, and check
that no resume starts, that the run stays interrupted, and that the run detail and a run event state the reason.

**Acceptance Scenarios**:

1. **Given** an interrupted run whose automatic check reports at least one `differs`, **When** Sync decides, **Then**
   no resume starts, the run stays interrupted, and the run detail states the reason and the number of `differs`.
2. **Given** an interrupted run whose automatic check fails a pre-write check (for example, the destination schema
   changed), **When** Sync decides, **Then** no resume starts and the reason names the failed check.
3. **Given** a site that requires approval and an interrupted run with no recorded approval for its plan, **When**
   Sync decides, **Then** no resume starts and the reason states that approval is missing. A later `approve` does not
   start an automatic resume.
4. **Given** a run that the operator asked to cancel and whose cancellation ended under rule 2, **When** the liveness
   monitor ends the execution, **Then** no automatic resume is considered.
5. **Given** a run whose worker reported a failure itself after its first write, **When** the run ends interrupted,
   **Then** no automatic resume is considered.
6. **Given** a run whose previous automatic resume ended without moving the progress marker forward, **When** its
   worker disappears again, **Then** no resume starts and the reason states that the previous automatic resume made
   no progress.
7. **Given** a run that already received 3 automatic resumes, **When** its worker disappears again, **Then** no
   resume starts and the reason states that the limit of 3 is reached.
8. **Given** any run where no automatic resume started, **When** the operator runs `verify` and `runs resume`, **Then**
   the manual resume works as spec 016 defines, and it does not count toward either limit.

---

### User Story 3 - A scheduled run on the default branch (Priority: P3)

An hourly schedule writes to the default branch. A worker restart interrupts one of its runs. Sync resumes it
automatically, the run ends applied, and the schedule's count of failed runs in a row is set to zero. A start time that
arrives while the resume runs is skipped and recorded.

**Why this priority**: Scheduled runs are the runs nobody watches. They rely on User Stories 1 and 2 and add only the
interaction with the schedule's rules.

**Independent Test**: Create a schedule that writes to the default branch, interrupt one of its runs by killing the
worker, and check the run's result, the schedule's failure count and the skipped start.

**Acceptance Scenarios**:

1. **Given** a schedule that writes to the default branch with 2 failed runs in a row, **When** its next run is
   interrupted under rule 8 and the automatic resume ends applied, **Then** the run counts as succeeded and the count
   is set to zero.
2. **Given** a scheduled run waiting for an automatic resume or running one, **When** the schedule's next start time
   arrives, **Then** the overlap policy treats the run as in progress, and the start is skipped and recorded with that
   reason.
3. **Given** a scheduled run that writes to a branch it created, **When** its worker disappears, **Then** no automatic
   resume is considered, because spec 021 makes such a run not resumable.
4. **Given** a scheduled run left interrupted after an automatic resume did not start, **When** the run is counted,
   **Then** it counts as one failed run toward the 5 of spec 021, FR-032.

---

### Edge Cases

- **The same operation kills the worker each time** (for example, it uses too much memory). The first automatic resume
  ends without moving the marker, so no further automatic resume starts (FR-009).
- **Workers keep stopping during a cluster incident, but each resume makes progress.** At most 3 automatic resumes
  start (FR-010).
- **An operator cancels the run while an automatic resume is waiting for a worker or running.** The cancellation ends
  the run as today, and no further automatic resume is considered.
- **An operator runs `runs resume` while an automatic check or resume is running.** The run already has an
  unfinished execution, so the manual resume is refused as today. While Sync waits for a live worker, the person
  takes over (FR-019). When the automatic resume is not started (a "no
  resume" decision), a manual resume is accepted.
- **The Sync API restarts between the decision and the submission of the resume.** The decision is stored before
  anything is submitted. After the restart, Sync submits the decided resume once (FR-014).
- **The automatic check cannot reach the destination.** The check fails, and no resume starts. The reason names the
  failure.
- **The automatic check leaves operations out because of the 2,000-operation limit.** The resume starts when no
  checked operation `differs`, and the run detail states how many operations were not checked (FR-007).
- **The setting is turned off while a decision waits for a worker.** The stored decision stays; the waiting check or resume
  is submitted. The setting is read only when Sync decides.
- **The setting is turned on while runs are already interrupted.** Those runs are not resumed automatically and show
  no decision, as before the setting was turned on.
- **A person runs `verify` or `runs resume` while Sync waits for a live worker.** The person takes over: the decision
  ends as "not started: a person took over", and the person's request proceeds as today (FR-019). While the automatic
  check or the automatic resume is running, the person's request is refused as today, because the run has an
  unfinished execution.
- **An automatic resume is itself interrupted by a disappearing worker.** The same rules apply again, inside the limits
  of FR-009 and FR-010.
- **A `sync` worker disappears while it is still planning.** The run saved no plan, so it is not resumable (spec 016,
  FR-013). The decision is "no resume" with that reason.
- **The run's plan left out records** (spec 007). The automatic resume reuses the left-out confirmation that admitted
  the interrupted write (FR-008).

## Requirements *(mandatory)*

### Functional Requirements

#### Which runs are resumed

- **FR-001**: The system MUST consider an automatic resume only for a run whose write execution (apply, `sync` or
  resume) the liveness monitor ended under rule 6 with the Prefect state `crashed`, or under rule 8. A flow run that
  Prefect reports as `failed`, `cancelled` or `completed` MUST NOT be considered.
- **FR-002**: The system MUST NOT consider an automatic resume for a run whose execution ended because of a
  cancellation (rules 1 and 2, and the cancellation recovery of the request), or because its worker reported a
  failure itself.
- **FR-003**: The system MUST consider an automatic resume only for a run that a person could resume with
  `runs resume` under spec 016 and spec 021. A run that saved no plan, a cancelled run, and a scheduled run that writes
  to a branch it created are never resumed automatically.
- **FR-004**: A site-wide setting of the Sync API MUST turn automatic resume on or off. It MUST be off by default.
  The system reads it when the liveness monitor ends the execution, so a run interrupted while it was off is not
  resumed automatically later. While the setting is off, the system MUST record no decision and no event, so a site
  that did not turn the feature on sees no change.

#### The automatic check, the approval and the confirmation

- **FR-005**: Before an automatic resume, the system MUST run the automatic check on the run. It MUST be recorded as a
  `verify` of the run with no account, so the rule of ADR 0027 that a resume needs a passing `verify` newer than the
  latest write execution holds unchanged.
- **FR-006**: The system MUST NOT start an automatic resume when the automatic check fails a pre-write check, cannot
  reach the destination, or reports at least one `differs`. A `missing` result MUST NOT prevent the resume.
- **FR-007**: The automatic check MUST use the same operation limit as `verify`, currently 2,000 (ADR 0027). When it
  leaves operations out, the resume MUST still start when no checked operation `differs`, and the run detail and the
  run event MUST state how many operations were not checked.
- **FR-008**: An automatic resume MUST pass the same conditions as `runs resume` (spec 016, FR-011): the saved plan's
  checksum, the configuration write lock, every pre-write check, and the run's approval when the site requires
  approval. It MUST reuse the left-out confirmation that admitted the interrupted write. When the site requires
  approval and the run has none, no resume starts, and a later `approve` MUST NOT start one.

#### Limits

- **FR-009**: The system MUST NOT start an automatic resume when the run's previous automatic resume ended without
  moving the progress marker forward.
- **FR-010**: The system MUST start at most 3 automatic resumes for one run. A resume that Sync submitted counts, also
  when no worker picked it up.
- **FR-011**: Manual resumes MUST NOT count toward the limits of FR-009 and FR-010, and MUST stay available after
  either limit is reached.

#### Start exactly once, and only with a live worker

- **FR-012**: The system MUST NOT submit an automatic resume while no worker is live. It MUST submit the resume once a
  worker is live, and the waiting resume MUST NOT be abandoned by the admission time limit.
- **FR-013**: The system MUST decide each interrupted run at most once for each interrupted execution, and MUST store
  the decision before it submits anything.
- **FR-014**: The system MUST submit each decided automatic resume exactly once, including when the Sync API restarts
  between the decision and the submission, and when several Sync API processes run the liveness monitor.

#### What the operator sees

- **FR-015**: The system MUST record an automatic resume with no account, with a marker that Sync started it, and with
  a reason built from the cause: rule 6 ("Prefect reported the flow run as ended") or rule 8 ("the worker `<name>`
  stopped sending heartbeats"). The CLI and the web interface MUST show "Sync (automatic resume)" where they show an
  account for a manual resume. When the run has a recorded approval, the run detail MUST keep showing the approving
  account.
- **FR-016**: The run detail in the Sync API, `runs show` and the run page MUST show, for each interrupted execution
  of the run, the decision and its reason, and the number of automatic resumes the run received. The reasons for "no
  resume" are: run not resumable, check failed (naming the check), check found `differs` (with the count), approval
  missing, previous automatic resume made no progress, limit of 3 reached, the check or the resume was not picked up
  by a worker, and a person took over.
- **FR-017**: The system MUST record a run event for each decision, with the reason and, for a "resume" decision, the
  number of operations the automatic check did not check.

#### Schedules

- **FR-018**: A scheduled run MUST count toward spec 021's FR-032 by its final result. A run that an automatic resume
  finished counts as succeeded and sets the count to zero. A run left interrupted counts as one failed run. While a
  run waits for or runs an automatic resume, it MUST count as in progress for the overlap policy, and a start time
  that arrives then is skipped and recorded with that reason.

#### A person takes over

- **FR-019**: When a person runs `verify` or `runs resume` on a run whose decision is waiting for a live worker, the
  system MUST end the decision as "not started: a person took over" once it has accepted the request, and MUST then
  handle the request as it does today. A request that the system refuses MUST leave the decision waiting. While Sync
  submits the waiting check or resume, and while the automatic check or the automatic resume runs, the system MUST
  refuse the person's request with `409`, as it refuses any request on a run with an unfinished execution.
  Changed during implementation: the first text ended the decision before the request was handled, so a refused
  request also ended it, and it did not cover the time between Sync's submission and the move of the decision.

#### Governance

- **FR-020**: Principle I of the constitution MUST be amended with a second named case, automatic resume, on the
  pattern of the schedule case: the explicit instruction is the interrupted run's own write and the site's setting;
  the target servers are those of the saved plan; the human approval is the approval of the interrupted run. A new
  decision record MUST amend ADR 0027 and the liveness monitor's rule that an execution is never replayed.

### Key Entities

- **Run** (existing): gains its decisions and the number of automatic resumes it received.
- **Resume execution** (existing, spec 016): gains a marker that Sync started it. An automatic resume has no account
  and a reason built from the cause.
- **`verify` result** (existing): the automatic check is recorded as a `verify` with no account.
- **Decision** (new): for one interrupted execution, "resume" or "no resume", the reason, the cause (rule 6 or rule 8),
  the time, and whether the resume was submitted.
- **Progress marker** (existing, ADR 0027): the start position of a resume, and the measure of progress for FR-009.
- **Live worker rule** (existing): shared with `/status` and the liveness monitor, and with the `/api/health` work of
  INFP-663.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a test where the worker is killed part way through an apply of at least 10,000 operations, the run
  ends applied, and a new plan of the same configuration version shows 0 creates and 0 updates, with no command run by
  a person.
- **SC-002**: In every case of FR-002, FR-003, FR-006 and FR-008, no automatic resume starts: 0 over the test matrix.
- **SC-003**: No run receives more than 3 automatic resumes, and a run whose automatic resume made no progress receives
  no further one.
- **SC-004**: From `runs show` or the run page alone, without worker logs or Prefect, an operator can tell whether a run
  was resumed automatically, how many times, and, when an automatic resume did not start, why.
- **SC-005**: A restart of the Sync API at any point between a decision and its submission leads to exactly one resume
  execution.

## Assumptions

- The Infrahub destination adapter stays the only adapter that applies saved plans, and its creates, updates and
  relationship writes stay convergent upserts (ADR 0012, ADR 0013).
- Plan approval will be partly deprecated. Automatic resume depends on approval only through the check it shares with
  `runs resume`.
- The live worker rule is the rule the liveness monitor applies today. If the `/api/health` work (INFP-663) moves it
  into one shared function, this feature uses that function.
- Pre-release V3 databases are recreated, not migrated, when the run storage changes (ADR 0010).

## Dependencies

- [INFP-663](https://opsmill.atlassian.net/browse/INFP-663), requirement 3: worker health in `/api/health`, branch
  `029-api-health`. Not blocking: this feature uses today's rule until that work lands.
- An existing issue about the 2,000-operation limit of `verify`. Not blocking.

## Out of Scope

- Retrying a failed operation, or any failure the worker reported itself.
- Resuming a run after a cancellation.
- A setting for each configuration or each schedule.
- Notifications.
- Raising or removing the 2,000-operation limit of the check.
- The recovery actions `replay-from-tier`, `re-extract` and `escalate`, and a list of each operation and its outcome
  (INFP-650, separate work). The user dropped `resolve-drift`.
- Adapters other than Infrahub.

## Open Questions

- [NEEDS CLARIFICATION: What is the key of the existing issue about the 2,000-operation limit of `verify`?]
- [NEEDS CLARIFICATION: The limit of 3 automatic resumes per run has no source. Does a real interruption pattern
  confirm it?]
