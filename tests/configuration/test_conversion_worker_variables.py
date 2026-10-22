"""``COMPOSE_WORKER_CREDENTIAL_VARIABLES`` matches the ``sync-worker`` service of ``docker-compose.yml``.

When the Compose file passes another credential variable to the Sync worker, this test fails
until the constant is updated (research R5).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from infrahub_sync.configuration.conversion import COMPOSE_WORKER_CREDENTIAL_VARIABLES
from infrahub_sync.configuration.credentials import ENV_CREDENTIAL_PREFIX

_COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def test_constant_matches_the_sync_worker_credential_variables() -> None:
    compose = yaml.safe_load(_COMPOSE_FILE.read_text(encoding="utf-8"))
    environment = compose["services"]["sync-worker"]["environment"]
    assert isinstance(environment, dict), "the sync-worker environment is expected as a mapping"
    keys = tuple(key for key in environment if key.startswith(ENV_CREDENTIAL_PREFIX))
    assert keys == COMPOSE_WORKER_CREDENTIAL_VARIABLES
