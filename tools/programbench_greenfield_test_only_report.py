#!/usr/bin/env python3
"""Build the 10-task greenfield test-only ProgramBench report."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

from programbench_trajectory_analyzer import compact_notes, trajectory_summary  # noqa: E402


TASKS = [
    ("sirwart__ripsecrets.34c9e03", "rs", "ripsecrets_test_only"),
    ("wfxr__csview.8ac4de0", "rs", "csview_test_only"),
    ("wfxr__code-minimap.0ddeea5", "rs", "code_minimap_test_only"),
    ("clog-tool__clog-cli.7066cba", "rs", "clog_cli_test_only"),
    ("drew-alleman__datasurgeon.d257cee", "rs", "datasurgeon_test_only"),
    ("sclevine__yj.8016400", "go", "yj_test_only"),
    ("multiprocessio__dsq.c3ae0ba", "go", "dsq_test_only"),
    ("rs__jplot.2a54bcc", "go", "jplot_test_only"),
    ("cmatsuoka__figlet.202a0a8", "c", "figlet_test_only"),
    ("cslarsen__jp2a.61d205f", "c", "jp2a_test_only"),
]

BASELINE_CANDIDATES = [
    "reports/programbench_upper_bound_runs_arch_plan/baseline/{iid}/{iid}.eval.json",
    "reports/programbench_upper_bound_runs/baseline/{iid}/{iid}.eval.json",
    "reports/programbench_upper_bound_runs_more_tasks/baseline/{iid}/{iid}.eval.json",
    "reports/programbench_upper_bound_runs_compact/baseline/{iid}/{iid}.eval.json",
    "reports/programbench_upper_bound_runs_jplot_timeout_plan/baseline/{iid}/{iid}.eval.json",
]

COMPACT_SPEC_CANDIDATES = [
    "reports/programbench_upper_bound_runs_arch_plan/oracle_spec/{iid}/{iid}.eval.json",
    "reports/programbench_upper_bound_runs_jplot_timeout_plan/oracle_spec/{iid}/{iid}.eval.json",
    "reports/programbench_upper_bound_runs_more_tasks/oracle_spec/{iid}/{iid}.eval.json",
    "reports/programbench_upper_bound_runs_compact/oracle_spec/{iid}/{iid}.eval.json",
    "reports/programbench_upper_bound_runs/oracle_spec/{iid}/{iid}.eval.json",
]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def eval_score(path: Path, instances: dict[str, Any], iid: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    from programbench.eval.eval import EvaluationResult
    from programbench.utils.load_data import get_active_branches, get_ignored_tests

    inst = instances[iid]
    result = (
        EvaluationResult.model_validate_json(path.read_text(encoding="utf-8"))
        .for_branches(get_active_branches(inst))
        .without_ignored(get_ignored_tests(inst))
    )
    return {
        "path": str(path),
        "passed": result.n_resolved,
        "total": len(result),
        "score": result.score * 100,
        "rounded": round(result.score * 100),
        "error_code": getattr(result, "error_code", None),
        "warnings": len(getattr(result, "warnings", []) or []),
    }


def first_score(patterns: list[str], instances: dict[str, Any], iid: str) -> dict[str, Any] | None:
    for pattern in patterns:
        score = eval_score(REPO_ROOT / pattern.format(iid=iid), instances, iid)
        if score is not None:
            return score
    return None


def traj_status(path: Path) -> tuple[str | None, dict[str, Any]]:
    payload = load_json(path)
    info = payload.get("info") or {}
    return info.get("exit_status"), info.get("model_stats") or {}


def infer_failure(row: dict[str, Any]) -> str:
    metrics = row["trajectory"].get("metrics") or {}
    score = row["test_only"]["score"]
    notes: list[str] = []
    if row["status"] != "Submitted":
        notes.append(f"budget/status={row['status']}")
    if metrics.get("dependency_attempts", 0) > 0:
        notes.append("external dependency attempts")
    if metrics.get("invalid_error_probes", 0) == 0:
        notes.append("little invalid/error behavior probing")
    if metrics.get("stdin_probes", 0) == 0:
        notes.append("little stdin behavior probing")
    if score < 25:
        notes.append("complex hidden behavior remains uncovered")
    elif score < 50:
        notes.append("partial CLI/core behavior only")
    else:
        notes.append("test scaffold exposed useful behavior")
    return "; ".join(notes)


def fmt_score(score: dict[str, Any] | None) -> str:
    if score is None:
        return "-"
    return f"{score['passed']}/{score['total']} ({score['score']:.2f}%)"


def fmt_delta(score: dict[str, Any] | None, baseline: dict[str, Any] | None) -> str:
    if score is None or baseline is None:
        return "-"
    return f"{score['score'] - baseline['score']:+.2f}"


def build_payload() -> dict[str, Any]:
    from programbench.utils.load_data import load_all_instances

    instances = {item["instance_id"]: item for item in load_all_instances(include_tests=True)}
    run_root = REPO_ROOT / "reports/programbench_greenfield_test_runs"
    rows = []
    for iid, lang, folder in TASKS:
        run_dir = run_root / folder / iid
        traj_path = run_dir / f"{iid}.traj.json"
        eval_path = run_dir / f"{iid}.eval.json"
        status, stats = traj_status(traj_path)
        test_only = eval_score(eval_path, instances, iid)
        if test_only is None:
            raise FileNotFoundError(eval_path)
        traj = trajectory_summary(traj_path)
        row = {
            "task": iid,
            "lang": lang,
            "run_dir": str(run_dir),
            "status": status,
            "cost": stats.get("instance_cost"),
            "api_calls": stats.get("api_calls"),
            "test_only": test_only,
            "baseline": first_score(BASELINE_CANDIDATES, instances, iid),
            "compact_spec": first_score(COMPACT_SPEC_CANDIDATES, instances, iid),
            "trajectory": traj,
        }
        row["failure_hypothesis"] = infer_failure(row)
        rows.append(row)
    return {
        "setting": "greenfield test-only executable-spec scaffold",
        "tasks": rows,
        "aggregate": {
            "n_tasks": len(rows),
            "avg_score": sum(row["test_only"]["score"] for row in rows) / len(rows),
            "avg_cost": sum(row["cost"] or 0 for row in rows) / len(rows),
            "submitted": sum(1 for row in rows if row["status"] == "Submitted"),
            "limits_exceeded": sum(1 for row in rows if row["status"] == "LimitsExceeded"),
        },
    }


def write_markdown(payload: dict[str, Any], path: Path) -> None:
    lines = [
        "# ProgramBench Greenfield Test-Only 10-Task Summary",
        "",
        "## Setting",
        "",
        "We evaluated a greenfield implementation setting on a 10-task dev set. The agent received the original cleanroom documentation plus sanitized executable oracle tests under `./oracle_tests`. It did not receive original source code, raw `tests.json` metadata as the primary interface, internet access, or the reference executable.",
        "",
        "This is an upper-bound experiment: it asks how much direct executable tests/specs can help before we invest in automatically mining comparable specs from black-box behavior.",
        "",
        "## Aggregate",
        "",
        f"- Tasks completed with official eval: {payload['aggregate']['n_tasks']}/10",
        f"- Average test-only score: {payload['aggregate']['avg_score']:.2f}%",
        f"- Average model cost per task: ${payload['aggregate']['avg_cost']:.3f}",
        f"- Agent exit statuses: Submitted={payload['aggregate']['submitted']}, LimitsExceeded={payload['aggregate']['limits_exceeded']}",
        "",
        "## Result Table",
        "",
        "| task | lang | status | cost | pass/total | score | baseline if available | compact-spec if available | delta vs baseline | route | probes/deps | failure/effect hypothesis |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for row in payload["tasks"]:
        tr = row["trajectory"]
        metrics = tr.get("metrics") or {}
        probe_s = "ref {ref}, err {err}, stdin {stdin}, deps {deps}".format(
            ref=metrics.get("reference_runs", 0),
            err=metrics.get("invalid_error_probes", 0),
            stdin=metrics.get("stdin_probes", 0),
            deps=metrics.get("dependency_attempts", 0),
        )
        lines.append(
            "| `{task}` | {lang} | {status} | ${cost:.3f} | {passed}/{total} | {score:.2f}% | {baseline} | {compact} | {delta} | {route} | {probes} | {hyp} |".format(
                task=row["task"],
                lang=row["lang"],
                status=row["status"],
                cost=row["cost"] or 0,
                passed=row["test_only"]["passed"],
                total=row["test_only"]["total"],
                score=row["test_only"]["score"],
                baseline=fmt_score(row["baseline"]),
                compact=fmt_score(row["compact_spec"]),
                delta=fmt_delta(row["test_only"], row["baseline"]),
                route=tr.get("route"),
                probes=probe_s,
                hyp=row["failure_hypothesis"],
            )
        )

    lines.extend(
        [
            "",
            "## Main Observations",
            "",
            "1. The test-only setting is now fully exercised across the 10-task dev set. This satisfies the current experimental TODO at MVP scale: docs plus executable tests were injected into no-internet cleanroom containers and evaluated with official ProgramBench eval.",
            "2. The signal is mixed but useful. Test-only specs strongly help several tasks with concrete CLI/test behavior (`ripsecrets`, `dsq`, `jplot`, `csview`), but are weak for tasks requiring large compatibility surfaces or image/font-heavy behavior (`figlet`, `jp2a`, `clog-cli`, `code-minimap`).",
            "3. Compared with earlier compact-spec/architecture-plan runs where available, raw executable tests are often weaker than a compact behavioral summary. This supports the research direction: the scaffold should not merely dump tests, but should mine, summarize, prioritize, and plan from them.",
            "4. Several runs hit `LimitsExceeded`, so budget pressure is a real part of the failure mode. The agent often spends many steps reading tests or implementing broad features instead of prioritizing high-coverage behavioral clusters.",
            "",
            "## Trajectory Notes",
            "",
            "| task | commands | compiles | notes |",
            "|---|---:|---:|---|",
        ]
    )
    for row in payload["tasks"]:
        tr = row["trajectory"]
        metrics = tr.get("metrics") or {}
        lines.append(
            f"| `{row['task']}` | {tr.get('commands')} | {metrics.get('compile_calls', 0)} | {compact_notes(tr.get('notes') or [])} |"
        )

    lines.extend(
        [
            "",
            "## Implementation/Eval Caveats",
            "",
            "- We fixed the runner so `oracle_tests` and `.git` are excluded from `submission.tar.gz`; all final submissions were checked clean.",
            "- We sanitized official test bundles to avoid source leakage from raw ProgramBench branch tarballs. Two leakage classes were found and fixed: generic `data/` source files and source-like files under `examples/`.",
            "- `csview` printed branch-level `results_read_failed` warnings during CLI eval, although the official parsed result file produced a normal score after filtering. Keep this task marked as having Mac/Docker eval instability.",
            "- The first `code-minimap` eval attempt failed because Docker Desktop was down; it was rerun after restarting Docker, and the rerun result is the one reported here.",
            "- The first `clog-cli` agent attempt ended in Bedrock/API connection error; it was backed up and rerun. The reported `clog-cli` result is the second normal `LimitsExceeded` run.",
            "",
            "## Next Research Step",
            "",
            "Use this 10-task result as the dev-set baseline for a better scaffold: executable-test triage, behavior-cluster summarization, and implementation planning. The target is to approach the compact-spec/architecture-plan upper bound without using oracle test metadata directly as final unfair input.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out-md",
        type=Path,
        default=REPO_ROOT / "reports/programbench_greenfield_test_only_10task_summary_2026-07-02.md",
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=REPO_ROOT / "reports/programbench_greenfield_test_only_10task_summary_2026-07-02.json",
    )
    args = parser.parse_args()
    payload = build_payload()
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_markdown(payload, args.out_md)
    print(json.dumps({"markdown": str(args.out_md), "json": str(args.out_json)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
