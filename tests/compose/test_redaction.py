"""The redaction boundary, and the proof that the suite's output goes through it.

Two halves, and the second is the one that matters.

The first checks `redact` against the forms a credential takes: a connection
string's userinfo, an authorization header, a secret-named assignment in shell,
JSON, or YAML, and any exact value registered for this session. Those are
properties of the function.

The second checks that the suite cannot retain a Compose stream that skipped it.
Each of those cases runs one real Compose command whose output is known to carry
a credential, and compares what `capture` returned against what the same command
printed. Deleting the `redact` calls from `Captured`, or reaching around
`capture` to `subprocess.run`, fails them -- which is the only thing that keeps
the boundary from quietly becoming optional.
"""

from __future__ import annotations

import json
import os
import subprocess  # noqa: S404 -- one deliberate unredacted run, to prove the boundary discriminates
from typing import TYPE_CHECKING

import pytest

from tests.compose.conftest import BUNDLE, COMPOSE_FILE, DEFAULTS_FILE, compose
from tests.compose.redaction import REDACTED, SECRETS, Captured, SecretRegistry, capture, redact

if TYPE_CHECKING:
    from collections.abc import Mapping

# A value shaped like the ones `init` generates, long enough to be a credential
# rather than a substring of ordinary output.
PLANTED = "canary-redaction-2b7f1c9d4e6a8b3c5d7e9f01"

# One row per form a credential is printed in by something this suite runs.
FORMS = [
    pytest.param(
        f"postgresql://infrahub_sync:{PLANTED}@postgres:5432/infrahub_sync",
        "a product connection string",
        id="url-userinfo",
    ),
    pytest.param(
        f"postgresql+asyncpg://prefect:{PLANTED}@postgres:5432/prefect",
        "a Prefect connection string",
        id="url-userinfo-driver",
    ),
    pytest.param(f"Authorization: Bearer {PLANTED}", "an authorization header", id="bearer"),
    pytest.param(f"authorization: bearer {PLANTED}", "a lowercased header", id="bearer-lowercase"),
    pytest.param(f"AWS_SECRET_ACCESS_KEY={PLANTED}", "an environment entry", id="assignment-env"),
    pytest.param(f"INFRAHUB_API_TOKEN={PLANTED}", "a destination credential entry", id="assignment-token"),
    pytest.param(f"INFRAHUB_SYNC_PREFECT_PASSWORD={PLANTED}", "a role password entry", id="assignment-password"),
    pytest.param(f'"AWS_SECRET_ACCESS_KEY": "{PLANTED}"', "a JSON member", id="json-member"),
    pytest.param(f'{{"operator": {{"token": "{PLANTED}"}}}}', "a nested JSON principal", id="json-nested"),
    pytest.param(f"  INFRAHUB_API_TOKEN: {PLANTED}", "a YAML mapping entry", id="yaml-entry"),
]


@pytest.mark.parametrize(("text", "description"), FORMS)
def test_a_credential_form_does_not_survive_the_boundary(text: str, description: str) -> None:
    """The forms are what catch a credential this session never generated.

    An exact-value registry cannot see a password the deployment was configured
    with elsewhere, or one a service quotes back from its own configuration. The
    shape it is printed in is the only thing left to recognise it by.
    """
    assert PLANTED not in redact(text), f"{description} carried its credential through the boundary"
    assert REDACTED in redact(text), f"{description} was not marked as redacted"


@pytest.mark.parametrize(("text", "description"), FORMS)
def test_redaction_keeps_the_part_that_says_what_was_redacted(text: str, description: str) -> None:
    """An operator reading a failure has to be able to tell which setting it was.

    Blanking the line would make every failure look the same, so the name, the
    role, and the host survive and only the credential does not.
    """
    del description
    redacted = redact(text)
    prefix = text.split(PLANTED, 1)[0].rstrip("\"' ")

    assert prefix.strip(), text
    assert prefix in redacted, redacted


def test_a_redacted_json_document_is_still_a_json_document() -> None:
    """`docker compose config --format json` is parsed after it is redacted.

    A boundary that broke the model would have to be bypassed by every test that
    reads one, which is the same as not having a boundary.
    """
    document = {
        "services": {
            "sync-api": {
                "environment": {
                    "INFRAHUB_SYNC_DATABASE_URL": f"postgresql://infrahub_sync:{PLANTED}@postgres:5432/db",
                    "AWS_SECRET_ACCESS_KEY": PLANTED,
                    "INFRAHUB_SYNC_SERVICE_HOST": "0.0.0.0",  # noqa: S104 -- the container listener the bundle sets
                }
            }
        }
    }

    reparsed = json.loads(redact(json.dumps(document, indent=2)))

    environment = reparsed["services"]["sync-api"]["environment"]
    assert PLANTED not in json.dumps(reparsed)
    assert environment["INFRAHUB_SYNC_SERVICE_HOST"] == "0.0.0.0"  # noqa: S104 -- a value read back, not bound


def test_an_ordinary_identifier_is_left_alone() -> None:
    """Container identifiers, digests, and ports are read back out of this output.

    Over-redaction is not the safe direction: a boundary that mangled an image
    digest or a container id would be worked around rather than fixed.
    """
    text = (
        "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n"
        "cafe1234beef 127.0.0.1:8021->8000/tcp\n"
        "INFRAHUB_SYNC_SERVICE_HOST=0.0.0.0\n"
    )

    assert redact(text) == text


def test_a_registered_value_is_removed_wherever_it_appears() -> None:
    """The generated canaries take no particular form; they are matched exactly."""
    registry = SecretRegistry()
    registry.register(PLANTED)

    redacted = redact(f"the worker refused: {PLANTED} was rejected", secrets=registry.values())

    assert PLANTED not in redacted
    assert REDACTED in redacted


def test_a_short_value_is_not_treated_as_a_secret() -> None:
    """Redacting a three-character value would corrupt output and protect nothing."""
    registry = SecretRegistry()
    registry.register("ok")

    assert registry.values() == frozenset()


def test_a_sweep_reports_names_and_never_values() -> None:
    """A leak report is retained output too, so it names the credential it found."""
    registry = SecretRegistry()

    found = registry.leaked(f"...{PLANTED}...", {"product": PLANTED, "prefect": "canary-prefect-unused-value"})

    assert found == ["product"]


# ---------------------------------------------------------------------------
# The boundary the suite actually runs through
# ---------------------------------------------------------------------------


@pytest.fixture
def leaking_environment(contract_environment: dict[str, str]) -> dict[str, str]:
    """Contract inputs whose credentials are this session's registered canaries.

    `docker compose config` prints every one of them back, which makes it the
    smallest real command that proves the boundary is in the path.
    """
    planted = {
        **contract_environment,
        "INFRAHUB_SYNC_DATABASE_URL": f"postgresql://infrahub_sync:{PLANTED}@postgres:5432/infrahub_sync",
        "INFRAHUB_SYNC_S3_SECRET_KEY": PLANTED,
        "INFRAHUB_API_TOKEN": PLANTED,
    }
    SECRETS.register(PLANTED)
    return planted


def raw_compose_config(environment: Mapping[str, str]) -> str:
    """Run the same command `compose` runs, deliberately around the boundary.

    This exists so the assertions below discriminate. Without it, a test that
    found no credential in redacted output would pass just as well against a
    command that never printed one.
    """
    completed = subprocess.run(  # noqa: S603 -- fixed argv, and the point of this helper
        [  # noqa: S607 -- Docker is resolved from the gate's PATH, as everywhere else here
            "docker",
            "compose",
            "--env-file",
            str(DEFAULTS_FILE),
            "--file",
            str(COMPOSE_FILE),
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        cwd=BUNDLE,
        env=dict(environment),
    )
    return completed.stdout + completed.stderr


def test_the_command_this_boundary_guards_really_does_print_the_credential(
    compose_version: str, leaking_environment: dict[str, str]
) -> None:
    """The premise, checked rather than assumed.

    If `docker compose config` ever stopped echoing interpolated credentials,
    the cases below would keep passing while proving nothing at all.
    """
    del compose_version

    assert PLANTED in raw_compose_config({**os.environ, **leaking_environment})


def test_retained_compose_output_carries_no_credential(
    compose_version: str, leaking_environment: dict[str, str]
) -> None:
    """What the suite keeps is redacted; what the command printed was not.

    Removing the `redact` calls from `Captured`, or running Compose through
    anything other than `capture`, fails here.
    """
    del compose_version

    retained = compose(["config", "--format", "json"], environment=leaking_environment)

    assert retained.returncode == 0, retained.stderr
    assert PLANTED not in retained.output
    assert PLANTED not in json.dumps(json.loads(retained.stdout))


def test_a_failing_compose_command_renders_no_credential(
    compose_version: str, leaking_environment: dict[str, str]
) -> None:
    """A refusal is the output most likely to be rendered into evidence.

    Compose quotes the value it could not use, so a failure is exactly where an
    unredacted stream would surface.
    """
    del compose_version

    refused = compose(["config", "--format", "json"], environment={**leaking_environment, "INFRAHUB_SYNC_IMAGE": ""})

    assert refused.returncode != 0
    assert PLANTED not in refused.output


def test_the_raw_stream_stays_reachable_for_the_leak_sweeps(
    compose_version: str, leaking_environment: dict[str, str]
) -> None:
    """The sweeps search raw output, or they pass by construction.

    This is the property that keeps every canary row in this suite honest: the
    boundary redacts what is rendered without hiding what is searched.
    """
    del compose_version

    retained = compose(["config", "--format", "json"], environment=leaking_environment)

    assert PLANTED in retained.unredacted()
    assert SECRETS.leaked(retained.unredacted(), {"planted": PLANTED}) == ["planted"]


def test_capture_is_what_produces_a_redacted_result() -> None:
    """`Captured` is not something a caller can be handed a raw equivalent of."""
    completed = subprocess.CompletedProcess(args=["true"], returncode=0, stdout=f"token={PLANTED}", stderr="")

    wrapped = Captured(completed)

    assert PLANTED not in wrapped.output
    assert PLANTED in wrapped.unredacted()


def test_capture_runs_the_command_and_redacts_in_one_step() -> None:
    """One call, one boundary: there is no window where a raw stream is a value."""
    result = capture(["/bin/sh", "-c", f'printf "INFRAHUB_API_TOKEN={PLANTED}"'], timeout=30)

    assert result.returncode == 0
    assert PLANTED not in result.output
    assert PLANTED in result.unredacted()
