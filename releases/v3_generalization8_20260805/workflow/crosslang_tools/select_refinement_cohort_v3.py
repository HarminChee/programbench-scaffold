#!/usr/bin/env python3
"""Select the next PB-free refinement cohort and maintain saturation history."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--prior-root", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--round", type=int, required=True)
    parser.add_argument("--minimum-coverage", type=float, default=85.0)
    parser.add_argument("--saturation-rounds", type=int, default=2)
    parser.add_argument("--maximum-delta-pp", type=float, default=0.5)
    parser.add_argument("--output-cohort", type=Path, required=True)
    parser.add_argument("--output-summary", type=Path, required=True)
    args = parser.parse_args()
    cohort = read(args.cohort)
    history = read(args.history) if args.history.is_file() else {"instances": {}}
    histories = history.setdefault("instances", {})
    active, rows = [], []
    for item in cohort["instances"]:
        instance = item["instance_id"]
        stop_path = args.prior_root / instance / "stop_gate.json"
        stop = read(stop_path) if stop_path.is_file() else {}
        values = histories.setdefault(instance, [])
        if stop:
            record = {
                "round": args.round - 1,
                "primary_percent": stop.get("primary_percent"),
                "secondary_percent": stop.get("secondary_percent"),
                "quality_passed": str(stop.get("status") or "").startswith("accepted"),
                "prior_root": str(args.prior_root),
            }
            if not values or values[-1].get("round") != record["round"]:
                values.append(record)
        latest = values[-1] if values else {}
        primary, secondary = latest.get("primary_percent"), latest.get("secondary_percent")
        quality_passed = bool(latest.get("quality_passed"))
        target_met = (
            quality_passed and primary is not None and secondary is not None
            and float(primary) >= args.minimum_coverage and float(secondary) >= args.minimum_coverage
        )
        comparable = [row for row in values if row.get("primary_percent") is not None]
        deltas = [
            float(comparable[index]["primary_percent"]) - float(comparable[index - 1]["primary_percent"])
            for index in range(1, len(comparable))
        ]
        saturated = (
            len(deltas) >= args.saturation_rounds
            and all(delta <= args.maximum_delta_pp for delta in deltas[-args.saturation_rounds :])
        )
        reason = "target_met" if target_met else "saturated_below_target" if saturated else "refine"
        if reason == "refine":
            active.append(item)
        rows.append({
            "instance_id": instance, "decision": reason, "primary_percent": primary,
            "secondary_percent": secondary, "recent_primary_deltas_pp": deltas[-args.saturation_rounds :],
        })
    history["schema"] = "programbench_oracle_gym_v3_multiround_history_v1"
    history["minimum_coverage_percent"] = args.minimum_coverage
    for path, payload in (
        (args.history, history),
        (args.output_cohort, {**cohort, "instances": active, "parent_cohort": str(args.cohort)}),
        (args.output_summary, {"round": args.round, "active": len(active), "rows": rows}),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"round": args.round, "active": len(active), "output": str(args.output_cohort)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
