#!/usr/bin/env python3
"""Validate completeness and leak boundaries of a V3 plan."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED = (
    "instance_spec.json",
    "repository_inventory.json",
    "capability_graph.json",
    "scenario_matrix.json",
    "fixture_plan.json",
    "generation_contract.json",
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan-dir", type=Path, required=True)
    args = ap.parse_args()
    root = args.plan_dir.resolve()
    errors = [f"missing {name}" for name in REQUIRED if not (root / name).is_file()]
    if not errors:
        graph = json.loads((root / "capability_graph.json").read_text())
        matrix = json.loads((root / "scenario_matrix.json").read_text())
        contract = json.loads((root / "generation_contract.json").read_text())
        if not graph.get("capabilities"):
            errors.append("no capabilities discovered")
        if not matrix.get("rows"):
            errors.append("no scenario rows")
        if not contract.get("target_pb_oracles_forbidden"):
            errors.append("target PB oracle leak boundary absent")
        if not contract.get("retain_repetitions"):
            errors.append("repetition retention policy absent")
    print(json.dumps({"passed": not errors, "plan_dir": str(root), "errors": errors}, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
