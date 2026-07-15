#!/usr/bin/env python3
"""Discover and run the agent-led oracle workflow over ProgramBench Go tasks."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASKS_ROOT = Path("/home/programbench/research/programbench/src/programbench/data/tasks")
DEFAULT_WORKSPACE_ROOT = Path("/home/programbench/research/oracle-workspace")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_language(path: Path) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("language:"):
            return line.split(":", 1)[1].strip()
    return None


def discover_go_tasks(tasks_root: Path) -> list[str]:
    return sorted(
        task_yaml.parent.name
        for task_yaml in tasks_root.glob("*/task.yaml")
        if parse_language(task_yaml) == "go"
    )


def read_status(workspace_root: Path, instance_id: str) -> str | None:
    summary = workspace_root / "experiments" / "runs" / instance_id / "final" / "run_summary.json"
    if not summary.exists():
        return None
    try:
        value = json.loads(summary.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "invalid_summary"
    return str(value.get("status") or "unknown")


def run_one(
    *,
    instance_id: str,
    tasks_root: Path,
    workspace_root: Path,
    generation_model: str,
    review_model: str,
    max_iterations: int,
    timeout: int,
    overwrite: bool,
    retry_infra: int,
    seed_only: bool,
    skip_review: bool,
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        "tools/programbench_agent_oracle_loop.py",
        instance_id,
        "--tasks-root",
        str(tasks_root),
        "--workspace-root",
        str(workspace_root),
        "--generation-model",
        generation_model,
        "--review-model",
        review_model,
        "--max-iterations",
        str(max_iterations),
    ]
    if overwrite:
        cmd.append("--overwrite")
    if seed_only:
        cmd.append("--seed-only")
    if skip_review:
        cmd.append("--skip-review")
    attempts = []
    for attempt in range(retry_infra + 1):
        started = time.time()
        try:
            proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout)
            result = {
                "attempt": attempt + 1,
                "returncode": proc.returncode,
                "timed_out": False,
                "duration_seconds": round(time.time() - started, 3),
                "stdout_tail": proc.stdout[-8000:],
                "stderr_tail": proc.stderr[-8000:],
            }
        except subprocess.TimeoutExpired as exc:
            result = {
                "attempt": attempt + 1,
                "returncode": 124,
                "timed_out": True,
                "duration_seconds": round(time.time() - started, 3),
                "stdout_tail": str(exc.stdout or "")[-8000:],
                "stderr_tail": str(exc.stderr or "")[-8000:],
            }
        attempts.append(result)
        status = read_status(workspace_root, instance_id)
        infrastructure_error = bool(
            status
            and status.startswith("blocked_")
            and ("provider" in status or "infrastructure" in status)
        )
        if result["returncode"] == 0 or not infrastructure_error or attempt >= retry_infra:
            break
        time.sleep(2**attempt)
    return {
        "instance_id": instance_id,
        "status": read_status(workspace_root, instance_id),
        "attempts": attempts,
        "command": cmd,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-root", type=Path, default=DEFAULT_TASKS_ROOT)
    parser.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE_ROOT)
    parser.add_argument("--instances", help="Comma-separated subset; default is all Go tasks")
    parser.add_argument("--workers", type=int, default=1, choices=range(1, 6))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--generation-model", default="claude-sonnet-5[1m]")
    parser.add_argument("--review-model", default="claude-opus-4.8")
    parser.add_argument("--max-iterations", type=int, default=8)
    parser.add_argument("--instance-timeout", type=int, default=28_800)
    parser.add_argument("--retry-infra", type=int, default=1)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--retry-quality-failed",
        action="store_true",
        help="With --resume, rerun seed ablations whose evidence completed but quality gates failed.",
    )
    parser.add_argument("--seed-only", action="store_true", help="Run the deterministic seed ablation.")
    parser.add_argument("--skip-review", action="store_true", help="Run the review ablation.")
    args = parser.parse_args()

    tasks_root = args.tasks_root.expanduser().resolve()
    workspace_root = args.workspace_root.expanduser().resolve()
    all_go = discover_go_tasks(tasks_root)
    if args.instances:
        requested = [item.strip() for item in args.instances.split(",") if item.strip()]
        unknown = sorted(set(requested) - set(all_go))
        if unknown:
            raise ValueError(f"Not Go ProgramBench tasks: {unknown}")
        selected = requested
    else:
        selected = all_go
    if args.resume:
        remaining = []
        for item in selected:
            existing = read_status(workspace_root, item) or ""
            seed_done = args.seed_only and existing.startswith("seed_ablation_complete")
            if args.retry_quality_failed and existing == "seed_ablation_complete_quality_failed":
                seed_done = False
            done = existing.startswith("accepted_") or seed_done
            if not done:
                remaining.append(item)
        selected = remaining
    if args.limit is not None:
        selected = selected[: args.limit]
    manifest = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "tasks_root": str(tasks_root),
        "workspace_root": str(workspace_root),
        "go_task_count": len(all_go),
        "selected_count": len(selected),
        "workers": args.workers,
        "generation_model": args.generation_model,
        "review_model": args.review_model,
        "max_iterations": args.max_iterations,
        "seed_ablation": args.seed_only,
        "review_ablation": args.skip_review,
        "instances": selected,
    }
    batch_id = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    batch_root = workspace_root / "experiments" / "batches" / batch_id
    write_json(workspace_root / "manifests" / "programbench_go_all.json", {**manifest, "instances": all_go})
    write_json(batch_root / "batch_manifest.json", manifest)

    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                run_one,
                instance_id=instance_id,
                tasks_root=tasks_root,
                workspace_root=workspace_root,
                generation_model=args.generation_model,
                review_model=args.review_model,
                max_iterations=args.max_iterations,
                timeout=args.instance_timeout,
                overwrite=args.overwrite or args.resume,
                retry_infra=args.retry_infra,
                seed_only=args.seed_only,
                skip_review=args.skip_review,
            ): instance_id
            for instance_id in selected
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            write_json(batch_root / "results" / f"{result['instance_id']}.json", result)
            print(json.dumps({"instance_id": result["instance_id"], "status": result["status"]}), flush=True)
    results.sort(key=lambda item: item["instance_id"])
    counts: dict[str, int] = {}
    for item in results:
        key = str(item.get("status") or "missing")
        counts[key] = counts.get(key, 0) + 1
    summary = {**manifest, "completed_at": dt.datetime.now(dt.UTC).isoformat(), "status_counts": counts, "results": results}
    write_json(batch_root / "batch_summary.json", summary)
    print(json.dumps({"batch_root": str(batch_root), "status_counts": counts}, indent=2, sort_keys=True))
    def completed(item: dict[str, Any]) -> bool:
        status = str(item.get("status") or "")
        return status.startswith("accepted_") or (args.seed_only and status.startswith("seed_ablation_complete"))

    return 0 if results and all(completed(item) for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
