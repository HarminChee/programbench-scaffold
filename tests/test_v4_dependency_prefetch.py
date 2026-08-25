from __future__ import annotations

from pathlib import Path
import json
import os
import shutil
import subprocess
import zipfile

import pytest

from v4.adapters import gofumpt_pilot_adapter as adapter
from v4.programbench_v4.dependencies import (
    MANIFEST_NAME,
    SCHEMA,
    DependencyCacheLock,
    dependency_cache_sha256,
    dependency_scope,
    discover_go_test_install_specs,
    go_offline_proxy_environment,
    resolve_go_prefetch_install_specs,
    validate_go_install_spec,
    validate_dependency_cache,
)
from v4.programbench_v4.io import atomic_write_json
from v4.programbench_v4.isolation import Mount
from v4.programbench_v4.isolation import ContainerContract
from v4.programbench_v4.provenance import source_tree_sha256


IMAGE = "sha256:" + "a" * 64


def test_dependency_prefetch_is_the_only_networked_dependency_stage(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    command = ContainerContract(
        image_id=IMAGE,
        stage="dependency_prefetch",
        name="dependency-prefetch-test",
        mounts=(Mount(source, "/source"),),
    ).docker_run(["-c", "true"])
    assert command[command.index("--network") + 1] == "bridge"
    assert "--read-only" in command
    assert "/var/run/docker.sock" not in " ".join(command)


def repository(tmp_path: Path, *, language: str = "go") -> dict:
    source = tmp_path / "source"
    source.mkdir()
    if language == "go":
        (source / "go.mod").write_text("module example.test/pilot\n\ngo 1.22\n")
    else:
        (source / "Cargo.toml").write_text(
            '[package]\nname="pilot"\nversion="0.1.0"\n'
        )
        (source / "Cargo.lock").write_text("# lock\n")
    toolchain = tmp_path / "toolchain"
    toolchain.mkdir()
    return {
        "instance_id": "pilot",
        "language": language,
        "commit": "b" * 40,
        "source_dir": str(source),
        "source_tree_sha256": source_tree_sha256(source),
        "runtime_image_id": IMAGE,
        "go_toolchain_root": str(toolchain),
        "rust_toolchain_root": str(toolchain),
        "rust_target_triple": "x86_64-unknown-linux-gnu",
    }


def publish_cache(repo_root: Path, repo: dict) -> None:
    current = repo_root / "dependencies/current"
    cache = current / "cache"
    subdir = cache / ("gomod" if repo["language"] == "go" else "cargo")
    subdir.mkdir(parents=True)
    (subdir / "dependency.txt").write_text("cached")
    scope_sha, scope = dependency_scope(repo)
    atomic_write_json(
        current / MANIFEST_NAME,
        {
            "schema": SCHEMA,
            "dependency_scope_sha256": scope_sha,
            "dependency_scope": scope,
            "cache_sha256": dependency_cache_sha256(cache),
            "network": "bridge",
        },
    )


def test_dependency_scope_tracks_manifests_not_unrelated_files(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    first, _ = dependency_scope(repo)
    source = Path(repo["source_dir"])
    (source / "README.md").write_text("unrelated")
    # The source snapshot itself is separately pinned by source_tree_sha256;
    # the dependency scope is specifically driven by dependency declarations.
    second, _ = dependency_scope(repo)
    assert second == first
    (source / "go.sum").write_text("example.test/module v1.0.0 h1:value\n")
    third, _ = dependency_scope(repo)
    assert third != first


def test_dependency_cache_validation_fails_closed_on_tamper(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo_root = tmp_path / "output"
    publish_cache(repo_root, repo)
    assert validate_dependency_cache(repo_root, repo)["network"] == "bridge"
    cache_file = repo_root / "dependencies/current/cache/gomod/dependency.txt"
    cache_file.write_text("tampered")
    with pytest.raises(ValueError, match="content hash"):
        validate_dependency_cache(repo_root, repo)


def test_prefetch_publishes_atomically_and_resumes(monkeypatch, tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo_root = tmp_path / "output"
    calls: list[str] = []

    def fake_run_container(*, mounts: tuple[Mount, ...], stage: str, **kwargs):
        assert stage == "dependency_prefetch"
        calls.append(stage)
        output = next(mount.source for mount in mounts if mount.target == "/out")
        (output / "gomod").mkdir()
        (output / "gomod/module.txt").write_text("fetched")
        return ["docker", "run", "--network", "bridge", IMAGE]

    monkeypatch.setattr(adapter, "run_container", fake_run_container)
    request = {"repository": repo}
    first = adapter.dependency_prefetch(request, repo_root)
    assert first["dependency_cache"]["resumed"] is False
    assert not list((repo_root / "dependencies").glob(".stage-*"))
    second = adapter.dependency_prefetch(request, repo_root)
    assert second["dependency_cache"]["resumed"] is True
    assert calls == ["dependency_prefetch"]


def test_prefetch_executes_only_discovered_allowlisted_pinned_tools(
    monkeypatch, tmp_path: Path
) -> None:
    repo = repository(tmp_path)
    script_file = Path(repo["source_dir"]) / "testdata/tool.sh"
    script_file.parent.mkdir(parents=True)
    script_file.write_text("go install example.test/tool@v1.2.3\n")
    repo["go_prefetch_install_specs"] = ["example.test/tool@v1.2.3"]
    repo_root = tmp_path / "output"
    scripts: list[str] = []

    def fake_run_container(*, mounts: tuple[Mount, ...], stage: str, script: str, **kwargs):
        assert stage == "dependency_prefetch"
        scripts.append(script)
        output = next(mount.source for mount in mounts if mount.target == "/out")
        (output / "gomod").mkdir()
        return ["docker", "run", "--network", "bridge", IMAGE]

    monkeypatch.setattr(adapter, "run_container", fake_run_container)
    adapter.dependency_prefetch({"repository": repo}, repo_root)
    assert scripts and (
        "GOBIN=/workspace/prefetch-bin go install example.test/tool@v1.2.3"
        in scripts[0]
    )
    assert "@latest" not in scripts[0]


def test_old_scope_cache_is_quarantined_before_atomic_republication(
    monkeypatch, tmp_path: Path
) -> None:
    old_repo = repository(tmp_path)
    repo_root = tmp_path / "output"
    publish_cache(repo_root, old_repo)
    old_payload = (
        repo_root / "dependencies/current/cache/gomod/dependency.txt"
    ).read_text()
    new_repo = {**old_repo, "go_build_package": "./cmd/new-scope"}
    calls: list[str] = []

    def fake_run_container(*, mounts: tuple[Mount, ...], stage: str, **kwargs):
        calls.append(stage)
        output = next(mount.source for mount in mounts if mount.target == "/out")
        (output / "gomod").mkdir()
        (output / "gomod/new.txt").write_text("new-cache")
        return ["docker", "run", "--network", "bridge", IMAGE]

    monkeypatch.setattr(adapter, "run_container", fake_run_container)
    result = adapter.dependency_prefetch({"repository": new_repo}, repo_root)
    quarantined = Path(result["dependency_cache"]["quarantined_previous"])
    assert quarantined.is_dir()
    assert (quarantined / "cache/gomod/dependency.txt").read_text() == old_payload
    assert validate_dependency_cache(repo_root, new_repo)["cache_sha256"]
    assert calls == ["dependency_prefetch"]


def test_corrupt_cache_fails_closed_instead_of_being_quarantined(
    monkeypatch, tmp_path: Path
) -> None:
    repo = repository(tmp_path)
    repo_root = tmp_path / "output"
    publish_cache(repo_root, repo)
    (repo_root / "dependencies/current/cache/gomod/dependency.txt").write_text("bad")
    monkeypatch.setattr(
        adapter,
        "run_container",
        lambda **kwargs: pytest.fail("corrupt cache must not be replaced"),
    )
    with pytest.raises(ValueError, match="content hash"):
        adapter.dependency_prefetch({"repository": repo}, repo_root)
    assert (repo_root / "dependencies/current").is_dir()
    assert not (repo_root / "dependencies/quarantine").exists()


def test_dependency_publisher_lock_refuses_live_owner(tmp_path: Path) -> None:
    dependencies = tmp_path / "dependencies"
    with DependencyCacheLock(dependencies):
        with pytest.raises(RuntimeError, match="publisher is active"):
            with DependencyCacheLock(dependencies):
                pytest.fail("second publisher acquired the same cache")
    assert not (dependencies / "publisher.lock").exists()


def test_rust_git_dependency_cache_is_part_of_content_hash(tmp_path: Path) -> None:
    repo = repository(tmp_path, language="rust")
    repo_root = tmp_path / "output"
    publish_cache(repo_root, repo)
    checkout = repo_root / "dependencies/current/cache/cargo/git/checkouts/repo/src"
    checkout.mkdir(parents=True)
    (checkout / "lib.rs").write_text("pub fn value() {}\n")
    # Publishing a file after the manifest must invalidate the snapshot.
    with pytest.raises(ValueError, match="content hash"):
        validate_dependency_cache(repo_root, repo)


def test_discovers_and_resolves_testscript_go_install_version(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    script = Path(repo["source_dir"]) / "testdata/script/diagnose.txtar"
    script.parent.mkdir(parents=True)
    script.write_text(
        "env TOOL_VERSION=v1.2.3-0.20250102030405-abcdef123456\n"
        "go install example.test/tool@${TOOL_VERSION}\n"
        "go install .\n"
    )
    rows = discover_go_test_install_specs(Path(repo["source_dir"]))
    assert [row["spec"] for row in rows] == [
        "example.test/tool@v1.2.3-0.20250102030405-abcdef123456"
    ]
    repo["go_prefetch_install_specs"] = [rows[0]["spec"]]
    assert resolve_go_prefetch_install_specs(repo)["specs"] == [rows[0]["spec"]]


@pytest.mark.parametrize(
    "spec",
    ["example.test/tool@latest", "example.test/tool@main", "./tool@v1.2.3", "tool"],
)
def test_go_install_allowlist_rejects_unpinned_or_local_specs(spec: str) -> None:
    with pytest.raises(ValueError):
        validate_go_install_spec(spec)


def test_discovered_go_install_must_be_explicitly_allowlisted(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    script = Path(repo["source_dir"]) / "testdata/tool.sh"
    script.parent.mkdir(parents=True)
    script.write_text("go install example.test/tool@v1.2.3\n")
    with pytest.raises(ValueError, match="allowlist mismatch"):
        resolve_go_prefetch_install_specs(repo)


def test_readme_latest_example_is_outside_native_test_dependency_scope(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    (Path(repo["source_dir"]) / "README.md").write_text(
        "go install example.test/tool@latest\n"
    )
    assert discover_go_test_install_specs(Path(repo["source_dir"])) == []


def test_offline_proxy_environment_has_no_network_fallback() -> None:
    environment = go_offline_proxy_environment()
    assert environment == {
        "GOMODCACHE": "/workspace/gomod",
        "GOPROXY": "file:///workspace/gomod/cache/download",
        "GOSUMDB": "off",
        "GONOSUMDB": "*",
        "GONOPROXY": "none",
        "GOVCS": "*:off",
        "GOENV": "off",
    }
    with pytest.raises(ValueError, match="unsafe"):
        go_offline_proxy_environment("/workspace/../host")


def test_pinned_go_install_works_from_local_download_cache_without_network(
    tmp_path: Path,
) -> None:
    go = shutil.which("go")
    if go is None and Path("/usr/local/go1.26.5/bin/go").is_file():
        go = "/usr/local/go1.26.5/bin/go"
    if go is None:
        pytest.skip("Go toolchain is unavailable")

    module = "example.test/offline-tool"
    version = "v1.2.3"
    proxy_version = tmp_path / "proxy" / module / "@v"
    proxy_version.mkdir(parents=True)
    (proxy_version / "list").write_text(version + "\n")
    (proxy_version / f"{version}.info").write_text(
        json.dumps({"Version": version, "Time": "2025-01-01T00:00:00Z"})
    )
    (proxy_version / f"{version}.mod").write_text(
        f"module {module}\n\ngo 1.22\n"
    )
    with zipfile.ZipFile(proxy_version / f"{version}.zip", "w") as archive:
        prefix = f"{module}@{version}/"
        archive.writestr(prefix + "go.mod", f"module {module}\n\ngo 1.22\n")
        archive.writestr(prefix + "main.go", "package main\nfunc main() {}\n")

    gomod = tmp_path / "gomod"
    # The copied cache's `cache/download` directory is itself a file proxy.
    shutil.copytree(tmp_path / "proxy", gomod / "cache/download")
    environment = {
        **os.environ,
        **go_offline_proxy_environment(str(gomod)),
        "GOCACHE": str(tmp_path / "gocache"),
        "GOBIN": str(tmp_path / "bin"),
        "HOME": str(tmp_path / "home"),
        "GO111MODULE": "on",
    }
    result = subprocess.run(
        [go, "install", f"{module}@{version}"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "bin/offline-tool").is_file()


def test_go_preflight_uses_local_file_proxy_in_offline_container(
    monkeypatch, tmp_path: Path
) -> None:
    repo = repository(tmp_path)
    repo.update(
        {
            "runtime_image_reference": "programbench/runtime:pinned",
            "go_build_package": ".",
            "go_native_test_command": "go test ./... -count=1",
            "go_native_coverage_package": "./...",
            "go_cover_package": "./...",
        }
    )
    repo_root = tmp_path / "output"
    publish_cache(repo_root, repo)
    scripts: list[str] = []

    def fake_run_container(
        *, mounts: tuple[Mount, ...], stage: str, script: str, **kwargs
    ) -> list[str]:
        assert stage == "source_build"
        scripts.append(script)
        output = next(mount.source for mount in mounts if mount.target == "/out")
        (output / "reference_executable").write_bytes(b"reference")
        (output / "coverage_executable").write_bytes(b"coverage")
        (output / "native.coverage.txt").write_text("total: 100.0%\n")
        return ["docker", "run", "--network", "none", IMAGE]

    monkeypatch.setattr(adapter, "run_container", fake_run_container)
    monkeypatch.setattr(adapter, "cleanroom_image", lambda *args, **kwargs: IMAGE)
    adapter.preflight({"repository": repo}, repo_root)
    assert len(scripts) == 1
    script = scripts[0]
    assert "GOPROXY=file:///workspace/gomod/cache/download" in script
    assert "GOPROXY=off" not in script
    assert "GOSUMDB=off" in script
    assert "GONOPROXY=none" in script
    assert "GOVCS='*:off'" in script or "GOVCS=*:off" in script
    assert "GOENV=off" in script
