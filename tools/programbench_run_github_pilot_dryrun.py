#!/usr/bin/env python3
"""Run a Mac-native clone/build/test dry run for external Gym candidates."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK_ROOT = Path("/private/tmp/programbench_gym_external_pilot5_dryrun")
DEFAULT_OUTPUT_ROOT = Path("reports/programbench_gym_external_pilot5_dryrun")
SCHEMA_VERSION = "0.1"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def resolve_repo_path(path: Path) -> Path:
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def slug_for_repo(repository: str, commit: str) -> str:
    owner, name = repository.split("/", 1)
    owner_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", owner).strip("-")
    name_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-")
    return f"{owner_slug}__{name_slug}.{commit[:7]}"


def short_text(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def command_available(name: str) -> bool:
    return shutil.which(name) is not None


def required_tools(command: str) -> list[str]:
    tools: list[str] = []
    for token in re.split(r"\s+|&&|\|\|", command.strip()):
        if not token or token.startswith("-"):
            continue
        if token in {"go", "cargo", "cmake", "make", "ctest", "python", "python3"} and token not in tools:
            tools.append(token)
    return tools


def run_command(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
    log_path: Path | None = None,
) -> dict[str, Any]:
    started = dt.datetime.now(dt.UTC)
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
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
        write_json(log_path, {**payload, "stdout": stdout, "stderr": stderr})
        payload["log_path"] = str(log_path)
    return payload


def download_url(url: str, dest: Path, *, timeout: int, log_path: Path) -> dict[str, Any]:
    started = dt.datetime.now(dt.UTC)
    bytes_written = 0
    status = "pass"
    error = None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=timeout) as response, dest.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                bytes_written += len(chunk)
        returncode = 0
    except Exception as exc:  # noqa: BLE001 - report the concrete fetch failure.
        status = "fail"
        error = repr(exc)
        returncode = 1
    ended = dt.datetime.now(dt.UTC)
    payload = {
        "url": url,
        "dest": str(dest),
        "returncode": returncode,
        "status": status,
        "error": error,
        "bytes_written": bytes_written,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_seconds": (ended - started).total_seconds(),
    }
    write_json(log_path, payload)
    return payload


def download_github_archive(candidate: dict[str, Any], dest: Path, *, timeout: int, log_path: Path) -> dict[str, Any]:
    endpoint = f"repos/{candidate['repository']}/tarball/{candidate['commit']}"
    started = dt.datetime.now(dt.UTC)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as handle:
        try:
            proc = subprocess.run(
                ["gh", "api", endpoint],
                stdout=handle,
                stderr=subprocess.PIPE,
                timeout=timeout,
                text=False,
            )
            returncode = proc.returncode
            stderr = proc.stderr.decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired as exc:
            returncode = 124
            stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr)
    ended = dt.datetime.now(dt.UTC)
    payload = {
        "endpoint": endpoint,
        "dest": str(dest),
        "returncode": returncode,
        "status": "pass" if returncode == 0 and dest.stat().st_size > 0 else "fail",
        "bytes_written": dest.stat().st_size if dest.exists() else 0,
        "stderr_tail": short_text(stderr),
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_seconds": (ended - started).total_seconds(),
    }
    write_json(log_path, payload)
    return payload


def safe_extract_archive(archive: Path, dest: Path) -> Path:
    extract_root = dest.parent / (dest.name + "_extract")
    if extract_root.exists():
        shutil.rmtree(extract_root)
    extract_root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        root = extract_root.resolve()
        for member in tar.getmembers():
            target = (extract_root / member.name).resolve()
            if not str(target).startswith(str(root) + os.sep):
                raise RuntimeError(f"unsafe archive path: {member.name}")
        tar.extractall(extract_root)
    children = [path for path in extract_root.iterdir() if path.is_dir()]
    if len(children) != 1:
        raise RuntimeError(f"expected one archive root in {archive}, found {len(children)}")
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(children[0]), str(dest))
    shutil.rmtree(extract_root)
    return dest


def shell_command(
    command: str,
    *,
    cwd: Path,
    timeout: int,
    log_path: Path,
) -> dict[str, Any]:
    return run_command(["bash", "-lc", command], cwd=cwd, timeout=timeout, log_path=log_path)


def clone_or_update(candidate: dict[str, Any], repo_dir: Path, log_dir: Path, timeout: int) -> dict[str, Any]:
    if repo_dir.exists():
        shutil.rmtree(repo_dir)
    archive = log_dir / "source_archive.tar.gz"
    archive_url = f"https://codeload.github.com/{candidate['repository']}/tar.gz/{candidate['commit']}"
    download = (
        download_github_archive(candidate, archive, timeout=timeout, log_path=log_dir / "download_archive_gh_api.json")
        if command_available("gh")
        else {"returncode": 1, "status": "skipped", "reason": "gh not available"}
    )
    if download["returncode"] != 0:
        download = download_url(archive_url, archive, timeout=timeout, log_path=log_dir / "download_archive_url.json")
    if download["returncode"] == 0:
        try:
            safe_extract_archive(archive, repo_dir)
            return {
                "status": "pass",
                "method": "github_archive",
                "archive_url": archive_url,
                "download": download,
                "head": candidate["commit"],
            }
        except Exception as exc:  # noqa: BLE001 - keep dry-run moving and report archive extraction issues.
            return {
                "status": "fail",
                "method": "github_archive",
                "archive_url": archive_url,
                "download": download,
                "extract_error": repr(exc),
                "head": None,
            }

    repo_dir.mkdir(parents=True, exist_ok=True)
    init = run_command(["git", "init"], cwd=repo_dir, timeout=30, log_path=log_dir / "git_init.json")
    if init["returncode"] != 0:
        return {"status": "fail", "init": init, "fetch": None, "checkout": None, "head": None}
    remote = run_command(
        ["git", "remote", "add", "origin", candidate["clone_url"]],
        cwd=repo_dir,
        timeout=30,
        log_path=log_dir / "git_remote_add.json",
    )
    if remote["returncode"] != 0:
        return {"status": "fail", "init": init, "remote": remote, "fetch": None, "checkout": None, "head": None}
    fetch = run_command(
        ["git", "fetch", "--depth", "1", "--no-tags", "origin", candidate["commit"]],
        cwd=repo_dir,
        timeout=timeout,
        log_path=log_dir / "git_fetch_commit.json",
    )
    if fetch["returncode"] != 0:
        return {"status": "fail", "init": init, "remote": remote, "fetch": fetch, "checkout": None, "head": None}

    checkout = run_command(
        ["git", "checkout", "--detach", "FETCH_HEAD"],
        cwd=repo_dir,
        timeout=120,
        log_path=log_dir / "checkout.json",
    )
    head = run_command(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir,
        timeout=30,
        log_path=log_dir / "rev_parse_head.json",
    )
    head_sha = head.get("stdout_tail", "").strip()
    status = "pass" if checkout["returncode"] == 0 and head_sha == candidate["commit"] else "fail"
    return {"status": status, "init": init, "remote": remote, "fetch": fetch, "checkout": checkout, "head": head_sha}


def docs_manifest(repo_dir: Path) -> dict[str, Any]:
    docs: list[str] = []
    for child in sorted(repo_dir.iterdir()):
        lower = child.name.lower()
        if child.is_file() and (lower.startswith("readme") or lower in {"license", "copying", "changelog.md"}):
            docs.append(child.name)
        elif child.is_dir() and lower in {"docs", "doc", "examples", "example"}:
            docs.append(child.name + "/")
    return {"status": "pass" if any(name.lower().startswith("readme") for name in docs) else "fail", "paths": docs}


def inferred_test_command(repo_dir: Path) -> str | None:
    if (repo_dir / "go.mod").exists():
        return "go test ./..."
    if (repo_dir / "Cargo.toml").exists():
        return "cargo test --release"
    if (repo_dir / "build" / "CTestTestfile.cmake").exists():
        return "ctest --test-dir build --output-on-failure"
    for makefile in ("Makefile", "makefile", "GNUmakefile"):
        path = repo_dir / makefile
        if path.exists() and re.search(r"^test\s*:", path.read_text(encoding="utf-8", errors="replace"), re.M):
            return "make test"
    return None


def executable_candidates(repo_dir: Path, repository: str) -> list[Path]:
    repo_name = repository.rsplit("/", 1)[1]
    common_names = {
        repo_name,
        repo_name.lower(),
        repo_name.replace("-", ""),
        repo_name.replace("_", ""),
        "executable",
        "dasel",
        "xan",
        "hl",
        "krep",
        "astcenc",
        "astcenc-avx2",
        "astcenc-sse2",
    }
    candidates: list[Path] = []
    preferred_dirs = [
        repo_dir,
        repo_dir / "target" / "release",
        repo_dir / "build",
        repo_dir / "build" / "Source",
        repo_dir / "bin",
    ]
    for base in preferred_dirs:
        if not base.exists():
            continue
        for name in common_names:
            path = base / name
            if path.is_file() and os.access(path, os.X_OK):
                candidates.append(path)

    skip_dirs = {".git", ".github", ".cargo", "target/debug", "target/debug/build"}
    for path in repo_dir.rglob("*"):
        rel = path.relative_to(repo_dir)
        rel_text = str(rel)
        if any(rel_text == item or rel_text.startswith(item + os.sep) for item in skip_dirs):
            continue
        if not path.is_file() or not os.access(path, os.X_OK):
            continue
        if path.suffix in {".a", ".dylib", ".o", ".rlib", ".so"}:
            continue
        if path.stat().st_size > 150 * 1024 * 1024:
            continue
        if path.name in common_names or "release" in rel.parts or rel.parts[:1] == ("build",):
            candidates.append(path)

    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            deduped.append(path)
    return deduped[:12]


def smoke_executable(candidates: list[Path], log_dir: Path, timeout: int) -> dict[str, Any]:
    if not candidates:
        return {"status": "fail", "reason": "no executable candidates found", "attempts": []}
    attempts: list[dict[str, Any]] = []
    for executable in candidates[:6]:
        for args in (["--help"], ["-h"], []):
            label = executable.name + ("_" + "_".join(arg.strip("-") for arg in args) if args else "_noargs")
            result = run_command(
                [str(executable), *args],
                cwd=executable.parent,
                timeout=timeout,
                log_path=log_dir / f"smoke_{re.sub(r'[^A-Za-z0-9_.-]+', '_', label)}.json",
            )
            attempts.append(
                {
                    "executable": str(executable),
                    "args": args,
                    "returncode": result["returncode"],
                    "timed_out": result["timed_out"],
                    "stdout_tail": result["stdout_tail"][:500],
                    "stderr_tail": result["stderr_tail"][:500],
                }
            )
            if not result["timed_out"]:
                return {"status": "pass", "selected_executable": str(executable), "attempts": attempts}
    return {"status": "fail", "reason": "all smoke attempts timed out", "attempts": attempts}


def run_candidate(
    candidate: dict[str, Any],
    *,
    work_root: Path,
    output_root: Path,
    clone_timeout: int,
    build_timeout: int,
    test_timeout: int,
    smoke_timeout: int,
) -> dict[str, Any]:
    instance_id = slug_for_repo(candidate["repository"], candidate["commit"])
    repo_dir = work_root / instance_id / "repo"
    log_dir = output_root / instance_id / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    clone = clone_or_update(candidate, repo_dir, log_dir, clone_timeout)
    docs = docs_manifest(repo_dir) if clone["status"] == "pass" else {"status": "skipped", "paths": []}

    build_command = candidate.get("build_command_hint") or ""
    missing_tools = [tool for tool in required_tools(build_command) if not command_available(tool)]
    if clone["status"] != "pass":
        build = {"status": "skipped", "reason": "clone or checkout failed", "missing_tools": missing_tools}
    elif missing_tools:
        build = {"status": "fail", "reason": "missing local tools", "missing_tools": missing_tools}
    else:
        result = shell_command(build_command, cwd=repo_dir, timeout=build_timeout, log_path=log_dir / "build.json")
        build = {"status": "pass" if result["returncode"] == 0 else "fail", "command": build_command, "result": result}

    candidates = executable_candidates(repo_dir, candidate["repository"]) if clone["status"] == "pass" else []
    smoke = smoke_executable(candidates, log_dir, smoke_timeout) if build.get("status") == "pass" else {
        "status": "skipped",
        "reason": "build did not pass",
        "executable_candidates": [str(path) for path in candidates],
    }

    test_command = inferred_test_command(repo_dir) if clone["status"] == "pass" else None
    if build.get("status") != "pass":
        tests = {"status": "skipped", "reason": "build did not pass", "command": test_command}
    elif not test_command:
        tests = {"status": "skipped", "reason": "no obvious native test command inferred", "command": None}
    else:
        test_missing = [tool for tool in required_tools(test_command) if not command_available(tool)]
        if test_missing:
            tests = {"status": "fail", "reason": "missing local tools", "command": test_command, "missing_tools": test_missing}
        else:
            result = shell_command(test_command, cwd=repo_dir, timeout=test_timeout, log_path=log_dir / "tests.json")
            tests = {"status": "pass" if result["returncode"] == 0 else "fail", "command": test_command, "result": result}

    gate_names = {
        "clone_pinned_commit": clone["status"],
        "readme_or_docs_found": docs["status"],
        "mac_native_build": build["status"],
        "reference_binary_smoke": smoke["status"],
        "native_existing_tests": tests["status"],
    }
    dryrun_passed = all(status == "pass" for status in gate_names.values())
    row = {
        "instance_id": instance_id,
        "repository": candidate["repository"],
        "commit": candidate["commit"],
        "language": candidate.get("language"),
        "build_command_hint": build_command,
        "dryrun_passed": dryrun_passed,
        "gates": gate_names,
        "clone": clone,
        "docs": docs,
        "build": build,
        "executable_candidates": [str(path) for path in candidates],
        "smoke": smoke,
        "tests": tests,
        "worktree": str(repo_dir),
    }
    write_json(output_root / instance_id / "dryrun_report.json", row)
    return row


def write_summary(output_root: Path, rows: list[dict[str, Any]]) -> None:
    aggregate = {
        "instances": len(rows),
        "clone_pinned_commit": sum(1 for row in rows if row["gates"]["clone_pinned_commit"] == "pass"),
        "readme_or_docs_found": sum(1 for row in rows if row["gates"]["readme_or_docs_found"] == "pass"),
        "mac_native_build": sum(1 for row in rows if row["gates"]["mac_native_build"] == "pass"),
        "reference_binary_smoke": sum(1 for row in rows if row["gates"]["reference_binary_smoke"] == "pass"),
        "native_existing_tests": sum(1 for row in rows if row["gates"]["native_existing_tests"] == "pass"),
        "dryrun_passed": sum(1 for row in rows if row["dryrun_passed"]),
    }
    summary = {
        "schema_version": SCHEMA_VERSION,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "output_root": str(output_root),
        "aggregate": aggregate,
        "instances": [
            {
                "instance_id": row["instance_id"],
                "repository": row["repository"],
                "language": row["language"],
                "dryrun_passed": row["dryrun_passed"],
                "gates": row["gates"],
            }
            for row in rows
        ],
    }
    write_json(output_root / "dryrun_summary.json", summary)

    lines = [
        "# External GitHub Pilot5 Mac-Native Dry Run",
        "",
        f"- Created: `{summary['created_at']}`",
        f"- Dry run passed: {aggregate['dryrun_passed']}/{aggregate['instances']}",
        f"- Clone pinned commit: {aggregate['clone_pinned_commit']}/{aggregate['instances']}",
        f"- Mac-native build: {aggregate['mac_native_build']}/{aggregate['instances']}",
        f"- Reference smoke: {aggregate['reference_binary_smoke']}/{aggregate['instances']}",
        f"- Native existing tests: {aggregate['native_existing_tests']}/{aggregate['instances']}",
        "",
        "| instance | repo | lang | clone | docs | build | smoke | tests | dryrun |",
        "| --- | --- | ---: | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        gates = row["gates"]
        lines.append(
            f"| `{row['instance_id']}` | `{row['repository']}` | {row['language']} | "
            f"{gates['clone_pinned_commit']} | {gates['readme_or_docs_found']} | "
            f"{gates['mac_native_build']} | {gates['reference_binary_smoke']} | "
            f"{gates['native_existing_tests']} | {row['dryrun_passed']} |"
        )
    lines.append("")
    (output_root / "dryrun_summary.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/programbench_gym_external_pilot5.json"))
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--clone-timeout", type=int, default=900)
    parser.add_argument("--build-timeout", type=int, default=1800)
    parser.add_argument("--test-timeout", type=int, default=1800)
    parser.add_argument("--smoke-timeout", type=int, default=30)
    args = parser.parse_args()

    config = read_json(resolve_repo_path(args.config))
    work_root = args.work_root.resolve()
    output_root = resolve_repo_path(args.output_root)
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)

    rows = [
        run_candidate(
            candidate,
            work_root=work_root,
            output_root=output_root,
            clone_timeout=args.clone_timeout,
            build_timeout=args.build_timeout,
            test_timeout=args.test_timeout,
            smoke_timeout=args.smoke_timeout,
        )
        for candidate in config["candidates"]
    ]
    write_summary(output_root, rows)
    print(json.dumps({"dryrun_passed": sum(1 for row in rows if row["dryrun_passed"]), "instances": len(rows)}, indent=2))
    return 0 if all(row["dryrun_passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
