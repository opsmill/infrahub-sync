# Contract: CLI review mode (`diff --from-plan`)

**Requirements**: FR-008, FR-006, FR-007, SC-009, SC-012. **Bar carried from the brief**: no new CLI
command **group** (AD019). AD005 additionally chooses to extend the existing non-mutating command
rather than add a sibling, so in fact no command is added either.

## Baseline, verified

The CLI is a single flat `typer.Typer()` (`infrahub_sync/cli.py:31`) with **no** `add_typer` anywhere
in the package, exposing exactly five commands: `list` (`:77`), `diff` (`:86`), `sync` (`:166`),
`apply` (`:295`), `generate` (`:355`). The group bar is trivially met; the command bar is the one doing
work, and it is met too.

## Surface added

```text
infrahub-sync diff (--name <sync> | --config-file <path>) [--directory <dir>]
                   --run-id <id> --from-plan [--detail] [--kind <kind>]
```

| Option | Type | Status | Meaning in `--from-plan` mode |
|---|---|---|---|
| `--from-plan` | flag, default off | **new** | Read the stored artifact instead of comparing live systems |
| `--detail` | flag, default off | **new** | Expand the summary to per-object records |
| `--kind` | string | **new** | Narrow the per-object detail to one destination kind |
| `--run-id` | string | **existing** (`infrahub_sync/cli.py:98`) | Selects the **stored run to read**. Its live-path meaning, "Re-use a specific cache run id", is unchanged |
| `--name` / `--config-file` / `--directory` | existing | unchanged | Still required: a stored run is located only as `cache_root_for(<sync name>)/<run_id>` (`infrahub_sync/cache/paths.py:56-59`), so review is adapter-free but stays configuration-bound |

Every other `diff` option (`--branch`, `--show-progress`, `--adapter-path`, `--concurrent-load`,
`--full-extract`) is meaningless in review mode and is ignored; passing one is not an error, because
the mode is a read and rejecting unrelated flags would be gratuitous.

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
op_3f2a1c9d0e4b6a58  create  BuiltinTag        name=prod
op_9b1d77c204e3af10  update  LocationRack      name=dc1-rack-a
op_c40e9a71b3d5f682  delete  BuiltinTag        name=retired
```

`--kind LocationRack` narrows to that kind. The rendering is operator-facing text, **not** a stability
contract: its wording, field order and layout may change without that being a breaking change (AD030).

## Errors

Every case below writes its message through the CLI's existing error path and exits non-zero. None
creates a run directory, and none is ever presented as an empty plan.

| Condition | Behavior | Requirement |
|---|---|---|
| `--from-plan` with no `--run-id` | Error naming the required option | AD036 |
| Run identifier does not exist | Error naming the run identifier **and** the expected artifact path | FR-008, AD021 |
| Run exists but holds no `plan/` | Error naming the run identifier and the expected artifact path, with the re-plan instruction (`PlanFormatV1Error`) | FR-019 |
| Torn artifact | Error naming which part is torn | FR-010 |
| Unrecognized `format_version` | Error naming the version found and the versions supported, textually distinct from the v1 message | FR-027, SC-018 |
| Run directory or file unreadable | Error naming the path that could not be read | AD036 |
| `--kind` matching no operation, or naming a kind the configuration does not declare | Error naming that kind — never empty output, for the same reason a mistyped run identifier is not an empty plan | FR-006, AD036 |
| Neither `--name` nor `--config-file`, or both | Existing behavior, unchanged (`infrahub_sync/cli.py:113-114`) | — |

## `apply` changes

Not a new surface — the existing `apply` command (`infrahub_sync/cli.py:295-352`) is rewired:

| Change | Rule | Requirement |
|---|---|---|
| Missing artifact | An apply naming a run that does not exist, or whose run holds no plan artifact, errors naming the run identifier and the expected artifact path, and **creates no run directory** — today the directory is created unconditionally first (`infrahub_sync/utils.py:244-246`) | AD026 |
| Pre-apply gate | The five checks plus the write-surface check run before any destination write; all failures named | FR-009 |
| Refusal state | `failed`, with an empty applied-operation set | AD010, AD036 |
| Pre-existing schema-subhash abort | Now records `failed`. Today it calls `print_error_and_abort` (`infrahub_sync/cli.py:336-340`) after `run.json` was written `running` (`:322-323`), leaving `running` on disk permanently | AD010 |
| Re-apply of an `applied` run | Permitted; verification still runs unconditionally | AD033 |

## SC-012 evidence procedure

Capture `uv run infrahub-sync --help` to a file before the change and after it, and compare as text.
The expected difference is **zero** at the command-list level: five commands before, five after, no
group. The new flags appear only under `uv run infrahub-sync diff --help`. Combined with SC-009's CLI
cases, that is the whole of SC-012's evidence.

## Documentation obligation

The new flags are a user-visible CLI change, so the same change updates
`docs/docs/reference/cli.mdx` (regenerated with `uv run invoke docs.generate`),
`docs/docs/running-a-sync.mdx` and `docs/docs/reference/cache-layout.mdx` (AD036, Constitution
"Documentation").
