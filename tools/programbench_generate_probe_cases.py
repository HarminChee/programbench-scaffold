#!/usr/bin/env python3
"""Generate generic black-box CLI probe cases for ProgramBench-style tasks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


STDIN_SEEDS = [
    "",
    "hello\n",
    "alpha beta\nsecond line\n",
    "1,2,3\n4,5,6\n",
    "{\"name\":\"programbench\",\"value\":7}\n",
]


def dedup(cases: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for case in cases:
        key = json.dumps(case, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(case)
    return out


def generic_cases(include_files: bool) -> list[dict]:
    cases = [
        {"name": "help_long", "args": ["--help"]},
        {"name": "help_short", "args": ["-h"]},
        {"name": "version_long", "args": ["--version"]},
        {"name": "invalid_flag", "args": ["--programbench-invalid-flag"]},
    ]
    for index, seed in enumerate(STDIN_SEEDS):
        cases.append({"name": f"stdin_seed_{index}", "stdin": seed})
    if include_files:
        cases.extend(
            [
                {
                    "name": "single_text_file",
                    "args": ["input.txt"],
                    "files": {"input.txt": "alpha beta\nsecond line\n"},
                },
                {
                    "name": "empty_file",
                    "args": ["empty.txt"],
                    "files": {"empty.txt": ""},
                },
                {
                    "name": "csv_file",
                    "args": ["table.csv"],
                    "files": {"table.csv": "a,b,c\n1,2,3\n4,5,6\n"},
                },
                {
                    "name": "missing_file",
                    "args": ["does-not-exist.txt"],
                },
            ]
        )
    return cases


def wc_cases() -> list[dict]:
    cases = generic_cases(include_files=True)
    for flag in ["-l", "-w", "-c", "-m"]:
        cases.append({"name": f"stdin_{flag[1:]}", "args": [flag], "stdin": "alpha beta\nsecond line\n"})
        cases.append(
            {
                "name": f"file_{flag[1:]}",
                "args": [flag, "input.txt"],
                "files": {"input.txt": "alpha beta\nsecond line\n"},
            }
        )
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["generic", "wc"], default="generic")
    parser.add_argument("--include-files", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    cases = wc_cases() if args.profile == "wc" else generic_cases(args.include_files)
    cases = dedup(cases)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"profile": args.profile, "cases": len(cases), "out": str(args.out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
