#!/usr/bin/env python3
"""Build a PB-oracle-free, language-aware V3 instance and behavior plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


IGNORE = {".git", ".hg", ".svn", "vendor", "node_modules", "target", "dist", "build", ".venv"}
LANGUAGE_SUFFIXES = {
    "go": {".go"},
    "rust": {".rs"},
    "c_cpp": {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"},
    "python": {".py"},
}
DOC_SUFFIXES = {".md", ".rst", ".txt", ".adoc"}
NATIVE_TEST_PATTERNS = {
    "go": lambda p: p.name.endswith("_test.go"),
    "rust": lambda p: p.suffix == ".rs" and ("test" in p.parts or p.name == "tests.rs"),
    "c_cpp": lambda p: "test" in "/".join(p.parts).lower(),
    "python": lambda p: p.name.startswith("test_") and p.suffix == ".py",
}

# These are evidence labels, not allow/deny lists. An agent may still explore a
# capability that is absent or weakly evidenced, and repetitions remain valid.
CAPABILITY_SIGNALS: dict[str, tuple[str, ...]] = {
    "command_surface": ("flag", "usage", "argument", "subcommand", "cobra", "urfave", "clap", "getopt"),
    "stdin_and_streams": ("stdin", "stdout", "stderr", "scanner", "bufio", "pipe", "stream"),
    "structured_data": ("json", "yaml", "toml", "xml", "csv", "marshal", "unmarshal", "decode", "encode"),
    "filesystem_state": ("readfile", "writefile", "open(", "filepath", "directory", "walk", "mkdir", "symlink"),
    "configuration_state": ("config", "getenv", "environment", "xdg", "homedir", "settings"),
    "network_protocol": ("http", "https", "url", "request", "response", "header", "cookie", "redirect", "socket"),
    "repository_state": ("git", "repository", "commit", "branch", "worktree", "module"),
    "terminal_interaction": ("terminal", "tty", "pty", "prompt", "interactive", "screen"),
    "source_language_analysis": ("lexer", "syntax", "language", "token", "complexity", "extension"),
    "query_processing": ("query", "select", "filter", "sort", "join", "expression"),
    "formatting_rendering": ("format", "formatter", "render", "color", "ansi", "html", "markdown", "template"),
    "concurrency_timing": ("goroutine", "thread", "async", "timeout", "interval", "timer", "context"),
}
FIXTURE_HINTS = {
    "stdin_and_streams": ["text_stream", "binary_stream"],
    "structured_data": ["structured_document_corpus"],
    "filesystem_state": ["filesystem_tree"],
    "configuration_state": ["isolated_home", "config_files", "environment"],
    "network_protocol": ["loopback_http"],
    "repository_state": ["local_git_repository"],
    "terminal_interaction": ["pty_script"],
    "source_language_analysis": ["source_corpus"],
    "query_processing": ["tabular_and_nested_data"],
    "formatting_rendering": ["rendering_corpus"],
    "concurrency_timing": ["bounded_clock_inputs"],
}


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and not any(part in IGNORE for part in path.relative_to(root).parts)
    )


def read(path: Path, limit: int = 350_000) -> str:
    try:
        return path.read_bytes()[:limit].decode("utf-8", "replace")
    except OSError:
        return ""


def detect_language(root: Path, all_files: list[Path]) -> str:
    # Build manifests are stronger evidence than raw suffix counts because
    # repositories commonly ship large cross-language example/test corpora.
    if (root / "go.mod").is_file():
        return "go"
    if (root / "Cargo.toml").is_file():
        return "rust"
    if (root / "CMakeLists.txt").is_file():
        return "c_cpp"
    counts = {
        language: sum(path.suffix.lower() in suffixes for path in all_files)
        for language, suffixes in LANGUAGE_SUFFIXES.items()
    }
    return max(counts, key=counts.get) if max(counts.values(), default=0) else "unknown"


def build_system(root: Path) -> tuple[list[str], list[str]]:
    build, tests = [], []
    if (root / "go.mod").is_file():
        build.append("go build ./...")
        tests.append("go test ./...")
    if (root / "Cargo.toml").is_file():
        build.append("cargo build --release")
        tests.append("cargo test")
    if (root / "CMakeLists.txt").is_file():
        build.append("cmake -S . -B build && cmake --build build")
        tests.append("ctest --test-dir build")
    if (root / "Makefile").is_file():
        build.append("make")
        tests.append("make test")
    return list(dict.fromkeys(build)), list(dict.fromkeys(tests))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("instance_id")
    ap.add_argument("--source-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()
    root = args.source_dir.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    all_files = files(root)
    language = detect_language(root, all_files)
    source_suffixes = LANGUAGE_SUFFIXES.get(language, set().union(*LANGUAGE_SUFFIXES.values()))
    source_files = [p for p in all_files if p.suffix.lower() in source_suffixes]
    docs = [p for p in all_files if p.suffix.lower() in DOC_SUFFIXES or p.name.lower().startswith("readme")]
    test_predicate = NATIVE_TEST_PATTERNS.get(language, lambda _p: False)
    native_tests = [p for p in source_files if test_predicate(p.relative_to(root))]
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    flags: Counter[str] = Counter()
    commands: Counter[str] = Counter()
    for path in [*source_files, *docs, *native_tests]:
        rel = path.relative_to(root).as_posix()
        body = read(path)
        lower = body.lower()
        for flag in re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*", lower):
            flags[flag] += 1
        for command in re.findall(r"\b(?:command|subcommand|name)\s*[:=(]\s*[\"']([a-z][a-z0-9_-]+)", lower):
            commands[command] += 1
        for capability, signals in CAPABILITY_SIGNALS.items():
            matched = sorted({signal for signal in signals if signal in lower})
            if matched:
                evidence[capability].append({"path": rel, "signals": matched[:8]})
    build_commands, native_test_commands = build_system(root)
    capabilities = []
    for capability, items in sorted(evidence.items()):
        capabilities.append({
            "id": capability,
            "evidence": items[:40],
            "confidence": "high" if len(items) >= 4 else "medium" if len(items) >= 2 else "low",
            "fixture_hints": FIXTURE_HINTS.get(capability, []),
            "scenario_kinds": ["normal", "boundary", "error", "state_interaction"],
        })
    rows = []
    for capability in capabilities:
        for kind in capability["scenario_kinds"]:
            rows.append({
                "id": f"{capability['id']}__{kind}",
                "capability": capability["id"],
                "scenario_kind": kind,
                "evidence": capability["evidence"],
                "fixture_hints": capability["fixture_hints"],
            })
    # Cross-capability rows create state/input/output combinations without
    # making any category mandatory or forbidden.
    for left, right in zip(capabilities, capabilities[1:]):
        rows.append({
            "id": f"cross__{left['id']}__{right['id']}",
            "capability": "cross_capability",
            "scenario_kind": "interaction",
            "evidence": [*left["evidence"][:4], *right["evidence"][:4]],
            "fixture_hints": sorted(set(left["fixture_hints"] + right["fixture_hints"])),
        })
    fixture_ids = sorted({fixture for row in rows for fixture in row["fixture_hints"]})
    build_digest = hashlib.sha256(
        json.dumps({"capabilities": capabilities, "rows": rows}, sort_keys=True).encode()
    ).hexdigest()
    out = args.output_dir.expanduser().resolve()
    dump(out / "instance_spec.json", {
        "schema": "programbench_oracle_gym_v3_instance",
        "instance_id": args.instance_id,
        "source_dir": str(root),
        "language": language,
        "build_commands": build_commands,
        "native_test_commands": native_test_commands,
        "reference_artifact_policy": "PB cleanroom when present; otherwise reproducible pinned-source container build",
        "coverage_adapter": language,
    })
    dump(out / "repository_inventory.json", {
        "language": language,
        "source_files": [p.relative_to(root).as_posix() for p in source_files],
        "documentation_files": [p.relative_to(root).as_posix() for p in docs],
        "native_test_files": [p.relative_to(root).as_posix() for p in native_tests],
        "detected_flags": [name for name, _ in flags.most_common(200)],
        "detected_commands": [name for name, _ in commands.most_common(100)],
    })
    dump(out / "capability_graph.json", {
        "schema": "programbench_oracle_gym_v3_capability_graph",
        "source_policy": "source, docs, and native tests only; target PB official oracle tests forbidden",
        "capabilities": capabilities,
        "plan_sha256": build_digest,
    })
    dump(out / "scenario_matrix.json", {"schema": "programbench_oracle_gym_v3_matrix", "rows": rows})
    dump(out / "fixture_plan.json", {
        "schema": "programbench_oracle_gym_v3_fixture_plan",
        "fixtures": [
            {
                "id": fixture,
                "provider": "controller_or_agent_materialized",
                "requirements": ["deterministic", "isolated", "hash_recorded"],
            }
            for fixture in fixture_ids
        ],
    })
    dump(out / "generation_contract.json", {
        "schema": "programbench_oracle_gym_v3_generation_contract",
        "retain_repetitions": "only meaningful input, fixture, state, or operation variations",
        "reject_empty_or_noop_cases": True,
        "collapse_strict_exact_execution_duplicates": True,
        "cap_identical_behavior_groups": 12,
        "exploratory_categories_allowed": True,
        "gold_capture_required": True,
        "target_pb_oracles_forbidden": True,
        "expected_outputs_must_not_be_invented": True,
        "dynamic_path_metrics_are_secondary": True,
        "coverage_stop_is_absolute_not_pb_relative": True,
    })
    print(json.dumps({
        "instance_id": args.instance_id,
        "language": language,
        "capabilities": len(capabilities),
        "matrix_rows": len(rows),
        "fixtures": len(fixture_ids),
        "output_dir": str(out),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
