from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from programbench_generate_cli_oracle_bundle import run_reference_case, write_generated_pytest  # noqa: E402


PYTHON_CASE_CODE = (
    "from pathlib import Path;import os,sys,urllib.request;"
    "print(Path('input.txt').read_text().strip());"
    "print(os.environ['PB_TEST_MODE']);"
    "print(urllib.request.urlopen(sys.argv[1]).read().decode())"
)


def fixture_case() -> dict:
    return {
        "name": "file_env_http",
        "area": "fixture_dsl",
        "argv0": "python3",
        "args": ["-c", PYTHON_CASE_CODE, "{http_url}"],
        "stdin": "",
        "env": {"PB_TEST_MODE": "env-value"},
        "files": {"input.txt": "file-value\n"},
        "http": {
            "path": "/data.txt",
            "status": 200,
            "headers": {"Content-Type": "text/plain"},
            "body": "http-value",
        },
    }


def test_reference_capture_and_generated_pytest_share_fixture_semantics(tmp_path: Path) -> None:
    python = Path(sys.executable).resolve()
    case = fixture_case()
    # CPython discovers its prefix from argv[0].  Point argv[0] at the real
    # interpreter so this executable-as-reference test does not emit an
    # unrelated uv standalone-runtime prefix warning on stderr.
    case["argv0"] = str(python)
    observed = run_reference_case(python, case, timeout=10)
    assert observed["returncode"] == 0
    assert observed["stderr"] == b""
    assert observed["stdout"] == b"file-value\nenv-value\nhttp-value\n"

    bundle = tmp_path / "oracle_tests"
    fixture_dir = bundle / "eval" / "fixtures" / "fixture-dsl"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "case.stdin").write_bytes(b"")
    (fixture_dir / "case.stdout").write_bytes(b"http-value\nenv-value\nfile-value\n")
    (fixture_dir / "case.stderr").write_bytes(observed["stderr"])
    manifest_case = {
        **case,
        "returncode": 0,
        "stdout_mode": "lines_unordered",
        "timeout": 10,
        "stdin_file": "case.stdin",
        "stdout_file": "case.stdout",
        "stderr_file": "case.stderr",
    }
    manifest = {"fixture_subdir": "fixture-dsl", "cases": [manifest_case]}
    (bundle / "eval" / "generated_cli_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    shutil.copy2(python, bundle / "executable")
    (bundle / "executable").chmod(0o755)
    write_generated_pytest(bundle, "fixture_dsl", [manifest_case])

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "eval/tests"],
        cwd=bundle,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
