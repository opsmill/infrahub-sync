"""The tutorial's record-against-service comparison, executed rather than read.

Its reader is deciding whether to load an image and deploy it. A comparison that
prints `WRONG` and then exits zero tells them nothing they will act on, and a
comparison that only checks the entries it happens to find accepts a record
describing five groups, or eight, as readily as six.

So these cases run the document's own block against crafted records and
inventories, and assert the status a reader is told to read. The block is the
same text the guide shows, extracted from it rather than restated here — a copy
would keep passing after the document changed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # noqa: S404 -- these cases drive the document's own shell
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDE = REPO_ROOT / "dev" / "guides" / "qualifying-an-internal-candidate.md"

SHELL = shutil.which("bash") or "bash"
JQ = shutil.which("jq")

# What identifies the comparison among the document's shell blocks.
BLOCK_MARKERS = ("$RECORDED", "$INVENTORY", "recorded group(s) do not match")

RECORDED_GROUPS = (
    "infrahub-sync-candidate-image",
    "infrahub-sync-candidate-identity",
    "infrahub-sync-candidate-distributions",
    "infrahub-sync-candidate-bundle",
    "infrahub-sync-candidate-sboms",
    "infrahub-sync-qualification-kit",
)
# The seventh group. It is in the service inventory and must never be in the
# record, because the record is written before its own upload exists.
SELF_EXCLUDED = "infrahub-sync-qualification-record"

pytestmark = pytest.mark.skipif(JQ is None, reason="jq is not installed; the document's own block needs it")


def comparison_block() -> str:
    """Return the guide's record-against-service comparison, as the guide shows it."""
    blocks: list[str] = []
    current: list[str] | None = None
    for line in GUIDE.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            if current is None:
                current = [] if line.startswith("```bash") else None
            else:
                blocks.append("\n".join(current))
                current = None
        elif current is not None:
            current.append(line)

    found = [block for block in blocks if all(marker in block for marker in BLOCK_MARKERS)]
    assert len(found) == 1, f"{len(found)} blocks of {GUIDE.name} compare the record against the service"
    return found[0]


def digest(seed: str) -> str:
    return seed * 64


def service_inventory(overrides: dict[str, tuple[str, str]] | None = None) -> str:
    """Render the seven-row service inventory the earlier step wrote."""
    rows = []
    for index, name in enumerate((*RECORDED_GROUPS, SELF_EXCLUDED)):
        identifier, held = (overrides or {}).get(name, (str(1000 + index), digest(str(index))))
        rows.append(f"{name}\t{identifier}\tsha256:{held}\t2026-09-09T03:18:51Z\t2026-10-09T03:18:51Z\tfalse")
    return "".join(f"{row}\n" for row in rows)


def record(entries: dict[str, tuple[str, str]]) -> str:
    """Render a qualification record whose artifact map holds exactly these entries."""
    return json.dumps({"artifacts": {name: {"id": value[0], "digest": value[1]} for name, value in entries.items()}})


def expected_entries() -> dict[str, tuple[str, str]]:
    return {name: (str(1000 + index), digest(str(index))) for index, name in enumerate(RECORDED_GROUPS)}


def compare(tmp_path: Path, *, recorded: str, inventory: str) -> subprocess.CompletedProcess[str]:
    """Run the document's own block over one record and one inventory."""
    (tmp_path / "record").mkdir(exist_ok=True)
    (tmp_path / "record" / "qualification.json").write_text(recorded, encoding="utf-8")
    inventory_path = tmp_path / "inventory.tsv"
    inventory_path.write_text(inventory, encoding="utf-8")
    return subprocess.run(  # noqa: S603 -- the document's own script, fixed argv
        [SHELL, "-c", comparison_block()],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "INVENTORY": str(inventory_path),
            "RECORDED": str(tmp_path / "recorded.tsv"),
        },
    )


def status(finished: subprocess.CompletedProcess[str]) -> int:
    """Return the status the document tells its reader to read.

    The block ends in a subshell, so the exit code of the whole snippet is that
    of the `echo` after it. What a reader acts on is the number that `echo`
    prints, which is what this reads back.
    """
    printed = [line for line in finished.stdout.splitlines() if line.startswith("comparison status:")]
    assert printed, finished.stdout + finished.stderr
    return int(printed[-1].split(":")[1])


def test_it_accepts_exactly_the_six_entries_that_match(tmp_path: Path) -> None:
    """The control. Without it every refusal below is satisfied by a block that always fails."""
    finished = compare(tmp_path, recorded=record(expected_entries()), inventory=service_inventory())

    assert status(finished) == 0, finished.stdout + finished.stderr
    assert "all six recorded groups match" in finished.stdout


def test_it_refuses_a_mismatched_digest(tmp_path: Path) -> None:
    """A record naming a digest the service does not hold describes other bytes."""
    entries = expected_entries()
    entries["infrahub-sync-candidate-bundle"] = (entries["infrahub-sync-candidate-bundle"][0], digest("f"))
    finished = compare(tmp_path, recorded=record(entries), inventory=service_inventory())

    assert status(finished) != 0, finished.stdout + finished.stderr
    assert "infrahub-sync-candidate-bundle" in finished.stderr


def test_it_refuses_a_mismatched_identifier(tmp_path: Path) -> None:
    """An identifier is what a later approval fetches by, so a wrong one fetches something else."""
    entries = expected_entries()
    entries["infrahub-sync-candidate-image"] = ("999999", entries["infrahub-sync-candidate-image"][1])
    finished = compare(tmp_path, recorded=record(entries), inventory=service_inventory())

    assert status(finished) != 0, finished.stdout + finished.stderr
    assert "infrahub-sync-candidate-image" in finished.stderr


def test_it_refuses_a_record_missing_one_group(tmp_path: Path) -> None:
    """Five entries mean a group was retained and never recorded."""
    entries = expected_entries()
    del entries["infrahub-sync-candidate-sboms"]
    finished = compare(tmp_path, recorded=record(entries), inventory=service_inventory())

    assert status(finished) != 0, finished.stdout + finished.stderr
    assert "wrong set of groups" in finished.stderr


def test_it_refuses_a_record_that_describes_its_own_upload(tmp_path: Path) -> None:
    """The record cannot carry the digest of the upload containing it.

    An entry for itself is therefore not an extra detail; it is a value nothing
    could have known at the time the document was written.
    """
    entries = expected_entries()
    entries[SELF_EXCLUDED] = ("1006", digest("6"))
    finished = compare(tmp_path, recorded=record(entries), inventory=service_inventory())

    assert status(finished) != 0, finished.stdout + finished.stderr
    assert "wrong set of groups" in finished.stderr


def test_it_refuses_a_recorded_group_the_service_does_not_hold(tmp_path: Path) -> None:
    """A record can name six groups correctly and still name one that is not there."""
    inventory = "\n".join(
        line for line in service_inventory().splitlines() if not line.startswith("infrahub-sync-candidate-identity")
    )
    finished = compare(tmp_path, recorded=record(expected_entries()), inventory=f"{inventory}\n")

    assert status(finished) != 0, finished.stdout + finished.stderr
    assert "ABSENT" in finished.stderr


def test_it_refuses_an_empty_record(tmp_path: Path) -> None:
    """A record with no artifact map at all has to fail rather than pass vacuously."""
    finished = compare(tmp_path, recorded=json.dumps({"artifacts": {}}), inventory=service_inventory())

    assert status(finished) != 0, finished.stdout + finished.stderr
