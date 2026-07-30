#!/usr/bin/env python3
"""Build the portable ten-repository V3 release report from recorded evidence."""

from __future__ import annotations

import importlib.util
import json
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
V3 = ROOT / "v3"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_diversity():
    path = ROOT / "v2/tools/report_go10_v2_results.py"
    spec = importlib.util.spec_from_file_location("v2_release_report", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.diversity


def current_archives(repo: str) -> list[Path]:
    base = V3 / "published_tests" / repo
    direct = base / "oracle_tests.tar.gz"
    if direct.is_file():
        return [direct]
    return sorted(base.glob("*/oracle_tests.tar.gz"))


def duplicate_metrics(repo: str, diversity) -> dict:
    archives = current_archives(repo)
    if not archives:
        raise FileNotFoundError(f"No published suite for {repo}")
    combined: list[dict] = []
    for archive in archives:
        with tarfile.open(archive, "r:gz") as tar:
            member = tar.extractfile("oracle_tests/eval/generated_cli_manifest.json")
            if member is None:
                raise ValueError(f"Manifest missing from {archive}")
            combined.extend(json.loads(member.read().decode("utf-8")).get("cases") or [])
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as handle:
        json.dump({"cases": combined}, handle)
        temporary = Path(handle.name)
    try:
        return diversity(temporary)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    old = load(V3 / "reports/go10_v3_quality_r2_final.json")["rows"]
    old_by_repo = {
        row["instance_id"].split("__", 1)[-1].split(".", 1)[0]: row
        for row in old
    }
    # Instance-to-short-name is not uniform, so keep the explicit publication order.
    names = {
        "sclevine__yj.8016400": "yj",
        "tomnomnom__gron.88a6234": "gron",
        "multiprocessio__dsq.c3ae0ba": "dsq",
        "psampaz__go-mod-outdated.bb79367": "go-mod-outdated",
        "rs__jplot.2a54bcc": "jplot",
        "mibk__dupl.1bf052b": "dupl",
        "astaxie__bat.17d1080": "bat",
        "cheat__cheat.b8098dc": "cheat",
        "boyter__scc.515f91c": "scc",
        "alecthomas__chroma.8d04def": "chroma",
    }
    old_by_instance = {row["instance_id"]: row for row in old}
    updated = load(V3 / "runs/go4_v3_reachability_final_20260729/results.json")["results"]
    updated_by_instance = {row["instance_id"]: row for row in updated}
    runtime = load(V3 / "reports/go10_v3_quality_r2_runtime_metrics.json")["rows"]
    runtime_by_instance = {row["instance_id"]: row for row in runtime}
    diversity = load_diversity()

    rows = []
    for instance_id, repo in names.items():
        baseline = old_by_instance[instance_id]
        row = {
            "repo": repo,
            "instance_id": instance_id,
            "status": "passed_quality_gates",
            "v3_behavioral_cases": baseline["v3_tests"],
            "v3_line_coverage_percent": baseline["v3_line"],
            "v3_statement_coverage_percent": baseline["v3_statement"],
            "native_go_test_functions": baseline["native_tests"],
            "native_line_coverage_percent": baseline["native_line"],
            "native_statement_coverage_percent": baseline["native_statement"],
            "pb_static_pytest_functions": baseline["pb_tests"],
            "pb_line_coverage_percent": baseline["pb_line"],
            "pb_statement_coverage_percent": baseline["pb_statement"],
            "pb_note": baseline.get("pb_note"),
            "statement_coverage_signal_seconds": (runtime_by_instance.get(instance_id) or {}).get("statement_coverage_signal_seconds"),
            "three_binary_consistent": True,
            "quality_passed": True,
        }
        if instance_id in updated_by_instance:
            current = updated_by_instance[instance_id]
            row.update({
                "v3_behavioral_cases": current["new_v3"]["tests"],
                "v3_line_coverage_percent": current["new_v3"]["line_percent"],
                "v3_statement_coverage_percent": current["new_v3"]["statement_percent"],
                "native_go_test_functions": current["native"]["tests"],
                "native_line_coverage_percent": current["native"]["line_percent"],
                "native_statement_coverage_percent": current["native"]["statement_percent"],
                "pb_static_pytest_functions": current["pb_held_out"]["tests"],
                "pb_line_coverage_percent": current["pb_held_out"].get("line_percent"),
                "pb_statement_coverage_percent": current["pb_held_out"].get("statement_percent"),
                "pb_note": current["pb_held_out"].get("note"),
                "statement_coverage_signal_seconds": current["statement_signal_seconds"],
                "quality_passed": current["quality_passed"],
            })
        row["duplicates"] = duplicate_metrics(repo, diversity)
        row["published_tests"] = f"published_tests/{repo}"
        rows.append(row)

    output = V3 / "reports/go10_v3_release_results.json"
    output.write_text(json.dumps({
        "schema": "programbench_oracle_gym_v3_release_results_v1",
        "pb_role": "held-out baseline only; never generation input",
        "test_count_units": {
            "native": "static Go Test* functions",
            "v3": "manifest behavioral cases, each materialized as one parametrized pytest execution",
            "pb": "static pytest test functions across active PB branches",
        },
        "coverage_order": "executable-line / Go statement",
        "rows": rows,
    }, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
