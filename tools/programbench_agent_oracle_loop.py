#!/usr/bin/env python3
"""Agent-dominant, coverage-guided ProgramBench Go oracle construction loop."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from programbench_agent_provider import run_agent_maestro_review, run_claude_code, write_json
from programbench_test_review_agent import REVIEW_SYSTEM, build_review_prompt, validate_review


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASKS_ROOT = Path("/home/programbench/research/programbench/src/programbench/data/tasks")
DEFAULT_WORKSPACE_ROOT = Path("/home/programbench/research/oracle-workspace")
DEFAULT_EXAMPLES = [
    "sclevine__yj.8016400",
    "multiprocessio__dsq.c3ae0ba",
    "rs__jplot.2a54bcc",
    "psampaz__go-mod-outdated.bb79367",
]
INFRA_MARKERS = [
    "bad record mac",
    "connection reset",
    "temporary failure",
    "timed out",
    "tls handshake",
    "could not resolve host",
    "gnutls",
    "rpc failed",
    "early eof",
    "unexpected disconnect",
    "connection closed by remote host",
    "could not pull reference image",
]

SUITE_UPDATE_SCHEMA = {
    "type": "object",
    "required": ["analysis_summary", "suite_updated", "case_count"],
    "properties": {
        "analysis_summary": {"type": "string"},
        "suite_updated": {"type": "boolean"},
        "case_count": {"type": "integer", "minimum": 1},
    },
    "additionalProperties": False,
}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def parse_simple_yaml(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw or raw.startswith((" ", "-", "#")) or ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        result[key.strip()] = value.strip().strip('"\'')
    return result


def run_step(name: str, cmd: list[str], logs_dir: Path, *, timeout: int = 3600) -> dict[str, Any]:
    started = dt.datetime.now(dt.UTC)
    try:
        proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout)
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
    result = {
        "name": name,
        "command": cmd,
        "returncode": returncode,
        "timed_out": timed_out,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_seconds": round((ended - started).total_seconds(), 3),
        "stdout_tail": stdout[-8000:],
        "stderr_tail": stderr[-8000:],
    }
    write_json(logs_dir / f"{name}.json", {**result, "stdout": stdout, "stderr": stderr})
    return result


def image_name(instance_id: str) -> str:
    owner, rest = instance_id.split("__", 1)
    return f"programbench/{owner}_1776_{rest}:task_cleanroom_v6"


def is_infrastructure_error(value: Any) -> bool:
    text = json.dumps(value, sort_keys=True, default=str).lower()
    return any(marker in text for marker in INFRA_MARKERS)


def materialize_reference(instance_id: str, workspace: Path, logs_dir: Path) -> Path:
    reference_dir = workspace / "reference"
    reference_dir.mkdir(parents=True, exist_ok=True)
    executable = reference_dir / "executable"
    image = image_name(instance_id)
    inspect = run_step("docker_inspect_reference", ["docker", "image", "inspect", image], logs_dir, timeout=120)
    if inspect["returncode"] != 0:
        pull = run_step("docker_pull_reference", ["docker", "pull", image], logs_dir, timeout=3600)
        if pull["returncode"] != 0:
            raise RuntimeError(f"Could not pull reference image for {instance_id}: {pull['stderr_tail']}")
    create = run_step("docker_create_reference", ["docker", "create", image], logs_dir, timeout=120)
    if create["returncode"] != 0:
        raise RuntimeError(f"Could not create reference container for {instance_id}: {create['stderr_tail']}")
    container_id = create["stdout_tail"].strip().splitlines()[-1]
    try:
        copied = run_step(
            "docker_copy_reference",
            ["docker", "cp", f"{container_id}:/workspace/executable", str(executable)],
            logs_dir,
            timeout=600,
        )
        if copied["returncode"] != 0:
            raise RuntimeError("Could not copy cleanroom reference executable")
    finally:
        subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, text=True)
    executable.chmod(0o111)
    return executable


def choose_example(instance_id: str, explicit: str | None) -> str:
    if explicit:
        if explicit == instance_id:
            raise ValueError("The one-shot example must be a different ProgramBench instance")
        return explicit
    return next(item for item in DEFAULT_EXAMPLES if item != instance_id)


def normalize_case_env(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    blocked = {"PATH", "HOME", "PWD", "OLDPWD", "SHELL", "USER", "LOGNAME", "TMPDIR"}
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        upper = key.upper()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key):
            continue
        if upper in blocked or upper.startswith(("LD_", "DYLD_")):
            continue
        if any(marker in upper for marker in ("SECRET", "TOKEN", "PASSWORD", "CREDENTIAL", "API_KEY")):
            continue
        text = str(raw_value)
        if "\x00" in text or len(text) > 4096:
            continue
        result[key] = text
        if len(result) >= 32:
            break
    return result


def normalize_case_files(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    total = 0
    for raw_path, raw_content in value.items():
        path = PurePosixPath(str(raw_path))
        text = str(raw_content)
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            continue
        size = len(text.encode("utf-8"))
        if "\x00" in str(path) or size > 256_000 or total + size > 1_000_000:
            continue
        result[str(path)] = text
        total += size
        if len(result) >= 16:
            break
    return result


def normalize_case_http(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    try:
        status = int(value.get("status", 200))
    except (TypeError, ValueError):
        return {}
    path = str(value.get("path") or "/")
    body = str(value.get("body") or "")
    if not path.startswith("/") or any(char in path for char in "\r\n"):
        return {}
    if not 100 <= status <= 599 or len(body.encode("utf-8")) > 256_000:
        return {}
    headers: dict[str, str] = {}
    for raw_key, raw_value in dict(value.get("headers") or {}).items():
        key, text = str(raw_key), str(raw_value)
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", key) or any(char in text for char in "\r\n"):
            continue
        headers[key] = text[:4096]
        if len(headers) >= 16:
            break
    return {"path": path, "status": status, "headers": headers, "body": body}


def case_signature(case: dict[str, Any]) -> str:
    core = {
        "args": case.get("args") or [],
        "stdin": str(case.get("stdin") or ""),
        "env": case.get("env") or {},
        "files": case.get("files") or {},
        "http": case.get("http") or {},
        "stdout_mode": case.get("stdout_mode") or "exact",
    }
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode("utf-8")).hexdigest()


def normalize_cases(payload: dict[str, Any], *, max_cases: int, iteration: int) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    signatures: set[str] = set()
    for index, raw in enumerate(payload.get("cases") or []):
        if not isinstance(raw, dict):
            continue
        args = raw.get("args") or []
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            continue
        if any("\x00" in item for item in args):
            continue
        case = {
            "name": re.sub(r"[^A-Za-z0-9_]+", "_", str(raw.get("name") or f"agent_{iteration}_{index}"))[:120],
            "area": str(raw.get("area") or "agent_generated")[:120],
            "args": args[:100],
            "stdin": str(raw.get("stdin") or "")[:256_000],
            "origin": str(raw.get("origin") or f"agent_iteration_{iteration}"),
            "rationale": str(raw.get("rationale") or "")[:2000],
        }
        env = normalize_case_env(raw.get("env"))
        files = normalize_case_files(raw.get("files"))
        http = normalize_case_http(raw.get("http"))
        if env:
            case["env"] = env
        if files:
            case["files"] = files
        if http:
            case["http"] = http
        stdout_mode = str(raw.get("stdout_mode") or "exact")
        if stdout_mode in {"exact", "lines_unordered"} and stdout_mode != "exact":
            case["stdout_mode"] = stdout_mode
        if not case["name"] or case["name"] in names:
            continue
        signature = case_signature(case)
        if signature in signatures:
            continue
        names.add(case["name"])
        signatures.add(signature)
        normalized.append(case)
        if len(normalized) >= max_cases:
            break
    return normalized


def seed_cases(
    *, instance_id: str, tasks_root: Path, workspace: Path, max_cases: int, logs_dir: Path
) -> Path:
    output = workspace / "candidate_cases.json"
    step = run_step(
        "source_aware_seed",
        [
            sys.executable,
            "tools/programbench_generate_source_aware_cli_cases.py",
            instance_id,
            "--tasks-root",
            str(tasks_root),
            "--work-root",
            str(workspace / "seed_work"),
            "--output-json",
            str(output),
            "--profile",
            "agent_seed_v1",
            "--max-cases",
            str(min(max_cases, 180)),
            "--overwrite",
        ],
        logs_dir,
        timeout=1800,
    )
    if step["returncode"] != 0:
        raise RuntimeError("Source-aware seed generation failed")
    return output


def prepare_agent_context(
    *,
    instance_id: str,
    example_instance_id: str,
    tasks_root: Path,
    workspace: Path,
    logs_dir: Path,
) -> Path:
    pack_root = workspace / "pack"
    pack_work = workspace / "pack_work"
    step = run_step(
        "prepare_agent_pack",
        [
            sys.executable,
            "tools/programbench_prepare_pb_style_go_agent_pack.py",
            "prepare",
            instance_id,
            "--tasks-root",
            str(tasks_root),
            "--example-instance-id",
            example_instance_id,
            "--output-root",
            str(pack_root),
            "--work-root",
            str(pack_work),
            "--pack-label",
            "agent_loop_v2",
            "--overwrite",
        ],
        logs_dir,
        timeout=1800,
    )
    if step["returncode"] != 0:
        raise RuntimeError("Agent pack preparation failed")
    source = pack_work / instance_id / "agent_loop_v2" / "target_source"
    context = pack_root / instance_id / "agent_loop_v2"
    shutil.copytree(context / "target_context", source / "agent_context" / "target", dirs_exist_ok=True)
    if (context / "one_shot_example").exists():
        shutil.copytree(context / "one_shot_example", source / "agent_context" / "one_shot", dirs_exist_ok=True)
    (source / "agent_tools").mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "tools" / "programbench_agent_probe_reference.py", source / "agent_tools" / "probe_reference.py")
    shutil.copy2(
        REPO_ROOT / "tools" / "programbench_agent_probe_reference_windows.ps1",
        source / "agent_tools" / "probe_reference_windows.ps1",
    )
    return source


def build_generation_prompt(
    *,
    instance_id: str,
    iteration: int,
    target_coverage: float,
    max_cases: int,
    previous: dict[str, Any] | None,
) -> str:
    feedback = "No previous iteration. Read and improve candidate_cases.json in place."
    if previous:
        feedback = json.dumps(previous, indent=2, sort_keys=True)
    windows_bridge = os.getenv("PROGRAMBENCH_WINDOWS_CLAUDE_BRIDGE", "").strip().lower() in {"1", "true", "yes"}
    if windows_bridge:
        configured_probe = os.getenv("PROGRAMBENCH_WINDOWS_PROBE_SCRIPT", "").strip()
        if configured_probe:
            windows_probe_script = configured_probe
        else:
            probe_script = REPO_ROOT / "tools" / "programbench_agent_probe_reference_windows.ps1"
            if os.name == "nt":
                windows_probe_script = str(probe_script.resolve())
            else:
                converted = subprocess.run(
                    ["wslpath", "-w", str(probe_script.resolve())],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                windows_probe_script = converted.stdout.strip()
        windows_probe_script = windows_probe_script.replace("\\", "/")
        probe_command = (
            "powershell.exe -NoProfile -ExecutionPolicy Bypass -File "
            f"{windows_probe_script}"
        )
        configured_validator = os.getenv("PROGRAMBENCH_WINDOWS_VALIDATOR_SCRIPT", "").strip()
        if configured_validator:
            windows_validator_script = configured_validator
        else:
            validator_script = REPO_ROOT / "tools" / "programbench_agent_validate_cases_windows.ps1"
            if os.name == "nt":
                windows_validator_script = str(validator_script.resolve())
            else:
                converted_validator = subprocess.run(
                    ["wslpath", "-w", str(validator_script.resolve())],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                windows_validator_script = converted_validator.stdout.strip()
        windows_validator_script = windows_validator_script.replace("\\", "/")
        validator_command = (
            "powershell.exe -NoProfile -ExecutionPolicy Bypass -File "
            f"{windows_validator_script}"
        )
    else:
        probe_command = "python3 agent_tools/probe_reference.py"
        validator_command = ""
    windows_command_rules = ""
    if windows_bridge:
        windows_command_rules = f"""
This is a Windows Claude process working on a WSL UNC directory. Do not diagnose
the current directory and do not run `pwd`, `Get-Location`, `python`, `ls`,
`stat`, `touch`, `printf`, pipes, redirects, or command chaining. For every
reference probe, use the fixed forward-slash command shown above; do not replace
it with a UNC path, `./`, or backslashes. The helper itself receives the target
workspace from its parent process and bridges safely back into Linux.
After writing the suite, run exactly `{validator_command}`. Remove invalid or
duplicate entries when practical, and use its `valid_unique_count` as the final
structured `case_count`; do not report the raw array length.
"""
    coverage_only_rules = ""
    previous_record = (previous or {}).get("previous_iteration") or previous or {}
    review_payload = (previous or {}).get("review") or {}
    review_verdict = previous_record.get("review_verdict") or review_payload.get("suite_verdict")
    if previous and previous_record.get("quality_passed") and review_verdict == "keep":
        coverage_only_rules = """
The current suite has already passed every quality gate and review. Treat this
as a coverage-only increment: preserve all existing cases, add no more than 16
new cases, and do not rewrite rationales or refactor cases that already pass.
Work down the supplied coverage gaps from lowest coverage upward, but skip a
gap once source inspection shows it is not reachable through the public CLI.
Before adding a case, map its name to one explicit function in `coverage_gaps`
and to the concrete source branch/input condition it should exercise. Do not
add a merely distinct behavior when you cannot identify a new uncovered branch
it should hit. Prefer reachable partially covered functions over zero-percent
helpers that source inspection shows are internal-only.
Use at most 20 reference probes. Commit the suite file immediately after the
last useful probe; do not spend turns trying to diagnose or inspect the probe
helper itself.
"""
    elif previous:
        coverage_only_rules = """
This is a bounded quality-repair increment. Preserve every case marked `keep`.
First remove `reject` cases, then repair only the named `revise` and
dummy-passing or `binary_inconsistent_case_names` cases. Do not add coverage-only cases
in this increment; coverage work resumes after the suite
passes quality and review. Use at most 12 reference
probes and commit the complete suite file immediately after those repairs. Do
not inspect or diagnose the probe helper itself.
"""
    return f"""You are the primary ProgramBench oracle-test generation agent for {instance_id}.

Work as the test designer. Inspect `agent_context/target`, the full source tree,
native tests, and docs. You may execute `{probe_command}`
to observe the execute-only reference. Read `candidate_cases.json`; it contains
the current complete suite. The cross-instance material in
`agent_context/one_shot` is a style example only. Never inspect target official
ProgramBench oracle tests or hidden target test blobs.

Edit `candidate_cases.json` in place so that it contains the complete revised
suite under its `cases` key. Do not replace a real suite with a placeholder and
do not shrink it merely to fit the final response: the suite is delivered by
the file, not by the response. Every case must exercise an externally observable
executable behavior. Use source knowledge to choose inputs, then use reference
probes to confirm the behavior exists. Harvest real CLI behaviors from native
tests. Cover Args, Config, Help, I/O, Subcommand and noninteractive TUI paths
when present. Include invalid-input and boundary cases. Avoid duplicates,
timestamps, random output, network dependencies, host-specific paths, and
implementation-only assertions. Each rationale must identify the behavior or
coverage gap it targets.

Cases may optionally use three deterministic fixture fields. `env` is a mapping
of static, non-secret environment variables. `files` maps safe relative paths
to UTF-8 contents; those files are created in a fresh temporary working
directory before execution. `http` describes one loopback-only response with
`path`, `status`, optional `headers`, and `body`; use the literal placeholder
`{{http_url}}` in args, stdin, or env and the harness replaces it with the
temporary server URL. Never use an external network host. Probe these fields
with the matching `--env-json`, `--files-json`, and `--http-json` options.
The optional `stdout_mode` may be `lines_unordered` only when source inspection
shows complete output lines are semantically meaningful but their order is
nondeterministic (for example Go map iteration). This remains a full set-equality
assertion; never use it to hide missing or extra lines.
{coverage_only_rules}

Use the Read tool, not Bash/cat/python, to inspect JSON or source files. For a
multiline probe input, use Write to create a file inside `agent_tools/`, then run
exactly one command of this form in each Bash call:
`{probe_command} ... --stdin-file agent_tools/INPUT`.
Do not use `cd`, pipes, redirects, command separators, shell variables, or an
absolute helper path in a Bash call; those commands are intentionally denied.
{windows_command_rules}

Before finishing, re-read `candidate_cases.json` and verify that it is valid JSON
with at least one case. Your final structured response is only a compact commit
record: set `suite_updated` to true, report the exact `case_count` in the file,
and summarize the changes. Do not return the case array in the final response.
Reserve the final two tool turns for writing `candidate_cases.json` and returning
the structured commit record. Do not spend the final turns diagnosing tools.

Iteration: {iteration}
Coverage goal: {target_coverage:.1f}% first-party executable-line coverage
Maximum complete suite size: {max_cases}

Previous gate, review, and coverage feedback:

```json
{feedback}
```
"""


def load_agent_updated_suite(
    path: Path,
    confirmation: dict[str, Any],
    *,
    max_cases: int,
    iteration: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fail closed unless the agent committed a valid suite file and exact count."""

    if confirmation.get("suite_updated") is not True:
        raise ValueError("generation agent did not confirm suite update")
    payload = read_json(path)
    cases = normalize_cases(payload, max_cases=max_cases, iteration=iteration)
    if not cases:
        raise ValueError("generation agent suite file contains no valid cases")
    try:
        reported_count = int(confirmation.get("case_count"))
    except (TypeError, ValueError) as exc:
        raise ValueError("generation agent did not report a valid case count") from exc
    if reported_count != len(cases):
        raise ValueError(
            f"generation agent reported {reported_count} cases but {len(cases)} valid unique cases were found"
        )
    return payload, cases


def is_recoverable_max_turn_run(agent_run: Any) -> bool:
    """Recognize only the provider's max-turn terminal condition.

    A recovered candidate remains untrusted: it must still pass the complete
    execution, quality, coverage, and independent-review pipeline before it can
    become a checkpoint. Timeouts and other non-zero exits continue to fail
    closed.
    """

    if agent_run.returncode == 0 or agent_run.timed_out:
        return False
    details = agent_run.structured_output if isinstance(agent_run.structured_output, dict) else {}
    terminal_reason = str(details.get("terminal_reason") or "").lower()
    subtype = str(details.get("subtype") or "").lower()
    errors = " ".join(str(item) for item in details.get("errors") or []).lower()
    return (
        terminal_reason == "max_turns"
        or subtype == "error_max_turns"
        or "maximum number of turns" in errors
    )


def load_recovered_agent_suite(
    path: Path,
    *,
    max_cases: int,
    iteration: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Normalize an on-disk max-turn candidate without faking confirmation."""

    payload = read_json(path)
    cases = normalize_cases(payload, max_cases=max_cases, iteration=iteration)
    if not cases:
        raise ValueError("max-turn recovery candidate contains no valid cases")
    return payload, cases


def coverage_gaps(coverage_summary: dict[str, Any]) -> list[dict[str, Any]]:
    profile_text = str((coverage_summary.get("generated_tests") or {}).get("profile") or "")
    if not profile_text or not Path(profile_text).exists():
        return []
    profile = Path(profile_text)
    proc = subprocess.run(
        ["go", "tool", "cover", "-func", str(profile)],
        cwd=profile.parent.parent,
        capture_output=True,
        text=True,
    )
    gaps: list[dict[str, Any]] = []
    pattern = re.compile(r"^(.*?):(\d+):\s+(.+?)\s+([0-9.]+)%$")
    for line in proc.stdout.splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        percent = float(match.group(4))
        if percent >= 100.0:
            continue
        gaps.append(
            {
                "file": match.group(1),
                "line": int(match.group(2)),
                "function": match.group(3),
                "coverage_percent": percent,
            }
        )
    return sorted(gaps, key=lambda item: (item["coverage_percent"], item["file"], item["line"]))[:120]


def quality_passed(summary: dict[str, Any]) -> bool:
    quality = summary.get("quality") or {}
    coverage = summary.get("coverage") or {}
    repeat = quality.get("repeat_junit_summary") or {}
    return all(
        [
            summary.get("status") == "passed",
            quality.get("all_dummies_rejected") is True,
            quality.get("source_leak_passed") is True,
            quality.get("assertion_lint_passed") is True,
            quality.get("assertion_lint_high_count") == 0,
            quality.get("repeat_returncode") == 0,
            repeat.get("failures", 0) == 0,
            repeat.get("errors", 0) == 0,
            coverage.get("binary_behavior_consistent") is True,
        ]
    )


def coverage_value(summary: dict[str, Any]) -> float:
    coverage = summary.get("coverage") or {}
    # Go's canonical coverage metric is statement coverage.  The executable
    # line value is derived from profile block ranges and is diagnostic only.
    value = coverage.get("generated_go_statement_coverage")
    if value is None:
        value = coverage.get("generated_go_line_coverage")
    return float(value) if value is not None else 0.0


def plateaued(history: list[dict[str, Any]], *, delta: float, patience: int) -> bool:
    if len(history) < patience + 1:
        return False
    values = [float(item.get("coverage_percent") or 0.0) for item in history[-(patience + 1) :]]
    return all(abs(values[index + 1] - values[index]) < delta for index in range(len(values) - 1))


def compact_reference_observations(quality_report: dict[str, Any]) -> dict[str, Any]:
    cases = []
    for case in quality_report.get("cases") or []:
        if not isinstance(case, dict):
            continue
        cases.append(
            {
                "name": case.get("name"),
                "returncode": case.get("returncode"),
                "stdout_bytes": case.get("stdout_bytes"),
                "stderr_bytes": case.get("stderr_bytes"),
                "stdout_sha256": case.get("stdout_sha256"),
                "stderr_sha256": case.get("stderr_sha256"),
                "timed_out": case.get("timed_out"),
            }
        )
    return {"kept": cases, "skipped": quality_report.get("skipped_cases") or []}


def case_name_from_pytest_name(value: Any) -> str:
    leaf = str(value or "").split(".")[-1]
    return re.sub(r"^test_\d+_", "", leaf)


def binary_inconsistent_case_names(coverage_summary: dict[str, Any]) -> list[str]:
    names: set[str] = set()
    for branch in coverage_summary.get("branch_results") or []:
        if not isinstance(branch, dict):
            continue
        for result in branch.get("binary_results") or []:
            if not isinstance(result, dict):
                continue
            junit = result.get("junit_summary") or {}
            for key in ("filtered_failed_test_names", "filtered_error_test_names"):
                for test_name in junit.get(key) or []:
                    name = case_name_from_pytest_name(test_name)
                    if name:
                        names.add(name)
    return sorted(names)


def review_evidence_batches(evidence: dict[str, Any], *, batch_size: int = 32) -> list[dict[str, Any]]:
    """Split a large review while keeping only per-case evidence for each batch."""

    cases = [case for case in evidence.get("cases") or [] if isinstance(case, dict)]
    observations = evidence.get("reference_observations") or {}
    quality = evidence.get("quality_gates") or {}
    previous = evidence.get("previous_review") or {}
    batches: list[dict[str, Any]] = []
    for start in range(0, len(cases), max(1, batch_size)):
        batch_cases = cases[start : start + max(1, batch_size)]
        names = {str(case.get("name") or "") for case in batch_cases}
        batch_observations = {
            "kept": [item for item in observations.get("kept") or [] if str(item.get("name") or "") in names],
            "skipped": [
                item
                for item in observations.get("skipped") or []
                if str(item.get("name") if isinstance(item, dict) else item) in names
            ],
        }
        batch_dummy_results = []
        for item in quality.get("dummy_results") or []:
            if not isinstance(item, dict):
                continue
            passing = [
                name
                for name in item.get("passing_test_names") or []
                if case_name_from_pytest_name(name) in names
            ]
            batch_dummy_results.append(
                {
                    "kind": item.get("kind"),
                    "all_tests_rejected": not passing,
                    "passing_test_count": len(passing),
                    "passing_test_names": passing,
                }
            )
        batch_quality = {
            key: quality.get(key)
            for key in [
                "all_dummies_rejected",
                "all_tests_reject_all_dummies",
                "assertion_lint_passed",
                "assertion_lint_high_count",
                "assertion_lint_medium_count",
                "assertion_lint_low_count",
                "repeat_returncode",
                "source_leak_passed",
            ]
        }
        batch_quality["dummy_passing_test_names"] = [
            name
            for name in quality.get("dummy_passing_test_names") or []
            if case_name_from_pytest_name(name) in names
        ]
        batch_quality["dummy_passing_case_names"] = [
            case_name_from_pytest_name(name) for name in batch_quality["dummy_passing_test_names"]
        ]
        batch_quality["dummy_passing_test_count"] = len(batch_quality["dummy_passing_test_names"])
        batch_quality["dummy_results"] = batch_dummy_results
        previous_decisions = [
            item
            for item in previous.get("decisions") or []
            if isinstance(item, dict) and str(item.get("name") or "") in names
        ]
        batches.append(
            {
                **evidence,
                "cases": batch_cases,
                "reference_observations": batch_observations,
                "quality_gates": batch_quality,
                "previous_review": {"decisions": previous_decisions},
                "review_batch": {
                    "index": len(batches) + 1,
                    "start": start,
                    "size": len(batch_cases),
                    "total_cases": len(cases),
                },
            }
        )
    return batches


def run_batched_review(
    *,
    evidence: dict[str, Any],
    review_dir: Path,
    model: str,
    batch_size: int = 32,
) -> dict[str, Any]:
    all_names = [str(case.get("name") or "") for case in evidence.get("cases") or []]
    decisions: list[dict[str, Any]] = []
    reasons: list[str] = []
    batches = review_evidence_batches(evidence, batch_size=batch_size)
    for index, batch in enumerate(batches, start=1):
        batch_names = [str(case.get("name") or "") for case in batch.get("cases") or []]
        raw = run_agent_maestro_review(
            system=REVIEW_SYSTEM,
            prompt=build_review_prompt(batch),
            output_root=review_dir / "provider" / f"batch_{index:03d}",
            model=model,
        )
        validated = validate_review(raw, batch_names)
        write_json(review_dir / f"batch_{index:03d}_validated.json", validated)
        decisions.extend(validated["decisions"])
        if validated.get("suite_reason"):
            reasons.append(str(validated["suite_reason"]))
    merged = validate_review(
        {
            "suite_verdict": "keep",
            "suite_reason": " | ".join(reasons),
            "decisions": decisions,
        },
        all_names,
    )
    merged["batch_count"] = len(batches)
    merged["batch_size"] = batch_size
    return merged


def run_iteration_pipeline(
    *,
    instance_id: str,
    iteration: int,
    tasks_root: Path,
    candidate_cases: Path,
    run_root: Path,
    overwrite: bool,
) -> tuple[dict[str, Any], Path, Path]:
    label = f"agent_loop_iter_{iteration:02d}"
    generated_root = run_root / "artifacts" / "generated"
    coverage_root = run_root / "artifacts" / "coverage"
    pipeline_root = run_root / "iterations"
    cmd = [
        sys.executable,
        "tools/programbench_run_pb_style_go_oracle_pipeline.py",
        instance_id,
        "--tasks-root",
        str(tasks_root),
        "--suite-label",
        label,
        "--cases-json",
        str(candidate_cases),
        "--work-root",
        str(run_root / "work" / label),
        "--output-root",
        str(pipeline_root),
        "--generated-output-root",
        str(generated_root),
        "--coverage-output-root",
        str(coverage_root),
        "--determinism-reruns",
        "3",
        "--xdist",
        "1",
        "--pytest-timeout",
        "1200",
    ]
    if overwrite:
        cmd.append("--overwrite")
    run_step(
        f"iteration_{iteration:02d}_pipeline",
        cmd,
        run_root / "logs",
        timeout=7200,
    )
    summary_path = pipeline_root / instance_id / label / "pipeline_summary.json"
    if not summary_path.exists():
        raise RuntimeError(f"Pipeline did not write a summary: {summary_path}")
    quality_path = generated_root / instance_id / label / "evaluation_quality_report.json"
    return read_json(summary_path), summary_path, quality_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance_id")
    parser.add_argument("--tasks-root", type=Path, default=DEFAULT_TASKS_ROOT)
    parser.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE_ROOT)
    parser.add_argument("--example-instance-id")
    parser.add_argument("--generation-model", default="claude-sonnet-5[1m]")
    parser.add_argument("--review-model", default="claude-opus-4.8")
    parser.add_argument("--max-iterations", type=int, default=8)
    parser.add_argument("--max-turns", type=int, default=24)
    parser.add_argument("--agent-timeout", type=int, default=1800)
    parser.add_argument("--target-coverage", type=float, default=85.0)
    parser.add_argument("--plateau-delta", type=float, default=0.5)
    parser.add_argument("--plateau-patience", type=int, default=2)
    parser.add_argument("--max-cases", type=int, default=2000)
    parser.add_argument(
        "--initial-cases-json",
        type=Path,
        help="Optional prior candidate suite to normalize and resume from after preflight.",
    )
    parser.add_argument(
        "--evaluate-initial-first",
        action="store_true",
        help="Evaluate/review a resumed initial suite before the next generation call.",
    )
    parser.add_argument("--skip-review", action="store_true", help="Ablation only; final suite is marked unreviewed.")
    parser.add_argument("--seed-only", action="store_true", help="Ablation only; do not call a generation agent.")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    tasks_root = args.tasks_root.expanduser().resolve()
    task_dir = tasks_root / args.instance_id
    metadata = parse_simple_yaml(task_dir / "task.yaml")
    if metadata.get("language") != "go":
        raise ValueError(f"This loop currently supports Go tasks, got {metadata.get('language')!r}")
    run_root = args.workspace_root.expanduser().resolve() / "experiments" / "runs" / args.instance_id
    if run_root.exists() and args.overwrite:
        shutil.rmtree(run_root)
    if run_root.exists() and any(run_root.iterdir()) and not args.overwrite:
        raise FileExistsError(f"Run already exists: {run_root}; pass --overwrite or use a new workspace")
    logs_dir = run_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    example = choose_example(args.instance_id, args.example_instance_id)
    workspace = run_root / "agent_workspace"
    try:
        candidate_cases = seed_cases(
            instance_id=args.instance_id,
            tasks_root=tasks_root,
            workspace=workspace,
            max_cases=args.max_cases,
            logs_dir=logs_dir,
        )
        source = prepare_agent_context(
            instance_id=args.instance_id,
            example_instance_id=example,
            tasks_root=tasks_root,
            workspace=workspace,
            logs_dir=logs_dir,
        )
        materialize_reference(args.instance_id, source, logs_dir)
        shutil.copy2(candidate_cases, source / "candidate_cases.json")
        if args.initial_cases_json:
            initial_path = args.initial_cases_json.expanduser().resolve()
            initial_payload = read_json(initial_path)
            initial_cases = normalize_cases(initial_payload, max_cases=args.max_cases, iteration=0)
            if not initial_cases:
                raise ValueError(f"Initial candidate suite contains no valid cases: {initial_path}")
            resumed_suite = {
                "profile": "pb_agent_coverage_guided_go_v2",
                "instance_id": args.instance_id,
                "iteration": 0,
                "resumed_from": str(initial_path),
                "cases": initial_cases,
            }
            write_json(candidate_cases, resumed_suite)
            write_json(source / "candidate_cases.json", resumed_suite)
    except Exception as exc:
        error = {"error_type": type(exc).__name__, "error": str(exc)}
        status = "blocked_infrastructure" if is_infrastructure_error(error) else "preflight_failed"
        write_json(run_root / "blocked.json", {"status": status, **error})
        final = {
            "status": status,
            "accepted": False,
            "instance_id": args.instance_id,
            "repository": metadata.get("repository"),
            "commit": metadata.get("commit"),
            "generation_model": args.generation_model,
            "review_model": None if args.skip_review else args.review_model,
            "review_ablation": args.skip_review,
            "seed_ablation": args.seed_only,
            "example_instance_id": example,
            "history": [],
            "final_pipeline_summary": None,
            "final_case_count": 0,
            "error": error,
            "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        }
        write_json(run_root / "final" / "run_summary.json", final)
        print(json.dumps(final, indent=2, sort_keys=True))
        return 3

    history: list[dict[str, Any]] = []
    feedback: dict[str, Any] | None = None
    status = "iteration_budget_exhausted"
    final_summary_path: Path | None = None
    final_cases: list[dict[str, Any]] = []
    best_quality_summary_path: Path | None = None
    best_quality_cases: list[dict[str, Any]] = []
    best_quality_coverage = float("-inf")
    for iteration in range(1, args.max_iterations + 1):
        agent_dir = run_root / "iterations" / f"iteration_{iteration:02d}" / "generation_agent"
        evaluate_resumed = args.evaluate_initial_first and iteration == 1
        if args.seed_only or evaluate_resumed:
            cases_payload = read_json(source / "candidate_cases.json")
            agent_manifest = {
                "provider": "resumed-suite-evaluation" if evaluate_resumed else "deterministic-seed-ablation",
                "iteration": iteration,
            }
        else:
            prompt = build_generation_prompt(
                instance_id=args.instance_id,
                iteration=iteration,
                target_coverage=args.target_coverage,
                max_cases=args.max_cases,
                previous=feedback,
            )
            try:
                agent_run = run_claude_code(
                    prompt=prompt,
                    cwd=source,
                    output_root=agent_dir,
                    model=args.generation_model,
                    max_turns=args.max_turns,
                    timeout=args.agent_timeout,
                    json_schema=SUITE_UPDATE_SCHEMA,
                )
            except Exception as exc:
                status = "blocked_generation_provider"
                write_json(run_root / "blocked.json", {"status": status, "error_type": type(exc).__name__, "error": str(exc)})
                break
            agent_manifest = dict(agent_run.manifest)
            recovered_after_max_turns = is_recoverable_max_turn_run(agent_run)
            try:
                if recovered_after_max_turns:
                    cases_payload, cases = load_recovered_agent_suite(
                        source / "candidate_cases.json",
                        max_cases=args.max_cases,
                        iteration=iteration,
                    )
                    cases_payload["analysis_summary"] = (
                        "Recovered an on-disk candidate after the generation agent exhausted its tool-turn budget; "
                        "candidate remains untrusted until all pipeline and review gates pass."
                    )
                    agent_manifest["recovered_after_max_turns"] = True
                elif agent_run.returncode != 0 or not agent_run.structured_output:
                    status = "generation_agent_failed"
                    write_json(run_root / "blocked.json", {"status": status, "manifest": agent_run.manifest})
                    break
                else:
                    confirmation = agent_run.structured_output
                    cases_payload, cases = load_agent_updated_suite(
                        source / "candidate_cases.json",
                        confirmation,
                        max_cases=args.max_cases,
                        iteration=iteration,
                    )
                    cases_payload["analysis_summary"] = confirmation.get("analysis_summary")
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                status = "generation_agent_invalid_suite_file"
                write_json(
                    run_root / "blocked.json",
                    {
                        "status": status,
                        "iteration": iteration,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "manifest": agent_run.manifest,
                    },
                )
                break
        if args.seed_only or evaluate_resumed:
            cases = normalize_cases(cases_payload, max_cases=args.max_cases, iteration=iteration)
        if not cases:
            status = "generation_agent_returned_no_cases"
            break
        suite = {
            "profile": "pb_agent_coverage_guided_go_v2",
            "instance_id": args.instance_id,
            "iteration": iteration,
            "example_instance_id": example,
            "example_policy": "different-instance same-language one-shot",
            "analysis_summary": cases_payload.get("analysis_summary"),
            "agent_manifest": agent_manifest,
            "cases": cases,
        }
        write_json(candidate_cases, suite)
        write_json(source / "candidate_cases.json", suite)
        pipeline_summary, summary_path, quality_path = run_iteration_pipeline(
            instance_id=args.instance_id,
            iteration=iteration,
            tasks_root=tasks_root,
            candidate_cases=candidate_cases,
            run_root=run_root,
            overwrite=True,
        )
        coverage_path_text = pipeline_summary.get("coverage_summary_path")
        if not coverage_path_text:
            failed_text = json.dumps(pipeline_summary.get("failed_step") or {}, sort_keys=True).lower()
            status = "blocked_infrastructure" if is_infrastructure_error(failed_text) else "pipeline_failed"
            write_json(
                run_root / "blocked.json",
                {"status": status, "iteration": iteration, "pipeline_summary": str(summary_path), "details": pipeline_summary},
            )
            break
        coverage_summary = read_json(Path(coverage_path_text))
        gaps = coverage_gaps(coverage_summary)
        review: dict[str, Any]
        if args.skip_review:
            review = {
                "suite_verdict": "keep",
                "suite_reason": "review ablation",
                "counts": {"keep": len(cases), "revise": 0, "reject": 0},
                "keep": [case["name"] for case in cases],
                "revise": [],
                "reject": [],
                "decisions": [],
                "ablation": True,
            }
        else:
            quality_report = read_json(quality_path) if quality_path.exists() else {}
            evidence = {
                "instance_id": args.instance_id,
                "iteration": iteration,
                "cases": cases,
                "reference_observations": compact_reference_observations(quality_report),
                "quality_gates": pipeline_summary.get("quality") or {},
                "coverage": pipeline_summary.get("coverage") or {},
                "coverage_gaps": gaps,
                "previous_review": (feedback or {}).get("review") or {},
            }
            review_dir = run_root / "iterations" / f"iteration_{iteration:02d}" / "review_agent"
            write_json(review_dir / "evidence.json", evidence)
            try:
                review = run_batched_review(
                    evidence=evidence,
                    review_dir=review_dir,
                    model=args.review_model,
                )
                write_json(review_dir / "validated_review.json", review)
            except Exception as exc:
                status = "blocked_review_provider"
                write_json(
                    review_dir / "blocked.json",
                    {"status": status, "error_type": type(exc).__name__, "error": str(exc)},
                )
                break
        coverage = coverage_value(pipeline_summary)
        record = {
            "iteration": iteration,
            "case_count": len(cases),
            "coverage_percent": coverage,
            "coverage_gap_count": len(gaps),
            "quality_passed": quality_passed(pipeline_summary),
            "review_verdict": review.get("suite_verdict"),
            "review_counts": review.get("counts"),
            "pipeline_summary": str(summary_path),
        }
        history.append(record)
        write_json(run_root / "iteration_history.json", {"history": history})
        quality_feedback = dict(pipeline_summary.get("quality") or {})
        quality_feedback["dummy_passing_case_names"] = sorted(
            {
                case_name_from_pytest_name(name)
                for name in quality_feedback.get("dummy_passing_test_names") or []
            }
        )
        quality_feedback["binary_inconsistent_case_names"] = binary_inconsistent_case_names(coverage_summary)
        feedback = {
            "previous_iteration": record,
            "quality": quality_feedback,
            "coverage": pipeline_summary.get("coverage") or {},
            "coverage_gaps": gaps,
            "review": review,
            "instruction": (
                "Return the full corrected suite. Remove rejected cases; repair revised, dummy-passing, and "
                "binary-inconsistent cases; then target uncovered functions."
            ),
        }
        write_json(source / "agent_context" / "feedback.json", feedback)
        gates_ok = quality_passed(pipeline_summary)
        review_ok = review.get("suite_verdict") == "keep"
        if gates_ok and review_ok and coverage > best_quality_coverage:
            best_quality_coverage = coverage
            best_quality_summary_path = summary_path
            best_quality_cases = json.loads(json.dumps(cases))
        if gates_ok and review_ok and coverage >= args.target_coverage:
            status = "accepted_target_coverage"
            final_summary_path = summary_path
            final_cases = cases
            break
        if gates_ok and review_ok and plateaued(history, delta=args.plateau_delta, patience=args.plateau_patience):
            # Preserve the best deterministic suite, but do not call a run
            # successful when it remains below the PB official target.
            status = "incomplete_quality_plateau"
            final_summary_path = summary_path
            final_cases = cases
            break
        if args.seed_only:
            status = "seed_ablation_complete" if gates_ok and review_ok else "seed_ablation_complete_quality_failed"
            final_summary_path = summary_path
            final_cases = cases
            break

    if not final_cases and best_quality_cases:
        final_cases = best_quality_cases
        final_summary_path = best_quality_summary_path
        if status == "iteration_budget_exhausted":
            status = "iteration_budget_exhausted_best_quality"

    accepted = status.startswith("accepted_")
    final = {
        "status": status,
        "accepted": accepted,
        "instance_id": args.instance_id,
        "repository": metadata.get("repository"),
        "commit": metadata.get("commit"),
        "generation_model": args.generation_model,
        "review_model": None if args.skip_review else args.review_model,
        "review_ablation": args.skip_review,
        "seed_ablation": args.seed_only,
        "example_instance_id": example,
        "target_coverage": args.target_coverage,
        "history": history,
        "final_pipeline_summary": str(final_summary_path) if final_summary_path else None,
        "final_case_count": len(final_cases),
        "best_quality_coverage": None if best_quality_coverage == float("-inf") else best_quality_coverage,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    final_dir = run_root / "final"
    write_json(final_dir / "run_summary.json", final)
    if final_cases:
        write_json(final_dir / "candidate_cases.json", {"profile": "pb_agent_coverage_guided_go_v2", "cases": final_cases})
    print(json.dumps(final, indent=2, sort_keys=True))
    return 0 if accepted or status.startswith("seed_ablation_complete") else 3


if __name__ == "__main__":
    raise SystemExit(main())
