#!/usr/bin/env python3
"""Build ProgramBench Gym instance directories.

The first Gym backend bootstraps from existing ProgramBench tasks. It creates a
standard instance layout, links or copies the source-leak-guarded oracle bundle,
writes reward scripts, and records static quality gates. Docker cleanroom
materialization is optional because local macOS Docker is not reliable for the
linux/amd64 ProgramBench images.
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
import textwrap
import uuid
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PROGRAMBENCH_SRC = REPO_ROOT / "external" / "ProgramBench" / "src"
if PROGRAMBENCH_SRC.exists() and str(PROGRAMBENCH_SRC) not in sys.path:
    sys.path.insert(0, str(PROGRAMBENCH_SRC))

DEFAULT_TASKS_ROOT = Path("external/ProgramBench/src/programbench/data/tasks")
DEFAULT_TEST_BUNDLE_ROOT = Path("reports/test_bundles_oracle_guarded_2026-07-07")
DEFAULT_OUTPUT_ROOT = Path("reports")
HF_CACHE_ROOT = Path.home() / ".cache/huggingface/hub/datasets--programbench--ProgramBench-Tests/snapshots"
SCHEMA_VERSION = "0.1"

DISALLOWED_NAMES = {
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Makefile",
    "CMakeLists.txt",
    "build.sh",
    "Dockerfile",
}

SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hpp",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".hs",
}

MVP3_TASKS = [
    "sclevine__yj.8016400",
    "sirwart__ripsecrets.34c9e03",
    "cmatsuoka__figlet.202a0a8",
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return f"<external:{path.name}>"


def parse_simple_yaml(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_list_key:
            data.setdefault(current_list_key, []).append(stripped[2:].strip("'\""))
            continue
        current_list_key = None
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
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


def image_name_from_instance_id(instance_id: str) -> str:
    try:
        from programbench.constants import image_name_from_instance_id as upstream

        return upstream(instance_id)
    except Exception:
        org = os.environ.get("PROGRAMBENCH_DOCKER_ORG", "programbench")
        return f"{org}/{instance_id.replace('__', '_1776_')}"


def ignored_test_names(branch: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for item in branch.get("ignored_tests") or []:
        if isinstance(item, str):
            names.add(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.add(item["name"])
    return names


def tests_summary(task_dir: Path) -> dict[str, Any]:
    payload = read_json(task_dir / "tests.json")
    branches = payload.get("branches") or {}
    active_branch_ids: list[str] = []
    ignored_branch_ids: list[str] = []
    active_tests = 0
    ignored_tests = 0
    total_tests = 0
    test_groups: Counter[str] = Counter()

    for branch_id, branch in branches.items():
        if not isinstance(branch, dict):
            continue
        if branch.get("ignored"):
            ignored_branch_ids.append(branch_id)
            continue
        active_branch_ids.append(branch_id)
        ignored = ignored_test_names(branch)
        ignored_tests += len(ignored)
        for name in branch.get("tests") or []:
            if not isinstance(name, str):
                continue
            total_tests += 1
            if name in ignored:
                continue
            active_tests += 1
            parts = name.split(".")
            group = parts[-2] if len(parts) >= 2 else name
            test_groups[group] += 1

    return {
        "branch_count": len(branches),
        "active_branch_count": len(active_branch_ids),
        "ignored_branch_count": len(ignored_branch_ids),
        "active_branch_ids": active_branch_ids,
        "ignored_branch_ids": ignored_branch_ids,
        "active_tests": active_tests,
        "ignored_tests": ignored_tests,
        "listed_tests_in_active_branches": total_tests,
        "top_test_groups": test_groups.most_common(12),
    }


def find_blob_dir(instance_id: str, explicit_root: Path | None) -> Path | None:
    roots: list[Path] = []
    if explicit_root is not None:
        roots.append(explicit_root.expanduser())
    env_root = os.environ.get("PROGRAMBENCH_BLOB_DIR")
    if env_root:
        roots.append(Path(env_root).expanduser())
    if HF_CACHE_ROOT.exists():
        roots.extend(sorted(HF_CACHE_ROOT.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True))

    for root in roots:
        candidate = root / instance_id
        if candidate.exists():
            return candidate.resolve()
        if root.name == instance_id and root.exists():
            return root.resolve()
    return None


def raw_blob_summary(instance_id: str, blob_root: Path | None) -> dict[str, Any]:
    blob_dir = find_blob_dir(instance_id, blob_root)
    if blob_dir is None:
        return {"exists": False, "blob_dir": None, "branch_archives": []}
    tests_dir = blob_dir / "tests"
    archives = sorted(
        path
        for path in tests_dir.glob("*.tar.gz")
        if path.is_file() or path.is_symlink()
    )
    return {
        "exists": tests_dir.exists(),
        "blob_dir": str(blob_dir),
        "tests_dir": str(tests_dir),
        "branch_archive_count": len(archives),
        "branch_archives": [
            {
                "branch_id": path.name.removesuffix(".tar.gz"),
                "path": str(path),
                "is_symlink": path.is_symlink(),
                "resolved_path": str(path.resolve()) if path.exists() else None,
            }
            for path in archives
        ],
    }


def ensure_link_or_copy(src: Path, dest: Path, *, copy: bool) -> str:
    if dest.exists() or dest.is_symlink():
        raise FileExistsError(f"Destination already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not copy:
        rel = os.path.relpath(src.resolve(), dest.parent.resolve())
        dest.symlink_to(rel, target_is_directory=src.is_dir())
        return "symlink"
    if src.is_dir():
        shutil.copytree(src, dest)
    else:
        shutil.copy2(src, dest)
    return "copy"


def normalize_export_permissions(path: Path) -> dict[str, Any]:
    """Make Docker-exported files readable by the current runner user."""
    chmod_failures: list[str] = []
    for root, dirs, files in os.walk(path):
        for name in [*dirs, *files]:
            target = Path(root) / name
            try:
                mode = target.stat().st_mode
                target.chmod(mode | stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
                if target.is_dir() or os.access(target, os.X_OK):
                    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except PermissionError:
                chmod_failures.append(str(target))
    if not chmod_failures:
        return {"status": "normalized", "method": "python_chmod"}

    sudo = shutil.which("sudo")
    if sudo is None:
        return {"status": "failed", "method": "python_chmod", "permission_failures": chmod_failures[:20]}

    uid_gid = f"{os.getuid()}:{os.getgid()}"
    chown = subprocess.run([sudo, "chown", "-R", uid_gid, str(path)], capture_output=True, text=True, timeout=120)
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


def path_is_allowed_source_like(rel: str) -> bool:
    # Python pytest files under eval/tests are the oracle tests themselves.
    if rel.startswith("eval/tests/") and rel.endswith(".py"):
        return True
    # eval/run.sh is the branch harness.
    if rel == "eval/run.sh":
        return True
    return False


def scan_sanitized_bundle(bundle_root: Path) -> dict[str, Any]:
    target = bundle_root.resolve()
    disallowed_names: list[str] = []
    source_like: list[str] = []
    file_count = 0
    manifest_path = target / "manifest.json"
    manifest_text = manifest_path.read_text(encoding="utf-8", errors="replace") if manifest_path.exists() else ""
    host_path_hits = [
        marker
        for marker in ("/Users/", "/home/", "/var/folders/", ".cache/huggingface", "source_blob_dir")
        if marker in manifest_text
    ]

    for root, _, filenames in os.walk(target, followlinks=True):
        for filename in filenames:
            path = Path(root) / filename
            rel = path.relative_to(target).as_posix()
            file_count += 1
            if filename in DISALLOWED_NAMES:
                disallowed_names.append(rel)
            if path.suffix in SOURCE_SUFFIXES and not path_is_allowed_source_like(rel):
                source_like.append(rel)

    return {
        "file_count": file_count,
        "manifest_exists": manifest_path.exists(),
        "disallowed_name_hits": disallowed_names[:100],
        "source_like_hits": source_like[:100],
        "host_path_hits": host_path_hits,
        "pass": not disallowed_names and not source_like and not host_path_hits and manifest_path.exists(),
    }


def gate(name: str, status: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "status": status, "details": details or {}}


def write_eval_files(eval_dir: Path) -> None:
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "README.md").write_text(
        """# Gym Evaluation Helpers

`score_generated_tests.py` is the first reward surface for binary-to-test
agents. It expects generated tests that execute `./executable` in the working
directory. The script runs the tests once against the reference binary and once
against a dummy executable.

The per-instance quality report records whether the reference executable has
been materialized locally. If it has not, run the builder with
`--materialize-cleanroom` on a Linux x86-64 host with ProgramBench Docker images.
""",
        encoding="utf-8",
    )
    (eval_dir / "reward_schema.json").write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "reward_terms": {
                    "pass_reference": {
                        "weight": 1.0,
                        "description": "Generated tests pass against the reference executable.",
                    },
                    "fail_dummy": {
                        "weight": 1.0,
                        "description": "Generated tests reject an empty/dummy executable.",
                    },
                    "deterministic": {
                        "weight": 0.5,
                        "description": "Repeated reference runs produce the same result.",
                    },
                    "timeout_safe": {
                        "weight": 0.25,
                        "description": "Tests finish within the configured timeout.",
                    },
                    "coverage_delta": {
                        "weight": 0.0,
                        "description": "Reserved hook for dev-time source coverage reward.",
                    },
                    "mutation_kill": {
                        "weight": 0.0,
                        "description": "Reserved hook for mutation/bad-implementation reward.",
                    },
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    score_script = eval_dir / "score_generated_tests.py"
    score_script.write_text(
        r'''#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path


def write_dummy(path: Path) -> None:
    path.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def run_pytest(tests_dir: Path, executable: Path, timeout: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="pb_gym_reward_") as tmp:
        root = Path(tmp)
        work_tests = root / "tests"
        shutil.copytree(tests_dir, work_tests)
        exe_link = root / "executable"
        if executable.is_symlink() or executable.exists():
            exe_link.symlink_to(executable.resolve())
        else:
            raise FileNotFoundError(executable)
        env = dict(os.environ)
        env["PROGRAMBENCH_GYM_EXECUTABLE"] = str(exe_link)
        proc = subprocess.run(
            ["python3", "-m", "pytest", "-q", str(work_tests)],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tests-dir", type=Path, required=True)
    parser.add_argument("--reference-executable", type=Path, default=Path("../cleanroom/executable"))
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--out", type=Path, default=Path("generated_test_score.json"))
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="pb_gym_dummy_") as tmp:
        dummy = Path(tmp) / "executable"
        write_dummy(dummy)
        reference = run_pytest(args.tests_dir, args.reference_executable, args.timeout)
        dummy_result = run_pytest(args.tests_dir, dummy, args.timeout)

    payload = {
        "pass_reference": reference["returncode"] == 0,
        "fail_dummy": dummy_result["returncode"] != 0,
        "reference": reference,
        "dummy": dummy_result,
    }
    payload["reward"] = float(payload["pass_reference"]) + float(payload["fail_dummy"])
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["pass_reference"] and payload["fail_dummy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
''',
        encoding="utf-8",
    )
    score_script.chmod(score_script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    smoke_script = eval_dir / "run_reference_smoke.py"
    smoke_script.write_text(
        r'''#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, default=Path("../cleanroom/executable"))
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()
    if not args.executable.exists():
        payload = {"status": "missing_reference_executable", "executable": str(args.executable)}
        print(json.dumps(payload, indent=2))
        return 2
    attempts = [
        [str(args.executable), "--help"],
        [str(args.executable), "-h"],
        [str(args.executable)],
    ]
    rows = []
    for cmd in attempts:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
            rows.append({"cmd": cmd, "returncode": proc.returncode, "stdout_len": len(proc.stdout), "stderr_len": len(proc.stderr)})
            break
        except subprocess.TimeoutExpired:
            rows.append({"cmd": cmd, "timeout": True})
    print(json.dumps({"status": "ran", "attempts": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
        encoding="utf-8",
    )
    smoke_script.chmod(smoke_script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def materialize_cleanroom_from_docker(instance_dir: Path, image_ref: str, docker: str) -> dict[str, Any]:
    cleanroom = instance_dir / "cleanroom"
    container_name = f"pb-gym-export-{uuid.uuid4().hex[:10]}"
    create = subprocess.run(
        [docker, "create", "--name", container_name, image_ref],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if create.returncode != 0:
        return {"status": "failed", "step": "docker_create", "stderr": create.stderr.strip()}
    try:
        if cleanroom.exists():
            shutil.rmtree(cleanroom)
        cleanroom.mkdir(parents=True, exist_ok=True)
        cp = subprocess.run(
            [docker, "cp", f"{container_name}:/workspace/.", str(cleanroom)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if cp.returncode != 0:
            return {"status": "failed", "step": "docker_cp", "stderr": cp.stderr.strip()}
        permissions = normalize_export_permissions(cleanroom)
        executable = cleanroom / "executable"
        return {
            "status": "materialized",
            "image": image_ref,
            "executable_exists": executable.exists(),
            "executable": "cleanroom/executable",
            "permissions": permissions,
        }
    finally:
        subprocess.run([docker, "rm", "-f", container_name], capture_output=True, text=True, timeout=60)


def build_instance(
    *,
    instance_id: str,
    config: dict[str, Any],
    output_root: Path,
    copy_tests: bool,
    materialize_cleanroom: bool,
    docker: str,
    blob_root: Path | None,
) -> dict[str, Any]:
    tasks_root = Path(config.get("tasks_root") or DEFAULT_TASKS_ROOT)
    if not tasks_root.is_absolute():
        tasks_root = (REPO_ROOT / tasks_root).resolve()
    test_bundle_root = Path(config.get("test_bundle_root") or DEFAULT_TEST_BUNDLE_ROOT)
    if not test_bundle_root.is_absolute():
        test_bundle_root = (REPO_ROOT / test_bundle_root).resolve()

    task_dir = tasks_root / instance_id
    if not task_dir.exists():
        raise FileNotFoundError(f"Unknown ProgramBench task: {instance_id}")

    instance_dir = output_root / instance_id
    instance_dir.mkdir(parents=True, exist_ok=False)
    for child in ("cleanroom", "private", "oracle_tests", "eval"):
        (instance_dir / child).mkdir(parents=True, exist_ok=True)
    (instance_dir / "private" / "coverage").mkdir(parents=True, exist_ok=True)
    (instance_dir / "private" / "mutants").mkdir(parents=True, exist_ok=True)

    task_meta = parse_simple_yaml(task_dir / "task.yaml")
    test_stats = tests_summary(task_dir)
    raw_blobs = raw_blob_summary(instance_id, blob_root)
    image_name = image_name_from_instance_id(instance_id)
    sanitized_src = test_bundle_root / instance_id / "oracle_tests"
    sanitized_dest = instance_dir / "oracle_tests" / "sanitized"
    bundle_link_mode = None
    if sanitized_src.exists():
        bundle_link_mode = ensure_link_or_copy(sanitized_src, sanitized_dest, copy=copy_tests)

    write_eval_files(instance_dir / "eval")
    cleanroom_status = {
        "status": "skipped",
        "reason": "run builder with --materialize-cleanroom on a host with ProgramBench Docker images",
    }
    if materialize_cleanroom:
        cleanroom_status = materialize_cleanroom_from_docker(instance_dir, f"{image_name}:task_cleanroom_v6", docker)
    else:
        (instance_dir / "cleanroom" / "README.md").write_text(
            textwrap.dedent(
                f"""\
                # Cleanroom Placeholder

                Instance: `{instance_id}`

                The reference executable and original cleanroom docs should be
                materialized from Docker image:

                ```text
                {image_name}:task_cleanroom_v6
                ```

                Run the Gym builder with `--materialize-cleanroom` on a Linux
                x86-64 host to copy `/workspace` from that image into this
                directory.
                """
            ),
            encoding="utf-8",
        )

    private_manifest = {
        "source_visibility": "private-only",
        "source_type": config.get("source_type", "programbench_existing"),
        "repository": task_meta.get("repository"),
        "commit": task_meta.get("commit"),
        "programbench_task_dir": str(task_dir),
        "raw_test_blobs": raw_blobs,
        "notes": [
            "This file can contain source/blob paths and must not be exposed to inference agents.",
            "For new GitHub repos, source checkout/build logs/coverage/mutants belong under private/.",
        ],
    }
    write_json(instance_dir / "private" / "source_manifest.json", private_manifest)
    write_json(instance_dir / "private" / "raw_test_blobs.json", raw_blobs)

    leak_scan = (
        scan_sanitized_bundle(sanitized_dest)
        if sanitized_dest.exists() or sanitized_dest.is_symlink()
        else {"pass": False, "reason": "missing sanitized bundle"}
    )
    gates = [
        gate(
            "metadata_loaded",
            "pass",
            {
                "task_yaml": display_path(task_dir / "task.yaml"),
                "tests_json": display_path(task_dir / "tests.json"),
            },
        ),
        gate(
            "raw_test_blobs_found",
            "pass" if raw_blobs.get("exists") else "fail",
            {
                "exists": raw_blobs.get("exists"),
                "branch_archive_count": raw_blobs.get("branch_archive_count", 0),
            },
        ),
        gate(
            "sanitized_bundle_found",
            "pass" if sanitized_src.exists() else "fail",
            {"bundle": display_path(sanitized_src), "mode": bundle_link_mode},
        ),
        gate("no_source_leakage_static", "pass" if leak_scan.get("pass") else "fail", leak_scan),
        gate(
            "no_source_path_leakage",
            "pass" if leak_scan.get("pass") and not leak_scan.get("host_path_hits") else "fail",
            {"host_path_hits": leak_scan.get("host_path_hits", [])},
        ),
        gate(
            "reference_binary_materialized",
            "pass" if cleanroom_status.get("executable_exists") else ("fail" if materialize_cleanroom else "skipped"),
            cleanroom_status,
        ),
        gate("reference_smoke_runs", "skipped", {"reason": "requires materialized cleanroom executable"}),
        gate("oracle_tests_pass_reference", "skipped", {"reason": "requires materialized cleanroom executable"}),
        gate("dummy_does_not_pass_all", "skipped", {"reason": "requires running oracle tests in an isolated workspace"}),
        gate("offline_reproducible_eval", "skipped", {"reason": "requires Linux x86-64 Docker eval host"}),
        gate("coverage_or_mutation_ready", "skipped", {"reason": "reserved for Phase 3/4 reward instrumentation"}),
    ]

    metadata = {
        "schema_version": SCHEMA_VERSION,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "instance_id": instance_id,
        "source_type": config.get("source_type", "programbench_existing"),
        "repository": task_meta.get("repository"),
        "commit": task_meta.get("commit"),
        "language": task_meta.get("language"),
        "difficulty": task_meta.get("difficulty"),
        "image_name": image_name,
        "image_tags": {
            "cleanroom": f"{image_name}:task_cleanroom_v6",
            "eval": f"{image_name}:task",
        },
        "task": test_stats,
        "cleanroom": {
            "path": "cleanroom",
            "materialization": cleanroom_status,
            "reference_executable": "cleanroom/executable",
            "agent_visible": True,
        },
        "oracle_tests": {
            "sanitized_path": "oracle_tests/sanitized",
            "sanitized_source": display_path(sanitized_src),
            "link_mode": bundle_link_mode,
            "raw_blob_count": raw_blobs.get("branch_archive_count", 0),
            "agent_visible_for_upper_bound": True,
            "hidden_for_binary_to_test_inference": True,
        },
        "evaluation": {
            "path": "eval",
            "generated_test_scorer": "eval/score_generated_tests.py",
            "reward_schema": "eval/reward_schema.json",
        },
        "quality_gates": gates,
    }
    write_json(instance_dir / "metadata.json", metadata)

    binary_to_test_sample = {
        "schema_version": SCHEMA_VERSION,
        "instance_id": instance_id,
        "input": {
            "docs_path": "cleanroom/",
            "reference_binary": "cleanroom/executable",
            "source_access": False,
        },
        "target_output": {
            "type": "executable_tests",
            "preferred_framework": "pytest",
            "convention": "tests execute ./executable in their working directory",
        },
        "reward_signals": [
            "tests_pass_reference",
            "tests_fail_dummy",
            "tests_are_deterministic",
            "tests_finish_before_timeout",
            "coverage_delta_reserved",
            "mutation_kill_reserved",
            "downstream_coding_agent_score_reserved",
        ],
    }
    write_json(instance_dir / "binary_to_test_sample.json", binary_to_test_sample)
    quality_report = {
        "schema_version": SCHEMA_VERSION,
        "instance_id": instance_id,
        "summary": {
            "static_gates_passed": all(
                row["status"] == "pass"
                for row in gates
                if row["name"]
                in {
                    "metadata_loaded",
                    "raw_test_blobs_found",
                    "sanitized_bundle_found",
                    "no_source_leakage_static",
                    "no_source_path_leakage",
                }
            ),
            "dynamic_gates_ready": materialize_cleanroom and bool(cleanroom_status.get("executable_exists")),
        },
        "gates": gates,
    }
    write_json(instance_dir / "quality_report.json", quality_report)
    return {
        "instance_id": instance_id,
        "instance_dir": str(instance_dir),
        "repository": task_meta.get("repository"),
        "language": task_meta.get("language"),
        "active_tests": test_stats["active_tests"],
        "active_branches": test_stats["active_branch_count"],
        "static_gates_passed": quality_report["summary"]["static_gates_passed"],
        "dynamic_gates_ready": quality_report["summary"]["dynamic_gates_ready"],
    }


def load_config(args: argparse.Namespace) -> dict[str, Any]:
    if args.config:
        return read_json(args.config)
    if args.preset == "mvp3":
        return {
            "name": "programbench_gym_mvp3",
            "source_type": "programbench_existing",
            "tasks_root": str(DEFAULT_TASKS_ROOT),
            "test_bundle_root": str(DEFAULT_TEST_BUNDLE_ROOT),
            "tasks": MVP3_TASKS,
        }
    if args.preset == "devset10":
        return read_json(REPO_ROOT / "configs/programbench_gym_devset10.json")
    raise ValueError("Either --config or --preset is required")


def write_summary_markdown(output_root: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# ProgramBench Gym Build Summary",
        "",
        f"- Build: `{summary['name']}`",
        f"- Created: `{summary['created_at']}`",
        f"- Output: `{summary['output_root']}`",
        f"- Instances: {len(summary['instances'])}",
        f"- Static gates passed: {summary['aggregate']['static_gates_passed']}/{len(summary['instances'])}",
        f"- Dynamic gates ready: {summary['aggregate']['dynamic_gates_ready']}/{len(summary['instances'])}",
        "",
        "| instance | repo | lang | tests | branches | static gates | dynamic ready |",
        "| --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for row in summary["instances"]:
        lines.append(
            "| `{instance_id}` | `{repository}` | {language} | {active_tests} | {active_branches} | {static_gates_passed} | {dynamic_gates_ready} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Static gates validate metadata, local raw test blobs, source-leak-guarded bundles, and absence of obvious source/path leakage.",
            "- Dynamic gates require materializing ProgramBench Docker cleanroom images and should be run on Linux x86-64.",
            "- Generated-test reward scripts are available under each instance's `eval/` directory.",
            "",
        ]
    )
    (output_root / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--preset", choices=["mvp3", "devset10"], default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--copy-tests", action="store_true", help="Copy sanitized bundles instead of symlinking them.")
    parser.add_argument("--materialize-cleanroom", action="store_true")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--blob-root", type=Path, default=None)
    args = parser.parse_args()

    config = load_config(args)
    build_name = config.get("name") or "programbench_gym"
    if args.output_root is None:
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        output_root = REPO_ROOT / DEFAULT_OUTPUT_ROOT / f"{build_name}_{stamp}"
    else:
        output_root = args.output_root if args.output_root.is_absolute() else REPO_ROOT / args.output_root
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError(f"Output root already exists: {output_root}")
    output_root.mkdir(parents=True)

    task_ids = config.get("tasks") or []
    if not task_ids:
        raise ValueError("Config has no tasks")

    rows = []
    for task_id in task_ids:
        rows.append(
            build_instance(
                instance_id=task_id,
                config=config,
                output_root=output_root,
                copy_tests=args.copy_tests,
                materialize_cleanroom=args.materialize_cleanroom,
                docker=args.docker,
                blob_root=args.blob_root,
            )
        )

    summary = {
        "schema_version": SCHEMA_VERSION,
        "name": build_name,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "output_root": str(output_root),
        "config": config,
        "instances": rows,
        "aggregate": {
            "static_gates_passed": sum(1 for row in rows if row["static_gates_passed"]),
            "dynamic_gates_ready": sum(1 for row in rows if row["dynamic_gates_ready"]),
        },
    }
    write_json(output_root / "gym_build_summary.json", summary)
    write_summary_markdown(output_root, summary)
    print(json.dumps(summary["aggregate"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
