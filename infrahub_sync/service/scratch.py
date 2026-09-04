"""One service stage's private filesystem scratch.

A worker shares no Sync filesystem with the API or with another flow run, so a stage
cannot be handed a directory and cannot leave one behind for its successor. What it
needs on disk it creates here, under a root only it can name, and the root is removed
when the stage ends however it ends.

The root comes from the process temporary directory rather than from a configured
location or the working directory: those are exactly the two places a second process
could also derive, which is what made the shared cache a shared filesystem.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp
from typing import TYPE_CHECKING

from infrahub_sync.cache.paths import run_dir

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["StageScratch", "stage_scratch"]

_PREFIX = "infrahub-sync-stage-"


@dataclass(frozen=True, slots=True)
class StageScratch:
    """The private root one stage works in, and the run directories inside it."""

    stage: str
    root: Path

    def run_directory(self, sync_name: str, run_id: str) -> Path:
        """Return this run's directory inside the private root.

        Derived through the cache layout's own translation, so the traversal guard that
        protects a configured root protects this one too: neither name can escape it.
        """
        return run_dir(sync_name, run_id, base_directory=self.root)


@contextmanager
def stage_scratch(stage: str) -> Iterator[StageScratch]:
    """Create a new empty private root for `stage` and remove it afterwards.

    `mkdtemp` is what makes the root both unique and private: it creates the directory
    itself, owner-only, and never returns a name that already existed. Removal is
    unconditional and tolerates a partially written tree -- the stage's durable evidence
    is in product storage by the time this returns, and anything still here is scratch.
    """
    root = Path(mkdtemp(prefix=f"{_PREFIX}{stage}-"))
    try:
        yield StageScratch(stage=stage, root=root)
    finally:
        rmtree(root, ignore_errors=True)
