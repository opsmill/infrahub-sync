# Quickstart: validate the conversion and the gate

Commands run from the repository root, in the development profile
(`uv sync --extra dev --extra prefect --extra service`).

## 1. Convert the v2 NetBox example (User Story 1)

```bash
mkdir -p /tmp/v2
git show 2.0.2:examples/netbox_to_infrahub/config.yml > /tmp/v2/config.yml
uv run infrahub-sync configs convert /tmp/v2/config.yml
```

Expected:

- The report lists `source.settings.token` and `destination.settings.token` with
  `INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN` and `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN`.
- `destination.settings.url` carries the `localhost` warning.
- Answer `n`: no `/tmp/v2/package.yml`, exit status 0.
- Run again with `--yes`: `/tmp/v2/package.yml` exists, exit status 0.
- `echo | uv run infrahub-sync configs convert /tmp/v2/config.yml --force` (no terminal, no
  `--yes`): nothing written, exit status 1.

Register it on a running preview stack (`uv run invoke preview.up`):

```bash
uv run infrahub-sync configs register /tmp/v2/package.yml
```

Expected: a configuration ID and version, with no edit to the file.

## 2. Refusals (User Story 2)

```bash
mkdir -p /tmp/v2c
git show 2.0.2:examples/custom_adapter/config.yml > /tmp/v2c/config.yml
uv run infrahub-sync configs convert /tmp/v2c/config.yml --yes; echo "exit=$?"
```

Expected: exit 1, no file, the reason cites INFP-654. Repeat with
`examples/infrahub_to_peering-manager/config.yml`: the reason says V3 writes only to Infrahub.

## 3. Every v2 example (SC-002)

```bash
uv run pytest tests/configuration/test_conversion_examples.py -q
```

Expected: 12 examples convert and their packages have no error finding; 2 are refused.

## 4. No credential value leaves the input (SC-003)

```bash
uv run pytest tests/configuration/test_conversion_secrets.py -q
```

## 5. The gate (User Story 3, SC-001)

Requires Docker, the pinned NetBox image and the preview stack, as in
[the `from-netbox` example check](../../../docs/docs/development/guidelines/testing-tiers.md).

```bash
uv run python .github/scripts/nightly_e2e.py suite v2-gate
```

Expected: `gate-result.json` with `passed: true`, and `counts` of 0 for `create`, `update`,
`left_out` and `problems`. Until spec 009 is merged, the suite stops and says that records left
out and problems cannot be counted on this build.
