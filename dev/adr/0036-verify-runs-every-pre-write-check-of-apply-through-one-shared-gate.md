# 36. `verify` runs every pre-write check of `apply` through one shared gate

**Status**: Accepted
**Date**: 2026-11-14
**Source**: `dev/specs/archive/014-plan-approval/research.md` (R1, R2, R3, R4, R5)

## Context

A team reviews a saved plan and applies it later. Before this decision, `verify` checked the plan
files only: the checkpoint, the run binding, the plan checksum, the source snapshot, the
configuration version and the format. It built no adapter and contacted no system. `apply` then
ran more checks before its first write: the live destination schema against the plan's schema
fingerprint, the destination URL and branch, the write surface of the destination adapter, every
planned Infrahub field, and the import account. A plan that passed `verify` could therefore be
refused by `apply`, and the operator learned it only when they started the write.

The apply-side checks were inside `Potenda.apply_plan`, directly before the write loop.

## Decision

- **One gate.** `Potenda.check_plan` is the gate that runs before the first write. It verifies the
  plan's bytes, checks the write surface, parses the plan, refuses a format that cannot be applied,
  checks every planned Infrahub field and resolves the import account. It returns the parsed plan
  and the names of the checks that passed. It proves no write ownership and calls no write method
  of the destination. `Potenda.apply_plan` reads the plan once, calls the gate, and then runs the
  write loop on the same parsed plan.
- **The applier's check.** `PlanApplier.check_plan` compares the destination URL and branch that
  the plan recorded with the destination it built, then calls the engine's gate.
- **What the `verify` stage does.** The worker first runs the file checks, which build no adapter.
  For a run of a registered configuration it then runs `_check_destination` in
  `infrahub_sync/service/flow.py`, which takes the steps that `apply` takes before its loop, in
  the same order: the configuration binding, one read of the live destination schema and the
  comparison with the plan's schema fingerprint, the destination adapter, and
  `PlanApplier.check_plan`.
- **What `verify` leaves out.** It compares no operator checksum, because a `verify` request
  carries none. It does not check the confirmation that left-out records were reviewed, because
  only an apply request carries it; `apply_plan` checks that confirmation after the gate.
- **Named checks.** The stage result lists each check that passed, from one fixed list
  (`PreWriteCheck` in `infrahub_sync/plan/models.py`): `checkpoint`, `run_binding`,
  `plan_checksum`, `source_snapshot`, `config_version`, `format_version`, `destination_schema`,
  `destination_binding`, `write_surface`, `planned_fields`, `lineage`. A check that does not apply
  is not reported as passed: `planned_fields` for a destination other than Infrahub, `lineage` for
  a destination that records none, and `destination_binding` for a plan that recorded no
  destination or a destination that exposes none.
- **No write guard.** `verify` does not take the configuration write guard. It writes nothing, and
  its result describes the destination at the time `verify` read it.
- **Branch and credentials.** The Sync API passes the run's stored `destination_branch` to the
  stage. The worker resolves the destination's credentials only; `verify` never reads the source.
- **Runs without a registered configuration.** A run that the service cannot apply reports its
  file checks, and its result says that the destination was not checked
  (`destination_checked: false`).
- **No change of state.** `verify` does not change the run's phase and does not approve the plan.

## Consequences

- An operator who runs `verify` before `apply` views the same refusal, under the same check name,
  that `apply` would give at that moment. This holds because both call the same function. The
  parity cases in `tests/service/test_verify_destination.py` break one check at a time and run
  both stages on the same saved plan. They cover every name of the list except `checkpoint`.
- A failure names the check that failed. For a failure in the plan files, the name can be
  narrower than a name of the list: `torn_operations`, `last_changes` or `report`.
- `verify` now needs to reach the destination, with the credentials and network access that
  `apply` uses. A `verify` fails when the destination cannot be reached.
- A passing `verify` is not a promise. The destination can change between `verify` and `apply`,
  and `apply` runs every check again.
- A new pre-write check is added in the gate and to `PreWriteCheck`. Both stages then run it, and
  there is no second list to update.
- `verify` does not wait behind a long apply of the same configuration, and does not block one.

## Alternatives considered

- **Copy the checks into the `verify` stage.** Rejected: a later change to one list of checks
  could be left out of the other.
- **Run `apply` with a flag that skips the writes.** Rejected: a flag on the write path adds a
  branch that must never write, in the place where a mistake costs most. A separate gate function
  cannot reach the write loop.
- **Take the write guard during `verify`.** Rejected: `verify` would wait behind a long apply and
  would block writers while it reads, with no gain in safety.
