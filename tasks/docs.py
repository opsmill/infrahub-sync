import sys
from pathlib import Path

from invoke import Context, task

from .utils import ESCAPED_REPO_PATH

NAMESPACE = "INFRAHUB-SYNC-DOCS"
CURRENT_DIRECTORY = Path(__file__).parent.resolve()
DOCUMENTATION_DIRECTORY = CURRENT_DIRECTORY.parent / "docs"


@task
def generate(context: Context) -> None:
    """Generate documentation for the infrahub-sync cli."""
    _generate_infrahubsync_documentation(context=context)


def _generate_infrahubsync_documentation(context: Context) -> None:
    """Generate the documentation for infrahub-sync using typer-cli."""

    print(" - Generate infrahub-sync CLI documentation")
    exec_cmd = 'uv run typer infrahub_sync.cli utils docs --name "infrahub-sync"'
    exec_cmd += " --output docs/docs/reference/cli.mdx"
    with context.cd(ESCAPED_REPO_PATH):
        context.run(exec_cmd)
    reference = DOCUMENTATION_DIRECTORY / "docs" / "reference" / "cli.mdx"
    _drop_hidden_commands(reference)
    _insert_api_token_intro(reference)


# Hand-written paragraph placed before the root command's first "**Usage**:" line. Typer has
# no place for it, so generation inserts it after every run.
_API_TOKEN_INTRO = (
    "Set `INFRAHUB_SYNC_API_TOKEN` to the bearer token for your actor before running any\n"
    "command; the CLI reads it from the environment and never accepts it as a flag. See\n"
    "[Sync HTTP API — Configure principals](./sync-http-api.mdx#configure-principals) for how\n"
    "tokens are issued.\n"
)


def _insert_api_token_intro(path: Path) -> None:
    """Insert the `INFRAHUB_SYNC_API_TOKEN` paragraph before the root command's usage block."""
    text = path.read_text(encoding="utf-8")
    if _API_TOKEN_INTRO in text:
        return
    marker = "**Usage**:\n"
    if marker not in text:
        msg = f"{path} has no '**Usage**:' line to place the API token paragraph before"
        raise ValueError(msg)
    path.write_text(text.replace(marker, f"{_API_TOKEN_INTRO}\n{marker}", 1), encoding="utf-8")


def _hidden_command_paths() -> set[str]:
    """Return the space-separated paths of the CLI commands registered as hidden."""
    # Imported here so `invoke --list` does not load the CLI.
    import typer  # noqa: PLC0415

    from infrahub_sync.cli import app  # noqa: PLC0415

    hidden: set[str] = set()

    # Typer may build its commands on a click copy of its own, so groups are recognized by
    # their `commands` mapping rather than by `isinstance(..., click.Group)`.
    def walk(command: object, path: tuple[str, ...]) -> None:
        if getattr(command, "hidden", False):
            hidden.add(" ".join(path))
        for name, child in getattr(command, "commands", {}).items():
            walk(child, (*path, name))

    walk(typer.main.get_command(app), ())
    hidden.discard("")
    return hidden


def _drop_hidden_commands(path: Path) -> None:
    """Remove hidden commands, such as the deprecated `diff` alias, from the generated reference.

    `typer utils docs` documents hidden commands too, so the reference would teach a command
    that `--help` does not show.
    """
    hidden = _hidden_command_paths()
    if not hidden:
        return
    kept: list[str] = []
    section = ""  # Command path of the current heading, "" for the root command.
    skip_level: int | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        marker, _, rest = line.partition(" ")
        level = len(marker) if marker and set(marker) == {"#"} else None
        if level is not None:
            if skip_level is not None and level <= skip_level:
                skip_level = None
            section = rest.strip().strip("`").removeprefix("infrahub-sync").strip()
            if section in hidden:
                skip_level = level
        if skip_level is not None:
            continue
        # A "**Commands**:" entry reads "* `name`: summary"; drop the entry of a hidden subcommand.
        name = line.removeprefix("* `").partition("`:")[0] if line.startswith("* `") and "`:" in line else None
        if name is not None and f"{section} {name}".strip() in hidden:
            continue
        kept.append(line)
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


@task
def rumdl(context: Context) -> None:
    """Lint all Markdown/MDX files with rumdl (config in pyproject.toml)."""
    print(" - [docs] Lint docs with rumdl")
    exec_cmd = "rumdl check ."
    with context.cd(ESCAPED_REPO_PATH):
        context.run(exec_cmd)


@task
def format_rumdl(context: Context) -> None:
    """Auto-fix all Markdown/MDX files with rumdl."""
    print(" - [docs] Format docs with rumdl")
    exec_cmd = "rumdl fmt ."
    with context.cd(ESCAPED_REPO_PATH):
        context.run(exec_cmd)


@task
def format(context: Context) -> None:  # noqa: A001
    """This will run all formatters."""
    format_rumdl(context)


@task
def lint(context: Context) -> None:
    """This will run all linters."""
    rumdl(context)


@task
def docusaurus(context: Context) -> None:
    """Build documentation website."""
    exec_cmd = "pnpm run build"

    with context.cd(DOCUMENTATION_DIRECTORY):
        output = context.run(exec_cmd)

    if output is None or output.exited != 0:
        sys.exit(-1)
