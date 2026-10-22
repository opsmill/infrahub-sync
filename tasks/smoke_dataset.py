"""The smoke dataset that `dev.seed`, `dev.smoke`, `preview.seed` and `preview.smoke` write.

One schema, one device on `main`, and the disposable smoke branch forked after it. The
smoke suite (`tests/preview/`) asserts on these names. This module holds no Invoke task
and imports no other task module, so both task modules can use it.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from .utils import REPO_BASE

if TYPE_CHECKING:
    from invoke import Context

NAMESPACE = "INFRAHUB-SYNC-SMOKE"

SCHEMA_FILE = REPO_BASE / "examples" / "prefect_remote_run" / "schemas" / "infra_device.yml"
SMOKE_BRANCH = "preview-smoke"
# The kind the smoke schema defines, and the one device both branches share. It is
# seeded on `main` *before* the smoke branch forks, so the branch inherits it; the
# registered smoke then mutates it on `main` alone and has a real update to plan and
# apply rather than only a create. Its name is one `custom-example`'s own source
# already owns, so the CLI-cycle smoke still converges to zero operations instead of
# carrying a recorded, never-executed delete for it on every re-plan.
SMOKE_KIND = "InfraDevice"
SHARED_DEVICE_NAME = "core01"
SHARED_DEVICE_SEED_TYPE = "preview-seed"
# Infrahub applies a loaded schema asynchronously, and the seed immediately creates a node
# of the kind it just loaded. Without waiting for the schema to converge across Infrahub's
# workers, that create fails with `SchemaNotFoundError` on a fresh instance.
SCHEMA_CONVERGE_SECONDS = 120


def ensure_smoke_branch(address: str, token: str) -> None:
    """Seed the shared device on `main`, then fork the disposable smoke branch.

    The order is the point. A branch inherits what `main` held when it forked, so
    seeding first is what leaves one device present on both sides — which is what gives
    the registered smoke an update to plan. Seeded afterwards the device would reach
    `main` alone and that smoke would plan a create instead.

    Both steps are skipped once the branch exists: seeding then could no longer reach
    it, and re-forking would discard the destination the smokes have been writing to.
    The seed itself upserts on the device's unique name, so a reset environment that
    kept its data converges rather than duplicating.
    """
    from infrahub_sdk import InfrahubClientSync  # noqa: PLC0415 -- keep Invoke task imports lightweight

    client = InfrahubClientSync(address=address, config={"api_token": token})
    if SMOKE_BRANCH in client.branch.all():
        return
    client.create(
        kind=SMOKE_KIND,
        branch="main",
        data={"name": SHARED_DEVICE_NAME, "type": SHARED_DEVICE_SEED_TYPE},
    ).save(allow_upsert=True)
    client.branch.create(SMOKE_BRANCH, sync_with_git=False)


def load_smoke_dataset(context: Context, address: str, token: str) -> None:
    """Load the smoke schema, then the shared device and the smoke branch."""
    print(f" - [{NAMESPACE}] Seeding {address}, which writes:")
    print(f" - [{NAMESPACE}]   the schema in {SCHEMA_FILE}")
    print(f" - [{NAMESPACE}]   {SMOKE_KIND} {SHARED_DEVICE_NAME} on main, then the {SMOKE_BRANCH} branch")
    context.run(
        f"uv run infrahubctl schema load --wait {SCHEMA_CONVERGE_SECONDS} {shlex.quote(str(SCHEMA_FILE))}",
        env={"INFRAHUB_ADDRESS": address, "INFRAHUB_API_TOKEN": token},
    )
    ensure_smoke_branch(address, token)
