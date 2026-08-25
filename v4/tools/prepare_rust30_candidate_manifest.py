#!/usr/bin/env python3
"""Resolve and gate a curated PB-external Rust30 cohort against GitHub."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from v4.programbench_v4.io import atomic_write_json


CANDIDATES: dict[str, dict[str, Any]] = {
    "sharkdp/sub": {"difficulty":"easy","binary":"sub","themes":["string substitutions","stdin and files","regex errors and Unicode"]},
    "solidiquis/erdtree": {"difficulty":"medium","binary":"erd","themes":["directory trees","filters and depth","hidden/gitignore and output modes"]},
    "dflemstr/rq": {"difficulty":"medium","binary":"rq","themes":["record parsing","query and transforms","formats and malformed records"]},
    "willdoescode/nat": {"difficulty":"easy","binary":"natls","themes":["directory listing","sort and display","hidden and Unicode paths"]},
    "vishaltelangre/ff": {"difficulty":"easy","binary":"ff","themes":["filename search","directory traversal","patterns and errors"]},
    "marcusbuffett/pipe-rename": {"difficulty":"medium","binary":"renamer","themes":["batch path renaming","stdin edit plans","collisions, Unicode, and filesystem errors"]},
    "sorairolake/qrtool": {"difficulty":"medium","binary":"qrtool","themes":["QR encode/decode","text and image fixtures","format options and errors"]},
    "typst/hayagriva": {"difficulty":"medium","binary":"hayagriva","themes":["bibliography formats","citation transforms","Unicode and malformed records"]},
    "Doctave/doctave": {"difficulty":"medium","binary":"doctave","themes":["documentation builds","config and links","Markdown trees and diagnostics"]},
    "avencera/rustywind": {"difficulty":"medium","binary":"rustywind","themes":["Tailwind class sorting","stdin and files","framework syntax and errors"]},
    "oberblastmeister/trashy": {"difficulty":"medium","binary":"trashy","themes":["trash/list/restore","isolated HOME state","collisions and errors"]},
    "SUPERCILEX/fuc": {"difficulty":"medium","binary":"rmz","themes":["filesystem removal","recursive and force modes","permissions and errors"]},
    "fioncat/otree": {"difficulty":"medium","binary":"otree","themes":["JSON/YAML/TOML/XML trees","queries and display","malformed structured data"]},
    "jez/as-tree": {"difficulty":"easy","binary":"as-tree","themes":["path-list trees","sorting and prefixes","empty and Unicode paths"]},
    "darakian/ddh": {"difficulty":"easy","binary":"ddh","themes":["duplicate discovery","hash and size","nested fixtures and errors"]},
    "anistark/feluda": {"difficulty":"medium","binary":"feluda","themes":["license scanning","project manifests","policy output and malformed files"]},
    "nickgerace/gfold": {"difficulty":"medium","binary":"gfold","themes":["Git repository discovery","status and filtering","nested repositories and config"]},
    "sioodmy/todo": {"difficulty":"easy","binary":"todo","themes":["todo CRUD","isolated state","filtering and malformed commands"]},
    "casey/intermodal": {
        "difficulty":"medium",
        "binary":"imdl",
        "themes":["torrent creation and inspection","piece/metadata options","verification and malformed torrents"],
        "omit_unsafe_symlink_paths":[
            "book/src/SUMMARY.md",
            "book/src/bittorrent.md",
            "book/src/commands",
            "book/src/commands.md",
            "book/src/faq.md",
            "book/src/introduction.md",
            "book/src/references",
            "book/src/references.md"
        ]
    },
    "mattrighetti/envelope": {"difficulty":"medium","binary":"envelope","themes":["environment variable storage","SQLite-backed state","import/export and errors"]},
    "Misterio77/flavours": {"difficulty":"medium","binary":"flavours","themes":["base16 scheme rendering","templates and config","list/apply and malformed schemes"]},
    "zdk/lowfat": {"difficulty":"easy","binary":"lowfat","themes":["command-output filtering","stdin transforms","presets and boundaries"]},
    "micahkepe/jsongrep": {"difficulty":"medium","binary":"jg","themes":["structured path queries","JSON/YAML/TOML input","filters and malformed data"]},
    "koraa/huniq": {"difficulty":"easy","binary":"huniq","themes":["duplicate-line counting","stdin and files","Unicode and large counts"]},
    "dskkato/rjo": {"difficulty":"easy","binary":"rjo","themes":["JSON output construction","typed values and nesting","escaping and malformed arguments"]},
    "phiresky/ripgrep-all": {"difficulty":"medium","binary":"rga","themes":["text search adapters","local document fixtures","filters and cache behavior"]},
    "m4b/bingrep": {"difficulty":"medium","binary":"bingrep","themes":["binary symbol inspection","ELF fixtures","filters and malformed binaries"]},
    "dalance/procs": {"difficulty":"medium","binary":"procs","themes":["process listing","columns and filters","deterministic explicit pid errors"]},
    "Ben-Lichtman/ropr": {"difficulty":"medium","binary":"ropr","themes":["ROP gadget discovery","binary format fixtures","architecture and malformed input"]},
    "ndd7xv/heh": {"difficulty":"medium","binary":"heh","themes":["hex viewing/edit commands","binary fixtures","offsets and boundaries"]}
}


def gh(path: str) -> dict[str, Any]:
    executable = shutil.which("gh") or shutil.which("gh.exe")
    if not executable:
        raise RuntimeError("GitHub CLI is unavailable")
    last_error = ""
    for attempt in range(3):
        done = subprocess.run([executable, "api", "-H", "Accept: application/vnd.github+json", path],
                              text=True, capture_output=True, timeout=120)
        if done.returncode == 0:
            return json.loads(done.stdout)
        last_error = done.stderr[-1000:]
        if attempt < 2:
            time.sleep(2 ** attempt)
    raise RuntimeError(last_error)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pb-catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pb = json.loads(args.pb_catalog.read_text(encoding="utf-8"))
    pb_names = {str(row["repository"]).lower() for row in pb.get("rows") or []}
    rows = []
    for repository, selection in CANDIDATES.items():
        if repository.lower() in pb_names:
            raise RuntimeError(f"candidate is in ProgramBench: {repository}")
        metadata = gh(f"repos/{repository}")
        if metadata.get("archived") or metadata.get("disabled"):
            raise RuntimeError(f"candidate is archived/disabled: {repository}")
        if str(metadata.get("language") or "").lower() != "rust":
            raise RuntimeError(f"candidate primary language is not Rust: {repository}")
        commit = gh(f"repos/{repository}/commits/{metadata['default_branch']}")["sha"]
        rows.append({
            "instance_id": repository.lower().replace("/", "__").replace(".", "-") + "." + commit[:7],
            "repository": repository,
            "commit": commit,
            "language": "rust",
            "license": (metadata.get("license") or {}).get("spdx_id"),
            "stars": metadata.get("stargazers_count"),
            "github_size_kib": metadata.get("size"),
            "default_branch": metadata.get("default_branch"),
            "updated_at": metadata.get("updated_at"),
            **selection,
        })
    if len(rows) != 30:
        raise RuntimeError("Rust30 selection must contain exactly 30 repositories")
    atomic_write_json(args.output, {
        "schema": "programbench_v4_rust30_candidate_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "programbench_catalog": str(args.pb_catalog.resolve()),
        "programbench_exclusion_verified": True,
        "repositories": rows,
    })
    print(json.dumps({"repositories": len(rows), "output": str(args.output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
