#!/usr/bin/env python3
"""Run checkpoint-bound Rust AFL metrics and a Xan native coverage retry.

This is a narrow campaign recovery utility.  It snapshots the accepted oracle
suite from the matching quick-coverage checkpoint, then runs every target in a
network-disabled, non-root, read-only container.  Xan's native retry adds the
host system timezone database as a read-only mount because its upstream tests
require IANA timezone data.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path("/home/programbench/research/pb-v4-rust20-20260824")
OUTPUT = ROOT / "output/repositories"
MEASURE = ROOT / "measurements/manual-20260824-xan-hl-projclean"
IMAGE = "sha256:752be1fd6a4e4a5c0137f0178fec24211c4789e4ce2415ba4a7ac3c9f1f5e122"
AFL_ROOT = Path("/home/programbench/research/tools/afl-src/aflplusplus-4.00c")
SCAFFOLD = Path("/mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold")
CHECKPOINTS = {
    "medialab__xan.60a89e5": 3,
    "pamburus__hl.6164b42": 3,
    "sigoden__projclean.2135f41": 22,
}


def hardened_base(name: str, repo: str, *, memory: str, cpus: str) -> list[str]:
    return [
        "docker", "run", "--rm", "--name", name,
        "--label", "programbench.campaign=v4-rust20-manual-metrics",
        "--label", f"programbench.repo={repo}",
        "--network", "none", "--read-only", "--user", "1000:1000",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "1024", "--memory", memory, "--memory-swap", memory,
        "--cpus", cpus,
    ]


def start_afl(instance: str, iteration: int) -> tuple[str, subprocess.Popen[bytes], object]:
    repo = OUTPUT / instance
    suite_source = repo / f"coverage/quick-{iteration:04d}/oracle"
    if not suite_source.is_dir():
        raise RuntimeError(f"missing accepted suite snapshot: {suite_source}")
    destination = MEASURE / instance / "suite"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(suite_source, destination)
    afl_output = MEASURE / instance / "afl"
    if afl_output.exists():
        shutil.rmtree(afl_output)
    afl_output.mkdir(parents=True)
    short = instance.split("__", 1)[1].split(".", 1)[0]
    command = hardened_base(f"pb-manual-afl-{short}", instance, memory="4g", cpus="1.5")
    command += [
        "--tmpfs", "/tmp:rw,nosuid,nodev,exec,uid=1000,gid=1000,mode=1777,size=1g",
        "--tmpfs", "/home/agent:rw,nosuid,nodev,uid=1000,gid=1000,mode=0700,size=64m",
        "-v", f"{SCAFFOLD}:/scaffold:ro",
        "-v", f"{destination}:/suite:ro",
        "-v", f"{repo / 'artifacts/reference_executable'}:/binary:ro",
        "-v", f"{AFL_ROOT}:/afl:ro",
        "-v", f"{afl_output}:/out:rw",
        "--entrypoint", "/bin/bash", IMAGE, "-lc",
        (
            "set -eu; export HOME=/home/agent PYTHONDONTWRITEBYTECODE=1 "
            "TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8; "
            "python3 /scaffold/v3/tools/run_afl_qemu_suite.py "
            f"--label {instance} --suite /suite --executable /binary "
            "--output /out --afl-root /afl --python /usr/bin/python3 --timeout-cap 30"
        ),
    ]
    log = (MEASURE / instance / "afl.container.log").open("wb")
    return f"afl:{instance}", subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT), log


def start_xan_native(*, resume: bool = False) -> tuple[str, subprocess.Popen[bytes], object]:
    instance = "medialab__xan.60a89e5"
    repo = OUTPUT / instance
    measure_root = MEASURE / instance / "native-retry"
    work, result = measure_root / "workspace", measure_root / "out"
    if not resume and work.exists():
        shutil.rmtree(work)
    if result.exists():
        shutil.rmtree(result)
    work.mkdir(parents=True, exist_ok=True)
    result.mkdir(parents=True)
    source = ROOT / "sources" / instance
    dependency_cache = repo / "dependencies/current/cache"
    script = r"""
set -eu
export PATH=/rust/bin:/cargo-tools:/usr/bin:/bin CARGO_HOME=/workspace/cargo CARGO_NET_OFFLINE=true CARGO_BUILD_JOBS=2
export HOME=/workspace/home TMPDIR=/workspace/tmp TZ=UTC TZDIR=/usr/share/zoneinfo LANG=C.UTF-8 LC_ALL=C.UTF-8
export LIBCLANG_PATH=/libclang LD_LIBRARY_PATH=/libclang
mkdir -p /workspace/src /workspace/home /workspace/tmp /workspace/cargo
cp -a /dependency-cache/cargo/. /workspace/cargo/
tar -C /source --exclude=.git --exclude=target -cf - . | tar -C /workspace/src -xf -
mkdir -p /workspace/target-native/tests
cp -a /workspace/src/tests/resources /workspace/target-native/tests/
cd /workspace/src
CARGO_TARGET_DIR=/workspace/target-native cargo llvm-cov --no-clean --workspace --locked --json --output-path /out/native.coverage.json > /out/native.coverage.log 2>&1
printf '{"native_test_returncode":0,"native_coverage_returncode":0,"tzdb_retry":true}\n' > /out/native.status.json
"""
    command = hardened_base("pb-manual-native-xan", instance, memory="8g", cpus="2")
    command += [
        "-v", f"{source}:/source:ro",
        "-v", "/home/programbench/.rustup/toolchains/stable-x86_64-unknown-linux-gnu:/rust:ro",
        "-v", "/home/programbench/.cargo/bin/cargo-llvm-cov:/cargo-tools/cargo-llvm-cov:ro",
        "-v", f"{dependency_cache}:/dependency-cache:ro",
        "-v", "/usr/lib/llvm-14/lib:/libclang:ro",
        "-v", "/usr/share/zoneinfo:/usr/share/zoneinfo:ro",
        "-v", f"{work}:/workspace:rw", "-v", f"{result}:/out:rw",
        "--entrypoint", "/bin/bash", IMAGE, "-lc", script,
    ]
    log = (measure_root / "container.log").open("wb")
    return "native:xan", subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT), log


def main() -> int:
    MEASURE.mkdir(parents=True, exist_ok=True)
    native_only = "--native-only" in sys.argv[1:]
    jobs = [] if native_only else [
        start_afl(instance, iteration) for instance, iteration in CHECKPOINTS.items()
    ]
    jobs.append(start_xan_native(resume=native_only))
    (MEASURE / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "programbench_v4_manual_metrics_batch_v1",
                "jobs": [name for name, _, _ in jobs],
                "accepted_iterations": CHECKPOINTS,
                "network": "none",
                "runtime_image_id": IMAGE,
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    failed: list[tuple[str, int]] = []
    for name, process, log in jobs:
        returncode = process.wait()
        log.close()
        print(f"{name} rc={returncode}", flush=True)
        if returncode:
            failed.append((name, returncode))
    print(f"failed={failed}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
