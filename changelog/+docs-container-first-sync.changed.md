The NetBox demo tutorial and the local development stack's first sync now start Infrahub from
Infrahub's own Compose file and run the Sync service in containers with `uv run invoke start`,
instead of host processes or the preview stack. The tutorial and the Run a sync guide now say
where to find why a run failed and how to finish an interrupted apply. The developer pages moved
from `/develop/` to `/development/`, matching Infrahub's docs, and the old addresses redirect.
