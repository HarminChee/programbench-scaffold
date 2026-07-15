from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from programbench_assertion_linter import function_issues, lint_oracle_root  # noqa: E402
from programbench_generate_cli_oracle_bundle import write_generated_pytest  # noqa: E402


def rules(source: str) -> set[str]:
    tree = ast.parse(source)
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
    return {issue["rule"] for issue in function_issues(fn, "test_example.py")}


class AssertionLinterTest(unittest.TestCase):
    def test_exact_output_with_catches_is_strong(self) -> None:
        found = rules(
            '''
def test_help():
    """CATCHES: implementations that print the wrong help text."""
    result = run_binary(["--help"])
    assert result.stdout == "complete expected help output"
'''
        )
        self.assertFalse(found & {"no_assertions", "all_assertions_weak", "catches"})

    def test_table8_high_rules(self) -> None:
        found = rules(
            '''
def test_weak():
    if enabled:
        assert True
    assert result.returncode in [0, 1]
    assert result.stdout == "left" or result.stdout == "right"
    assert "tiny" in result.stdout
'''
        )
        self.assertTrue(
            {"trivially_true", "assertion_disjunction", "if_no_else", "returncode_in_list", "short_substring"}
            <= found
        )

    def test_pass_and_swallowed_exception_are_distinct(self) -> None:
        self.assertIn("pass_body", rules("def test_empty():\n    pass\n"))
        found = rules(
            '''
def test_parse():
    try:
        assert parse(output) == expected
    except Exception:
        pass
'''
        )
        self.assertIn("try_except_swallow", found)
        self.assertNotIn("pass_body", found)

    def test_medium_rules_do_not_fail_as_high(self) -> None:
        found = rules(
            '''
def test_files():
    assert output.exists()
'''
        )
        self.assertIn("file_exists_no_content", found)
        self.assertNotIn("all_assertions_weak", found)

    def test_short_substring_boundary_is_fifteen(self) -> None:
        self.assertIn("short_substring", rules('def test_short():\n    assert "12345678901234" in output\n'))
        self.assertNotIn("short_substring", rules('def test_long():\n    assert "123456789012345" in output\n'))

    def test_any_all_and_relative_length_rules(self) -> None:
        found = rules(
            '''
def test_rows():
    assert all(row.valid for row in rows)
    assert len(rows) >= 1
'''
        )
        self.assertIn("relative_length_assertion", found)
        self.assertNotIn("any_all_no_guard", found)

    def test_golden_rules(self) -> None:
        found = rules(
            '''
def test_render(tmp_path):
    """CATCHES: incorrect rendering compared with the golden result.

    Golden: expected.txt
    """
    golden = tmp_path / "golden.txt"
    golden.write_text(run_binary().stdout)
    assert golden.exists()
'''
        )
        self.assertIn("golden_written_in_test", found)
        self.assertIn("golden_no_equality", found)

    def test_generated_oracle_template_passes_lint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_generated_pytest(
                root,
                "generic-cli-smoke",
                [{"name": "help"}, {"name": "invalid-input"}],
            )
            report = lint_oracle_root(root)
        self.assertTrue(report["passed"])
        self.assertEqual(report["issue_count"], 0)
        self.assertEqual(report["test_function_count"] if "test_function_count" in report else sum(
            item["test_function_count"] for item in report["files"]
        ), 2)


if __name__ == "__main__":
    unittest.main()
