"""22 developer and reference pages were published but reachable only through a link on
an index page, not through the sidebar itself. Docusaurus renders no navigation entry,
and search engines see no path to them, for a page the sidebar omits.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = REPO_ROOT / "docs" / "sidebars.ts"

DOCUMENT_IDS = [
    "develop/knowledge/sync-architecture",
    "develop/knowledge/repository-tour",
    "develop/knowledge/adapter-anatomy",
    "develop/knowledge/schema-mapping",
    "develop/knowledge/incremental-and-cache",
    "develop/knowledge/plan-artifact",
    "develop/knowledge/planned-write-and-apply",
    "develop/knowledge/apply-guard",
    "develop/knowledge/configuration-foundation",
    "develop/knowledge/execution-surface",
    "develop/knowledge/orchestration-prefect",
    "develop/knowledge/quality-gates",
    "develop/guides/adding-an-adapter",
    "develop/guides/testing-an-adapter",
    "develop/guides/building-a-tester-packet",
    "develop/guides/qualifying-an-internal-candidate",
    "develop/guidelines/writing-an-adapter",
    "develop/guidelines/testing-adapters",
    "develop/guidelines/testing",
    "develop/guidelines/testing-tiers",
    "develop/guidelines/secret-redaction",
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
