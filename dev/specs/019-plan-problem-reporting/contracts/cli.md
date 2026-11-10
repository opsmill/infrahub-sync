# CLI contract

## Plan summary

`infrahub-sync plan` and `infrahub-sync runs plan <run_id>` print, after the counts by action:

```text
left_out: 6 (with a problem: 1, depending on one: 5)
problems: 1
kept_without_relationship: 0
```

When the run had no destination schema snapshot:

```text
Dropdown values were not checked: no destination schema snapshot in this run
```

`infrahub-sync plan` then prints the first 20 problems and the command that lists the rest:
`infrahub-sync runs plan <run_id> --problems`.

## Problem, left-out and kept lists (decision 8)

```text
infrahub-sync runs plan <run_id> --problems [--type <problem_type>] [--kind <kind>]
infrahub-sync runs plan <run_id> --left-out [--kind <kind>]
infrahub-sync runs plan <run_id> --kept [--kind <kind>]
```

Each option reads every page and prints one line per entry:

```text
dropdown_value_not_in_choices  DcimDevice  location=r1 name=rtr1  status 'planned' is not one of: active, maintenance, provisioning
depends_on_left_out  InterfacePhysical  device=rtr1 name=eth0  depends on DcimDevice location=r1 name=rtr1 through device
peer_not_found_in_source  IpamIPAddress  address=10.0.0.1/32  vrf refers to IpamVRF source ID 17, which is not in the source
```

`--problems`, `--left-out`, `--kept` and `--detail` are exclusive. `--type` needs `--problems`.
No `runs problems`, `runs left-out` or `runs kept` command exists.

## Apply a plan that leaves out records

```text
infrahub-sync apply <run_id> --expected-checksum <hex> --confirm-left-out
```

- `--confirm-left-out` is a flag without a value; it sends `confirm_left_out: true`.
- Without it, on a plan with records left out, the command exits with code 1 before any write and
  prints `apply refused: this plan leaves out 12 records; confirm with --confirm-left-out`.
- On a plan with nothing left out, the flag is accepted and ignored.
- A `sync` run that stopped after its plan is applied the same way.

## Cause of a failed or stopped run

`plan`, `verify`, `apply`, `sync` and `runs show` keep the lines printed today and add the new
fields between `<stage> failed:` and `hint:`. Each value goes through the existing `_display`
guard.

```text
apply failed: OperationApplyFailedError
failure type: destination_rejected_write (destination infrahub)
cause: Infrahub rejected a write to DcimDevice attribute status (ATTRIBUTE_CONSTRAINT_VIOLATION)
Infrahub message: planned is not a valid value for status
written: 692 of 2291 operations
failed operation: op_518aada155d790ad update DcimDevice location=r1 name=rtr1 (may have written part of its change)
hint: Fix the cause, then create and review a new plan with `infrahub-sync plan`.
```

A `sync` that stops after its plan ended without a stage failure record. The CLI reads the
`failure` object of the stage result in the run's `results` (the field the worker's last log line
also uses) and prints:

```text
sync stopped: left_out_review_required (12 records left out)
hint: Review the plan with `infrahub-sync runs plan <run_id> --problems` and `--left-out`, then apply it with `infrahub-sync apply <run_id> --expected-checksum <hex> --confirm-left-out`.
```

The exit code is 1 for a failed or stopped run, as today.

## Configuration validation

```text
infrahub-sync configs validate <existing arguments> --branch <name>
```

`--branch` is the only new option; the existing arguments of `configs validate`
(`infrahub_sync/cli.py:600`) stay as they are.

Prints each finding with its code, location and message; the exit code is 1 when a finding has
severity error.
