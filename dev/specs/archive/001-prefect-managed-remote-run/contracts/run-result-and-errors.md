# Contract: RunResult, Failure Classes, and the Canonical Plan Fingerprint

Owner: `infrahub_sync/execution.py` (result + errors) and
`infrahub_sync/cache/fingerprint.py` (fingerprint). This contract is the one future API
or orchestration briefs must extend rather than replacing with a second run lifecycle
(brief "Shared contract this brief owns").

## 1. RunResult

```python
Operation = Literal["plan", "sync"]
Status = Literal["planned", "applied", "no-change"]
ActionKey = Literal["create", "update", "delete"]


@dataclass(frozen=True, slots=True)
class RunResult:
    sync_name: str        # resolved logical configuration name
    operation: Operation  # requested remote operation
    run_id: str           # cache run id (YYYYMMDDTHHMM-<8 hex>); == Path(artifact_path).name
    status: Status        # planned (plan w/ changes) | applied (sync that wrote) | no-change
    changed: bool         # plan contained destination changes
    summary: Mapping[ActionKey, int]  # all three keys ALWAYS present, zero-filled; read-only
    artifact_path: str    # absolute runner-local run directory (run.json + plan.parquet)
```

Binding properties (all asserted by DBA-010's result-schema tests):

1. **Exact field set** — a successful result carries these seven fields and no others
   (`slots=True` prevents extra attributes; `dataclasses.fields()` count == 7).
2. **Immutability** — `frozen=True`; assignment after construction raises
   `dataclasses.FrozenInstanceError`. `summary` is additionally exposed as an
   immutable mapping: `__post_init__` wraps the constructed value in
   `types.MappingProxyType` via `object.__setattr__` (E14 — `frozen` prevents only
   rebinding; a plain dict could be mutated after validation, silently breaking the
   cross-field invariants). `result.summary["create"] += 1` raises `TypeError`.
3. **Invariants** (validated in `__post_init__`, raising `ValueError` on violation —
   an invariant violation is a bug, not a run failure):
   - `changed ⇔ status != "no-change" ⇔ sum(summary.values()) > 0`
   - `status == "planned"` ⇒ `operation == "plan"`
   - `status == "applied"` ⇒ `operation == "sync"`
   - `Path(artifact_path).is_absolute()` — the field crosses a process boundary and a
     remote caller cannot recover the serving process's cwd, so the "absolute
     runner-local run directory" wording is enforced here rather than assumed.
     `cache.paths.cache_root_for` absolutizes a relative `INFRAHUB_SYNC_CACHE_DIR`
     at the single derivation point (`absolute()`, not `resolve()`, so the final
     segment the next invariant compares against is preserved).
   - `run_id == Path(artifact_path).name`
   - `set(summary) == {"create", "update", "delete"}`
4. `summary` counts the run's plan rows per action — derived from the in-memory
   materialized row list (the same rows written to `plan.parquet`), never by
   re-reading the file (single-source derivation, execute_run step 7 / D009-adjacent
   E8 remediation).
5. The Prefect flow returns an **asdict-shaped dict** built by explicit seven-key
   construction — `{f.name: getattr(result, f.name) for f in dataclasses.fields(result)}`
   with `summary` replaced by `dict(result.summary)` — and **never**
   `dataclasses.asdict(result)`: `asdict()` deep-copies field values and the E14
   `summary` mappingproxy is not deep-copyable, so the call raises
   `TypeError: cannot pickle 'mappingproxy' object` (root-probed) and would fail
   every successful run at return time (X15). The reason is recorded inline at the
   construction site so nobody simplifies it back. The typed object itself remains
   the surface-level contract.

## 2. Failure classes

```python
class RunValidationError(Exception):
    """Request or configuration failure — every input-boundary refusal.

    Qualifying causes (exhaustive for this feature):
      - operation == "sync" and not confirm_writes   (message states that
        confirm_writes=true is required to run operation=sync)
      - sync_name matches no installed configuration (unknown, path-like, and
        command-like values all fail here — the value is never interpreted)
      - the matched configuration is unreadable or invalid (message names the
        configuration by logical name, may name the offending file, never prints
        configuration contents or credential values)
    """


class RunExecutionError(Exception):
    """Adapter or engine failure after validation passed.

    Qualifying causes include:
      - missing runner-environment credentials (raised at adapter initialization;
        the `run_remote_request` wrap message names the missing input — for the
        infrahub adapter the variables `INFRAHUB_ADDRESS` and
        `INFRAHUB_API_TOKEN`, by NAME only, never a value. D012 option A: the
        naming is added at the remote wrap boundary, NOT in
        `infrahub_sync/adapters/infrahub.py`, which this delivery leaves
        untouched so DBR-009's CLI byte-identity stays absolute. Attribution is
        per FAILING adapter, read from the `Error initializing <Name>Adapter:`
        prefix `utils.get_potenda_from_instance` always emits, and an adapter
        with no known variables gets no naming at all — a hint that names the
        wrong system's variables is worse than none. The hint is computed from the
        RAW detail and appended to the REDACTED message: it emits only variable
        NAMES, never detail text, while the `Error initializing <Name>Adapter:`
        prefix and the marker phrases it matches on are themselves redaction
        targets — computing it from the redacted detail is one collected substring
        away from silently dropping the hint)
      - unreachable source/destination systems
      - a nonexistent Infrahub branch (surfaces from the adapter/engine phase)
      - pipeline-lock contention (existing 60 s acquisition timeout elapsed — bounded,
        never a hang)
      - adapter import failure
    """
```

Shared obligations (both classes):

- **Wrap locus (D009)**: these classes are raised by the surface's own validation
  (`resolve_sync_instance`, `execute_run` step 1) and by the sanitize-and-wrap
  boundary in `run_remote_request` — never by `execute_run`'s lifecycle handling,
  which preserves the `9edc1bc` CLI pattern and re-raises original types
  (contracts/execution-surface.md "Failure semantics").
- **Specific**: the message names the failing input or stage and the underlying cause.
- **Sanitized — over the WHOLE cause chain (E5)**: before raising, the message passes
  value-based redaction — every occurrence of a configured secret value is replaced
  with `***`. Because Prefect logs a failed flow's exception WITH traceback, and a
  traceback renders every `__cause__`/`__context__` message, redacting only the
  wrapper message is insufficient: at the wrap point the original cause is either
  rebuilt as a sanitized copy (e.g. re-chained from an exception constructed with
  `redact(str(exc))`) or suppressed via `__suppress_context__` with its redacted
  text inlined into the wrapper message. Binding property: a full traceback
  rendering of the raised error (`traceback.format_exception(...)`) must contain
  **no unredacted original message** anywhere in the chain. Secret values are
  collected from two sources, deliberately wider than the key names an adapter
  happens to document, because a value missed here reaches a remote caller verbatim:

  1. **The runner environment** (E10: DBR-006 routes adapter credentials such as
     `NETBOX_TOKEN` into the runner environment, outside the resolved-settings
     source). A variable's NAME qualifies when it *contains* `TOKEN`, `PASSWORD`,
     `PASSWD`, `SECRET`, `CREDENTIAL`, or `APIKEY` — substring matching, so the bare
     `TOKEN` / `PASSWORD` names the shipped `genericrestapi` adapter reads by default
     (`adapters/genericrestapi.py:72,90`) and `AWS_SECRET_ACCESS_KEY` both qualify —
     or *ends with* `_KEY` / `_AUTH`, or equals `KEY` / `AUTH` / `INFRAHUB_API_TOKEN`.
     `KEY` and `AUTH` are matched at a name boundary rather than as substrings so
     unrelated variables (`KEYCHAIN`, `SSH_AUTH_SOCK`) are not collected.
     Additionally, the **userinfo of every environment value** is collected
     regardless of the variable's name: the runner-side endpoint variables
     (`NETBOX_ADDRESS`, `PROM_URL`, `CISCO_APIC_URL`, …) are how every adapter learns
     where to connect (`adapters/netbox.py:42`, `adapters/prometheus.py:388`,
     `adapters/aci.py:208`), their names are NOT credential-shaped, and a password
     embedded in one otherwise reaches a remote caller verbatim in the first
     connection-refused message. Userinfo still has to clear the length floor below,
     so the name-blind scan over-collects almost nothing.
  2. **The resolved configuration's settings** — `source`, `destination`, AND
     `store` — walked **recursively**, not just at the top level. A key qualifies
     when its name *contains* `token`, `password`, `passwd`, `secret`, `credential`,
     `apikey`, or `authorization`, or *ends with* `_key` / `_auth`, or equals `key` /
     `auth` — the same three boundary rules the environment half uses, for the same
     reason. This covers `api_key` and `secret_key` by suffix, ipfabric's bare `auth`
     by exact name, a nested `headers.authorization` or `params.api_key`, and
     `store.settings.password`. `key` and `auth` are matched at a name boundary
     rather than as bare substrings because the bare forms sweep in the shipped
     non-secret keys `response_key_pattern` and `auth_method`
     (`adapters/genericrestapi.py:71,190`) whose ordinary values (`objects`,
     `api-key`, `x-auth-token`) then shred the very operator diagnostics this
     boundary exists to keep readable — `Authentication method '***' requires a valid
     API token!`. The qualifying context is inherited by everything nested beneath a
     matched key (a `credentials:` block of plain-named entries), which is also what
     makes narrow matching load-bearing: one over-broad match turns every ordinary
     word below it into a redaction target. Values are coerced from `str`/`int`/
     `float`/`Decimal`, so a non-string credential is collected too, while an object
     with a raising `__str__` cannot escape the public collector. The walk is
     cycle-guarded (on `(id(container), context)`) and depth-capped at 64:
     `yaml.safe_load` builds self-referential structures from aliases
     (`token: &A\n  nested: *A`), and an unbounded walk turns that into a
     `RecursionError` failing EVERY run of that configuration. Additionally, the
     **userinfo of every URL-shaped value** is collected (`settings.url =
     "http://admin:pw@host/api"` hides a credential next to an already-redacted
     sibling `token`), and every `*_env_vars` list whose key itself qualifies
     (`token_env_vars`, `password_env_vars` — `adapters/genericrestapi.py:59-92`,
     `adapters/peeringmanager.py:31-34`) contributes the VALUES of the environment
     variables it names, never the names themselves.

  Collected values shorter than **6 characters** are dropped. A short value — the
  `1` of a `SKIP_TOKEN=1` feature flag — would turn redaction into a substring
  shredder over unrelated text (observed: `within 6***.0 seconds`), and no real
  credential is that short, so dropping it cannot hide one.
  The same obligation covers forwarded log records (verified by DBA-008's canary scan:
  seeded canary values appear nowhere in flow parameters, results, Prefect-visible
  logs, or example request bodies).
- **No successful result**: a raise means no `RunResult` exists for the run; the
  Prefect flow run ends FAILED with the sanitized message as its state message; any
  already-created `run.json` is left with `status="failed"` (today's behavior).

Validation locus (spec Key Entities): `operation` membership is enforced by Prefect
parameter typing before the flow body runs (HTTP 409 at run creation — probe d₁);
`confirm_writes` gating and `sync_name` resolution are enforced inside the shared
surface so the same refusals apply to the CLI seam and any programmatic caller.

## 3. Canonical plan fingerprint

```python
# infrahub_sync/cache/fingerprint.py
PLAN_FINGERPRINT_FIELDS = ("action", "resource", "source_id", "attribute", "new_value")


def compute_plan_fingerprint(run_dir: Path) -> str:
    """SHA-256 hex digest of the canonicalized plan rows in <run_dir>/plan.parquet.

    Algorithm (spec clarification #1, binding):
      1. Read plan.parquet; project exactly PLAN_FINGERPRINT_FIELDS per row.
      2. Serialize each row as compact sorted-key JSON:
         json.dumps(row, sort_keys=True, separators=(",", ":"))
      3. Sort rows by (resource, source_id, action, attribute), using the row's full
         serialized form as the final tie-breaker. Null normalization (binding, E12):
         each field in the SORT KEY (including the tie-breaker's leading fields)
         normalizes None to "" (`x if x is not None else ""`) so the tuple sort stays
         total when a future row format carries nulls (PLAN_SCHEMA already declares
         `attribute`/`new_value` nullable); the SERIALIZED form is unchanged (None
         still serializes as JSON `null`). (The current plan writer emits one row per
         element, so source_id is unique within resource and ties cannot occur; the
         tie-breaker keeps the digest total under any future row format.)
      4. Join with "\n", encode UTF-8, return hashlib.sha256(...).hexdigest().

    Timestamps, run identifiers, and filesystem paths are excluded by construction, so
    reset-fixture runs compare equal (SC-007).
    """
```

One shared helper computes it for **both** sides of the DBA-009/SC-007 comparison
(CLI `diff` run dir vs remote `plan` run dir). Tests may not reimplement the algorithm.

## 4. Verification map

| Assertion | Test level |
|---|---|
| Field set + immutability + invariants | Unit (`tests/test_execution_surface.py`) — DBA-010 |
| Validation refusals (unconfirmed sync; unknown / `../` / absolute / separator / flag-like / shell-metacharacter sync_name) before adapter construction, no out-of-directory read, no subprocess | Parametrized unit tests with filesystem + subprocess spies — DBA-006/007, SC-004 |
| Execution failure (unreachable system / missing credential) → RunExecutionError, sanitized | Unit + one flow-level test — DBA-010 |
| Canary values absent everywhere Prefect-visible | Canary scan test — DBA-008, SC-005 |
| CLI diff ≡ remote plan (status, changed, summary, fingerprint) on reset fixtures | Paired comparison — DBA-009, SC-007 |
| Fingerprint determinism, ordering independence, field exclusion | Unit (`tests/test_plan_fingerprint.py`) |
