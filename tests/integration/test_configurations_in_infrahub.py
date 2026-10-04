"""Configurations live in Infrahub, and a run records the version it uses (quickstart scenario 3).

Against the preview's own Infrahub and Sync API: the test writes a `SyncConfiguration`
the way an operator does — on the default branch, or on a branch that is then merged —
and starts plan runs without naming a version. Each run records a version only when no
version already holds the configuration's current content; two runs started together on
new content share one; an invalid document is refused with its finding and never becomes
a version; validating a branch's document reports findings and records nothing; and the
registration routes answer 410, because configurations are not created through Sync.

Whether a run's plan later succeeds is not under test: a version is recorded when the run
is admitted, and the run's mirror is written then too.

Needs the preview stack (`uv run invoke preview.up`). An absent stack is the only thing
it skips for, and the skip names the missing service.
"""

from __future__ import annotations

import concurrent.futures
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from infrahub_sync.platform.records import RUN_KIND
from tasks.preview import PreviewError, load_preview_env, preview_urls
from tests.infrahub_records import load_sync_schema, put_configuration

if TYPE_CHECKING:
    from collections.abc import Iterator

    from infrahub_sdk import InfrahubClientSync

pytestmark = pytest.mark.integration

IN_INFRAHUB = "configurations-in-infrahub"
REASON = "configurations in Infrahub: record the version this run uses"
MIRROR_TIMEOUT_SECONDS = 30
# A literal value where a credential reference belongs: admission refuses it.
LITERAL_TOKEN = "not-a-reference-0123456789"  # noqa: S105 -- deliberately invalid document content


@dataclass(frozen=True, slots=True)
class LiveStack:
    """The reachable preview endpoints and the caller's Infrahub token."""

    sync_api: str
    infrahub: str
    token: str


@pytest.fixture(name="live_stack", scope="module")
def live_stack_fixture() -> LiveStack:
    """Return the running preview stack, or skip naming exactly what is missing."""
    try:
        values = load_preview_env()
    except PreviewError as exc:
        pytest.skip(f"preview settings unavailable ({exc}); start the stack with `invoke preview.up`")
    urls = preview_urls(values)
    for description, url in (
        ("Infrahub", f"{urls['infrahub']}/api/config"),
        ("Sync API", f"{urls['sync_api']}/openapi.json"),
    ):
        try:
            response = httpx.get(url, timeout=10)
        except httpx.HTTPError as exc:
            pytest.skip(f"preview {description} unreachable at {url} ({exc}); start it with `invoke preview.up`")
        if response.status_code >= 500:
            pytest.skip(f"preview {description} unhealthy at {url} (HTTP {response.status_code})")
    return LiveStack(sync_api=urls["sync_api"], infrahub=urls["infrahub"], token=values["INFRAHUB_INITIAL_ADMIN_TOKEN"])


@pytest.fixture(name="infrahub", scope="module")
def infrahub_fixture(live_stack: LiveStack) -> InfrahubClientSync:
    """The operator's Infrahub client, with the Sync schema extension loaded."""
    from infrahub_sdk import InfrahubClientSync  # pylint: disable=import-outside-toplevel

    client = InfrahubClientSync(address=live_stack.infrahub, config={"api_token": live_stack.token})
    load_sync_schema(client)
    return client


@pytest.fixture(name="api")
def api_fixture(live_stack: LiveStack) -> Iterator[httpx.Client]:
    """The Sync API, called with the caller's Infrahub API token."""
    with httpx.Client(base_url=live_stack.sync_api, headers={"X-INFRAHUB-KEY": live_stack.token}, timeout=60) as client:
        yield client


def _package(infrahub_url: str, *, verify_ssl: bool | None = None, token: object = None) -> dict[str, Any]:
    """A declared Infrahub-to-Infrahub package; `verify_ssl` varies its content, `token` breaks it."""
    reference = {"$credential": "infrahub-token"}
    destination: dict[str, Any] = {"url": infrahub_url, "branch": "main", "token": reference}
    if verify_ssl is not None:
        destination["verify_ssl"] = verify_ssl
    return {
        "format_version": 1,
        "configuration": {
            "name": "configurations-in-infrahub",
            "source": {
                "name": "infrahub",
                "settings": {"url": infrahub_url, "branch": "main", "token": reference if token is None else token},
            },
            "destination": {"name": "infrahub", "settings": destination},
            "schema_mapping": [
                {
                    "name": "InfraDevice",
                    "mapping": "InfraDevice",
                    "identifiers": ["name"],
                    "fields": [{"name": "name", "mapping": "name"}],
                }
            ],
        },
        "credentials": {
            "infrahub-token": {"provider": "env", "identifier": "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN"}
        },
    }


def _start_plan(api: httpx.Client, config_id: str) -> httpx.Response:
    """`POST /runs` without a version: the run uses the configuration's current document."""
    return api.post(
        "/runs",
        headers={"Idempotency-Key": f"configurations-in-infrahub-{uuid.uuid4()}"},
        json={"operation": "plan", "config_id": config_id, "reason": REASON},
    )


def _planned_version(api: httpx.Client, config_id: str) -> dict[str, Any]:
    """Start a plan and return the run it admitted, which names the version it recorded."""
    created = _start_plan(api, config_id)
    assert created.status_code == 202, created.text
    return dict(created.json()["run"])


def _versions(api: httpx.Client, config_id: str) -> list[int]:
    listed = api.get(f"/configs/{config_id}/versions")
    assert listed.status_code == 200, listed.text
    return [entry["registry_version"] for entry in listed.json()]


def _mirrored_run(infrahub: InfrahubClientSync, run_id: str) -> Any:  # noqa: ANN401 -- the SDK's node
    """The `SyncRun` node mirroring one run, once the service has written it."""
    deadline = time.monotonic() + MIRROR_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        found = infrahub.filters(kind=RUN_KIND, run_id__value=run_id)
        if found:
            return found[0]
        time.sleep(1)
    return pytest.fail(f"run {run_id} was not mirrored into {RUN_KIND} within {MIRROR_TIMEOUT_SECONDS}s")


@contextmanager
def _branch(infrahub: InfrahubClientSync) -> Iterator[str]:
    """A disposable Infrahub branch, deleted afterwards whether or not it was merged."""
    name = f"sync-configuration-{uuid.uuid4().hex[:8]}"
    infrahub.branch.create(branch_name=name, description="configurations in Infrahub: a proposed change")
    try:
        yield name
    finally:
        if name in infrahub.branch.all():
            infrahub.branch.delete(name)


def test_a_run_records_the_version_it_uses_and_reuses_unchanged_content(
    api: httpx.Client, infrahub: InfrahubClientSync, live_stack: LiveStack
) -> None:
    """Lazy versions, reuse, a merged change, simultaneous starts, refusal, branch validation."""
    config_id = f"configurations-in-infrahub-{uuid.uuid4().hex[:12]}"
    put_configuration(infrahub, config_id, _package(live_stack.infrahub))

    first = _planned_version(api, config_id)
    assert first["registry_version"] == 1, first
    mirrored = _mirrored_run(infrahub, first["run_id"])
    # The account that asked, as Infrahub identified it, not a name Sync configured.
    assert mirrored.requested_by.value == first["actor"], mirrored.requested_by.value
    assert first["actor"], first

    again = _planned_version(api, config_id)
    assert again["registry_version"] == 1, again
    assert again["package_checksum"] == first["package_checksum"]
    assert _versions(api, config_id) == [1]

    # A change proposed on a branch is validated there and records nothing until merged.
    with _branch(infrahub) as branch:
        put_configuration(infrahub, config_id, _package(live_stack.infrahub, verify_ssl=True), branch=branch)
        proposed = api.post(f"/configs/{config_id}/validate", params={"branch": branch})
        assert proposed.status_code == 200, proposed.text
        assert proposed.json()["findings"] == [], proposed.text
        assert proposed.json()["checksum"] != first["package_checksum"]
        assert _versions(api, config_id) == [1]
        infrahub.branch.merge(branch)
    merged = _planned_version(api, config_id)
    assert merged["registry_version"] == 2, merged
    assert merged["package_checksum"] == proposed.json()["checksum"]
    # Version 1 is immutable: the merge changed the configuration, not its recorded history.
    assert api.get(f"/configs/{config_id}/versions/1").json()["package_checksum"] == first["package_checksum"]

    # Two runs started together on new content share the one version the lock records.
    put_configuration(infrahub, config_id, _package(live_stack.infrahub, verify_ssl=False))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        together = list(pool.map(lambda _: _start_plan(api, config_id), range(2)))
    assert [response.status_code for response in together] == [202, 202], [response.text for response in together]
    assert {response.json()["run"]["registry_version"] for response in together} == {3}
    assert _versions(api, config_id) == [1, 2, 3]

    # An invalid change on an unmerged branch: findings come back, and nothing is recorded.
    with _branch(infrahub) as branch:
        put_configuration(infrahub, config_id, _package(live_stack.infrahub, token=LITERAL_TOKEN), branch=branch)
        checked = api.post(f"/configs/{config_id}/validate", params={"branch": branch})
        assert checked.status_code == 200, checked.text
        assert [finding["code"] for finding in checked.json()["findings"]] == ["inline-credential-value"], checked.text
    assert _versions(api, config_id) == [1, 2, 3]

    # The same invalid document on the default branch: the run is refused, no version 4.
    put_configuration(infrahub, config_id, _package(live_stack.infrahub, token=LITERAL_TOKEN))
    refused = _start_plan(api, config_id)
    assert refused.status_code == 422, refused.text
    error = refused.json()["error"]
    assert error["code"] == "configuration-invalid", error
    assert "/configuration/source/settings/token" in error["message"], error
    assert LITERAL_TOKEN not in refused.text
    assert _versions(api, config_id) == [1, 2, 3]


def test_configurations_are_not_created_or_versioned_through_the_sync_api(
    api: httpx.Client, infrahub: InfrahubClientSync, live_stack: LiveStack
) -> None:
    """`POST /configs` and `POST /configs/{id}/versions` answer 410 and record nothing."""
    config_id = f"configurations-in-infrahub-{uuid.uuid4().hex[:12]}"
    put_configuration(infrahub, config_id, _package(live_stack.infrahub))
    body = {"package": _package(live_stack.infrahub, verify_ssl=True), "reason": REASON}

    registered = api.post("/configs", headers={"Idempotency-Key": f"refused-{uuid.uuid4()}"}, json=body)
    versioned = api.post(
        f"/configs/{config_id}/versions", headers={"Idempotency-Key": f"refused-{uuid.uuid4()}"}, json=body
    )

    for response in (registered, versioned):
        assert response.status_code == 410, response.text
        assert response.json()["error"]["code"] == IN_INFRAHUB, response.text
    assert _versions(api, config_id) == []
