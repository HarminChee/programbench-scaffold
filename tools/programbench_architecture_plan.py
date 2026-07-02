#!/usr/bin/env python3
"""Generate a compact implementation plan from a ProgramBench oracle-spec JSON."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


FORMAT_KEYWORDS = {
    "json": "JSON",
    "yaml": "YAML",
    "toml": "TOML",
    "hcl": "HCL",
    "csv": "CSV",
    "html": "HTML",
    "xml": "XML",
    "sql": "SQL",
}


def top_keywords(active_tests: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for test in active_tests:
        text = " ".join(
            [
                str(test.get("group", "")),
                str(test.get("case", "")),
                str(test.get("intent", "")),
                str(test.get("name", "")),
            ]
        ).lower()
        for key, label in FORMAT_KEYWORDS.items():
            if key in text:
                counts[label] += 1
        for key in ("flag", "error", "order", "unicode", "roundtrip", "stdin", "empty", "nested"):
            if key in text:
                counts[key] += 1
    return counts


def yj_plan(payload: dict[str, Any]) -> list[str]:
    return [
        "## Task-Specific Architecture Plan",
        "",
        "This task is `yj`, a CLI format converter. Optimize for a compiling, self-contained Go implementation first, then maximize common conversion behavior.",
        "",
        "### Implementation Priorities",
        "",
        "1. Use only the Go standard library unless an existing vendored dependency is already present in the workspace. Do not add a `go.mod` dependency that needs network access.",
        "2. Implement robust CLI flag parsing before parser details. Support conversion selectors for YAML/TOML/JSON/HCL combinations and modifier flags such as indentation, HTML escaping, non-finite float handling, and key parsing where feasible.",
        "3. Use `encoding/json` for JSON input/output. Detect JSON input that starts with `{` or `[` even when the default input mode is YAML; this avoids mangling JSON as YAML.",
        "4. Implement a small internal data model using ordered key-value pairs so key-order tests have a chance to pass. Fall back to maps only when order is not important.",
        "5. Implement a minimal YAML reader/writer for common benchmark cases: top-level maps, nested maps by indentation, arrays with `-`, quoted/unquoted scalars, booleans, null, integers, floats, and empty input.",
        "6. Implement a minimal TOML reader/writer for common cases: key-value pairs, sections, dotted keys, arrays, booleans, numbers, strings, and comments. It does not need to be perfect, but it must not crash.",
        "7. Implement a minimal HCL reader/writer for common cases: `key = value`, comments, simple blocks, nested attributes, and empty input. If HCL is partial, return valid output for simple attribute cases instead of a blanket 'not implemented' error.",
        "8. Preserve exact stdout/stderr/exit-code behavior for help, version, invalid flags, unsupported combinations, and empty stdin. These are high-yield tests.",
        "",
        "### Exploration Budget",
        "",
        "Spend at most the first 10-12 tool calls probing the reference executable. Probe only representative cases: help/version, invalid flag, empty stdin, JSON->YAML, YAML->JSON, TOML->JSON, HCL->JSON, HTML escaping, indentation, and one nested structure. Then implement.",
        "",
        "### Submission Checklist",
        "",
        "- `./compile.sh` exists, is executable, and builds `./executable` without network access.",
        "- Run `./compile.sh` before submitting.",
        "- Run at least these smoke tests before submitting: `./executable -h`, `./executable -v`, empty stdin, invalid flag, JSON input, YAML input, TOML input, HCL input.",
        "- Keep generated binaries and build artifacts out of git.",
    ]


def dsq_plan(payload: dict[str, Any]) -> list[str]:
    return [
        "## Task-Specific Architecture Plan",
        "",
        "This task is `dsq`, a CLI that loads structured data files and runs SQL-like queries over them. Do not implement a SQL engine from scratch. Use offline standard-library components to maximize behavior.",
        "",
        "### Implementation Priorities",
        "",
        "1. A Python executable is acceptable if `compile.sh` creates an executable `./executable` script. Prefer Python standard library over Go here because `sqlite3`, `csv`, and `json` are available without network downloads.",
        "2. Use an in-memory SQLite database (`sqlite3`) as the query engine. Load each input file into a table, then execute the user query directly against SQLite.",
        "3. Support the high-yield formats first: CSV, JSON array of objects, JSON object, JSONL/NDJSON, and stdin. Add simple TOML via `tomllib` if Python provides it. For YAML/logfmt, implement minimal parsers only after core CSV/JSON works.",
        "4. If no query is provided, default to dumping all rows from the loaded table. If a query is provided as the last argument and contains spaces or starts with `select`, execute it.",
        "5. Implement output modes before rare input formats: default JSON rows, pretty/table output if requested, schema output, count/aggregation results, column ordering, empty results, and error messages.",
        "6. Handle multiple files by loading each file as a table named from its basename, plus a reasonable default table name for single-file queries.",
        "7. Implement CLI flags conservatively: help/version, schema flags (`--schema`, `-c`), query-file flags, stdin mimetype flags, output format flags, and invalid-argument errors.",
        "8. Do not chase Avro/Excel/ODS/ORC/Parquet first. They are lower-yield and likely need non-stdlib dependencies. Return clear errors for unsupported formats after core formats compile and pass smoke tests.",
        "",
        "### Exploration Budget",
        "",
        "Spend at most 10-12 tool calls probing the reference executable: help/version, no args, one CSV query, one JSON query, no-query dump, schema flag, stdin CSV, query file, invalid SQL, and one join. Then implement.",
        "",
        "### Submission Checklist",
        "",
        "- `./compile.sh` creates or validates an executable `./executable` without network access.",
        "- Run smoke tests for CSV, JSON, JSONL, stdin, schema flag, invalid SQL, no args, and no-query dump.",
        "- Ensure stdout/stderr/exit-code behavior is deliberate.",
        "- Keep generated caches, databases, and executable artifacts out of git.",
    ]


def jplot_plan(payload: dict[str, Any]) -> list[str]:
    return [
        "## Task-Specific Architecture Plan",
        "",
        "This task is `jplot`, a CLI that reads JSON values from stdin/files/HTTP sources and renders terminal-oriented plots or dashboards for selected fields. Optimize for deterministic CLI, parsing, and non-interactive behavior first.",
        "",
        "### Implementation Priorities",
        "",
        "1. A Python executable is acceptable if `compile.sh` creates an executable `./executable` script. Prefer Python standard library (`json`, `argparse`, `urllib.request`, `datetime`) over Go dependencies.",
        "2. Implement robust argument parsing first. The oracle focuses heavily on CLI flags, field-spec parsing, invalid typed values, missing values, `--` behavior, interval/rows/steps parsing, and exact error paths.",
        "3. Implement input sources in this order: stdin JSON lines, stdin JSON array/object, file path, then HTTP URL using `urllib.request`. For HTTP errors, return clear nonzero errors.",
        "4. Implement JSON field extraction for dot paths, nested objects, hyphen/underscore/numeric field names, duplicate keys last-wins behavior, and wrong-type errors.",
        "5. Implement field specs before plot rendering: support single fields, `+` combined fields, comma-separated options, backward-compatible colon options, `counter`, `marker`, and multiple graph specs.",
        "6. Terminal graphics are lower priority than parse/output correctness. If real plotting is too costly, produce stable text output that preserves requested field names, numeric values, counters, markers, and rows/steps limits.",
        "7. Handle non-TTY / no-graphics behavior explicitly. Many tests check terminal environment handling, invalid flags, and no-tty errors rather than visual fidelity.",
        "8. Never implement an unbounded URL/interval loop for benchmark mode. Cap URL polling to a small fixed number of iterations when not attached to an interactive TTY, and make malformed/unreachable HTTP sources fail fast.",
        "9. Do not spend budget on complex dashboards until stdin parsing, field specs, counter/marker, invalid args, and JSON processing compile and smoke-test correctly.",
        "",
        "### Exploration Budget",
        "",
        "Spend at most 10-12 tool calls probing the reference executable: help/version, invalid flag, empty stdin, one JSONL stdin field, nested field path, field not found, counter option, marker option, rows/steps/interval flags, no-TTY/no-graphics behavior, and one URL error case. Then implement.",
        "",
        "### Submission Checklist",
        "",
        "- `./compile.sh` creates an executable `./executable` without network access.",
        "- Run smoke tests for help, invalid flag, empty stdin, JSONL stdin, nested field path, counter, marker, rows, steps, and malformed JSON.",
        "- Ensure stderr and exit codes are deliberate for invalid input and invalid CLI flags.",
        "- Keep generated binaries, logs, and temporary fixtures out of git.",
    ]


def figlet_plan(payload: dict[str, Any]) -> list[str]:
    return [
        "## Task-Specific Architecture Plan",
        "",
        "This task is `figlet`, a CLI that renders text using FIGlet fonts. Optimize for deterministic CLI/font/layout behavior first; exact full-font fidelity can be partial as long as the implementation compiles and covers common fonts/options.",
        "",
        "### Implementation Priorities",
        "",
        "1. A C implementation is natural, but a Python or C executable is acceptable if `compile.sh` creates `./executable` without network access. Prefer a self-contained implementation over depending on system figlet.",
        "2. Implement CLI parsing first: help/version, width (`-w`), font selection (`-f`), font directory (`-d`), justification (`-l/-c/-r`), layout/kerning/smushing flags, paragraph mode, German/ISO charset options, and invalid option behavior.",
        "3. Support input from argv and stdin. Preserve behavior for empty input, whitespace, multiple words, newlines, long lines, binary/high-ASCII-ish input, and trailing newlines.",
        "4. Implement FIGlet `.flf` font loading enough for bundled fonts: parse header, hardblank, height, comment lines, character glyphs, and missing/corrupt font errors. Add compressed-font support only after plain fonts and CLI behavior work.",
        "5. Implement a fallback built-in font for smoke tests, but prioritize loading external `.flf` files because many oracle tests check font loading and directory behavior.",
        "6. Implement layout in layers: left/right/center justification, width wrapping, then kerning/smushing/overlap. Exact rare smushing rules are lower priority than stable output and correct error handling.",
        "7. Preserve stdout/stderr/exit codes for font-not-found, invalid control files, unsupported compressed fonts, illegal option combinations, help, and version.",
        "",
        "### Exploration Budget",
        "",
        "Spend at most 10-12 tool calls probing the reference executable: help/version, no args/stdin, one simple word, width wrapping, center/right justify, font-not-found, `-f` with bundled font, invalid flag, German charset sample, and long text. Then implement.",
        "",
        "### Submission Checklist",
        "",
        "- `./compile.sh` builds or installs a self-contained `./executable` without network access.",
        "- Run smoke tests for help, version, stdin, argv text, width, center/right/left, font loading, missing font, invalid flag, and a punctuation-heavy string.",
        "- Keep generated binaries and temporary font fixtures out of git.",
    ]


def htmlq_plan(payload: dict[str, Any]) -> list[str]:
    return [
        "## Task-Specific Architecture Plan",
        "",
        "This task is `htmlq`, a CLI HTML selector/query tool. The oracle is large, so prioritize a small selector engine plus exact CLI/output behavior over a broad but fragile parser.",
        "",
        "### Implementation Priorities",
        "",
        "1. A Python executable is acceptable if `compile.sh` creates `./executable`; Python standard library `html.parser`, `re`, and `urllib.parse` are enough for a useful subset. Avoid Rust crates or network downloads.",
        "2. Implement CLI behavior first: help/version, selector arguments, `-a/--attribute`, `-t/--text`, `-r/--remove-nodes`, `-b/--base`, `--detect-base`, file input, stdin, invalid selectors, and mutually exclusive/invalid flag errors.",
        "3. Build a simple DOM tree with tag name, attributes, text, children, and original-ish serialization. Preserve element order and duplicate matches.",
        "4. Selector support should cover the high-yield subset: tag, `.class`, `#id`, `[attr]`, `[attr=value]`, descendant, child `>`, comma-separated selectors, simple chains such as `div.item a`, and common pseudo/no-op cases if easy.",
        "5. Output modes matter: full matched HTML, text-only mode, attribute-only mode one value per line, whitespace handling, empty stdin, binary-ish stdin, and no-match output.",
        "6. Implement URL rewriting for `--base`/`--detect-base` on tags like `a`, `area`, `link`, `img`, and preserve special cases such as absolute URLs and protocol-relative URLs.",
        "7. Defer full CSS selector compliance. When selector parsing fails, mimic panic/error behavior and exit code rather than silently returning no results.",
        "",
        "### Exploration Budget",
        "",
        "Spend at most 10-12 tool calls probing help/version, empty stdin, tag/class/id selector, descendant/child selector, attribute output, text output, base URL rewrite, remove-nodes, file input, invalid selector, and no-match behavior. Then implement.",
        "",
        "### Submission Checklist",
        "",
        "- `./compile.sh` creates `./executable` without downloading crates/packages.",
        "- Run smoke tests for tag/class/id/attribute selectors, text mode, attribute mode, base URL rewrite, file input, empty stdin, invalid selector, and no match.",
        "- Keep parser implementation small and deterministic.",
    ]


def ripsecrets_plan(payload: dict[str, Any]) -> list[str]:
    return [
        "## Task-Specific Architecture Plan",
        "",
        "This task is `ripsecrets`, a CLI scanner for secrets in files/directories. Optimize for pattern coverage, ignore semantics, output format, and exit codes.",
        "",
        "### Implementation Priorities",
        "",
        "1. A Python executable is acceptable if `compile.sh` creates `./executable`; prefer Python standard library `re`, `argparse`, `pathlib`, and `os.walk` over Rust dependencies.",
        "2. Implement CLI parsing first: help/version, file/directory arguments, no-argument current-directory scan, `--only-matching`, `--additional-pattern`, ignore/strict-ignore flags, install-pre-commit, and invalid option behavior.",
        "3. Implement recursive scanning with deterministic file order. Handle multiple paths, missing files, empty files, binary/NUL bytes, long lines, no trailing newline, unicode text, and permission-ish errors gracefully.",
        "4. Implement built-in secret detectors for high-yield patterns: AWS access keys, generic API/token/password assignments, private key markers, JWT-looking tokens, Slack/GitHub-like tokens, hex/base64 random-looking strings when tied to secret names.",
        "5. Implement additional regex patterns exactly enough for oracle behavior: invalid regex errors, capturing-group filtering when expected, case-sensitive/case-insensitive behavior, multiple patterns, and `--only-matching` output.",
        "6. Implement ignore semantics: `.secretsignore`, allowlist comments, strict ignore for explicit paths, duplicate suppression vs duplicate reporting where indicated, and line-number/file-path formatting.",
        "7. Preserve stdout/stderr/exit code behavior: no findings exit 0 and usually no output; findings exit nonzero; invalid regex/missing path/pre-commit errors should go to stderr.",
        "8. Implement `install-pre-commit` as a filesystem operation that writes a hook when inside a git repo and errors clearly otherwise; do not rely on git network access.",
        "",
        "### Exploration Budget",
        "",
        "Spend at most 10-12 tool calls probing help/version, clean file, AWS key file, generic password line, only-matching, additional pattern, invalid regex, missing file, directory recursion, `.secretsignore`, pre-commit outside git, and binary file. Then implement.",
        "",
        "### Submission Checklist",
        "",
        "- `./compile.sh` creates `./executable` without cargo downloads.",
        "- Run smoke tests for clean/no-secret, one secret, directory recursion, missing file, only-matching, additional pattern, invalid regex, ignore file, help/version, and pre-commit error.",
        "- Keep generated fixtures and hook test directories out of git.",
    ]


def generic_plan(payload: dict[str, Any]) -> list[str]:
    active_tests = payload.get("active_tests") or []
    group_counts = payload.get("group_counts") or []
    keyword_counts = top_keywords(active_tests)
    lines = [
        "## Spec-to-Architecture Plan",
        "",
        "Use this plan to convert the oracle-derived requirements into an implementation strategy. The goal is not to satisfy every listed behavior immediately; the goal is to choose a structure that can cover many behaviors while still compiling within budget.",
        "",
        "### Global Constraints",
        "",
        "- The environment is offline. Do not depend on downloads during implementation or evaluation.",
        "- A non-compiling solution scores zero. Prefer a smaller compiling implementation over a broader but fragile one.",
        "- Treat CLI parsing, stdin/stdout/stderr, exit codes, and error messages as first-class behavior.",
        "",
        "### High-Yield Behavior Areas",
        "",
    ]
    for group, count in group_counts[:8]:
        lines.append(f"- `{group}`: {count} oracle tests")
    if keyword_counts:
        lines.extend(["", "### Cross-Cutting Keywords", ""])
        for key, count in keyword_counts.most_common(10):
            lines.append(f"- {key}: {count} mentions")
    lines.extend(
        [
            "",
            "### Recommended Work Order",
            "",
            "1. Probe the reference executable only for representative cases from the largest behavior areas.",
            "2. Implement CLI parsing and a minimal internal data model.",
            "3. Implement the most frequent input/output paths first.",
            "4. Add exact handling for invalid flags, empty input, stdout/stderr, and exit codes.",
            "5. Compile and run smoke tests before submitting.",
        ]
    )
    return lines


def architecture_plan(payload: dict[str, Any]) -> str:
    repo = str(payload.get("repository", "")).lower()
    lines = generic_plan(payload)
    if repo == "sclevine/yj":
        lines.extend(["", *yj_plan(payload)])
    elif repo == "multiprocessio/dsq":
        lines.extend(["", *dsq_plan(payload)])
    elif repo == "rs/jplot":
        lines.extend(["", *jplot_plan(payload)])
    elif repo == "cmatsuoka/figlet":
        lines.extend(["", *figlet_plan(payload)])
    elif repo == "mgdm/htmlq":
        lines.extend(["", *htmlq_plan(payload)])
    elif repo == "sirwart/ripsecrets":
        lines.extend(["", *ripsecrets_plan(payload)])
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("oracle_spec_json", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.oracle_spec_json.read_text(encoding="utf-8"))
    text = architecture_plan(payload)
    out = args.out or args.oracle_spec_json.with_suffix(".architecture_plan.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
