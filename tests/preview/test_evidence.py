"""Offline cover for the two helpers the live qualification rows capture evidence with.

These run in the offline gate, not against the preview: both helpers are pure enough to
drive with a mock transport and plain values, and the properties they carry — an
authorization value never reaching a transcript, a token never reaching any captured
artifact — are exactly the ones a live run cannot be relied on to exercise. The live rows
apply them; this module proves they work.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx

from tests.preview.evidence import canary_leaks, transcript_hooks

if TYPE_CHECKING:
    from pathlib import Path

CANARY = "06438eb2-8019-4776-878c-0941b1f1d1ec"


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _client(path: Path, handler: object) -> httpx.Client:
    return httpx.Client(
        base_url="http://sync.invalid",
        transport=httpx.MockTransport(handler),  # ty: ignore[invalid-argument-type]
        event_hooks=transcript_hooks(path),
    )


def test_the_transcript_records_the_exchange_with_the_authorization_value_removed(tmp_path: Path) -> None:
    """Method, path, status and both header sets are recorded; only the value is dropped."""
    path = tmp_path / "transcript.jsonl"
    with _client(path, lambda _request: httpx.Response(201, json={"config_id": "c-1"})) as client:
        client.post("/configs", headers={"Authorization": "Bearer preview-tester-token-0001"}, json={"reason": "why"})

    (record,) = _records(path)
    assert record["method"] == "POST"
    assert record["path"] == "/configs"
    assert record["status"] == 201
    # The header name is evidence that the route was called authenticated; the value is not.
    assert record["request_headers"]["authorization"] == "<redacted>"
    assert "preview-tester-token-0001" not in path.read_text(encoding="utf-8")
    assert json.loads(record["request_body"]) == {"reason": "why"}
    assert json.loads(record["response_body"]) == {"config_id": "c-1"}


def test_the_transcript_records_a_response_body_the_hook_receives_unread(tmp_path: Path) -> None:
    """A streamed response is unread inside the hook; serializing it first loses the body.

    This is the fixture that separates a hook which reads before serializing from one that
    does not: `httpx` raises `ResponseNotRead` for the second, so the exchange is never
    recorded at all.
    """
    path = tmp_path / "transcript.jsonl"
    with _client(path, lambda _request: httpx.Response(200, content=iter([b'{"ok":', b"true}"]))) as client:
        client.get("/status")

    (record,) = _records(path)
    assert json.loads(record["response_body"]) == {"ok": True}


def test_canary_leaks_names_every_captured_artifact_carrying_the_token() -> None:
    """Text, bytes and an object rendered through `repr` are all scanned, and all named."""
    leaks = canary_leaks(
        CANARY,
        {
            "cli stdout": f"token: {CANARY}",
            "artifact bytes": f'{{"token":"{CANARY}"}}'.encode(),
            "client resource": httpx.URL(f"http://sync.invalid/?token={CANARY}"),
            "clean transcript": '{"path":"/configs"}',
        },
    )

    assert leaks == ["artifact bytes", "cli stdout", "client resource"]


def test_canary_leaks_reports_nothing_when_no_artifact_carries_the_token() -> None:
    leaks = canary_leaks(CANARY, {"cli stdout": "config_id: c-1", "artifact bytes": b'{"summary":{}}'})

    assert leaks == []
