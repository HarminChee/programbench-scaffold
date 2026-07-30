#!/usr/bin/env python3
"""Finish the remaining Go-10 V2 suites from their latest repair manifests."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
V2 = ROOT / "v2" / "tools"
COMPAT = ROOT / "v2" / "python_compat"


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def invoke(cmd: list[str], env: dict[str, str], log: Path) -> int:
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    proc = subprocess.run(cmd, text=True, capture_output=True, env=env)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps(
            {
                "cmd": cmd,
                "started_at": started,
                "ended_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--tasks-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--xdist", default="4")
    parser.add_argument("--start-index", type=int, default=0)
    args = parser.parse_args()

    run_root = args.run_root.resolve()
    status_path = run_root / "go10_final_repair_status.json"
    tasks = [
        (
            "boyter__scc.515f91c",
            run_root / "boyter__scc.515f91c" / "merged" / "v2_expansive_filtered_candidates.json",
        ),
        (
            "cheat__cheat.b8098dc",
            run_root
            / "cheat__cheat.b8098dc"
            / "merged"
            / "v2_expansive_external_network_repair_4.json",
        ),
        (
            "psampaz__go-mod-outdated.bb79367",
            run_root
            / "psampaz__go-mod-outdated.bb79367"
            / "merged"
            / "v2_expansive_filtered_candidates_leakfix2.json",
        ),
        (
            "rs__jplot.2a54bcc",
            run_root / "rs__jplot.2a54bcc" / "merged" / "v2_expansive_binary_repair_1.json",
        ),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(COMPAT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PATH"] = "/usr/local/go1.21.13/bin:" + env.get("PATH", "")
    results: list[dict] = []

    for index, (instance, initial_cases) in enumerate(tasks):
        if index < args.start_index:
            continue
        cases = initial_cases
        if not cases.is_file():
            results.append({"instance_id": instance, "status": "missing_cases", "path": str(cases)})
            write(status_path, {"state": "blocked", "index": index, "results": results})
            return 2
        for attempt in range(1, 5):
            write(
                status_path,
                {
                    "state": "running",
                    "index": index,
                    "current_instance": instance,
                    "attempt": attempt,
                    "cases_json": str(cases),
                    "results": results,
                },
            )
            runner_log = (
                run_root / instance / "final_repair_logs" / f"runner_attempt_{attempt}.json"
            )
            invoke(
                [
                    sys.executable,
                    str(V2 / "run_go_v2_final_manual.py"),
                    instance,
                    "--run-root",
                    str(run_root),
                    "--tasks-root",
                    str(args.tasks_root),
                    "--work-root",
                    str(args.work_root),
                    "--xdist",
                    args.xdist,
                    "--resume-cases",
                    str(cases),
                ],
                env,
                runner_log,
            )
            summary_path = (
                run_root / "formal" / instance / "v2_expansive_final" / "pipeline_summary.json"
            )
            if not summary_path.is_file():
                continue
            summary = load(summary_path)
            if summary.get("status") == "passed":
                results.append(
                    {
                        "instance_id": instance,
                        "status": "passed",
                        "attempt": attempt,
                        "summary_path": str(summary_path),
                    }
                )
                break

            current_cases = Path(summary.get("cases_json") or cases)
            manifest = (
                run_root
                / "generated"
                / instance
                / "v2_expansive_final"
                / "oracle_tests"
                / "eval"
                / "generated_cli_manifest.json"
            )
            quality = (
                run_root
                / "generated"
                / instance
                / "v2_expansive_final"
                / "evaluation_quality_report.json"
            )
            repaired = (
                run_root
                / instance
                / "merged"
                / f"v2_expansive_post_quality_repair_{attempt}.json"
            )
            if manifest.is_file() and quality.is_file() and current_cases.is_file():
                filter_log = (
                    run_root / instance / "final_repair_logs" / f"filter_attempt_{attempt}.json"
                )
                invoke(
                    [
                        sys.executable,
                        str(V2 / "filter_candidates_from_capture_quality_v2.py"),
                        "--candidates",
                        str(current_cases),
                        "--capture-manifest",
                        str(manifest),
                        "--quality-report",
                        str(quality),
                        "--output",
                        str(repaired),
                    ],
                    env,
                    filter_log,
                )
                if repaired.is_file() and len(load(repaired).get("cases") or []) < len(
                    load(current_cases).get("cases") or []
                ):
                    cases = repaired
                    continue
            cases = current_cases
        else:
            results.append({"instance_id": instance, "status": "needs_repair"})
            write(status_path, {"state": "needs_repair", "index": index, "results": results})
            return 1
        if results[-1].get("status") != "passed":
            write(status_path, {"state": "needs_repair", "index": index, "results": results})
            return 1

    write(status_path, {"state": "passed", "results": results})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
