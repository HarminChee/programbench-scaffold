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
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    args = parser.parse_args()
    workspace = args.workspace_root.resolve()
    source = args.source_dir.resolve(strict=True)
    config = {
        "schema": "programbench_v4_campaign_v1",
        "campaign_id": "v4-gofumpt-controlled-pilot",
        "mode": "pilot",
        "output_root": str(args.output_root.resolve()),
        "repo_workers": 1,
        "generation_workers": 1,
        "heartbeat_seconds": 10,
        "stage_timeout_seconds": 1800,
        "stale_lock_seconds": 90,
        "marginal_policy": {
            "saturation_windows": 2,
            "minimum_promoted_observations": 3,
            "minimum_primary_gain_pp": 0.25,
            "minimum_novelty": 1,
            "repo_wall_budget_seconds": 7200,
            "raw_candidate_fuse": 160,
            "retained_suite_fuse": 64,
            "adaptive_retained_fuse": True,
            "retained_suite_hard_fuse": 128,
            "retained_fuse_headroom_ratio": 0.20,
            "retained_fuse_minimum_growth": 8,
            "pilot_iteration_fuse": 6
        },
        "tranche_policy": {
            "initial_size": 24,
            "minimum_size": 12,
            "maximum_size": 32,
            "high_yield_ratio": 0.25,
            "low_yield_ratio": 0.05
        },
        "repositories": [
            {
                "instance_id": "mvdan__gofumpt.5dca7d8",
                "language": "go",
                "commit": "5dca7d819315c5c6338d290ad2e7847f07438693",
                "source_dir": str(source),
                "source_tree_sha256": source_tree_sha256(source),
                "runtime_image_id": args.image_id,
                "runtime_image_reference": "programbench/v3-external-runtime:ubuntu22-local",
                "stage_adapter": [
                    sys.executable,
                    str(workspace / "v4/adapters/gofumpt_pilot_adapter.py")
                ],
                "scope_files": [
                    str(workspace / "v4/adapters/gofumpt_pilot_adapter.py"),
                    str(workspace / "v4/programbench_v4/witnesses.py"),
                    str(workspace / "v4/tools/build_agent_context.py"),
                    str(workspace / "tools/programbench_agent_provider.py"),
                    str(workspace / "setup/invoke_agent_maestro_anthropic.ps1"),
                    str(workspace / "tools/programbench_generate_cli_oracle_bundle.py"),
                    str(workspace / "tools/programbench_run_generated_oracle_quality_gates.py")
                ],
                "go_toolchain_root": "/usr/local/go1.26.5",
                "go_mod_cache": "/home/programbench/go/pkg/mod",
                "model": args.model,
                "pilot_raw_fuse": 160,
                "pilot_retained_fuse": 64,
                "family_quota": 8
            }
        ]
    }
    atomic_write_json(args.config.resolve(), config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
