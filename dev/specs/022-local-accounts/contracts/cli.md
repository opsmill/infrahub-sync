# Contract: CLI

This contract was brought in line with the implementation on 2026-11-18. The options below match
`uv run infrahub-sync <command> --help`.

## Which value the CLI presents

1. `INFRAHUB_SYNC_API_TOKEN`, when it is set and not empty. Unchanged. The session file is then
   not read.
2. Otherwise, the session token that `infrahub-sync login` saved for the address in
   `INFRAHUB_SYNC_API_URL` (or `--api-url`). The CLI does this lookup. `SyncClient.from_environment`
   reads the variable only, so a script that uses the client as a library reads no file.
3. Otherwise, the existing "no token" hint, which now names both `infrahub-sync login` and
   `INFRAHUB_SYNC_API_TOKEN`.

## New commands

| Command | Who | Result |
| --- | --- | --- |
| `infrahub-sync login [--name NAME] [--password-stdin]` | anyone | Asks for the name when not given, and for the password. Saves the session token. Prints the account name and its level. |
| `infrahub-sync logout` | a signed-in person | Ends the session and removes it from the file. Without a saved session, prints that there is none. |
| `infrahub-sync password [--password-stdin] [--reason TEXT]` | a signed-in person | Asks for the current password once and the new one twice. With `--password-stdin`, reads two lines: the current password, then the new one. |
| `infrahub-sync tokens list [--json]` | an account | Name, token ID, creation date, expiry date, date of last use. |
| `infrahub-sync tokens create NAME [--expires DATE] [--reason TEXT] [--json]` | an account | Prints the token value as the only line of standard output, and on standard error a line that says it is not shown again. |
| `infrahub-sync tokens delete TOKEN_ID [--reason TEXT]` | an account | |
| `infrahub-sync accounts list [--state active\|disabled\|all] [--json]` | administrator | Name, level, state, and the time until which sign-ins are refused. |
| `infrahub-sync accounts create NAME [--administrator] [--password-stdin] [--reason TEXT] [--json]` | administrator | Asks for the initial password, twice. |
| `infrahub-sync accounts set-level NAME --administrator\|--no-administrator [--reason TEXT] [--json]` | administrator | One of the two options is required. |
| `infrahub-sync accounts set-password NAME [--password-stdin] [--reason TEXT]` | administrator | Asks for the new password, twice. |
| `infrahub-sync accounts unlock NAME [--reason TEXT]` | administrator | Ends a refusal after repeated failures. Accepted for `admin`. |
| `infrahub-sync accounts disable NAME [--reason TEXT] [--json]` | administrator | |
| `infrahub-sync accounts enable NAME [--reason TEXT] [--json]` | administrator | |
| `infrahub-sync accounts delete NAME [--yes] [--reason TEXT]` | administrator | Asks for confirmation. The prompt says that the action cannot be reversed and that `disable` can. Prints the placeholder. |
| `infrahub-sync accounts tokens NAME [--json]` | administrator | The tokens of another account. |
| `infrahub-sync accounts delete-token NAME TOKEN_ID [--reason TEXT]` | administrator | |

`--expires` takes an ISO 8601 date or date-time. A date alone, such as `2027-01-31`, means the
last second of that day in UTC. A date-time must carry a time zone, such as
`2027-01-31T12:00:00+00:00` or `2027-01-31T12:00:00Z`. Another value is refused before any
request. Without the option, the token does not expire.

## Rules

- **A password is never a command argument.** It is read from a prompt without echo. With
  `--password-stdin`, it is read from standard input, one password for each line, for a script.
  Without a terminal and without `--password-stdin`, the command refuses before any request.
- **`login --password-stdin` requires `--name`**, and so does `login` without a terminal.
- **`accounts delete` without a terminal requires `--yes`.**
- **`infrahub-sync whoami`** keeps its fields: it prints the account name as `actor`, the level
  and whether the Sync API requires approval.
- **`login` and `logout` while `INFRAHUB_SYNC_API_TOKEN` is set**: both act on the saved
  session, and print one line that says the variable takes precedence for every other command.
- **On the Compose deployment**, the `cli` service keeps no file, so `login` does not persist
  there. The operator sets the `admin` token ([environment.md](environment.md)).
- **The session file** is `sessions.json` in the directory that
  `typer.get_app_dir("infrahub-sync")` returns. The directory is created with mode `0700`, and
  the file is written through a temporary file with mode `0600`. It holds, for each Sync API
  address, the session token and the account name. The address is normalized: the scheme and the
  host in lower case, trailing slashes and one trailing `/api` removed, the query and the
  fragment dropped, the port kept as written. `logout` removes the entry. A session that the API
  refuses with `401 unauthenticated` is removed from the file, and the CLI prints the `login`
  hint. A file that cannot be read counts as no saved session.
- **Output formats**: a table, or `key: value` lines, by default; JSON with `--json` on the
  commands that list it above.
- **Error messages**: the client keeps no message text of an answer, so the CLI prints, for each
  refusal code of the account routes, its own fixed message with the status and the code. The
  message for `environment-managed-account` says that `admin` is always an active administrator,
  and names both `INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD` and `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`.
- **Exit codes** follow the existing mapping of API errors. A refusal before any request (a
  missing terminal, a missing option, an `--expires` value that is not accepted) exits with `2`.
- Every new command has generated reference documentation (`uv run invoke docs.generate`).

Changed during implementation:

- The contract named "the existing output option", which does not exist. `--json` is a new
  option.
- The CLI prints fixed messages, because the client does not keep the message of an answer (see
  the secret-redaction guideline). So one message covers every `admin` refusal.
- The options `--reason`, `--password-stdin` on `login` and `password`, and the rules for a
  missing terminal were added during the implementation.
