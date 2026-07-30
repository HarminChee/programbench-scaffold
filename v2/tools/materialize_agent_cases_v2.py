#!/usr/bin/env python3
"""Parse a V2 agent batch, preserve supported fixture DSL fields, and validate matrix labels."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def extract_json(text: str) -> dict:
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = match.group(1) if match else text[text.find("{") : text.rfind("}") + 1]
    return json.loads(candidate)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-output", type=Path, required=True)
    parser.add_argument("--matrix-rows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = extract_json(args.agent_output.read_text(encoding="utf-8"))
    allowed = {row["id"] for row in json.loads(args.matrix_rows.read_text(encoding="utf-8"))["rows"]}
    supported = {
        "args", "stdin", "env", "files", "executable_files", "binary_files", "repeat_files", "observe_files", "file_modes",
        "git", "http", "terminal", "stdout_mode", "isolate_home_tmp", "stdin_regular_file", "timeout_seconds",
        "normalize_go_log_prefix", "normalize_epoch_numbers", "normalize_tui_metrics",
    }
    cases = []
    errors = []
    for index, raw in enumerate(payload.get("cases") or []):
        if not isinstance(raw, dict):
            errors.append(f"case {index}: not an object")
            continue
        area = str(raw.get("area") or "")
        rationale = str(raw.get("rationale") or "")
        args_value = raw.get("args")
        stdin_value = raw.get("stdin")
        if "cli" in raw or "command" in raw or "assert" in raw:
            errors.append(f"case {index}: nested cli/command/assert schema is forbidden")
            continue
        if area not in allowed:
            errors.append(f"case {index}: area not in batch matrix: {area}")
            continue
        if not rationale or not isinstance(args_value, list) or not all(isinstance(item, str) for item in args_value):
            errors.append(f"case {index}: missing rationale or mandatory flat args")
            continue
        if not isinstance(stdin_value, str):
            errors.append(f"case {index}: mandatory flat stdin must be a string")
            continue
        case = {"name": str(raw.get("name") or f"v2_{area}_{index:03d}")[:120], "area": area, "origin": "v2_topic_agent", "rationale": rationale[:2000]}
        for key in supported:
            if key in raw:
                case[key] = raw[key]
        cases.append(case)
    output = {"profile": "programbench_oracle_gym_v2", "source_policy": "target PB oracle forbidden", "candidate_case_count": len(cases), "cases": cases, "errors": errors}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"valid_cases": len(cases), "errors": errors, "output": str(args.output)}, indent=2))
    # Preserve every valid flat-DSL case even if the model appended a few
    # malformed rows.  The caller records validation errors in the manifest;
    # only a batch with no usable case is a hard materialization failure.
    return 0 if cases else 1


if __name__ == "__main__":
    raise SystemExit(main())
