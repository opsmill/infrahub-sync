from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]


def test_release_publisher_prefers_curated_release_notes() -> None:
    workflow = (REPOSITORY_ROOT / ".github/workflows/release-publish.yml").read_text()

    notes_page = "docs/docs/release-notes/infrahub-sync/release-${VERSION//./_}.mdx"
    assert notes_page in workflow, "release publisher does not select the curated Sync release-notes page"
    assert "scripts/release_body.py" in workflow, "release publisher does not render the curated MDX page"
    assert "CHANGELOG.md" in workflow, "release publisher no longer retains the changelog fallback"
