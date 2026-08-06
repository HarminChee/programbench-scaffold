#!/usr/bin/env python3
"""Build an auditable human-plus-agent case annotation queue for V3."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def load_case_signals(path: Path | None) -> dict[str, dict[str, Any]]:
    if not path or not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    raw = payload.get("cases", payload) if isinstance(payload, dict) else payload
    if isinstance(raw, dict):
        return {str(name): dict(value) for name, value in raw.items() if isinstance(value, dict)}
    return {
        str(item.get("name")): dict(item)
        for item in raw or [] if isinstance(item, dict) and item.get("name")
    }


def load_human(path: Path | None) -> dict[str, dict[str, str]]:
    if not path or not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return {
            str(row.get("name")): {
                "decision": str(row.get("human_decision") or row.get("decision") or "").lower(),
                "notes": str(row.get("human_notes") or row.get("notes") or ""),
            }
            for row in csv.DictReader(handle) if row.get("name")
        }


def invocation(case: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "args", "env", "stdin_sha256", "files", "binary_files", "executable_files",
        "repeat_files", "file_modes", "git", "http", "terminal", "lifecycle",
        "isolate_home_tmp", "stdin_regular_file", "timeout", "observe_files",
    )
    return {key: case.get(key) for key in keys}


def behavior(case: dict[str, Any]) -> dict[str, Any]:
    return {
        key: case.get(key)
        for key in ("returncode", "stdout_sha256", "stderr_sha256", "timed_out", "observed_files", "interactions")
    }


def categories(case: dict[str, Any]) -> tuple[list[str], str, list[str]]:
    assertions = []
    if int(case.get("stdout_bytes") or 0):
        assertions.append("stdout")
    if int(case.get("stderr_bytes") or 0):
        assertions.append("stderr")
    if case.get("observed_files"):
        assertions.append("filesystem")
    if case.get("interactions"):
        assertions.append("interaction")
    if case.get("returncode") not in (None, 0):
        assertions.append("exit_error")
    error = "timeout" if case.get("timed_out") else "nonzero_exit" if case.get("returncode") else "success"
    fixtures = [
        key for key in (
            "files", "binary_files", "executable_files", "repeat_files", "git", "http",
            "terminal", "lifecycle", "env", "stdin_regular_file",
        ) if case.get(key)
    ]
    return assertions, error, fixtures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-manifest", type=Path, required=True)
    parser.add_argument("--case-signals", type=Path)
    parser.add_argument("--agent-annotations", type=Path)
    parser.add_argument("--human-annotations", type=Path)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.capture_manifest.read_text(encoding="utf-8-sig"))
    signals = load_case_signals(args.case_signals)
    agent = load_case_signals(args.agent_annotations)
    human = load_human(args.human_annotations)
    behavior_sizes = Counter(digest(behavior(case)) for case in manifest.get("cases") or [])
    rows = []
    for index, case in enumerate(manifest.get("cases") or []):
        name = str(case.get("name"))
        signal = signals.get(name, {})
        agent_item = agent.get(name, {})
        human_item = human.get(name, {})
        assertion_categories, error_category, fixture_categories = categories(case)
        source_blocks = (
            int(signal.get("new_source_blocks") or signal.get("source_block_gain") or 0)
            if signal.get("source_signal_valid", True) else 0
        )
        dynamic_edges = (
            int(signal.get("new_dynamic_edges") or signal.get("dynamic_edge_gain") or 0)
            if signal.get("dynamic_signal_valid", True) else 0
        )
        agent_decision = str(agent_item.get("decision") or agent_item.get("suggestion") or "review").lower()
        human_decision = str(human_item.get("decision") or "").lower()
        score = min(source_blocks, 100) * 4 + min(dynamic_edges, 100) * 3
        score += 8 * len(assertion_categories) + 4 * len(fixture_categories)
        score += 20 if error_category != "success" else 0
        score += 30 if agent_decision == "keep" else -30 if agent_decision == "reject" else 0
        score += 1_000 if human_decision == "keep" else -10_000 if human_decision == "reject" else 0
        row = {
            "name": name,
            "original_index": index,
            "strict_exact_key": digest((digest(invocation(case)), digest(behavior(case)))),
            "behavior_key": digest(behavior(case)),
            "behavior_group_size": behavior_sizes[digest(behavior(case))],
            "new_source_blocks": source_blocks,
            "new_dynamic_edges": dynamic_edges,
            "assertion_categories": assertion_categories,
            "error_category": error_category,
            "fixture_categories": fixture_categories,
            "agent_decision": agent_decision,
            "agent_notes": str(agent_item.get("notes") or ""),
            "human_decision": human_decision,
            "human_notes": str(human_item.get("notes") or ""),
            "priority_score": score,
        }
        rows.append(row)
    payload = {
        "schema": "programbench_oracle_gym_v3_case_annotations_v1",
        "policy": (
            "Human decisions override agent suggestions. Selection then prioritizes new source blocks, "
            "dynamic edges, assertion/error classes, and fixture diversity before applying behavior caps."
        ),
        "cases": {row["name"]: row for row in rows},
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    fields = [
        "name", "priority_score", "new_source_blocks", "new_dynamic_edges",
        "assertion_categories", "error_category", "fixture_categories",
        "behavior_group_size", "agent_decision", "agent_notes", "human_decision", "human_notes",
    ]
    with args.output_csv.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(row[key], ensure_ascii=False) if isinstance(row.get(key), list) else row.get(key, "")
                for key in fields
            })
    print(json.dumps({"cases": len(rows), "output_json": str(args.output_json), "output_csv": str(args.output_csv)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
