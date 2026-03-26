from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from diffsync.enum import DiffSyncFlags
from tqdm import tqdm

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from diffsync import Adapter
    from diffsync.diff import Diff

    from infrahub_sync import SyncInstance


class Potenda:
    def __init__(
        self,
        source: Adapter,
        destination: Adapter,
        config: SyncInstance,
        top_level: list[str],
        partition=None,
        show_progress: bool | None = None,
        verbosity: int | None = None,
    ):
        self.top_level = top_level

        self.config = config

        self.source = source
        self.destination = destination

        self.source.top_level = top_level
        self.destination.top_level = top_level

        self.partition = partition
        self.progress_bar = None
        self.show_progress = show_progress if show_progress is not None else sys.stderr.isatty()

        if verbosity is not None:
            logging.getLogger("diffsync").setLevel(verbosity)

        # Combine DiffSyncFlags from the configuration
        self.flags = DiffSyncFlags.NONE
        for flag in self.config.diffsync_flags:
            self.flags |= flag

        # Fallback to `SKIP_UNMATCHED_DST` if nothing is define
        if self.flags == DiffSyncFlags.NONE:
            self.flags = DiffSyncFlags.SKIP_UNMATCHED_DST

    def _print_callback(self, stage: str, elements_processed: int, total_models: int):
        """Callback for DiffSync progress tracking."""
        if self.show_progress:
            if self.progress_bar is None:
                self.progress_bar = tqdm(total=total_models, desc=stage, unit="models")

            self.progress_bar.n = elements_processed
            self.progress_bar.refresh()

            if elements_processed == total_models:
                self.progress_bar.close()
                self.progress_bar = None
        elif elements_processed == total_models:
            logger.info("%s: %d/%d models processed", stage, elements_processed, total_models)

    def source_load(self):
        try:
            logger.info("Load: Importing data from %s", self.source)
            self.source.load()
        except Exception as exc:
            msg = f"An error occurred while loading {self.source}: {exc!s}"
            raise ValueError(msg) from exc

    def destination_load(self):
        try:
            logger.info("Load: Importing data from %s", self.destination)
            self.destination.load()
        except Exception as exc:
            msg = f"An error occurred while loading {self.destination}: {exc!s}"
            raise ValueError(msg) from exc

    def load(self):
        try:
            self.source_load()
            self.destination_load()
        except Exception as exc:
            msg = f"An error occurred while loading the sync: {exc!s}"
            raise ValueError(msg) from exc

    def diff(self) -> Diff:
        logger.info("Diff: Comparing data from %s to %s", self.source, self.destination)
        self.progress_bar = None
        return self.destination.diff_from(self.source, flags=self.flags, callback=self._print_callback)

    def sync(self, diff: Diff | None = None):
        logger.info("Sync: Importing data from %s to %s based on Diff", self.source, self.destination)
        self.progress_bar = None
        return self.destination.sync_from(self.source, diff=diff, flags=self.flags, callback=self._print_callback)
