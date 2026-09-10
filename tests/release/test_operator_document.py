"""What the deployment guide and the archive's own copy of it have to say.

The bundle ships a document because a host that has the archive and nothing else
still has to be told what to do with it. Two documents for two audiences drift,
so what a deployment cannot be operated without is asserted against both — and
against the code that decides it, rather than against a list written here. A
verb, a record field, or a summary key named only in this file would go on
passing after the thing it describes was renamed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infrahub_sync.client.models import PublicRunResource
from infrahub_sync.plan.models import ApplyRecord

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDE = REPO_ROOT / "docs" / "docs" / "compose-deployment.mdx"
OPERATOR_DOCUMENT = REPO_ROOT / "deploy" / "compose" / "OPERATING.md"
DOCUMENTS = (GUIDE, OPERATOR_DOCUMENT)

ENTRY_POINT = REPO_ROOT / "deploy" / "compose" / "infrahub-sync-compose"

# The lifecycle an operator with either document can reach. Compared against what
# the entry point really dispatches on, so this floor cannot silently become the
# whole list if the dispatch block stops being readable.
LIFECYCLE = frozenset({"init", "preflight", "start", "status", "logs", "stop", "restart", "reset"})

# What this alpha withholds. A document that stopped saying one of these would be
# offering a guarantee the topology does not carry.
WITHHELD = ("no in-place state migration", "backup or restore")

# What a host has to do before it trusts either artifact: the archive against the
# checksum shipped beside it, and the image against the digest a record names.
VERIFICATION = ("sha256sum -c", "docker image inspect")

# Replacing this alpha, in the order that makes it a replacement rather than a
# restart. `init` between them is the step that generates the identity the
# removed volumes were labelled for.
REPLACEMENT = ("infrahub-sync-compose reset", "infrahub-sync-compose init", "infrahub-sync-compose start")

# The two records an uncertain write leaves, as the service really writes them.
# `interrupted`/`ambiguous` is a write execution that ended without reporting;
# `apply-failed`/`failed` is an apply that began writing and then failed.
UNCERTAIN_PHASES = ("interrupted", "apply-failed")
UNCERTAIN_OUTCOMES = ("ambiguous", "failed")
# Derived, because these are the names an operator greps for. The safety bit is a
# field of the run resource; the partial-write marker is a summary key the apply
# record itself decides.
SAFETY_FIELD = "reconciliation_required"
PARTIAL_WRITE_KEY = "may_have_partially_written"

# What a documented recipe needs before it can be run. A variable used before it
# is set leaves an operator sending an empty value.
REQUEST_VARIABLES = ("INFRAHUB_SYNC_API_URL", "CURL_CONFIG")
PRINCIPAL_VARIABLE = "INFRAHUB_SYNC_API_TOKEN"
# Where `init` puts the API principal, and the field inside it. Both are read off
# the entry point below, so a change to either fails rather than leaving the
# documented extraction quietly yielding an empty string.
PRINCIPAL_SETTING = "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS"
PRINCIPAL_FIELD = '"token": "'


def documented(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def prose(path: Path) -> str:
    """Return one document with its line breaks collapsed.

    Two documents wrap differently, so a phrase spanning a line break is present
    in one and absent in the other while both say the same thing.
    """
    return " ".join(documented(path).split())


def lifecycle_verbs() -> set[str]:
    """Return the subcommands the entry point dispatches on.

    Read from its own `case` block rather than listed here, so a verb added to the
    entry point fails this until both documents name it.
    """
    body = documented(ENTRY_POINT)
    opened = body.index('case "${1:-}" in')
    dispatch = body[opened : body.index("esac", opened)]
    found = {
        head.strip()
        for line in dispatch.splitlines()
        for head, separator, _ in [line.partition(")")]
        if separator and head.strip().isalpha()
    }
    return found - {"help", "case"}


def refusal_families() -> set[str]:
    """Return every family name the entry point can refuse with.

    Read off its own `refuse` calls. A family an operator can be shown and cannot
    look up is a dead end at exactly the moment they need the document.
    """
    body = documented(ENTRY_POINT)
    return {
        word.strip()
        for line in body.splitlines()
        if " refuse " in line
        for _, _, tail in [line.partition(" refuse ")]
        for word in [tail.split()[0]]
        if word.replace("-", "").isalpha()
    }


def test_the_entry_point_dispatch_block_is_readable() -> None:
    """Guards every derivation below against reading nothing and proving nothing."""
    assert lifecycle_verbs() >= LIFECYCLE, f"the dispatch block yielded {sorted(lifecycle_verbs())}"
    assert len(refusal_families()) > len(LIFECYCLE), f"only {sorted(refusal_families())} were read"


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda path: path.name)
def test_both_documents_name_every_lifecycle_verb(path: Path) -> None:
    """An operator with one of these documents can reach the whole lifecycle."""
    body = prose(path)
    missing = [verb for verb in sorted(lifecycle_verbs()) if f"infrahub-sync-compose {verb}" not in body]

    assert not missing, f"{path.name} names no {missing}"


def test_the_shipped_document_explains_every_refusal_a_host_can_be_shown() -> None:
    """The archive's copy is the only thing a host without the site can read.

    The site guide documents the families an operator meets in normal use; this
    one is the fallback, so it accounts for all of them.
    """
    body = prose(OPERATOR_DOCUMENT)
    missing = sorted(family for family in refusal_families() if f"`{family}`" not in body)

    assert not missing, f"{OPERATOR_DOCUMENT.name} explains no {missing}"


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda path: path.name)
@pytest.mark.parametrize("step", VERIFICATION)
def test_both_documents_say_how_to_verify_what_arrived(path: Path, step: str) -> None:
    """A bundle and an image are both taken on trust unless something checks them."""
    assert step in prose(path)


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda path: path.name)
@pytest.mark.parametrize("withheld", WITHHELD)
def test_both_documents_withhold_what_this_alpha_does_not_promise(path: Path, withheld: str) -> None:
    """A replacement procedure read as an upgrade is the misreading that costs data."""
    assert withheld in prose(path)


def commands(path: Path) -> list[str]:
    """Return every line of shell one document shows, in order.

    Prose is dropped, so a variable named in a sentence is not read as a use of
    it; indentation is kept, because a `curl` continuation line is where the
    document's own variables actually appear.
    """
    return [line for block in command_blocks(path) for line in block]


def command_blocks(path: Path) -> list[list[str]]:
    """Return each fenced shell block of one document as its list of lines."""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in documented(path).splitlines():
        if line.startswith("```"):
            if current is None:
                current = [] if line.startswith("```bash") else None
            else:
                blocks.append(current)
                current = None
        elif current is not None:
            current.append(line)
    return blocks


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda path: path.name)
def test_both_documents_describe_replacement_as_reset_then_init_then_start(path: Path) -> None:
    """The documented procedure is the one the qualification exercises, and it is not an upgrade.

    Asserted as one ordered block rather than as two positions in the whole
    document: `reset` and `start` each appear in the lifecycle section too, so
    "a reset somewhere above a start" is satisfied by a document that never
    describes replacement at all. What makes it a replacement is `init` between
    them, generating the identity the removed volumes were labelled for.
    """
    procedure = [
        block
        for block in command_blocks(path)
        if [step for step in REPLACEMENT if any(step in line for line in block)] == list(REPLACEMENT)
        and _ordered(block, REPLACEMENT)
    ]

    assert procedure, f"{path.name} has no block running {' then '.join(REPLACEMENT)} in that order"
    assert "cold bootstrap" in documented(path)


def _ordered(block: list[str], steps: tuple[str, ...]) -> bool:
    """Report whether every step appears in this block in the order given."""
    offset = 0
    for step in steps:
        found = [index for index, line in enumerate(block) if step in line and index >= offset]
        if not found:
            return False
        offset = found[0] + 1
    return True


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda path: path.name)
def test_both_documents_name_the_safety_bit_the_run_resource_carries(path: Path) -> None:
    """Taken from the resource an operator reads, so a renamed field fails here.

    This is the one field that answers "does this run need reconciling" without
    parsing failure evidence, which is why it is on the run and not in `results`.
    """
    assert SAFETY_FIELD in PublicRunResource.model_fields, "the run resource no longer carries the safety bit"
    assert SAFETY_FIELD in prose(path), f"{path.name} does not name {SAFETY_FIELD}"


def test_the_shipped_document_distinguishes_the_two_uncertain_write_records() -> None:
    """An operator who cannot tell them apart cannot tell what is already written.

    One ended without reporting and may have written; the other is known to have
    started writing and failed. They call for the same next action and carry
    different evidence, so both are named with the phase and outcome the service
    actually records.
    """
    body = prose(OPERATOR_DOCUMENT)

    assert PARTIAL_WRITE_KEY in ApplyRecord().as_summary_keys(), "the apply record no longer marks a partial write"
    for named in (*UNCERTAIN_PHASES, *UNCERTAIN_OUTCOMES, PARTIAL_WRITE_KEY, "fresh plan"):
        assert named in body, f"{OPERATOR_DOCUMENT.name} does not name {named}"
    assert "does not retry" in body, "the document does not say the deployment leaves the write alone"


def test_the_guide_states_no_checkout_is_needed_to_deploy() -> None:
    """The subject is a released archive, so the instruction cannot be a directory in this tree."""
    body = prose(GUIDE)

    assert "no product checkout" in body
    assert "belong to a later unit" not in body


def test_the_guide_points_at_the_copy_the_archive_carries() -> None:
    """An operator reading the site has to know the archive answers on its own."""
    assert OPERATOR_DOCUMENT.name in documented(GUIDE)


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda path: path.name)
def test_no_document_puts_the_bearer_token_on_a_command_line(path: Path) -> None:
    """A header expanded into argv is readable by every process on the host.

    The authenticated reads an operator makes go through `cli`, which presents
    the token `init` generated from inside the deployment's own network. What is
    left is checked as an equality over every shell line, so a returning recipe
    fails here rather than being noticed by a reader.
    """
    exposed = [line.strip() for line in commands(path) if "Authorization: Bearer $" in line]

    assert exposed == [], f"{path.name} expands the bearer token into a command line"
    assert "infrahub-sync-compose cli runs show" in documented(path), f"{path.name} does not read a run through the CLI"
    assert "infrahub-sync-compose cli runs results" in documented(path), (
        f"{path.name} does not read a run's results through the CLI"
    )


def test_the_shipped_document_needs_no_credential_of_its_own() -> None:
    """Every authenticated read it documents is a CLI call, so it derives no token."""
    body = documented(OPERATOR_DOCUMENT)

    assert PRINCIPAL_SETTING in body, f"{OPERATOR_DOCUMENT.name} does not say which setting holds the token"
    assert "curl" not in body, f"{OPERATOR_DOCUMENT.name} makes a direct HTTP call it no longer needs"
    assert f"{PRINCIPAL_VARIABLE}=$(" not in body, f"{OPERATOR_DOCUMENT.name} extracts a token it does not use"


def test_the_site_guide_derives_every_variable_its_one_direct_recipe_needs() -> None:
    """The direct call is for the one route the CLI has no command for.

    A variable used before it is set leaves an operator sending an empty header,
    which the API answers with a refusal that looks like a permissions problem.
    """
    lines = commands(GUIDE)

    def assigns(line: str, variable: str) -> bool:
        """Whether one line sets the variable, exported or not."""
        head = line.lstrip().removeprefix("export ")
        return head.startswith(f"{variable}=")

    for variable in REQUEST_VARIABLES:
        used = [index for index, line in enumerate(lines) if f"${variable}" in line and not assigns(line, variable)]
        assigned = [index for index, line in enumerate(lines) if assigns(line, variable)]

        assert used, f"{GUIDE.name} never uses ${variable}"
        assert assigned, f"{GUIDE.name} uses ${variable} without ever setting it"
        assert min(assigned) < min(used), f"{GUIDE.name} uses ${variable} before it is set"


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda path: path.name)
def test_no_document_sources_or_renders_the_credential_file(path: Path) -> None:
    """`operator.env` holds every other credential of the deployment.

    Sourcing it exports all of them; printing the line renders one.
    """
    body = documented(path)

    for sourcing in ("source operator.env", ". operator.env", "set -a"):
        assert sourcing not in body, f"{path.name} sources the credential file with {sourcing!r}"
    for rendering in ("cat operator.env", f"echo ${PRINCIPAL_VARIABLE}", f'echo "${PRINCIPAL_VARIABLE}"'):
        assert rendering not in body, f"{path.name} renders a credential with {rendering!r}"


def test_the_extraction_the_site_guide_gives_matches_what_init_writes() -> None:
    """The pattern is only useful while it matches the line the entry point really generates.

    Read off the entry point's own heredoc rather than restated here, so changing
    how the token is written fails this instead of leaving the operator with a
    private curl configuration that silently holds an empty header.
    """
    generated = [line for line in documented(ENTRY_POINT).splitlines() if line.startswith(f"{PRINCIPAL_SETTING}=")]

    assert len(generated) == 1, f"the entry point writes {len(generated)} {PRINCIPAL_SETTING} lines"
    assert PRINCIPAL_FIELD in generated[0], (
        f"the entry point no longer writes {PRINCIPAL_FIELD!r}, so the documented extraction cannot match it"
    )
    assert PRINCIPAL_FIELD in documented(GUIDE), f"{GUIDE.name} does not extract the field the entry point writes"
