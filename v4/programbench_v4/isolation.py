from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


OFFLINE_STAGES = {
    "source_build",
    "native_tests",
    "agent_tool_execution",
    "oracle_capture",
    "quick_coverage",
    "full_coverage",
    "quality_gates",
    "freeze_verification",
}


@dataclass(frozen=True)
class Mount:
    source: Path
    target: str
    readonly: bool = True


@dataclass(frozen=True)
class ContainerContract:
    image_id: str
    stage: str
    name: str
    mounts: tuple[Mount, ...] = ()
    memory: str = "4g"
    cpus: float = 2.0
    pids_limit: int = 512
    entrypoint: str = "/bin/sh"

    def __post_init__(self) -> None:
        if not self.image_id.startswith("sha256:"):
            raise ValueError("V4 requires an immutable sha256 Docker image ID")
        if not self.name or any(character.isspace() for character in self.name):
            raise ValueError("container name must be nonempty and whitespace-free")

    def docker_run(self, command: Iterable[str]) -> list[str]:
        network = "none" if self.stage in OFFLINE_STAGES else "bridge"
        result = [
            "docker",
            "run",
            "--rm",
            "--name",
            self.name,
            "--label",
            "programbench.workflow=v4",
            "--label",
            f"programbench.stage={self.stage}",
            "--network",
            network,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "1000:1000",
            "--pids-limit",
            str(self.pids_limit),
            "--memory",
            self.memory,
            "--cpus",
            str(self.cpus),
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,uid=1000,gid=1000,mode=1770",
            "--tmpfs",
            "/workspace:rw,exec,nosuid,nodev,uid=1000,gid=1000,mode=0750",
            # Some CLIs legitimately invoke their language toolchain at run
            # time. Give the unprivileged identity an ephemeral private HOME
            # for caches without exposing any host home, credentials, or
            # persistent state through the read-only root filesystem.
            "--tmpfs",
            "/home/agent:rw,exec,nosuid,nodev,uid=1000,gid=1000,mode=0700",
            "--entrypoint",
            self.entrypoint,
        ]
        for mount in self.mounts:
            source = mount.source.resolve(strict=True)
            specification = f"type=bind,source={source},target={mount.target}"
            if mount.readonly:
                specification += ",readonly"
            result.extend(["--mount", specification])
        result.append(self.image_id)
        result.extend(str(part) for part in command)
        return result


REQUIRED_SECURITY = {
    "read_only_rootfs": True,
    "cap_drop_all": True,
    "no_new_privileges": True,
    "non_root_user": True,
    "docker_socket_mounted": False,
}


def validate_execution_provenance(
    provenance: dict[str, Any], *, expected_stage: str
) -> list[str]:
    errors: list[str] = []
    image = str(provenance.get("image_id") or "")
    if not image.startswith("sha256:"):
        errors.append("runtime image is not pinned by sha256 image ID")
    if provenance.get("stage") != expected_stage:
        errors.append("stage provenance mismatch")
    if expected_stage in OFFLINE_STAGES and provenance.get("network") != "none":
        errors.append("offline stage did not use network=none")
    security = provenance.get("security") or {}
    for key, expected in REQUIRED_SECURITY.items():
        if security.get(key) is not expected:
            errors.append(f"security requirement failed: {key}")
    if not provenance.get("binary_sha256") and expected_stage in {
        "oracle_capture",
        "quick_coverage",
        "full_coverage",
        "quality_gates",
        "freeze_verification",
    }:
        errors.append("target execution has no binary SHA256")
    return errors


def validate_pb_generation_scope(scope: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if scope.get("pb_official_tests_visible") is not False:
        errors.append("ProgramBench official tests must be held out")
    if scope.get("source_visible_to_generation_agent") is not True:
        errors.append("generation agent must receive the audited source context")
    if scope.get("native_tests_visible_to_generation_agent") is not True:
        errors.append("generation agent must receive native tests per PB construction")
    if scope.get("inference_image_binary_only") is not True:
        errors.append("inference image must be binary-only")
    if scope.get("credentials_mounted_into_container") is not False:
        errors.append("credentials must not be mounted into repository containers")
    return errors
