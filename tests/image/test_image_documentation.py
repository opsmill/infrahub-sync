"""The image page reaches readers and states the facts a caller cannot guess."""

from __future__ import annotations

from pathlib import Path

import pytest

from tasks import image
from tests.image.conftest import REPO_ROOT, TMPFS_OPTIONS, WRITABLE_ROOTS

DOCUMENT_ID = "container-image"
PAGE = REPO_ROOT / "docs" / "docs" / f"{DOCUMENT_ID}.mdx"
SIDEBAR = REPO_ROOT / "docs" / "sidebars.ts"

# The four command forms the image supplies beside its default, and the module
# paths a reader has to type exactly.
COMMAND_FORMS = (
    "python -m infrahub_sync.service.worker",
    "python -m infrahub_sync.service.deploy",
    "infrahub-sync --help",
    "import infrahub_sync",
)


def sync_sidebar() -> str:
    """Return the text of the `syncSidebar` array alone."""
    text = SIDEBAR.read_text(encoding="utf-8")
    start = text.index("[", text.index("syncSidebar:"))
    depth = 0
    for offset in range(start, len(text)):
        if text[offset] == "[":
            depth += 1
        elif text[offset] == "]":
            depth -= 1
            if depth == 0:
                return text[start : offset + 1]
    msg = f"syncSidebar in {SIDEBAR} is not a closed array"
    raise AssertionError(msg)


def test_the_container_image_page_is_listed_in_the_docs_sidebar() -> None:
    """Docusaurus renders no navigation entry for a page the sidebar omits."""
    assert PAGE.is_file(), PAGE
    assert f"'{DOCUMENT_ID}'" in sync_sidebar()


@pytest.mark.parametrize("path", WRITABLE_ROOTS)
def test_the_page_documents_every_writable_path(path: str) -> None:
    """An undocumented writable path is one a read-only deployment forgets to mount."""
    assert path in PAGE.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", WRITABLE_ROOTS)
def test_the_documented_mount_hands_every_writable_path_to_the_runtime_user(path: str) -> None:
    """A mount without these options arrives root-owned, and the container cannot write.

    The suite mounts them this way, so the page has to as well; a reader who copies a
    bare `--tmpfs` from here gets a container that fails on its first write.
    """
    assert f"--tmpfs {path}:{TMPFS_OPTIONS}" in PAGE.read_text(encoding="utf-8")


@pytest.mark.parametrize("form", COMMAND_FORMS)
def test_the_page_documents_every_command_form(form: str) -> None:
    """A command form nobody wrote down is one the image appears not to have."""
    assert form in PAGE.read_text(encoding="utf-8")


@pytest.mark.parametrize("task_name", ["build", "inspect", "smoke", "sbom", "scan", "clean"])
def test_the_page_documents_every_image_task(task_name: str) -> None:
    """The page is where a developer finds these; `invoke --list` only names them."""
    assert f"invoke image.{task_name}" in PAGE.read_text(encoding="utf-8")


def test_the_page_names_the_waiver_file_a_reader_has_to_find() -> None:
    """Taking a waiver means editing that file, so the page has to say where it is."""
    assert str(image.WAIVER_FILE.relative_to(REPO_ROOT)) in PAGE.read_text(encoding="utf-8")


def test_the_documented_page_path_is_the_one_the_tests_read() -> None:
    """Guards the parametrised checks above against silently reading a missing file."""
    assert Path(PAGE).read_text(encoding="utf-8").startswith("---\ntitle: Container image\n---")
