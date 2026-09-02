"""The pre-write gate: an apply refuses a plan the destination schema has moved under.

A saved plan records the destination schema semantics it was computed against, and the
apply recomputes them from one live read before any adapter is constructed. This module
makes that happen for real — plan, change a consumed attribute's kind on the destination
branch, apply — and reads the refusal back through all three interfaces: the CLI's
rendering, the typed client's recorded apply failure, and the raw results body.

The change is scoped to the disposable smoke branch and is reversed in a `finally`, with
the original kind read from the running destination first rather than assumed, so a
failure mid-test cannot leave the branch on a schema the other modules do not expect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import yaml

from infrahub_sync.client import SyncClient
from tasks.preview import SCHEMA_FILE, SHARED_DEVICE_NAME, SMOKE_BRANCH, SMOKE_KIND
from tests.preview.evidence import canary_leaks
from tests.preview.test_cli_client import ANSI, package_file, run_cli, run_cli_command
from tests.preview.test_service_api import (
    authenticated_client,
    device_types,
    infrahub_client,
    seed_source_branch,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.preview

REASON = "preview qualification: refuse an apply whose destination schema moved"
# The plan maps this attribute, so its kind is part of the semantics the fingerprint
# covers. Text and TextArea both hold the seeded string values, so the conversion is
# reversible in either direction and no device loses its `type`.
DRIFTED_ATTRIBUTE = "type"
ORIGINAL_KIND = "Text"
DRIFTED_KIND = "TextArea"


def _attribute_kind(client: Any, attribute: str) -> str:  # noqa: ANN401 — the SDK's sync client
    """The destination branch's current kind for one attribute of the smoke node."""
    node = client.schema.get(kind=SMOKE_KIND, branch=SMOKE_BRANCH, refresh=True)
    return next(declared.kind for declared in node.attributes if declared.name == attribute)


def _load_attribute_kind(client: Any, kind: str) -> None:  # noqa: ANN401 — the SDK's sync client
    """Load the seeded node schema onto the smoke branch with one attribute kind changed.

    Infrahub applies a loaded schema asynchronously, so the load waits for convergence:
    the apply under test reads the destination schema live, and an unconverged read would
    make the refusal depend on timing rather than on the change.
    """
    schema = yaml.safe_load(SCHEMA_FILE.read_text(encoding="utf-8"))
    for node in schema["nodes"]:
        for attribute in node["attributes"]:
            if attribute["name"] == DRIFTED_ATTRIBUTE:
                attribute["kind"] = kind
    client.schema.load(schemas=[schema], branch=SMOKE_BRANCH, wait_until_converged=True)
    assert _attribute_kind(client, DRIFTED_ATTRIBUTE) == kind


def test_an_apply_refuses_a_plan_whose_destination_schema_changed(
    preview_env: dict[str, Any], tmp_path: Path, deliberate_terminal_flow_runs: dict[str, str]
) -> None:
    """`PlanSchemaChangedError` through CLI rendering, `get_results`, and the raw body."""
    seed_source_branch(preview_env)
    sdk = infrahub_client(preview_env)
    assert _attribute_kind(sdk, DRIFTED_ATTRIBUTE) == ORIGINAL_KIND

    registered = run_cli(
        preview_env,
        "configs",
        "register",
        str(package_file(preview_env, tmp_path)),
        "--reason",
        REASON,
    )
    planned = run_cli(
        preview_env,
        "diff",
        "--config-id",
        registered["config_id"],
        "--version",
        registered["registry_version"],
        "--branch",
        SMOKE_BRANCH,
        "--reason",
        REASON,
    )
    run_id, checksum = planned["run_id"], planned["plan_checksum"]
    assert planned["schema_fingerprint"], planned
    before = device_types(sdk, SMOKE_BRANCH)[SHARED_DEVICE_NAME]

    try:
        _load_attribute_kind(sdk, DRIFTED_KIND)
        refused = run_cli_command(
            preview_env,
            "apply",
            run_id,
            "--expected-checksum",
            checksum,
            "--branch",
            SMOKE_BRANCH,
            "--reason",
            REASON,
        )
    finally:
        _load_attribute_kind(sdk, ORIGINAL_KIND)

    assert refused.returncode == 1, refused.stdout
    rendered = ANSI.sub("", refused.stderr)
    assert "apply failed: PlanSchemaChangedError" in rendered, rendered
    assert "hint: create and review a new plan before applying again" in rendered, rendered

    with SyncClient(preview_env["urls"]["sync_api"], preview_env["bearer_token"], timeout=30.0) as client:
        results = client.get_results(run_id)
    failure = results.results["apply_failure"]
    assert failure["stage"] == "apply"
    assert failure["error_type"] == "PlanSchemaChangedError"
    # The gate runs before any adapter is constructed, so a refusal cannot have written.
    assert failure.get("may_have_partially_written") in {None, False}, failure

    with authenticated_client(preview_env) as api:
        recorded = api.get(f"/runs/{run_id}")
        assert recorded.status_code == 200, recorded.text
        assert recorded.json()["run"]["phase"] == "apply-failed", recorded.text
        body = api.get(f"/runs/{run_id}/results")
        assert body.status_code == 200, body.text
        assert body.json()["results"]["apply_failure"]["error_type"] == "PlanSchemaChangedError", body.text
        deliberate_terminal_flow_runs[recorded.json()["orchestration"][-1]["flow_run_id"]] = "FAILED"

    assert device_types(sdk, SMOKE_BRANCH)[SHARED_DEVICE_NAME] == before
    assert _attribute_kind(sdk, DRIFTED_ATTRIBUTE) == ORIGINAL_KIND
    assert (
        canary_leaks(
            preview_env["infrahub_token"],
            {
                "apply stdout": refused.stdout,
                "apply stderr": refused.stderr,
                "get_results resource": results,
                "raw results body": body.text,
            },
        )
        == []
    )
