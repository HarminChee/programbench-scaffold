from __future__ import annotations

import threading
import time

import pytest

from v4.programbench_v4.resource_admission import (
    ResourceAdmission,
    ResourceRequest,
    resource_request,
)


def test_resource_requests_are_conservative_for_default_host() -> None:
    rust = resource_request("preflight", {"language": "rust"})
    large_quality = resource_request(
        "quality_gates", {"language": "go"}, {"retained_suite_cases": 800}
    )
    small_quality = resource_request(
        "quality_gates", {"language": "go"}, {"retained_suite_cases": 120}
    )
    assert rust == type(rust)("rust_preflight", 4, 8 * 1024, 2)
    assert large_quality.cpu == 8 and large_quality.slot_limit == 1
    assert small_quality.cpu == 4 and small_quality.slot_limit == 2


def test_rust_preflight_allows_two_and_third_waits_with_heartbeat() -> None:
    admission = ResourceAdmission(cpu_capacity=10, memory_capacity_mib=22 * 1024, heartbeat_seconds=0.02)
    request = resource_request("preflight", {"language": "rust"})
    first = admission.acquire(request, remaining_seconds=1)
    second = admission.acquire(request, remaining_seconds=1)
    entered = threading.Event()
    waits: list[float] = []
    result: list[object] = []

    def waiter() -> None:
        try:
            lease = admission.acquire(
                request,
                status_callback=lambda waited, _snapshot: (waits.append(waited), entered.set()),
                remaining_seconds=2,
            )
            result.append(lease)
        except BaseException as exc:  # pragma: no cover - assertion below reports it
            result.append(exc)

    thread = threading.Thread(target=waiter)
    thread.start()
    assert entered.wait(1)
    assert admission.snapshot().waiting == 1
    first.release()
    thread.join(2)
    assert not thread.is_alive()
    assert waits
    assert not isinstance(result[0], BaseException)
    result[0].release()  # type: ignore[union-attr]
    second.release()
    assert admission.snapshot().active_cpu == 0


def test_large_quality_does_not_share_its_slot() -> None:
    admission = ResourceAdmission(cpu_capacity=10, memory_capacity_mib=22 * 1024, heartbeat_seconds=0.02)
    request = resource_request("quality_gates", {"language": "go"}, {"retained_suite_cases": 700})
    first = admission.acquire(request, remaining_seconds=1)
    finished = threading.Event()
    result: list[object] = []

    def waiter() -> None:
        try:
            lease = admission.acquire(request, remaining_seconds=1)
            result.append(lease)
        except BaseException as exc:  # pragma: no cover - assertion below reports it
            result.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.08)
    assert not finished.is_set()
    first.release()
    assert finished.wait(1)
    thread.join(1)
    assert len(result) == 1 and not isinstance(result[0], BaseException)
    result[0].release()  # type: ignore[union-attr]


def test_impossible_reservation_times_out_without_leaking_capacity() -> None:
    admission = ResourceAdmission(cpu_capacity=2, memory_capacity_mib=1024, heartbeat_seconds=0.01)
    with pytest.raises(TimeoutError):
        admission.acquire(resource_request("preflight", {"language": "rust"}), remaining_seconds=0.05)
    snapshot = admission.snapshot()
    assert snapshot.active_cpu == 0
    assert snapshot.active_memory_mib == 0
    assert snapshot.waiting == 0


def test_bounded_backfill_avoids_head_of_line_idle_capacity() -> None:
    admission = ResourceAdmission(
        cpu_capacity=10,
        memory_capacity_mib=1024,
        heartbeat_seconds=0.01,
        backfill_grace_seconds=0.5,
    )
    active = admission.acquire(ResourceRequest("active", 4, 100), remaining_seconds=1)
    results: dict[str, object] = {}
    large_waiting = threading.Event()

    def large() -> None:
        results["large"] = admission.acquire(
            ResourceRequest("large", 8, 100),
            status_callback=lambda *_: large_waiting.set(),
            remaining_seconds=2,
        )

    large_thread = threading.Thread(target=large)
    large_thread.start()
    assert large_waiting.wait(1)

    # The eight-CPU head cannot fit beside the active four-CPU lease, but a
    # later light task can use otherwise idle capacity during the bounded
    # grace period.
    light = admission.acquire(ResourceRequest("light", 1, 50), remaining_seconds=1)
    assert admission.snapshot().active_cpu == 5
    light.release()
    active.release()
    large_thread.join(1)
    assert not large_thread.is_alive()
    assert not isinstance(results["large"], BaseException)
    results["large"].release()  # type: ignore[union-attr]
    assert admission.snapshot().active_cpu == 0
