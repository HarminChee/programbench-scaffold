#!/usr/bin/env python3
"""Build an integrity and content manifest for protected experiment archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_tar(path: Path) -> dict[str, object]:
    with tarfile.open(path, "r:*") as tar:
        names = [member.name for member in tar.getmembers() if member.isfile()]
    return {
        "file_count": len(names),
        "runnable_test_files": sum(x.endswith("/test_generated_cli_oracle.py") for x in names),
        "candidate_ledgers": sum(x.endswith("/candidates/current.json") for x in names),
        "recovery_checkpoints": sum(x.endswith("/recovery_checkpoint.json") for x in names),
        "settlement_summaries": sum(x.endswith("/frozen/settlement_summary.json") for x in names),
        "pipeline_summaries": sum(x.endswith("/frozen/pipeline_summary.json") for x in names),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("collection", type=Path)
    args = parser.parse_args()
    root = args.collection.resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)

    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in {"COLLECTION_MANIFEST.json", "DO_NOT_DELETE"}:
            continue
        row: dict[str, object] = {
            "path": str(path.relative_to(root)),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        if path.name.endswith((".tar", ".tar.gz", ".tgz")):
            row["tar_content"] = inspect_tar(path)
        files.append(row)

    legacy = [row for row in files if str(row["path"]).startswith("old16-metadata/")]
    legacy_has_tests = any(
        int(row.get("tar_content", {}).get("runnable_test_files", 0)) > 0 for row in legacy
    )
    manifest = {
        "schema": "programbench_protected_result_collection_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "do_not_delete": True,
        "collection": str(root),
        "files": files,
        "legacy_old16_content_status": {
            "runnable_v4_tests_present": legacy_has_tests,
            "classification": "complete" if legacy_has_tests else "metadata_only",
            "note": (
                "The preserved old16 archive contains checkpoints and settlement evidence but no runnable V4 test files."
                if not legacy_has_tests
                else "Runnable V4 tests are present."
            ),
        },
    }
    (root / "COLLECTION_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (root / "DO_NOT_DELETE").write_text(
        "Protected ProgramBench frozen results. Consult COLLECTION_MANIFEST.json before cleanup.\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
