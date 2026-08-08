"""Credential hygiene scan over the shipped Prefect remote-run example (DBA-008, SC-005).

The example under `examples/prefect_remote_run/` is documentation a reader copies
from, so it must contain no real credential, no value seeded as a test canary, and
every credential-shaped string must use the fixed placeholder sentinels the example
README and its request corpus agreed on.

This module deliberately has NO prefect dependency: it must run in a base install.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "examples" / "prefect_remote_run"

# The fixed placeholder convention. Every credential-shaped value in the example is
# one of these verbatim; that is what makes this scan deterministic.
TOKEN_SENTINEL = "<your-api-token>"  # noqa: S105 - a placeholder, not a credential
ADDRESS_SENTINEL = "<your-infrahub-address>"
BEARER_SENTINEL = f"Bearer {TOKEN_SENTINEL}"

# Canary values seeded by the redaction tests (tests/orchestration/test_flow.py).
# Duplicated rather than imported: that module skips without prefect installed, and
# this scan must run in a base install.
SEEDED_CANARIES = (
    "ZZ-FLOW-ENV-INFRAHUB-TOKEN-0001",
    "ZZ-FLOW-ENV-NETBOX-TOKEN-0002",
    "ZZ-FLOW-SETTINGS-SOURCE-TOKEN-0003",
    "ZZ-FLOW-SETTINGS-DEST-PASSWORD-0004",
)
# Any canary follows the same "ZZ-" sentinel shape, so catch unseen ones too.
CANARY_SHAPE = re.compile(r"\bZZ-[A-Z0-9-]{8,}\b")

# `token: value`, `PASSWORD=value`, `api_key: value`, ... - the key word sits
# immediately against the delimiter, so ordinary prose ending in a colon does not match.
CREDENTIAL_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<key>[A-Za-z0-9_-]*(?:token|password|passwd|secret|api[_-]?key)) \b
    ["']? \s* [:=] \s*
    (?P<value>"[^"\n]*" | '[^'\n]*' | [^\s,;]+)
    """
)
BEARER_VALUE = re.compile(r"(?i)\bbearer\s+(?P<value>\S+)")
# scheme://user:password@host - a credential embedded in a URL
URL_WITH_CREDENTIALS = re.compile(r"[a-z][a-z0-9+.-]*://[^\s/@\"']+:[^\s/@\"']+@")
# A long literal run on a line that talks about credentials is how a real token looks.
LONG_LITERAL = re.compile(r"[A-Za-z0-9_-]{24,}")
# No word boundaries on purpose: `INFRAHUB_API_TOKEN` must count as a credential mention.
CREDENTIAL_WORD = re.compile(r"(?i)(token|password|passwd|secret|api[_-]?key)")

# Values a credential-shaped assignment may legitimately carry in the example.
ALLOWED_VALUES = frozenset(
    {
        TOKEN_SENTINEL,
        f'"{TOKEN_SENTINEL}"',
        BEARER_SENTINEL,
        ADDRESS_SENTINEL,
        f'"{ADDRESS_SENTINEL}"',
    }
)


def _example_files() -> list[Path]:
    """Every text file shipped in the example tree."""
    return sorted(p for p in EXAMPLE_DIR.rglob("*") if p.is_file())


def _is_placeholder_or_env_reference(value: str) -> bool:
    """True when a credential-shaped value is a sentinel or an environment reference."""
    stripped = value.strip("\"'`,.")
    if stripped in ALLOWED_VALUES or value in ALLOWED_VALUES:
        return True
    # `<...>` placeholder, `$VAR` / `${VAR}` environment reference, or a variable NAME
    # used as documentation (`INFRAHUB_API_TOKEN`).
    return bool(
        re.fullmatch(r"<[a-z0-9-]+>", stripped)
        or re.fullmatch(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?", stripped)
        or re.fullmatch(r"[A-Z][A-Z0-9_]*", stripped)
    )


def test_example_directory_exists() -> None:
    """The scanned corpus is the shipped example, README plus requests plus schemas."""
    assert EXAMPLE_DIR.is_dir(), f"{EXAMPLE_DIR} is missing"
    shipped = {p.relative_to(EXAMPLE_DIR).as_posix() for p in _example_files()}
    assert "README.md" in shipped
    assert any(name.startswith("requests/") for name in shipped)
    assert any(name.startswith("schemas/") for name in shipped)


example_file = pytest.mark.parametrize("path", _example_files(), ids=lambda p: p.relative_to(EXAMPLE_DIR).as_posix())


@example_file
def test_no_seeded_canary_value(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for canary in SEEDED_CANARIES:
        assert canary not in text, f"seeded canary {canary} appears in {path}"
    found = CANARY_SHAPE.findall(text)
    assert not found, f"canary-shaped value(s) {found} appear in {path}"


@example_file
def test_credential_assignments_use_the_placeholder_convention(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    offenders = [
        (match.group("key"), match.group("value"))
        for match in CREDENTIAL_ASSIGNMENT.finditer(text)
        if not _is_placeholder_or_env_reference(match.group("value"))
    ]
    assert not offenders, f"credential-shaped value(s) not using the sentinels in {path}: {offenders}"


@example_file
def test_bearer_headers_use_the_token_sentinel(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    offenders = [
        m.group("value").strip("\"'`")
        for m in BEARER_VALUE.finditer(text)
        if m.group("value").strip("\"'`") != TOKEN_SENTINEL
    ]
    assert not offenders, f"Bearer value(s) other than {TOKEN_SENTINEL} in {path}: {offenders}"


@example_file
def test_no_credentials_embedded_in_urls(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not URL_WITH_CREDENTIALS.search(text), f"URL with embedded credentials in {path}"


@example_file
def test_no_realistic_secret_literal_near_credential_words(path: Path) -> None:
    """A long opaque literal on a credential-mentioning line is what a real token looks like."""
    offenders = [
        (lineno, literal)
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if CREDENTIAL_WORD.search(line)
        for literal in LONG_LITERAL.findall(line)
    ]
    assert not offenders, f"credential-like literal(s) in {path}: {offenders}"


def test_readme_uses_the_agreed_sentinels() -> None:
    """The README uses the sentinels verbatim, which is what keeps this scan deterministic."""
    readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
    assert TOKEN_SENTINEL in readme
    assert ADDRESS_SENTINEL in readme


def test_request_corpus_carries_no_credential_fields() -> None:
    """Flow parameters never carry credentials, so no request body may contain one."""
    for body in sorted((EXAMPLE_DIR / "requests").glob("*.json")):
        text = body.read_text(encoding="utf-8")
        assert not CREDENTIAL_WORD.search(text), f"{body} mentions a credential field"
