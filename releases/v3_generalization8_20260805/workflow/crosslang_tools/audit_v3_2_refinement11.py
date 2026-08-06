#!/usr/bin/env python3
"""Build the auditable final table for the V3.2 refinement-11 experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def qemu_summary(metrics: dict[str, Any] | None) -> dict[str, Any]:
    metrics = metrics or {}
    qemu = metrics.get("qemu") or {}
    timing = metrics.get("timing") or {}
    unsupported = metrics.get("unsupported_cases") or []
    return {
        "requested_cases": metrics.get("requested_cases"),
        "completed_cases": qemu.get("completed_cases"),
        "unique_translation_blocks": qemu.get("unique_translation_blocks_union"),
        "unique_block_edges": qemu.get("unique_block_edges_union"),
        "unique_first_party_blocks": qemu.get("unique_first_party_blocks_union"),
        "unique_first_party_edges": qemu.get("unique_first_party_edges_union"),
        "afl_like_bitmap_slots": qemu.get("afl_like_bitmap_slots_union"),
        "runtime_seconds": timing.get("qemu_runtime_seconds"),
        "collector_runtime_seconds": timing.get("total_collector_runtime_seconds"),
        "unsupported_cases": len(unsupported),
        "interpretation": metrics.get("interpretation"),
    }


def quality(summary: dict[str, Any]) -> dict[str, Any]:
    stop = summary.get("stop_gate") or {}
    checks = stop.get("checks") or {}
    tests_passed = summary.get("all_tests_passed")
    if tests_passed is None:
        tests_passed = checks.get("generated_tests_passed")
    consistent = summary.get("binary_consistent")
    if consistent is None:
        consistent = checks.get("three_binary_consistent")
    required = {
        "generated_tests_passed": bool(tests_passed),
        "three_binary_consistent": bool(consistent),
        "all_dummies_rejected": bool(checks.get("all_dummies_rejected")),
        "assertion_lint": bool(checks.get("assertion_lint")),
        "source_leak": bool(checks.get("source_leak")),
    }
    return {"accepted": all(required.values()), "checks": required}


def record_from_summary(
    label: str, round_number: int, path: Path, language: str
) -> dict[str, Any]:
    summary = load(path)
    stop = summary.get("stop_gate") or {}
    source = summary.get("source_coverage") or {}
    primary = stop.get("primary_percent")
    secondary = stop.get("secondary_percent")
    # The Go harness stores primary=statement and secondary=executable-line,
    # while the cross-language report presents line/statement.
    if language == "go":
        primary, secondary = secondary, primary
    manifest_path = summary.get("manifest")
    case_count = summary.get("case_count")
    if case_count is None and manifest_path:
        manifest = Path(manifest_path)
        if manifest.exists():
            case_count = load(manifest).get("case_count")
    coverage_valid = (stop.get("checks") or {}).get("coverage_signal_valid")
    if coverage_valid is None:
        coverage_valid = source.get("valid")
    if coverage_valid is None:
        coverage_valid = (
            primary is not None
            and secondary is not None
            and not source.get("invalid_reason")
        )
    coverage_valid = bool(coverage_valid) and bool(case_count)
    quality_result = quality(summary)
    quality_result["checks"]["nonempty_suite"] = bool(case_count)
    quality_result["accepted"] = quality_result["accepted"] and bool(case_count)
    return {
        "label": label,
        "round": round_number,
        "case_count": case_count,
        "primary_percent": primary,
        "secondary_percent": secondary,
        "coverage_valid": coverage_valid,
        "coverage_invalid_reason": source.get("invalid_reason"),
        "quality": quality_result,
        "qemu": qemu_summary(summary.get("dynamic_path_metrics")),
        "coverage_seconds": (
            (summary.get("coverage_timing") or {}).get("statement_coverage_signal_seconds")
            if language == "go"
            else (summary.get("coverage_timing") or {}).get("generated_suite_coverage_seconds")
        ),
        "summary_path": str(path),
        "manifest_path": manifest_path,
    }


def baseline_record(row: dict[str, Any], summary_path: Path) -> dict[str, Any]:
    summary = load(summary_path)
    values = row.get("v3_coverage_primary_secondary") or [None, None]
    source = summary.get("source_coverage") or {}
    stop = summary.get("stop_gate") or {}
    coverage_valid = (stop.get("checks") or {}).get("coverage_signal_valid")
    if coverage_valid is None:
        coverage_valid = source.get("valid")
    if coverage_valid is None:
        coverage_valid = (
            values[0] is not None
            and values[1] is not None
            and not source.get("invalid_reason")
        )
    case_count = row.get("v3_behavioral_test_functions")
    coverage_valid = bool(coverage_valid) and bool(case_count)
    quality_result = quality(summary)
    quality_result["checks"]["nonempty_suite"] = bool(case_count)
    quality_result["accepted"] = quality_result["accepted"] and bool(case_count)
    return {
        "label": "v3_1",
        "round": 0,
        "case_count": case_count,
        "primary_percent": values[0],
        "secondary_percent": values[1],
        "coverage_valid": coverage_valid,
        "coverage_invalid_reason": source.get("invalid_reason"),
        "quality": quality_result,
        "qemu": qemu_summary(row.get("qemu")),
        "coverage_seconds": row.get("v3_coverage_seconds"),
        "summary_path": str(summary_path),
        "manifest_path": row.get("manifest_path"),
    }


def choose_best(records: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    def score(record: dict[str, Any]) -> tuple[float, float, int]:
        primary = record.get("primary_percent")
        secondary = record.get("secondary_percent")
        return (
            float(primary) if primary is not None else -1.0,
            float(secondary) if secondary is not None else -1.0,
            int(record.get("round") or 0),
        )

    accepted = [
        item
        for item in records
        if item.get("coverage_valid") and item.get("quality", {}).get("accepted")
    ]
    if accepted:
        return max(accepted, key=score), "best_quality_accepted_valid_coverage"
    valid = [item for item in records if item.get("coverage_valid")]
    if valid:
        return max(valid, key=score), "best_valid_coverage_but_quality_gate_failed"
    with_cases = [item for item in records if item.get("case_count") is not None]
    if with_cases:
        return max(with_cases, key=lambda item: int(item.get("round") or 0)), "no_valid_source_coverage"
    return records[-1], "no_usable_suite_record"


def fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crosslang-root", type=Path, required=True)
    parser.add_argument("--experiment-root", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()

    cohort = load(args.cohort)
    base = load(args.crosslang_root / "reports" / "crosslang20_results.json")
    base_by_id = {row["instance_id"]: row for row in base["rows"]}
    final_selection_path = args.experiment_root / "round_4" / "selection_summary.json"
    final_selection = load(final_selection_path) if final_selection_path.exists() else {"rows": []}
    final_decisions = {
        item["instance_id"]: item.get("decision") for item in final_selection.get("rows", [])
    }
    final_rows: list[dict[str, Any]] = []

    for instance in cohort["instances"]:
        instance_id = instance["instance_id"]
        base_row = base_by_id[instance_id]
        baseline_path = args.crosslang_root / "refined_runs" / instance_id / "pipeline_summary.json"
        records = [baseline_record(base_row, baseline_path)]
        for round_number in range(1, 5):
            path = args.experiment_root / f"round_{round_number}" / "final" / instance_id / "pipeline_summary.json"
            if path.exists():
                records.append(
                    record_from_summary(
                        f"round_{round_number}", round_number, path, instance["language"]
                    )
                )

        selected, selection_basis = choose_best(records)
        primary = selected.get("primary_percent")
        if selection_basis == "no_valid_source_coverage":
            stop_reason = "no_valid_source_coverage_after_max_rounds"
        elif not selected["quality"]["accepted"]:
            stop_reason = "quality_gate_failed_at_max_rounds"
        elif primary is not None and float(primary) >= 85.0:
            stop_reason = "accepted_target_coverage"
        else:
            saturated = final_decisions.get(instance_id) == "saturated_below_target"
            stop_reason = "saturated_below_target" if saturated else "max_rounds_below_target"

        labels = base_row.get("coverage_labels")
        if instance["language"] == "go":
            labels = "executable-line/statement"
        pb_values = base_row.get("pb_coverage_primary_secondary") or [None, None]
        pb_coverage_valid = pb_values[0] is not None and pb_values[1] is not None
        pb_coverage_source = "crosslang20_results.json"
        pb_reaudit_path = (
            args.crosslang_root
            / "reports"
            / "coverage_reaudit_20260804"
            / f"{instance_id}.pb_preserved.json"
        )
        if pb_reaudit_path.exists():
            pb_reaudit = load(pb_reaudit_path)
            if pb_reaudit.get("valid"):
                pb_values = [
                    pb_reaudit.get("line_percent"),
                    pb_reaudit.get("branch_percent"),
                ]
                pb_coverage_valid = True
                pb_coverage_source = str(pb_reaudit_path)
        native_values = base_row.get("native_coverage_primary_secondary") or [None, None]
        v31_values = base_row.get("v3_coverage_primary_secondary") or [None, None]
        final_rows.append(
            {
                "instance_id": instance_id,
                "repository": instance["repository"],
                "language": instance["language"],
                "coverage_labels": labels,
                "native_test_declarations": base_row.get("native_test_declarations"),
                "native_coverage": native_values,
                "pb_oracle_test_functions": base_row.get("pb_oracle_behavioral_test_functions"),
                "pb_official_coverage": pb_values,
                "pb_coverage_valid": pb_coverage_valid,
                "pb_coverage_source": pb_coverage_source,
                "pb_all_tests_passed": base_row.get("pb_all_tests_passed"),
                "v3_1_case_count": base_row.get("v3_behavioral_test_functions"),
                "v3_1_coverage": v31_values,
                "v3_1_qemu": qemu_summary(base_row.get("qemu")),
                "v3_2_case_count": selected.get("case_count"),
                "v3_2_coverage": [selected.get("primary_percent"), selected.get("secondary_percent")],
                "v3_2_minus_v3_1_primary_pp": (
                    None
                    if selected.get("primary_percent") is None or v31_values[0] is None
                    else float(selected["primary_percent"]) - float(v31_values[0])
                ),
                "v3_2_minus_pb_primary_pp": (
                    None
                    if selected.get("primary_percent") is None or pb_values[0] is None
                    else float(selected["primary_percent"]) - float(pb_values[0])
                ),
                "v3_2_coverage_valid": selected.get("coverage_valid"),
                "v3_2_coverage_seconds": selected.get("coverage_seconds"),
                "v3_2_qemu": selected.get("qemu"),
                "quality": selected.get("quality"),
                "selected_round": selected.get("round"),
                "selected_label": selected.get("label"),
                "selection_basis": selection_basis,
                "stop_reason": stop_reason,
                "selected_summary_path": selected.get("summary_path"),
                "selected_manifest_path": selected.get("manifest_path"),
                "available_records": records,
            }
        )

    output = {
        "schema": "programbench_oracle_gym_v3_2_refinement11_final_audit_v1",
        "selection_policy": {
            "first": "highest primary coverage among suites passing source coverage and all mandatory quality gates",
            "fallback": "highest valid source coverage, explicitly marked quality-failed; otherwise latest suite record",
            "qemu": "auxiliary sampled dynamic reachability, not a source coverage percentage",
            "pb_holdout": "PB official tests were not provided to generation agents",
        },
        "rows": final_rows,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    columns = [
        "Repo", "Lang", "Metric", "Native tests", "Native cov", "PB tests", "PB cov",
        "PB run", "V3.1 tests", "V3.1 cov", "V3.2 tests", "V3.2 cov", "Delta V3.1", "Round", "Quality",
        "QEMU TB/edges", "QEMU sec", "Stop reason",
    ]
    lines = [
        "# ProgramBench V3.2 refinement-11 final audit",
        "",
        "Selection uses the best quality-accepted, source-coverage-valid suite across inherited V3.1 and rounds 1-4. QEMU is auxiliary sampled reachability, not a coverage percentage.",
        "",
        "Counts: Native is static language-native test declarations; PB is unique active PB pytest behavioral functions; V3.1/V3.2 is retained behavioral cases (one generated pytest function per case). Round 0 means the inherited V3.1 suite. Quality pass requires a non-empty suite, generated-test pass, three-binary consistency, dummy rejection, assertion lint, and source-leak checks. PB run reports whether that official suite completed cleanly in the preserved baseline.",
        "",
        "| " + " | ".join(columns) + " |",
        "|" + "|".join(["---"] + ["---:"] * (len(columns) - 1)) + "|",
    ]
    for row in final_rows:
        qemu = row["v3_2_qemu"] or {}
        values = [
            row["repository"], row["language"], row["coverage_labels"],
            fmt(row["native_test_declarations"]), "/".join(fmt(x) for x in row["native_coverage"]),
            fmt(row["pb_oracle_test_functions"]), "/".join(fmt(x) for x in row["pb_official_coverage"]),
            "pass" if row["pb_all_tests_passed"] else "unstable/fail",
            fmt(row["v3_1_case_count"]), "/".join(fmt(x) for x in row["v3_1_coverage"]),
            fmt(row["v3_2_case_count"]), "/".join(fmt(x) for x in row["v3_2_coverage"]),
            fmt(row["v3_2_minus_v3_1_primary_pp"]),
            fmt(row["selected_round"]), "pass" if row["quality"]["accepted"] else "FAIL",
            f"{fmt(qemu.get('unique_translation_blocks'))}/{fmt(qemu.get('unique_block_edges'))}",
            fmt(qemu.get("runtime_seconds")), row["stop_reason"],
        ]
        lines.append("| " + " | ".join(values) + " |")
    args.output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
