#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import uuid
from pathlib import Path

from v4.programbench_v4.io import atomic_write_json
from v4.programbench_v4.isolation import ContainerContract, Mount
from v4.programbench_v4.state import utc_now


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    checks: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="pb-v4-isolation-") as temporary:
        fixture = Path(temporary)
        sentinel = fixture / "binary-only-sentinel"
        sentinel.write_text("immutable\n", encoding="utf-8")
        name = f"pb-v4-isolation-{uuid.uuid4().hex}"
        contract = ContainerContract(
            image_id=args.image_id,
            stage="freeze_verification",
            name=name,
            mounts=(Mount(fixture, "/cleanroom", readonly=True),),
            memory="512m",
            cpus=1,
            pids_limit=64,
            entrypoint="/bin/sh",
        )
        script = r'''
set -eu
test "$(id -u)" != "0"
test -f /cleanroom/binary-only-sentinel
test ! -e /source
test ! -e /var/run/docker.sock
if echo bad >> /cleanroom/binary-only-sentinel 2>/dev/null; then exit 21; fi
if echo bad > /etc/pb-v4-root-write 2>/dev/null; then exit 22; fi
if command -v python3 >/dev/null 2>&1; then
  python3 - <<'PY'
import socket
s = socket.socket()
s.settimeout(1)
try:
    s.connect(("1.1.1.1", 53))
except OSError:
    raise SystemExit(0)
raise SystemExit(23)
PY
fi
printf 'ok\n'
'''
        command = contract.docker_run(["-c", script])
        result = subprocess.run(command, text=True, capture_output=True, timeout=30)
        if result.returncode != 0:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)
        checks = {
            "container_passed": result.returncode == 0 and result.stdout.strip().endswith("ok"),
            "host_fixture_unchanged": sentinel.read_text(encoding="utf-8") == "immutable\n",
            "immutable_image_id": args.image_id.startswith("sha256:"),
            "network_none": "none" in command,
            "read_only_root": "--read-only" in command,
            "non_root": "1000:1000" in command,
            "explicit_entrypoint": "--entrypoint" in command,
        }
        report = {
            "schema": "programbench_v4_docker_isolation_smoke_v1",
            "created_at": utc_now(),
            "image_id": args.image_id,
            "checks": checks,
            "passed": all(checks.values()),
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "docker_command": command,
        }
    atomic_write_json(args.output.resolve(), report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
