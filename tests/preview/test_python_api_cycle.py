"""Public Python API surface: the documented plan → verify → apply cycle."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from infrahub_sync.api.v1 import ApplyRequest, PlanRequest, VerifyRequest, apply, plan, verify
from tasks.preview import EXPECT_MAIN_EMPTY_ENV, SMOKE_BRANCH

pytestmark = pytest.mark.preview


def test_python_api_plan_verify_apply(cli_environment: dict[str, Any], tmp_path: Path) -> None:
    product_cache = str(tmp_path / "product-cache")

    result = plan(
        PlanRequest(
            sync_name="custom-example",
            config_directory=cli_environment["examples_dir"],
            branch=SMOKE_BRANCH,
            product_cache_location=product_cache,
        )
    )
    assert result.run_id

    verified = verify(
        VerifyRequest(
            sync_name="custom-example",
            config_directory=cli_environment["examples_dir"],
            run_id=result.run_id,
            product_cache_location=product_cache,
        )
    )
    assert verified.outcome == "verified"

    manifest_path = next(artifact.path for artifact in result.artifacts if artifact.kind == "plan-manifest")
    reviewed_checksum = json.loads(Path(manifest_path).read_text(encoding="utf-8"))["plan_checksum"]

    applied = apply(
        ApplyRequest(
            sync_name="custom-example",
            config_directory=cli_environment["examples_dir"],
            run_id=result.run_id,
            expected_checksum=reviewed_checksum,
            branch=SMOKE_BRANCH,
            product_cache_location=product_cache,
        )
    )
    assert applied.outcome in {"applied", "no-change"}


def test_startup_smoke_preserves_main_for_manual_walkthrough(cli_environment: dict[str, Any], tmp_path: Path) -> None:
    if os.environ.get(EXPECT_MAIN_EMPTY_ENV) != "1":
        pytest.skip("pristine-main acceptance runs only during preview.up")

    main_result = plan(
        PlanRequest(
            sync_name="custom-example",
            config_directory=cli_environment["examples_dir"],
            branch="main",
            product_cache_location=str(tmp_path / "main-product-cache"),
        )
    )
    assert main_result.counts.create == 5, "smoke writes must leave main empty for the manual walkthrough"
    assert main_result.counts.update == 0
    assert main_result.counts.delete == 0
