# Ergonomics Critique — Round 1

**Lens**: consumer experience — remote API caller, serve-process operator, example-following
developer (the DBA-011 stranger), contributor running the repo workflow, future
API/orchestration brief authors.

**Reviewed**: `spec.md`, `plan.md`, `tasks.md`, `quickstart.md`, `contracts/*`,
`data-model.md`, `research.md`, plus repository code (`infrahub_sync/`, `examples/`,
`docs/docs/`, `pyproject.toml`). D001–D008 treated as settled; findings below are
ergonomic consequences, not relitigations. Every claim is grounded in a file:line
checked in this worktree at `9edc1bc`+spec artifacts.

Severity vocabulary: Must-Address (blocking) · RETHINK (blocking, design-level) ·
Recommended · Nit.

---

## X1 — The qualified example silently degrades to an empty plan unless the serve process runs from the repository root

**Severity**: Must-Address

**Locations**: `examples/custom_adapter/config.yml:6` (adapter spec
`./examples/custom_adapter/custom_adapter_src/custom_adapter.py:MockdbAdapter`) and
`:9` (`db_path: "./examples/custom_adapter/custom_adapter_src/mock_db.json"`);
`infrahub_sync/plugin_loader.py:235-238` (`./`-prefixed specs resolve via
`Path(path).resolve()` — process CWD); `examples/custom_adapter/custom_adapter_src/custom_adapter.py:40-50`
(`self.data = {}` default; the file is loaded **only** `if filepath and Path(filepath).exists()`);
`examples/custom_adapter/mockdb/sync_adapter.py:12` (the generated adapter re-resolves the
same CWD-relative spec); `quickstart.md` Scenario 1 Terminal B; `tasks.md` T034, T037.

**Consumer affected**: the DBA-011 clean-context developer and any operator starting
`python -m infrahub_sync.orchestration.serve`.

**Failure scenario**: the config's two `./` paths resolve against the **serving process's
working directory**, not the configuration directory. Start serve from anywhere but the
repo root and there is no error: `MockDBClient` falls back to `self.data = {}` and the
remote plan **completes successfully with zero creates** (`status: no-change`). The
stranger following the README sees a green COMPLETED run that contradicts the promised
five creates, with nothing in the Prefect log naming the cause. (If the CWD miss instead
breaks the adapter-spec resolution, they get a marginally better `ImportError`-shaped
failure — still nothing that says "wrong directory".) Neither `quickstart.md` Scenario 1
nor T034's README content list pins the working directory; `INFRAHUB_SYNC_CONFIG_DIRECTORY="$PWD/examples"`
hints at it but the config-directory value is independent of the CWD the adapter paths
need.

**Recommendation**: (a) T034 must state explicitly, as a prerequisite line and again next
to the serve command, that the serving process (and any local CLI comparison run) starts
from the repository root, and show the expected `Loading 5 InfraDevice nodes` /
five-creates checkpoint so an empty plan is recognizably wrong; (b) add the same one-line
note to `quickstart.md` Scenario 1; (c) consider the cheap fixture hardening — since the
brief allows repairing the fixture "without changing its five-device outcome", making
`MockDBClient` raise (or at least log a WARNING through the `infrahub_sync` hierarchy so
the bridge forwards it) when a configured `db_path` does not exist turns the silent wrong
answer into a diagnosable failure. I wonder if we want the DEBUG `print()`s there anyway —
see X14.

---

## X2 — One invalid `config.yml` in the configured directory poisons every remote run, and the resolution contract promises error behavior `get_instance` semantics cannot deliver

**Severity**: Must-Address

**Locations**: `contracts/execution-surface.md` `resolve_sync_instance` docstring
("Semantics identical to `utils.get_instance(...)`" + "Raises ... the matched config.yml
is unreadable/invalid (message names the logical name)"); `infrahub_sync/utils.py:128-135`
(`get_all_sync` parses **every** discovered `config.yml` — `SyncConfig(**config_data)`
at `utils.py:132` raises on the first invalid file, before any name comparison);
`data-model.md` §1 validation step 4; `tasks.md` T011 ("a `config.yml` whose `name`
matches but whose content is invalid"); `quickstart.md` Scenario 1
(`INFRAHUB_SYNC_CONFIG_DIRECTORY="$PWD/examples"` — 14 configurations).

**Consumer affected**: every remote caller and the serve operator; also the T011
implementer.

**Failure scenario**: discovery is eager. With the directory pointed at `examples/`, an
operator drops one broken `config.yml` anywhere under it (or edits one badly — contents
are re-resolved per run by design) and **every** remote run for **every** `sync_name`
fails, surfacing a raw pydantic/YAML error about a file the caller never asked for. The
contract's promise — a `RunValidationError` "naming the logical name" for the
matched-but-invalid case — is unimplementable under "semantics identical to
`get_instance`", because the parse failure happens during discovery, before the requested
name can be matched. T011's test as specified cannot be written against those semantics.

**Recommendation**: reconcile the contract now, before T006/T011: specify that
`resolve_sync_instance` iterates discovered configs tolerantly — an unparsable config
that is not the requested one is skipped with a WARNING naming the offending **file**;
one whose raw `name:` equals the request raises `RunValidationError` naming the logical
name and the file. That keeps the CLI untouched (the CLI keeps calling `get_instance`
directly per the caller-obligations table) while giving remote callers the promised
failure shape. Update T006/T011 wording to match whichever behavior is decided.

---

## X3 — `tests/test_no_prefect_import.py` as specified fails in the documented dev environment

**Severity**: Must-Address

**Locations**: `tasks.md` T028 ("passes regardless of the extra being installed";
in-process `sys.modules` assertion); `contracts/prefect-flow.md` §6 ("executed in an
environment where prefect is absent"); `pyproject.toml:141`
(`addopts = "-vs ... --dist loadscope"`); `tasks.md` T040 (full suite runs in the
dev+prefect environment).

**Consumer affected**: every contributor running `uv run pytest -q` per AGENTS.md; the
T040 gate itself.

**Failure scenario**: in the dev+prefect environment, `tests/orchestration/test_flow.py`
imports prefect. Pytest collects `tests/orchestration/` before
`tests/test_no_prefect_import.py` (alphabetical: `orchestration` < `test_...`), and with
`--dist loadscope` module-to-worker assignment is arbitrary — so the in-process
`sys.modules` assertion observes prefect loaded by a *neighboring test module* and fails
(or flickers per-worker). T028's "passes regardless of the extra" and contract §6's
"environment where prefect is absent" contradict each other as written.

**Recommendation**: specify subprocess isolation: the test launches a fresh interpreter
(`sys.executable -c ...`, exactly the shape of quickstart Scenario 0's heredoc) that
imports the package, runs the CLI sanity in-process, and asserts over its own
`sys.modules`. That makes the assertion meaningful with the extra installed and stable
under xdist. Keep the import-graph half (execution.py imports nothing from
orchestration/prefect) as a source-level check, which is order-independent.

---

## X4 — Missing-extra failure of the serve entrypoint is a bare traceback with no next action

**Severity**: Recommended

**Locations**: `contracts/prefect-flow.md` §3; `tasks.md` T015; `plan.md` Project
Structure (`orchestration/` is the only prefect importer).

**Consumer affected**: the operator who ran `uv sync --extra dev` (R-1 muscle memory)
but not `--extra prefect`, then follows the README to
`python -m infrahub_sync.orchestration.serve`.

**Failure scenario**: the very first integration command they run dies with
`ModuleNotFoundError: No module named 'prefect'` — a traceback that names neither the
optional extra nor the install command. The contract specifies a helpful serve-start
failure for the config directory but nothing for the far more likely missing-extra case.

**Recommendation**: T015 gains one contractual behavior: `serve.py` catches the prefect
`ImportError` at startup and exits non-zero with one line naming the fix (e.g.
`prefect is not installed - install the optional integration: pip install 'infrahub-sync[prefect]'`),
mirroring the config-directory failure's style. Same guard (or a module docstring note)
on `flow.py` for programmatic importers.

---

## X5 — The missing-credential failure does not name the environment variables the remote operator must set

**Severity**: Recommended

**Locations**: `infrahub_sync/adapters/infrahub.py:301-303` (`"Both url and token must
be specified!"`); spec Edge Cases ("names the missing input"); `data-model.md` §3 (quotes
the adapter message as the qualifying cause); `tasks.md` T011 ("names the missing input
(the variable/setting name)").

**Consumer affected**: the remote caller reading a FAILED state message; the serve
operator who forgot to export credentials before starting the process.

**Failure scenario**: the state message reads
`Failed to initialize the Sync Instance: Error initializing InfrahubAdapter: Both url and token must be specified!`.
"url and token" are settings-file vocabulary; the remote operator's actual next action is
"export `INFRAHUB_ADDRESS` / `INFRAHUB_API_TOKEN` in the serve process's environment and
restart" — none of which the message says, and a remote caller cannot even distinguish
whose environment is at fault. The repo already has the better pattern:
`adapters/ipfabricsync.py:59` names `IPF_URL` and `IPF_TOKEN` explicitly in the same
situation. As written, T011's "names the variable name" assertion will pass only on a
generous reading.

**Recommendation**: extend the infrahub adapter message to the ipfabric pattern
("... Please specify in the config or via the `INFRAHUB_ADDRESS` and `INFRAHUB_API_TOKEN`
environment variables.") — additive wording, checked against the DBA-009 population
(none of it asserts this message text). If touching the adapter is judged out of scope,
enrich the message at the surface wrap in `execute_run` step 3 instead, and have the
README state plainly that credentials live in the serve process's environment, set
before starting it.

---

## X6 — Quickstart (and by inheritance the README) points the config directory at all of `examples/`

**Severity**: Recommended

**Locations**: `quickstart.md` Scenario 1 Terminal B
(`INFRAHUB_SYNC_CONFIG_DIRECTORY="$PWD/examples"`); `tasks.md` T034 (runner
configuration section); `examples/` (14 configuration directories).

**Consumer affected**: the operator standing up the demo; indirectly every remote caller.

**Failure scenario**: with the directory at `examples/`, all fourteen example
configurations become remotely invokable by name — including `operation=sync` +
`confirm_writes=true` against whatever `from-netbox`, `from-nautobot`, etc. resolve to
(their configs default to localhost endpoints, and several will die confusingly on
missing optional adapter deps). It also maximizes the X2 blast radius: any one broken
example config bricks the whole remote surface. The feature's own key entity says the
directory is the input boundary; the walkthrough should model scoping it.

**Recommendation**: quickstart and README use
`INFRAHUB_SYNC_CONFIG_DIRECTORY="$PWD/examples/custom_adapter"` (recursive `**/config.yml`
discovery finds the one config; name lookup is unchanged) and add one sentence: point the
variable at a directory containing only the configurations you intend to expose remotely.
Safety rails default-on; the wide setting stays available to whoever wants it.

---

## X7 — The remote caller has no documented way to obtain the RunResult; one log line is the de facto result API and it is not marked contractual

**Severity**: Recommended

**Locations**: `contracts/prefect-flow.md` §2 step 3 (summary line
`"run %s finished: status=%s changed=%s summary=%s artifact=%s"`) and §5 (REST table:
find deployment / create run / state / logs — no "read result" row); `contracts/run-result-and-errors.md`
§1 point 5; `quickstart.md` Scenario 2 (expects the follow-up plan to "report
`status: no-change`, `changed: false`, all-zero summary" — obtainable remotely only from
that line).

**Consumer affected**: remote API callers, and the future API-façade brief (B-001) that
must extend this contract.

**Failure scenario**: the flow returns `asdict(RunResult)`, but no probe verified — and
the REST table does not claim — that a served flow run's return value is retrievable
through Prefect 3.5.0's REST API (result persistence is not asserted anywhere). So the
only remote carrier of `status`/`changed`/`summary`/`artifact_path` is the summary log
line, whose `summary=%s` renders a Python dict repr. Callers will regex it; the first
innocent rewording breaks them, and nothing marks the line load-bearing.

**Recommendation**: pick one and write it down in the flow contract and the docs page:
either (a) probe result retrieval on 3.5.0 and, if it works, add the REST row; or (b)
declare the summary line the supported remote observation surface, give it a fixed
key=value format (`summary=create:5,update:0,delete:0` rather than a dict repr), and
state that the fields mirror RunResult. Cheap now, expensive after remote callers exist.

---

## X8 — The live integration test has more prerequisites than the `integration` marker documents, with no specified skip behavior

**Severity**: Recommended

**Locations**: `tasks.md` T017; `pyproject.toml:137-139` (marker help: "require a running
Infrahub instance ... opt in with `-m integration` and `INFRAHUB_ADDRESS` +
`INFRAHUB_API_TOKEN` set"); `tests/integration/__init__.py` (existing pattern: skip when
env vars absent).

**Consumer affected**: the contributor who runs `-m integration` per the marker's own
help text.

**Failure scenario**: `tests/integration/test_remote_run_live.py` additionally needs a
live Prefect server, `PREFECT_API_URL`, and a **separately started** served deployment.
T017 lists these as requirements but specifies no guard, so a developer with only
Infrahub up (exactly what the marker text tells them suffices) gets failures or a poll
loop against a dead API instead of a clean skip naming what is missing.

**Recommendation**: T017 specifies skip guards in the existing house style —
`pytest.skip` with a reason when `PREFECT_API_URL` is unset, when the API is unreachable,
or when the deployment lookup 404s ("serve not running — start
`python -m infrahub_sync.orchestration.serve`") — and updates the marker help text to
mention the Prefect-side prerequisites.

---

## X9 — `docs/docs/orchestration.mdx` still teaches the pattern this feature obsoletes

**Severity**: Recommended

**Locations**: `docs/docs/orchestration.mdx` Prefect section ("Wrap a sync run in a
Prefect flow ..."); `tasks.md` T035 ("a short cross-link from `docs/docs/orchestration.mdx`").

**Consumer affected**: any docs reader deciding how to run Sync under Prefect.

**Failure scenario**: the page's Prefect advice is to hand-wrap the CLI in a flow — the
exact architecture DBR-008 forbids the package itself to use, and the external control
plane the Overview says developers should no longer need to build. A cross-link at the
bottom leaves the DIY guidance as the primary text; readers will hand-roll a wrapper
while a packaged flow ships in the same release.

**Recommendation**: T035 revises the Prefect subsection to lead with the packaged
optional integration (one short paragraph + link to the new reference page and example),
demoting CLI-wrapping to "if you need behaviors the feature doesn't cover (schedules,
retries, work pools)". Keep the rest of the page as is.

---

## X10 — Flow-executing unit tests will write to the developer's real `~/.prefect` and pay ephemeral-server startup unless isolation is specified

**Severity**: Recommended

**Locations**: `tasks.md` T016, T022, T023 (in-process flow calls); `research.md` probe
environment ("`PREFECT_HOME` inside the scratchpad" — the probes isolated this
deliberately; the test tasks do not).

**Consumer affected**: every contributor running `uv run pytest -q`.

**Failure scenario**: executing a `@flow` in-process in Prefect 3 without a configured
API spins up its ephemeral server against the default `PREFECT_HOME` — the developer's
real `~/.prefect` SQLite — adding startup seconds and leaving run records from unit tests
in their personal database. The probe setup shows the authors know this footgun; the
tasks don't carry the knowledge forward.

**Recommendation**: the flow-test tasks name the isolation mechanism explicitly — a
session fixture using `prefect.testing.utilities.prefect_test_harness()` (or
`PREFECT_HOME`/`PREFECT_API_URL` pointed at `tmp_path`) for any test that actually
executes the flow. Pure contract assertions (parameter signature, bridge behavior) need
no harness and should stay harness-free for speed.

---

## X11 — Example README operational gaps: empty-destination prerequisite and reset procedure, port collision, cleanup

**Severity**: Recommended

**Locations**: `tasks.md` T034 (prerequisite list omits the zero-`InfraDevice`
destination state and any reset procedure; no port or cleanup content); `quickstart.md`
Prerequisites (has "zero InfraDevice objects" and "Port 4200 free" — internal-only
artifacts the stranger never sees); spec US5 / DBA-011.

**Consumer affected**: the DBA-011 stranger reproducing the demonstration.

**Failure scenario**: (a) against a non-empty destination the plan legitimately reports
fewer than five creates; without the prerequisite stated and a verify/reset recipe, the
stranger cannot reconcile the README's promise with their result. (b) Port 4200 already
in use (a second Prefect, another dev's server) fails `prefect server start` with no
README guidance. (c) After the walkthrough, a server process, a serve process, Prefect's
SQLite under `~/.prefect`, and `.infrahub-sync-cache/` run directories are all left
behind with no cleanup section; the run artifacts being runner-local (not fetchable
through Prefect) also deserves one sentence so remote readers know where
`artifact_path` lives.

**Recommendation**: T034's content list gains: the empty-destination prerequisite with a
short verify command and a reset pointer; a one-line port-4200 note; a "stopping and
cleaning up" section (stop serve, stop server, where `PREFECT_HOME` and the sync cache
live); and a sentence that `artifact_path` is on the runner host.

---

## X12 — `/deployments/name/infrahub-sync/infrahub-sync` — decide the doubled name now, while renaming is still free

**Severity**: Nit

**Locations**: `contracts/prefect-flow.md` §2 (`FLOW_NAME = DEPLOYMENT_NAME =
"infrahub-sync"`), §5 REST table; `quickstart.md` Scenario 1.

**Consumer affected**: remote callers (the lookup URL is the first thing they type);
future orchestration briefs adding flows/deployments beside this one.

**Friction**: the flow/deployment pair produces the stuttering path
`/api/deployments/name/infrahub-sync/infrahub-sync` in every transcript, and the flat
name leaves no room for siblings (a future B-002 apply deployment would sit
asymmetrically next to it). Renaming after delivery breaks every caller's lookup.

**Recommendation**: consider `DEPLOYMENT_NAME = "run"` (path
`/deployments/name/infrahub-sync/run`; reads as what it is, and future briefs get
`.../infrahub-sync/<verb>` for free). If the doubled name is preferred for greppability,
keep it — but record the choice as deliberate in the flow contract so the next brief
doesn't churn it.

---

## X13 — Three status vocabularies for one run; the docs page must carry the mapping

**Severity**: Nit

**Locations**: `contracts/run-result-and-errors.md` §1 (`status: planned|applied|no-change`);
`data-model.md` §6 (`run.json`: `dry-run|applied|failed`); `contracts/prefect-flow.md` §5
(Prefect `COMPLETED|FAILED`); `tasks.md` T035 (docs page content list has "the RunResult
fields" but no cross-surface mapping).

**Consumer affected**: an operator correlating a Prefect run, its state message, the
summary log line, and the on-runner `run.json`.

**Friction**: one successful remote plan is simultaneously `COMPLETED` (Prefect),
`status=planned` (RunResult/log line), and `status: dry-run` (run.json). Each is
individually reasonable; nowhere user-facing says they are the same run in three
dialects.

**Recommendation**: T035's page includes a four-row table (Prefect state · RunResult
status · run.json status · meaning). Data-model already contains the facts; this is a
copy, not new design.

---

## X14 — The example custom adapter narrates via `print()`, which the log bridge cannot forward

**Severity**: Nit

**Locations**: `examples/custom_adapter/custom_adapter_src/custom_adapter.py` (multiple
`print("DEBUG: ...")` lines, e.g. around lines 47-49, 60-63, 89-91); `contracts/prefect-flow.md`
§4 (bridge forwards the `infrahub_sync` logger hierarchy only).

**Consumer affected**: adapter authors using the example as the pattern for
remotely-observable adapters; remote viewers of the demo run.

**Friction**: the mockdb source's own lifecycle ("Loading 5 InfraDevice nodes") goes to
the serve process's stdout and never reaches the Prefect log, so the example quietly
teaches a pattern whose output is invisible in the very remote-observability story it
demonstrates. (SC-002 is unaffected — its denominator is the logger hierarchy.)

**Recommendation**: either switch the example adapter's prints to
`logging.getLogger(__name__)` under a name the bridge covers (nicely reinforcing the
tasks.md logging convention, and giving X1's missing-file case a forwardable WARNING), or
add a README sentence that adapter output must use the `infrahub_sync` logger hierarchy
to appear remotely.

---

## Summary

| ID | Severity | One-line |
|---|---|---|
| X1 | Must-Address | Qualified example is CWD-dependent and silently yields a zero-create "successful" plan when serve starts outside the repo root; README/quickstart never pin the working directory |
| X2 | Must-Address | One invalid config.yml in the configured directory fails every remote run via eager `get_all_sync` parsing; resolution contract promises error naming that "identical to get_instance" semantics cannot deliver |
| X3 | Must-Address | T028's in-process `sys.modules` assertion contradicts contract §6 and fails/flakes in the documented dev+prefect environment; needs subprocess isolation |
| X4 | Recommended | Serve entrypoint without the extra dies with a bare `ModuleNotFoundError` naming neither the extra nor the install command |
| X5 | Recommended | Missing-credential failure says "Both url and token must be specified!" without naming `INFRAHUB_ADDRESS`/`INFRAHUB_API_TOKEN` or the runner locus; ipfabric adapter already models the fix |
| X6 | Recommended | Quickstart/README point `INFRAHUB_SYNC_CONFIG_DIRECTORY` at all of `examples/`, exposing 14 configs remotely and maximizing X2's blast radius; scope to the one intended directory |
| X7 | Recommended | RunResult is not documented as remotely retrievable; the summary log line is the de facto result API but is unmarked and formats `summary` as a dict repr |
| X8 | Recommended | T017 live test needs Prefect server + served deployment beyond the marker's documented env vars, with no specified skip behavior |
| X9 | Recommended | `orchestration.mdx` still advises hand-wrapping the CLI in a Prefect flow; a cross-link alone leaves obsolete guidance primary |
| X10 | Recommended | Flow-executing unit tests lack a specified `prefect_test_harness`/`PREFECT_HOME` isolation and will write to the developer's real `~/.prefect` |
| X11 | Recommended | README omits the empty-destination prerequisite/reset recipe, port-4200 note, cleanup section, and artifact-locality sentence |
| X12 | Nit | `infrahub-sync/infrahub-sync` deployment path stutters and leaves no naming room for future sibling deployments; decide now while renaming is free |
| X13 | Nit | One run wears three status vocabularies (COMPLETED / planned / dry-run); docs page should carry the mapping table |
| X14 | Nit | Example adapter narrates via `print()`, invisible to the log bridge it demonstrates |

**Blocking findings**: 3 (X1, X2, X3 — all Must-Address; no RETHINK: the surface/flow/contract
design itself is sound and probe-grounded).
