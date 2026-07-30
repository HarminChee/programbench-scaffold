#!/usr/bin/env python3
"""Build deterministic, GitHub-friendly V2 and V3 oracle-suite releases."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Suite:
    version: str
    repo: str
    instance_id: str
    shard: str
    oracle_root: str
    evidence: tuple[str, ...] = ()


V2_SUITES = (
    Suite("v2", "yj", "sclevine__yj.8016400", "final", "v2/runs/go10_v2_expansive_r2/generated/sclevine__yj.8016400/yj_v2_expansive_union/oracle_tests", (
        "v2/runs/go10_v2_expansive_r2/coverage/sclevine__yj.8016400/yj_v2_expansive_union.go_coverage_summary.json",
        "v2/runs/go10_v2_expansive_r2/generated/sclevine__yj.8016400/yj_v2_expansive_union/evaluation_quality_report.json",
    )),
    Suite("v2", "gron", "tomnomnom__gron.88a6234", "final", "v2/runs/go10_v2_expansive_r2/generated/tomnomnom__gron.88a6234/v2_expansive_final/oracle_tests", (
        "v2/runs/go10_v2_expansive_r2/coverage/tomnomnom__gron.88a6234/v2_expansive_final.go_coverage_summary.json",
        "v2/runs/go10_v2_expansive_r2/generated/tomnomnom__gron.88a6234/v2_expansive_final/evaluation_quality_report.json",
        "v2/runs/go10_v2_expansive_r2/formal/tomnomnom__gron.88a6234/v2_expansive_final/pipeline_summary.json",
    )),
    Suite("v2", "dsq", "multiprocessio__dsq.c3ae0ba", "final", "v2/runs/go10_v2_expansive_r2/generated/multiprocessio__dsq.c3ae0ba/v2_expansive_final/oracle_tests", (
        "v2/runs/go10_v2_expansive_r2/coverage/multiprocessio__dsq.c3ae0ba/v2_expansive_final.go_coverage_summary.json",
        "v2/runs/go10_v2_expansive_r2/generated/multiprocessio__dsq.c3ae0ba/v2_expansive_final/evaluation_quality_report.json",
        "v2/runs/go10_v2_expansive_r2/formal/multiprocessio__dsq.c3ae0ba/v2_expansive_final/pipeline_summary.json",
    )),
    Suite("v2", "go-mod-outdated", "psampaz__go-mod-outdated.bb79367", "final", "v2/runs/go10_v2_expansive_r2/generated/psampaz__go-mod-outdated.bb79367/v2_expansive_final/oracle_tests", (
        "v2/runs/go10_v2_expansive_r2/coverage/psampaz__go-mod-outdated.bb79367/v2_expansive_final.go_coverage_summary.json",
        "v2/runs/go10_v2_expansive_r2/generated/psampaz__go-mod-outdated.bb79367/v2_expansive_final/evaluation_quality_report.json",
        "v2/runs/go10_v2_expansive_r2/formal/psampaz__go-mod-outdated.bb79367/v2_expansive_final/pipeline_summary.json",
    )),
    Suite("v2", "jplot", "rs__jplot.2a54bcc", "final", "v2/runs/go10_v2_expansive_r2/generated/rs__jplot.2a54bcc/v2_expansive_final/oracle_tests", (
        "v2/runs/go10_v2_expansive_r2/coverage/rs__jplot.2a54bcc/v2_expansive_final.go_coverage_summary.json",
        "v2/runs/go10_v2_expansive_r2/generated/rs__jplot.2a54bcc/v2_expansive_final/evaluation_quality_report.json",
        "v2/runs/go10_v2_expansive_r2/formal/rs__jplot.2a54bcc/v2_expansive_final/pipeline_summary.json",
    )),
    Suite("v2", "dupl", "mibk__dupl.1bf052b", "final", "v2/runs/go10_v2_expansive_r2/generated/mibk__dupl.1bf052b/v2_expansive_final/oracle_tests", (
        "v2/runs/go10_v2_expansive_r2/coverage/mibk__dupl.1bf052b/v2_expansive_final.go_coverage_summary.json",
        "v2/runs/go10_v2_expansive_r2/generated/mibk__dupl.1bf052b/v2_expansive_final/evaluation_quality_report.json",
        "v2/runs/go10_v2_expansive_r2/formal/mibk__dupl.1bf052b/v2_expansive_final/pipeline_summary.json",
    )),
    Suite("v2", "bat", "astaxie__bat.17d1080", "final", "v2/runs/go10_v2_expansive_r2/generated/astaxie__bat.17d1080/v2_expansive_final/oracle_tests", (
        "v2/runs/go10_v2_expansive_r2/coverage/astaxie__bat.17d1080/v2_expansive_final.go_coverage_summary.json",
        "v2/runs/go10_v2_expansive_r2/generated/astaxie__bat.17d1080/v2_expansive_final/evaluation_quality_report.json",
        "v2/runs/go10_v2_expansive_r2/formal/astaxie__bat.17d1080/v2_expansive_final/pipeline_summary.json",
    )),
    Suite("v2", "cheat", "cheat__cheat.b8098dc", "final", "v2/runs/go10_v2_expansive_r2/generated/cheat__cheat.b8098dc/v2_expansive_final/oracle_tests", (
        "v2/runs/go10_v2_expansive_r2/coverage/cheat__cheat.b8098dc/v2_expansive_final.go_coverage_summary.json",
        "v2/runs/go10_v2_expansive_r2/generated/cheat__cheat.b8098dc/v2_expansive_final/evaluation_quality_report.json",
        "v2/runs/go10_v2_expansive_r2/formal/cheat__cheat.b8098dc/v2_expansive_final/pipeline_summary.json",
    )),
    Suite("v2", "scc", "boyter__scc.515f91c", "final", "v2/runs/go10_v2_expansive_r2/generated/boyter__scc.515f91c/v2_expansive_final/oracle_tests", (
        "v2/runs/go10_v2_expansive_r2/coverage/boyter__scc.515f91c/v2_expansive_final.go_coverage_summary.json",
        "v2/runs/go10_v2_expansive_r2/generated/boyter__scc.515f91c/v2_expansive_final/evaluation_quality_report.json",
        "v2/runs/go10_v2_expansive_r2/formal/boyter__scc.515f91c/v2_expansive_final/pipeline_summary.json",
    )),
    Suite("v2", "chroma", "alecthomas__chroma.8d04def", "final", "v2/runs/go10_v2_expansive_r2/generated/alecthomas__chroma.8d04def/v2_expansive_final/oracle_tests", (
        "v2/runs/go10_v2_expansive_r2/coverage/alecthomas__chroma.8d04def/v2_expansive_final.go_coverage_summary.json",
        "v2/runs/go10_v2_expansive_r2/generated/alecthomas__chroma.8d04def/v2_expansive_final/evaluation_quality_report.json",
        "v2/runs/go10_v2_expansive_r2/formal/alecthomas__chroma.8d04def/v2_expansive_final/pipeline_summary.json",
    )),
)


V3_SUITES = (
    Suite("v3", "yj", "sclevine__yj.8016400", "final", "v3/runs/go10_v3_quality_r2/generated/sclevine__yj.8016400/v3_final/oracle_tests"),
    Suite("v3", "gron", "tomnomnom__gron.88a6234", "final", "v3/runs/go10_v3_quality_r2/generated/tomnomnom__gron.88a6234/v3_final/oracle_tests"),
    Suite("v3", "dsq", "multiprocessio__dsq.c3ae0ba", "final", "v3/runs/go10_v3_quality_r2/generated/multiprocessio__dsq.c3ae0ba/v3_final/oracle_tests"),
    Suite("v3", "go-mod-outdated", "psampaz__go-mod-outdated.bb79367", "final", "v3/runs/go10_v3_quality_r2/generated/psampaz__go-mod-outdated.bb79367/v3_final/oracle_tests"),
    Suite("v3", "dupl", "mibk__dupl.1bf052b", "final", "v3/runs/go10_v3_quality_r2/generated/mibk__dupl.1bf052b/v3_final/oracle_tests"),
    Suite("v3", "cheat", "cheat__cheat.b8098dc", "final", "v3/runs/go10_v3_quality_r2/generated/cheat__cheat.b8098dc/v3_final/oracle_tests"),
    Suite("v3", "jplot", "rs__jplot.2a54bcc", "final", "v3/runs/go4_v3_reachability_final_20260729/jplot/v3_final/oracle_tests"),
    Suite("v3", "bat", "astaxie__bat.17d1080", "final", "v3/runs/go4_v3_reachability_final_20260729/bat/v3_final/oracle_tests"),
    Suite("v3", "scc", "boyter__scc.515f91c", "final", "v3/runs/go4_v3_reachability_final_20260729/scc/v3_final/oracle_tests"),
    Suite("v3", "chroma", "alecthomas__chroma.8d04def", "r4", "v3/runs/go4_v3_reachability_final_20260729/chroma_r4/v3_final/oracle_tests"),
    Suite("v3", "chroma", "alecthomas__chroma.8d04def", "r5", "v3/runs/go4_v3_reachability_final_20260729/chroma_r5/v3_final/oracle_tests"),
    Suite("v3", "chroma", "alecthomas__chroma.8d04def", "r6", "v3/runs/go4_v3_reachability_final_20260729/chroma_r6/v3_final/oracle_tests"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    return info


def make_archive(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as archive:
                for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
                    arcname = Path("oracle_tests") / path.relative_to(source)
                    archive_path: str | Path = path
                    if os.name == "nt":
                        archive_path = "\\\\?\\" + str(path.resolve())
                    archive.add(archive_path, arcname=arcname.as_posix(), recursive=False, filter=stable_tar_filter)


def manifest_cases(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError(f"Expected a cases list in {path}")
    return len(cases)


def safe_reset(path: Path) -> None:
    resolved_root = ROOT.resolve()
    resolved = path.resolve()
    if resolved.parent.parent != resolved_root:
        raise RuntimeError(f"Refusing to reset unexpected path: {resolved}")
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def evidence_for_v3(suite: Suite) -> Iterable[Path]:
    if suite.repo in {"yj", "gron", "dsq", "go-mod-outdated", "dupl", "cheat"}:
        base = ROOT / "v3/runs/go10_v3_quality_r2"
        yield base / "coverage" / suite.instance_id / "v3_final.go_coverage_summary.json"
        yield base / "generated" / suite.instance_id / "v3_final/evaluation_quality_report.json"
        yield base / suite.instance_id / "pipeline_summary.json"
        return
    base = ROOT / "v3/runs/go4_v3_reachability_final_20260729"
    if suite.repo != "chroma":
        yield base / suite.repo / "coverage" / suite.instance_id / "v3_final.go_coverage_summary.json"
        yield base / suite.repo / "v3_final/evaluation_quality_report.json"
        yield base / suite.repo / "pipeline_summary.json"
        return
    shard = base / f"chroma_{suite.shard}"
    coverage = shard / "coverage" / suite.instance_id / "v3_final.go_coverage_summary.json"
    if coverage.is_file():
        yield coverage
    alternate_coverage = shard / suite.instance_id / "v3_final.go_coverage_summary.json"
    if alternate_coverage.is_file() and alternate_coverage != coverage:
        yield alternate_coverage
    yield shard / "v3_final/evaluation_quality_report.json"
    yield shard / "pipeline_summary.json"


def build_version(version: str, suites: tuple[Suite, ...]) -> list[dict[str, object]]:
    release_root = ROOT / version / "published_tests"
    safe_reset(release_root)
    rows: list[dict[str, object]] = []
    for suite in suites:
        source = ROOT / suite.oracle_root
        manifest = source / "eval/generated_cli_manifest.json"
        pytest_file = source / "eval/tests/test_generated_cli_oracle.py"
        for required in (source, manifest, pytest_file):
            if not required.exists():
                raise FileNotFoundError(required)

        destination = release_root / suite.repo
        if suite.shard != "final":
            destination /= suite.shard
        destination.mkdir(parents=True, exist_ok=True)
        manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
        case_rows = manifest_payload.get("cases")
        if not isinstance(case_rows, list):
            raise ValueError(f"Expected a cases list in {manifest}")
        with (destination / "CASE_INDEX.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(("index", "name", "area", "origin", "rationale"))
            for index, case in enumerate(case_rows):
                writer.writerow((
                    index,
                    case.get("name") or "",
                    case.get("area") or "",
                    case.get("origin") or "",
                    case.get("rationale") or "",
                ))
        archive = destination / "oracle_tests.tar.gz"
        make_archive(source, archive)

        evidence_dir = destination / "evidence"
        evidence_paths = tuple(ROOT / item for item in suite.evidence) if suite.evidence else tuple(evidence_for_v3(suite))
        copied_evidence: list[str] = []
        for evidence in evidence_paths:
            if not evidence.is_file():
                raise FileNotFoundError(evidence)
            evidence_dir.mkdir(exist_ok=True)
            target = evidence_dir / evidence.name
            shutil.copy2(evidence, target)
            copied_evidence.append(target.relative_to(ROOT / version).as_posix())

        case_count = manifest_cases(manifest)
        readme = destination / "README.md"
        readme.write_text(
            "\n".join((
                f"# {suite.repo} - Oracle Gym {version.upper()}",
                "",
                f"- ProgramBench instance: `{suite.instance_id}`",
                f"- Published shard: `{suite.shard}`",
                f"- Behavioral cases: **{case_count:,}**",
                "- `CASE_INDEX.csv`: a GitHub-browsable case name/area/origin index.",
                "- `oracle_tests.tar.gz`: the complete suite, including all fixtures and captured stdout/stderr.",
                "  It contains `eval/generated_cli_manifest.json` and `eval/tests/test_generated_cli_oracle.py`.",
                "- `evidence/`: coverage, quality-gate, and pipeline summaries.",
                "",
                "Extract the complete suite with:",
                "",
                "```bash",
                "tar -xzf oracle_tests.tar.gz",
                "```",
                "",
            )),
            encoding="utf-8",
        )
        rows.append({
            "repo": suite.repo,
            "instance_id": suite.instance_id,
            "shard": suite.shard,
            "behavioral_cases": case_count,
            "archive": archive.relative_to(ROOT / version).as_posix(),
            "archive_bytes": archive.stat().st_size,
            "archive_sha256": sha256(archive),
            "source_artifact": suite.oracle_root,
            "evidence": copied_evidence,
        })

    output = ROOT / version / "PUBLISHED_ARTIFACTS.json"
    output.write_text(json.dumps({
        "schema": "programbench_oracle_gym_published_artifacts_v1",
        "version": version,
        "complete": True,
        "suites": rows,
    }, indent=2) + "\n", encoding="utf-8")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", choices=("v2", "v3", "all"), default="all")
    args = parser.parse_args()
    rows: list[dict[str, object]] = []
    if args.version in {"v2", "all"}:
        rows.extend(build_version("v2", V2_SUITES))
    if args.version in {"v3", "all"}:
        rows.extend(build_version("v3", V3_SUITES))
    print(json.dumps({"packaged_suites": len(rows), "behavioral_cases": sum(int(row["behavioral_cases"]) for row in rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
