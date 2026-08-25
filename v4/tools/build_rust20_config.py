#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
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
    "facebookincubator__fastmod.974e3ef": {"repository":"facebookincubator/fastmod","binary_name":"fastmod","complexity":1.0,"behavior_themes":["literal and regex replacement", "stdin and file traversal", "include/exclude patterns", "Unicode, malformed expressions, and diagnostics"]},
    "mike-engel__jwt-cli.6b203a2": {"repository":"mike-engel/jwt-cli","binary_name":"jwt","complexity":1.0,"behavior_themes":["JWT decode and JSON output", "JWT encode with fixed keys", "algorithms, headers, claims, and timestamps", "malformed tokens and key errors"]},
    "burntsushi__bttf.fa8b543": {"repository":"BurntSushi/bttf","binary_name":"bttf","complexity":1.25,"behavior_themes":["datetime parsing", "datetime arithmetic", "formatting and timezone conversion", "boundary dates, invalid input, and fixed locale"]},
    "svetlitski__fcp.f8db060": {"repository":"Svetlitski/fcp","binary_name":"fcp","complexity":1.0,"behavior_themes":["single-file copy", "recursive directory copy", "overwrite and destination semantics", "permissions, empty files, Unicode, and errors"]},
    "lukas-reineke__cbfmt.88a3e46": {"repository":"lukas-reineke/cbfmt","binary_name":"cbfmt","complexity":1.0,"behavior_themes":["Markdown fenced code blocks", "Org and reStructuredText blocks", "formatter selection and config", "stdin, file, malformed, and idempotence behavior"]},
    "sigoden__projclean.2135f41": {"repository":"sigoden/projclean","binary_name":"projclean","complexity":1.0,"behavior_themes":["project artifact discovery", "size and dry-run reporting", "cleanup selection", "nested trees, exclusions, and errors"]},
    "ikanago__omekasy.a54c47a": {"repository":"ikanago/omekasy","binary_name":"omekasy","complexity":1.0,"behavior_themes":["Unicode style transforms", "stdin and argument input", "mixed scripts and punctuation", "empty, boundary, and unsupported characters"]},
    "camdencheek__fre.6574ee7": {"repository":"camdencheek/fre","binary_name":"fre","complexity":1.25,"behavior_themes":["record frecency events", "query and ranking", "aging and deletion", "isolated HOME, paths, state, and errors"]},
    "bgreenwell__lstr.722cb63": {"repository":"bgreenwell/lstr","binary_name":"lstr","complexity":1.25,"behavior_themes":["directory tree rendering", "depth, sort, and filtering", "metadata and output modes", "Unicode, hidden files, links-as-fixtures, and errors"]},
    "greymd__teip.07a1777": {"repository":"greymd/teip","binary_name":"teip","complexity":1.25,"behavior_themes":["line and field selection", "regex and character ranges", "external command transformations", "stdin, binary text, Unicode, and errors"]},
    "juan-leon__lowcharts.3e47c2c": {"repository":"juan-leon/lowcharts","binary_name":"lowcharts","complexity":1.0,"behavior_themes":["numeric series parsing", "chart types and dimensions", "labels and multiple series", "fixed terminal, malformed values, and boundaries"]},
    "theryangeary__choose.f1c53ee": {"repository":"theryangeary/choose","binary_name":"choose","complexity":1.0,"behavior_themes":["field and range selection", "separator and regex modes", "negative and open ranges", "stdin, Unicode, malformed selectors, and errors"]},
    "crate-ci__committed.800a04e": {"repository":"crate-ci/committed","binary_name":"committed","complexity":1.0,"behavior_themes":["commit message validation", "conventional commit rules", "configuration and overrides", "stdin messages, Unicode, and diagnostics"]},
    "bgreenwell__doxx.062819a": {"repository":"bgreenwell/doxx","binary_name":"doxx","complexity":1.25,"behavior_themes":["DOCX paragraph extraction", "tables, lists, and metadata", "rendering and output options", "minimal fixtures, malformed ZIP/XML, and errors"]},
    "mufeedvh__code2prompt.ab4fa06": {"repository":"mufeedvh/code2prompt","binary_name":"code2prompt","complexity":1.25,"behavior_themes":["source tree collection", "include/exclude and ignore rules", "templates and token summaries", "stdin, Unicode paths, nested projects, and errors"]},
    "alexhallam__tv.f71935e": {"repository":"alexhallam/tv","binary_name":"tidy-viewer","complexity":1.25,"behavior_themes":["CSV and delimiter parsing", "table width and formatting", "headers, types, and selection", "fixed terminal, Unicode, malformed rows, and errors"]},
    "the-lean-crate__cargo-diet.fadfa31": {"repository":"the-lean-crate/cargo-diet","binary_name":"cargo-diet","complexity":1.25,"behavior_themes":["Cargo package include analysis", "manifest and workspace layouts", "ignored and generated files", "offline synthetic crates, diagnostics, and boundaries"]},
    "ribbondz__rsv.b2f647f": {"repository":"ribbondz/rsv","binary_name":"rsv","complexity":1.5,"behavior_themes":["CSV and TXT inspection", "Excel workbook inspection", "selection, filtering, and statistics", "local fixtures, encodings, malformed rows, and errors"]},
    "pamburus__hl.6164b42": {"repository":"pamburus/hl","binary_name":"hl","complexity":1.5,"behavior_themes":["JSON log parsing", "logfmt and plain logs", "filtering, formatting, and time", "stdin/files, multiline, Unicode, malformed logs, and errors"]},
    "medialab__xan.60a89e5": {"repository":"medialab/xan","binary_name":"xan","complexity":1.5,"behavior_themes":["CSV selection and transformation", "filtering, sorting, and joining", "aggregation, frequency, and statistics", "local files/stdin, dialects, malformed rows, and errors"]},
}


def cargo_bin_scope(source: Path, preferred: str, cargo: Path) -> tuple[str, str]:
    result = subprocess.run(
        [str(cargo), "metadata", "--no-deps", "--format-version", "1", "--manifest-path", str(source / "Cargo.toml")],
        text=True, capture_output=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cargo metadata failed for {source}: {result.stderr[-1000:]}")
    metadata = json.loads(result.stdout)
    matches = []
    for package in metadata.get("packages") or []:
        for target in package.get("targets") or []:
            if "bin" in (target.get("kind") or []) and target.get("name") == preferred:
                matches.append((str(package["name"]), preferred))
    if len(matches) != 1:
        available = sorted(
            target.get("name")
            for package in metadata.get("packages") or []
            for target in package.get("targets") or []
            if "bin" in (target.get("kind") or [])
        )
        raise RuntimeError(f"expected one binary {preferred!r}; available={available}")
    return matches[0]


def scaled(instance: str, source: Path, multiplier: float) -> dict[str, Any]:
    value = scale_target_range(instance, "rust", first_party_source_metrics(source, "rust"))
    for key in ("target_retained_cases_min", "target_retained_cases_max"):
        value[key] = int(round(int(value[key]) * multiplier / 50) * 50)
    value["target_retained_cases_hard"] = max(
        value["target_retained_cases_max"] + 200,
        int(round(value["target_retained_cases_max"] * 1.5 / 50) * 50),
    )
    value["target_scale_profile"] = "simple_rust_cli" if multiplier <= 1.25 else "broad_rust_cli"
    value["target_scale_multiplier"] = multiplier
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-summary", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--generation-workers", type=int, default=8)
    parser.add_argument("--model", default="gpt-5.6-sol")
    args = parser.parse_args()
    if not 1 <= args.workers <= 10 or not 1 <= args.generation_workers <= min(8, args.workers):
        raise SystemExit("worker bounds are 1..10 repositories and 1..min(8,repositories) generation")
    summary = json.loads(args.source_summary.read_text())
    rows = summary.get("repositories") or []
    if len(rows) != 20 or any(row.get("state") != "completed" for row in rows):
        raise RuntimeError("Rust20 config requires exactly 20 completed source snapshots")
    by_id = {str(row["instance_id"]): row for row in rows}
    if set(by_id) != set(CATALOG):
        raise RuntimeError("Rust20 source summary and catalog differ")

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
    for instance, metadata in CATALOG.items():
        row = by_id[instance]
        source = Path(row["source_dir"]).resolve(strict=True)
        package, binary = cargo_bin_scope(source, str(metadata["binary_name"]), cargo)
        scale = scaled(instance, source, float(metadata["complexity"]))
        repositories.append({
            "instance_id": instance,
            "repository": metadata["repository"],
            "language": "rust",
            "binary_name": binary,
            "rust_build_args": f"--release --locked -p {package} --bin {binary}",
            "rust_binary_relpath": f"release/{binary}",
            "behavior_themes": metadata["behavior_themes"],
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
            "suite_label": "v4_rust20",
            "rust_afl_qemu_enabled": True,
            "afl_qemu_enabled": True,
            "rust_afl_root": "/home/programbench/research/tools/afl-src/aflplusplus-4.00c",
            "afl_qemu_root": "/home/programbench/research/tools/afl-src/aflplusplus-4.00c",
            "afl_measurement_period": 3,
            "afl_path_auxiliary_stop": True,
            "rust_afl_timeout_seconds": 3600,
        })

    config = {
        "schema": "programbench_v4_campaign_v1",
        "campaign_id": "v4-rust20-20260824",
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
            "auxiliary_path_minimum_relative_gain": 0.01
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
                "source_entrypoints",
                "docs_native_behavior",
                "successful_workflow",
                "boundary_and_error",
                "fixture_and_unicode",
                "protocol_and_format",
                "state_and_interaction",
                "reachability_repair",
            ],
            "reservoir_rerank_period": 3
        },
        "repositories": repositories,
    }
    atomic_write_json(args.config.resolve(), config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
