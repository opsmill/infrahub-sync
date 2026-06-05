from pathlib import Path

from infrahub_sync.cache.sidecars import RunCounterFile


def test_counter_starts_at_zero(tmp_path: Path) -> None:
    counter = RunCounterFile.load_or_default(tmp_path / "run-counter.json")
    assert counter.runs_since_full == 0


def test_increment_and_save_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "run-counter.json"
    counter = RunCounterFile.load_or_default(path)
    counter.runs_since_full = 4
    counter.save()
    assert RunCounterFile.load_or_default(path).runs_since_full == 4
