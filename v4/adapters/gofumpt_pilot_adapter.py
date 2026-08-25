#!/usr/bin/env python3
"""V4 adapter for deterministic Go command-line repositories.

The original controlled gofumpt pilot established these contracts. Repository
specific build/test/package data now comes exclusively from the scoped campaign
manifest so the same hardened stages can serve the wider Go cohort.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.programbench_agent_provider import run_agent_maestro_review
from v4.programbench_v4.candidates import exact_key, select_tranche
from v4.programbench_v4.dependencies import (
    DependencyCacheLock,
    MANIFEST_NAME as DEPENDENCY_MANIFEST_NAME,
    SCHEMA as DEPENDENCY_SCHEMA,
    dependency_cache_sha256,
    dependency_scope,
    go_offline_proxy_environment,
    quarantine_stale_dependency_cache,
    resolve_go_prefetch_install_specs,
    validate_dependency_cache,
)
from v4.programbench_v4.io import atomic_write_json, read_json
from v4.programbench_v4.isolation import ContainerContract, Mount
from v4.programbench_v4.go_afl_qemu import (
    build_go_qemu_metrics,
    persist_go_qemu_checkpoint,
    validate_go_qemu_scope,
)
from v4.programbench_v4.rust_afl_qemu import (
    build_rust_qemu_checkpoint,
    build_rust_qemu_metrics,
    persist_rust_qemu_checkpoint,
    validate_rust_qemu_scope,
)
from v4.programbench_v4.stage_resilience import (
    canonical_case_name,
    canonicalize_case_identities,
    repeat_failure_case_names,
    run_semantic_retry,
)
from v4.programbench_v4.witnesses import (
    adaptive_witness_capacity,
    validate_witness_map,
    witness_preserving_replacement,
)


SUPPORTED_CASE_FIELDS = {
    "name", "area", "args", "argv0", "stdin", "env", "files",
    "binary_files", "executable_files", "repeat_files", "file_modes",
    "terminal", "sequence", "observe_files", "isolate_home_tmp",
    "stdin_regular_file", "timeout_seconds", "rationale",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture_scope_id(cases_path: Path) -> str:
    return sha256_file(cases_path)[:16]


def provenance(
    stage: str, image: str, binary: Path | None = None, *, network: str = "none"
) -> dict[str, Any]:
    return {
        "stage": stage,
        "image_id": image,
        "network": network,
        "binary_sha256": sha256_file(binary) if binary is not None else None,
        "security": {
            "read_only_rootfs": True,
            "cap_drop_all": True,
            "no_new_privileges": True,
            "non_root_user": True,
            "docker_socket_mounted": False,
        },
    }


def run_container(
    *,
    image: str,
    stage: str,
    mounts: tuple[Mount, ...],
    script: str,
    log: Path,
    timeout: int = 900,
    memory: str = "4g",
) -> list[str]:
    contract = ContainerContract(
        image_id=image,
        stage=stage,
        name=f"pb-v4-go-{stage}-{uuid.uuid4().hex}",
        mounts=mounts,
        memory=memory,
        cpus=2,
        pids_limit=512,
        entrypoint="/bin/sh",
    )
    command = contract.docker_run(["-c", script])
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        subprocess.run(["docker", "rm", "-f", contract.name], text=True, capture_output=True)
        atomic_write_json(
            log,
            {
                "command": command,
                "returncode": None,
                "timed_out": True,
                "timeout_seconds": timeout,
                "stdout": exc.stdout or "",
                "stderr": exc.stderr or "",
            },
        )
        raise RuntimeError(f"container stage {stage} timed out after {timeout}s") from exc
    atomic_write_json(
        log,
        {
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        },
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"container stage {stage} failed: {detail[-3000:]}")
    return command


def cleanup_repo_containers(repo_root: Path) -> list[str]:
    """Best-effort cleanup of Docker containers whose command references this repo."""

    result = subprocess.run(
        ["docker", "ps", "--no-trunc", "--format", "{{.ID}}\t{{.Command}}\t{{.Names}}"],
        text=True,
        capture_output=True,
    )
    removed: list[str] = []
    if result.returncode != 0:
        return removed
    needle = str(repo_root)
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 2:
            continue
        container_id, command = parts[0], parts[1]
        if needle not in command:
            continue
        subprocess.run(["docker", "rm", "-f", container_id], text=True, capture_output=True)
        removed.append(container_id)
    return removed


def dependency_prefetch(request: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    """Serialize cache publication and preserve valid caches from older scopes."""

    dependencies = repo_root / "dependencies"
    dependencies.mkdir(parents=True, exist_ok=True)
    with DependencyCacheLock(dependencies):
        current = dependencies / "current"
        quarantined: Path | None = None
        if current.exists():
            try:
                validate_dependency_cache(repo_root, request["repository"])
            except ValueError as exc:
                if "scope mismatch" not in str(exc):
                    # Incomplete/tampered caches remain in place for audit and
                    # stop the workflow instead of being silently replaced.
                    raise
                quarantined = quarantine_stale_dependency_cache(
                    repo_root, request["repository"]
                )
        result = _dependency_prefetch_locked(request, repo_root)
        result["dependency_cache"]["quarantined_previous"] = (
            str(quarantined) if quarantined else None
        )
        return result


def _dependency_prefetch_locked(request: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    """Populate one scope-bound cache with network, then publish it atomically."""

    repo = request["repository"]
    image = str(repo["runtime_image_id"])
    dependencies = repo_root / "dependencies"
    dependencies.mkdir(parents=True, exist_ok=True)
    current = dependencies / "current"
    if current.exists():
        manifest = validate_dependency_cache(repo_root, repo)
        return {
            "execution_provenance": [
                provenance("dependency_prefetch", image, network="bridge")
            ],
            "dependency_cache": {**manifest, "resumed": True},
        }
    for stale in dependencies.glob(".stage-*"):
        if stale.is_dir():
            shutil.rmtree(stale)

    stage = dependencies / f".stage-{uuid.uuid4().hex}"
    cache = stage / "cache"
    cache.mkdir(parents=True)
    source = Path(repo["source_dir"])
    language = str(repo["language"])
    if language == "go":
        go_root = Path(repo["go_toolchain_root"])
        test_install_dependencies = resolve_go_prefetch_install_specs(repo)
        install_lines = "\n".join(
            "GOBIN=/workspace/prefetch-bin go install " + shlex.quote(spec)
            for spec in test_install_dependencies["specs"]
        )
        script = rf'''
set -eu
export PATH=/go/bin:/usr/bin:/bin GOROOT=/go GOMODCACHE=/out/gomod GOCACHE=/workspace/gocache GOTMPDIR=/workspace/gotmp TMPDIR=/workspace/tmp
export GOTOOLCHAIN=local GOPROXY=https://proxy.golang.org,direct GOSUMDB=sum.golang.org
export HOME=/workspace/home TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8
mkdir -p /out/gomod /workspace/src /workspace/home /workspace/gocache /workspace/gotmp /workspace/tmp /workspace/prefetch-bin
cp -R --no-preserve=ownership,mode,timestamps /source/. /workspace/src/
cd /workspace/src
go mod download all
{install_lines}
go list -deps -test ./... > /out/go-dependencies.txt || true
'''
        mounts = (
            Mount(source, "/source"),
            Mount(go_root, "/go"),
            Mount(cache, "/out", readonly=False),
        )
    elif language == "rust":
        rust_root = Path(repo["rust_toolchain_root"])
        target = str(repo.get("rust_target_triple") or "x86_64-unknown-linux-gnu")
        fetch_args = str(repo.get("rust_fetch_args") or f"--locked --target {target}")
        script = rf'''
set -eu
export PATH=/rust/bin:/cargo/bin:/usr/bin:/bin CARGO_HOME=/out/cargo
export CARGO_NET_GIT_FETCH_WITH_CLI=false HOME=/workspace/home TMPDIR=/workspace/tmp
export TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8
mkdir -p /out/cargo /workspace/src /workspace/home /workspace/tmp
tar -C /source --exclude=.git --exclude=target -cf - . | tar -C /workspace/src -xf -
cd /workspace/src
cargo fetch {fetch_args}
'''
        mounts = (
            Mount(source, "/source"),
            Mount(rust_root, "/rust"),
            Mount(cache, "/out", readonly=False),
        )
    else:
        raise ValueError(f"unsupported language: {language}")
    try:
        command = run_container(
            image=image,
            stage="dependency_prefetch",
            mounts=mounts,
            script=script,
            log=repo_root / "logs/dependency_prefetch_container.json",
            timeout=int(repo.get("dependency_prefetch_timeout_seconds") or 3600),
        )
        scope_sha, scope_payload = dependency_scope(repo)
        manifest = {
            "schema": DEPENDENCY_SCHEMA,
            "dependency_scope_sha256": scope_sha,
            "dependency_scope": scope_payload,
            "cache_sha256": dependency_cache_sha256(cache),
            "network": "bridge",
            "container_command": command,
        }
        atomic_write_json(stage / DEPENDENCY_MANIFEST_NAME, manifest)
        stage.replace(current)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    validated = validate_dependency_cache(repo_root, repo)
    return {
        "execution_provenance": [
            provenance("dependency_prefetch", image, network="bridge")
        ],
        "dependency_cache": {**validated, "resumed": False},
    }


def normalize_agent_cases(payload: dict[str, Any], *, iteration: int) -> list[dict[str, Any]]:
    rows = payload.get("cases")
    if not isinstance(rows, list):
        raise ValueError("agent response has no cases array")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            continue
        args = raw.get("args")
        stdin = raw.get("stdin", "")
        if not isinstance(args, list) or not all(isinstance(value, str) for value in args):
            continue
        if not isinstance(stdin, str):
            continue
        forbidden = {"returncode", "stdout", "stderr", "expected_stdout", "expected_stderr"}
        if forbidden & set(raw):
            continue
        case = {key: raw[key] for key in SUPPORTED_CASE_FIELDS if key in raw}
        # The oracle generator canonicalizes case names for pytest identifiers.
        # Canonicalize before persistence as well, otherwise a hyphenated agent
        # name changes identity during capture and dummy failures cannot map
        # back to the retained candidate.
        raw_name = str(raw.get("name") or "case")[:80]
        case["name"] = canonical_case_name(
            f"v4_t{iteration:02d}_{index:03d}_{raw_name}"
        )
        case["area"] = str(raw.get("area") or "exploratory")[:80]
        case["args"] = args
        case["stdin"] = stdin
        case["origin"] = "v4_agent_tranche"
        key = exact_key(case)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(case)
    if not normalized:
        raise ValueError("agent produced no valid behavioral candidates")
    return normalized


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def _decode_json_relaxed(text: str) -> Any:
    return json.JSONDecoder(strict=False).decode(_strip_json_fence(text))


def _extract_cases_payload_from_text(text: str) -> dict[str, Any] | None:
    """Recover agent cases from complete or partially truncated JSON text.

    Agent responses are often valid in intent but not strict JSON: long fixture
    strings may contain literal control characters, and large tranches can be
    cut after several complete case objects. Treat the raw response as the
    authoritative source and salvage complete case objects before falling back
    to generic seeds.
    """

    stripped = _strip_json_fence(text)
    if not stripped:
        return None
    decoder = json.JSONDecoder(strict=False)
    try:
        payload = decoder.decode(stripped)
        if isinstance(payload, dict) and isinstance(payload.get("cases"), list):
            return payload
        if isinstance(payload, list):
            return {"cases": payload}
    except json.JSONDecodeError:
        pass

    marker = re.search(r'"cases"\s*:\s*\[', stripped)
    if marker is None:
        return None
    index = marker.end()
    cases: list[Any] = []
    while index < len(stripped):
        while index < len(stripped) and stripped[index] in " \t\r\n,":
            index += 1
        if index >= len(stripped) or stripped[index] == "]":
            break
        if stripped[index] != "{":
            next_object = stripped.find("{", index)
            if next_object < 0:
                break
            index = next_object
        try:
            item, end = decoder.raw_decode(stripped, index)
        except json.JSONDecodeError:
            break
        if isinstance(item, dict):
            cases.append(item)
        index = end
    if cases:
        return {"cases": cases, "partial_agent_json_recovered": True}
    return None


def _load_agent_payload_from_artifacts(attempt_root: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    for name in ("response_text.txt", "review.json"):
        path = attempt_root / name
        if not path.is_file():
            continue
        try:
            payload = _extract_cases_payload_from_text(path.read_text(encoding="utf-8-sig", errors="replace"))
        except OSError:
            payload = None
        if payload is not None:
            payload["payload_source"] = name
            return payload
    if isinstance(fallback, dict) and isinstance(fallback.get("cases"), list):
        return fallback
    if isinstance(fallback, dict) and isinstance(fallback.get("args"), list):
        return {"cases": [fallback], "payload_source": "provider_single_case"}
    return fallback


def fallback_behavioral_cases(*, iteration: int, requested: int) -> list[dict[str, Any]]:
    """Small black-box seed tranche used when the model returns malformed JSON."""

    templates = [
        ("help", ["--help"], ""),
        ("short_help", ["-h"], ""),
        ("version", ["--version"], ""),
        ("invalid_flag", ["--programbench-invalid-flag"], ""),
        ("stdin_empty", [], ""),
        ("stdin_unicode", [], "alpha\nUnicode: 測試\n"),
        ("stdin_long_line", [], "x" * 256 + "\n"),
        ("stdin_malformed_bytes_text", [], "\u0000\u0001not-binary-but-odd\n"),
        ("missing_file", ["missing-programbench-input.txt"], ""),
        ("dash_stdin", ["-"], "one\ntwo\n"),
    ]
    limit = max(1, min(int(requested or 0), len(templates), 16))
    cases: list[dict[str, Any]] = []
    for index, (label, args, stdin) in enumerate(templates[:limit]):
        cases.append(
            {
                "name": canonical_case_name(f"v4_t{iteration:02d}_{index:03d}_fallback_{label}"),
                "area": "fallback_seed",
                "args": list(args),
                "stdin": stdin,
                "origin": "v4_agent_semantic_fallback",
                "rationale": "Fallback black-box seed after malformed model payload.",
            }
        )
    return cases


def optional_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    return read_json(path) if path.is_file() else dict(default)


def cleanroom_image(
    repo_root: Path, runtime_image: str, runtime_reference: str, binary: Path,
    *, instance_id: str,
) -> str:
    context = repo_root / "cleanroom_context"
    context.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, context / "executable")
    (context / "README.md").write_text(
        f"ProgramBench V4 cleanroom for {instance_id}\n", encoding="utf-8"
    )
    (context / "Dockerfile").write_text(
        f"FROM {runtime_reference}\n"
        "USER root\nRUN mkdir -p /workspace && chown 1000:1000 /workspace\n"
        "COPY --chown=1000:1000 executable /workspace/executable\n"
        "COPY --chown=1000:1000 README.md /workspace/README.md\n"
        "RUN chmod 0500 /workspace/executable && chmod 0400 /workspace/README.md\n"
        "USER 1000:1000\nWORKDIR /workspace\nENTRYPOINT [\"/workspace/executable\"]\n",
        encoding="utf-8",
    )
    tag = f"programbench/v4-go-cleanroom:{uuid.uuid4().hex}"
    result = subprocess.run(
        ["docker", "build", "--pull=false", "--network", "none", "-t", tag, str(context)],
        text=True,
        capture_output=True,
        timeout=600,
    )
    atomic_write_json(
        repo_root / "logs/cleanroom_build.json",
        {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "tag": tag},
    )
    if result.returncode != 0:
        raise RuntimeError(f"cleanroom build failed: {result.stderr[-2000:]}")
    base_inspect = subprocess.run(
        ["docker", "image", "inspect", runtime_reference, "--format", "{{.Id}}"],
        text=True,
        capture_output=True,
        check=True,
    )
    if base_inspect.stdout.strip() != runtime_image:
        raise RuntimeError("runtime tag drifted from the configured immutable image ID")
    inspect = subprocess.run(
        ["docker", "image", "inspect", tag, "--format", "{{.Id}}"],
        text=True,
        capture_output=True,
        check=True,
    )
    image_id = inspect.stdout.strip()
    if not image_id.startswith("sha256:"):
        raise RuntimeError("cleanroom image is not immutable")
    listing = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "/bin/sh", image_id, "-c", "find /workspace -maxdepth 2 -type f -printf '%P\\n' | sort"],
        text=True,
        capture_output=True,
        timeout=30,
    )
    if listing.returncode != 0 or listing.stdout.split() != ["README.md", "executable"]:
        raise RuntimeError(f"cleanroom is not binary-only: {listing.stdout} {listing.stderr}")
    return image_id


def preflight(request: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    repo = request["repository"]
    if str(repo.get("language")) == "rust":
        return preflight_rust(request, repo_root)
    image = repo["runtime_image_id"]
    source = Path(repo["source_dir"])
    go_root = Path(repo["go_toolchain_root"])
    dependency = validate_dependency_cache(repo_root, repo)
    dependency_cache = Path(dependency["cache_dir"])
    artifacts = repo_root / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    build_package = str(repo.get("go_build_package") or ".")
    native_package = str(repo.get("go_native_coverage_package") or "./...")
    native_test_command = str(repo.get("go_native_test_command") or "go test ./... -count=1")
    cover_package = str(repo.get("go_cover_package") or "./...")
    offline_go_environment = go_offline_proxy_environment("/workspace/gomod")
    offline_go_export = " ".join(
        f"{name}={shlex.quote(value)}"
        for name, value in sorted(offline_go_environment.items())
    )
    script = rf'''
set -eu
export PATH=/go/bin:/usr/bin:/bin GOROOT=/go GOCACHE=/workspace/gocache GOTMPDIR=/workspace/gotmp TMPDIR=/workspace/tmp
export {offline_go_export}
export GOTOOLCHAIN=local HOME=/workspace/home TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8
mkdir -p /workspace/src /workspace/home /workspace/gomod /workspace/gocache /workspace/gotmp /workspace/tmp
cp -a /dependency-cache/gomod/. /workspace/gomod/
cp -R --no-preserve=ownership,mode,timestamps /source/. /workspace/src/
cd /workspace/src
set +e
{native_test_command} > /out/native.tests.log 2>&1
native_test_rc=$?
go test {shlex.quote(native_package)} -count=1 -coverprofile=/out/native.coverprofile > /out/native.coverage.log 2>&1
native_coverage_rc=$?
if [ "$native_coverage_rc" -eq 0 ]; then
  go tool cover -func=/out/native.coverprofile > /out/native.coverage.txt 2>/out/native.cover.tool.log
else
  printf 'native coverage unavailable rc=%s\n' "$native_coverage_rc" > /out/native.coverage.txt
fi
printf '{{"native_test_returncode":%s,"native_coverage_returncode":%s}}\n' "$native_test_rc" "$native_coverage_rc" > /out/native.status.json
set -e
go build -trimpath -o /out/reference_executable {shlex.quote(build_package)}
go build -trimpath -cover -covermode=atomic -coverpkg={shlex.quote(cover_package)} -o /out/coverage_executable {shlex.quote(build_package)}
chmod 0755 /out/reference_executable /out/coverage_executable
'''
    run_container(
        image=image,
        stage="source_build",
        mounts=(
            Mount(source, "/source"),
            Mount(go_root, "/go"),
            Mount(dependency_cache, "/dependency-cache"),
            Mount(artifacts, "/out", readonly=False),
        ),
        script=script,
        log=repo_root / "logs/preflight_container.json",
    )
    reference = artifacts / "reference_executable"
    coverage = artifacts / "coverage_executable"
    image_id = cleanroom_image(
        repo_root, image, str(repo["runtime_image_reference"]), reference,
        instance_id=str(repo["instance_id"]),
    )
    payload = {
        "reference_binary": str(reference),
        "reference_binary_sha256": sha256_file(reference),
        "coverage_binary": str(coverage),
        "coverage_binary_sha256": sha256_file(coverage),
        "cleanroom_image_id": image_id,
        "native_coverage_text": (artifacts / "native.coverage.txt").read_text(encoding="utf-8"),
        "native_status": optional_json(
            artifacts / "native.status.json",
            {"native_test_returncode": None, "native_coverage_returncode": None},
        ),
        "native_test_scope": {
            "executed": [native_test_command, f"go test {native_package} -count=1 -coverprofile=..."],
            "coverage_package": native_package,
            "excluded": list(repo.get("native_test_exclusions") or []),
            "reason": str(repo.get("native_test_scope_note") or "strict offline V4 execution")
        },
    }
    atomic_write_json(artifacts / "preflight.json", payload)
    return {
        "execution_provenance": [
            provenance("source_build", image, reference),
            provenance("native_tests", image, reference),
        ],
        "preflight": payload,
    }


def preflight_rust(request: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    repo = request["repository"]
    image = repo["runtime_image_id"]
    source = Path(repo["source_dir"])
    rust_root = Path(repo["rust_toolchain_root"])
    cargo_llvm_cov = Path(repo["rust_cargo_llvm_cov_binary"])
    expected_cargo_llvm_cov = str(repo["rust_cargo_llvm_cov_sha256"])
    if sha256_file(cargo_llvm_cov) != expected_cargo_llvm_cov:
        raise RuntimeError("cargo-llvm-cov binary drifted from the configured SHA256")
    dependency = validate_dependency_cache(repo_root, repo)
    dependency_cache = Path(dependency["cache_dir"])
    artifacts = repo_root / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    build_args = str(repo.get("rust_build_args") or "--release --locked")
    native_args = str(repo.get("rust_native_test_args") or "--workspace --locked --no-fail-fast")
    llvm_cov_args = str(repo.get("rust_llvm_cov_args") or "--workspace --locked")
    binary_relpath = str(repo.get("rust_binary_relpath") or "release/" + str(repo["binary_name"]))
    libclang_root = repo.get("rust_libclang_root")
    libclang_env = "export LIBCLANG_PATH=/libclang LD_LIBRARY_PATH=/libclang${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" if libclang_root else ""
    script = rf'''
set -eu
export PATH=/rust/bin:/cargo-tools:/usr/bin:/bin CARGO_HOME=/workspace/cargo CARGO_NET_OFFLINE=true
# Cargo defaults to the host CPU count, which can exceed the per-repository
# container memory budget on large Rust workspaces.  Keep repository-level
# parallelism in the controller, but compile serially inside each container.
export CARGO_BUILD_JOBS=${{CARGO_BUILD_JOBS:-1}}
export HOME=/workspace/home TMPDIR=/workspace/tmp TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8
{libclang_env}
mkdir -p /workspace/src /workspace/home /workspace/tmp /workspace/cargo
cp -a /dependency-cache/cargo/. /workspace/cargo/
tar -C /source --exclude=.git --exclude=target -cf - . | tar -C /workspace/src -xf -
cd /workspace/src
set +e
CARGO_TARGET_DIR=/workspace/target-native cargo test {native_args} > /out/native.tests.log 2>&1
native_test_rc=$?
CARGO_TARGET_DIR=/workspace/target-native cargo llvm-cov {llvm_cov_args} --json --output-path /out/native.coverage.json > /out/native.coverage.log 2>&1
native_coverage_rc=$?
if [ "$native_coverage_rc" -ne 0 ]; then
  printf '{{"data":[],"native_coverage_unavailable":true,"native_coverage_returncode":%s}}\n' "$native_coverage_rc" > /out/native.coverage.json
fi
printf '{{"native_test_returncode":%s,"native_coverage_returncode":%s}}\n' "$native_test_rc" "$native_coverage_rc" > /out/native.status.json
set -e
CARGO_TARGET_DIR=/workspace/target-ref cargo build {build_args}
cp /workspace/target-ref/{shlex.quote(binary_relpath)} /out/reference_executable
export RUSTFLAGS='-C instrument-coverage -C link-dead-code'
CARGO_TARGET_DIR=/workspace/target-cov cargo build {build_args}
cp /workspace/target-cov/{shlex.quote(binary_relpath)} /out/coverage_executable
chmod 0755 /out/reference_executable /out/coverage_executable
'''
    mounts = [
        Mount(source, "/source"),
        Mount(rust_root, "/rust"),
        Mount(cargo_llvm_cov, "/cargo-tools/cargo-llvm-cov"),
        Mount(dependency_cache, "/dependency-cache"),
        Mount(artifacts, "/out", readonly=False),
    ]
    if libclang_root:
        mounts.append(Mount(Path(str(libclang_root)), "/libclang"))
    run_container(
        image=image,
        stage="source_build",
        mounts=tuple(mounts),
        script=script,
        log=repo_root / "logs/preflight_container.json",
        timeout=int(repo.get("preflight_timeout_seconds") or 3600),
        memory=str(repo.get("rust_preflight_memory") or "6g"),
    )
    reference = artifacts / "reference_executable"
    coverage = artifacts / "coverage_executable"
    image_id = cleanroom_image(
        repo_root,
        image,
        str(repo["runtime_image_reference"]),
        reference,
        instance_id=str(repo["instance_id"]),
    )
    native_json = read_json(artifacts / "native.coverage.json")
    payload = {
        "reference_binary": str(reference),
        "reference_binary_sha256": sha256_file(reference),
        "coverage_binary": str(coverage),
        "coverage_binary_sha256": sha256_file(coverage),
        "cleanroom_image_id": image_id,
        "native_coverage": native_json,
        "native_status": optional_json(
            artifacts / "native.status.json",
            {"native_test_returncode": None, "native_coverage_returncode": None},
        ),
        "native_test_scope": {
            "executed": [f"cargo test {native_args}", f"cargo llvm-cov {llvm_cov_args}"],
            "excluded": list(repo.get("native_test_exclusions") or []),
            "reason": str(repo.get("native_test_scope_note") or "strict offline V4 execution"),
        },
    }
    atomic_write_json(artifacts / "preflight.json", payload)
    return {
        "execution_provenance": [
            provenance("source_build", image, reference),
            provenance("native_tests", image, reference),
        ],
        "preflight": payload,
    }


def coverage_gap_feedback(repo_root: Path, *, limit: int = 40) -> str:
    latest = repo_root / "artifacts/latest_quick_coverage.json"
    if not latest.is_file():
        return ""
    profile = Path(str(read_json(latest).get("profile") or ""))
    if not profile.is_file():
        return ""
    if profile.suffix == ".json":
        # Keep prompt size bounded, but add function-level clusters so the
        # agent can distinguish parser/format/error families inside one large
        # source file. Exact LLVM segments remain internal evidence.
        export = read_json(profile)
        gaps: list[tuple[int, str]] = []
        for file_row in ((export.get("data") or [{}])[0].get("files") or []):
            regions = (file_row.get("summary") or {}).get("regions") or {}
            missing = int(regions.get("count") or 0) - int(regions.get("covered") or 0)
            if missing > 0:
                gaps.append((missing, str(file_row.get("filename") or "")))
        gaps.sort(key=lambda item: (-item[0], item[1]))
        functions: list[str] = []
        for row in ((export.get("data") or [{}])[0].get("functions") or []):
            if int(row.get("count") or 0) != 0:
                continue
            filenames = [str(name) for name in (row.get("filenames") or [])]
            if not any("/workspace/src/" in name.replace("\\", "/") for name in filenames):
                continue
            name = str(row.get("name") or "").strip()
            if name and name not in functions:
                functions.append(name)
        rows = [f"- file: {name} ({missing} uncovered regions)" for missing, name in gaps[:limit]]
        rows += [f"- function cluster: {name}" for name in functions[: min(16, limit)]]
        return "\n".join(rows)
    gaps: list[tuple[int, str]] = []
    for line in profile.read_text(encoding="utf-8").splitlines()[1:]:
        match = re.match(r"(.+?)\s+(\d+)\s+(\d+)$", line)
        if not match or int(match.group(3)) != 0:
            continue
        gaps.append((int(match.group(2)), match.group(1)))
    gaps.sort(key=lambda item: (-item[0], item[1]))
    return "\n".join(f"- {location} ({statements} statements)" for statements, location in gaps[:limit])


def plan_tranche(request: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    iteration = int(request["iteration"])
    context = request.get("workflow_context") or {}
    target_min = int(context.get("target_retained_cases_min") or 0)
    target_max = int(context.get("target_retained_cases_max") or 0)
    retained = int(context.get("retained_suite_cases") or 0)
    requested = int(context["recommended_tranche_size"])
    if target_min and retained < target_min:
        gap = max(0, target_min - retained)
        requested = max(requested, min(384, max(64, round(gap * 0.25))))
    themes = list(request["repository"].get("behavior_themes") or [
        "primary successful CLI workflows with representative files and stdin",
        "flag combinations, malformed inputs, diagnostics, and boundary behavior",
        "multi-file fixtures, stateful filesystem effects, Unicode, and deterministic edge cases",
    ])
    bootstrap_iterations = int(context.get("breadth_bootstrap_iterations") or 0)
    configured_views = list(context.get("breadth_bootstrap_views") or [])
    if iteration <= bootstrap_iterations and configured_views:
        # Breadth bootstrap is a bounded set of views in one tranche, not a
        # Cartesian product of themes, flags and perspectives. This gives the
        # Agent useful V3-style breadth without recreating the old 48-batch
        # candidate explosion.
        breadth_views = [
            {
                "view": view,
                "theme": themes[(iteration - 1 + index) % len(themes)],
            }
            for index, view in enumerate(configured_views[:8])
        ]
    else:
        breadth_views = []
    plan = {
        "theme": themes[(iteration - 1) % len(themes)],
        # Two bounded themes maintain broad exploration after bootstrap. They
        # are a portfolio, never a Cartesian product with views or fixtures.
        "theme_portfolio": list(dict.fromkeys((
            themes[(iteration - 1) % len(themes)],
            themes[(iteration + max(1, len(themes) // 2) - 1) % len(themes)],
        ))),
        "breadth_bootstrap": bool(breadth_views),
        "breadth_views": breadth_views,
        "reservoir_rerank_period": int(context.get("reservoir_rerank_period") or 3),
        "requested_cases": requested,
        "target_retained_cases_min": target_min or None,
        "target_retained_cases_max": target_max or None,
        "target_scale_profile": context.get("target_scale_profile"),
        "iteration": iteration,
        "coverage_gaps": coverage_gap_feedback(repo_root),
    }
    atomic_write_json(repo_root / f"plans/tranche-{iteration:04d}.json", plan)
    return {"plan": plan}


def agent_request_limits(repo: dict[str, Any], planned_cases: int) -> dict[str, int]:
    """Return bounded per-transaction limits without changing suite targets."""

    return {
        "context_max_bytes": max(
            40_000,
            min(220_000, int(repo.get("agent_context_max_bytes") or 120_000)),
        ),
        "requested_cases": max(
            1,
            min(int(planned_cases), int(repo.get("agent_request_case_cap") or 48)),
        ),
        "max_tokens": max(
            4096,
            min(20_000, int(repo.get("agent_max_tokens") or 12_000)),
        ),
        "transport_retries": max(
            1,
            min(5, int(repo.get("agent_transport_retries") or 2)),
        ),
    }


def generate(request: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    repo = request["repository"]
    iteration = int(request["iteration"])
    plan = read_json(repo_root / f"plans/tranche-{iteration:04d}.json")
    context_dir = repo_root / f"agent_context/tranche-{iteration:04d}"
    context_dir.mkdir(parents=True, exist_ok=True)
    mapper = ROOT / "v4/tools/build_agent_context.py"
    # Large late-round requests were repeatedly reaching the model gateway's
    # fixed 600 second deadline (128 cases, 220 KB of source context and a
    # 20k-token response).  Keep the campaign-level suite target unchanged,
    # but make each Agent transaction small enough to complete and let the
    # marginal controller accumulate multiple successful tranches.
    request_limits = agent_request_limits(repo, int(plan["requested_cases"]))
    context_max_bytes = request_limits["context_max_bytes"]
    requested_cases = request_limits["requested_cases"]
    run_container(
        image=repo["runtime_image_id"],
        stage="agent_tool_execution",
        mounts=(
            Mount(Path(repo["source_dir"]), "/source"),
            Mount(mapper, "/scaffold/build_agent_context.py"),
            Mount(context_dir, "/out", readonly=False),
        ),
        script=(
            "python3 /scaffold/build_agent_context.py --source /source "
            f"--output /out/source_context.md --max-bytes {context_max_bytes}"
        ),
        log=repo_root / f"logs/context-{iteration:04d}.json",
        timeout=120,
    )
    source_context = (context_dir / "source_context.md").read_text(encoding="utf-8")
    repository_name = str(repo.get("repository") or repo["instance_id"])
    binary_name = str(repo.get("binary_name") or repository_name.rsplit("/", 1)[-1])
    target_guidance = ""
    if plan.get("target_retained_cases_min") and plan.get("target_retained_cases_max"):
        target_guidance = (
            f"The campaign's PB-scale retained-suite target for this repository is roughly "
            f"{plan['target_retained_cases_min']}-{plan['target_retained_cases_max']} quality-filtered behavioral cases. "
            "This target is not a license for template filling: generate only cases with distinct behavior, fixture, error, assertion, state, or coverage value."
        )
    breadth_guidance = ""
    if plan.get("breadth_views"):
        breadth_guidance = (
            "For this bounded breadth bootstrap, cover each of these independent views "
            "at least once within the single tranche; do not form a Cartesian product "
            "between views, themes, options, or fixtures:\n"
            + "\n".join(
                f"- {row['view']}: {row['theme']}" for row in plan["breadth_views"]
            )
        )
    portfolio = "\n".join(f"- {theme}" for theme in plan.get("theme_portfolio") or [plan["theme"]])
    prompt = f"""Generate at most {requested_cases} deterministic black-box behavioral candidates for the pinned {repository_name} CLI executable `{binary_name}`.
Use this bounded behavior-theme portfolio, covering both without a Cartesian expansion:
{portfolio}
{breadth_guidance}
{target_guidance}
Prioritize these currently uncovered instrumented source blocks when they are reachable through the CLI:
{plan.get('coverage_gaps') or 'No comparable prior coverage profile is available yet.'}
The runtime directory starts empty. Materialize every needed path through the `files` field.
Use only these fields: name, area, args, stdin, env, files, observe_files, timeout_seconds, rationale.
Do not invent expected stdout, stderr, or return codes. Do not use shell commands, public network, random time, or source-relative runtime paths.
Avoid help/version repetitions and shallow missing-file variants. Prefer realistic successful workflows, fixture-rich cases, and bounded malformed/boundary cases appropriate to this repository.
For parsers and option-bearing commands, include a small structured boundary set when applicable: empty/minimal, one representative normal case, one malformed case, Unicode, and one interaction between two meaningful options. Do not enumerate every value or option combination.
Treat the pinned native tests as a high-value behavior map: study their CLI invocations, fixtures, option combinations, error paths, state transitions, and regression cases, then express those behaviors as independent black-box candidates. Do not merely translate implementation-level unit assertions, and never use held-out ProgramBench official oracle tests.
Return exactly one JSON object: {{"cases": [...]}}.

{source_context}
"""
    previous = os.environ.get("PROGRAMBENCH_WINDOWS_CLAUDE_BRIDGE")
    os.environ["PROGRAMBENCH_WINDOWS_CLAUDE_BRIDGE"] = "1"
    try:
        semantic_attempts = max(1, min(5, int(repo.get("agent_semantic_attempts") or 4)))

        def produce_payload(attempt: int, errors: tuple[str, ...]) -> dict[str, Any]:
            repair_feedback = ""
            if errors:
                repair_feedback = (
                    "\nYour previous response was rejected by the deterministic schema validator: "
                    + errors[-1]
                    + "\nReturn a fresh JSON object with a non-empty `cases` array and no prose."
                )
            attempt_root = repo_root / f"agent_runs/tranche-{iteration:04d}/attempt-{attempt:02d}"
            payload = run_agent_maestro_review(
                system=(
                    "You are the V4 ProgramBench oracle candidate generator. Target PB official tests are held out. "
                    "Use pinned source/docs/native tests to design executable black-box behavior tests. Return JSON only."
                ),
                prompt=prompt + repair_feedback,
                output_root=attempt_root,
                model=str(repo.get("model") or "gpt-5.6-sol"),
                max_tokens=request_limits["max_tokens"],
                timeout=900,
                retries=request_limits["transport_retries"],
            )
            return _load_agent_payload_from_artifacts(attempt_root, payload)

        semantic_fallback_used = False
        try:
            cases, semantic_errors = run_semantic_retry(
                produce_payload,
                lambda payload: normalize_agent_cases(payload, iteration=iteration),
                maximum_attempts=semantic_attempts,
            )
        except ValueError as exc:
            semantic_errors = (str(exc),)
            semantic_fallback_used = True
            cases = fallback_behavioral_cases(
                iteration=iteration, requested=requested_cases
            )
    finally:
        if previous is None:
            os.environ.pop("PROGRAMBENCH_WINDOWS_CLAUDE_BRIDGE", None)
        else:
            os.environ["PROGRAMBENCH_WINDOWS_CLAUDE_BRIDGE"] = previous
    atomic_write_json(
        repo_root / f"agent_runs/tranche-{iteration:04d}/semantic_validation.json",
        {
            "maximum_attempts": semantic_attempts,
            "attempts_used": len(semantic_errors) + 1,
            "rejected_payload_errors": list(semantic_errors),
            "semantic_fallback_used": semantic_fallback_used,
        },
    )
    candidate_path = repo_root / f"agent_cases/tranche-{iteration:04d}.json"
    atomic_write_json(candidate_path, {"profile": "programbench_v4", "cases": cases})
    reference = Path(read_json(repo_root / "artifacts/preflight.json")["reference_binary"])
    return {
        "candidate_path": str(candidate_path),
        "generated_cases": len(cases),
        "semantic_attempts_used": len(semantic_errors) + 1,
        "generation_scope": {
            "pb_official_tests_visible": False,
            "source_visible_to_generation_agent": True,
            "native_tests_visible_to_generation_agent": True,
            "inference_image_binary_only": True,
            "credentials_mounted_into_container": False,
        },
        "execution_provenance": [
            provenance("agent_tool_execution", repo["runtime_image_id"], reference)
        ],
    }


def static_select(request: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    iteration = int(request["iteration"])
    current_path = repo_root / "candidates/current.json"
    current = read_json(current_path).get("cases", []) if current_path.is_file() else []
    atomic_write_json(
        repo_root / f"candidates/pre-iteration-{iteration:04d}.json",
        {"profile": "programbench_v4", "cases": current},
    )
    current_keys = {exact_key(case) for case in current}
    incoming = read_json(repo_root / f"agent_cases/tranche-{iteration:04d}.json").get("cases", [])
    reservoir_path = repo_root / "candidates/reservoir.json"
    prior_reservoir = (
        read_json(reservoir_path).get("cases", [])
        if reservoir_path.is_file()
        else []
    )
    ledger_path = repo_root / "artifacts/raw_candidate_ledger.json"
    raw_ledger = read_json(ledger_path) if ledger_path.is_file() else {"iterations": {}}
    raw_iterations = dict(raw_ledger.get("iterations") or {})
    # Attempt volume remains diagnostic, but the explosion fuse is charged to
    # the durable unique reservoir. Re-generated duplicates must not consume
    # the safety budget again after a crash, retry, or witness replacement.
    raw_iterations[str(iteration)] = len(incoming)
    prior_keys = set(raw_ledger.get("unique_candidate_keys") or [])
    # Migrate V1 ledgers conservatively by seeding the keys that are still
    # materialized in the retained suite. New/deferred rows are then persisted
    # in this ledger even when they are not scheduled for capture yet.
    prior_keys.update(current_keys)
    incoming_unique: list[tuple[str, dict[str, Any]]] = []
    seen_incoming: set[str] = set()
    for case in incoming:
        key = exact_key(case)
        if key not in seen_incoming:
            incoming_unique.append((key, case))
            seen_incoming.add(key)
    raw_policy_fuse = int(
        request.get("workflow_context", {}).get("raw_candidate_fuse") or 0
    )
    if raw_policy_fuse and len(prior_keys) > raw_policy_fuse:
        raise RuntimeError("raw candidate ledger exceeds hard limit and requires repair")
    new_unique = [(key, case) for key, case in incoming_unique if key not in prior_keys]
    remaining_slots = (
        max(0, raw_policy_fuse - len(prior_keys))
        if raw_policy_fuse
        else len(new_unique)
    )
    admitted_new = new_unique[:remaining_slots]
    overflow = new_unique[remaining_slots:]
    admitted_keys = prior_keys | {key for key, _ in admitted_new}
    unique_keys = admitted_keys
    cumulative_raw = len(unique_keys)
    generation_attempts = sum(int(value) for value in raw_iterations.values())
    atomic_write_json(
        ledger_path,
        {
            "schema": "programbench_v4_raw_candidate_ledger_v2",
            "iterations": raw_iterations,
            "total_generation_attempts": generation_attempts,
            "unique_candidate_keys": sorted(unique_keys),
            "unique_persisted_raw_candidates": cumulative_raw,
            # Compatibility alias. Semantics are now explicitly unique.
            "total_generated_candidates": cumulative_raw,
        },
    )
    overflow_path: Path | None = None
    overflow_sha256: str | None = None
    if overflow:
        agent_path = repo_root / f"agent_cases/tranche-{iteration:04d}.json"
        overflow_payload = {
            "schema": "programbench_v4_raw_candidate_overflow_v1",
            "reason": "raw_candidate_hard_fuse_capacity_exhausted",
            "scope_sha256": request.get("scope_sha256"),
            "iteration": iteration,
            "raw_candidate_limit": raw_policy_fuse,
            "prior_unique_candidates": len(prior_keys),
            "remaining_unique_slots": remaining_slots,
            "admitted_new_unique_candidates": len(admitted_new),
            "overflow_unique_candidates": len(overflow),
            "agent_candidate_artifact": str(agent_path),
            "agent_candidate_artifact_sha256": sha256_file(agent_path),
            "overflow_exact_keys_sha256": hashlib.sha256(
                json.dumps(
                    [key for key, _ in overflow], separators=(",", ":")
                ).encode()
            ).hexdigest(),
            "cases": [
                {"exact_key": key, "case": case} for key, case in overflow
            ],
        }
        overflow_path = (
            repo_root
            / f"artifacts/raw_candidate_overflow/iteration-{iteration:04d}.json"
        )
        atomic_write_json(overflow_path, overflow_payload)
        overflow_sha256 = sha256_file(overflow_path)
    eligible_keys = prior_keys | {key for key, _ in admitted_new}
    rerank_period = max(
        1,
        int(request.get("workflow_context", {}).get("reservoir_rerank_period") or 3),
    )
    rerank_reservoir = bool(prior_reservoir) and (
        iteration % rerank_period == 0
    )
    # Fresh Agent candidates get first opportunity on ordinary iterations;
    # every Nth iteration the deferred reservoir is placed first and is
    # re-ranked together with fresh candidates. The reservoir is never
    # silently deleted: every unselected admitted case is written back.
    pool_source = (prior_reservoir + incoming) if rerank_reservoir else (incoming + prior_reservoir)
    pool_unique: list[tuple[str, dict[str, Any]]] = []
    seen_pool: set[str] = set()
    for case in pool_source:
        key = exact_key(case)
        if key not in seen_pool:
            pool_unique.append((key, case))
            seen_pool.add(key)
    unseen: list[dict[str, Any]] = []
    seen_eligible: set[str] = set()
    for key, case in pool_unique:
        if key in eligible_keys and key not in current_keys and key not in seen_eligible:
            unseen.append(case)
            seen_eligible.add(key)
    recommended = int(request["workflow_context"]["recommended_tranche_size"])
    fuse = int(request["repository"].get("pilot_raw_fuse") or 128)
    selected = select_tranche(
        unseen,
        preferred_size=recommended,
        safety_fuse=fuse,
        family_quota=max(1, int(request["repository"].get("family_quota") or 8)),
    )
    if selected.fuse_tripped:
        raise RuntimeError("candidate safety fuse requires audit; no truncation performed")
    atomic_write_json(
        reservoir_path,
        {
            "schema": "programbench_v4_candidate_reservoir_v1",
            "iteration": iteration,
            "reranked": rerank_reservoir,
            "rerank_period": rerank_period,
            "input_reservoir_cases": len(prior_reservoir),
            "input_fresh_cases": len(incoming),
            "deferred_cases": len(selected.deferred),
            "cases": selected.deferred,
        },
    )
    merged = [*current, *selected.selected]
    retained_fuse = int(
        request["workflow_context"].get("retained_suite_fuse")
        or request["repository"].get("pilot_retained_fuse")
        or 0
    )
    raw_fuse_crossed = bool(overflow)
    if raw_fuse_crossed:
        # Controller will pause before capture. Keep the last accepted suite as
        # canonical state and persist the staged rows only in the reservoir so
        # a later budget adjustment can resume from its exact checkpoint.
        atomic_write_json(
            repo_root / f"candidates/iteration-{iteration:04d}.raw_fuse_reservoir.json",
            {
                "profile": "programbench_v4",
                "reason": "raw_candidate_fuse_before_capture",
                "cases": selected.selected,
                "admitted_exact_keys": sorted(
                    exact_key(case) for case in selected.selected
                ),
            },
        )
    else:
        atomic_write_json(current_path, {"profile": "programbench_v4", "cases": merged})
    atomic_write_json(repo_root / f"candidates/tranche-{iteration:04d}.selection.json", selected.report)
    return {
        "selected_new_cases": len(selected.selected),
        "deferred_new_cases": len(selected.deferred),
        "cumulative_cases": len(merged),
        "cumulative_raw_candidates": cumulative_raw,
        "unique_persisted_raw_candidates": cumulative_raw,
        "raw_generation_attempts": generation_attempts,
        "candidate_state_committed": not raw_fuse_crossed,
        "raw_fuse_crossed": raw_fuse_crossed,
        "raw_overflow_count": len(overflow),
        "raw_overflow_path": str(overflow_path) if overflow_path else None,
        "raw_overflow_sha256": overflow_sha256,
        "reservoir_cases_before": len(prior_reservoir),
        "reservoir_cases_after": len(selected.deferred),
        "reservoir_reranked": rerank_reservoir,
        "reservoir_rerank_period": rerank_period,
        "replacement_planned": bool(retained_fuse and len(merged) > retained_fuse),
    }


def capture(request: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    repo = request["repository"]
    preflight_data = read_json(repo_root / "artifacts/preflight.json")
    cases_path = repo_root / "candidates/current.json"
    suite_label = str(repo.get("suite_label") or "v4_generated")
    cases_scope = capture_scope_id(cases_path)
    bundle = repo_root / "bundles" / repo["instance_id"] / suite_label / "oracle_tests"
    cases_payload = read_json(cases_path)
    canonical_cases, renamed = canonicalize_case_identities(
        cases_payload.get("cases") or []
    )
    if renamed:
        atomic_write_json(
            cases_path,
            {**cases_payload, "cases": canonical_cases},
        )
        atomic_write_json(
            repo_root / "artifacts/case_identity_migration.json",
            {
                "schema": "programbench_v4_case_identity_migration_v1",
                "renamed": renamed,
                "case_count": len(canonical_cases),
            },
        )
    command = [
        sys.executable,
        str(ROOT / "tools/programbench_generate_cli_oracle_bundle.py"),
        repo["instance_id"],
        "--cases-json", str(cases_path),
        "--suite-label", suite_label,
        "--image", preflight_data["cleanroom_image_id"],
        "--isolation-runtime-image", repo["runtime_image_id"],
        "--work-root", str(repo_root / "capture_work" / cases_scope),
        "--output-root", str(repo_root / "bundles"),
        "--case-timeout", "5",
        "--determinism-reruns", "1",
        "--resume-capture",
        "--overwrite",
    ]
    try:
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=1800)
    except subprocess.TimeoutExpired as exc:
        removed = cleanup_repo_containers(repo_root)
        atomic_write_json(
            repo_root / f"logs/capture-{request['iteration']:04d}.json",
            {
                "command": command,
                "returncode": None,
                "timed_out": True,
                "timeout_seconds": 1800,
                "stdout": exc.stdout or "",
                "stderr": exc.stderr or "",
                "removed_containers": removed,
            },
        )
        manifest_path = bundle / "eval/generated_cli_manifest.json"
        if not manifest_path.is_file():
            raise RuntimeError("oracle capture timed out before writing a manifest") from exc
        result = subprocess.CompletedProcess(
            command,
            0,
            exc.stdout or "",
            (exc.stderr or "") + "\ncontinued from completed capture manifest after timeout cleanup",
        )
    atomic_write_json(
        repo_root / f"logs/capture-{request['iteration']:04d}.json",
        {"command": command, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr},
    )
    if result.returncode != 0:
        raise RuntimeError(f"oracle capture failed: {result.stderr[-3000:]}")
    manifest = read_json(bundle / "eval/generated_cli_manifest.json")
    candidate_names = {
        str(case.get("name") or "") for case in read_json(cases_path).get("cases") or []
    }
    manifest_names = {str(case.get("name") or "") for case in manifest.get("cases") or []}
    if not manifest_names.issubset(candidate_names):
        unexpected = sorted(manifest_names - candidate_names)
        raise RuntimeError(
            "oracle capture changed stable case identity: " + ", ".join(unexpected[:8])
        )
    atomic_write_json(
        repo_root / "artifacts/current_bundle.json",
        {
            "bundle_root": str(bundle),
            "captured_cases": len(manifest.get("cases") or []),
            "cases_scope_sha256": sha256_file(cases_path),
            "capture_work_root": str(repo_root / "capture_work" / cases_scope),
        },
    )
    reference = Path(preflight_data["reference_binary"])
    return {
        "bundle_root": str(bundle),
        "captured_cases": len(manifest.get("cases") or []),
        "cases_scope_sha256": sha256_file(cases_path),
        "execution_provenance": [provenance("oracle_capture", repo["runtime_image_id"], reference)],
    }


def _coverage_profile(request: dict[str, Any], repo_root: Path, *, full: bool) -> dict[str, Any]:
    repo = request["repository"]
    if str(repo.get("language")) == "rust":
        return _rust_coverage_profile(request, repo_root, full=full)
    preflight_data = read_json(repo_root / "artifacts/preflight.json")
    bundle = Path(read_json(repo_root / "artifacts/current_bundle.json")["bundle_root"])
    manifest = read_json(bundle / "eval/generated_cli_manifest.json")
    cases = manifest.get("cases") or []
    cov_root = repo_root / "coverage" / ("full" if full else f"quick-{int(request['iteration']):04d}")
    if cov_root.exists():
        shutil.rmtree(cov_root)
    data = cov_root / "data"
    out = cov_root / "out"
    data.mkdir(parents=True)
    out.mkdir(parents=True)
    coverage_binary = Path(preflight_data["coverage_binary"])
    # Docker cannot create a child bind mount below a read-only parent bind.
    # Materialize a stage-private immutable execution bundle instead: the
    # generated tests and the exact instrumented executable are then exposed
    # together through one read-only /oracle mount.
    execution_bundle = cov_root / "oracle"
    shutil.copytree(bundle, execution_bundle)
    shutil.copy2(coverage_binary, execution_bundle / "executable")
    (execution_bundle / "executable").chmod(0o500)
    # The pilot suite is bounded by a small safety fuse.  Run the complete
    # retained union for marginal measurements so consecutive observations are
    # comparable and coverage is monotonic under pure suite growth.  The old
    # five-index sample changed whenever suite length changed, so a reported
    # gain/regression was often just sample rotation rather than test value.
    selector = ""
    script = (
        "set -eu; export GOCOVERDIR=/cov PYTHONDONTWRITEBYTECODE=1 HOME=/workspace/home TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8; "
        "mkdir -p /workspace/home; cd /oracle; python3 -m pytest -q -p no:cacheprovider"
        + selector
        + " eval/tests/test_generated_cli_oracle.py"
    )
    stage = "full_coverage" if full else "quick_coverage"
    pytest_rc = 0
    try:
        run_container(
            image=repo["runtime_image_id"],
            stage=stage,
            mounts=(
                Mount(execution_bundle, "/oracle"),
                Mount(data, "/cov", readonly=False),
            ),
            script=script,
            log=repo_root / f"logs/{stage}-{request['iteration']:04d}.json",
            timeout=900,
        )
    except RuntimeError:
        pytest_log = repo_root / f"logs/{stage}-{request['iteration']:04d}.json"
        if pytest_log.is_file():
            pytest_rc = int(read_json(pytest_log).get("returncode") or 1)
        else:
            pytest_rc = 1
    try:
        run_container(
            image=repo["runtime_image_id"],
            stage=stage,
            mounts=(
                Mount(Path(repo["go_toolchain_root"]), "/go"),
                Mount(data, "/cov"),
                Mount(out, "/out", readonly=False),
            ),
            script=(
                "set -eu; mkdir -p /workspace/gotmp /workspace/gocache; "
                "export PATH=/go/bin:/usr/bin:/bin GOROOT=/go "
                "GOTMPDIR=/workspace/gotmp GOCACHE=/workspace/gocache; "
                "go tool covdata textfmt -i=/cov -o=/out/profile.txt"
            ),
            log=repo_root / f"logs/{stage}-textfmt-{request['iteration']:04d}.json",
            timeout=120,
        )
    except RuntimeError:
        (out / "profile.txt").write_text("mode: atomic\n", encoding="utf-8")
    units: set[str] = set()
    covered_statements = 0
    total_statements = 0
    for line in (out / "profile.txt").read_text(encoding="utf-8").splitlines()[1:]:
        match = re.match(r"(.+?)\s+(\d+)\s+(\d+)$", line)
        if not match:
            continue
        statements, count = int(match.group(2)), int(match.group(3))
        total_statements += statements
        if count:
            covered_statements += statements
            units.add(match.group(1))
    percent = 100.0 * covered_statements / max(1, total_statements)
    result = {
        "primary_coverage": percent,
        "covered_statements": covered_statements,
        "total_statements": total_statements,
        "covered_units": sorted(units),
        "profile": str(out / "profile.txt"),
        "sampled_cases": len(cases),
        "sample_policy": "complete_retained_suite",
        "coverage_valid": total_statements > 0,
        "coverage_pytest_returncode": pytest_rc,
    }
    atomic_write_json(cov_root / "coverage.json", result)
    if not full:
        atomic_write_json(repo_root / "artifacts/latest_quick_coverage.json", result)
    else:
        atomic_write_json(repo_root / "artifacts/final_full_coverage.json", result)
    return {
        "coverage": result,
        "execution_provenance": [provenance(stage, repo["runtime_image_id"], coverage_binary)],
    }


def _rust_llvm_paths(repo: dict[str, Any]) -> tuple[str, str]:
    triple = str(repo.get("rust_target_triple") or "x86_64-unknown-linux-gnu")
    prefix = f"/rust/lib/rustlib/{triple}/bin"
    return f"{prefix}/llvm-profdata", f"{prefix}/llvm-cov"


RUST_FIRST_PARTY_COVERAGE_SCHEMA = "programbench_v4_rust_first_party_regions_v1"


def _rust_export_summary(
    export: dict[str, Any], *, source_root: str = "/workspace/src"
) -> tuple[int, int, set[str]]:
    """Return first-party region coverage and stable relative exact units.

    Rust instrumented binaries also expose stdlib and Cargo dependency regions.
    Those are neither ProgramBench target source nor deterministic witnesses,
    so both the scalar denominator and exact-unit set must use the same strict
    repository source root.
    """

    data = export.get("data") or []
    if not data:
        raise RuntimeError("llvm-cov export contains no data")
    normalized_root = "/" + source_root.strip("/")
    prefix = normalized_root + "/"
    count = 0
    covered = 0
    units: set[str] = set()
    first_party_files = 0
    for file_row in data[0].get("files") or []:
        filename = str(file_row.get("filename") or "")
        if not filename.startswith(prefix):
            continue
        relative = filename[len(prefix) :]
        parts = Path(relative).parts
        if (
            not relative
            or relative.startswith("/")
            or ".." in parts
            or "target" in parts
            or ".git" in parts
            or not relative.endswith(".rs")
        ):
            continue
        summary = (file_row.get("summary") or {}).get("regions") or {}
        file_count = int(summary.get("count") or 0)
        file_covered = int(summary.get("covered") or 0)
        if file_count < 0 or file_covered < 0 or file_covered > file_count:
            raise RuntimeError("invalid first-party Rust region summary")
        first_party_files += 1
        count += file_count
        covered += file_covered
        for segment in file_row.get("segments") or []:
            if len(segment) < 6:
                continue
            line, column, execution_count, has_count, is_region_entry = segment[:5]
            if bool(has_count) and bool(is_region_entry) and int(execution_count) > 0:
                units.add(f"{relative}:{int(line)}:{int(column)}")
    if first_party_files == 0 or count <= 0:
        raise RuntimeError(
            f"llvm-cov export has no first-party Rust regions under {normalized_root}"
        )
    return covered, count, units


def _rust_coverage_profile(
    request: dict[str, Any], repo_root: Path, *, full: bool
) -> dict[str, Any]:
    repo = request["repository"]
    preflight_data = read_json(repo_root / "artifacts/preflight.json")
    bundle = Path(read_json(repo_root / "artifacts/current_bundle.json")["bundle_root"])
    cases = read_json(bundle / "eval/generated_cli_manifest.json").get("cases") or []
    cov_root = repo_root / "coverage" / ("full" if full else f"quick-{int(request['iteration']):04d}")
    if cov_root.exists():
        shutil.rmtree(cov_root)
    data, out = cov_root / "data", cov_root / "out"
    data.mkdir(parents=True)
    out.mkdir(parents=True)
    execution_bundle = cov_root / "oracle"
    shutil.copytree(bundle, execution_bundle)
    coverage_binary = Path(preflight_data["coverage_binary"])
    shutil.copy2(coverage_binary, execution_bundle / "executable")
    (execution_bundle / "executable").chmod(0o500)
    stage = "full_coverage" if full else "quick_coverage"
    pytest_rc = 0
    try:
        run_container(
            image=repo["runtime_image_id"],
            stage=stage,
            mounts=(Mount(execution_bundle, "/oracle"), Mount(data, "/cov", readonly=False)),
            script=(
                "set -eu; export LLVM_PROFILE_FILE=/cov/default_%m_%p.profraw "
                "PYTHONDONTWRITEBYTECODE=1 HOME=/workspace/home TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8; "
                "mkdir -p /workspace/home; cd /oracle; "
                "python3 -m pytest -q -p no:cacheprovider eval/tests/test_generated_cli_oracle.py"
            ),
            log=repo_root / f"logs/{stage}-{request['iteration']:04d}.json",
            timeout=1200,
        )
    except RuntimeError:
        pytest_log = repo_root / f"logs/{stage}-{request['iteration']:04d}.json"
        pytest_rc = int(read_json(pytest_log).get("returncode") or 1) if pytest_log.is_file() else 1
    profdata, llvm_cov = _rust_llvm_paths(repo)
    try:
        run_container(
            image=repo["runtime_image_id"],
            stage=stage,
            mounts=(
                Mount(Path(repo["rust_toolchain_root"]), "/rust"),
                Mount(data, "/cov"),
                Mount(out, "/out", readonly=False),
                Mount(coverage_binary, "/binary"),
            ),
            script=(
                f"set -eu; if ! ls /cov/*.profraw >/dev/null 2>&1; then "
                "printf '{\"data\":[]}' > /out/export.json; exit 0; fi; "
                f"{profdata} merge -sparse /cov/*.profraw -o /out/merged.profdata; "
                f"{llvm_cov} export -instr-profile=/out/merged.profdata /binary > /out/export.json"
            ),
            log=repo_root / f"logs/{stage}-llvm-{request['iteration']:04d}.json",
            timeout=300,
        )
    except RuntimeError:
        atomic_write_json(out / "export.json", {"data": []})
    export = read_json(out / "export.json")
    covered, total, units = _rust_export_summary(export)
    result = {
        "primary_coverage": 100.0 * covered / max(1, total),
        "covered_regions": covered,
        "total_regions": total,
        "covered_units": sorted(units),
        "profile": str(out / "export.json"),
        "sampled_cases": len(cases),
        "sample_policy": "complete_retained_suite",
        "coverage_valid": total > 0,
        "coverage_pytest_returncode": pytest_rc,
        "coverage_filter_schema": RUST_FIRST_PARTY_COVERAGE_SCHEMA,
        "source_scope": "/workspace/src",
    }
    atomic_write_json(cov_root / "coverage.json", result)
    atomic_write_json(
        repo_root / ("artifacts/final_full_coverage.json" if full else "artifacts/latest_quick_coverage.json"),
        result,
    )
    return {
        "coverage": result,
        "execution_provenance": [provenance(stage, repo["runtime_image_id"], coverage_binary)],
    }


def _profile_units(profile: Path) -> set[str]:
    units: set[str] = set()
    for line in profile.read_text(encoding="utf-8").splitlines()[1:]:
        match = re.match(r"(.+?)\s+(\d+)\s+(\d+)$", line)
        if match and int(match.group(3)):
            units.add(match.group(1))
    return units


def _case_static_witnesses(
    captured: dict[str, Any], candidate: dict[str, Any]
) -> set[str]:
    # A behavior witness represents a reusable behavioral *family*, not the
    # identity of one golden output.  Hashing stdout/stderr bytes made every
    # formatting fixture a sole witness provider and therefore made safe suite
    # replacement mathematically impossible.  Exact output is still retained
    # by the selected oracle case; the family describes the externally visible
    # mode that must remain represented while exact source blocks are tracked
    # separately by coverage witnesses.
    args = [str(value) for value in (candidate.get("args") or [])]
    option_families: set[str] = set()
    positional_count = 0
    for arg in args:
        if arg.startswith("-"):
            option_families.add(arg.split("=", 1)[0])
        else:
            positional_count += 1
    returncode = int(captured.get("returncode") or 0)
    behavior = {
        "result_class": "success" if returncode == 0 else "error",
        "option_families": sorted(option_families),
        "input_mode": "stdin" if candidate.get("stdin") is not None else "files",
        "positional_arity": min(positional_count, 3),
        "stdout": bool(int(captured.get("stdout_bytes") or 0)),
        "stderr": bool(int(captured.get("stderr_bytes") or 0)),
        "observed_file_suffixes": sorted(
            {Path(str(name)).suffix for name in (captured.get("observed_files") or {})}
        ),
        "sequence": bool(candidate.get("sequence") or captured.get("interactions")),
    }
    behavior_hash = hashlib.sha256(
        json.dumps(behavior, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    witnesses = {f"behavior:{behavior_hash}", "assertion:returncode"}
    if int(captured.get("stdout_bytes") or 0):
        witnesses.add("assertion:stdout_exact")
    if int(captured.get("stderr_bytes") or 0):
        witnesses.add("assertion:stderr_exact")
    if captured.get("observed_files"):
        witnesses.add("assertion:filesystem_content")
    if captured.get("interactions"):
        witnesses.add("assertion:interaction_sequence")
    if returncode:
        witnesses.add(f"error:returncode:{returncode}")
    fixture_shape = {
        "stdin": bool(candidate.get("stdin")),
        "args": len(candidate.get("args") or []),
        "files": sorted(Path(name).suffix for name in (candidate.get("files") or {})),
        "observe_files": len(candidate.get("observe_files") or []),
        "terminal": bool(candidate.get("terminal")),
        "sequence": bool(candidate.get("sequence")),
    }
    fixture_hash = hashlib.sha256(
        json.dumps(fixture_shape, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    witnesses.add(f"fixture:{fixture_hash}")
    if candidate.get("sequence") or captured.get("interactions") or captured.get("lifecycle"):
        state_hash = hashlib.sha256(
            json.dumps(
                {
                    "sequence": candidate.get("sequence") or [],
                    "interactions": captured.get("interactions") or [],
                    "lifecycle": captured.get("lifecycle") or {},
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        witnesses.add(f"state:{state_hash}")
    return witnesses


def collect_exact_case_witnesses(
    request: dict[str, Any], repo_root: Path
) -> tuple[dict[str, list[str]], dict[str, Any]]:
    repo = request["repository"]
    if str(repo.get("language")) == "rust":
        return collect_exact_case_witnesses_rust(request, repo_root)
    preflight_data = read_json(repo_root / "artifacts/preflight.json")
    bundle_info = read_json(repo_root / "artifacts/current_bundle.json")
    bundle = Path(bundle_info["bundle_root"])
    manifest_cases = read_json(bundle / "eval/generated_cli_manifest.json").get("cases") or []
    current = read_json(repo_root / "candidates/current.json").get("cases") or []
    current_by_name = {str(case.get("name") or ""): case for case in current}
    captured_names = [str(case.get("name") or "") for case in manifest_cases]
    if not captured_names or any(not name for name in captured_names):
        raise RuntimeError("captured manifest has missing case names")
    if len(captured_names) != len(set(captured_names)):
        raise RuntimeError("captured case names are not unique")
    if any(name not in current_by_name for name in captured_names):
        raise RuntimeError("captured manifest is not contained in candidate suite")

    root = repo_root / "witnesses" / f"iteration-{int(request['iteration']):04d}"
    if root.exists():
        shutil.rmtree(root)
    data = root / "casecov"
    profiles = root / "profiles"
    execution_bundle = root / "oracle"
    data.mkdir(parents=True)
    profiles.mkdir(parents=True)
    shutil.copytree(bundle, execution_bundle)
    coverage_binary = Path(preflight_data["coverage_binary"])
    shutil.copy2(coverage_binary, execution_bundle / "executable")
    (execution_bundle / "executable").chmod(0o500)

    test_file = execution_bundle / "eval/tests/test_generated_cli_oracle.py"
    tree = ast.parse(test_file.read_text(encoding="utf-8"))
    tests: dict[int, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            match = re.match(r"test_(\d{4})_", node.name)
            if match:
                tests[int(match.group(1))] = node.name
    if set(tests) != set(range(len(manifest_cases))):
        raise RuntimeError("generated pytest functions do not match captured manifest")

    pytest_lines = [
        "set -eu",
        "export PYTHONDONTWRITEBYTECODE=1 TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8",
    ]
    for index in range(len(manifest_cases)):
        (data / f"{index:04d}").mkdir()
        node_id = f"/oracle/eval/tests/test_generated_cli_oracle.py::{tests[index]}"
        pytest_lines.append(
            f"GOCOVERDIR=/casecov/{index:04d} python3 -m pytest -q -p no:cacheprovider {shlex.quote(node_id)}"
        )
    run_container(
        image=repo["runtime_image_id"],
        stage="quick_coverage",
        mounts=(
            Mount(execution_bundle, "/oracle"),
            Mount(data, "/casecov", readonly=False),
        ),
        script="; ".join(pytest_lines),
        log=repo_root / f"logs/case-witness-pytest-{request['iteration']:04d}.json",
        timeout=1200,
    )

    cov_lines = [
        "set -eu",
        "mkdir -p /workspace/gotmp /workspace/gocache",
        "export PATH=/go/bin:/usr/bin:/bin GOROOT=/go GOTMPDIR=/workspace/gotmp GOCACHE=/workspace/gocache",
    ]
    for index in range(len(manifest_cases)):
        cov_lines.append(
            f"go tool covdata textfmt -i=/casecov/{index:04d} -o=/profiles/{index:04d}.txt"
        )
    run_container(
        image=repo["runtime_image_id"],
        stage="quick_coverage",
        mounts=(
            Mount(Path(repo["go_toolchain_root"]), "/go"),
            Mount(data, "/casecov"),
            Mount(profiles, "/profiles", readonly=False),
        ),
        script="; ".join(cov_lines),
        log=repo_root / f"logs/case-witness-textfmt-{request['iteration']:04d}.json",
        timeout=600,
    )

    witness_map: dict[str, list[str]] = {}
    for index, captured in enumerate(manifest_cases):
        name = captured_names[index]
        witnesses = _case_static_witnesses(captured, current_by_name[name])
        witnesses.update(f"coverage:{unit}" for unit in _profile_units(profiles / f"{index:04d}.txt"))
        witness_map[name] = sorted(witnesses)
    payload = {
        "schema": "programbench_v4_case_witnesses_v1",
        "cases_scope_sha256": bundle_info.get("cases_scope_sha256"),
        "coverage_binary_sha256": sha256_file(coverage_binary),
        "case_count": len(witness_map),
        "witness_map": witness_map,
    }
    atomic_write_json(root / "case_witnesses.json", payload)
    return witness_map, payload


def collect_exact_case_witnesses_rust(
    request: dict[str, Any], repo_root: Path
) -> tuple[dict[str, list[str]], dict[str, Any]]:
    repo = request["repository"]
    preflight_data = read_json(repo_root / "artifacts/preflight.json")
    bundle_info = read_json(repo_root / "artifacts/current_bundle.json")
    bundle = Path(bundle_info["bundle_root"])
    manifest_cases = read_json(bundle / "eval/generated_cli_manifest.json").get("cases") or []
    current = read_json(repo_root / "candidates/current.json").get("cases") or []
    current_by_name = {str(case.get("name") or ""): case for case in current}
    captured_names = [str(case.get("name") or "") for case in manifest_cases]
    if not captured_names or len(captured_names) != len(set(captured_names)):
        raise RuntimeError("captured Rust manifest has invalid case names")
    if any(name not in current_by_name for name in captured_names):
        raise RuntimeError("captured Rust manifest is not contained in candidate suite")

    root = repo_root / "witnesses" / f"iteration-{int(request['iteration']):04d}"
    if root.exists():
        shutil.rmtree(root)
    data, exports, execution_bundle = root / "casecov", root / "exports", root / "oracle"
    data.mkdir(parents=True)
    exports.mkdir(parents=True)
    shutil.copytree(bundle, execution_bundle)
    coverage_binary = Path(preflight_data["coverage_binary"])
    shutil.copy2(coverage_binary, execution_bundle / "executable")
    (execution_bundle / "executable").chmod(0o500)
    tree = ast.parse((execution_bundle / "eval/tests/test_generated_cli_oracle.py").read_text(encoding="utf-8"))
    tests: dict[int, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            match = re.match(r"test_(\d{4})_", node.name)
            if match:
                tests[int(match.group(1))] = node.name
    if set(tests) != set(range(len(manifest_cases))):
        raise RuntimeError("generated pytest functions do not match captured Rust manifest")

    pytest_lines = ["set -eu", "export PYTHONDONTWRITEBYTECODE=1 TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8"]
    for index in range(len(manifest_cases)):
        (data / f"{index:04d}").mkdir()
        node_id = f"/oracle/eval/tests/test_generated_cli_oracle.py::{tests[index]}"
        pytest_lines.append(
            f"LLVM_PROFILE_FILE=/casecov/{index:04d}/default_%m_%p.profraw "
            f"python3 -m pytest -q -p no:cacheprovider {shlex.quote(node_id)}"
        )
    run_container(
        image=repo["runtime_image_id"],
        stage="quick_coverage",
        mounts=(Mount(execution_bundle, "/oracle"), Mount(data, "/casecov", readonly=False)),
        script="; ".join(pytest_lines),
        log=repo_root / f"logs/case-witness-pytest-{request['iteration']:04d}.json",
        timeout=1800,
    )
    profdata, llvm_cov = _rust_llvm_paths(repo)
    export_lines = ["set -eu"]
    for index in range(len(manifest_cases)):
        export_lines.append(
            f"{profdata} merge -sparse /casecov/{index:04d}/*.profraw -o /exports/{index:04d}.profdata"
        )
        export_lines.append(
            f"{llvm_cov} export -instr-profile=/exports/{index:04d}.profdata /binary > /exports/{index:04d}.json"
        )
    run_container(
        image=repo["runtime_image_id"],
        stage="quick_coverage",
        mounts=(
            Mount(Path(repo["rust_toolchain_root"]), "/rust"),
            Mount(data, "/casecov"),
            Mount(exports, "/exports", readonly=False),
            Mount(coverage_binary, "/binary"),
        ),
        script="; ".join(export_lines),
        log=repo_root / f"logs/case-witness-llvm-{request['iteration']:04d}.json",
        timeout=1800,
    )
    witness_map: dict[str, list[str]] = {}
    for index, captured in enumerate(manifest_cases):
        name = captured_names[index]
        witnesses = _case_static_witnesses(captured, current_by_name[name])
        _, _, units = _rust_export_summary(read_json(exports / f"{index:04d}.json"))
        witnesses.update(f"coverage:{unit}" for unit in units)
        witness_map[name] = sorted(witnesses)
    payload = {
        "schema": "programbench_v4_case_witnesses_v1",
        "cases_scope_sha256": bundle_info.get("cases_scope_sha256"),
        "coverage_binary_sha256": sha256_file(coverage_binary),
        "case_count": len(witness_map),
        "witness_map": witness_map,
    }
    atomic_write_json(root / "case_witnesses.json", payload)
    return witness_map, payload


def replace_suite_preserving_witnesses(
    request: dict[str, Any], repo_root: Path, *, cap: int
) -> dict[str, Any]:
    current_path = repo_root / "candidates/current.json"
    current = read_json(current_path).get("cases") or []
    witness_map, witness_payload = collect_exact_case_witnesses(request, repo_root)
    captured_names = list(witness_map)
    rows = validate_witness_map(captured_names, witness_map)
    context = request.get("workflow_context") or {}
    adaptive = bool(context.get("adaptive_retained_fuse"))
    capacity = None
    if adaptive:
        capacity = adaptive_witness_capacity(
            rows,
            base_cap=cap,
            hard_cap=int(context.get("retained_suite_hard_fuse") or 0),
            headroom_ratio=float(context.get("retained_fuse_headroom_ratio") or 0.0),
            minimum_growth=int(context.get("retained_fuse_minimum_growth") or 1),
            minimum_cases=min(cap, 32),
        )
        result = capacity.replacement
        effective_cap = capacity.effective_cap
    else:
        result = witness_preserving_replacement(
            rows,
            cap=cap,
            minimum_cases=min(cap, 32),
        )
        effective_cap = cap
    report = {
        "schema": "programbench_v4_witness_replacement_v1",
        "cap": effective_cap,
        "base_cap": cap,
        "adaptive_capacity": (
            {
                "hard_cap": capacity.hard_cap,
                "effective_cap": capacity.effective_cap,
                "minimum_required_cap": capacity.minimum_required_cap,
                "witness_count": capacity.witness_count,
                "candidate_count": capacity.candidate_count,
                "mandatory_case_count": capacity.mandatory_case_count,
                "mean_witnesses_per_case": capacity.mean_witnesses_per_case,
                "witness_density": capacity.witness_density,
                "attempted_caps": list(capacity.attempted_caps),
                "expanded": capacity.expanded,
                "blocked_reason": capacity.blocked_reason,
            }
            if capacity is not None
            else None
        ),
        "input_candidate_count": len(current),
        "captured_case_count": len(captured_names),
        "selected_case_ids": list(result.selected_case_ids),
        "rejected_case_ids": list(result.rejected_case_ids),
        "mandatory_case_ids": list(result.mandatory_case_ids),
        "universe_witness_count": len(result.universe),
        "covered_witness_count": len(result.covered),
        "preserves_all_witnesses": result.preserves_all_witnesses,
        "blocked_reason": capacity.blocked_reason if capacity is not None else result.blocked_reason,
        "witness_scope": {
            "cases_scope_sha256": witness_payload.get("cases_scope_sha256"),
            "coverage_binary_sha256": witness_payload.get("coverage_binary_sha256"),
        },
    }
    report_path = repo_root / f"witnesses/iteration-{int(request['iteration']):04d}/replacement.json"
    atomic_write_json(report_path, report)
    if not result.preserves_all_witnesses:
        return report
    selected = set(result.selected_case_ids)
    selected_cases = [case for case in current if str(case.get("name") or "") in selected]
    rejected_cases = [case for case in current if str(case.get("name") or "") not in selected]
    if len(selected_cases) != len(result.selected_case_ids):
        raise RuntimeError("replacement selection did not map exactly to candidates")
    atomic_write_json(current_path, {"profile": "programbench_v4", "cases": selected_cases})
    atomic_write_json(
        repo_root / f"candidates/iteration-{int(request['iteration']):04d}.replacement_reservoir.json",
        {"profile": "programbench_v4", "reason": "dominated_or_uncaptured", "cases": rejected_cases},
    )
    return report


def dummy_failure_indexes(report: dict[str, Any]) -> list[int]:
    indexes: set[int] = set()
    for name in report.get("dummy_passing_test_names") or []:
        match = re.search(r"\.test_(\d{4})_", str(name))
        if match:
            indexes.add(int(match.group(1)))
    return sorted(indexes)


def dummy_failure_case_names(
    report: dict[str, Any], manifest_cases: list[dict[str, Any]]
) -> list[str]:
    names: set[str] = set()
    for index in dummy_failure_indexes(report):
        if index >= len(manifest_cases):
            raise RuntimeError("dummy failure index is outside captured manifest")
        name = str(manifest_cases[index].get("name") or "")
        if not name:
            raise RuntimeError("captured dummy failure has no stable case name")
        names.add(name)
    return sorted(names)


def quality_subprocess_timeout_seconds(
    repo: dict[str, Any], *, captured_case_count: int
) -> int:
    """Choose a bounded outer quality-process timeout.

    The quality command runs several whole-suite pytest passes, so its wall
    time scales with retained cases even though each individual pytest pass
    keeps its own ``--timeout`` policy.  Explicit repository configuration is
    authoritative inside the controller's practical 7200-second stage bound;
    otherwise small suites receive 3600 seconds and larger suites grow toward
    a 6600-second ceiling that leaves controller cleanup margin.
    """

    configured = repo.get("quality_timeout_seconds")
    if configured is not None:
        value = int(configured)
        if value < 60 or value > 7200:
            raise ValueError("quality_timeout_seconds must be in 60..7200")
        return value
    cases = max(0, int(captured_case_count))
    return min(6600, max(3600, 900 + cases * 10))


def quality_pytest_pass_timeout_seconds(*, captured_case_count: int) -> int:
    """Scale one complete-suite pytest pass without permitting an unbounded run."""

    cases = max(0, int(captured_case_count))
    return min(1800, max(900, 300 + cases * 2))


def quality_container_cpus(repo: dict[str, Any], *, captured_case_count: int) -> int:
    """Allocate enough CPU for concurrent full-suite quality gates."""

    configured = repo.get("quality_container_cpus")
    if configured is not None:
        value = int(configured)
        if value < 1 or value > 16:
            raise ValueError("quality_container_cpus must be in 1..16")
        return value
    return 8 if int(captured_case_count) >= 500 else 4


def quality(request: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    repo = request["repository"]
    preflight_data = read_json(repo_root / "artifacts/preflight.json")
    bundle = Path(read_json(repo_root / "artifacts/current_bundle.json")["bundle_root"])
    manifest_cases = read_json(bundle / "eval/generated_cli_manifest.json").get("cases") or []
    quality_timeout = quality_subprocess_timeout_seconds(
        repo, captured_case_count=len(manifest_cases)
    )
    pytest_pass_timeout = quality_pytest_pass_timeout_seconds(
        captured_case_count=len(manifest_cases)
    )
    container_cpus = quality_container_cpus(
        repo, captured_case_count=len(manifest_cases)
    )
    output = repo_root / "quality" / f"iteration-{int(request['iteration']):04d}.json"
    command = [
        sys.executable,
        str(ROOT / "tools/programbench_run_generated_oracle_quality_gates.py"),
        "--oracle-material-root", str(bundle),
        "--output-json", str(output),
        "--work-root", str(repo_root / "quality_work"),
        "--repeat-executable", preflight_data["reference_binary"],
        "--execution-runtime-image", repo["runtime_image_id"],
        "--container-python", "/usr/bin/python3",
        "--container-cpus", str(container_cpus),
        "--timeout", str(pytest_pass_timeout),
        "--overwrite",
    ]
    def run_quality(label: str) -> tuple[subprocess.CompletedProcess[str], dict[str, Any] | None]:
        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=quality_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            removed = cleanup_repo_containers(repo_root)
            stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            result = subprocess.CompletedProcess(
                command,
                124,
                stdout,
                stderr + f"\nquality gates timed out after {quality_timeout}s",
            )
            atomic_write_json(
                repo_root / f"logs/quality-{request['iteration']:04d}-{label}.json",
                {
                    "command": command,
                    "returncode": None,
                    "timed_out": True,
                    "timeout_seconds": quality_timeout,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "removed_containers": removed,
                },
            )
            return result, read_json(output) if output.is_file() else None
        atomic_write_json(
            repo_root / f"logs/quality-{request['iteration']:04d}-{label}.json",
            {
                "command": command,
                "returncode": result.returncode,
                "timed_out": False,
                "timeout_seconds": quality_timeout,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )
        return result, read_json(output) if output.is_file() else None

    result, report = run_quality("initial")
    repair_cycles: list[dict[str, Any]] = []
    for cycle in range(1, 5):
        if result.returncode == 0 or report is None:
            break
        manifest_cases = read_json(
            bundle / "eval/generated_cli_manifest.json"
        ).get("cases") or []
        dummy_names = dummy_failure_case_names(report, manifest_cases)
        repeat_names = list(repeat_failure_case_names(report, manifest_cases))
        repaired_names = sorted(set(dummy_names) | set(repeat_names))
        repairable = (
            bool(repaired_names)
            and report.get("all_target_executions_isolated") is True
            and (report.get("source_leak_scan") or {}).get("passed") is True
            and (report.get("assertion_lint") or {}).get("passed") is True
        )
        if not repairable:
            break
        current_path = repo_root / "candidates/current.json"
        current = read_json(current_path).get("cases") or []
        rejected = set(repaired_names)
        retained = [case for case in current if str(case.get("name") or "") not in rejected]
        if len(retained) >= len(current):
            raise RuntimeError("dummy failures did not map back to retained candidates")
        atomic_write_json(current_path, {"profile": "programbench_v4", "cases": retained})
        repair_cycles.append(
            {
                "cycle": cycle,
                "reason": (
                    "dummy_acceptance_and_repeat_nondeterminism"
                    if dummy_names and repeat_names
                    else "dummy_acceptance"
                    if dummy_names
                    else "repeat_nondeterminism"
                ),
                "removed_case_names": repaired_names,
                "removed_dummy_case_names": dummy_names,
                "removed_repeat_failure_case_names": repeat_names,
                "before": len(current),
                "after": len(retained),
            }
        )
        atomic_write_json(
            repo_root / f"quality/iteration-{int(request['iteration']):04d}.repair.json",
            {"maximum_cycles": 4, "cycles": repair_cycles},
        )
        capture(request, repo_root)
        _coverage_profile(request, repo_root, full=False)
        result, report = run_quality(f"after-quality-repair-{cycle}")
    if result.returncode != 0 and report is not None:
        isolated_quality_failure = (
            report.get("all_target_executions_isolated") is True
            and (report.get("source_leak_scan") or {}).get("passed") is True
            and (report.get("assertion_lint") or {}).get("passed") is True
        )
        if isolated_quality_failure:
            target_timed_out = any(
                bool(row.get("timed_out"))
                for row in [
                    *(report.get("dummy_reject") or []),
                    *([report.get("repeat_check")] if report.get("repeat_check") else []),
                ]
                if isinstance(row, dict)
            )
            return {
                "quality_report": str(output),
                "quality_unrepairable": True,
                "quality_block_reason": (
                    "quality_execution_timeout"
                    if target_timed_out
                    else "quality_case_rejection_did_not_converge"
                ),
                "pytest_pass_timeout_seconds": pytest_pass_timeout,
                "dummy_repair_cycles": repair_cycles,
                "quality_repair_cycles": repair_cycles,
                "execution_provenance": [
                    provenance(
                        "freeze_verification"
                        if request["stage"] == "freeze_verification"
                        else "quality_gates",
                        repo["runtime_image_id"],
                        Path(preflight_data["reference_binary"]),
                    )
                ],
            }
    if report is None:
        raise RuntimeError(f"quality gates failed: {result.stderr[-3000:]}")
    if result.returncode != 0:
        return {
            "quality_report": str(output),
            "quality_unrepairable": True,
            "quality_block_reason": "unclassified_quality_failure",
            "dummy_repair_cycles": repair_cycles,
            "quality_repair_cycles": repair_cycles,
            "execution_provenance": [
                provenance(
                    "freeze_verification"
                    if request["stage"] == "freeze_verification"
                    else "quality_gates",
                    repo["runtime_image_id"],
                    Path(preflight_data["reference_binary"]),
                )
            ],
        }
    replacement_report: dict[str, Any] | None = None
    retained_fuse = int(
        (request.get("workflow_context") or {}).get("retained_suite_fuse")
        or repo.get("pilot_retained_fuse")
        or 0
    )
    current_count = len(read_json(repo_root / "candidates/current.json").get("cases") or [])
    if retained_fuse and current_count > retained_fuse:
        replacement_report = replace_suite_preserving_witnesses(
            request, repo_root, cap=retained_fuse
        )
        if not replacement_report.get("preserves_all_witnesses"):
            prior_path = repo_root / f"candidates/pre-iteration-{int(request['iteration']):04d}.json"
            if not prior_path.is_file():
                raise RuntimeError("suite replacement failed without a rollback snapshot")
            prior_cases = read_json(prior_path).get("cases") or []
            if not prior_cases:
                raise RuntimeError("suite replacement rollback snapshot is empty")
            atomic_write_json(
                repo_root / "candidates/current.json",
                {"profile": "programbench_v4", "cases": prior_cases},
            )
            # Restore every downstream artifact to the last accepted suite so
            # a safety pause never leaves a staged/oversized suite looking
            # current.  Final verification may now safely run on this rollback.
            capture(request, repo_root)
            _coverage_profile(request, repo_root, full=False)
            return {
                "quality_report": str(output),
                "quality_unrepairable": True,
                "suite_fuse_blocked": True,
                "quality_block_reason": replacement_report.get("blocked_reason"),
                "rolled_back_retained_cases": len(prior_cases),
                "witness_replacement": replacement_report,
                "execution_provenance": [
                    provenance("quality_gates", repo["runtime_image_id"], Path(preflight_data["reference_binary"]))
                ],
            }
        capture(request, repo_root)
        _coverage_profile(request, repo_root, full=False)
        result, report = run_quality("after-witness-replacement")
        if result.returncode != 0 or report is None:
            return {
                "quality_report": str(output),
                "quality_unrepairable": True,
                "quality_block_reason": "post_replacement_quality_regression",
                "witness_replacement": replacement_report,
                "execution_provenance": [
                    provenance("quality_gates", repo["runtime_image_id"], Path(preflight_data["reference_binary"]))
                ],
            }
    if not report.get("all_target_executions_isolated"):
        raise RuntimeError("quality report did not prove isolated target execution")
    return {
        "quality_report": str(output),
        "dummy_repair_cycles": repair_cycles,
        "quality_repair_cycles": repair_cycles,
        "removed_dummy_accepting_cases": sum(
            len(item["removed_dummy_case_names"]) for item in repair_cycles
        ),
        "removed_repeat_nondeterministic_cases": sum(
            len(item["removed_repeat_failure_case_names"]) for item in repair_cycles
        ),
        "witness_replacement": replacement_report,
        "execution_provenance": [
            provenance(
                "freeze_verification" if request["stage"] == "freeze_verification" else "quality_gates",
                repo["runtime_image_id"],
                Path(preflight_data["reference_binary"]),
            )
        ],
    }


def _coverage_comparison_scope(
    result: dict[str, Any], *, coverage_binary_sha256: str, language: str
) -> str:
    """Hash every invariant needed for a meaningful marginal comparison."""

    metric = "rust_region" if language == "rust" else "go_statement"
    denominator = result.get("total_regions", result.get("total_statements"))
    if language == "rust":
        if result.get("coverage_filter_schema") != RUST_FIRST_PARTY_COVERAGE_SCHEMA:
            raise RuntimeError("Rust coverage is not first-party filtered")
        if result.get("source_scope") != "/workspace/src":
            raise RuntimeError("Rust coverage source scope changed")
        if int(denominator or 0) <= 0:
            raise RuntimeError("Rust first-party coverage denominator is empty")
    payload = {
        "metric": metric,
        "denominator": denominator,
        "coverage_binary_sha256": coverage_binary_sha256,
        "sample_policy": result.get("sample_policy"),
    }
    if language == "rust":
        payload.update(
            {
                "coverage_filter_schema": result.get("coverage_filter_schema"),
                "source_scope": result.get("source_scope"),
            }
        )
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _revalidate_recovery_baseline(
    *,
    legacy_observation: dict[str, Any],
    legacy_units: set[str],
    restored: dict[str, Any],
    coverage_scope_sha256: str,
) -> dict[str, Any]:
    """Decide whether a fresh exact-suite measurement may rebase legacy scalar data."""

    restored_units = {str(unit) for unit in (restored.get("covered_units") or [])}
    lost = sorted(legacy_units - restored_units)
    gained = sorted(restored_units - legacy_units)
    evidence = {
        "schema": "programbench_v4_coverage_baseline_revalidation_v1",
        "coverage_scope_sha256": coverage_scope_sha256,
        "legacy_primary_coverage": float(legacy_observation["primary_coverage"]),
        "fresh_primary_coverage": float(restored["primary_coverage"]),
        "legacy_covered_unit_count": len(legacy_units),
        "fresh_covered_unit_count": len(restored_units),
        "lost_covered_units": lost,
        "gained_covered_units": gained,
    }
    if not legacy_units:
        return {
            "action": "pause_missing_exact_legacy_witnesses",
            "blocked": True,
            "evidence": evidence,
        }
    if lost:
        return {
            "action": "pause_exact_coverage_units_regressed",
            "blocked": True,
            "evidence": evidence,
        }
    replacement = dict(legacy_observation)
    replacement.update(
        {
            "primary_coverage": float(restored["primary_coverage"]),
            "coverage_scope_sha256": coverage_scope_sha256,
            "covered_units": tuple(sorted(restored_units)),
        }
    )
    return {
        "action": "rebase_stale_legacy_scalar_to_fresh_exact_suite",
        "blocked": False,
        "evidence": evidence,
        "replacement_observation": replacement,
    }


def _plausible_coverage_unit_owners(
    *, repo_root: Path, missing_units: set[str], candidates: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Find auditable exact or conservative semantic owners for missing units."""

    owners: dict[str, dict[str, str]] = {}
    for witness_path in sorted(
        repo_root.glob("witnesses/iteration-*/case_witnesses.json"), reverse=True
    ):
        witness_map = read_json(witness_path).get("witness_map") or {}
        for case_id, witnesses in witness_map.items():
            hits = sorted(
                unit
                for unit in missing_units
                if f"coverage:{unit}" in {str(value) for value in witnesses}
            )
            if hits:
                owners[str(case_id)] = {
                    "case_id": str(case_id),
                    "basis": "exact_case_witness_map",
                    "matched_units": json.dumps(hits, ensure_ascii=False),
                }
        if owners:
            return sorted(owners.values(), key=lambda row: row["case_id"])

    semantic_tokens = {
        Path(unit.split(":", 1)[0]).stem.lower()
        for unit in missing_units
        if unit.split(":", 1)[0]
    }
    for case in candidates:
        case_id = str(case.get("name") or "")
        command = str((case.get("args") or [""])[0]).lstrip("-").lower()
        haystack = " ".join(
            [case_id, str(case.get("area") or ""), command]
        ).lower()
        hits = sorted(token for token in semantic_tokens if token and token in haystack)
        if hits:
            owners[case_id] = {
                "case_id": case_id,
                "basis": "semantic_source_stem_behavior_family_or_command",
                "matched_units": json.dumps(hits, ensure_ascii=False),
            }
    return sorted(owners.values(), key=lambda row: row["case_id"])


def _union_exact_remeasurements(
    *, legacy_units: set[str], measurements: list[dict[str, Any]]
) -> dict[str, Any]:
    if not measurements:
        raise ValueError("exact-unit recovery requires at least one measurement")
    scopes = {str(row.get("coverage_scope_sha256") or "") for row in measurements}
    union: set[str] = set()
    for row in measurements:
        union.update(str(unit) for unit in (row.get("covered_units") or []))
    missing = sorted(legacy_units - union)
    best = max(
        measurements,
        key=lambda row: (
            int(row.get("covered_statements", row.get("covered_regions", 0)) or 0),
            float(row.get("primary_coverage") or 0.0),
        ),
    )
    return {
        "accepted": bool(len(scopes) == 1 and not missing),
        "coverage_scope_sha256_values": sorted(scopes),
        "legacy_unit_count": len(legacy_units),
        "union_unit_count": len(union),
        "missing_units_after_union": missing,
        "reproduced_units": sorted(legacy_units & union),
        "union_coverage": {
            **best,
            "covered_units": sorted(union),
            "aggregation": "exact_unit_union_with_best_fresh_scalar",
        },
    }


def _quality_valid_for_baseline_revalidation(report: dict[str, Any]) -> bool:
    repeat = report.get("repeat_check") or {}
    repeat_code = repeat.get("pytest_returncode", repeat.get("returncode", 1))
    return bool(
        report.get("all_dummies_rejected")
        and report.get("all_target_executions_isolated")
        and report.get("all_tests_reject_all_dummies")
        and not report.get("dummy_passing_test_names")
        and int(repeat_code) == 0
    )


def _restore_zero_witness_tranche(
    request: dict[str, Any], repo_root: Path, *, prior_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Restore and revalidate the exact pre-tranche suite.

    The rejected generation/capture evidence remains immutable and auditable;
    only the canonical accepted candidate, bundle, coverage and quality state
    are rebuilt.  The returned receipt is itself immutable and can therefore
    be bound into the recovery checkpoint.
    """

    iteration = int(request["iteration"])
    rollback_root = repo_root / f"zero_witness_rollbacks/iteration-{iteration:04d}"
    receipt_path = rollback_root / "receipt.json"
    current_path = repo_root / "candidates/current.json"
    if receipt_path.is_file():
        receipt = read_json(receipt_path)
        if sha256_file(current_path) != receipt.get("restored_candidate_sha256"):
            raise RuntimeError("zero-witness rollback receipt candidate digest changed")
        for relative, expected in (receipt.get("restored_artifact_sha256") or {}).items():
            artifact = rollback_root / relative
            if not artifact.is_file() or sha256_file(artifact) != expected:
                raise RuntimeError("zero-witness rollback receipt artifact digest changed")
        return receipt, {
            "receipt_path": str(receipt_path),
            "receipt_sha256": sha256_file(receipt_path),
            "candidate_state_sha256": receipt["restored_candidate_sha256"],
        }

    prior_payload = read_json(prior_path)
    prior_cases = prior_payload.get("cases") or []
    staged_cases = read_json(current_path).get("cases") or []
    if not prior_cases or len(staged_cases) <= len(prior_cases):
        raise RuntimeError("zero-witness rollback requires a nonempty expanded suite")
    rollback_root.mkdir(parents=True)
    shutil.copy2(current_path, rollback_root / "rejected_candidates.json")
    current_bundle_path = repo_root / "artifacts/current_bundle.json"
    shutil.copy2(current_bundle_path, rollback_root / "rejected_bundle_descriptor.json")
    rejected_bundle = Path(read_json(current_bundle_path)["bundle_root"])
    rejected_manifest = rejected_bundle / "eval/generated_cli_manifest.json"
    shutil.copy2(rejected_manifest, rollback_root / "rejected_manifest.json")
    quick_path = repo_root / "artifacts/latest_quick_coverage.json"
    shutil.copy2(quick_path, rollback_root / "rejected_coverage.json")
    quality_path = repo_root / f"quality/iteration-{iteration:04d}.json"
    if not quality_path.is_file():
        raise RuntimeError("zero-witness rollback has no quality report")
    shutil.copy2(quality_path, rollback_root / "rejected_quality.json")

    # Preserve the exact byte identity of the pre-iteration candidate backup;
    # recovery checkpoints key their accepted suite to this digest.
    shutil.copy2(prior_path, current_path)
    capture_response = capture(request, repo_root)
    coverage_response = _coverage_profile(request, repo_root, full=False)
    restored_coverage = read_json(quick_path)
    quality_response = quality(request, repo_root)
    if quality_response.get("quality_unrepairable") or quality_response.get("suite_fuse_blocked"):
        raise RuntimeError("restored zero-witness baseline failed quality revalidation")
    restored_quality_path = Path(str(quality_response.get("quality_report") or ""))
    if not restored_quality_path.is_file() or not _quality_valid_for_baseline_revalidation(
        read_json(restored_quality_path)
    ):
        raise RuntimeError("restored zero-witness baseline lacks valid quality evidence")

    restored_bundle_path = repo_root / "artifacts/current_bundle.json"
    restored_bundle = Path(read_json(restored_bundle_path)["bundle_root"])
    snapshots = {
        "restored_candidates.json": current_path,
        "restored_bundle_descriptor.json": restored_bundle_path,
        "restored_manifest.json": restored_bundle / "eval/generated_cli_manifest.json",
        "restored_coverage.json": quick_path,
        "restored_quality.json": restored_quality_path,
    }
    for name, source in snapshots.items():
        shutil.copy2(source, rollback_root / name)
    restored_hashes = {
        name: sha256_file(rollback_root / name) for name in snapshots
    }
    receipt = {
        "schema": "programbench_v4_zero_witness_rollback_v1",
        "reason": "zero_novelty_and_zero_primary_coverage_gain",
        "iteration": iteration,
        "rejected_candidate_count": len(staged_cases),
        "restored_candidate_count": len(prior_cases),
        "rejected_candidate_sha256": sha256_file(
            rollback_root / "rejected_candidates.json"
        ),
        "restored_candidate_sha256": sha256_file(current_path),
        "restored_artifact_sha256": restored_hashes,
        "capture_response": capture_response,
        "coverage_execution_provenance": coverage_response.get(
            "execution_provenance"
        ),
        "quality_report": str(restored_quality_path),
    }
    atomic_write_json(receipt_path, receipt)
    return receipt, {
        "receipt_path": str(receipt_path),
        "receipt_sha256": sha256_file(receipt_path),
        "candidate_state_sha256": receipt["restored_candidate_sha256"],
    }


def _measure_rust_afl_qemu(
    request: dict[str, Any],
    repo_root: Path,
    *,
    bundle: Path,
    path_history: list[int],
) -> dict[str, Any]:
    """Measure the accepted Rust suite on one fixed source-built binary.

    This is invoked only after LLVM region/strong-novelty signals are already
    low.  It therefore adds a second saturation signal without putting QEMU on
    every productive iteration's critical path.  Edge union is persisted for
    reporting but never consulted by the controller's stop policy.
    """

    repo = request["repository"]
    preflight = read_json(repo_root / "artifacts/preflight.json")
    binary = Path(preflight["reference_binary"])
    afl_root = Path(str(repo.get("afl_qemu_root") or repo.get("rust_afl_root") or ""))
    if not (afl_root / "afl-showmap").is_file() or not (
        afl_root / "afl-qemu-trace"
    ).is_file():
        raise RuntimeError("Rust AFL/QEMU toolchain is incomplete")
    scope = validate_rust_qemu_scope(
        repo,
        executable=binary,
        executable_sha256=str(preflight["reference_binary_sha256"]),
        suite=bundle,
        suite_sha256=None,
        scope_sha256=str(request["scope_sha256"]),
        runtime_image_id=str(repo["runtime_image_id"]),
    )
    iteration = int(request["iteration"])
    output = repo_root / "coverage" / "afl-qemu" / f"iteration-{iteration:04d}"
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    timeout = min(7200, max(300, int(repo.get("rust_afl_timeout_seconds") or 3600)))
    script = " ".join(
        [
            "set -eu;",
            "export HOME=/home/agent PYTHONDONTWRITEBYTECODE=1 TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8;",
            "python3 /scaffold/v3/tools/run_afl_qemu_suite.py",
            f"--label {shlex.quote(str(repo['instance_id']))}",
            "--suite /suite",
            "--executable /binary",
            "--output /out",
            "--afl-root /afl",
            "--python /usr/bin/python3",
            "--timeout-cap 30",
        ]
    )
    run_container(
        image=str(repo["runtime_image_id"]),
        stage="quick_coverage",
        mounts=(
            Mount(ROOT, "/scaffold"),
            Mount(bundle, "/suite"),
            Mount(binary, "/binary"),
            Mount(afl_root, "/afl"),
            Mount(output, "/out", readonly=False),
        ),
        script=script,
        log=repo_root / f"logs/afl-qemu-{iteration:04d}.json",
        timeout=timeout,
        memory="4g",
    )
    result_path = output / "result.json"
    if not result_path.is_file():
        raise RuntimeError("Rust AFL/QEMU stage produced no result.json")
    qemu_result = read_json(result_path)
    replay_status = str(qemu_result.get("semantic_replay_status") or "")
    transport_limited_but_usable = bool(
        replay_status == "bitmap_usable_exit_status_transport_limitation"
        and qemu_result.get("wrapper_exit_status_only_mismatch") is True
        and int(qemu_result.get("pytest_failed") or 0)
        == int(qemu_result.get("expected_nonzero_exit_cases") or 0)
    )
    if (
        (
            replay_status != "passed"
            or int(qemu_result.get("pytest_returncode") or 0) != 0
        )
        and not transport_limited_but_usable
    ) or int(qemu_result.get("binary_calls") or 0) <= 0:
        raise RuntimeError("Rust AFL/QEMU semantic replay did not produce valid calls")
    metrics = build_rust_qemu_metrics(
        qemu_result, scope=scope, result_path=result_path
    )
    path_value = int(metrics["path"]["value"])
    checkpoint = build_rust_qemu_checkpoint(
        metrics=metrics,
        scope=scope,
        iteration=iteration,
        path_history=[*path_history, path_value],
    )
    checkpoint_path = persist_rust_qemu_checkpoint(
        output / "checkpoint.json", checkpoint
    )
    response = {
        "path_value": path_value,
        "edge_value": int(metrics["edge"]["value"]),
        "calls": int(metrics["binary_calls"]),
        "checkpoint": str(checkpoint_path),
        "metrics": metrics,
        "report_only": False,
    }
    atomic_write_json(repo_root / "artifacts/latest_afl_qemu.json", {
        **metrics, "checkpoint": str(checkpoint_path), "iteration": iteration,
        "path_stop_policy": "auxiliary_only", "edge_stop_policy": "report_only",
    })
    return response


def _measure_go_afl_qemu(
    request: dict[str, Any], repo_root: Path, *, bundle: Path
) -> dict[str, Any]:
    """Measure stable Go first-party control-flow statistics (report-only)."""

    repo = request["repository"]
    preflight = read_json(repo_root / "artifacts/preflight.json")
    binary = Path(preflight["reference_binary"])
    afl_root = Path(str(repo.get("afl_qemu_root") or repo.get("go_afl_root") or ""))
    if not (afl_root / "afl-showmap").is_file() or not (afl_root / "afl-qemu-trace").is_file():
        raise RuntimeError("Go AFL/QEMU toolchain is incomplete")
    scope = validate_go_qemu_scope(
        repo, executable=binary,
        executable_sha256=str(preflight["reference_binary_sha256"]),
        suite=bundle, scope_sha256=str(request["scope_sha256"]),
        runtime_image_id=str(repo["runtime_image_id"]),
    )
    iteration = int(request["iteration"])
    output = repo_root / "coverage" / "afl-qemu" / f"iteration-{iteration:04d}"
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    max_calls = max(1, min(5000, int(repo.get("go_afl_max_calls") or 2000)))
    commands = []
    for repeat in range(1, 4):
        commands.append(" ".join([
            "python3 /scaffold/v3/tools/run_afl_qemu_suite.py",
            f"--label {shlex.quote(str(repo['instance_id']))}-r{repeat}",
            "--suite /suite --executable /binary",
            f"--output /out/repeat{repeat}",
            "--afl-root /afl --python /usr/bin/python3 --timeout-cap 30",
            f"--sample-calls {max_calls} --sample-tests {max_calls}",
            "--go-first-party-only",
        ]))
    script = (
        "set -eu; export HOME=/home/agent PYTHONDONTWRITEBYTECODE=1 "
        "GOMAXPROCS=1 GODEBUG=randautoseed=0 TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8; "
        + "; ".join(commands)
    )
    timeout = min(14400, max(900, int(repo.get("go_afl_timeout_seconds") or 7200)))
    run_container(
        image=str(repo["runtime_image_id"]), stage="quick_coverage",
        mounts=(Mount(ROOT, "/scaffold"), Mount(bundle, "/suite"),
                Mount(binary, "/binary"), Mount(afl_root, "/afl"),
                Mount(output, "/out", readonly=False)),
        script=script, log=repo_root / f"logs/go-afl-qemu-{iteration:04d}.json",
        timeout=timeout, memory="4g",
    )
    repeats = [output / f"repeat{repeat}" for repeat in range(1, 4)]
    metrics = build_go_qemu_metrics(repeats, scope=scope)
    checkpoint = persist_go_qemu_checkpoint(
        output / "checkpoint.json", metrics=metrics, iteration=iteration
    )
    atomic_write_json(repo_root / "artifacts/latest_afl_qemu.json", {
        **metrics, "checkpoint": str(checkpoint), "iteration": iteration,
    })
    return {"path_value": int(metrics["distinct_path_signatures"]),
            "edge_value": int(metrics["absolute_tuple_union"]),
            "calls": int(metrics["calls"]), "checkpoint": str(checkpoint),
            "metrics": metrics, "report_only": True}


def evaluate(request: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    quick = read_json(repo_root / "artifacts/latest_quick_coverage.json")
    preflight = read_json(repo_root / "artifacts/preflight.json")

    def comparison_scope(result: dict[str, Any]) -> str:
        return _coverage_comparison_scope(
            result,
            coverage_binary_sha256=str(preflight["coverage_binary_sha256"]),
            language=str(request["repository"].get("language") or ""),
        )

    scope_sha256 = comparison_scope(quick)
    history = request.get("workflow_context", {}).get("marginal_history") or []
    comparable = [
        row
        for row in history
        if row.get("promoted", True)
        and row.get("quality_passed", True)
        and row.get("coverage_valid", True)
        and row.get("coverage_scope_sha256") == scope_sha256
    ]
    recovery: dict[str, Any] | None = None
    baseline_rebase: dict[str, Any] | None = None
    if comparable and float(quick["primary_coverage"]) < float(comparable[-1]["primary_coverage"]) - 1e-9:
        # Reject the whole staged tranche, not individual witnesses. The raw
        # rows remain accounted for in the durable reservoir and may be
        # reconsidered after a later policy/audit decision.
        iteration = int(request["iteration"])
        current_path = repo_root / "candidates/current.json"
        prior_path = repo_root / f"candidates/pre-iteration-{iteration:04d}.json"
        if not prior_path.is_file():
            raise RuntimeError("coverage regression has no suite rollback snapshot")
        rejected = read_json(current_path).get("cases") or []
        prior = read_json(prior_path).get("cases") or []
        if not prior:
            raise RuntimeError("coverage regression rollback suite is empty")
        atomic_write_json(
            repo_root / f"candidates/iteration-{iteration:04d}.coverage_regression_reservoir.json",
            {
                "profile": "programbench_v4",
                "reason": "coverage_regression_rejected_tranche",
                "cases": rejected,
            },
        )
        atomic_write_json(current_path, {"profile": "programbench_v4", "cases": prior})
        capture(request, repo_root)
        first_measurement_run = _coverage_profile(request, repo_root, full=False)
        restored = read_json(repo_root / "artifacts/latest_quick_coverage.json")
        restored_scope = comparison_scope(restored)
        if restored_scope != scope_sha256:
            raise RuntimeError("coverage rollback changed the comparison scope")
        legacy_units = {
            str(unit) for unit in (comparable[-1].get("covered_units") or [])
        }
        if not legacy_units:
            # One-time compatibility path for checkpoints created before exact
            # units were embedded in Observation. Match the scalar and scope to
            # a prior complete-suite artifact; do not guess from another run.
            for prior_iteration in range(iteration - 1, 0, -1):
                prior_coverage_path = (
                    repo_root / f"coverage/quick-{prior_iteration:04d}/coverage.json"
                )
                if not prior_coverage_path.is_file():
                    continue
                prior_coverage = read_json(prior_coverage_path)
                if (
                    comparison_scope(prior_coverage) == scope_sha256
                    and abs(
                        float(prior_coverage["primary_coverage"])
                        - float(comparable[-1]["primary_coverage"])
                    )
                    <= 1e-9
                ):
                    legacy_units = {
                        str(unit)
                        for unit in (prior_coverage.get("covered_units") or [])
                    }
                    break
        revalidation = _revalidate_recovery_baseline(
            legacy_observation=comparable[-1],
            legacy_units=legacy_units,
            restored=restored,
            coverage_scope_sha256=scope_sha256,
        )
        exact_recovery: dict[str, Any] | None = None
        if revalidation["blocked"] and revalidation["action"] == "pause_exact_coverage_units_regressed":
            current_candidates = read_json(current_path).get("cases") or []
            missing_units = set(revalidation["evidence"]["lost_covered_units"])
            owners = _plausible_coverage_unit_owners(
                repo_root=repo_root,
                missing_units=missing_units,
                candidates=current_candidates,
            )
            repeat_limit = min(
                3,
                max(
                    1,
                    int(
                        request["repository"].get(
                            "coverage_baseline_revalidation_repeats", 3
                        )
                    ),
                ),
            )
            measurements = [
                {
                    **restored,
                    "coverage_scope_sha256": restored_scope,
                    "repeat": 1,
                    "execution_provenance": first_measurement_run.get(
                        "execution_provenance"
                    ),
                }
            ]
            for repeat in range(2, repeat_limit + 1):
                repeat_run = _coverage_profile(request, repo_root, full=False)
                repeat_result = read_json(
                    repo_root / "artifacts/latest_quick_coverage.json"
                )
                measurements.append(
                    {
                        **repeat_result,
                        "coverage_scope_sha256": comparison_scope(repeat_result),
                        "repeat": repeat,
                        "execution_provenance": repeat_run.get("execution_provenance"),
                    }
                )
            union = _union_exact_remeasurements(
                legacy_units=legacy_units, measurements=measurements
            )
            prior_quality_path = (
                repo_root / f"quality/iteration-{iteration - 1:04d}.json"
            )
            quality_valid = bool(
                prior_quality_path.is_file()
                and _quality_valid_for_baseline_revalidation(
                    read_json(prior_quality_path)
                )
            )
            exact_recovery = {
                "schema": "programbench_v4_exact_unit_regression_recovery_v1",
                "repeat_limit": repeat_limit,
                "unchanged_candidate_scope_sha256": sha256_file(current_path),
                "plausible_owner_cases": owners,
                "measurements": measurements,
                "union": union,
                "quality_report": str(prior_quality_path),
                "quality_valid": quality_valid,
                "accepted": bool(union["accepted"] and quality_valid),
            }
            atomic_write_json(
                repo_root
                / f"coverage/iteration-{iteration:04d}.exact_unit_recovery.json",
                exact_recovery,
            )
            if exact_recovery["accepted"]:
                restored = dict(union["union_coverage"])
                revalidation = _revalidate_recovery_baseline(
                    legacy_observation=comparable[-1],
                    legacy_units=legacy_units,
                    restored=restored,
                    coverage_scope_sha256=scope_sha256,
                )
            else:
                revalidation = {
                    **revalidation,
                    "evidence": {
                        **revalidation["evidence"],
                        "exact_unit_recovery": exact_recovery,
                    },
                }
        recovery = {
            "schema": "programbench_v4_coverage_regression_recovery_v1",
            "rejected_coverage": float(quick["primary_coverage"]),
            "accepted_baseline_coverage": float(comparable[-1]["primary_coverage"]),
            "restored_coverage": float(restored["primary_coverage"]),
            "coverage_scope_sha256": scope_sha256,
            "rejected_candidate_count": len(rejected),
            "restored_candidate_count": len(prior),
            "action": "rollback_tranche_remeasure_complete_suite",
            "baseline_revalidation": revalidation,
            "exact_unit_recovery": exact_recovery,
        }
        atomic_write_json(
            repo_root / f"coverage/iteration-{iteration:04d}.regression_recovery.json",
            recovery,
        )
        quick = restored
        if revalidation["blocked"]:
            return {
                "baseline_revalidation_blocked": True,
                "coverage_baseline_revalidation": revalidation,
                "coverage_regression_recovery": recovery,
                "total_raw_candidates": int(
                    read_json(repo_root / "artifacts/raw_candidate_ledger.json").get(
                        "unique_persisted_raw_candidates", 0
                    )
                ),
                "retained_suite_cases": len(prior),
            }
        if (
            abs(
                float(restored["primary_coverage"])
                - float(comparable[-1]["primary_coverage"])
            )
            > 1e-9
        ):
            baseline_rebase = revalidation
    bundle = Path(read_json(repo_root / "artifacts/current_bundle.json")["bundle_root"])
    manifest = read_json(bundle / "eval/generated_cli_manifest.json")
    cases = manifest.get("cases") or []
    current = read_json(repo_root / "candidates/current.json").get("cases") or []
    current_by_name = {str(case.get("name") or ""): case for case in current}
    behaviors: set[str] = set()
    assertions: set[str] = set()
    states: set[str] = set()
    for case in cases:
        name = str(case.get("name") or "")
        candidate = current_by_name.get(name)
        if candidate is None:
            raise RuntimeError("captured case is missing from retained candidates")
        static_witnesses = _case_static_witnesses(case, candidate)
        behaviors.update(
            witness.removeprefix("behavior:")
            for witness in static_witnesses
            if witness.startswith("behavior:")
        )
        assertions.update(
            witness.removeprefix("assertion:")
            for witness in static_witnesses
            if witness.startswith("assertion:")
        )
        states.update(
            witness.removeprefix("state:")
            for witness in static_witnesses
            if witness.startswith("state:")
        )
    errors = {int(case.get("returncode") or 0) for case in cases if int(case.get("returncode") or 0) != 0}
    fixtures = {
        tuple(sorted(key for key in ("files", "binary_files", "sequence", "terminal") if case.get(key)))
        for case in cases
    }
    ledger_path = repo_root / "artifacts/witness_ledger.json"
    prior = read_json(ledger_path) if ledger_path.is_file() else {"coverage_units": [], "behaviors": [], "errors": [], "fixtures": [], "assertions": [], "states": []}
    # V4 ledgers created before assertion/state novelty was explicit must not
    # misclassify the entire accepted baseline as novel on upgrade. Rebuild
    # only those missing dimensions from the exact pre-iteration suite and its
    # already-captured rows.
    legacy_assertions: set[str] = set(prior.get("assertions") or [])
    legacy_states: set[str] = set(prior.get("states") or [])
    if "assertions" not in prior or "states" not in prior:
        prior_candidate_path = (
            repo_root / f"candidates/pre-iteration-{int(request['iteration']):04d}.json"
        )
        captured_by_name = {
            str(case.get("name") or ""): case for case in cases
        }
        if prior_candidate_path.is_file():
            for candidate in read_json(prior_candidate_path).get("cases") or []:
                captured = captured_by_name.get(str(candidate.get("name") or ""))
                if captured is None:
                    continue
                legacy_witnesses = _case_static_witnesses(captured, candidate)
                legacy_assertions.update(
                    value.removeprefix("assertion:")
                    for value in legacy_witnesses
                    if value.startswith("assertion:")
                )
                legacy_states.update(
                    value.removeprefix("state:")
                    for value in legacy_witnesses
                    if value.startswith("state:")
                )
    coverage_units = set(quick.get("covered_units") or [])
    new_units = coverage_units - set(prior.get("coverage_units") or [])
    new_behaviors = behaviors - set(prior.get("behaviors") or [])
    new_errors = {str(value) for value in errors} - set(prior.get("errors") or [])
    fixture_strings = {json.dumps(value) for value in fixtures}
    new_fixtures = fixture_strings - set(prior.get("fixtures") or [])
    new_assertions = assertions - legacy_assertions
    new_states = states - legacy_states
    ledger = {
        "coverage_units": sorted(coverage_units | set(prior.get("coverage_units") or [])),
        "behaviors": sorted(behaviors | set(prior.get("behaviors") or [])),
        "errors": sorted({str(value) for value in errors} | set(prior.get("errors") or [])),
        "fixtures": sorted(fixture_strings | set(prior.get("fixtures") or [])),
        "assertions": sorted(assertions | legacy_assertions),
        "states": sorted(states | legacy_states),
    }
    zero_witness_rollback: dict[str, Any] | None = None
    accepted_artifact_bindings: dict[str, Any] | None = None
    iteration = int(request["iteration"])
    prior_path = repo_root / f"candidates/pre-iteration-{iteration:04d}.json"
    prior_case_count = (
        len(read_json(prior_path).get("cases") or []) if prior_path.is_file() else 0
    )
    strong_novelty_total = sum(
        len(value)
        for value in (
            new_units,
            new_behaviors,
            new_errors,
            new_states,
        )
    )
    weak_novelty_total = len(new_fixtures) + len(new_assertions)
    novelty_total = strong_novelty_total + weak_novelty_total
    zero_primary_gain = bool(
        comparable
        and abs(
            float(quick["primary_coverage"])
            - float(comparable[-1]["primary_coverage"])
        )
        <= 1e-9
    )
    if (
        recovery is None
        and zero_primary_gain
        # Fixture/assertion variation is retained as evidence but cannot keep
        # adding zero-coverage tranches forever. Roll back only when no strong
        # behavioral witness was added.
        and strong_novelty_total == 0
        and prior_path.is_file()
        and len(current) > prior_case_count > 0
    ):
        staged_coverage = dict(quick)
        zero_witness_rollback, accepted_artifact_bindings = (
            _restore_zero_witness_tranche(request, repo_root, prior_path=prior_path)
        )
        quick = read_json(repo_root / "artifacts/latest_quick_coverage.json")
        if comparison_scope(quick) != scope_sha256:
            raise RuntimeError("zero-witness rollback changed the comparison scope")
        legacy_units = {
            str(unit) for unit in (comparable[-1].get("covered_units") or [])
        }
        restored_units = {str(unit) for unit in (quick.get("covered_units") or [])}
        if legacy_units - restored_units:
            raise RuntimeError("zero-witness rollback lost accepted exact coverage units")
        if (
            abs(
                float(quick["primary_coverage"])
                - float(comparable[-1]["primary_coverage"])
            )
            > 1e-9
        ):
            raise RuntimeError("zero-witness rollback did not restore accepted coverage")
        zero_witness_rollback = {
            **zero_witness_rollback,
            "rejected_primary_coverage": float(staged_coverage["primary_coverage"]),
            "restored_primary_coverage": float(quick["primary_coverage"]),
            "coverage_scope_sha256": scope_sha256,
        }
        current = read_json(repo_root / "candidates/current.json").get("cases") or []
        bundle = Path(read_json(repo_root / "artifacts/current_bundle.json")["bundle_root"])
        cases = read_json(bundle / "eval/generated_cli_manifest.json").get("cases") or []
        coverage_units = restored_units
    atomic_write_json(ledger_path, ledger)
    cumulative = read_json(repo_root / "candidates/current.json").get("cases") or []
    raw_ledger = read_json(repo_root / "artifacts/raw_candidate_ledger.json")
    total_raw_candidates = int(
        raw_ledger.get("unique_persisted_raw_candidates")
        or raw_ledger.get("total_generated_candidates")
        or 0
    )
    if total_raw_candidates < len(cumulative):
        raise RuntimeError("raw candidate ledger is smaller than retained suite")
    iteration_generated = int(
        (raw_ledger.get("iterations") or {}).get(str(int(request["iteration"]))) or 0
    )
    if iteration_generated <= 0:
        raise RuntimeError("raw candidate ledger has no current-tranche attempt count")
    auxiliary_path: dict[str, Any] | None = None
    auxiliary_path_error: str | None = None
    context = request.get("workflow_context") or {}
    primary_gain = (
        float(quick["primary_coverage"]) - float(comparable[-1]["primary_coverage"])
        if comparable
        else float("inf")
    )
    language = str(request["repository"].get("language") or "").lower()
    afl_enabled = bool(
        request["repository"].get("afl_qemu_enabled") is True
        or request["repository"].get("rust_afl_qemu_enabled") is True
        or request["repository"].get("go_afl_qemu_enabled") is True
    )
    afl_period = max(1, int(request["repository"].get("afl_measurement_period") or 3))
    low_primary_and_novelty = bool(
        primary_gain < float(context.get("minimum_primary_gain_pp") or 0.25)
        and strong_novelty_total < int(context.get("minimum_strong_novelty") or 1)
    )
    path_measurement_due = bool(
        language in {"rust", "c", "cpp"} and afl_enabled
        and context.get("require_auxiliary_path_saturation") is True
        and (low_primary_and_novelty or iteration % afl_period == 0)
        and recovery is None
    )
    go_measurement_due = bool(
        language == "go" and afl_enabled and iteration % afl_period == 0
        and recovery is None
    )
    if path_measurement_due:
        prior_path_values = [
            int(row["auxiliary_path_coverage"])
            for row in comparable
            if row.get("auxiliary_path_valid")
            and row.get("auxiliary_path_coverage") is not None
        ]
        try:
            auxiliary_path = _measure_rust_afl_qemu(
                request,
                repo_root,
                bundle=bundle,
                path_history=prior_path_values,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            auxiliary_path_error = str(exc)
            atomic_write_json(
                repo_root
                / f"coverage/afl-qemu/iteration-{iteration:04d}.failed.json",
                {
                    "schema": "programbench_v4_rust_afl_qemu_failure_v1",
                    "iteration": iteration,
                    "error": auxiliary_path_error,
                    "retryable": True,
                    "region_and_strong_novelty_low": True,
                },
            )
    go_afl: dict[str, Any] | None = None
    go_afl_error: str | None = None
    if go_measurement_due:
        try:
            go_afl = _measure_go_afl_qemu(request, repo_root, bundle=bundle)
        except (OSError, RuntimeError, ValueError) as exc:
            go_afl_error = str(exc)
            atomic_write_json(
                repo_root / f"coverage/afl-qemu/iteration-{iteration:04d}.failed.json",
                {"schema": "programbench_v4_go_afl_qemu_failure_v1",
                 "iteration": iteration, "error": go_afl_error,
                 "retryable": True, "report_only": True},
            )
    return {
        "observation": {
            "primary_coverage": float(quick["primary_coverage"]),
            "coverage_scope_sha256": comparison_scope(quick),
            "retained_cases": len(cases),
            "generated_candidates": iteration_generated,
            "wall_seconds": 1.0,
            "new_behavior_families": len(new_behaviors),
            "new_error_classes": len(new_errors),
            "new_fixture_shapes": len(new_fixtures),
            "new_assertion_classes": len(new_assertions),
            "new_state_transitions": len(new_states),
            "new_coverage_units": len(new_units),
            "covered_units": tuple(sorted(coverage_units)),
            "coverage_valid": bool(quick.get("coverage_valid", True)),
            "auxiliary_path_coverage": (
                int(auxiliary_path["path_value"]) if auxiliary_path else None
            ),
            "auxiliary_path_valid": auxiliary_path is not None,
            # A recovered regression is evidence about an invalid tranche, not
            # a second promoted observation of the same accepted suite.
            "promoted": recovery is None,
        },
        "novelty_evidence": {
            "strong_novelty": strong_novelty_total,
            "weak_novelty": weak_novelty_total,
            "weak_novelty_blocks_saturation": False,
        },
        "rust_afl_qemu": auxiliary_path,
        "rust_afl_qemu_measurement_due": path_measurement_due,
        "rust_afl_qemu_error": auxiliary_path_error,
        "afl_qemu": auxiliary_path or go_afl,
        "afl_qemu_measurement_due": path_measurement_due or go_measurement_due,
        "afl_qemu_error": auxiliary_path_error or go_afl_error,
        "go_afl_qemu": go_afl,
        "go_afl_qemu_report_only": True if language == "go" else None,
        "total_raw_candidates": total_raw_candidates,
        "unique_persisted_raw_candidates": total_raw_candidates,
        "retained_suite_cases": len(cases),
        "coverage_regression_recovery": recovery,
        "coverage_baseline_rebase": baseline_rebase,
        "zero_witness_rollback": zero_witness_rollback,
        "accepted_artifact_bindings": accepted_artifact_bindings,
    }


def freeze(request: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    quality_files = sorted((repo_root / "quality").glob("iteration-*.json"))
    full = read_json(repo_root / "artifacts/final_full_coverage.json")
    repo = request["repository"]
    language = str(repo.get("language") or "").lower()
    afl_enabled = bool(repo.get("afl_qemu_enabled") is True
                       or repo.get("rust_afl_qemu_enabled") is True
                       or repo.get("go_afl_qemu_enabled") is True)
    final_afl: dict[str, Any] | None = None
    final_afl_error: str | None = None
    if afl_enabled:
        bundle = Path(read_json(repo_root / "artifacts/current_bundle.json")["bundle_root"])
        try:
            if language == "go":
                final_afl = _measure_go_afl_qemu(request, repo_root, bundle=bundle)
            elif language in {"rust", "c", "cpp"}:
                history = (request.get("workflow_context") or {}).get("marginal_history") or []
                path_history = [int(row["auxiliary_path_coverage"]) for row in history
                                if row.get("auxiliary_path_valid")
                                and row.get("auxiliary_path_coverage") is not None]
                final_afl = _measure_rust_afl_qemu(
                    request, repo_root, bundle=bundle, path_history=path_history
                )
        except (OSError, RuntimeError, ValueError) as exc:
            # AFL is an auxiliary/reporting metric. Preserve an explicit
            # unavailable receipt but never relabel a quality-valid suite as
            # failed solely because QEMU is unavailable.
            final_afl_error = str(exc)
            atomic_write_json(repo_root / "frozen/afl_qemu_unavailable.json", {
                "schema": "programbench_v4_final_afl_qemu_unavailable_v1",
                "language": language, "error": final_afl_error,
                "does_not_invalidate_suite": True,
            })
    summary = {
        "schema": "programbench_v4_frozen_suite_v1",
        "instance_id": request["repository"]["instance_id"],
        "bundle": read_json(repo_root / "artifacts/current_bundle.json"),
        "coverage": full,
        "quality_report": str(quality_files[-1]) if quality_files else None,
        "stop_basis": "marginal_saturation",
        "afl_qemu": final_afl,
        "afl_qemu_error": final_afl_error,
        "afl_qemu_policy": (
            "report_only_never_stops" if language == "go"
            else "path_auxiliary_edge_report_only"
        ),
    }
    atomic_write_json(repo_root / "frozen/pipeline_summary.json", summary)
    return {"freeze_accepted": True, "pipeline_summary": str(repo_root / "frozen/pipeline_summary.json")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    args = parser.parse_args()
    request = read_json(args.request)
    stage = request["stage"]
    repo_root = Path(request["repository_root"])
    handlers = {
        "dependency_prefetch": dependency_prefetch,
        "preflight": preflight,
        "plan_tranche": plan_tranche,
        "generate": generate,
        "static_select": static_select,
        "oracle_capture": capture,
        "quick_coverage": lambda req, root: _coverage_profile(req, root, full=False),
        "quality_gates": quality,
        "evaluate_marginal": evaluate,
        "final_capture": capture,
        "full_coverage": lambda req, root: _coverage_profile(req, root, full=True),
        "freeze_verification": quality,
        "freeze": freeze,
    }
    if stage not in handlers:
        raise ValueError(f"unsupported stage: {stage}")
    result = handlers[stage](request, repo_root)
    response = {
        "schema": "programbench_v4_stage_response_v1",
        "stage": stage,
        "scope_sha256": request["scope_sha256"],
        **result,
    }
    atomic_write_json(args.response, response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
