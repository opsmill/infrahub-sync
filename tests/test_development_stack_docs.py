"""The local development stack page is reachable from the two entrypoints that lead to it.

Neither link is covered by the documentation build. Docusaurus only warns on a broken
Markdown link, and it never inspects the root README at all, so a page that exists but
is listed nowhere still builds clean while nobody can find it.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_ID = "development-stack"
PAGE = REPO_ROOT / "docs" / "docs" / f"{DOCUMENT_ID}.mdx"


def test_the_development_stack_page_is_listed_in_the_docs_sidebar() -> None:
    """Docusaurus renders no navigation entry for a page the sidebar omits."""
    sidebar = (REPO_ROOT / "docs" / "sidebars.ts").read_text(encoding="utf-8")

    assert PAGE.is_file(), PAGE
    assert f"'{DOCUMENT_ID}'" in sidebar


def test_the_root_readme_links_the_development_stack_page() -> None:
    """The published page is how a contributor reaches the stack from the repository front page."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert f"https://docs.infrahub.app/sync/{DOCUMENT_ID}" in readme
