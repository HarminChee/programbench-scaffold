#!/usr/bin/env python3
"""Fetch and prove offline source builds for a prepared Rust cohort.

This is a standalone readiness probe.  It never creates a campaign lock or
starts a V4 controller.  Its cache is deliberately separate from production;
the controller still publishes its own scope-bound dependency caches.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from v4.programbench_v4.io import atomic_write_json
from v4.tools.build_rust20_config import cargo_bin_scope


def sha256_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def run_one(candidate: dict[str, Any], snapshot: dict[str, Any], cache_root: Path,
            cargo: Path, timeout: int) -> dict[str, Any]:
    instance = str(candidate["instance_id"])
    source = Path(snapshot["source_dir"]).resolve(strict=True)
    package, binary = cargo_bin_scope(source, str(candidate["binary"]), cargo)
    repo_root = cache_root / "repositories" / instance
    cargo_home = cache_root / "cargo-home"
    target = repo_root / "target"
    logs = repo_root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    cargo_home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "CARGO_HOME": str(cargo_home),
        "CARGO_BUILD_JOBS": "2",
        "CARGO_TERM_COLOR": "never",
    })
    fetch = subprocess.run(
        [str(cargo), "fetch", "--locked", "--manifest-path", str(source / "Cargo.toml")],
        text=True, capture_output=True, env=env, timeout=timeout,
    )
    (logs / "fetch.stdout.log").write_text(fetch.stdout, encoding="utf-8")
    (logs / "fetch.stderr.log").write_text(fetch.stderr, encoding="utf-8")
    if fetch.returncode != 0:
        return {"instance_id": instance, "repository": candidate["repository"],
                "state": "failed", "stage": "cargo_fetch_locked", "returncode": fetch.returncode,
                "error": fetch.stderr[-2000:]}
    build = subprocess.run(
        [str(cargo), "build", "--offline", "--release", "--locked", "-p", package,
         "--bin", binary, "--target-dir", str(target), "--manifest-path", str(source / "Cargo.toml")],
        text=True, capture_output=True, env=env, timeout=timeout,
    )
    (logs / "build.stdout.log").write_text(build.stdout, encoding="utf-8")
    (logs / "build.stderr.log").write_text(build.stderr, encoding="utf-8")
    executable = target / "release" / binary
    if build.returncode != 0 or not executable.is_file():
        return {"instance_id": instance, "repository": candidate["repository"],
                "state": "failed", "stage": "cargo_build_offline_locked", "returncode": build.returncode,
                "error": build.stderr[-2000:]}
    return {
        "instance_id": instance,
        "repository": candidate["repository"],
        "state": "completed",
        "package": package,
        "binary": binary,
        "binary_path": str(executable),
        "binary_sha256": sha256_file(executable),
        "fetch_returncode": fetch.returncode,
        "offline_build_returncode": build.returncode,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--source-summary", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument("--cargo", type=Path, default=Path("/home/programbench/.cargo/bin/cargo"))
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        raise SystemExit("preflight workers must be in 1..4")
    candidates = json.loads(args.candidate_manifest.read_text(encoding="utf-8"))["repositories"]
    snapshots = json.loads(args.source_summary.read_text(encoding="utf-8"))["repositories"]
    by_id = {str(row["instance_id"]): row for row in snapshots if row.get("state") == "completed"}
    if set(by_id) != {str(row["instance_id"]) for row in candidates}:
        raise RuntimeError("not every candidate has a completed source snapshot")

    previous: dict[str, dict[str, Any]] = {}
    if args.report.is_file():
        old = json.loads(args.report.read_text(encoding="utf-8"))
        previous = {str(row["instance_id"]): row for row in old.get("repositories") or []
                    if row.get("state") == "completed" and Path(row.get("binary_path", "")).is_file()}
    pending = [row for row in candidates if str(row["instance_id"]) not in previous]
    results = dict(previous)
    args.cache_root.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one, row, by_id[str(row["instance_id"])], args.cache_root,
                               args.cargo, args.timeout_seconds): row for row in pending}
        for future in concurrent.futures.as_completed(futures):
            row = future.result()
            results[str(row["instance_id"])] = row
            ordered = [results[str(item["instance_id"])] for item in candidates
                       if str(item["instance_id"]) in results]
            atomic_write_json(args.report, {
                "schema": "programbench_v4_rust_candidate_preflight_v1",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "expected": len(candidates),
                "completed": sum(item["state"] == "completed" for item in ordered),
                "failed": sum(item["state"] == "failed" for item in ordered),
                "repositories": ordered,
            })
            print(json.dumps({"repository": row["repository"], "state": row["state"],
                              "stage": row.get("stage")}, sort_keys=True), flush=True)
    return 0 if all(row["state"] == "completed" for row in results.values()) and len(results) == len(candidates) else 1


if __name__ == "__main__":
    raise SystemExit(main())
