# Quickstart: check sign-in with a local account

These scenarios prove the feature on the development stack. Contracts:
[HTTP API](contracts/http-api.md), [CLI](contracts/cli.md),
[variables](contracts/environment.md), [web interface](contracts/web-interface.md).

## Prerequisites

```bash
uv sync --extra dev --extra prefect --extra service
uv run invoke dev.start
export INFRAHUB_SYNC_API_URL=http://127.0.0.1:8001
```

The development stack sets the `admin` password `infrahub-sync-dev-password` and the token
`infrahub-sync-dev-token`. Without a terminal, give each password with `--password-stdin`;
`login --password-stdin` also needs `--name`, and `password --password-stdin` reads two lines,
the current password and then the new one.

## Offline checks

```bash
uv run invoke format
uv run invoke lint
uv run ruff check .
uv run invoke tests.tests-unit
uv run infrahub-sync --help
uv run infrahub-sync accounts --help
uv run infrahub-sync tokens --help
```

## Scenario 1: add a person and give a script a token (User Story 1)

```bash
unset INFRAHUB_SYNC_API_TOKEN
uv run infrahub-sync login --name admin          # development password
uv run infrahub-sync accounts create alice       # type an initial password twice
uv run infrahub-sync logout
uv run infrahub-sync login --name alice
uv run infrahub-sync whoami                      # actor: alice, administrator: false
uv run infrahub-sync password                    # replace the initial password
uv run infrahub-sync runs list
TOKEN=$(uv run infrahub-sync tokens create ci-nightly)   # the value is the only line of standard output
INFRAHUB_SYNC_API_TOKEN=$TOKEN uv run infrahub-sync whoami   # actor: alice
```

Expected: no file of the deployment changed and no service restarted.

## Scenario 2: the removed variable is ignored

Set `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` in the environment of `sync-api`, restart it, and call
`whoami` with a token that only that variable defines. Expected: `401`.

## Scenario 3: the `admin` account

```bash
uv run infrahub-sync login --name admin
uv run infrahub-sync password                    # refused: 409 environment-managed-account
uv run infrahub-sync accounts disable admin      # refused: 409 environment-managed-account
uv run infrahub-sync accounts unlock admin       # accepted
```

Each refusal prints the status, the code and the CLI's fixed message for the code, which names
`INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD` and `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`.

Change the password variable of `sync-api`, restart `sync-api` only, and sign in with the new
password. Expected: the old password is refused. Start `sync-api` without the password variable.
Expected: it does not start and names the variable.

## Scenario 4: repeated failures

Sign in as `alice` with a wrong password 5 times, then with the right password. Expected: the
sixth attempt is refused with the same message. Her token `ci-nightly` still works. As `admin`,
run `infrahub-sync accounts unlock alice`. Expected: she signs in.

## Scenario 5: disable, enable, delete (User Story 3)

```bash
uv run infrahub-sync accounts disable alice      # as admin
INFRAHUB_SYNC_API_TOKEN=$TOKEN uv run infrahub-sync whoami   # 401
uv run infrahub-sync accounts enable alice
uv run infrahub-sync accounts delete alice       # asks for confirmation; prints "Deleted account <short identifier>"
uv run infrahub-sync runs list                   # her runs show the placeholder
uv run infrahub-sync accounts create alice
```

Expected: the new `alice` cannot apply or cancel a run of the deleted account, the old runs still
display the placeholder, and a search of the product database finds the old name only in
free-text reasons, if a person typed it there.

## Scenario 5a: the `admin` password cannot be tested through a reason

As `alice`, create a plan run with `--reason` set to the development password of `admin`.
Expected: the request is accepted like any other reason. A reason that contains `ifs_pat_` is
refused.

## Scenario 6: the web interface (User Stories 2 and 4)

Open `http://127.0.0.1:8001/`. Expected: a sign-in form. Sign in as an account, view a run that
another account started, open "My account", create a token, sign out. As `admin`, open
"Accounts", create and disable an account.
