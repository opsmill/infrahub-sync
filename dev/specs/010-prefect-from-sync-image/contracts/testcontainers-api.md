# Contract: `infrahub_sync.testcontainers` (experimental)

Public Python surface of the new optional extra. It is experimental until a demo repository
adopts it (D-16); it may change without a deprecation period until then.

## Installation

```bash
pip install "infrahub-sync[testcontainers]"
```

The extra installs `infrahub-testcontainers` and `testcontainers`. Its `prefect-client` pin
names the same Prefect version as Sync's `prefect` pin (FR-024, research R4).

## `TestInfrahubSyncDocker`

```python
from infrahub_sync.testcontainers import TestInfrahubSyncDocker


class TestMyPackage(TestInfrahubSyncDocker):
    def test_plan_and_apply(self, sync_api_url, infrahub_port):
        ...
```

- Inherits `infrahub_testcontainers.helpers.TestInfrahubDocker`, so every Infrahub fixture
  (`infrahub_app`, `infrahub_port`, `default_branch`, `tmp_directory`, …) is available
  unchanged.
- Adds class-scoped fixtures. `infrahub_sync_compose_files` is a fixed name (spec 013 overrides
  it); the others are proposed and fixed in implementation:

| Fixture | Value |
|---------|-------|
| `infrahub_sync_image` | Image reference used for every Sync-image service |
| `infrahub_sync_compose_files` | Hook: a list of extra Compose file paths for the Sync project, passed with `-f` after the packaged root file, in order. Default `[]`. A subclass overrides it to add an override file. |
| `infrahub_sync_compose` | The started Compose project for the packaged root file plus `infrahub_sync_compose_files` |
| `sync_api_url` | URL of the Sync API on the loopback address |

The Infrahub project's own `infrahub_compose` fixture is unchanged and starts Infrahub only; a
Sync override file goes through `infrahub_sync_compose_files`, not through `infrahub_compose`.

```python
class TestWithProxy(TestInfrahubSyncDocker):
    @pytest.fixture(scope="class")
    def infrahub_sync_compose_files(self) -> list[Path]:
        return [Path(__file__).parent / "proxy.override.yml"]
```

## Settings

Each variable the root file reads is written to the generated `.env` as
`os.environ.get(name, test_default)`, as `InfrahubDockerCompose.create_env_file` does
(`infrahub/python_testcontainers/infrahub_testcontainers/container.py:162-197`). A test or CI job
sets a value by exporting it before the class starts; an unset variable gets a test default
(test-only credentials, loopback ports).

## Image selection

| Source | Result |
|--------|--------|
| Default | `registry.opsmill.io/opsmill/infrahub-sync:<installed infrahub-sync version>` |
| `INFRAHUB_SYNC_DOCKER_IMAGE` set | Replaces the repository part |
| `VERSION` set | Replaces the tag |

CI sets both variables to the image it built. These are the same variables the root file reads.

## Behaviour

- Starts Infrahub first (through the parent class), then the packaged root file and the files
  from `infrahub_sync_compose_files`, with the generated `.env` described under Settings.
- Connects `sync-worker` to the Infrahub project's network so the worker reaches
  `infrahub-server`.
- On teardown, stops both projects and removes their volumes; collects logs when a test failed,
  as the parent class does.
- Loading `infrahub_sync.testcontainers` without the extra raises `ImportError` naming the
  extra.
- The unit test tier disables the `infrahub-testcontainers` pytest plugin and never imports this
  module (FR-025).
