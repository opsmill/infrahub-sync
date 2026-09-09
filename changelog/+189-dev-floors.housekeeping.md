Development tools declared without a lower bound now have one, so a lowest-version dependency
resolution can no longer select releases that predate Python 3. The `typer-cli` floor matters
most: older releases pull `typer-slim`, which shadows the real `typer` package and breaks the CLI.
