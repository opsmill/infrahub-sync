from pathlib import Path

from invoke import Context, UnexpectedExit

path = Path(__file__)
TASKS_DIR = path.parent
REPO_BASE = TASKS_DIR.parent


def check_if_command_available(context: Context, command_name: str) -> bool:
    try:
        context.run(f"command -v {command_name}", hide=True)
        return True
    except UnexpectedExit:
        return False


def escape_path(path: Path) -> str:
    """Escape special characters in the provided path string to make it shell-safe."""
    return str(path).translate(
        str.maketrans(
            {
                "-": r"\-",
                "]": r"\]",
                "\\": r"\\",
                "^": r"\^",
                "$": r"\$",
                "*": r"\*",
                "(": r"\(",
                ")": r"\)",
                ".": r"\.",
            }
        )
    )


ESCAPED_REPO_PATH = escape_path(REPO_BASE)
