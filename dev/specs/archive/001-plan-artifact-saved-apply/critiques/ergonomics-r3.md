# Ergonomics critique — round 3

**Feature**: `001-plan-artifact-saved-apply-infp-653` · **Head**: `5570270` · **Lens**: ergonomics
(CLI operator, in-process API caller, adapter author) · **Persona**: reviewed through `wwbd`
(Benoit Kohler), used as a lens only — no new requirements taken from it.

This is the last permitted round. Round 2 raised three Must-Address findings (ERG-21, ERG-22, ERG-24)
and four Recommended ones the collation batched (ERG-23, ERG-25, ERG-27, plus the level pinning). All
seven claims below were verified against the artifacts and against the running CLI rather than
accepted; where a claim was checkable by execution it was executed. The quickstart was walked literally
command by command, including the two steps earlier rounds passed without running.

## Round-2 disposition

| ID | Round-2 summary | Verdict |
|---|---|---|
| ERG-21 | Two of FR-030's four derivation failures have no exception class, so AD059 does not reach them | **CLOSED** (residue → ERG-32) |
| ERG-22 | The negative walkthrough's last case cannot produce the error it demonstrates | **CLOSED** |
| ERG-24 | The run-id enumeration is unspecified when empty and unbounded when not | **CLOSED** (residue → ERG-N5) |
| ERG-23 | `--kind` without `--detail` stated only in a help string | **CLOSED** |
| ERG-25 | The skip warning's level unpinned; the completion line names no skip | **CLOSED** |
| ERG-27 | `--run-id` silently ignored alongside `--from-plan` | **CLOSED** |

## New findings

| ID | Severity | Summary | Anchor |
|---|---|---|---|
| ERG-30 | Recommended | A Track 1 step — the track defined as "local, no servers" — exits 1 with `ServerNotReachableError`. Verified by execution | `quickstart.md:43`, `:59` |
| ERG-31 | Recommended | The AD066 keyedness warning is the only operator-facing disclosure of a possible non-convergence, and it is unpinned in level, unspecified in content, untested, and undocumented — while its sibling warning three sections away was pinned in all three one round ago | `contracts/destination-write-surface.md:98`, `:146`; `tasks.md:339` (T045); `tasks.md:428-430` (T068–T071) |
| ERG-32 | Recommended | `SourcePeerUnresolvedError` is raised for the ambiguous-kind store probe, but its taxonomy row describes only the *absent* case and its next action is written for that case — a smaller recurrence of ERG-21's own defect | `tasks.md:280` (T029) vs `contracts/plan-reader-api.md:89` |
| ERG-33 | Recommended | The live walkthrough's step 6 states unconditional convergence, which AD066 struck for relationship-crossing kinds; the Track 2 SC-002/SC-003 rows carry no caveat either | `quickstart.md:205-206`, `:165-166` |
| ERG-34 | Nit | `RUN_ID` is assigned a literal before the step that produces one; walked literally, step 2 fails with the unknown-run-id error | `quickstart.md:174-178` |
| ERG-N5 | Nit | The no-runs next action does not name the command, unlike the sibling rows in the same table | `contracts/cli-review-mode.md:185` |

Carried forward, unresolved, not re-argued: **ERG-12** (`verification_notes: list[str]` versus a
structured failure), **ERG-13** (`by_action: dict[str, int]` against a closed vocabulary), **ERG-15**
(no task tells adapter number ten the contract changed — `grep "dev/guides\|dev/knowledge\|adding-an-adapter"`
over `tasks.md`, `plan.md` and `quickstart.md` still returns nothing), **ERG-16** (no machine-readable
review output), **ERG-26** (the SC-012 comparison is a whole-file `diff` against width-dependent Rich
output with the capture environment unpinned), **ERG-28** (the reader does not say what
`operations(kind=…)` does when `config` is `None`). Carried Nits: **ERG-N1**, **ERG-N2**, **ERG-N4**.

Closed since round 2 without being routed: **ERG-14** — `verify_plan` *is* a supported surface, because
`plan-reader-api.md:3-5` declares "only the names below are a supported surface" and `verify_plan` is one
of them. **ERG-N3** — the help table is still unscoped, but T090 asserts the strings against `diff --help`
specifically (`tasks.md:428`), so the wrong-on-`apply` reading cannot ship.

**Counts, new this round: 0 Must-Address · 0 RETHINK · 4 Recommended · 2 Nit.** Carried unresolved: 6
Recommended, 3 Nit. **Blocking residue: none.** Every finding above is a sentence, a clause or an
assertion, and none of them changes a decision.

---

## Verification of the round-2 claims

### ERG-21 — CLOSED

Both classes exist, both carry a distinct route, and the sweep that would have missed them now reaches
them.

`contracts/plan-reader-api.md:88-89` declares `UnformableDestinationIdentityError` (names the kind and
the identity attributes that resolved to nothing; next action: add the missing attribute to that kind's
`identifiers`, or drop the kind from the mapping) and `SourcePeerUnresolvedError` (names the referring
operation, the peer kind and the peer identity; next action: add the peer's kind to the configuration so
it is loaded, or remove the relationship from the mapping, "**Kept textually distinct from
`PeerNotFoundError`, whose remedy is a destination-side one and fixes nothing here**"). The paragraph at
`:97-107` states why the gap was structural rather than editorial — the sweep walks declared taxonomy
entries, so a condition with no entry was never swept — which is the sentence that stops a later reader
re-collapsing the two.

The three instrumentation points all moved, which is what I checked rather than the prose:

- **T003** (`tasks.md:200`) lists both classes by name in an exhaustive, closed list, keeps
  `next_action: str` required and non-empty on the base class, and states that their next actions "must be
  distinct from each other and from `PeerNotFoundError`'s".
- **T083** (`tasks.md:307`) now requires "**Assert the class and the next action, not only the kind and
  the cause**" and, separately, "**Assert the two source-side classes are textually distinct from
  `PeerNotFoundError`'s destination-side remedy**". Round 2's complaint was that the task asked for kind
  and cause alone, which is satisfiable by the exact dead-end message AD059 exists to remove. Both halves
  are in the assertion list.
- **T089** (`tasks.md:427`) closes the sweep hole explicitly: "**The parametrization must cover the two
  classes AD071 adds**, since it is generated from the taxonomy table and the point of naming them was to
  bring them inside this sweep."

The decision is recorded at `spec.md:941-950` with the reasoning intact, and FR-030 (`spec.md:1877`)
carries the next-action obligation normatively. Closed. One residue, in the same family and much
smaller, is ERG-32.

### ERG-22 — CLOSED

The negative walkthrough was reordered, both branches are labelled, and the renderer case now names a
kind the configuration actually declares. I verified the declarations rather than the comments.

`quickstart.md:221-226` states the ordering rule and why it exists. The sequence is now:

```text
:231   "--- Read-only cases first: the plan must still be intact for these. ---"
:233   case 1  unknown run id, bounded to twenty with the total     (read-only)
:237   case 2  READER branch    — --kind CoreStandardGroup          (read-only)
:243   case 3  RENDERER branch  — --kind IpamRouteTarget            (read-only)
:250   "--- Destructive cases last: each of these leaves the run unusable for the cases above. ---"
:252   case 4  corrupt the checksum                                 (destructive)
:262   case 5  rm -rf "$RUN/plan"                                   (destructive, and says so)
```

Both kind claims check out against the qualified configuration:

- `CoreStandardGroup` occurs **once** in `examples/netbox_to_infrahub/config.yml`, at line 43, inside the
  commented-out `order:` example — so it is undeclared and the **reader** raises `UnknownPlanKindError`.
  The quickstart cites `:43` and says exactly that.
- `IpamRouteTarget` is a real `schema_mapping` entry at `config.yml:469` (`mapping: ipam.route-targets`,
  `identifiers: ["name"]`) — declared, so the reader returns `[]` and the **renderer** raises. The
  quickstart cites `:469`.

`:268-271` then states the property the pair exists to prove: they must produce different messages from
different layers, "If both come back identical, AD058's split has not been implemented and the
walkthrough has caught it." That converts two commands into a real check rather than two commands that
both error.

Case 3's kind is chosen with a hedge — "IpamRouteTarget … is usually empty against demo data" — but the
line above it gives a deterministic method ("Pick any kind the summary in step 2 of the walkthrough above
did NOT list a count for"), and the summary's `By kind` line lists only kinds that have operations, so the
method is executable. Acceptable. Closed.

### ERG-24 — CLOSED

Both halves landed, in both contracts and in three tasks, with the bound stated consistently as twenty
everywhere I could find it.

**The bound.** `cli-review-mode.md:184` — "**the most recent twenty run identifiers for that sync — with
the total stated when the list truncates**", with the reason attached rather than left as a number:
"the bound exists because nothing in the repository prunes a run directory and retention is out of scope,
so an hourly pipeline would otherwise answer the commonest typo in this feature with thousands of lines".
Mirrored at `plan-reader-api.md:87`, which also records why the ordering is free — run ids sort by time by
construction (`generate_run_id()` is `YYYYMMDDTHHMM-<8 hex>`, verified at
`infrahub_sync/cache/paths.py:44-52`).

**The empty case.** `cli-review-mode.md:185` is a row of its own: "Error naming the run identifier the
operator asked for and stating plainly that **this sync has no stored runs**, with 'produce a plan for this
sync first' as the next action. Not a traceback: `cache_root_for` computes a path and never creates or
checks it (`infrahub_sync/cache/paths.py:26-43`), so an unguarded listing raises `FileNotFoundError` on a
sync that has never run — which is the *first-run* experience". I re-read `paths.py:26-43` and the code
fact is exactly right: `cache_root_for` returns `Path.cwd() / ".infrahub-sync-cache" / sync_name` (or the
`INFRAHUB_SYNC_CACHE_DIR` override) and never touches the filesystem.

**Test coverage.** T062 (`tasks.md:418`) case (a) covers both arms — "a sync whose cache root does not
exist, **or exists and holds no runs**, produces the stated no-runs message … **not** a
`FileNotFoundError` traceback" — and adds the bound case: "with more than twenty runs present the message
lists twenty and states the total". T089 asserts the same two properties as part of the enumeration sweep.
T059 (`tasks.md:402`) carries both into the `apply` refusal path, and `cli-review-mode.md:227` says apply
is "bounded and guarded exactly as the review-mode rows above". Closed. One Nit residue: ERG-N5.

### The batched Recommended items

All three landed, and two of them landed in the stronger form.

**ERG-23 — `--kind` without `--detail`.** It is now a normative row
(`cli-review-mode.md:186`) with the reason stated at the row — "a documented prerequisite that nothing
enforces is the defect class the `--detail`-without-`--from-plan` row exists to close" — and T062 asserts
it: "**`--kind` without `--detail` errors naming its missing prerequisite too**, which is the one
combination the help string asserts and nothing previously enforced." The help string at `:70` states the
prerequisite, so the operator learns it before running anything.

**ERG-25 — the skip warning's level and the completion line.** `destination-write-surface.md:65` pins it:
"**The level is `logging.WARNING`**, pinned rather than described as 'operator-visible': `--quiet` floors
the package logger at `logging.WARNING` (`infrahub_sync/cli.py:29` …), so an `INFO`-level emission would
satisfy every prose description of this row and vanish for exactly the scripted and CI invocations where
this warning and the run record are the only signals." T054 asserts the level and not only the text
(`record.levelno >= logging.WARNING`). The completion line is an obligation in four places
(`destination-write-surface.md:66`, `cli-review-mode.md:232`, `spec.md:1675`, T059 at `tasks.md:404`) with
the sample text `Applied run <id>: 33 operations applied, 4 deletes skipped`, replacing today's bare
`logger.info("Applied run %s", …)` which I re-read at `infrahub_sync/cli.py:352`.

**ERG-27 — `--run-id` alongside `--from-plan`.** Now warned about rather than silently ignored, with the
asymmetry justified at the row (`cli-review-mode.md:33`, restated at `:38-41`): it is the one exception to
"ignored silently" because it is the only other option that names a run. T062 case (b) asserts the warning
*and* that the run actually read is the `--from-plan` one — the second half matters, because a test that
only checks for a warning would pass against an implementation that warned and then read the wrong run.

---

## Quickstart walk

Walked literally, command by command. Every command that can run today was run, **including the two
steps rounds 1 and 2 recorded as "not executed"** — which is where this round's quickstart finding came
from.

| Step | Command | Result |
|---|---|---|
| Prereqs (`:26-29`) | `uv sync` | **pass** |
| Gate (`:48-51`) | `uv run invoke format` | not run — mutates the tree, this review is read-only |
| Gate | `uv run invoke lint` | not run — same reason |
| Gate | `uv run ty check .` | **pass** — exits 0 (3 `unused-ignore-comment` warnings, no errors), so "must exit 0" holds |
| Gate | `uv run pytest -q` | **pass** — 110 passed, 3 skipped |
| Sanity (`:57`) | `uv run infrahub-sync --help` | **pass** — five commands, no group |
| Sanity (`:58`) | `uv run infrahub-sync list --directory examples/` | **pass** — 14 syncs listed |
| Sanity (`:59`) | `uv run infrahub-sync generate --name from-netbox --directory examples/` | **FAIL — ERG-30.** Exit **1**, `ServerNotReachableError: Unable to connect to 'http://localhost:8000'`, full Rich traceback. Executed against a scratch copy of `examples/`; the real tree was not touched |
| SC-012 (`:65`) | `uv run infrahub-sync --help > /tmp/help-after.txt` | **pass** |
| SC-012 (`:66`) | `diff tests/data/cli_help_baseline.txt …` | **pass as specified** — `tests/data/` correctly absent today, which is what T002 says. Latent width fragility unfixed: ERG-26 |
| SC-012 (`:67`) | `uv run infrahub-sync diff --help \| grep -E 'from-plan\|detail\|kind'` | **pass for today's state** — no match, because the options do not exist yet. Option names are short enough that Rich's 80-column wrapping will not break the match post-change (verified against the existing `--full-extract` row, where only the *help text* is elided) |
| Suites (`:82-87`) | six `pytest` paths | **pass** — `tests/cache` and `tests/adapters` exist; `tests/plan`, `tests/data`, `tests/test_cli_plan_review.py`, `tests/test_potenda_plan_artifact.py` do not, matching T001/T002 |
| Criteria table (`:95-113`) | — | consistent with `tasks.md`'s evidence table; SC-007 reads `applied` on both halves |
| Inspect (`:120-123`) | `python -m json.tool`, `head -3 \| python -c`, `wc -l` | **pass** — executed against a synthetic run directory. Bare `python`: ERG-N4 |
| Checksum (`:131-141`) | `uv run python - "$RUN" <<'PY'` | **pass, re-verified by execution** — round 1's defect stays fixed: `:131` passes `"$RUN"`, `:133` is `pathlib.Path(sys.argv[1])` with no default. Recorded and recomputed hashes matched |
| Track 2 (`:149`) | `pytest -m integration …` | **pass as specified** — the marker is declared at `pyproject.toml:133-135` exactly as cited |
| Walkthrough 1 (`:177`) | `diff --name from-netbox --directory examples/` | not run — needs NetBox and Infrahub |
| Walkthrough 2 (`:182`) | `diff … --from-plan "$RUN_ID"` | **surface correct** — matches `cli-review-mode.md:24-25`. `RUN_ID` literalness: ERG-34 |
| Walkthrough 3 (`:187-188`) | `… --from-plan "$RUN_ID" --detail --kind LocationSite` | **pass** — `LocationSite` is declared at `config.yml:58` |
| Walkthrough 4 (`:191`) | `apply --name … --directory … --run-id "$RUN_ID"` | **pass** — `apply --help` confirms `--name`, `--directory` and a **required** `--run-id`; annotation now says exit 0, `applied`, `WARNING` level and the completion line |
| Walkthrough 5 (`:199-200`) | `python -c "…run.json…"` | **pass** — the `\`-newline inside double quotes is a valid continuation; `RunFile.KEYS` includes `summary` (`infrahub_sync/cache/sidecars.py:76`), so both reads resolve. Bare `python`: ERG-N4 |
| Walkthrough 6 (`:206`) | `apply … --run-id "$RUN_ID"` (again) | **runs**, but its annotation overstates what AD066 now guarantees: ERG-33 |
| Negative 1 (`:235`) | `diff … --from-plan not-a-run-id` | **pass** — read-only, and now first |
| Negative 2 (`:240-241`) | `… --detail --kind CoreStandardGroup` | **pass** — undeclared, verified at `config.yml:43`; labelled READER |
| Negative 3 (`:247-248`) | `… --detail --kind IpamRouteTarget` | **pass** — declared, verified at `config.yml:469`; labelled RENDERER |
| Negative 4 (`:253-260`) | corrupt heredoc, `apply`, status read | **pass** — all three blocks executed successfully against a synthetic run directory |
| Negative 5 (`:264-265`) | `rm -rf "$RUN/plan"`, `apply` | **pass** — last, and the comment says it removes `plan/` for good |
| Docs (`:279-280`) | `uv run invoke docs.generate` | **pass** — `tasks/docs.py:19-26` shells out to `typer … utils docs`, no server involved |
| Docs (`:280`) | `uv run rumdl check .` | **pass** — "No issues found in 76 files", spec directory included |

**One broken step: ERG-30**, and it is a step both prior rounds waved through — my round-2 walk recorded
it as "not executed (it writes files)", which was an assumption, not an observation. It is also the first
quickstart defect in three rounds that fails **loudly**: rounds 1 and 2 found false *passes* (a heredoc
that silently defaulted to `.`, a `git stash` that made the comparison a self-diff, a `--kind` case that
errored for the wrong reason and therefore looked satisfied). This one is an unmistakable non-zero exit
with a traceback, which is why it is Recommended rather than Must-Address.

---

## The narrowed keyedness signal, from the operator's seat

**Verdict on the design: correct, and correctly split. Verdict on the signal: under-specified and
untested — ERG-31. Not blocking.**

**The split is right, and I would defend it.** Raise where an unkeyed render can only be a defect; warn
where it is the library's expected behavior for a reason this outcome does not control. The
discriminating predicate — is the destination kind's human-friendly ID all-direct, or does it cross a
relationship — is a property of the destination schema that the check already holds at the point it runs
(`destination-write-surface.md:143-146`), so the branch is decidable rather than aspirational. And the
rationale for not refusing is stated as a consequence rather than a preference: refusing would withdraw
the ten identity-bearing-reference mapping entries of the qualified configuration
(`LocationRack.site`, `DcimDeviceType.manufacturer`, `DcimDevice.location` twice,
`Interface{Physical,Virtual,Lag}.device`, `IpamVLAN.vlan_group`, `IpamPrefix.vrf`, `IpamIPAddress.vrf`
— `destination-write-surface.md:305-308`) from what the outcome delivers, which is the
relationship-bearing capability DBR-013 and DBA-008 require. Withdrawing a capability to preserve a
guarantee that was false anyway is the worse trade.

**Striking the flat claim is also right**, and it was struck properly rather than softened: the phrase
"an unkeyed write is never issued" is gone from all three places and replaced everywhere by the narrower
sentence — spec FR-013 (`spec.md:1522-1530`), `plan.md:537-539`, `destination-write-surface.md:148-153`,
T045 (`tasks.md:339`), and the AD066 traceability row (`tasks.md:118`). Round 2's AD066 said "not both",
and the artifacts did not do both.

**Once per kind is the right cardinality.** Per operation would put one line per row on a 4 000-operation
apply — the same drowning failure ERG-24's bound exists to prevent, one section over.

**Correctly distinguished from the hard failure?** Yes, and visibly so. The two arms sit in one table with
one row each and the reason in the third column, the diagnostic (step 3b, per component, names *which*
component) is kept separate from the gate (step 5b, on the rendered input) with the distinction stated at
`:131-139`, and the offline harness carries the relationship-crossing case as `xfail(strict=True)` so the
limitation retires itself instead of the risk table going stale (T081 assertion 2). An operator or a
maintainer reading these artifacts cannot confuse the two cases.

**Where it falls short is the signal itself, and that is ERG-31.** Four gaps, all in the same warning:

1. **The level is not pinned.** T045 says "**one operator-visible warning per destination kind**" —
   `operator-visible` is the exact phrase this run already ruled insufficient. One round ago T050
   (`tasks.md:350`) was changed to read "Pin the level rather than describing it as operator-visible:
   `--quiet` floors the package logger at `logging.WARNING`". The same argument applies verbatim here and
   was not applied.
2. **The message content is "naming the recorded risk"** (`destination-write-surface.md:98`, `:146`;
   T045). The recorded risk is a row in `plan.md#risks` — an artifact the operator does not have. What the
   operator needs is the kind, that the write was issued anyway, and what to watch for: a duplicate of
   that kind at the destination if the server does not key on the components as sent.
3. **No task asserts it.** `grep "5b\|once per kind\|unkeyed"` over `tasks.md` returns T045
   (implementation), T081 (the keyedness xfail) and the AD066 traceability row. T081's four assertions
   are keyedness-all-direct, keyedness-crossing-as-xfail, the issued re-read, and repeat-render identity —
   none of them the warning. So neither its existence, nor its level, nor its once-per-kind
   deduplication is asserted anywhere. T062's own words apply: "an untested bound is a bound that will be
   removed by someone tidying up."
4. **No documentation obligation.** T068–T071 (`tasks.md:428-430`) cover the `plan/` layout, the
   review-then-apply workflow, the regenerated CLI reference and a delete-claims sweep. T069 states the
   delete limitation plainly, by name, because AD055 required it. Nothing requires any page to state that
   convergence is unverified for destination kinds whose identity crosses a relationship.

**Why this is Recommended and not Must-Address.** I considered blocking it and decided against, for
reasons I want on the record so the collation can disagree with them. The word in the artifacts is
"warn", so an implementer will almost certainly emit `logger.warning` and the level will land right by
accident. The message will almost certainly name the kind, because T045's sentence is about the kind. The
substance is disclosed thoroughly for the *implementer* — spec FR-013, `plan.md`'s two Material risk
rows, both write-path contracts, T045 and T081 all carry it, and the quickstart's preamble
(`:20-22`) states the limitation up front. And the residue that would actually reach an operator — one
docs clause and one test assertion — does not make the surface unusable and does not mislead anyone into
a damaging action on its own. The asymmetry with AD055's four-layer disclosure is real and worth fixing;
it is not worth terminating a run over.

*Minimum fix, four clauses*: pin `logging.WARNING` in `destination-write-surface.md:146` / spec
`:1527` / T045; say what the message says (kind, write issued, watch for a duplicate of that kind) rather
than "the recorded risk"; add one assertion — emitted once per kind, not once per operation, at
`WARNING` — to T045's done-when or T081; and add one clause to T069 stating that convergence is not
verified in this release for kinds whose identity crosses a relationship.

---

## The operator story, end to end

**Both flows the brief cares about hold. The residue is annotation, not structure.**

### plan → review → apply

The chain is complete and each link is a stated obligation with a test behind it.

1. **`diff` produces the plan.** New hard failures on the most-run command
   (`cli-review-mode.md:201-214`), all four now with a named class and a route (ERG-21), no tolerance
   switch, and the constitutional reading written down rather than assumed (`plan.md:171`).
2. **`diff --from-plan <run-id>` reviews it.** One option, one meaning — the two-inverse-meanings trap
   AD057 removed is gone from every surface I could grep, and `spec.md:89` keeps the old spelling only
   inside its own decision record, superseded three lines later. No adapter, no extraction, no lock, no
   run directory, run state untouched — five separate obligation rows with requirement tags
   (`cli-review-mode.md:83-87`). `--detail` and `--kind` name their prerequisites in their own help text,
   and both combination rules are now enforced rather than documented.
3. **Every way this can go wrong names a next action.** Fifteen taxonomy rows, `next_action` required on
   the base class, T089 sweeping `__subclasses__()` transitively so a later addition trips rather than
   shipping a dead end, and four enumerations that the code demonstrably holds at the raising site.
   The two that were dead ends in round 2 are routes now.
4. **`apply --run-id` executes exactly what was reviewed.** Refuses before constructing anything;
   bounded, guarded enumeration on a missing run; all five checks plus the write-surface check named
   individually; `failed` with a present-and-empty applied set rather than absent fields; one writer for
   `run.json` so the record survives the CLI's own save (AD069); and re-apply permitted with verification
   still running.
5. **SC-005 closes the loop as a value.** The identifier set from review equals
   `summary["applied_operations"]` in order — an equality, not an inference.

### The delete-bearing variant

Unchanged from round 2's judgment, and I re-verified the two gaps closed. Disclosure is layered across
every surface an operator touches: a NOTE in the review summary naming the count and saying plainly that
none will be executed and that the apply will complete and record the skips (this is the load-bearing one
— the operator is told while approving); a per-record `(not executed)` marker under `--detail`; one
warning during the apply at a pinned `logging.WARNING` that survives `--quiet`; a completion line that
now names the counts instead of a bare `Applied run <id>`; two recorded values in `run.json` with
`applied ∪ skipped` required to equal the plan's whole identifier set; and a plain statement in two docs
pages (T069, T071). Exit 0 remains correct — the operator got exactly what the review screen told them
they would get, and a permanently red exit code on the ordinary case of any non-pristine destination is
the worse outcome.

### What an operator still meets undocumented

The keyedness limitation (ERG-31 clause 4) and the walkthrough annotation that contradicts it (ERG-33).
Both are sentences.

---

## Regression hunt across three rounds

Checked every surface a previous fix touched. **Nothing regressed.** In particular:

| Previously fixed | State at `5570270` |
|---|---|
| The checksum heredoc (`sys.argv[1]` with no default) | Intact and **re-executed successfully** |
| The SC-012 committed fixture; no `git stash` | Intact; the reason recorded in three places |
| Manual walkthrough steps 4–5 expecting exit 0 / `applied` | Intact, with the reasoning at `:209-214` |
| `--from-plan <run-id>` spelling | Consistent across `spec.md`, `plan.md`, both CLI-facing contracts, `tasks.md` and `quickstart.md`; the retired error case is named as retired and T062 is told not to assert it |
| `operations(kind=…)` returning `[]` versus raising | Split intact at `plan-reader-api.md:55-56`, the never-empty rule stated at the renderer, both branches tested and both now hand-demonstrated |
| `verify_plan(write_surface_missing_on: str \| None)` | Intact, with the reasoning at `:125-131` |
| `PeerResolver.resolve` as the single declared entry point | Intact; `resolve_one`/`resolve_many` gone, the caller branches on `ref.cardinality`, the rationale at `:107-113` |
| The four help strings and T090 | Intact; T090 scopes them to `diff --help`, which incidentally closes ERG-N3 |
| AD070's withdrawal of the `update_node` change | Propagated cleanly: T042 is **new** code and says "do not touch `update_node`", the traceability row at `tasks.md:122` records the withdrawal, and `tasks.md:657-661` records the pre-existing defect for a later outcome. Nothing still claims the live path changes |
| Stale checklist rows asserting `failed` | Fixed — `apply-safety.md:10`, `review-ux.md:10` and `write-convergence.md:10` all carry the AD055 banner, and CHK030 is restated per AD055 rather than left contradicting it |
| `contracts/plan-artifact-format.md` (the one contract untouched at HEAD) | No stale claim — its only `unkeyed` mention (`:109`) is about payload identity and is still true |

Two things I checked specifically because they are the kind of thing three rounds of editing breaks:
the bound is stated as **twenty** in all five places it appears (`cli-review-mode.md:184`, `:227`;
`plan-reader-api.md:87`; T059; T062; T089) with no drift; and the AD055 delete story and the AD066
keyedness story do not contradict each other anywhere — the two limitations are consistently described as
different in kind (one designed and certain, one a library constraint and uncertain), which is why the
disclosure asymmetry in ERG-31 is a gap rather than an inconsistency.

---

## Recommended

**ERG-30 — a Track 1 step needs a server.** `quickstart.md:43` opens "## Track 1 — local, no servers";
`:54` is "### CLI sanity (from AGENTS.md)"; `:59` is
`uv run infrahub-sync generate --name from-netbox --directory examples/`. Run against a scratch copy of
`examples/`, that command exits **1** with `ServerNotReachableError: Unable to connect to
'http://localhost:8000'` and a full Rich traceback — `generate` reaches Infrahub for the destination
schema. So a step under a heading that promises no servers cannot complete without one, and a maintainer
working the run guide offline meets a red traceback on the third sanity command with nothing telling them
whether the feature or the step is at fault. The contradiction originates upstream, not here:
`AGENTS.md` lists this command under "CLI sanity after changes" and *also* says under Known Issues that
"`generate` and `sync` require running servers" — the quickstart copied the first without the second, on
a command this feature does not touch. *Fix*: one clause — either move the line to Track 2, or annotate
it "requires a reachable Infrahub for the destination schema; skipped on Track 1". Worth doing in the
same pass as ERG-33, since both are quickstart annotations that promise more than the environment
delivers.

**ERG-31 — the keyedness warning is unpinned, unspecified, untested and undocumented.** Argued in full
in the section above, with the four-clause minimum fix.

**ERG-32 — `SourcePeerUnresolvedError` carries a remedy written for only one of the two conditions that
raise it.** This is ERG-21's own defect, one size smaller, in the class ERG-21 created. T029
(`tasks.md:280`) uses the class for two different outcomes of the bounded store probe: "**Zero hits and
more than one hit both fail the command**, naming the owning kind, the field, the unique-id and the
candidates tried", then "**An unresolvable or ambiguous peer fails the command** as
`SourcePeerUnresolvedError`". But the taxonomy row (`plan-reader-api.md:89`) defines the class as "A
relationship peer is absent from the **loaded source store**" and gives the next action "add the peer's
kind to the configuration so it is loaded, or remove the relationship from the mapping." For the
**ambiguous** arm nothing is absent — the same unique-id resolved in two candidate model buckets
(`{LocationRack, LocationSite}` for `DcimDevice.location`, `config.yml:239` and `:281`) — so "add the
peer's kind so it is loaded" routes the operator at a condition that is not the one they have. The
mitigating fact, and the reason this is Recommended rather than Must-Address, is that T029 requires the
message to name "the candidates tried", so an operator who reads it sees two kinds listed and can work
out that the problem is ambiguity rather than absence. But that is inference, and AD059's whole standard
in this feature is that a cause without a route is a dead end. *Fix*: add the ambiguity arm to the
taxonomy row with its own next action — disambiguate the field's `reference` across the schema-mapping
entries that declare the owning kind — or give it its own class. One row either way.

**ERG-33 — the live walkthrough's convergence step states what AD066 struck.** `quickstart.md:205-206`
is `# 6. Apply again — converges, no duplicates.` with no qualification, under the **Track 2 — live
destination** heading, on the qualified `from-netbox` configuration whose identity-bearing references
number ten. Under AD066 that is exactly the population for which the render is unkeyed today and
convergence is unverified. The Track 2 table's SC-002 row (`:165`) — "applies the identical plan again,
compares — no duplicates" — and SC-003 row (`:166`) carry no caveat either. So the maintainer who runs
the deferred evidence may see duplicated devices, interfaces and prefixes while the page tells them that
cannot happen, and has to reach the preamble 190 lines up (`:20-22`) or `plan.md`'s risk row to learn
that it is a recorded limitation rather than a defect they just introduced. *Fix*: one clause on step 6
and one on the SC-002/SC-003 rows — for a destination kind whose identity crosses a relationship,
convergence is what these criteria are *measuring*, not what they are asserting, and a duplicate there is
the recorded AD066/AD067 limitation rather than a regression. The failure direction is a false alarm and
not a false pass, and the truth is in the same file, which is why this is Recommended.

## Nits

**ERG-34** — `quickstart.md:174` assigns `RUN_ID=20260726T1804-9f3ac210` **before** step 1 produces a
run, and `:178` shows step 1 printing that same identifier. Walked literally, step 2 reads a run id that
does not exist and fails with the (well-specified) unknown-run-id error. Every later step in both
walkthroughs depends on the variable, including `RUN=…/"$RUN_ID"` at `:229`. One comment — "set this to
the run id step 1 prints" — removes the ambiguity. Worth doing precisely because this file has been read
literally three times now.

**ERG-N5** — the no-runs next action is "produce a plan for this sync first"
(`cli-review-mode.md:185`, mirrored at `plan-reader-api.md:87`), which does not name the command. Two
rows above, `PlanFormatV1Error`'s next action does: "re-run `diff` for this sync to produce a
current-format artifact". The audience for the no-runs row is by construction the first-run operator, who
is the least likely to already know. AD073's own recommendation said "run `diff` first"; the word `diff`
did not survive into the recorded wording. One word.

**ERG-N1, ERG-N2, ERG-N4** carry forward unchanged and unargued:
`PlanFormatVersionError`'s next action still does not name "upgrade `infrahub-sync`" for a version above
the supported set; `cli-review-mode.md:198`'s "before any of this code runs" is over-claimed mid-command
(Click consumes the next option as the value and reports an unexpected extra argument instead — the
conclusion holds, only the parenthetical does not); and the quickstart still mixes bare `python`
(`:121`, `:122`, `:199`) with `uv run python` (`:131`, `:253`, `:260`) and hardcodes
`.infrahub-sync-cache/` (`:120`, `:199`, `:229`) where `INFRAHUB_SYNC_CACHE_DIR` overrides it
(`infrahub_sync/cache/paths.py:34-41`). `PeerResolver.remember` is still positional while `resolve` is
keyword-only (`destination-write-surface.md:275`).

---

## Answer: is the consumer experience sound enough to implement from?

**Yes. Implement.**

No blocking residue remains from my lens. All three of round 2's Must-Address findings are closed, and
closed in the strong form rather than the minimum one: the two new exception classes are in T003's closed
list, in the taxonomy with distinct routes, in T083's assertion list *and* in T089's sweep, with the
structural reason the gap existed recorded so it cannot recur silently; the negative walkthrough is
reordered with an explicit destructive/read-only boundary, each case labelled with the branch it
exercises, and both kind claims verified against the configuration rather than asserted; the enumeration
is bounded to twenty with the total on truncation, the empty and absent cache root has its own error row
with a stated message, and three tasks assert both arms including a >20 case. The three batched
Recommended items also landed, two of them stronger than I proposed.

The design change I was asked to judge is sound. Narrowing the keyedness guarantee was the right call and
was executed honestly — the false claim is struck everywhere rather than softened, the two arms are split
on a predicate the check actually holds, the per-kind cardinality is right, and the limitation is carried
as a strict expected failure that retires itself. My reservations are about the *signal*, not the
decision, and they are a docs clause, a pinned level, a message sentence and a test assertion.

What remains is four Recommended findings and two Nits, plus six Recommended and three Nits carried from
earlier rounds. None of them changes a decision, an interface shape, an error taxonomy or a task's
structure. Three of the four new ones are single clauses in `quickstart.md` and one is a single row in
the error taxonomy. Every one of them is something an implementer would either settle correctly by
default or fix in the pass that touches the file anyway — which is the test for Recommended, and the
reason none of them should hold the run.

One thing I would ask the implementer to carry forward rather than treat as closed: **ERG-15 is now three
rounds old and has never been routed.** `grep "dev/guides\|dev/knowledge\|adding-an-adapter"` over
`tasks.md`, `plan.md` and `quickstart.md` still returns nothing, so the nine adapters that are not
Infrahub, and whoever writes the tenth, learn about `apply_planned_operation` from the pre-write refusal
message and nowhere else. That refusal is graceful and points at `sync`, so nothing breaks — but
`dev/guides/adding-an-adapter.md` is the file an adapter author reads, and it will not mention the
surface that now exists.

## Process

`wwbd` **was invoked** and `references/profile.md` was loaded and used as the review lens — in
particular its grounding rule, which is why `generate`'s exit code, `ty`'s exit code, the unit suite, the
`rumdl` sweep, the four quickstart heredocs, the config declarations of `CoreStandardGroup`,
`IpamRouteTarget` and `LocationSite`, the pytest marker line range, `RunFile.KEYS`, `cache_root_for`'s
filesystem behavior and `docs.generate`'s implementation were all executed or read rather than asserted
— and specifically why ERG-30 was found at all: the profile's insistence on running the command is what
made me execute a step I had passed on an assumption one round earlier. Its safety-rails posture drove
ERG-31 and ERG-33; its "a comment or message carries operational rationale or dies" rule drove ERG-32 and
ERG-N5; its conservative-rollout instinct is what argued me *down* from blocking on ERG-31 — a designed
limitation disclosed and recorded is not a fault, and the disclosure gap here is a sentence rather than a
missing mechanism. The persona also argued me down on ERG-30: a loud non-zero exit on a pre-existing
command the brief does not touch is a documentation wart, not a reason to stop a run, and the three
rounds of quickstart defects that *were* blocking all shared a property this one does not — they passed
while checking nothing.
