#!/usr/bin/env python3
"""Sequentially finalize the nine V2 Go suites after the accepted yj pilot."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path


INSTANCES = [
    "tomnomnom__gron.88a6234",
    "multiprocessio__dsq.c3ae0ba",
    "psampaz__go-mod-outdated.bb79367",
    "rs__jplot.2a54bcc",
    "mibk__dupl.1bf052b",
    "astaxie__bat.17d1080",
    "cheat__cheat.b8098dc",
    "boyter__scc.515f91c",
    "alecthomas__chroma.8d04def",
]


def save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--tasks-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--xdist", default="4")
    args = parser.parse_args()
    run_root = args.run_root.resolve()
    status_path = run_root / "go9_manual_final_coordinator_status.json"
    runner = Path(__file__).resolve().parent / "run_go_v2_final_manual.py"
    results: list[dict] = []
    env = os.environ.copy()
    for index, instance in enumerate(INSTANCES):
        save(status_path, {"state": "running", "current_instance": instance, "index": index, "total": len(INSTANCES), "results": results, "updated_at": dt.datetime.now(dt.timezone.utc).isoformat()})
        proc = subprocess.run([
            sys.executable, str(runner), instance,
            "--run-root", str(run_root), "--tasks-root", str(args.tasks_root),
            "--work-root", str(args.work_root), "--xdist", args.xdist,
        ], env=env)
        summary_path = run_root / "formal" / instance / "v2_expansive_final" / "pipeline_summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
        results.append({"instance_id": instance, "returncode": proc.returncode, "status": summary.get("status", "missing_summary"), "summary_path": str(summary_path)})
    complete = all(item["status"] == "passed" for item in results)
    save(status_path, {"state": "completed" if complete else "needs_repair", "results": results, "updated_at": dt.datetime.now(dt.timezone.utc).isoformat()})
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
