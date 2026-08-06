#!/usr/bin/env python3
"""Fail closed on incomplete or unsafe V2 behavior-planning artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED = ("behavior_map.json", "scenario_matrix.json", "fixture_catalog.json", "generation_contract.json")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.plan_dir.expanduser().resolve()
    missing = [name for name in REQUIRED if not (root / name).is_file()]
    errors: list[str] = []
    if missing:
        errors.append(f"missing artifacts: {', '.join(missing)}")
    if not errors:
        behavior = load(root / "behavior_map.json")
        matrix = load(root / "scenario_matrix.json")
        fixtures = load(root / "fixture_catalog.json")
        contract = load(root / "generation_contract.json")
        families = behavior.get("families") or []
        rows = matrix.get("rows") or []
        if not families:
            errors.append("behavior map has no detected families")
        if len(rows) < len(families) * 3:
            errors.append("matrix does not contain normal/boundary/error rows for every family")
        forbidden = str(behavior.get("source_policy") or "").lower()
        if "official" not in forbidden or "forbidden" not in forbidden:
            errors.append("source policy does not forbid target official oracles")
        if "coverage" not in str(contract.get("coverage_rule") or "").lower():
            errors.append("generation contract lacks coverage boundary")
        fixture_ids = {item.get("id") for item in fixtures.get("fixtures") or []}
        required_fixtures = {fid for row in rows for fid in row.get("fixture_requirements") or []}
        if not required_fixtures.issubset(fixture_ids):
            errors.append("matrix references fixture IDs absent from fixture catalog")
    result = {"passed": not errors, "plan_dir": str(root), "errors": errors}
    print(json.dumps(result, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
