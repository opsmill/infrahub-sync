"""Two live workers, one registered configuration: the guard is what serializes them.

These legs contend from **two different runs of the same configuration**, which is the
only shape that puts the advisory guard under test. Two writes on one run are refused by
the store's admission arbitration long before a worker starts, so a test built that way
would prove the admission rule and say nothing about the lock. Admission is per run, so
both runs here are admitted, both reach a worker, and what has to keep them apart is the
PostgreSQL advisory key derived from their shared `config_id`.

The evidence is read from the service database's own `pg_locks`: which backends held that
exact key, when, and whether they had to wait for it. A run that merely happened to start
after another proves nothing about serialization — measured here, two workers submitted
together were staggered by start-up alone often enough to make an incidental overlap
useless as evidence. So each leg takes the configuration's key itself first, releases it
only once **both** workers are observed blocked on that exact key, and then reads what the
two of them do with it. Both being blocked at once is the fact that makes what follows
serialization rather than luck.

The composed-sync leg adds the half of the property that is easy to lose: its second
worker must perform no destination extraction and no planning until the first releases.
A sync publishes its reviewed plan from inside the guarded region, after extracting both
sides, so the leg reads each run's plan publication against the hold windows — the second
run must have published nothing while the first still held the key, and its own
publication must fall inside its own hold.

Run outcomes are deliberately *not* evidence here. Measured against two unrelated
configurations, which the guard does not serialize at all, the pair still came back
`applied` and `no-change`, because worker start-up staggered them on its own. Only the
lock readings distinguish serialization from luck, which is why every leg also requires
an observed waiting request.

These legs need the live preview stack (`invoke preview.up` plus `invoke preview.seed`).
An absent stack is the only thing they skip for, and the skip names the missing service.
Psycopg is imported outright rather than skipped past: it ships with the service profile
these legs exercise, so a missing driver is a broken environment, not a reason to report
green.
"""

from __future__ import annotations

import concurrent.futures
import json
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import pairwise
from operator import itemgetter
from typing import TYPE_CHECKING, Any

import httpx
import psycopg
import pytest
from typing_extensions import Self

from infrahub_sync.service.apply_guard import advisory_lock_key
from tasks.preview import (
    SHARED_DEVICE_NAME,
    SMOKE_BRANCH,
    SMOKE_KIND,
    PreviewError,
    load_preview_env,
    preview_urls,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration

_MAPPED_FIELDS = ("name", "type")
_SAMPLE_INTERVAL_SECONDS = 0.02
_PHASE_TIMEOUT_SECONDS = 420
_PHASE_POLL_SECONDS = 2
_HOLDER_COUNT = 2
_BLOCKED_WORKERS = 2
# How long both submitted writes may take to reach the guard. This is Prefect worker
# start-up — queue poll plus a fresh interpreter — and it is unrelated to the guard, so it
# gets a generous budget. Waiting this long deliberately outlasts the guard's own
# 30-second acquisition deadline for whichever worker blocked first, which is why these
# legs accept that worker taking the specified contention result instead of the key.
_BOTH_BLOCKED_TIMEOUT_SECONDS = 180
_BLOCKED_POLL_SECONDS = 0.1
_CONTENTION_ERROR = "ApplyGuardContentionError"
# A write stage that ran to completion reports what its plan did. `no-change` is a success:
# the second contender often plans after the first has already written, and finds the
# destination converged.
_COMPLETED_OUTCOMES = frozenset({"applied", "no-change"})

_HELD_KEY_SAMPLE = (
    "SELECT pid, granted FROM pg_locks "
    "WHERE locktype = 'advisory' AND classid::bigint = %s AND objid::bigint = %s AND objsubid = 1"
)
_PUBLISHED_PLANS = "SELECT run_id FROM artifact_refs WHERE run_id = ANY(%s) AND published = 1"
_TAKE_KEY = "SELECT pg_advisory_lock(%s), pg_backend_pid()"
_DROP_KEY = "SELECT pg_advisory_unlock(%s)"


@dataclass(frozen=True, slots=True)
class LiveStack:
    """The reachable preview endpoints one leg drives."""

    sync_api: str
    infrahub: str
    infrahub_token: str
    bearer_token: str
    database_url: str


@pytest.fixture(name="live_stack", scope="session")
def live_stack_fixture() -> LiveStack:
    """Return the running preview stack, or skip naming exactly what is missing."""
    try:
        values = load_preview_env()
    except PreviewError as exc:
        pytest.skip(f"preview settings unavailable ({exc}); start the stack with `invoke preview.up`")
    urls = preview_urls(values)
    database_url = f"postgresql://postgres:postgres@127.0.0.1:{values['PREVIEW_STORAGE_POSTGRES_PORT']}/infrahub_sync"
    for description, url in (
        ("Sync API", f"{urls['sync_api']}/openapi.json"),
        ("Infrahub", f"{urls['infrahub']}/api/config"),
        ("Prefect", f"{urls['prefect']}/api/health"),
    ):
        try:
            response = httpx.get(url, timeout=10)
        except httpx.HTTPError as exc:
            pytest.skip(f"preview {description} unreachable at {url} ({exc}); start it with `invoke preview.up`")
        if response.status_code >= 500:
            pytest.skip(f"preview {description} unhealthy at {url} (HTTP {response.status_code})")
    try:
        with psycopg.connect(database_url, connect_timeout=10) as probe:
            probe.execute("SELECT 1")
    except psycopg.Error as exc:
        pytest.skip(f"preview service PostgreSQL unreachable ({exc}); start it with `invoke preview.up`")
    principals = json.loads(values["PREVIEW_BEARER_TOKENS"])
    actor = min(principals)
    return LiveStack(
        sync_api=urls["sync_api"],
        infrahub=urls["infrahub"],
        infrahub_token=values["INFRAHUB_INITIAL_ADMIN_TOKEN"],
        bearer_token=principals[actor]["token"],
        database_url=database_url,
    )


@pytest.fixture(name="api")
def api_fixture(live_stack: LiveStack) -> Iterator[httpx.Client]:
    """A bearer-authenticated client for the live Sync API."""
    with httpx.Client(
        base_url=live_stack.sync_api,
        headers={"Authorization": f"Bearer {live_stack.bearer_token}"},
        timeout=60,
    ) as client:
        yield client


def _idempotency_headers() -> dict[str, str]:
    """A fresh key per mutation, so a re-run never replays an earlier leg's response."""
    return {"Idempotency-Key": f"guard-live-{uuid.uuid4()}"}


def _register_configuration(api: httpx.Client, live_stack: LiveStack, name: str) -> tuple[str, int]:
    """Register this leg's own package and return the identity the API assigned it.

    Each leg registers its own, because the advisory key is derived from the `config_id`:
    sharing one with another leg or with leftover probe state would let unrelated work
    contend for the key this leg is measuring.
    """
    package = {
        "format_version": 1,
        "configuration": {
            "name": name,
            "source": {
                "name": "infrahub",
                "settings": {
                    "url": live_stack.infrahub,
                    "branch": "main",
                    "token": {"$credential": "infrahub-token"},
                },
            },
            "destination": {
                "name": "infrahub",
                "settings": {
                    "url": live_stack.infrahub,
                    "branch": SMOKE_BRANCH,
                    "token": {"$credential": "infrahub-token"},
                },
            },
            "schema_mapping": [
                {
                    "name": SMOKE_KIND,
                    "mapping": SMOKE_KIND,
                    "identifiers": ["name"],
                    "fields": [{"name": field_name, "mapping": field_name} for field_name in _MAPPED_FIELDS],
                }
            ],
        },
        "credentials": {"infrahub-token": {"provider": "env", "identifier": "INFRAHUB_API_TOKEN"}},
    }
    registered = api.post(
        "/configs",
        headers=_idempotency_headers(),
        json={"package": package, "reason": f"live write-guard leg: register {name}"},
    )
    assert registered.status_code == 201, registered.text
    version = registered.json()["version"]
    return version["config_id"], version["registry_version"]


def _seed_one_source_update(live_stack: LiveStack) -> str:
    """Leave the source differing from the destination in exactly one mapped value.

    Mirroring the destination's devices into `main` verbatim removes everything else from
    the plan; mutating the shared device afterwards leaves the single update these legs
    contend over. The value is fresh on every run, because a fixed one converges after the
    first apply and the update the assertions rest on quietly vanishes.
    """
    from infrahub_sdk import InfrahubClientSync  # pylint: disable=import-outside-toplevel

    client = InfrahubClientSync(address=live_stack.infrahub, config={"api_token": live_stack.infrahub_token})
    # Copied verbatim, every mapped field: a mirror carrying any manufactured value would
    # be an update, and an update rewriting a device's own unique name is rejected.
    payloads = [
        {name: getattr(node, name).value for name in _MAPPED_FIELDS}
        for node in client.all(kind=SMOKE_KIND, branch=SMOKE_BRANCH)
    ]
    assert SHARED_DEVICE_NAME in {payload["name"] for payload in payloads}, (
        f"{SHARED_DEVICE_NAME!r} is not on {SMOKE_BRANCH}; run `uv run invoke preview.seed`"
    )
    for payload in payloads:
        client.create(kind=SMOKE_KIND, branch="main", data=payload).save(allow_upsert=True)
    mutated_type = f"guard-live-{uuid.uuid4().hex[:12]}"
    shared = client.create(kind=SMOKE_KIND, branch="main", data={"name": SHARED_DEVICE_NAME, "type": mutated_type})
    shared.save(allow_upsert=True)
    return mutated_type


def _await_phase(api: httpx.Client, run_id: str, phase: str) -> dict[str, Any]:
    """Poll the durable record until it reaches `phase`, failing on any terminal detour."""
    record = _await_terminal(api, run_id, phase, allow_failure=False)
    assert record["phase"] == phase, f"run {run_id} reached {record['phase']!r} while waiting for {phase!r}"
    return record


def _await_terminal(api: httpx.Client, run_id: str, phase: str, *, allow_failure: bool) -> dict[str, Any]:
    """Poll until the record reaches `phase`, or a terminal failure this caller admits."""
    deadline = time.monotonic() + _PHASE_TIMEOUT_SECONDS
    record: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = api.get(f"/runs/{run_id}")
        assert response.status_code == 200, response.text
        record = response.json()["run"]
        if record["phase"] == phase:
            return record
        if record["phase"] in {"accepted", "planned"}:
            time.sleep(_PHASE_POLL_SECONDS)
            continue
        if allow_failure and record["finished_at"] is not None:
            return record
        pytest.fail(f"run {run_id} reached {record['phase']!r} while waiting for {phase!r}: {record}")
    pytest.fail(f"run {run_id} did not reach {phase!r} within {_PHASE_TIMEOUT_SECONDS}s: {record}")


@dataclass(slots=True)
class _Hold:
    """One backend's observed hold of the configuration's advisory key."""

    pid: int
    first_seen: float
    last_seen: float


@dataclass(slots=True)
class KeyObservations:
    """What the sampler saw of one configuration's advisory key while workers ran."""

    samples: int = 0
    max_granted: int = 0
    max_waiting: int = 0
    first_waiting_at: float | None = None
    holds: dict[int, _Hold] = field(default_factory=dict)
    first_published: dict[str, float] = field(default_factory=dict)
    published_during: list[tuple[float, frozenset[str]]] = field(default_factory=list)

    @property
    def ordered_holds(self) -> list[_Hold]:
        """The observed holds, earliest first."""
        return sorted(self.holds.values(), key=lambda hold: hold.first_seen)


class _KeySampler:
    """Sample one advisory key's holders, and optionally each run's plan publication.

    Both readings come from one statement pair on one connection, so a sample is a single
    consistent view: which backends held the key at that instant, and which of the runs
    had already published a plan.
    """

    def __init__(self, database_url: str, configuration_id: str, run_ids: list[str] | None = None) -> None:
        key = advisory_lock_key(configuration_id)
        self._columns = ((key >> 32) & 0xFFFFFFFF, key & 0xFFFFFFFF)
        self._database_url = database_url
        # Shared with the caller, which fills it in once the API has answered: sampling
        # starts before the submissions so no hold can begin unobserved.
        self._run_ids = run_ids
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.observations = KeyObservations()

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *_exception: object) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    def _run(self) -> None:
        with psycopg.connect(self._database_url, autocommit=True) as connection:
            while not self._stop.is_set():
                rows = connection.execute(_HELD_KEY_SAMPLE, self._columns).fetchall()
                run_ids = list(self._run_ids or ())
                published: frozenset[str] = frozenset()
                if run_ids:
                    published = frozenset(
                        str(row[0]) for row in connection.execute(_PUBLISHED_PLANS, (run_ids,)).fetchall()
                    )
                self._record(time.monotonic(), rows, published, tracking=bool(run_ids))
                time.sleep(_SAMPLE_INTERVAL_SECONDS)

    def _record(self, at: float, rows: list[tuple[Any, ...]], published: frozenset[str], *, tracking: bool) -> None:
        observations = self.observations
        observations.samples += 1
        granted = [int(pid) for pid, is_granted in rows if is_granted]
        observations.max_granted = max(observations.max_granted, len(granted))
        waiting = len([pid for pid, is_granted in rows if not is_granted])
        observations.max_waiting = max(observations.max_waiting, waiting)
        if waiting and observations.first_waiting_at is None:
            observations.first_waiting_at = at
        for pid in granted:
            hold = observations.holds.get(pid)
            if hold is None:
                observations.holds[pid] = _Hold(pid=pid, first_seen=at, last_seen=at)
            else:
                hold.last_seen = at
        for run_id in published:
            observations.first_published.setdefault(run_id, at)
        if tracking:
            observations.published_during.append((at, published))


class _KeyHolder:
    """Hold one configuration's advisory key from the test, to force real contention.

    Submitting two writes together is not enough on this stack: worker start-up staggers
    them often enough that an incidental overlap cannot be relied on. Taking the key first
    makes both workers block on it, so releasing is the starting gun and what follows is
    the guard ordering two contenders rather than two runs that never met.
    """

    def __init__(self, database_url: str, configuration_id: str) -> None:
        self._key = advisory_lock_key(configuration_id)
        self._connection = psycopg.connect(database_url, autocommit=True)
        row = self._connection.execute(_TAKE_KEY, (self._key,)).fetchone()
        assert row is not None
        self.backend_pid = int(row[1])
        self._held = True

    def release(self) -> None:
        """Release the key, letting the blocked workers contend with each other."""
        if self._held:
            self._connection.execute(_DROP_KEY, (self._key,))
            self._held = False

    def close(self) -> None:
        """Release and close, whatever the leg did."""
        self.release()
        self._connection.close()


@contextmanager
def _held_key(database_url: str, configuration_id: str) -> Iterator[_KeyHolder]:
    """Hold the configuration's key for the block, releasing it whatever happens."""
    holder = _KeyHolder(database_url, configuration_id)
    try:
        yield holder
    finally:
        holder.close()


def _await_blocked_workers(sampler: _KeySampler) -> None:
    """Wait until both workers are blocked on the key this test is holding.

    Both being blocked on that exact key at the same instant is the whole point: it is what
    makes everything after the release the guard ordering two contenders, rather than two
    runs that never met. Worker start-up decides how long that takes and has nothing to do
    with the guard, so the budget is generous — deliberately longer than the guard's own
    acquisition deadline, which is why these legs accept the first blocked worker taking the
    specified contention result instead of the key.
    """
    observations = sampler.observations
    deadline = time.monotonic() + _BOTH_BLOCKED_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if observations.max_waiting >= _BLOCKED_WORKERS:
            return
        time.sleep(_BLOCKED_POLL_SECONDS)
    pytest.fail(
        f"only {observations.max_waiting} worker(s) were ever blocked on the configuration's "
        f"advisory key within {_BOTH_BLOCKED_TIMEOUT_SECONDS}s, so the two never contended for it"
    )


def _assert_serialized(observations: KeyObservations, *, holder_pid: int) -> list[_Hold]:
    """Require the workers to have contended for the key and never to have shared it."""
    assert observations.max_waiting >= _BLOCKED_WORKERS, (
        f"at most {observations.max_waiting} worker(s) were ever blocked on the key at once, so "
        f"the two never contended for it"
    )
    assert observations.max_granted == 1, (
        f"the configuration's advisory key was held by {observations.max_granted} backends at once"
    )
    worker_holds = [hold for hold in observations.ordered_holds if hold.pid != holder_pid]
    assert worker_holds, (
        f"no worker ever held the key after the test released it, across {observations.samples} samples"
    )
    for earlier, later in pairwise(worker_holds):
        assert earlier.last_seen < later.first_seen, (
            f"two worker holds overlapped: pid {earlier.pid} [{earlier.first_seen}, {earlier.last_seen}] "
            f"and pid {later.pid} [{later.first_seen}, {later.last_seen}]"
        )
    return worker_holds


def _assert_each_plan_had_its_own_hold(first_published: dict[str, float], worker_holds: list[_Hold]) -> None:
    """Require each published plan to sit in a hold of its own, in order.

    "Inside some hold" is not the property: with holds `[10, 20]` and `[21, 30]`, two plans
    published at 15 and 16 both sit inside a hold and both were nonetheless produced while
    the *first* worker held the key — the exact violation this leg exists to exclude. Each
    publication therefore has to claim a distinct hold, and because the holds are disjoint
    and time-ordered, that is the same as saying no worker extracted or planned while
    another held the key.

    A contender that lost the key publishes nothing, so there are never more publications
    than holds; requiring the counts to match is what stops a silent second publication
    inside one hold from passing.
    """
    ordered = sorted(first_published.items(), key=itemgetter(1))
    assert ordered, "no sync published a plan while sampled"
    assert len(ordered) == len(worker_holds), (
        f"{len(ordered)} plan publication(s) against {len(worker_holds)} worker hold(s): "
        f"{ordered} versus {[(hold.pid, hold.first_seen, hold.last_seen) for hold in worker_holds]}"
    )
    for (run_id, published_at), hold in zip(ordered, worker_holds, strict=True):
        assert hold.first_seen <= published_at <= hold.last_seen, (
            f"run {run_id} published its reviewed plan at {published_at}, outside the hold it must "
            f"have used — pid {hold.pid} [{hold.first_seen}, {hold.last_seen}] — so it extracted and "
            f"planned while another worker held the configuration's key, or held none at all"
        )


def _assert_write_verdict(label: str, record: dict[str, Any]) -> bool:
    """Require one live write to have either run its stage or lost the key cleanly.

    Whichever worker blocked first may have been waiting longer than the guard's own
    acquisition deadline, because this leg holds the key until both are blocked. Losing it
    that way is the envelope's specified contention result and writes nothing, so it is a
    legal end for one of the two — but never for both. Either way the run is certain about
    what it did, so neither end may require reconciliation.
    """
    assert record["reconciliation_required"] is False, f"write {label} reported uncertainty: {record}"
    if record["outcome"] in _COMPLETED_OUTCOMES:
        return True
    failure = next(
        (value for name, value in record["results"].items() if name.endswith("_failure")),
        {},
    )
    assert failure.get("error_type") == _CONTENTION_ERROR, (
        f"write {label} ended {record['outcome']!r} for a reason other than losing the key: {record}"
    )
    return False


def test_two_live_apply_workers_serialize_on_one_configuration(api: httpx.Client, live_stack: LiveStack) -> None:
    """Two reviewed applies, two runs, one configuration: one worker writes at a time.

    Both applies are admitted, because a write admission is per run — so nothing but the
    advisory key stands between the two workers, and the key is what the assertions read.
    """
    config_id, registry_version = _register_configuration(api, live_stack, "guard-live-apply")
    _seed_one_source_update(live_stack)

    approved: dict[str, tuple[str, str]] = {}
    for label in ("A", "B"):
        created = api.post(
            "/runs",
            headers=_idempotency_headers(),
            json={
                "operation": "plan",
                "config_id": config_id,
                "registry_version": registry_version,
                "branch": SMOKE_BRANCH,
                "reason": f"live write-guard apply leg: plan {label}",
            },
        )
        assert created.status_code == 202, created.text
        approved[label] = (created.json()["run"]["run_id"], "")
    for label, (run_id, _) in list(approved.items()):
        _await_phase(api, run_id, "planned")
        plan = api.get(f"/runs/{run_id}/plan")
        assert plan.status_code == 200, plan.text
        summary = plan.json()["summary"]
        assert summary["by_action"].get("update", 0) >= 1, f"plan {label} carries no update to contend over: {summary}"
        approved[label] = (run_id, plan.json()["checksum"])

    def apply(label: str) -> httpx.Response:
        run_id, checksum = approved[label]
        return api.post(
            f"/runs/{run_id}/apply",
            headers=_idempotency_headers(),
            json={
                "expected_checksum": checksum,
                "confirm_writes": True,
                "branch": SMOKE_BRANCH,
                "reason": f"live write-guard apply leg: apply {label}",
            },
        )

    with (
        _KeySampler(live_stack.database_url, config_id) as sampler,
        _held_key(live_stack.database_url, config_id) as holder,
    ):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(apply, ("A", "B")))
        assert [response.status_code for response in responses] == [202, 202], [r.text for r in responses]
        _await_blocked_workers(sampler)
        holder.release()
        records = {
            label: _await_terminal(api, run_id, "applied", allow_failure=True)
            for label, (run_id, _) in approved.items()
        }

    _assert_serialized(sampler.observations, holder_pid=holder.backend_pid)
    completed = [label for label, record in records.items() if _assert_write_verdict(label, record)]
    assert completed, f"neither apply held the key long enough to run its write stage: {records}"


def test_two_live_sync_workers_serialize_extraction_and_planning(api: httpx.Client, live_stack: LiveStack) -> None:
    """Two composed syncs, two runs, one configuration: the second plans only after release.

    A composed sync holds the key across its own destination extraction, planning,
    verification, and apply, and publishes its reviewed plan from inside that hold. So the
    second worker having published nothing while the first held the key is the observable
    for "it extracted and planned nothing" — and its own publication landing inside its own
    hold places that work after the first released.

    The local pipeline lock cannot stand in for this: it covers the read-only plan stage
    alone and is released before the apply, so under it alone the second worker would plan
    while the first was still verifying and applying — well inside the first hold.
    """
    config_id, registry_version = _register_configuration(api, live_stack, "guard-live-sync")
    _seed_one_source_update(live_stack)

    def start(label: str) -> httpx.Response:
        return api.post(
            "/runs",
            headers=_idempotency_headers(),
            json={
                "operation": "sync",
                "config_id": config_id,
                "registry_version": registry_version,
                "branch": SMOKE_BRANCH,
                "confirm_writes": True,
                "reason": f"live write-guard sync leg: sync {label}",
            },
        )

    sampled_run_ids: list[str] = []
    run_ids: dict[str, str] = {}
    with (
        _KeySampler(live_stack.database_url, config_id, run_ids=sampled_run_ids) as sampler,
        _held_key(live_stack.database_url, config_id) as holder,
    ):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            responses = dict(zip(("A", "B"), pool.map(start, ("A", "B")), strict=True))
        for label, response in responses.items():
            assert response.status_code == 202, response.text
            run_ids[label] = response.json()["run"]["run_id"]
        sampled_run_ids.extend(run_ids.values())
        _await_blocked_workers(sampler)
        holder.release()
        records = {
            label: _await_terminal(api, run_id, "applied", allow_failure=True) for label, run_id in run_ids.items()
        }

    worker_holds = _assert_serialized(sampler.observations, holder_pid=holder.backend_pid)
    observations = sampler.observations
    completed = [label for label, record in records.items() if _assert_write_verdict(label, record)]
    assert completed, f"neither sync held the key long enough to run its write stage: {records}"

    _assert_each_plan_had_its_own_hold(observations.first_published, worker_holds)


def test_publications_must_claim_distinct_holds_in_order() -> None:
    """The predicate the sync leg rests on, exercised on readings it must reject.

    Two plans published at 15 and 16, against holds `[10, 20]` and `[21, 30]`, both sit
    inside *a* hold — and both were produced while the first worker held the key, which is
    the violation the leg exists to exclude. Requiring each publication to claim a hold of
    its own, in order, is what tells that apart from the correct interleaving.

    This case needs no stack: the predicate is pure, and its readings are the ones a broken
    guard would produce.
    """
    holds = [_Hold(pid=1, first_seen=10.0, last_seen=20.0), _Hold(pid=2, first_seen=21.0, last_seen=30.0)]

    with pytest.raises(AssertionError, match="outside the hold it must have used"):
        _assert_each_plan_had_its_own_hold({"run-a": 15.0, "run-b": 16.0}, holds)

    _assert_each_plan_had_its_own_hold({"run-a": 15.0, "run-b": 25.0}, holds)


def test_a_contender_that_never_held_the_key_must_have_published_nothing() -> None:
    """One worker holding means one plan: the loser extracted and planned nothing."""
    holds = [_Hold(pid=1, first_seen=10.0, last_seen=20.0)]

    _assert_each_plan_had_its_own_hold({"run-a": 15.0}, holds)

    with pytest.raises(AssertionError, match="plan publication"):
        _assert_each_plan_had_its_own_hold({"run-a": 15.0, "run-b": 16.0}, holds)
