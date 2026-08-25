#!/usr/bin/env python3
"""Deterministic V4 stage adapter used only by smoke/fault tests.

It never claims to be a production oracle generator.  Its purpose is to test
the controller, checkpoints, marginal policy, isolation validation and atomic
freeze path without model calls or repository mutation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any


def provenance(stage: str, image_id: str, binary_sha: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "image_id": image_id,
        "network": "none",
        "binary_sha256": binary_sha,
        "security": {
            "read_only_rootfs": True,
            "cap_drop_all": True,
            "no_new_privileges": True,
            "non_root_user": True,
            "docker_socket_mounted": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    stage = request["stage"]
    iteration = int(request["iteration"])
    repo = request["repository"]
    image = repo["runtime_image_id"]
    binary = "1" * 64
    delay = float((repo.get("smoke_profile") or {}).get("delay_seconds") or 0)
    if delay:
        time.sleep(delay)
    profile = repo.get("smoke_profile") or {}
    fail_stage = profile.get("fail_stage_once")
    fail_iteration = int(profile.get("fail_stage_iteration") or 0)
    if fail_stage == stage and fail_iteration == iteration:
        marker = Path(request["repository_root"]) / f".smoke-failed-{stage}-{iteration}"
        if not marker.exists():
            marker.write_text("intentional one-shot smoke failure\n", encoding="utf-8")
            return 7
    response: dict[str, Any] = {
        "schema": "programbench_v4_stage_response_v1",
        "stage": stage,
        "scope_sha256": request["scope_sha256"],
    }
    expected = {
        "oracle_capture": "oracle_capture",
        "quick_coverage": "quick_coverage",
        "quality_gates": "quality_gates",
        "final_capture": "oracle_capture",
        "full_coverage": "full_coverage",
        "freeze_verification": "freeze_verification",
    }.get(stage)
    if expected:
        response["execution_provenance"] = [provenance(expected, image, binary)]
    if stage == "generate":
        response["generation_scope"] = {
            "pb_official_tests_visible": False,
            "source_visible_to_generation_agent": True,
            "native_tests_visible_to_generation_agent": True,
            "inference_image_binary_only": True,
            "credentials_mounted_into_container": False,
        }
        response["execution_provenance"] = [
            provenance("agent_tool_execution", image, binary)
        ]
    if stage == "dependency_prefetch":
        item = provenance("dependency_prefetch", image, binary)
        item["network"] = "bridge"
        response["execution_provenance"] = [item]
        response["dependency_cache"] = {"scope_bound": True, "resumed": False}
    if stage == "preflight":
        response["execution_provenance"] = [
            provenance("source_build", image, binary),
            provenance("native_tests", image, binary),
        ]
    if stage == "quality_gates" and (repo.get("smoke_profile") or {}).get("quality_unrepairable"):
        response["quality_unrepairable"] = True
    if stage == "quality_gates" and (repo.get("smoke_profile") or {}).get("suite_fuse_blocked"):
        response.update(
            {
                "quality_unrepairable": True,
                "suite_fuse_blocked": True,
                "rolled_back_retained_cases": 17,
            }
        )
    if stage == "static_select":
        profile = repo.get("smoke_profile") or {}
        projected = profile.get("projected_cumulative_cases")
        overflow_count = int(profile.get("raw_overflow_count") or 0)
        context = request.get("workflow_context") or {}
        generated = int(context.get("recommended_tranche_size") or 16)
        unique_raw = int(context.get("total_raw_candidates") or 0) + generated
        repo_root = Path(request["repository_root"])
        current_path = repo_root / "candidates/current.json"
        current_path.parent.mkdir(parents=True, exist_ok=True)
        current_count = int(projected) if projected is not None else 10 + iteration
        if not overflow_count:
            current_path.write_text(
                json.dumps(
                    {
                        "profile": "programbench_v4_smoke",
                        "cases": [
                            {"name": f"smoke-{index}", "args": [str(index)]}
                            for index in range(current_count)
                        ],
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        if projected is not None:
            response.update(
                {
                    "selected_new_cases": int(projected),
                    "deferred_new_cases": 0,
                    "cumulative_cases": int(projected),
                    "replacement_planned": bool(
                        (repo.get("smoke_profile") or {}).get("replacement_planned")
                    ),
                }
            )
        if overflow_count:
            effective = int(context.get("raw_candidate_fuse") or unique_raw)
            unique_raw = min(unique_raw, effective)
            overflow_path = repo_root / f"artifacts/raw_candidate_overflow/iteration-{iteration:04d}.json"
            overflow_path.parent.mkdir(parents=True, exist_ok=True)
            overflow_path.write_text(
                json.dumps({"overflow_unique_candidates": overflow_count}),
                encoding="utf-8",
            )
            response.update(
                {
                    "raw_overflow_count": overflow_count,
                    "raw_overflow_path": str(overflow_path),
                    "raw_overflow_sha256": hashlib.sha256(overflow_path.read_bytes()).hexdigest(),
                    "candidate_state_committed": False,
                }
            )
        response["unique_persisted_raw_candidates"] = unique_raw
        response["cumulative_raw_candidates"] = unique_raw
    if stage == "evaluate_marginal":
        profile = repo.get("smoke_profile") or {}
        coverage = list(profile.get("coverage") or [20.0, 20.1, 20.15])
        novelty = list(profile.get("novelty") or [4, 0, 0])
        index = min(iteration - 1, len(coverage) - 1)
        generated = int(request.get("workflow_context", {}).get("recommended_tranche_size") or 16)
        prior_raw = int(request.get("workflow_context", {}).get("total_raw_candidates") or 0)
        response.update(
            {
                "observation": {
                    "primary_coverage": float(coverage[index]),
                    "coverage_scope_sha256": "2" * 64,
                    "retained_cases": 10 + iteration,
                    "generated_candidates": generated,
                    "wall_seconds": 0.1,
                    "new_behavior_families": int(novelty[index]),
                    "new_coverage_units": int(novelty[index]),
                },
                "total_raw_candidates": prior_raw,
                "unique_persisted_raw_candidates": prior_raw,
                "retained_suite_cases": 10 + iteration,
            }
        )
    if stage == "full_coverage":
        active_cases = int(request.get("workflow_context", {}).get("retained_suite_cases") or 0)
        coverage = list(profile.get("coverage") or [20.0, 20.1, 20.15])
        response["coverage"] = {
            "primary_coverage": float(coverage[-1]),
            "coverage_valid": True,
            "sample_policy": "complete_retained_suite",
            "sampled_cases": active_cases,
        }
    if stage == "final_capture":
        response["captured_cases"] = int(
            request.get("workflow_context", {}).get("retained_suite_cases") or 0
        )
    if stage == "freeze":
        response["freeze_accepted"] = True
    args.response.parent.mkdir(parents=True, exist_ok=True)
    args.response.write_text(
        json.dumps(response, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
