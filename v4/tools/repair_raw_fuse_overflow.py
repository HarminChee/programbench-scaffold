from __future__ import annotations

import argparse
import json
from pathlib import Path

from v4.programbench_v4.io import read_json
from v4.programbench_v4.raw_overflow_repair import (
    apply_raw_overflow_repair,
    plan_raw_overflow_repair,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit or apply a strict one-time V4 raw hard-fuse overflow repair"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--repo", action="append", required=True)
    parser.add_argument("--apply", action="store_true", help="default is audit-only dry-run")
    args = parser.parse_args()
    config = read_json(args.config.resolve())
    root = args.campaign_root.resolve()
    results = []
    for instance_id in args.repo:
        plan = plan_raw_overflow_repair(
            campaign_config=config,
            campaign_root=root,
            instance_id=instance_id,
        )
        results.append(
            apply_raw_overflow_repair(plan, campaign_root=root)
            if args.apply
            else plan
        )
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
