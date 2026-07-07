#!/usr/bin/env python3
"""Select candidate GitHub repositories for ProgramBench Gym scaling.

This tool does not certify repositories. It creates a first-wave candidate list
that must later pass the Gym builder gates: pinned commit, offline build,
reference smoke, tests pass on reference, dummy fails, source-leak sanitization,
and license review.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_ROOT = REPO_ROOT / "external/ProgramBench/src/programbench/data/tasks"
PERMISSIVE_LICENSES = {
    "Apache-2.0",
    "MIT",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "MPL-2.0",
    "Unlicense",
    "Zlib",
}
MAX_SIZE_KB = 150_000
BLOCK_TERMS = {
    "ai",
    "assistant",
    "aws",
    "blockchain",
    "cloud",
    "database",
    "db",
    "email",
    "fuzzy",
    "game",
    "gui",
    "http-client",
    "http-server",
    "imap",
    "interactive",
    "jira",
    "kubernetes",
    "load-testing",
    "network",
    "notification",
    "smtp",
    "spotify",
    "server",
    "tui",
    "wayland",
    "wasm",
}
POSITIVE_TERMS = {
    "csv",
    "diff",
    "file",
    "find",
    "format",
    "formatter",
    "grep",
    "json",
    "markdown",
    "parse",
    "parser",
    "query",
    "text",
    "toml",
    "transform",
    "xml",
    "yaml",
}

LANGUAGE_TARGETS = {
    "Rust": 10,
    "Go": 6,
    "C": 2,
    "C++": 2,
}

SEARCH_QUERIES = {
    "Rust": [
        "language:Rust topic:cli stars:100..8000 pushed:>2025-01-01 archived:false fork:false",
        "language:Rust command-line stars:100..8000 pushed:>2025-01-01 archived:false fork:false",
        "language:Rust json cli stars:50..8000 pushed:>2024-01-01 archived:false fork:false",
        "language:Rust csv cli stars:50..8000 pushed:>2024-01-01 archived:false fork:false",
        "language:Rust text cli stars:50..8000 pushed:>2024-01-01 archived:false fork:false",
    ],
    "Go": [
        "language:Go topic:cli stars:100..8000 pushed:>2025-01-01 archived:false fork:false",
        "language:Go command-line stars:100..8000 pushed:>2025-01-01 archived:false fork:false",
        "language:Go json cli stars:50..8000 pushed:>2024-01-01 archived:false fork:false",
        "language:Go csv cli stars:50..8000 pushed:>2024-01-01 archived:false fork:false",
        "language:Go text cli stars:50..8000 pushed:>2024-01-01 archived:false fork:false",
    ],
    "C": [
        "language:C cli stars:100..8000 pushed:>2025-01-01 archived:false fork:false",
        "language:C command-line stars:100..8000 pushed:>2025-01-01 archived:false fork:false",
        "language:C json cli stars:50..8000 pushed:>2024-01-01 archived:false fork:false",
        "language:C text cli stars:50..8000 pushed:>2024-01-01 archived:false fork:false",
    ],
    "C++": [
        "language:C++ cli stars:100..8000 pushed:>2025-01-01 archived:false fork:false",
        "language:C++ command-line stars:100..8000 pushed:>2025-01-01 archived:false fork:false",
        "language:C++ json cli stars:50..8000 pushed:>2024-01-01 archived:false fork:false",
        "language:C++ text cli stars:50..8000 pushed:>2024-01-01 archived:false fork:false",
    ],
}


def parse_simple_yaml(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value and not value.startswith("[") and not value.startswith("{"):
            data[key.strip()] = value.strip("'\"")
    return data


def programbench_repositories(tasks_root: Path = TASKS_ROOT) -> set[str]:
    repos: set[str] = set()
    if not tasks_root.exists():
        return repos
    for task_yaml in tasks_root.glob("*/task.yaml"):
        meta = parse_simple_yaml(task_yaml)
        repo = str(meta.get("repository") or "").lower()
        if repo:
            repos.add(repo)
    return repos


def gh_api_json(args: list[str]) -> dict[str, Any]:
    last_error = ""
    for attempt in range(3):
        proc = subprocess.run(["gh", "api", *args], capture_output=True, text=True, timeout=60)
        if proc.returncode == 0:
            return json.loads(proc.stdout)
        last_error = proc.stderr.strip() or proc.stdout.strip()
        time.sleep(1 + attempt)
    raise RuntimeError(last_error)


def search_repositories(query: str, per_page: int) -> list[dict[str, Any]]:
    payload = gh_api_json(["--method", "GET", "search/repositories", "-f", f"q={query}", "-f", f"per_page={per_page}"])
    return list(payload.get("items") or [])


def resolve_head_sha(full_name: str, branch: str) -> str | None:
    try:
        payload = gh_api_json([f"repos/{full_name}/commits/{branch}"])
    except Exception:
        return None
    sha = payload.get("sha")
    return sha if isinstance(sha, str) else None


def build_hint(language: str) -> str:
    if language == "Rust":
        return "cargo build --release"
    if language == "Go":
        return "go build -o executable ./..."
    if language == "C":
        return "make || cmake -S . -B build && cmake --build build"
    if language == "C++":
        return "cmake -S . -B build && cmake --build build || make"
    return ""


def searchable_text(item: dict[str, Any]) -> str:
    parts = [
        str(item.get("full_name") or ""),
        str(item.get("name") or ""),
        str(item.get("description") or ""),
        " ".join(str(topic) for topic in item.get("topics") or []),
    ]
    return " ".join(parts).lower()


def looks_programbench_like(item: dict[str, Any]) -> bool:
    text = searchable_text(item)
    if any(term in text for term in BLOCK_TERMS):
        return False
    return any(term in text for term in POSITIVE_TERMS)


def normalize_repo(item: dict[str, Any], *, resolved_sha: bool) -> dict[str, Any]:
    license_payload = item.get("license") or {}
    full_name = item["full_name"]
    branch = item.get("default_branch") or "main"
    language = item.get("language") or ""
    head_sha = resolve_head_sha(full_name, branch) if resolved_sha else None
    return {
        "repository": full_name,
        "html_url": item.get("html_url"),
        "clone_url": item.get("clone_url"),
        "default_branch": branch,
        "commit": head_sha,
        "language": language,
        "description": item.get("description") or "",
        "stars": item.get("stargazers_count"),
        "forks": item.get("forks_count"),
        "size_kb": item.get("size"),
        "pushed_at": item.get("pushed_at"),
        "license": license_payload.get("spdx_id"),
        "topics": item.get("topics") or [],
        "build_command_hint": build_hint(language),
        "status": "candidate_needs_clone_build_test_validation",
        "required_next_gates": [
            "clone pinned commit",
            "inspect README/docs and executable shape",
            "build reference binary",
            "run existing tests on reference",
            "run dummy implementation against tests",
            "verify offline build",
            "sanitize tests and scan for source leakage",
            "record coverage/mutation hooks",
        ],
    }


def select_candidates(raw_by_language: dict[str, list[dict[str, Any]]], exclude_repos: set[str], target_total: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for language, target in LANGUAGE_TARGETS.items():
        count = 0
        for item in raw_by_language.get(language, []):
            repo = str(item.get("full_name") or "")
            if not repo or repo.lower() in exclude_repos or repo.lower() in seen:
                continue
            license_id = ((item.get("license") or {}).get("spdx_id") or "")
            if license_id not in PERMISSIVE_LICENSES:
                continue
            if (item.get("size") or 0) > MAX_SIZE_KB:
                continue
            if not looks_programbench_like(item):
                continue
            selected.append(item)
            seen.add(repo.lower())
            count += 1
            if count >= target:
                break
    if len(selected) < target_total:
        for rows in raw_by_language.values():
            for item in rows:
                repo = str(item.get("full_name") or "")
                if not repo or repo.lower() in exclude_repos or repo.lower() in seen:
                    continue
                license_id = ((item.get("license") or {}).get("spdx_id") or "")
                if license_id not in PERMISSIVE_LICENSES:
                    continue
                if (item.get("size") or 0) > MAX_SIZE_KB:
                    continue
                if not looks_programbench_like(item):
                    continue
                selected.append(item)
                seen.add(repo.lower())
                if len(selected) >= target_total:
                    return selected
    return selected[:target_total]


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    rows = payload["candidates"]
    lines = [
        "# ProgramBench Gym Scale Seed Repositories",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Target first wave: {payload['selection_policy']['target_count_first_wave']}",
        f"- Selected candidates: {len(rows)}",
        f"- Language counts: {payload['summary']['language_counts']}",
        "",
        "These are not accepted Gym instances yet. Each repo must pass clone, build, test, dummy, offline, leak-scan, and license gates before becoming training data.",
        "",
        "| repo | lang | stars | license | commit | build hint | note |",
        "| --- | ---: | ---: | --- | --- | --- | --- |",
    ]
    for row in rows:
        commit = (row.get("commit") or "unresolved")[:12]
        desc = (row.get("description") or "").replace("|", "/")[:80]
        lines.append(
            f"| `{row['repository']}` | {row.get('language')} | {row.get('stars')} | {row.get('license')} | `{commit}` | `{row.get('build_command_hint')}` | {desc} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("reports/programbench_gym_scale_seed_repos_current.json"))
    parser.add_argument("--md-out", type=Path, default=Path("reports/programbench_gym_scale_seed_repos_current.md"))
    parser.add_argument("--per-query", type=int, default=30)
    parser.add_argument("--target-total", type=int, default=20)
    parser.add_argument("--resolve-commits", action="store_true")
    args = parser.parse_args()

    exclude = programbench_repositories()
    raw_by_language: dict[str, list[dict[str, Any]]] = {}
    search_errors: dict[str, str] = {}
    for language, queries in SEARCH_QUERIES.items():
        rows: list[dict[str, Any]] = []
        for query in queries:
            try:
                rows.extend(search_repositories(query, args.per_query))
            except Exception as exc:  # noqa: BLE001 - record and keep other queries
                search_errors[f"{language}: {query}"] = str(exc)
        # Stable order by stars, then smaller repos first.
        dedup: dict[str, dict[str, Any]] = {}
        for item in rows:
            dedup.setdefault(str(item.get("full_name")), item)
        raw_by_language[language] = sorted(
            dedup.values(),
            key=lambda row: (-(row.get("stargazers_count") or 0), row.get("size") or 0),
        )

    selected_raw = select_candidates(raw_by_language, exclude, args.target_total)
    candidates = [normalize_repo(item, resolved_sha=args.resolve_commits) for item in selected_raw]
    payload = {
        "schema_version": "0.1",
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "source": "GitHub Search API via gh api",
        "selection_policy": {
            "target_count_first_wave": args.target_total,
            "language_targets": LANGUAGE_TARGETS,
            "permissive_licenses": sorted(PERMISSIVE_LICENSES),
            "max_size_kb": MAX_SIZE_KB,
            "blocked_terms": sorted(BLOCK_TERMS),
            "positive_terms": sorted(POSITIVE_TERMS),
            "excluded_programbench_repositories": len(exclude),
        },
        "search_errors": search_errors,
        "summary": {
            "language_counts": dict(Counter(row.get("language") for row in candidates)),
            "license_counts": dict(Counter(row.get("license") for row in candidates)),
        },
        "candidates": candidates,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(args.md_out, payload)
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
