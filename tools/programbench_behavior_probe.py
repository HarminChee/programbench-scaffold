#!/usr/bin/env python3
"""Black-box behavior probe scaffold for ProgramBench-style reconstruction tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_json_list(value: str, field: str) -> list[str]:
    payload = json.loads(value)
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise ValueError(f"{field} must be a JSON string list")
    return payload


def resolve_existing_command_paths(command: list[str], base_dir: Path) -> list[str]:
    resolved = []
    for item in command:
        path = Path(item)
        if path.is_absolute() or not any(sep in item for sep in ("/", "\\")):
            resolved.append(item)
            continue
        candidate = base_dir / path
        resolved.append(str(candidate.resolve()) if candidate.exists() else item)
    return resolved


def load_case(value: str) -> dict[str, Any]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("--case must be a JSON object")
    payload.setdefault("name", f"case_{abs(hash(value))}")
    payload.setdefault("args", [])
    payload.setdefault("stdin", "")
    payload.setdefault("files", {})
    return payload


def load_cases_file(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("--cases-file must contain a JSON list")
    cases = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("--cases-file entries must be JSON objects")
        item.setdefault("name", f"case_{len(cases)}")
        item.setdefault("args", [])
        item.setdefault("stdin", "")
        item.setdefault("files", {})
        cases.append(item)
    return cases


def write_input_files(workdir: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = workdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def file_snapshot(workdir: Path) -> dict[str, dict[str, Any]]:
    snapshot = {}
    for path in sorted(workdir.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(workdir))
            raw = path.read_bytes()
            snapshot[rel] = {"bytes": len(raw), "sha256": sha256_bytes(raw)}
    return snapshot


def run_case(command: list[str], case: dict[str, Any], timeout: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="pb_probe_") as tmp:
        workdir = Path(tmp)
        write_input_files(workdir, case["files"])
        start = time.time()
        proc = subprocess.run(
            command + list(case["args"]),
            cwd=workdir,
            input=case["stdin"].encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        elapsed = round(time.time() - start, 4)
        return {
            "command": command + list(case["args"]),
            "returncode": proc.returncode,
            "stdout": proc.stdout.decode("utf-8", errors="replace"),
            "stderr": proc.stderr.decode("utf-8", errors="replace"),
            "stdout_sha256": sha256_bytes(proc.stdout),
            "stderr_sha256": sha256_bytes(proc.stderr),
            "elapsed_s": elapsed,
            "files_after": file_snapshot(workdir),
        }


def compare(reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "returncode": reference["returncode"] == candidate["returncode"],
        "stdout": reference["stdout"] == candidate["stdout"],
        "stderr": reference["stderr"] == candidate["stderr"],
        "files_after": reference["files_after"] == candidate["files_after"],
    }
    return {
        "exact_match": all(checks.values()),
        "checks": checks,
        "partial_reward": sum(checks.values()) / len(checks),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-command", required=True, help='JSON list, e.g. ["/usr/bin/wc"]')
    parser.add_argument("--candidate-command", help='Optional JSON list, e.g. ["python3", "solution.py"]')
    parser.add_argument("--case", action="append", default=[], help="JSON object with name,args,stdin,files")
    parser.add_argument("--cases-file", type=Path, help="JSON list of case objects")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    reference_command = resolve_existing_command_paths(
        load_json_list(args.reference_command, "--reference-command"), Path.cwd()
    )
    candidate_command = (
        resolve_existing_command_paths(load_json_list(args.candidate_command, "--candidate-command"), Path.cwd())
        if args.candidate_command
        else None
    )
    cases = []
    if args.cases_file:
        cases.extend(load_cases_file(args.cases_file))
    cases.extend(load_case(raw) for raw in args.case)
    if not cases:
        raise ValueError("Provide at least one --case or --cases-file")

    results = []
    for case in cases:
        reference = run_case(reference_command, case, args.timeout)
        item = {"case": case, "reference": reference}
        if candidate_command:
            candidate = run_case(candidate_command, case, args.timeout)
            item["candidate"] = candidate
            item["comparison"] = compare(reference, candidate)
        results.append(item)

    summary = {
        "cases": len(results),
        "candidate_evaluated": candidate_command is not None,
        "exact_matches": sum(1 for item in results if item.get("comparison", {}).get("exact_match")),
        "mean_partial_reward": (
            sum(item.get("comparison", {}).get("partial_reward", 0.0) for item in results) / len(results)
            if candidate_command
            else None
        ),
    }
    payload = {"summary": summary, "results": results}
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
