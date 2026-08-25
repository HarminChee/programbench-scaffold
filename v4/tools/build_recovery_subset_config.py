from __future__ import annotations

import argparse
import json
from pathlib import Path


def filter_fast_settlement(value: dict, requested: set[str]) -> None:
    """Keep optional fast-settlement policy consistent with a repo subset."""

    section = value.get("fast_settlement")
    if not isinstance(section, dict):
        return
    rows = section.get("repositories")
    if not isinstance(rows, list):
        value.pop("fast_settlement", None)
        return
    retained = [
        row for row in rows
        if isinstance(row, dict) and str(row.get("instance_id")) in requested
    ]
    if retained:
        section["repositories"] = retained
    else:
        value.pop("fast_settlement", None)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an auditable subset config from an existing V4 campaign."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--repo-workers", type=int, default=10)
    parser.add_argument("--generation-workers", type=int, default=8)
    parser.add_argument("--stage-timeout-seconds", type=int)
    parser.add_argument("--saturation-windows", type=int)
    parser.add_argument("--minimum-primary-gain-pp", type=float)
    parser.add_argument("--minimum-novelty", type=int)
    parser.add_argument("--heavy-rust-repo", action="append", default=[])
    parser.add_argument("--heavy-rust-memory", default="8g")
    parser.add_argument("--heavy-rust-preflight-timeout-seconds", type=int, default=7200)
    parser.add_argument("--repo", action="append", required=True)
    args = parser.parse_args()

    if not 1 <= args.repo_workers <= 10:
        raise SystemExit("--repo-workers must be in 1..10")
    if not 1 <= args.generation_workers <= min(8, args.repo_workers):
        raise SystemExit("--generation-workers must be in 1..min(8,--repo-workers)")
    value = json.loads(args.input.read_text(encoding="utf-8"))
    requested = list(dict.fromkeys(args.repo))
    by_id = {str(row["instance_id"]): row for row in value["repositories"]}
    missing = [instance for instance in requested if instance not in by_id]
    if missing:
        raise SystemExit(f"unknown repository IDs: {missing}")
    value["campaign_id"] = args.campaign_id
    value["repo_workers"] = min(args.repo_workers, len(requested))
    value["generation_workers"] = min(args.generation_workers, value["repo_workers"])
    value.setdefault(
        "resource_admission",
        {"cpu_capacity": 10, "memory_capacity_mib": 22 * 1024},
    )
    if args.stage_timeout_seconds is not None:
        if args.stage_timeout_seconds <= 0:
            raise SystemExit("--stage-timeout-seconds must be positive")
        value["stage_timeout_seconds"] = args.stage_timeout_seconds
    policy = value.setdefault("marginal_policy", {})
    if args.saturation_windows is not None:
        if args.saturation_windows < 1:
            raise SystemExit("--saturation-windows must be positive")
        policy["saturation_windows"] = args.saturation_windows
    if args.minimum_primary_gain_pp is not None:
        if args.minimum_primary_gain_pp < 0:
            raise SystemExit("--minimum-primary-gain-pp must be nonnegative")
        policy["minimum_primary_gain_pp"] = args.minimum_primary_gain_pp
    if args.minimum_novelty is not None:
        if args.minimum_novelty < 0:
            raise SystemExit("--minimum-novelty must be nonnegative")
        policy["minimum_novelty"] = args.minimum_novelty
    value["repositories"] = [by_id[instance] for instance in requested]
    filter_fast_settlement(value, set(requested))
    heavy_rust = set(args.heavy_rust_repo)
    unknown_heavy = sorted(heavy_rust - set(requested))
    if unknown_heavy:
        raise SystemExit(f"unknown heavy Rust repository IDs: {unknown_heavy}")
    for row in value["repositories"]:
        if str(row["instance_id"]) in heavy_rust:
            if str(row.get("language", "")).lower() != "rust":
                raise SystemExit(f"heavy Rust override requested for non-Rust repo: {row['instance_id']}")
            row["rust_preflight_memory"] = args.heavy_rust_memory
            row["preflight_timeout_seconds"] = args.heavy_rust_preflight_timeout_seconds
    value["recovery_parent_config"] = str(args.input.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
