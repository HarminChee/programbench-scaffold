#!/usr/bin/env python3
"""PB-style structural assertion linter for generated pytest oracle tests."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any


HIGH_RULES = {
    "no_assertions",
    "sole_returncode",
    "returncode_in_list",
    "assertion_disjunction",
    "pass_body",
    "try_except_swallow",
    "all_assertions_weak",
    "short_substring",
    "file_exists_no_content",
}
MEDIUM_RULES = {"weak_sole_assertion", "only_negative_assertions"}


def node_text(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return node.__class__.__name__


def is_returncode_assert(assert_node: ast.Assert) -> bool:
    return "returncode" in node_text(assert_node.test)


def is_strong_output_assert(assert_node: ast.Assert) -> bool:
    text = node_text(assert_node.test)
    if "stdout" not in text and "stderr" not in text:
        return False
    return "==" in text or "!=" in text


def is_short_substring(assert_node: ast.Assert) -> bool:
    test = assert_node.test
    if not isinstance(test, ast.Compare):
        return False
    if not any(isinstance(op, (ast.In, ast.NotIn)) for op in test.ops):
        return False
    values: list[ast.AST] = [test.left, *test.comparators]
    for value in values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str) and len(value.value) < 5:
            return True
        if isinstance(value, ast.Constant) and isinstance(value.value, bytes) and len(value.value) < 5:
            return True
    return False


def is_returncode_in_list(assert_node: ast.Assert) -> bool:
    test = assert_node.test
    if not isinstance(test, ast.Compare):
        return False
    if not any(isinstance(op, ast.In) for op in test.ops):
        return False
    return "returncode" in node_text(test)


def is_file_exists_assert(assert_node: ast.Assert) -> bool:
    text = node_text(assert_node.test)
    return ".exists()" in text or "is_file()" in text or "is_dir()" in text


def function_issues(fn: ast.FunctionDef, relpath: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    assertions = [node for node in ast.walk(fn) if isinstance(node, ast.Assert)]
    if not assertions:
        issues.append({"rule": "no_assertions", "severity": "high", "path": relpath, "line": fn.lineno, "function": fn.name})

    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            if isinstance(node.test, ast.BoolOp) and isinstance(node.test.op, ast.Or):
                issues.append(
                    {"rule": "assertion_disjunction", "severity": "high", "path": relpath, "line": node.lineno, "function": fn.name}
                )
            if is_returncode_in_list(node):
                issues.append(
                    {"rule": "returncode_in_list", "severity": "high", "path": relpath, "line": node.lineno, "function": fn.name}
                )
            if is_short_substring(node):
                issues.append({"rule": "short_substring", "severity": "high", "path": relpath, "line": node.lineno, "function": fn.name})
        elif isinstance(node, ast.Pass):
            issues.append({"rule": "pass_body", "severity": "high", "path": relpath, "line": node.lineno, "function": fn.name})
        elif isinstance(node, ast.ExceptHandler):
            if not node.body or all(isinstance(item, ast.Pass) for item in node.body):
                issues.append(
                    {"rule": "try_except_swallow", "severity": "high", "path": relpath, "line": node.lineno, "function": fn.name}
                )

    if len(assertions) == 1:
        only = assertions[0]
        if is_returncode_assert(only):
            issues.append({"rule": "sole_returncode", "severity": "high", "path": relpath, "line": only.lineno, "function": fn.name})
        elif is_file_exists_assert(only):
            issues.append(
                {"rule": "file_exists_no_content", "severity": "high", "path": relpath, "line": only.lineno, "function": fn.name}
            )
        elif not is_strong_output_assert(only):
            issues.append({"rule": "weak_sole_assertion", "severity": "medium", "path": relpath, "line": only.lineno, "function": fn.name})

    if assertions and all(is_returncode_assert(item) or is_file_exists_assert(item) for item in assertions):
        issues.append({"rule": "all_assertions_weak", "severity": "high", "path": relpath, "line": fn.lineno, "function": fn.name})

    return issues


def lint_file(path: Path, root: Path) -> dict[str, Any]:
    relpath = str(path.relative_to(root))
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return {
            "path": relpath,
            "parse_error": str(exc),
            "issues": [{"rule": "syntax_error", "severity": "high", "path": relpath, "line": exc.lineno or 1}],
        }
    issues: list[dict[str, Any]] = []
    test_functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and (node.name.startswith("test_") or any("pytest" in node_text(d) for d in node.decorator_list))
    ]
    for fn in test_functions:
        issues.extend(function_issues(fn, relpath))
    return {"path": relpath, "test_function_count": len(test_functions), "issues": issues}


def lint_oracle_root(oracle_root: Path) -> dict[str, Any]:
    eval_root = oracle_root / "eval" if (oracle_root / "eval").exists() else oracle_root
    test_root = eval_root / "tests"
    files = sorted(test_root.rglob("test_*.py")) if test_root.exists() else []
    file_results = [lint_file(path, oracle_root) for path in files]
    issues = [issue for result in file_results for issue in result.get("issues", [])]
    high = [issue for issue in issues if issue.get("severity") == "high"]
    medium = [issue for issue in issues if issue.get("severity") == "medium"]
    return {
        "oracle_root": str(oracle_root),
        "test_file_count": len(files),
        "issue_count": len(issues),
        "high_count": len(high),
        "medium_count": len(medium),
        "passed": len(high) == 0,
        "issues": issues[:200],
        "files": file_results,
        "rule_reference": {
            "high": sorted(HIGH_RULES),
            "medium": sorted(MEDIUM_RULES),
            "note": "Approximation of ProgramBench Appendix A.3.5 structural assertion rules.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle-material-root", type=Path, required=True)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()
    root = args.oracle_material_root.expanduser().resolve()
    payload = lint_oracle_root(root)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
