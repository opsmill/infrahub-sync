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

import importlib.util
import json
import re
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

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
# The one budget
# ---------------------------------------------------------------------------
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
    """Zero remaining is the budget already gone, and a request sent then cannot be inside it."""
    assert proxy.remaining_budget(time.time() - proxy.PROXY_BUDGET_SECONDS - 1) <= 0
    assert "if timeout <= 0:" in (CHECKS / "destination_proxy.py").read_text(encoding="utf-8"), (
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
