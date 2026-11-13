# Quickstart results (T040)

Run on 2026-10-22 between 20:08 and 20:10 UTC, on branch `012-v2-configuration-conversion`,
from the repository root, in the development profile. Inputs were extracted from the `2.0.2`
tag with `git show` into a scratch directory instead of `/tmp`. No Sync API, preview stack or
NetBox was started.

## 1. Convert the v2 NetBox example

| Step | Result |
| --- | --- |
| Report lists `source.settings.token` and `destination.settings.token` with `INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN` and `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN` | Pass |
| `destination.settings.url` carries the loopback warning (`http://localhost:8000`) | Pass |
| Answer `n` at `Write …/package.yml? [y/N]` (through a pseudo-terminal with `script`) | Pass: "nothing written", exit 0, no `package.yml` |
| Run again with `--yes` | Pass: exit 0, `package.yml` exists |
| `echo \| … configs convert config.yml --force` (no terminal, no `--yes`) | Pass: "nothing written; run again with --yes", exit 1 |
| `configs register package.yml` | Not run: needs a running Sync API |

## 2. Refusals

| Input | Result |
| --- | --- |
| `examples/custom_adapter/config.yml` | Pass: exit 1, no file, the reason cites INFP-654 and 3.0 (a second reason: `mockdb` is not a bundled adapter) |
| `examples/infrahub_to_peering-manager/config.yml` | Pass: exit 1, no file, "V3 does not write to peeringmanager yet; this release writes only to Infrahub" |

## 3. Every v2 example

`uv run pytest tests/configuration/test_conversion_examples.py -q`: `17 passed in 0.29s`.

## 4. No credential value leaves the input

`uv run pytest tests/configuration/test_conversion_secrets.py -q`: `30 passed in 0.73s`.

## 5. The gate

Not run in this task. It needs Docker, the pinned NetBox and the preview stack (T033), and it
cannot pass until spec 009 is merged (T031).
