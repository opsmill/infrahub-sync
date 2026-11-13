"""The client's `resume` request and the run's progress view (spec 022 T023, contracts/http-api.md)."""

from __future__ import annotations

import json

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
        if request.url.path == "/version":
            return httpx.Response(200, json=_version())
        sent.append(request)
        return httpx.Response(202, json=accepted)

    request = ResumeRunRequest(expected_checksum=_CHECKSUM, confirm_writes=True, reason="worker restarted")
    resource = _client(handler).resume("run-1", request, "resume-key")

    [posted] = sent
    assert (posted.method, posted.url.path) == ("POST", "/runs/run-1/resume")
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
        if request.url.path == "/version":
            return httpx.Response(200, json=_version())
        return httpx.Response(200, json=_run("run-1"))

    resource = _client(handler).get_run("run-1")

    assert resource.apply_progress is None
    assert resource.resumes == ()


@pytest.mark.parametrize("code", ["verify-required", "run-not-resumable", "run-execution-conflict"])
def test_a_refused_resume_raises_an_api_error_with_its_code(code: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json=_version())
        return httpx.Response(
            409,
            json={"error": {"code": code, "message": "refused", "status": 409, "run_id": "run-1", "mutation_id": None}},
        )

    with pytest.raises(APIError) as raised:
        _client(handler).resume("run-1", ResumeRunRequest(expected_checksum=_CHECKSUM, confirm_writes=True), "key")

    assert (raised.value.status, raised.value.code, raised.value.run_id) == (409, code, "run-1")
