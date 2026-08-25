from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io import atomic_write_json, read_json
from .isolation import validate_execution_provenance
from .state import utc_now


_AUDIT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _audit_id(value: str | None) -> str:
    """Return a path-safe, collision-resistant audit identifier."""

    if value is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        value = f"audit-{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    if not _AUDIT_ID_RE.fullmatch(value):
        raise ValueError(
            "audit-id must be a path-safe identifier (letters, digits, '.', '_' or '-')"
        )
    return value


def _age_seconds(value: str) -> float:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - moment).total_seconds())


def _directory_stats(path: Path) -> tuple[int, int]:
    files = 0
    size = 0
    for root, directories, names in os.walk(path, followlinks=False):
        directories[:] = [name for name in directories if not (Path(root) / name).is_symlink()]
        for name in names:
            candidate = Path(root) / name
            try:
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                files += 1
                size += candidate.stat().st_size
            except OSError:
                continue
    return files, size


def audit_campaign(
    root: Path,
    *,
    stale_seconds: float,
    maximum_repo_files: int,
    maximum_repo_bytes: int,
    audit_id: str | None = None,
    enforce_safe_pause: bool = False,
) -> dict[str, Any]:
    """Run an advisory audit without mutating campaign execution state.

    Each invocation writes one report under ``audit/runs/<audit-id>.json``.
    The legacy ``enforce_safe_pause`` parameter is retained for callers that
    still pass it, but is deliberately a no-op: an auditor never writes repo
    control/status files and never acquires the campaign lock.
    """

    del enforce_safe_pause
    audit_id = _audit_id(audit_id)
    report_path = root / "audit" / "runs" / f"{audit_id}.json"
    if report_path.exists():
        raise FileExistsError(f"audit report already exists: {report_path}")
    issues: list[dict[str, Any]] = []
    campaign_status = read_json(root / "campaign_status.json") if (root / "campaign_status.json").is_file() else {}
    active_run_id = campaign_status.get("run_id") if campaign_status.get("state") == "running" else None
    repositories_root = root / "repositories"
    for repo_root in sorted(repositories_root.iterdir()) if repositories_root.is_dir() else []:
        if not repo_root.is_dir():
            continue
        status_path = repo_root / "status.json"
        if not status_path.is_file():
            issues.append(
                {"repo": repo_root.name, "severity": "high", "kind": "missing_status", "action": "pause_repo"}
            )
            continue
        try:
            status = read_json(status_path)
        except Exception as exc:
            issues.append(
                {"repo": repo_root.name, "severity": "critical", "kind": "invalid_status", "error": str(exc), "action": "pause_repo"}
            )
            continue
        # Queued repositories can legitimately retain the last run's terminally
        # interrupted `running` status until the current controller reaches
        # them.  Only the current run owns a live heartbeat.
        status_belongs_to_active_run = (
            active_run_id is None
            or status.get("run_id") is None
            or status.get("run_id") == active_run_id
        )
        if status.get("state") in {"running", "stopping"} and status_belongs_to_active_run:
            age = _age_seconds(str(status.get("updated_at")))
            if age > stale_seconds:
                issues.append(
                    {"repo": repo_root.name, "severity": "high", "kind": "stale_heartbeat", "age_seconds": age, "action": "pause_repo"}
                )
        file_count, byte_count = _directory_stats(repo_root)
        if file_count > maximum_repo_files or byte_count > maximum_repo_bytes:
            issues.append(
                {
                    "repo": repo_root.name,
                    "severity": "high",
                    "kind": "artifact_growth_fuse",
                    "files": file_count,
                    "bytes": byte_count,
                    "action": "pause_repo",
                }
            )
        for response_path in repo_root.glob("stages/iteration-*/**/response.json"):
            try:
                response = read_json(response_path)
            except Exception as exc:
                issues.append(
                    {"repo": repo_root.name, "severity": "critical", "kind": "invalid_stage_response", "path": str(response_path), "error": str(exc), "action": "quarantine_artifact"}
                )
                continue
            for provenance in response.get("execution_provenance") or []:
                expected = str(provenance.get("stage") or "")
                errors = validate_execution_provenance(provenance, expected_stage=expected)
                if errors:
                    issues.append(
                        {"repo": repo_root.name, "severity": "critical", "kind": "isolation_policy_violation", "path": str(response_path), "errors": errors, "action": "pause_repo"}
                    )

    orphaned: list[str] = []
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "label=programbench.workflow=v4", "--format", "{{.Names}}"],
            text=True,
            capture_output=True,
            timeout=15,
        )
        if result.returncode == 0:
            orphaned = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.TimeoutExpired):
        pass
    if campaign_status.get("state") not in {"running"} and orphaned:
        issues.append(
            {"repo": None, "severity": "critical", "kind": "orphaned_v4_containers", "containers": orphaned, "action": "pause_campaign"}
        )
    report = {
        "schema": "programbench_v4_audit_report_v1",
        "audit_id": audit_id,
        "campaign_root": str(root.resolve()),
        "created_at": utc_now(),
        "issues": issues,
        "issue_count": len(issues),
        "critical_count": sum(issue["severity"] == "critical" for issue in issues),
        "recommended_action": "continue" if not issues else "pause_or_quarantine",
        "intervention_mode": "advisory_only",
    }
    atomic_write_json(report_path, report)
    return report


def aggregate_audits(
    root: Path, *, audit_ids: list[str] | None = None
) -> dict[str, Any]:
    """Deterministically merge run reports into the shared latest report.

    This is the sole operation that writes ``audit/latest.json``.  Reports and
    issues are sorted by audit id/content, and the aggregate timestamp is
    derived from source reports rather than generated afresh, making repeated
    aggregation over the same run set deterministic.
    """

    runs_root = root / "audit" / "runs"
    if audit_ids is None:
        paths = sorted(runs_root.glob("*.json"), key=lambda path: path.name)
    else:
        normalized = sorted({_audit_id(value) for value in audit_ids})
        paths = [runs_root / f"{value}.json" for value in normalized]
    reports: list[tuple[str, dict[str, Any]]] = []
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"audit report does not exist: {path}")
        report = read_json(path)
        if report.get("schema") != "programbench_v4_audit_report_v1":
            raise ValueError(f"unsupported audit report schema: {path}")
        report_id = _audit_id(str(report.get("audit_id") or path.stem))
        if report_id != path.stem:
            raise ValueError(f"audit report id/path mismatch: {path}")
        reports.append((report_id, report))

    reports.sort(key=lambda item: item[0])
    issues: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    created_values: list[str] = []
    campaign_roots: set[str] = set()
    for report_id, report in reports:
        created = str(report.get("created_at") or "")
        if created:
            created_values.append(created)
        if report.get("campaign_root"):
            campaign_roots.add(str(report["campaign_root"]))
        summaries.append(
            {
                "audit_id": report_id,
                "created_at": created,
                "issue_count": int(report.get("issue_count") or 0),
                "critical_count": int(report.get("critical_count") or 0),
            }
        )
        for issue in report.get("issues") or []:
            if not isinstance(issue, dict):
                raise ValueError(f"audit report contains a non-object issue: {report_id}")
            issues.append({**issue, "audit_id": report_id})
    issues.sort(key=lambda issue: json.dumps(issue, sort_keys=True, ensure_ascii=False))
    aggregate = {
        "schema": "programbench_v4_audit_report_v1",
        "audit_id": "aggregate",
        "campaign_root": next(iter(campaign_roots)) if len(campaign_roots) == 1 else str(root.resolve()),
        "created_at": max(created_values) if created_values else None,
        "intervention_mode": "advisory_only",
        "aggregate": True,
        "audit_count": len(reports),
        "audits": summaries,
        "issues": issues,
        "issue_count": len(issues),
        "critical_count": sum(issue.get("severity") == "critical" for issue in issues),
        "recommended_action": "continue" if not issues else "pause_or_quarantine",
    }
    atomic_write_json(root / "audit" / "latest.json", aggregate)
    return aggregate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--stale-seconds", type=float, default=120)
    parser.add_argument("--maximum-repo-files", type=int, default=100000)
    parser.add_argument("--maximum-repo-bytes", type=int, default=20 * 1024**3)
    parser.add_argument(
        "--audit-id",
        help="path-safe id for this audit run; report is written under audit/runs/",
    )
    parser.add_argument(
        "--aggregate",
        action="store_true",
        help="atomically aggregate audit/runs/*.json into audit/latest.json",
    )
    # Deprecated compatibility flag. It is now advisory-only and never writes
    # repository control state.
    parser.add_argument("--enforce-safe-pause", action="store_true")
    args = parser.parse_args(argv)
    if args.aggregate:
        report = aggregate_audits(args.campaign_root.resolve())
    else:
        report = audit_campaign(
            args.campaign_root.resolve(),
            stale_seconds=args.stale_seconds,
            maximum_repo_files=args.maximum_repo_files,
            maximum_repo_bytes=args.maximum_repo_bytes,
            audit_id=args.audit_id,
            enforce_safe_pause=args.enforce_safe_pause,
        )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if report["critical_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
