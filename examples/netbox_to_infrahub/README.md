# NetBox to Infrahub example

The package maps the public NetBox demo into the Infrahub schema library. Before running
it, follow the [NetBox demo tutorial](../../docs/docs/tutorials/netbox-demo-to-infrahub.mdx)
through **Register the configuration package**. The tutorial is the setup authority for
the schema-library source revision, a current `nbt_...` token, and the required `pynetbox`
worker dependency.

Use a fresh Infrahub instance for the tutorial schema. Loading the schema library over an
incompatible schema can fail when existing kinds define different relationships.

To run it against a local NetBox with the official demo data, follow
[Run from source](../../docs/docs/development-stack.mdx#run-from-source) instead. That
path writes a copy of this package with the addresses the local worker container uses.

The worker uses the two URLs in this package, `https://demo.netbox.dev` and
`http://localhost:8000`. Environment variables such as `NETBOX_URL` and
`INFRAHUB_ADDRESS` do not change them. To use other addresses, edit the URLs in a copy
of the package before you register it, or register a new version of it with
`configs version`. Only the two tokens, `INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN` and
`INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN`, come from the worker's environment.

Connect to a Sync service whose worker has `pynetbox` installed, then register, plan,
review, and apply:

```bash
uv run infrahub-sync configs register examples/netbox_to_infrahub/package.yml \
  --reason "register NetBox demo import"
uv run infrahub-sync plan --config-id <config-id> --version <version> \
  --branch netbox-import --reason "review NetBox demo import"
uv run infrahub-sync runs plan <run-id> --detail
uv run infrahub-sync apply <run-id> \
  --expected-checksum <plan-checksum-from-review> \
  --branch netbox-import --reason "apply reviewed NetBox plan"
```

## Device status translation

NetBox has seven device statuses. The `DcimDevice.status` attribute of the schema library has
five choices: `active`, `reserved`, `provisioning`, `maintenance` and `deprecated`. A
`device_status` transform in both `DcimDevice` entries of `package.yml` (racked and unracked
devices) translates each NetBox status to one of these choices:

| NetBox status | Infrahub status |
|---------------|-----------------|
| `active` | `active` |
| `planned` | `provisioning` |
| `staged` | `provisioning` |
| `offline` | `maintenance` |
| `failed` | `maintenance` |
| `inventory` | `reserved` |
| `decommissioning` | `deprecated` |

A status that is not in this table is kept as it is. The plan then lists the device as a
`dropdown_value_not_in_choices` problem and leaves it out, and the plan still finishes.

These pairs are one reading of the statuses. Before you import devices, change them in the
`device_status` transform of `package.yml` to what each status means for your team. The
transform is written once and reused by the second `DcimDevice` entry, and `config.yml` holds
the same configuration.

## Device data that the mapping writes and device data that a plan lists as problems

NetBox accepts three kinds of device data that the schema library does not accept
([#374](https://github.com/opsmill/infrahub-sync/issues/374)). The mapping handles them as
follows:

- A device status that is not a choice, such as `planned`: the mapping writes the device with
  the translated status from the table above.
- A device at a rack position that is not a whole number, such as `2.5`: the plan lists the
  device as a `wrong_value_type` problem and leaves it out. Infrahub stores a rack position as
  a whole number, and rounding would write a position the device does not have.
- Two devices with the same name at two sites or racks: the mapping keeps them apart by
  `location` and `name`, but Infrahub identifies a `DcimDevice` by its name only (human-friendly
  ID `name__value` in the schema library). When neither device exists in Infrahub yet, the plan
  lists both as one `destination_identity_collision` problem and leaves both out.

Records that depend on a left-out device through a required relationship, such as its
interfaces, are left out too. Review the problems and the left-out records, then confirm them
when you apply:

```bash
uv run infrahub-sync runs plan <run-id> --problems
uv run infrahub-sync runs plan <run-id> --left-out
uv run infrahub-sync apply <run-id> --expected-checksum <plan-checksum-from-review> \
  --confirm-left-out --branch netbox-import --reason "apply reviewed NetBox plan"
```

`sync` stops after a plan that leaves out records and does not apply it.

How these statements are verified:

- Unit tests on this example's mapping, in `tests/plan/test_netbox_device_status.py`, verify the
  status translation. They check that each of the seven NetBox statuses becomes one of the
  choices of the pinned schema snapshot in `tests/data/nightly_schema/`, and that an unknown
  status becomes a `dropdown_value_not_in_choices` problem in a plan.
- Unit tests on the shared problem fixture, in `tests/plan/test_problems.py`, verify the rack
  position and the device name cases with records of the same shape:
  `test_wrong_value_type` (position `2.5`) and `test_destination_identity_collision` (two
  devices whose mapping identities differ and whose names are equal).
- The [`from-netbox` example check](../../docs/docs/development/guidelines/testing-tiers.md#the-from-netbox-example-check)
  ran with this mapping on 2026-11-12, on the pinned `demo` dataset. #374 observed the three
  kinds on the public NetBox demo; the pinned dataset gives these results:
    - All 72 devices have the status `active`, so the status translation changes no value.
    - No device has a rack position that is not a whole number.
    - One name is used by three devices at three sites: `PP:MDF`. The mapping does not read
      these devices, because both `DcimDevice` entries leave out every device whose name
      contains `PP:`. The plan neither writes nor lists them.

  The plan has 1,688 operations, no problems and no left-out records. So on this dataset only
  the unit tests above verify the rack position and the same-name cases.

## Current limitations

- The public NetBox demo changes over time, and any visitor can edit it. A current token
  and the data assumed by a bounded integration test can expire independently of this
  repository.
- Infrahub identifies VLANs, devices, and racks by name, while NetBox names are unique
  only within a group or site. The mapping therefore prefixes VLAN and rack names with
  their group or site name, and skips patch panels (devices whose name contains `PP:`).
  The [NetBox demo tutorial](../../docs/docs/tutorials/netbox-demo-to-infrahub.mdx#verify-the-imported-data)
  explains these rules.
- A plan records one delete of Infrahub's built-in `default` IP namespace when no NetBox
  VRF has that name. Apply never executes it.

On 2026-09-29, against a local NetBox with the official v4.7 demo data, a plan held
1,687 creates across 20 kinds and the one recorded delete. After apply, every kind's
object count on the branch matched the plan, and a second plan held only the recorded
delete.

The bounded live acceptance test in
`tests/integration/test_saved_plan_apply_integration.py` exercises the internal worker
execution path. It does not restore a local public CLI mode.
