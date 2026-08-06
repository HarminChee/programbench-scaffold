#!/usr/bin/env python3
"""Build a target-oracle-free V2 behavior map, matrix, and fixture catalog.

The planner only reads the pinned target source tree, documentation, and native
tests.  It deliberately has no ProgramBench test-blob or official-oracle input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


IGNORE_PARTS = {".git", "vendor", "node_modules", "dist", "build", ".venv"}
SOURCE_SUFFIXES = {".go"}
DOC_SUFFIXES = {".md", ".rst", ".txt"}


FEATURES: dict[str, dict[str, Any]] = {
    "cli_and_argument_parsing": {
        "signals": ["flag", "argument", "cobra", "urfave", "usage", "help"],
        "fixtures": [],
        "dimensions": ["valid_flag", "unknown_flag", "missing_value", "flag_combination"],
    },
    "structured_input_and_conversion": {
        "signals": ["json", "yaml", "toml", "hcl", "csv", "xml", "decode", "encode", "marshal", "unmarshal"],
        "fixtures": ["structured_documents"],
        "dimensions": ["format", "validity", "nesting", "special_values", "output_mode"],
    },
    "filesystem_and_directory_state": {
        "signals": ["os.open", "readfile", "writefile", "filepath", "walk", "mkdir", "chmod", "symlink", "directory"],
        "fixtures": ["filesystem_tree"],
        "dimensions": ["tree_shape", "file_content", "permission_mode", "missing_path", "operation"],
    },
    "configuration_and_environment": {
        "signals": ["config", "environment", "getenv", "home", "xdg", "settings"],
        "fixtures": ["config_files", "environment"],
        "dimensions": ["config_state", "env_override", "missing_field", "invalid_value"],
    },
    "network_and_protocol": {
        "signals": ["http", "url", "request", "response", "cookie", "header", "proxy", "redirect"],
        "fixtures": ["loopback_http"],
        "dimensions": ["method", "status", "body_encoding", "headers", "redirect"],
    },
    "terminal_and_interactive": {
        "signals": ["tty", "terminal", "termui", "tcell", "pty", "prompt", "interactive"],
        "fixtures": ["pty_script"],
        "dimensions": ["terminal_size", "input_sequence", "initial_screen", "error_exit"],
    },
    "source_analysis_and_language_detection": {
        "signals": ["lexer", "language", "syntax", "token", "parse", "complexity", "line", "extension"],
        "fixtures": ["source_corpus"],
        "dimensions": ["language", "source_shape", "size", "filter", "output_format"],
    },
    "query_and_data_processing": {
        "signals": ["query", "sql", "sqlite", "select", "join", "cache", "schema"],
        "fixtures": ["tabular_data"],
        "dimensions": ["input_format", "query_family", "result_shape", "error_kind", "cache_state"],
    },
    "module_or_dependency_state": {
        "signals": ["module", "go.mod", "replace", "version", "dependency", "timestamp"],
        "fixtures": ["module_metadata"],
        "dimensions": ["dependency_kind", "version_relation", "replacement", "metadata_validity"],
    },
    "git_and_repository_state": {
        "signals": ["git", "repository", "commit", "branch", "worktree"],
        "fixtures": ["git_repository"],
        "dimensions": ["history", "branch_state", "dirty_state", "operation"],
    },
    "output_formatting_and_rendering": {
        "signals": ["html", "markdown", "format", "color", "ansi", "render", "output"],
        "fixtures": ["structured_documents"],
        "dimensions": ["formatter", "color_mode", "content_shape", "output_destination"],
    },
}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def relevant_files(source_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in source_dir.rglob("*"):
        if not path.is_file() or any(part in IGNORE_PARTS for part in path.parts):
            continue
        if path.suffix.lower() in SOURCE_SUFFIXES | DOC_SUFFIXES or path.name.endswith("_test.go"):
            files.append(path)
    return sorted(files)


def text(path: Path, limit: int = 300_000) -> str:
    try:
        return path.read_bytes()[:limit].decode("utf-8", "replace").lower()
    except OSError:
        return ""


def classify(source_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    evidence: dict[str, list[str]] = defaultdict(list)
    flags: Counter[str] = Counter()
    native_tests: list[str] = []
    docs: list[str] = []
    modules: Counter[str] = Counter()
    for path in relevant_files(source_dir):
        rel = str(path.relative_to(source_dir)).replace("\\", "/")
        body = text(path)
        if path.name.endswith("_test.go"):
            native_tests.append(rel)
        if path.suffix.lower() in DOC_SUFFIXES or path.name.lower().startswith("readme"):
            docs.append(rel)
        if path.suffix.lower() == ".go":
            modules[str(path.relative_to(source_dir).parent).replace("\\", "/")] += 1
        for flag in re.findall(r"(?<![A-Za-z0-9])--[a-z][a-z0-9-]*", body):
            flags[flag] += 1
        for name, spec in FEATURES.items():
            if any(token in body for token in spec["signals"]):
                evidence[name].append(rel)

    families: list[dict[str, Any]] = []
    for name, spec in FEATURES.items():
        hits = sorted(set(evidence[name]))
        if not hits:
            continue
        families.append(
            {
                "id": name,
                "source_evidence": hits[:30],
                "fixture_requirements": spec["fixtures"],
                "dimensions": spec["dimensions"],
                "minimum_scenarios": ["normal", "boundary", "error"],
            }
        )
    inventory = {
        "source_dir": str(source_dir),
        "source_file_count": sum(1 for p in relevant_files(source_dir) if p.suffix == ".go"),
        "native_test_files": sorted(native_tests),
        "documentation_files": sorted(docs),
        "top_level_modules": modules.most_common(30),
        "detected_flags": [flag for flag, _ in flags.most_common(120)],
    }
    return families, inventory


def make_matrix(families: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for family in families:
        dimensions = family["dimensions"]
        for scenario in family["minimum_scenarios"]:
            rows.append(
                {
                    "id": f"{family['id']}__{scenario}",
                    "family": family["id"],
                    "scenario_kind": scenario,
                    "dimensions": dimensions,
                    "fixture_requirements": family["fixture_requirements"],
                    "source_evidence": family["source_evidence"],
                    "required_before_coverage_refinement": True,
                }
            )
    return rows


def make_fixture_catalog(families: list[dict[str, Any]]) -> list[dict[str, Any]]:
    needers: dict[str, list[str]] = defaultdict(list)
    for family in families:
        for fixture in family["fixture_requirements"]:
            needers[fixture].append(family["id"])
    catalog: list[dict[str, Any]] = []
    for fixture, needed_by in sorted(needers.items()):
        catalog.append(
            {
                "id": fixture,
                "needed_by": sorted(needed_by),
                "policy": "controller-created, deterministic, local-only, hash-recorded",
                "forbidden": ["external network", "host-specific paths", "target ProgramBench oracle material"],
            }
        )
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance_id")
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    source_dir = args.source_dir.expanduser().resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(source_dir)
    families, inventory = classify(source_dir)
    matrix = make_matrix(families)
    fixtures = make_fixture_catalog(families)
    digest = hashlib.sha256(json.dumps({"families": families, "matrix": matrix}, sort_keys=True).encode()).hexdigest()
    behavior_map = {
        "schema": "programbench_oracle_gym_v2_behavior_map",
        "instance_id": args.instance_id,
        "source_policy": "source_docs_native_tests_only; target ProgramBench official oracles forbidden",
        "inventory": inventory,
        "families": families,
        "plan_sha256": digest,
    }
    output = args.output_dir.expanduser().resolve()
    write_json(output / "behavior_map.json", behavior_map)
    write_json(output / "scenario_matrix.json", {"schema": "programbench_oracle_gym_v2_scenario_matrix", "rows": matrix})
    write_json(output / "fixture_catalog.json", {"schema": "programbench_oracle_gym_v2_fixture_catalog", "fixtures": fixtures})
    write_json(output / "generation_contract.json", {
        "schema": "programbench_oracle_gym_v2_generation_contract",
        "instance_id": args.instance_id,
        "required_inputs": ["behavior_map.json", "scenario_matrix.json", "fixture_catalog.json"],
        "generation_rule": "generate separate topic batches; every case must name a family, scenario matrix row, fixture IDs, and source evidence",
        "quality_rule": "capture on gold binary, then determinism, strong assertion, dummy rejection, three-binary consistency, source-leak, and semantic-novelty gates",
        "coverage_rule": "coverage is reported after matrix completion and used only to investigate reachable public behavior gaps",
    })
    print(json.dumps({"instance_id": args.instance_id, "families": len(families), "matrix_rows": len(matrix), "fixtures": len(fixtures), "output_dir": str(output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
