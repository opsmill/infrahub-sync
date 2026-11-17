# Contract: CLI

## Which value the CLI presents

1. `INFRAHUB_SYNC_API_TOKEN`, when it is set and not empty. Unchanged.
2. Otherwise, the session token that `infrahub-sync login` saved for the address in
   `INFRAHUB_SYNC_API_URL`. The CLI does this lookup. `SyncClient.from_environment` reads the
   variable only, so a script that uses the client as a library reads no file.
3. Otherwise, the existing "no token" hint, which now names both `infrahub-sync login` and
   `INFRAHUB_SYNC_API_TOKEN`.

## New commands

| Command | Who | Result |
| --- | --- | --- |
| `infrahub-sync login [--name NAME]` | anyone | Asks for the name when not given, and for the password. Saves the session token. Prints the account name. |
| `infrahub-sync logout` | a signed-in person | Ends the session and removes it from the file. |
| `infrahub-sync password` | a signed-in person | Asks for the current password and the new one, twice. |
| `infrahub-sync tokens list` | an account | Name, token ID, creation date, expiry date, date of last use. |
| `infrahub-sync tokens create NAME [--expires DATE]` | an account | Prints the token value once, with a line that says it is not shown again. |
| `infrahub-sync tokens delete TOKEN_ID` | an account | |
| `infrahub-sync accounts list [--state active\|disabled\|all]` | administrator | Name, level, state. |
| `infrahub-sync accounts create NAME [--administrator]` | administrator | Asks for the initial password, twice. |
| `infrahub-sync accounts set-level NAME --administrator\|--no-administrator` | administrator | |
| `infrahub-sync accounts set-password NAME` | administrator | Asks for the new password, twice. |
| `infrahub-sync accounts unlock NAME` | administrator | Ends a refusal after repeated failures. |
| `infrahub-sync accounts disable NAME` | administrator | |
| `infrahub-sync accounts enable NAME` | administrator | |
| `infrahub-sync accounts delete NAME [--yes]` | administrator | Asks for confirmation. The prompt says that the action cannot be reversed and that `disable` can. Prints the placeholder. |
| `infrahub-sync accounts tokens NAME` | administrator | The tokens of another account. |
| `infrahub-sync accounts delete-token NAME TOKEN_ID` | administrator | |

## Rules

- **A password is never a command argument.** It is read from a prompt without echo. With
  `--password-stdin`, it is read from standard input, for a script that creates accounts.
- **`infrahub-sync whoami`** is unchanged: it prints the account name, the level and whether the
  Sync API requires approval.
- **`login` and `logout` while `INFRAHUB_SYNC_API_TOKEN` is set**: both act on the saved
  session, and print one line that says the variable takes precedence for every other command.
- **On the Compose deployment**, the `cli` service keeps no file, so `login` does not persist
  there. The operator sets the `admin` token ([environment.md](environment.md)).
- **The session file** is `sessions.json` in the directory `typer.get_app_dir("infrahub-sync")`
  returns, created with mode `0600`. It holds one session token for each Sync API address. `logout` removes the entry. A
  session the API refuses is removed from the file, and the CLI prints the `login` hint.
- **Output formats** follow the existing commands: a table by default, JSON with the existing
  output option.
- **Exit codes** follow the existing mapping of API errors.
- Every new command has generated reference documentation (`uv run invoke docs.generate`).
