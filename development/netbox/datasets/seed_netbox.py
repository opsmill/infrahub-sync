"""Seed a fresh disposable NetBox with a deterministic S, M, or L benchmark tier."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Invoke executes this file by path; keep its package imports available in a base checkout.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from development.netbox.datasets.netbox_api import NetboxAPI, environment_credentials, seed_dataset, verify_counts
from development.netbox.datasets.tier_data import build_dataset


def main() -> None:
    """Seed using environment credentials; never include a token in argv or output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=("S", "M", "L"), default="S")
    parser.add_argument("--verify-only", action="store_true", help="Verify a pristine tier before dumping it")
    args = parser.parse_args()
    try:
        url, token = environment_credentials()
        with NetboxAPI(url, token) as api:
            if args.verify_only:
                verify_counts(api, args.tier)
                if any(tag["slug"] == "benchmark-change-started" for tag in api.all("extras/tags")):
                    msg = "restore the pristine tier before dumping it"
                    parser.exit(1, f"{msg}\n")
            else:
                seed_dataset(api, build_dataset(args.tier), args.tier)
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
