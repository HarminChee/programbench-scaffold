from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any


def source_tree_sha256(root: Path) -> str:
    """Hash a pinned source snapshot and fail closed on unsafe filesystem nodes."""

    root = root.resolve(strict=True)
    digest = hashlib.sha256()
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        base = Path(directory)
        names[:] = sorted(name for name in names if name != ".git")
        for name in list(names):
            path = base / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError(f"source tree contains a symlink: {path}")
            if not stat.S_ISDIR(mode):
                raise ValueError(f"source tree contains a non-directory node: {path}")
        for name in sorted(files):
            path = base / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError(f"source tree contains a symlink: {path}")
            if not stat.S_ISREG(mode):
                raise ValueError(f"source tree contains a non-regular file: {path}")
            relative = path.relative_to(root).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(oct(stat.S_IMODE(mode)).encode("ascii"))
            digest.update(b"\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
    return digest.hexdigest()


def validate_repository_scope(repository: dict[str, Any]) -> dict[str, str]:
    commit = str(repository.get("commit") or "")
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit.lower()):
        raise ValueError("repository commit must be a full 40-character SHA")
    expected = str(repository.get("source_tree_sha256") or "")
    if len(expected) != 64:
        raise ValueError("repository source_tree_sha256 is missing")
    source_dir = Path(str(repository.get("source_dir") or "")).resolve(strict=True)
    actual = source_tree_sha256(source_dir)
    if actual != expected:
        raise ValueError(
            f"source tree hash mismatch: expected={expected} actual={actual}"
        )
    image = str(repository.get("runtime_image_id") or "")
    if not image.startswith("sha256:") or len(image) != 71:
        raise ValueError("runtime_image_id must be an immutable sha256 image ID")
    return {
        "commit": commit,
        "source_dir": str(source_dir),
        "source_tree_sha256": actual,
        "runtime_image_id": image,
    }
