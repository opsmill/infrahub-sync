"""The branch and proposed change operations of a scheduled run, against a running Infrahub.

contracts/branch-review.md and research R15 of spec 021. The test creates one branch with a
unique name, opens a proposed change from it, reads and closes the proposed change, and deletes
the branch in `finally`. It also repeats each operation, to check that repeating is safe. The
proposed change stays in Infrahub in the state `closed`; its title starts with
`infrahub-sync integration test`.

The token's account needs the permissions of contracts/branch-review.md ("Permissions in
Infrahub"), and the destination's default branch must be `main`. Run with::

    INFRAHUB_ADDRESS=http://localhost:8000 \\
    INFRAHUB_API_TOKEN=<token> \\
    uv run pytest -m integration tests/integration/test_branch_review_operations_integration.py
"""

from __future__ import annotations

import os
import uuid

import pytest

from infrahub_sync.service.branch_review import (
    CloseResult,
    build_client,
    check_default_branch_is_main,
    close_proposed_change,
    create_branch,
    delete_branch,
    open_proposed_change,
    read_proposed_change,
)

pytestmark = pytest.mark.integration


def _env_or_skip() -> tuple[str, str]:
    address = os.environ.get("INFRAHUB_ADDRESS")
    token = os.environ.get("INFRAHUB_API_TOKEN")
    if not address or not token:
        pytest.skip("INFRAHUB_ADDRESS and INFRAHUB_API_TOKEN must be set")
    return address, token


def test_the_five_operations_against_infrahub() -> None:
    address, token = _env_or_skip()
    branch = f"sync/integration-test-{uuid.uuid4().hex[:12]}"
    # A registered context: the client reads the address and the token from these settings only.
    settings = {"url": address, "token": token, "_infrahub_sync_registered_context": True}
    client = build_client(settings, branch)

    check_default_branch_is_main(client)
    try:
        create_branch(client, branch)
        # A branch that exists counts as created.
        create_branch(client, branch)

        proposed_change_id = open_proposed_change(
            client,
            branch=branch,
            title=f"infrahub-sync integration test {branch}",
            description="Opened by tests/integration/test_branch_review_operations_integration.py",
        )
        assert read_proposed_change(client, proposed_change_id) == "open"
        assert read_proposed_change(client, proposed_change_id, branch="main") == "open"

        closed = close_proposed_change(client, proposed_change_id, f"Replaced by test (run {branch})")
        assert closed == CloseResult(state="open", closed_now=True)
        assert read_proposed_change(client, proposed_change_id) == "closed"

        # A proposed change that is closed counts as closed, with no request to change it.
        again = close_proposed_change(client, proposed_change_id, "not sent")
        assert again == CloseResult(state="closed", closed_now=False)
        assert again.done
    finally:
        delete_branch(client, branch)

    # A branch that no longer exists counts as deleted.
    delete_branch(client, branch)
