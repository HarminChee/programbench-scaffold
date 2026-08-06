#!/usr/bin/env python3
"""Convert per-case source/QEMU observations into reusable V3 novelty signals."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def load(path: Path | None) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig")) if path and path.is_file() else {}


def rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("cases") or []
    if isinstance(raw, dict):
        return [{"name": name, **value} for name, value in raw.items() if isinstance(value, dict)]
    return [item for item in raw if isinstance(item, dict) and item.get("name")]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qemu-report", type=Path)
    parser.add_argument("--source-report", type=Path, help="Optional per-case report with source_block_ids arrays")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    qemu = load(args.qemu_report)
    source = load(args.source_report)
    qemu_rows = list((qemu.get("qemu") or {}).get("cases") or [])
    source_rows = rows(source)
    edge_frequency = Counter(
        edge for row in qemu_rows for edge in set(row.get("first_party_edge_ids") or [])
    )
    block_frequency = Counter(
        block for row in source_rows for block in set(row.get("source_block_ids") or [])
    )
    signals: dict[str, dict[str, Any]] = {}
    for row in qemu_rows:
        name = str(row.get("name"))
        edges = set(row.get("first_party_edge_ids") or [])
        signals.setdefault(name, {}).update({
            "new_dynamic_edges": sum(edge_frequency[edge] == 1 for edge in edges),
            "dynamic_edge_count": len(edges),
            "dynamic_signal_valid": not row.get("trace_limit_hit") and bool(row.get("returncode_matches_oracle")),
        })
    for row in source_rows:
        name = str(row.get("name"))
        blocks = set(row.get("source_block_ids") or [])
        signals.setdefault(name, {}).update({
            "new_source_blocks": sum(block_frequency[block] == 1 for block in blocks),
            "source_block_count": len(blocks),
            "source_signal_valid": bool(row.get("valid", True)),
        })
    payload = {
        "schema": "programbench_oracle_gym_v3_case_novelty_signals_v1",
        "definition": (
            "new_* counts are suite-unique first-party items. They prioritize representative cases; "
            "they are not whole-program coverage percentages."
        ),
        "cases": signals,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"cases": len(signals), "output": str(args.output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
