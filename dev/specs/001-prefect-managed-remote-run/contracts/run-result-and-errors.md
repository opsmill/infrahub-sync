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
    summary: dict[ActionKey, int]  # all three keys ALWAYS present, zero-filled
    artifact_path: str    # absolute runner-local run directory (run.json + plan.parquet)
```

Binding properties (all asserted by DBA-010's result-schema tests):

1. **Exact field set** — a successful result carries these seven fields and no others
   (`slots=True` prevents extra attributes; `dataclasses.fields()` count == 7).
2. **Immutability** — `frozen=True`; assignment after construction raises
   `dataclasses.FrozenInstanceError`.
3. **Invariants** (validated in `__post_init__`, raising `ValueError` on violation —
   an invariant violation is a bug, not a run failure):
   - `changed ⇔ status != "no-change" ⇔ sum(summary.values()) > 0`
   - `status == "planned"` ⇒ `operation == "plan"`
   - `status == "applied"` ⇒ `operation == "sync"`
   - `run_id == Path(artifact_path).name`
   - `set(summary) == {"create", "update", "delete"}`
4. `summary` counts the run's plan rows (`plan.parquet`) per action.
5. The Prefect flow returns `dataclasses.asdict(RunResult)`; the typed object itself is
   the surface-level contract.

## 2. Failure classes

```python
class RunValidationError(Exception):
    """Request or configuration failure — every input-boundary refusal.

    Qualifying causes (exhaustive for this preview):
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
      - missing runner-environment credentials (raised at adapter initialization,
        naming the missing input)
      - unreachable source/destination systems
      - a nonexistent Infrahub branch (surfaces from the adapter/engine phase)
      - pipeline-lock contention (existing 60 s acquisition timeout elapsed — bounded,
        never a hang)
      - adapter import failure
    """
```

Shared obligations (both classes):

- **Specific**: the message names the failing input or stage and the underlying cause
  (original exceptions are chained via `raise ... from exc`).
- **Sanitized**: before raising, the message passes value-based redaction — every
  occurrence of a configured secret value is replaced with `***`. Secret values are
  collected from: the runner-environment credential variables (at minimum
  `INFRAHUB_API_TOKEN`) and the values of secret-valued keys (`token`, `password`,
  `secret`, `api_key`) in the resolved configuration's source/destination settings.
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
         serialized form as the final tie-breaker. (The current plan writer emits one
         row per element, so source_id is unique within resource and ties cannot
         occur; the tie-breaker keeps the digest total under any future row format.)
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
