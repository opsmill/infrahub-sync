"""Sync API surface: auth boundary and the full registered run lifecycle.

The shipped API is registered-only: a run names a registered configuration version, not
a directory on the worker's disk. So the smoke registers its own package first, through
`POST /configs`, and drives plan → review → apply against that exact version.

The package is Infrahub-to-Infrahub against the preview's own instance — `main` as the
source, the disposable smoke branch as the destination — because the registered path
resolves adapters through the installed loader and admits no filesystem adapter.

What this smoke proves is an **update**. `preview.seed` puts one device on `main` before
the smoke branch forks, so both branches hold it; the smoke then mutates that device on
`main` alone, and the registered workflow has one real cross-branch update to plan and
apply. The mutation carries a fresh value on every run, because a fixed one would
converge: the first apply writes it to the destination, the next run's mirror copies it
straight back, and the update the assertions rest on would quietly vanish.

Everything else has to compare equal, or the plan stops being that single update. So the
smoke mirrors every device the destination holds into `main` first — copied **verbatim**,
every mapped field — leaving nothing source-absent and nothing destination-absent. That
matters because a plan of deletes alone still completes green: a v2 artifact records
deletes without ever executing them, so such an apply reaches `applied` having written
nothing at all.

Copying verbatim is the part that has to be exact. Mirroring a device with any
manufactured value makes it an update instead of a match, and an update that rewrites a
device's own unique attribute is rejected by the destination's uniqueness constraint —
so the apply fails on its first operation having written nothing.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

import httpx
import pytest

from tasks.preview import SHARED_DEVICE_NAME, SMOKE_BRANCH, SMOKE_KIND

pytestmark = pytest.mark.preview

POLL_TIMEOUT_SECONDS = 240

# The fields the package maps. A mirror has to carry all of them, or the mirrored device
# becomes an update rather than a match. The kind and the shared device's name come from
# `tasks.preview`, whose `seed` task puts that device on `main` before the branch forks.
SMOKE_FIELDS = ("name", "type")


def smoke_package(infrahub_url: str) -> dict[str, Any]:
    """The declared package the smoke registers, as `POST /configs` accepts it.

    Both adapters are the bundled `infrahub` one, so the registered worker resolves them
    through the installed loader with nothing generated and nothing on the filesystem. The
    token is a credential *reference* — the worker resolves `INFRAHUB_API_TOKEN` from its
    own environment, so no secret value is ever posted or recorded.
    """
    return {
        "format_version": 1,
        "configuration": {
            "name": "preview-smoke-registered",
            "source": {
                "name": "infrahub",
                "settings": {"url": infrahub_url, "branch": "main", "token": {"$credential": "infrahub-token"}},
            },
            "destination": {
                "name": "infrahub",
                "settings": {
                    "url": infrahub_url,
                    "branch": SMOKE_BRANCH,
                    "token": {"$credential": "infrahub-token"},
                },
            },
            "schema_mapping": [
                {
                    "name": SMOKE_KIND,
                    "mapping": SMOKE_KIND,
                    "identifiers": ["name"],
                    # Derived from `SMOKE_FIELDS` so the mirror below cannot cover fewer
                    # fields than the plan compares.
                    "fields": [{"name": name, "mapping": name} for name in SMOKE_FIELDS],
                }
            ],
        },
        "credentials": {"infrahub-token": {"provider": "env", "identifier": "INFRAHUB_API_TOKEN"}},
    }


def register_request(infrahub_url: str) -> dict[str, Any]:
    """The `POST /configs` body: a declared package plus its audit reason."""
    return {"package": smoke_package(infrahub_url), "reason": "preview smoke: register the smoke configuration"}


def create_run_request(config_id: str, registry_version: int) -> dict[str, Any]:
    """The `POST /runs` body: a registered version, never a directory name."""
    return {
        "operation": "plan",
        "config_id": config_id,
        "registry_version": registry_version,
        "branch": SMOKE_BRANCH,
        "reason": "preview smoke: create a service plan",
    }


def apply_run_request(checksum: str) -> dict[str, Any]:
    """The `POST /runs/{id}/apply` body: the reviewed checksum the operator approved."""
    return {
        "expected_checksum": checksum,
        "confirm_writes": True,
        "branch": SMOKE_BRANCH,
        "reason": "preview smoke: apply the reviewed plan",
    }


def unwritten_plan_reasons(summary: Mapping[str, Any]) -> list[str]:
    """Why this plan would write nothing at the destination, or `[]` when it would write.

    The trap this smoke exists to catch: a plan of deletes alone. FR-016 records deletes
    and never executes them, so such an apply reaches `applied` with a valid checksum and
    a recorded schema binding while leaving the destination untouched — every signal the
    smoke used to check stays green. A plan that exercises the approved path carries at
    least one create or update and no delete for the apply to skip.
    """
    by_action = dict(summary.get("by_action", {}))
    reasons: list[str] = []
    if by_action.get("create", 0) + by_action.get("update", 0) == 0:
        reasons.append(f"no create or update operation to apply (by_action={by_action})")
    if summary.get("deletes_not_executed", 0):
        reasons.append(f"{summary['deletes_not_executed']} delete operation(s) would be recorded and skipped")
    return reasons


def _client(preview_env: dict[str, Any], token: str | None) -> httpx.Client:
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(base_url=preview_env["urls"]["sync_api"], headers=headers, timeout=30)


def _idempotency() -> dict[str, str]:
    """A fresh key per mutation, so a re-run never replays an earlier smoke's response."""
    return {"Idempotency-Key": f"preview-smoke-{uuid.uuid4()}"}


def _wait_for_phase(client: httpx.Client, run_id: str, target_phase: str) -> dict[str, Any]:
    """Poll until the durable record reaches the target phase.

    Polling ``finished_at`` is not enough: an admitted apply continues the
    planning run's record, and only the flow's eventual finish updates the
    phase — the plan stage's ``finished_at`` is already set and stays set.
    """
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        phase = payload["run"]["phase"]
        if phase == target_phase:
            return payload
        if "failed" in phase:
            pytest.fail(f"run {run_id} failed while waiting for {target_phase!r}: {payload['run']}")
        time.sleep(3)
    pytest.fail(f"run {run_id} did not reach {target_phase!r} within {POLL_TIMEOUT_SECONDS}s: {payload}")


def _registered_version(client: httpx.Client, preview_env: dict[str, Any]) -> tuple[str, int]:
    """Register the smoke package and prove the returned version validates cleanly."""
    registered = client.post("/configs", headers=_idempotency(), json=register_request(preview_env["urls"]["infrahub"]))
    assert registered.status_code == 201, registered.text
    version = registered.json()["version"]
    config_id, registry_version = version["config_id"], version["registry_version"]

    validated = client.post(f"/configs/{config_id}/versions/{registry_version}/validate")
    assert validated.status_code == 200, validated.text
    assert validated.json()["findings"] == [], validated.text
    return config_id, registry_version


def infrahub_client(preview_env: dict[str, Any]) -> Any:  # noqa: ANN401 — the SDK's sync client
    from infrahub_sdk import InfrahubClientSync

    return InfrahubClientSync(
        address=preview_env["urls"]["infrahub"], config={"api_token": preview_env["infrahub_token"]}
    )


def mirrored_device_payloads(nodes: Iterable[Any]) -> list[dict[str, Any]]:
    """Copy each destination device verbatim — every mapped field, not just its identity.

    A mirror exists so the source and destination compare **equal** for these devices and
    the plan reduces to the one device the smoke owns. Manufacturing any value instead of
    copying it turns each mirrored device into an update, and an update that rewrites a
    device's own unique attributes is rejected at the destination — which is exactly how
    the first live replay failed. `.value` is how the adapter reads an attribute, so it is
    how the mirror reads one too.
    """
    return [{name: getattr(node, name).value for name in SMOKE_FIELDS} for node in nodes]


def mutated_device_type() -> str:
    """A `type` value no earlier run used, for the one device the smoke mutates.

    Freshness is what keeps the smoke honest across runs. A fixed value converges: the
    first apply writes it to the destination, the next run's mirror copies it back into
    `main`, the shared device then compares equal, and the plan holds no update at all.
    """
    return f"preview-smoke-{uuid.uuid4().hex[:12]}"


def mutation_payload(mutated_type: str) -> dict[str, Any]:
    """The single post-fork write on `main`: the shared device's new mapped values.

    It names the device `preview.up` seeded before the smoke branch forked, so the write
    lands on one the destination already holds — an update, never a create.
    """
    return {"name": SHARED_DEVICE_NAME, "type": mutated_type}


def device_types(client: Any, branch: str) -> dict[str, Any]:  # noqa: ANN401 — the SDK's sync client
    return {node.name.value: node.type.value for node in client.all(kind=SMOKE_KIND, branch=branch)}


def seed_source_branch(preview_env: dict[str, Any]) -> str:
    """Leave `main` differing from the destination in one device, and return its new type.

    Mirroring first is what removes everything else from the plan: `main` ends up holding
    exactly the devices the destination holds, carrying exactly the destination's values,
    so nothing is created and nothing is deleted. Mutating the shared device afterwards
    leaves the single update this smoke exists to apply — afterwards, because a mirror
    run second would copy the destination's value straight back over the mutation.

    Both writes upsert on the device's unique name, so a re-run converges rather than
    duplicating.
    """
    client = infrahub_client(preview_env)
    payloads = mirrored_device_payloads(client.all(kind=SMOKE_KIND, branch=SMOKE_BRANCH))
    assert SHARED_DEVICE_NAME in {payload["name"] for payload in payloads}, (
        f"{SHARED_DEVICE_NAME!r} is not on {SMOKE_BRANCH}; `tasks.preview.ensure_smoke_branch` seeds it on "
        f"main before the branch forks, and without it this smoke has no update to plan; "
        f"run `uv run invoke preview.seed`"
    )
    for payload in payloads:
        client.create(kind=SMOKE_KIND, branch="main", data=payload).save(allow_upsert=True)
    mutated_type = mutated_device_type()
    client.create(kind=SMOKE_KIND, branch="main", data=mutation_payload(mutated_type)).save(allow_upsert=True)
    return mutated_type


def test_requests_without_a_bearer_token_are_refused(preview_env: dict[str, Any]) -> None:
    with _client(preview_env, token=None) as client:
        response = client.get("/runs/does-not-exist")
    assert response.status_code == 401


def test_service_plan_and_apply_lifecycle(preview_env: dict[str, Any]) -> None:
    mutated_type = seed_source_branch(preview_env)
    assert device_types(infrahub_client(preview_env), SMOKE_BRANCH)[SHARED_DEVICE_NAME] != mutated_type

    with _client(preview_env, token=preview_env["bearer_token"]) as client:
        config_id, registry_version = _registered_version(client, preview_env)

        created = client.post("/runs", headers=_idempotency(), json=create_run_request(config_id, registry_version))
        assert created.status_code == 202, created.text
        run_id = created.json()["run"]["run_id"]

        planned = _wait_for_phase(client, run_id, "planned")
        assert planned["run"]["outcome"] is not None, planned["run"]

        plan_view = client.get(f"/runs/{run_id}/plan")
        assert plan_view.status_code == 200, plan_view.text
        plan_payload = plan_view.json()
        assert plan_payload["checksum_ok"] is True
        # A registered plan records the destination schema semantics it was computed
        # against; the apply refuses before any write when they no longer match.
        assert plan_payload["schema_fingerprint"], plan_payload
        # The plan must exercise the approved create/update path; a delete-only plan
        # applies cleanly and writes nothing.
        summary = plan_payload["summary"]
        assert unwritten_plan_reasons(summary) == [], summary
        # Exactly the one update the pre-fork seed and the post-fork mutation set up. A
        # create means the mirror missed a device the destination holds; a skipped delete
        # means `main` is not the destination's mirror; a second update means the mirror
        # manufactured a value instead of copying it.
        assert summary["by_action"].get("update", 0) == 1, summary
        assert summary["by_action"].get("create", 0) == 0, summary
        assert summary.get("deletes_not_executed", 0) == 0, summary
        checksum = plan_payload["checksum"]

        apply_accepted = client.post(f"/runs/{run_id}/apply", headers=_idempotency(), json=apply_run_request(checksum))
        assert apply_accepted.status_code == 202, apply_accepted.text

        applied = _wait_for_phase(client, run_id, "applied")
        assert applied["run"]["outcome"] is not None, applied["run"]
        # What the apply actually did, not merely that it finished.
        applied_summary = applied["run"]["summary"]
        assert applied_summary.get("update", 0) > 0, applied_summary
        assert applied_summary.get("create", 0) == 0, applied_summary
        assert applied_summary.get("delete", 0) == 0, applied_summary

        results = client.get(f"/runs/{run_id}/results")
        assert results.status_code == 200, results.text
        # The applied-operation identifiers stay on the worker's own run file; over HTTP
        # the recorded action counts are the positive-applied evidence.
        assert results.json()["results"]["summary"]["update"] > 0, results.text

    # The destination now carries the value the source was mutated to.
    assert device_types(infrahub_client(preview_env), SMOKE_BRANCH)[SHARED_DEVICE_NAME] == mutated_type
