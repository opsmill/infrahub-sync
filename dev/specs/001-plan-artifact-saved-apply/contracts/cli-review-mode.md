# Contract: CLI review mode (`diff --from-plan <run-id>`)

**Requirements**: FR-008, FR-006, FR-007, SC-009, SC-012. **Bar carried from the brief**: no new CLI
command **group** (AD019). AD005 additionally chooses to extend the existing non-mutating command
rather than add a sibling, so in fact no command is added either. **Ratified corrections applied here**:
AD055 (a delete-bearing apply ends `applied` with a recorded skip count), AD056 (both depths surface the
delete-computation record and annotate the delete count), AD057 (`--from-plan` takes the run identifier as
its value), AD058 (a declared kind with no operations is empty at the reader and an error at the renderer),
AD059 (every failure names a next action), AD060 (the SC-012 baseline is a committed fixture), AD061 (help
text is specified here, not discovered), AD063 (the schema-subhash repair is dropped), AD069 (this
command is the single writer of the run record), AD073 (the run-identifier enumeration is bounded and its
empty case is stated).

## Baseline, verified

The CLI is a single flat `typer.Typer()` (`infrahub_sync/cli.py:31`) with **no** `add_typer` anywhere
in the package, exposing exactly five commands: `list` (`:77`), `diff` (`:86`), `sync` (`:166`),
`apply` (`:295`), `generate` (`:355`). The group bar is trivially met; the command bar is the one doing
work, and it is met too.

## Surface added

```text
infrahub-sync diff (--name <sync> | --config-file <path>) [--directory <dir>]
                   --from-plan <run-id> [--detail] [--kind <kind>]
```

| Option | Type | Status | Meaning in review mode |
|---|---|---|---|
| `--from-plan` | string, default unset | **new** | **Takes the run identifier as its value.** Its presence selects review mode; its value selects the stored run to read |
| `--detail` | flag, default off | **new** | Expand the summary to per-object records |
| `--kind` | string | **new** | Narrow the per-object detail to one destination kind. **Requires `--detail`**: without it there is no per-object listing to narrow, so `--kind` alone is a usage error rather than a silently ignored option (see the errors table) |
| `--run-id` | string | **existing** (`infrahub_sync/cli.py:98`) | **Unchanged and unused by review mode.** It keeps exactly its live-path meaning, "re-use a specific cache run id". Passing it **together with** `--from-plan` is ignored — and, unlike the other ignored options, **warned about**, naming which option selected the run: it is the only other option that names a run, so `--from-plan A --run-id B` is the one invocation where an operator cannot tell which run they just reviewed. It resolves to `A`. Nothing is destroyed (the mode branches above `get_potenda_from_instance`, so `B` is not even created), which is why this is a warning rather than an error; the file already warns on an ignored option at `infrahub_sync/cli.py:249-252` |
| `--name` / `--config-file` / `--directory` | existing | unchanged | Still required: a stored run is located only as `cache_root_for(<sync name>)/<run_id>` (`infrahub_sync/cache/paths.py:56-59`), so review is adapter-free but stays configuration-bound |

Every other `diff` option (`--branch`, `--show-progress`, `--adapter-path`, `--concurrent-load`,
`--full-extract`) is meaningless in review mode and is ignored; passing one is not an error, because
the mode is a read and rejecting unrelated flags would be gratuitous. `--run-id` is the one exception to
"ignored silently", for the reason its row above gives: it is the only other option that names a run, so
ignoring it without saying so leaves the operator unable to tell which run they reviewed — the residue of
exactly the ambiguity AD057 was created to remove.

### Why `--from-plan` takes the run identifier rather than pairing with `--run-id` (AD057)

The `--run-id <id> --from-plan` spelling this contract first carried gives **one option two inverse
meanings**, discriminated by a flag the operator can omit:

| Invocation | What `--run-id` means | An unknown value |
|---|---|---|
| `diff --run-id X` | a **write target** | silently created (`infrahub_sync/utils.py:244-246`), and the run's stored plan is overwritten |
| `diff --run-id X --from-plan` | a **read source** | an error |

So forgetting `--from-plan` turns a read into a destructive write against the very artifact being read —
the worst possible accident in a review-before-write feature, and one an operator cannot defend against by
reading the help text, because both spellings are valid. Folding the identifier into the review option's
own value removes the overload at no cost: `--from-plan` cannot be given without a run identifier, so the
"`--from-plan` with no `--run-id`" error case ceases to exist, and `--run-id` keeps one meaning.
`--detail` and `--kind` are unaffected.

## Help text, specified rather than discovered (AD061)

The reference documentation is generated from these strings (`uv run invoke docs.generate` →
`docs/docs/reference/cli.mdx`), so leaving them to the implementer means the operator-facing text ships
unreviewed. They are fixed here.

| Option | Help string |
|---|---|
| `--from-plan` | `Review the saved plan artifact for this run id instead of comparing live systems. Constructs no adapter, extracts nothing, and takes no lock.` |
| `--detail` | `Expand the plan summary to one record per operation. Requires --from-plan.` |
| `--kind` | `Narrow --detail to a single destination kind. Requires --from-plan and --detail.` |
| `--run-id` | `Re-use a specific cache run id for the live comparison. To review a saved plan instead, pass --from-plan <run-id>.` |

The `--run-id` string is **corrected**, not left as it stands: its current text (`infrahub_sync/cli.py:98`,
"Re-use a specific cache run id.") is now incomplete in a way that matters, because an operator reading it
has no way to learn that reviewing a stored plan is a different option. The cross-reference is the whole
point of the change. `--detail` and `--kind` name their prerequisite in their own text rather than only
failing at parse time.

## Behavioral obligations

| Obligation | Rule | Requirement |
|---|---|---|
| No adapter constructed | The mode branches **above** `get_potenda_from_instance`, which imports and instantiates both adapters (`infrahub_sync/utils.py:183-235`) | FR-008, SC-009 |
| Nothing extracted | Neither side is loaded | FR-008 |
| No pipeline lock | The mode branches **above** the `with pipeline_lock(...)` block at `infrahub_sync/cli.py:129`, so review neither blocks nor is blocked by a running sync (60-second timeout, `infrahub_sync/cache/locks.py:21-33`) | FR-008, AD021 |
| No run directory created or modified | Today `get_potenda_from_instance` does `mkdir(parents=True, exist_ok=True)` and writes `schema-sub-hash.txt` before any check (`infrahub_sync/utils.py:244-246`, `:256-263`). Branching above it is what prevents a typo'd run id from rendering as a valid zero-operation plan | FR-008, AD021 |
| Run state untouched | Review never writes `run.json` | AD031 |
| Output channel | **stdout**, via `typer.echo` — the command framework's echo facility, as the CLI already uses for help (`infrahub_sync/cli.py:69`) — never the builtin `print`, which is what the project's logging rule bans | FR-008, AD032 |
| Live path unchanged | The existing live comparison output keeps going through the logger (`infrahub_sync/cli.py:153`) | AD023 |
| Single implementation | The command is a thin renderer over `read_saved_plan`; it re-implements no reading, filtering or summarizing | FR-029 |

## Output

### Summary (default)

```text
Plan 20260726T1804-9f3ac210  (from-netbox)
checksum: OK   format: 2   operations: 37   deletes computed: yes

By action     create 21   update 12   delete 4
By kind       BuiltinTag 3   DcimDevice 8   InterfacePhysical 14   IpamPrefix 6   LocationSite 6

NOTE  4 delete operations are recorded in this plan and NONE will be executed against the
      destination by this release. Applying this plan will complete successfully and record
      4 skipped deletes on the run.
```

### The delete-computation record and the delete count are mandatory output (AD056)

Both are obligations, not formatting choices. Without them a plan whose whole delete class was omitted
renders identically to a plan that genuinely has no deletes, and FR-015's claim that the omission is
"explicit and reviewable" is delivered by nothing.

| Condition | Summary must show | Detail must show |
|---|---|---|
| `delete_operations_computed: true`, delete count 0 | `deletes computed: yes` | nothing extra — there are no deletes |
| `delete_operations_computed: true`, delete count > 0 | `deletes computed: yes` **and** the `NOTE` above, naming the count | the same annotation, plus a per-record marker on each `delete` line |
| `delete_operations_computed: false` | `deletes computed: NO` **and** a plain statement that deletes were **not computed for this plan**, so the plan may be missing deletes that exist | the same statement, repeated at the head of the detail listing |

The not-computed wording says what it means rather than restating a field name:

```text
Plan 20260726T1901-77c0be14  (from-netbox)
checksum: OK   format: 2   operations: 12   deletes computed: NO

By action     create 9   update 3
By kind       BuiltinTag 2   LocationSite 4   DcimDevice 6

NOTE  Delete operations were NOT computed for this plan: the destination side was loaded
      incrementally, which cannot enumerate it completely. This plan may be missing deletes
      that exist. Re-run with a full destination extract to compute them.
```

A plan with zero operations states so explicitly rather than printing an empty table (FR-022):

```text
Plan 20260726T1812-04bb31de  (from-netbox)
checksum: OK   format: 2   operations: 0   deletes computed: yes

This plan contains no operations.
```

A plan whose checksum does not verify is **still rendered**, with the verdict prominent (AD031):

```text
Plan 20260726T1804-9f3ac210  (from-netbox)
checksum: FAILED — recomputed a91c… does not match recorded 7d40…
                   This plan will be refused if applied. Re-run `diff` to rebuild it.
```

### Per-object detail (`--detail`)

One record per operation, at minimum the operation identifier, the action, the destination kind and
the destination identity (AD020) — the review-side source SC-005 compares against the apply result:

```text
NOTE  4 delete operations below will NOT be executed against the destination by this release.

op_3f2a1c9d0e4b6a58  create  BuiltinTag        name=prod
op_9b1d77c204e3af10  update  LocationRack      name=dc1-rack-a
op_c40e9a71b3d5f682  delete  BuiltinTag        name=retired        (not executed)
```

`--kind LocationRack` narrows to that kind. The rendering is operator-facing text, **not** a stability
contract: its wording, field order and layout may change without that being a breaking change (AD030) —
but the *presence* of the delete-computation record and the delete annotation is an obligation under
FR-006 and is asserted by SC-009, not a layout choice (AD056).

**Where the never-empty rule lives (AD058).** `--kind <declared-kind-with-no-operations>` is an error
here, in the **renderer**, listing the kinds the plan does hold. The reader underneath returns `[]` for
that case and raises only for a kind the configuration does not declare, because FR-029 requires a
programmatic caller to consume it as data — forcing every such caller to catch an exception to learn a
count would be a presentation rule leaking into the data interface.

## Errors

Every case below writes its message through the CLI's existing error path and exits non-zero. None
creates a run directory, and none is ever presented as an empty plan. **Every one names the operator's
next action (AD059)**, and where an enumeration is already in hand the message lists it rather than
echoing the operator's input back.

| Condition | Behavior | Requirement |
|---|---|---|
| Run identifier does not exist | Error naming the run identifier, the expected artifact path, **the most recent twenty run identifiers for that sync — with the total stated when the list truncates** — and the next action. The cache root is already resolved at this point, so the enumeration costs one directory listing; the bound exists because nothing in the repository prunes a run directory and retention is out of scope, so an hourly pipeline would otherwise answer the commonest typo in this feature with thousands of lines (AD073) | FR-008, AD021, AD059, AD073 |
| Sync has no stored runs at all | Error naming the run identifier the operator asked for and stating plainly that **this sync has no stored runs**, with "produce a plan for this sync first" as the next action. Not a traceback: `cache_root_for` computes a path and never creates or checks it (`infrahub_sync/cache/paths.py:26-43`), so an unguarded listing raises `FileNotFoundError` on a sync that has never run — which is the *first-run* experience, and the one operator most likely to reach for `--from-plan` before ever having produced a plan (AD073) | FR-008, AD059, AD073 |
| `--kind` without `--detail` | Error naming the missing prerequisite, which `--kind`'s own help text also states. Not silently ignored: a documented prerequisite that nothing enforces is the defect class the `--detail`-without-`--from-plan` row exists to close | FR-008, AD061 |
| Run exists but holds no `plan/` | Error naming the run identifier and the expected artifact path, with the re-plan instruction as its next action (`PlanFormatV1Error`) | FR-019, AD059 |
| Torn artifact | Error naming which part is torn, the expected versus found value, and the next action (re-run `diff`; a partial artifact cannot be repaired) | FR-010, AD059 |
| Unrecognized `format_version` | Error naming the version found, **listing the versions supported**, and the next action, textually distinct from the v1 message | FR-027, SC-018, AD059 |
| Run directory or file unreadable | Error naming the path that could not be read and the next action (check permissions and ownership on that path) | AD036, AD059 |
| `--kind` naming a declared kind the plan holds no operation for | Error naming that kind, **listing the kinds the plan does hold**, and the next action — never empty output, for the same reason a mistyped run identifier is not an empty plan. Raised by the **renderer**; the reader returns `[]` (AD058) | FR-006, AD036, AD058, AD059 |
| `--kind` naming a kind the configuration does not declare | Same message shape; raised by the reader as `UnknownPlanKindError` | FR-006, AD036, AD058, AD059 |
| The plan carries an operation whose action is outside the recognized vocabulary | **Review refuses it**, with the same message the apply path shows, because review reads through the same reader and the refusal happens while reading. Stated and tested rather than left implicit: it is the one bound on "review renders a plan it would refuse to apply", which is scoped to *verification* failures | FR-006, FR-017, AD055 |
| `--detail` or `--kind` without `--from-plan` | Error naming the missing prerequisite, which each option's help text also states | FR-008, AD061 |
| Neither `--name` nor `--config-file`, or both | Existing behavior, unchanged (`infrahub_sync/cli.py:113-114`) | — |

**Retired (AD057)**: "`--from-plan` with no `--run-id`". `--from-plan` now takes the run identifier as its
value, so the mode cannot be requested without one and the command framework rejects a valueless option
before any of this code runs. No task asserts it.

## `diff`'s live path also gains hard failures (FR-030, AD047)

Separate from review mode: the live `diff` path now derives and writes the plan artifact, so a
derivation failure — an operation with no formable destination identity, a relationship peer absent
from the loaded source store, an unencodable payload value, a duplicate operation identifier — **fails
the command** with a non-zero exit and an error naming the destination kind, the cause **and the
operator's next action** (AD059). Naming a cause without a remedy on the command an operator runs most
often is the failure mode AD059 exists to close.

There is no tolerance switch. `--continue-on-error` is declared on `sync` only
(`infrahub_sync/cli.py:190`, consumed at `:234`) and is **not** added to `diff`; derivation does not
degrade to warn-and-skip there either. A silently incomplete plan is the divergence between the
reviewed set and the applied set that FR-017 exists to prevent, and it is worst in the one feature
whose product is a plan an operator is asked to trust.

This is compatible with the constitution's "`list`, `diff` and `generate` … MUST stay safe to run at
any time" read as **the command performs no destination mutation** — which still holds: derivation
runs after a read-only comparison and writes only inside the run directory. The reading is stated in
[plan.md's Constitution Check](../plan.md#constitution-check) so it is reviewable rather than assumed.

## `apply` changes

Not a new surface — the existing `apply` command (`infrahub_sync/cli.py:295-352`) is rewired:

| Change | Rule | Requirement |
|---|---|---|
| Missing artifact | An apply naming a run that does not exist, or whose run holds no plan artifact, errors naming the run identifier, the expected artifact path, the run identifiers that do exist — **bounded and guarded exactly as the review-mode rows above (AD073)** — and the next action, and **creates no run directory**; today the directory is created unconditionally first (`infrahub_sync/utils.py:244-246`) | AD026, AD059, AD073 |
| Pre-apply gate | The five checks plus the write-surface check run before any destination write; all failures named, each with its next action. The write-surface check receives the **adapter's name**, since its message names the adapter (AD058) | FR-009, AD058 |
| Refusal state | `failed`, with `summary["applied_operations"]` recorded as `[]` and `summary["skipped_delete_count"]` as `0` | AD010, AD036, AD062 |
| Who writes the run record | **This command, and only this command (AD069).** `apply_plan` returns the record; `apply` merges it into `run_file.summary` before the save at `infrahub_sync/cli.py:350-351`. Without the merge that save destroys it: it writes the whole payload from an instance whose `summary` is the empty one built at `:322-323` (`infrahub_sync/cache/sidecars.py:87-89`). A rejection mid-apply carries its partial record on the raised error, so the same merge happens before `failed` is recorded — which is what lets FR-025's last-applied pointer survive a partial apply | FR-020, FR-025, AD069 |
| Unrecognized action | An operation whose action is outside the closed vocabulary is refused at load, before any write, naming the identifier, the action found, the recognized actions and the next action; run recorded `failed` | FR-017, AD055, AD059 |
| **Delete-bearing plan** | Completes in **`applied`**, having applied every non-delete operation and executed no delete, with `summary["skipped_delete_count"]` non-zero, `summary["skipped_delete_operations"]` recorded, and a warning on the run's log stream at **`logging.WARNING`** naming the count — pinned to that level because `--quiet` floors the package logger there (`infrahub_sync/cli.py:29`), so an `INFO` emission would disappear for precisely the scripted invocations that have no other signal. The command's **completion line** names the count too when it is non-zero, replacing today's bare `Applied run <id>` (`:352`), which is otherwise the last line an operator reads. It does **not** exit non-zero and does **not** record `failed` | FR-016, FR-017, SC-007, AD055 |
| Re-apply of an `applied` run | Permitted; verification still runs unconditionally | AD033 |

**Not changed here (AD063).** An earlier draft had `apply` also repairing the pre-existing schema-subhash
abort so it records `failed` instead of leaving `running` on disk. That path cannot execute: it imports
`infrahub_sync.utils._resolve_infrahub_schema`, which the package does not define
(`infrahub_sync/cli.py:330`, called `:332`), so the import raises and the `except ImportError: pass` at
`:341-342` swallows the whole block, leaving the abort at `:336-340` unreachable. The repair and its test
case are **dropped**; AD010's run-state rule stands for the new refusal paths above, which is what DBA-004
measures. Making the check live is a different outcome's work.

The delete-bearing warning is the only new operator-facing output on the `apply` path, and it goes to the
**log stream** where the apply already reports, not to the standard-output channel FR-008 reserves for
review (AD023).

## SC-012 evidence procedure (AD060)

Compare `uv run infrahub-sync --help`, captured **after** the change, as text against the **committed
baseline fixture** captured before any CLI change (`tests/data/cli_help_baseline.txt`, produced by task
T002). The expected difference is **zero** at the command-list level: five commands before, five after, no
group. The new options appear only under `uv run infrahub-sync diff --help`. Combined with SC-009's CLI
cases, that is the whole of SC-012's evidence.

**Do not recover the baseline by reverting the working tree at comparison time.** `git stash` on a
committed tree is a no-op that exits 0 and stashes nothing, so a `git stash && capture && git stash pop`
sequence captures the *post-change* help output as the "before" file, then diffs it against itself and
passes with no baseline at all — and `git stash pop` fails afterwards, which is easy to read as an
unrelated hiccup. The committed fixture is the only form of this evidence that cannot silently degrade,
which is why T002 exists in the setup phase.

## Documentation obligation

The new flags are a user-visible CLI change, so the same change updates
`docs/docs/reference/cli.mdx` (regenerated with `uv run invoke docs.generate`),
`docs/docs/running-a-sync.mdx` and `docs/docs/reference/cache-layout.mdx` (AD036, Constitution
"Documentation").
