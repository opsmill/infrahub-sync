"""The local development stack page is reachable, and documents the `dev` tasks it describes.

Neither link is covered by the documentation build. Docusaurus only warns on a broken
Markdown link, and it never inspects the root README at all, so a page that exists but
is listed nowhere still builds clean while nobody can find it.
"""

import re
from pathlib import Path

import pytest

from tasks import dev

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_ID = "development-stack"
PAGE = REPO_ROOT / "docs" / "docs" / f"{DOCUMENT_ID}.mdx"
SIDEBAR = REPO_ROOT / "docs" / "sidebars.ts"
# The link has to be a Markdown target, not the address written somewhere in prose:
# only a target is what a reader can follow.
README_LINK = re.compile(
    rf"\]\(https://feature-v3-develop\.infrahub-sync\.pages\.dev/{re.escape(DOCUMENT_ID)}(?:#[^)]*)?\)"
)


def sync_sidebar() -> str:
    """Return the text of the `syncSidebar` array alone.

    Scoping matters: the document id appearing anywhere else in the file — a redirect, a
    second sidebar, a comment — would satisfy a whole-file search while the rendered
    navigation still omits the page. Document ids carry no brackets, so matching the
    array's own brackets by depth is enough and needs no parser.
    """
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


def test_the_development_stack_page_is_listed_in_the_docs_sidebar() -> None:
    """Docusaurus renders no navigation entry for a page the sidebar omits."""
    assert PAGE.is_file(), PAGE
    assert f"'{DOCUMENT_ID}'" in sync_sidebar()


def test_the_root_readme_links_the_development_stack_page() -> None:
    """The published page is how a contributor reaches the stack from the repository front page."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert README_LINK.search(readme) is not None


PREVIEW_PAGE = REPO_ROOT / "docs" / "docs" / "development" / "guides" / "preview-stack.md"
# A command a reader would copy: the top-level tasks the `dev` namespace replaces.
TOP_LEVEL_COMMAND = re.compile(r"uv run invoke (?:build|start|destroy)\b(?!\.)")


def test_the_development_stack_page_documents_every_dev_task() -> None:
    """The page is the only document an engineer needs, so no `dev` task may be missing from it."""
    text = PAGE.read_text(encoding="utf-8")
    missing = sorted(name for name in dev.ns.task_names if f"`dev.{name}" not in text)

    assert missing == [], f"{PAGE.name} does not document dev.{', dev.'.join(missing)}"


@pytest.mark.parametrize(
    "phrase",
    ["`dev.smoke`", "`dev.restart`", "--infrahub-network", "INFRAHUB_PORT", "uv.lock"],
    ids=["smoke", "restart", "infrahub-network", "infrahub-port", "stale-image-inputs"],
)
def test_the_development_stack_page_covers_both_modes_and_their_settings(phrase: str) -> None:
    assert phrase in PAGE.read_text(encoding="utf-8")


def test_the_development_stack_page_runs_no_top_level_stack_task() -> None:
    """The commands on the page use the `dev` namespace, not the top-level tasks it replaces."""
    commands = TOP_LEVEL_COMMAND.findall(PAGE.read_text(encoding="utf-8"))

    assert commands == [], f"{PAGE.name} still runs {commands}"


def test_the_preview_stack_page_points_to_the_development_stack() -> None:
    """The preview stack stays until it is removed; its page sends readers to the replacement."""
    head = PREVIEW_PAGE.read_text(encoding="utf-8").split("### Commands", 1)[0]

    assert "(../../development-stack.mdx)" in head
    assert "will be removed" in head
