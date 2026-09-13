The declared dependency metadata now matches what the package actually imports: `jinja2`, `pyyaml`,
`typer`, `requests`, `urllib3`, `packaging`, `pydantic` and `typing-extensions` are declared
directly, the `netutils` floor is measured rather than guessed, development tools have lower
bounds, and `prek` replaces `pre-commit` for the commit hooks. `infrahub-sdk` itself is unchanged:
the supported range stays `>=1.17,<2`, the locked and tested version stays 1.18.1, and the `[all]`
extra is still installed because `infrahubctl` needs it. Moving to a newer SDK is a separate
change.
