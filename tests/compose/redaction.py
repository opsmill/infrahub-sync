"""The one boundary every retained Compose and Docker stream passes through.

A Compose command's output carries whatever it was interpolated with. `docker
compose config` prints every connection string in full; `docker inspect` prints
every environment entry a container was given; a failing `up` prints whichever
of those the engine happened to quote back. The suite retains all of it, and
then renders it into assertion messages, `pytest.fail` text, and the evidence a
gate keeps. A credential that reaches any of those has left the deployment.

So capture is the boundary, not rendering. `capture` is the only place this
suite runs a command whose output it keeps, and the value it returns is redacted
before anything can hold a reference to it. There is nothing to remember to do
at the point a message is built, which is the point a leak would otherwise be
introduced.

Two things are redacted, and the difference matters:

* every value registered as a secret for this session -- the generated canaries,
  the operator's own inputs, the fixture's token. Exact values, so a credential
  is caught wherever it appears and in whatever it is embedded in.
* the *forms* a credential takes -- URL userinfo, a bearer token, a secret-named
  assignment in shell, JSON, or YAML. These catch what the registry cannot: a
  credential this session never generated, printed by something it did not
  configure.

The one deliberate exception is `Captured.unredacted`, which the leak sweeps
use. A sweep searches raw output for known values and reports the *names* it
found; it never renders the text it searched. Searching redacted output instead
would make every sweep pass by construction.
"""

from __future__ import annotations

import re
import subprocess  # noqa: S404 -- this module is the fixed-argv capture boundary itself
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

REDACTED = "[redacted]"

# Below this length a "secret" is a substring of ordinary output, and redacting
# it would corrupt what an operator reads without protecting anything. Every
# credential this suite plants is far longer.
SHORTEST_SECRET = 8


class SecretRegistry:
    """The exact values this session must never render."""

    def __init__(self) -> None:
        self._values: set[str] = set()

    def register(self, *values: str) -> None:
        """Record values whose appearance in retained output is a leak."""
        for value in values:
            text = str(value)
            if len(text) >= SHORTEST_SECRET:
                self._values.add(text)

    def values(self) -> frozenset[str]:
        """Return every registered value."""
        return frozenset(self._values)

    @staticmethod
    def leaked(text: str, named: Mapping[str, str]) -> list[str]:
        """Return the names of the given credentials that appear in raw text.

        The names, never the values: a failure message is retained output too.
        """
        return sorted(name for name, value in named.items() if value and value in text)


SECRETS = SecretRegistry()


# A credential-bearing setting name, in any of the spellings the services here
# use. Matched case-insensitively, so `token`, `TOKEN`, and `Token` are one rule.
SECRET_NAME = r"[A-Za-z0-9_.-]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|ACCESS_KEY|CREDENTIAL)[A-Za-z0-9_.-]*"  # noqa: S105 -- a pattern that recognises credential-bearing names, not a credential

# One JSON string, escapes and all. Compose and Docker both print structured
# output whose values are JSON-encoded documents of their own, so a value pattern
# that stopped at the first quote would cut one in half and leave the rest
# behind -- unparseable, and still carrying the credential.
JSON_STRING = r"\"(?:\\.|[^\"\\])*\""

# Each pattern keeps the part that identifies what was redacted and replaces the
# part that is the credential, so the output stays readable and stays parseable:
# a redacted JSON document is still JSON.
FORMS: tuple[tuple[re.Pattern[str], str], ...] = (
    # `"NAME": "value"` -- a JSON member, including one whose value is itself a
    # JSON document, as the API's principals are.
    (re.compile(rf"(?i)(?P<lead>\"{SECRET_NAME}\"\s*:\s*){JSON_STRING}"), rf'\g<lead>"{REDACTED}"'),
    # `"NAME=value"` -- one entry of a `docker inspect` environment array, whose
    # value is a whole JSON string however much punctuation it contains.
    (re.compile(rf"(?i)\"(?P<name>{SECRET_NAME})=(?:\\.|[^\"\\])*\""), rf'"\g<name>={REDACTED}"'),
    # `scheme://role:credential@host` -- every connection string this bundle uses.
    (re.compile(r"(?P<lead>[a-zA-Z][\w+.-]*://[^\s/:@\"']+:)[^\s/@\"']+@"), rf"\g<lead>{REDACTED}@"),
    # An HTTP authorization header, however it was quoted.
    (re.compile(r"(?i)(?P<lead>\bbearer\s+)[^\s\"',}\\]+"), rf"\g<lead>{REDACTED}"),
    # `NAME=value` -- an unquoted environment entry in a shell line.
    (re.compile(rf"(?i)(?P<lead>\b{SECRET_NAME}=)[^\s\"']+"), rf"\g<lead>{REDACTED}"),
    # `NAME: value` -- the YAML `docker compose config` prints without `--format json`.
    (re.compile(rf"(?im)^(?P<lead>\s*{SECRET_NAME}:[ \t]+)\S.*$"), rf"\g<lead>{REDACTED}"),
)


def redact(text: str, *, secrets: Iterable[str] | None = None) -> str:
    """Return text with every registered value and credential form removed.

    Exact values first: a canary embedded in a form is then already gone, and
    the form pass has nothing left to find. Longest first, so a value that
    contains another is not left with a redacted fragment inside it, and by
    value after that, so the same input redacts the same way every time.
    """
    if not text:
        return text
    redacted = text
    known = SECRETS.values() if secrets is None else frozenset(str(value) for value in secrets)
    for value in sorted(known, key=lambda candidate: (-len(candidate), candidate)):
        if len(value) >= SHORTEST_SECRET:
            redacted = re.sub(re.escape(value), REDACTED, redacted)
    for pattern, replacement in FORMS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


class Captured:
    """One command's output, redacted before anything can hold a reference to it.

    A drop-in for what `subprocess.run` returns, minus the streams a caller
    could render unredacted by accident. `stdout` and `stderr` have already been
    through the boundary; the raw text is reachable only by asking for it by
    name, which is what makes a sweep's use of it deliberate.
    """

    def __init__(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.returncode = completed.returncode
        self.stdout = redact(completed.stdout or "")
        self.stderr = redact(completed.stderr or "")
        self._raw = (completed.stdout or "") + (completed.stderr or "")

    @property
    def output(self) -> str:
        """Both redacted streams, in the order a reader expects them."""
        return self.stdout + self.stderr

    def unredacted(self) -> str:
        """Raw output, for sweeps that report names and never render text.

        Every other reader wants `stdout`, `stderr`, or `output`. A caller that
        renders what this returns has moved the leak from the deployment into
        the evidence.
        """
        return self._raw


def capture(
    argv: Sequence[str],
    *,
    timeout: int,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> Captured:
    """Run one fixed-argv command and return its output, redacted at capture.

    Every Compose and Docker invocation this suite retains output from comes
    through here. That is the whole design: there is one place where a stream
    becomes a value the suite holds, so there is one place to redact it.
    """
    return Captured(
        subprocess.run(  # noqa: S603 -- fixed argv from this suite's own drivers
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=dict(env) if env is not None else None,
            cwd=cwd,
        )
    )
