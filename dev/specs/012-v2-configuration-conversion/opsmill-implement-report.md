# Implementation report: Convert a v1 or v2 configuration and prove the first V3 plan

- **Spec directory**: `dev/specs/012-v2-configuration-conversion`
- **Branch**: `012-v2-configuration-conversion`, in the worktree `.claude/worktrees/012-v2-conversion` (not pushed)
- **Base commit**: `7dc0d4c9`
- **Head commit**: `be357bd3`
- **Run status**: INCOMPLETE. 38 of 40 tasks are done. T031 is blocked on spec 009, and T033 (the first real gate run) is deferred. All local-pass evidence is present.

## 1. Chunk ledger

| # | Chunk | Tasks | ✅ / ⚠️ / ❌ | Commits | Flagged upward |
| --- | --- | --- | --- | --- | --- |
| 1 | Setup | T001–T003 | 3 / 0 / 0 | `a332f437` | yamllint ignores `tests/data/v2_examples` (4 copied files break its rules); report sections are typed tuples |
| 2 | Foundational | T004–T008 | 5 / 0 / 0 | `b628eb70` | research R2 was wrong for Peering Manager (reads `PEERING_MANAGER_*`) and IP Fabric (file first); corrected in `33338ec3`, `95e438ba` |
| 3 | US1 conversion core | T009–T012, T014–T017, T019 | 9 / 0 / 0 | `d5a2eded` | absent ACI `verify` is left out of the package; unknown top-level v2 keys dropped with a note; fixed a credential-prefix literal from chunk 2 |
| 4 | US1 CLI | T013, T018 | 2 / 0 / 0 | `cbd8a6dd` | report text moved to `conversion_report.py` (Pylint module size); header says "v1 or v2 configuration" |
| 5 | US2 refusals | T020–T023 | 4 / 0 / 0 | `bb1a8034` | also fixed the missing "no v2 variable is set here" line (FR-013) |
| 6 | Examples and secrets | T024, T025 | 2 / 0 / 0 | `8df24194` | observium and slurpit examples hold placeholder text as `url`; they convert with `--source-url` |
| 7 | US3 gate | T026–T030, T032 | 6 / 0 / 0 | `d0638806` | v2 needs `generate` before `sync` and `pynetbox` added; nightly job timeout 240 → 300 minutes |
| 8 | Polish | T034–T040 | 7 / 0 / 0 | `fde661e8` | gate result left as a `TODO(release)` in `v2-to-v3.mdx`; `configs convert` docstring extended so the CLI reference lists refusals |

## 2. Tasks not completed

- **T031**: records left out and problems in the gate. Blocked on spec 009 (`009-plan-problem-reporting`) being merged into `feature/v3-develop`. Until then the `v2-gate` suite stops with "records left out and problems cannot be counted on this build" and never reports a pass. The task also adds `V2_GATE` to the outcomes that fail the nightly job.
- **T033**: the first real gate run. Deferred because it seeds the NetBox `demo` dataset and resets the preview stack (`preview.down --volumes`), which other sessions on this machine use. Command: `uv run python .github/scripts/nightly_e2e.py suite v2-gate`.

## 3. Local-pass evidence

Times are UTC on 2026-10-22, on macOS with Python 3.11.8 in the worktree's `.venv`, unless noted.

| Test id | Type | Run command | Passed at | Environment | Verbatim pass line |
| --- | --- | --- | --- | --- | --- |
| `tests/configuration/test_conversion.py` (tables, naming, visible variables; 91 cases) and `test_conversion_worker_variables.py` | unit | `uv run pytest tests/configuration/test_conversion.py tests/configuration/test_conversion_worker_variables.py -q` | 19:22:24Z | n/a | `92 passed in 0.10s` |
| `tests/configuration/test_conversion.py` (conversion core, 54 cases) | unit | `uv run pytest tests/configuration -q` | 19:31:56Z | n/a | `904 passed in 1.59s` |
| `tests/cli/test_convert_command.py` (20 cases) | unit | `uv run pytest tests/cli/test_convert_command.py -q` | 19:38:14Z | n/a | `20 passed in 0.39s` |
| refusal tests (`test_refusal` and 7 others, 19 cases) | unit | `uv run pytest tests/cli tests/configuration -q` | 19:43:34Z | n/a | `1039 passed in 2.46s` |
| `tests/configuration/test_conversion_examples.py` (17), `test_conversion_secrets.py` (30) | unit | `uv run pytest tests/configuration/test_conversion_examples.py tests/configuration/test_conversion_secrets.py -q` | 19:47:43Z | n/a | `47 passed in 0.71s` |
| `tests/test_nightly_v2_gate.py` (14), `tests/test_workflow_contracts.py` (modified) | unit | `uv run pytest tests/test_nightly_v2_gate.py -q -rA`; `uv run pytest tests/test_nightly_e2e.py tests/test_workflow_contracts.py -q` | 19:56:45Z, 19:56:57Z | n/a | `14 passed in 0.16s`; `215 passed in 11.88s` |
| review fixes: secrets, addresses, gate, CLI write, types | unit | `uv run pytest tests/cli tests/configuration tests/test_nightly_v2_gate.py tests/test_nightly_e2e.py tests/test_workflow_contracts.py tests/test_credential_canary.py -q` | 20:23:12Z | n/a | `1378 passed in 16.11s` |
| code-review fixes (16 new tests) | unit | `uv run invoke tests.tests-unit` | 2026-10-22, after `313bd898` | n/a | 6031 passed (reported by the code-review run) |
| credential and placeholder decisions (20 + 6 cases) | unit | `uv run pytest tests/cli tests/configuration tests/test_credential_canary.py -q` | 21:24:54Z | n/a | `1196 passed in 3.84s` |
| whole unit tier at head | unit | `uv run invoke tests.tests-unit` | 21:25:19Z | n/a | `6077 passed, 4 skipped, 203 deselected, 4 warnings in 145.37s` |
| `v2-gate` suite | e2e | `uv run python .github/scripts/nightly_e2e.py suite v2-gate` | deferred — local E2E not run (T033) | Docker, NetBox `demo` dataset, preview stack | n/a |

The quickstart steps that run without a Sync API passed; results are in
[quickstart-results.md](quickstart-results.md). `configs register` was not run because it needs a
running Sync API.

## 4. Review findings

Three speckit review agents (code and errors, tests and types, comments and docs), then
`/code-review xhigh --fix`.

| Severity | File | Finding | Status |
| --- | --- | --- | --- |
| High | `conversion.py` | A refused, misspelled credential setting (`tokn`) printed its value | Fixed `cadf833f`: refusals for unaccepted settings never carry a value |
| High (security) | `conversion.py` | A scheme-less address (`user:secret@host`) hid its user information, and the value that lost was printed | Fixed `cadf833f`: every candidate checked; no value printed |
| High | `nightly_e2e.py`, `testing-tiers.md` | The gate recorded no value v2 wrote (FR-025) | Fixed `180c8dc5`: current values read from Infrahub by destination id; `<not read>` when unavailable |
| Medium | `nightly_e2e.py` | The gate could pass with operations it did not parse, or with deletes not computed | Fixed `180c8dc5` |
| Medium | `workflow-nightly-e2e.yml` | The nightly job would fail every night until T031 | Fixed `180c8dc5`: `V2_GATE` is out of the failing outcomes until T031 |
| Medium | `conversion.py` | Every declared credential got a reference, so genericrestapi, prometheus and slurpit packages needed unused secrets | Fixed `be357bd3` after the product owner's decision: references follow the v2 authentication method |
| Medium | `conversion.py` | A placeholder changed the variable order away from v2's | Fixed `be357bd3` after the product owner's decision: v2's fixed order |
| Medium | `conversion.py` | `*_env_vars` lists ignored; Peering Manager not handled like genericrestapi; non-mapping settings accepted; booleans compared as text | Fixed `313bd898` |
| Medium | `conversion.py` | Same adapter on both sides shares one credential reference | Report note added `313bd898`; naming unchanged (FR-006) |
| Low | `cli.py` | Output could be the input file; a file created during the prompt could be replaced; write errors gave no reason; hard links unavailable on some file systems; YAML aliases in the package | Fixed `cadf833f`, `313bd898` |
| Medium | type design | Entries allowed a placeholder or variable origin without a variable; `is_loopback` stored separately | Fixed `cadf833f` |
| Low | docs | Exit-status text, ACI wording, two missing refusals, changelog wording | Fixed `cadf833f` |
| Low | `nightly_e2e.py` | Expected-delete identities are compared without the CLI's display escaping | Deferred: the list is empty; matching would import a private CLI helper |
| Low | docs | Refusal text cites the internal key INFP-654 | Deferred: kept as agreed in the grilling session |

The simplify aspect of the speckit review did not run separately; `/code-review` included
simplification findings, which were applied.

## 5. Autonomous decisions to revisit

- The implementation ran in a separate worktree, and the shared checkout was switched back to `cli-optional-reason`.
- T031 and T033 were not attempted (see section 2).
- The gate reads the value v2 wrote through the Infrahub SDK by destination id, not by HFID.
- `V2_GATE` does not fail the nightly job until T031 lands; its result is still in the run summary and uploaded.
- Prometheus: an unknown `auth_method` is refused, although v2 silently sent no credential; `basic` now needs both username and password, although v2 accepted an empty password.
- Slurpit: only `api_key` is referenced; a `token` setting would have failed in v2 too.
- `tests/test_workflow_contracts.py` failed 6 tests in one run and passed in the next two; the cause was not found.

## 6. Suggested next steps

1. Review the branch and decide on the base: it still carries the two `cli-optional-reason` commits.
2. Run T033 on a machine where the preview stack and NetBox can be reset, attach `gate-result.json`, and open a defect for each finding.
3. Do T031 once spec 009 is merged.
4. Open a pull request when you ask for it.

## 7. Move onto `feature/v3-develop` (2026-11-13)

The branch `012-v2-configuration-conversion` was deleted after this report, and its worktree was
removed. Its 22 commits (`be804847..65d20c55`) were cherry-picked onto `feature/v3-develop` at
`7d7aabba`. The conflicts were in `CLAUDE.md` (develop's active-spec line was kept) and in the
imports of `infrahub_sync/cli.py`.

Changes made for the code that `feature/v3-develop` gained since 2026-10-22:

- Spec 014 refuses a bare `source` or `owner` name. The conversion now writes a v2 group name as
  `{kind: CoreAccountGroup, name}`, the kind v2 2.0.2 looked up (`adapters/infrahub.py` lines
  339-357 at tag `2.0.2`), and notes that V3 stops the run where v2 fell back to an account.
  Spec FR-019 and the lineage assumption are updated.
- Spec 019 added `approve`. The migration page now says to approve the plan before `apply` when
  the Sync API requires approval.
- Spec 009 is now spec 020 (`020-plan-problem-reporting`, not merged). T031 still waits for it.

Still open:

- T031 (blocked on spec 020) and T033 (the first real gate run, which resets the preview stack).
- Decided on 2026-11-13: the conversion does not set `protect`, because it is not the V3 default.
- The `v2-gate` suite imports `tasks/preview.py` and starts the preview stack. The branch
  `010-prefect-pr2-preview-removal` deletes that stack, so the suite must move to its replacement
  when that branch merges. The commit that moved the preview Infrahub to port 8000 (`65d20c55`)
  was left out of this branch, because it changes only files that branch deletes and the gate
  reads the port from the preview settings.
- `COMPOSE_WORKER_CREDENTIAL_VARIABLES` is pinned to `docker-compose.yml` by a test. The branch
  `021-compose-store-credentials-ca` changes that file, so the list may need an update when it
  merges.

The `Claude-Session` trailers of the cherry-picked commits were removed. The gate fixture
`tests/data/v2_gate/plan_detail_sample.txt` now holds the lines `runs plan --detail` prints on
this base (`schema_fingerprint`, `verification_note`, the owner notice and `last change`), and
the gate tests pass with them.

## 8. First gate run (T033), 2026-11-13

Run from this worktree with `uv run python .github/scripts/nightly_e2e.py suite v2-gate`, with
`PREVIEW_INFRAHUB_PORT=8000` in the git-ignored `development/preview.local.env`.

1. The first attempt stopped before v2 ran: `infrahub-sync==2.0.2` is not on PyPI, and the
   `2.0.2` GitHub release is still a draft. The nightly job fails the same way until it is
   published. The second attempt installed v2 from the local `2.0.2` tag (`1453aafa`) through a
   temporary edit that was not committed.
2. v2 `generate` and `sync` wrote the pinned NetBox `demo` dataset to the `v2-gate` branch.
   `configs convert` and `configs register` succeeded.
3. The first V3 plan failed before it read either side, with `UnwritableConvergenceIdentityError`:
   `destination kind 'IpamPrefix': no complete writable destination convergence identity;
   unusable components: ip_namespace, ip_namespace__name__value`. `IpamIPAddress` fails the same
   check. The other 19 mappings pass it.

Cause: the v2 NetBox example identifies prefixes by `(prefix, vrf)` and addresses by
`(address, vrf)` and does not map `ip_namespace`, so v2 wrote every prefix and address into the
`default` namespace. V3 requires every component of a destination uniqueness constraint to be
mapped, and Infrahub's constraint for these kinds includes `ip_namespace`. The V3 example package
maps each NetBox VRF to its own namespace (`ipam.vrfs` → `IpamNamespace`), which does not match
what v2 wrote either. Adding `ip_namespace` without an `IpamNamespace` mapping fails at the source
read (`'NetboxAdapter' object has no attribute 'IpamNamespace'`).

No creates, updates or deletes could be counted. The gate does not pass, and the fix needs a
decision: change how V3 builds the identity of a kind whose namespace defaults to `default`, make
the conversion add a namespace mapping, or document that a v2 NetBox configuration must be edited
before its first V3 plan.

The suite now calls `plan` instead of the deprecated `diff`.

Decided by the product owner on 2026-11-13: the namespace comes from the destination schema, so
the conversion does not force it. A team edits the converted package by hand. The owner also
suggested raising the case with the Infrahub team.

No working edit is known yet. Mapping `ip_namespace` as a reference needs an `IpamNamespace`
mapping on the source side, and no NetBox endpoint yields the one `default` namespace that v2
used. Mapping each VRF to a namespace, as the V3 example does, puts prefixes in other namespaces
than v2 did. V3 has no way to set an identity component to a peer that exists only in Infrahub,
such as the built-in `default` namespace. Until such an edit exists, the gate cannot plan the
NetBox example.

Rerun on 2026-11-13 with v2 `2.0.1` from PyPI (product owner decision, because `2.0.2` is not
published), still with the `2.0.2` NetBox example: v2 `generate` and `sync` processed all 1,119
models, `configs convert` and `configs register` succeeded, and the plan stopped at the same
`ip_namespace` identity check. The gate is blocked only by that check.

Proposed by the product owner on 2026-11-13: read the Infrahub schema, and when `ip_namespace`
is mandatory and not mapped, set it to the `default` namespace and say so in the report. Tried on
the v2 gate data with `ip_namespace` as `static: default` and in the identifiers:

- The identity check passes, and the NetBox side loads the static value.
- The Infrahub side drops the value: it turns a relationship into the peer's DiffSync key, which
  needs the peer kind to be mapped (`_resolve_peer_unique_id` in `adapters/infrahub.py`).
  `IpamNamespace` is not mapped, so the plan fails with `IpamIPAddress identifier field
  'ip_namespace' cannot be None`.

So the conversion can write the value, but V3 must first accept a static relationship value whose
peer kind is not mapped: read it as the peer's name on the Infrahub side, and resolve it by name
when it writes.

## 9. `--live-schema` and the next gate finding (2026-11-13)

The planner already records a peer that exists only in Infrahub by name
(`plan/destination_only_peer.py`), as long as the peer kind is mapped. So no engine change was
needed. `configs convert --live-schema` (FR-002, FR-031) reads the destination schema without a
token. For each mapped kind whose uniqueness constraint names an IP namespace that the file
does not map, it:

- writes `{name: ip_namespace, static: default, reference: IpamNamespace}`
- sets the identifiers to the constraint fields, such as `[prefix, ip_namespace]`
- adds an `IpamNamespace` mapping with no source endpoint

Two forms were tried and failed:

- A bare `static: default`: the Infrahub side drops the value.
- Keeping `vrf` in the identifiers: a NetBox address with no VRF is refused, with
  `identifier field 'vrf' cannot be None`.

Infrahub declares `ip_namespace` optional, so the rule uses the uniqueness constraint, not a
mandatory flag. The anonymous read works on a default preview Infrahub.

The gate now passes `--live-schema`. On the next run, v2 synced 1,119 models, the conversion
filled both kinds, and the plan got past the identity check. It then stopped with
`DestinationIdentityCollisionError`: two planned creates of `DcimDevice` named `PP:MDF`. The
v2 log reports three `PP:MDF` devices created, in IDF117, IDF118 and IDF128. Infrahub holds
one, in IDF118. v2 merged three NetBox devices into one Infrahub node without reporting it, and
V3 refuses the plan instead. The counts of creates and updates are still not known, because
this refusal stops the plan first. What the gate expects for these devices is not decided.

During these runs, the branch `010-prefect-pr2-preview-removal` used ports 8000 and 9010. The
local preview settings now use 8090 and 9020.

Decided by the product owner on 2026-11-13: the `PP:MDF` collision is not a defect. NetBox
device names are unique only together with other fields such as the site and the rack, while
the destination schema identifies a device by name alone. Spec 020 turns this refusal into
records left out with the problem type `destination_identity_collision`. FR-023 now accepts
records left out and problems that are on a reviewed list, as it does for deletes. T031 adds that
list once spec 020 is merged.
