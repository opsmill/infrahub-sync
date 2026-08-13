# NetBox to Infrahub example

The `from-netbox` configuration maps the public NetBox demo into the Infrahub
schema library. Before running it, follow the
[NetBox demo tutorial](../../docs/docs/tutorials/netbox-demo-to-infrahub.mdx)
through **Generate the sync code**. The tutorial is the setup authority for the
exact schema-library source revision, a current `nbt_...` token, and the required `pynetbox`
adapter dependency.

The configuration does not target the similarly named
`models/examples/netbox/netbox.yml` file in the Infrahub source repository. That file
defines different destination kinds, so `generate` correctly reports `LocationSite`,
`DcimDevice`, `IpamPrefix`, and related mapped kinds as missing when it is loaded instead.

Use a fresh Infrahub instance for the tutorial schema. Loading the schema library
over another schema, including `demo-fabric`, can fail when existing kinds define
different relationships.

When reviewing a repository checkout, keep the checkout installed and add only
the adapter dependency after `uv sync`:

```bash
uv pip install pynetbox
```

Then generate, plan, review, and apply with the NetBox example:

```bash
uv run infrahub-sync generate --name from-netbox --directory examples/
uv run infrahub-sync diff --name from-netbox --directory examples/
uv run infrahub-sync diff --name from-netbox --directory examples/ --from-plan <run-id>
uv run infrahub-sync apply --name from-netbox --directory examples/ --run-id <run-id> \
  --expected-checksum <plan-checksum-from-review>
```

## Current limitations

- The public NetBox demo changes over time. A current token and the data assumed
  by a bounded integration test can expire independently of this repository.
- With the current schema-library revision, saved-plan operations for
  `IpamPrefix` and `IpamIPAddress` do not supply the `ip_namespace` component of
  the destination identity. Each affected operation is refused before it is
  written, although earlier operations in the same apply may already have run.
- `LocationRack` uses `name` plus `site` in this configuration, while the current
  destination schema keys racks by `name` alone. Reapplying or replanning racks
  is not convergent when different sites contain racks with the same name.

For the bounded NetBox acceptance path, run
`tests/integration/test_saved_plan_apply_integration.py`. It derives a ten-kind
slice from this configuration, excludes the unsupported IPAM and VLAN
relationships, and checks plan, review, apply, refusal, and convergence behavior
against live services.
