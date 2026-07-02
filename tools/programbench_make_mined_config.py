#!/usr/bin/env python3
"""Build a mini-swe-agent config override for fair mined behavior specs."""

from __future__ import annotations

import argparse
from pathlib import Path


def clean_text(value: str) -> str:
    cleaned = []
    for char in value:
        if char in "\n\r\t" or char.isprintable():
            cleaned.append(char)
        else:
            cleaned.append(f"\\x{ord(char):02x}")
    return "".join(cleaned)


def extract_agent_instance_template(config_text: str) -> str:
    lines = config_text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line == "  instance_template: |":
            start = index + 1
            break
    if start is None:
        raise ValueError("Could not find 'agent.instance_template: |' in base config")

    block: list[str] = []
    for line in lines[start:]:
        if line and not line.startswith("    "):
            break
        block.append(line[4:] if line.startswith("    ") else "")
    return "\n".join(block).rstrip()


def default_programbench_config() -> Path:
    try:
        from minisweagent.config import builtin_config_dir
    except ImportError as exc:
        raise RuntimeError(
            "Provide --base-config, or run this script in an environment with mini-swe-agent installed."
        ) from exc
    return builtin_config_dir / "benchmarks" / "programbench.yaml"


def yaml_block(value: str, indent: int = 2) -> str:
    prefix = " " * indent
    return "\n".join(prefix + line if line else prefix for line in value.splitlines())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--base-config", type=Path)
    parser.add_argument("--mined-spec", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    base_config = args.base_config or default_programbench_config()
    base_template = extract_agent_instance_template(base_config.read_text(encoding="utf-8"))
    spec = clean_text(args.mined_spec.read_text(encoding="utf-8"))
    instance_template = f"""{base_template}

## Black-Box Mined Specification Scaffold

The following behavioral notes were mined automatically from the reference
executable using black-box probes only. They do not use official tests or source
code. Treat them as implementation guidance and verify behavior yourself when
possible.

{spec}

Before coding, make a compact implementation plan that prioritizes the mined
requirements. Preserve process-level behavior: CLI arguments, stdin/file input,
stdout/stderr placement, exit codes, and formatting. Keep the first version
compiling, then improve behavior iteratively.
"""

    content = "agent:\n  instance_template: |-\n" + yaml_block(instance_template, 4) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(content, encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
