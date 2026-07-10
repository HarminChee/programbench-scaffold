"""Generated black-box CLI oracle tests."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


WORKSPACE = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parents[1]
MANIFEST_PATH = EVAL_DIR / "generated_cli_manifest.json"
if not MANIFEST_PATH.exists():
    MANIFEST_PATH = EVAL_DIR / "generated_yj_manifest.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
FIXTURE_DIR = EVAL_DIR / "fixtures" / MANIFEST.get("fixture_subdir", "generated_cli")
CASES = MANIFEST["cases"]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_generated_black_box_behavior(case: dict, tmp_path: Path) -> None:
    executable = WORKSPACE / "executable"
    assert executable.exists(), f"missing executable at {executable}"

    stdin = (FIXTURE_DIR / case["stdin_file"]).read_bytes()
    expected_stdout = (FIXTURE_DIR / case["stdout_file"]).read_bytes()
    expected_stderr = (FIXTURE_DIR / case["stderr_file"]).read_bytes()
    env = os.environ.copy()
    env["TZ"] = "UTC"
    env.update(case.get("env", {}))

    argv0 = case.get("argv0", "/workspace/executable")
    proc = subprocess.run(
        [argv0, *case["args"]],
        executable=str(executable),
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=tmp_path,
        env=env,
        timeout=case.get("timeout", 5),
    )

    assert proc.returncode == case["returncode"]
    assert proc.stdout == expected_stdout
    assert proc.stderr == expected_stderr
