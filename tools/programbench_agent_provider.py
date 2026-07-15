#!/usr/bin/env python3
"""Provider adapters for ProgramBench oracle-generation and review agents.

Secrets are accepted only through the process environment.  Run manifests
record model and command policy, but never credential values.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import base64
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


DEFAULT_CLAUDE_TOOLS = "Read,Glob,Grep,Write,Edit,Bash"
DEFAULT_ALLOWED_TOOLS = ",".join(
    [
        "Read",
        "Glob",
        "Grep",
        "Write",
        "Edit",
        "Bash(git status *)",
        "Bash(git diff *)",
        "Bash(go list *)",
        "Bash(go test *)",
        "Bash(python3 agent_tools/probe_reference.py *)",
    ]
)
WINDOWS_BRIDGE_ALLOWED_TOOLS = ",".join(
    [
        "Read",
        "Glob",
        "Grep",
        "Write",
        "Edit",
        "Bash(git status *)",
        "Bash(git diff *)",
        "Bash(go list *)",
        "Bash(go test *)",
        "Bash(powershell.exe -NoProfile -ExecutionPolicy Bypass -File *probe_reference_windows.ps1 *)",
        "Bash(powershell.exe -NoProfile -ExecutionPolicy Bypass -File *probe_reference_windows.ps1)",
        "Bash(powershell.exe -NoProfile -ExecutionPolicy Bypass -File *validate_cases_windows.ps1 *)",
        "Bash(powershell.exe -NoProfile -ExecutionPolicy Bypass -File *validate_cases_windows.ps1)",
    ]
)


def windows_bridge_enabled() -> bool:
    return os.getenv("PROGRAMBENCH_WINDOWS_CLAUDE_BRIDGE", "").strip().lower() in {"1", "true", "yes"}


def to_windows_path(path: Path) -> str:
    if os.name == "nt":
        return str(path.resolve())
    proc = subprocess.run(["wslpath", "-w", str(path.resolve())], capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def resolve_windows_powershell() -> str:
    found = shutil.which("powershell.exe")
    if found:
        return found
    mounted = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    if mounted.is_file():
        return str(mounted)
    raise FileNotFoundError("Windows PowerShell was not found for the WSL bridge")


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def find_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from plain or fenced model output."""

    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            value = json.loads(stripped)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        value = json.loads(fenced.group(1))
        if isinstance(value, dict):
            return value
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("No JSON object found in agent output")


def resolve_claude(executable: str | None = None) -> str:
    if executable:
        return executable
    found = shutil.which("claude")
    if found:
        return found
    windows_native = Path.home() / ".local" / "bin" / "claude.exe"
    if windows_native.exists():
        return str(windows_native)
    raise FileNotFoundError("Claude Code was not found in PATH")


def configure_agent_maestro_claude_env(env: Mapping[str, str]) -> dict[str, str]:
    """Map Agent Maestro environment variables to Claude Code safely.

    The proxy secret remains only in the child-process environment.  Callers
    opt into this route by setting AGENT_MAESTRO_BASE_URL; official Claude
    authentication remains untouched when that variable is absent.
    """

    configured = dict(env)
    base = configured.get("AGENT_MAESTRO_BASE_URL", "").rstrip("/")
    if not base:
        return configured
    key = configured.get("AGENT_MAESTRO_API_KEY")
    configured["ANTHROPIC_BASE_URL"] = f"{base}/api/anthropic"
    if key:
        configured["ANTHROPIC_API_KEY"] = key
        configured["ANTHROPIC_AUTH_TOKEN"] = key
    configured.setdefault("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "936000")
    configured.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    configured.setdefault("CLAUDE_CODE_ATTRIBUTION_HEADER", "0")
    configured.pop("CLAUDE_CODE_USE_BEDROCK", None)
    return configured


def build_claude_command(
    *,
    executable: str,
    model: str,
    max_turns: int,
    tools: str = DEFAULT_CLAUDE_TOOLS,
    allowed_tools: str = DEFAULT_ALLOWED_TOOLS,
    json_schema: Mapping[str, Any] | None = None,
) -> list[str]:
    cmd = [
        executable,
        "-p",
        "--output-format",
        "json",
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
        "--max-turns",
        str(max_turns),
        "--model",
        model,
        "--tools",
        tools,
        "--allowedTools",
        allowed_tools,
    ]
    if json_schema is not None:
        cmd.extend(["--json-schema", json.dumps(json_schema, separators=(",", ":"))])
    return cmd


@dataclass(frozen=True)
class AgentRun:
    returncode: int
    timed_out: bool
    result_text: str
    structured_output: dict[str, Any] | None
    manifest: dict[str, Any]


def run_claude_code(
    *,
    prompt: str,
    cwd: Path,
    output_root: Path,
    model: str = "claude-sonnet-5[1m]",
    max_turns: int = 24,
    timeout: int = 1800,
    executable: str | None = None,
    tools: str = DEFAULT_CLAUDE_TOOLS,
    allowed_tools: str = DEFAULT_ALLOWED_TOOLS,
    json_schema: Mapping[str, Any] | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> AgentRun:
    """Run one isolated, non-persistent Claude Code job.

    The prompt is sent on stdin so source context is not embedded in process
    listings.  The manifest deliberately omits environment values.
    """

    bridge = windows_bridge_enabled()
    effective_allowed_tools = (
        WINDOWS_BRIDGE_ALLOWED_TOOLS if bridge and allowed_tools == DEFAULT_ALLOWED_TOOLS else allowed_tools
    )
    if bridge:
        bridge_script = Path(__file__).resolve().parents[1] / "setup" / "run_claude_code_via_agent_maestro_noninteractive.ps1"
        if not bridge_script.is_file():
            raise FileNotFoundError(f"Windows Claude bridge was not found: {bridge_script}")
        cmd = [
            resolve_windows_powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            to_windows_path(bridge_script),
            "-Model",
            model,
            "-MaxTurns",
            str(max_turns),
            "-Tools",
            tools,
            "-AllowedTools",
            effective_allowed_tools,
            "-JsonSchemaBase64",
            base64.b64encode(json.dumps(json_schema or {}, separators=(",", ":")).encode("utf-8")).decode("ascii"),
            "-WorkingDirectory",
            to_windows_path(cwd),
        ]
    else:
        claude = resolve_claude(executable)
        cmd = build_claude_command(
            executable=claude,
            model=model,
            max_turns=max_turns,
            tools=tools,
            allowed_tools=effective_allowed_tools,
            json_schema=json_schema,
        )
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    if not bridge:
        env = configure_agent_maestro_claude_env(env)
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        returncode = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", "replace")
        timed_out = True
    duration = time.time() - started
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "stdout.json").write_text(stdout, encoding="utf-8")
    (output_root / "stderr.txt").write_text(stderr, encoding="utf-8")

    envelope: dict[str, Any] = {}
    if stdout.strip():
        try:
            parsed = json.loads(stdout)
            if isinstance(parsed, dict):
                envelope = parsed
        except json.JSONDecodeError:
            pass
    result_text = str(envelope.get("result") or stdout)
    structured: dict[str, Any] | None = None
    candidate = envelope.get("structured_output")
    if isinstance(candidate, dict):
        structured = candidate
    elif result_text.strip():
        try:
            structured = find_json_object(result_text)
        except (ValueError, json.JSONDecodeError):
            structured = None

    manifest = {
        "provider": "windows-claude-code-agent-maestro" if bridge else "claude-code",
        "model": model,
        "cwd": str(cwd),
        "command": cmd,
        "prompt_sha256": __import__("hashlib").sha256(prompt.encode("utf-8")).hexdigest(),
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_seconds": round(duration, 3),
        "max_turns": max_turns,
        "tools": tools,
        "allowed_tools": effective_allowed_tools,
        "session_persistence": False,
        "permission_mode": "dontAsk",
        "credential_source": "windows-dpapi" if bridge else "environment",
        "agent_maestro_route": bridge or bool(env.get("AGENT_MAESTRO_BASE_URL")),
        "structured_output_present": structured is not None,
        "stderr_tail": stderr[-8000:],
    }
    write_json(output_root / "run_manifest.json", manifest)
    if structured is not None:
        write_json(output_root / "structured_output.json", structured)
    return AgentRun(returncode, timed_out, result_text, structured, manifest)


def _anthropic_text(payload: Mapping[str, Any]) -> str:
    parts = payload.get("content") or []
    return "\n".join(
        str(item.get("text") or "")
        for item in parts
        if isinstance(item, dict) and item.get("type") == "text"
    )


def run_agent_maestro_review(
    *,
    system: str,
    prompt: str,
    output_root: Path,
    model: str = "claude-opus-4.8",
    max_tokens: int = 8192,
    timeout: int = 600,
    retries: int = 2,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Run a text-only review call through Agent Maestro's Anthropic route."""

    bridge = windows_bridge_enabled()
    base = (base_url or os.getenv("AGENT_MAESTRO_BASE_URL") or "http://127.0.0.1:23333").rstrip("/")
    url = "http://127.0.0.1:23333/api/anthropic/v1/messages" if bridge else f"{base}/api/anthropic/v1/messages"
    key = os.getenv("AGENT_MAESTRO_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["x-api-key"] = key
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }
    output_root.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    response: dict[str, Any] | None = None
    started = time.time()
    for attempt in range(retries + 1):
        try:
            if bridge:
                bridge_script = Path(__file__).resolve().parents[1] / "setup" / "invoke_agent_maestro_anthropic.ps1"
                proc = subprocess.run(
                    [
                        resolve_windows_powershell(),
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        to_windows_path(bridge_script),
                    ],
                    input=json.dumps(body),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                if proc.returncode != 0:
                    raise ConnectionError(proc.stderr[-4000:] or f"Windows review bridge exited {proc.returncode}")
                value = json.loads(proc.stdout)
            else:
                request = urllib.request.Request(
                    url,
                    data=json.dumps(body).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=timeout) as raw:
                    value = json.loads(raw.read().decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("Agent Maestro returned a non-object response")
            response = value
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2**attempt)
                continue
            raise
    if response is None:
        raise RuntimeError(f"Agent review failed: {last_error}")
    text = _anthropic_text(response)
    parsed = find_json_object(text)
    manifest = {
        "provider": "agent-maestro-anthropic",
        "model": model,
        "url": url,
        "duration_seconds": round(time.time() - started, 3),
        "credential_source": "windows-dpapi" if bridge else ("environment" if key else "none"),
        "system_sha256": __import__("hashlib").sha256(system.encode("utf-8")).hexdigest(),
        "prompt_sha256": __import__("hashlib").sha256(prompt.encode("utf-8")).hexdigest(),
    }
    (output_root / "response_text.txt").write_text(text, encoding="utf-8")
    write_json(output_root / "review.json", parsed)
    write_json(output_root / "run_manifest.json", manifest)
    return parsed
