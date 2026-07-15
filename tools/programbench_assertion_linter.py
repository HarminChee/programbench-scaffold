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
    "trivially_true",
    "sole_returncode",
    "returncode_in_list",
    "assertion_disjunction",
    "pass_body",
    "if_no_else",
    "if_else_both_assert",
    "try_except_swallow",
    "all_assertions_weak",
    "short_substring",
    "golden_written_in_test",
    "golden_no_equality",
    "golden_docstring",
}
MEDIUM_RULES = {
    "for_no_guard",
    "weak_sole_assertion",
    "relative_length_assertion",
    "any_all_no_guard",
    "file_exists_no_content",
    "only_negative_assertions",
}
LOW_RULES = {"catches"}

SHORT_SUBSTRING_LIMIT = 15
MIN_CATCHES_DESCRIPTION = 20


def node_text(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return node.__class__.__name__


def is_returncode_assert(assert_node: ast.Assert) -> bool:
    return "returncode" in node_text(assert_node.test)


def is_exact_success_returncode_assert(assert_node: ast.Assert) -> bool:
    test = assert_node.test
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    if len(test.comparators) != 1:
        return False
    sides = (test.left, test.comparators[0])
    return any("returncode" in node_text(side) for side in sides) and any(
        isinstance(side, ast.Constant) and side.value == 0 for side in sides
    )


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
        if (
            isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and len(value.value) < SHORT_SUBSTRING_LIMIT
        ):
            return True
        if (
            isinstance(value, ast.Constant)
            and isinstance(value.value, bytes)
            and len(value.value) < SHORT_SUBSTRING_LIMIT
        ):
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


def contains_call(node: ast.AST, names: set[str]) -> bool:
    for item in ast.walk(node):
        if not isinstance(item, ast.Call):
            continue
        func = item.func
        if isinstance(func, ast.Name) and func.id in names:
            return True
        if isinstance(func, ast.Attribute) and func.attr in names:
            return True
    return False


def is_length_assert(assert_node: ast.Assert) -> bool:
    return contains_call(assert_node.test, {"len"})


def is_relative_length_assert(assert_node: ast.Assert) -> bool:
    test = assert_node.test
    if not isinstance(test, ast.Compare) or not is_length_assert(assert_node):
        return False
    return any(isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)) for op in test.ops)


def is_trivially_true(assert_node: ast.Assert) -> bool:
    test = assert_node.test
    if isinstance(test, ast.Constant) and test.value is True:
        return True
    return any(
        isinstance(item, ast.BoolOp)
        and isinstance(item.op, ast.Or)
        and any(isinstance(value, ast.Constant) and value.value is True for value in item.values)
        for item in ast.walk(test)
    )


def is_negative_assert(assert_node: ast.Assert) -> bool:
    test = assert_node.test
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return True
    if not isinstance(test, ast.Compare):
        return False
    return bool(test.ops) and all(isinstance(op, (ast.NotEq, ast.NotIn, ast.IsNot)) for op in test.ops)


def is_weak_assert(assert_node: ast.Assert) -> bool:
    return (
        is_returncode_assert(assert_node)
        or is_length_assert(assert_node)
        or contains_call(assert_node.test, {"isdigit"})
    )


def has_nonempty_guard(assertions: list[ast.Assert], guarded: ast.Assert) -> bool:
    for item in assertions:
        if item is guarded:
            continue
        if is_length_assert(item):
            return True
        test = item.test
        if isinstance(test, (ast.Name, ast.Attribute, ast.Subscript)):
            return True
    if isinstance(guarded.test, ast.BoolOp) and isinstance(guarded.test.op, ast.And):
        return any(not contains_call(value, {"any", "all"}) for value in guarded.test.values)
    return False


def body_assertions(statements: list[ast.stmt]) -> list[ast.Assert]:
    result: list[ast.Assert] = []
    for statement in statements:
        result.extend(item for item in ast.walk(statement) if isinstance(item, ast.Assert))
    return result


def test_body_is_pass(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = list(fn.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return bool(body) and all(isinstance(item, ast.Pass) for item in body)


def golden_docstring(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    doc = ast.get_docstring(fn, clean=True)
    return doc if doc and "golden" in doc.lower() else None


def writes_golden(fn: ast.FunctionDef | ast.AsyncFunctionDef, has_golden_doc: bool) -> ast.AST | None:
    for item in ast.walk(fn):
        if not isinstance(item, ast.Call):
            continue
        text = node_text(item).lower()
        is_writer = any(token in text for token in (".write_text(", ".write_bytes(", ".write("))
        if isinstance(item.func, ast.Name) and item.func.id == "open" and len(item.args) >= 2:
            mode = item.args[1]
            is_writer = (
                isinstance(mode, ast.Constant)
                and isinstance(mode.value, str)
                and any(c in mode.value for c in "wax")
            )
        if is_writer and ("golden" in text or has_golden_doc):
            return item
    return None


def has_equality_assert(assertions: list[ast.Assert]) -> bool:
    return any(
        any(isinstance(op, ast.Eq) for op in item.ops)
        for assertion in assertions
        for item in ast.walk(assertion.test)
        if isinstance(item, ast.Compare)
    )


def catches_description_issue(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    doc = ast.get_docstring(fn, clean=True) or ""
    marker = "catches:"
    index = doc.lower().find(marker)
    if index < 0:
        return True
    description = doc[index + len(marker) :].strip().splitlines()[0] if doc[index + len(marker) :].strip() else ""
    return len(description) < MIN_CATCHES_DESCRIPTION


def function_issues(fn: ast.FunctionDef | ast.AsyncFunctionDef, relpath: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    assertions = [node for node in ast.walk(fn) if isinstance(node, ast.Assert)]
    seen: set[tuple[str, int]] = set()

    def add(rule: str, severity: str, node: ast.AST | None = None) -> None:
        line = getattr(node or fn, "lineno", fn.lineno)
        key = (rule, line)
        if key in seen:
            return
        seen.add(key)
        issues.append({"rule": rule, "severity": severity, "path": relpath, "line": line, "function": fn.name})

    if not assertions:
        add("no_assertions", "high")

    if test_body_is_pass(fn):
        add("pass_body", "high")

    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            if any(isinstance(item, ast.BoolOp) and isinstance(item.op, ast.Or) for item in ast.walk(node.test)):
                add("assertion_disjunction", "high", node)
            if is_trivially_true(node):
                add("trivially_true", "high", node)
            if is_returncode_in_list(node):
                add("returncode_in_list", "high", node)
            if is_short_substring(node):
                add("short_substring", "high", node)
            if is_relative_length_assert(node):
                add("relative_length_assertion", "medium", node)
            if contains_call(node.test, {"any", "all"}) and not has_nonempty_guard(assertions, node):
                add("any_all_no_guard", "medium", node)
        elif isinstance(node, ast.ExceptHandler):
            if not node.body or all(isinstance(item, ast.Pass) for item in node.body):
                add("try_except_swallow", "high", node)
        elif isinstance(node, ast.If):
            body_has_assert = bool(body_assertions(node.body))
            else_has_assert = bool(body_assertions(node.orelse))
            if body_has_assert and not node.orelse:
                add("if_no_else", "high", node)
            if body_has_assert and else_has_assert:
                add("if_else_both_assert", "high", node)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            loop_assertions = body_assertions(node.body)
            if loop_assertions and len(loop_assertions) == len(assertions) and not any(
                is_length_assert(item) and item not in loop_assertions for item in assertions
            ):
                add("for_no_guard", "medium", node)

    if len(assertions) == 1:
        only = assertions[0]
        if is_exact_success_returncode_assert(only):
            add("sole_returncode", "high", only)
        elif is_file_exists_assert(only):
            add("file_exists_no_content", "medium", only)
        elif is_relative_length_assert(only):
            add("weak_sole_assertion", "medium", only)

    if assertions and all(is_weak_assert(item) for item in assertions):
        add("all_assertions_weak", "high")

    if assertions and all(is_negative_assert(item) for item in assertions):
        add("only_negative_assertions", "medium")

    golden_doc = golden_docstring(fn)
    writer = writes_golden(fn, bool(golden_doc))
    if writer:
        add("golden_written_in_test", "high", writer)
    if golden_doc:
        body_without_doc = (
            "\n".join(node_text(item) for item in fn.body[1:])
            if ast.get_docstring(fn, clean=False)
            else node_text(fn)
        )
        if "golden" not in body_without_doc.lower():
            add("golden_docstring", "high")
        if not has_equality_assert(assertions):
            add("golden_no_equality", "high")

    if catches_description_issue(fn):
        add("catches", "low")

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
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and (node.name.startswith("test_") or any("pytest" in node_text(d) for d in node.decorator_list))
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
    low = [issue for issue in issues if issue.get("severity") == "low"]
    return {
        "oracle_root": str(oracle_root),
        "test_file_count": len(files),
        "issue_count": len(issues),
        "high_count": len(high),
        "medium_count": len(medium),
        "low_count": len(low),
        "passed": len(high) == 0,
        "issues": issues[:200],
        "files": file_results,
        "rule_reference": {
            "high": sorted(HIGH_RULES),
            "medium": sorted(MEDIUM_RULES),
            "low": sorted(LOW_RULES),
            "note": (
                "ProgramBench Appendix A.3.5 Table 8 structural assertion rules, "
                "implemented as a conservative AST approximation."
            ),
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
