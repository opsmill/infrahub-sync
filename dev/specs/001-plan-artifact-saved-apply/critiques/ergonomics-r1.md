# Ergonomics critique — round 1

**Feature**: `001-plan-artifact-saved-apply-infp-653` · **Head**: `016802e` · **Lens**: ergonomics
(CLI operator, in-process API caller, adapter author) · **Persona**: reviewed through `wwbd`
(Benoit Kohler), used as a lens only — no new requirements taken from it.

Nothing is built yet. Everything below is judged against the planned surface as the artifacts
describe it, and against the current tree as it actually behaves. Every claim about present
behavior was verified by reading the file or running the command; anchors are `file:line`.

## Findings

| ID | Severity | Summary | Anchor |
|---|---|---|---|
| ERG-01 | Must-Address | `diff --run-id` carries two opposite meanings mode-switched by a second flag; omitting `--from-plan` silently overwrites the artifact the operator meant to read | `infrahub_sync/cli.py:98`, `:152`; `contracts/cli-review-mode.md:26` |
| ERG-02 | Must-Address | Deletes recorded by default make a normal, healthy apply end in run state `failed` on any destination holding objects absent from the source — the documented default posture | `spec.md:1450`, `:1134`; `docs/docs/migrating-from-netbox-or-nautobot.mdx:92` |
| ERG-03 | Must-Address | The review summary prints `delete N` without stating that no delete will be executed and that its presence will fail the apply | `contracts/cli-review-mode.md:55` |
| ERG-04 | Must-Address | Reader and derivation errors carry no next-action obligation, and two of them echo the operator's own input back with nothing enumerable | `contracts/plan-reader-api.md:65-76`, `:111-118` |
| ERG-05 | Must-Address | `SavedPlan.operations(kind=…)` raises on an empty result — a CLI presentation rule pushed down into the data API, against FR-029's stated purpose | `contracts/plan-reader-api.md:40`, `:52-53` |
| ERG-06 | Must-Address | `verify_plan(write_surface_available: bool)` cannot produce the error message the same contract promises | `contracts/plan-reader-api.md:79-85`, `:104` |
| ERG-07 | Must-Address | Three quickstart steps do not work as written; one of them makes SC-012's evidence pass for the wrong reason | `quickstart.md:60-64`, `:116-126` |
| ERG-08 | Must-Address | The adapter write-surface contract calls two `PeerResolver` methods that the class it defines does not have | `contracts/destination-write-surface.md:54` vs `:146-152` |
| ERG-09 | Must-Address | No artifact specifies the help text for the three new flags, and none updates `--run-id`'s now-wrong help string — which `docs.generate` then copies into the reference docs | `tasks.md:285`, `:314`; `infrahub_sync/cli.py:98` |
| ERG-10 | Must-Address | `--kind` without `--detail`, and `--detail`/`--kind` on the live path, have no specified behavior | `contracts/cli-review-mode.md:23-31` |
| ERG-11 | Recommended | `--from-plan` is a user-visible flag with a known short life and a name that reads backwards on `diff` | `spec.md:1601-1607` |
| ERG-12 | Recommended | `verification_notes: list[str]` forces an in-process caller to parse prose for information the verifier already models structurally | `contracts/plan-reader-api.md:37`, `:111-118` |
| ERG-13 | Recommended | `PlanSummary.by_action` is stringly typed against a closed action vocabulary | `contracts/plan-reader-api.md:43` |
| ERG-14 | Recommended | `verify_plan` is documented in the supported-surface contract but AD029 names only `read_saved_plan` as supported | `contracts/plan-reader-api.md:5-7`, `:79` |
| ERG-15 | Recommended | The adapter contract changes (`apply_cached_row` removed, `apply_planned_operation` added) with no task updating any `dev/` adapter documentation | `dev/guidelines/writing-an-adapter.md`, `tasks.md` (no match) |
| ERG-16 | Recommended | No machine-readable review output, and the rendered text is explicitly declared unstable — CI gating has one path and nothing documents it | `spec.md:1586-1593` |
| ERG-17 | Recommended | The delete-docs sweep list omits two pages that make delete claims this change falsifies | `tasks.md:317`; `docs/docs/migrating-from-netbox-or-nautobot.mdx:92`, `docs/docs/readme.mdx:74` |
| ERG-18 | Nit | `PeerResolver.resolve` is keyword-only, `remember` positional, in the same class block | `contracts/destination-write-surface.md:149-152` |
| ERG-19 | Nit | Quickstart mixes bare `python` and `uv run python`; the corruption snippet drops `ensure_ascii=False` | `quickstart.md:186-191` |
| ERG-20 | Nit | The negative walkthrough leaves the run directory permanently corrupted with no stated recovery step | `quickstart.md:182-198` |

Counts: **10 Must-Address · 0 RETHINK · 7 Recommended · 3 Nit.**

---

## ERG-01 — Must-Address — `--run-id` means two opposite things on one command

**Evidence.** Today `diff --run-id X` means "re-use run directory X for a live comparison". Verified:
the option is declared at `infrahub_sync/cli.py:98` with help `"Re-use a specific cache run id."`, it
is passed into `get_potenda_from_instance` (`:137`), which does
`rdir.mkdir(parents=True, exist_ok=True)` (`infrahub_sync/utils.py:244-246`) — so an unknown id is
**created**, not rejected — and the run then writes its plan into that directory at
`infrahub_sync/cli.py:152` (`ptd.write_plan(mydiff)`). The user documentation states the purpose
plainly: `docs/docs/running-a-sync.mdx:40` — "re-use a specific cache run id; **useful when you want
to overwrite a previous run's plan in place**."

The new mode gives the same option on the same command the opposite meaning — "the stored run to
read" — with an unknown id now a hard error, selected by a separate boolean
(`contracts/cli-review-mode.md:19`, `:26`; `spec.md:237-247`).

So `--run-id X` on `diff`:

| `--from-plan` | Meaning | Unknown id | Effect on the artifact at X |
|---|---|---|---|
| absent | write target | created silently | **overwritten** |
| present | read source | hard error | untouched |

**Why it matters.** The two modes are not merely different, they are inverses, and the discriminator
is a flag whose omission is invisible. Concretely: an operator means to review run
`20260726T1804-9f3ac210`, types `infrahub-sync diff --name from-netbox --run-id 20260726T1804-9f3ac210`
and forgets `--from-plan`. What happens is not "nothing" — it is a full live extract of both systems,
the pipeline lock taken for the duration, and the plan they were about to review destroyed and
replaced in place. Under FR-030 the command may then also hard-fail, leaving the run directory in a
state neither the old plan nor a new one. This population of users does not exist today: nobody
currently types `--run-id` on `diff` intending a read, because reading was not a thing `diff` did.
The feature creates the users and the footgun in the same change.

Two further symptoms fall out of the same design: `--from-plan` with no `--run-id` needs its own
error case (`spec.md:387-388`, AD036), and `--run-id` needs two help strings it cannot have (ERG-09).

The spec's own reading of the brief makes the cheap escape available and then declines it: AD019
(`spec.md:223-230`) establishes that the brief forbids only a command **group**, and that a sibling
top-level command would be compliant. AD005 chose to extend `diff` anyway. That choice is defensible;
reusing `--run-id` to carry it is the part that isn't.

**Minimum fix.** Make the run identifier the **value of the review flag** rather than a second flag:

```text
infrahub-sync diff (--name <sync> | --config-file <path>) [--directory <dir>]
                   --from-plan <run-id> [--detail] [--kind <kind>]
```

`--run-id` keeps exactly one meaning; the missing-flag accident becomes impossible, because there is
no way to name a stored run without also selecting read mode; AD036's `--from-plan`-without-`--run-id`
error case disappears; and `diff --from-plan` with no value is a Typer usage error for free. If the
two-flag shape is kept instead, then ERG-09 and ERG-10 are not optional, and `--run-id` must be
rejected on `diff` when it names a run that already holds a `plan/` artifact and `--from-plan` was
not passed — the guardrail-by-default posture the repo already takes on destructive automation
(`infrahub_sync/cli.py:186-189`, the rowcount guardrail).

## ERG-02 — Must-Address — the normal apply now ends `failed`

**Evidence.** FR-015 (`spec.md:1134-1149`) records deletes in every plan, unconditionally: "Delete
operations MUST be recorded in the plan, changing today's default of suppressing them… The comparison
flags a project configures MUST keep their present meaning for the write path and MUST NOT be
loosened to make deletes visible." And: "No input is added for requesting that deletes be computed."
There is no opt-out.

FR-016/FR-017 (`spec.md:1170-1175`) and SC-007 (`spec.md:1450-1455`) then settle what an apply does
with one: "A plan containing a delete operation applies its non-delete operations, does not delete
from the destination, and **ends in run state `failed`**". Out of Scope confirms it is deliberate
(`spec.md:1539-1542`): "Until that lands, a plan containing a delete behaves as FR-017 and SC-007
specify: the delete is reported and the run fails."

Now the population. Deletes are derived from destination-only identities in the mapped kinds. The
default comparison flag is `SKIP_UNMATCHED_DST` (`docs/docs/creating-a-sync-project.mdx:103`), and
the documentation actively recommends the posture that produces destination-only objects:
`docs/docs/migrating-from-netbox-or-nautobot.mdx:92` — "Add Infrahub-only data (new schemas, design
objects, intent data) without worrying that a sync run will delete it." Any source-side filter, any
object created directly in Infrahub, any partial migration produces destination-only identities in a
mapped kind.

**Why it matters.** For the majority configuration, the headline workflow this feature exists to
deliver — plan, review, apply — terminates in `failed` on a completely healthy sync, every time. Two
consequences, both operator-facing:

1. `run.json` `status` stops being a usable health signal. Any CI gate, dashboard, or `for` loop
   reading it sees permanent red.
2. There is no lever. No flag suppresses delete derivation, no flag makes apply tolerate a recorded
   delete, and the refusal has no remedy the operator can execute — the remedy is "wait for the
   ownership grammar outcome". A failure the operator cannot clear is worse than a warning.

The artifacts do notice adjacent versions of this. AD024 (`spec.md:266-288`) goes to real trouble to
prevent a *phantom* delete from "becoming a spurious operator-facing run failure in the one feature
whose purpose is trustworthy review-before-write" — and SC-017 asserts it (`spec.md:1524`). The same
sentence applies verbatim to a **genuine** delete on a default configuration, and there it is
accepted rather than prevented. The quickstart's own manual walkthrough (`quickstart.md:155-175`)
runs `diff` then `apply` and expects success; against any non-pristine Infrahub, step 4 fails.

**Minimum fix.** Separate "recorded but not executable" from "unsupported". A delete is a *known,
intended, specified* non-execution, not an operation the apply failed to understand. Let the apply
complete, report the deletes explicitly — count, identifiers, and the reason — and record a run state
that means "applied, with N recorded deletes not executed", reserving `failed` for FR-017's real
case: an operation the apply genuinely cannot handle. FR-017's "never silently skipped" contract is
satisfied by the report, not by the run state. If changing the run state is out of reach, the
alternative is to gate delete derivation behind an explicit input, contrary to FR-015's current
"no input is added" — but the first fix is smaller and keeps the reviewability FR-015 wants.

## ERG-03 — Must-Address — the summary shows `delete 4` and does not say what that means

**Evidence.** `contracts/cli-review-mode.md:53-55`:

```text
By action     create 21   update 12   delete 4
```

Nothing in the rendering states that those four deletes will not be executed (FR-016), or that their
presence will fail the apply (ERG-02). The manifest's `deletes computed: yes` line sits directly
above and reads, to anyone who has not read the spec, as confirmation that deletes are part of the
change set.

**Why it matters.** This is a review screen — its entire job is to tell an operator what is about to
happen. It currently tells them the opposite of what will happen, in the one direction where being
wrong is expensive. An operator seeing `delete 4` on a production sync either aborts a safe apply, or
approves it believing the cleanup is included. Both are wrong, and both are the review surface's
fault. This is separate from ERG-02 because it survives ERG-02's fix: even with a clean run state,
the count needs its caveat.

**Minimum fix.** Where the count is non-zero, annotate it in the render — deletes recorded for review,
never executed by apply — and repeat it on each delete record under `--detail`. Add the same
statement to `docs/docs/running-a-sync.mdx` in T069.

## ERG-04 — Must-Address — most refusals leave the operator without a next action

The verifier does this well and the reader does not. `VerificationFailure`
(`contracts/plan-reader-api.md:111-118`) carries `check`, `run_id`, `expected`, `found`, and — the
part that matters — `next_action: str`, a required field. Every pre-apply refusal therefore
structurally cannot ship without telling the operator what to do. That is the right design.

The reader's error taxonomy (`contracts/plan-reader-api.md:65-76`) has no equivalent. Its "Message
must name" column is content-only, and AD036 (`spec.md:376-379`) scopes the next-action obligation to
*refusals* — meaning the verifier — so the reader escaped it. Walking the full set:

| Failure | Next action stated? | What the operator is left with |
|---|---|---|
| Checksum mismatch (render) | **Yes** — "Re-run `diff` to rebuild it" (`cli-review-mode.md:72`) | fine |
| Config-version mismatch | Yes, via `next_action` | fine |
| Snapshot mismatch / absent / truncated | Yes, via `next_action` | fine |
| Run-id mismatch | Yes, via `next_action` | fine |
| Unsupported operation / no write surface | **Yes** — "tells the operator to use `sync`" (`plan-reader-api.md:104`) | fine |
| Pre-existing-format plan (`PlanFormatV1Error`) | **Yes** — "the instruction to re-plan" | fine |
| **Torn artifact** (`PlanArtifactTornError`) | **No** | names which part is torn and expected-vs-found. Then what? Re-plan, presumably — unstated |
| **Unrecognized format version** (`PlanFormatVersionError`) | **No** | AD028 (`spec.md:320-328`) justifies a distinct message on the grounds that "the two conditions have different operator remedies" — and then the contract never states the remedy. Found-below-supported means re-plan; found-above-supported means upgrade `infrahub-sync`. The operator must infer which, from two hex-free integers |
| **Unreadable path** (`PlanArtifactUnreadableError`) | **No** | names the path |
| **Unknown run id** | **No**, and see below | names the id and the expected path |
| **`--kind` matching nothing** (`UnknownPlanKindError`) | **No**, and see below | names the kind — which the operator just typed |
| **Derivation failure on `diff`** (FR-030) | **No** | "an error naming the destination kind and the cause" (`cli-review-mode.md:109-110`). This is the one that newly hard-fails a command that has always succeeded, and there is deliberately no tolerance switch (`:112`). The operator gets a kind and a cause and no route forward |
| **Peer zero-match** | **No** | names peer kind, peer identity, referring operation id |
| **Peer multi-match** | **No** | names peer kind, peer identity, match count |
| **Duplicate operation id** | **No** | pathological by construction (FR-021); acceptable |
| **Unserializable payload value** | **No** | names kind, field, Python type. Fails `diff`. Remedy is a mapping change or a bug report — unstated |

Two of these are worse than merely silent, because they echo the operator's own input and withhold
information already in hand:

- **`--kind LocationSit`** (a typo) produces "no operation for kind LocationSit". The reader has just
  computed `PlanSummary.by_kind` (`contracts/plan-reader-api.md:44`) — the exact list of kinds the
  plan holds. Not offering it forces a second command.
- **An unknown run id** produces the id and the expected path. The cache root
  (`cache_root_for(sync_name)`, `infrahub_sync/cache/paths.py:26-42`) is a directory whose entries are
  the valid run ids, and the spec explicitly puts a `runs` command group out of scope
  (`spec.md:1555-1556`) — so `ls` is the operator's only recourse and nothing tells them that.

**Minimum fix.** Give `PlanArtifactError` the same obligation `VerificationFailure` already carries:
every subclass's message ends with the operator's next action, stated in the error taxonomy table
alongside "Message must name". Specifically: torn and unreadable → re-plan / check permissions on the
named path; version → branch on found-vs-supported and say upgrade or re-plan; FR-030 derivation
failures → the concrete route (correct the mapping / report it), since there is no tolerance switch;
peer misses → resolve at the destination or in the source. Additionally, `UnknownPlanKindError` lists
the kinds present in the plan, and the unknown-run-id error lists the run ids present under the cache
root (bounded — most recent N).

## ERG-05 — Must-Address — the data API raises where it should return empty

**Evidence.** `contracts/plan-reader-api.md:40` defines
`operations(self, *, kind: str | None = None) -> list[PlannedOperation]`, and `:52` obliges it to
raise `UnknownPlanKindError` when `kind` matches no operation in the plan.

**Why it matters.** That rule is FR-006's *CLI* rule — "MUST NOT be presented as empty detail"
(`spec.md:969-971`) — pushed down into the library. It is right for a renderer and wrong for a data
accessor. The same document states the reason it is wrong three lines earlier: the reader "Returns
**data**, never rendered text, so a caller consumes it without parsing output" (`:29`). A caller who
writes the natural loop —

```python
for kind in kinds_i_care_about:
    ops = plan.operations(kind=kind)
```

— gets an uncaught `UnknownPlanKindError` in production the first time one of those kinds has no
operations, which is a completely ordinary state of a plan. The caller can pre-check against
`summary().by_kind`, but requiring a pre-check to avoid exception control flow on a filter is exactly
the awkwardness FR-029 exists to remove. Note also that the two conditions collapsed into one
exception — "no operation of this kind" and "kind the configuration does not declare" — are
semantically different, and the second is the only one that is genuinely an error.

**Minimum fix.** `operations(kind=…)` returns `[]` for a declared kind with no operations, and raises
only for a kind the supplied `config` does not declare. The CLI renderer, which is where FR-006's
never-show-empty rule belongs, turns an empty list into the named error. This keeps FR-006 intact at
the surface it was written for and leaves the data API behaving like a filter.

## ERG-06 — Must-Address — `verify_plan` cannot produce the message it promises

**Evidence.** The signature (`contracts/plan-reader-api.md:79-85`) takes
`write_surface_available: bool`. The check table (`:104`) says of the write-surface check: "Error
**names the adapter class** and tells the operator to use `sync`."

A `bool` carries no adapter class. `VerificationFailure.found` (`:116`) therefore cannot be populated
with it, and the function has no other input from which to recover it.

**Why it matters.** FR-023 (`spec.md:1212-1216`) requires "a clear, actionable error **naming the
adapter**", and the engine already produces exactly that today
(`infrahub_sync/potenda/__init__.py:341-370`, per the write-surface contract's own baseline table).
As specified, the new gate is a regression on an existing, working error message: the operator loses
the adapter name at the moment they most need it, since "use `sync` instead" only makes sense once
they know *which* destination lacks the surface. Either the implementer silently widens the signature
(and the contract is wrong) or they honor it (and the message is worse than today's).

**Minimum fix.** Replace the bool with the thing the message needs — `destination_adapter: str` plus
a `write_surface_available: bool`, or a single small value carrying both. State in the contract that
`VerificationFailure.found` for this check is the adapter class name.

## ERG-07 — Must-Address — quickstart steps that do not work as written

Read literally and executed, three steps fail or mislead.

**(a) The independent checksum recompute never reads the run directory** (`quickstart.md:116-126`).
The snippet is `uv run python - <<'PY'` with no arguments, and its first line is
`run = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")`. Verified empirically:

```text
argv: ['-']
resolved run dir: /private/tmp
would open: /private/tmp/plan/manifest.json exists: False
```

`argv` is always `['-']`, so `run` is always `.`, and the block opens `./plan/manifest.json` relative
to wherever the operator is standing — the repository root, in the flow the page describes. The `RUN`
shell variable set ten lines earlier (`:107`) is never passed. Result: `FileNotFoundError`. The
recipe itself is correct — popping `plan_checksum`, `run_id`, `created_at`, `ensure_ascii=False`, and
`body + ops` with no separator all match `contracts/plan-artifact-format.md:41` and AD035 — so this
is purely a plumbing defect in the one snippet whose whole purpose is to let an operator verify the
checksum without trusting the tool.
*Fix*: `uv run python - "$RUN" <<'PY'`.

**(b) SC-012's evidence procedure is a no-op on a committed branch** (`quickstart.md:60-64`):

```bash
git stash && uv run infrahub-sync --help > /tmp/help-before.txt && git stash pop
```

Verified on this tree: `git stash` with nothing to save prints "No local changes to save" and exits
0. The SpecKit flow commits per phase, so at evidence time the feature work is committed and the
tree is clean — `git stash` does nothing, `help-before.txt` is captured from the **post-change**
binary, and the `diff` on line 62 passes because it is comparing a file to itself. SC-012 would
report success without ever having observed the pre-change command list. Worse, `&&` still advances
to `git stash pop`, which on a developer with an unrelated stash entry applies that stash into the
working tree.
*Fix*: capture the baseline from the merge-base rather than the working tree —
`git show $(git merge-base HEAD main):<path>` into a temporary worktree, or simply
`git worktree add` at the merge-base and run `--help` there. Drop the `git stash` pair.

**(c) The manual walkthrough's steps 4 and 5 fail against any non-pristine destination**
(`quickstart.md:168-175`) — see ERG-02. The page presents them as the happy path.

## ERG-08 — Must-Address — the adapter contract calls methods that do not exist

**Evidence.** `contracts/destination-write-surface.md:54`, inside the numbered flow an adapter author
implements:

```text
3. for ref in operation.relationships:
       data[ref.field] = peers.resolve_one(ref) | peers.resolve_many(ref)   # destination node ids
```

The `PeerResolver` the same document defines ninety lines later (`:146-152`) has neither method:

```python
class PeerResolver:
    def resolve(self, *, peer_kind: str, identity: dict[str, Any],
                referring_operation_id: str) -> str: ...
    def remember(self, kind: str, identity: dict[str, Any], node_id: str) -> None: ...
```

Beyond the names, the line is not evaluable as written: `|` between a single resolved id and a
peer-set is not an operation with a meaning, and `resolve`'s real signature takes three keyword
arguments — `peer_kind`, `identity`, `referring_operation_id` — none of which the pseudo-code's
`ref` supplies without unpacking.

**Why it matters.** This is the one document an author of adapter number ten reads to learn what they
must implement, and the step that translates a plan's relationship references into destination ids is
the hardest part of the job. It is currently a sketch that cannot be transcribed. It also hides a
real design question the author needs answered: does the resolver handle cardinality, or does the
adapter branch on `ref.cardinality` and call `resolve` once per peer? The contract's step 7
(`_replace_relationship_set`, `:59-60`) implies the adapter owns the many case, but step 3 implies
the resolver does.

**Minimum fix.** Rewrite step 3 against the real signature, branching on `ref.cardinality` explicitly,
and state which side owns the many case. A three-line correction.

## ERG-09 — Must-Address — nothing specifies the help text, including the one string that becomes wrong

**Evidence.** T058 (`tasks.md:285`) specifies the branch point, the resolution, the renderer, and the
channel — and says nothing about what the three new options say in `--help`. No other task, contract,
or requirement does either; a grep for `help=` across `plan.md` and `tasks.md` returns no flag copy.
T070 (`tasks.md:314`) then runs `uv run invoke docs.generate` to regenerate
`docs/docs/reference/cli.mdx` **from those strings**, so whatever the implementer improvises becomes
the published reference documentation in the same commit.

Separately, `--run-id`'s existing help — `"Re-use a specific cache run id."`
(`infrahub_sync/cli.py:98`), rendered today as line 61 of `docs/docs/reference/cli.mdx` — becomes
false in one of the two modes the option now has, and no task changes it.

**Why it matters.** The remit's question is whether `--from-plan`'s invariants — no adapter, no
extraction, no lock, no run directory, hard error on an unknown run id — are discoverable from the
help text or only from the spec. As planned: **only from the spec**, plus whatever prose T069 puts in
`running-a-sync.mdx`. The two that an operator actually needs at the prompt are the two that
distinguish the modes: that review takes no lock (so it is safe during a running sync — the thing
that makes the feature usable, `quickstart.md:177-178`) and that an unknown run id errors rather than
being created. `--help` is where an operator checks before running something against production.

**Minimum fix.** Fix the copy in the task, so it is reviewable rather than improvised:

- `--from-plan` — "Read the stored plan for `--run-id` instead of comparing live systems. Constructs
  no adapter, extracts nothing, takes no lock; an unknown run id is an error."
- `--detail` — "With `--from-plan`, list one record per operation instead of the summary."
- `--kind` — "With `--from-plan --detail`, show only this destination kind."
- `--run-id` — "Cache run id. Without `--from-plan`, the run to write into (an existing plan there is
  overwritten); with `--from-plan`, the stored run to read."

If ERG-01's fix is taken, the last of these reverts to its current wording and `--from-plan` carries
the id.

## ERG-10 — Must-Address — undefined flag combinations

**Evidence.** `contracts/cli-review-mode.md:23-25` defines `--detail` as "Expand the summary to
per-object records" and `--kind` as "Narrow the **per-object detail** to one destination kind". Two
combinations follow from that and neither is specified anywhere in the spec, plan, tasks, or
contracts:

1. `--from-plan --kind X` **without** `--detail`. Is `--kind` ignored? An error? Does it filter the
   summary? The error table (`:102`) says a `--kind` naming nothing is an error, which implies
   `--kind` is evaluated in summary mode too — but the summary render (`:50-56`) has no filtered form.
2. `--detail` or `--kind` on the **live** path, with no `--from-plan`. The contract handles the
   reverse direction only ("Every other `diff` option is meaningless in review mode and is ignored",
   `:30-31`) and is silent here. Silently ignored is the likely implementation, which is a flag that
   accepts input and does nothing.

**Why it matters.** Both are combinations an operator will type — the first because narrowing before
expanding is the natural order to think in, the second as a consequence of ERG-01's forgotten flag.
Unspecified means improvised, and improvised means the behavior will not match whatever T069 writes
in `running-a-sync.mdx`. Filtering the summary by kind is also a genuinely useful behavior that
nobody has decided against; it is just missing.

**Minimum fix.** State both in `contracts/cli-review-mode.md`. Suggested: `--kind` narrows both
depths (so `--from-plan --kind X` gives a one-kind summary), and `--detail` or `--kind` passed without
`--from-plan` is a usage error naming `--from-plan`, not a silent no-op — consistent with the
existing precedent at `infrahub_sync/cli.py:249-252`, where `--parallel` being ignored is at least
warned about.

---

## Recommended

**ERG-11 — `--from-plan` is a short-lived user-visible flag with a backwards name.** `spec.md:1601-1607`
records that a later outcome folds the review spelling into a command group and declines to say
anything about the fold. So `--from-plan` ships, appears in `cli.mdx`, appears in `running-a-sync.mdx`,
and is then deprecated — a deprecation cycle bought for one release. The name also reads against the
grain: on a command called `diff`, `--from-plan` parses as "diff *against* a plan" rather than "show
the stored plan", and the run-mode vocabulary the spec fixes elsewhere is `plan`/`sync`/`apply`
(`spec.md:1623`), so the operator learns "plan mode" and "the `diff` command" for the same thing.
*Suggested*: `--show-plan` or `--read-plan` states the action rather than the source, and survives the
fold better as `plan show`.

**ERG-12 — `verification_notes: list[str]` versus `VerificationFailure`.** `SavedPlan` exposes
`checksum_ok: bool` plus a list of prose strings (`contracts/plan-reader-api.md:36-37`); the verifier
models the identical information as a structured `VerificationFailure` with `check`, `expected`,
`found`, `next_action` (`:111-118`). An in-process caller wanting to branch on *why* a plan is suspect
must string-match — the thing FR-029 says they should never have to do. *Suggested*: reuse
`VerificationFailure` for `verification_notes`, or drop `checksum_ok` in favor of a single structured
list whose emptiness is the verdict.

**ERG-13 — `by_action: dict[str, int]` against a closed vocabulary.** FR-002 fixes the action set;
`contracts/plan-reader-api.md:43` hands the caller a plain `dict[str, int]` keyed on magic strings,
with no guarantee about which keys are present when a count is zero. *Suggested*: a `Literal` or enum
key type, and state whether zero-count actions appear.

**ERG-14 — is `verify_plan` supported or internal?** The contract is titled "the in-process plan
reader **and the pre-apply verifier**" and gives `verify_plan` a full public-looking signature
(`:79-85`), while its own opening paragraph says "only the names below are a supported surface" and
AD029 says "a single reader entry point is named and nothing else" (`:5-7`; `spec.md:329-333`). A
caller cannot tell whether importing `verify_plan` is sanctioned. *Suggested*: one sentence marking
it internal-to-`apply` (or supported), explicitly.

**ERG-15 — nothing tells adapter number ten that the contract changed.** This outcome removes
`apply_cached_row` from the engine's dispatch and adds `apply_planned_operation` + `PeerResolver`
(`contracts/destination-write-surface.md:26-30`, `:146-152`). The repo's adapter documentation —
`dev/guidelines/writing-an-adapter.md`, `dev/knowledge/adapter-anatomy.md`,
`dev/guides/adding-an-adapter.md` — is where an author looks; a grep of `tasks.md` for `dev/` returns
nothing, and the docs tasks (T068–T071) cover `docs/docs/` only. The status quo is equally silent
(neither `apply_cached_row` nor "write surface" appears anywhere under `dev/`), so this is not a
regression — but the change is the moment to fix it, and the write surface landing on one adapter of
nine (`plan.md:632`) is precisely the situation where the other eight need to know what optional means.
*Suggested*: one task adding the method to `dev/knowledge/adapter-anatomy.md` and a short "planned
writes (optional)" section to `dev/guidelines/writing-an-adapter.md`, stating that omitting it is
supported and produces the FR-023 error.

**ERG-16 — no machine-readable review output, and nothing says so.** Review output is explicitly not
a stability contract: "their wording, field order, and layout may change without that being a
breaking change" (`spec.md:1586-1593`). The most obvious use of a review-before-write feature is a CI
gate, and a CI gate needs to answer "does this plan contain deletes / more than N operations / kind
X". The only sanctioned route is Python against `read_saved_plan`, which is a fine answer — but
nothing in the docs plan (T068–T070) says the rendered text is unstable, so operators will `grep` it,
and the first layout tweak will break them silently. *Suggested*: a line in `running-a-sync.mdx`
stating the text is for humans and pointing at `read_saved_plan` for automation. A `--json` flag would
be better, and is a defensible scope call to decline.

**ERG-17 — the delete-docs sweep misses two pages.** T071 (`tasks.md:317`) names
`creating-a-sync-project.mdx`, `reference/schema-mapping.mdx`, `reference/incremental-extraction.mdx`
and `reference/cache-layout.mdx`. It omits `docs/docs/migrating-from-netbox-or-nautobot.mdx:92` —
"Add Infrahub-only data … without worrying that a sync run will delete it", which is the exact claim
that now produces deletes in every plan and (per ERG-02) a `failed` apply — and
`docs/docs/readme.mdx:74`, which describes the three flags as controlling "what each run is allowed
to change (creates, deletes, modifications)", now true of the write path but not of the plan.
*Suggested*: add both to T071's grep list.

## Nits

**ERG-18** — `PeerResolver.resolve` is keyword-only, `remember` positional, in the same class block
(`contracts/destination-write-surface.md:149-152`), while `apply_planned_operation` and
`read_saved_plan` are keyword-only. Make `remember` keyword-only for consistency.

**ERG-19** — `quickstart.md:186-191` uses bare `python` where the rest of the page uses
`uv run python`; and the corruption snippet writes the manifest without `ensure_ascii=False`, unlike
the canonical encoding at `contracts/plan-artifact-format.md:41`. Harmless here (the point is to break
the checksum) but it models the wrong recipe next to the right one.

**ERG-20** — the negative walkthrough (`quickstart.md:182-198`) corrupts the manifest and then deletes
`$RUN/plan` entirely, leaving the run directory permanently unusable, with no closing step telling the
reader to produce a fresh run. Add one line: re-run `diff` to get a clean run id.

---

## Verdict on the `--run-id` overload

It is not something an operator can hold in their head, and the reason is not that two meanings are
hard to remember — it is that the two meanings are **inverses whose discriminator is invisible**.
`diff --run-id X` writes to and overwrites X; `diff --run-id X --from-plan` reads X and refuses if it
is absent. Dropping one flag turns a read into a destructive write against the exact artifact the
operator was trying to read, and the live behavior is not incidental — `running-a-sync.mdx:40`
advertises "overwrite a previous run's plan in place" as the flag's purpose. A mode toggle is
acceptable when forgetting it does nothing; here forgetting it destroys the evidence.

The plan reaches for this shape without needing to. AD019 establishes on its own reading that the
brief forbids only a command *group*, leaving a flat sibling command in scope; AD005 declined it. Even
holding AD005, the run identifier can simply be the value of `--from-plan`, which eliminates the
overload, the missing-flag accident, and one of AD036's error cases at zero cost. I would not ship the
two-flag form. If it ships anyway, ERG-09's dual-meaning help string and a guard on overwriting an
existing `plan/` are not optional.

## Failure-message walk — where the operator is left stranded

Fully actionable today (five): checksum mismatch, config-version mismatch, snapshot
mismatch/absent/truncated, run-id mismatch, unsupported operation / missing write surface,
pre-existing-format plan. All either flow through `VerificationFailure.next_action` or state the
remedy inline.

**No next action (nine):** torn artifact; unrecognized format version (worst of the group — AD028
justifies a distinct message *on the grounds of distinct remedies* and then never states either);
unreadable path; unknown run id; `--kind` matching nothing; derivation failure on `diff` under FR-030;
peer zero-match; peer multi-match; unserializable payload value. Duplicate operation id is also silent
but is pathological by construction and acceptable.

Two of those additionally echo the operator's own input while withholding an enumeration already
computed or trivially available: `UnknownPlanKindError` has `summary().by_kind` in hand, and the
unknown-run-id error has the cache-root directory listing — and with `runs` out of scope
(`spec.md:1555`), `ls` is the operator's only fallback and nothing says so.

The structural cause is scope: AD036 attached the next-action obligation to *refusals*, so the
verifier got it and the reader and the derivation path did not. Fixing it is a column in one table.

## Quickstart steps that would not work as written

1. **`quickstart.md:116-126`** — `uv run python - <<'PY'` … `sys.argv[1] if len(sys.argv) > 1 else "."`.
   Verified: `argv == ['-']`, so it opens `./plan/manifest.json` relative to the repo root and raises
   `FileNotFoundError`. `$RUN` is never passed. Fix: `uv run python - "$RUN" <<'PY'`.
2. **`quickstart.md:60-64`** — `git stash && … --help > /tmp/help-before.txt && git stash pop`.
   Verified on this tree: with a clean (committed) worktree `git stash` prints "No local changes to
   save" and exits 0, so the "before" capture comes from the post-change binary and the `diff` on line
   62 compares a file to itself. SC-012 passes without observing the baseline. `git stash pop` then
   runs anyway and will pop an unrelated stash if the developer has one.
3. **`quickstart.md:168-175`** — steps 4 and 5 (`apply`, then `apply` again, expecting convergence)
   end in run state `failed` against any destination holding an object absent from the source, because
   the plan from step 1 contains deletes and SC-007 makes that a `failed` run. Presented as the happy
   path (ERG-02).

## Process

`wwbd` **was invoked** and the profile at `references/profile.md` was loaded and used as the review
lens — in particular its safety-rails-default-on / fast-paths-opt-in posture (ERG-01, ERG-02), its
insistence that a comment or message carries operational rationale or dies (ERG-04), and its
grounding rule, which is why every behavioral claim above was checked against the tree or executed
rather than asserted. One suspicion it caused me to drop: `SyncConfig` in the reader signature looked
like a wrong type name, but it is real (`infrahub_sync/__init__.py:86`) and `SyncInstance` subclasses
it, so the annotation is correct and there is no finding.
