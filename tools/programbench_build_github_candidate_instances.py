#!/usr/bin/env python3
"""Build candidate-only Gym skeletons for external GitHub repositories.

This is intentionally a static intake step. It records pinned repo metadata,
non-overlap with ProgramBench official tasks, and the gates that still need to
pass before a repo becomes an accepted Gym instance.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_ROOT = REPO_ROOT / "external" / "ProgramBench" / "src" / "programbench" / "data" / "tasks"
SCHEMA_VERSION = "0.1"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_simple_yaml(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value and not value.startswith("[") and not value.startswith("{"):
            data[key.strip()] = value.strip("'\"")
    return data


def programbench_repositories(tasks_root: Path = TASKS_ROOT) -> set[str]:
    repos: set[str] = set()
    if not tasks_root.exists():
        return repos
    for task_yaml in tasks_root.glob("*/task.yaml"):
        meta = parse_simple_yaml(task_yaml)
        repo = str(meta.get("repository") or "").lower()
        if repo:
            repos.add(repo)
    return repos


def slug_for_repo(repository: str, commit: str) -> str:
    owner, name = repository.split("/", 1)
    owner_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", owner).strip("-")
    name_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-")
    return f"{owner_slug}__{name_slug}.{commit[:7]}"


def gate(name: str, status: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "status": status, "details": details or {}}


def build_candidate(
    *,
    candidate: dict[str, Any],
    output_root: Path,
    official_repos: set[str],
) -> dict[str, Any]:
    repository = candidate["repository"]
    commit = candidate["commit"]
    instance_id = slug_for_repo(repository, commit)
    instance_dir = output_root / instance_id
    if instance_dir.exists():
        shutil.rmtree(instance_dir)
    for child in ("cleanroom", "private", "oracle_tests", "eval"):
        (instance_dir / child).mkdir(parents=True, exist_ok=True)

    overlaps_programbench = repository.lower() in official_repos
    gates = [
        gate("candidate_metadata_loaded", "pass", {"config_fields": sorted(candidate)}),
        gate("programbench_repo_non_overlap", "pass" if not overlaps_programbench else "fail", {"repository": repository}),
        gate("license_recorded", "pass" if candidate.get("license") else "fail", {"license": candidate.get("license")}),
        gate(
            "build_command_recorded",
            "pass" if candidate.get("build_command_hint") else "fail",
            {"build_command_hint": candidate.get("build_command_hint")},
        ),
        gate("clone_pinned_commit", "skipped", {"reason": "candidate-only static intake"}),
        gate("mac_native_build_smoke", "skipped", {"reason": "next intake step"}),
        gate("reference_binary_materialized", "skipped", {"reason": "requires accepted build recipe"}),
        gate("oracle_tests_created", "skipped", {"reason": "requires existing-test adaptation or generated tests"}),
        gate("tests_pass_reference", "skipped", {"reason": "requires built reference binary"}),
        gate("dummy_does_not_pass_all", "skipped", {"reason": "requires executable tests"}),
        gate("offline_reproducible_eval", "skipped", {"reason": "requires Linux/Docker validation"}),
        gate("source_leakage_static", "skipped", {"reason": "requires sanitized test bundle"}),
        gate("coverage_or_mutation_ready", "skipped", {"reason": "Phase 3/4 instrumentation"}),
    ]

    metadata = {
        "schema_version": SCHEMA_VERSION,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "instance_id": instance_id,
        "source_type": "github_repo_candidate",
        "status": "candidate_only",
        "repository": repository,
        "clone_url": candidate.get("clone_url"),
        "html_url": candidate.get("html_url"),
        "commit": commit,
        "default_branch": candidate.get("default_branch"),
        "language": candidate.get("language"),
        "license": candidate.get("license"),
        "build_command_hint": candidate.get("build_command_hint"),
        "why_pilot": candidate.get("why_pilot"),
        "cleanroom": {
            "path": "cleanroom",
            "agent_visible": True,
            "materialization": {"status": "not_started"},
            "reference_executable": "cleanroom/executable",
        },
        "oracle_tests": {
            "sanitized_path": "oracle_tests/sanitized",
            "status": "not_started",
            "hidden_for_binary_to_test_inference": True,
        },
        "quality_gates": gates,
    }
    write_json(instance_dir / "metadata.json", metadata)

    private_manifest = {
        "source_visibility": "private-only",
        "repository": repository,
        "clone_url": candidate.get("clone_url"),
        "commit": commit,
        "notes": [
            "Source checkout, build logs, coverage, and mutants will be written here after clone/build validation.",
            "Do not expose private/ to inference agents.",
        ],
    }
    write_json(instance_dir / "private" / "source_manifest.json", private_manifest)

    binary_sample = {
        "schema_version": SCHEMA_VERSION,
        "instance_id": instance_id,
        "status": "candidate_only",
        "input": {
            "docs_path": "cleanroom/",
            "reference_binary": "cleanroom/executable",
            "source_access": False,
        },
        "target_output": {
            "type": "executable_tests",
            "preferred_framework": "pytest",
            "convention": "tests execute ./executable in their working directory",
        },
        "reward_signals": [
            "tests_pass_reference_reserved",
            "tests_fail_dummy_reserved",
            "coverage_delta_reserved",
            "mutation_kill_reserved",
            "downstream_coding_agent_score_reserved",
        ],
    }
    write_json(instance_dir / "binary_to_test_sample.json", binary_sample)

    quality = {
        "schema_version": SCHEMA_VERSION,
        "instance_id": instance_id,
        "summary": {
            "candidate_static_gates_passed": all(
                row["status"] == "pass"
                for row in gates
                if row["name"]
                in {
                    "candidate_metadata_loaded",
                    "programbench_repo_non_overlap",
                    "license_recorded",
                    "build_command_recorded",
                }
            ),
            "accepted_for_training": False,
        },
        "gates": gates,
    }
    write_json(instance_dir / "quality_report.json", quality)

    (instance_dir / "README.md").write_text(
        "\n".join(
            [
                f"# {instance_id}",
                "",
                "Candidate-only external GitHub Gym instance.",
                "",
                f"- Repository: `{repository}`",
                f"- Commit: `{commit}`",
                f"- Build hint: `{candidate.get('build_command_hint')}`",
                "",
                "This instance is not accepted training data until clone, build, test,",
                "dummy, offline, source-leakage, and coverage/mutation gates pass.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    return {
        "instance_id": instance_id,
        "repository": repository,
        "language": candidate.get("language"),
        "license": candidate.get("license"),
        "candidate_static_gates_passed": quality["summary"]["candidate_static_gates_passed"],
        "status": "candidate_only",
    }


def write_summary(output_root: Path, config: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    summary = {
        "schema_version": SCHEMA_VERSION,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "name": config.get("name"),
        "output_root": str(output_root),
        "instances": rows,
        "aggregate": {
            "instances": len(rows),
            "candidate_static_gates_passed": sum(1 for row in rows if row["candidate_static_gates_passed"]),
        },
    }
    write_json(output_root / "github_candidate_summary.json", summary)

    lines = [
        "# GitHub Candidate Gym Summary",
        "",
        f"- Build: `{summary['name']}`",
        f"- Instances: {len(rows)}",
        f"- Candidate static gates passed: {summary['aggregate']['candidate_static_gates_passed']}/{len(rows)}",
        "",
        "| instance | repo | lang | license | static gates | status |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| `{instance_id}` | `{repository}` | {language} | {license} | {candidate_static_gates_passed} | {status} |".format(
                **row
            )
        )
    lines.append("")
    (output_root / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    output_root = args.output_root if args.output_root.is_absolute() else REPO_ROOT / args.output_root
    config = read_json(config_path)
    candidates = list(config.get("candidates") or [])
    if not candidates:
        raise ValueError(f"No candidates in {config_path}")
    if output_root.exists():
        if not args.overwrite:
            raise FileExistsError(output_root)
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)

    official_repos = programbench_repositories()
    rows = [build_candidate(candidate=item, output_root=output_root, official_repos=official_repos) for item in candidates]
    write_summary(output_root, config, rows)
    print(json.dumps({"instances": len(rows), "candidate_static_gates_passed": sum(row["candidate_static_gates_passed"] for row in rows)}, indent=2))
    return 0 if all(row["candidate_static_gates_passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
