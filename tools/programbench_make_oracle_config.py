#!/usr/bin/env python3
"""Build a mini-swe-agent config override that injects an oracle spec summary."""

from __future__ import annotations

import argparse
from pathlib import Path


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
        if line.startswith("    "):
            block.append(line[4:])
        else:
            block.append("")
    return "\n".join(block).rstrip()


def indent_block(text: str, spaces: int = 4) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else prefix for line in text.splitlines())


def default_programbench_config() -> Path:
    try:
        from minisweagent.config import builtin_config_dir
    except ImportError as exc:
        raise RuntimeError(
            "Provide --base-config, or run this script in an environment with mini-swe-agent installed."
        ) from exc
    return builtin_config_dir / "benchmarks" / "programbench.yaml"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--base-config", type=Path)
    parser.add_argument("--oracle-spec", type=Path, required=True)
    parser.add_argument(
        "--architecture-plan",
        type=Path,
        help="Optional scaffold-generated implementation plan to inject before the oracle spec.",
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    base_config = args.base_config or default_programbench_config()
    base_template = extract_agent_instance_template(base_config.read_text(encoding="utf-8"))
    oracle_text = args.oracle_spec.read_text(encoding="utf-8").strip()
    sections = [
        base_template,
        "## Oracle-Test Specification Scaffold",
        "The following section is an upper-bound scaffold generated from the official ProgramBench oracle-test names. Treat it as behavioral requirements to satisfy. It does not provide implementation source code, but it tells you what the hidden evaluator is likely checking.",
        "Important implementation constraint: the environment has no internet access during the task or evaluation. Do not introduce new external package dependencies unless they are already vendored in the repository. Prefer a small self-contained implementation that compiles offline, even if it covers fewer behaviors.",
        "Use the oracle-derived requirements to prioritize behavior, but keep a compiling submission as the top priority. A non-compiling solution scores zero.",
    ]
    if args.architecture_plan:
        sections.extend(
            [
                "## Scaffold-Generated Implementation Plan",
                args.architecture_plan.read_text(encoding="utf-8").strip(),
            ]
        )
    sections.append(oracle_text)
    augmented = "\n\n".join(sections)

    out = args.out or args.oracle_spec.with_suffix(".mini_swe_oracle_config.yaml")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("agent:\n  instance_template: |\n" + indent_block(augmented) + "\n", encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
