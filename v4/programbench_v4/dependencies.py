from __future__ import annotations

import hashlib
import json
import os
import stat
import re
import uuid
import time
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from .io import sha256_json


SCHEMA = "programbench_v4_dependency_cache_v1"
MANIFEST_NAME = "manifest.json"

_GO_MODULE = r"[A-Za-z0-9][A-Za-z0-9._~+/-]*"
_GO_PINNED_VERSION = re.compile(
    r"^(?:v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?|[0-9a-fA-F]{12,40})$"
)
_GO_INSTALL = re.compile(r"\bgo\s+install\s+([^\s;&|]+)")
_TESTSCRIPT_ENV = re.compile(
    r"^\s*(?:env\s+)?([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)\s*$"
)
_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def _manifest_names(language: str) -> frozenset[str]:
    if language == "go":
        return frozenset({"go.mod", "go.sum", "go.work", "go.work.sum"})
    if language == "rust":
        return frozenset({"Cargo.toml", "Cargo.lock", "config", "config.toml"})
    raise ValueError(f"unsupported dependency language: {language}")


def validate_go_install_spec(value: str) -> str:
    """Accept immutable module@version tools, never latest/branches/local paths."""

    value = str(value).strip()
    if value.count("@") != 1:
        raise ValueError(f"Go install dependency must be module@pinned-version: {value!r}")
    module, version = value.rsplit("@", 1)
    if not re.fullmatch(_GO_MODULE, module) or module.startswith((".", "/")):
        raise ValueError(f"invalid Go install module path: {module!r}")
    if not _GO_PINNED_VERSION.fullmatch(version):
        raise ValueError(f"Go install dependency is not pinned: {value!r}")
    return f"{module}@{version}"


def go_offline_proxy_environment(
    gomodcache: str = "/workspace/gomod",
) -> dict[str, str]:
    """Route all module lookups to Go's copied download-cache file proxy."""

    value = PurePosixPath(gomodcache)
    if (
        not value.is_absolute()
        or ".." in value.parts
        or not re.fullmatch(r"/[A-Za-z0-9._/+~-]+(?:/[A-Za-z0-9._+~-]+)*", str(value))
    ):
        raise ValueError(f"unsafe container GOMODCACHE path: {gomodcache!r}")
    proxy = value / "cache" / "download"
    return {
        "GOMODCACHE": str(value),
        "GOPROXY": f"file://{proxy}",
        "GOSUMDB": "off",
        "GONOSUMDB": "*",
        "GONOPROXY": "none",
        "GOVCS": "*:off",
        "GOENV": "off",
    }


def _is_go_test_support_file(path: Path, source: Path) -> bool:
    relative = path.relative_to(source)
    lowered = [part.lower() for part in relative.parts]
    return path.name.endswith("_test.go") or any(
        part in {"test", "tests", "testdata", "integration", "integration-tests"}
        for part in lowered[:-1]
    )


def discover_go_test_install_specs(source: Path) -> list[dict[str, Any]]:
    """Resolve pinned `go install` tools declared by native test support files."""

    source = source.resolve(strict=True)
    rows: list[dict[str, Any]] = []
    for path in sorted(source.rglob("*")):
        if (
            not path.is_file()
            or path.is_symlink()
            or not _is_go_test_support_file(path, source)
            or path.stat().st_size > 2 * 1024 * 1024
        ):
            continue
        raw = path.read_bytes()
        if b"\0" in raw:
            continue
        text = raw.decode("utf-8", errors="replace")
        environment: dict[str, str] = {}
        for line_number, line in enumerate(text.splitlines(), 1):
            assignment = _TESTSCRIPT_ENV.match(line)
            if assignment:
                environment[assignment.group(1)] = assignment.group(2).strip("'\"")
            for match in _GO_INSTALL.finditer(line):
                raw_spec = match.group(1).strip("'\"")
                if "@" not in raw_spec:
                    # `go install .` is satisfied by the pinned source module.
                    continue

                unresolved: set[str] = set()

                def replace_env(reference: re.Match[str]) -> str:
                    name = reference.group(1) or reference.group(2)
                    if name not in environment:
                        unresolved.add(name)
                        return reference.group(0)
                    return environment[name]

                expanded = _ENV_REFERENCE.sub(replace_env, raw_spec)
                relative = path.relative_to(source).as_posix()
                if unresolved:
                    raise ValueError(
                        f"unresolved Go install version variable in {relative}:{line_number}: "
                        + ", ".join(sorted(unresolved))
                    )
                spec = validate_go_install_spec(expanded)
                rows.append(
                    {
                        "spec": spec,
                        "source": relative,
                        "line": line_number,
                        "raw": raw_spec,
                    }
                )
    unique: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in rows:
        unique[(row["spec"], row["source"], row["line"])] = row
    return [unique[key] for key in sorted(unique)]


def resolve_go_prefetch_install_specs(repository: dict[str, Any]) -> dict[str, Any]:
    """Require static discoveries and the explicit network allowlist to agree."""

    if str(repository.get("language") or "") != "go":
        return {"specs": [], "discoveries": []}
    source = Path(str(repository.get("source_dir") or ""))
    discoveries = discover_go_test_install_specs(source)
    discovered = {str(row["spec"]) for row in discoveries}
    configured_values = repository.get("go_prefetch_install_specs") or []
    if not isinstance(configured_values, list):
        raise ValueError("go_prefetch_install_specs must be an array")
    configured = {validate_go_install_spec(str(value)) for value in configured_values}
    missing = discovered - configured
    extra = configured - discovered
    if missing or extra:
        raise ValueError(
            "Go install dependency allowlist mismatch: "
            f"missing={sorted(missing)} extra={sorted(extra)}"
        )
    return {"specs": sorted(configured), "discoveries": discoveries}


def dependency_input_files(source: Path, language: str) -> dict[str, str]:
    """Hash only dependency declarations, never the ambient host cache."""

    source = source.resolve(strict=True)
    names = _manifest_names(language)
    rows: dict[str, str] = {}
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(source).as_posix()
        if path.name not in names:
            continue
        if language == "rust" and path.name in {"config", "config.toml"}:
            if "/.cargo/" not in f"/{relative}" and not relative.startswith(".cargo/"):
                continue
        rows[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    required = "go.mod" if language == "go" else "Cargo.toml"
    if not any(path == required or path.endswith("/" + required) for path in rows):
        raise ValueError(f"source snapshot has no {required}")
    return rows


def dependency_scope(repository: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    language = str(repository.get("language") or "")
    source = Path(str(repository.get("source_dir") or "")).resolve(strict=True)
    knobs = {
        key: repository[key]
        for key in sorted(repository)
        if key.startswith("go_") or key.startswith("rust_")
        if key not in {"go_mod_cache", "cargo_home"}
    }
    payload = {
        "schema": SCHEMA,
        "instance_id": str(repository.get("instance_id") or ""),
        "language": language,
        "commit": str(repository.get("commit") or ""),
        "source_tree_sha256": str(repository.get("source_tree_sha256") or ""),
        "runtime_image_id": str(repository.get("runtime_image_id") or ""),
        "dependency_inputs": dependency_input_files(source, language),
        "dependency_knobs": knobs,
    }
    if language == "go":
        payload["go_test_install_dependencies"] = resolve_go_prefetch_install_specs(repository)
    return sha256_json(payload), payload


def dependency_cache_sha256(root: Path) -> str:
    """Hash a cache without dereferencing links; reject links escaping the cache."""

    root = root.resolve(strict=True)
    digest = hashlib.sha256()
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        base = Path(directory)
        names[:] = sorted(names)
        for name in [*names, *sorted(files)]:
            path = base / name
            relative = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(oct(stat.S_IMODE(mode)).encode("ascii"))
            digest.update(b"\0")
            if stat.S_ISLNK(mode):
                target = os.readlink(path)
                if os.path.isabs(target):
                    raise ValueError(f"dependency cache contains absolute symlink: {relative}")
                resolved = (path.parent / target).resolve(strict=False)
                if root not in (resolved, *resolved.parents):
                    raise ValueError(f"dependency cache symlink escapes cache: {relative}")
                digest.update(b"link\0" + target.encode("utf-8") + b"\0")
            elif stat.S_ISREG(mode):
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                digest.update(b"\0")
            elif stat.S_ISDIR(mode):
                digest.update(b"dir\0")
            else:
                raise ValueError(f"dependency cache contains unsafe node: {relative}")
    return digest.hexdigest()


def inspect_dependency_cache(current: Path) -> dict[str, Any]:
    """Validate a published cache against its own immutable manifest."""

    manifest_path = current / MANIFEST_NAME
    cache = current / "cache"
    if not manifest_path.is_file() or not cache.is_dir():
        raise ValueError("dependency cache is incomplete")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA:
        raise ValueError("dependency cache schema mismatch")
    scope = manifest.get("dependency_scope")
    if not isinstance(scope, dict):
        raise ValueError("dependency cache has no scope payload")
    if manifest.get("dependency_scope_sha256") != sha256_json(scope):
        raise ValueError("dependency cache scope payload hash mismatch")
    actual = dependency_cache_sha256(cache)
    if manifest.get("cache_sha256") != actual:
        raise ValueError("dependency cache content hash mismatch")
    return {**manifest, "cache_dir": str(cache), "manifest_path": str(manifest_path)}


class DependencyCacheLock:
    """Exclusive per-repository publisher lock with dead-owner recovery."""

    def __init__(self, dependencies: Path) -> None:
        self.path = dependencies / "publisher.lock"
        self.token = uuid.uuid4().hex
        self.acquired = False

    def __enter__(self) -> "DependencyCacheLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                descriptor = os.open(
                    self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
            except FileExistsError:
                try:
                    lock_age = max(0.0, time.time() - self.path.stat().st_mtime)
                except FileNotFoundError:
                    continue
                try:
                    owner = json.loads(self.path.read_text(encoding="utf-8"))
                    pid = int(owner.get("pid") or 0)
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    pid = 0
                if pid <= 0 and lock_age < 60:
                    # Another publisher may be between O_EXCL creation and
                    # its fsynced owner record. Never steal a fresh lock.
                    raise RuntimeError("dependency cache publisher lock is initializing")
                alive = False
                if pid > 0:
                    try:
                        os.kill(pid, 0)
                        alive = True
                    except ProcessLookupError:
                        pass
                    except PermissionError:
                        alive = True
                if alive:
                    raise RuntimeError(f"dependency cache publisher is active: pid={pid}")
                stale = self.path.with_name(
                    f"publisher.lock.stale.{pid}.{uuid.uuid4().hex}"
                )
                try:
                    self.path.replace(stale)
                except FileNotFoundError:
                    pass
                continue
            try:
                os.write(
                    descriptor,
                    json.dumps(
                        {"pid": os.getpid(), "token": self.token}, sort_keys=True
                    ).encode("utf-8"),
                )
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self.acquired = True
            return self
        raise RuntimeError("could not acquire dependency cache publisher lock")

    def __exit__(self, exc_type, exc, traceback) -> None:
        if not self.acquired:
            return
        try:
            try:
                owner = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return
            if owner.get("token") == self.token:
                self.path.unlink(missing_ok=True)
        finally:
            self.acquired = False


def quarantine_stale_dependency_cache(
    repo_root: Path, repository: dict[str, Any]
) -> Path | None:
    """Move a valid old-scope cache aside; never quarantine corrupt content."""

    dependencies = repo_root / "dependencies"
    current = dependencies / "current"
    if not current.exists():
        return None
    manifest = inspect_dependency_cache(current)
    expected_scope, expected_payload = dependency_scope(repository)
    if (
        manifest.get("dependency_scope_sha256") == expected_scope
        and manifest.get("dependency_scope") == expected_payload
    ):
        return None
    quarantine = dependencies / "quarantine"
    quarantine.mkdir(parents=True, exist_ok=True)
    stem = (
        f"{manifest['dependency_scope_sha256']}-"
        f"{manifest['cache_sha256'][:12]}"
    )
    destination = quarantine / stem
    sequence = 0
    while destination.exists():
        sequence += 1
        destination = quarantine / f"{stem}.{sequence}"
    current.replace(destination)
    return destination


def validate_dependency_cache(repo_root: Path, repository: dict[str, Any]) -> dict[str, Any]:
    current = repo_root / "dependencies" / "current"
    manifest = inspect_dependency_cache(current)
    expected_scope, expected_payload = dependency_scope(repository)
    if manifest.get("dependency_scope_sha256") != expected_scope:
        raise ValueError("dependency cache scope mismatch")
    if manifest.get("dependency_scope") != expected_payload:
        raise ValueError("dependency cache scope payload mismatch")
    return manifest
