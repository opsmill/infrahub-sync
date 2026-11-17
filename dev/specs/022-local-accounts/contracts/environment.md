# Contract: deployment variables

## New variables of the `sync-api` service

| Variable | Required | Rule |
| --- | --- | --- |
| `INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD` | yes | The password of the `admin` account. At least 12 characters. No default value. |
| `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN` | no | One API token of the `admin` account. At least 16 characters. It must not start with `ifs_pat_` or `ifs_ses_`. Empty or unset: `admin` has no token. |
| `INFRAHUB_SYNC_SESSION_IDLE_SECONDS` | no | The period without activity after which a session ends. Default `28800`. A whole number of at least 60. |

- `sync-api` applies the first two at each start (research R5). It then removes the password
  from its own process environment. The start also ends a refusal after repeated failures for
  `admin`.
- **First sign-in on the Compose deployment**: the `cli` service keeps no file, so the operator
  sets `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN` and gives the same value to
  `INFRAHUB_SYNC_API_TOKEN`.
- `sync-api` does not start, and names the variable, when the password is unset, empty or too
  short, when the token is set and too short or starts with a reserved prefix, or when the idle
  period is not a valid number.
  The message never contains the value.
- `docker-compose.yml` marks the password as required with the `${NAME:?message}` form, as it
  marks the removed variable today.

## Removed variable

`INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` is no longer read. A deployment that still sets it starts
normally and ignores it. The upgrade note in the documentation says to remove it.

## Unchanged variable

`INFRAHUB_SYNC_API_TOKEN`, read by the CLI and the Compose `cli` service. It holds the value of
`INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`, or a token that an account created.

## The development stack and the test deployment

- `development/docker-compose.yml` sets a fixed development password, and the token
  `infrahub-sync-dev-token`, so the documented development commands keep working.
- `infrahub_sync/testcontainers/` sets a test password and keeps `TEST_API_TOKEN` as the token
  of `admin`.
