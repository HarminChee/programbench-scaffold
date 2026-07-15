#!/usr/bin/env python3
"""Constrained black-box probe helper copied into an agent workspace."""

from __future__ import annotations

import argparse
import contextlib
import http.server
import json
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Iterator


MAX_INPUT_BYTES = 256_000
MAX_OUTPUT_BYTES = 64_000


def clipped(data: bytes) -> dict[str, object]:
    was_clipped = len(data) > MAX_OUTPUT_BYTES
    text = data[:MAX_OUTPUT_BYTES].decode("utf-8", "replace")
    return {"text": text, "bytes": len(data), "clipped": was_clipped}


def safe_env(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("--env-json must be an object")
    blocked = {"PATH", "HOME", "PWD", "OLDPWD", "SHELL", "USER", "LOGNAME", "TMPDIR"}
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key, text = str(raw_key), str(raw_value)
        upper = key.upper()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key):
            raise ValueError(f"invalid environment key: {key!r}")
        if upper in blocked or upper.startswith(("LD_", "DYLD_")) or any(
            marker in upper for marker in ("SECRET", "TOKEN", "PASSWORD", "CREDENTIAL", "API_KEY")
        ):
            raise ValueError(f"blocked environment key: {key}")
        if "\x00" in text or len(text) > 4096:
            raise ValueError(f"invalid environment value for {key}")
        result[key] = text
    if len(result) > 32:
        raise ValueError("environment variable budget exceeded")
    return result


def safe_files(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("--files-json must be an object")
    result: dict[str, str] = {}
    total = 0
    for raw_path, raw_content in value.items():
        path = PurePosixPath(str(raw_path))
        text = str(raw_content)
        size = len(text.encode("utf-8"))
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError(f"unsafe fixture path: {path}")
        if size > MAX_INPUT_BYTES or total + size > 1_000_000:
            raise ValueError("fixture file budget exceeded")
        result[str(path)] = text
        total += size
    if len(result) > 16:
        raise ValueError("fixture file count exceeded")
    return result


def safe_http(value: Any) -> dict[str, Any] | None:
    if value in ({}, None):
        return None
    if not isinstance(value, dict):
        raise ValueError("--http-json must be an object")
    path = str(value.get("path") or "/")
    status = int(value.get("status", 200))
    body = str(value.get("body") or "")
    if not path.startswith("/") or any(char in path for char in "\r\n"):
        raise ValueError("invalid HTTP fixture path")
    if not 100 <= status <= 599 or len(body.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("invalid HTTP fixture response")
    headers = {str(key): str(item) for key, item in dict(value.get("headers") or {}).items()}
    if len(headers) > 16 or any(
        not re.fullmatch(r"[A-Za-z0-9-]{1,64}", key) or any(char in item for char in "\r\n")
        for key, item in headers.items()
    ):
        raise ValueError("invalid HTTP fixture headers")
    return {"path": path, "status": status, "headers": headers, "body": body}


@contextlib.contextmanager
def fixture_runtime(files: dict[str, str], http_fixture: dict[str, Any] | None) -> Iterator[tuple[Path, str]]:
    with tempfile.TemporaryDirectory(prefix="programbench-probe-") as temp:
        cwd = Path(temp)
        for relative, content in files.items():
            target = cwd / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        server: http.server.ThreadingHTTPServer | None = None
        thread: threading.Thread | None = None
        url = ""
        if http_fixture:
            response = http_fixture

            class Handler(http.server.BaseHTTPRequestHandler):
                def do_GET(self) -> None:  # noqa: N802
                    if self.path != response["path"]:
                        self.send_error(404)
                        return
                    body = response["body"].encode("utf-8")
                    self.send_response(response["status"])
                    for key, value in response["headers"].items():
                        self.send_header(key, value)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, format: str, *args: object) -> None:
                    return

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}{response['path']}"
        try:
            yield cwd, url
        finally:
            if server:
                server.shutdown()
                server.server_close()
            if thread:
                thread.join(timeout=2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--args-json", default="[]")
    parser.add_argument("--stdin-text", default="")
    parser.add_argument("--stdin-file", type=Path)
    parser.add_argument("--env-json", default="{}")
    parser.add_argument("--files-json", default="{}")
    parser.add_argument("--http-json", default="{}")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    executable = (Path(__file__).resolve().parents[1] / "reference" / "executable").resolve()
    if not executable.is_file():
        raise FileNotFoundError(f"Reference executable is missing: {executable}")
    argv = json.loads(args.args_json)
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise ValueError("--args-json must be a JSON string list")
    if len(argv) > 100 or sum(len(item) for item in argv) > 32_000:
        raise ValueError("argument budget exceeded")
    if args.stdin_file:
        stdin_path = args.stdin_file.resolve()
        workspace = Path(__file__).resolve().parents[1]
        if workspace not in stdin_path.parents:
            raise ValueError("stdin file must be inside the agent workspace")
        stdin = stdin_path.read_bytes()
    else:
        stdin = args.stdin_text.encode("utf-8")
    if len(stdin) > MAX_INPUT_BYTES:
        raise ValueError("stdin budget exceeded")
    case_env = safe_env(json.loads(args.env_json))
    files = safe_files(json.loads(args.files_json))
    http_fixture = safe_http(json.loads(args.http_json))
    env = {
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "TZ": "UTC",
    }
    with fixture_runtime(files, http_fixture) as (cwd, http_url):
        expanded_argv = [item.replace("{http_url}", http_url) for item in argv]
        expanded_stdin = stdin.replace(b"{http_url}", http_url.encode("utf-8"))
        env.update({key: value.replace("{http_url}", http_url) for key, value in case_env.items()})
        try:
            proc = subprocess.run(
                [str(executable), *expanded_argv],
                input=expanded_stdin,
                capture_output=True,
                timeout=max(0.1, min(args.timeout, 30.0)),
                cwd=cwd,
                env=env,
            )
            result = {
                "args": argv,
                "returncode": proc.returncode,
                "stdout": clipped(proc.stdout),
                "stderr": clipped(proc.stderr),
                "timed_out": False,
            }
        except subprocess.TimeoutExpired as exc:
            result = {
                "args": argv,
                "returncode": None,
                "stdout": clipped(exc.stdout or b""),
                "stderr": clipped(exc.stderr or b""),
                "timed_out": True,
            }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
