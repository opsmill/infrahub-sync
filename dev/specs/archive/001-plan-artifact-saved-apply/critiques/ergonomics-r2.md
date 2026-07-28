# Ergonomics critique — round 2

**Feature**: `001-plan-artifact-saved-apply-infp-653` · **Head**: `844e98f` · **Lens**: ergonomics
(CLI operator, in-process API caller, adapter author) · **Persona**: reviewed through `wwbd`
(Benoit Kohler), used as a lens only — no new requirements taken from it.

Round 1 raised ten Must-Address findings (ERG-01 … ERG-10). ERG-02 went to the human and was
ratified; the other nine went to remediation. This round verifies each claim against the artifacts and
the tree rather than accepting it, walks the quickstart and the failure catalogue again, judges the new
delete-bearing apply from the operator's seat, and hunts for regressions the `--from-plan` change could
have left behind.

Nothing is built yet. Every claim about present behavior below was checked by reading the file or
running the command; anchors are `file:line`.

## Round-1 disposition

| ID | Round-1 summary | Verdict |
|---|---|---|
| ERG-01 | `--run-id` carries two inverse meanings behind an omissible flag | **CLOSED** |
| ERG-02 | Deletes recorded by default make an ordinary apply end `failed` | **CLOSED** (human-ratified fix, propagated) |
| ERG-03 | The review summary prints `delete N` without saying what it means | **CLOSED** |
| ERG-04 | Nine failures leave the operator without a next action | **PARTIALLY CLOSED** → ERG-21 |
| ERG-05 | `operations(kind=…)` raises on an empty result | **CLOSED** |
| ERG-06 | `verify_plan(bool)` cannot produce the message it promises | **CLOSED** |
| ERG-07 | Three quickstart steps do not work as written | **PARTIALLY CLOSED** → ERG-22 |
| ERG-08 | The write-surface contract calls methods `PeerResolver` lacks | **CLOSED** |
| ERG-09 | Help text unspecified; `--run-id`'s string becomes wrong | **CLOSED** |
| ERG-10 | `--kind` without `--detail`, and the new flags on the live path | **PARTIALLY CLOSED** → ERG-23 |

## New findings

| ID | Severity | Summary | Anchor |
|---|---|---|---|
| ERG-21 | Must-Address | Two of FR-030's four derivation-failure classes have no named exception, so AD059's next-action obligation does not reach them; the task that tests them requires only "kind and cause" | `contracts/plan-reader-api.md:94-97`, `:79-91`; `tasks.md:273`; `tasks.md:167` (T003) |
| ERG-22 | Must-Address | The negative walkthrough's last two commands cannot demonstrate what they claim: the `--kind` case runs against a run whose `plan/` was deleted two commands earlier, and names a kind the example configuration does not declare | `quickstart.md:230`, `:236-238`; `examples/netbox_to_infrahub/config.yml:43` |
| ERG-23 | Recommended | `--from-plan X --kind Y` without `--detail` is stated only in a help string; the errors table has no row for it and no task asserts it | `contracts/cli-review-mode.md:65` vs `:186`; `tasks.md:371` |
| ERG-24 | Must-Address | The unknown-run-id enumeration has no specified behavior when no runs exist, and no bound — and nothing in the repository ever prunes a run directory | `contracts/cli-review-mode.md:179`; `contracts/plan-reader-api.md:86`; `infrahub_sync/cache/paths.py:26-42` |
| ERG-25 | Recommended | On a delete-bearing apply the operator's last line is `Applied run <id>`, which names no skip; and the warning's level is never pinned | `infrahub_sync/cli.py:352`; `contracts/destination-write-surface.md` (Warning row); `tasks.md:324` |
| ERG-26 | Recommended | SC-012's committed baseline is compared with a whole-file `diff` against width-dependent Rich output, with the capture environment unpinned | `quickstart.md:63-64`; `tasks.md:147`, `:373` |
| ERG-27 | Recommended | `--run-id` passed alongside `--from-plan` is silently ignored — the residue of the ambiguity AD057 removed | `contracts/cli-review-mode.md:31`, `:34-36` |
| ERG-28 | Recommended | The reader does not say what `operations(kind=…)` does when `config` is `None` | `contracts/plan-reader-api.md:25-27`, `:54-55` |

Carried forward unchanged from round 1, not re-argued: **ERG-12** (`verification_notes: list[str]` versus
the structured `VerificationFailure`), **ERG-13** (`by_action: dict[str, int]` against a closed
vocabulary), **ERG-14** (is `verify_plan` a supported surface or internal — still unstated),
**ERG-15** (no task tells adapter number ten the contract changed; `grep dev/` over `tasks.md` and
`plan.md` still returns nothing), **ERG-16** (no machine-readable review output and nothing telling
operators the text is unstable). All four were Recommended in round 1 and were not routed by the
collation; none regressed.

**Counts: 3 Must-Address · 0 RETHINK · 6 Recommended · 4 Nit.** Blocking residue: **yes — ERG-21,
ERG-22, ERG-24.** All three are small: two error classes, a two-line quickstart reordering, and two
sentences in one table row.

---

## Verification of the round-1 claims

### ERG-01 — CLOSED

`--from-plan <run-id>` is the surface everywhere it appears: `contracts/cli-review-mode.md:23`, `:28`,
`plan.md:579`, `tasks.md:351-352`, `quickstart.md:180`. `--run-id` is explicitly "**Unchanged and unused
by review mode**" (`cli-review-mode.md:31`). The retired error case is not merely dropped but named as
retired and forbidden to test (`cli-review-mode.md:189-191`, `tasks.md:371` — "must NOT be asserted").

The one place the old spelling survives is the AD005 decision record (`spec.md:89`), immediately
superseded in the same bullet (`:92`) — that is a decision record keeping its own history, which is
right, not a stale statement.

The framework claim at `cli-review-mode.md:190` ("the command framework rejects a valueless option
before any of this code runs") holds for the trailing case and is looser than stated mid-command.
Verified: `diff --name from-netbox --run-id` → "Option '--run-id' requires an argument"; but
`diff --name from-netbox --run-id --directory examples/` assigns `--directory` as the *value* and fails
with "Got unexpected extra argument (examples/)". Both are hard, non-destructive failures before our
code, so the claim's conclusion stands and only its precision does not. Nit.

### ERG-02 — CLOSED

The ratified shape is carried consistently, which is what I checked rather than the choice itself:
`spec.md:1456-1487` (FR-016 designed-limitation framing, FR-017's two classes), `spec.md:1786-1799`
(SC-007, with "A run state of `failed` **fails this criterion**"), `spec.md:930-957` (User Story 4),
`data-model.md:290-300` (run-state table, no new member),
`contracts/destination-write-surface.md` (the `delete` section and its five-row obligation table),
`contracts/cli-review-mode.md:223`, `tasks.md:324` (T054), `:357` (T059), `:374` (T065), `:436` (T078),
`quickstart.md:188-209`. The knowability invariant — `applied ∪ skipped` equals the plan's identifier
set — is asserted at T054 and T078, and `deletes_not_executed` reaches the review surface through
`PlanSummary` (`plan-reader-api.md:47`).

Two things worth recording as consequences the remediation got right without being asked: the phantom
delete cannot inflate the count (T037 asserts `skipped_delete_count == 0` on an incremental run), and
round 1's ERG-17 largely evaporates — `docs/docs/migrating-from-netbox-or-nautobot.mdx:92` ("without
worrying that a sync run will delete it") is *still true* under AD055, because the write path still
cannot delete and the apply no longer fails.

My operator-seat judgment on the resulting apply is in its own section below.

### ERG-03 — CLOSED

`contracts/cli-review-mode.md:103-113` makes both the delete-computation record and the delete
annotation obligations rather than layout, in a three-row condition table covering computed-with-zero,
computed-with-some, and not-computed. The rendered samples carry the text at both depths (`:98-100`,
`:124-126`, `:152`, `:156` — the per-record `(not executed)` marker). `FR-006` now states it normatively
(`spec.md:1212-1220`), `PlanSummary` carries the two fields as required (`plan-reader-api.md:46-47`,
`tasks.md:184`), and T061/T087 assert it including two cases against an incrementally-loaded plan so the
not-computed wording is proven reachable rather than assumed. The summary NOTE also tells the operator
what the apply will do — "Applying this plan will complete successfully and record 4 skipped deletes on
the run" — which is the sentence that makes the count safe to read.

### ERG-04 — PARTIALLY CLOSED

The structural half is done properly. `PlanArtifactError` declares `next_action: str` as a required,
non-empty base-class attribute (`plan-reader-api.md:74-75`, `tasks.md:167`), T003's done-when is
"constructing any of them without a `next_action` raises", and T089 walks
`PlanArtifactError.__subclasses__()` transitively so a later addition trips rather than shipping a dead
end. The taxonomy table gained a next-action column with real remedies, and the four enumerations are
real constants the code has in hand at the raising site: `SUPPORTED_FORMAT_VERSIONS = frozenset({2})`
and `ACTIONS = ("create","update","delete")` are defined at T009 (`data-model.md:20-21`), the plan's
kinds come from `summary().by_kind`, and the run identifiers come from a listing of
`cache_root_for(sync_name)`.

Walking the nine round-1 orphans one by one, against the standard "actionable, not a restatement":

| Round-1 orphan | Next action now | Genuinely actionable? |
|---|---|---|
| Torn artifact | "re-run `diff` to rebuild the artifact; the partial one cannot be repaired" | yes |
| Unrecognized format version | "lists `SUPPORTED_FORMAT_VERSIONS`; … re-plan with this one or apply with the one that wrote it" | yes, both branches; see Nit below |
| Unreadable path | "check permissions and ownership on the named path, then retry" | yes |
| Unknown run id | "lists the run identifiers that exist for that sync" | yes — but see **ERG-24** |
| `--kind` matching nothing | "lists the destination kinds the plan holds" | yes |
| Derivation failure on `diff` | partly — see **ERG-21** | **no, for two of four classes** |
| Peer zero-match | "create the peer at the destination, or re-plan so the peer is created by the same plan" | yes |
| Peer multi-match | "de-duplicate at the destination or narrow the mapping's identifiers" | yes |
| Unserializable payload | "narrow the field's mapping or add the type to the canonical-value table" | yes |

Eight of nine are closed and the remedies are routes, not restatements. The ninth — the FR-030
derivation path, which is the one that newly hard-fails a command that has always succeeded — is
ERG-21.

### ERG-05 — CLOSED

`plan-reader-api.md:54-55` splits the two conditions exactly as round 1 asked: a declared kind with no
operations returns `[]`, an undeclared kind raises. The never-empty rule is relocated to the renderer
and *stated* there (`cli-review-mode.md:164-168`), FR-006 carries the split normatively
(`spec.md:1206-1211`), and both branches get their own error row (`cli-review-mode.md:184-185`) and
their own test case (T062). The rationale is recorded at the point of change rather than left implicit,
which is what stops a later reader "simplifying" it back.

### ERG-06 — CLOSED

`write_surface_missing_on: str | None = None` (`plan-reader-api.md:107`), with the reasoning stated at
`:115-121` and the check row updated at `:135`. T021 implements it, T025 asserts the returned failure's
message contains the supplied adapter name and notes that "a `bool` argument could not have produced
it" — which is the assertion that keeps the fix from silently regressing. `tasks.md:220` also updates
the apply gate to pass the adapter's name.

### ERG-07 — PARTIALLY CLOSED

Both round-1 defects are fixed, and I re-verified the first empirically.

**(a) The checksum recompute now works.** `quickstart.md:129` is `uv run python - "$RUN" <<'PY'` and
`:131` is `run = pathlib.Path(sys.argv[1])` with the default removed. Executed against a synthetic run
directory, the block resolved the path passed to it and printed both hashes. The page also explains why
the argument is required (`:141-142`), so the shape survives editing.

**(b) The SC-012 baseline is a committed fixture.** `quickstart.md:63-64`, T002 (`tasks.md:147`) and
T064 (`tasks.md:373`) all use `tests/data/cli_help_baseline.txt`; T064 must "**fail** rather than
regenerating it" when absent, which closes the silent-self-comparison hole. The `git stash` pair is
gone and the reason is recorded in three places. `tests/data/` does not exist in the tree today, which
matches T002's own statement. Residual fragility in how the comparison is performed is ERG-26.

**(c) The manual walkthrough's steps 4 and 5** now expect exit 0 and `applied` (`quickstart.md:188-209`),
consistent with AD055. Closed.

**But the same file gained a new broken step** in the negative walkthrough — ERG-22.

### ERG-08 — CLOSED

`contracts/destination-write-surface.md:77-81` calls only `peers.resolve(peer_kind=…, identity=…,
referring_operation_id=…)`, branches on `ref.cardinality` in the caller, and is now evaluable as
written. The note at `:96-101` states which side owns the many case and why — the exact design question
round 1 said the sketch was hiding. `remember` is still positional while `resolve` is keyword-only
(`:207-209`); that was and remains a Nit.

### ERG-09 — CLOSED

All four strings are fixed in the contract (`cli-review-mode.md:61-66`), with the reason the table
exists at all stated at `:56-59`, and T090 asserts each one against `diff --help` including the
corrected `--run-id` cross-reference. T070's done-when requires the regenerated `cli.mdx` to show
`--from-plan` as taking a run identifier rather than as a flag.

One thing round 1 asked for is absent and I no longer think it should be there: "an unknown run id is an
error" is dropped from `--from-plan`'s help. Under AD057 a typo'd `--from-plan` value cannot destroy
anything, so the property no longer needs to be discoverable at the prompt. Correct call.

Nit: the help table is not scoped to a command, and `--run-id` exists on **two** commands —
`diff` (`infrahub_sync/cli.py:98`) and `apply` (`:301`, help "Cache run id produced by a previous
`diff`."). The corrected string says "for the live comparison", which is wrong on `apply`. One clause
scoping the row to `diff`'s option closes it.

### ERG-10 — PARTIALLY CLOSED

The live-path half is closed: `--detail` or `--kind` without `--from-plan` is now an explicit error row
(`cli-review-mode.md:186`), asserted at T062, and each option's help text names its prerequisite —
which is better than round 1's suggestion, because the operator learns it before running anything.

The other half is only half-specified: see ERG-23.

---

## Must-Address

### ERG-21 — two FR-030 derivation failures have no exception, so AD059 does not reach them

**Evidence.** `cli-review-mode.md:196-198` enumerates the four derivation failures that newly fail
`diff`: "an operation with no formable destination identity, a relationship peer absent from the loaded
source store, an unencodable payload value, a duplicate operation identifier". The reader contract then
says a derivation failure "raises whichever of the above applies — most often `UnknownPlanKindError`'s
peer-probe siblings, `UnserializablePayloadValueError`, or `DuplicateOperationIdError`"
(`plan-reader-api.md:94-97`).

Two of the four have nothing above that applies:

- **No formable destination identity** — there is no exception for it. T003's list is exhaustive and
  closed (`tasks.md:167`): eleven classes, none of them this one. `grep -rn "formable\|cannot be
  formed\|IdentityError"` over the spec directory returns only prose descriptions
  (`spec.md:1027`, `plan.md:406`, `cli-review-mode.md:196`), never a class.
- **A relationship peer absent from the loaded source store** — the nearest class is
  `PeerNotFoundError`, but the contract defines that as "A peer identity matches no **destination**
  object (FR-014, SC-016)" with the next action "create the peer at the destination, or re-plan so the
  peer is created by the same plan" (`plan-reader-api.md:90`). Reusing it for a plan-time source-store
  miss would attach a remedy that is wrong for the condition: nothing is missing at the destination and
  creating something there fixes nothing.

`data-model.md:270` then counts "derivation failure" as **one** of the five failures that "have no
enumeration in hand and carry a next action only" — so the whole four-class family is treated as a
single row that no class implements. And the task that tests them asks for less than AD059 promises:
T083 (`tasks.md:273`) requires "an error naming the destination kind and the cause" — the next action is
not in its assertion list, and T089's parametrized sweep walks *taxonomy entries*, so a condition with
no taxonomy entry is not swept.

**Why it matters to the consumer.** This is the highest-traffic new failure in the feature. `diff` is
the command an operator runs constantly, it has never failed on data before, and AD047 deliberately
gives it no tolerance switch (`--continue-on-error` is `sync`-only, verified at
`infrahub_sync/cli.py:190`). An operator whose nightly `diff` starts exiting non-zero gets a kind and a
cause and, for these two classes, nothing to do about it — which is precisely the shape AD059 exists to
eliminate, in the one place AD059's own instrumentation does not look. It also leaves the implementer
free to raise a bare `ValueError`, which then bypasses the `next_action` base-class guard entirely and
T089 will not catch it.

**Minimum fix.** Add two classes to T003 and to the taxonomy table with their next actions, and add the
next action to T083's assertion list:

- `UnformableDestinationIdentityError` — names the kind and the identity attributes that resolved to
  nothing; next action: add the missing attribute to that kind's `identifiers` in the schema mapping, or
  drop the kind from the mapping.
- `SourcePeerUnresolvedError` — names the referring operation, the peer kind and the peer identity; next
  action: add the peer's kind to the configuration so it is loaded, or remove the relationship from the
  mapping. Keep it textually distinct from `PeerNotFoundError`, whose remedy is a destination one.

### ERG-22 — the negative walkthrough's last case cannot produce the error it demonstrates

**Evidence.** The negative walkthrough (`quickstart.md:214-239`) runs in this order against one run
directory, `RUN=.infrahub-sync-cache/from-netbox/"$RUN_ID"`:

```text
:220-227   corrupt the manifest checksum, apply, assert `failed`
:230       rm -rf "$RUN/plan"                      # to demonstrate the v1 re-plan message
:231       apply                                    # → PlanFormatV1Error, as intended
:234       diff --from-plan not-a-run-id            # → unknown run id, fine (different id)
:237-238   diff --from-plan "$RUN_ID" --detail --kind CoreStandardGroup
```

The last command reads `$RUN_ID`, whose `plan/` directory was deleted at `:230`. Under
`plan-reader-api.md:81` an absent `plan/` is classified **before** anything else, so this produces
`PlanFormatV1Error` and its re-plan message. It cannot produce the unknown-kind error the comment
promises ("names the kind AND lists the kinds it does hold (AD058, AD059)").

Second defect in the same command: `CoreStandardGroup` is **not declared** by the example
configuration. `examples/netbox_to_infrahub/config.yml:43` is its only occurrence and it is a
commented-out `order:` entry; `grep -n "^  - name: CoreStandardGroup"` returns nothing. So even after
the run directory is repaired, the command exercises the **reader's** undeclared-kind branch
(`UnknownPlanKindError`), not the **renderer's** declared-but-empty branch — the two paths AD058 exists
to separate, one of which this is the only hand-run demonstration of.

**Why it matters to the consumer.** The negative walkthrough is the procedure a human follows to
convince themselves the error surfaces work. Read literally it errors both times, so it *looks* like it
passed — the same failure mode as round 1's `git stash` step, where a check succeeded without observing
what it claimed to observe. AD060 was created to stop exactly that, and this is a new instance of it in
the file AD060 repaired. Round 1's ERG-20 (the walkthrough leaves the run directory unusable with no
recovery step) was filed as a Nit; the remediation added two steps downstream of the deletion, which
promotes it to the cause of a broken demonstration.

**Minimum fix.** Two lines. Move the `--kind` case **above** the `rm -rf "$RUN/plan"` line, or re-plan
between them (`uv run infrahub-sync diff --name from-netbox --directory examples/` and re-read the new
run id). And name a kind the configuration declares but the plan holds no operation for — pick one from
the config's own list at run time, or add both cases explicitly and label which branch each takes:
`--kind CoreStandardGroup` as the undeclared-kind (reader) case, and a declared kind as the empty-detail
(renderer) case.

### ERG-24 — the unknown-run-id enumeration is unspecified when empty and unbounded when not

**Evidence.** Two artifacts promise the enumeration and neither bounds or guards it:

- `cli-review-mode.md:179` — "the run identifiers that do exist for that sync … The cache root is
  already resolved at this point, so the enumeration costs one directory listing".
- `plan-reader-api.md:86` — "**lists the run identifiers that exist for that sync** — the command has
  already located the cache root, so the enumeration is in hand".

Neither states what happens when the cache root does not exist or holds no runs. `cache_root_for`
(`infrahub_sync/cache/paths.py:26-42`) *computes* a path and never creates or checks it — for a sync
that has never run, `Path.cwd()/".infrahub-sync-cache"/<name>` simply is not there, and the "one
directory listing" the contracts describe as safe raises `FileNotFoundError`. No task covers the case:
T062 asserts the message lists "the run identifiers that do exist", which is vacuous when there are
none, and T089 asserts the enumeration is present, not that it degrades.

Nor is there a bound. `grep -rn "prune\|retention\|rmtree" infrahub_sync/` returns one unrelated
tmp-file cleanup (`cache/sidecars.py:22`), and `docs/docs/reference/cache-layout.mdx` says nothing about
pruning: **run directories accumulate one per `diff` or `sync` invocation, forever**. An hourly pipeline
holds thousands. Round 1's minimum fix said "bounded — most recent N"; the bound did not survive into
the remediation.

**Why it matters to the consumer.** Both ends of the range are the wrong experience, and the empty end
is the first-run experience. The person most likely to mistype a run id, or to try `--from-plan` before
ever having produced a plan, is the person meeting this feature for the first time — and what the
artifacts specify for them is either a traceback or an error that names their input, lists nothing, and
offers a next action about picking from a list that is empty. The full end is the opposite failure: the
most common typo in the feature dumps four thousand lines, and with `runs` explicitly out of scope
(`spec.md:1555`) the operator has no command to narrow it with.

**Minimum fix.** Two sentences in that table row, mirrored in `plan-reader-api.md:86`: the enumeration
lists the **most recent N** run identifiers (they sort by time by construction —
`generate_run_id()` is `YYYYMMDDTHHMM-<hex>`, `paths.py:44-52`) with the total count when truncated; and
when the cache root is absent or holds no runs, the message says so plainly and the next action is to
run `diff` for that sync first. Add the no-runs case to T062.

---

## Recommended

**ERG-23 — `--from-plan X --kind Y` without `--detail` is stated only in a help string.** The
remediation chose to make `--kind` require `--detail` (`cli-review-mode.md:65`, "Requires --from-plan
and --detail") rather than round 1's suggestion of narrowing both depths. That is a legitimate choice
and the help text makes it discoverable. But the errors table has exactly one row for the family —
"`--detail` or `--kind` without `--from-plan`" (`:186`) — and no row for `--kind` without `--detail`;
`--kind`'s own meaning row still reads "Narrow the per-object detail to one destination kind" (`:30`),
which describes rather than constrains; and T062 (`tasks.md:371`) asserts only the
without-`--from-plan` case. So the one combination round 1 flagged as unspecified is still not in the
normative behavior table and is not tested, while the shipped help string asserts a rule nothing
enforces — a flag whose documented prerequisite may be silently ignored, which is the exact defect
class ERG-10 named. *Fix*: one row in the errors table and one clause in T062.

**ERG-25 — the last line of a delete-bearing apply names no skip, and the warning's level is never
pinned.** Two small gaps in an otherwise well-instrumented disclosure. (a) The warning is required "on
the run's log stream" at "operator-visible level" (`destination-write-surface.md`, Warning row;
`tasks.md:324`) — but never pinned to `WARNING`. That matters because `--quiet` maps to
`logging.WARNING` (`infrahub_sync/cli.py:29`), so an `INFO`-level message vanishes for exactly the
scripted and CI invocations where the run record is the only other signal. (b) The apply's terminal
output is `logger.info("Applied run %s", ptd.run_id)` (`infrahub_sync/cli.py:352`), emitted *after* the
engine's warning and after `run_file.save()`. So on a long apply the operator's final line says
"Applied run 20260726T1804-9f3ac210" and nothing else, with the skip disclosure some distance upstream.
*Fix*: pin the level to `logging.WARNING` in T054's assertion (`record.levelno >= logging.WARNING`), and
have T059's completion line name the counts when the skip count is non-zero — "Applied run <id>: 33
operations applied, 4 deletes skipped". One f-string.

**ERG-26 — the SC-012 comparison is a whole-file `diff` against width-dependent output.** The
quickstart's step is `diff tests/data/cli_help_baseline.txt /tmp/help-after.txt` with the comment
"expected: no difference at the command list" (`quickstart.md:63-64`), and T064 says "compare as text
against the committed T002 baseline fixture" (`tasks.md:373`). `--help` is Rich-rendered and its layout
depends on the terminal width. Verified on this tree: `COLUMNS=80` yields 34 lines, `COLUMNS=120` 23,
`COLUMNS=240` 21 — `diff` between the 80- and 240-column captures is 66 lines of pure formatting noise.
Neither T002 nor the quickstart pins the capture environment. The redirected default is stable at 80
(verified with `env -u COLUMNS`, which reproduced the 80-column form exactly), so the step works as
written today and this is latent rather than broken — the exposure is a shell that exports `COLUMNS`, a
CI runner that sets it, or a Rich/Typer version bump over the fixture's life. Note the failure direction
is a false *failure*, which is much better than round 1's false pass. *Fix*: capture and compare under a
pinned environment (`COLUMNS=80 TERM=dumb NO_COLOR=1 uv run infrahub-sync --help`) in T002, T064 and the
quickstart, or make T064 compare the extracted command names rather than the rendered text — which is
what its own done-when actually describes.

**ERG-27 — `--run-id` alongside `--from-plan` is silently ignored.** `cli-review-mode.md:31` keeps
`--run-id` "unchanged and unused by review mode", and `:34-36` says passing any other `diff` option in
review mode "is not an error, because the mode is a read and rejecting unrelated flags would be
gratuitous". For most options that is right. `--run-id` is not most options: it is the only other option
that names a run, so `diff --from-plan A --run-id B` is the one invocation where an operator genuinely
cannot tell which run they just reviewed, and it resolves silently to A. Nothing is destroyed — the mode
branches above `get_potenda_from_instance` so no directory is created — so this is comprehension, not
safety. But it is the residue of the very ambiguity AD057 was created to remove, and the remediation
already accepted the stricter posture next door by making `--detail` without `--from-plan` an error
rather than a no-op. The repository's own precedent is at least to warn (`infrahub_sync/cli.py:249-252`,
where an ignored `--parallel` is warned about). *Fix*: one row — `--run-id` with `--from-plan` is a usage
error naming which option selects the run, or at minimum a warning.

**ERG-28 — the reader does not say what `kind=` does when `config` is `None`.** `config` is optional and
"used for one thing only: validating that a `kind` filter names a kind the configuration declares"
(`plan-reader-api.md:25-27`). The two `kind` rows at `:54-55` both presuppose a config: one is about a
kind "the configuration **declares**", the other about one it "does **not** declare". With `config=None`
neither predicate is evaluable, and the contract never says which way it falls — presumably "return
`[]`, never raise", but an in-process caller cannot know that, and the CLI (which always has a config)
will never exercise it, so it will be settled by accident. FR-029's whole point is that this surface is
consumable without reading the implementation. *Fix*: one sentence — with `config=None` no kind
validation occurs and `operations(kind=…)` returns `[]` for any kind with no operations.

## Nits

**ERG-N1** — `PlanFormatVersionError`'s next action offers "re-plan with this one or apply with the one
that wrote it" (`plan-reader-api.md:83`). Both branches are present, which is what AD028 promised, but
for a version *above* the supported set the real remedy is "upgrade `infrahub-sync`", which no wording
states. Since the message already prints found and supported, the operator can work out which side they
are on; naming the upgrade would save the inference.

**ERG-N2** — `cli-review-mode.md:190` claims the command framework rejects a valueless `--from-plan`
"before any of this code runs". True for a trailing option; mid-command, Click consumes the next option
as the value and reports "Got unexpected extra argument (…)" instead (both verified above). The
conclusion holds — nothing destructive is reachable — only the parenthetical is over-claimed.

**ERG-N3** — the help table (`cli-review-mode.md:61-66`) is not scoped to `diff`, and `--run-id` also
exists on `apply` (`infrahub_sync/cli.py:301`) where "for the live comparison" would be wrong. One
clause. Carried in the ERG-09 block above.

**ERG-N4** — round 1's ERG-18 and ERG-19 survive unchanged: `PeerResolver.remember` is positional while
`resolve` is keyword-only (`destination-write-surface.md:207-209`), and the quickstart still mixes bare
`python` with `uv run python` (`quickstart.md:119-120`, `:194`, `:227`) — `python` resolves to a pyenv
shim on this machine and may be absent or the wrong interpreter elsewhere. `quickstart.md:194` also
hardcodes `.infrahub-sync-cache/`, which `INFRAHUB_SYNC_CACHE_DIR` overrides
(`infrahub_sync/cache/paths.py:34-41`).

---

## The delete-bearing apply, from the operator's seat

**Verdict: the disclosure is sound, and exit 0 is the right call. Two small gaps, both in ERG-25.**

The shape that erodes trust is a run reporting success while quietly not doing part of what it listed.
This one does not do that, and the reason is that the disclosure is layered across every surface an
operator touches:

1. **Before the apply.** The review surface cannot render a delete-bearing plan without a NOTE naming
   the count and saying plainly that none will be executed and that the apply will complete and record
   the skips (`cli-review-mode.md:98-100`), plus a per-record `(not executed)` marker under `--detail`
   (`:156`). This is the load-bearing one: the operator is told the outcome *while approving it*, which
   is where a review-before-write feature should tell them.
2. **During the apply.** One warning naming the count, explicitly "not a debug line and not a
   per-operation trace" (`destination-write-surface.md`, Warning row). It reaches the terminal:
   `_setup_logging` attaches a `StreamHandler` (`infrahub_sync/cli.py:41-47`, stderr by default) and
   `--quiet` floors at `logging.WARNING` (`:29`), so a `WARNING` survives every verbosity the CLI
   offers.
3. **After the apply.** `summary["skipped_delete_count"]` and `summary["skipped_delete_operations"]` in
   `run.json`, with `applied ∪ skipped` required to equal the plan's whole identifier set (T054, T078).
   This is the part that makes it machine-checkable: a CI gate that cares reads one integer, and the
   quickstart shows exactly that read (`quickstart.md:194-198`). It also answers round 1's ERG-16 for
   this specific fact, which the review surface does not.
4. **In the documentation.** T069 and T071 both carry the plain statement.

**On the exit code specifically.** Exit 0 is correct, and I would defend it against the instinct to
signal in-band. A non-zero exit is the CLI's word for "what you asked for did not happen". Here what the
operator asked for *did* happen — they approved a plan whose review screen told them, in the output they
approved, that the deletes would not be executed. Exiting non-zero on a run that behaved exactly as
reviewed and as documented trains operators to ignore the exit code of the command that matters most,
and under the engine's fallback flag set (`potenda/__init__.py:92-93`, `SKIP_UNMATCHED_DST`) it would be
the *ordinary* case on any non-pristine destination — a permanently red signal, which is the failure
round 1's ERG-02 was about. The obligation is explicit and tested rather than incidental:
`cli-review-mode.md:223` ("does **not** exit non-zero"), T059 ("the command must not translate a skipped
delete into a non-zero exit"), and T065's positive CLI case asserting exit 0 "because a CLI-level
non-zero exit would be invisible to T054's in-process assertions" — that last is a good catch by the
remediation, not something I asked for.

**What is not loud enough.** The warning's level is never pinned, and `--quiet` is precisely where an
`INFO`-level version would disappear; and the operator's *last* line on the CLI path is
`Applied run <id>` (`infrahub_sync/cli.py:352`), which names no skip. On a long apply the one disclosure
in the terminal sits upstream of a wall of progress lines, and the sentence the operator reads as the
verdict says only "Applied". Both are one-line fixes and both are in ERG-25; neither is blocking,
because the pre-apply NOTE means an operator who reviewed has already been told, and the run record
means an operator who scripted can already check.

## Quickstart walk

Walked literally, command by command, against the real CLI where a command exists today.

| Step | Status |
|---|---|
| Prerequisites, `uv sync` (`:24-27`) | fine |
| The gate (`:45-50`) | fine; not executed (`invoke format` mutates the tree and this review is read-only) |
| CLI sanity (`:54-58`) | `--help` and `list --directory examples/` run clean; `generate` not executed (it writes files) |
| **SC-012 (`:60-75`)** | **round-1 defect fixed** — committed fixture, no `git stash`; `tests/data/` correctly absent today. Latent width fragility: ERG-26 |
| Unit and CLI suites (`:79-86`) | paths are the ones the tasks create; `tests/cache` and `tests/adapters` exist today, `tests/plan` and `tests/data` do not, as T001/T002 state |
| Criteria table (`:93-111`) | consistent with `tasks.md`; SC-007's row now reads `applied` on both halves |
| Inspect by hand (`:117-122`) | works; bare `python` (ERG-N4) |
| **Checksum recompute (`:124-142`)** | **round-1 defect fixed and re-verified empirically** — argument passed, path resolved, both hashes printed |
| Track 2 (`:146-167`) | consistent; SC-007's live half asserts `applied` |
| **Manual walkthrough (`:171-212`)** | **round-1 defect fixed** — step 4 expects exit 0 and `applied`, step 5 reads the skip count, and `:204-209` explains why. Steps 1–3 and 6 read correctly against the planned surface |
| **Negative walkthrough (`:216-239`)** | **NEW DEFECT — ERG-22.** The final `--kind` case runs against a run whose `plan/` was deleted at `:230`, and names a kind the example config does not declare. The other three cases read correctly |
| Documentation check (`:246-253`) | fine, and consistent with T068–T071 |

Three round-1 steps fixed, two of them re-verified by execution. One new broken step, in the section
round 1 flagged as leaving the run directory unusable.

## Failure-catalogue walk

All nine round-1 orphans now carry a next action, and the eight I could trace to a raising site carry a
route rather than a restatement — the table in the ERG-04 block above walks them individually. The
obligation is structural (`next_action` required on `PlanArtifactError`, T003's done-when, T089's
transitive subclass sweep) rather than a convention a later contributor can quietly drop, which is the
part that will still be true in a year.

The four promised enumerations are ones the code can actually produce at the point it raises:
`SUPPORTED_FORMAT_VERSIONS` and `ACTIONS` are module constants defined at T009 (`data-model.md:20-21`);
the plan's kinds come from `PlanSummary.by_kind`, already computed; the run identifiers come from a
listing of `cache_root_for(sync_name)`, which the command has resolved by then. T089 asserts each of the
four explicitly and requires the assertion to fail if the message degrades to echoing the operator's
input.

Two residues: the FR-030 derivation family, where two of four classes have no exception and therefore
no next action (**ERG-21**); and the run-identifier enumeration, which is unguarded when empty and
unbounded when not (**ERG-24**).

## Regression hunt on the `--from-plan` change

Checked every surface the change touches. The spelling agrees across `spec.md:92`,
`plan.md:42`/`:579`/`:585`/`:646`, `contracts/cli-review-mode.md:23`/`:28`,
`tasks.md:96`/`:351`/`:370`/`:371`/`:381`/`:398`/`:401`/`:617`, and `quickstart.md:180`/`:185`/`:234`/`:237`.
The retired error case is retired consistently and T062 is told not to assert it. `spec.md:89` keeps the
superseded spelling inside the AD005 decision record and corrects it in the same bullet — history, not
drift. The prior-round checklist reviews under `checklists/reviews/` still describe the two-flag form;
those are dated review artifacts, not normative, and I do not read them as stale statements.

The documentation obligation is coherent. `cli-review-mode.md:253-258` names `cli.mdx`,
`running-a-sync.mdx` and `cache-layout.mdx`; T068, T069, T070 and T071 cover exactly those plus the
delete sweep, and T070's done-when pins what the regenerated `cli.mdx` must show (`--from-plan` taking
a run identifier, `--run-id`'s corrected string, diff confined to that command). The existing
`docs/docs/running-a-sync.mdx:40` description of `--run-id` — "useful when you want to overwrite a
previous run's plan in place" — stays true under AD057, which is one of the things folding the id into
`--from-plan` bought. Round 1's ERG-17 is largely mooted: T071's grep plus its "no docs page still
asserts that plans omit deletes" done-when will reach `readme.mdx:74`, and
`migrating-from-netbox-or-nautobot.mdx:92` no longer says anything false, because the write path still
cannot delete and the apply no longer fails.

Two coherence gaps rather than regressions: the help table is unscoped while `--run-id` exists on two
commands (ERG-N3), and `--run-id` alongside `--from-plan` is silently ignored (ERG-27).

## Process

`wwbd` **was invoked** and `references/profile.md` was loaded and used as the review lens — in
particular its safety-rails-default-on posture (ERG-24, ERG-27), its "a comment or message carries
operational rationale or dies" rule (ERG-21), its determinism preference (ERG-26), and above all its
grounding rule, which is why the heredoc, the help-width behavior, the Click option-parsing behavior,
the `CoreStandardGroup` declaration, the logging levels and the absence of run pruning were all
executed or read rather than asserted. The persona also argued me *down* on two suspicions: that exit 0
on a delete-bearing apply is too quiet (his own conservative-rollout instinct cuts the other way — a
designed limitation reported and recorded is not a fault, and a permanently red exit code is the worse
outcome), and that the width-sensitive `--help` comparison is broken as written (it is not; the
redirected default is stable at 80, so it is latent and therefore Recommended, not Must-Address).
