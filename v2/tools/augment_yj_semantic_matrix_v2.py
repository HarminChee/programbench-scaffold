#!/usr/bin/env python3
"""Add the source-derived yj format/flag/corpus matrix to V2 agent candidates.

The deterministic yj corpus already lives in the scaffold capture engine.  It
was authored from yj source, documentation, and native tests; it is not copied
from ProgramBench target Oracle tests.  V2 treats it as the repo-specific
fixture planner that complements topic-agent proposals.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


METADATA = {"name", "area", "origin", "rationale", "source_batch"}


def key(case: dict) -> str:
    executable = {k: v for k, v in case.items() if k not in METADATA}
    encoded = json.dumps(executable, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def load_yj_cases(scaffold_root: Path) -> list[dict]:
    module_path = scaffold_root / "tools" / "programbench_generate_cli_oracle_bundle.py"
    spec = importlib.util.spec_from_file_location("programbench_capture_profiles", module_path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.yj_cases()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-candidates", type=Path, required=True)
    parser.add_argument("--scaffold-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.agent_candidates.read_text(encoding="utf-8"))
    combined: list[dict] = []
    seen: set[str] = set()
    removed: list[dict] = []
    sanitized_program_names = 0
    for raw in payload.get("cases") or []:
        case = {k: v for k, v in raw.items() if k != "source_batch"}
        argv = list(case.get("args") or [])
        if argv and argv[0] == "yj":
            case["args"] = argv[1:]
            sanitized_program_names += 1
        signature = key(case)
        if signature in seen:
            removed.append({"name": case.get("name"), "reason": "duplicate_after_argv_sanitization"})
            continue
        seen.add(signature)
        case["origin"] = "v2_topic_agent"
        combined.append(case)

    matrix_added = 0
    for raw in load_yj_cases(args.scaffold_root):
        case = dict(raw)
        signature = key(case)
        if signature in seen:
            removed.append({"name": case.get("name"), "reason": "matrix_invocation_already_present"})
            continue
        seen.add(signature)
        case["origin"] = "v2_yj_source_fixture_matrix"
        case["rationale"] = f"Repo-specific source-derived yj fixture matrix: {case.get('area', 'behavior')}."
        combined.append(case)
        matrix_added += 1

    result = {
        "profile": "programbench_oracle_gym_v2_yj",
        "source_policy": "target PB oracle forbidden",
        "agent_input_cases": payload.get("input_case_count", payload.get("candidate_case_count")),
        "agent_unique_cases": payload.get("candidate_case_count"),
        "sanitized_program_name_count": sanitized_program_names,
        "repo_matrix_cases_added": matrix_added,
        "candidate_case_count": len(combined),
        "removed_cases": removed,
        "cases": combined,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in (
        "agent_unique_cases", "sanitized_program_name_count",
        "repo_matrix_cases_added", "candidate_case_count"
    )}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
