"""End-to-end test class that starts Infrahub and Sync in Docker (experimental).

Install the extra on Python 3.11 or later: `pip install "infrahub-sync[testcontainers]"`.

```python
from infrahub_sync.testcontainers import TestInfrahubSyncDocker


class TestMyPackage(TestInfrahubSyncDocker):
    def test_plan_and_apply(self, sync_api_url, infrahub_port): ...
```
"""

from __future__ import annotations

try:
    import infrahub_testcontainers  # noqa: F401  # pylint: disable=unused-import
except ImportError as exc:
    msg = (
        "infrahub_sync.testcontainers needs the `testcontainers` extra, on Python 3.11 or later: "
        'pip install "infrahub-sync[testcontainers]"'
    )
    raise ImportError(msg) from exc

from infrahub_sync.testcontainers.helpers import TestInfrahubSyncDocker

__all__ = ["TestInfrahubSyncDocker"]
