#!/usr/bin/env python3
"""Build executable oracle-test bundles from ProgramBench test blobs.

The canonical upper-bound mode is ``oracle-guarded``: it keeps as much official
executable-test material as possible while filtering source/build artifacts and
not exposing source blob paths in the agent-visible manifest. The older
``sanitized`` and ``full-safe`` names are kept only for backward compatibility
with earlier pilot runs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import tarfile
from dataclasses import dataclass
from pathlib import Path
from collections import Counter
from typing import Any, Literal


DEFAULT_TASKS_ROOT = Path("external/ProgramBench/src/programbench/data/tasks")
HF_CACHE_ROOT = Path.home() / ".cache/huggingface/hub/datasets--programbench--ProgramBench-Tests/snapshots"

ALLOWED_PREFIXES = (
    "eval/",
    "testdata/",
    "tests/",
    "fixtures/",
    "fixture/",
    "examples/",
    "samples/",
    "resources/",
    "assets/",
)

ORACLE_GUARDED_TOP_LEVEL_PREFIXES = (
    "eval/",
    "testdata/",
    "tests/",
    "fixtures/",
    "fixture/",
    "examples/",
    "samples/",
    "resources/",
    "assets/",
    "data/",
    "inputs/",
    "expected/",
)

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
    ".sh",
}

BundleMode = Literal["sanitized", "oracle-guarded", "full-safe"]


@dataclass(frozen=True)
class Decision:
    include: bool
    reason: str


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def ignored_test_names(branch: dict[str, Any]) -> set[str]:
    ignored: set[str] = set()
    for item in branch.get("ignored_tests") or []:
        if isinstance(item, str):
            ignored.add(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            ignored.add(item["name"])
    return ignored


def active_branch_ids(task_dir: Path) -> list[str]:
    tests_json = task_dir / "tests.json"
    payload = read_json(tests_json)
    branches = payload.get("branches") or {}
    active: list[str] = []
    for branch_id, branch in branches.items():
        if not isinstance(branch, dict) or branch.get("ignored"):
            continue
        tests = [name for name in branch.get("tests") or [] if isinstance(name, str)]
        ignored = ignored_test_names(branch)
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
        raise FileNotFoundError(f"Could not find blob directory for {instance_id} under {candidate}")

    env_root = os.environ.get("PROGRAMBENCH_BLOB_DIR")
    if env_root:
        candidate = Path(env_root).expanduser().resolve() / instance_id
        if candidate.exists():
            return candidate

    matches = sorted(HF_CACHE_ROOT.glob(f"*/{instance_id}"), key=lambda path: path.stat().st_mtime, reverse=True)
    if matches:
        return matches[0]
    raise FileNotFoundError(
        f"Could not locate ProgramBench test blob for {instance_id}. "
        "Run `programbench blob sync <instance_id>` on a networked machine or pass --blob-dir."
    )


def safe_member_path(name: str) -> Path | None:
    normalized = Path(name)
    if normalized.is_absolute() or ".." in normalized.parts:
        return None
    if name.endswith("/"):
        return None
    return normalized


def decide_member(name: str, is_file: bool, *, mode: BundleMode) -> Decision:
    mode = canonical_mode(mode)
    path = safe_member_path(name)
    if path is None:
        return Decision(False, "unsafe path")
    if not is_file:
        return Decision(False, "not a regular file")

    posix = path.as_posix()
    base = path.name

    if base in DISALLOWED_NAMES:
        return Decision(False, "build/source metadata")

    if mode == "sanitized":
        if posix.startswith("eval/tests/"):
            return Decision(True, "pytest tests")
        if posix in {"eval/run.sh", "eval/README.md"}:
            return Decision(True, "eval harness")

        if path.suffix in SOURCE_SUFFIXES:
            return Decision(False, "source-like file")

        if any(posix.startswith(prefix) for prefix in ALLOWED_PREFIXES):
            return Decision(True, "test fixture")

        return Decision(False, "outside allowed test paths")

    if path.suffix in SOURCE_SUFFIXES and posix != "eval/run.sh":
        return Decision(False, "source-like file")

    if any(posix.startswith(prefix) for prefix in ORACLE_GUARDED_TOP_LEVEL_PREFIXES):
        return Decision(True, "source-leak-guarded oracle material")

    return Decision(False, "outside recognized oracle-test paths")


def canonical_mode(mode: BundleMode) -> BundleMode:
    if mode == "full-safe":
        return "oracle-guarded"
    return mode


def extract_member(tar: tarfile.TarFile, member: tarfile.TarInfo, dest_root: Path) -> None:
    path = safe_member_path(member.name)
    if path is None:
        raise ValueError(f"Unsafe tar member path: {member.name}")
    target = dest_root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    source = tar.extractfile(member)
    if source is None:
        return
    with source, target.open("wb") as out:
        out.write(source.read())
    mode = member.mode & 0o777
    if mode:
        target.chmod(mode)


def write_helper_scripts(bundle_root: Path) -> None:
    run_branch = bundle_root / "run_branch.sh"
    run_branch.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: ./oracle_tests/run_branch.sh <branch-id>" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(cd "${SCRIPT_DIR}/.." && pwd)"
BRANCH="$1"
BRANCH_DIR="${SCRIPT_DIR}/branches/${BRANCH}"
RESULT_DIR="${SCRIPT_DIR}/results/${BRANCH}"

if [ ! -d "${BRANCH_DIR}" ]; then
  echo "unknown branch: ${BRANCH}" >&2
  exit 2
fi

if [ ! -x "${WORKSPACE}/executable" ]; then
  if [ -x "${WORKSPACE}/compile.sh" ]; then
    (cd "${WORKSPACE}" && ./compile.sh)
  fi
fi

if [ ! -f "${WORKSPACE}/executable" ]; then
  echo "missing ${WORKSPACE}/executable; create it via ./compile.sh first" >&2
  exit 1
fi

mkdir -p "${RESULT_DIR}"
ln -sf "${WORKSPACE}/executable" "${BRANCH_DIR}/executable"

cd "${BRANCH_DIR}"
if [ -d "eval/tests" ]; then
  python3 -m pytest --junitxml="${RESULT_DIR}/results.xml" -q eval/tests
elif [ -x "eval/run.sh" ]; then
  bash eval/run.sh
else
  echo "no runnable pytest tests or eval/run.sh found for ${BRANCH}" >&2
  exit 1
fi
""",
        encoding="utf-8",
    )
    run_branch.chmod(run_branch.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    run_all = bundle_root / "run_all.sh"
    run_all.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 - "${SCRIPT_DIR}/manifest.json" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text())
script_dir = Path(sys.argv[1]).resolve().parent
failed = []
for branch in manifest["branches"]:
    branch_id = branch["branch_id"]
    print(f"== running oracle branch {branch_id} ==")
    proc = subprocess.run([str(script_dir / "run_branch.sh"), branch_id])
    if proc.returncode != 0:
        failed.append((branch_id, proc.returncode))
if failed:
    print("failed branches:", failed)
    raise SystemExit(1)
PY
""",
        encoding="utf-8",
    )
    run_all.chmod(run_all.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def build_bundle(
    *,
    instance_id: str,
    tasks_root: Path,
    blob_dir: Path | None,
    out_dir: Path,
    branches: list[str] | None,
    max_branches: int | None,
    mode: BundleMode,
) -> dict[str, Any]:
    mode = canonical_mode(mode)
    task_dir = tasks_root / instance_id
    if not task_dir.exists():
        raise FileNotFoundError(f"Unknown task: {instance_id}")

    selected_branches = branches or active_branch_ids(task_dir)
    if max_branches is not None:
        selected_branches = selected_branches[:max_branches]

    source_blob_dir = find_blob_dir(instance_id, blob_dir)
    tests_dir = source_blob_dir / "tests"
    if not tests_dir.exists():
        raise FileNotFoundError(f"Blob directory has no tests/ directory: {source_blob_dir}")

    bundle_root = out_dir / instance_id / "oracle_tests"
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    bundle_root.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "instance_id": instance_id,
        "mode": mode,
        "policy": {
            "description": (
                "Conservative sanitized executable-test bundle"
                if mode == "sanitized"
                else (
                    "Source-leak-guarded oracle-test bundle: keep as much official executable-test "
                    "material as possible while filtering source/build artifacts"
                )
            ),
            "allowed_prefixes": list(ALLOWED_PREFIXES if mode == "sanitized" else ORACLE_GUARDED_TOP_LEVEL_PREFIXES),
            "disallowed_names": sorted(DISALLOWED_NAMES),
            "source_suffixes_excluded_outside_fixtures": sorted(SOURCE_SUFFIXES),
        },
        "branches": [],
    }
    private_manifest: dict[str, Any] = {
        "instance_id": instance_id,
        "mode": mode,
        "source_blob_dir": str(source_blob_dir),
        "branches": [],
    }

    for branch_id in selected_branches:
        tar_path = tests_dir / f"{branch_id}.tar.gz"
        if not tar_path.exists():
            raise FileNotFoundError(f"Missing branch tarball: {tar_path}")

        branch_out = bundle_root / "branches" / branch_id
        branch_out.mkdir(parents=True, exist_ok=True)
        included: list[dict[str, str]] = []
        excluded: list[dict[str, str]] = []

        with tarfile.open(tar_path, "r:gz") as tar:
            for member in tar.getmembers():
                decision = decide_member(member.name, member.isfile(), mode=mode)
                row = {"path": member.name, "reason": decision.reason}
                if decision.include:
                    extract_member(tar, member, branch_out)
                    included.append(row)
                else:
                    excluded.append(row)

        excluded_reasons = Counter(item["reason"] for item in excluded)
        included_reasons = Counter(item["reason"] for item in included)
        manifest["branches"].append(
            {
                "branch_id": branch_id,
                "included_count": len(included),
                "excluded_count": len(excluded),
                "included_reason_counts": dict(sorted(included_reasons.items())),
                "excluded_reason_counts": dict(sorted(excluded_reasons.items())),
            }
        )
        private_manifest["branches"].append(
            {
                "branch_id": branch_id,
                "source_tar": str(tar_path),
                "included_count": len(included),
                "excluded_count": len(excluded),
                "included": included,
                "excluded": excluded,
            }
        )

    write_helper_scripts(bundle_root)
    title = (
        "Sanitized ProgramBench Oracle Tests"
        if mode == "sanitized"
        else "Source-Leak-Guarded ProgramBench Oracle Tests"
    )
    (bundle_root / "README.md").write_text(
        f"""# {title}

Instance: `{instance_id}`

This directory contains executable oracle tests built from official ProgramBench
test blobs. It intentionally excludes upstream implementation source files,
build metadata, and source-blob paths from the agent-visible manifest.

Run one branch:

```bash
./oracle_tests/run_branch.sh <branch-id>
```

Run all bundled branches:

```bash
./oracle_tests/run_all.sh
```

The tests expect `./compile.sh` to produce `./executable` in the workspace root.
If `./executable` does not exist, the helper script will try to run
`./compile.sh` before executing pytest.
""",
        encoding="utf-8",
    )
    (bundle_root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out_dir / instance_id / "oracle_tests_private_manifest.json").write_text(
        json.dumps(private_manifest, indent=2),
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance_id")
    parser.add_argument("--tasks-root", type=Path, default=DEFAULT_TASKS_ROOT)
    parser.add_argument("--blob-dir", type=Path, default=None, help="Instance blob dir or parent blob root")
    parser.add_argument("--out-dir", type=Path, default=Path("reports/test_bundles"))
    parser.add_argument("--branch", action="append", dest="branches", help="Branch id to include; repeatable")
    parser.add_argument("--max-branches", type=int, default=None)
    parser.add_argument(
        "--mode",
        choices=["sanitized", "oracle-guarded", "full-safe"],
        default="oracle-guarded",
        help="Use oracle-guarded for new upper-bound runs; full-safe is a legacy alias.",
    )
    args = parser.parse_args()

    manifest = build_bundle(
        instance_id=args.instance_id,
        tasks_root=args.tasks_root,
        blob_dir=args.blob_dir,
        out_dir=args.out_dir,
        branches=args.branches,
        max_branches=args.max_branches,
        mode=args.mode,
    )
    included_total = sum(branch["included_count"] for branch in manifest["branches"])
    excluded_total = sum(branch["excluded_count"] for branch in manifest["branches"])
    print(
        json.dumps(
            {
                "instance_id": manifest["instance_id"],
                "bundle_dir": str(args.out_dir / args.instance_id / "oracle_tests"),
                "branches": len(manifest["branches"]),
                "included_files": included_total,
                "excluded_files": excluded_total,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
