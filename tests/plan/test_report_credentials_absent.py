"""No credential and no exception text reaches the plan files (spec 009 T039, SC-005, SC-007).

The two fixture credentials are configured as referenced credentials, resolved into the source
adapter's settings as a run resolves them, and placed in the text of every exception the
fixture adapter raises while loading. The assertions read the raw bytes of the three plan files
and of every report entry, so they hold whether or not any value replacement exists.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from infrahub_sync import PeerReferenceError, SyncAdapter
from infrahub_sync.plan.writer import MANIFEST_FILE_NAME, OPERATIONS_FILE_NAME, PLAN_DIR_NAME, REPORT_FILE_NAME
from tests.plan.problem_fixtures import (
    SHORT_CREDENTIAL,
    SHORT_CREDENTIAL_NAME,
    SPECIAL_CREDENTIAL,
    SPECIAL_CREDENTIAL_NAME,
    ProblemFixtureAdapter,
)
from tests.plan.problem_plan import fixture_config, plan_fixture

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from infrahub_sync import SyncConfig

# Text that only the adapter's exception messages hold.
EXCEPTION_MARKER = "adapter-exception-text"


class LeakyFixtureAdapter(ProblemFixtureAdapter):
    """The fixture adapter, with the credentials in its settings and in every exception it raises."""

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, **kwargs: Any) -> None:  # noqa: ANN401
        """Resolve the referenced credentials into the settings, as a run does."""
        super().__init__(target, adapter, config, **kwargs)
        self.settings["token"] = SHORT_CREDENTIAL
        self.settings["password"] = SPECIAL_CREDENTIAL
        self.raised = 0

    def peer_unique_id(self, *args: Any, **kwargs: Any) -> str | None:  # noqa: ANN401
        """Re-raise every peer error with the credentials in its text."""
        try:
            return super().peer_unique_id(*args, **kwargs)
        except PeerReferenceError as exc:
            self.raised += 1
            exc.args = (f"{exc} {EXCEPTION_MARKER} token={SHORT_CREDENTIAL} password={SPECIAL_CREDENTIAL}",)
            raise


def _forms(value: str) -> list[bytes]:
    """The value as raw UTF-8 and as it would appear inside a JSON string."""
    return [value.encode("utf-8"), json.dumps(value)[1:-1].encode("utf-8")]


def test_no_credential_and_no_exception_text_reaches_the_plan_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SHORT_CREDENTIAL_NAME, SHORT_CREDENTIAL)
    monkeypatch.setenv(SPECIAL_CREDENTIAL_NAME, SPECIAL_CREDENTIAL)
    config = fixture_config()
    config.source = SyncAdapter(
        name="problem_fixture",
        settings={
            "url": "https://source.invalid",
            "token": {"$credential": SHORT_CREDENTIAL_NAME},
            "password": {"$credential": SPECIAL_CREDENTIAL_NAME},
        },
    )

    planned = plan_fixture(tmp_path / "run", source_class=LeakyFixtureAdapter, config=config)

    # The fixture raised peer errors whose text holds both credentials.
    assert isinstance(planned.source, LeakyFixtureAdapter)
    assert planned.source.raised >= 2
    files = {
        name: (planned.run_dir / PLAN_DIR_NAME / name).read_bytes()
        for name in (OPERATIONS_FILE_NAME, REPORT_FILE_NAME, MANIFEST_FILE_NAME)
    }
    entries = json.dumps(planned.report.entries()).encode("utf-8")
    assert planned.report.problems
    for secret in (SHORT_CREDENTIAL, SPECIAL_CREDENTIAL, EXCEPTION_MARKER):
        for form in _forms(secret):
            assert form not in entries, secret
            for name, data in files.items():
                assert form not in data, (secret, name)
    for problem in planned.report.problems:
        for secret in (SHORT_CREDENTIAL, SPECIAL_CREDENTIAL, EXCEPTION_MARKER, "Next action", "Traceback"):
            assert secret not in problem.reason
