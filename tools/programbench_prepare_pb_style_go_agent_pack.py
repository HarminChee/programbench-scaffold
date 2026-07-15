#!/usr/bin/env python3
"""Prepare and optionally run PB-style source-aware Go oracle-generation prompts."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path
from typing import Any

from programbench_agent_provider import run_claude_code


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASKS_ROOTS = [
    Path("/home/programbench/research/programbench/src/programbench/data/tasks"),
    Path("/home/harminchee/codex-workspaces/ProgramBench/src/programbench/data/tasks"),
    REPO_ROOT / "external/ProgramBench/src/programbench/data/tasks",
]
HF_CACHE_ROOT = Path.home() / ".cache/huggingface/hub/datasets--programbench--ProgramBench-Tests/snapshots"


def parse_simple_yaml(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip("'\"")
    return data


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def resolve_tasks_root(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    for root in DEFAULT_TASKS_ROOTS:
        if root.exists():
            return root.resolve()
    raise FileNotFoundError("ProgramBench tasks root not found; pass --tasks-root")


def run_command(cmd: list[str], *, cwd: Path | None = None, timeout: int = 600) -> dict[str, Any]:
    started = dt.datetime.now(dt.UTC)
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        returncode = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", "replace")
        timed_out = True
    ended = dt.datetime.now(dt.UTC)
    return {
        "cmd": cmd,
        "cwd": str(cwd) if cwd else None,
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_seconds": (ended - started).total_seconds(),
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
    }


def clone_source(repository: str, commit: str, dest: Path, logs_dir: Path, overwrite: bool) -> dict[str, Any]:
    if dest.exists():
        if overwrite:
            shutil.rmtree(dest)
        else:
            return {"source_dir": str(dest), "reused": True}
    logs_dir.mkdir(parents=True, exist_ok=True)
    clone = run_command(["git", "clone", "--filter=blob:none", f"https://github.com/{repository}.git", str(dest)], timeout=900)
    write_json(logs_dir / f"git_clone_{repository.replace('/', '__')}.json", clone)
    if clone["returncode"] != 0:
        raise RuntimeError(f"git clone failed: {clone['stderr_tail']}")
    checkout = run_command(["git", "checkout", commit], cwd=dest, timeout=300)
    write_json(logs_dir / f"git_checkout_{repository.replace('/', '__')}.json", checkout)
    if checkout["returncode"] != 0:
        raise RuntimeError(f"git checkout failed: {checkout['stderr_tail']}")
    return {"source_dir": str(dest), "reused": False, "clone": clone, "checkout": checkout}


def safe_read(path: Path, limit: int) -> str:
    try:
        data = path.read_bytes()[:limit]
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def source_inventory(source_dir: Path) -> dict[str, Any]:
    ignored = {".git", "vendor", "node_modules", "dist", "build", ".venv"}
    files: list[str] = []
    docs: list[str] = []
    native_tests: list[str] = []
    go_files: list[str] = []
    for path in sorted(source_dir.rglob("*")):
        if any(part in ignored for part in path.parts) or not path.is_file():
            continue
        rel = str(path.relative_to(source_dir))
        files.append(rel)
        if path.name.lower().startswith("readme") or path.suffix.lower() in {".md", ".rst", ".txt"}:
            docs.append(rel)
        if path.name.endswith("_test.go"):
            native_tests.append(rel)
        if path.suffix == ".go":
            go_files.append(rel)
    return {
        "file_count": len(files),
        "go_file_count": len(go_files),
        "doc_files": docs[:80],
        "native_test_files": native_tests[:120],
        "sample_files": files[:200],
    }


def excerpt_files(source_dir: Path, rels: list[str], per_file_limit: int, total_limit: int) -> str:
    chunks: list[str] = []
    used = 0
    for rel in rels:
        if used >= total_limit:
            break
        path = source_dir / rel
        text = safe_read(path, per_file_limit)
        if not text:
            continue
        chunk = f"\n\n## {rel}\n\n```\n{text[:per_file_limit]}\n```"
        chunks.append(chunk)
        used += len(chunk)
    return "".join(chunks)[:total_limit]


def active_branches(task_dir: Path) -> list[str]:
    tests_path = task_dir / "tests.json"
    if not tests_path.exists():
        return []
    data = json.loads(tests_path.read_text(encoding="utf-8"))
    return [name for name, info in (data.get("branches") or {}).items() if not info.get("ignored")]


def find_blob_dir(instance_id: str) -> Path | None:
    if not HF_CACHE_ROOT.exists():
        return None
    for snapshot in sorted(HF_CACHE_ROOT.glob("*"), reverse=True):
        candidate = snapshot / instance_id
        if (candidate / "tests").exists():
            return candidate
    return None


def safe_extract(tar_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path) as tar:
        root = dest.resolve()
        for member in tar.getmembers():
            target = (dest / member.name).resolve()
            if root not in target.parents and target != root:
                raise RuntimeError(f"Refusing unsafe tar member: {member.name}")
        tar.extractall(dest)


def materialize_one_shot_example(
    *,
    example_instance_id: str,
    target_instance_id: str,
    tasks_root: Path,
    pack_root: Path,
    test_file_limit: int,
) -> dict[str, Any]:
    if example_instance_id == target_instance_id:
        raise ValueError("example_instance_id must differ from target_instance_id")
    task_dir = tasks_root / example_instance_id
    task_yaml = parse_simple_yaml(task_dir / "task.yaml")
    target_yaml = parse_simple_yaml(tasks_root / target_instance_id / "task.yaml")
    if task_yaml.get("language") != target_yaml.get("language"):
        raise ValueError("one-shot example and target must use the same language")
    branches = active_branches(task_dir)
    example_root = pack_root / "one_shot_example"
    example_root.mkdir(parents=True, exist_ok=True)
    metadata = {
        "example_instance_id": example_instance_id,
        "repository": task_yaml.get("repository"),
        "commit": task_yaml.get("commit"),
        "language": task_yaml.get("language"),
        "active_branch_count": len(branches),
        "active_branches_sample": branches[:10],
        "target_instance_id": target_instance_id,
        "target_language": target_yaml.get("language"),
        "example_separation_verified": True,
        "ablation_label": f"oneshot_{example_instance_id}_for_{target_instance_id}",
        "policy": "Allowed only as a same-language one-shot example. Never use target official oracle tests.",
    }
    blob_dir = find_blob_dir(example_instance_id)
    copied: list[str] = []
    if blob_dir and branches:
        tar_path = blob_dir / "tests" / f"{branches[0]}.tar.gz"
        if tar_path.exists():
            metadata["example_branch"] = branches[0]
            metadata["example_archive_sha256"] = hashlib.sha256(tar_path.read_bytes()).hexdigest()
            extract_dir = pack_root / "_example_extract"
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
            safe_extract(tar_path, extract_dir)
            for path in sorted((extract_dir / "eval" / "tests").rglob("test_*.py"))[:test_file_limit]:
                rel = path.relative_to(extract_dir)
                dest = example_root / "oracle_excerpt" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(path.read_text(encoding="utf-8", errors="replace")[:30_000], encoding="utf-8")
                copied.append(str(dest.relative_to(example_root)))
    metadata["oracle_excerpt_files"] = copied
    metadata["oracle_excerpt_sha256"] = {
        rel: hashlib.sha256((example_root / rel).read_bytes()).hexdigest() for rel in copied
    }
    write_json(example_root / "metadata.json", metadata)
    return metadata


def build_prompt(pack_root: Path, target_meta: dict[str, Any], example_meta: dict[str, Any] | None) -> None:
    prompts_dir = pack_root / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    system = """You are a ProgramBench-style oracle-test generation agent.

Generate executable behavioral tests, not source-level unit tests. You may use
target source code, docs, and native tests during benchmark construction, but
the final tests must assert only externally observable executable behavior.
Do not read or use target ProgramBench official oracle tests or target hidden
test blobs. A same-language one-shot example may be used only when it is from a
different ProgramBench instance.
"""
    (prompts_dir / "system.md").write_text(system, encoding="utf-8")
    schema = {
        "profile": "pb_agent_source_aware_go_v1",
        "cases": [
            {
                "name": "short_unique_name",
                "area": "args|config|help|io|subcommand|tui|native_harvest|coverage_gap|error_handling",
                "args": ["--help"],
                "stdin": "",
                "rationale": "why this probes a meaningful executable behavior",
            }
        ],
    }
    request = f"""# Task

Produce a JSON case spec for `tools/programbench_generate_cli_oracle_bundle.py`.
Do not include expected stdout, stderr, or returncode. The capture engine will
run each case against the cleanroom reference executable and materialize exact
fixtures.

# Target

```json
{json.dumps(target_meta, indent=2, sort_keys=True)}
```

# Strategy To Reproduce ProgramBench

Use the PB-style loop:

1. harvest existing behavioral tests from native tests when they exercise the CLI;
2. add monolithic broad probes from docs/source;
3. add decomposed probes for Args, Config, Help, I/O, Subcommand, and TUI;
4. prefer cases likely to cover first-party source paths;
5. include negative/error-path cases;
6. keep tests executable-level and deterministic.

# Output Schema

Return only JSON with this shape:

```json
{json.dumps(schema, indent=2)}
```

# Files In This Pack

- `target_context/source_inventory.json`
- `target_context/docs_excerpt.md`
- `target_context/native_tests_excerpt.md`
- `one_shot_example/metadata.json`
- optional `one_shot_example/oracle_excerpt/eval/tests/*.py`

The one-shot example is a style example from another Go instance, not target
gold. Never transfer target-specific official oracle content into the target
case spec.
"""
    if example_meta:
        request += f"\n# One-shot Example Metadata\n\n```json\n{json.dumps(example_meta, indent=2, sort_keys=True)}\n```\n"
    (prompts_dir / "agent_request.md").write_text(request, encoding="utf-8")

    decomposed = {
        "args": "Focus on argument parsing, aliases, invalid flags, and positional combinations.",
        "config": "Focus on config files, environment variables, defaults, and precedence if present.",
        "help": "Focus on help/version/usage output and command discovery.",
        "io": "Focus on stdin/stdout/stderr, file input/output, encodings, empty input, and invalid input.",
        "subcommand": "Focus on subcommands and command-specific flags if the program has them.",
        "tui": "Focus on terminal behaviors only when the program exposes noninteractive observable modes.",
    }
    decomp_dir = prompts_dir / "decomposed"
    decomp_dir.mkdir(parents=True, exist_ok=True)
    for name, body in decomposed.items():
        (decomp_dir / f"{name}.md").write_text(f"{request}\n\n# Specialized Bucket\n\n{body}\n", encoding="utf-8")


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    tasks_root = resolve_tasks_root(args.tasks_root)
    task_dir = tasks_root / args.instance_id
    task_yaml = parse_simple_yaml(task_dir / "task.yaml")
    pack_root = (args.output_root / args.instance_id / args.pack_label).resolve()
    work_root = (args.work_root / args.instance_id / args.pack_label).resolve()
    if pack_root.exists():
        if not args.overwrite:
            raise FileExistsError(f"Pack exists: {pack_root}")
        shutil.rmtree(pack_root)
    if work_root.exists():
        if args.overwrite:
            shutil.rmtree(work_root)
    logs_dir = work_root / "logs"
    source_dir = work_root / "target_source"
    clone_info = clone_source(str(task_yaml["repository"]), str(task_yaml["commit"]), source_dir, logs_dir, args.overwrite)
    inventory = source_inventory(source_dir)
    target_context = pack_root / "target_context"
    target_context.mkdir(parents=True, exist_ok=True)
    target_meta = {
        "instance_id": args.instance_id,
        "repository": task_yaml.get("repository"),
        "commit": task_yaml.get("commit"),
        "language": task_yaml.get("language"),
        "difficulty": task_yaml.get("difficulty"),
        "source_policy": "target source/docs/native tests allowed; target official ProgramBench oracle tests forbidden",
        "clone_info": clone_info,
    }
    write_json(target_context / "task_metadata.json", target_meta)
    write_json(target_context / "source_inventory.json", inventory)
    (target_context / "docs_excerpt.md").write_text(
        excerpt_files(source_dir, inventory["doc_files"], args.per_file_excerpt_bytes, args.total_doc_excerpt_bytes),
        encoding="utf-8",
    )
    (target_context / "native_tests_excerpt.md").write_text(
        excerpt_files(source_dir, inventory["native_test_files"], args.per_file_excerpt_bytes, args.total_test_excerpt_bytes),
        encoding="utf-8",
    )
    example_meta = None
    if args.example_instance_id:
        example_meta = materialize_one_shot_example(
            example_instance_id=args.example_instance_id,
            target_instance_id=args.instance_id,
            tasks_root=tasks_root,
            pack_root=pack_root,
            test_file_limit=args.example_test_file_limit,
        )
    build_prompt(pack_root, {**target_meta, "inventory": inventory}, example_meta)
    summary = {
        "pack_root": str(pack_root),
        "work_root": str(work_root),
        "target": target_meta,
        "inventory": inventory,
        "one_shot_example": example_meta,
        "agent_request": str(pack_root / "prompts" / "agent_request.md"),
    }
    write_json(pack_root / "pack_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_anthropic_text(payload: dict[str, Any]) -> str:
    parts = payload.get("content") or []
    texts = [item.get("text", "") for item in parts if isinstance(item, dict) and item.get("type") == "text"]
    return "\n".join(texts)


def call_agent(args: argparse.Namespace) -> int:
    pack_root = args.pack_root.expanduser().resolve()
    prompt = (pack_root / "prompts" / "agent_request.md").read_text(encoding="utf-8")
    system = (pack_root / "prompts" / "system.md").read_text(encoding="utf-8")
    model = args.model or ("claude-sonnet-5[1m]" if args.provider == "claude-code" else "claude-sonnet-5")
    output = args.output or (pack_root / "agent_outputs" / f"{args.provider}_{model}.txt")
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.provider == "agent-maestro-anthropic":
        base = os.getenv("AGENT_MAESTRO_BASE_URL", "http://127.0.0.1:23333")
        url = f"{base.rstrip('/')}/api/anthropic/v1/messages"
        headers = {"Content-Type": "application/json"}
        key = os.getenv("AGENT_MAESTRO_API_KEY")
        if key:
            headers["x-api-key"] = key
        payload = {
            "model": model,
            "max_tokens": args.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            response = post_json(url, payload, headers, args.timeout)
        except Exception as exc:
            write_json(
                output.with_suffix(".error.json"),
                {
                    "provider": args.provider,
                    "model": model,
                    "url": url,
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                    "hint": "Start Agent Maestro API server or use --provider copilot-cli/claude-code when available.",
                },
            )
            return 1
        text = extract_anthropic_text(response)
        output.write_text(text, encoding="utf-8")
        write_json(output.with_suffix(".response.json"), response)
        print(str(output))
        return 0
    if args.provider == "copilot-cli":
        exe = shutil.which("copilot")
        if not exe:
            raise FileNotFoundError("copilot CLI not found in PATH")
        cmd = [exe, "-p", prompt, "--model", model, "--output-format", "text", "--max-ai-credits", str(args.max_ai_credits)]
    elif args.provider == "claude-code":
        run = run_claude_code(
            prompt=f"{system}\n\n{prompt}",
            cwd=pack_root,
            output_root=output.parent / f"{output.stem}_run",
            model=model,
            max_turns=args.max_turns,
            timeout=args.timeout,
        )
        output.write_text(run.result_text, encoding="utf-8")
        print(str(output))
        return run.returncode
    else:
        raise ValueError(f"unknown provider: {args.provider}")
    result = subprocess.run(cmd, cwd=pack_root, capture_output=True, text=True, timeout=args.timeout)
    output.write_text(result.stdout, encoding="utf-8")
    write_json(output.with_suffix(".run.json"), {"cmd": cmd, "returncode": result.returncode, "stderr": result.stderr[-8000:]})
    print(str(output))
    return result.returncode


def find_json_object(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in agent output")
    return json.loads(text[start : end + 1])


def extract_cases(args: argparse.Namespace) -> int:
    payload = find_json_object(args.agent_output.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError("Agent output JSON does not contain a cases list")
    normalized: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            continue
        normalized.append(
            {
                "name": str(case.get("name") or f"agent_case_{index:03d}"),
                "area": str(case.get("area") or "agent_generated"),
                "args": list(case.get("args") or []),
                "stdin": str(case.get("stdin") or ""),
                "origin": "agent_generated",
                "rationale": str(case.get("rationale") or ""),
            }
        )
    out = args.output_json if args.output_json.is_absolute() else (REPO_ROOT / args.output_json).resolve()
    write_json(out, {"profile": args.profile, "source": str(args.agent_output), "cases": normalized})
    print(json.dumps({"output_json": str(out), "case_count": len(normalized)}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_prepare = sub.add_parser("prepare")
    p_prepare.add_argument("instance_id")
    p_prepare.add_argument("--example-instance-id")
    p_prepare.add_argument("--tasks-root", type=Path)
    p_prepare.add_argument("--work-root", type=Path, default=Path("/tmp/programbench_pb_style_go_agent_packs"))
    p_prepare.add_argument("--output-root", type=Path, default=REPO_ROOT / "reports/programbench_pb_style_agent_packs")
    p_prepare.add_argument("--pack-label", default="pb_style_go_v1")
    p_prepare.add_argument("--per-file-excerpt-bytes", type=int, default=20_000)
    p_prepare.add_argument("--total-doc-excerpt-bytes", type=int, default=120_000)
    p_prepare.add_argument("--total-test-excerpt-bytes", type=int, default=120_000)
    p_prepare.add_argument("--example-test-file-limit", type=int, default=4)
    p_prepare.add_argument("--overwrite", action="store_true")
    p_prepare.set_defaults(func=prepare)

    p_call = sub.add_parser("call-agent")
    p_call.add_argument("--pack-root", type=Path, required=True)
    p_call.add_argument("--provider", choices=["agent-maestro-anthropic", "copilot-cli", "claude-code"], default="agent-maestro-anthropic")
    p_call.add_argument("--model")
    p_call.add_argument("--max-tokens", type=int, default=8192)
    p_call.add_argument("--max-ai-credits", type=int, default=30)
    p_call.add_argument("--timeout", type=int, default=900)
    p_call.add_argument("--max-turns", type=int, default=24)
    p_call.add_argument("--output", type=Path)
    p_call.set_defaults(func=call_agent)

    p_extract = sub.add_parser("extract-cases")
    p_extract.add_argument("--agent-output", type=Path, required=True)
    p_extract.add_argument("--output-json", type=Path, required=True)
    p_extract.add_argument("--profile", default="pb_agent_source_aware_go_v1")
    p_extract.set_defaults(func=extract_cases)

    args = parser.parse_args()
    result = args.func(args)
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
