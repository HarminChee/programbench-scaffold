#!/usr/bin/env python3
"""Parse and validate a V3 model response without deduplicating valid cases."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SUPPORTED = {
    "args", "argv0", "stdin", "env", "files", "binary_files", "executable_files",
    "file_modes", "git", "http", "terminal", "repeat_files", "observe_files",
    "isolate_home_tmp", "stdin_regular_file", "timeout_seconds", "stdout_mode",
    "normalize_go_log_prefix", "normalize_benchmark_output",
    "normalize_epoch_numbers", "normalize_tui_metrics",
}


def extract(text: str) -> dict:
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    body = fenced.group(1) if fenced else text[text.find("{") : text.rfind("}") + 1]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        # High-volume responses occasionally contain one malformed object
        # while dozens of earlier/later objects remain valid. Recover balanced
        # object slices from the cases array and audit the partial recovery.
        marker = re.search(r'"cases"\s*:\s*\[', body)
        if not marker:
            raise
        recovered, start = [], marker.end()
        depth, in_string, escaped, object_start = 0, False, False, None
        for index in range(start, len(body)):
            char = body[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                if depth == 0:
                    object_start = index
                depth += 1
            elif char == "}" and depth:
                depth -= 1
                if depth == 0 and object_start is not None:
                    try:
                        recovered.append(json.loads(body[object_start : index + 1]))
                    except json.JSONDecodeError:
                        pass
                    object_start = None
            elif char == "]" and depth == 0:
                break
        if not recovered:
            raise
        return {"cases": recovered, "_partial_recovery": True}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent-output", type=Path, required=True)
    ap.add_argument("--matrix-rows", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    payload = extract(args.agent_output.read_text(encoding="utf-8-sig"))
    matrix = json.loads(args.matrix_rows.read_text())
    allowed = {row["id"] for row in matrix["rows"]} | ({"exploratory"} if matrix.get("allow_exploratory") else set())
    cases, errors = [], []
    for index, raw in enumerate(payload.get("cases", [])):
        if not isinstance(raw, dict):
            errors.append(f"{index}: not an object")
            continue
        area = str(raw.get("area") or "exploratory")
        argv = raw.get("args")
        stdin = raw.get("stdin")
        if area not in allowed:
            area = "exploratory"
        if not isinstance(argv, list) or not all(isinstance(v, str) for v in argv):
            errors.append(f"{index}: args must be a string list")
            continue
        # Models commonly express "no standard input" as JSON null or omit the
        # field. Both are equivalent to an empty byte stream in the fixture DSL.
        # Continue rejecting other non-string values so malformed structured
        # inputs do not silently become executable cases.
        if stdin is None:
            stdin = ""
            raw = dict(raw)
            raw["stdin"] = stdin
        if not isinstance(stdin, str):
            errors.append(f"{index}: stdin must be a string")
            continue
        case = {
            "name": str(raw.get("name") or f"v3_{index:04d}")[:150],
            "area": area,
            "origin": "v3_topic_agent",
            "rationale": str(raw.get("rationale") or "")[:3000],
        }
        for key in SUPPORTED:
            if key in raw:
                case[key] = raw[key]
        cases.append(case)
    out = {
        "profile": "programbench_oracle_gym_v3",
        "source_policy": "target PB oracle forbidden",
        "retain_repetitions": True,
        "candidate_case_count": len(cases),
        "cases": cases,
        "errors": errors,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"valid_cases": len(cases), "errors": len(errors), "output": str(args.output)}, indent=2))
    return 0 if cases else 1


if __name__ == "__main__":
    raise SystemExit(main())
