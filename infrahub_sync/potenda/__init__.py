from __future__ import annotations

from typing import TYPE_CHECKING

from diffsync.enum import DiffSyncFlags
from diffsync.logging import enable_console_logging
from tqdm import tqdm

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
        show_progress: bool | None = False,
    ):
        self.top_level = top_level

        self.config = config

        self.source = source
        self.destination = destination

        self.source.top_level = top_level
        self.destination.top_level = top_level

        self.partition = partition
        self.progress_bar = None
        self.show_progress = show_progress
        enable_console_logging(verbosity=1)
        # Combine DiffSyncFlags from the configuration
        self.flags = DiffSyncFlags.NONE
        for flag in self.config.diffsync_flags:
            self.flags |= flag

        # Fallback to `SKIP_UNMATCHED_DST` if nothing is define
        if self.flags == DiffSyncFlags.NONE:
            self.flags = DiffSyncFlags.SKIP_UNMATCHED_DST

    def _print_callback(self, stage: str, elements_processed: int, total_models: int):
        """Callback for DiffSync using tqdm"""
        if self.show_progress:
            if self.progress_bar is None:
                self.progress_bar = tqdm(total=total_models, desc=stage, unit="models")

            self.progress_bar.n = elements_processed
            self.progress_bar.refresh()

            if elements_processed == total_models:
                self.progress_bar.close()
                self.progress_bar = None

    def source_load(self):
        try:
            print(f"Load: Importing data from {self.source}")
            self.source.load()
        except Exception as exc:
            msg = f"An error occurred while loading {self.source}: {exc!s}"
            raise ValueError(msg) from exc

    def destination_load(self):
        try:
            print(f"Load: Importing data from {self.destination}")
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
        print(f"Diff: Comparing data from {self.source} to {self.destination}")
        self.progress_bar = None
        return self.destination.diff_from(self.source, flags=self.flags, callback=self._print_callback)

    def sync(self, diff: Diff | None = None):
        print(f"Sync: Importing data from {self.source} to {self.destination} based on Diff")
        self.progress_bar = None
        return self.destination.sync_from(self.source, diff=diff, flags=self.flags, callback=self._print_callback)
