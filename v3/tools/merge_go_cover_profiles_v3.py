#!/usr/bin/env python3
"""Merge compatible Go ``mode: set`` profiles using block-wise union."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def read_profile(path: Path) -> tuple[str, dict[str, tuple[int, int]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or not lines[0].startswith("mode: "):
        raise ValueError(f"invalid Go cover profile: {path}")
    mode = lines[0].split(":", 1)[1].strip()
    blocks: dict[str, tuple[int, int]] = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        location, statements, count = line.rsplit(" ", 2)
        value = (int(statements), int(count))
        previous = blocks.get(location)
        if previous is not None and previous[0] != value[0]:
            raise ValueError(f"incompatible statement count for {location}")
        blocks[location] = value
    return mode, blocks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("profiles", nargs="+", type=Path)
    args = parser.parse_args()

    merged: dict[str, tuple[int, int]] = {}
    mode: str | None = None
    for profile in args.profiles:
        current_mode, blocks = read_profile(profile)
        if mode is None:
            mode = current_mode
        if current_mode != mode or mode != "set":
            raise ValueError("V3 profile union currently requires compatible mode: set profiles")
        for location, (statements, count) in blocks.items():
            previous = merged.get(location)
            if previous is not None and previous[0] != statements:
                raise ValueError(f"incompatible profiles at {location}")
            merged[location] = (statements, max(count, previous[1] if previous else 0))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = [f"mode: {mode}"]
    payload.extend(
        f"{location} {statements} {count}"
        for location, (statements, count) in sorted(merged.items())
    )
    args.output.write_text("\n".join(payload) + "\n", encoding="utf-8")

    total_statements = sum(statements for statements, _ in merged.values())
    covered_statements = sum(statements for statements, count in merged.values() if count > 0)
    summary = {
        "schema": "programbench_oracle_gym_v3_go_profile_union",
        "mode": mode,
        "profiles": [str(path) for path in args.profiles],
        "profile": str(args.output),
        "block_count": len(merged),
        "covered_statements": covered_statements,
        "total_statements": total_statements,
        "statement_coverage_percent": round(100.0 * covered_statements / total_statements, 1)
        if total_statements
        else 0.0,
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
