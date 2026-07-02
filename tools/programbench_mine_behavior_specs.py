#!/usr/bin/env python3
"""Mine fair black-box behavior specs from a ProgramBench reference executable.

This tool intentionally does not read official tests or source code. It starts
the task cleanroom image, runs generic CLI probes against `/workspace/executable`,
and writes a compact JSON/Markdown behavior spec for a coding agent.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PROGRAMBENCH_SRC = REPO_ROOT / "external" / "ProgramBench" / "src"
if PROGRAMBENCH_SRC.exists() and str(PROGRAMBENCH_SRC) not in sys.path:
    sys.path.insert(0, str(PROGRAMBENCH_SRC))


STDIN_SEEDS = {
    "empty": "",
    "plain_text": "hello\nworld\n",
    "csv": "a,b,c\n1,2,3\n4,5,6\n",
    "json": '{"name":"programbench","value":7}\n',
    "yamlish": "name: programbench\nvalue: 7\n",
}

FILE_SEEDS = {
    "input.txt": "hello\nworld\n",
    "table.csv": "a,b,c\n1,2,3\n4,5,6\n",
    "data.json": '{"name":"programbench","value":7}\n',
    "empty.txt": "",
}

DOC_EXTENSIONS = {".md", ".txt", ".rst", ".1", ".man"}
DOC_NAME_TOKENS = ("readme", "usage", "help", "doc", "manual", "example", "tutorial")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def clean_text(value: str) -> str:
    cleaned = []
    for char in value:
        if char in "\n\r\t" or char.isprintable():
            cleaned.append(char)
        else:
            cleaned.append(f"\\x{ord(char):02x}")
    return "".join(cleaned)


def b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def short(value: str, limit: int) -> str:
    value = clean_text(value.replace("\r\n", "\n"))
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[truncated {len(value) - limit} chars]"


def image_for_task(task_id: str) -> str:
    docker_org = os.environ.get("PROGRAMBENCH_DOCKER_ORG", "programbench")
    try:
        from programbench.constants import DOCKER_ORG, image_name_from_instance_id

        return f"{image_name_from_instance_id(task_id)}:task_cleanroom_v6"
    except Exception:
        return f"{docker_org}/{task_id.replace('__', '_1776_')}:task_cleanroom_v6"


def run_local(cmd: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def start_container(image: str, cpus: int, memory: str) -> str:
    name = f"pb-mine-{uuid.uuid4().hex[:10]}"
    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "-w",
        "/workspace",
        "--rm",
        "--network",
        "none",
        "--cpus",
        str(cpus),
        "--memory",
        memory,
        "--memory-swap",
        memory,
        "--user",
        "agent",
        "--cap-drop",
        "SYS_PTRACE",
        image,
        "sleep",
        "2h",
    ]
    proc = run_local(cmd)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout.strip()


def stop_container(container_id: str) -> None:
    subprocess.run(["docker", "stop", container_id], capture_output=True, text=True, timeout=30)


def shell_write_files(files: dict[str, str]) -> str:
    lines = []
    for name, content in files.items():
        qname = shlex.quote(name)
        lines.append(f"mkdir -p $(dirname {qname})")
        lines.append(f"printf %s {shlex.quote(b64(content))} | base64 -d > {qname}")
    return "\n".join(lines)


def run_probe(container_id: str, case: dict[str, Any], timeout_s: int, excerpt_limit: int) -> dict[str, Any]:
    args = " ".join(shlex.quote(str(arg)) for arg in case.get("args", []))
    env = " ".join(
        f"{shlex.quote(str(key))}={shlex.quote(str(value))}"
        for key, value in sorted((case.get("env") or {}).items())
    )
    env_prefix = f"env {env} " if env else ""
    files = dict(case.get("files") or {})
    stdin = str(case.get("stdin", ""))
    files["_stdin"] = stdin
    setup = shell_write_files(files)
    command = f"""
tmp=$(mktemp -d)
cd "$tmp" || exit 97
{setup}
timeout {int(timeout_s)}s {env_prefix}/workspace/executable {args} < _stdin
rc=$?
exit $rc
"""
    proc = run_local(
        ["docker", "exec", "-w", "/workspace", container_id, "bash", "-lc", command],
        timeout=timeout_s + 10,
    )
    stdout = proc.stdout
    stderr = proc.stderr
    return {
        "name": case["name"],
        "area": case.get("area", "generic"),
        "args": case.get("args", []),
        "env": case.get("env", {}),
        "stdin_bytes": len(stdin.encode()),
        "files": sorted((case.get("files") or {}).keys()),
        "returncode": proc.returncode,
        "stdout_len": len(stdout.encode()),
        "stderr_len": len(stderr.encode()),
        "stdout_sha256": sha256_text(stdout),
        "stderr_sha256": sha256_text(stderr),
        "stdout_excerpt": short(stdout, excerpt_limit),
        "stderr_excerpt": short(stderr, excerpt_limit),
    }


def base_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = [
        {"name": "no_args", "area": "basic_invocation", "args": []},
        {"name": "help_long", "area": "help_usage", "args": ["--help"]},
        {"name": "help_short", "area": "help_usage", "args": ["-h"]},
        {"name": "version_long", "area": "help_usage", "args": ["--version"]},
        {"name": "version_short", "area": "help_usage", "args": ["-V"]},
        {"name": "invalid_long_flag", "area": "errors", "args": ["--programbench-invalid-flag"]},
        {"name": "invalid_short_flag", "area": "errors", "args": ["-Z"]},
        {"name": "missing_file", "area": "file_io", "args": ["does-not-exist.txt"]},
        {
            "name": "terminal_kitty_no_args",
            "area": "terminal",
            "args": [],
            "env": {"TERM": "xterm-kitty", "COLORTERM": "truecolor"},
        },
        {
            "name": "terminal_sixel_no_args",
            "area": "terminal",
            "args": [],
            "env": {"TERM": "xterm-256color", "COLORTERM": "truecolor"},
        },
    ]
    for name, seed in STDIN_SEEDS.items():
        cases.append({"name": f"stdin_{name}", "area": "stdin", "args": [], "stdin": seed})
        cases.append({"name": f"dash_stdin_{name}", "area": "stdin", "args": ["-"], "stdin": seed})
    for filename, content in FILE_SEEDS.items():
        cases.append(
            {
                "name": f"file_{filename.replace('.', '_')}",
                "area": "file_io",
                "args": [filename],
                "files": {filename: content},
            }
        )
    return cases


def extract_flags(text: str, limit: int) -> list[str]:
    flags: list[str] = []
    for match in re.finditer(r"(?<![A-Za-z0-9])(--[A-Za-z0-9][A-Za-z0-9_-]*|-[A-Za-z][A-Za-z0-9_-]*)", text):
        flag = match.group(1)
        if flag in {"--help", "-h", "--version", "-V"}:
            continue
        if flag not in flags:
            flags.append(flag)
        if len(flags) >= limit:
            break
    return flags


def flag_cases(help_text: str, limit: int) -> list[dict[str, Any]]:
    cases = []
    for flag in extract_flags(help_text, limit):
        cases.append({"name": f"flag_{flag.lstrip('-').replace('-', '_')}", "area": "flags", "args": [flag]})
    return cases


def looks_like_doc(path: str) -> bool:
    lowered = path.lower()
    name = Path(lowered).name
    suffix = Path(lowered).suffix
    return suffix in DOC_EXTENSIONS or any(token in name for token in DOC_NAME_TOKENS)


def printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    printable = sum(1 for char in text if char.isprintable() or char in "\n\r\t")
    return printable / len(text)


def collect_documents(container_id: str, excerpt_limit: int, max_docs: int) -> list[dict[str, str]]:
    proc = run_local(
        [
            "docker",
            "exec",
            "-w",
            "/workspace",
            container_id,
            "bash",
            "-lc",
            "find . -maxdepth 4 -type f ! -path './.git/*' -printf '%p\\n' 2>/dev/null",
        ],
        timeout=30,
    )
    candidates = []
    for raw in proc.stdout.splitlines():
        path = raw.strip()
        if not path or path in {"./executable", "./compile.sh"}:
            continue
        if looks_like_doc(path):
            candidates.append(path)
    docs = []
    for path in candidates[:max_docs]:
        proc = run_local(
            [
                "docker",
                "exec",
                "-w",
                "/workspace",
                container_id,
                "bash",
                "-lc",
                f"head -c {int(excerpt_limit)} {shlex.quote(path)} 2>/dev/null",
            ],
            timeout=30,
        )
        text = proc.stdout.replace("\r\n", "\n")
        if proc.returncode == 0 and printable_ratio(text) > 0.85 and text.strip():
            docs.append({"path": path, "excerpt": text})
    return docs


def classify_result(result: dict[str, Any]) -> list[str]:
    tags = [result["area"]]
    if result["returncode"] == 0:
        tags.append("exit_zero")
    else:
        tags.append("exit_nonzero")
    if result["stdout_len"]:
        tags.append("stdout")
    if result["stderr_len"]:
        tags.append("stderr")
    if result["returncode"] == 124:
        tags.append("timeout")
    return tags


def summarize(results: list[dict[str, Any]], documents: list[dict[str, str]]) -> dict[str, Any]:
    area_counts = Counter(item["area"] for item in results)
    behavior_tags = Counter(tag for item in results for tag in classify_result(item))
    returncode_counts = Counter(str(item["returncode"]) for item in results)
    return {
        "probe_count": len(results),
        "document_count": len(documents),
        "areas": dict(area_counts),
        "returncodes": dict(returncode_counts),
        "behavior_tags": dict(behavior_tags),
    }


def prioritized_findings(results: list[dict[str, Any]], documents: list[dict[str, str]]) -> list[str]:
    findings: list[str] = []
    if documents:
        findings.append("Use the bundled documentation as the primary feature inventory, then use probes to pin down process-level behavior.")
    help_outputs = [item for item in results if item["area"] == "help_usage" and item["stdout_len"]]
    if help_outputs:
        findings.append("Preserve help/version behavior; help probes produced stdout and define the visible CLI surface.")
    errors = [item for item in results if item["area"] == "errors" and item["returncode"] != 0]
    if errors:
        findings.append("Preserve invalid-argument behavior, including nonzero exit codes and stderr/stdout placement.")
    stdin = [item for item in results if item["area"] == "stdin" and (item["stdout_len"] or item["stderr_len"])]
    if stdin:
        findings.append("Stdin behavior is observable; do not assume the program is file-only.")
    file_io = [item for item in results if item["area"] == "file_io" and (item["stdout_len"] or item["stderr_len"])]
    if file_io:
        findings.append("File-path behavior is observable; handle existing, empty, and missing files carefully.")
    flags = [item for item in results if item["area"] == "flags"]
    if flags:
        findings.append(f"Help-derived option probes found {len(flags)} candidate flags; implement common flags before edge-only behavior.")
    if not findings:
        findings.append("Generic probes found limited behavior; agent should inspect documentation before choosing an implementation plan.")
    return findings


def write_markdown(task_id: str, payload: dict[str, Any], out: Path) -> None:
    lines = [
        f"# Mined Black-Box Behavior Spec: `{task_id}`",
        "",
        "This scaffold was generated without official tests or source code. It only uses docs already in the cleanroom image plus black-box executions of `/workspace/executable`.",
        "",
        "## Prioritized Requirements",
        "",
    ]
    lines.extend(f"- {item}" for item in payload["prioritized_findings"])
    if payload.get("documents"):
        lines.extend(["", "## Bundled Documentation Signals", ""])
        for doc in payload["documents"]:
            excerpt = short(doc["excerpt"], 1000).strip().replace("```", "'''")
            lines.extend([f"### `{doc['path']}`", "", "```text", excerpt, "```", ""])
    lines.extend(
        [
            "",
            "## Probe Coverage",
            "",
            f"- Total probes: {payload['summary']['probe_count']}",
            f"- Areas: `{json.dumps(payload['summary']['areas'], sort_keys=True)}`",
            f"- Return codes: `{json.dumps(payload['summary']['returncodes'], sort_keys=True)}`",
            "",
            "## Representative Evidence",
            "",
        "| case | area | args/env | rc | stdout | stderr |",
        "|---|---|---|---:|---|---|",
        ]
    )
    for item in payload["results"]:
        if item["area"] == "flags" and item["stdout_len"] == 0 and item["stderr_len"] == 0:
            continue
        stdout = item["stdout_excerpt"].replace("\n", "\\n").replace("|", "\\|")
        stderr = item["stderr_excerpt"].replace("\n", "\\n").replace("|", "\\|")
        args = shlex.join(map(str, item["args"]))
        env = " ".join(f"{key}={value}" for key, value in sorted((item.get("env") or {}).items()))
        invocation = " ".join(part for part in [env, args] if part)
        lines.append(
            f"| `{item['name']}` | {item['area']} | `{invocation}` | {item['returncode']} | {stdout[:180]} | {stderr[:180]} |"
        )
    lines.extend(
        [
            "",
            "## Agent Instruction",
            "",
            "Use this as behavioral evidence, not as implementation source. First implement the high-priority CLI surface and exact process behavior: arguments, stdin/file handling, stdout/stderr placement, exit codes, and formatting.",
            "",
        ]
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")


def mine_task(args: argparse.Namespace) -> dict[str, Any]:
    image = image_for_task(args.task)
    container_id = start_container(image, args.cpus, args.memory)
    try:
        documents = collect_documents(container_id, args.doc_excerpt_limit, args.max_docs)
        results = []
        for case in base_cases():
            results.append(run_probe(container_id, case, args.timeout, args.excerpt_limit))
        help_text = "\n".join(
            item["stdout_excerpt"] + "\n" + item["stderr_excerpt"]
            for item in results
            if item["area"] == "help_usage"
        )
        for case in flag_cases(help_text, args.max_help_flags):
            results.append(run_probe(container_id, case, args.timeout, args.excerpt_limit))
    finally:
        stop_container(container_id)

    payload = {
        "task": args.task,
        "image": image,
        "method": "fair_black_box_reference_probing",
        "summary": summarize(results, documents),
        "prioritized_findings": prioritized_findings(results, documents),
        "documents": documents,
        "results": results,
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "reports" / "mined_specs")
    parser.add_argument("--timeout", type=int, default=5)
    parser.add_argument("--cpus", type=int, default=2)
    parser.add_argument("--memory", default="8g")
    parser.add_argument("--excerpt-limit", type=int, default=700)
    parser.add_argument("--doc-excerpt-limit", type=int, default=2500)
    parser.add_argument("--max-docs", type=int, default=6)
    parser.add_argument("--max-help-flags", type=int, default=12)
    args = parser.parse_args()

    payload = mine_task(args)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / f"{args.task}.mined_spec.json"
    md_path = args.out_dir / f"{args.task}.mined_spec.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_markdown(args.task, payload, md_path)
    print(json.dumps({"json": str(json_path), "markdown": str(md_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
