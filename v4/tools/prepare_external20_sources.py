#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from v4.programbench_v4.io import atomic_write_json
from v4.programbench_v4.provenance import source_tree_sha256


def _safe_name(name: str) -> PurePosixPath:
    value = PurePosixPath(name)
    if value.is_absolute() or not value.parts or ".." in value.parts:
        raise RuntimeError(f"unsafe archive path: {name!r}")
    return value


def _git(repo: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def ensure_commit(repo: Path, commit: str) -> None:
    probe = _git(repo, "cat-file", "-e", f"{commit}^{{commit}}")
    if probe.returncode == 0:
        return
    errors: list[str] = []
    for delay in (0, 3, 7, 15):
        if delay:
            time.sleep(delay)
        try:
            fetch = _git(repo, "fetch", "--no-tags", "origin", commit, timeout=900)
        except subprocess.TimeoutExpired:
            errors.append("fetch timed out after 900 seconds")
            continue
        if fetch.returncode == 0 and _git(repo, "cat-file", "-e", f"{commit}^{{commit}}").returncode == 0:
            return
        errors.append(fetch.stderr[-1000:])
    raise RuntimeError(f"cannot fetch pinned commit {commit}: {' | '.join(errors)}")


def archive_commit(repo: Path, commit: str, tar_path: Path) -> None:
    with tar_path.open("wb") as output:
        result = subprocess.run(
            ["git", "-C", str(repo), "archive", "--format=tar", commit],
            stdout=output,
            stderr=subprocess.PIPE,
            timeout=600,
        )
    if result.returncode != 0:
        raise RuntimeError(f"git archive failed: {result.stderr.decode(errors='replace')[-2000:]}")


def extract_snapshot(
    tar_path: Path,
    destination: Path,
    *,
    omit_symlinks: frozenset[str] = frozenset(),
    omitted: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    symlinks: list[tuple[Path, str]] = []
    with tarfile.open(tar_path, "r:") as archive:
        members = archive.getmembers()
        for member in members:
            relative = _safe_name(member.name)
            target = destination.joinpath(*relative.parts)
            resolved_parent = target.parent.resolve()
            if destination.resolve() not in (resolved_parent, *resolved_parent.parents):
                raise RuntimeError(f"archive parent escapes destination: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isreg():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise RuntimeError(f"archive member has no payload: {member.name}")
                with target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)
            elif member.issym():
                normalized = relative.as_posix()
                if normalized in omit_symlinks:
                    if omitted is not None:
                        omitted.append({"path": normalized, "target": member.linkname})
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                symlinks.append((target, member.linkname))
            else:
                raise RuntimeError(f"archive contains unsupported node: {member.name}")

    materialized: list[dict[str, str]] = []
    pending = list(symlinks)
    for _ in range(len(pending) + 1):
        if not pending:
            break
        retry: list[tuple[Path, str]] = []
        progressed = False
        for path, linkname in pending:
            link = PurePosixPath(linkname)
            if link.is_absolute():
                raise RuntimeError(f"absolute symlink is forbidden: {path} -> {linkname}")
            unresolved = path.parent.joinpath(*link.parts)
            try:
                target = unresolved.resolve(strict=True)
            except FileNotFoundError:
                retry.append((path, linkname))
                continue
            root = destination.resolve()
            if root not in (target, *target.parents):
                raise RuntimeError(f"symlink escapes snapshot: {path} -> {linkname}")
            if target.is_file():
                shutil.copy2(target, path)
            elif target.is_dir():
                shutil.copytree(target, path)
            else:
                raise RuntimeError(f"symlink target is not regular: {path} -> {linkname}")
            materialized.append(
                {"path": path.relative_to(destination).as_posix(), "target": linkname}
            )
            progressed = True
        if retry and not progressed:
            raise RuntimeError(
                "unresolved or cyclic symlinks: "
                + ", ".join(f"{p.relative_to(destination)}->{v}" for p, v in retry)
            )
        pending = retry

    for base, names, files in os.walk(destination, followlinks=False):
        for name in [*names, *files]:
            path = Path(base) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise RuntimeError(f"unsafe node remains after materialization: {path}")
    return materialized


def snapshot_content_sha256(root: Path) -> str:
    root = root.resolve(strict=True)
    digest = hashlib.sha256()
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        base = Path(directory)
        names[:] = sorted(name for name in names if name != ".git")
        for name in sorted(files):
            path = base / name
            relative = path.relative_to(root).as_posix()
            if relative == ".programbench_v4_snapshot.json":
                continue
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode):
                raise RuntimeError(f"snapshot contains unsafe file: {path}")
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(oct(stat.S_IMODE(mode)).encode("ascii"))
            digest.update(b"\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
    return digest.hexdigest()


def prepare(item: dict[str, Any], output_root: Path) -> dict[str, Any]:
    declared_source = Path(str(item.get("source_repo") or "")).expanduser()
    source_repo = declared_source.resolve(strict=True) if declared_source.exists() else declared_source
    commit = str(item["commit"])
    if not source_repo.exists() or _git(source_repo, "rev-parse", "--git-dir").returncode != 0:
        repository = str(item.get("repository") or "")
        if not repository:
            raise RuntimeError(f"source is not a Git repository and no remote is declared: {source_repo}")
        mirror = output_root.parent / "git-mirrors" / str(item["instance_id"])
        if mirror.exists() and _git(mirror, "rev-parse", "--git-dir").returncode != 0:
            shutil.rmtree(mirror)
        if not mirror.exists():
            mirror.parent.mkdir(parents=True, exist_ok=True)
            errors: list[str] = []
            for delay in (0, 3, 7, 15):
                if delay:
                    time.sleep(delay)
                if mirror.exists():
                    shutil.rmtree(mirror)
                try:
                    clone = subprocess.run(
                        ["git", "clone", "--no-checkout", "--filter=blob:none", f"https://github.com/{repository}.git", str(mirror)],
                        text=True,
                        capture_output=True,
                        timeout=600,
                    )
                except subprocess.TimeoutExpired:
                    errors.append("clone timed out after 600 seconds")
                    continue
                if clone.returncode == 0:
                    break
                errors.append(clone.stderr[-1000:])
            else:
                raise RuntimeError(f"clean mirror clone failed: {' | '.join(errors)}")
        source_repo = mirror.resolve(strict=True)
    ensure_commit(source_repo, commit)
    instance = str(item["instance_id"])
    final = output_root / instance
    if final.exists():
        manifest_path = final / ".programbench_v4_snapshot.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            actual = snapshot_content_sha256(final)
            if manifest.get("commit") == commit and manifest.get("archive_content_sha256") == actual:
                return {
                    **manifest,
                    "final_source_tree_sha256": source_tree_sha256(final),
                    "source_dir": str(final),
                    "resumed": True,
                }
        raise RuntimeError(f"existing snapshot is not scope-valid: {final}")

    output_root.mkdir(parents=True, exist_ok=True)
    stage = output_root / f".stage-{instance}-{os.getpid()}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    try:
        with tempfile.NamedTemporaryFile(dir=output_root, suffix=".tar", delete=False) as handle:
            tar_path = Path(handle.name)
        try:
            archive_commit(source_repo, commit, tar_path)
            omitted: list[dict[str, str]] = []
            materialized = extract_snapshot(
                tar_path,
                stage,
                omit_symlinks=frozenset(str(value) for value in item.get("omit_unsafe_symlink_paths") or []),
                omitted=omitted,
            )
            declared_omissions = {
                str(value) for value in item.get("omit_unsafe_symlink_paths") or []
            }
            observed_omissions = {row["path"] for row in omitted}
            if declared_omissions != observed_omissions:
                raise RuntimeError(
                    "declared symlink omission scope mismatch: "
                    f"missing={sorted(declared_omissions - observed_omissions)} "
                    f"unexpected={sorted(observed_omissions - declared_omissions)}"
                )
        finally:
            tar_path.unlink(missing_ok=True)
        content_hash = source_tree_sha256(stage)
        manifest = {
            "schema": "programbench_v4_source_snapshot_v1",
            "instance_id": instance,
            "commit": commit,
            "source_repository": str(source_repo),
            "archive_content_sha256": content_hash,
            "materialized_internal_symlinks": materialized,
            "omitted_declared_unsafe_symlinks": omitted,
        }
        atomic_write_json(stage / ".programbench_v4_snapshot.json", manifest)
        # The manifest contains the pre-manifest archive hash. The campaign
        # scope is computed after publication and includes the exact manifest
        # bytes, avoiding a circular self-hash.
        stage.replace(final)
        actual = source_tree_sha256(final)
        return {**manifest, "final_source_tree_sha256": actual, "source_dir": str(final), "resumed": False}
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.workers <= 10:
        raise SystemExit("--workers must be in 1..10")
    value = json.loads(args.manifest.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    failed = False
    items = value.get("repositories") or []

    def run(item: dict[str, Any]) -> dict[str, Any]:
        try:
            return {"instance_id": item["instance_id"], "state": "completed", **prepare(item, args.output_root)}
        except Exception as exc:
            return {"instance_id": item.get("instance_id"), "state": "failed", "error": str(exc)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_rows = [executor.submit(run, item) for item in items]
        for future in future_rows:
            row = future.result()
            failed = failed or row.get("state") != "completed"
            rows.append(row)
    atomic_write_json(
        args.summary,
        {"schema": "programbench_v4_source_preparation_v1", "repositories": rows},
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
