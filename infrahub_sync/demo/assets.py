"""Find the demo's destination schema and NetBox package.

A wheel carries both under `infrahub_sync/demo/assets/` (`schema/*.yml` and
`netbox/package.yml`). An editable install from the source tree has no such directory, so
the two repository paths they are built from are read instead (research R-1).
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from infrahub_sync.demo.errors import DemoAssetError

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_SCHEMA_DIR = REPO_ROOT / "tests" / "data" / "nightly_schema" / "traditional-infrastructure-sot"
SOURCE_PACKAGE = REPO_ROOT / "examples" / "netbox_to_infrahub" / "package.yml"


def bundled_root() -> Path | None:
    """The `assets` directory path next to this module, or `None` when it is not on the file system.

    An editable install returns a path that does not exist, because the source tree has no
    `assets` directory; `_bundled` then answers `None` and the callers read the source tree.
    """
    root = files("infrahub_sync.demo") / "assets"
    # A wheel installed into site-packages gives a real directory; anything else (a zip
    # import) is not supported and falls back to the source tree.
    return root if isinstance(root, Path) else None


def _bundled(relative: str) -> Path | None:
    root = bundled_root()
    if root is None:
        return None
    candidate = root / relative
    return candidate if candidate.exists() else None


def schema_files() -> list[Path]:
    """The destination schema files, sorted by name."""
    directory = _bundled("schema") or SOURCE_SCHEMA_DIR
    if not directory.is_dir():
        msg = f"demo schema directory not found: {directory}"
        raise DemoAssetError(msg)
    found = sorted(directory.glob("*.yml"), key=lambda path: path.name)
    if not found:
        msg = f"demo schema directory holds no .yml file: {directory}"
        raise DemoAssetError(msg)
    return found


def netbox_package_text() -> str:
    """The text of the shipped `from-netbox` configuration package."""
    path = _bundled("netbox/package.yml") or SOURCE_PACKAGE
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        msg = f"demo NetBox package cannot be read: {path}"
        raise DemoAssetError(msg) from error
