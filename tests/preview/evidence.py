"""What the live qualification rows capture: an HTTP transcript and a secret canary scan.

Two helpers, shared by every module that drives the running stack. The transcript hook is
what makes an in-process `httpx` client an auditable direct-HTTP surface: without a
recorded request and response pair, "the raw route was exercised" rests on the test's own
narration. The canary scan is the single place the preview's Infrahub token is looked for,
so a newly captured artifact is covered by naming it in one mapping rather than by writing
another check.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    import httpx

REDACTED = "<redacted>"


def _headers(headers: httpx.Headers) -> dict[str, str]:
    """Header names with the authorization value replaced, never the header itself.

    Which routes were called authenticated is the evidence; the bearer value is not.
    """
    return {name: (REDACTED if name.lower() == "authorization" else value) for name, value in headers.items()}


def transcript_hooks(path: Path) -> dict[str, list[Callable[[httpx.Response], None]]]:
    """Return `httpx` event hooks appending one JSON record per exchange to `path`.

    The hook reads the response before serializing it. A response handed to an event hook
    has not been read yet, and touching `.text` first raises `ResponseNotRead` — which
    would lose the exchange the transcript exists to record.
    """
    path.write_text("", encoding="utf-8")

    def record(response: httpx.Response) -> None:
        response.read()
        request = response.request
        entry = {
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "request_headers": _headers(request.headers),
            "response_headers": _headers(response.headers),
            "request_body": request.content.decode("utf-8", "replace"),
            "response_body": response.text,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")

    return {"response": [record]}


def canary_leaks(canary: str, artifacts: Mapping[str, object]) -> list[str]:
    """Name every captured artifact whose rendered text carries the canary value.

    Bytes are decoded and everything else is rendered through `repr`, because a run
    produces exactly those three shapes: CLI output and transcript text, artifact and
    response bytes, and the typed resources the Python client returns.
    """
    leaked = []
    for name, artifact in sorted(artifacts.items()):
        if isinstance(artifact, str):
            text = artifact
        elif isinstance(artifact, bytes):
            text = artifact.decode("utf-8", "replace")
        else:
            text = repr(artifact)
        if canary in text:
            leaked.append(name)
    return leaked
