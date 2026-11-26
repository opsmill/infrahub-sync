# CLI contract: `infrahub-sync credentials`

Every command calls the Sync API with the saved session or `INFRAHUB_SYNC_API_TOKEN`. No command
prints a value.

| Command | Who | Behavior |
| --- | --- | --- |
| `credentials list [--json]` | every account | Table of name, fields, revision, updated by, updated at |
| `credentials show NAME [--json]` | every account | The credential resource |
| `credentials create NAME [--description TEXT] [--username TEXT] [--token-stdin \| --password-stdin]` | administrator | Reads the token or the password from a hidden prompt, or from stdin with the flag. Without `--username`, it prompts for a token. With `--username`, it prompts for a password unless `--token-stdin` is given. Prints the resource. |
| `credentials set NAME [--username TEXT] [--token-stdin \| --password-stdin]` | administrator | Replaces the value, same input rules as `create`. Prints the new revision. |
| `credentials describe NAME TEXT` | administrator | Changes the description only |
| `credentials delete NAME [--yes]` | administrator | Asks for confirmation. On `credential-in-use`, prints the configurations and schedules that name it and exits 1. |

Exit codes follow the existing commands: 0 success, 1 refusal, 2 usage error.

## Changed: `configs convert`

- Writes `credential: <name>` in each block that uses authentication, and no `credentials` block.
- Names follow `<adapter>-<setting>` as today (`conversion.py:144-161`), for example `netbox-token` and
  `infrahub-token`.
- The report lists each credential to create, with the fields it needs and the command to create it,
  for example `infrahub-sync credentials create netbox-token`.
