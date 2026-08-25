#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path


ALLOWED_NAMES = {"README.md", "README", "go.mod", "go.sum", "Cargo.toml", "Cargo.lock"}
ALLOWED_SUFFIXES = {".go", ".rs", ".md", ".txt"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, default=220_000)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    selected: list[Path] = []
    for root, directories, files in os.walk(source, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in {".git", "vendor", "target"})
        for name in sorted(files):
            path = Path(root) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise ValueError(f"unsafe source entry: {path}")
            relative = path.relative_to(source)
            if name in ALLOWED_NAMES or path.suffix in ALLOWED_SUFFIXES:
                selected.append(relative)
    selected.sort(key=lambda path: ("test" not in path.name.lower(), len(path.parts), path.as_posix()))
    parts = ["# Audited pinned source, documentation, and native-test context\n"]
    used = sum(len(part.encode("utf-8")) for part in parts)
    for relative in selected:
        raw = (source / relative).read_bytes()
        if b"\x00" in raw:
            continue
        text = raw.decode("utf-8", "replace")
        section = f"\n## {relative.as_posix()}\n```\n{text}\n```\n"
        size = len(section.encode("utf-8"))
        if used + size > args.max_bytes:
            continue
        parts.append(section)
        used += size
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(parts), encoding="utf-8")
    print(f"files={len(parts)-1} bytes={used}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
