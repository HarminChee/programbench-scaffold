#!/usr/bin/env python3
"""Build a language-neutral, bounded source/docs/native-test context for V3."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def read(path: Path, limit: int) -> str:
    try:
        return path.read_bytes()[:limit].decode("utf-8", "replace")
    except OSError:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan-dir", type=Path, required=True)
    ap.add_argument("--batch-dir", type=Path, required=True)
    ap.add_argument("--source-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--max-bytes", type=int, default=220_000)
    args = ap.parse_args()
    plan, batch, source, out = (
        args.plan_dir.resolve(),
        args.batch_dir.resolve(),
        args.source_dir.resolve(),
        args.output_dir.resolve(),
    )
    spec = json.loads((plan / "instance_spec.json").read_text())
    inventory = json.loads((plan / "repository_inventory.json").read_text())
    rows = json.loads((batch / "matrix_rows.json").read_text())["rows"]
    evidence = {
        item["path"]
        for row in rows
        for item in row.get("evidence", [])
        if isinstance(item, dict) and item.get("path")
    }
    # Matrix evidence and executable entrypoints must not be crowded out by a
    # repository with thousands of native tests or documentation files.
    fallback_entrypoints = [
        rel
        for rel in inventory.get("source_files", [])
        if rel in {"main.go", "src/main.rs"}
        or "/cmd/" in f"/{rel}"
        or rel.startswith("cmd/")
        or Path(rel).name in {"main.c", "main.cc", "main.cpp"}
    ]
    entrypoints = list(dict.fromkeys([
        *inventory.get("detected_entrypoints", []),
        *fallback_entrypoints,
    ]))
    ordered = list(dict.fromkeys([
        *entrypoints,
        *sorted(evidence),
        *inventory.get("documentation_files", [])[:20],
        *inventory.get("native_test_files", [])[:30],
        *inventory.get("first_party_source_files", inventory.get("source_files", [])),
    ]))
    chunks, included, used = [], [], 0
    per_file = max(8_000, min(40_000, args.max_bytes // max(1, min(len(ordered), 12))))
    for rel in ordered:
        if used >= args.max_bytes:
            break
        candidate = source / rel
        # Avoid crowding out project behavior with generated/amalgamated
        # dependency headers. Such files remain available in the pinned source,
        # but are summarized by first-party call sites instead of pasted.
        if candidate.suffix.lower() in {".h", ".hh", ".hpp"} and candidate.stat().st_size > 150_000:
            continue
        body = read(candidate, per_file)
        if not body:
            continue
        chunk = f"\n\n## {rel}\n\n```text\n{body}\n```"
        chunk = chunk[: args.max_bytes - used]
        chunks.append(chunk)
        included.append(rel)
        used += len(chunk)
    feedback_path = plan / "refinement_feedback.md"
    feedback = read(feedback_path, 60_000) if feedback_path.is_file() else ""
    header = (
        "# V3 source-only context\n\n"
        f"Instance: {spec['instance_id']}\n\nLanguage: {spec['language']}\n\n"
        "Target PB official oracle tests are not included.\n"
    )
    if feedback:
        header += (
            "\n# Prior-run reachability and coverage feedback\n\n"
            "This section was derived only from our own earlier candidates, "
            "reference-binary captures, and source coverage. It contains no "
            "target PB oracle material.\n\n"
            + feedback
            + "\n"
        )
    out.mkdir(parents=True, exist_ok=True)
    (out / "source_context.md").write_text(header + "".join(chunks), encoding="utf-8")
    (out / "context_manifest.json").write_text(json.dumps({
        "source_policy": "pinned source/docs/native tests only",
        "refinement_feedback_included": bool(feedback),
        "included_files": included,
        "bytes": used,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"included_files": len(included), "bytes": used, "output": str(out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
