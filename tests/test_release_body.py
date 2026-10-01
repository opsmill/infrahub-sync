"""Tests for converting a release-notes page into a GitHub Release body."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release_body.py"
spec = importlib.util.spec_from_file_location("release_body", SCRIPT)
if spec is None or spec.loader is None:
    msg = f"could not load release-body script at {SCRIPT}"
    raise ImportError(msg)
release_body = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_body)

PAGE = """---
title: Release 9.9.9
---

<table>
  <tbody>
    <tr><th>Release Number</th><td>9.9.9</td></tr>
  </tbody>
</table>

A maintenance release with important fixes.

## Bug fixes

:::note What to expect after upgrading

Nothing changed.

- Review one thing.

:::

Use [configuration](../../references/configuration.mdx) and see [Upgrade notes](#upgrade-notes)
or [towncrier](https://towncrier.readthedocs.io/).

:::tip

Untitled tip.

:::

## Upgrade notes
"""


def test_drops_site_metadata_and_keeps_summary() -> None:
    body = release_body.render(PAGE, "9.9.9")
    assert body.startswith("A maintenance release with important fixes.\n")
    assert "title:" not in body
    assert "<table>" not in body


def test_admonitions_become_blockquotes() -> None:
    body = release_body.render(PAGE, "9.9.9")
    assert ":::" not in body
    assert "> **What to expect after upgrading**\n>\n> Nothing changed.\n>\n> - Review one thing." in body
    assert "> **Tip**\n>\n> Untitled tip." in body


def test_relative_links_reduced_to_text_others_kept() -> None:
    body = release_body.render(PAGE, "9.9.9")
    assert "Use configuration and" in body
    assert "[Upgrade notes](#upgrade-notes)" in body
    assert "[towncrier](https://towncrier.readthedocs.io/)" in body


def test_compare_link_uses_unprefixed_tags() -> None:
    assert "Full Changelog" not in release_body.render(PAGE, "9.9.9")
    body = release_body.render(PAGE, "9.9.9", "9.9.8")
    assert body.endswith(
        "\n---\n\n**Full Changelog**: https://github.com/opsmill/infrahub-sync/compare/9.9.8...9.9.9\n"
    )


def test_page_without_table_or_heading_is_rejected() -> None:
    with pytest.raises(ValueError, match="has no metadata table or '## ' heading"):
        release_body.render("---\ntitle: x\n---\n\nno heading\n", "9.9.9")


def test_every_published_page_renders() -> None:
    pages = sorted(release_body.NOTES_DIR.glob("release-*.mdx"))
    assert pages
    for page in pages:
        body = release_body.render(page.read_text(), "0.0.0")
        assert ":::" not in body, page.name
        assert "](../" not in body, page.name
