import base64
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "v3" / "tools" / "materialize_source_fixtures_v3.py"


def test_materializes_text_binary_and_glob_with_provenance(tmp_path):
    source = tmp_path / "source"
    (source / "testdata").mkdir(parents=True)
    (source / "testdata" / "a.txt").write_text("hello\n", encoding="utf-8")
    (source / "testdata" / "b.bin").write_bytes(b"\x00\xff")
    candidates = tmp_path / "input.json"
    candidates.write_text(json.dumps({"cases": [{
        "name": "assets",
        "source_files": {"corpus/a.txt": "testdata/a.txt"},
        "source_globs": [{"pattern": "testdata/*.bin", "dest_root": "copied", "max_files": 2}],
        "args": ["corpus/a.txt", "copied/testdata/b.bin"],
    }]}), encoding="utf-8")
    output = tmp_path / "output.json"
    subprocess.run([
        sys.executable, str(SCRIPT), "--input", str(candidates),
        "--source-dir", str(source), "--output", str(output),
    ], check=True)
    payload = json.loads(output.read_text(encoding="utf-8"))
    case = payload["cases"][0]
    assert case["files"]["corpus/a.txt"] == "hello\n"
    assert base64.b64decode(case["binary_files"]["copied/testdata/b.bin"]) == b"\x00\xff"
    assert "source_files" not in case and "source_globs" not in case
    assert len(payload["source_fixture_materialization"]["provenance"][0]["assets"]) == 2


def test_rejects_escape_without_aborting_other_cases(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    candidates = tmp_path / "input.json"
    candidates.write_text(json.dumps({"cases": [
        {"name": "bad", "source_files": {"x": "../secret"}},
        {"name": "good", "args": ["--help"]},
    ]}), encoding="utf-8")
    output = tmp_path / "output.json"
    subprocess.run([
        sys.executable, str(SCRIPT), "--input", str(candidates),
        "--source-dir", str(source), "--output", str(output),
    ], check=True)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert [case["name"] for case in payload["cases"]] == ["good"]
    assert payload["source_fixture_materialization"]["rejected"][0]["name"] == "bad"
