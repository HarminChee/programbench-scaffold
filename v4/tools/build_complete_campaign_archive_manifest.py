#!/usr/bin/env python3
"""Verify and index a complete, in-place frozen V4 campaign archive.

Unlike ``archive_frozen_results.py`` this tool does not compact repository
outputs. It proves that source snapshots, runnable bundles, binaries, candidate
state, quality/freeze evidence, configs and stage history remain present in the
full campaign tree. Large caches and raw profiles are intentionally retained.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_stats(root: Path) -> tuple[int, int]:
    files = 0
    size = 0
    for path in root.rglob("*"):
        if path.is_file():
            files += 1
            size += path.stat().st_size
    return files, size


def first(root: Path, patterns: tuple[str, ...], label: str) -> Path:
    matches = sorted({path.resolve() for pattern in patterns for path in root.glob(pattern) if path.is_file()})
    if not matches:
        raise RuntimeError(f"{root.name}: missing {label}")
    return matches[-1]


def inspect_repo(campaign: Path, instance: str) -> dict[str, object]:
    output = campaign / "output/repositories" / instance
    source = campaign / "sources" / instance
    if not output.is_dir() or not source.is_dir():
        raise RuntimeError(f"{instance}: source/output pair is incomplete")
    status_path = output / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if status.get("state") != "completed":
        raise RuntimeError(f"{instance}: status is not completed")
    required = {
        "source_snapshot": first(source, (".programbench_v4_snapshot.json",), "source snapshot"),
        "candidate_suite": first(output, ("candidates/current.json",), "candidate suite"),
        "runnable_test": first(output, ("bundles/**/tests/test_generated_cli_oracle.py",), "runnable test"),
        "bundle_manifest": first(output, ("bundles/**/generated_cli_manifest.json",), "bundle manifest"),
        "reference_binary": first(output, ("artifacts/reference_executable",), "reference binary"),
        "coverage_binary": first(output, ("artifacts/coverage_executable",), "coverage binary"),
        "full_coverage": first(output, ("artifacts/final_full_coverage.json",), "full coverage"),
        "quality": first(output, ("quality/iteration-*.json",), "quality report"),
        "frozen_summary": first(output, ("frozen/settlement_summary.json", "frozen/pipeline_summary.json"), "frozen summary"),
        "recovery": first(output, ("recovery_checkpoint.json",), "recovery checkpoint"),
    }
    full_coverage = json.loads(required["full_coverage"].read_text(encoding="utf-8"))
    output_files, output_bytes = tree_stats(output)
    source_files, source_bytes = tree_stats(source)
    return {
        "instance_id": instance,
        "state": "completed",
        "retained_tests": status.get("retained_suite_cases"),
        "primary_coverage": (
            status.get("primary_coverage")
            if status.get("primary_coverage") is not None
            else full_coverage.get("primary_coverage")
        ),
        "stop_reason": status.get("stop_reason"),
        "output": {"path": str(output), "files": output_files, "bytes": output_bytes},
        "source": {"path": str(source), "files": source_files, "bytes": source_bytes},
        "required_artifacts": {
            name: {"path": str(path.relative_to(campaign)), "bytes": path.stat().st_size,
                   "sha256": sha256(path)} for name, path in required.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign", type=Path)
    parser.add_argument("--expected", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    campaign = args.campaign.resolve(strict=True)
    repositories = sorted(path.name for path in (campaign / "output/repositories").iterdir() if path.is_dir())
    sources = sorted(path.name for path in (campaign / "sources").iterdir() if path.is_dir())
    if repositories != sources or len(repositories) != args.expected:
        raise RuntimeError(f"source/output set mismatch: repos={len(repositories)} sources={len(sources)}")
    configs = sorted(str(path.relative_to(campaign)) for path in campaign.rglob("campaign*.json"))
    if not configs:
        raise RuntimeError("campaign archive has no campaign configuration")
    rows = [inspect_repo(campaign, instance) for instance in repositories]
    document = {
        "schema": "programbench_v4_complete_frozen_campaign_archive_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "do_not_delete": True,
        "archive_policy": "complete in-place campaign; no files omitted",
        "campaign": str(campaign),
        "repository_count": len(rows),
        "repositories": rows,
        "campaign_configs": configs,
        "complete_tree_retained": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"repository_count": len(rows), "output": str(args.output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
