#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from v4.programbench_v4.io import atomic_write_json
from v4.programbench_v4.provenance import source_tree_sha256


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    args = parser.parse_args()
    workspace = args.workspace_root.resolve()
    adapter = workspace / "v4/tools/smoke_stage_adapter.py"
    repositories = []
    for index, (name, language) in enumerate((("smoke-go", "go"), ("smoke-rust", "rust")), 1):
        source = workspace / "v4/fixtures" / name
        repositories.append(
            {
                "instance_id": name,
                "language": language,
                "commit": f"{index:x}" * 40,
                "source_dir": str(source),
                "source_tree_sha256": source_tree_sha256(source),
                "runtime_image_id": args.image_id,
                "stage_adapter": [sys.executable, str(adapter)],
                "scope_files": [str(adapter)],
                "smoke_profile": {"delay_seconds": 0.02},
            }
        )
    config = {
        "schema": "programbench_v4_campaign_v1",
        "campaign_id": "v4-controlled-smoke",
        "mode": "smoke",
        "output_root": str(args.output_root.resolve()),
        "repo_workers": 2,
        "generation_workers": 2,
        "heartbeat_seconds": 0.1,
        "stage_timeout_seconds": 15,
        "stale_lock_seconds": 30,
        "marginal_policy": {
            "saturation_windows": 2,
            "minimum_promoted_observations": 3,
            "minimum_primary_gain_pp": 0.35,
            "minimum_novelty": 1,
            "repo_wall_budget_seconds": 60,
            "raw_candidate_fuse": 1000,
            "retained_suite_fuse": 100,
            "pilot_iteration_fuse": 6
        },
        "tranche_policy": {
            "initial_size": 16,
            "minimum_size": 8,
            "maximum_size": 32,
            "high_yield_ratio": 0.25,
            "low_yield_ratio": 0.05
        },
        "repositories": repositories,
    }
    atomic_write_json(args.config.resolve(), config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
