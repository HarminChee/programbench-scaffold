#!/usr/bin/env python3
"""Build evidence-backed, high-volume V3 agent batches."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


PERSPECTIVES = {
    "reachability_and_anchor_repair": "Diagnose prior shallow failures and first establish successful substantive execution anchors before expanding them.",
    "source_and_entrypoints": "Trace executable entrypoints, branches, helpers, formats, and error sites exposed by the source.",
    "docs_and_native_behaviors": "Harvest and systematically vary documented examples and native-test behavior without copying target PB oracles.",
    "source_corpus_harvest": "Materialize representative source/testdata/example corpora into runtime fixtures, then vary extensions, content shapes, sizes, encodings, and combinations.",
    "protocol_and_fixture_matrix": "Use deterministic loopback services, files, stdin, environment, and protocol states; never substitute public network services for local fixtures. For stdin/stdout RPC or server protocols exposed by the source, construct complete bounded request streams (initialization when required, multiple methods, ids, params, malformed messages, and clean EOF) rather than merely launching a blocking server with empty stdin.",
    "terminal_and_stateful_interaction": "Exercise TTY, terminal-size, capability negotiation, graphics backends, signals, multi-step state, and filesystem side effects with explicit deterministic fixtures. When source or docs expose multiple terminal protocols, establish a successful anchor for each reachable protocol instead of mapping every case to one terminal kind.",
    "blackbox_and_boundaries": "Design reference-binary probes for aliases, ordering, malformed values, sizes, state transitions, and ambiguous behavior.",
    "cartesian_expansion": "Enumerate broad input and state combinations. Repeated invocations, templates, and outcomes are explicitly allowed.",
}

FOCUS_CONSTRAINTS = {
    "reachability_and_anchor_repair": (
        "Generate repaired reachability cases only. For each dominant failed "
        "behavior cluster, identify the missing success-critical argument, "
        "fixture, environment, or interaction from a nearby successful anchor. "
        "Preserve the original behavioral intent, add the missing prerequisite, "
        "and vary real inputs across uncovered entrypoint branches. At least "
        "80% of cases must be expected to pass the shallow gate; keep at most "
        "two representatives of any one known failure signature."
    ),
    "protocol_and_fixture_matrix": (
        "Generate protocol cases only. Except for at most two liveness/error "
        "anchors, every case must send a complete bounded request stream, "
        "exercise a protocol method/state/parameter variation, and close "
        "cleanly. Do not spend this batch re-enumerating ordinary CLI flags."
    ),
    "source_corpus_harvest": (
        "Generate corpus-backed cases only. Every normal case must materialize "
        "a real file/tree/input corpus and reach substantive processing; do "
        "not spend this batch on help/version or missing-path parser errors."
    ),
    "terminal_and_stateful_interaction": (
        "Generate terminal or stateful cases only. Every normal case must "
        "declare a terminal/state fixture and exercise a real interaction or "
        "state transition."
    ),
}


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--rows-per-batch", type=int, default=8)
    ap.add_argument("--minimum-cases", type=int, default=55)
    ap.add_argument(
        "--perspectives",
        default="",
        help=(
            "Optional comma-separated subset for PB-free refinement runs. "
            "The default uses every generic perspective."
        ),
    )
    args = ap.parse_args()
    plan = args.plan_dir.resolve()
    out = args.output_dir.resolve()
    spec = json.loads((plan / "instance_spec.json").read_text())
    rows = json.loads((plan / "scenario_matrix.json").read_text())["rows"]
    inventory = json.loads((plan / "repository_inventory.json").read_text())
    requested = [item.strip() for item in args.perspectives.split(",") if item.strip()]
    unknown = sorted(set(requested) - set(PERSPECTIVES))
    if unknown:
        ap.error(f"unknown perspectives: {', '.join(unknown)}")
    perspectives = (
        [(name, PERSPECTIVES[name]) for name in requested]
        if requested
        else list(PERSPECTIVES.items())
    )
    batches = []
    for start in range(0, len(rows), args.rows_per_batch):
        selected = rows[start : start + args.rows_per_batch]
        for perspective, instruction in perspectives:
            idx = len(batches) + 1
            batch_id = f"batch_{idx:02d}_{perspective}"
            directory = out / batch_id
            prompt = f"""# ProgramBench Oracle Gym V3 candidate generation

Instance: {spec['instance_id']}
Language: {spec['language']}
Perspective: {perspective}

{instruction}
{FOCUS_CONSTRAINTS.get(perspective, "")}

Use only the pinned target source, documentation, native tests, capability
graph, matrix rows, and deterministic reference-binary observations supplied
by the controller. Target ProgramBench official oracle tests are forbidden.

Generate at least {args.minimum_cases} concrete behavioral cases. Every case
must contain a real stimulus (arguments, stdin, files, environment, state, or
interaction) and an intended observable behavior. Do not emit empty/no-op
cases, placeholder cases, or two cases with exactly the same complete
invocation. Repetition is allowed only when input, fixture, state, or operation
meaningfully differs; similar templates are welcome when they enumerate real
values or boundaries. Do not invent return code/stdout/stderr; those are
captured from the reference binary.

This is a focused `{perspective}` batch, not another general CLI sweep. At
least 70% of the cases must directly implement this perspective using the
corresponding source, documentation, native-test, or prior-coverage evidence.
If that evidence exposes a protocol, corpus, terminal backend, or stateful
feature, enumerate its successful, boundary, malformed, and state-transition
variants before spending cases on unrelated flags. For a protocol batch,
empty stdin plus a timeout is only one liveness boundary case; the rest must
send complete, bounded requests and cleanly reach EOF.

Prefer semantic breadth over name-only breadth. Include normal, boundary, and
error behavior, but avoid generating dozens of inputs that all intentionally
terminate at the same shallow parser error. The rationale must identify the
source/doc/native-test evidence and what differs from nearby cases.

Before expanding any family, establish a viable anchor that reaches the
intended substantive behavior. Use the prior-run feedback to repair dominant
shallow failures. If a default value or default environment failed previously,
explicitly select a valid documented/listed alternative rather than repeating
the broken default. Preserve a limited number of the failure cases as boundary
tests, but spend most cases beyond that gate.

The runtime working directory is empty: reading source code in this prompt does
not make repository paths available to the executable. Every path that is
expected to exist must be materialized in `files`, `binary_files`,
`executable_files`, or `repeat_files`. A missing path is allowed only for an
intentional missing-path error test whose rationale says so.

Prefer fixture-backed real executions. The fixture DSL is:
`args`, `argv0`, `stdin`, `env`, `files`, `binary_files`, `executable_files`,
`file_modes`, `git`, `http`, `terminal`, `repeat_files`, `observe_files`,
`isolate_home_tmp`, `stdin_regular_file`, `timeout_seconds`,
`normalize_benchmark_output`, and `normalize_epoch_numbers`.

Use these exact portable patterns:
```json
{{
  "argv0": "/workspace/tool-alias",
  "args": ["--help"]
}}
{{
  "files": {{"corpus/main.go": "package main\\nfunc main() {{}}\\n"}},
  "args": ["corpus/main.go"]
}}
{{
  "http": {{
    "path": "/api/items",
    "status": 200,
    "headers": {{"Content-Type": "application/json"}},
    "body": "{{\\"ok\\":true}}"
  }},
  "args": ["{{http_url}}"]
}}
{{
  "terminal": {{
    "kind": "iterm2",
    "rows": 24,
    "cols": 80,
    "cell_width": 10,
    "cell_height": 20,
    "stdin_mode": "pipe",
    "send_eof": true,
    "output_mode": "control_tail_trim"
  }},
  "stdin": "one complete input followed by EOF\\n"
}}
{{
  "repeat_files": {{
    "corpus/large.txt": {{"prefix": "", "row": "value\\n", "count": 1000, "suffix": ""}}
  }},
  "args": ["corpus/large.txt"]
}}
```
Use terminal `stdin_mode: "pipe"` when the program consumes piped data but
requires stdout/stderr to be a TTY. Keep the default PTY stdin for interactive
keyboard/TUI cases, expressed with `input_events`.
For terminal-aware programs, inspect source and documentation for capability
queries, backend selection, and environment-controlled negotiation. Enumerate
the supported `terminal.kind` values (`generic`, `iterm2`, `kitty`, `sixel`)
only when evidence shows that backend is reachable. Establish at least one
substantive successful case per reachable backend before expanding sizes,
flags, or malformed protocol replies. Do not label a case as one backend while
using another backend's terminal kind.
Use `{{http_url}}` only when an `http` fixture exists. Do not use example.com,
httpbin.org, GitHub, or any public service for successful network behavior.
For corpus-driven tools, enumerate multiple meaningful file contents and
extensions rather than referring to source-tree paths that will not exist.
When a documented benchmark or progress mode emits scheduler-dependent
durations, rates, or latency histograms, opt in to
`normalize_benchmark_output: true`. It masks only those volatile metric values
while retaining labels, table structure, response content, exit status, and
all other output. Do not use it for ordinary deterministic commands or to hide
a semantically different result.

Detected flags are hints, not a closed allow-list:
{json.dumps(inventory.get('detected_flags', [])[:120], indent=2)}

Return exactly one JSON object:
```json
{{"cases": [{{
  "name": "unique_name",
  "area": "matrix_row_id_or_exploratory",
  "args": [],
  "stdin": "",
  "rationale": "source evidence, fixture/state, and intended behavior"
}}]}}
```

The special area `exploratory` is allowed for justified probes beyond the
detected matrix. Matrix rows:
```json
{json.dumps(selected, indent=2)}
```
"""
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "agent_prompt.md").write_text(prompt, encoding="utf-8")
            dump(directory / "matrix_rows.json", {"rows": selected, "allow_exploratory": True})
            batches.append({"batch_id": batch_id, "perspective": perspective, "path": str(directory)})
    dump(out / "batch_manifest.json", {
        "schema": "programbench_oracle_gym_v3_batches",
        "instance_id": spec["instance_id"],
        "batches": batches,
    })
    print(json.dumps({"instance_id": spec["instance_id"], "batches": len(batches), "output": str(out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
