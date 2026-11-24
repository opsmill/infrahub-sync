"""The client's `resume` request, the run's progress view (spec 016 T023) and its automatic resumes (spec 030 T024)."""

from __future__ import annotations

import json
from typing import Any, cast

import httpx
import pytest

from infrahub_sync.client import APIError, ResumeRunRequest, SyncClient
from tests.client.test_client import TOKEN, _run, _version

_CHECKSUM = "a" * 64


def _client(handler: object) -> SyncClient:
    return SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))  # ty: ignore[invalid-argument-type]


def test_resume_posts_the_request_with_the_idempotency_key_and_parses_the_progress_view() -> None:
    sent: list[httpx.Request] = []
    accepted = {
        **_run("run-1"),
        "apply_progress": {
            "position": 5999,
            "saved_at": "2026-11-12T10:15:00Z",
            "resumable": True,
            "verify_required": False,
            "by_kind": [{"kind": "DcimDevice", "committed": 6000, "uncertain": 0, "not_reached": 4000}],
        },
        "resumes": [
            {
                "actor": "operator",
                "requested_at": "2026-11-12T11:02:00Z",
                "reason": None,
                "start_after": 5999,
                "outcome": None,
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json=_version())
        sent.append(request)
        return httpx.Response(202, json=accepted)

    request = ResumeRunRequest(expected_checksum=_CHECKSUM, confirm_writes=True, reason="worker restarted")
    resource = _client(handler).resume("run-1", request, "resume-key")

    [posted] = sent
    assert (posted.method, posted.url.path) == ("POST", "/api/runs/run-1/resume")
    assert posted.headers["Idempotency-Key"] == "resume-key"
    assert json.loads(posted.content) == {
        "expected_checksum": _CHECKSUM,
        "confirm_writes": True,
        "confirm_left_out": False,
        "reason": "worker restarted",
    }
    assert resource.apply_progress is not None
    assert resource.apply_progress.position == 5999
    assert [(entry.kind, entry.committed, entry.not_reached) for entry in resource.apply_progress.by_kind] == [
        ("DcimDevice", 6000, 4000)
    ]
    assert resource.resumes[0].start_after == 5999


def test_a_run_from_a_sync_api_that_predates_resume_has_no_progress_view() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json=_version())
        return httpx.Response(200, json=_run("run-1"))

    resource = _client(handler).get_run("run-1")

    assert resource.apply_progress is None
    assert resource.resumes == ()


@pytest.mark.parametrize("code", ["verify-required", "run-not-resumable", "run-execution-conflict"])
def test_a_refused_resume_raises_an_api_error_with_its_code(code: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json=_version())
        return httpx.Response(
            409,
            json={"error": {"code": code, "message": "refused", "status": 409, "run_id": "run-1", "mutation_id": None}},
        )

    with pytest.raises(APIError) as raised:
        _client(handler).resume("run-1", ResumeRunRequest(expected_checksum=_CHECKSUM, confirm_writes=True), "key")

    assert (raised.value.status, raised.value.code, raised.value.run_id) == (409, code, "run-1")


def _record(run_id: str) -> dict[str, Any]:
    """The public run record of `_run`, as a run list item."""
    return cast("dict[str, Any]", _run(run_id)["run"])


def _automatic_resume(**changes: object) -> dict[str, object]:
    """One automatic resume record as the Sync API returns it (spec 030 contracts/http-api.md)."""
    return {
        "interrupted_flow_run_id": "flow-apply",
        "actor": "Sync (automatic resume)",
        "cause": "rule-8",
        "cause_text": "the worker infrahub-sync-worker-3b2f stopped sending heartbeats",
        "state": "ended",
        "decision": "resume",
        "reason": None,
        "reason_text": None,
        "operations_not_checked": 0,
        "start_after": 5999,
        "check_flow_run_id": "flow-check",
        "resume_flow_run_id": "flow-resume",
        "decided_at": "2026-11-23T21:04:11Z",
        "updated_at": "2026-11-23T21:06:40Z",
        **changes,
    }


def test_a_run_parses_its_automatic_resumes_and_their_count() -> None:
    not_started = _automatic_resume(
        interrupted_flow_run_id="flow-resume",
        cause="rule-6",
        cause_text="Prefect reported the flow run as crashed",
        decision="no-resume",
        reason="check-found-differs",
        reason_text="the check found 1 node that differs from the plan",
        start_after=None,
        resume_flow_run_id=None,
    )
    body = {
        **_run("run-1"),
        "automatic_resumes": [_automatic_resume(), not_started],
        "automatic_resume_count": 1,
    }
    body["run"] = {**_record("run-1"), "automatic_resume_count": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json=_version())
        return httpx.Response(200, json=body)

    resource = _client(handler).get_run("run-1")

    assert (resource.automatic_resume_count, resource.run.automatic_resume_count) == (1, 1)
    started, ended = resource.automatic_resumes
    assert (started.cause, started.decision, started.start_after, started.operations_not_checked) == (
        "rule-8",
        "resume",
        5999,
        0,
    )
    assert started.cause_text == "the worker infrahub-sync-worker-3b2f stopped sending heartbeats"
    assert started.actor == "Sync (automatic resume)"
    assert (ended.decision, ended.reason, ended.reason_text) == (
        "no-resume",
        "check-found-differs",
        "the check found 1 node that differs from the plan",
    )


def test_a_run_from_a_sync_api_that_predates_automatic_resume_has_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json=_version())
        return httpx.Response(200, json=_run("run-1"))

    resource = _client(handler).get_run("run-1")

    assert (resource.automatic_resumes, resource.automatic_resume_count) == ((), 0)
    assert resource.run.automatic_resume_count == 0


def test_a_run_list_parses_the_automatic_resume_count() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json=_version())
        resumed = {**_record("run-2"), "automatic_resume_count": 2}
        return httpx.Response(200, json={"items": [resumed, _record("run-1")], "next_cursor": None})

    page = _client(handler).list_runs()

    assert [(run.run_id, run.automatic_resume_count) for run in page.items] == [("run-2", 2), ("run-1", 0)]
