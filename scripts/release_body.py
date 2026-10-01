#!/usr/bin/env python3
"""Render a curated release-notes page as a GitHub Release body.

Reads ``docs/docs/release-notes/infrahub-sync/release-X_Y_Z.mdx`` and prints
GitHub-flavored Markdown. The site-only frontmatter and metadata table are
removed, while the narrative that follows the table is retained. Docusaurus
admonitions become blockquotes and relative documentation links become plain
text because they do not resolve from a GitHub Release. With
``--previous-tag``, a Full Changelog comparison link is appended.

Usage: release_body.py VERSION [--previous-tag X.Y.Z]
"""

import argparse
import re
import sys
from pathlib import Path

REPO = "opsmill/infrahub-sync"
NOTES_DIR = Path(__file__).resolve().parent.parent / "docs" / "docs" / "release-notes" / "infrahub-sync"

ADMONITION = re.compile(r"^:::(\w+)[ \t]*([^\n]*)\n(.*?)\n:::[ \t]*$", re.DOTALL | re.MULTILINE)
RELATIVE_LINK = re.compile(r"\[([^\]]+)\]\((?!https?:|#|mailto:)[^)]+\)")


def notes_path(version: str) -> Path:
    return NOTES_DIR / f"release-{version.replace('.', '_')}.mdx"


def _blockquote(match: re.Match[str]) -> str:
    kind, title, inner = match.groups()
    lines = [f"> **{title.strip() or kind.capitalize()}**", ">"]
    lines += [f"> {line}" if line.strip() else ">" for line in inner.strip("\n").split("\n")]
    return "\n".join(lines)


def _release_content(mdx: str) -> str:
    table_end = mdx.find("</table>")
    if table_end >= 0:
        return mdx[table_end + len("</table>") :].lstrip()

    first_heading = re.search(r"^## ", mdx, re.MULTILINE)
    if first_heading is None:
        message = "release-notes page has no metadata table or '## ' heading to start the body from"
        raise ValueError(message)
    return mdx[first_heading.start() :]


def render(mdx: str, version: str, previous_tag: str | None = None) -> str:
    body = _release_content(mdx)
    body = ADMONITION.sub(_blockquote, body)
    body = RELATIVE_LINK.sub(r"\1", body)
    body = body.rstrip() + "\n"
    if previous_tag:
        compare = f"https://github.com/{REPO}/compare/{previous_tag}...{version}"
        body += f"\n---\n\n**Full Changelog**: {compare}\n"
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="release version, e.g. 2.0.1")
    parser.add_argument("--previous-tag", help="tag to compare against, e.g. 2.0.0")
    args = parser.parse_args()

    path = notes_path(args.version)
    if not path.is_file():
        sys.stderr.write(f"no release-notes page at {path}\n")
        return 1
    sys.stdout.write(render(path.read_text(), args.version, args.previous_tag))
    return 0


if __name__ == "__main__":
    sys.exit(main())
