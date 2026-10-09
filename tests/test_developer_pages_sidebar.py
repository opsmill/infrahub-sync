"""22 developer and reference pages were published and linked from index pages, but the
sidebar omitted them, so Docusaurus rendered no navigation entry for them.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = REPO_ROOT / "docs" / "sidebars.ts"

DOCUMENT_IDS = [
    "development/knowledge/sync-architecture",
    "development/knowledge/repository-tour",
    "development/knowledge/adapter-anatomy",
    "development/knowledge/schema-mapping",
    "development/knowledge/incremental-and-cache",
    "development/knowledge/plan-artifact",
    "development/knowledge/planned-write-and-apply",
    "development/knowledge/apply-guard",
    "development/knowledge/configuration-foundation",
    "development/knowledge/execution-surface",
    "development/knowledge/orchestration-prefect",
    "development/knowledge/quality-gates",
    "development/guides/adding-an-adapter",
    "development/guides/testing-an-adapter",
    "development/guidelines/writing-an-adapter",
    "development/guidelines/testing-adapters",
    "development/guidelines/testing",
    "development/guidelines/testing-tiers",
    "development/guidelines/secret-redaction",
    "reference/cache-layout",
]

PAGE_SUFFIXES = (".md", ".mdx")


def sync_sidebar() -> str:
    """Return the text of the `syncSidebar` array alone.

    Scoping matters: a document id appearing anywhere else in the file — a redirect, a
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


def _page_path(document_id: str) -> Path:
    base = REPO_ROOT / "docs" / "docs" / document_id
    for suffix in PAGE_SUFFIXES:
        candidate = base.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    msg = f"no {PAGE_SUFFIXES} page for document id {document_id}"
    raise AssertionError(msg)


def test_every_previously_unreachable_page_exists() -> None:
    for document_id in DOCUMENT_IDS:
        assert _page_path(document_id).is_file()


def test_every_previously_unreachable_page_is_listed_in_the_docs_sidebar() -> None:
    """Docusaurus renders no navigation entry for a page the sidebar omits."""
    sidebar = sync_sidebar()
    for document_id in DOCUMENT_IDS:
        assert f"'{document_id}'" in sidebar, document_id
