#!/usr/bin/env bash
# Single-scenario rerun for incremental on a freshly-rebuilt Infrahub,
# with sync output streamed to a per-cell log so failures don't get
# swallowed by /dev/null.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INFRAHUB_REPO="$(cd "$REPO_ROOT/../infrahub" && pwd)"
CFG_DIR=/tmp/bench-nautobot-cfg
CSV="$REPO_ROOT/bench-incremental.csv"

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

shutil.copytree(src / "nautobot", out / "nautobot")
shutil.copytree(src / "infrahub", out / "infrahub")
PY
}

run_sync() {
    local FLAGS="$1"
    local LABEL="$2"
    local LOG="$REPO_ROOT/bench-incremental-$LABEL.log"
    cd "$REPO_ROOT"
    local START
    START=$(python -c "import time; print(time.monotonic())")
    # Don't `set -e` propagate from sync; we want to record exit status
    set +e
    # shellcheck disable=SC2086
    uv run infrahub-sync sync \
        --name from-nautobot-v2 \
        --directory "$CFG_DIR" \
        --no-diff \
        --no-show-progress \
        --continue-on-error \
        $FLAGS \
        >"$LOG" 2>&1
    local EXIT=$?
    set -e
    local END
    END=$(python -c "import time; print(time.monotonic())")
    local ELAPSED
    ELAPSED=$(python -c "print(f'{$END - $START:.2f}')")
    echo "$LABEL,$ELAPSED,$EXIT" >> "$CSV"
    echo ">>>   $LABEL took ${ELAPSED}s (exit=$EXIT)"
    if [ "$EXIT" -ne 0 ]; then
        echo ">>>   FAILED. Tail of $LOG:"
        tail -10 "$LOG"
    fi
}

echo "scenario,seconds,exit_code" > "$CSV"

echo "=== Incremental: cold (force --full-extract) + warm (cursor-driven) ==="
rebuild_infrahub
build_config
rm -rf "$REPO_ROOT/.infrahub-sync-cache/from-nautobot-v2"
run_sync "--parallel --concurrent-load --full-extract" "incremental-cold"
run_sync "--parallel --concurrent-load --no-full-extract" "incremental-warm"

echo ""
echo "=== Results ==="
cat "$CSV"
