#!/usr/bin/env python3
"""Merge captured oracle manifests while namespacing their fixture files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


FILE_FIELDS = ("stdin_file", "stdout_file", "stderr_file")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("manifests", nargs="+", type=Path)
    args = parser.parse_args()

    output = args.output.resolve()
    fixture_root = output.parent / "fixtures"
    fixture_root.mkdir(parents=True, exist_ok=True)
    cases: list[dict] = []
    sources = []

    for source_index, manifest_path in enumerate(args.manifests):
        manifest_path = manifest_path.resolve()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        namespace = f"source_{source_index:02d}"
        source_fixtures = (
            manifest_path.parent
            / "fixtures"
            / str(manifest.get("fixture_subdir") or "")
        )
        target_fixtures = fixture_root / namespace
        source_cases = list(manifest.get("cases") or [])
        for case_index, raw in enumerate(source_cases):
            case = dict(raw)
            case["name"] = f"{namespace}_{case_index:04d}_{case.get('name', 'case')}"[:220]
            for field in FILE_FIELDS:
                value = case.get(field)
                if value:
                    case[field] = f"fixtures/{namespace}/{value}"
                    # Dynamic collectors need the captured stdin bytes, but
                    # compare stdout/stderr through hashes already present in
                    # the manifest. Copying only stdin keeps unions of
                    # thousand-case suites inexpensive on mounted filesystems.
                    if field == "stdin_file":
                        source_file = source_fixtures / str(value)
                        target_file = target_fixtures / str(value)
                        target_file.parent.mkdir(parents=True, exist_ok=True)
                        content = source_file.read_bytes()
                        if not target_file.is_file() or target_file.stat().st_size != len(content):
                            target_file.write_bytes(content)
            cases.append(case)
        sources.append({"manifest": str(manifest_path), "cases": len(source_cases)})

    result = {
        "schema": "programbench_oracle_gym_v3_captured_manifest_union",
        "sources": sources,
        "cases": cases,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "sources": len(sources), "cases": len(cases)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
