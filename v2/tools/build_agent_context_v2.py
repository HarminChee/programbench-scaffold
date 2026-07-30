#!/usr/bin/env python3
"""Materialize a bounded source-only context for a V2 topic-batch agent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def read_text(path: Path, limit: int = 18_000) -> str:
    try:
        return path.read_bytes()[:limit].decode("utf-8", "replace")
    except OSError:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", type=Path, required=True)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, default=110_000)
    args = parser.parse_args()
    plan = args.plan_dir.resolve()
    batch = args.batch_dir.resolve()
    source = args.source_dir.resolve()
    output = args.output_dir.resolve()
    behavior = json.loads((plan / "behavior_map.json").read_text(encoding="utf-8"))
    rows = json.loads((batch / "matrix_rows.json").read_text(encoding="utf-8"))["rows"]
    needed = {path for row in rows for path in row.get("source_evidence") or []}
    # Include native tests and docs first when they intersect the evidence set;
    # then include the evidence files themselves. This remains target-oracle-free.
    inventory = behavior["inventory"]
    ordered = list(dict.fromkeys([
        *[p for p in inventory.get("documentation_files") or [] if p in needed],
        *[p for p in inventory.get("native_test_files") or [] if p in needed],
        *sorted(needed),
    ]))
    chunks: list[str] = []
    used = 0
    for rel in ordered:
        if used >= args.max_bytes:
            break
        candidate = source / rel
        body = read_text(candidate)
        if not body:
            continue
        chunk = f"\n\n## {rel}\n\n```go\n{body}\n```"
        chunks.append(chunk[: max(0, args.max_bytes - used)])
        used += len(chunks[-1])
    output.mkdir(parents=True, exist_ok=True)
    (output / "source_excerpt.md").write_text("# Source-only V2 Context\n" + "".join(chunks), encoding="utf-8")
    (output / "context_manifest.json").write_text(json.dumps({
        "source_policy": "target source/docs/native tests only; target ProgramBench official oracles forbidden",
        "batch": batch.name,
        "matrix_rows": [row["id"] for row in rows],
        "included_files": ordered,
        "bytes": used,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"batch": batch.name, "included_files": len(ordered), "bytes": used, "output_dir": str(output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
