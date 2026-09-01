"""The local development stack page is reachable from the two entrypoints that lead to it.

Neither link is covered by the documentation build. Docusaurus only warns on a broken
Markdown link, and it never inspects the root README at all, so a page that exists but
is listed nowhere still builds clean while nobody can find it.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_ID = "development-stack"
PAGE = REPO_ROOT / "docs" / "docs" / f"{DOCUMENT_ID}.mdx"
SIDEBAR = REPO_ROOT / "docs" / "sidebars.ts"
# The link has to be a Markdown target, not the address written somewhere in prose:
# only a target is what a reader can follow.
README_LINK = re.compile(rf"\]\(https://docs\.infrahub\.app/sync/{re.escape(DOCUMENT_ID)}\)")


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
