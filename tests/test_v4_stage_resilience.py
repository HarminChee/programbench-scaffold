from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from v4.adapters.gofumpt_pilot_adapter import (
    _extract_cases_payload_from_text,
    agent_request_limits,
    normalize_agent_cases,
    quality,
    quality_container_cpus,
    quality_pytest_pass_timeout_seconds,
    quality_subprocess_timeout_seconds,
)
from v4.programbench_v4.stage_resilience import (
    canonical_case_name,
    canonicalize_case_identities,
    repeat_failure_case_names,
    run_semantic_retry,
)


def test_case_identity_matches_oracle_slug_contract() -> None:
    assert canonical_case_name("V4-T01 / Custom Taskfile") == "v4_t01_custom_taskfile"
    assert canonical_case_name("  ") == "case"


def test_agent_request_limits_keep_large_campaign_tranches_transactional() -> None:
    assert agent_request_limits({}, 128) == {
        "context_max_bytes": 120_000,
        "requested_cases": 48,
        "max_tokens": 12_000,
        "transport_retries": 2,
    }
    assert agent_request_limits(
        {
            "agent_context_max_bytes": 999_999,
            "agent_request_case_cap": 32,
            "agent_max_tokens": 1,
            "agent_transport_retries": 99,
        },
        64,
    ) == {
        "context_max_bytes": 220_000,
        "requested_cases": 32,
        "max_tokens": 4096,
        "transport_retries": 5,
    }


def test_existing_case_identities_are_migrated_without_losing_rows() -> None:
    rows, renamed = canonicalize_case_identities(
        [{"name": "V4_T01_Custom-Taskfile", "args": []}, {"name": "already_safe"}]
    )
    assert [row["name"] for row in rows] == ["v4_t01_custom_taskfile", "already_safe"]
    assert renamed == {"V4_T01_Custom-Taskfile": "v4_t01_custom_taskfile"}


def test_identity_migration_fails_closed_on_collision() -> None:
    with pytest.raises(ValueError, match="identity collision"):
        canonicalize_case_identities([{"name": "same-name"}, {"name": "same_name"}])


def test_adapter_persists_the_same_lowercase_identity_capture_will_emit() -> None:
    cases = normalize_agent_cases(
        {
            "cases": [
                {
                    "name": "Custom Taskfile / Upper CASE",
                    "area": "workflow",
                    "args": ["--taskfile", "Taskfile.yml"],
                    "stdin": "",
                }
            ]
        },
        iteration=2,
    )
    assert cases[0]["name"] == "v4_t02_000_custom_taskfile_upper_case"


def test_adapter_recovers_complete_cases_from_truncated_agent_json() -> None:
    text = (
        '{"cases":['
        '{"name":"first","args":["--help"],"stdin":""},'
        '{"name":"second","args":[],"stdin":"literal\ncontrol"},'
        '{"name":"third","args":["unterminated"]'
    )
    payload = _extract_cases_payload_from_text(text)
    assert payload is not None
    assert payload["partial_agent_json_recovered"] is True
    cases = normalize_agent_cases(payload, iteration=3)
    assert [case["name"] for case in cases] == [
        "v4_t03_000_first",
        "v4_t03_001_second",
    ]
    assert cases[1]["stdin"] == "literal\ncontrol"


def test_semantic_retry_repairs_missing_cases_payload() -> None:
    calls: list[tuple[int, tuple[str, ...]]] = []

    def produce(attempt: int, errors: tuple[str, ...]) -> dict:
        calls.append((attempt, errors))
        return {"message": "not cases"} if attempt == 1 else {"cases": [{"name": "ok"}]}

    def validate(payload: dict) -> list[dict]:
        rows = payload.get("cases")
        if not isinstance(rows, list):
            raise ValueError("agent response has no cases array")
        return rows

    rows, errors = run_semantic_retry(produce, validate, maximum_attempts=2)
    assert rows == [{"name": "ok"}]
    assert errors == ("agent response has no cases array",)
    assert calls[1] == (2, errors)


def test_semantic_retry_does_not_swallow_transport_failure() -> None:
    def produce(attempt: int, errors: tuple[str, ...]) -> dict:
        raise RuntimeError("bridge unavailable")

    with pytest.raises(RuntimeError, match="bridge unavailable"):
        run_semantic_retry(produce, lambda payload: payload, maximum_attempts=2)


def test_repeat_failures_map_back_to_stable_case_names() -> None:
    report = {
        "repeat_check": {
            "junit_summary": {
                "failed_test_names": [
                    "eval.tests.test_generated_cli_oracle.test_0047_preview",
                    "eval.tests.test_generated_cli_oracle.test_0054_unicode",
                    "unrelated.test_name",
                ]
            }
        }
    }
    manifest = [{"name": f"case_{index}"} for index in range(55)]
    assert repeat_failure_case_names(report, manifest) == ("case_47", "case_54")


def test_repeat_failure_mapping_fails_closed_on_bad_index() -> None:
    report = {
        "repeat_check": {
            "junit_summary": {"failed_test_names": ["suite.test_0002_missing"]}
        }
    }
    with pytest.raises(ValueError, match="outside captured manifest"):
        repeat_failure_case_names(report, [{"name": "only"}])


def test_quality_timeout_defaults_and_adapts_to_suite_size() -> None:
    assert quality_subprocess_timeout_seconds({}, captured_case_count=24) == 3600
    assert quality_subprocess_timeout_seconds({}, captured_case_count=400) == 4900
    assert quality_subprocess_timeout_seconds({}, captured_case_count=1000) == 6600


def test_quality_pytest_pass_timeout_scales_for_large_suites() -> None:
    assert quality_pytest_pass_timeout_seconds(captured_case_count=24) == 900
    assert quality_pytest_pass_timeout_seconds(captured_case_count=400) == 1100
    assert quality_pytest_pass_timeout_seconds(captured_case_count=741) == 1782
    assert quality_pytest_pass_timeout_seconds(captured_case_count=2000) == 1800


def test_quality_container_cpus_adapts_for_large_suites() -> None:
    assert quality_container_cpus({}, captured_case_count=24) == 4
    assert quality_container_cpus({}, captured_case_count=499) == 4
    assert quality_container_cpus({}, captured_case_count=500) == 8
    assert quality_container_cpus(
        {"quality_container_cpus": 12}, captured_case_count=24
    ) == 12
    with pytest.raises(ValueError, match="1..16"):
        quality_container_cpus({"quality_container_cpus": 17}, captured_case_count=1)


def test_quality_timeout_honors_valid_explicit_repository_setting() -> None:
    assert quality_subprocess_timeout_seconds(
        {"quality_timeout_seconds": 4200}, captured_case_count=1000
    ) == 4200
    with pytest.raises(ValueError, match="60..7200"):
        quality_subprocess_timeout_seconds(
            {"quality_timeout_seconds": 7201}, captured_case_count=1
        )


def test_quality_stage_passes_and_logs_selected_subprocess_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "eval").mkdir(parents=True)
    (bundle / "eval/generated_cli_manifest.json").write_text(
        json.dumps({"cases": []}), encoding="utf-8"
    )
    binary = tmp_path / "reference"
    binary.write_bytes(b"binary")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "preflight.json").write_text(
        json.dumps({"reference_binary": str(binary)}), encoding="utf-8"
    )
    (artifacts / "current_bundle.json").write_text(
        json.dumps({"bundle_root": str(bundle)}), encoding="utf-8"
    )
    observed: dict[str, int] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed["timeout"] = int(kwargs["timeout"])
        command = list(args[0])
        observed["pytest_pass_timeout"] = int(command[command.index("--timeout") + 1])
        observed["container_cpus"] = int(command[command.index("--container-cpus") + 1])
        return subprocess.CompletedProcess(args[0], 1, "", "quality failed")

    monkeypatch.setattr(
        "v4.adapters.gofumpt_pilot_adapter.subprocess.run", fake_run
    )
    request = {
        "iteration": 1,
        "stage": "quality_gates",
        "repository": {
            "runtime_image_id": "sha256:" + "a" * 64,
            "quality_timeout_seconds": 4321,
        },
    }
    with pytest.raises(RuntimeError, match="quality gates failed"):
        quality(request, tmp_path)
    log = json.loads((tmp_path / "logs/quality-0001-initial.json").read_text())
    assert observed["timeout"] == 4321
    assert observed["pytest_pass_timeout"] == 900
    assert observed["container_cpus"] == 4
    assert log["timeout_seconds"] == 4321
    assert log["timed_out"] is False


def test_freeze_quality_timeout_preserves_stage_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "eval").mkdir(parents=True)
    (bundle / "eval/generated_cli_manifest.json").write_text(
        json.dumps({"cases": []}), encoding="utf-8"
    )
    binary = tmp_path / "reference"
    binary.write_bytes(b"binary")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "preflight.json").write_text(
        json.dumps({"reference_binary": str(binary)}), encoding="utf-8"
    )
    (artifacts / "current_bundle.json").write_text(
        json.dumps({"bundle_root": str(bundle)}), encoding="utf-8"
    )

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        output = tmp_path / "quality/iteration-0001.json"
        output.parent.mkdir()
        output.write_text(
            json.dumps(
                {
                    "all_target_executions_isolated": True,
                    "source_leak_scan": {"passed": True},
                    "assertion_lint": {"passed": True},
                    "dummy_reject": [{"timed_out": True, "passing_test_names": []}],
                    "repeat_check": None,
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args[0], 1, "", "timed out")

    monkeypatch.setattr(
        "v4.adapters.gofumpt_pilot_adapter.subprocess.run", fake_run
    )
    response = quality(
        {
            "iteration": 1,
            "stage": "freeze_verification",
            "repository": {"runtime_image_id": "sha256:" + "a" * 64},
        },
        tmp_path,
    )
    assert response["quality_unrepairable"] is True
    assert response["quality_block_reason"] == "quality_execution_timeout"
    assert response["execution_provenance"][0]["stage"] == "freeze_verification"
