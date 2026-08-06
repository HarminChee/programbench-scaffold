#!/usr/bin/env python3
"""Turn a validated V2 scenario matrix into bounded, independent agent batches."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-rows-per-batch", type=int, default=6)
    parser.add_argument("--grouped", action="store_true", help="combine related families into three cost-bounded agent topics")
    parser.add_argument("--expansive", action="store_true", help="create multiple high-volume perspectives and retain redundant cases")
    parser.add_argument("--minimum-cases", type=int, default=40)
    args = parser.parse_args()
    plan_dir = args.plan_dir.expanduser().resolve()
    behavior = json.loads((plan_dir / "behavior_map.json").read_text(encoding="utf-8"))
    matrix = json.loads((plan_dir / "scenario_matrix.json").read_text(encoding="utf-8"))["rows"]
    grouped: dict[str, list[dict]] = defaultdict(list)
    group_names = {
        "cli_and_argument_parsing": "interface_and_errors",
        "structured_input_and_conversion": "interface_and_errors",
        "query_and_data_processing": "semantic_core",
        "source_analysis_and_language_detection": "semantic_core",
        "output_formatting_and_rendering": "semantic_core",
        "filesystem_and_directory_state": "state_and_fixtures",
        "configuration_and_environment": "state_and_fixtures",
        "module_or_dependency_state": "state_and_fixtures",
        "git_and_repository_state": "state_and_fixtures",
        "network_and_protocol": "state_and_fixtures",
        "terminal_and_interactive": "state_and_fixtures",
    }
    for row in matrix:
        key = group_names.get(row["family"], row["family"]) if args.grouped else row["family"]
        grouped[key].append(row)
    output = args.output_dir.expanduser().resolve()
    batches: list[dict] = []
    perspectives = [
        (
            "source_paths",
            "Focus on source-code branches, error sites, format implementations, "
            "flag interactions, and internal paths that can be reached through the CLI.",
        ),
        (
            "docs_native_harvest",
            "Focus on documented examples, README promises, native-test behaviors, "
            "upstream fixtures, regressions, and systematic variations of those examples.",
        ),
        (
            "binary_probe_design",
            "Focus on black-box probe design for the reference binary: boundary inputs, "
            "malformed inputs, ambiguous inputs, aliases, ordering, special values, and "
            "cases that may distinguish subtly different implementations. Gold output is "
            "captured later, so do not invent expected output.",
        ),
        (
            "combinatorial_expansion",
            "Focus on broad Cartesian expansion across formats, flags, input shapes, "
            "normal/boundary/error classes, nesting, scalar types, sizes, and ordering. "
            "Enumerate many concrete cases even when they reuse the same test template.",
        ),
    ] if args.expansive else [("balanced", "Use balanced source, documentation, native-test, and fixture evidence.")]
    for family, rows in sorted(grouped.items()):
        for start in range(0, len(rows), args.max_rows_per_batch):
            selected = rows[start : start + args.max_rows_per_batch]
            for perspective_id, perspective_instruction in perspectives:
                index = len(batches) + 1
                batch_id = f"batch_{index:02d}_{family}_{perspective_id}"
                volume_instruction = (
                    f"Generate {args.minimum_cases} to {args.minimum_cases + 5} concrete cases. Across the many "
                    "independent prompts this produces a large suite without truncating one JSON response. "
                    "Repetition is allowed and must not be removed: it is acceptable "
                    "to repeat an invocation, template, or behavior when it represents another matrix cell, "
                    "input variation, evidence source, or robustness check. Favor breadth and enumeration."
                    if args.expansive else
                    "Generate a bounded set of strong, nontrivial cases."
                )
                prompt = f"""# V2 topic batch: {batch_id}

You are generating behavioral oracle cases for one bounded topic batch.
The target ProgramBench official oracle tests are forbidden input. Use only the
target source, docs, native tests, this behavior plan, deterministic local
fixtures, and observations from the reference binary.

Target instance: {behavior['instance_id']}
Family: {family}
Perspective: {perspective_id}

{perspective_instruction}

{volume_instruction}

Generate cases only for the matrix rows below. For each case, set `area` to the
exact matrix row ID. The rationale must state the source evidence, fixture IDs,
and behavior distinguished from other cases. Use normal, boundary, and error
scenarios. Do not make external-network calls. Do not use host-dependent values.
Do not add flag-only cases when a real fixture-backed semantic behavior exists.

Return exactly this flat fixture DSL shape, with no `cli`, `command`, `assert`,
or expected-output wrapper:
```json
{{"cases": [
  {{
    "name": "unique_case_name",
    "area": "exact_matrix_row_id",
    "args": ["-example"],
    "stdin": "literal input text",
    "rationale": "evidence and distinguished behavior"
  }}
]}}
```
`args` and `stdin` are mandatory on every case. `args` contains only arguments
after the executable name. Expected return code/stdout/stderr must not be
invented; the reference-capture stage records them. Optional fixture DSL fields
such as `env`, `files`, `http`, and `terminal` must remain top-level case fields.

Matrix rows:
```json
{json.dumps(selected, indent=2)}
```

Return a JSON object with a `cases` array. Cases must be directly executable by
the V2 CLI fixture DSL and must contain strong observable assertions after
reference capture.
"""
                directory = output / batch_id
                directory.mkdir(parents=True, exist_ok=True)
                write_json(directory / "matrix_rows.json", {
                    "batch_id": batch_id,
                    "perspective": perspective_id,
                    "rows": selected,
                })
                (directory / "agent_prompt.md").write_text(prompt, encoding="utf-8")
                batches.append({
                    "batch_id": batch_id,
                    "family": family,
                    "perspective": perspective_id,
                    "matrix_rows": [row["id"] for row in selected],
                    "path": str(directory),
                })
    write_json(output / "batch_manifest.json", {"schema": "programbench_oracle_gym_v2_topic_batches", "instance_id": behavior["instance_id"], "batches": batches})
    print(json.dumps({"instance_id": behavior["instance_id"], "batches": len(batches), "output_dir": str(output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
