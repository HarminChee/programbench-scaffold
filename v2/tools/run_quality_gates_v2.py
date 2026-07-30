#!/usr/bin/env python3
"""V2 compatibility entry point for quality gates.

V1's implementation creates an isolated pytest environment but invokes pytest
with xdist workers.  V2 keeps the original untouched and installs pytest-xdist
in its own quality-gate environment before delegating to the same gate logic.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT_TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(ROOT_TOOLS))
import programbench_run_generated_oracle_quality_gates as base  # noqa: E402


original = base.install_pytest_venv
original_run_pytest_bundle = base.run_pytest_bundle
original_copytree = base.shutil.copytree
original_fixed_workspace_executable = base.fixed_workspace_executable


def install_pytest_venv_with_xdist(venv_dir: Path, logs_dir: Path) -> Path:
    python = original(venv_dir, logs_dir)
    result = base.run_command(
        [str(python), "-m", "pip", "install", "-q", "pytest-xdist"],
        timeout=900,
        log_path=logs_dir / "pip_install_pytest_xdist_v2.json",
    )
    if result["returncode"] != 0:
        raise RuntimeError(f"pytest-xdist install failed: {result['stderr_tail']}")
    return python


base.install_pytest_venv = install_pytest_venv_with_xdist


def run_pytest_bundle_v2(*args, **kwargs):
    """Use bounded local parallelism for expansive V2 suites.

    Four dummy gates run concurrently, so four workers per gate keeps the
    machine at 16 pytest workers instead of oversubscribing it with 64.
    """
    requested = int(kwargs.get("pytest_workers", 1))
    if requested == 1:
        kwargs["pytest_workers"] = 4
    return original_run_pytest_bundle(*args, **kwargs)


base.run_pytest_bundle = run_pytest_bundle_v2


def copytree_v2(src, dst, *args, **kwargs):
    """Hardlink immutable oracle fixtures on ext4, with a safe copy fallback."""
    positional = list(args)
    if len(positional) < 3:
        kwargs.setdefault("copy_function", os.link)
    try:
        result = original_copytree(src, dst, *positional, **kwargs)
    except OSError:
        destination = Path(dst)
        if destination.exists():
            base.shutil.rmtree(destination)
        if len(positional) >= 3:
            positional[2] = base.shutil.copy2
        else:
            kwargs["copy_function"] = base.shutil.copy2
        result = original_copytree(src, dst, *positional, **kwargs)
    # Quality runs already copy one executable into each isolated workspace.
    # Point the copied manifest at that executable so independent dummy gates
    # do not serialize on the legacy global /workspace/executable lock.
    destination = Path(dst)
    is_repeat_workspace = "repeat_workspace" in destination.parts
    for name in ("generated_cli_manifest.json", "generated_yj_manifest.json"):
        manifest = destination / name
        if not manifest.is_file() or is_repeat_workspace:
            continue
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        changed = False
        for case in payload.get("cases") or []:
            if case.get("argv0") == "/workspace/executable":
                case["argv0"] = str(destination.parent / "executable")
                changed = True
        if changed:
            # copytree uses hardlinks for speed.  Writing the destination in
            # place would therefore mutate the shared source manifest and race
            # the other concurrent dummy gates.  Atomic replacement breaks the
            # hardlink before publishing the workspace-specific manifest.
            temporary = manifest.with_name(f".{manifest.name}.v2.tmp")
            temporary.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, manifest)
    return result


base.shutil.copytree = copytree_v2


@contextlib.contextmanager
def isolated_workspace_executable(executable: Path):
    """Use the PB-standard alias for repeat; isolate concurrent dummy gates."""
    if "repeat_workspace" in executable.parts:
        with original_fixed_workspace_executable(executable):
            yield
    else:
        yield


base.fixed_workspace_executable = isolated_workspace_executable

if __name__ == "__main__":
    raise SystemExit(base.main())
