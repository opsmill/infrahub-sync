#!/usr/bin/env bash
# Clean-Infrahub comparison: rebuild between scenarios so each cold+warm
# pair sees a freshly-rebuilt destination. Drops InfraInterfaceL2L3 from
# the nautobot example mapping.
#
# Outputs: bench-clean.csv with one row per scenario_phase.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INFRAHUB_REPO="$(cd "$REPO_ROOT/../infrahub" && pwd)"
CFG_DIR=/tmp/bench-nautobot-cfg
CSV="$REPO_ROOT/bench-clean.csv"

rebuild_infrahub() {
    echo ">>> Rebuilding Infrahub..."
    (cd "$INFRAHUB_REPO" && uv run invoke dev.destroy dev.start) >/dev/null 2>&1
    echo ">>> Loading nautobot-v2 schema..."
    (cd "$INFRAHUB_REPO" && uv run infrahubctl schema load models/examples/nautobot/nautobot-v2.yml) >/dev/null 2>&1
}

build_config() {
    cd "$REPO_ROOT"
    uv run python - <<'PY'
import shutil
from pathlib import Path
import yaml

src = Path("examples/nautobot-v2_to_infrahub")
out = Path("/tmp/bench-nautobot-cfg")
shutil.rmtree(out, ignore_errors=True)
out.mkdir()

data = yaml.safe_load((src / "config.yml").read_text(encoding="utf-8"))
data["schema_mapping"] = [sm for sm in data["schema_mapping"] if sm["name"] != "InfraInterfaceL2L3"]
data.pop("order", None)
(out / "config.yml").write_text(yaml.dump(data, sort_keys=False), encoding="utf-8")

# The adapters live under <config>/<adapter_name>/sync_adapter.py
shutil.copytree(src / "nautobot", out / "nautobot")
shutil.copytree(src / "infrahub", out / "infrahub")
PY
}

run_sync() {
    local FLAGS="$1"
    local LABEL="$2"
    cd "$REPO_ROOT"
    local START
    START=$(python -c "import time; print(time.monotonic())")
    # shellcheck disable=SC2086
    uv run infrahub-sync sync \
        --name from-nautobot-v2 \
        --directory "$CFG_DIR" \
        --no-diff \
        --no-show-progress \
        --continue-on-error \
        $FLAGS \
        >/dev/null 2>&1
    local END
    END=$(python -c "import time; print(time.monotonic())")
    local ELAPSED
    ELAPSED=$(python -c "print(f'{$END - $START:.2f}')")
    echo "$LABEL,$ELAPSED" >> "$CSV"
    echo ">>>   $LABEL took ${ELAPSED}s"
}

echo "scenario,seconds" > "$CSV"

# ----- Scenario 1: baseline (no parallel, no concurrent, no incremental) -----
echo "=== Scenario 1: baseline (serial, sequential, no incremental) ==="
rebuild_infrahub
build_config
rm -rf "$REPO_ROOT/.infrahub-sync-cache/from-nautobot-v2"
run_sync "--no-parallel --no-concurrent-load --full-extract" "baseline-cold"
run_sync "--no-parallel --no-concurrent-load --full-extract" "baseline-warm"

# ----- Scenario 2: parallel+concurrent (no incremental) -----
echo "=== Scenario 2: parallel+concurrent (no incremental) ==="
rebuild_infrahub
build_config
rm -rf "$REPO_ROOT/.infrahub-sync-cache/from-nautobot-v2"
run_sync "--parallel --concurrent-load --full-extract" "parconc-cold"
run_sync "--parallel --concurrent-load --full-extract" "parconc-warm"

# ----- Scenario 3: incremental (parallel+concurrent, cursor-driven on warm) -----
echo "=== Scenario 3: incremental (parallel+concurrent, cursor-driven warm) ==="
rebuild_infrahub
build_config
rm -rf "$REPO_ROOT/.infrahub-sync-cache/from-nautobot-v2"
run_sync "--parallel --concurrent-load --full-extract" "incremental-cold"
run_sync "--parallel --concurrent-load --no-full-extract" "incremental-warm"

echo ""
echo "=== Results ==="
cat "$CSV"
