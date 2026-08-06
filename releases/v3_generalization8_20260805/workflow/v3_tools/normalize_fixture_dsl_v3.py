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
    lifecycle = case.get("lifecycle")
    if isinstance(lifecycle, dict):
        values.extend(
            str(event.get("url") or "")
            for event in lifecycle.get("events") or []
            if isinstance(event, dict)
        )
    sequence = case.get("sequence")
    if isinstance(sequence, list):
        for step in sequence:
            if not isinstance(step, dict):
                continue
            values.extend(str(value) for value in step.get("args") or [])
            if isinstance(step.get("env"), dict):
                values.extend(str(value) for value in step["env"].values())
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
        lifecycle = case.get("lifecycle")
        sequence = case.get("sequence")
        if sum(bool(value) for value in (lifecycle, sequence, case.get("terminal"))) > 1:
            rejected.append({
                "index": index,
                "name": case.get("name"),
                "reason": "sequence_lifecycle_terminal_are_mutually_exclusive",
            })
            continue
        if sequence:
            if (
                not isinstance(sequence, list)
                or not 1 <= len(sequence) <= 32
                or any(
                    not isinstance(step, dict)
                    or not isinstance(step.get("args", []), list)
                    or not all(isinstance(value, str) for value in step.get("args", []))
                    or not isinstance(step.get("stdin", ""), str)
                    for step in sequence
                )
            ):
                rejected.append({
                    "index": index,
                    "name": case.get("name"),
                    "reason": "invalid_sequence_schema",
                })
                continue
        if lifecycle:
            if not isinstance(lifecycle, dict) or not isinstance(lifecycle.get("events", []), list):
                rejected.append({"index": index, "name": case.get("name"), "reason": "invalid_lifecycle_schema"})
                continue
            if not lifecycle.get("events"):
                rejected.append({
                    "index": index, "name": case.get("name"),
                    "reason": "lifecycle_requires_a_substantive_event",
                })
                continue
            allowed_actions = {
                "write_file", "remove_path", "stdin", "close_stdin", "signal",
                "http_request", "tcp_send", "sleep",
                "parallel",
            }
            invalid_actions = [
                event.get("action")
                for event in lifecycle.get("events") or []
                if not isinstance(event, dict) or event.get("action") not in allowed_actions
            ]
            if invalid_actions or len(lifecycle.get("events") or []) > 64:
                rejected.append({
                    "index": index, "name": case.get("name"),
                    "reason": "unsupported_or_excessive_lifecycle_events",
                    "actions": invalid_actions,
                })
                continue
            for event in lifecycle.get("events") or []:
                if event.get("action") != "parallel":
                    continue
                children = event.get("events") or []
                if (
                    not isinstance(children, list) or not 1 <= len(children) <= 8
                    or any(not isinstance(child, dict) or child.get("action") not in {"http_request", "tcp_send"} for child in children)
                ):
                    rejected.append({
                        "index": index, "name": case.get("name"),
                        "reason": "invalid_parallel_lifecycle_event",
                    })
                    break
            else:
                pass
            if rejected and rejected[-1].get("index") == index:
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
