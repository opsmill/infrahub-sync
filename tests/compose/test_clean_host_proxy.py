"""What the fixture proxy row 8 interrupts a write through must actually do.

Row 8's claim is that an *actual* ambiguous destination write is interrupted. The
only place a write can be held after the destination completed it and before the
worker learns the outcome is between the two, so the gate puts its own reverse
proxy there and the deployment's registered configuration names it.

The proxy itself runs inside the candidate image on a clean host, and this suite
reaches neither. What it can do is drive the decisions that make the row mean
anything — whether a request is the mutation being armed for, whether the
destination actually completed it, and whether an arm can be claimed twice — and
those are exactly the places a held write turns into a vacuous pass.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import re
import sys
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKS = REPO_ROOT / "tests" / "compose" / "clean_host" / "checks"
# The timeout the Infrahub adapter gives the SDK for one destination call. A hold
# that outlived it would be reported to the worker as its own client timing out,
# which is a different failure from the one this row induces.
ADAPTER = REPO_ROOT / "infrahub_sync" / "adapters" / "infrahub.py"

MUTATION = 'mutation InfraDeviceUpdate { InfraDeviceUpdate(data: {id: "x"}) { ok } }'
QUERY = "query InfraDevice { InfraDevice { edges { node { id } } } }"


@pytest.fixture(scope="module")
def proxy() -> Iterator[ModuleType]:
    """Load the proxy the way its own container does, with the kit importable beside it."""
    sys.path.insert(0, str(CHECKS))
    try:
        specification = importlib.util.spec_from_file_location("clean_host_proxy", CHECKS / "destination_proxy.py")
        assert specification is not None
        loader = specification.loader
        assert loader is not None
        module = importlib.util.module_from_spec(specification)
        loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(CHECKS))


def body(query: object) -> bytes:
    """One GraphQL request body as the SDK sends it."""
    return json.dumps({"query": query, "variables": {}}).encode("utf-8")


# ---------------------------------------------------------------------------
# Which request the proxy arms for
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "document",
    [
        MUTATION,
        "  \n  " + MUTATION,
        "# a leading comment the SDK is free to send\n" + MUTATION,
        "mutation{InfraDeviceUpdate{ok}}",
    ],
)
def test_a_graphql_mutation_is_recognised_from_the_document_it_carries(proxy: ModuleType, document: str) -> None:
    """The row arms for a write, and a write is a mutation operation. Nothing else."""
    assert proxy.is_target_mutation("POST", "/graphql/qualification", body(document))


@pytest.mark.parametrize(
    "document",
    [
        QUERY,
        "{ InfraDevice { edges { node { id } } } }",
        "mutationlike { notAMutation }",
        "mutations { notAMutation }",
        "fragment f on InfraDevice { id }",
        "",
    ],
)
def test_a_request_that_is_not_a_mutation_is_not_the_one_armed_for(proxy: ModuleType, document: str) -> None:
    """A plan reads the destination, so a row that armed for any request would hold a read.

    Holding a read leaves the destination unwritten, and row 8 would then
    interrupt a run with nothing ambiguous about it — the exact vacuous pass the
    row exists to exclude.
    """
    assert not proxy.is_target_mutation("POST", "/graphql/qualification", body(document))


@pytest.mark.parametrize(("method", "path"), [("GET", "/graphql/qualification"), ("POST", "/api/schema")])
def test_a_mutation_reached_by_another_method_or_route_is_not_recognised(
    proxy: ModuleType, method: str, path: str
) -> None:
    """Recognition is structural: the route, the method, and the document together."""
    assert not proxy.is_target_mutation(method, path, body(MUTATION))


@pytest.mark.parametrize(
    "raw",
    [b"", b"not json at all", b"[]", b'"a string"', b'{"variables": {}}', b'{"query": 17}', b'{"query": null}'],
)
def test_a_body_that_is_not_a_graphql_document_is_not_recognised(proxy: ModuleType, raw: bytes) -> None:
    """A body the proxy cannot read is not a body it may claim to have recognised."""
    assert not proxy.is_target_mutation("POST", "/graphql/qualification", raw)


# ---------------------------------------------------------------------------
# Whether the destination actually completed the write
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("status", "document"),
    [
        (200, {"data": {"InfraDeviceUpdate": {"ok": True}}}),
        (201, {"data": {"InfraDeviceUpdate": {"ok": True}}}),
        (200, {"data": {"InfraDeviceUpdate": {"ok": True}}, "errors": []}),
    ],
)
def test_only_an_answer_the_destination_completed_is_a_completed_write(
    proxy: ModuleType, status: int, document: dict[str, object]
) -> None:
    """The row's precondition: the destination did the write before the worker lost it.

    A hold placed over a request the destination refused leaves nothing ambiguous
    to reconcile, and row 8 would report success having interrupted a refusal.
    """
    assert proxy.upstream_verdict(status, json.dumps(document).encode("utf-8")) is None


@pytest.mark.parametrize(
    ("status", "raw"),
    [
        (500, b'{"data": {"InfraDeviceUpdate": {"ok": true}}}'),
        (401, b"{}"),
        (200, b"not json at all"),
        (200, b"[]"),
        (200, b'{"errors": [{"message": "no"}]}'),
        (200, b'{"data": null}'),
        (200, b"{}"),
        # Infrahub's GraphQL wrapper answers HTTP 200 with data *and* errors when a
        # mutation partially applied. Only the errors decide, and this is the one
        # case that says so: every other refusal here is also missing its data.
        (200, b'{"data": {"InfraDeviceUpdate": null}, "errors": [{"message": "no"}]}'),
    ],
)
def test_an_answer_the_destination_did_not_complete_is_refused(proxy: ModuleType, status: int, raw: bytes) -> None:
    """Each of these leaves the destination unwritten, so none of them may be held."""
    assert proxy.upstream_verdict(status, raw) is not None


def test_the_reason_an_upstream_answer_is_refused_carries_no_destination_data(proxy: ModuleType) -> None:
    """The reason reaches a refusal sentence and an artifact. A status code is all it may be.

    A GraphQL error message quotes the document that provoked it, which is
    declared configuration and destination content — neither of which this gate
    reports.
    """
    reason = proxy.upstream_verdict(403, b'{"errors": [{"message": "device clean-host-secret-value refused"}]}')

    assert reason is not None
    assert "clean-host-secret-value" not in reason
    assert "refused" not in reason.replace("refused the", "")
    assert "403" in reason


# ---------------------------------------------------------------------------
# One arm, one mutation
# ---------------------------------------------------------------------------
def test_the_arm_can_be_claimed_once_so_exactly_one_mutation_is_held(
    proxy: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sync issues one mutation here, but a proxy that re-armed would hold the next run.

    The hold is released by the driver and the deployment is restarted into row
    8's recovery checks, whose own plan reaches the destination. An arm that
    survived its claim would hold that too.
    """
    monkeypatch.setattr(proxy, "CONTROL", tmp_path)
    (tmp_path / proxy.ARM).write_text("", encoding="utf-8")

    assert proxy.claim_arm() is True
    assert proxy.claim_arm() is False


def test_nothing_is_claimed_when_the_row_never_armed(
    proxy: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every other row's traffic goes through this proxy and none of it may be held."""
    monkeypatch.setattr(proxy, "CONTROL", tmp_path)

    assert proxy.claim_arm() is False


# ---------------------------------------------------------------------------
# The one budget, as an absolute deadline over every step inside it
# ---------------------------------------------------------------------------
@pytest.fixture
def control(proxy: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The private control directory, pointed at a temporary one for this test.

    Every handshake state below is a file in here, so a test that records one is
    recording it where this test can read it and nowhere near a real run.
    """
    monkeypatch.setattr(proxy, "CONTROL", tmp_path)
    return tmp_path


def expired(proxy: ModuleType) -> float:
    """An accept instant whose budget has already run out."""
    return time.time() - proxy.PROXY_BUDGET_SECONDS - 1


def test_an_upstream_exchange_is_bounded_as_a_whole_and_not_phase_by_phase(
    proxy: ModuleType, control: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scalar httpx timeout is applied to connect, read, write and pool separately.

    Twenty seconds each is eighty seconds of exchange obeying a twenty-second
    timeout. The budget is one absolute deadline over the whole exchange, so the
    forwarding has to be bounded as a unit rather than per phase — and by then the
    worker's own sixty-second SDK timeout would have fired first, which is the
    failure this row must not be reporting.
    """
    monkeypatch.setattr(proxy, "PROXY_BUDGET_SECONDS", 0.5)
    # Released in the `finally` below rather than slept through. `cancel_futures`
    # cannot cancel a call that is already running, and an executor's threads are
    # not daemons, so a fixed sleep here leaves the interpreter joining this worker
    # for the rest of that sleep after the case has already reported. The ceiling
    # is a backstop for a release that never arrives, not the wait itself.
    released = threading.Event()

    def slowly(_timeout: float) -> object:
        """A destination that has not answered by the time the whole budget is out."""
        released.wait(30)
        return None

    started = time.monotonic()
    try:
        answered = proxy.forward_within_budget(slowly, time.time())
        spent = time.monotonic() - started

        assert answered is None, "an exchange the budget ended is reported as an answer"
        assert spent < 5, f"the exchange was bounded phase by phase rather than as a whole: {spent:.1f}s"
        assert not (control / proxy.UPSTREAM_COMPLETED).exists(), "an exchange the budget ended recorded a completion"
    finally:
        released.set()


def test_a_forward_that_succeeds_after_the_budget_ended_is_only_ever_an_expiry(
    proxy: ModuleType, control: Path
) -> None:
    """The destination completed it, but too late for anything to be arranged around.

    Recording completion here would tell row 8's check to go and prove a write
    while the driver had already given up, and the two would then be coordinating
    over a budget that no longer existed. A late success is the budget expiring.
    """
    outcome = proxy.record_upstream_completed(expired(proxy))

    assert outcome == proxy.HELD_EXPIRED
    assert (control / proxy.EXPIRED).exists(), "a late forward is not recorded as the budget running out"
    assert not (control / proxy.UPSTREAM_COMPLETED).exists(), "a late forward is recorded as a completed write"
    assert not (control / proxy.ACKNOWLEDGED).exists()


def test_a_forward_that_succeeds_inside_the_budget_is_recorded_as_completed(proxy: ModuleType, control: Path) -> None:
    """What makes the case above mean anything: on time, this is the durable signal."""
    outcome = proxy.record_upstream_completed(time.time())

    assert outcome == proxy.HELD_COMPLETED
    assert (control / proxy.UPSTREAM_COMPLETED).exists()
    assert not (control / proxy.EXPIRED).exists()


def test_a_release_already_waiting_is_still_refused_once_the_budget_has_ended(proxy: ModuleType, control: Path) -> None:
    """The driver released it, but the release arrived after the budget was gone.

    Read without a recheck, the file's mere presence acknowledges a coordination
    that did not happen in time — and the driver would take that acknowledgement
    as licence to carry on into row 8's recovery checks.
    """
    (control / proxy.RELEASE).write_text("", encoding="utf-8")

    outcome = proxy.await_release(expired(proxy))

    assert outcome == proxy.HELD_EXPIRED
    assert (control / proxy.EXPIRED).exists(), "a late release is not recorded as the budget running out"
    assert not (control / proxy.ACKNOWLEDGED).exists(), "a late release is acknowledged as though it were on time"


def test_a_release_waiting_inside_the_budget_is_acknowledged(proxy: ModuleType, control: Path) -> None:
    """What makes the case above mean anything: on time, this is the acknowledgement."""
    (control / proxy.RELEASE).write_text("", encoding="utf-8")

    outcome = proxy.await_release(time.time())

    assert outcome == proxy.HELD_ACKNOWLEDGED
    assert (control / proxy.ACKNOWLEDGED).exists()
    assert not (control / proxy.EXPIRED).exists()


def test_a_release_that_never_comes_ends_as_an_expiry(proxy: ModuleType, control: Path) -> None:
    """The hold is a share of the budget, not a wait of its own."""
    assert proxy.await_release(expired(proxy)) == proxy.HELD_EXPIRED
    assert (control / proxy.EXPIRED).exists()


def test_an_expiry_always_disarms_so_nothing_after_it_is_held(proxy: ModuleType, control: Path) -> None:
    """The proxy serves the whole matrix, and the rows after this one must pass through."""
    (control / proxy.ARMED).write_text("", encoding="utf-8")

    proxy.record_upstream_completed(expired(proxy))

    assert not (control / proxy.ARMED).exists(), "an expiry leaves the proxy armed for whatever comes next"


def test_what_is_left_of_the_budget_is_measured_from_the_accepted_instant(proxy: ModuleType) -> None:
    """Every share of the budget reads the same clock, so nothing gets an allowance.

    The forwarding, the hold, and the driver's wait are parts of one interval. A
    step that measured from its own start would restart the budget, which is the
    exact defect the one budget exists to exclude.
    """
    now = time.time()

    assert proxy.remaining_budget(now) == pytest.approx(proxy.PROXY_BUDGET_SECONDS, abs=1)
    assert proxy.remaining_budget(now - proxy.PROXY_BUDGET_SECONDS / 2) == pytest.approx(
        proxy.PROXY_BUDGET_SECONDS / 2, abs=1
    )
    assert proxy.remaining_budget(now - proxy.PROXY_BUDGET_SECONDS) <= 0
    assert proxy.remaining_budget(now - proxy.PROXY_BUDGET_SECONDS - 60) <= 0


def test_a_forwarding_that_begins_with_no_budget_left_is_never_attempted(proxy: ModuleType) -> None:
    """Zero remaining is the budget already gone, and a request sent then is outside it.

    Driven rather than read: what matters is that the destination is not reached,
    not that some particular line guards it.
    """

    def unreachable(_timeout: float) -> object:
        """A destination that must not be asked for anything."""
        return pytest.fail("the write was forwarded after the budget covering it had ended")

    assert proxy.remaining_budget(time.time() - proxy.PROXY_BUDGET_SECONDS - 1) <= 0
    assert proxy.forward_within_budget(unreachable, expired(proxy)) is None, (
        "a claimed request is forwarded even when the budget covering it is already gone"
    )


def test_the_one_budget_is_comfortably_below_the_timeout_the_adapter_gives_the_sdk() -> None:
    """A hold that outlived the worker's own client would be reported as its timeout.

    The interruption this row induces is a worker killed while its destination
    call is held. If the SDK gave up first, the run would record a transport
    timeout instead, and the row would be observing something else entirely.
    """
    source = (CHECKS / "destination_proxy.py").read_text(encoding="utf-8")
    declared = re.search(r"^PROXY_BUDGET_SECONDS = ([0-9.]+)$", source, re.MULTILINE)
    assert declared is not None, "the proxy names no single coordination budget"

    given = re.search(r'"timeout": (\d+)', ADAPTER.read_text(encoding="utf-8"))
    assert given is not None, "the adapter names no SDK timeout to stay below"
    assert float(declared.group(1)) <= float(given.group(1)) / 2, (
        "the budget is not comfortably below the timeout the adapter gives one destination call"
    )


# ---------------------------------------------------------------------------
# What reaches the worker has to describe itself correctly
# ---------------------------------------------------------------------------
# `httpx` decodes a response body according to its own `Content-Encoding`, so
# `answer.content` is plain bytes. Forwarded with the header that described the
# compressed form, the worker's own client tries to decompress plain JSON and
# reports a decoding error — which is what ended the live matrix, in a row that
# had nothing to do with this proxy's own behaviour.
GRAPHQL_ANSWER = {"data": {"InfraDevice": {"edges": [{"node": {"id": "x"}}]}}}
GRAPHQL_REJECTION = {"data": None, "errors": [{"message": "no"}]}


def gzipping_upstream(
    document: Mapping[str, object], seen: list[dict[str, str]] | None = None
) -> type[BaseHTTPRequestHandler]:
    """A destination that answers one GraphQL document, gzipped, and says so.

    Records the headers each request arrived with, so a test can say what the
    proxy passed on as well as what it sent back.
    """
    packed = gzip.compress(json.dumps(document).encode("utf-8"))
    recorded = seen if seen is not None else []

    class Upstream(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - the base handler's signature
            """Say nothing: this is a test fixture, not a log."""

        def do_POST(self) -> None:
            """Answer with a gzipped document, exactly as Infrahub's server would."""
            recorded.append({name.lower(): value for name, value in self.headers.items()})
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(packed)))
            self.end_headers()
            self.wfile.write(packed)

    return Upstream


@contextmanager
def serving(handler: type[BaseHTTPRequestHandler]) -> Iterator[int]:
    """Run one handler on a loopback port for as long as the block needs it."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()


@contextmanager
def proxy_in_front_of(
    proxy: ModuleType, document: Mapping[str, object], seen: list[dict[str, str]] | None = None
) -> Iterator[int]:
    """The real proxy handler, forwarding to a real gzipping destination."""
    with serving(gzipping_upstream(document, seen)) as upstream:
        client = httpx.Client(base_url=f"http://127.0.0.1:{upstream}", timeout=10)
        previous = proxy.Proxy.upstream
        proxy.Proxy.upstream = client
        try:
            with serving(proxy.Proxy) as front:
                yield front
        finally:
            proxy.Proxy.upstream = previous
            client.close()


def test_a_gzipped_answer_reaches_the_worker_as_one_valid_json_document(proxy: ModuleType, control: Path) -> None:
    """The live failure: a plan that reached the destination and could not read it.

    Every row's traffic goes through this proxy, so a body that describes itself
    wrongly breaks rows that have nothing to do with row 8 — and it did.
    """
    del control
    with proxy_in_front_of(proxy, GRAPHQL_ANSWER) as front:
        answered = httpx.post(
            f"http://127.0.0.1:{front}/graphql/qualification",
            json={"query": "query InfraDevice { InfraDevice { edges { node { id } } } }"},
            timeout=10,
        )

    assert answered.status_code == httpx.codes.OK
    assert answered.json() == GRAPHQL_ANSWER, "the document that arrived is not the one the destination sent"
    assert "content-encoding" not in answered.headers, "the answer still claims an encoding its bytes no longer have"
    assert int(answered.headers["content-length"]) == len(answered.content), (
        "the length does not describe the bytes that arrived"
    )


def test_a_rejected_claimed_answer_also_reaches_the_worker_readable(proxy: ModuleType, control: Path) -> None:
    """The claimed path answers the worker too, and shares the same normalisation.

    A mutation the destination rejected is forwarded back so the run records what
    the destination said. Unreadable, it would record a decoding error instead.
    """
    (control / proxy.ARM).write_text("", encoding="utf-8")

    with proxy_in_front_of(proxy, GRAPHQL_REJECTION) as front:
        answered = httpx.post(
            f"http://127.0.0.1:{front}/graphql/qualification",
            json={"query": "mutation InfraDeviceUpdate { InfraDeviceUpdate { ok } }"},
            timeout=10,
        )

    assert answered.json() == GRAPHQL_REJECTION, "the rejection that arrived is not the one the destination sent"
    assert "content-encoding" not in answered.headers
    assert (control / proxy.UPSTREAM_FAILED).exists(), "the claimed mutation's rejection was not recorded"
    assert not (control / proxy.ARMED).exists(), "a rejected claim left the proxy armed"


def test_the_headers_a_graphql_answer_needs_are_kept(proxy: ModuleType, control: Path) -> None:
    """Only what stopped describing the bytes is dropped. The rest is the answer."""
    del control
    with proxy_in_front_of(proxy, GRAPHQL_ANSWER) as front:
        answered = httpx.post(
            f"http://127.0.0.1:{front}/graphql/qualification", json={"query": "query X { a }"}, timeout=10
        )

    assert answered.headers["content-type"] == "application/json", "the answer no longer says what it is"


def test_a_forwarded_request_keeps_the_headers_that_still_describe_it(proxy: ModuleType, control: Path) -> None:
    """The other direction, and the reason the two header sets are kept apart.

    A request body is forwarded exactly as it arrived, so whatever described it
    still does — including its own encoding. And the destination's credential is
    in a header: dropped, nothing this gate does would reach the destination.
    """
    del control
    seen: list[dict[str, str]] = []
    with proxy_in_front_of(proxy, GRAPHQL_ANSWER, seen) as front:
        httpx.post(
            f"http://127.0.0.1:{front}/graphql/qualification",
            headers={"X-INFRAHUB-KEY": "a-destination-token", "Content-Encoding": "identity"},
            json={"query": "query X { a }"},
            timeout=10,
        )

    assert seen, "the request never reached the destination"
    assert seen[0].get("x-infrahub-key") == "a-destination-token", "the destination's credential did not reach it"
    assert seen[0].get("content-encoding") == "identity", (
        "a request body's own encoding was stripped, though the body was forwarded unchanged"
    )
    assert "127.0.0.1" in seen[0].get("host", ""), "the destination was addressed as something other than itself"
