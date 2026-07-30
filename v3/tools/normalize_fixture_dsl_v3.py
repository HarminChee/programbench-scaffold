#!/usr/bin/env python3
"""Normalize permissive agent-authored V3 fixtures for the generic capture runtime."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit


URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def public_network_urls(case: dict) -> list[str]:
    values = [str(value) for value in case.get("args") or []]
    if isinstance(case.get("env"), dict):
        values.extend(str(value) for value in case["env"].values())
    rejected = []
    for value in values:
        for url in URL_RE.findall(value):
            if "{http_url}" in url or "{http_host}" in url:
                continue
            try:
                host = (urlsplit(url).hostname or "").lower()
            except ValueError:
                # Malformed URL-like agent output must be rejected as a case,
                # not allowed to crash normalization for the whole suite.
                rejected.append(url)
                continue
            if host not in {"127.0.0.1", "localhost", "::1"}:
                rejected.append(url)
    return rejected


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    repairs = []
    rejected = []
    retained = []
    for index, case in enumerate(data.get("cases") or []):
        external = public_network_urls(case)
        if external:
            rejected.append({
                "index": index,
                "name": case.get("name"),
                "reason": "public_network_url_without_loopback_fixture",
                "urls": external,
            })
            continue
        terminal = case.get("terminal")
        if (
            isinstance(terminal, dict)
            and terminal
            and "stdin_mode" not in terminal
            and isinstance(case.get("stdin"), str)
            and bool(case.get("stdin"))
            and not terminal.get("input_events")
        ):
            terminal["stdin_mode"] = "pipe"
            repairs.append({
                "index": index,
                "field": "terminal.stdin_mode",
                "reason": "nonempty_stdin_with_tty_output_uses_pipe",
            })
        repeated = case.get("repeat_files")
        if isinstance(repeated, dict):
            fixed = {}
            for path, spec in repeated.items():
                if isinstance(spec, dict):
                    fixed[path] = spec
                elif isinstance(spec, int):
                    fixed[path] = {"row": "x", "count": max(0, spec)}
                    repairs.append({"index": index, "field": f"repeat_files.{path}", "reason": "integer_to_repeat_spec"})
                else:
                    fixed[path] = {"row": str(spec), "count": 1}
                    repairs.append({"index": index, "field": f"repeat_files.{path}", "reason": "scalar_to_repeat_spec"})
            case["repeat_files"] = fixed
        modes = case.get("file_modes")
        if isinstance(modes, dict):
            for path, mode in list(modes.items()):
                if isinstance(mode, str):
                    try:
                        modes[path] = int(mode, 8) if mode.startswith("0") else int(mode)
                    except ValueError:
                        modes.pop(path)
                        repairs.append({"index": index, "field": f"file_modes.{path}", "reason": "invalid_mode_removed"})
        retained.append(case)
    data["cases"] = retained
    data["candidate_case_count"] = len(retained)
    data["fixture_normalization"] = {
        "repairs": repairs,
        "rejected": rejected,
        "input_case_count": len(retained) + len(rejected),
        "case_count": len(retained),
        "repetitions_retained": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print(json.dumps({
        "cases": len(retained),
        "repairs": len(repairs),
        "rejected": len(rejected),
        "output": str(args.output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
