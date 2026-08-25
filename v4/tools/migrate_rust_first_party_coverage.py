from __future__ import annotations

import argparse
import json
from pathlib import Path

from v4.programbench_v4.io import read_json
from v4.programbench_v4.rust_coverage_migration import (
    apply_rust_first_party_migration,
    plan_rust_first_party_migration,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit/apply one-time Rust first-party coverage migration"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--repo", action="append", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    config = read_json(args.config.resolve())
    root = args.campaign_root.resolve()
    results = []
    for instance_id in args.repo:
        plan = plan_rust_first_party_migration(
            campaign_config=config, campaign_root=root, instance_id=instance_id
        )
        results.append(
            apply_rust_first_party_migration(plan, campaign_root=root)
            if args.apply
            else plan
        )
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
