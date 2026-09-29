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
`configs version`. Only the two tokens, `NETBOX_TOKEN` and `INFRAHUB_API_TOKEN`, come
from the worker's environment.

Connect to a Sync service whose worker has `pynetbox` installed, then register, plan,
review, and apply:

```bash
uv run infrahub-sync configs register examples/netbox_to_infrahub/package.yml \
  --reason "register NetBox demo import"
uv run infrahub-sync diff --config-id <config-id> --version <version> \
  --branch netbox-import --reason "review NetBox demo import"
uv run infrahub-sync runs plan <run-id> --detail
uv run infrahub-sync apply <run-id> \
  --expected-checksum <plan-checksum-from-review> \
  --branch netbox-import --reason "apply reviewed NetBox plan"
```

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
