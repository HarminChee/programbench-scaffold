#!/usr/bin/env python3
"""Create compact, verified archives of frozen V4 repository results.

The archive keeps the runnable oracle bundle and its fixtures, candidate ledger,
quality/provenance evidence, native/AFL artifacts, and final coverage summary.
Large reproducible build/dependency caches and per-case LLVM profiles are omitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path


KEPT_DIRS = (
    "agent_cases",
    "agent_context",
    "agent_runs",
    "artifacts",
    "bundles",
    "candidates",
    "cleanroom_context",
    "dropped_tranches",
    "frozen",
    "logs",
    "plans",
    "quality",
    "stages",
    "witnesses",
    "zero_witness_rollbacks",
)
KEPT_ROOT_FILES = (
    "marginal_decision.json",
    "recovery_autorollback.json",
    "recovery_checkpoint.json",
    "status.json",
)
KEPT_COVERAGE_FILES = (
    "coverage/full/coverage.json",
    "coverage/full/out/export.json",
    "coverage/full/out/merged.profdata",
    "coverage/full/oracle/README.md",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_path(tar: tarfile.TarFile, path: Path, arcname: Path) -> None:
    if path.exists():
        tar.add(path, arcname=str(arcname), recursive=True)


def archive_repo(repo: Path, destination: Path) -> dict[str, object]:
    status = json.loads((repo / "status.json").read_text(encoding="utf-8"))
    if status.get("state") != "completed":
        raise RuntimeError(f"{repo.name}: status is not completed")
    frozen = repo / "frozen"
    if not frozen.is_dir() or not any(frozen.iterdir()):
        raise RuntimeError(f"{repo.name}: missing frozen evidence")

    archive = destination / f"{repo.name}.tar.gz"
    if archive.exists():
        raise FileExistsError(archive)
    with tarfile.open(archive, "w:gz", compresslevel=6) as tar:
        prefix = Path(repo.name)
        for name in KEPT_DIRS:
            add_path(tar, repo / name, prefix / name)
        for name in KEPT_ROOT_FILES:
            add_path(tar, repo / name, prefix / name)
        for name in KEPT_COVERAGE_FILES:
            add_path(tar, repo / name, prefix / name)

    with tarfile.open(archive, "r:gz") as tar:
        members = [item.name for item in tar.getmembers() if item.isfile()]
    required = {
        "runnable_test": any(x.endswith("/test_generated_cli_oracle.py") for x in members),
        "candidate_ledger": any(x.endswith("/candidates/current.json") for x in members),
        "frozen_evidence": any("/frozen/" in x for x in members),
        "quality_evidence": any("/quality/" in x or x.endswith("/quality_report.json") for x in members),
        "status": any(x.endswith("/status.json") for x in members),
    }
    if not all(required.values()):
        raise RuntimeError(f"{repo.name}: archive verification failed: {required}")
    return {
        "repository": repo.name,
        "archive": archive.name,
        "bytes": archive.stat().st_size,
        "sha256": sha256(archive),
        "retained_tests": status.get("retained_suite_cases"),
        "primary_coverage": status.get("primary_coverage"),
        "stop_reason": status.get("stop_reason"),
        "verification": required,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repositories-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("repositories", nargs="+")
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=False)
    rows = [archive_repo(args.repositories_root / name, args.destination) for name in args.repositories]
    manifest = {
        "schema": "programbench_v4_protected_frozen_archive_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "do_not_delete": True,
        "repositories_root": str(args.repositories_root),
        "archive_policy": {
            "kept": "runnable bundles and fixtures, candidates, quality, provenance, native/AFL artifacts, final coverage export",
            "omitted_rebuildable": "dependency/build caches, capture workdirs, quality workdirs, per-case LLVM profraw",
        },
        "repository_count": len(rows),
        "repositories": rows,
    }
    manifest_path = args.destination / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.destination / "DO_NOT_DELETE").write_text(
        "Protected frozen ProgramBench results. See MANIFEST.json before any cleanup.\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
