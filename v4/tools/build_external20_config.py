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


SOURCE_EXTENSIONS = {
    "go": {".go"},
    "rust": {".rs"},
}

FIRST_PARTY_EXCLUDED_DIRS = {
    ".git",
    "vendor",
    "third_party",
    "node_modules",
    "target",
    "build",
    "dist",
    "out",
    "generated",
    "gen",
    "test",
    "tests",
    "testdata",
    "fixtures",
    "fixture",
    "benches",
    "benchmark",
    "benchmarks",
    "examples",
    "docs",
    "doc",
}

COMPLEXITY_PROFILES: dict[str, dict[str, Any]] = {
    "charmbracelet__glow.e5cb757": {"kind": "format_rich_cli", "multiplier": 1.25},
    "charmbracelet__gum.716d8b5": {"kind": "multi_subcommand_tui_cli", "multiplier": 1.50},
    "go-task__task.d2b02e3": {"kind": "stateful_task_runner", "multiplier": 1.50},
    "mvdan__sh.c9ed92e": {"kind": "parser_formatter_language_surface", "multiplier": 1.75},
    "restic__restic.a80be14": {"kind": "stateful_repository_cli", "multiplier": 1.75},
    "air-verse__air.9f19e52": {"kind": "daemon_watch_process_cli", "multiplier": 1.50},
    "rustic-rs__rustic.b8218d6": {"kind": "stateful_repository_cli", "multiplier": 1.75},
    "casey__just.4f41f60": {"kind": "task_language_runner", "multiplier": 1.75},
    "sharkdp__vivid.a5d05c0": {"kind": "simple_transformer_cli", "multiplier": 1.00},
    "watchexec__watchexec.db427ba": {"kind": "watch_process_concurrency_cli", "multiplier": 1.75},
    "starship__starship.6bf659e": {"kind": "multi_module_prompt_renderer", "multiplier": 1.50},
    "charmbracelet__freeze.f427610": {"kind": "rendering_multiformat_cli", "multiplier": 1.50},
    "mvdan__gofumpt.5dca7d8": {"kind": "formatter_cli", "multiplier": 1.25},
    "itchyny__gojq.2e210b5": {"kind": "query_language_interpreter", "multiplier": 2.00},
    "itchyny__mmv.cf6f045": {"kind": "small_filesystem_cli", "multiplier": 1.25},
    "muesli__duf.4636deb": {"kind": "system_state_reporting_cli", "multiplier": 1.25},
    "peltoche__lsd.5a36723": {"kind": "filesystem_listing_cli", "multiplier": 1.25},
    "orhun__git-cliff.5963160": {"kind": "git_history_template_cli", "multiplier": 1.50},
    "ouch-org__ouch.3843842": {"kind": "archive_multiformat_cli", "multiplier": 1.75},
    "crate-ci__typos.5e38bf2": {"kind": "static_analyzer_dictionary_surface", "multiplier": 1.75},
}


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def first_party_source_metrics(source: Path, language: str) -> dict[str, Any]:
    extensions = SOURCE_EXTENSIONS[language]
    files = 0
    bytes_total = 0
    for path in source.rglob("*"):
        if not path.is_file() or path.suffix not in extensions:
            continue
        relative = path.relative_to(source)
        if set(relative.parts[:-1]) & FIRST_PARTY_EXCLUDED_DIRS:
            continue
        name = path.name.lower()
        if language == "go" and name.endswith("_test.go"):
            continue
        if (
            "generated" in name
            or name.endswith(".pb.go")
            or name.endswith("_codegen.rs")
            or name == "codegen.rs"
            or name == "dict_codegen.rs"
            or name == "word_codegen.rs"
            or name == "vars_codegen.rs"
        ):
            continue
        files += 1
        bytes_total += path.stat().st_size
    return {
        "first_party_source_bytes": bytes_total,
        "first_party_source_mib": bytes_total / (1024 * 1024),
        "first_party_source_files": files,
    }


def base_target_range(source_mib: float, source_files: int) -> tuple[int, int]:
    if source_mib < 0.25:
        low, high = 500, 900
    elif source_mib < 1.0:
        low, high = 700, 1500
    elif source_mib < 4.0:
        low, high = 1000, 2000
    elif source_mib < 16.0:
        low, high = 1500, 3000
    else:
        low, high = 3000, 6000
    if source_files > 300:
        low = max(low, 1200)
        high = max(high, 2000)
    elif source_files > 100:
        low = max(low, 900)
        high = max(high, 1600)
    elif source_files > 50:
        low = max(low, 700)
        high = max(high, 1200)
    return low, high


def scale_target_range(instance: str, language: str, metrics: dict[str, Any]) -> dict[str, Any]:
    base_low, base_high = base_target_range(
        float(metrics["first_party_source_mib"]),
        int(metrics["first_party_source_files"]),
    )
    profile = COMPLEXITY_PROFILES.get(instance, {"kind": "ordinary_cli", "multiplier": 1.0})
    multiplier = float(profile["multiplier"])
    if language in {"c", "cpp", "c++"}:
        multiplier *= 1.3
    low = round(base_low * multiplier / 50) * 50
    high = round(base_high * multiplier / 50) * 50
    if profile["kind"] in {
        "query_language_interpreter",
        "static_analyzer_dictionary_surface",
    }:
        high = max(high, 5000)
    low = max(400, int(low))
    high = max(low + 200, int(high))
    hard = max(high + 1, round(high * 1.5 / 50) * 50)
    return {
        **metrics,
        "target_scale_basis": "pb200_active_test_reference_distribution_by_first_party_source_size",
        "target_scale_profile": profile["kind"],
        "target_scale_multiplier": multiplier,
        "target_retained_cases_min": low,
        "target_retained_cases_max": high,
        "target_retained_cases_hard": hard,
    }


CATALOG: dict[str, dict[str, Any]] = {
    "charmbracelet__glow.e5cb757": {"repository":"charmbracelet/glow","language":"go","binary_name":"glow","go_build_package":".","behavior_themes":["markdown rendering from files and stdin", "style, width, pager, and output flags", "malformed markdown, Unicode, links, and filesystem fixtures"]},
    "charmbracelet__gum.716d8b5": {"repository":"charmbracelet/gum","language":"go","binary_name":"gum","go_build_package":".","behavior_themes":["deterministic noninteractive text subcommands", "format, style, table, join, and filter flag combinations", "stdin, files, Unicode, diagnostics, and boundary data"]},
    "go-task__task.d2b02e3": {"repository":"go-task/task","language":"go","binary_name":"task","go_build_package":"./cmd/task","behavior_themes":["Taskfile parsing and local task execution", "variables, includes, dependencies, status and summary", "multi-file fixtures, errors, dry-run, and deterministic state"]},
    "mvdan__sh.c9ed92e": {"repository":"mvdan/sh","language":"go","binary_name":"shfmt","go_build_package":"./cmd/shfmt","behavior_themes":["shell formatting from stdin and files", "language dialect, simplify, diff, list and write modes", "comments, heredocs, Unicode, malformed and multi-file shell fixtures"]},
    "restic__restic.a80be14": {"repository":"restic/restic","language":"go","binary_name":"restic","go_build_package":"./cmd/restic","behavior_themes":["offline local repository initialization and inspection", "backup, snapshots, restore, forget and check on tiny fixtures", "config, exclude, stdin, errors, and deterministic filesystem state"]},
    "air-verse__air.9f19e52": {"repository":"air-verse/air","language":"go","binary_name":"air","go_build_package":".","behavior_themes":["configuration parsing and init output", "bounded build/watch startup with deterministic timeout", "exclude/include patterns, filesystem changes, errors and signals"]},
    "rustic-rs__rustic.b8218d6": {"repository":"rustic-rs/rustic","language":"rust","binary_name":"rustic","rust_build_args":"--release --locked --bin rustic","rust_binary_relpath":"release/rustic"},
    "casey__just.4f41f60": {"repository":"casey/just","language":"rust","binary_name":"just","rust_build_args":"--release --locked --bin just","rust_binary_relpath":"release/just","native_test_exclusions":["two dangling generated-doc manpage symlinks omitted from the immutable source snapshot"]},
    "sharkdp__vivid.a5d05c0": {"repository":"sharkdp/vivid","language":"rust","binary_name":"vivid","rust_build_args":"--release --locked --bin vivid","rust_binary_relpath":"release/vivid"},
    "watchexec__watchexec.db427ba": {"repository":"watchexec/watchexec","language":"rust","binary_name":"watchexec","rust_build_args":"--release --bin watchexec","rust_native_test_args":"--workspace --no-fail-fast","rust_binary_relpath":"release/watchexec"},
    "starship__starship.6bf659e": {"repository":"starship/starship","language":"rust","binary_name":"starship","rust_build_args":"--release --locked --bin starship","rust_binary_relpath":"release/starship"},
    "charmbracelet__freeze.f427610": {"repository":"charmbracelet/freeze","language":"go","binary_name":"freeze","go_build_package":".","behavior_themes":["render local code and terminal input to images", "language, theme, font, dimensions and output options", "stdin, Unicode, malformed input and deterministic image metadata"]},
    "mvdan__gofumpt.5dca7d8": {"repository":"mvdan/gofumpt","language":"go","binary_name":"gofumpt","go_build_package":".","go_native_coverage_package":"./format"},
    "itchyny__gojq.2e210b5": {"repository":"itchyny/gojq","language":"go","binary_name":"gojq","go_build_package":"./cmd/gojq","behavior_themes":["JSON filters, streams, variables and modules", "raw, compact, slurp, null-input and exit flags", "errors, Unicode, dates under fixed TZ, files and stdin"]},
    "itchyny__mmv.cf6f045": {"repository":"itchyny/mmv","language":"go","binary_name":"mmv","go_build_package":".","behavior_themes":["batch rename preview and execution", "patterns, collisions, overwrite and directory fixtures", "Unicode, permissions, symlinks represented as fixtures, and errors"]},
    "muesli__duf.4636deb": {"repository":"muesli/duf","language":"go","binary_name":"duf","go_build_package":".","behavior_themes":["deterministic help, filters and output modes", "JSON and table formatting over explicit mount arguments", "invalid filters, widths, colors disabled and boundary values"]},
    "peltoche__lsd.5a36723": {"repository":"Peltoche/lsd","language":"rust","binary_name":"lsd","rust_build_args":"--release --locked --bin lsd","rust_binary_relpath":"release/lsd"},
    "orhun__git-cliff.5963160": {"repository":"orhun/git-cliff","language":"rust","binary_name":"git-cliff","rust_build_args":"--release --locked -p git-cliff --bin git-cliff","rust_binary_relpath":"release/git-cliff"},
    "ouch-org__ouch.3843842": {"repository":"ouch-org/ouch","language":"rust","binary_name":"ouch","rust_build_args":"--release --locked --bin ouch","rust_binary_relpath":"release/ouch"},
    "crate-ci__typos.5e38bf2": {"repository":"crate-ci/typos","language":"rust","binary_name":"typos","rust_build_args":"--release --locked --bin typos","rust_binary_relpath":"release/typos"},
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-summary", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    # External20 is intentionally wide: repository stages are isolated and
    # the controller gates only Agent generation. Users can lower these for
    # constrained hosts, but the production default is 10 x 8.
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--generation-workers", type=int, default=8)
    parser.add_argument("--quality-timeout-seconds", type=int)
    parser.add_argument("--afl-root", default="/home/programbench/research/tools/afl-src/aflplusplus-4.00c")
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--instance", action="append", dest="instances",
                        help="build a pinned subset; repeat for each instance_id")
    parser.add_argument("--campaign-id", default="v4-external20-20260816")
    args = parser.parse_args()
    summary = json.loads(args.source_summary.read_text(encoding="utf-8"))
    rows = summary.get("repositories") or []
    selected = set(args.instances or CATALOG)
    if not selected or not selected.issubset(CATALOG):
        raise RuntimeError(f"unknown/empty selected instances: {sorted(selected - set(CATALOG))}")
    if len(rows) != len(selected) or any(row.get("state") != "completed" for row in rows):
        raise RuntimeError("config requires one completed source snapshot per selected instance")
    if selected != {str(row["instance_id"]) for row in rows}:
        raise RuntimeError("source summary and selected catalog subset differ")
    adapter = ROOT / "v4/adapters/gofumpt_pilot_adapter.py"
    scope_files = [
        adapter,
        ROOT / "v4/programbench_v4/witnesses.py",
        ROOT / "v4/programbench_v4/controller.py",
        ROOT / "v4/programbench_v4/policy.py",
        ROOT / "v4/programbench_v4/scheduler.py",
        ROOT / "v4/programbench_v4/candidates.py",
        ROOT / "v4/programbench_v4/go_afl_qemu.py",
        ROOT / "v4/programbench_v4/native_afl_qemu.py",
        ROOT / "v4/programbench_v4/rust_afl_qemu.py",
        ROOT / "v3/tools/run_afl_qemu_suite.py",
        ROOT / "v3/tools/go_pclntab_ranges.go",
        ROOT / "v3/tools/afl_qemu_capture_wrapper.sh",
        ROOT / "v4/programbench_v4/dependencies.py",
        ROOT / "v4/tools/build_agent_context.py",
        ROOT / "tools/programbench_agent_provider.py",
        ROOT / "setup/invoke_agent_maestro_anthropic.ps1",
        ROOT / "tools/programbench_generate_cli_oracle_bundle.py",
        ROOT / "tools/programbench_run_generated_oracle_quality_gates.py",
    ]
    repositories: list[dict[str, Any]] = []
    by_id = {str(row["instance_id"]): row for row in rows}
    for instance, metadata in CATALOG.items():
        if instance not in selected:
            continue
        row = by_id[instance]
        source = Path(row["source_dir"]).resolve(strict=True)
        scale = scale_target_range(instance, str(metadata["language"]), first_party_source_metrics(source, str(metadata["language"])))
        repository = {
            "instance_id": instance,
            **metadata,
            **scale,
            "commit": row["commit"],
            "source_dir": str(source),
            "source_tree_sha256": source_tree_sha256(source),
            "runtime_image_id": args.image_id,
            "runtime_image_reference": "programbench/v3-external-runtime:ubuntu22-local",
            "stage_adapter": [sys.executable, str(adapter)],
            "scope_files": [str(path) for path in scope_files],
            "model": args.model,
            "pilot_raw_fuse": max(1000, int(scale["target_retained_cases_max"])),
            "pilot_retained_fuse": int(scale["target_retained_cases_max"]),
            "family_quota": 12,
            "suite_label": "v4_external20",
            "afl_qemu_enabled": True,
            "afl_qemu_root": args.afl_root,
            "afl_measurement_period": 3,
        }
        if args.quality_timeout_seconds is not None:
            if not 60 <= args.quality_timeout_seconds <= 7200:
                raise SystemExit("--quality-timeout-seconds must be in 60..7200")
            repository["quality_timeout_seconds"] = args.quality_timeout_seconds
        if metadata["language"] == "go":
            repository.update(
                {
                    "go_toolchain_root": "/usr/local/go1.26.5",
                    "go_mod_cache": "/home/programbench/go/pkg/mod",
                    "go_native_test_command": "go test ./... -count=1",
                    "go_native_coverage_package": metadata.get("go_native_coverage_package", "./..."),
                    "go_cover_package": "./...",
                    "go_afl_max_calls": 2000,
                    "go_afl_timeout_seconds": 7200,
                }
            )
            if instance == "mvdan__gofumpt.5dca7d8":
                repository["go_prefetch_install_specs"] = [
                    "mvdan.cc/gofumpt@v0.8.1-0.20250831111522-b5fd2eb6e821"
                ]
        else:
            cargo_llvm_cov = Path("/home/programbench/.cargo/bin/cargo-llvm-cov").resolve(strict=True)
            repository.update(
                {
                    "rust_toolchain_root": "/home/programbench/.rustup/toolchains/stable-x86_64-unknown-linux-gnu",
                    "cargo_home": "/home/programbench/.cargo",
                    "rust_libclang_root": "/usr/lib/llvm-14/lib",
                    "rust_native_test_args": metadata.get("rust_native_test_args", "--workspace --locked --no-fail-fast"),
                    "rust_llvm_cov_args": "--workspace" if instance == "watchexec__watchexec.db427ba" else "--workspace --locked",
                    "rust_target_triple": "x86_64-unknown-linux-gnu",
                    "rust_cargo_llvm_cov_binary": str(cargo_llvm_cov),
                    "rust_cargo_llvm_cov_sha256": sha256_file(cargo_llvm_cov),
                    "afl_path_auxiliary_stop": True,
                }
            )
            if instance == "watchexec__watchexec.db427ba":
                repository["rust_fetch_args"] = "--target x86_64-unknown-linux-gnu"
        repositories.append(repository)
    if not 1 <= args.workers <= 10:
        raise SystemExit("--workers must be in 1..10")
    if not 1 <= args.generation_workers <= min(8, args.workers):
        raise SystemExit("--generation-workers must be in 1..min(8,--workers)")
    config = {
        "schema": "programbench_v4_campaign_v1",
        "campaign_id": args.campaign_id,
        "mode": "production",
        "output_root": str(args.output_root.resolve()),
        "repo_workers": args.workers,
        "generation_workers": args.generation_workers,
        # Keep the repository/generation queue wide while admitting only a
        # host-safe aggregate of heavyweight Docker stages by default.
        "resource_admission": {
            "cpu_capacity": 10,
            "memory_capacity_mib": 22 * 1024,
        },
        "heartbeat_seconds": 15,
        "stage_timeout_seconds": 7200,
        "stale_lock_seconds": 120,
        "marginal_policy": {
            "saturation_windows": 3,
            "minimum_promoted_observations": 4,
            "minimum_primary_gain_pp": 0.25,
            "minimum_novelty": 1,
            "repo_wall_budget_seconds": 129600,
            "raw_candidate_fuse": None,
            "adaptive_raw_fuse": False,
            "raw_candidate_hard_fuse": None,
            "raw_fuse_headroom_ratio": 0.50,
            "raw_fuse_minimum_growth": 500,
            "raw_fuse_minimum_witness_density": 0.10,
            "raw_fuse_minimum_promoted_observations": 2,
            "retained_suite_fuse": 1000,
            "adaptive_retained_fuse": True,
            "retained_suite_hard_fuse": 1500,
            "retained_fuse_headroom_ratio": 0.50,
            "retained_fuse_minimum_growth": 200
            ,"require_auxiliary_path_saturation": True
            ,"auxiliary_path_saturation_windows": 2
            ,"auxiliary_path_minimum_relative_gain": 0.01
        },
        "tranche_policy": {
            "initial_size": 64,
            "minimum_size": 32,
            "maximum_size": 128,
            "high_yield_ratio": 0.25,
            "low_yield_ratio": 0.05,
            "growth_factor": 1.5,
            "shrink_factor": 0.5,
            "breadth_bootstrap_iterations": 1,
            "breadth_bootstrap_views": [
                "successful_workflow",
                "boundary_and_error",
                "fixture_and_unicode",
                "state_and_interaction"
            ],
            "reservoir_rerank_period": 3
        },
        "repositories": repositories,
    }
    atomic_write_json(args.config.resolve(), config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
