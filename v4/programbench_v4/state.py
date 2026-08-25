from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io import atomic_write_json


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def process_identity(pid: int) -> str | None:
    if pid <= 0:
        return None
    try:
        return str(Path(f"/proc/{pid}/stat").read_text().split()[21])
    except (FileNotFoundError, PermissionError, IndexError, OSError):
        if os.name == "nt" and pid == os.getpid():
            return "current-windows-process"
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return "alive-identity-unavailable"


@dataclass
class CampaignLock:
    path: Path
    campaign_id: str
    run_id: str
    config_sha256: str
    stale_after_seconds: float = 120.0
    _owner_token: str | None = None

    def acquire(self) -> "CampaignLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        payload = {
            "schema": "programbench_v4_campaign_lock_v1",
            "campaign_id": self.campaign_id,
            "run_id": self.run_id,
            "owner_token": token,
            "pid": os.getpid(),
            "process_identity": process_identity(os.getpid()),
            "host": socket.gethostname(),
            "config_sha256": self.config_sha256,
            "created_at": utc_now(),
            "heartbeat_epoch": time.time(),
        }
        for _ in range(2):
            try:
                descriptor = os.open(
                    self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
            except FileExistsError:
                try:
                    existing = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise RuntimeError(f"campaign lock is unreadable: {exc}") from exc
                live_identity = process_identity(int(existing.get("pid") or -1))
                fresh = time.time() - float(existing.get("heartbeat_epoch") or 0) < self.stale_after_seconds
                same_identity = live_identity == existing.get("process_identity")
                if live_identity is not None and (same_identity or fresh):
                    raise RuntimeError(
                        f"campaign already owned by pid={existing.get('pid')} run={existing.get('run_id')}"
                    )
                quarantine = self.path.with_name(
                    f"{self.path.name}.stale.{int(time.time())}.{uuid.uuid4().hex}"
                )
                os.replace(self.path, quarantine)
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._owner_token = token
            return self
        raise RuntimeError("failed to acquire campaign lock")

    def heartbeat(self, *, stage: str) -> None:
        payload = self._owned_payload()
        payload.update({"heartbeat_epoch": time.time(), "heartbeat_at": utc_now(), "stage": stage})
        atomic_write_json(self.path, payload)

    def release(self) -> None:
        if self._owner_token is None:
            return
        self._owned_payload()
        self.path.unlink(missing_ok=False)
        self._owner_token = None

    def _owned_payload(self) -> dict[str, Any]:
        if self._owner_token is None:
            raise RuntimeError("lock is not owned")
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("owner_token") != self._owner_token:
            raise RuntimeError("campaign lock ownership changed")
        return payload


class AtomicState:
    def __init__(self, path: Path, *, campaign_id: str, run_id: str) -> None:
        self.path = path
        self.campaign_id = campaign_id
        self.run_id = run_id

    def write(self, *, state: str, stage: str, **fields: Any) -> dict[str, Any]:
        sequence = 1
        if self.path.is_file():
            previous = json.loads(self.path.read_text(encoding="utf-8"))
            if previous.get("run_id") == self.run_id:
                sequence = int(previous.get("sequence") or 0) + 1
        payload = {
            "schema": "programbench_v4_atomic_state_v1",
            "campaign_id": self.campaign_id,
            "run_id": self.run_id,
            "sequence": sequence,
            "state": state,
            "stage": stage,
            "updated_at": utc_now(),
            **fields,
        }
        atomic_write_json(self.path, payload)
        return payload
