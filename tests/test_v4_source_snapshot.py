from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from v4.tools.prepare_external20_sources import extract_snapshot, snapshot_content_sha256


def archive(path: Path, entries: list[tuple[str, str, bytes | str]]) -> None:
    with tarfile.open(path, "w") as value:
        for kind, name, payload in entries:
            item = tarfile.TarInfo(name)
            if kind == "file":
                data = bytes(payload)
                item.size = len(data)
                item.mode = 0o644
                value.addfile(item, io.BytesIO(data))
            elif kind == "symlink":
                item.type = tarfile.SYMTYPE
                item.linkname = str(payload)
                value.addfile(item)


def test_internal_symlink_is_materialized_as_regular_file(tmp_path: Path) -> None:
    tar = tmp_path / "source.tar"
    archive(tar, [("file", "data/value.txt", b"ok"), ("symlink", "alias.txt", "data/value.txt")])
    output = tmp_path / "out"
    output.mkdir()
    rows = extract_snapshot(tar, output)
    assert (output / "alias.txt").is_file()
    assert not (output / "alias.txt").is_symlink()
    assert (output / "alias.txt").read_text() == "ok"
    assert rows == [{"path": "alias.txt", "target": "data/value.txt"}]


def test_external_symlink_fails_closed(tmp_path: Path) -> None:
    tar = tmp_path / "source.tar"
    archive(tar, [("symlink", "escape", "../../outside")])
    output = tmp_path / "out"
    output.mkdir()
    with pytest.raises(RuntimeError, match="unresolved|escapes"):
        extract_snapshot(tar, output)


def test_snapshot_content_hash_ignores_only_its_scope_manifest(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("value")
    first = snapshot_content_sha256(tmp_path)
    (tmp_path / ".programbench_v4_snapshot.json").write_text("{}")
    assert snapshot_content_sha256(tmp_path) == first
    (tmp_path / "source.txt").write_text("changed")
    assert snapshot_content_sha256(tmp_path) != first
