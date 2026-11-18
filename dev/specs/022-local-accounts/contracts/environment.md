# Contract: deployment variables

This contract was brought in line with the implementation on 2026-11-18.

## New variables of the `sync-api` service

| Variable | Required | Rule |
| --- | --- | --- |
| `INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD` | yes | The password of the `admin` account. 12 to 1,024 characters of valid UTF-8 text. No default value. |
| `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN` | no | One API token of the `admin` account. At least 16 characters. It must not start with `ifs_pat_` or `ifs_ses_`. Empty or unset: `admin` has no token. |
| `INFRAHUB_SYNC_SESSION_IDLE_SECONDS` | no | The period without activity after which a session ends. Default `28800`. A whole number of at least 60. Empty or unset: the default. |

- `sync-api` applies the first two at each start (research R5). It then removes the password
  from its own process environment. The start also ends a refusal after repeated failures for
  `admin`.
- **First sign-in on the Compose deployment**: the `cli` service keeps no file, so the operator
  sets `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN` and gives the same value to
  `INFRAHUB_SYNC_API_TOKEN`.
- `sync-api` does not start, and names the variable, in each of these cases:
    - the password is unset or empty, shorter than 12 characters, longer than 1,024 characters,
      or not valid UTF-8 text;
    - the token is set and shorter than 16 characters, or starts with `ifs_pat_` or `ifs_ses_`;
    - the idle period is not a whole number of at least 60.

  The message never contains the value.
- `docker-compose.yml` marks the password as required with the `${NAME:?message}` form, as it
  marked the removed variable.

Changed during implementation: the maximum of 1,024 characters and the UTF-8 rule were added to
the password after the review. No sign-in can succeed with a longer password, because a sign-in
refuses it before any check.

## Removed variable

`INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` is no longer read. A deployment that still sets it starts
normally and ignores it. The upgrade note in the documentation says to remove it.

## Unchanged variable

`INFRAHUB_SYNC_API_TOKEN`, read by the CLI and the Compose `cli` service. It holds the value of
`INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`, or a token that an account created.

## The development stack and the test deployment

- `development/docker-compose.yml` sets the development password `infrahub-sync-dev-password`
  and the token `infrahub-sync-dev-token`, so the documented development commands keep working.
  The variables `ADMIN_ACCOUNT_PASSWORD` and `ADMIN_ACCOUNT_TOKEN`, when the developer's shell
  sets them, replace those values.
- `infrahub_sync/testcontainers/` sets a test password and keeps `TEST_API_TOKEN` as the token
  of `admin`.

## The Kubernetes chart

The chart is in the repository `opsmill/infrahub-helm`, and
`docs/docs/kubernetes-deployment.mdx` describes it. It passes the variables above to the Sync API
container only:

| Chart value | Variable | Rule |
| --- | --- | --- |
| `credentials.adminAccountPassword` | `INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD` | Required when `credentials.existingSecret` is empty. The chart refuses to render a value shorter than 12 characters. |
| `credentials.adminAccountToken` | `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN` | Optional, empty by default; empty leaves the key out of the Secret. The chart refuses to render a value shorter than 16 characters or one that starts with `ifs_pat_` or `ifs_ses_`. |
| `settings.sessionIdleSeconds` | `INFRAHUB_SYNC_SESSION_IDLE_SECONDS` | Empty by default, so the Sync API's 28800 seconds apply. Set only when given. The chart refuses to render a value that is not a whole number of at least 60. |

With `credentials.existingSecret`, the Secret holds the keys `INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD`
and `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`. The chart cannot check them; a value that the Sync API
does not accept stops it at start. The values replace `credentials.bearerTokens`, which set the
removed variable.
