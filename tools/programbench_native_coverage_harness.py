#!/usr/bin/env python3
"""PB-style source coverage harness for Rust and C/C++ command-line tasks."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
from pathlib import Path
from typing import Any

from programbench_go_coverage_harness import (
    REPO_ROOT,
    active_branches,
    branch_test_metadata,
    compare_binary_results,
    copy_oracle_material,
    find_blob_dir,
    materialize_cleanroom_binary,
    parse_simple_yaml,
    resolve_oracle_material_root,
    run_command,
    run_pytest_for_binary,
    safe_extract,
    safe_run_label,
    select_branches,
    write_json,
)


DEFAULT_TASKS_ROOT = Path("external/ProgramBench/src/programbench/data/tasks")
CARGO = str(Path.home() / ".cargo/bin/cargo")
GCOVR = shutil.which("gcovr") or str(
    Path.home() / "research/programbench-scaffold/.venv/bin/gcovr"
)
CMAKE = str(Path.home() / "research/programbench-scaffold/.venv/bin/cmake")
CTEST = str(Path.home() / "research/programbench-scaffold/.venv/bin/ctest")


def compact_line_ranges(lines: list[int]) -> list[str]:
    """Return exact uncovered line numbers without bloating agent feedback."""

    values = sorted({int(line) for line in lines if int(line) > 0})
    if not values:
        return []
    ranges: list[str] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ranges


def summarize_gcovr_file_details(item: dict[str, Any]) -> dict[str, Any]:
    """Compress exact uncovered gcov lines while preserving their C/C++ function context."""

    uncovered_lines: list[int] = []
    by_function: dict[str, list[int]] = {}
    for line in item.get("lines") or []:
        if line.get("line_number") is None or int(line.get("count") or 0) != 0:
            continue
        line_number = int(line["line_number"])
        uncovered_lines.append(line_number)
        function_name = str(line.get("function_name") or "<file-scope>")
        by_function.setdefault(function_name, []).append(line_number)
    uncovered_functions = [
        {"name": function.get("name"), "line": function.get("lineno")}
        for function in item.get("functions") or []
        if int(function.get("execution_count") or 0) == 0
    ]
    function_regions = [
        {"function": name, "uncovered_line_ranges": compact_line_ranges(lines)}
        for name, lines in by_function.items()
    ]
    function_regions.sort(
        key=lambda value: (
            int(str(value["uncovered_line_ranges"][0]).split("-", 1)[0]),
            str(value["function"]),
        )
    )
    return {
        "uncovered_line_ranges": compact_line_ranges(uncovered_lines),
        "uncovered_functions": uncovered_functions,
        "uncovered_function_regions": function_regions,
    }


def parse_export_env(text: str, base: dict[str, str]) -> dict[str, str]:
    env = dict(base)
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("export ") or "=" not in line:
            continue
        key, encoded = line.removeprefix("export ").split("=", 1)
        values = shlex.split(encoded)
        if len(values) != 1:
            raise ValueError(f"unexpected cargo llvm-cov environment line: {raw!r}")
        env[key] = values[0]
    return env


def rust_binary_name(repo: Path, logs: Path) -> str:
    result = run_command(
        [CARGO, "metadata", "--no-deps", "--format-version", "1"],
        cwd=repo,
        timeout=120,
        log_path=logs / "cargo_metadata.json",
        include_output=True,
    )
    if result["returncode"] != 0:
        raise RuntimeError(result["stderr_tail"])
    metadata = json.loads(result["stdout"])
    for package in metadata.get("packages") or []:
        for target in package.get("targets") or []:
            if "bin" in (target.get("kind") or []):
                return str(target["name"])
    raise RuntimeError("Cargo metadata contains no binary target")


def rust_builds(
    source_repo: Path,
    coverage_repo: Path,
    logs: Path,
) -> tuple[Path, Path, dict[str, str], dict[str, Any]]:
    binary_name = rust_binary_name(source_repo, logs)
    source = run_command(
        [CARGO, "build", "--release", "--locked"],
        cwd=source_repo,
        timeout=1200,
        log_path=logs / "cargo_build_source.json",
    )
    if source["returncode"] != 0:
        raise RuntimeError(f"Rust source build failed: {source['stderr_tail']}")

    clean = run_command(
        [CARGO, "llvm-cov", "clean", "--workspace"],
        cwd=coverage_repo,
        timeout=300,
        log_path=logs / "cargo_llvm_cov_clean.json",
    )
    show_env = run_command(
        [CARGO, "llvm-cov", "show-env", "--sh"],
        cwd=coverage_repo,
        timeout=300,
        log_path=logs / "cargo_llvm_cov_show_env.json",
        include_output=True,
    )
    if clean["returncode"] != 0 or show_env["returncode"] != 0:
        raise RuntimeError(f"cargo llvm-cov setup failed: {show_env['stderr_tail']}")
    coverage_env = parse_export_env(show_env["stdout"], os.environ.copy())
    coverage = run_command(
        [CARGO, "build", "--release", "--locked"],
        cwd=coverage_repo,
        env=coverage_env,
        timeout=1200,
        log_path=logs / "cargo_build_coverage.json",
    )
    if coverage["returncode"] != 0:
        raise RuntimeError(f"Rust coverage build failed: {coverage['stderr_tail']}")

    target_dir = Path(coverage_env["CARGO_LLVM_COV_TARGET_DIR"])
    source_binary = source_repo / "target" / "release" / binary_name
    coverage_binary = target_dir / "release" / binary_name
    if not source_binary.is_file() or not coverage_binary.is_file():
        raise FileNotFoundError(f"missing Rust binaries: {source_binary}, {coverage_binary}")
    return source_binary, coverage_binary, coverage_env, {
        "kind": "rust-cargo-llvm-cov",
        "binary_name": binary_name,
        "source": source,
        "coverage": coverage,
        "coverage_env_keys": sorted(
            key for key in coverage_env if key.startswith("CARGO_LLVM_COV") or key in {"LLVM_PROFILE_FILE", "RUSTC_WRAPPER"}
        ),
    }


def c_make_builds(
    source_repo: Path,
    coverage_repo: Path,
    logs: Path,
    binary_name: str,
) -> tuple[Path, Path, dict[str, Any]]:
    source_make_dir = find_make_build_dir(source_repo, binary_name)
    relative_make_dir = source_make_dir.relative_to(source_repo)
    coverage_make_dir = coverage_repo / relative_make_dir
    # Several established C/C++ projects (notably 7-Zip) keep a Windows
    # nmake file named ``makefile`` beside the portable GNU recipe
    # ``makefile.gcc``.  Prefer the explicit GNU recipe when it exists.
    make_args = ["-f", "makefile.gcc"] if (source_make_dir / "makefile.gcc").is_file() else []
    run_command(
        ["make", *make_args, "clean"],
        cwd=source_make_dir,
        timeout=300,
        log_path=logs / "make_source_clean.json",
    )
    source = run_command(
        ["make", *make_args],
        cwd=source_make_dir,
        timeout=1200,
        log_path=logs / "make_source.json",
    )
    if source["returncode"] != 0:
        raise RuntimeError(f"C/C++ source build failed: {source['stderr_tail']}")
    run_command(
        ["make", *make_args, "clean"],
        cwd=coverage_make_dir,
        timeout=300,
        log_path=logs / "make_coverage_clean.json",
    )
    coverage_overrides = (
        # Preserve project-owned compile flags in GNU make recipes such as
        # 7-Zip's (they include required -c/-o and variant macros). Wrapping
        # the compilers instruments both compilation and the final link.
        ["CC=gcc --coverage", "CXX=g++ --coverage", "LDFLAGS=--coverage"]
        if make_args
        else [
            "CFLAGS=-O0 -g --coverage -Wall -Wno-unused-value",
            "CXXFLAGS=-O0 -g --coverage -Wall",
            "LDFLAGS=--coverage",
        ]
    )
    coverage = run_command(
        ["make", *make_args, *coverage_overrides],
        cwd=coverage_make_dir,
        timeout=1200,
        log_path=logs / "make_coverage.json",
    )
    if coverage["returncode"] != 0:
        raise RuntimeError(f"C/C++ coverage build failed: {coverage['stderr_tail']}")
    source_binary = find_built_binary(source_repo, binary_name)
    coverage_binary = find_built_binary(coverage_repo, binary_name)
    return source_binary, coverage_binary, {
        "kind": "c-cpp-make-gcov",
        "binary_name": binary_name,
        "make_directory": str(relative_make_dir),
        "gcov_object_directory": str(relative_make_dir),
        "source": source,
        "coverage": coverage,
    }


def find_make_build_dir(repo: Path, binary_name: str) -> Path:
    """Locate the first-party Makefile that builds the repository CLI."""

    for filename in ("Makefile", "GNUmakefile", "makefile"):
        if (repo / filename).is_file():
            return repo
    candidates: list[tuple[int, int, int, str, Path]] = []
    wanted = binary_name.casefold()
    seen: set[Path] = set()
    for filename in ("Makefile", "GNUmakefile", "makefile", "makefile.gcc"):
        for makefile in repo.rglob(filename):
            if makefile.parent in seen:
                continue
            seen.add(makefile.parent)
            relative = makefile.relative_to(repo)
            lowered_parts = {part.casefold() for part in relative.parts}
            if lowered_parts & {"test", "tests", "third_party", "vendor", "vendors"}:
                continue
            # Score the portable recipe when present, even if discovery first
            # encountered the adjacent nmake file.
            scoring_file = makefile.parent / "makefile.gcc"
            if not scoring_file.is_file():
                scoring_file = makefile
            text = scoring_file.read_text(encoding="utf-8", errors="replace").casefold()
            exact_program_match = int(
                re.search(
                    rf"(?m)^\s*(?:prog|program)\s*[:?+]?=\s*{re.escape(wanted)}(?:\s|$)",
                    text,
                )
                is not None
            )
            name_match = int(wanted in text)
            source_count = sum(1 for _ in makefile.parent.rglob("*.cpp")) + sum(
                1 for _ in makefile.parent.rglob("*.c")
            )
            candidates.append(
                (-exact_program_match, -name_match, -source_count, str(relative), makefile.parent)
            )
    if not candidates:
        raise FileNotFoundError(f"could not find a Makefile under {repo}")
    return sorted(candidates)[0][4]


def find_built_binary(build_dir: Path, binary_name: str) -> Path:
    matches = [path for path in build_dir.rglob(binary_name) if path.is_file() and os.access(path, os.X_OK)]
    if not matches:
        # GitHub repository names are not guaranteed to preserve the casing of
        # the produced CLI (for example fastText builds ``fasttext``). Keep the
        # repository-name convention, but make the lookup portable across that
        # common packaging mismatch.
        wanted = binary_name.casefold()
        matches = [
            path
            for path in build_dir.rglob("*")
            if path.is_file() and path.name.casefold() == wanted and os.access(path, os.X_OK)
        ]
    if not matches:
        raise FileNotFoundError(f"could not find built executable {binary_name!r} under {build_dir}")
    return sorted(matches, key=lambda path: (len(path.parts), str(path)))[0]


def cpp_cmake_builds(
    source_repo: Path,
    coverage_repo: Path,
    logs: Path,
    binary_name: str,
) -> tuple[Path, Path, dict[str, Any]]:
    source_build = source_repo / "build"
    coverage_build = coverage_repo / "build"
    source_configure = run_command(
        [
            CMAKE, "-S", str(source_repo), "-B", str(source_build),
            "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
        ],
        cwd=source_repo,
        timeout=1200,
        log_path=logs / "cmake_source_configure.json",
    )
    source_target = run_command(
        [CMAKE, "--build", str(source_build), "--target", binary_name, "--parallel", "2"],
        cwd=source_repo,
        timeout=1800,
        log_path=logs / "cmake_source_build_target.json",
    )
    source = source_target
    if source_target["returncode"] != 0:
        source = run_command(
            [CMAKE, "--build", str(source_build), "--parallel", "2"],
            cwd=source_repo,
            timeout=1800,
            log_path=logs / "cmake_source_build_fallback_all.json",
        )
    if source_configure["returncode"] != 0 or source["returncode"] != 0:
        raise RuntimeError(f"C++ source build failed: {source_configure['stderr_tail']} {source['stderr_tail']}")
    coverage_configure = run_command(
        [
            CMAKE, "-S", str(coverage_repo), "-B", str(coverage_build),
            "-DCMAKE_BUILD_TYPE=Debug",
            "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
            "-DCMAKE_C_FLAGS=-O0 -g --coverage",
            "-DCMAKE_CXX_FLAGS=-O0 -g --coverage",
            # Some projects overwrite CMAKE_CXX_FLAGS in CMakeLists.txt
            # (fastText sets its own -O3 flags). Per-config flags are appended
            # later by CMake, preserving instrumentation and enough
            # optimization for time-bounded behavioral tests.
            "-DCMAKE_C_FLAGS_DEBUG=-O2 -g --coverage",
            "-DCMAKE_CXX_FLAGS_DEBUG=-O2 -g --coverage",
            "-DCMAKE_EXE_LINKER_FLAGS=--coverage",
        ],
        cwd=coverage_repo,
        timeout=1200,
        log_path=logs / "cmake_coverage_configure.json",
    )
    coverage_target = run_command(
        [CMAKE, "--build", str(coverage_build), "--target", binary_name, "--parallel", "2"],
        cwd=coverage_repo,
        timeout=1800,
        log_path=logs / "cmake_coverage_build_target.json",
    )
    coverage = coverage_target
    if coverage_target["returncode"] != 0:
        coverage = run_command(
            [CMAKE, "--build", str(coverage_build), "--parallel", "2"],
            cwd=coverage_repo,
            timeout=1800,
            log_path=logs / "cmake_coverage_build_fallback_all.json",
        )
    if coverage_configure["returncode"] != 0 or coverage["returncode"] != 0:
        raise RuntimeError(f"C++ coverage build failed: {coverage_configure['stderr_tail']} {coverage['stderr_tail']}")
    return (
        find_built_binary(source_build, binary_name),
        find_built_binary(coverage_build, binary_name),
        {
            "kind": "c-cpp-cmake-gcov",
            "binary_name": binary_name,
            "source_configure": source_configure,
            "source_target_attempt": source_target,
            "source": source,
            "coverage_configure": coverage_configure,
            "coverage_target_attempt": coverage_target,
            "coverage": coverage,
        },
    )


def parse_llvm_cov_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False}
    payload = json.loads(path.read_text(encoding="utf-8"))
    data = payload.get("data") or []
    totals = data[0].get("totals") if data else {}
    metrics: dict[str, Any] = {"exists": True, "format": "llvm-cov-export"}
    for name in ("lines", "regions", "functions", "branches"):
        value = (totals or {}).get(name) or {}
        metrics[name] = {
            "count": value.get("count"),
            "covered": value.get("covered"),
            "percent": value.get("percent"),
        }
    exported = data[0] if data else {}
    functions = exported.get("functions") or []
    file_details: list[dict[str, Any]] = []
    for item in exported.get("files") or []:
        filename = item.get("filename")
        line_counts: dict[int, list[int]] = {}
        uncovered_functions: list[dict[str, Any]] = []
        for function in functions:
            filenames = function.get("filenames") or []
            for file_index, function_filename in enumerate(filenames):
                if function_filename != filename:
                    continue
                regions = [
                    region
                    for region in function.get("regions") or []
                    if len(region) >= 6 and int(region[5]) == file_index
                ]
                if int(function.get("count") or 0) == 0 and regions:
                    uncovered_functions.append(
                        {
                            "name": function.get("name"),
                            "line": min(int(region[0]) for region in regions),
                        }
                    )
                for region in regions:
                    start_line, end_line, count = int(region[0]), int(region[2]), int(region[4])
                    for line in range(start_line, end_line + 1):
                        line_counts.setdefault(line, []).append(count)
        uncovered_lines = [
            line for line, counts in line_counts.items() if counts and not any(count > 0 for count in counts)
        ]
        line_summary = (item.get("summary") or {}).get("lines") or {}
        file_details.append(
            {
                "filename": filename,
                "line_total": line_summary.get("count"),
                "line_covered": line_summary.get("covered"),
                "line_percent": line_summary.get("percent"),
                "uncovered_line_ranges": compact_line_ranges(uncovered_lines),
                "uncovered_functions": sorted(
                    uncovered_functions,
                    key=lambda value: (int(value.get("line") or 0), str(value.get("name") or "")),
                ),
            }
        )
    metrics["files"] = file_details
    return metrics


def rust_report(repo: Path, env: dict[str, str], output: Path, logs: Path, label: str) -> dict[str, Any]:
    result = run_command(
        [CARGO, "llvm-cov", "report", "--release", "--json", "--output-path", str(output)],
        cwd=repo,
        env=env,
        timeout=600,
        log_path=logs / f"cargo_llvm_cov_report_{label}.json",
    )
    return {"command_returncode": result["returncode"], **parse_llvm_cov_json(output), "path": str(output)}


def gcovr_report(
    repo: Path,
    output: Path,
    logs: Path,
    label: str,
    *,
    object_directory: Path | None = None,
) -> dict[str, Any]:
    details_output = output.with_name(f"{output.stem}_details.json")
    result = run_command(
        [
            GCOVR,
            "--root",
            str(repo),
            "--object-directory",
            str(object_directory or repo),
            "--exclude",
            r"(.*/)?build/_deps/.*",
            "--exclude",
            r"(.*/)?build/CMakeFiles/.*",
            "--exclude",
            r".*_test\.(c|cc|cpp|cxx)$",
            "--exclude",
            r"(.*/)?(test|tests)/.*",
            "--exclude",
            r"(.*/)?src/test\.(c|cc|cpp|cxx|h|hpp)$",
            "--exclude",
            r"(.*/)?(src/)?third_party/.*",
            "--gcov-ignore-parse-errors",
            "suspicious_hits.warn_once_per_file",
            "--gcov-ignore-parse-errors",
            "negative_hits.warn_once_per_file",
            "--gcov-ignore-errors",
            "source_not_found",
            "--gcov-ignore-errors",
            "no_working_dir_found",
            "--json-summary-pretty",
            "--json-summary",
            str(output),
            "--json-pretty",
            "--json",
            str(details_output),
        ],
        cwd=repo,
        timeout=600,
        log_path=logs / f"gcovr_{label}.json",
    )
    if not output.is_file():
        return {"exists": False, "command_returncode": result["returncode"]}
    payload = json.loads(output.read_text(encoding="utf-8"))
    details_payload = (
        json.loads(details_output.read_text(encoding="utf-8")) if details_output.is_file() else {}
    )
    details_by_file: dict[str, dict[str, Any]] = {}
    for item in details_payload.get("files") or []:
        filename = str(item.get("file") or "")
        details_by_file[filename] = summarize_gcovr_file_details(item)
    files = []
    for item in payload.get("files") or []:
        enriched = dict(item)
        filename = str(item.get("filename") or item.get("file") or "")
        enriched.update(details_by_file.get(filename) or {})
        files.append(enriched)
    report = {
        "exists": True,
        "format": "gcovr-json-summary",
        "command_returncode": result["returncode"],
        "line_total": payload.get("line_total"),
        "line_covered": payload.get("line_covered"),
        "line_percent": payload.get("line_percent"),
        "branch_total": payload.get("branch_total"),
        "branch_covered": payload.get("branch_covered"),
        "branch_percent": payload.get("branch_percent"),
        "function_total": payload.get("function_total"),
        "function_covered": payload.get("function_covered"),
        "function_percent": payload.get("function_percent"),
        "files": files,
        "path": str(output),
        "details_path": str(details_output) if details_output.is_file() else None,
    }
    enriched_output = output.with_name(f"{output.stem}_enriched.json")
    report["enriched_path"] = str(enriched_output)
    write_json(enriched_output, report)
    return report


def remove_files(root: Path, suffix: str) -> int:
    count = 0
    for path in root.rglob(f"*{suffix}"):
        path.unlink()
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("instance_id")
    parser.add_argument("--tasks-root", type=Path, default=DEFAULT_TASKS_ROOT)
    parser.add_argument("--blob-dir", type=Path)
    parser.add_argument("--branch", default="first-active")
    parser.add_argument("--oracle-material-root", type=Path)
    parser.add_argument("--suite-label")
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--pytest-python", type=Path, required=True)
    parser.add_argument("--pytest-timeout", type=int, default=1200)
    parser.add_argument("--xdist", default="1")
    parser.add_argument(
        "--coverage-xdist",
        help="Optional worker count for the instrumented binary; use 1 to avoid concurrent gcda writes.",
    )
    parser.add_argument("--fixed-workspace", type=Path, default=Path("/workspace"))
    parser.add_argument("--binary-name")
    parser.add_argument(
        "--reuse-build-root",
        type=Path,
        help="Reuse source_build/ and coverage_build/ from a prior harness work root.",
    )
    parser.add_argument("--skip-native", action="store_true")
    parser.add_argument(
        "--coverage-only",
        action="store_true",
        help="Run only the instrumented binary, for an official same-build coverage baseline.",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    tasks_root = args.tasks_root if args.tasks_root.is_absolute() else (REPO_ROOT / args.tasks_root).resolve()
    task_dir = tasks_root / args.instance_id
    metadata = parse_simple_yaml(task_dir / "task.yaml")
    language = str(metadata.get("language", "")).lower()
    if language not in {"rs", "rust", "c", "cpp", "c++"}:
        raise ValueError(f"unsupported language {language!r}")
    all_active = active_branches(task_dir)
    tests_metadata = branch_test_metadata(task_dir)
    generated_root = resolve_oracle_material_root(args.oracle_material_root) if args.oracle_material_root else None
    generated_pytest_timeout: int | None = None
    if generated_root:
        selected = [safe_run_label(args.suite_label or generated_root.parent.name)]
        blob_dir = None
        manifest_path = next(
            (
                path
                for path in (
                    generated_root / "eval" / "generated_cli_manifest.json",
                    generated_root / "eval" / "generated_yj_manifest.json",
                )
                if path.is_file()
            ),
            None,
        )
        if manifest_path is not None:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            case_timeouts = [int(case.get("timeout", 5)) for case in manifest.get("cases", [])]
            # Generated cases enforce their own subprocess timeout.  Give
            # pytest enough headroom to let that behavioral timeout fire;
            # the official five-second watchdog otherwise kills valid heavy
            # CLI cases such as archive benchmarks before their assertions.
            generated_pytest_timeout = min(args.pytest_timeout, max([5, *case_timeouts]) + 5)
    else:
        selected = select_branches(args.branch, all_active)
        blob_dir = find_blob_dir(args.instance_id, args.blob_dir)

    work = args.work_root.expanduser().resolve()
    if work.exists():
        if not args.overwrite:
            raise FileExistsError(f"Work root exists: {work}")
        shutil.rmtree(work)
    logs = work / "logs"
    template = work / "source_template"
    build_root = args.reuse_build_root.expanduser().resolve() if args.reuse_build_root else work
    source_repo = build_root / "source_build"
    coverage_repo = build_root / "coverage_build"
    branch_root = work / "branches"
    junit = work / "junit"
    logs.mkdir(parents=True, exist_ok=True)
    junit.mkdir(parents=True, exist_ok=True)

    clone = run_command(
        ["git", "clone", f"https://github.com/{metadata['repository']}.git", str(template)],
        timeout=900,
        log_path=logs / "git_clone.json",
    )
    if clone["returncode"] != 0:
        raise RuntimeError(f"git clone failed: {clone['stderr_tail']}")
    checkout = run_command(
        ["git", "checkout", str(metadata["commit"])],
        cwd=template,
        timeout=120,
        log_path=logs / "git_checkout.json",
    )
    if checkout["returncode"] != 0:
        raise RuntimeError(f"git checkout failed: {checkout['stderr_tail']}")
    coverage_env: dict[str, str] = {}
    if args.reuse_build_root:
        if not source_repo.is_dir() or not coverage_repo.is_dir():
            raise FileNotFoundError(f"reusable build directories not found under {build_root}")
        binary_name = args.binary_name or (
            rust_binary_name(source_repo, logs)
            if language in {"rs", "rust"}
            else str(metadata["repository"]).split("/")[-1]
        )
        if language in {"rs", "rust"}:
            show_env = run_command(
                [CARGO, "llvm-cov", "show-env", "--sh"],
                cwd=coverage_repo,
                timeout=300,
                log_path=logs / "cargo_llvm_cov_show_env_reuse.json",
                include_output=True,
            )
            if show_env["returncode"] != 0:
                raise RuntimeError(f"cargo llvm-cov reuse setup failed: {show_env['stderr_tail']}")
            coverage_env = parse_export_env(show_env["stdout"], os.environ.copy())
            source_binary = source_repo / "target" / "release" / binary_name
            coverage_binary = Path(coverage_env["CARGO_LLVM_COV_TARGET_DIR"]) / "release" / binary_name
            remove_files(coverage_repo / "target", ".profraw")
        else:
            if (source_repo / "CMakeLists.txt").is_file():
                source_binary = find_built_binary(source_repo / "build", binary_name)
                coverage_binary = find_built_binary(coverage_repo / "build", binary_name)
            else:
                source_binary = find_built_binary(source_repo, binary_name)
                coverage_binary = find_built_binary(coverage_repo, binary_name)
            remove_files(coverage_repo, ".gcda")
        if not source_binary.is_file() or not coverage_binary.is_file():
            raise FileNotFoundError(f"reused binaries missing: {source_binary}, {coverage_binary}")
        build = {
            "kind": "reused",
            "from": str(build_root),
            "binary_name": binary_name,
        }
    else:
        shutil.copytree(template, source_repo, symlinks=True, ignore=shutil.ignore_patterns(".git"))
        shutil.copytree(template, coverage_repo, symlinks=True, ignore=shutil.ignore_patterns(".git"))
        if language in {"rs", "rust"}:
            source_binary, coverage_binary, coverage_env, build = rust_builds(source_repo, coverage_repo, logs)
        else:
            binary_name = args.binary_name or str(metadata["repository"]).split("/")[-1]
            if (template / "CMakeLists.txt").is_file():
                source_binary, coverage_binary, build = cpp_cmake_builds(
                    source_repo, coverage_repo, logs, binary_name
                )
            else:
                source_binary, coverage_binary, build = c_make_builds(
                    source_repo, coverage_repo, logs, binary_name
                )

    native: dict[str, Any] | None = None
    if not args.skip_native:
        if language in {"rs", "rust"}:
            native_json = work / "native_llvm_cov.json"
            native_command = run_command(
                [CARGO, "llvm-cov", "--json", "--output-path", str(native_json), "--locked"],
                cwd=source_repo,
                timeout=1800,
                log_path=logs / "cargo_llvm_cov_native.json",
            )
            native = {"command_returncode": native_command["returncode"], **parse_llvm_cov_json(native_json)}
        else:
            native_cmd = (
                [CTEST, "--test-dir", str(coverage_repo / "build"), "--output-on-failure"]
                if build.get("kind") == "c-cpp-cmake-gcov"
                else ["sh", "run-tests.sh"]
            )
            native_command = run_command(
                native_cmd, cwd=coverage_repo, timeout=1200, log_path=logs / "native_tests.json"
            )
            native = {
                "command_returncode": native_command["returncode"],
                **gcovr_report(
                    coverage_repo,
                    work / "native_gcovr_summary.json",
                    logs,
                    "native",
                    object_directory=(
                        coverage_repo / str(build["gcov_object_directory"])
                        if build.get("gcov_object_directory")
                        else None
                    ),
                ),
            }
            remove_files(coverage_repo, ".gcda")

    cleanroom_binary = work / "executable_cleanroom"
    cleanroom: dict[str, Any] | None = None
    if not args.coverage_only:
        cleanroom = materialize_cleanroom_binary(args.instance_id, cleanroom_binary, "docker", logs)
        if cleanroom["returncode"] != 0:
            raise RuntimeError(f"cleanroom binary materialization failed: {cleanroom}")

    branch_results: list[dict[str, Any]] = []
    for branch in selected:
        if generated_root:
            material = generated_root
            ignored: set[str] = set()
            tests_json = {"expected_count": None, "active_count": None, "ignored_count": 0}
        else:
            assert blob_dir is not None
            tarball = blob_dir / "tests" / f"{branch}.tar.gz"
            material = branch_root / branch / "blob"
            safe_extract(tarball, material)
            ignored = tests_metadata[branch]["ignored"]
            tests_json = {
                "expected_count": tests_metadata[branch]["expected_count"],
                "active_count": tests_metadata[branch]["active_count"],
                "ignored_count": tests_metadata[branch]["ignored_count"],
            }
        copied = copy_oracle_material(material, template)
        common = {
            "python": args.pytest_python.expanduser().absolute(),
            "repo_dir": template,
            "branch": branch,
            "result_dir": junit,
            "logs_dir": logs,
            "timeout": args.pytest_timeout,
            "xdist": args.xdist,
            "ignored_tests": ignored,
            "fixed_workspace": args.fixed_workspace,
            "detach_tty": True,
        }
        coverage_common = {**common, "xdist": args.coverage_xdist or args.xdist}
        results = []
        if not args.coverage_only:
            results.extend(
                [
                    run_pytest_for_binary(
                        executable_source=cleanroom_binary,
                        label="cleanroom",
                        pytest_timeout_override=generated_pytest_timeout,
                        **common,
                    ),
                    run_pytest_for_binary(
                        executable_source=source_binary,
                        label="source",
                        pytest_timeout_override=generated_pytest_timeout,
                        **common,
                    ),
                ]
            )
        results.append(
            run_pytest_for_binary(
                executable_source=coverage_binary,
                label="coverage",
                extra_env=coverage_env if language in {"rs", "rust"} else None,
                pytest_timeout_override=max(360, generated_pytest_timeout or 0),
                **coverage_common,
            )
        )
        branch_results.append(
            {
                "branch": branch,
                "tests_json": tests_json,
                "copied_oracle_material": copied,
                "binary_results": results,
                "comparison": compare_binary_results(results),
            }
        )

    if language in {"rs", "rust"}:
        coverage = rust_report(coverage_repo, coverage_env, work / "suite_llvm_cov.json", logs, "suite")
    else:
        gcov_object_directory = (
            coverage_repo / str(build["gcov_object_directory"])
            if build.get("gcov_object_directory")
            else (
                coverage_binary.parent.parent
                if coverage_binary.parent.name.startswith("_o")
                else None
            )
        )
        coverage = gcovr_report(
            coverage_repo,
            work / "suite_gcovr_summary.json",
            logs,
            "suite",
            object_directory=gcov_object_directory,
        )

    all_consistent = all(item["comparison"]["behavior_consistent"] for item in branch_results)
    all_gold_passed = all(item["comparison"]["all_filtered_passed"] for item in branch_results)
    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "instance_id": args.instance_id,
        "repository": metadata.get("repository"),
        "commit": metadata.get("commit"),
        "language": language,
        "suite_kind": "generated" if generated_root else "official",
        "binary_scope": "coverage-only" if args.coverage_only else "cleanroom-source-coverage",
        "selected_branches": selected,
        "active_branch_count": len(all_active),
        "build": build,
        "native": native,
        "coverage": coverage,
        "cleanroom_binary": cleanroom,
        "source_binary": str(source_binary),
        "coverage_binary": str(coverage_binary),
        "all_branch_binary_comparisons_consistent": all_consistent,
        "all_filtered_tests_passed": all_gold_passed,
        "branch_results": branch_results,
    }
    write_json(args.output_json.expanduser().resolve(), payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if all_consistent and all_gold_passed and coverage.get("exists") else 1


if __name__ == "__main__":
    raise SystemExit(main())
