#!/usr/bin/env python3
"""Deterministic process-lifecycle runner for ProgramBench behavioral cases.

The module is deliberately repository- and language-neutral.  It supports the
common shape that a one-shot ``subprocess.communicate`` runner cannot express:
start a target, wait until it is ready, mutate its environment, interact with
it, and then shut it down deterministically.
"""

from __future__ import annotations

import base64
import concurrent.futures
import contextlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


MAX_EVENTS = 64
MAX_EVENT_BYTES = 1_000_000
MAX_SEQUENCE_STEPS = 32


def _safe_path(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    resolved = root.resolve()
    if target != resolved and resolved not in target.parents:
        raise ValueError(f"lifecycle path escaped workspace: {relative}")
    return target


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _replace(value: str, replacements: dict[str, str]) -> str:
    for key, replacement in replacements.items():
        value = value.replace(key, replacement)
    return value


def _bounded_bytes(value: Any, *, base64_encoded: bool = False) -> bytes:
    data = (
        base64.b64decode(str(value), validate=True)
        if base64_encoded
        else str(value or "").encode("utf-8")
    )
    if len(data) > MAX_EVENT_BYTES:
        raise ValueError("lifecycle event payload exceeds 1 MB")
    return data


def _signal_number(value: str) -> int:
    name = value.upper()
    if not name.startswith("SIG"):
        name = "SIG" + name
    number = getattr(signal, name, None)
    if not isinstance(number, int):
        raise ValueError(f"unsupported lifecycle signal: {value}")
    return number


def _terminate(proc: subprocess.Popen[bytes], sig: int, grace: float) -> None:
    if proc.poll() is not None:
        return
    if os.name != "nt":
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, sig)
    else:
        proc.send_signal(sig)
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        proc.wait(timeout=5)


def _collector(stream: Any, output: bytearray, limit: int) -> None:
    while True:
        chunk = stream.read(65536)
        if not chunk:
            return
        output.extend(chunk)
        if len(output) > limit:
            return


def _wait_ready(
    proc: subprocess.Popen[bytes],
    readiness: dict[str, Any],
    stdout: bytearray,
    stderr: bytearray,
    replacements: dict[str, str],
    deadline: float,
) -> dict[str, Any]:
    kind = str(readiness.get("kind") or "sleep")
    if kind == "sleep":
        duration = max(0.0, min(10.0, float(readiness.get("seconds", 0.1))))
        time.sleep(duration)
        return {"kind": kind, "ready": proc.poll() is None}
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return {"kind": kind, "ready": False, "reason": "process_exited"}
        if kind == "tcp":
            host = _replace(str(readiness.get("host") or "127.0.0.1"), replacements)
            port = int(_replace(str(readiness.get("port") or "{target_port}"), replacements))
            try:
                with socket.create_connection((host, port), timeout=0.1):
                    return {"kind": kind, "ready": True}
            except OSError:
                pass
        elif kind in {"stdout", "stderr"}:
            haystack = bytes(stdout if kind == "stdout" else stderr)
            needle = _replace(str(readiness.get("contains") or ""), replacements).encode()
            pattern = readiness.get("regex")
            if (needle and needle in haystack) or (
                pattern and re.search(_replace(str(pattern), replacements).encode(), haystack)
            ):
                return {"kind": kind, "ready": True}
        else:
            raise ValueError(f"unsupported lifecycle readiness kind: {kind}")
        time.sleep(0.02)
    return {"kind": kind, "ready": False, "reason": "readiness_timeout"}


def _network_event(
    event: dict[str, Any], *, host: str, port: int,
    replacements: dict[str, str], deadline: float,
) -> dict[str, Any]:
    action = str(event.get("action") or "")
    result: dict[str, Any] = {"action": action, "ok": True}
    if action == "http_request":
        url = _replace(str(event.get("url") or "{target_url}/"), replacements)
        data = None if "body" not in event else _bounded_bytes(event.get("body"))
        request = urllib.request.Request(
            url, data=data, method=str(event.get("method") or "GET"),
            headers={str(k): str(v) for k, v in dict(event.get("headers") or {}).items()},
        )
        try:
            with urllib.request.urlopen(request, timeout=min(5.0, max(0.1, deadline - time.monotonic()))) as response:
                body = response.read(MAX_EVENT_BYTES + 1)
                result.update({"status": response.status, "body_base64": base64.b64encode(body).decode()})
        except urllib.error.HTTPError as exc:
            body = exc.read(MAX_EVENT_BYTES + 1)
            result.update({"status": exc.code, "body_base64": base64.b64encode(body).decode()})
        if len(body) > MAX_EVENT_BYTES:
            raise ValueError("lifecycle HTTP response exceeds 1 MB")
    elif action == "tcp_send":
        data = _bounded_bytes(event.get("data"), base64_encoded=bool(event.get("base64")))
        with socket.create_connection((host, port), timeout=2) as client:
            client.sendall(data)
            if event.get("shutdown_write", True):
                client.shutdown(socket.SHUT_WR)
            reply = client.recv(min(MAX_EVENT_BYTES, int(event.get("max_read_bytes", 65536))))
        result["reply_base64"] = base64.b64encode(reply).decode()
    else:
        raise ValueError(f"parallel lifecycle event does not support: {action}")
    return result


def run_sequence_case(
    *,
    executable: Path,
    argv0: str,
    sequence: list[dict[str, Any]],
    cwd: Path,
    env: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    """Run bounded target invocations against one persistent fixture state."""
    if not sequence or len(sequence) > MAX_SEQUENCE_STEPS:
        raise ValueError(f"sequence requires one to {MAX_SEQUENCE_STEPS} steps")
    started = time.monotonic()
    interactions: list[dict[str, Any]] = []
    last_returncode = 0
    last_stdout = b""
    last_stderr = b""
    timed_out = False
    for index, raw_step in enumerate(sequence):
        if not isinstance(raw_step, dict):
            raise ValueError("sequence steps must be objects")
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            timed_out = True
            break
        args = raw_step.get("args") or []
        if not isinstance(args, list) or not all(isinstance(value, str) for value in args):
            raise ValueError("sequence step args must be a string list")
        step_env = dict(env)
        step_env.update({str(k): str(v) for k, v in dict(raw_step.get("env") or {}).items()})
        step_stdin = str(raw_step.get("stdin") or "").encode("utf-8")
        step_argv0 = str(raw_step.get("argv0") or argv0)
        step_timeout = min(
            remaining,
            max(0.1, min(60.0, float(raw_step.get("timeout_seconds") or remaining))),
        )
        try:
            proc = subprocess.run(
                [step_argv0, *args],
                executable=str(executable),
                input=step_stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=step_env,
                timeout=step_timeout,
                start_new_session=os.name != "nt",
            )
            last_returncode, last_stdout, last_stderr = proc.returncode, proc.stdout, proc.stderr
            step_timed_out = False
        except subprocess.TimeoutExpired as exc:
            last_returncode = 124
            last_stdout = exc.stdout if isinstance(exc.stdout, bytes) else b""
            last_stderr = exc.stderr if isinstance(exc.stderr, bytes) else b""
            step_timed_out = timed_out = True
        if len(last_stdout) > MAX_EVENT_BYTES or len(last_stderr) > MAX_EVENT_BYTES:
            raise ValueError("sequence step output exceeds 1 MB")
        interactions.append({
            "index": index,
            "action": "run_target",
            "args": list(args),
            "returncode": last_returncode,
            "stdout_base64": base64.b64encode(last_stdout).decode(),
            "stderr_base64": base64.b64encode(last_stderr).decode(),
            "timed_out": step_timed_out,
        })
        if step_timed_out:
            break
    return {
        "returncode": last_returncode,
        "stdout": last_stdout,
        "stderr": last_stderr,
        "timed_out": timed_out,
        "interactions": interactions,
    }


def run_lifecycle_case(
    *,
    executable: Path,
    argv0: str,
    args: list[str],
    stdin: bytes,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    lifecycle: dict[str, Any],
) -> dict[str, Any]:
    """Run one bounded lifecycle case and return stable behavioral evidence."""

    events = list(lifecycle.get("events") or [])
    if len(events) > MAX_EVENTS:
        raise ValueError(f"lifecycle has more than {MAX_EVENTS} events")
    host = "127.0.0.1"
    port = int(lifecycle.get("target_port") or _free_port())
    replacements = {
        "{target_host}": host,
        "{target_port}": str(port),
        "{target_url}": f"http://{host}:{port}",
    }
    run_env = {key: _replace(str(value), replacements) for key, value in env.items()}
    run_argv0 = _replace(argv0, replacements)
    run_args = [_replace(value, replacements) for value in args]
    run_stdin = _replace(stdin.decode("utf-8", errors="surrogateescape"), replacements).encode(
        "utf-8", errors="surrogateescape"
    )
    proc = subprocess.Popen(
        [run_argv0, *run_args], executable=str(executable), cwd=cwd, env=run_env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=os.name != "nt",
    )
    stdout = bytearray()
    stderr = bytearray()
    output_limit = max(1024, min(16 * 1024 * 1024, int(lifecycle.get("max_output_bytes", 4 * 1024 * 1024))))
    threads = [
        threading.Thread(target=_collector, args=(proc.stdout, stdout, output_limit), daemon=True),
        threading.Thread(target=_collector, args=(proc.stderr, stderr, output_limit), daemon=True),
    ]
    for thread in threads:
        thread.start()
    def write_stdin(data: bytes) -> bool:
        if not proc.stdin or proc.stdin.closed:
            return False
        try:
            proc.stdin.write(data)
            proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            # A valid target may exit before consuming optional input.  The
            # exit status/output remain the behavioral oracle; an early-closed
            # pipe must not abort capture of the entire suite.
            return False

    if run_stdin:
        write_stdin(run_stdin)
    deadline = time.monotonic() + timeout
    interactions: list[dict[str, Any]] = []
    ready = _wait_ready(
        proc, dict(lifecycle.get("readiness") or {"kind": "sleep", "seconds": 0.1}),
        stdout, stderr, replacements, deadline,
    )
    interactions.append({"phase": "readiness", **ready})
    timed_out = not ready.get("ready") and ready.get("reason") == "readiness_timeout"
    try:
        if ready.get("ready"):
            for index, raw in enumerate(events):
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                event = dict(raw)
                delay = max(0.0, min(10.0, float(event.get("after_seconds", 0))))
                if delay:
                    time.sleep(delay)
                action = str(event.get("action") or "")
                result: dict[str, Any] = {"index": index, "action": action, "ok": True}
                if action == "write_file":
                    target = _safe_path(cwd, _replace(str(event["path"]), replacements))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    data = _bounded_bytes(event.get("data"), base64_encoded=bool(event.get("base64")))
                    with target.open("ab" if event.get("append") else "wb") as handle:
                        handle.write(data)
                    result["bytes"] = len(data)
                elif action == "remove_path":
                    target = _safe_path(cwd, _replace(str(event["path"]), replacements))
                    if target.is_dir() and not target.is_symlink():
                        shutil.rmtree(target)
                    elif target.exists() or target.is_symlink():
                        target.unlink()
                    result["existed"] = not target.exists()
                elif action == "stdin":
                    data = _bounded_bytes(event.get("data"), base64_encoded=bool(event.get("base64")))
                    written = write_stdin(data)
                    result["bytes"] = len(data) if written else 0
                    result["ok"] = written
                    if not written:
                        result["reason"] = "stdin_closed"
                elif action == "close_stdin":
                    if proc.stdin and not proc.stdin.closed:
                        with contextlib.suppress(BrokenPipeError, OSError, ValueError):
                            proc.stdin.close()
                elif action == "signal":
                    sig = _signal_number(str(event.get("signal") or "TERM"))
                    if os.name != "nt":
                        os.killpg(proc.pid, sig)
                    else:
                        proc.send_signal(sig)
                    result["signal"] = signal.Signals(sig).name
                elif action in {"http_request", "tcp_send"}:
                    result.update(_network_event(
                        event, host=host, port=port, replacements=replacements, deadline=deadline
                    ))
                    result["index"] = index
                elif action == "parallel":
                    children = list(event.get("events") or [])
                    if not children or len(children) > 8 or any(not isinstance(child, dict) for child in children):
                        raise ValueError("parallel lifecycle event requires one to eight child events")
                    with concurrent.futures.ThreadPoolExecutor(max_workers=len(children)) as pool:
                        futures = [
                            pool.submit(
                                _network_event, child, host=host, port=port,
                                replacements=replacements, deadline=deadline,
                            )
                            for child in children
                        ]
                        result["results"] = [future.result() for future in futures]
                elif action == "sleep":
                    pass
                else:
                    raise ValueError(f"unsupported lifecycle action: {action}")
                interactions.append(result)
        if lifecycle.get("close_stdin_after_events") and proc.stdin and not proc.stdin.closed:
            with contextlib.suppress(BrokenPipeError, OSError, ValueError):
                proc.stdin.close()
        remaining = max(0.0, deadline - time.monotonic())
        if lifecycle.get("expect_exit", True):
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                timed_out = True
        if proc.poll() is None:
            shutdown = dict(lifecycle.get("shutdown") or {})
            _terminate(
                proc,
                _signal_number(str(shutdown.get("signal") or "TERM")),
                max(0.1, min(10.0, float(shutdown.get("grace_seconds", 1.0)))),
            )
    finally:
        if proc.poll() is None:
            _terminate(proc, signal.SIGKILL, 0.1)
        if proc.stdin and not proc.stdin.closed:
            with contextlib.suppress(BrokenPipeError, OSError, ValueError):
                proc.stdin.close()
        for thread in threads:
            thread.join(timeout=2)
        for stream in (proc.stdout, proc.stderr):
            if stream:
                stream.close()
    stdout_bytes = bytes(stdout)
    stderr_bytes = bytes(stderr)
    for token, value in replacements.items():
        stdout_bytes = stdout_bytes.replace(value.encode(), token.encode())
        stderr_bytes = stderr_bytes.replace(value.encode(), token.encode())
    return {
        "returncode": int(proc.returncode if proc.returncode is not None else 124),
        "stdout": stdout_bytes,
        "stderr": stderr_bytes,
        "timed_out": timed_out,
        "interactions": json.loads(json.dumps(interactions, sort_keys=True)),
    }
