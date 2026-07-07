#!/usr/bin/env python3
"""Run Linux/Docker dynamic quality gates for ProgramBench Gym instances.

This is the CI-side companion to ``programbench_build_gym_instances.py``. It
materializes reference cleanrooms, runs source-leak-guarded oracle tests against
the reference executable, checks that a trivial dummy executable does not pass,
and records the results back into each instance's quality report.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST_BUNDLE_ROOT = Path("reports/test_bundles_oracle_guarded_2026-07-07")
REQUIRED_DYNAMIC_GATES = {
    "reference_binary_materialized",
    "reference_smoke_runs",
    "oracle_tests_pass_reference",
    "dummy_does_not_pass_all",
    "offline_reproducible_eval",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def resolve_repo_path(path: Path) -> Path:
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def short_text(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def run_command(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
    env: dict[str, str] | None = None,
    log_path: Path | None = None,
) -> dict[str, Any]:
    started = dt.datetime.now(dt.UTC)
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        timed_out = False
        returncode = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", errors="replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", errors="replace")

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
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps({**payload, "stdout": stdout, "stderr": stderr}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        payload["log_path"] = str(log_path.relative_to(REPO_ROOT)) if log_path.is_relative_to(REPO_ROOT) else str(log_path)
    return payload


def load_config(path: Path) -> dict[str, Any]:
    config = read_json(path)
    if not config.get("tasks"):
        raise ValueError(f"Config has no tasks: {path}")
    return config


def sync_blobs(task_ids: list[str], uv: str) -> None:
    programbench_dir = REPO_ROOT / "external" / "ProgramBench"
    if not (programbench_dir / "pyproject.toml").exists():
        raise FileNotFoundError("external/ProgramBench is missing; clone it before syncing blobs")
    for task_id in task_ids:
        result = run_command([uv, "run", "programbench", "blob", "sync", task_id], cwd=programbench_dir, timeout=1800)
        if result["returncode"] != 0:
            raise RuntimeError(f"blob sync failed for {task_id}: {result['stderr_tail']}")


def prepare_test_bundles(
    *,
    task_ids: list[str],
    config: dict[str, Any],
    config_path: Path,
    blob_root: Path | None,
    force: bool,
) -> Path:
    bundle_root = resolve_repo_path(Path(config.get("test_bundle_root") or DEFAULT_TEST_BUNDLE_ROOT))
    tasks_root = resolve_repo_path(Path(config.get("tasks_root") or "external/ProgramBench/src/programbench/data/tasks"))
    for task_id in task_ids:
        bundle_dir = bundle_root / task_id / "oracle_tests"
        if bundle_dir.exists() and not force:
            continue
        cmd = [
            sys.executable,
            str(REPO_ROOT / "tools" / "programbench_build_test_bundle.py"),
            task_id,
            "--tasks-root",
            str(tasks_root),
            "--out-dir",
            str(bundle_root),
            "--mode",
            "oracle-guarded",
        ]
        if blob_root is not None:
            cmd.extend(["--blob-dir", str(blob_root)])
        result = run_command(cmd, cwd=REPO_ROOT, timeout=1800)
        if result["returncode"] != 0:
            raise RuntimeError(
                f"test bundle build failed for {task_id} using {config_path}: {result['stderr_tail']}"
            )
    return bundle_root


def build_gym_instances(
    *,
    config_path: Path,
    output_root: Path,
    blob_root: Path | None,
    docker: str,
    overwrite: bool,
    copy_tests: bool,
) -> None:
    if output_root.exists():
        if not overwrite:
            raise FileExistsError(f"Output root exists: {output_root}")
        shutil.rmtree(output_root)
    cmd = [
        sys.executable,
        str(REPO_ROOT / "tools" / "programbench_build_gym_instances.py"),
        "--config",
        str(config_path),
        "--output-root",
        str(output_root),
        "--materialize-cleanroom",
        "--docker",
        docker,
    ]
    if copy_tests:
        cmd.append("--copy-tests")
    if blob_root is not None:
        cmd.extend(["--blob-root", str(blob_root)])
    result = run_command(cmd, cwd=REPO_ROOT, timeout=3600)
    if result["returncode"] != 0:
        raise RuntimeError(f"Gym instance build failed: {result['stderr_tail']}")


def write_dummy(path: Path) -> None:
    path.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def normalize_read_permissions(path: Path) -> dict[str, Any]:
    targets = [path] if path.is_file() else [item for item in path.rglob("*")]
    failures: list[str] = []
    for target in targets:
        try:
            mode = target.stat().st_mode
            target.chmod(mode | stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            if target.is_dir() or os.access(target, os.X_OK):
                target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except PermissionError:
            failures.append(str(target))
    if not failures:
        return {"status": "normalized", "method": "python_chmod"}

    sudo = shutil.which("sudo")
    if sudo is None:
        return {"status": "failed", "method": "python_chmod", "permission_failures": failures[:20]}
    chown = subprocess.run(
        [sudo, "chown", "-R", f"{os.getuid()}:{os.getgid()}", str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    chmod = subprocess.run(
        [sudo, "chmod", "-R", "u+rwX,go+rX", str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return {
        "status": "normalized" if chown.returncode == 0 and chmod.returncode == 0 else "failed",
        "method": "sudo_chown_chmod",
        "chown_returncode": chown.returncode,
        "chmod_returncode": chmod.returncode,
        "chown_stderr": chown.stderr.strip(),
        "chmod_stderr": chmod.stderr.strip(),
    }


def copy_reference_workspace(instance_dir: Path, workspace: Path, *, dummy: bool) -> None:
    oracle_src = instance_dir / "oracle_tests" / "sanitized"
    executable_src = instance_dir / "cleanroom" / "executable"
    if not oracle_src.exists():
        raise FileNotFoundError(f"missing sanitized oracle tests: {oracle_src}")
    if not dummy and not executable_src.exists():
        raise FileNotFoundError(f"missing reference executable: {executable_src}")

    workspace.mkdir(parents=True, exist_ok=True)
    shutil.copytree(oracle_src, workspace / "oracle_tests", symlinks=False)
    executable_dest = workspace / "executable"
    if dummy:
        write_dummy(executable_dest)
    else:
        permissions = normalize_read_permissions(executable_src)
        if permissions["status"] != "normalized":
            raise PermissionError(f"could not normalize reference executable permissions: {permissions}")
        shutil.copy2(executable_src, executable_dest)
        executable_dest.chmod(executable_dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def docker_or_host_run(
    *,
    runner: str,
    docker: str,
    image_ref: str,
    workspace: Path,
    command: str,
    timeout: int,
    log_path: Path,
) -> dict[str, Any]:
    if runner == "docker":
        cmd = [
            docker,
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-v",
            f"{workspace.resolve()}:/workspace",
            "-w",
            "/workspace",
            image_ref,
            "bash",
            "-lc",
            command,
        ]
        return run_command(cmd, timeout=timeout, log_path=log_path)
    return run_command(["bash", "-lc", command], cwd=workspace, timeout=timeout, log_path=log_path)


def run_reference_smoke(
    *,
    instance_dir: Path,
    metadata: dict[str, Any],
    runner: str,
    docker: str,
    timeout: int,
    log_dir: Path,
) -> dict[str, Any]:
    executable = instance_dir / "cleanroom" / "executable"
    if not executable.exists():
        return {"status": "fail", "details": {"reason": "missing cleanroom/executable"}}

    command = r'''
if [ ! -x ./executable ]; then
  echo "missing executable" >&2
  exit 42
fi
attempt() {
  timeout "$1" "${@:2}" >/tmp/pb_gym_smoke.out 2>/tmp/pb_gym_smoke.err
  rc=$?
  out_bytes=$(wc -c </tmp/pb_gym_smoke.out)
  err_bytes=$(wc -c </tmp/pb_gym_smoke.err)
  echo "cmd=${*:2} rc=${rc} stdout_bytes=${out_bytes} stderr_bytes=${err_bytes}"
  if [ "$rc" -ne 124 ]; then
    exit 0
  fi
}
attempt 20 ./executable --help
attempt 20 ./executable -h
attempt 20 ./executable
exit 1
'''
    if runner == "docker":
        workspace = instance_dir / "cleanroom"
        image_ref = metadata["image_tags"]["cleanroom"]
    else:
        workspace = instance_dir / "cleanroom"
        image_ref = ""
    result = docker_or_host_run(
        runner=runner,
        docker=docker,
        image_ref=image_ref,
        workspace=workspace,
        command=command,
        timeout=timeout,
        log_path=log_dir / "reference_smoke.json",
    )
    status = "pass" if result["returncode"] == 0 else "fail"
    return {"status": status, "details": {"runner": runner, **result}}


def run_oracle_gate(
    *,
    instance_dir: Path,
    metadata: dict[str, Any],
    runner: str,
    docker: str,
    timeout: int,
    dummy: bool,
    log_dir: Path,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="pb_gym_dynamic_", ignore_cleanup_errors=True) as tmp:
        workspace = Path(tmp) / "workspace"
        copy_reference_workspace(instance_dir, workspace, dummy=dummy)
        image_ref = metadata["image_tags"]["eval"] if runner == "docker" else ""
        result = docker_or_host_run(
            runner=runner,
            docker=docker,
            image_ref=image_ref,
            workspace=workspace,
            command="./oracle_tests/run_all.sh",
            timeout=timeout,
            log_path=log_dir / ("dummy_oracle_tests.json" if dummy else "reference_oracle_tests.json"),
        )
    if dummy:
        status = "pass" if result["returncode"] != 0 else "fail"
    else:
        status = "pass" if result["returncode"] == 0 else "fail"
    return {"status": status, "details": {"runner": runner, "network": "none" if runner == "docker" else "host", **result}}


def replace_gate(gates: list[dict[str, Any]], name: str, status: str, details: dict[str, Any]) -> None:
    for gate in gates:
        if gate.get("name") == name:
            gate["status"] = status
            gate["details"] = details
            return
    gates.append({"name": name, "status": status, "details": details})


def update_instance_reports(instance_dir: Path, updates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    quality_path = instance_dir / "quality_report.json"
    metadata_path = instance_dir / "metadata.json"
    quality = read_json(quality_path)
    metadata = read_json(metadata_path)

    for gate_name, row in updates.items():
        replace_gate(quality["gates"], gate_name, row["status"], row["details"])
        replace_gate(metadata["quality_gates"], gate_name, row["status"], row["details"])

    dynamic_passed = all(
        gate.get("status") == "pass"
        for gate in quality["gates"]
        if gate.get("name") in REQUIRED_DYNAMIC_GATES
    )
    quality.setdefault("summary", {})["dynamic_gates_passed"] = dynamic_passed
    quality["summary"]["dynamic_gates_ready"] = dynamic_passed
    metadata["dynamic_gates_passed"] = dynamic_passed

    write_json(quality_path, quality)
    write_json(metadata_path, metadata)
    return {
        "instance_id": instance_dir.name,
        "dynamic_gates_passed": dynamic_passed,
        "gates": {name: row["status"] for name, row in updates.items()},
    }


def write_summary(output_root: Path, rows: list[dict[str, Any]]) -> None:
    summary = {
        "schema_version": "0.1",
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "output_root": str(output_root),
        "instances": rows,
        "aggregate": {
            "instances": len(rows),
            "dynamic_gates_passed": sum(1 for row in rows if row["dynamic_gates_passed"]),
        },
    }
    write_json(output_root / "gym_dynamic_gate_summary.json", summary)

    lines = [
        "# ProgramBench Gym Dynamic Gate Summary",
        "",
        f"- Created: `{summary['created_at']}`",
        f"- Dynamic gates passed: {summary['aggregate']['dynamic_gates_passed']}/{summary['aggregate']['instances']}",
        "",
        "| instance | dynamic gates | reference | oracle/reference | dummy reject | offline |",
        "| --- | ---: | --- | --- | --- | --- |",
    ]
    for row in rows:
        gates = row["gates"]
        lines.append(
            f"| `{row['instance_id']}` | {row['dynamic_gates_passed']} | "
            f"{gates.get('reference_smoke_runs')} | {gates.get('oracle_tests_pass_reference')} | "
            f"{gates.get('dummy_does_not_pass_all')} | {gates.get('offline_reproducible_eval')} |"
        )
    lines.append("")
    (output_root / "gym_dynamic_gate_summary.md").write_text("\n".join(lines), encoding="utf-8")


def run_dynamic_gates_for_instance(
    *,
    instance_dir: Path,
    runner: str,
    docker: str,
    smoke_timeout: int,
    oracle_timeout: int,
) -> dict[str, Any]:
    metadata = read_json(instance_dir / "metadata.json")
    log_dir = instance_dir / "private" / "dynamic_gate_logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    materialized = (instance_dir / "cleanroom" / "executable").exists()
    updates: dict[str, dict[str, Any]] = {
        "reference_binary_materialized": {
            "status": "pass" if materialized else "fail",
            "details": {
                "executable": "cleanroom/executable",
                "exists": materialized,
                "image": metadata["image_tags"]["cleanroom"],
            },
        }
    }
    updates["reference_smoke_runs"] = run_reference_smoke(
        instance_dir=instance_dir,
        metadata=metadata,
        runner=runner,
        docker=docker,
        timeout=smoke_timeout,
        log_dir=log_dir,
    )
    updates["oracle_tests_pass_reference"] = run_oracle_gate(
        instance_dir=instance_dir,
        metadata=metadata,
        runner=runner,
        docker=docker,
        timeout=oracle_timeout,
        dummy=False,
        log_dir=log_dir,
    )
    updates["dummy_does_not_pass_all"] = run_oracle_gate(
        instance_dir=instance_dir,
        metadata=metadata,
        runner=runner,
        docker=docker,
        timeout=oracle_timeout,
        dummy=True,
        log_dir=log_dir,
    )
    offline_status = "pass" if runner == "docker" and updates["oracle_tests_pass_reference"]["status"] == "pass" else "skipped"
    updates["offline_reproducible_eval"] = {
        "status": offline_status,
        "details": {
            "runner": runner,
            "network": "none" if runner == "docker" else "host",
            "reason": None if offline_status == "pass" else "host runner cannot prove no-internet reproducibility",
        },
    }
    return update_instance_reports(instance_dir, updates)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--blob-root", type=Path, default=None)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--runner", choices=["docker", "host"], default="docker")
    parser.add_argument("--sync-blobs", action="store_true")
    parser.add_argument("--prepare-bundles", action="store_true")
    parser.add_argument("--force-bundles", action="store_true")
    parser.add_argument("--copy-tests", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--smoke-timeout", type=int, default=180)
    parser.add_argument("--oracle-timeout", type=int, default=900)
    args = parser.parse_args()

    config_path = resolve_repo_path(args.config)
    output_root = resolve_repo_path(args.output_root)
    blob_root = resolve_repo_path(args.blob_root) if args.blob_root else None
    config = load_config(config_path)
    task_ids = list(config["tasks"])

    if args.sync_blobs:
        sync_blobs(task_ids, args.uv)
    if args.prepare_bundles:
        prepare_test_bundles(
            task_ids=task_ids,
            config=config,
            config_path=config_path,
            blob_root=blob_root,
            force=args.force_bundles,
        )

    build_gym_instances(
        config_path=config_path,
        output_root=output_root,
        blob_root=blob_root,
        docker=args.docker,
        overwrite=args.overwrite,
        copy_tests=args.copy_tests,
    )

    rows = []
    for task_id in task_ids:
        rows.append(
            run_dynamic_gates_for_instance(
                instance_dir=output_root / task_id,
                runner=args.runner,
                docker=args.docker,
                smoke_timeout=args.smoke_timeout,
                oracle_timeout=args.oracle_timeout,
            )
        )
    write_summary(output_root, rows)
    print(json.dumps({"dynamic_gates_passed": sum(1 for row in rows if row["dynamic_gates_passed"]), "instances": len(rows)}, indent=2))
    return 0 if all(row["dynamic_gates_passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
