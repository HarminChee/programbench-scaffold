from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .io import atomic_write_json, read_json, sha256_json
from .isolation import validate_execution_provenance, validate_pb_generation_scope
from .policy import MarginalPolicy, Observation
from .provenance import validate_repository_scope
from .resource_admission import ResourceAdmission, resource_request
from .scheduler import AdaptiveTranchePolicy
from .state import AtomicState, CampaignLock, utc_now


ITERATION_STAGES = (
    "plan_tranche",
    "generate",
    "static_select",
    "oracle_capture",
    "quick_coverage",
    "quality_gates",
    "evaluate_marginal",
)
FINAL_STAGES = (
    "final_capture",
    "full_coverage",
    "freeze_verification",
    "freeze",
)
STAGES_REQUIRING_ISOLATION = {
    "oracle_capture": "oracle_capture",
    "quick_coverage": "quick_coverage",
    "quality_gates": "quality_gates",
    "final_capture": "oracle_capture",
    "full_coverage": "full_coverage",
    "freeze_verification": "freeze_verification",
}
RECOVERABLE_ITERATION_STAGE_FAILURES = {
    "generate",
    "oracle_capture",
    "quick_coverage",
    "quality_gates",
    "evaluate_marginal",
}


class WorkflowError(RuntimeError):
    pass


class FairGenerationSemaphore:
    """FIFO generation gate shared by repository workers.

    ``threading.BoundedSemaphore`` does not promise FIFO wakeups. With a
    large external campaign this can starve a repository that is waiting for
    an Agent slot while other workers repeatedly reacquire the semaphore.
    This gate keeps the familiar ``acquire(timeout=...)``/``release()``
    interface but serves waiters in ticket order. A canceled timed-out ticket
    is skipped by the next acquirer, so a dead worker cannot wedge the queue.
    """

    def __init__(self, value: int):
        if int(value) < 1:
            raise ValueError("FairGenerationSemaphore value must be positive")
        self._capacity = int(value)
        self._available = int(value)
        self._next_ticket = 0
        self._serving = 0
        self._cancelled: set[int] = set()
        self._condition = threading.Condition()

    def _skip_cancelled(self) -> None:
        while self._serving in self._cancelled:
            self._cancelled.remove(self._serving)
            self._serving += 1

    def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        if not blocking:
            timeout = 0.0
        elif timeout is not None and timeout < 0:
            timeout = 0.0
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            ticket = self._next_ticket
            self._next_ticket += 1
            while True:
                self._skip_cancelled()
                if ticket == self._serving and self._available > 0:
                    self._serving += 1
                    self._available -= 1
                    self._skip_cancelled()
                    self._condition.notify_all()
                    return True
                if not blocking:
                    self._cancelled.add(ticket)
                    self._skip_cancelled()
                    self._condition.notify_all()
                    return False
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    self._cancelled.add(ticket)
                    self._skip_cancelled()
                    self._condition.notify_all()
                    return False
                self._condition.wait(remaining)

    def release(self) -> None:
        with self._condition:
            if self._available >= self._capacity:
                raise ValueError("FairGenerationSemaphore released too many times")
            self._available += 1
            self._skip_cancelled()
            self._condition.notify_all()


def _repository_recovery_identity(repo: dict[str, Any]) -> str:
    """Identify immutable target inputs independently of controller code."""

    return sha256_json(
        {
            "instance_id": repo.get("instance_id"),
            "commit": repo.get("commit"),
            "source_tree_sha256": repo.get("source_tree_sha256"),
            "runtime_image_id": repo.get("runtime_image_id"),
        }
    )


def _checkpoint_rollback_candidate(
    repo_root: Path, *, accepted_iteration: int, expected_sha256: str
) -> Path | None:
    """Find the newest exact accepted-suite backup after dropped iterations."""

    matches: list[tuple[int, Path]] = []
    for candidate in (repo_root / "candidates").glob("pre-iteration-*.json"):
        match = re.fullmatch(r"pre-iteration-(\d{4})\.json", candidate.name)
        if not match:
            continue
        staged_iteration = int(match.group(1))
        if staged_iteration <= accepted_iteration:
            continue
        if _sha256_file(candidate) == expected_sha256:
            matches.append((staged_iteration, candidate))
    return max(matches, default=(0, None), key=lambda row: row[0])[1]


def _load_recovery_checkpoint(
    repo_root: Path, repo: dict[str, Any]
) -> tuple[list[Observation], int, int, int, str | None]:
    """Import accepted domain state while invalidating old stage receipts."""

    path = repo_root / "recovery_checkpoint.json"
    if not path.is_file():
        return [], 0, 0, 0, None
    value = read_json(path)
    if value.get("schema") != "programbench_v4_recovery_checkpoint_v1":
        raise WorkflowError("unsupported repository recovery checkpoint schema")
    if value.get("repository_recovery_identity") != _repository_recovery_identity(repo):
        raise WorkflowError("repository recovery checkpoint target identity changed")
    candidate_path = repo_root / "candidates/current.json"
    if not candidate_path.is_file():
        raise WorkflowError("repository recovery checkpoint has no candidate state")
    if value.get("candidate_state_sha256") != _sha256_file(candidate_path):
        iteration = int(value.get("last_completed_iteration") or 0)
        expected_sha = str(value.get("candidate_state_sha256") or "")
        rollback = _checkpoint_rollback_candidate(
            repo_root,
            accepted_iteration=iteration,
            expected_sha256=expected_sha,
        )
        if rollback is not None:
            backup = (
                repo_root
                / "candidates"
                / f"current.interrupted-before-recovery-{int(time.time())}.json"
            )
            shutil.copy2(candidate_path, backup)
            shutil.copy2(rollback, candidate_path)
            atomic_write_json(
                repo_root / "recovery_autorollback.json",
                {
                    "schema": "programbench_v4_recovery_autorollback_v1",
                    "reason": "candidate_state_sha256_mismatch",
                    "last_completed_iteration": iteration,
                    "restored_from": str(rollback),
                    "interrupted_current_backup": str(backup),
                    "candidate_state_sha256": expected_sha,
                    "updated_at": utc_now(),
                },
            )
        else:
            raise WorkflowError("repository recovery checkpoint candidate state changed")
    bindings = value.get("accepted_artifact_bindings")
    if bindings:
        _validate_accepted_artifact_bindings(repo_root, bindings)
    try:
        observations = [Observation(**row) for row in value.get("observations") or []]
    except (TypeError, ValueError) as exc:
        raise WorkflowError(f"invalid recovery observations: {exc}") from exc
    iteration = int(value.get("last_completed_iteration") or 0)
    total_raw = int(value.get("unique_persisted_raw_candidates") or 0)
    retained = int(value.get("retained_suite_cases") or 0)
    if min(iteration, total_raw, retained) < 0:
        raise WorkflowError("repository recovery checkpoint has negative counters")
    return observations, total_raw, retained, iteration, str(value.get("scope_sha256") or "")


def _restore_checkpoint_candidate_state(
    repo_root: Path, repo: dict[str, Any], *, failed_iteration: int, stage: str, error: str
) -> dict[str, Any] | None:
    """Drop an unaccepted tranche and restore the last accepted candidate suite.

    This is deliberately narrow: it only acts when a valid recovery checkpoint
    exists and the checkpoint candidate hash can be restored exactly.  It keeps
    bad/flaky tranches auditable without converting one failed stage into a
    terminal repository failure.
    """

    checkpoint_path = repo_root / "recovery_checkpoint.json"
    if not checkpoint_path.is_file():
        return None
    checkpoint = read_json(checkpoint_path)
    if checkpoint.get("schema") != "programbench_v4_recovery_checkpoint_v1":
        return None
    if checkpoint.get("repository_recovery_identity") != _repository_recovery_identity(repo):
        return None
    accepted_iteration = int(checkpoint.get("last_completed_iteration") or 0)
    expected_sha = str(checkpoint.get("candidate_state_sha256") or "")
    if accepted_iteration <= 0 or not expected_sha:
        return None

    candidate_path = repo_root / "candidates" / "current.json"
    rollback_path = _checkpoint_rollback_candidate(
        repo_root,
        accepted_iteration=accepted_iteration,
        expected_sha256=expected_sha,
    )
    if candidate_path.is_file() and _sha256_file(candidate_path) == expected_sha:
        restored_from = str(candidate_path)
        already_current = True
    elif rollback_path is not None:
        dropped_dir = repo_root / "dropped_tranches"
        dropped_dir.mkdir(parents=True, exist_ok=True)
        if candidate_path.is_file():
            shutil.copy2(
                candidate_path,
                dropped_dir / f"iteration-{failed_iteration:04d}-{stage}-candidate.json",
            )
        shutil.copy2(rollback_path, candidate_path)
        restored_from = str(rollback_path)
        already_current = False
    else:
        return None

    record = {
        "schema": "programbench_v4_dropped_tranche_v1",
        "failed_iteration": failed_iteration,
        "failed_stage": stage,
        "accepted_iteration_restored": accepted_iteration,
        "candidate_state_sha256": expected_sha,
        "restored_from": restored_from,
        "already_current": already_current,
        "error": error,
        "updated_at": utc_now(),
    }
    dropped_dir = repo_root / "dropped_tranches"
    dropped_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        dropped_dir / f"iteration-{failed_iteration:04d}-{stage}.json",
        record,
    )
    return record


def _write_recovery_checkpoint(
    *,
    repo_root: Path,
    repo: dict[str, Any],
    scope_sha256: str,
    observations: list[Observation],
    total_raw: int,
    retained: int,
    iteration: int,
    accepted_artifact_bindings: dict[str, Any] | None = None,
) -> None:
    candidate_path = repo_root / "candidates/current.json"
    if not candidate_path.is_file():
        raise WorkflowError("cannot checkpoint a repository without candidate state")
    checkpoint = {
        "schema": "programbench_v4_recovery_checkpoint_v1",
        "repository_recovery_identity": _repository_recovery_identity(repo),
        "scope_sha256": scope_sha256,
        "candidate_state_sha256": _sha256_file(candidate_path),
        "last_completed_iteration": iteration,
        "unique_persisted_raw_candidates": total_raw,
        "retained_suite_cases": retained,
        "observations": [asdict(row) for row in observations],
        "updated_at": utc_now(),
    }
    if accepted_artifact_bindings:
        _validate_accepted_artifact_bindings(repo_root, accepted_artifact_bindings)
        if accepted_artifact_bindings.get("candidate_state_sha256") != checkpoint["candidate_state_sha256"]:
            raise WorkflowError("accepted artifact binding disagrees with candidate state")
        checkpoint["accepted_artifact_bindings"] = accepted_artifact_bindings
    atomic_write_json(
        repo_root / "recovery_checkpoint.json",
        checkpoint,
    )


def _validate_accepted_artifact_bindings(
    repo_root: Path, bindings: dict[str, Any]
) -> None:
    receipt_path = Path(str(bindings.get("receipt_path") or ""))
    expected = str(bindings.get("receipt_sha256") or "")
    try:
        receipt_path.resolve().relative_to(repo_root.resolve())
    except (ValueError, OSError) as exc:
        raise WorkflowError("accepted artifact receipt is outside repository state") from exc
    if not receipt_path.is_file() or not expected:
        raise WorkflowError("accepted artifact receipt is missing")
    if _sha256_file(receipt_path) != expected:
        raise WorkflowError("accepted artifact receipt digest changed")
    receipt = read_json(receipt_path)
    if (
        bindings.get("candidate_state_sha256")
        != receipt.get("restored_candidate_sha256")
    ):
        raise WorkflowError("accepted artifact receipt candidate binding changed")
    for relative, digest in (receipt.get("restored_artifact_sha256") or {}).items():
        artifact = receipt_path.parent / str(relative)
        try:
            artifact.resolve().relative_to(receipt_path.parent.resolve())
        except (ValueError, OSError) as exc:
            raise WorkflowError("accepted artifact snapshot is outside receipt state") from exc
        if not artifact.is_file() or _sha256_file(artifact) != str(digest):
            raise WorkflowError("accepted artifact snapshot digest changed")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def campaign_scope(config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    files: dict[str, str] = {}
    controller_files = [Path(__file__).resolve()]
    for repo in config.get("repositories") or []:
        for value in repo.get("scope_files") or []:
            controller_files.append(Path(str(value)).resolve(strict=True))
    for path in sorted(set(controller_files), key=str):
        if not path.is_file():
            raise WorkflowError(f"scope file is not a regular file: {path}")
        files[str(path)] = _sha256_file(path)
    manifest = {
        "config": config,
        "scope_files": files,
        "policy_module_sha256": _sha256_file(Path(__file__).with_name("policy.py")),
        "candidate_module_sha256": _sha256_file(Path(__file__).with_name("candidates.py")),
        "dependency_module_sha256": _sha256_file(Path(__file__).with_name("dependencies.py")),
        "isolation_module_sha256": _sha256_file(Path(__file__).with_name("isolation.py")),
    }
    return sha256_json(manifest), manifest


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


class StageRunner:
    def __init__(
        self,
        *,
        adapter: list[str],
        repo_root: Path,
        scope_sha256: str,
        heartbeat_seconds: float,
        stage_timeout_seconds: float,
        status: AtomicState,
        resource_admission: ResourceAdmission | None = None,
    ) -> None:
        self.adapter = adapter
        self.repo_root = repo_root
        self.scope_sha256 = scope_sha256
        self.heartbeat_seconds = heartbeat_seconds
        self.stage_timeout_seconds = stage_timeout_seconds
        self.status = status
        self.resource_admission = resource_admission

    def run(
        self,
        stage: str,
        *,
        iteration: int,
        repo: dict[str, Any],
        remaining_seconds: float | None,
        workflow_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        stage_root = self.repo_root / "stages" / f"iteration-{iteration:04d}" / stage
        request_path = stage_root / "request.json"
        response_path = stage_root / "response.json"
        receipt_path = stage_root / "receipt.json"
        request = {
            "schema": "programbench_v4_stage_request_v1",
            "stage": stage,
            "iteration": iteration,
            "scope_sha256": self.scope_sha256,
            "repository": repo,
            "repository_root": str(self.repo_root),
            "workflow_context": workflow_context or {},
        }
        request_sha = sha256_json(request)
        if receipt_path.is_file() and response_path.is_file():
            receipt = read_json(receipt_path)
            if (
                receipt.get("scope_sha256") == self.scope_sha256
                and receipt.get("request_sha256") == request_sha
                and receipt.get("response_sha256") == _sha256_file(response_path)
                and receipt.get("exit_code") == 0
            ):
                return read_json(response_path)

        if response_path.is_file():
            # Never let a failed rerun appear to have returned a response merely
            # because an invalid old-scope file was left in place. Preserve the
            # complete stale transaction for audit before starting the child.
            stale_digest = _sha256_file(response_path)
            stale_root = stage_root / "stale" / stale_digest[:16]
            suffix = 1
            while stale_root.exists():
                stale_root = stage_root / "stale" / f"{stale_digest[:16]}-{suffix}"
                suffix += 1
            stale_root.mkdir(parents=True)
            quarantined: dict[str, str] = {}
            for stale_path in (request_path, response_path, receipt_path):
                if stale_path.is_file():
                    destination = stale_root / stale_path.name
                    os.replace(stale_path, destination)
                    quarantined[stale_path.name] = str(destination)
            atomic_write_json(
                stale_root / "quarantine.json",
                {
                    "schema": "programbench_v4_stale_stage_quarantine_v1",
                    "reason": "invalid_scoped_receipt_before_rerun",
                    "response_sha256": stale_digest,
                    "quarantined": quarantined,
                    "updated_at": utc_now(),
                },
            )

        stage_root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(request_path, request)
        timeout = self.stage_timeout_seconds
        if remaining_seconds is not None:
            timeout = min(timeout, max(1.0, remaining_seconds))
        command = [*self.adapter, "--request", str(request_path), "--response", str(response_path)]
        stdout_path = stage_root / "stdout.log"
        stderr_path = stage_root / "stderr.log"
        admission = self.resource_admission
        lease = None
        if admission is not None:
            request = resource_request(stage, repo, workflow_context)

            def resource_wait_status(waited: float, snapshot: object) -> None:
                # The callback runs only while this repository is blocked.  It
                # keeps the controller/auditor heartbeat live without claiming
                # that a child process has started.
                self.status.write(
                    state="running",
                    stage="waiting_resource_slot",
                    iteration=iteration,
                    blocked_stage=stage,
                    resource_wait_seconds=waited,
                    resource_request={
                        "kind": request.kind,
                        "cpu": request.cpu,
                        "memory_mib": request.memory_mib,
                        "slot_limit": request.slot_limit,
                    },
                    resource_snapshot=getattr(snapshot, "__dict__", {}),
                )

            try:
                lease = admission.acquire(
                    request,
                    status_callback=resource_wait_status,
                    remaining_seconds=remaining_seconds,
                )
            except TimeoutError as exc:
                raise WorkflowError(str(exc)) from exc
        try:
            started = time.monotonic()
            creation = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
                "w", encoding="utf-8"
            ) as stderr:
                process = subprocess.Popen(
                    command,
                    cwd=self.repo_root,
                    stdout=stdout,
                    stderr=stderr,
                    text=True,
                    **creation,
                )
                while process.poll() is None:
                    elapsed = time.monotonic() - started
                    if elapsed >= timeout:
                        _terminate_process_tree(process)
                        atomic_write_json(
                            receipt_path,
                            {
                                "scope_sha256": self.scope_sha256,
                                "request_sha256": request_sha,
                                "exit_code": None,
                                "timed_out": True,
                                "elapsed_seconds": elapsed,
                                "ended_at": utc_now(),
                            },
                        )
                        raise WorkflowError(f"stage {stage} timed out after {elapsed:.1f}s")
                    self.status.write(
                        state="running",
                        stage=stage,
                        iteration=iteration,
                        child_pid=process.pid,
                        elapsed_seconds=elapsed,
                    )
                    time.sleep(min(self.heartbeat_seconds, max(0.05, timeout - elapsed)))
                exit_code = int(process.returncode or 0)
            elapsed = time.monotonic() - started
        finally:
            if lease is not None:
                lease.release()
        receipt = {
            "scope_sha256": self.scope_sha256,
            "request_sha256": request_sha,
            "exit_code": exit_code,
            "timed_out": False,
            "elapsed_seconds": elapsed,
            "ended_at": utc_now(),
        }
        if exit_code != 0 or not response_path.is_file():
            atomic_write_json(receipt_path, receipt)
            raise WorkflowError(
                f"stage {stage} failed: exit={exit_code}, response_exists={response_path.is_file()}"
            )
        response = read_json(response_path)
        if response.get("scope_sha256") != self.scope_sha256:
            raise WorkflowError(f"stage {stage} returned a mismatched scope")
        if response.get("stage") != stage:
            raise WorkflowError(f"stage {stage} returned a mismatched stage")
        receipt["response_sha256"] = _sha256_file(response_path)
        atomic_write_json(receipt_path, receipt)
        return response


def _policy_from_config(value: dict[str, Any]) -> MarginalPolicy:
    allowed = set(MarginalPolicy.__dataclass_fields__)
    unknown = set(value) - allowed
    if unknown:
        raise WorkflowError(f"unknown marginal policy keys: {sorted(unknown)}")
    return MarginalPolicy(**value)


def _tranche_policy_from_config(value: dict[str, Any]) -> AdaptiveTranchePolicy:
    allowed = set(AdaptiveTranchePolicy.__dataclass_fields__)
    unknown = set(value) - allowed
    if unknown:
        raise WorkflowError(f"unknown tranche policy keys: {sorted(unknown)}")
    return AdaptiveTranchePolicy(**value)


def _policy_for_repository(base: MarginalPolicy, repo: dict[str, Any]) -> MarginalPolicy:
    """Apply repository-scale targets without changing global quality policy."""

    language = str(repo.get("language") or "").lower()
    # Go AFL/QEMU is deliberately report-only. A mixed-language campaign may
    # enable native path saturation globally, but Go must never inherit it.
    path_policy = bool(
        language in {"rust", "c", "cpp"}
        and repo.get("afl_path_auxiliary_stop", base.require_auxiliary_path_saturation)
    )
    base = replace(base, require_auxiliary_path_saturation=path_policy)
    retained_max = repo.get("target_retained_cases_max")
    if retained_max is None:
        return base
    retained_fuse = int(retained_max)
    if retained_fuse <= 0:
        raise WorkflowError("target_retained_cases_max must be positive")
    hard = repo.get("target_retained_cases_hard")
    retained_hard = int(hard) if hard is not None else max(retained_fuse + 1, round(retained_fuse * 1.5))
    if retained_hard <= retained_fuse:
        retained_hard = retained_fuse + 1
    return replace(
        base,
        retained_suite_fuse=retained_fuse,
        adaptive_retained_fuse=True,
        retained_suite_hard_fuse=retained_hard,
    )


def _validate_stage_response(stage: str, response: dict[str, Any]) -> None:
    if stage == "dependency_prefetch":
        provenances = response.get("execution_provenance") or []
        if len(provenances) != 1:
            raise WorkflowError("dependency prefetch has no unique execution provenance")
        errors = validate_execution_provenance(
            provenances[0], expected_stage="dependency_prefetch"
        )
        if errors:
            raise WorkflowError("dependency prefetch isolation failed: " + "; ".join(errors))
        if provenances[0].get("network") != "bridge":
            raise WorkflowError("dependency prefetch must use the scoped networked stage")
    if stage == "generate":
        errors = validate_pb_generation_scope(response.get("generation_scope") or {})
        if errors:
            raise WorkflowError("invalid PB generation scope: " + "; ".join(errors))
        provenances = response.get("execution_provenance") or []
        if not provenances:
            raise WorkflowError("generation stage has no isolated agent-tool provenance")
        errors = [
            error
            for item in provenances
            for error in validate_execution_provenance(
                item, expected_stage="agent_tool_execution"
            )
        ]
        if errors:
            raise WorkflowError("generation isolation failed: " + "; ".join(errors))
    if stage == "preflight":
        provenances = response.get("execution_provenance") or []
        actual = {str(item.get("stage") or "") for item in provenances}
        if not {"source_build", "native_tests"}.issubset(actual):
            raise WorkflowError("preflight lacks source-build/native-test isolation evidence")
        errors = [
            error
            for item in provenances
            for error in validate_execution_provenance(
                item, expected_stage=str(item.get("stage") or "")
            )
        ]
        if errors:
            raise WorkflowError("preflight isolation failed: " + "; ".join(errors))
    expected = STAGES_REQUIRING_ISOLATION.get(stage)
    if expected:
        provenances = response.get("execution_provenance") or []
        if not provenances:
            raise WorkflowError(f"stage {stage} has no execution provenance")
        errors = [
            error
            for provenance in provenances
            for error in validate_execution_provenance(provenance, expected_stage=expected)
        ]
        if errors:
            raise WorkflowError(f"stage {stage} isolation failed: {'; '.join(errors)}")


def _observation(response: dict[str, Any]) -> Observation:
    value = response.get("observation")
    if not isinstance(value, dict):
        raise WorkflowError("evaluate_marginal response has no observation")
    try:
        return Observation(**value)
    except (TypeError, ValueError) as exc:
        raise WorkflowError(f"invalid marginal observation: {exc}") from exc


def _control_action(repo_root: Path) -> dict[str, Any] | None:
    path = repo_root / "control" / "request.json"
    if not path.is_file():
        return None
    request = read_json(path)
    if request.get("schema") != "programbench_v4_control_request_v1":
        raise WorkflowError("invalid auditor control request schema")
    if request.get("action") not in {"pause_repo", "quarantine_artifact"}:
        raise WorkflowError("auditor requested an unauthorized action")
    return request


def _run_iteration_stage(
    runner: StageRunner,
    *,
    stage: str,
    iteration: int,
    repo: dict[str, Any],
    remaining_seconds: float | None,
    workflow_context: dict[str, Any],
    generation_semaphore: threading.BoundedSemaphore,
) -> dict[str, Any]:
    """Run one stage, globally limiting only concurrent Agent generation.

    Repository workers remain free to build, capture, measure, and verify in
    parallel.  Waiting repositories periodically refresh their status so the
    auditor does not mistake intentional backpressure for a stale worker.
    """

    if stage != "generate":
        return runner.run(
            stage,
            iteration=iteration,
            repo=repo,
            remaining_seconds=remaining_seconds,
            workflow_context=workflow_context,
        )

    wait_started = time.monotonic()
    acquired = False
    while not acquired:
        elapsed = time.monotonic() - wait_started
        remaining = (
            None if remaining_seconds is None else remaining_seconds - elapsed
        )
        if remaining is not None and remaining <= 0:
            raise WorkflowError("repo wall budget exhausted waiting for generation slot")
        wait_slice = runner.heartbeat_seconds
        if remaining is not None:
            wait_slice = min(wait_slice, remaining)
        acquired = generation_semaphore.acquire(timeout=max(0.05, wait_slice))
        if not acquired:
            runner.status.write(
                state="running",
                stage="waiting_generation_slot",
                iteration=iteration,
                generation_wait_seconds=time.monotonic() - wait_started,
            )
    try:
        elapsed = time.monotonic() - wait_started
        adjusted_remaining = (
            None if remaining_seconds is None else remaining_seconds - elapsed
        )
        if adjusted_remaining is not None and adjusted_remaining <= 0:
            raise WorkflowError("repo wall budget exhausted waiting for generation slot")
        return runner.run(
            stage,
            iteration=iteration,
            repo=repo,
            remaining_seconds=adjusted_remaining,
            workflow_context=workflow_context,
        )
    finally:
        generation_semaphore.release()


def run_repository(
    *,
    campaign_id: str,
    run_id: str,
    output_root: Path,
    repo: dict[str, Any],
    config: dict[str, Any],
    scope_sha256: str,
    generation_semaphore: threading.BoundedSemaphore,
    resource_admission: ResourceAdmission | None = None,
) -> dict[str, Any]:
    instance = str(repo["instance_id"])
    repo_root = output_root / "repositories" / instance
    repo_root.mkdir(parents=True, exist_ok=True)
    status = AtomicState(repo_root / "status.json", campaign_id=campaign_id, run_id=run_id)
    # A pause request must be honored before dependency prefetch or preflight.
    # Otherwise a repeatedly failing preflight (for example an oversized Rust
    # build) is relaunched on every campaign resume before the normal
    # per-iteration control check is reached.
    initial_control = _control_action(repo_root)
    if initial_control:
        status.write(
            state="paused",
            stage="auditor_intervention",
            iteration=0,
            control=initial_control,
        )
        return {
            "instance_id": instance,
            "state": "paused",
            "stop_reason": str(initial_control.get("reason")),
        }
    prior_status_path = repo_root / "status.json"
    if prior_status_path.is_file():
        prior_status = read_json(prior_status_path)
        if (
            prior_status.get("state") == "completed"
            and prior_status.get("scope_sha256") == scope_sha256
        ):
            return {
                "instance_id": instance,
                "state": "completed",
                "stop_reason": str(prior_status.get("stop_reason") or "already_frozen"),
                "reused_completed_result": True,
            }
    policy = _policy_for_repository(_policy_from_config(config["marginal_policy"]), repo)
    tranche_policy = _tranche_policy_from_config(config.get("tranche_policy") or {})
    runner = StageRunner(
        adapter=[str(part) for part in repo["stage_adapter"]],
        repo_root=repo_root,
        scope_sha256=scope_sha256,
        heartbeat_seconds=float(config.get("heartbeat_seconds") or 15),
        stage_timeout_seconds=float(config["stage_timeout_seconds"]),
        status=status,
        resource_admission=resource_admission,
    )
    observations: list[Observation] = []
    total_raw = 0
    retained = 0
    iteration = 0
    imported_scope: str | None = None
    wall_start = time.monotonic()

    def workflow_context() -> dict[str, Any]:
        raw_evidence = policy.raw_fuse_evidence(
            observations, unique_persisted_raw_candidates=total_raw
        )
        return {
            "recommended_tranche_size": tranche_policy.next_size(observations),
            "breadth_bootstrap_iterations": tranche_policy.breadth_bootstrap_iterations,
            "breadth_bootstrap_views": list(tranche_policy.breadth_bootstrap_views),
            "reservoir_rerank_period": tranche_policy.reservoir_rerank_period,
            "target_retained_cases_min": repo.get("target_retained_cases_min"),
            "target_retained_cases_max": repo.get("target_retained_cases_max"),
            "target_scale_profile": repo.get("target_scale_profile"),
            "marginal_history": [asdict(row) for row in observations],
            "total_raw_candidates": total_raw,
            "raw_candidate_fuse": raw_evidence["effective_limit"],
            "raw_candidate_fuse_evidence": raw_evidence,
            "retained_suite_cases": retained,
            "retained_suite_fuse": policy.retained_suite_fuse,
            "adaptive_retained_fuse": policy.adaptive_retained_fuse,
            "retained_suite_hard_fuse": policy.retained_suite_hard_fuse,
            "retained_fuse_headroom_ratio": policy.retained_fuse_headroom_ratio,
            "retained_fuse_minimum_growth": policy.retained_fuse_minimum_growth,
            "minimum_primary_gain_pp": policy.minimum_primary_gain_pp,
            "minimum_strong_novelty": (
                policy.minimum_novelty
                if policy.minimum_strong_novelty is None
                else policy.minimum_strong_novelty
            ),
            "require_auxiliary_path_saturation": policy.require_auxiliary_path_saturation,
            "auxiliary_path_saturation_windows": policy.auxiliary_path_saturation_windows,
            "auxiliary_path_minimum_relative_gain": policy.auxiliary_path_minimum_relative_gain,
        }
    try:
        observations, total_raw, retained, iteration, imported_scope = (
            _load_recovery_checkpoint(repo_root, repo)
        )
        repository_scope = validate_repository_scope(repo)
        status.write(
            state="running",
            stage="source_scope_validated",
            repository_scope=repository_scope,
            recovered_iteration=iteration,
            recovery_imported_from_scope=imported_scope,
        )
        dependency_prefetch = runner.run(
            "dependency_prefetch", iteration=0, repo=repo,
            remaining_seconds=policy.repo_wall_budget_seconds,
            workflow_context={"purpose": "manifest-scoped dependency cache"},
        )
        _validate_stage_response("dependency_prefetch", dependency_prefetch)
        preflight = runner.run(
            "preflight", iteration=0, repo=repo, remaining_seconds=policy.repo_wall_budget_seconds,
            workflow_context={"recommended_tranche_size": tranche_policy.initial_size},
        )
        _validate_stage_response("preflight", preflight)

        # An operator may explicitly request strict settlement of a repository
        # that already has a validated accepted checkpoint above its measured
        # native baseline.  This is not an acceptance shortcut: the retained
        # suite still passes the complete final capture, full coverage, quality
        # verification, and freeze pipeline.  If the fresh full-suite coverage
        # no longer clears native, refinement resumes normally.
        settlement_requests = {
            str(row["instance_id"]): row
            for row in (config.get("fast_settlement") or {}).get("repositories", [])
        }
        settlement = settlement_requests.get(instance)
        if settlement is not None:
            if not observations:
                raise WorkflowError("fast settlement requires a recovery checkpoint")
            latest = observations[-1]
            if not latest.promoted or not latest.coverage_valid:
                raise WorkflowError(
                    "fast settlement requires a promoted coverage-valid checkpoint"
                )
            saturation_revalidation = bool(settlement.get("saturation_revalidation"))
            checkpoint_closeout = bool(
                settlement.get("checkpoint_closeout_revalidation")
            )
            native_value = settlement.get("native_primary_coverage")
            native = float(native_value) if native_value is not None else None
            if native is None and not saturation_revalidation and not checkpoint_closeout:
                raise WorkflowError(
                    "fast settlement requires measured native coverage"
                )
            margin = float(settlement.get("minimum_margin_pp") or 0.0)
            threshold = native + margin if native is not None else None
            manual_below_native = bool(settlement.get("allow_below_native_manual"))
            saturation_receipt = None
            if saturation_revalidation:
                receipts = sorted(
                    repo_root.glob(
                        "recovery_repairs/trim-unpromoted-tail-*/receipt.json"
                    )
                )
                if not receipts:
                    raise WorkflowError(
                        "saturation revalidation requires an audited tail-repair receipt"
                    )
                saturation_receipt = read_json(receipts[-1])
                checkpoint_now = read_json(repo_root / "recovery_checkpoint.json")
                if (
                    saturation_receipt.get("candidate_sha256")
                    != checkpoint_now.get("candidate_state_sha256")
                    or int(saturation_receipt.get("trimmed_observations") or 0) < 2
                    or saturation_receipt.get("coverage_scope_sha256")
                    != latest.coverage_scope_sha256
                ):
                    raise WorkflowError(
                        "saturation revalidation receipt does not match checkpoint"
                    )
            if (
                threshold is not None
                and latest.primary_coverage <= threshold
                and not manual_below_native
                and not saturation_revalidation
                and not checkpoint_closeout
            ):
                raise WorkflowError(
                    "fast settlement checkpoint does not clear native coverage"
                )
            settlement_basis = (
                "marginal_saturation_revalidation"
                if saturation_revalidation
                else (
                    "checkpoint_closeout_revalidation"
                    if checkpoint_closeout
                    else (
                        "operator_approved_checkpoint_settlement"
                        if manual_below_native
                        else "validated_checkpoint_above_native"
                    )
                )
            )
            status.write(
                state="running",
                stage="fast_settlement_revalidation",
                iteration=iteration,
                native_primary_coverage=native,
                minimum_margin_pp=margin,
                checkpoint_primary_coverage=latest.primary_coverage,
                retained_suite_cases=retained,
            )
            final_responses: dict[str, Any] = {}
            for stage in FINAL_STAGES[:2]:
                response = runner.run(
                    stage,
                    iteration=iteration,
                    repo=repo,
                    remaining_seconds=policy.repo_wall_budget_seconds,
                    workflow_context={
                        "marginal_history": [asdict(row) for row in observations],
                        "total_raw_candidates": total_raw,
                        "retained_suite_cases": retained,
                        "settlement_basis": settlement_basis,
                    },
                )
                _validate_stage_response(stage, response)
                final_responses[stage] = response
            active_suite_cases = int(
                final_responses["final_capture"].get("captured_cases") or 0
            )
            if active_suite_cases <= 0:
                raise WorkflowError("fast settlement final capture has no active cases")
            full_coverage = final_responses["full_coverage"].get("coverage") or {}
            fresh_primary = float(full_coverage.get("primary_coverage") or 0.0)
            if not bool(full_coverage.get("coverage_valid")):
                raise WorkflowError("fast settlement full coverage is invalid")
            sampled_cases = int(full_coverage.get("sampled_cases") or 0)
            if sampled_cases and sampled_cases != active_suite_cases:
                raise WorkflowError(
                    "fast settlement full coverage does not match active captured suite"
                )
            fresh_above_native = (
                threshold is not None and fresh_primary > threshold
            )
            if (
                fresh_above_native
                or manual_below_native
                or saturation_revalidation
                or checkpoint_closeout
            ):
                for stage in FINAL_STAGES[2:]:
                    response = runner.run(
                        stage,
                        iteration=iteration,
                        repo=repo,
                        remaining_seconds=policy.repo_wall_budget_seconds,
                        workflow_context={
                            "marginal_history": [asdict(row) for row in observations],
                            "total_raw_candidates": total_raw,
                            "retained_suite_cases": retained,
                            "settlement_basis": settlement_basis,
                            "native_primary_coverage": native,
                            "minimum_margin_pp": margin,
                            "fresh_full_primary_coverage": fresh_primary,
                        },
                    )
                    _validate_stage_response(stage, response)
                    final_responses[stage] = response
                if not bool(final_responses["freeze"].get("freeze_accepted")):
                    raise WorkflowError("freeze response did not accept the suite")
                reason = (
                    "validated_checkpoint_above_native"
                    if fresh_above_native
                    else (
                        "marginal_saturation_revalidated"
                        if saturation_revalidation
                        else (
                            "checkpoint_closeout_revalidated"
                            if checkpoint_closeout
                            else "operator_approved_settlement"
                        )
                    )
                )
                atomic_write_json(
                    repo_root / "frozen/settlement_summary.json",
                    {
                        "schema": "programbench_v4_fast_settlement_v1",
                        "instance_id": instance,
                        "successful": True,
                        "stop_reason": reason,
                        "iteration": iteration,
                        "retained_suite_cases": active_suite_cases,
                        "retained_checkpoint_cases": retained,
                        "total_raw_candidates": total_raw,
                        "native_primary_coverage": native,
                        "minimum_margin_pp": margin,
                        "operator_approved": manual_below_native,
                        "saturation_revalidation": saturation_revalidation,
                        "checkpoint_closeout_revalidation": checkpoint_closeout,
                        "saturation_repair_receipt": saturation_receipt,
                        "coverage_above_native": fresh_above_native,
                        "checkpoint_primary_coverage": latest.primary_coverage,
                        "final_primary_coverage": fresh_primary,
                        "final_verification": final_responses,
                        "updated_at": utc_now(),
                    },
                )
                status.write(
                    state="completed",
                    stage="frozen",
                    scope_sha256=scope_sha256,
                    iteration=iteration,
                    stop_reason=reason,
                    observations=[asdict(row) for row in observations],
                    total_raw_candidates=total_raw,
                    retained_suite_cases=active_suite_cases,
                    retained_checkpoint_cases=retained,
                    native_primary_coverage=native,
                    final_primary_coverage=fresh_primary,
                )
                return {
                    "instance_id": instance,
                    "state": "completed",
                    "stop_reason": reason,
                    "retained_suite_cases": active_suite_cases,
                    "primary_coverage": fresh_primary,
                }
            status.write(
                state="running",
                stage="fast_settlement_declined_below_native",
                iteration=iteration,
                native_primary_coverage=native,
                minimum_margin_pp=margin,
                checkpoint_primary_coverage=latest.primary_coverage,
                fresh_full_primary_coverage=fresh_primary,
                retained_suite_cases=retained,
            )
        while True:
            # A recovered campaign whose durable raw reservoir already fills
            # the currently-authorized fuse cannot safely ask the agent for
            # another tranche.  Recovery must remain idempotent: pause at the
            # checkpoint instead of manufacturing an overflow on every
            # restart.  Raising the explicitly configured fuse later permits
            # a normal resume without rewriting accepted artifacts.
            recovered_raw_evidence = policy.raw_fuse_evidence(
                observations, unique_persisted_raw_candidates=total_raw
            )
            recovered_raw_limit = recovered_raw_evidence["effective_limit"]
            if recovered_raw_limit is not None and total_raw >= recovered_raw_limit:
                reason = "raw_candidate_fuse_exhausted"
                evidence = {
                    "reason": reason,
                    "unique_persisted_raw_candidates": total_raw,
                    "effective_raw_candidate_fuse": recovered_raw_limit,
                    "raw_candidate_fuse_evidence": recovered_raw_evidence,
                    "canonical_suite_unchanged": True,
                    "successful": False,
                }
                atomic_write_json(repo_root / "marginal_decision.json", evidence)
                status.write(
                    state="paused",
                    stage="paused_incomplete",
                    iteration=iteration,
                    stop_reason=reason,
                    evidence=evidence,
                )
                return {
                    "instance_id": instance,
                    "state": "paused",
                    "stop_reason": reason,
                }
            iteration += 1
            dropped_iteration = False
            for stage in ITERATION_STAGES:
                control = _control_action(repo_root)
                if control:
                    status.write(
                        state="paused",
                        stage="auditor_intervention",
                        iteration=iteration,
                        control=control,
                    )
                    return {"instance_id": instance, "state": "paused", "stop_reason": str(control.get("reason"))}
                remaining = (
                    None
                    if policy.repo_wall_budget_seconds is None
                    else policy.repo_wall_budget_seconds - (time.monotonic() - wall_start)
                )
                if remaining is not None and remaining <= 0:
                    decision = policy.decide(
                        observations,
                        total_raw_candidates=total_raw,
                        retained_suite_cases=retained,
                        iteration=iteration,
                    )
                    raise WorkflowError(decision.reason)
                try:
                    response = _run_iteration_stage(
                        runner,
                        stage=stage,
                        iteration=iteration,
                        repo=repo,
                        remaining_seconds=remaining,
                        workflow_context=workflow_context(),
                        generation_semaphore=generation_semaphore,
                    )
                    _validate_stage_response(stage, response)
                except WorkflowError as exc:
                    if observations and stage in RECOVERABLE_ITERATION_STAGE_FAILURES:
                        restored = _restore_checkpoint_candidate_state(
                            repo_root,
                            repo,
                            failed_iteration=iteration,
                            stage=stage,
                            error=str(exc),
                        )
                        if restored:
                            status.write(
                                state="running",
                                stage="dropped_failed_tranche",
                                iteration=iteration,
                                dropped_tranche=restored,
                                retained_suite_cases=retained,
                                total_raw_candidates=total_raw,
                            )
                            dropped_iteration = True
                            break
                    raise
                if stage == "quality_gates" and bool(response.get("suite_fuse_blocked")):
                    retained = int(response.get("rolled_back_retained_cases") or retained)
                    final_responses: dict[str, Any] = {}
                    # A capacity pause is not marginal saturation.  Reverify
                    # capture/coverage/quality, but never call the accepting
                    # freeze publisher or label the suite completed.
                    for final_stage in FINAL_STAGES[:-1]:
                        final_response = runner.run(
                            final_stage,
                            iteration=iteration,
                            repo=repo,
                            remaining_seconds=remaining,
                            workflow_context={
                                "marginal_history": [asdict(row) for row in observations],
                                "total_raw_candidates": total_raw,
                                "retained_suite_cases": retained,
                            },
                        )
                        _validate_stage_response(final_stage, final_response)
                        final_responses[final_stage] = final_response
                    reason = "suite_witness_capacity_exhausted"
                    atomic_write_json(
                        repo_root / "paused_suite_summary.json",
                        {
                            "schema": "programbench_v4_paused_suite_v1",
                            "successful": False,
                            "stop_reason": reason,
                            "iteration": iteration,
                            "retained_suite_cases": retained,
                            "total_raw_candidates": total_raw,
                            "final_verification": final_responses,
                            "updated_at": utc_now(),
                        },
                    )
                    status.write(
                        state="paused",
                        stage="paused_incomplete",
                        iteration=iteration,
                        stop_reason=reason,
                        successful=False,
                        retained_suite_cases=retained,
                        total_raw_candidates=total_raw,
                        final_verification=final_responses,
                    )
                    return {
                        "instance_id": instance,
                        "state": "paused",
                        "stop_reason": reason,
                    }
                if stage == "quality_gates" and bool(response.get("quality_unrepairable")):
                    restored = _restore_checkpoint_candidate_state(
                        repo_root,
                        repo,
                        failed_iteration=iteration,
                        stage=stage,
                        error=str(response.get("quality_block_reason") or "quality_unrepairable"),
                    )
                    if restored:
                        status.write(
                            state="running",
                            stage="dropped_failed_tranche",
                            iteration=iteration,
                            dropped_tranche=restored,
                            retained_suite_cases=retained,
                            total_raw_candidates=total_raw,
                        )
                        dropped_iteration = True
                        break
                    decision = policy.decide(
                        observations,
                        total_raw_candidates=total_raw,
                        retained_suite_cases=retained,
                        iteration=iteration,
                        quality_unrepairable=True,
                    )
                    atomic_write_json(
                        repo_root / "marginal_decision.json",
                        {**asdict(decision), "updated_at": utc_now()},
                    )
                    status.write(
                        state="needs_attention",
                        stage="quality_blocked",
                        iteration=iteration,
                        stop_reason=decision.reason,
                        decision=asdict(decision),
                    )
                    return {
                        "instance_id": instance,
                        "state": "needs_attention",
                        "stop_reason": decision.reason,
                    }
                if stage == "static_select":
                    selected_new = int(response.get("selected_new_cases") or 0)
                    deferred_new = int(response.get("deferred_new_cases") or 0)
                    explicit_unique = response.get("unique_persisted_raw_candidates")
                    if explicit_unique is not None:
                        projected_raw = int(explicit_unique)
                        if projected_raw < total_raw:
                            raise WorkflowError(
                                "unique persisted raw-candidate count regressed"
                            )
                    else:
                        # Compatibility for deterministic smoke adapters. Real
                        # adapters must report the durable unique reservoir.
                        projected_raw = max(
                            total_raw + selected_new + deferred_new,
                            int(response.get("cumulative_cases") or 0),
                            int(response.get("cumulative_raw_candidates") or 0),
                        )
                    projected_retained = int(response.get("cumulative_cases") or retained)
                    fuse_reason = None
                    raw_fuse_evidence = policy.raw_fuse_evidence(
                        observations, unique_persisted_raw_candidates=total_raw
                    )
                    effective_raw_fuse = raw_fuse_evidence["effective_limit"]
                    raw_overflow_count = int(response.get("raw_overflow_count") or 0)
                    if raw_overflow_count:
                        fuse_reason = "raw_candidate_hard_fuse_overflow_before_capture"
                    elif (
                        effective_raw_fuse is not None
                        and projected_raw > effective_raw_fuse
                    ):
                        fuse_reason = "raw_candidate_fuse_before_capture"
                    replacement_planned = bool(response.get("replacement_planned"))
                    if (
                        fuse_reason is None
                        and policy.adaptive_retained_fuse
                        and policy.retained_suite_hard_fuse is not None
                        and projected_retained > policy.retained_suite_hard_fuse
                    ):
                        fuse_reason = "retained_suite_fuse_before_capture"
                    elif (
                        fuse_reason is None
                        and policy.retained_suite_fuse is not None
                        and projected_retained > policy.retained_suite_fuse
                        and not replacement_planned
                    ):
                        fuse_reason = "retained_suite_fuse_before_capture"
                    if fuse_reason:
                        canonical_suite_unchanged = response.get(
                            "candidate_state_committed"
                        ) is False
                        evidence = {
                            "reason": fuse_reason,
                            "projected_raw_candidates": projected_raw,
                            "projected_retained_cases": projected_retained,
                            "raw_candidate_fuse": policy.raw_candidate_fuse,
                            "effective_raw_candidate_fuse": effective_raw_fuse,
                            "raw_candidate_fuse_evidence": raw_fuse_evidence,
                            "raw_overflow_count": raw_overflow_count,
                            "raw_overflow_path": response.get("raw_overflow_path"),
                            "raw_overflow_sha256": response.get("raw_overflow_sha256"),
                            "retained_suite_fuse": policy.retained_suite_fuse,
                            "retained_suite_hard_fuse": policy.retained_suite_hard_fuse,
                            "adaptive_retained_fuse": policy.adaptive_retained_fuse,
                            "canonical_suite_unchanged": canonical_suite_unchanged,
                            "successful": False,
                        }
                        raw_fuse_pause = fuse_reason in {
                            "raw_candidate_fuse_before_capture",
                            "raw_candidate_hard_fuse_overflow_before_capture",
                        }
                        if raw_fuse_pause and not canonical_suite_unchanged:
                            raise WorkflowError(
                                "raw fuse adapter did not preserve the last accepted suite"
                            )
                        atomic_write_json(repo_root / "marginal_decision.json", evidence)
                        hard_raw_pause = bool(
                            raw_fuse_pause
                            and (
                                raw_overflow_count
                                or raw_fuse_evidence.get("at_hard_limit")
                            )
                        )
                        status.write(
                            state="paused" if hard_raw_pause else "needs_attention",
                            stage="paused_incomplete" if hard_raw_pause else "safety_fuse_before_capture",
                            iteration=iteration,
                            stop_reason=fuse_reason,
                            evidence=evidence,
                        )
                        return {
                            "instance_id": instance,
                            "state": "paused" if hard_raw_pause else "needs_attention",
                            "stop_reason": fuse_reason,
                        }
                    total_raw = projected_raw
                    retained = projected_retained
                if stage == "evaluate_marginal":
                    if response.get("baseline_revalidation_blocked"):
                        evidence = response.get("coverage_baseline_revalidation") or {}
                        atomic_write_json(
                            repo_root / "marginal_decision.json",
                            {
                                "reason": "coverage_baseline_exact_units_regressed",
                                "successful": False,
                                "evidence": evidence,
                                "updated_at": utc_now(),
                            },
                        )
                        status.write(
                            state="paused",
                            stage="paused_incomplete",
                            iteration=iteration,
                            stop_reason="coverage_baseline_exact_units_regressed",
                            evidence=evidence,
                        )
                        return {
                            "instance_id": instance,
                            "state": "paused",
                            "stop_reason": "coverage_baseline_exact_units_regressed",
                        }
                    baseline_rebase = response.get("coverage_baseline_rebase")
                    if baseline_rebase:
                        if not observations:
                            raise WorkflowError("coverage baseline rebase has no imported history")
                        replacement = _observation(
                            {"observation": baseline_rebase.get("replacement_observation")}
                        )
                        if replacement.coverage_scope_sha256 != observations[-1].coverage_scope_sha256:
                            raise WorkflowError("coverage baseline rebase changed comparison scope")
                        observations[-1] = replacement
                    observation = _observation(response)
                    observations.append(observation)
                    reported_raw = int(
                        response.get("unique_persisted_raw_candidates")
                        or response.get("total_raw_candidates")
                        or total_raw
                    )
                    if reported_raw < total_raw:
                        raise WorkflowError("evaluated raw-candidate count regressed")
                    total_raw = reported_raw
                    retained = int(response.get("retained_suite_cases") or retained)
                    _write_recovery_checkpoint(
                        repo_root=repo_root,
                        repo=repo,
                        scope_sha256=scope_sha256,
                        observations=observations,
                        total_raw=total_raw,
                        retained=retained,
                        iteration=iteration,
                        accepted_artifact_bindings=response.get(
                            "accepted_artifact_bindings"
                        ),
                    )
            if dropped_iteration:
                continue
            decision = policy.decide(
                observations,
                total_raw_candidates=total_raw,
                retained_suite_cases=retained,
                iteration=iteration,
                quality_unrepairable=bool(response.get("quality_unrepairable")),
                infrastructure_blocked=bool(response.get("infrastructure_blocked")),
            )
            atomic_write_json(
                repo_root / "marginal_decision.json",
                {**asdict(decision), "updated_at": utc_now()},
            )
            status.write(
                state="running" if decision.continue_refinement else "stopping",
                stage="marginal_decision",
                iteration=iteration,
                decision=asdict(decision),
            )
            if decision.continue_refinement:
                continue
            if not decision.successful:
                final_state = "blocked" if decision.reason == "infrastructure_blocked" else "needs_attention"
                status.write(
                    state=final_state,
                    stage="terminal_policy",
                    iteration=iteration,
                    stop_reason=decision.reason,
                    observations=[asdict(row) for row in observations],
                )
                return {"instance_id": instance, "state": final_state, "stop_reason": decision.reason}
            for stage in FINAL_STAGES:
                control = _control_action(repo_root)
                if control:
                    status.write(
                        state="paused",
                        stage="auditor_intervention",
                        iteration=iteration,
                        control=control,
                    )
                    return {"instance_id": instance, "state": "paused", "stop_reason": str(control.get("reason"))}
                remaining = (
                    None
                    if policy.repo_wall_budget_seconds is None
                    else policy.repo_wall_budget_seconds - (time.monotonic() - wall_start)
                )
                response = runner.run(
                    stage, iteration=iteration, repo=repo, remaining_seconds=remaining,
                    workflow_context={
                        "marginal_history": [asdict(row) for row in observations],
                        "total_raw_candidates": total_raw,
                        "retained_suite_cases": retained,
                    },
                )
                _validate_stage_response(stage, response)
            if not bool(response.get("freeze_accepted")):
                raise WorkflowError("freeze response did not accept the suite")
            status.write(
                state="completed",
                stage="frozen",
                scope_sha256=scope_sha256,
                iteration=iteration,
                stop_reason=decision.reason,
                observations=[asdict(row) for row in observations],
                total_raw_candidates=total_raw,
                retained_suite_cases=retained,
            )
            return {"instance_id": instance, "state": "completed", "stop_reason": decision.reason}
    except Exception as exc:
        status.write(
            state="needs_attention",
            stage="repository_workflow",
            iteration=iteration,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return {"instance_id": instance, "state": "needs_attention", "error": str(exc)}


def validate_config(config: dict[str, Any]) -> None:
    if config.get("schema") != "programbench_v4_campaign_v1":
        raise WorkflowError("unsupported V4 campaign schema")
    repositories = config.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise WorkflowError("campaign must contain repositories")
    workers = int(config.get("repo_workers") or 0)
    if workers < 1 or workers > 10:
        raise WorkflowError("repo_workers must be in 1..10")
    generation_workers = int(config.get("generation_workers", 2))
    if generation_workers < 1 or generation_workers > min(8, workers):
        raise WorkflowError("generation_workers must be in 1..min(8,repo_workers)")
    identifiers = [str(repo.get("instance_id") or "") for repo in repositories]
    if any(not identifier or "/" in identifier or "\\" in identifier for identifier in identifiers):
        raise WorkflowError("repository instance IDs must be safe path components")
    if len(set(identifiers)) != len(identifiers):
        raise WorkflowError("duplicate repository instance IDs")
    settlement_rows = (config.get("fast_settlement") or {}).get("repositories", [])
    if not isinstance(settlement_rows, list):
        raise WorkflowError("fast_settlement.repositories must be a list")
    settlement_ids: list[str] = []
    for row in settlement_rows:
        if not isinstance(row, dict):
            raise WorkflowError("fast settlement request must be an object")
        settlement_id = str(row.get("instance_id") or "")
        if settlement_id not in identifiers:
            raise WorkflowError("fast settlement references an unknown repository")
        saturation_revalidation = bool(row.get("saturation_revalidation"))
        checkpoint_closeout = bool(row.get("checkpoint_closeout_revalidation"))
        try:
            native_value = row["native_primary_coverage"]
            native = float(native_value) if native_value is not None else None
            margin = float(row.get("minimum_margin_pp") or 0.0)
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkflowError("fast settlement coverage values must be numeric") from exc
        if native is None and not saturation_revalidation and not checkpoint_closeout:
            raise WorkflowError(
                "fast settlement requires native coverage unless saturation is revalidated"
            )
        if (native is not None and (native < 0 or native > 100)) or margin < 0:
            raise WorkflowError("fast settlement coverage values are out of range")
        settlement_ids.append(settlement_id)
    if len(set(settlement_ids)) != len(settlement_ids):
        raise WorkflowError("duplicate fast settlement repository IDs")
    if "maximum_rounds" in config or "coverage_target" in config:
        raise WorkflowError("V4 forbids production maximum_rounds and fixed coverage_target")
    _policy_from_config(config.get("marginal_policy") or {})
    _tranche_policy_from_config(config.get("tranche_policy") or {})
    mode = str(config.get("mode") or "production")
    if mode not in {"production", "smoke", "pilot"}:
        raise WorkflowError("mode must be production, smoke, or pilot")
    pilot_fuse = (config.get("marginal_policy") or {}).get("pilot_iteration_fuse")
    if mode == "production" and pilot_fuse is not None:
        raise WorkflowError("production V4 forbids pilot_iteration_fuse")
    for repo in repositories:
        adapter = repo.get("stage_adapter")
        if not isinstance(adapter, list) or not adapter:
            raise WorkflowError(f"repository {repo.get('instance_id')} has no stage adapter")
        if repo.get("pb_official_tests"):
            raise WorkflowError("PB official tests may not enter a V4 generation campaign")
        for required in ("commit", "source_dir", "source_tree_sha256", "runtime_image_id"):
            if not repo.get(required):
                raise WorkflowError(
                    f"repository {repo.get('instance_id')} is missing {required}"
                )
        scope_files = repo.get("scope_files")
        if not isinstance(scope_files, list) or not scope_files:
            raise WorkflowError(
                f"repository {repo.get('instance_id')} must declare scope_files"
            )


def run_campaign(config_path: Path, *, run_id: str | None = None) -> int:
    config = read_json(config_path.resolve())
    validate_config(config)
    run_id = run_id or f"v4-{uuid.uuid4().hex}"
    campaign_id = str(config["campaign_id"])
    scope_sha256, scope_manifest = campaign_scope(config)
    output_root = Path(str(config["output_root"])).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    state = AtomicState(output_root / "campaign_status.json", campaign_id=campaign_id, run_id=run_id)
    lock = CampaignLock(
        output_root / "campaign.lock",
        campaign_id=campaign_id,
        run_id=run_id,
        config_sha256=scope_sha256,
        stale_after_seconds=float(config.get("stale_lock_seconds") or 120),
    )
    results: list[dict[str, Any]] = []
    try:
        lock.acquire()
        atomic_write_json(output_root / "scope_manifest.json", scope_manifest)
        state.write(state="running", stage="dispatch", scope_sha256=scope_sha256)
        # FIFO gate avoids starvation when ten repository workers compete for
        # eight expensive Agent-generation slots.
        generation_semaphore = FairGenerationSemaphore(
            int(config.get("generation_workers", 2))
        )
        resource_config = config.get("resource_admission") or {}
        if not isinstance(resource_config, dict):
            raise WorkflowError("resource_admission must be an object")
        resource_admission = ResourceAdmission(
            cpu_capacity=int(resource_config.get("cpu_capacity", 10)),
            memory_capacity_mib=int(resource_config.get("memory_capacity_mib", 22 * 1024)),
            heartbeat_seconds=float(config.get("heartbeat_seconds") or 15),
        )
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=int(config["repo_workers"]), thread_name_prefix="pb-v4-repo"
        ) as executor:
            futures = {
                executor.submit(
                    run_repository,
                    campaign_id=campaign_id,
                    run_id=run_id,
                    output_root=output_root,
                    repo=repo,
                    config=config,
                    scope_sha256=scope_sha256,
                    generation_semaphore=generation_semaphore,
                    resource_admission=resource_admission,
                ): str(repo["instance_id"])
                for repo in config["repositories"]
            }
            while futures:
                done, _ = concurrent.futures.wait(
                    futures, timeout=float(config.get("heartbeat_seconds") or 15), return_when=concurrent.futures.FIRST_COMPLETED
                )
                lock.heartbeat(stage="repository_workers")
                for future in done:
                    results.append(future.result())
                    del futures[future]
                state.write(
                    state="running",
                    stage="repository_workers",
                    completed_repositories=len(results),
                    expected_repositories=len(config["repositories"]),
                    results=sorted(results, key=lambda row: row["instance_id"]),
                )
        failed = [row for row in results if row.get("state") != "completed"]
        terminal = "completed" if not failed else "needs_attention"
        state.write(
            state=terminal,
            stage="campaign_terminal",
            scope_sha256=scope_sha256,
            results=sorted(results, key=lambda row: row["instance_id"]),
        )
        return 0 if not failed else 1
    except Exception as exc:
        state.write(
            state="needs_attention",
            stage="campaign_controller",
            error_type=type(exc).__name__,
            error=str(exc),
            results=sorted(results, key=lambda row: row["instance_id"]),
        )
        return 1
    finally:
        if lock._owner_token is not None:
            lock.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    return run_campaign(args.config, run_id=args.run_id)


if __name__ == "__main__":
    raise SystemExit(main())
