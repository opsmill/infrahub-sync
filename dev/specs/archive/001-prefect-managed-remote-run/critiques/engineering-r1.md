# Engineering Critique — Round 1

**Feature**: `dev/specs/001-prefect-managed-remote-run` (branch `001-prefect-managed-remote-run-local-dp-001`)
**Lens**: Engineering — correctness, feasibility, testability, error handling, typing, repository standards, of the design artifacts only (no implementation exists yet).
**Reviewer stance**: every claim about existing behavior was checked against the code at `9edc1bc`; citations are file:line in this worktree.
**Severity vocabulary**: Must-Address (blocking), RETHINK (blocking, design-level), Recommended, Nit.

Claims verified true before findings (so they don't get re-litigated): the pipeline lock exists with a 60 s default (`infrahub_sync/cache/locks.py:20`); `generate_run_id` emits `YYYYMMDDTHHMM-<8 hex>` (`infrahub_sync/cache/paths.py:47-53`); the DBA-009 population patches `infrahub_sync.cli.get_potenda_from_instance` exactly as research claims (`tests/test_cli_parallel.py:42`, `tests/test_cli_full_extract.py:61`, `tests/cache/test_cli_sync_cache.py:33`); the CLI defaults in `execute_run`'s signature match `cli.py` at `9edc1bc` (`show_progress=None`, `concurrent_load=True`, `full_extract=True`, `allow_rowcount_drop=False`, `continue_on_error=False`, `print_diff=True`); `structlog` is a base dependency with zero usage in `infrahub_sync/` (D008's factual premise holds); `RedisStore` is imported unconditionally at `utils.py:11` (F1's premise holds); the `integration` marker is registered (`pyproject.toml` `[tool.pytest.ini_options]`); R-5's no-op timeout mark exists (`tests/test_potenda_parallel.py:70`); no `[[tool.ty.overrides]]` exists.

---

## E1 — Must-Address: CLI error mapping by `__cause__` type is ambiguous and cannot reproduce `9edc1bc` behavior (DBR-009)

**Artifacts**: `contracts/execution-surface.md` §"CLI error mapping"; tasks T025/T026.

**Code evidence**: the current CLI has *three* distinct behaviors for engine-phase errors, two of which share the same underlying exception type:

- Factory `ValueError` → `print_error_and_abort(f"Failed to initialize the Sync Instance: {exc}")` (`infrahub_sync/cli.py:139-140`, `237-238`).
- Serial-sync `load_both_sides` `ValueError` → `print_error_and_abort(str(exc))` — **no** "Failed to initialize" prefix (`cli.py:263-268`).
- Every other lifecycle failure (diff lifecycle entirely; guardrail, diff, write_plan, sync, persist in the sync lifecycle) → mark `run.json` failed and **bare re-raise the original exception** — an uncaught traceback of the original type (`cli.py:156-159`, `285-288`).

Both load paths wrap *any* failure into `ValueError` (`infrahub_sync/potenda/__init__.py:230-246`), so "`__cause__` is `ValueError`" does not discriminate factory-init from load-time failures.

**Failure scenario**: `execute_run` step 6 wraps every lifecycle failure in `RunExecutionError` chaining the original; the contract's mapping rule says any surface error with `__cause__` `ValueError` → `print_error_and_abort` with the "Failed to initialize the Sync Instance: ..." wording. An unreachable Infrahub during `diff` raises `ValueError` from `source_load` — today an uncaught `ValueError` traceback; under the mapping, an abort with factory wording. On `sync --no-parallel`, a load failure today aborts with `str(exc)` (no prefix); under the mapping it gains the wrong prefix. Both are user-visible output changes — DBR-009 ("exit codes and output identical") is unsatisfiable as specified, and T027's population check won't catch it (those fakes never fail mid-lifecycle).

**Recommendation**: discriminate by *stage*, not cause type. Give `RunExecutionError` a typed `stage: Literal["factory", "load", "engine"]` attribute (or use distinct subclasses) set at wrap time, and bind the CLI mapping to stage: `factory` → prefixed abort; `load` (sync command only) → unprefixed abort; everything else → re-raise `__cause__` (see E2 for the cleaner alternative of not wrapping on the CLI path at all). Add a negative CLI test per stage asserting today's exact wording/traceback type.

## E2 — Must-Address: contract step 6 ("any failure") and the plan's no-broad-except claim cannot both hold

**Artifacts**: `contracts/execution-surface.md` `execute_run` step 6; `plan.md` Constitution Check row IV ("the surface catches the engine's *declared* `ValueError`/`filelock.Timeout`/`ImportError` boundaries"); constitution IV ("a broad `except Exception:` is prohibited in touched code").

**Code evidence**: lifecycle failures beyond the "declared" set are routine — `diff()`/`sync()` propagate arbitrary adapter/SDK/network exception types (`potenda/__init__.py:287-295` delegates straight to diffsync/adapters), and `write_plan` can raise pyarrow/OS errors. Today's CLI handles this with exactly the broad `except Exception:` the constitution bans (`cli.py:156-159`, `285-288`) to guarantee `run.json` ends `failed`.

**Failure scenario**: implemented with narrow catches only, a non-`ValueError` failure during step 4/5 leaves `run.json` at `status="running"` forever — a regression versus today (and it poisons `previous_successful_run_dir`-adjacent semantics for later runs) — and step 6's "any failure … marks run.json failed and raises RunExecutionError" is simply not delivered. Implemented with a broad catch-and-wrap, the plan's Constitution-IV claim is false, `uv run invoke lint` (ruff BLE001 is live under `select = ["ALL"]` with no BLE ignore for `infrahub_sync/**` in `pyproject.toml`) flags it, and the CLI traceback type changes (E1).

**Recommendation**: make the stance explicit instead of implying both. The behavior-preserving resolution: inside `execute_run`, keep today's exact pattern — `except Exception: run_file.status = "failed"; run_file.save(); raise` (bare re-raise; this is a mark-and-rethrow, and it is the *preserved* `9edc1bc` pattern, which the plan should say out loud, with the targeted `# noqa: BLE001` if ruff requires it) — and move the sanitize-and-wrap into `run_remote_request`, the remote-only composition. That gives the CLI byte-identical failure behavior for free (resolving most of E1) and gives Prefect the typed sanitized `RunExecutionError` DBR-015 requires. Whichever option is chosen, plan.md's Constitution Check row IV must describe the real design.

## E3 — Must-Address: T028's `sys.modules` assertion cannot pass in the full-suite environment T040 mandates

**Artifacts**: tasks T028 ("passes regardless of the extra being installed"), T016 (module-level `pytest.importorskip("prefect")`), T040 (full suite in the dev+prefect environment); `contracts/prefect-flow.md` §6.

**Code evidence**: `pyproject.toml` `addopts = "-vs --cov-report term-missing --cov-report xml --dist loadscope"` — pytest collects (imports) all test modules before running tests, and under xdist every worker imports the full module set. `pytest.importorskip("prefect")` at module scope in `tests/orchestration/test_flow.py` *imports prefect during collection*.

**Failure scenario**: in the dev+prefect env, by the time `tests/test_no_prefect_import.py` executes, `prefect` is already in `sys.modules` courtesy of `test_flow.py`'s collection — the assertion `not any(m == "prefect" or m.startswith("prefect.") ...)` fails no matter how pure `infrahub_sync` is. T028 and T040 are two rules that cannot both hold as written.

**Recommendation**: run the probe in a fresh interpreter: the test builds a small script (import package, run CLI sanity in-process, assert on that interpreter's `sys.modules`) and executes it via `subprocess.run([sys.executable, "-c", script])`, asserting exit code and output. Keep the static import-graph check (no `infrahub_sync.orchestration` import from base modules) in-process — that part is collection-safe. Quickstart Scenario 0 / T030 (clean venv) is unaffected and stays the authoritative SC-006 evidence.

## E4 — Must-Address: the log bridge ignores the source logger's effective-level gate, so DBR-012/SC-002 forwarding is not actually independent of Prefect logging configuration

**Artifacts**: `contracts/prefect-flow.md` §4; spec clarification #4; research "Log bridging" decision and probe c₁; tasks T014/T016.

**Code evidence**: attaching a `logging.Handler` does not defeat the level check — `Logger.info()` consults `isEnabledFor(INFO)` *before* any handler sees the record. The `infrahub_sync` logger hierarchy is level-`NOTSET` by default; the CLI makes INFO effective explicitly via `_setup_logging` (`infrahub_sync/cli.py:41-48`), which the flow never calls. In the flow-run process the effective level therefore comes from whatever root/ambient configuration Prefect installed — i.e., from Prefect logging settings the operator can change (`PREFECT_LOGGING_LEVEL` et al.). Probe c₁ records that forwarding worked but does not record what made INFO effective in the probe flow, so it does not establish the independence the clarification promises.

**Failure scenario**: an operator sets Prefect logging to WARNING (or the ambient root config simply gates INFO); every `logger.info` lifecycle line in `potenda` is dropped at the source logger; the bridge forwards nothing; SC-002's "100% of lifecycle log lines observable" silently becomes 0% with no error anywhere — precisely the operator-dependence spec clarification #4 exists to exclude.

**Recommendation**: the flow must own the level, not just the handler: capture `logging.getLogger("infrahub_sync").level`, set it to the run's intended level (INFO) before the surface call, restore it in the same `finally` that removes the bridge. Extend T016 with a case that forces the root logger to WARNING and asserts the bridged records still arrive (the SC-002 denominator test is otherwise satisfiable by an empty set).

## E5 — Must-Address: `raise ... from exc` re-leaks the unredacted cause into Prefect-visible logs, defeating the sanitization contract (SC-005)

**Artifacts**: `contracts/run-result-and-errors.md` §2 ("Specific: … chained via `raise ... from exc`" and "Sanitized: before raising, the message passes value-based redaction"); data-model §3; tasks T005/T022.

**Code evidence**: redaction as specified applies to the *new* wrapper message only; the chained original keeps its args. Engine-phase messages routinely embed upstream detail — e.g. `source_load` wraps the adapter exception's full text (`potenda/__init__.py:235-237`), which for HTTP-layer failures can contain URLs with embedded credentials or echoed settings. When a flow raises, Prefect's engine logs the exception *with traceback* to the flow-run logger, and a traceback renders the entire `__cause__` chain including each exception's message — server-side, remotely visible.

**Failure scenario**: an induced execution fault whose original message contains the canary (exactly T022's failing-run case) passes the wrapper-message redaction but surfaces the canary in the flow-run log via the traceback of the chained cause. The DBA-008 canary scan is correctly designed and will fail; the contract's mechanism cannot satisfy it — the test and the contract disagree.

**Recommendation**: state the mechanism that closes the chain: apply value-based redaction over the *whole* cause chain at the wrap point (rebuild the cause as a sanitized copy — e.g. chain from `RuntimeError(redact(str(exc)))` — or set `__suppress_context__` and inline the redacted cause text into the wrapper message). Keep T022 exactly as written; it is the regression guard for this.

## E6 — Must-Address: the `sync_name`-resolution contract does not survive `get_instance`'s actual semantics — unrelated invalid configs abort resolution, and their pydantic errors leak content the redactor cannot see

**Artifacts**: `contracts/execution-surface.md` `resolve_sync_instance`; data-model §1 validation step 4 ("pydantic `SyncConfig` parse errors **on the matched config.yml**"); tasks T006/T011.

**Code evidence**: `get_instance(name=...)` calls `get_all_sync`, which eagerly `yaml.safe_load`s and `SyncConfig(**data)`-validates **every** `config.yml` discovered under the directory before any name comparison happens (`infrahub_sync/utils.py:123-135, 138-148`). Three consequences the artifacts miss: (a) a broken `config.yml` *anywhere* under `INFRAHUB_SYNC_CONFIG_DIRECTORY` (quickstart points it at all of `examples/`) raises during discovery and blocks resolution of **all** names, not just its own; (b) the raised error names whichever file is broken — the promised "message names the logical name" is not derivable from it; (c) pydantic-v2 `ValidationError` messages embed `input_value=...` — potentially a token from that file's `settings:` — and the redaction candidate set ("values … in the **resolved** configuration's adapter settings") is empty precisely because resolution failed. That contradicts the contract's "never … file contents or credential values" on its own terms.

**Failure scenario**: an operator drops a half-edited config into the directory; every subsequent remote run of every configuration fails; the `RunValidationError` message carries pydantic's echo of the broken file's fields, including any inline secret — visible in Prefect state messages and logs.

**Recommendation**: specify `resolve_sync_instance` as its own tolerant walk (same glob, same exact-name match) that (1) treats each file's yaml/pydantic/OS error per-file, (2) when the failing file is the one whose `name` was requested (or the name is unreadable), raises `RunValidationError` naming the *logical name and file path only* — original parse detail discarded or redacted, never chained verbatim, (3) skips-with-a-logged-warning unrelated broken files so one bad neighbor doesn't take down every configuration. Extend T011's negative set with "unrelated config in the directory is invalid → requested valid name still resolves". Note data-model step 4's "on the matched config.yml" must be reworded — as written it describes behavior the reused lookup does not have.

## E7 — Recommended: the `changed ⇔ summary > 0` invariant is not sound against the engine's two divergent change sources

**Artifacts**: data-model §2 invariants; `contracts/run-result-and-errors.md` §1.3; spec Key Entities RunResult.

**Code evidence**: `_diff_to_rows` walks only the diff root's direct children and skips elements with empty `action` (`potenda/__init__.py:297-331`) — it does not recurse into nested child elements — while `Diff.has_diffs()` (which gates `ptd.sync(diff)` in the serial lifecycle, `cli.py:272-280`) is recursive. Generated models currently declare no `_children` (the `get_children` filter exists in `infrahub_sync/generator/__init__.py:103-117` but `templates/diffsync_models.j2` never emits it), so today's generated configs can't diverge — but hand-written adapters can declare diffsync children, and remote runs accept *any* installed configuration.

**Failure scenario**: a custom-model configuration whose only changes sit in nested child elements gives `has_diffs() == True` (sync runs, writes happen) with zero materialized plan rows. If `status` derives from `has_diffs()` ("applied") and `summary` from rows (all zero), `__post_init__` raises `ValueError` → the run is reported failed *after* successfully writing — the worst possible ordering.

**Recommendation**: derive `changed`, `status`, and `summary` from **one** source — the materialized plan rows — and record explicitly that nested-child-only changes are outside the feature's result fidelity (or make the row materializer recurse, which changes `plan.parquet` content and needs its own compatibility note). Add a unit test with a synthetic nested diff pinning whichever behavior is chosen.

## E8 — Must-Address: T029's parity test is unsatisfiable with the fixture pattern it names, and the summary-derivation mechanism must be pinned to in-memory rows

**Artifacts**: tasks T029 ("using the patched-factory fixture pattern from the existing CLI tests"), T027; `contracts/execution-surface.md` step 7; data-model §2 "Derivation".

**Code evidence**: the existing CLI-test fakes are `MagicMock` Potendas whose `write_plan` is a no-op and whose `diff()` returns a `MagicMock` (`tests/cache/test_cli_sync_cache.py:17-24`, `tests/test_cli_parallel.py:20-32`) — no `plan.parquet` is ever produced. `compute_plan_fingerprint(run_dir)` reads `<run_dir>/plan.parquet`; with that fixture pattern both sides of T029's comparison have nothing to read. Separately: if `execute_run` derives `summary` by *reading back* `plan.parquet`, the unmodified DBA-009 population (T027) fails for the same reason — the mocked `write_plan` writes nothing. The population survives only if derivation stays in-memory (`ptd._diff_to_rows(mydiff)` on a `MagicMock` iterates empty by MagicMock's `__iter__` magic — which is luck, not design).

**Failure scenario**: T029 implemented as instructed cannot compute either fingerprint; or T027 fails because `execute_run` tries to read a plan file the fakes never wrote — either way DBA-009's automated half is unsatisfiable as specified.

**Recommendation**: (1) pin the contract: `summary` is counted from the in-memory row list (`ptd._diff_to_rows(diff)` or an equivalent shared function over the diff), never by re-reading `plan.parquet`; (2) T029 must build a behavioral fake engine — a small typed `FakePotenda` whose `write_plan` really writes deterministic rows through `cache.parquet_io.write_plan` and whose `diff()` returns a minimal real-shaped diff — not the MagicMock pattern (this also aligns new tests with adapter-fake-over-mock discipline instead of extending `MagicMock` usage); (3) note in T027 that its guarantee depends on (1).

## E9 — Recommended: `uv.lock` is committed but no task regenerates or commits it when T012 edits `pyproject.toml`

**Artifacts**: tasks T012, T039–T041; `contracts/prefect-flow.md` §1.

**Code evidence**: `uv.lock` exists at the repo root; T012's verification (`uv sync --extra dev --extra prefect`) will implicitly re-resolve and rewrite it, and every later `uv run` keeps it dirty if uncommitted.

**Failure scenario**: T041's `git status --porcelain` clean-tree gate fails on the stray `uv.lock` diff (or, in a `--frozen`/CI context, sync fails because the lock predates the new extra). Additionally, T039's `uv run ty check .` only passes if the venv actually has prefect installed — otherwise the new `orchestration/` modules produce unresolved-import diagnostics — so the gate environment needs to be stated.

**Recommendation**: T012 explicitly runs `uv lock` (or accepts the implicit re-lock) and stages `uv.lock` alongside `pyproject.toml` in the same commit; T039 states it runs in the dev+prefect-synced environment.

## E10 — Recommended: the redaction candidate set omits non-Infrahub credential env vars the adapters actually read

**Artifacts**: research "Secret redaction" decision; `contracts/run-result-and-errors.md` §2; tasks T005.

**Code evidence**: adapters consume credentials from adapter-specific env vars, not only `INFRAHUB_API_TOKEN` — e.g. `NETBOX_TOKEN` (`infrahub_sync/adapters/netbox.py:43`), and equivalents in other adapters. DBR-006 routes *all* credentials through the runner environment, so for a NetBox-source configuration the token may exist **only** in env — outside both documented collection sources ("env `INFRAHUB_API_TOKEN`" + resolved-settings secret keys).

**Failure scenario**: a remote run of a netbox→infrahub configuration fails with an adapter message that embeds the NetBox token (e.g. echoed connection detail); the redactor has never collected that value; the sanitization obligation is violated for exactly the credential class DBR-006 pushed into the environment.

**Recommendation**: collect env values by name pattern — every environment variable matching `*_TOKEN`, `*_PASSWORD`, `*_SECRET`, `*_API_KEY` (documented in the contract) — plus the resolved-settings values already listed. Cheap, exact for the canary, and covers every adapter's documented env credential.

## E11 — Recommended: the qualified fixture is CWD-dependent and no artifact states the serve process's working-directory prerequisite

**Artifacts**: `contracts/prefect-flow.md` §3; quickstart Scenario 1; tasks T015/T018/T034.

**Code evidence**: `examples/custom_adapter/config.yml` uses repo-root-relative paths (`adapter: ./examples/custom_adapter/custom_adapter_src/custom_adapter.py:MockdbAdapter`, `db_path: ./examples/...`), and the cache root defaults to `Path.cwd()/.infrahub-sync-cache` (`infrahub_sync/cache/paths.py:26-44`). Quickstart only works because `serve` is implicitly started from the repo root (`INFRAHUB_SYNC_CONFIG_DIRECTORY="$PWD/examples"`).

**Failure scenario**: a README follower starts `python -m infrahub_sync.orchestration.serve` from any other directory; adapter import fails (`RunExecutionError`) or `.infrahub-sync-cache/` (and `artifact_path`) lands somewhere unexpected — DBA-011's clean-context walkthrough is where this bites first.

**Recommendation**: state the working-directory prerequisite in `contracts/prefect-flow.md` §3 and make T034's README name the exact directory to run each command from; optionally have serve log its CWD and cache root at startup for diagnosability.

## E12 — Recommended: fingerprint sort key crashes on null fields — in exactly the future row format the tie-breaker exists to protect

**Artifacts**: `contracts/run-result-and-errors.md` §3 algorithm; spec clarification #1; tasks T004/T009. (The clarified definition is a provisional checkpoint decision; this is a concrete latent-defect note, not a relitigation.)

**Code evidence**: `PLAN_SCHEMA` declares `attribute`, `new_value` (and others) nullable (`infrahub_sync/cache/parquet_io.py:26-38`). Today's writer emits `""` never `None` (`potenda/__init__.py:317-330`), so the algorithm is safe *today* — but a future attribute-level row format (the stated reason for the tie-breaker) would naturally carry `None`s, and Python's tuple sort raises `TypeError: '<' not supported between instances of 'NoneType' and 'str'` the moment two rows tie on the leading fields.

**Recommendation**: define null normalization in the algorithm (e.g. key uses `x if x is not None else ""` per field, or sort by the serialized-JSON form alone, which is total). One line in the contract now avoids a digest-definition change later; add a null-bearing case to T009's tie-breaker test.

## E13 — Nit: serve.py's "log/print one error line" collides with the repo's AST no-print gate

`tests/test_logging.py::test_no_print_calls_in_package` walks every `.py` under `infrahub_sync/` and fails on any bare `print()` (`tests/test_logging.py:67-79`); new modules are automatically in scope. `contracts/prefect-flow.md` §3's "log/print one error line" invites the failing choice. Specify the mechanism: `logging` (the lastResort handler writes ERROR to stderr without configuration) or `sys.stderr.write`, never `print()`.

## E14 — Nit: `RunResult` "immutability" does not cover the mutable `summary` dict

`frozen=True` prevents rebinding only; `result.summary["create"] += 1` mutates a "validated" result after `__post_init__` ran, silently breaking the cross-field invariants (and `frozen`'s generated `__hash__` raises on the dict field if anyone ever hashes a result). Consider `types.MappingProxyType` set via `object.__setattr__` in `__post_init__`, or a three-int frozen sub-record — either keeps DBA-010's immutability assertion honest.

## E15 — Nit: the lock-contention test needs a seam the contract doesn't expose

`pipeline_lock` accepts `timeout:` (`cache/locks.py:20`) but `execute_run` per contract calls it with defaults; T011's "held lock, shortened timeout" test therefore has no documented way to shorten the wait short of monkeypatching the surface's imported symbol. Either name that monkeypatch target as the sanctioned seam or add a private `_lock_timeout` parameter — decide now so the test doesn't sleep 60 s or improvise.

---

## Summary

| ID | Severity | One-line |
|---|---|---|
| E1 | Must-Address | `__cause__`-type CLI error mapping can't distinguish factory vs load `ValueError` and rewrites today's tracebacks/wording — DBR-009 unsatisfiable as specified |
| E2 | Must-Address | Contract step 6 ("any failure → wrap") and plan's "no broad except / declared-only catches" claim cannot both hold; narrow catches also leave `run.json` stuck at `running` |
| E3 | Must-Address | T028's in-process `sys.modules` assertion fails in the dev+prefect full suite (test_flow.py imports prefect at collection); needs a subprocess probe |
| E4 | Must-Address | Log bridge attaches a handler but never manages the `infrahub_sync` logger level — forwarding silently depends on Prefect/root logging config, contradicting clarification #4 and SC-002 |
| E5 | Must-Address | `raise ... from exc` re-leaks the unredacted cause via Prefect's traceback logging; redaction must cover the cause chain or the DBA-008 canary scan fails by design |
| E6 | Must-Address | `get_instance` eagerly validates every config.yml: one broken neighbor blocks all remote runs and pydantic's `input_value` echo leaks unresolvable-config secrets the redactor never collected |
| E7 | Recommended | `changed ⇔ summary>0` invariant unsound: recursive `has_diffs()` vs non-recursive `_diff_to_rows` can fail a run *after* successful writes on nested-children models |
| E8 | Must-Address | T029 parity/fingerprint test unsatisfiable with the named MagicMock fixture pattern (no plan.parquet ever written); summary derivation must be pinned to in-memory rows |
| E9 | Recommended | `uv.lock` is committed but T012 never regenerates/commits it — T041's clean-tree gate fails; T039's ty gate needs the prefect-synced env stated |
| E10 | Recommended | Redaction set omits adapter env credentials (e.g. `NETBOX_TOKEN`) that DBR-006 forces into the environment — collect by env-name pattern |
| E11 | Recommended | Qualified fixture and cache root are CWD-relative; the serve process's working directory is an unstated functional prerequisite (bites DBA-011) |
| E12 | Recommended | Fingerprint sort key raises `TypeError` on null fields — in the future row format the tie-breaker exists for; define null normalization now |
| E13 | Nit | serve.py "log/print" wording collides with the repo's AST no-print test; pin the mechanism |
| E14 | Nit | `RunResult.summary` is a mutable dict inside a "frozen" result; invariants can be broken post-validation |
| E15 | Nit | Lock-contention test has no sanctioned seam to shorten the 60 s timeout; name one |

**Blocking count: 7** (E1, E2, E3, E4, E5, E6, E8 — all Must-Address; no RETHINK: each defect has a local, behavior-preserving fix that leaves the settled D001–D008 decisions intact).
