#!/usr/bin/env python3
"""Run official ProgramBench pytest branches against a Go source coverage build."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASKS_ROOT = Path("external/ProgramBench/src/programbench/data/tasks")
HF_CACHE_ROOT = Path.home() / ".cache/huggingface/hub/datasets--programbench--ProgramBench-Tests/snapshots"
ORACLE_MATERIAL_DIRS = (
    "eval",
    "testdata",
    "fixtures",
    "fixture",
    "tests",
    "examples",
    "samples",
    "resources",
    "assets",
    "data",
    "inputs",
    "expected",
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_simple_yaml(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- ") and current_list_key:
            data.setdefault(current_list_key, []).append(line[2:].strip("'\""))
            continue
        current_list_key = None
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not value:
            current_list_key = key
            data[key] = []
        elif value.startswith("[") or value.startswith("{"):
            data[key] = value
        else:
            data[key] = value.strip("'\"")
    return data


def ignored_test_names(branch: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for item in branch.get("ignored_tests") or []:
        if isinstance(item, str):
            names.add(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.add(item["name"])
    return names


def active_branches(task_dir: Path) -> list[str]:
    payload = read_json(task_dir / "tests.json")
    active: list[str] = []
    for branch_id, branch in (payload.get("branches") or {}).items():
        if not isinstance(branch, dict) or branch.get("ignored"):
            continue
        ignored = ignored_test_names(branch)
        tests = [name for name in branch.get("tests") or [] if isinstance(name, str)]
        if any(name not in ignored for name in tests):
            active.append(branch_id)
    return active


def find_blob_dir(instance_id: str, explicit: Path | None) -> Path:
    if explicit is not None:
        candidate = explicit.expanduser().resolve()
        if candidate.name == instance_id:
            return candidate
        nested = candidate / instance_id
        if nested.exists():
            return nested
        raise FileNotFoundError(f"Could not find {instance_id} under {candidate}")

    env_root = os.environ.get("PROGRAMBENCH_BLOB_DIR")
    if env_root:
        candidate = Path(env_root).expanduser().resolve() / instance_id
        if candidate.exists():
            return candidate

    matches = sorted(HF_CACHE_ROOT.glob(f"*/{instance_id}"), key=lambda path: path.stat().st_mtime, reverse=True)
    if matches:
        return matches[0]
    raise FileNotFoundError(
        f"Could not locate ProgramBench test blobs for {instance_id}. "
        "Run `programbench blob sync <instance_id>` first or pass --blob-dir."
    )


def short_text(text: str, limit: int = 5000) -> str:
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
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
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
        write_json(log_path, {**payload, "stdout": stdout, "stderr": stderr})
        payload["log_path"] = str(log_path)
    return payload


def safe_extract(tar_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:gz") as archive:
        for member in archive.getmembers():
            target = (dest / member.name).resolve()
            if not target.is_relative_to(dest.resolve()):
                raise ValueError(f"Unsafe tar member path: {member.name}")
        archive.extractall(dest)


def copy_oracle_material(extract_dir: Path, repo_dir: Path) -> list[str]:
    copied: list[str] = []
    for name in ORACLE_MATERIAL_DIRS:
        source = extract_dir / name
        if not source.exists():
            continue
        target = repo_dir / name
        if target.exists() and name == "eval":
            shutil.rmtree(target)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        copied.append(name)
    return copied


def parse_total_coverage(text: str) -> float | None:
    for line in reversed(text.splitlines()):
        match = re.search(r"\btotal:\s+\(statements\)\s+([0-9.]+)%", line)
        if match:
            return float(match.group(1))
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("instance_id")
    parser.add_argument("--tasks-root", type=Path, default=DEFAULT_TASKS_ROOT)
    parser.add_argument("--blob-dir", type=Path)
    parser.add_argument("--branch", default="first-active")
    parser.add_argument("--work-root", type=Path, default=Path("/tmp/programbench_source_coverage"))
    parser.add_argument("--output-root", type=Path, default=Path("reports/programbench_source_coverage"))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    tasks_root = args.tasks_root if args.tasks_root.is_absolute() else (REPO_ROOT / args.tasks_root).resolve()
    output_root = args.output_root if args.output_root.is_absolute() else (REPO_ROOT / args.output_root).resolve()
    task_dir = tasks_root / args.instance_id
    meta = parse_simple_yaml(task_dir / "task.yaml")
    if str(meta.get("language", "")).lower() not in {"go", "golang"}:
        raise ValueError(f"{args.instance_id} is not a Go task: {meta.get('language')}")

    branches = active_branches(task_dir)
    if not branches:
        raise ValueError(f"{args.instance_id} has no active branches")
    branch = branches[0] if args.branch == "first-active" else args.branch
    if branch not in branches:
        raise ValueError(f"{branch} is not an active branch for {args.instance_id}")

    blob_dir = find_blob_dir(args.instance_id, args.blob_dir)
    branch_tar = blob_dir / "tests" / f"{branch}.tar.gz"
    if not branch_tar.exists():
        raise FileNotFoundError(f"Missing branch tarball: {branch_tar}")

    safe_instance = args.instance_id.replace("/", "_")
    work_dir = args.work_root / safe_instance / branch
    if work_dir.exists():
        if not args.overwrite:
            raise FileExistsError(f"Work dir exists: {work_dir}")
        shutil.rmtree(work_dir)
    repo_dir = work_dir / "source"
    extract_dir = work_dir / "branch_blob"
    logs_dir = work_dir / "logs"
    coverage_dir = repo_dir / "coverage" / "raw"
    logs_dir.mkdir(parents=True, exist_ok=True)

    repository = str(meta["repository"])
    commit = str(meta["commit"])
    clone_url = f"https://github.com/{repository}.git"
    clone = run_command(["git", "clone", clone_url, str(repo_dir)], timeout=900, log_path=logs_dir / "git_clone.json")
    if clone["returncode"] != 0:
        raise RuntimeError(f"git clone failed: {clone['stderr_tail']}")
    checkout = run_command(["git", "checkout", commit], cwd=repo_dir, timeout=120, log_path=logs_dir / "git_checkout.json")
    if checkout["returncode"] != 0:
        raise RuntimeError(f"git checkout failed: {checkout['stderr_tail']}")

    safe_extract(branch_tar, extract_dir)
    copied_material = copy_oracle_material(extract_dir, repo_dir)

    mod_download = run_command(["go", "mod", "download"], cwd=repo_dir, timeout=900, log_path=logs_dir / "go_mod_download.json")
    if mod_download["returncode"] != 0:
        raise RuntimeError(f"go mod download failed: {mod_download['stderr_tail']}")
    build = run_command(
        ["go", "build", "-cover", "-coverpkg=./...", "-o", "executable", "."],
        cwd=repo_dir,
        timeout=900,
        log_path=logs_dir / "go_build_cover.json",
    )
    if build["returncode"] != 0:
        raise RuntimeError(f"go coverage build failed: {build['stderr_tail']}")

    venv_dir = work_dir / ".venv"
    venv = run_command(["python3", "-m", "venv", str(venv_dir)], timeout=300, log_path=logs_dir / "venv.json")
    if venv["returncode"] != 0:
        raise RuntimeError(f"venv creation failed: {venv['stderr_tail']}")
    python = venv_dir / "bin" / "python"
    pip = run_command(
        [str(python), "-m", "pip", "install", "-q", "pytest", "pytest-timeout", "pytest-xdist"],
        timeout=900,
        log_path=logs_dir / "pip_install_pytest.json",
    )
    if pip["returncode"] != 0:
        raise RuntimeError(f"pytest dependency install failed: {pip['stderr_tail']}")

    coverage_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["GOCOVERDIR"] = str(coverage_dir)
    pytest = run_command(
        [
            str(python),
            "-m",
            "pytest",
            "eval/tests/",
            "--junitxml=coverage/results.xml",
            "--timeout=5",
            "--timeout-method=thread",
            "-n",
            "auto",
            "-v",
        ],
        cwd=repo_dir,
        env=env,
        timeout=1800,
        log_path=logs_dir / "pytest_official_branch.json",
    )

    cov_percent = run_command(
        ["go", "tool", "covdata", "percent", "-i", str(coverage_dir)],
        cwd=repo_dir,
        timeout=300,
        log_path=logs_dir / "go_covdata_percent.json",
    )
    profile_path = repo_dir / "coverage" / "profile.txt"
    cov_textfmt = run_command(
        ["go", "tool", "covdata", "textfmt", "-i", str(coverage_dir), "-o", str(profile_path)],
        cwd=repo_dir,
        timeout=300,
        log_path=logs_dir / "go_covdata_textfmt.json",
    )
    cover_func = run_command(
        ["go", "tool", "cover", "-func", str(profile_path)],
        cwd=repo_dir,
        timeout=300,
        log_path=logs_dir / "go_cover_func.json",
    )

    summary = {
        "instance_id": args.instance_id,
        "repository": repository,
        "commit": commit,
        "language": meta.get("language"),
        "branch": branch,
        "active_branch_count": len(branches),
        "branch_tar": str(branch_tar),
        "work_dir": str(work_dir),
        "copied_oracle_material": copied_material,
        "go_build_returncode": build["returncode"],
        "pytest_returncode": pytest["returncode"],
        "covdata_percent_returncode": cov_percent["returncode"],
        "covdata_textfmt_returncode": cov_textfmt["returncode"],
        "cover_func_returncode": cover_func["returncode"],
        "coverage_total_percent": parse_total_coverage(cover_func["stdout_tail"]),
        "logs_dir": str(logs_dir),
    }
    out_dir = output_root / args.instance_id
    write_json(out_dir / f"{branch}.go_coverage_summary.json", summary)
    (out_dir / f"{branch}.go_coverage_summary.md").write_text(
        "\n".join(
            [
                f"# Go Coverage Smoke: `{args.instance_id}`",
                "",
                f"- Repository: `{repository}`",
                f"- Commit: `{commit}`",
                f"- Branch: `{branch}`",
                f"- Pytest return code: `{pytest['returncode']}`",
                f"- Coverage total: `{summary['coverage_total_percent']}`",
                f"- Work dir: `{work_dir}`",
                f"- Logs dir: `{logs_dir}`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if pytest["returncode"] == 0 and cover_func["returncode"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
