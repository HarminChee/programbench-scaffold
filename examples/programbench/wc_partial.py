#!/usr/bin/env python3
"""Small partial reimplementation used to demo ProgramBench-style rewards."""

from __future__ import annotations

import sys
from pathlib import Path


USAGE = "usage: wc [-Lclmw] [file ...]\n"


def counts(data: bytes) -> tuple[int, int, int, int, int]:
    text = data.decode("utf-8", errors="replace")
    lines = text.count("\n")
    words = len(text.split())
    bytes_count = len(data)
    chars = len(text)
    max_line = max((len(line.rstrip("\n")) for line in text.splitlines(True)), default=0)
    return lines, words, bytes_count, chars, max_line


def format_counts(values: list[int], name: str | None) -> str:
    fields = "".join(f"{value:8d}" for value in values)
    return f"{fields} {name}\n" if name else f"{fields}\n"


def selected_values(all_counts: tuple[int, int, int, int, int], flags: set[str]) -> list[int]:
    line_count, word_count, byte_count, char_count, max_line = all_counts
    if not flags:
        return [line_count, word_count, byte_count]
    values = []
    for flag in ["l", "w", "c", "m", "L"]:
        if flag not in flags:
            continue
        values.append(
            {
                "l": line_count,
                "w": word_count,
                "c": byte_count,
                "m": char_count,
                "L": max_line,
            }[flag]
        )
    return values


def parse_args(argv: list[str]) -> tuple[set[str], list[str], str | None]:
    flags: set[str] = set()
    files: list[str] = []
    for arg in argv:
        if arg == "--version":
            return flags, files, "version"
        if arg.startswith("--"):
            return flags, files, "invalid_long"
        if arg.startswith("-") and arg != "-":
            for char in arg[1:]:
                if char not in "Lclmw":
                    return flags, files, "invalid_short"
                flags.add(char)
        else:
            files.append(arg)
    return flags, files, None


def main(argv: list[str]) -> int:
    flags, files, error = parse_args(argv)
    if error:
        sys.stderr.write(f"{sys.argv[0]}: illegal option -- -\n{USAGE}")
        return 1

    if not files:
        data = sys.stdin.buffer.read()
        sys.stdout.write(format_counts(selected_values(counts(data), flags), None))
        return 0

    status = 0
    for name in files:
        try:
            data = Path(name).read_bytes()
        except OSError:
            sys.stderr.write(f"wc: {name}: open: No such file or directory\n")
            status = 1
            continue
        sys.stdout.write(format_counts(selected_values(counts(data), flags), name))
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
