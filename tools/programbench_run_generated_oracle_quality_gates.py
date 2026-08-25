#!/usr/bin/env python3
"""Run reusable quality gates for generated executable oracle bundles."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from programbench_assertion_linter import lint_oracle_root
from programbench_go_coverage_harness import (
    run_whole_harness_in_isolated_container,
    sha256_file,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_LIKE_NAMES = {"go.mod", "go.sum", "Makefile", "Dockerfile"}
SOURCE_LIKE_SUFFIXES = {".go", ".c", ".h", ".rs", ".cc", ".cpp", ".hpp"}
SOURCE_IDENTIFYING_PATTERNS = (
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
    started = dt.datetime.now(dt.timezone.utc)
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
    ended = dt.datetime.now(dt.timezone.utc)
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
    passed_names: list[str] = []
    failed_names: list[str] = []
    error_names: list[str] = []
    skipped_names: list[str] = []
    for case in root.iter("testcase"):
        classname = case.attrib.get("classname", "")
        name = case.attrib.get("name", "")
        qualified = f"{classname}.{name}" if classname else name
        if case.find("failure") is not None:
            failed_names.append(qualified)
        elif case.find("error") is not None:
            error_names.append(qualified)
        elif case.find("skipped") is not None:
            skipped_names.append(qualified)
        else:
            passed_names.append(qualified)
    return {
        "exists": True,
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "passed_test_names": passed_names,
        "failed_test_names": failed_names,
        "error_test_names": error_names,
        "skipped_test_names": skipped_names,
    }


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


@contextlib.contextmanager
def fixed_workspace_executable(executable: Path):
    """Provide the PB `/workspace/executable` alias without cross-run races."""

    if os.name == "nt":
        yield
        return
    import fcntl

    lock = Path("/tmp/programbench-fixed-workspace.lock").open("a+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    alias = Path("/workspace/executable")
    try:
        alias.parent.mkdir(parents=True, exist_ok=True)
        if alias.is_dir() and not alias.is_symlink():
            raise IsADirectoryError(alias)
        if alias.exists() or alias.is_symlink():
            alias.unlink()
        shutil.copy2(executable, alias)
        alias.chmod(alias.stat().st_mode | 0o555)
        yield
    finally:
        if (alias.exists() or alias.is_symlink()) and not alias.is_dir():
            alias.unlink()
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def rewrite_fixed_workspace_alias(root: Path, executable: Path) -> int:
    """Give one dummy gate a private executable path.

    Generated PB tests and fixture scripts may hard-code
    ``/workspace/executable``.  Dummy gates run against disposable copies, so
    replacing that exact byte sequence with the gate-local executable keeps
    semantics while removing the global alias lock that previously serialized
    the four independent dummy policies.
    """

    source = b"/workspace/executable"
    replacement = str(executable).encode("utf-8")
    rewritten = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if source not in data:
            continue
        path.write_bytes(data.replace(source, replacement))
        rewritten += 1
    return rewritten


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
    case_timeout_cap: float | None = None,
    pytest_workers: int = 1,
    policy_scope: str,
    private_executable_alias: bool = False,
) -> dict[str, Any]:
    if os.environ.get("PROGRAMBENCH_WHOLE_HARNESS_ISOLATED") != "1":
        raise RuntimeError("quality-gate target execution outside isolated whole-harness container is forbidden")
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    shutil.copytree(oracle_root / "eval", workspace / "eval")
    shutil.copy2(executable, workspace / "executable")
    (workspace / "executable").chmod(0o755)
    rewritten_alias_files = 0
    if private_executable_alias:
        rewritten_alias_files = rewrite_fixed_workspace_alias(
            workspace / "eval", workspace / "executable"
        )
    junit = workspace / junit_name
    env = os.environ.copy()
    env["TZ"] = "UTC"
    if case_timeout_cap is not None:
        env["PROGRAMBENCH_CASE_TIMEOUT_CAP"] = str(max(0.1, float(case_timeout_cap)))
    if gocoverdir is not None:
        gocoverdir.mkdir(parents=True, exist_ok=True)
        env["GOCOVERDIR"] = str(gocoverdir)
    log_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{workspace.parent.name}_{junit_name}").strip("_.-")
    cmd = [str(python), "-m", "pytest", "-q", "eval/tests", f"--junitxml={junit}"]
    if pytest_workers > 1:
        cmd.extend(["-n", str(pytest_workers)])
    alias_context = (
        contextlib.nullcontext()
        if private_executable_alias
        else fixed_workspace_executable(workspace / "executable")
    )
    with alias_context:
        result = run_command(
            cmd,
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
        "private_executable_alias": private_executable_alias,
        "rewritten_alias_files": rewritten_alias_files,
        "execution_isolation": {
            "mode": "whole_harness_docker_per_repository",
            "runtime_image": os.environ.get("PROGRAMBENCH_COVERAGE_RUNTIME_IMAGE"),
            "runtime_image_digest": os.environ.get("PROGRAMBENCH_COVERAGE_RUNTIME_DIGEST"),
            "network": "none",
            "read_only_rootfs": True,
            "cap_drop": ["ALL"],
            "no_new_privileges": True,
            "user": "1000:1000",
            "docker_socket_mounted": False,
            "binary_sha256": sha256_file(executable),
            "harness_sha256": sha256_file(Path(__file__).resolve()),
            "policy_scope": policy_scope,
        },
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
    def run_one(kind: str) -> dict[str, Any]:
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
            case_timeout_cap=1.0,
            pytest_workers=4,
            policy_scope=f"dummy_rejection:{kind}",
            private_executable_alias=True,
        )
        summary = run["junit_summary"]
        passing_tests = summary.get("passed_test_names") or []
        all_tests_rejected = (
            summary.get("tests", 0) > 0
            and not passing_tests
            and summary.get("skipped", 0) == 0
        )
        return {
            "kind": kind,
            **run,
            "rejected": run["pytest_returncode"] != 0 or summary.get("failures", 0) > 0 or summary.get("errors", 0) > 0,
            "all_tests_rejected": all_tests_rejected,
            "passing_test_count": len(passing_tests),
            "passing_test_names": passing_tests,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, max(1, len(dummy_kinds)))) as executor:
        futures = {kind: executor.submit(run_one, kind) for kind in dummy_kinds}
        return [futures[kind].result() for kind in dummy_kinds]


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


def prepare_work_root(work_root: Path, *, overwrite: bool) -> None:
    """Prepare a possibly bind-mounted work root without unlinking the mount."""
    if not work_root.exists():
        return
    if not overwrite:
        raise FileExistsError(f"Work root exists: {work_root}")
    for child in work_root.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle-material-root", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, default=Path("/tmp/programbench_generated_oracle_quality_gates"))
    parser.add_argument(
        "--python",
        type=Path,
        help="Python executable with pytest installed. Defaults to the current interpreter, then an isolated venv fallback.",
    )
    parser.add_argument("--dummy-kind", action="append", default=["true", "cat-stdin", "false", "empty-stderr"])
    parser.add_argument("--repeat-executable", type=Path)
    parser.add_argument("--repeat-gocoverdir", type=Path)
    parser.add_argument("--docker", default="docker")
    parser.add_argument(
        "--execution-runtime-image",
        default=os.environ.get("PROGRAMBENCH_COVERAGE_RUNTIME_IMAGE"),
    )
    parser.add_argument("--container-python", default="/usr/bin/python3")
    parser.add_argument("--container-cpus", type=int, default=2)
    parser.add_argument("--inner-container", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--skip-assertion-lint", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if not args.execution_runtime_image:
        parser.error("--execution-runtime-image is required; host quality-gate target execution is forbidden")
    if not args.inner_container:
        work_host = args.work_root.expanduser().resolve()
        output_host = args.output_json.expanduser().resolve()
        readonly = [args.oracle_material_root.expanduser().resolve()]
        if args.repeat_executable:
            readonly.append(args.repeat_executable.expanduser().resolve())
        writable = [work_host, output_host.parent]
        if args.repeat_gocoverdir:
            writable.append(args.repeat_gocoverdir.expanduser().resolve())
        return run_whole_harness_in_isolated_container(
            docker=args.docker,
            runtime_image=args.execution_runtime_image,
            script_name=Path(__file__).name,
            argv=list(sys.argv[1:]),
            writable_paths=writable,
            readonly_paths=readonly,
            target_env={},
            log_path=work_host / "_isolation_bootstrap" / "whole_harness.json",
            container_python=args.container_python,
            container_cpus=args.container_cpus,
        )
    args.python = Path(args.container_python)

    oracle_root = resolve_oracle_root(args.oracle_material_root)
    output_json = args.output_json if args.output_json.is_absolute() else (REPO_ROOT / args.output_json).resolve()
    work_root = args.work_root.expanduser().resolve()
    prepare_work_root(work_root, overwrite=args.overwrite)
    logs_dir = work_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    if args.python is None:
        # Preserve a virtualenv symlink so pytest remains importable.
        current_python = Path(sys.executable).absolute()
        python = current_python if ensure_pytest(current_python) else install_pytest_venv(work_root / ".venv", logs_dir)
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
            policy_scope="repeat_determinism",
            pytest_workers=4,
        )

    dummy_passing_tests = sorted(
        {
            name
            for result in dummy_results
            for name in result.get("passing_test_names") or []
        }
    )
    all_tests_reject_all_dummies = all(item["all_tests_rejected"] for item in dummy_results)
    target_results = [*dummy_results, *([repeat] if repeat else [])]
    all_target_executions_isolated = all(
        (item.get("execution_isolation") or {}).get("mode") == "whole_harness_docker_per_repository"
        and (item.get("execution_isolation") or {}).get("network") == "none"
        and (item.get("execution_isolation") or {}).get("read_only_rootfs") is True
        and (item.get("execution_isolation") or {}).get("docker_socket_mounted") is False
        and str((item.get("execution_isolation") or {}).get("runtime_image_digest") or "").startswith("sha256:")
        and bool((item.get("execution_isolation") or {}).get("binary_sha256"))
        and bool((item.get("execution_isolation") or {}).get("harness_sha256"))
        and bool((item.get("execution_isolation") or {}).get("policy_scope"))
        for item in target_results
    )
    payload = {
        "oracle_material_root": str(oracle_root),
        "python": str(python),
        "dummy_reject": dummy_results,
        "all_dummies_rejected": all_tests_reject_all_dummies,
        "all_tests_reject_all_dummies": all_tests_reject_all_dummies,
        "dummy_passing_test_count": len(dummy_passing_tests),
        "dummy_passing_test_names": dummy_passing_tests,
        "source_leak_scan": leak,
        "assertion_lint": assertion_lint,
        "repeat_check": repeat,
        "all_target_executions_isolated": all_target_executions_isolated,
        "execution_isolation": {
            "mode": "whole_harness_docker_per_repository",
            "runtime_image": os.environ.get("PROGRAMBENCH_COVERAGE_RUNTIME_IMAGE"),
            "runtime_image_digest": os.environ.get("PROGRAMBENCH_COVERAGE_RUNTIME_DIGEST"),
            "harness_sha256": sha256_file(Path(__file__).resolve()),
            "policy_scopes": sorted(
                (item.get("execution_isolation") or {}).get("policy_scope")
                for item in target_results
                if (item.get("execution_isolation") or {}).get("policy_scope")
            ),
            "network": "none",
            "read_only_rootfs": True,
            "cap_drop": ["ALL"],
            "no_new_privileges": True,
            "user": "1000:1000",
            "docker_socket_mounted": False,
        },
    }
    write_json(output_json, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))

    repeat_ok = repeat is None or (
        repeat["pytest_returncode"] == 0
        and repeat["junit_summary"].get("failures") == 0
        and repeat["junit_summary"].get("errors") == 0
    )
    lint_ok = assertion_lint is None or assertion_lint.get("passed", False)
    return 0 if (
        payload["all_tests_reject_all_dummies"]
        and leak["passed"]
        and lint_ok
        and repeat_ok
        and all_target_executions_isolated
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
