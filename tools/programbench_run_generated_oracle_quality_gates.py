#!/usr/bin/env python3
"""Run reusable quality gates for generated executable oracle bundles."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from programbench_assertion_linter import lint_oracle_root


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_LIKE_NAMES = {"go.mod", "go.sum", "Makefile", "Dockerfile"}
SOURCE_LIKE_SUFFIXES = {".go", ".c", ".h", ".rs", ".cc", ".cpp", ".hpp"}
SOURCE_IDENTIFYING_PATTERNS = (
    "package main",
    "func Run",
    "func Parse",
    "type Config",
    "github.com/sclevine/yj/v5",
    "gopkg.in/yaml.v3",
    "BurntSushi/toml",
    "hashicorp/hcl",
)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def short_text(text: str, limit: int = 5000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def run_command(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 300,
    log_path: Path | None = None,
) -> dict[str, Any]:
    started = dt.datetime.now(dt.UTC)
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        returncode = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", errors="replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", errors="replace")
        timed_out = True
    ended = dt.datetime.now(dt.UTC)
    payload = {
        "cmd": cmd,
        "cwd": str(cwd) if cwd else None,
        "returncode": returncode,
        "timed_out": timed_out,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_seconds": (ended - started).total_seconds(),
        "stdout_tail": short_text(stdout),
        "stderr_tail": short_text(stderr),
    }
    if log_path is not None:
        write_json(log_path, {**payload, "stdout": stdout, "stderr": stderr})
        payload["log_path"] = str(log_path)
    return payload


def resolve_oracle_root(path: Path) -> Path:
    candidate = path.expanduser().resolve()
    if (candidate / "eval" / "tests").exists():
        return candidate
    nested = candidate / "oracle_tests"
    if (nested / "eval" / "tests").exists():
        return nested
    raise FileNotFoundError(f"Could not find eval/tests under {candidate}")


def parse_junit(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8", errors="replace"))
    except ET.ParseError as exc:
        return {"exists": True, "parse_error": str(exc)}
    tests = failures = errors = skipped = 0
    for suite in root.iter("testsuite"):
        tests += int(float(suite.attrib.get("tests", "0") or 0))
        failures += int(float(suite.attrib.get("failures", "0") or 0))
        errors += int(float(suite.attrib.get("errors", "0") or 0))
        skipped += int(float(suite.attrib.get("skipped", "0") or 0))
    if tests == 0 and root.tag == "testsuite":
        tests = int(float(root.attrib.get("tests", "0") or 0))
        failures = int(float(root.attrib.get("failures", "0") or 0))
        errors = int(float(root.attrib.get("errors", "0") or 0))
        skipped = int(float(root.attrib.get("skipped", "0") or 0))
    return {"exists": True, "tests": tests, "failures": failures, "errors": errors, "skipped": skipped}


def ensure_pytest(python: Path) -> bool:
    result = run_command([str(python), "-c", "import pytest"], timeout=60)
    return result["returncode"] == 0


def install_pytest_venv(venv_dir: Path, logs_dir: Path) -> Path:
    venv = run_command(["python3", "-m", "venv", str(venv_dir)], timeout=300, log_path=logs_dir / "venv.json")
    if venv["returncode"] != 0:
        raise RuntimeError(f"venv creation failed: {venv['stderr_tail']}")
    python = venv_dir / "bin" / "python"
    pip = run_command(
        [str(python), "-m", "pip", "install", "-q", "pytest", "pytest-timeout"],
        timeout=900,
        log_path=logs_dir / "pip_install_pytest.json",
    )
    if pip["returncode"] != 0:
        raise RuntimeError(f"pytest dependency install failed: {pip['stderr_tail']}")
    return python


def materialize_dummy(kind: str, dest: Path) -> None:
    scripts = {
        "true": "exit 0\n",
        "false": "exit 1\n",
        "cat-stdin": "cat\n",
        "empty-stderr": "cat >/dev/null\nprintf 'dummy stderr\\n' >&2\nexit 0\n",
    }
    try:
        body = scripts[kind]
    except KeyError as exc:
        raise ValueError(f"Unsupported dummy kind: {kind}") from exc
    dest.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    dest.chmod(0o755)


def run_pytest_bundle(
    *,
    python: Path,
    oracle_root: Path,
    executable: Path,
    workspace: Path,
    junit_name: str,
    logs_dir: Path,
    timeout: int,
    gocoverdir: Path | None = None,
) -> dict[str, Any]:
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    shutil.copytree(oracle_root / "eval", workspace / "eval")
    shutil.copy2(executable, workspace / "executable")
    (workspace / "executable").chmod(0o755)
    junit = workspace / junit_name
    env = os.environ.copy()
    env["TZ"] = "UTC"
    if gocoverdir is not None:
        gocoverdir.mkdir(parents=True, exist_ok=True)
        env["GOCOVERDIR"] = str(gocoverdir)
    log_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{workspace.parent.name}_{junit_name}").strip("_.-")
    result = run_command(
        [str(python), "-m", "pytest", "-q", "eval/tests", f"--junitxml={junit}"],
        cwd=workspace,
        env=env,
        timeout=timeout,
        log_path=logs_dir / f"{log_label}.pytest.json",
    )
    return {
        "workspace": str(workspace),
        "junit": str(junit),
        "pytest_returncode": result["returncode"],
        "timed_out": result["timed_out"],
        "junit_summary": parse_junit(junit),
        "log_path": result.get("log_path"),
    }


def run_dummy_gates(
    *,
    python: Path,
    oracle_root: Path,
    work_root: Path,
    logs_dir: Path,
    timeout: int,
    dummy_kinds: list[str],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for kind in dummy_kinds:
        dummy_dir = work_root / f"dummy_{kind}"
        dummy_dir.mkdir(parents=True, exist_ok=True)
        dummy_executable = dummy_dir / "dummy_executable"
        materialize_dummy(kind, dummy_executable)
        run = run_pytest_bundle(
            python=python,
            oracle_root=oracle_root,
            executable=dummy_executable,
            workspace=dummy_dir / "workspace",
            junit_name=f"{kind}.results.xml",
            logs_dir=logs_dir,
            timeout=timeout,
        )
        summary = run["junit_summary"]
        results.append(
            {
                "kind": kind,
                **run,
                "rejected": run["pytest_returncode"] != 0 or summary.get("failures", 0) > 0 or summary.get("errors", 0) > 0,
            }
        )
    return results


def source_leak_scan(oracle_root: Path) -> dict[str, Any]:
    source_like: list[str] = []
    matches: list[dict[str, Any]] = []
    regex = re.compile("|".join(re.escape(item) for item in SOURCE_IDENTIFYING_PATTERNS))
    total_files = 0
    for path in oracle_root.rglob("*"):
        if not path.is_file():
            continue
        total_files += 1
        if path.name in SOURCE_LIKE_NAMES or path.suffix in SOURCE_LIKE_SUFFIXES:
            source_like.append(str(path.relative_to(oracle_root)))
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                matches.append({"path": str(path.relative_to(oracle_root)), "line": lineno})
                break
    return {
        "passed": not source_like and not matches,
        "total_files_scanned": total_files,
        "source_like_file_matches": len(source_like),
        "source_identifying_string_matches": len(matches),
        "source_like_files": source_like[:50],
        "source_identifying_matches": matches[:50],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle-material-root", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, default=Path("/tmp/programbench_generated_oracle_quality_gates"))
    parser.add_argument("--python", type=Path, help="Python executable with pytest installed. Defaults to an auto-created venv.")
    parser.add_argument("--dummy-kind", action="append", default=["true", "cat-stdin", "false", "empty-stderr"])
    parser.add_argument("--repeat-executable", type=Path)
    parser.add_argument("--repeat-gocoverdir", type=Path)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--skip-assertion-lint", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    oracle_root = resolve_oracle_root(args.oracle_material_root)
    output_json = args.output_json if args.output_json.is_absolute() else (REPO_ROOT / args.output_json).resolve()
    work_root = args.work_root.expanduser().resolve()
    if work_root.exists():
        if not args.overwrite:
            raise FileExistsError(f"Work root exists: {work_root}")
        shutil.rmtree(work_root)
    logs_dir = work_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    if args.python is None:
        python = install_pytest_venv(work_root / ".venv", logs_dir)
    else:
        python = args.python
        if not ensure_pytest(python):
            raise RuntimeError(f"pytest is not importable by {python}; omit --python to auto-create a venv")

    dummy_results = run_dummy_gates(
        python=python,
        oracle_root=oracle_root,
        work_root=work_root,
        logs_dir=logs_dir,
        timeout=args.timeout,
        dummy_kinds=args.dummy_kind,
    )
    leak = source_leak_scan(oracle_root)
    assertion_lint = None if args.skip_assertion_lint else lint_oracle_root(oracle_root)
    repeat = None
    if args.repeat_executable is not None:
        repeat = run_pytest_bundle(
            python=python,
            oracle_root=oracle_root,
            executable=args.repeat_executable.expanduser().resolve(),
            workspace=work_root / "repeat_workspace",
            junit_name="repeat.results.xml",
            logs_dir=logs_dir,
            timeout=args.timeout,
            gocoverdir=args.repeat_gocoverdir,
        )

    payload = {
        "oracle_material_root": str(oracle_root),
        "python": str(python),
        "dummy_reject": dummy_results,
        "all_dummies_rejected": all(item["rejected"] for item in dummy_results),
        "source_leak_scan": leak,
        "assertion_lint": assertion_lint,
        "repeat_check": repeat,
    }
    write_json(output_json, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))

    repeat_ok = repeat is None or (
        repeat["pytest_returncode"] == 0
        and repeat["junit_summary"].get("failures") == 0
        and repeat["junit_summary"].get("errors") == 0
    )
    lint_ok = assertion_lint is None or assertion_lint.get("passed", False)
    return 0 if payload["all_dummies_rejected"] and leak["passed"] and lint_ok and repeat_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
