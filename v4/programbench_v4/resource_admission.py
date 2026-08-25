"""Host-level admission control for concurrent V4 stage processes.

Repository workers and Agent generation slots are deliberately independent of
this gate.  The gate only delays a stage that would start an expensive local
process (build, capture, coverage, quality, or final verification).  This
keeps a wide repository queue while preventing the sum of Docker quotas from
overcommitting a small WSL host.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ResourceRequest:
    """A conservative reservation for one stage process."""

    kind: str
    cpu: int
    memory_mib: int
    slot_limit: int | None = None


@dataclass(frozen=True)
class ResourceSnapshot:
    cpu_capacity: int
    memory_capacity_mib: int
    active_cpu: int
    active_memory_mib: int
    active_by_kind: dict[str, int]
    waiting: int


class ResourceLease:
    def __init__(self, owner: "ResourceAdmission", request: ResourceRequest):
        self._owner = owner
        self.request = request
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._owner.release(self.request)

    def __enter__(self) -> "ResourceLease":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


def resource_request(
    stage: str,
    repo: dict[str, Any],
    workflow_context: dict[str, Any] | None = None,
) -> ResourceRequest:
    """Return the default reservation for a stage.

    Values are host reservations, not Docker commands.  Docker remains free
    to enforce a lower per-container limit; this gate bounds the aggregate.
    """

    language = str(repo.get("language") or "").lower()
    context = workflow_context or {}
    retained = int(context.get("retained_suite_cases") or 0)
    if stage == "preflight":
        if language == "rust":
            # Two bounded Cargo builds fit the campaign's 10 CPU / 22 GiB
            # admission envelope.  Inner Cargo jobs remain separately capped;
            # allowing two repositories avoids serializing a wide Rust cohort.
            return ResourceRequest("rust_preflight", 4, 8 * 1024, 2)
        return ResourceRequest("go_preflight", 2, 4 * 1024, 2)
    if stage == "dependency_prefetch":
        return ResourceRequest("dependency_prefetch", 2, 2 * 1024, 3)
    if stage == "generate":
        return ResourceRequest("generation_process", 1, 1024, None)
    if stage == "quality_gates":
        configured = repo.get("quality_container_cpus")
        cpu = int(configured) if configured is not None else (8 if retained >= 500 else 4)
        cpu = max(1, min(16, cpu))
        return ResourceRequest("quality_large" if cpu >= 8 else "quality_small", cpu, 4 * 1024, 1 if cpu >= 8 else 2)
    if stage in {"oracle_capture", "final_capture"}:
        return ResourceRequest("capture", 2, 4 * 1024, 3)
    if stage in {"quick_coverage", "full_coverage"}:
        return ResourceRequest("coverage", 2, 4 * 1024, 3)
    if stage in {"freeze_verification", "freeze"}:
        return ResourceRequest("finalization", 2, 4 * 1024, 3)
    # Planning, static selection and marginal evaluation are mostly Python
    # bookkeeping but still receive a small reservation for fairness.
    return ResourceRequest("light_stage", 1, 512, 8)


class ResourceAdmission:
    """Fair weighted semaphore for aggregate CPU/memory reservations."""

    def __init__(
        self,
        *,
        cpu_capacity: int = 10,
        memory_capacity_mib: int = 22 * 1024,
        heartbeat_seconds: float = 15.0,
        backfill_grace_seconds: float = 30.0,
    ) -> None:
        if cpu_capacity < 1 or memory_capacity_mib < 1:
            raise ValueError("resource capacities must be positive")
        self.cpu_capacity = int(cpu_capacity)
        self.memory_capacity_mib = int(memory_capacity_mib)
        self.heartbeat_seconds = max(0.05, float(heartbeat_seconds))
        self.backfill_grace_seconds = max(0.0, float(backfill_grace_seconds))
        self._active_cpu = 0
        self._active_memory_mib = 0
        self._active_by_kind: dict[str, int] = {}
        self._waiting = 0
        self._next_ticket = 0
        self._serving = 0
        self._cancelled: set[int] = set()
        self._waiting_since: dict[int, float] = {}
        self._condition = threading.Condition()

    def _skip_cancelled(self) -> None:
        while self._serving in self._cancelled:
            self._cancelled.remove(self._serving)
            self._serving += 1

    def _fits(self, request: ResourceRequest) -> bool:
        if request.cpu > self.cpu_capacity or request.memory_mib > self.memory_capacity_mib:
            return False
        if self._active_cpu + request.cpu > self.cpu_capacity:
            return False
        if self._active_memory_mib + request.memory_mib > self.memory_capacity_mib:
            return False
        if request.slot_limit is not None and self._active_by_kind.get(request.kind, 0) >= request.slot_limit:
            return False
        return True

    def snapshot(self) -> ResourceSnapshot:
        with self._condition:
            return ResourceSnapshot(
                cpu_capacity=self.cpu_capacity,
                memory_capacity_mib=self.memory_capacity_mib,
                active_cpu=self._active_cpu,
                active_memory_mib=self._active_memory_mib,
                active_by_kind=dict(self._active_by_kind),
                waiting=self._waiting,
            )

    def acquire(
        self,
        request: ResourceRequest,
        *,
        status_callback: Callable[[float, ResourceSnapshot], None] | None = None,
        remaining_seconds: float | None = None,
    ) -> ResourceLease:
        started = time.monotonic()
        deadline = None if remaining_seconds is None else started + max(0.0, remaining_seconds)
        with self._condition:
            ticket = self._next_ticket
            self._next_ticket += 1
            self._waiting += 1
            self._waiting_since[ticket] = started
            try:
                while True:
                    self._skip_cancelled()
                    now = time.monotonic()
                    head_wait = now - self._waiting_since.get(self._serving, now)
                    bounded_backfill = (
                        ticket > self._serving
                        and head_wait < self.backfill_grace_seconds
                    )
                    if self._fits(request) and (
                        ticket == self._serving or bounded_backfill
                    ):
                        if ticket == self._serving:
                            self._serving += 1
                        else:
                            # This ticket is already served out of order.  The
                            # normal cancelled-ticket skip machinery advances
                            # past it once the older head is finally admitted.
                            self._cancelled.add(ticket)
                        self._waiting_since.pop(ticket, None)
                        self._skip_cancelled()
                        self._waiting -= 1
                        self._active_cpu += request.cpu
                        self._active_memory_mib += request.memory_mib
                        self._active_by_kind[request.kind] = self._active_by_kind.get(request.kind, 0) + 1
                        self._condition.notify_all()
                        return ResourceLease(self, request)
                    if deadline is not None and now >= deadline:
                        self._cancelled.add(ticket)
                        self._waiting_since.pop(ticket, None)
                        self._waiting -= 1
                        self._skip_cancelled()
                        self._condition.notify_all()
                        raise TimeoutError(f"resource slot timeout for {request.kind}")
                    wait_for = self.heartbeat_seconds
                    if deadline is not None:
                        wait_for = min(wait_for, max(0.05, deadline - now))
                    if status_callback is not None:
                        status_callback(now - started, self.snapshot())
                    self._condition.wait(wait_for)
            except BaseException:
                # A callback or unexpected exception must not strand a ticket.
                if ticket >= self._serving and ticket not in self._cancelled:
                    self._cancelled.add(ticket)
                    self._waiting_since.pop(ticket, None)
                    self._waiting = max(0, self._waiting - 1)
                    self._skip_cancelled()
                    self._condition.notify_all()
                raise

    def release(self, request: ResourceRequest) -> None:
        with self._condition:
            self._active_cpu = max(0, self._active_cpu - request.cpu)
            self._active_memory_mib = max(0, self._active_memory_mib - request.memory_mib)
            current = self._active_by_kind.get(request.kind, 0)
            if current <= 1:
                self._active_by_kind.pop(request.kind, None)
            else:
                self._active_by_kind[request.kind] = current - 1
            self._condition.notify_all()
