#!/usr/bin/env python3
"""Verify published V2/V3 counts, hashes, archives, and result consistency."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify(version: str) -> dict[str, int]:
    base = ROOT / version
    for required in ("README.md", "REPRODUCE.md", "RESULTS.md", "PUBLISHED_ARTIFACTS.json"):
        if not (base / required).is_file():
            raise FileNotFoundError(base / required)
    publication = load(base / "PUBLISHED_ARTIFACTS.json")
    suites = publication["suites"]
    if len({row["repo"] for row in suites}) != 10:
        raise ValueError(f"{version}: expected exactly ten repositories")
    total_cases = 0
    for row in suites:
        archive = base / row["archive"]
        case_index = archive.parent / "CASE_INDEX.csv"
        for path in (archive, case_index):
            if not path.is_file():
                raise FileNotFoundError(path)
        actual_hash = sha256(archive)
        if actual_hash != row["archive_sha256"]:
            raise ValueError(f"{version}: hash mismatch for {archive}")
        with tarfile.open(archive, "r:gz") as tar:
            names = set(tar.getnames())
            manifest_name = "oracle_tests/eval/generated_cli_manifest.json"
            if manifest_name not in names:
                raise ValueError(f"{version}: manifest missing from {archive}")
            if "oracle_tests/eval/tests/test_generated_cli_oracle.py" not in names:
                raise ValueError(f"{version}: pytest adapter missing from {archive}")
            member = tar.extractfile(manifest_name)
            if member is None:
                raise ValueError(f"{version}: cannot read manifest from {archive}")
            cases = json.loads(member.read().decode("utf-8")).get("cases")
        if not isinstance(cases, list) or len(cases) != row["behavioral_cases"]:
            raise ValueError(f"{version}: case-count mismatch for {archive}")
        total_cases += len(cases)

    if version == "v2":
        report_rows = load(base / "reports/go10_v2_release_results.json")
        report_total = sum(row["duplicates"]["cases"] for row in report_rows)
    else:
        report_rows = load(base / "reports/go10_v3_release_results.json")["rows"]
        report_total = sum(row["v3_behavioral_cases"] for row in report_rows)
    if len(report_rows) != 10 or report_total != total_cases:
        raise ValueError(f"{version}: publication/report total mismatch")
    return {"repositories": 10, "archives": len(suites), "behavioral_cases": total_cases}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", choices=("v2", "v3", "all"), default="all")
    args = parser.parse_args()
    versions = ("v2", "v3") if args.version == "all" else (args.version,)
    result = {version: verify(version) for version in versions}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
