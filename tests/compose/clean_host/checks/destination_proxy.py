"""The gate's own reverse proxy, and the only place a destination write is in flight.

Row 8's claim is that an *actual* ambiguous destination write is interrupted and
is not retried. That requires a moment in which the destination has completed a
write and the worker does not yet know it -- and there is exactly one such moment
per write: the response on the wire. Nothing readable from outside gets there. A
run phase leaves ``planned`` before any byte is sent, a log line is written after
the answer arrived, the write guard blocks before the SDK is called at all, and a
configured response delay is a timing hope rather than a state.

So the deployment's registered configuration names this proxy instead of the
pinned destination, and every destination request the deployment makes passes
through it. For every row but row 8 it forwards and answers, and nothing about it
is observable. For row 8 the check arms it, and the next request that is
structurally a GraphQL mutation is:

* claimed -- once, by renaming the arm, so exactly one request is held and the
  rows after this one are not;
* forwarded to the real pinned destination, whose answer must be a completed
  GraphQL write and not merely an answer;
* recorded as having completed upstream, durably, in a file that stays written;
* withheld from the Sync worker until the driver releases it.

While it is withheld the driver kills the worker. The write happened; the worker
never learned its outcome; that is the ambiguity row 8 exists to observe, and it
is induced rather than hoped for.

Nothing here is a product test hook. The product is configured with a destination
address, as any operator would configure it, and this is what is at that address.

Nothing about a request or a response is recorded. Every one of them carries the
destination's API token, and one of them carries the write itself.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, ClassVar

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterable

# The private directory the driver creates for row 8 and mounts into this
# container and row 8's check container, and into nothing else.
CONTROL = pathlib.Path(os.environ.get("CLEAN_HOST_PROXY_CONTROL", "/control"))

# Every state either side of the handshake records. Declared here because the
# driver names these files in shell and row 8's check imports them: two spellings
# of one handshake is a handshake that never completes.
READY = "proxy-ready"
ARM = "proxy-arm"
ARMED = "proxy-armed"
ACCEPTED_AT = "proxy-accepted-at"
UPSTREAM_COMPLETED = "proxy-upstream-completed"
UPSTREAM_FAILED = "proxy-upstream-failed"
RELEASE = "proxy-release"
ACKNOWLEDGED = "proxy-acknowledged"
EXPIRED = "proxy-expired"

# One absolute budget for the whole coordination, beginning at the instant this
# proxy accepts the target mutation and covering everything to the driver's
# acknowledgement: the forwarding, the durable signal, the check's direct read of
# the destination, the check's exit, the worker being killed, and the release.
#
# The bound that makes it a budget rather than a preference is the worker's own:
# `adapters/infrahub.py` gives the SDK a sixty-second timeout for one destination
# call. A hold that outlived it would be recorded as the worker's client timing
# out, which is a different failure from the interruption this row induces -- so
# this is a third of it, and there is one of it rather than a deadline per step.
PROXY_BUDGET_SECONDS = 20.0
HOLD_POLL_SECONDS = 0.25

# What an *unclaimed* request gets, and only an unclaimed one. It is not part of
# the budget above, because a request nobody armed for is not part of row 8's
# coordination at all -- it is ordinary traffic, and what actually bounds it is
# the sixty seconds the caller's own SDK allows. This stays under that, so a
# forwarding this proxy could not finish is never what the caller reports.
#
# The claimed request is bounded by what is left of the one budget instead, taken
# from the instant it was accepted. An independent timeout longer than the budget
# would let the forwarding alone outlast the whole coordination, and the row would
# then report a stalled destination rather than the interruption it arranged.
UNCLAIMED_TIMEOUT_SECONDS = 55.0

# The one sentence either side reports when that budget runs out. Both the check
# and the driver say exactly this, so neither can claim something else happened.
BUDGET_SENTENCE = "the coordination budget that began when the destination proxy accepted this run's mutation ran out"

# What a GraphQL request is, structurally. The route, the method, and a document
# whose operation is a mutation -- all three, because a plan reads the destination
# over the same route and holding a read would leave the destination unwritten.
GRAPHQL_ROUTE = "/graphql"
MUTATION_METHOD = "POST"
_LEADING_MUTATION = re.compile(r"\A\s*mutation(?![A-Za-z0-9_])")

# Headers that describe one connection rather than one message. Forwarded, they
# would describe this proxy's connection to the party on the other side of it.
HOP_BY_HOP = frozenset(
    {
        "connection",
        "content-length",
        "host",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

# What a held request is finally answered with, if the socket is still there at
# all. By then the worker that sent it has been killed; this exists so a request
# is never left without an answer on any path.
WITHHELD_STATUS = 504
UNREACHABLE_STATUS = 502


def control_path(name: str) -> pathlib.Path:
    """Return where one handshake state is recorded."""
    return CONTROL / name


def signal_proxy(name: str, text: str = "") -> None:
    """Record one state, which stays recorded: no step of this handshake is sampled."""
    control_path(name).write_text(text, encoding="utf-8")


def held(name: str) -> bool:
    """Answer whether one state has been recorded yet."""
    return control_path(name).exists()


def recorded(name: str) -> str:
    """Return what one recorded state says, or nothing when it was never recorded."""
    try:
        return control_path(name).read_text(encoding="utf-8")
    except OSError:
        return ""


def claim_arm() -> bool:
    """Take the arm if it is there, and take it exactly once.

    A rename is the claim, so two concurrent requests cannot both be the one this
    row holds -- and an arm that survived its claim would hold the recovery
    checks' own traffic after the driver restarted the deployment.
    """
    try:
        control_path(ARM).rename(control_path(ARMED))
    except OSError:
        return False
    return True


def disarm() -> None:
    """Drop the claimed arm, so nothing after the released write is ever held."""
    control_path(ARMED).unlink(missing_ok=True)


def remaining_budget(accepted_at: float) -> float:
    """Return what is left of the one budget that began when the mutation was accepted.

    Absolute, so every step reads the same clock: the forwarding, the hold, and
    the driver's own wait are shares of one interval rather than allowances of
    their own. A non-positive answer is the budget already gone.
    """
    return accepted_at + PROXY_BUDGET_SECONDS - time.time()


def is_target_mutation(method: str, path: str, body: bytes) -> bool:
    """Answer whether one request is structurally a GraphQL mutation.

    Structural, not heuristic: the row arms for a write, and a write is a mutation
    operation on the GraphQL route. A plan run reads the destination over the same
    route with the same method, and a proxy that held one of those reads would
    leave the destination unwritten -- which is the vacuous pass row 8 exists to
    exclude.
    """
    if method != MUTATION_METHOD or GRAPHQL_ROUTE not in path:
        return False
    try:
        document = json.loads(body)
    except ValueError:
        return False
    if not isinstance(document, dict):
        return False
    operation = document.get("query")
    if not isinstance(operation, str):
        return False
    without_comments = "\n".join(line for line in operation.splitlines() if not line.lstrip().startswith("#"))
    return _LEADING_MUTATION.match(without_comments) is not None


def upstream_verdict(status_code: int, body: bytes) -> str | None:
    """Return why an upstream answer is not a completed write, or nothing when it is.

    The row's precondition is that the destination *did* the write before the
    worker lost the answer. A hold placed over a request the destination rejected
    leaves nothing ambiguous to reconcile, and row 8 would then report success
    having interrupted a rejection.

    The reason reaches a refusal sentence and a retained artifact, so it is the
    status code and a fixed phrase. A GraphQL error message quotes the document
    that provoked it, which is declared configuration and destination content.
    """
    if not 200 <= status_code < 300:
        return f"the destination answered with HTTP {status_code}"
    try:
        document = json.loads(body)
    except ValueError:
        return f"the destination answered HTTP {status_code} with something that is not a GraphQL document"
    if not isinstance(document, dict):
        return f"the destination answered HTTP {status_code} with something that is not a GraphQL document"
    if document.get("errors"):
        return f"the destination answered HTTP {status_code} and reported GraphQL errors"
    if document.get("data") is None:
        return f"the destination answered HTTP {status_code} with no GraphQL data"
    return None


class Proxy(BaseHTTPRequestHandler):
    """One forwarded request, and for row 8 one held one."""

    protocol_version = "HTTP/1.1"
    # Set once before the server starts. A class attribute rather than a module
    # global so there is one client and one place it comes from.
    upstream: ClassVar[httpx.Client | None] = None

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - the base handler's own signature
        """Record nothing.

        Every request through this proxy carries the destination's API token in a
        header, and one of them carries the write itself. The base handler would
        put the request line on the error stream, which the gate retains.
        """

    def _forward(self, method: str) -> None:
        """Forward one request, and withhold the answer when this is row 8's mutation."""
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        # Claimed before the forwarding, because the instant recorded here is the
        # instant the one budget starts and the forwarding is the first thing
        # inside it.
        if is_target_mutation(method, self.path, body) and claim_arm():
            accepted_at = time.time()
            signal_proxy(ACCEPTED_AT, f"{accepted_at:.3f}\n")
            self._hold_claimed(method, body, accepted_at)
        else:
            self._pass_through(method, body)

    def _pass_through(self, method: str, body: bytes) -> None:
        """Forward one request nobody armed for, and answer it as the destination did.

        This is every request in the matrix but one. It is not part of row 8's
        coordination, so the budget has nothing to say about it: what bounds it is
        the ordinary forwarding bound, which stays under the timeout the caller's
        own SDK allows.
        """
        client = self.upstream
        if client is None:
            self._answer(UNREACHABLE_STATUS, b"", ())
            return
        try:
            answer = client.request(
                method, self.path, content=body, headers=self._forwarded(), timeout=UNCLAIMED_TIMEOUT_SECONDS
            )
        except httpx.HTTPError:
            self._answer(UNREACHABLE_STATUS, b"", ())
            return
        self._answer(answer.status_code, answer.content, answer.headers.items())

    def _hold_claimed(self, method: str, body: bytes, accepted_at: float) -> None:
        """Forward row 8's mutation inside the one budget, then withhold its answer.

        The forwarding is bounded by what is left of that budget and by nothing
        else. A timeout of its own would be a second clock over the same interval:
        longer than the budget, the forwarding alone could outlast the whole
        coordination and the row would report a stalled destination instead of the
        interruption it arranged; shorter, a slow destination would be refused for
        a reason the budget never gave.
        """
        client = self.upstream
        timeout = remaining_budget(accepted_at)
        if client is None:
            signal_proxy(UPSTREAM_FAILED, "this proxy had no route to the destination to forward the write over\n")
            disarm()
            self._answer(UNREACHABLE_STATUS, b"", ())
            return
        if timeout <= 0:
            self._expire()
            return
        try:
            answer = client.request(method, self.path, content=body, headers=self._forwarded(), timeout=timeout)
        except httpx.HTTPError as error:
            # A forwarding the budget ended is the budget expiring, not a
            # destination that could not be reached. Told apart here, so both sides
            # report the one sentence they share rather than two different reasons
            # for the same event.
            if remaining_budget(accepted_at) <= 0:
                self._expire()
                return
            signal_proxy(UPSTREAM_FAILED, f"the destination could not be reached: {type(error).__name__}\n")
            disarm()
            self._answer(UNREACHABLE_STATUS, b"", ())
            return
        reason = upstream_verdict(answer.status_code, answer.content)
        if reason is not None:
            signal_proxy(UPSTREAM_FAILED, f"{reason}\n")
            disarm()
            self._answer(answer.status_code, answer.content, answer.headers.items())
            return
        # Durable, and recorded before the answer is withheld: row 8's check reads
        # this to know the destination completed the write it is about to prove.
        signal_proxy(UPSTREAM_COMPLETED, "")
        self._withhold(accepted_at)

    def _withhold(self, accepted_at: float) -> None:
        """Keep the completed answer from the worker until the driver releases it.

        Bounded by the one budget, absolutely, from the instant the mutation was
        accepted. Expiring is recorded rather than merely happening: the driver
        reads that record instead of timing the same budget a second time.
        """
        while not held(RELEASE):
            if remaining_budget(accepted_at) <= 0:
                self._expire()
                return
            time.sleep(HOLD_POLL_SECONDS)
        # Disarmed before the acknowledgement, so the driver cannot restart the
        # deployment into a proxy that would hold the recovery checks' own traffic.
        disarm()
        signal_proxy(ACKNOWLEDGED, "")
        self._answer(WITHHELD_STATUS, b"", ())

    def _expire(self) -> None:
        """Record the one budget running out, and close without an answer saying otherwise.

        Recorded, because the driver reads this rather than timing the same budget
        a second time. Never acknowledged and never a success status: an expiry
        that answered like a release would be this proxy reporting a coordination
        it did not complete.
        """
        signal_proxy(EXPIRED, "")
        disarm()
        self._answer(WITHHELD_STATUS, b"", ())

    def _forwarded(self) -> dict[str, str]:
        """Return the request's own headers, without the ones that describe a connection."""
        return {name: value for name, value in self.headers.items() if name.lower() not in HOP_BY_HOP}

    def _answer(self, status: int, body: bytes, headers: Iterable[tuple[str, str]]) -> None:
        """Answer one request, or give up quietly when the party that sent it has gone.

        A held request's sender has been killed by the time this runs, so a dead
        socket is the expected case rather than a failure of this proxy.
        """
        try:
            self.send_response(status)
            for name, value in headers:
                if str(name).lower() not in HOP_BY_HOP:
                    self.send_header(str(name), str(value))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)
        except OSError:
            self.close_connection = True

    def do_GET(self) -> None:
        """Forward one GET."""
        self._forward("GET")

    def do_POST(self) -> None:
        """Forward one POST, which is where every GraphQL operation arrives."""
        self._forward("POST")

    def do_PUT(self) -> None:
        """Forward one PUT."""
        self._forward("PUT")

    def do_PATCH(self) -> None:
        """Forward one PATCH."""
        self._forward("PATCH")

    def do_DELETE(self) -> None:
        """Forward one DELETE."""
        self._forward("DELETE")

    def do_HEAD(self) -> None:
        """Forward one HEAD."""
        self._forward("HEAD")

    def do_OPTIONS(self) -> None:
        """Forward one OPTIONS."""
        self._forward("OPTIONS")


def main() -> None:
    """Serve until the container is removed, forwarding everything the deployment sends."""
    upstream = os.environ["CLEAN_HOST_PROXY_UPSTREAM"]
    port = int(os.environ["CLEAN_HOST_PROXY_PORT"])
    # Every request passes its own timeout, so this is only the floor under one
    # that somehow does not: the claimed request is bounded by the budget and an
    # unclaimed one by the ordinary forwarding bound.
    Proxy.upstream = httpx.Client(base_url=upstream, timeout=UNCLAIMED_TIMEOUT_SECONDS, follow_redirects=False)
    server = ThreadingHTTPServer(("", port), Proxy)
    server.daemon_threads = True
    # Recorded once the socket is bound, so the driver waits for a proxy that can
    # actually carry the deployment's traffic rather than for a container to exist.
    signal_proxy(READY, "")
    server.serve_forever()


if __name__ == "__main__":
    main()
