from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from programbench_agent_oracle_loop import (  # noqa: E402
    build_generation_prompt,
    binary_inconsistent_case_names,
    case_name_from_pytest_name,
    coverage_value,
    is_recoverable_max_turn_run,
    load_agent_updated_suite,
    load_recovered_agent_suite,
    normalize_case_env,
    normalize_case_files,
    normalize_case_http,
    normalize_cases,
    plateaued,
    review_evidence_batches,
)
from programbench_agent_provider import build_claude_command, configure_agent_maestro_claude_env  # noqa: E402
import programbench_go_coverage_harness as go_harness  # noqa: E402
from programbench_go_coverage_harness import parse_cover_profile_line_coverage  # noqa: E402
from programbench_test_review_agent import REVIEW_SYSTEM, build_review_prompt, validate_review  # noqa: E402
from programbench_run_generated_oracle_quality_gates import parse_junit  # noqa: E402


def test_claude_command_is_nonpersistent_and_does_not_bypass_permissions() -> None:
    cmd = build_claude_command(executable="claude", model="claude-sonnet-5[1m]", max_turns=12)
    assert "--no-session-persistence" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert "--dangerously-skip-permissions" not in cmd
    assert cmd[cmd.index("--max-turns") + 1] == "12"


def test_agent_maestro_env_maps_proxy_secret_without_bedrock() -> None:
    env = configure_agent_maestro_claude_env(
        {
            "AGENT_MAESTRO_BASE_URL": "http://127.0.0.1:23333/",
            "AGENT_MAESTRO_API_KEY": "process-only-secret",
            "CLAUDE_CODE_USE_BEDROCK": "1",
        }
    )
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:23333/api/anthropic"
    assert env["ANTHROPIC_API_KEY"] == "process-only-secret"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "process-only-secret"
    assert env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "936000"
    assert "CLAUDE_CODE_USE_BEDROCK" not in env


def test_normalize_cases_deduplicates_observationally_identical_inputs() -> None:
    payload = {
        "cases": [
            {"name": "help-short", "area": "help", "args": ["-h"], "stdin": "", "rationale": "short"},
            {"name": "help_duplicate", "area": "help", "args": ["-h"], "stdin": "", "rationale": "dup"},
            {"name": "help-long", "area": "help", "args": ["--help"], "stdin": "", "rationale": "long"},
            {"name": "invalid-nul", "area": "invalid", "args": ["bad\x00arg"], "stdin": "", "rationale": "invalid"},
        ]
    }
    cases = normalize_cases(payload, max_cases=10, iteration=1)
    assert [case["name"] for case in cases] == ["help_short", "help_long"]


def test_generation_prompt_uses_suite_file_commit_protocol() -> None:
    prompt = build_generation_prompt(
        instance_id="owner__tool.abcdef0",
        iteration=2,
        target_coverage=88.5,
        max_cases=2000,
        previous={"coverage_percent": 70.0},
    )
    assert "Edit `candidate_cases.json` in place" in prompt
    assert "suite is delivered by\nthe file" in prompt
    assert "Do not return the case array" in prompt
    assert "Do not use `cd`, pipes, redirects" in prompt


def test_generation_prompt_selects_windows_reference_bridge(monkeypatch) -> None:
    monkeypatch.setenv("PROGRAMBENCH_WINDOWS_CLAUDE_BRIDGE", "1")
    prompt = build_generation_prompt(
        instance_id="owner__tool.abcdef0",
        iteration=1,
        target_coverage=88.5,
        max_cases=2000,
        previous=None,
    )
    assert "tools/programbench_agent_probe_reference_windows.ps1" in prompt
    assert "programbench_agent_validate_cases_windows.ps1" in prompt
    assert "valid_unique_count" in prompt
    assert "python3 agent_tools/probe_reference.py" not in prompt


def test_generation_prompt_switches_to_bounded_coverage_only_increment() -> None:
    prompt = build_generation_prompt(
        instance_id="owner__tool.abcdef0",
        iteration=4,
        target_coverage=88.5,
        max_cases=2000,
        previous={"quality_passed": True, "review_verdict": "keep"},
    )
    assert "coverage-only increment" in prompt
    assert "preserve all existing cases" in prompt
    assert "at most 20 reference probes" in prompt

    nested_prompt = build_generation_prompt(
        instance_id="owner__tool.abcdef0",
        iteration=5,
        target_coverage=88.5,
        max_cases=2000,
        previous={
            "previous_iteration": {"quality_passed": True, "review_verdict": "keep"},
            "review": {"suite_verdict": "keep"},
        },
    )
    assert "coverage-only increment" in nested_prompt
    assert "map its name to one explicit function" in nested_prompt
    assert "{http_url}" in nested_prompt
    assert "lines_unordered" in nested_prompt


def test_generation_prompt_bounds_quality_repair_before_coverage_work() -> None:
    prompt = build_generation_prompt(
        instance_id="owner__tool.abcdef0",
        iteration=2,
        target_coverage=88.5,
        max_cases=2000,
        previous={
            "previous_iteration": {"quality_passed": False, "review_verdict": "revise"},
            "review": {"suite_verdict": "revise", "revise": ["weak_case"]},
        },
    )
    assert "bounded quality-repair increment" in prompt
    assert "Preserve every case marked `keep`" in prompt
    assert "Do not add coverage-only cases" in prompt
    assert "at most 12 reference\nprobes" in prompt


def test_agent_suite_file_confirmation_fails_closed_on_wrong_count(tmp_path: Path) -> None:
    suite = tmp_path / "candidate_cases.json"
    suite.write_text(
        '{"cases":[{"name":"help","area":"help","args":["--help"],'
        '"stdin":"","rationale":"exercise full help behavior"}]}',
        encoding="utf-8",
    )
    payload, cases = load_agent_updated_suite(
        suite,
        {"suite_updated": True, "case_count": 1, "analysis_summary": "kept help"},
        max_cases=10,
        iteration=1,
    )
    assert payload["cases"][0]["name"] == "help"
    assert [case["name"] for case in cases] == ["help"]

    try:
        load_agent_updated_suite(
            suite,
            {"suite_updated": True, "case_count": 2, "analysis_summary": "wrong"},
            max_cases=10,
            iteration=1,
        )
    except ValueError as exc:
        assert "reported 2 cases" in str(exc)
    else:
        raise AssertionError("wrong case count must fail closed")


def test_max_turn_candidate_is_recovered_but_other_failures_stay_closed(tmp_path: Path) -> None:
    suite = tmp_path / "candidate_cases.json"
    suite.write_text(
        '{"cases":[{"name":"help","area":"help","args":["--help"],'
        '"stdin":"","rationale":"exercise full help behavior"}]}',
        encoding="utf-8",
    )
    max_turn_run = SimpleNamespace(
        returncode=1,
        timed_out=False,
        structured_output={"subtype": "error_max_turns", "terminal_reason": "max_turns"},
    )
    assert is_recoverable_max_turn_run(max_turn_run)
    payload, cases = load_recovered_agent_suite(suite, max_cases=10, iteration=2)
    assert payload["cases"][0]["name"] == "help"
    assert [case["name"] for case in cases] == ["help"]

    timeout_run = SimpleNamespace(
        returncode=124,
        timed_out=True,
        structured_output={"subtype": "error_max_turns"},
    )
    transport_run = SimpleNamespace(returncode=1, timed_out=False, structured_output=None)
    assert not is_recoverable_max_turn_run(timeout_run)
    assert not is_recoverable_max_turn_run(transport_run)


def test_fixture_dsl_normalization_is_bounded_and_affects_case_identity() -> None:
    assert normalize_case_env({"HTTP_PROXY": "http://127.0.0.1:1", "API_KEY": "blocked"}) == {
        "HTTP_PROXY": "http://127.0.0.1:1"
    }
    assert normalize_case_files({"nested/input.json": '{"ok":true}', "../escape": "no"}) == {
        "nested/input.json": '{"ok":true}'
    }
    assert normalize_case_http(
        {"path": "/data.json", "status": 200, "headers": {"Content-Type": "application/json"}, "body": "{}"}
    ) == {
        "path": "/data.json",
        "status": 200,
        "headers": {"Content-Type": "application/json"},
        "body": "{}",
    }

    payload = {
        "cases": [
            {"name": "plain", "args": ["input.json"], "stdin": ""},
            {
                "name": "fixture",
                "args": ["input.json"],
                "stdin": "",
                "files": {"input.json": "{}"},
                "env": {"TERM": "xterm"},
                "http": {"path": "/data", "body": "{}"},
                "stdout_mode": "lines_unordered",
            },
        ]
    }
    cases = normalize_cases(payload, max_cases=10, iteration=1)
    assert [case["name"] for case in cases] == ["plain", "fixture"]
    assert cases[1]["files"] == {"input.json": "{}"}
    assert cases[1]["env"] == {"TERM": "xterm"}
    assert cases[1]["http"]["path"] == "/data"
    assert cases[1]["stdout_mode"] == "lines_unordered"


def test_binary_inconsistent_case_names_are_extracted_for_quality_repair() -> None:
    summary = {
        "branch_results": [
            {
                "binary_results": [
                    {
                        "junit_summary": {
                            "filtered_failed_test_names": [
                                "tests.test_generated_cli_oracle.test_0013_flag_no_sort"
                            ],
                            "filtered_error_test_names": [],
                        }
                    },
                    {
                        "junit_summary": {
                            "filtered_failed_test_names": [],
                            "filtered_error_test_names": [
                                "tests.test_generated_cli_oracle.test_0090_url_case"
                            ],
                        }
                    },
                ]
            }
        ]
    }
    assert binary_inconsistent_case_names(summary) == ["flag_no_sort", "url_case"]


def test_review_omission_fails_closed_as_revise() -> None:
    review = {
        "suite_verdict": "keep",
        "decisions": [{"name": "a", "verdict": "keep", "reason": "distinct"}],
    }
    validated = validate_review(review, ["a", "b"])
    assert validated["suite_verdict"] == "revise"
    assert validated["counts"] == {"keep": 1, "reject": 0, "revise": 1}
    assert validated["revise"] == ["b"]


def test_review_reject_cannot_merge_to_keep() -> None:
    review = {
        "suite_verdict": "keep",
        "decisions": [
            {"name": "strong", "verdict": "keep", "reason": "distinct"},
            {"name": "weak", "verdict": "reject", "reason": "redundant"},
        ],
    }
    validated = validate_review(review, ["strong", "weak"])
    assert validated["suite_verdict"] == "revise"
    assert validated["counts"] == {"keep": 1, "reject": 1, "revise": 0}
    assert validated["reject"] == ["weak"]


def test_review_policy_is_commit_pinned_and_path_aware() -> None:
    prompt = build_review_prompt({"cases": []})
    assert "Exact full stdout or\nstderr" in REVIEW_SYSTEM
    assert "different inputs, source formats, flags, or parser paths" in REVIEW_SYSTEM
    assert "current proposed suite" in REVIEW_SYSTEM
    assert "suggested change is not already\nsatisfied" in REVIEW_SYSTEM
    assert "full usage/error output is stable evidence" in prompt


def test_oracle_material_replacement_removes_stale_branch_directories(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    extract = tmp_path / "branch"
    (repo / "eval").mkdir(parents=True)
    (repo / "eval" / "stale.txt").write_text("stale", encoding="utf-8")
    (extract / "fixtures").mkdir(parents=True)
    (extract / "fixtures" / "fresh.txt").write_text("fresh", encoding="utf-8")

    copied = go_harness.copy_oracle_material(extract, repo)

    assert copied == ["fixtures"]
    assert not (repo / "eval").exists()
    assert (repo / "fixtures" / "fresh.txt").read_text(encoding="utf-8") == "fresh"


def test_review_evidence_batches_cover_each_case_once() -> None:
    cases = [
        {"name": f"case_{index}", "args": [str(index)], "stdin": "", "area": "io", "rationale": "distinct"}
        for index in range(5)
    ]
    evidence = {
        "cases": cases,
        "reference_observations": {
            "kept": [{"name": case["name"], "returncode": 0} for case in cases],
            "skipped": [],
        },
        "quality_gates": {
            "dummy_passing_test_names": ["eval.tests.test_generated.case_3"],
            "dummy_results": [
                {"kind": "true", "passing_test_names": ["eval.tests.test_generated.case_3"]}
            ],
        },
        "previous_review": {"decisions": [{"name": "case_4", "verdict": "revise"}]},
    }
    batches = review_evidence_batches(evidence, batch_size=2)
    assert [len(batch["cases"]) for batch in batches] == [2, 2, 1]
    assert [case["name"] for batch in batches for case in batch["cases"]] == [case["name"] for case in cases]
    assert batches[1]["quality_gates"]["dummy_passing_test_count"] == 1
    assert batches[1]["quality_gates"]["dummy_passing_case_names"] == ["case_3"]
    assert batches[2]["previous_review"]["decisions"][0]["name"] == "case_4"


def test_pytest_name_maps_back_to_generated_case_name() -> None:
    assert case_name_from_pytest_name("eval.tests.generated.test_0017_empty_stdin") == "empty_stdin"


def test_plateau_requires_all_recent_deltas_below_threshold() -> None:
    assert plateaued(
        [{"coverage_percent": 70.0}, {"coverage_percent": 70.3}, {"coverage_percent": 70.6}],
        delta=0.5,
        patience=2,
    )
    assert not plateaued(
        [{"coverage_percent": 70.0}, {"coverage_percent": 71.0}, {"coverage_percent": 71.2}],
        delta=0.5,
        patience=2,
    )
    assert not plateaued(
        [{"coverage_percent": 70.0}, {"coverage_percent": 60.0}, {"coverage_percent": 59.8}],
        delta=0.5,
        patience=2,
    )


def test_go_statement_coverage_is_the_primary_acceptance_metric() -> None:
    assert coverage_value(
        {
            "coverage": {
                "generated_go_statement_coverage": 91.2,
                "generated_go_line_coverage": 94.8,
            }
        }
    ) == 91.2
    assert coverage_value({"coverage": {"generated_go_line_coverage": 88.5}}) == 88.5


def test_go_cover_profile_line_coverage_uses_line_union(tmp_path: Path) -> None:
    profile = tmp_path / "coverage.out"
    profile.write_text(
        "mode: set\n"
        "example.org/tool/main.go:10.1,12.2 2 1\n"
        "example.org/tool/main.go:12.2,14.1 1 0\n"
        "example.org/tool/other.go:3.1,3.20 1 0\n",
        encoding="utf-8",
    )
    result = parse_cover_profile_line_coverage(profile)
    assert result["covered_executable_lines"] == 3
    assert result["total_executable_lines"] == 5
    assert result["line_coverage_percent"] == 60.0


def test_go_main_discovery_source_fallback_prefers_repository_command(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "cmd" / "atlas").mkdir(parents=True)
    (tmp_path / "cmd" / "atlas" / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    (tmp_path / "internal" / "ci").mkdir(parents=True)
    (tmp_path / "internal" / "ci" / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")

    monkeypatch.setattr(
        go_harness,
        "run_command",
        lambda *args, **kwargs: {"returncode": 1, "stdout": "", "log_path": "go-list.json"},
    )
    result = go_harness.discover_go_main_packages(tmp_path, "ariga/atlas", tmp_path / "logs")
    assert result["fallback_used"] is True
    assert result["selected"] == "./cmd/atlas"
    assert result["candidates"][0]["discovered_by"] == ["source_scan"]


def test_junit_parser_reports_individual_dummy_passing_tests(tmp_path: Path) -> None:
    junit = tmp_path / "dummy.xml"
    junit.write_text(
        """<testsuites><testsuite tests="3" failures="1" errors="1" skipped="0">
<testcase classname="eval.tests.test_generated" name="test_passes_dummy" />
<testcase classname="eval.tests.test_generated" name="test_rejects_dummy"><failure /></testcase>
<testcase classname="eval.tests.test_generated" name="test_dummy_error"><error /></testcase>
</testsuite></testsuites>""",
        encoding="utf-8",
    )
    summary = parse_junit(junit)
    assert summary["passed_test_names"] == ["eval.tests.test_generated.test_passes_dummy"]
    assert summary["failed_test_names"] == ["eval.tests.test_generated.test_rejects_dummy"]
    assert summary["error_test_names"] == ["eval.tests.test_generated.test_dummy_error"]
