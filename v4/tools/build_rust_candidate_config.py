#!/usr/bin/env python3
"""Build a launch-ready V4 campaign from a pinned Rust candidate manifest.

This intentionally stops at configuration.  Running it never starts a
controller, so a prepared cohort cannot race an active campaign lock.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from v4.programbench_v4.io import atomic_write_json
from v4.programbench_v4.provenance import source_tree_sha256
from v4.tools.build_external20_config import sha256_file
from v4.tools.build_rust20_config import cargo_bin_scope, scaled


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--source-summary", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--generation-workers", type=int, default=8)
    parser.add_argument("--model", default="gpt-5.6-sol")
    args = parser.parse_args()
    if not 1 <= args.workers <= 10:
        raise SystemExit("workers must be in 1..10")
    if not 1 <= args.generation_workers <= min(8, args.workers):
        raise SystemExit("generation-workers must be in 1..min(8, workers)")

    manifest = json.loads(args.candidate_manifest.read_text(encoding="utf-8"))
    summary = json.loads(args.source_summary.read_text(encoding="utf-8"))
    candidates = manifest.get("repositories") or []
    snapshots = summary.get("repositories") or []
    if not candidates or len(candidates) != len(snapshots):
        raise RuntimeError("candidate manifest and source summary counts differ")
    if any(row.get("state") != "completed" for row in snapshots):
        raise RuntimeError("all source snapshots must be completed")
    by_id = {str(row["instance_id"]): row for row in snapshots}
    if set(by_id) != {str(row["instance_id"]) for row in candidates}:
        raise RuntimeError("candidate manifest and source summary identities differ")

    adapter = ROOT / "v4/adapters/gofumpt_pilot_adapter.py"
    scope_files = [
        adapter,
        ROOT / "v4/programbench_v4/witnesses.py",
        ROOT / "v4/programbench_v4/dependencies.py",
        ROOT / "v4/programbench_v4/policy.py",
        ROOT / "v4/programbench_v4/scheduler.py",
        ROOT / "v4/programbench_v4/resource_admission.py",
        ROOT / "v4/programbench_v4/rust_afl_qemu.py",
        ROOT / "v4/programbench_v4/native_afl_qemu.py",
        ROOT / "v3/tools/run_afl_qemu_suite.py",
        ROOT / "v3/tools/afl_qemu_capture_wrapper.sh",
        ROOT / "v4/tools/build_agent_context.py",
        ROOT / "tools/programbench_agent_provider.py",
        ROOT / "tools/programbench_generate_cli_oracle_bundle.py",
        ROOT / "tools/programbench_run_generated_oracle_quality_gates.py",
    ]
    toolchain = Path("/home/programbench/.rustup/toolchains/stable-x86_64-unknown-linux-gnu").resolve(strict=True)
    cargo = toolchain / "bin/cargo"
    llvm_cov = Path("/home/programbench/.cargo/bin/cargo-llvm-cov").resolve(strict=True)
    repositories = []
    for candidate in candidates:
        instance = str(candidate["instance_id"])
        row = by_id[instance]
        source = Path(row["source_dir"]).resolve(strict=True)
        package, binary = cargo_bin_scope(source, str(candidate["binary"]), cargo)
        complexity = 1.0 if candidate.get("difficulty") == "easy" else 1.25
        scale = scaled(instance, source, complexity)
        repositories.append({
            "instance_id": instance,
            "repository": candidate["repository"],
            "language": "rust",
            "binary_name": binary,
            "rust_build_args": f"--release --locked -p {package} --bin {binary}",
            "rust_binary_relpath": f"release/{binary}",
            "behavior_themes": candidate["themes"],
            **scale,
            "commit": row["commit"],
            "source_dir": str(source),
            "source_tree_sha256": source_tree_sha256(source),
            "runtime_image_id": args.image_id,
            "runtime_image_reference": "programbench/v3-external-runtime:ubuntu22-local",
            "stage_adapter": [sys.executable, str(adapter)],
            "scope_files": [str(path) for path in scope_files],
            "model": args.model,
            "rust_toolchain_root": str(toolchain),
            "cargo_home": "/home/programbench/.cargo",
            "rust_libclang_root": "/usr/lib/llvm-14/lib",
            "rust_native_test_args": "--workspace --locked --no-fail-fast",
            "rust_llvm_cov_args": "--workspace --locked",
            "rust_target_triple": "x86_64-unknown-linux-gnu",
            "rust_cargo_llvm_cov_binary": str(llvm_cov),
            "rust_cargo_llvm_cov_sha256": sha256_file(llvm_cov),
            "pilot_raw_fuse": max(1500, int(scale["target_retained_cases_max"])),
            "pilot_retained_fuse": int(scale["target_retained_cases_max"]),
            "family_quota": 16,
            "suite_label": "v4_rust_candidate_cohort",
            "rust_afl_qemu_enabled": True,
            "afl_qemu_enabled": True,
            "rust_afl_root": "/home/programbench/research/tools/afl-src/aflplusplus-4.00c",
            "afl_qemu_root": "/home/programbench/research/tools/afl-src/aflplusplus-4.00c",
            "afl_measurement_period": 3,
            "afl_path_auxiliary_stop": True,
            "rust_afl_timeout_seconds": 3600,
        })

    atomic_write_json(args.config.resolve(), {
        "schema": "programbench_v4_campaign_v1",
        "campaign_id": args.campaign_id,
        "mode": "production",
        "output_root": str(args.output_root.resolve()),
        "repo_workers": args.workers,
        "generation_workers": args.generation_workers,
        "resource_admission": {"cpu_capacity": 10, "memory_capacity_mib": 22528},
        "heartbeat_seconds": 15,
        "stage_timeout_seconds": 7200,
        "stale_lock_seconds": 120,
        "marginal_policy": {
            "saturation_windows": 2,
            "minimum_promoted_observations": 4,
            "minimum_primary_gain_pp": 0.25,
            "minimum_novelty": 1,
            "minimum_strong_novelty": 1,
            "repo_wall_budget_seconds": 172800,
            "raw_candidate_fuse": None,
            "adaptive_raw_fuse": False,
            "raw_candidate_hard_fuse": None,
            "retained_suite_fuse": 1500,
            "adaptive_retained_fuse": True,
            "retained_suite_hard_fuse": 3000,
            "retained_fuse_headroom_ratio": 0.5,
            "retained_fuse_minimum_growth": 250,
            "require_auxiliary_path_saturation": True,
            "auxiliary_path_saturation_windows": 2,
            "auxiliary_path_minimum_relative_gain": 0.01,
        },
        "tranche_policy": {
            "initial_size": 96,
            "minimum_size": 32,
            "maximum_size": 192,
            "high_yield_ratio": 0.25,
            "low_yield_ratio": 0.05,
            "growth_factor": 1.5,
            "shrink_factor": 0.5,
            "breadth_bootstrap_iterations": 2,
            "breadth_bootstrap_views": [
                "source_entrypoints", "docs_native_behavior", "successful_workflow",
                "boundary_and_error", "fixture_and_unicode", "protocol_and_format",
                "state_and_interaction", "reachability_repair",
            ],
            "reservoir_rerank_period": 3,
        },
        "repositories": repositories,
    })
    print(json.dumps({"repositories": len(repositories), "config": str(args.config.resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
