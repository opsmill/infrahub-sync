"""The internal tutorial, against the workflow and the bundle it describes.

A procedure is wrong in the one way that matters when it names an artifact, a
window or a job that the thing it describes no longer has — and its reader is a
teammate on a clean host with no way to tell the difference. So every name here
is read off the candidate workflow and the bundle entry point rather than
written down twice.

It also has to stay internal. The artifacts are unpublished pre-release bytes,
so a page explaining how to download and run them belongs in `dev/`, not on the
Docusaurus site.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_workflow_contracts import (
    CANDIDATE_GROUPS,
    CANDIDATE_WINDOW_DAYS,
    CANDIDATE_WINDOW_NAME,
    CANDIDATE_WORKFLOW,
    CLEAN_HOST_JOB,
    RUN_TITLE,
    candidates,
    jobs,
    load,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDE = REPO_ROOT / "dev" / "guides" / "qualifying-an-internal-candidate.md"
INDEX = REPO_ROOT / "dev" / "guides" / "README.md"
DOCS_SITE = REPO_ROOT / "docs" / "docs"
SIDEBAR = REPO_ROOT / "docs" / "sidebars.ts"
ENTRY_POINT = REPO_ROOT / "deploy" / "compose" / "infrahub-sync-compose"

# The lifecycle a reader is taken through. Every verb, because the tutorial's
# whole purpose is that its reader reaches a working deployment and then takes
# it down again.
LIFECYCLE = ("init", "preflight", "start", "status", "logs", "restart", "stop", "reset")

# What the reader has to be told they cannot do. An operator who believes the
# window can be extended will discover otherwise on the day it lapses.
EXPIRY = ("cannot be extended", "same exact commit")

# An interpreter the runner or the host happens to ship is still an interpreter.
# The claim is that a host holding the artifact and nothing else can run it, so a
# procedure reaching for one has stopped demonstrating that.
FORBIDDEN_TOOLS = ("python ", "python3 ", "uv ", "uvx ", "pip ", "pipx ")

# The tools the procedure really does use, which therefore have to be declared
# before the reader reaches the first command that needs one.
DECLARED_TOOLS = ("jq", "sha256sum", "gh", "docker")

# A dispatched run refers to two commits and they are not interchangeable:
# `head_sha` is the tip of the ref the run started against, so it is the revision
# of the workflow definition, while the `sha` input is what was built. Equating
# them fails exactly when the branch has moved -- which is the case the route is
# built for, because a lapsed window is answered by rebuilding the same commit
# from a later tip.
CANDIDATE_COMMIT = "CANDIDATE_SHA"
WORKFLOW_REVISION = "WORKFLOW_REVISION"
HEAD_SHA_FIELD = "headSha"

# The identifiers and window the service alone can answer for, and the record's
# own group, which cannot appear in its own map.
TRANSPORT_FIELDS = ("expires_at", "created_at", ".digest")
SELF_EXCLUDED_GROUP = "infrahub-sync-qualification-record"

# The image the deployment and the CLI are both given: the configuration digest
# read out of the record and exported, never a tag and never an unbound name.
IMAGE_VARIABLE = "INFRAHUB_SYNC_IMAGE"
# The record the release generates into the archive. The operator names no
# image, so this is where the guide's image check has moved to.
BINDING_FILE = "image.bind"

# A single confirmed write with no reviewed plan between the request and the
# destination. It is a real capability and it is not what this procedure
# qualifies, because it demonstrates nothing about the admission path.
DIRECT_WRITE = "cli sync"

# The procedure changes directory twice, so anything one step writes and a later
# step reads is named by one absolute variable rather than by a relative path
# that silently means two different files.
WORK_VARIABLE = "WORK"
CARRIED_FILES = ("INVENTORY", "RECORDED")

# The file every credential of a deployment lives in. Rendering a line of it puts
# a destination token in a terminal, a scrollback buffer, and whatever the reader
# pastes into a report -- which is the one place a tutorial's own output must
# never reach.
CREDENTIAL_FILE = "operator.env"
CREDENTIAL_SETTINGS = ("INFRAHUB_API_TOKEN", "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS")
# Commands that put what they read on standard output no matter how they are
# called, and the two that do so only when they are not suppressed: `grep -q`
# reports a match without showing it, and `sed -i` edits the file in place.
# Reading those two as unconditional renderers would flag the very commands the
# document uses to set a value and confirm it without exposing one.
RENDERING_COMMANDS = ("cat", "head", "tail", "awk", "echo", "printf")
SUPPRESSED_BY = {"grep": "-q", "sed": "-i"}
# `cd` wherever it appears, not only at the start of a line: `mkdir -p x && cd x`
# changes directory too, and a check anchored to the line start misses it.
CHANGES_DIRECTORY = re.compile(r"(?:^|&&|;|\|)\s*cd\s+(\S+)").search
# What separates one command from the next, so a rendering command can be judged
# on its own invocation rather than on whatever else shares its line. Quoted
# spans are masked before this is applied: an alternation inside a pattern, as in
# `grep -E '^(A|B)='`, otherwise splits the command away from its own argument.
SEPARATORS = re.compile(r"&&|\|\||[|;]")
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
# Words that stand in front of a command without being one. Skipping them is
# what keeps `if cat …` from reading as an invocation of `if`.
SHELL_KEYWORDS = frozenset({"if", "elif", "then", "else", "while", "until", "do", "done", "!", "{", "}", "("})


def guide() -> str:
    return GUIDE.read_text(encoding="utf-8")


def test_the_guide_is_where_the_index_says_it_is() -> None:
    """A guide nobody can find is a guide nobody follows."""
    assert GUIDE.is_file(), GUIDE
    assert GUIDE.name in INDEX.read_text(encoding="utf-8"), f"{INDEX} does not link {GUIDE.name}"


def test_the_guide_is_not_on_the_public_documentation_site() -> None:
    """The artifacts are unpublished and unadvertised; a public page would advertise them."""
    assert not (DOCS_SITE / GUIDE.name).exists(), "the internal tutorial is on the docs site"
    assert not list(DOCS_SITE.rglob(GUIDE.name)), "the internal tutorial is under the docs site tree"
    assert GUIDE.stem not in SIDEBAR.read_text(encoding="utf-8"), "the internal tutorial is in the site sidebar"


def test_the_guide_names_every_artifact_group_the_run_retains() -> None:
    """Derived from the uploads, so a renamed or added group fails here.

    A reader downloading by name cannot discover that a group was renamed; they
    get an empty directory and a procedure that carries on regardless.
    """
    retained = {str(declared["name"]) for path, _job, _step, declared in candidates() if path == CANDIDATE_WORKFLOW}
    body = guide()

    assert retained, f"{CANDIDATE_WORKFLOW.name} retains nothing, so this proves nothing"
    missing = sorted(name for name in retained if name not in body)
    assert not missing, f"{GUIDE.name} never names {missing}"


def test_the_guide_states_the_window_the_workflow_really_asks_for() -> None:
    """A number written twice is a number that will disagree with itself."""
    declared = load(CANDIDATE_WORKFLOW)["env"][CANDIDATE_WINDOW_NAME]

    assert declared == CANDIDATE_WINDOW_DAYS
    assert f"{declared} days" in guide(), f"{GUIDE.name} does not state a {declared}-day window"


def test_the_guide_requires_both_jobs_of_the_run_to_have_succeeded() -> None:
    """Retained uploads are not a qualification: the downstream job is what qualified them.

    Both job names are read off the workflow, so splitting or renaming either one
    fails here rather than leaving a reader accepting a run that built bytes
    nothing ever ran.
    """
    defined = set(jobs(CANDIDATE_WORKFLOW))
    body = guide()

    assert CLEAN_HOST_JOB in defined
    missing = sorted(name for name in defined if f"`{name}`" not in body)
    assert not missing, f"{GUIDE.name} never names the {missing} job"


@pytest.mark.parametrize("verb", LIFECYCLE)
def test_the_guide_takes_its_reader_through_the_whole_lifecycle(verb: str) -> None:
    """Compared against the entry point, so a verb it no longer has is not documented here."""
    assert f"    {verb})" in ENTRY_POINT.read_text(encoding="utf-8"), f"the bundle has no {verb}"
    assert f"infrahub-sync-compose {verb}" in guide(), f"{GUIDE.name} never runs {verb}"


@pytest.mark.parametrize("stated", EXPIRY)
def test_the_guide_says_expiry_is_answered_by_rebuilding_the_same_commit(stated: str) -> None:
    """The window is not renewable, and the replacement is bound to the commit, not the bytes."""
    assert stated in guide(), f"{GUIDE.name} does not say {stated!r}"


def test_the_guide_sends_its_reader_to_a_destination_they_may_write_to() -> None:
    """The procedure applies a real write, so consent is part of the instruction."""
    body = guide()

    assert "disposable" in body, f"{GUIDE.name} does not say the destination must be disposable"
    assert "authorised to write to" in body, f"{GUIDE.name} does not require an authorised destination"


def prose(body: str) -> str:
    """Return the document with its line breaks collapsed.

    A sentence that wraps is present in the file and absent from any search for
    the phrase it contains, which would make every claim below depend on where
    the paragraph happened to break.
    """
    return " ".join(body.split())


def commands(body: str) -> str:
    """Return only the fenced shell of a document, so prose about a tool is not a use of it."""
    blocks: list[str] = []
    inside = False
    for line in body.splitlines():
        if line.startswith("```"):
            inside = line.startswith("```bash")
            continue
        if inside:
            blocks.append(line)
    return "\n".join(blocks)


def test_the_guide_keeps_the_two_commits_a_dispatched_run_refers_to_apart() -> None:
    """`head_sha` is the ref's tip, so it is the workflow's revision and not the built commit.

    They coincide only while the branch has not moved, and this route exists for
    the case where it has. A procedure that reads the built commit out of
    `head_sha` sends its reader to reject artifacts that are correct, or to
    accept artifacts built from something else.
    """
    body = guide()
    script = commands(body)

    assert RUN_TITLE in load(CANDIDATE_WORKFLOW), "the workflow states no run title to read the commit from"
    assert CANDIDATE_COMMIT in script, f"{GUIDE.name} never reads the candidate commit"
    assert WORKFLOW_REVISION in script, f"{GUIDE.name} never reads the workflow revision separately"
    assert "displayTitle" in script, f"{GUIDE.name} does not take the candidate commit from the run's title"
    assert "not interchangeable" in prose(body), f"{GUIDE.name} does not say the two commits are different things"


def test_the_guide_binds_the_built_revision_to_the_candidate_commit_not_the_ref() -> None:
    """`identity.revision` is `git rev-parse HEAD` of what was checked out, so it is the input.

    Comparing it against `head_sha` is the specific mistake: it passes only while
    the branch has not moved and fails on a legitimate rebuild.
    """
    script = commands(guide())
    # The variable the document reads `identity.revision` into. Named here so the
    # negative case below can be about the built revision specifically: reading
    # the *workflow* revision out of `head_sha` is correct and has to stay legal.
    built = "BUILT_FROM"
    comparing = [line for line in script.splitlines() if built in line and CANDIDATE_COMMIT in line]
    confused = [line for line in script.splitlines() if built in line and HEAD_SHA_FIELD in line]

    assert f"{built}=$(jq -r '.revision' identity/identity.json)" in script, (
        f"{GUIDE.name} does not read the built revision out of the identity the run recorded"
    )
    assert comparing, f"{GUIDE.name} never compares the built revision against {CANDIDATE_COMMIT}"
    assert not confused, f"{GUIDE.name} compares the built revision against {HEAD_SHA_FIELD}: {confused}"


def test_the_guide_verifies_the_service_inventory_for_every_retained_group() -> None:
    """Only the service can answer what it is holding, and for how long.

    The record cannot: it is written before its own upload exists, so its map
    covers six groups and the seventh has to be read from the service and
    recorded by hand.
    """
    script = commands(guide())
    body = guide()

    missing = sorted(name for name in CANDIDATE_GROUPS if name not in script)
    assert not missing, f"{GUIDE.name} never asks the service about {missing}"
    for field in TRANSPORT_FIELDS:
        assert field in script, f"{GUIDE.name} never reads {field} from the service"
    assert f"-eq {CANDIDATE_WINDOW_DAYS}" in script, (
        f"{GUIDE.name} does not check the granted window is exactly {CANDIDATE_WINDOW_DAYS} days"
    )
    assert "cannot appear in its own" in prose(body), (
        f"{GUIDE.name} does not explain why {SELF_EXCLUDED_GROUP} is verified separately"
    )


def test_the_guide_compares_the_recorded_identifiers_against_what_the_service_holds() -> None:
    """A record naming identifiers nothing holds describes bytes that are not there."""
    script = commands(guide())

    assert "recorded.tsv" in script, f"{GUIDE.name} does not extract what the record claims"
    assert "inventory.tsv" in script, f"{GUIDE.name} does not compare it against the service inventory"
    assert ".artifacts | to_entries" in script, f"{GUIDE.name} never reads the record's own artifact map"


def test_the_guide_keeps_the_kinds_of_digest_apart() -> None:
    """Three digests over three different things. Confusing them passes a check that did not happen."""
    body = guide()

    for named in ("Service transport digest", "Bundle file digest", "Image configuration digest"):
        assert named in body, f"{GUIDE.name} does not name the {named.lower()}"
    assert "not the same thing" in body, f"{GUIDE.name} does not warn that the digests are distinct"


def test_the_guide_qualifies_a_write_only_through_a_reviewed_plan() -> None:
    """A confirmed write with no reviewed plan between it and the destination proves nothing here.

    `sync sync` is a real capability and is deliberately absent: what is being
    qualified is the admission path, which is plan, read the saved plan, then
    apply that plan by its checksum.
    """
    script = commands(guide())

    assert DIRECT_WRITE not in script, f"{GUIDE.name} runs `{DIRECT_WRITE}`, which skips the reviewed plan"
    assert "cli diff" in script, f"{GUIDE.name} never plans"
    assert "cli runs plan" in script, f"{GUIDE.name} never reads the saved plan"
    assert "--expected-checksum" in script, f"{GUIDE.name} never binds the reviewed checksum to the apply"
    plan = script.index("cli runs plan")
    assert plan < script.index("cli apply"), f"{GUIDE.name} applies before reading the plan"


@pytest.mark.parametrize("tool", FORBIDDEN_TOOLS)
def test_the_guide_needs_no_interpreter_on_the_host(tool: str) -> None:
    """The claim is that the released artifact runs on a host that holds only the artifact."""
    assert tool not in commands(guide()), f"{GUIDE.name} runs {tool.strip()} on the host"


@pytest.mark.parametrize("tool", DECLARED_TOOLS)
def test_the_guide_declares_the_tools_it_actually_uses(tool: str) -> None:
    """A prerequisite discovered halfway through is a prerequisite the reader did not have."""
    body = guide()
    script = commands(body)
    # Everything before the first numbered step, which is all the reader has seen
    # when they run the first command. Case-insensitive because the table names
    # some of these as prose ("Docker") and some as literals (`jq`).
    prerequisites = body[: body.index("## 1.")].lower()

    assert tool in script, f"{GUIDE.name} declares {tool} and never uses it"
    assert tool in prerequisites, f"{GUIDE.name} uses {tool} without declaring it up front"


def test_the_guide_verifies_the_bundles_binding_against_the_digest_it_checked() -> None:
    """The deployment is never handed an image, so what has to be ordered is the check.

    Every container the procedure runs -- the API, the worker, the bootstrap job
    and the CLI -- comes from the record the archive shipped, which the operator
    does not write. What the guide still owes a reader is proof that the record
    names the candidate they verified, and the three steps have one order: read
    the digest out of the qualification record, export it, and compare the
    bundle's own binding against it. An unbound variable in that comparison
    compares against nothing and passes.
    """
    script = commands(guide())
    lines = script.splitlines()
    exported = next(
        (index for index, line in enumerate(lines) if line.strip() == f"export {IMAGE_VARIABLE}"),
        None,
    )
    compared = next(
        (index for index, line in enumerate(lines) if BINDING_FILE in line and f"${IMAGE_VARIABLE}" in line),
        None,
    )
    read_from_record = next(
        (index for index, line in enumerate(lines) if IMAGE_VARIABLE in line and "platforms" in line),
        None,
    )

    assert read_from_record is not None, f"{GUIDE.name} does not read the image digest out of the record"
    assert exported is not None, f"{GUIDE.name} never exports {IMAGE_VARIABLE}"
    assert compared is not None, f"{GUIDE.name} never checks {BINDING_FILE} against {IMAGE_VARIABLE}"
    assert read_from_record < exported < compared, (
        f"{GUIDE.name} compares against {IMAGE_VARIABLE} before verifying and exporting it"
    )


def test_the_guide_never_tells_a_reader_to_name_an_image_themselves() -> None:
    """The whole point of the shipped record is that this step no longer exists.

    A leftover instruction is worse than a missing one: it tells a reader to edit
    a setting the deployment does not read, and the deployment they end up with
    runs something else than the line they edited says.
    """
    script = commands(guide())

    assert f"{IMAGE_VARIABLE}=" not in script.replace(f"{IMAGE_VARIABLE}=$(", ""), (
        f"{GUIDE.name} still assigns {IMAGE_VARIABLE} somewhere an operator would edit"
    )
    assert "REPLACE-ME" not in script, f"{GUIDE.name} still refers to a placeholder nothing writes"


def test_every_file_carried_across_a_directory_change_is_named_absolutely() -> None:
    """A relative path written before a `cd` is not the file the later step reads.

    The service inventory is written in step 2 and read again in step 4, with a
    directory change between them, so a bare `inventory.tsv` names two different
    files and the second one does not exist. Every carried file is therefore one
    variable rooted at `$WORK`, and no bare form may survive anywhere.
    """
    script = commands(guide())
    lines = script.splitlines()
    defined = next((index for index, line in enumerate(lines) if line.startswith(f"{WORK_VARIABLE}=")), None)

    assert defined is not None, f"{GUIDE.name} defines no {WORK_VARIABLE}"
    assert f'cd "${WORK_VARIABLE}"' in script, f"{GUIDE.name} never enters {WORK_VARIABLE}"
    for carried in CARRIED_FILES:
        rooted = [line for line in lines if line.startswith(f"{carried}=")]
        assert rooted, f"{GUIDE.name} defines no {carried}"
        assert all(f"${WORK_VARIABLE}/" in line for line in rooted), f"{carried} is not rooted at ${WORK_VARIABLE}"
        assert min(lines.index(line) for line in rooted) > defined, f"{carried} is set before {WORK_VARIABLE}"

        # Every use of the file, excluding the line that defines where it is.
        bare = f"{carried.lower()}.tsv"
        unrooted = [
            line for line in lines if bare in line and f"${carried}" not in line and not line.startswith(f"{carried}=")
        ]
        assert not unrooted, f"{GUIDE.name} still names {bare} relatively: {unrooted}"


def test_no_directory_change_precedes_the_first_file_the_procedure_writes() -> None:
    """Run end to end from a fresh shell, the commands have to agree on where they are.

    A `cd` after something has already been written leaves that file behind, and
    every relative read after it resolves somewhere else.
    """
    lines = commands(guide()).splitlines()
    # Every `cd`, wherever it sits in the line: `mkdir -p x && cd x` changes
    # directory just as much as a line that begins with it, and looking only at
    # the start of a line is how a relative one gets back in.
    entered = [(index, target.group(1)) for index, line in enumerate(lines) if (target := CHANGES_DIRECTORY(line))]
    first_write = next((index for index, line in enumerate(lines) if ">" in line and "$" in line), None)

    assert entered, f"{GUIDE.name} never changes directory, so this proves nothing"
    assert first_write is not None, f"{GUIDE.name} writes no file"
    assert min(index for index, _target in entered) < first_write, (
        f"{GUIDE.name} writes a file before it enters a known directory"
    )
    relative = [
        (index, target)
        for index, target in entered
        if not target.startswith(('"$', "$")) and not target.startswith("infrahub-sync-compose-")
    ]
    assert not relative, f"{GUIDE.name} changes into a relative directory: {relative}"


@pytest.mark.parametrize("setting", CREDENTIAL_SETTINGS)
def test_the_guide_never_renders_a_credential_it_touches(setting: str) -> None:
    """A tutorial's own output ends up in a terminal, a scrollback, and a pasted report.

    Confirming a setting is present is legitimate; printing the line that holds
    it is not. `grep -q` is how the document does the first without the second,
    so what is checked here is that nothing which writes its match to standard
    output is ever pointed at the credential file.
    """
    lines = commands(guide()).splitlines()
    # Judged per command, not per line. A line-wide exemption for `-q` is
    # satisfied by any other quiet command sharing the line, which is how
    # `cat operator.env && ! grep -q …` would otherwise pass.
    rendering = [
        segment.strip()
        for line in lines
        for segment in segments(line)
        if CREDENTIAL_FILE in segment and _renders(segment)
    ]

    assert not rendering, f"{GUIDE.name} renders {CREDENTIAL_FILE}: {rendering}"
    exposing = [
        segment.strip()
        for line in lines
        for segment in segments(line)
        if f"${setting}" in segment and _renders(segment)
    ]
    assert not exposing, f"{GUIDE.name} renders {setting}: {exposing}"


def segments(line: str) -> list[str]:
    """Return one line's separate commands, without splitting inside a quoted span."""
    masked = QUOTED.sub(lambda found: "\x00" * len(found.group(0)), line)
    spans = []
    start = 0
    for separator in SEPARATORS.finditer(masked):
        spans.append(line[start : separator.start()])
        start = separator.end()
    spans.append(line[start:])
    return spans


def _renders(segment: str) -> bool:
    """Report whether one command writes what it reads to standard output.

    A `grep` renders unless it was told not to; everything in the list renders
    unconditionally. Leading shell keywords are skipped so `if cat …` is read as
    the `cat` it runs, and the command word is compared rather than the whole
    segment so a file name containing a command's name is not a match.
    """
    words = [word for word in segment.split() if word not in SHELL_KEYWORDS]
    if not words:
        return False
    command = words[0].removeprefix("!").rsplit("/", maxsplit=1)[-1]
    if command in SUPPRESSED_BY:
        flag = SUPPRESSED_BY[command]
        return not any(word.startswith(flag) for word in words[1:])
    return command in RENDERING_COMMANDS


def test_the_guide_confirms_what_it_can_without_rendering_a_credential() -> None:
    """Removing the rendering leaves the reader needing to know it worked.

    The binding is the one file here that holds no credential, so the guide reads
    it out loud and compares it. Every other confirmation still has to reach the
    terminal as a sentence rather than as a value, which the rendering scan below
    is what enforces.
    """
    script = commands(guide())

    assert BINDING_FILE in script, f"{GUIDE.name} never looks at the record the bundle ships"
    assert "OK: the bundle names the candidate you loaded" in script, (
        f"{GUIDE.name} never confirms the bundle names the verified candidate"
    )


def test_the_guide_reads_the_api_token_without_sourcing_the_credential_file() -> None:
    """`operator.env` holds every other credential too, so sourcing it exports all of them."""
    script = commands(guide())

    assert "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS" in script, f"{GUIDE.name} does not say where the token is"
    assert "operator.env" in script, f"{GUIDE.name} does not read the token from the file that holds it"
    for sourcing in ("source operator.env", ". operator.env", "set -a"):
        assert sourcing not in script, f"{GUIDE.name} sources the credential file with {sourcing!r}"
