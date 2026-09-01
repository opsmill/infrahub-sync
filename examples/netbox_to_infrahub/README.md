# NetBox to Infrahub example

The package maps the public NetBox demo into the Infrahub schema library. Before running
it, follow the [NetBox demo tutorial](../../docs/docs/tutorials/netbox-demo-to-infrahub.mdx)
through **Register the configuration package**. The tutorial is the setup authority for
the schema-library source revision, a current `nbt_...` token, and the required `pynetbox`
worker dependency.

Use a fresh Infrahub instance for the tutorial schema. Loading the schema library over an
incompatible schema can fail when existing kinds define different relationships.

Connect to a Sync service whose worker has `pynetbox` installed, then register, plan,
review, and apply:

```bash
uv run infrahub-sync configs register examples/netbox_to_infrahub/package.yml \
  --reason "register NetBox demo import"
uv run infrahub-sync diff --config-id <config-id> --version <version> \
  --reason "review NetBox demo import"
uv run infrahub-sync runs plan <run-id> --detail
uv run infrahub-sync apply <run-id> \
  --expected-checksum <plan-checksum-from-review> \
  --reason "apply reviewed NetBox plan"
```

## Current limitations

- The public NetBox demo changes over time. A current token and the data assumed by a
  bounded integration test can expire independently of this repository.
- With the current schema-library revision, saved-plan operations for `IpamPrefix` and
  `IpamIPAddress` do not supply the `ip_namespace` component of the destination identity.
  Each affected operation is refused before it is written, although earlier operations in
  the same apply may already have run.
- `LocationRack` uses `name` plus `site` in this configuration, while the current
  destination schema keys racks by `name` alone. Reapplying or replanning racks is not
  convergent when different sites contain racks with the same name.
- After a write creates physical interfaces bundled into a LAG, a later plan can fail while
  loading the destination. The [tutorial troubleshooting section](../../docs/docs/tutorials/netbox-demo-to-infrahub.mdx#diff-fails-after-a-sync-that-wrote-interfaces)
  records the error, cause, and recovery procedure.

The bounded live acceptance test in
`tests/integration/test_saved_plan_apply_integration.py` exercises the internal worker
execution path. It does not restore a local public CLI mode.
