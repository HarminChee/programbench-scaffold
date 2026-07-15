#!/usr/bin/env python3
"""Measure ProgramBench official pytest coverage on a pinned Go source repo."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET
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


def branch_test_metadata(task_dir: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(task_dir / "tests.json")
    metadata: dict[str, dict[str, Any]] = {}
    for branch_id, branch in (payload.get("branches") or {}).items():
        if not isinstance(branch, dict):
            continue
        ignored = ignored_test_names(branch)
        tests = {name for name in branch.get("tests") or [] if isinstance(name, str)}
        metadata[branch_id] = {
            "ignored": ignored,
            "expected": tests,
            "active": tests - ignored,
            "ignored_count": len(ignored),
            "expected_count": len(tests),
            "active_count": len(tests - ignored),
        }
    return metadata


def select_branches(branch_spec: str, active: list[str]) -> list[str]:
    if branch_spec == "first-active":
        return [active[0]]
    if branch_spec == "all":
        return active
    selected = [item.strip() for item in branch_spec.split(",") if item.strip()]
    unknown = [item for item in selected if item not in active]
    if unknown:
        raise ValueError(f"Unknown or inactive branches: {', '.join(unknown)}")
    return selected


def image_name_from_instance_id(instance_id: str) -> str:
    return f"programbench/{instance_id.replace('__', '_1776_')}"


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
    include_output: bool = False,
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
    if include_output:
        payload["stdout"] = stdout
        payload["stderr"] = stderr
    return payload


def safe_extract(tar_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:gz") as archive:
        dest_resolved = dest.resolve()
        for member in archive.getmembers():
            target = (dest / member.name).resolve()
            if not target.is_relative_to(dest_resolved):
                raise ValueError(f"Unsafe tar member path: {member.name}")
        archive.extractall(dest)


def copy_oracle_material(extract_dir: Path, repo_dir: Path) -> list[str]:
    copied: list[str] = []
    # A branch is a cleanroom unit. Remove material left by the previous
    # branch even when the new tarball does not contain the same directory.
    for name in ORACLE_MATERIAL_DIRS:
        target = repo_dir / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    for name in ORACLE_MATERIAL_DIRS:
        source = extract_dir / name
        if not source.exists():
            continue
        target = repo_dir / name
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        copied.append(name)
    return copied


def resolve_oracle_material_root(path: Path) -> Path:
    candidate = path.expanduser().resolve()
    if (candidate / "eval").exists():
        return candidate
    nested = candidate / "oracle_tests"
    if (nested / "eval").exists():
        return nested
    raise FileNotFoundError(f"Could not find eval/ under oracle material root: {candidate}")


def safe_run_label(value: str) -> str:
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_.-")
    return label or "suite"


def parse_total_statement_coverage(text: str) -> float | None:
    for line in reversed(text.splitlines()):
        match = re.search(r"\btotal:\s+\(statements\)\s+([0-9.]+)%", line)
        if match:
            return float(match.group(1))
    return None


def parse_cover_profile_statement_coverage(path: Path) -> float | None:
    if not path.exists():
        return None
    total = 0
    covered = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("mode:"):
            continue
        parts = line.split()
        if len(parts) != 3:
            continue
        try:
            statements = int(parts[1])
            count = int(parts[2])
        except ValueError:
            continue
        total += statements
        if count:
            covered += statements
    if total == 0:
        return None
    return round((covered / total) * 100, 1)


def parse_cover_profile_line_coverage(path: Path) -> dict[str, Any]:
    """Approximate PB-style line coverage from Go cover-profile blocks.

    Go records statement blocks rather than individual executable lines. We
    take the union of source lines spanned by those blocks and mark a line
    covered when at least one block spanning it has a non-zero count.
    """

    if not path.exists():
        return {"line_coverage_percent": None, "covered_executable_lines": 0, "total_executable_lines": 0}
    all_lines: dict[str, set[int]] = {}
    covered_lines: dict[str, set[int]] = {}
    pattern = re.compile(r"^(.*):(\d+)\.(\d+),(\d+)\.(\d+)\s+(\d+)\s+(\d+)$")
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if raw.startswith("mode:"):
            continue
        match = pattern.match(raw.strip())
        if not match:
            continue
        source = match.group(1)
        start_line = int(match.group(2))
        end_line = int(match.group(4))
        end_column = int(match.group(5))
        statements = int(match.group(6))
        count = int(match.group(7))
        if statements <= 0:
            continue
        if end_column <= 1 and end_line > start_line:
            end_line -= 1
        lines = set(range(start_line, max(start_line, end_line) + 1))
        all_lines.setdefault(source, set()).update(lines)
        if count:
            covered_lines.setdefault(source, set()).update(lines)
    total = sum(len(lines) for lines in all_lines.values())
    covered = sum(len(covered_lines.get(source, set()) & lines) for source, lines in all_lines.items())
    per_file = {
        source: {
            "covered_executable_lines": len(covered_lines.get(source, set()) & lines),
            "total_executable_lines": len(lines),
            "line_coverage_percent": round(
                len(covered_lines.get(source, set()) & lines) / len(lines) * 100,
                1,
            )
            if lines
            else None,
        }
        for source, lines in sorted(all_lines.items())
    }
    return {
        "line_coverage_percent": round(covered / total * 100, 1) if total else None,
        "covered_executable_lines": covered,
        "total_executable_lines": total,
        "line_coverage_source": "go_cover_profile_block_line_union",
        "line_coverage_note": "Approximation of PB line coverage from Go statement-block ranges.",
        "per_file_line_coverage": per_file,
    }


def parse_go_test_package_coverage(text: str) -> float | None:
    matches = re.findall(r"coverage:\s+([0-9.]+)%\s+of\s+statements", text)
    if not matches:
        return None
    return float(matches[-1])


def parse_json_stream(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    items: list[dict[str, Any]] = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        item, end = decoder.raw_decode(text, index)
        if isinstance(item, dict):
            items.append(item)
        index = end
    return items


def relative_go_package(repo_dir: Path, package: dict[str, Any]) -> str:
    package_dir = Path(str(package.get("Dir", ""))).resolve()
    repo_resolved = repo_dir.resolve()
    if package_dir == repo_resolved:
        return "."
    try:
        rel = package_dir.relative_to(repo_resolved)
    except ValueError:
        return str(package.get("ImportPath", "."))
    return "./" + rel.as_posix()


def discover_go_main_packages(
    repo_dir: Path, repository: str, logs_dir: Path, go_executable: str = "go"
) -> dict[str, Any]:
    result = run_command(
        [go_executable, "list", "-e", "-json", "./..."],
        cwd=repo_dir,
        timeout=900,
        log_path=logs_dir / "go_list_packages.json",
        include_output=True,
    )
    packages = parse_json_stream(result.get("stdout", ""))
    repo_name = repository.rstrip("/").split("/")[-1]
    normalized_repo_name = re.sub(r"[^a-z0-9]", "", repo_name.lower())
    candidates_by_target: dict[str, dict[str, Any]] = {}

    def score_target(target: str) -> int:
        rel = "." if target == "." else target.removeprefix("./")
        parts = [] if rel == "." else rel.split("/")
        leaf = parts[-1] if parts else repo_name
        score = 0
        if target == ".":
            score += 120
        if parts[:1] == ["cmd"]:
            score += 90
        if re.sub(r"[^a-z0-9]", "", leaf.lower()) == normalized_repo_name:
            score += 70
        if "internal" in parts:
            score -= 80
        if any(part in {"example", "examples", "testdata", "tests", "tools"} for part in parts):
            score -= 100
        return score - len(parts)

    def add_candidate(*, target: str, import_path: str | None, directory: str, discovered_by: str) -> None:
        candidate = {
            "import_path": import_path,
            "dir": directory,
            "target": target,
            "score": score_target(target),
            "discovered_by": [discovered_by],
        }
        previous = candidates_by_target.get(target)
        if previous:
            previous["discovered_by"] = sorted(set(previous["discovered_by"] + [discovered_by]))
            if import_path:
                previous["import_path"] = import_path
            return
        candidates_by_target[target] = candidate

    for package in packages:
        if package.get("Name") != "main":
            continue
        target = relative_go_package(repo_dir, package)
        add_candidate(
            target=target,
            import_path=package.get("ImportPath"),
            directory=str(package.get("Dir", "")),
            discovered_by="go_list",
        )

    # Some pinned repositories require a newer Go toolchain than the caller,
    # so `go list` can omit their intended command.  A source scan gives us a
    # deterministic fallback and lets the subsequent build-attempt loop choose
    # the first candidate that really compiles.
    excluded_parts = {".git", "vendor", "node_modules"}
    for go_file in repo_dir.rglob("*.go"):
        relative = go_file.relative_to(repo_dir)
        if go_file.name.endswith("_test.go") or any(part in excluded_parts for part in relative.parts):
            continue
        try:
            prefix = go_file.read_text(encoding="utf-8", errors="replace")[:8192]
        except OSError:
            continue
        if not re.search(r"(?m)^\s*package\s+main\b", prefix):
            continue
        parent = relative.parent
        target = "." if str(parent) == "." else "./" + parent.as_posix()
        add_candidate(
            target=target,
            import_path=None,
            directory=str(go_file.parent.resolve()),
            discovered_by="source_scan",
        )

    candidates = list(candidates_by_target.values())
    candidates.sort(key=lambda item: (-int(item["score"]), str(item["target"])))
    selected = candidates[0]["target"] if candidates else "."
    return {
        "command_returncode": result["returncode"],
        "log_path": result.get("log_path"),
        "selected": selected,
        "candidates": candidates,
        "fallback_used": not any("go_list" in item["discovered_by"] for item in candidates),
    }


def select_go_build_package(
    repo_dir: Path,
    requested: str,
    repository: str,
    logs_dir: Path,
    go_executable: str = "go",
) -> dict[str, Any]:
    if requested != "auto":
        return {
            "mode": "explicit",
            "selected": requested,
            "candidates": [],
            "fallback_used": False,
        }
    discovery = discover_go_main_packages(repo_dir, repository, logs_dir, go_executable)
    return {"mode": "auto", **discovery}


def normalize_test_name(name: str) -> str:
    if name.startswith("eval.tests."):
        return name.removeprefix("eval.")
    return name


def parse_junit(path: Path, *, ignored_tests: set[str] | None = None) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8", errors="replace"))
    except ET.ParseError as exc:
        return {"exists": True, "parse_error": str(exc)}

    tests = 0
    failures = 0
    errors = 0
    skipped = 0
    names: set[str] = set()
    failed_names: set[str] = set()
    error_names: set[str] = set()
    skipped_names: set[str] = set()
    failure_details: list[dict[str, str]] = []
    ignored = ignored_tests or set()
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
    for testcase in root.iter("testcase"):
        classname = testcase.attrib.get("classname", "")
        name = testcase.attrib.get("name", "")
        full_name = normalize_test_name(f"{classname}.{name}" if classname else name)
        names.add(full_name)
        child_tags = {child.tag for child in testcase}
        if "failure" in child_tags:
            failed_names.add(full_name)
        if "error" in child_tags:
            error_names.add(full_name)
        if "skipped" in child_tags:
            skipped_names.add(full_name)
        if full_name not in ignored and len(failure_details) < 40:
            for child in testcase:
                if child.tag not in {"failure", "error"}:
                    continue
                detail = "\n".join(part for part in [child.attrib.get("message", ""), child.text or ""] if part)
                failure_details.append(
                    {
                        "test": full_name,
                        "kind": child.tag,
                        "detail": detail[-2000:],
                    }
                )
    if tests == 0 and names:
        tests = len(names)
    filtered_names = names - ignored
    filtered_failed = failed_names - ignored
    filtered_errors = error_names - ignored
    filtered_skipped = skipped_names - ignored
    return {
        "exists": True,
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "test_names_hash": stable_names_hash(names),
        "ignored_observed": len(names & ignored),
        "filtered_tests": len(filtered_names),
        "filtered_failures": len(filtered_failed),
        "filtered_errors": len(filtered_errors),
        "filtered_skipped": len(filtered_skipped),
        "filtered_failed_test_names": sorted(filtered_failed),
        "filtered_error_test_names": sorted(filtered_errors),
        "filtered_skipped_test_names": sorted(filtered_skipped),
        "failure_details": failure_details,
        "filtered_test_names_hash": stable_names_hash(filtered_names),
    }


def stable_names_hash(names: set[str]) -> str:
    import hashlib

    h = hashlib.sha256()
    for name in sorted(names):
        h.update(name.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def materialize_cleanroom_binary(instance_id: str, dest: Path, docker: str, logs_dir: Path) -> dict[str, Any]:
    image = f"{image_name_from_instance_id(instance_id)}:task_cleanroom_v6"
    inspect = run_command([docker, "image", "inspect", image], timeout=60, log_path=logs_dir / "docker_image_inspect.json")
    if inspect["returncode"] != 0:
        pull = run_command([docker, "pull", image], timeout=1800, log_path=logs_dir / "docker_pull_cleanroom.json")
        if pull["returncode"] != 0:
            return {"image": image, "returncode": pull["returncode"], "error": pull["stderr_tail"], "log_path": pull.get("log_path")}
    create = run_command([docker, "create", image], timeout=120, log_path=logs_dir / "docker_create_cleanroom.json")
    if create["returncode"] != 0:
        return {"image": image, "returncode": create["returncode"], "error": create["stderr_tail"]}
    container_id = create["stdout_tail"].strip().splitlines()[-1]
    try:
        cp = run_command([docker, "cp", f"{container_id}:/workspace/executable", str(dest)], timeout=300, log_path=logs_dir / "docker_cp_cleanroom_binary.json")
        if cp["returncode"] != 0:
            return {"image": image, "container_id": container_id, "returncode": cp["returncode"], "error": cp["stderr_tail"]}
        dest.chmod(dest.stat().st_mode | 0o555)
        return {"image": image, "container_id": container_id, "returncode": 0, "path": str(dest)}
    finally:
        run_command([docker, "rm", "-f", container_id], timeout=120, log_path=logs_dir / "docker_rm_cleanroom.json")


def prepare_gocoverdir_alias(target: Path) -> dict[str, Any]:
    """Make branch tests that hardcode /tmp/gocoverdir write into *target*."""
    alias = Path("/tmp/gocoverdir")
    target.mkdir(parents=True, exist_ok=True)
    if alias.exists() or alias.is_symlink():
        if alias.is_dir() and not alias.is_symlink():
            shutil.rmtree(alias)
        else:
            alias.unlink()
    alias.symlink_to(target, target_is_directory=True)
    return {"alias": str(alias), "target": str(target), "status": "linked"}


def install_pytest(venv_dir: Path, logs_dir: Path) -> Path:
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
    return python


def run_pytest_for_binary(
    *,
    python: Path,
    repo_dir: Path,
    executable_source: Path,
    label: str,
    branch: str,
    result_dir: Path,
    logs_dir: Path,
    timeout: int,
    xdist: str,
    gocoverdir: Path | None = None,
    ignored_tests: set[str] | None = None,
    workspace_alias: Path | None = None,
    deselected_tests: list[str] | None = None,
) -> dict[str, Any]:
    # ProgramBench executes each branch in a fresh container. Mirror that
    # isolation for every binary so cache files, SQLite databases, fixtures,
    # and pytest side effects cannot leak between cleanroom/source/coverage
    # runs or across branches.
    execution_dir = repo_dir.parent / "pytest_workspaces" / branch / label
    if execution_dir.exists():
        shutil.rmtree(execution_dir)
    shutil.copytree(
        repo_dir,
        execution_dir,
        ignore=shutil.ignore_patterns(".git", "coverage", "executable", "__pycache__", ".pytest_cache"),
    )
    executable = execution_dir / "executable"
    shutil.copy2(executable_source, executable)
    executable.chmod(executable.stat().st_mode | 0o555)

    junit = result_dir / f"{branch}.{label}.results.xml"
    timeout_method = "signal" if str(xdist).strip().lower() in {"0", "no", "false"} else "thread"
    cmd = [
        str(python),
        "-m",
        "pytest",
        "eval/tests/",
        f"--junitxml={junit}",
        "--timeout=5",
        f"--timeout-method={timeout_method}",
        "-n",
        xdist,
        "-v",
    ]
    for node_id in sorted(set(deselected_tests or [])):
        cmd.append(f"--deselect={node_id}")
    env = os.environ.copy()
    env["TZ"] = "UTC"
    if gocoverdir is not None:
        gocoverdir.mkdir(parents=True, exist_ok=True)
        env["GOCOVERDIR"] = str(gocoverdir)
    alias_executable: Path | None = None
    if workspace_alias is not None:
        if not workspace_alias.is_dir():
            raise FileNotFoundError(f"pytest workspace alias directory does not exist: {workspace_alias}")
        alias_executable = workspace_alias / "executable"
        if alias_executable.exists() or alias_executable.is_symlink():
            raise FileExistsError(f"refusing to replace existing workspace alias executable: {alias_executable}")
        alias_executable.symlink_to(executable)
    try:
        result = run_command(
            cmd,
            cwd=execution_dir,
            env=env,
            timeout=timeout,
            log_path=logs_dir / f"pytest_{branch}_{label}.json",
        )
    finally:
        if alias_executable is not None and alias_executable.is_symlink():
            alias_executable.unlink()
    return {
        "label": label,
        "branch": branch,
        "returncode": result["returncode"],
        "timed_out": result["timed_out"],
        "junit": str(junit),
        "junit_summary": parse_junit(junit, ignored_tests=ignored_tests),
        "log_path": result.get("log_path"),
        "execution_workspace": str(execution_dir),
        "workspace_alias": str(workspace_alias) if workspace_alias else None,
        "deselected_tests": sorted(set(deselected_tests or [])),
    }


def run_native_tests(repo_dir: Path, logs_dir: Path, coverpkg: str, go_executable: str = "go") -> dict[str, Any]:
    profile = repo_dir / "coverage" / "native_profile.txt"
    profile.parent.mkdir(parents=True, exist_ok=True)
    test = run_command(
        [go_executable, "test", f"-coverpkg={coverpkg}", f"-coverprofile={profile}", "./..."],
        cwd=repo_dir,
        timeout=1800,
        log_path=logs_dir / "go_native_tests.json",
    )
    cover = run_command(
        [go_executable, "tool", "cover", "-func", str(profile)],
        cwd=repo_dir,
        timeout=300,
        log_path=logs_dir / "go_native_cover_func.json",
    )
    return {
        "go_test_returncode": test["returncode"],
        "go_test_package_coverage_percent": parse_go_test_package_coverage(test["stdout_tail"]),
        "cover_func_returncode": cover["returncode"],
        "statement_coverage_percent": parse_total_statement_coverage(cover["stdout_tail"]),
        **parse_cover_profile_line_coverage(profile),
        "profile": str(profile),
        "logs": {
            "go_test": test.get("log_path"),
            "go_cover_func": cover.get("log_path"),
        },
    }


def compare_binary_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    summaries = [item["junit_summary"] for item in results]
    all_returncode_zero = all(item["returncode"] == 0 for item in results)
    comparable = all(summary.get("exists") and not summary.get("parse_error") for summary in summaries)
    same_tests = len({summary.get("filtered_tests") for summary in summaries}) == 1 if comparable else False
    same_names = len({summary.get("filtered_test_names_hash") for summary in summaries}) == 1 if comparable else False
    same_failures = len(
        {
            (
                summary.get("filtered_failures"),
                summary.get("filtered_errors"),
                summary.get("filtered_skipped"),
            )
            for summary in summaries
        }
    ) == 1 if comparable else False
    all_filtered_passed = all(
        summary.get("filtered_failures") == 0
        and summary.get("filtered_errors") == 0
        and summary.get("filtered_skipped") == 0
        for summary in summaries
    ) if comparable else False
    return {
        "all_returncode_zero": all_returncode_zero,
        "all_filtered_passed": all_filtered_passed,
        "all_junit_parseable": comparable,
        "same_filtered_test_count": same_tests,
        "same_filtered_test_names_hash": same_names,
        "same_filtered_failure_error_skip_counts": same_failures,
        "behavior_consistent": comparable and same_tests and same_names and same_failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("instance_id")
    parser.add_argument("--tasks-root", type=Path, default=DEFAULT_TASKS_ROOT)
    parser.add_argument("--blob-dir", type=Path)
    parser.add_argument("--branch", default="first-active", help="first-active, all, or comma-separated active branch ids")
    parser.add_argument(
        "--oracle-material-root",
        type=Path,
        help="Run a generated/custom oracle bundle root containing eval/ instead of official ProgramBench blobs.",
    )
    parser.add_argument("--suite-label", help="Label used for custom/generated suite reports")
    parser.add_argument("--work-root", type=Path, default=Path("/tmp/programbench_source_coverage"))
    parser.add_argument("--output-root", type=Path, default=Path("reports/programbench_source_coverage"))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--run-native-tests", action="store_true")
    parser.add_argument("--compare-binaries", action="store_true")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--xdist", default="auto")
    parser.add_argument("--pytest-timeout", type=int, default=1800)
    parser.add_argument(
        "--workspace-alias",
        type=Path,
        help="Optional pre-created directory used for tests that hard-code /workspace/executable.",
    )
    parser.add_argument(
        "--deselect-test",
        action="append",
        default=[],
        help="Exact pytest node id to remove before execution and coverage; repeatable and recorded per binary.",
    )
    parser.add_argument(
        "--pytest-python",
        type=Path,
        help="Existing Python with pytest/pytest-timeout/pytest-xdist; defaults to the current interpreter when usable.",
    )
    parser.add_argument(
        "--go-build-package",
        default="auto",
        help="Go package to build as executable, or auto to discover a main package.",
    )
    parser.add_argument("--go-executable", default="go", help="Primary Go command or absolute executable path.")
    parser.add_argument(
        "--fallback-go-executable",
        action="append",
        default=[],
        help="Fallback Go executable for pinned repositories incompatible with the primary toolchain.",
    )
    parser.add_argument("--go-coverpkg", default="./...", help="Value for go build/test -coverpkg.")
    args = parser.parse_args()

    tasks_root = args.tasks_root if args.tasks_root.is_absolute() else (REPO_ROOT / args.tasks_root).resolve()
    output_root = args.output_root if args.output_root.is_absolute() else (REPO_ROOT / args.output_root).resolve()
    task_dir = tasks_root / args.instance_id
    meta = parse_simple_yaml(task_dir / "task.yaml")
    if str(meta.get("language", "")).lower() not in {"go", "golang"}:
        raise ValueError(f"{args.instance_id} is not a Go task: {meta.get('language')}")

    all_active = active_branches(task_dir)
    test_metadata = branch_test_metadata(task_dir)
    if not all_active:
        raise ValueError(f"{args.instance_id} has no active branches")

    oracle_material_root = resolve_oracle_material_root(args.oracle_material_root) if args.oracle_material_root else None
    suite_kind = "generated" if oracle_material_root else "official"
    if oracle_material_root:
        run_label = safe_run_label(args.suite_label or oracle_material_root.parent.name or "generated_oracle")
        selected_branches = [run_label]
    else:
        selected_branches = select_branches(args.branch, all_active)
        run_label = "all_active" if selected_branches == all_active else "_".join(selected_branches)

    safe_instance = args.instance_id.replace("/", "_")
    work_dir = args.work_root / safe_instance / run_label
    if work_dir.exists():
        if not args.overwrite:
            raise FileExistsError(f"Work dir exists: {work_dir}")
        shutil.rmtree(work_dir)
    repo_dir = work_dir / "source"
    branch_root = work_dir / "branches"
    logs_dir = work_dir / "logs"
    result_dir = repo_dir / "coverage" / "junit"
    suite_cov_dir = repo_dir / "coverage" / f"{run_label}_merged_raw"
    logs_dir.mkdir(parents=True, exist_ok=True)

    repository = str(meta["repository"])
    commit = str(meta["commit"])
    blob_dir = None if oracle_material_root else find_blob_dir(args.instance_id, args.blob_dir)
    clone_url = f"https://github.com/{repository}.git"

    clone = run_command(["git", "clone", clone_url, str(repo_dir)], timeout=900, log_path=logs_dir / "git_clone.json")
    if clone["returncode"] != 0:
        raise RuntimeError(f"git clone failed: {clone['stderr_tail']}")
    checkout = run_command(["git", "checkout", commit], cwd=repo_dir, timeout=120, log_path=logs_dir / "git_checkout.json")
    if checkout["returncode"] != 0:
        raise RuntimeError(f"git checkout failed: {checkout['stderr_tail']}")

    toolchain_executables = [args.go_executable, *args.fallback_go_executable]
    legacy_go = Path("/usr/local/go1.21.13/bin/go")
    if legacy_go.exists() and str(legacy_go) not in toolchain_executables:
        toolchain_executables.append(str(legacy_go))

    mod_download = run_command(
        [args.go_executable, "mod", "download"],
        cwd=repo_dir,
        timeout=900,
        log_path=logs_dir / "go_mod_download.json",
    )
    if mod_download["returncode"] != 0:
        raise RuntimeError(f"go mod download failed: {mod_download['stderr_tail']}")

    selected_toolchain = run_command(
        [args.go_executable, "version"],
        cwd=repo_dir,
        timeout=300,
        log_path=logs_dir / "go_selected_toolchain.json",
        include_output=True,
    )
    toolchain_env = run_command(
        [args.go_executable, "env", "GOTOOLCHAIN", "GOVERSION", "GOROOT"],
        cwd=repo_dir,
        timeout=300,
        log_path=logs_dir / "go_toolchain_env.json",
        include_output=True,
    )

    go_build_package = select_go_build_package(
        repo_dir, args.go_build_package, repository, logs_dir, args.go_executable
    )
    source_binary = work_dir / "executable_source"
    coverage_binary = work_dir / "executable_coverage"
    if go_build_package.get("mode") == "explicit":
        build_targets = [str(go_build_package["selected"])]
    else:
        build_targets = [str(item["target"]) for item in go_build_package.get("candidates") or []]
        if "." not in build_targets:
            build_targets.append(".")
    candidate_dirs = {
        str(item["target"]): Path(str(item["dir"])).resolve()
        for item in go_build_package.get("candidates") or []
        if item.get("dir")
    }

    def build_context(target: str) -> tuple[Path, str]:
        candidate_dir = candidate_dirs.get(target)
        if candidate_dir is None:
            candidate_dir = repo_dir if target == "." else (repo_dir / target.removeprefix("./")).resolve()
        module_root = candidate_dir
        while module_root != repo_dir and not (module_root / "go.mod").exists():
            module_root = module_root.parent
        if not (module_root / "go.mod").exists():
            module_root = repo_dir
        if module_root == repo_dir:
            return repo_dir, target
        relative_target = candidate_dir.relative_to(module_root)
        nested_target = "." if str(relative_target) == "." else "./" + relative_target.as_posix()
        return module_root, nested_target

    build_attempts: list[dict[str, Any]] = []
    build_source: dict[str, Any] | None = None
    build_coverage: dict[str, Any] | None = None
    go_build_target: str | None = None
    go_build_cwd: Path | None = None
    go_build_command_target: str | None = None
    selected_go_executable: str | None = None
    attempt_index = 0
    for target in build_targets:
        candidate_cwd, candidate_target = build_context(target)
        label = re.sub(r"[^A-Za-z0-9_.-]+", "_", target).strip("_") or "root"
        for go_executable in toolchain_executables:
            attempt_index += 1
            source_binary.unlink(missing_ok=True)
            coverage_binary.unlink(missing_ok=True)
            candidate_source = run_command(
                [go_executable, "build", "-o", str(source_binary), candidate_target],
                cwd=candidate_cwd,
                timeout=900,
                log_path=logs_dir / f"go_build_source_{attempt_index:02d}_{label}.json",
            )
            attempt: dict[str, Any] = {
                "target": target,
                "go_executable": go_executable,
                "build_cwd": str(candidate_cwd),
                "build_target": candidate_target,
                "source": candidate_source,
            }
            if candidate_source["returncode"] != 0:
                build_attempts.append(attempt)
                continue
            candidate_coverage = run_command(
                [
                    go_executable,
                    "build",
                    "-cover",
                    f"-coverpkg={args.go_coverpkg}",
                    "-o",
                    str(coverage_binary),
                    candidate_target,
                ],
                cwd=candidate_cwd,
                timeout=900,
                log_path=logs_dir / f"go_build_cover_{attempt_index:02d}_{label}.json",
            )
            attempt["coverage"] = candidate_coverage
            build_attempts.append(attempt)
            if candidate_coverage["returncode"] == 0:
                go_build_target = target
                go_build_cwd = candidate_cwd
                go_build_command_target = candidate_target
                selected_go_executable = go_executable
                build_source = candidate_source
                build_coverage = candidate_coverage
                break
        if selected_go_executable is not None:
            break
    go_build_package["attempts"] = build_attempts
    go_build_package["selected"] = go_build_target
    go_build_package["selected_build_cwd"] = str(go_build_cwd) if go_build_cwd else None
    go_build_package["selected_build_target"] = go_build_command_target
    go_build_package["selected_go_executable"] = selected_go_executable
    if (
        go_build_target is None
        or go_build_cwd is None
        or selected_go_executable is None
        or build_source is None
        or build_coverage is None
    ):
        final_attempt = build_attempts[-1] if build_attempts else {}
        details = final_attempt.get("coverage") or final_attempt.get("source") or {}
        raise RuntimeError(f"no discovered Go command built successfully: {details.get('stderr_tail', final_attempt)}")

    selected_toolchain = run_command(
        [selected_go_executable, "version"],
        cwd=go_build_cwd,
        timeout=300,
        log_path=logs_dir / "go_selected_build_toolchain.json",
        include_output=True,
    )
    toolchain_env = run_command(
        [selected_go_executable, "env", "GOTOOLCHAIN", "GOVERSION", "GOROOT"],
        cwd=go_build_cwd,
        timeout=300,
        log_path=logs_dir / "go_selected_build_toolchain_env.json",
        include_output=True,
    )
    native = (
        run_native_tests(go_build_cwd, logs_dir, args.go_coverpkg, selected_go_executable)
        if args.run_native_tests
        else None
    )

    cleanroom_binary = work_dir / "executable_cleanroom"
    cleanroom = None
    if args.compare_binaries:
        cleanroom = materialize_cleanroom_binary(args.instance_id, cleanroom_binary, args.docker, logs_dir)
        if cleanroom["returncode"] != 0:
            raise RuntimeError(f"cleanroom binary materialization failed: {cleanroom}")

    if args.pytest_python:
        python = args.pytest_python.expanduser().absolute()
        if not python.exists():
            raise FileNotFoundError(f"pytest interpreter does not exist: {python}")
    else:
        dependency_check = run_command(
            [sys.executable, "-c", "import pytest, pytest_timeout, xdist"],
            timeout=60,
            log_path=logs_dir / "pytest_dependency_check.json",
        )
        # Preserve a virtualenv symlink. Path.resolve() can jump to uv's base
        # interpreter and silently lose the venv site-packages.
        python = Path(sys.executable).absolute() if dependency_check["returncode"] == 0 else install_pytest(work_dir / ".venv", logs_dir)
    gocoverdir_alias = prepare_gocoverdir_alias(suite_cov_dir)
    branch_results: list[dict[str, Any]] = []
    for branch in selected_branches:
        if oracle_material_root:
            branch_meta = {
                "ignored": set(),
                "expected_count": None,
                "active_count": None,
                "ignored_count": 0,
            }
            ignored_tests: set[str] = set()
            branch_tar = None
            extract_dir = oracle_material_root
        else:
            branch_meta = test_metadata[branch]
            ignored_tests = branch_meta["ignored"]
            assert blob_dir is not None
            branch_tar = blob_dir / "tests" / f"{branch}.tar.gz"
            if not branch_tar.exists():
                raise FileNotFoundError(f"Missing branch tarball: {branch_tar}")
            extract_dir = branch_root / branch / "blob"
            safe_extract(branch_tar, extract_dir)
        copied_material = copy_oracle_material(extract_dir, repo_dir)

        binary_results: list[dict[str, Any]] = []
        if args.compare_binaries:
            binary_results.append(
                run_pytest_for_binary(
                    python=python,
                    repo_dir=repo_dir,
                    executable_source=cleanroom_binary,
                    label="cleanroom",
                    branch=branch,
                    result_dir=result_dir,
                    logs_dir=logs_dir,
                    timeout=args.pytest_timeout,
                    xdist=args.xdist,
                    ignored_tests=ignored_tests,
                    workspace_alias=args.workspace_alias,
                    deselected_tests=args.deselect_test,
                )
            )
            binary_results.append(
                run_pytest_for_binary(
                    python=python,
                    repo_dir=repo_dir,
                    executable_source=source_binary,
                    label="source",
                    branch=branch,
                    result_dir=result_dir,
                    logs_dir=logs_dir,
                    timeout=args.pytest_timeout,
                    xdist=args.xdist,
                    ignored_tests=ignored_tests,
                    workspace_alias=args.workspace_alias,
                    deselected_tests=args.deselect_test,
                )
            )
        coverage_result = run_pytest_for_binary(
            python=python,
            repo_dir=repo_dir,
            executable_source=coverage_binary,
            label="coverage",
            branch=branch,
            result_dir=result_dir,
            logs_dir=logs_dir,
            timeout=args.pytest_timeout,
            xdist=args.xdist,
            gocoverdir=suite_cov_dir,
            ignored_tests=ignored_tests,
            workspace_alias=args.workspace_alias,
            deselected_tests=args.deselect_test,
        )
        binary_results.append(coverage_result)
        branch_result = {
            "branch": branch,
            "tests_json": {
                "expected_count": branch_meta["expected_count"],
                "active_count": branch_meta["active_count"],
                "ignored_count": branch_meta["ignored_count"],
            },
            "copied_oracle_material": copied_material,
            "binary_results": binary_results,
            "comparison": compare_binary_results(binary_results),
        }
        if branch_tar is not None:
            branch_result["branch_tar"] = str(branch_tar)
        if oracle_material_root is not None:
            branch_result["oracle_material_root"] = str(oracle_material_root)
        branch_results.append(branch_result)

    suite_profile = repo_dir / "coverage" / f"{run_label}_merged_profile.txt"
    cov_percent = run_command(
        [selected_go_executable, "tool", "covdata", "percent", "-i", str(suite_cov_dir)],
        cwd=repo_dir,
        timeout=300,
        log_path=logs_dir / f"go_{run_label}_covdata_percent.json",
    )
    cov_textfmt = run_command(
        [selected_go_executable, "tool", "covdata", "textfmt", "-i", str(suite_cov_dir), "-o", str(suite_profile)],
        cwd=repo_dir,
        timeout=300,
        log_path=logs_dir / f"go_{run_label}_covdata_textfmt.json",
    )
    cover_func = run_command(
        [selected_go_executable, "tool", "cover", "-func", str(suite_profile)],
        cwd=repo_dir,
        timeout=300,
        log_path=logs_dir / f"go_{run_label}_cover_func.json",
    )
    statement_coverage = parse_total_statement_coverage(cover_func["stdout_tail"])
    coverage_percent_source = "go_tool_cover_func"
    if statement_coverage is None:
        statement_coverage = parse_cover_profile_statement_coverage(suite_profile)
        coverage_percent_source = "cover_profile_statement_blocks"

    all_branch_comparisons_ok = all(item["comparison"]["behavior_consistent"] for item in branch_results)
    all_coverage_pytests_ok = all(
        result["junit_summary"].get("filtered_failures") == 0
        and result["junit_summary"].get("filtered_errors") == 0
        and result["junit_summary"].get("filtered_skipped") == 0
        for item in branch_results
        for result in item["binary_results"]
        if result["label"] == "coverage"
    )
    test_suite_summary = {
        "kind": suite_kind,
        "label": run_label,
        "coverage_metric": "go_statement_coverage",
        "pytest_all_coverage_runs_passed": all_coverage_pytests_ok,
        "suite_count": len(selected_branches),
        "statement_coverage_percent": statement_coverage,
        **parse_cover_profile_line_coverage(suite_profile),
        "coverage_percent_source": coverage_percent_source,
        "covdata_percent_returncode": cov_percent["returncode"],
        "covdata_textfmt_returncode": cov_textfmt["returncode"],
        "cover_func_returncode": cover_func["returncode"],
        "profile": str(suite_profile),
    }
    summary = {
        "instance_id": args.instance_id,
        "repository": repository,
        "commit": commit,
        "language": meta.get("language"),
        "go_toolchain": {
            "selected_version": selected_toolchain.get("stdout", "").strip(),
            "version_returncode": selected_toolchain.get("returncode"),
            "environment": toolchain_env.get("stdout", "").splitlines(),
            "environment_returncode": toolchain_env.get("returncode"),
        },
        "go_build_package": go_build_package,
        "go_coverpkg": args.go_coverpkg,
        "selected_branches": selected_branches,
        "active_branch_count": len(all_active),
        "work_dir": str(work_dir),
        "compare_binaries": args.compare_binaries,
        "pytest_python": str(python),
        "cleanroom_binary": cleanroom,
        "gocoverdir_alias": gocoverdir_alias,
        "test_suite": test_suite_summary,
        "native_tests": native,
        "branch_results": branch_results,
        "all_branch_binary_comparisons_consistent": all_branch_comparisons_ok,
        "logs_dir": str(logs_dir),
    }
    if suite_kind == "official":
        summary["blob_dir"] = str(blob_dir)
        summary["official_tests"] = {**test_suite_summary, "branch_count": len(selected_branches)}
    else:
        summary["oracle_material_root"] = str(oracle_material_root)
        summary["generated_tests"] = test_suite_summary

    out_dir = output_root / args.instance_id
    json_path = out_dir / f"{run_label}.go_coverage_summary.json"
    md_path = out_dir / f"{run_label}.go_coverage_summary.md"
    write_json(json_path, summary)
    suite_display = "Official-test" if suite_kind == "official" else "Generated-test"
    md_lines = [
        f"# Go Coverage Run: `{args.instance_id}`",
        "",
        f"- Repository: `{repository}`",
        f"- Commit: `{commit}`",
        f"- Suite kind: `{suite_kind}`",
        f"- Suite label: `{run_label}`",
        f"- Branches/suites: `{', '.join(selected_branches)}`",
        f"- {suite_display} coverage metric: `{test_suite_summary['coverage_metric']}`",
        f"- {suite_display} statement coverage: `{test_suite_summary['statement_coverage_percent']}`",
        f"- {suite_display} executable-line coverage: `{test_suite_summary['line_coverage_percent']}`",
        f"- Native-test statement coverage: `{native['statement_coverage_percent'] if native else None}`",
        f"- Native-test executable-line coverage: `{native['line_coverage_percent'] if native else None}`",
        f"- Coverage pytest runs passed: `{all_coverage_pytests_ok}`",
        f"- Binary comparisons consistent: `{all_branch_comparisons_ok}`",
        f"- Work dir: `{work_dir}`",
        f"- Logs dir: `{logs_dir}`",
        "",
    ]
    for item in branch_results:
        labels = ", ".join(f"{r['label']}={r['returncode']}" for r in item["binary_results"])
        tests = ", ".join(f"{r['label']}:{r['junit_summary'].get('tests')}" for r in item["binary_results"])
        md_lines.append(
            f"- `{item['branch']}`: returns `{labels}`, tests `{tests}`, "
            f"filtered pass `{item['comparison']['all_filtered_passed']}`, "
            f"consistent `{item['comparison']['behavior_consistent']}`"
        )
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    coverage_summary_ok = statement_coverage is not None and cov_textfmt["returncode"] == 0
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if all_coverage_pytests_ok and coverage_summary_ok and all_branch_comparisons_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
