#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from v4.programbench_v4.io import atomic_write_json
from v4.programbench_v4.provenance import source_tree_sha256
from v4.tools.build_external20_config import (
    first_party_source_metrics,
    scale_target_range,
    sha256_file,
)


CATALOG: dict[str, dict[str, Any]] = {
    "wfxr__csview.8ac4de0": {
        "repository": "wfxr/csview",
        "binary_name": "csview",
        "behavior_themes": [
            "CSV and delimiter-aware rendering from stdin and files",
            "width, alignment, style, header, and Unicode combinations",
            "malformed records, empty data, quoting, diagnostics, and boundary fields",
        ],
    },
    "sirwart__ripsecrets.34c9e03": {
        "repository": "sirwart/ripsecrets",
        "binary_name": "ripsecrets",
        "behavior_themes": [
            "secret-pattern scanning over deterministic text and source fixtures",
            "recursive paths, globs, hidden files, exclusions, and output modes",
            "clean inputs, mixed findings, malformed paths, Unicode, and diagnostics",
        ],
    },
    "wintermute-cell__ngrrram.8ea13c3": {
        "repository": "wintermute-cell/ngrrram",
        "binary_name": "ngrrram",
        "behavior_themes": [
            "n-gram generation from deterministic stdin and file content",
            "length, separator, normalization, and output-format combinations",
            "empty, Unicode, repeated, short, malformed, and boundary inputs",
        ],
    },
    "chmln__sd.87d1ba5": {
        "repository": "chmln/sd",
        "binary_name": "sd",
        "behavior_themes": [
            "literal and regular-expression replacement over stdin and files",
            "capture groups, flags, separators, Unicode, and multi-line data",
            "invalid expressions, empty matches, boundary sizes, and diagnostics",
        ],
    },
    "sharkdp__hexyl.2e26437": {
        "repository": "sharkdp/hexyl",
        "binary_name": "hexyl",
        "behavior_themes": [
            "hex rendering for deterministic binary files and stdin",
            "length, skip, block size, color-disabled, plain, and border modes",
            "empty, sparse, Unicode bytes, invalid offsets, and missing paths",
        ],
    },
    "pemistahl__grex.fa3e8ed": {
        "repository": "pemistahl/grex",
        "binary_name": "grex",
        "behavior_themes": [
            "regular-expression synthesis from arguments, stdin, and fixture files",
            "case, repetition, anchors, escaping, Unicode, and dialect options",
            "empty sets, overlapping examples, metacharacters, and diagnostics",
        ],
    },
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-summary", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--generation-workers", type=int, default=1)
    args = parser.parse_args()

    summary = json.loads(args.source_summary.read_text(encoding="utf-8"))
    rows = summary.get("repositories") or []
    if len(rows) != 6 or any(row.get("state") != "completed" for row in rows):
        raise RuntimeError("PB Rust6 config requires six completed immutable source snapshots")
    by_id = {str(row["instance_id"]): row for row in rows}
    if set(by_id) != set(CATALOG):
        raise RuntimeError("source summary and PB Rust6 catalog differ")
    if not 1 <= args.generation_workers <= args.workers <= 6:
        raise RuntimeError("workers must satisfy 1 <= generation-workers <= workers <= 6")

    adapter = ROOT / "v4/adapters/gofumpt_pilot_adapter.py"
    scope_files = [
        adapter,
        ROOT / "v4/programbench_v4/witnesses.py",
        ROOT / "v4/programbench_v4/dependencies.py",
        ROOT / "v4/tools/build_agent_context.py",
        ROOT / "tools/programbench_agent_provider.py",
        ROOT / "setup/invoke_agent_maestro_anthropic.ps1",
        ROOT / "tools/programbench_generate_cli_oracle_bundle.py",
        ROOT / "tools/programbench_run_generated_oracle_quality_gates.py",
    ]
    cargo_llvm_cov = Path("/home/programbench/.cargo/bin/cargo-llvm-cov").resolve(strict=True)
    repositories: list[dict[str, Any]] = []
    for instance, metadata in CATALOG.items():
        row = by_id[instance]
        source = Path(row["source_dir"]).resolve(strict=True)
        scale = scale_target_range(
            instance,
            "rust",
            first_party_source_metrics(source, "rust"),
        )
        repositories.append(
            {
                "instance_id": instance,
                **metadata,
                **scale,
                "language": "rust",
                "commit": row["commit"],
                "source_dir": str(source),
                "source_tree_sha256": source_tree_sha256(source),
                "runtime_image_id": args.image_id,
                "runtime_image_reference": "programbench/v3-external-runtime:ubuntu22-local",
                "stage_adapter": [sys.executable, str(adapter)],
                "scope_files": [str(path) for path in scope_files],
                "model": args.model,
                "suite_label": "v4_pb_rust6",
                "behavior_themes": metadata["behavior_themes"],
                "pilot_raw_fuse": max(1000, int(scale["target_retained_cases_max"])),
                "pilot_retained_fuse": int(scale["target_retained_cases_max"]),
                "family_quota": 12,
                "dependency_prefetch_timeout_seconds": 3600,
                "preflight_timeout_seconds": 7200,
                "quality_timeout_seconds": 7200,
                "rust_toolchain_root": "/home/programbench/.rustup/toolchains/stable-x86_64-unknown-linux-gnu",
                "cargo_home": "/home/programbench/.cargo",
                "rust_libclang_root": "/usr/lib/llvm-14/lib",
                "rust_build_args": f"--release --locked --bin {metadata['binary_name']}",
                "rust_binary_relpath": f"release/{metadata['binary_name']}",
                "rust_native_test_args": "--workspace --locked --no-fail-fast",
                "rust_llvm_cov_args": "--workspace --locked",
                "rust_target_triple": "x86_64-unknown-linux-gnu",
                "rust_cargo_llvm_cov_binary": str(cargo_llvm_cov),
                "rust_cargo_llvm_cov_sha256": sha256_file(cargo_llvm_cov),
            }
        )

    config = {
        "schema": "programbench_v4_campaign_v1",
        "campaign_id": "pb-v4-pb-rust6-20260820",
        "mode": "production",
        "output_root": str(args.output_root.resolve()),
        "repo_workers": args.workers,
        "generation_workers": args.generation_workers,
        "heartbeat_seconds": 15,
        "stage_timeout_seconds": 7200,
        "stale_lock_seconds": 120,
        "marginal_policy": {
            "saturation_windows": 3,
            "minimum_promoted_observations": 4,
            "minimum_primary_gain_pp": 0.25,
            "minimum_novelty": 1,
            "repo_wall_budget_seconds": 86400,
            "raw_candidate_fuse": None,
            "adaptive_raw_fuse": False,
            "raw_candidate_hard_fuse": None,
            "raw_fuse_headroom_ratio": 0.50,
            "raw_fuse_minimum_growth": 500,
            "raw_fuse_minimum_witness_density": 0.10,
            "raw_fuse_minimum_promoted_observations": 2,
            "retained_suite_fuse": 900,
            "adaptive_retained_fuse": True,
            "retained_suite_hard_fuse": 1350,
            "retained_fuse_headroom_ratio": 0.50,
            "retained_fuse_minimum_growth": 200,
        },
        "tranche_policy": {
            "initial_size": 64,
            "minimum_size": 32,
            "maximum_size": 128,
            "high_yield_ratio": 0.25,
            "low_yield_ratio": 0.05,
            "growth_factor": 1.5,
            "shrink_factor": 0.5,
        },
        "repositories": repositories,
    }
    atomic_write_json(args.config.resolve(), config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
