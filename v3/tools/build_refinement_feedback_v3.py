#!/usr/bin/env python3
"""Build PB-oracle-free refinement feedback from an earlier V3 run."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def fixture_text(manifest: Path, name: str | None, limit: int = 300) -> str:
    if not name:
        return ""
    matches = list((manifest.parent / "fixtures").glob(f"**/{name}"))
    if len(matches) != 1:
        return ""
    return matches[0].read_text(encoding="utf-8", errors="replace")[:limit]


def behavior_key(case: dict[str, Any]) -> tuple[Any, ...]:
    return (
        case.get("returncode"),
        case.get("timed_out"),
        case.get("stdout_sha256"),
        case.get("stderr_sha256"),
        json.dumps(case.get("observed_files") or {}, sort_keys=True),
    )


def nonempty_mapping(case: dict[str, Any], key: str) -> bool:
    return isinstance(case.get(key), dict) and bool(case[key])


def looks_like_runtime_path(value: str) -> bool:
    if value.startswith("-") or "://" in value or value.startswith("{http_url}"):
        return False
    return "/" in value or "\\" in value or bool(re.search(r"\.[A-Za-z0-9]{1,8}$", value))


def uncovered_source_blocks(generated: dict[str, Any], limit: int = 80) -> list[dict[str, Any]]:
    """Return bounded source excerpts for uncovered Go profile blocks.

    This is source/coverage feedback from our own instrumented build. It does
    not inspect PB tests. Other language adapters may add equivalent mappings
    later and simply return no blocks until then.
    """
    profile_value = generated.get("profile")
    if not profile_value:
        return []
    profile = Path(str(profile_value))
    if not profile.is_file():
        return []
    source_root = profile.parent.parent
    go_mod = source_root / "go.mod"
    module = ""
    if go_mod.is_file():
        match = re.search(
            r"(?m)^\s*module\s+(\S+)\s*$",
            go_mod.read_text(encoding="utf-8", errors="replace"),
        )
        module = match.group(1) if match else ""
    results: list[dict[str, Any]] = []
    pattern = re.compile(
        r"^(?P<file>.+):(?P<start>\d+)\.\d+,(?P<end>\d+)\.\d+\s+"
        r"(?P<statements>\d+)\s+(?P<count>\d+)$"
    )
    for raw in profile.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(raw)
        if not match or int(match.group("count")) != 0:
            continue
        covered_name = match.group("file")
        relative = covered_name
        if module and covered_name.startswith(module + "/"):
            relative = covered_name[len(module) + 1 :]
        source = source_root / relative
        if not source.is_file():
            matches = list(source_root.rglob(Path(relative).name))
            matches = [item for item in matches if item.as_posix().endswith(relative)]
            if len(matches) != 1:
                continue
            source = matches[0]
        lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
        start, end = int(match.group("start")), int(match.group("end"))
        excerpt_start = max(1, start - 2)
        excerpt_end = min(len(lines), max(end, start) + 2)
        excerpt = "\n".join(
            f"{line_no}: {lines[line_no - 1]}"
            for line_no in range(excerpt_start, excerpt_end + 1)
        )
        results.append(
            {
                "file": covered_name,
                "start_line": start,
                "end_line": end,
                "statements": int(match.group("statements")),
                "source_excerpt": excerpt[:2400],
            }
        )
        if len(results) >= limit:
            break
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("instance_id")
    parser.add_argument("--prior-run-root", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()

    repo = args.prior_run_root / args.instance_id
    pipeline = load(repo / "pipeline_summary.json")
    coverage = load(Path(str(pipeline["coverage"])))
    raw_manifest_path = (
        args.prior_run_root
        / "generated"
        / args.instance_id
        / "v3_raw"
        / "oracle_tests"
        / "eval"
        / "generated_cli_manifest.json"
    )
    raw_manifest = load(raw_manifest_path)
    candidate_path = repo / "merged" / "v3_quality_filtered.json"
    candidates = load(candidate_path).get("cases") or []
    by_name = {str(case.get("name")): case for case in candidates}

    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for case in raw_manifest.get("cases") or []:
        groups[behavior_key(case)].append(case)
    clusters = []
    for cases in sorted(groups.values(), key=len, reverse=True)[:12]:
        sample = cases[0]
        source = by_name.get(str(sample.get("name"))) or {}
        clusters.append(
            {
                "count": len(cases),
                "returncode": sample.get("returncode"),
                "timed_out": sample.get("timed_out"),
                "stdout_bytes": sample.get("stdout_bytes"),
                "stderr_bytes": sample.get("stderr_bytes"),
                "stdout_excerpt": fixture_text(
                    raw_manifest_path, sample.get("stdout_file")
                ),
                "stderr_excerpt": fixture_text(
                    raw_manifest_path, sample.get("stderr_file")
                ),
                "representative_name": sample.get("name"),
                "representative_args": source.get("args"),
                "representative_rationale": source.get("rationale"),
            }
        )

    success_anchors = []
    seen_success: set[tuple[Any, ...]] = set()
    for captured in raw_manifest.get("cases") or []:
        if captured.get("returncode") != 0 or captured.get("timed_out"):
            continue
        key = behavior_key(captured)
        if key in seen_success:
            continue
        seen_success.add(key)
        source = by_name.get(str(captured.get("name"))) or {}
        success_anchors.append(
            {
                "name": captured.get("name"),
                "args": source.get("args"),
                "stdin_excerpt": str(source.get("stdin") or "")[:240],
                "files": sorted((source.get("files") or {}).keys())[:20],
                "env": source.get("env") or {},
                "http": source.get("http") or {},
                "terminal": source.get("terminal") or {},
                "stdout_excerpt": fixture_text(
                    raw_manifest_path, captured.get("stdout_file"), 240
                ),
                "stderr_excerpt": fixture_text(
                    raw_manifest_path, captured.get("stderr_file"), 240
                ),
            }
        )
        if len(success_anchors) >= 24:
            break

    missing_runtime_paths: Counter[str] = Counter()
    for case in candidates:
        materialized = {
            str(path)
            for key in ("files", "binary_files", "executable_files", "repeat_files")
            for path in (case.get(key) or {})
        }
        for arg in case.get("args") or []:
            value = str(arg)
            if looks_like_runtime_path(value) and value not in materialized:
                missing_runtime_paths[value] += 1

    generated = coverage.get("generated_tests") or {}
    per_file = generated.get("per_file_line_coverage") or {}
    coverage_gaps = [
        {
            "file": name,
            "line_coverage_percent": item.get("line_coverage_percent"),
            "covered_executable_lines": item.get("covered_executable_lines"),
            "total_executable_lines": item.get("total_executable_lines"),
            "uncovered_executable_lines": (
                int(item.get("total_executable_lines") or 0)
                - int(item.get("covered_executable_lines") or 0)
            ),
        }
        for name, item in per_file.items()
    ]
    coverage_gaps.sort(
        key=lambda item: (
            item["line_coverage_percent"] is None,
            item["line_coverage_percent"] or 0,
            -item["uncovered_executable_lines"],
        )
    )

    fixture_usage = {
        key: sum(nonempty_mapping(case, key) for case in candidates)
        for key in ("files", "binary_files", "executable_files", "git", "http", "terminal")
    }
    fixture_usage.update(
        {
            "stdin": sum(bool(case.get("stdin")) for case in candidates),
            "env": sum(nonempty_mapping(case, "env") for case in candidates),
            "repeat_files": sum(nonempty_mapping(case, "repeat_files") for case in candidates),
        }
    )
    source_blocks = uncovered_source_blocks(generated)

    result = {
        "schema": "programbench_oracle_gym_v3_refinement_feedback",
        "instance_id": args.instance_id,
        "source_policy": (
            "Derived only from our prior V3 candidates, reference-binary captures, "
            "and source-mapped coverage. PB official oracle tests are absent."
        ),
        "prior_metrics": {
            "candidate_cases": len(candidates),
            "raw_captured_cases": len(raw_manifest.get("cases") or []),
            "line_coverage_percent": generated.get("line_coverage_percent"),
            "statement_coverage_percent": generated.get("statement_coverage_percent"),
        },
        "largest_behavior_clusters": clusters,
        "successful_anchor_recipes": success_anchors,
        "fixture_usage": fixture_usage,
        "likely_unmaterialized_runtime_paths": [
            {"path": path, "cases": count}
            for path, count in missing_runtime_paths.most_common(40)
        ],
        "source_coverage_gaps": coverage_gaps,
        "uncovered_source_blocks": source_blocks,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# PB-oracle-free V3 refinement feedback",
        "",
        f"Instance: `{args.instance_id}`",
        "",
        "This feedback contains only our earlier candidate executions and source coverage.",
        "It contains no target PB official tests, fixtures, names, or expected outputs.",
        "",
        "## Required use",
        "",
        "- Treat large identical clusters as reachability failures to diagnose and repair, not templates to repeat.",
        "- Establish at least one successful substantive anchor before expanding a flag/format/input matrix.",
        "- The runtime workspace is empty. Materialize every successful path argument with `files`, `repeat_files`, or another fixture provider.",
        "- Replace external HTTP dependencies with the `http` fixture and `{http_url}` placeholder.",
        "- Use `terminal` fixtures for code gated by TTY, terminal size, or graphics protocol detection.",
        "- Target low-covered first-party files through reachable CLI behavior; do not invent internal-only hooks.",
        "",
        "## Largest observed behavior clusters",
        "",
        "```json",
        json.dumps(clusters, indent=2, ensure_ascii=False),
        "```",
        "",
        "## Successful anchor recipes",
        "",
        "```json",
        json.dumps(success_anchors, indent=2, ensure_ascii=False),
        "```",
        "",
        "## Likely missing runtime paths",
        "",
        "```json",
        json.dumps(result["likely_unmaterialized_runtime_paths"], indent=2),
        "```",
        "",
        "## Source coverage gaps",
        "",
        "```json",
        json.dumps(coverage_gaps, indent=2),
        "```",
        "",
        "## Uncovered source blocks",
        "",
        "These bounded excerpts are mapped from our own instrumented coverage profile.",
        "",
        "```json",
        json.dumps(source_blocks, indent=2, ensure_ascii=False),
        "```",
    ]
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "instance_id": args.instance_id,
                "clusters": len(clusters),
                "successful_anchors": len(success_anchors),
                "coverage_files": len(coverage_gaps),
                "output": str(args.output_json),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
