# CLI contract for spec 009

The CLI calls only the Sync API routes in [sync-api.md](sync-api.md).

## Plan summary

`infrahub-sync diff` and `infrahub-sync runs plan <run_id>` print, after the counts by action:

```text
left_out: 6 (with a problem: 1, depending on one: 5)
problems: 1
kept_without_relationship: 0
```

Then, only when they apply (review #3 and #8, 2026-10-17), one line per kind whose deletes the
plan withholds, and one line when the run had no destination schema snapshot:

```text
deletes not computed for IpamVRF: 1 records without identity
Dropdown values were not checked: no destination schema snapshot in this run
```

A `sync` that stops after its plan prints the next action with this run's ID and plan checksum:
review the plan with `runs plan` and `runs left-out`, then `apply <run_id> --expected-checksum
<hex> --confirm-left-out`. It never says to run the stage again.

## Problem, left-out and kept lists

```text
infrahub-sync runs problems <run_id> [--type <problem_type>] [--kind <kind>]
infrahub-sync runs left-out <run_id> [--kind <kind>]
infrahub-sync runs kept <run_id> [--kind <kind>]
```

Each command reads every page and prints one line per entry, for example:

```text
dropdown_value_not_in_choices  DcimDevice  location=dc1 name=rtr1  status='planned' is not one of: active, deprecated, maintenance, provisioning, reserved
depends_on_left_out  InterfacePhysical  device=rtr1 name=eth0  depends on DcimDevice location=dc1 name=rtr1 through device
peer_not_found_in_source  IpamIPAddress  address=10.0.0.1/32  vrf refers to IpamVRF source ID 17, which is not in the source
destination_identity_ambiguous  LocationSite  name=twin  2 Infrahub objects hold this identity: node-1, node-2; no operation targets it
transform_failed  InterfaceL2  source_id=77  the transform transforms[0] of l2_mode failed for this record; the record is not loaded
```

`diff` prints the first 20 problems and the command that lists the rest.

## Apply a plan that leaves out records

```text
infrahub-sync apply <run_id> --expected-checksum <hex> --confirm-left-out
```

- `--confirm-left-out` is a flag without a value. It sends `confirm_left_out: true`.
- When the plan leaves out records and `--confirm-left-out` is missing, the command exits with
  code 1 before any write and prints
  `apply refused: this plan leaves out 12 records; confirm with --confirm-left-out`.
- When the plan leaves out no records, the option is not needed, and it is accepted and ignored
  when it is given.
- A run that stopped after its plan (a `sync` with records left out) is applied the same way.

## Cause of a failed or stopped run

`diff`, `sync`, `apply` and `runs show` print the cause when the run failed or stopped,
replacing today's `<stage> failed: <ClassName>` line (`cli.py:730-745`). The CLI prints only
the fields of the run record's `failure` object; it adds no text of its own from an error:

```text
apply stopped: Infrahub rejected a write (ATTRIBUTE_CONSTRAINT_VIOLATION)
  node kind: DcimDevice   attribute: status
  Infrahub message: planned is not a valid value for status
  written: 692 of 2291 operations
  failed operation: op_518aada155d790ad update DcimDevice name=rtr1 (may have written part of its change)
  next: fix the cause, then create and review a new plan with `infrahub-sync diff`
```

```text
plan failed: the source (netbox) did not answer in time
plan failed: the destination (infrahub) rejected the token
plan failed: the transform of field status failed for DcimDevice source_id=42
```

The exit code stays 1 for a failed or stopped run.

## Validation with the destination schema

```text
infrahub-sync configs validate <config_id> <version> --branch <name>
```

Prints the findings of [sync-api.md](sync-api.md#configuration-validation).
