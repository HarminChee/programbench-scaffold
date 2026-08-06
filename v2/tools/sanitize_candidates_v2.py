#!/usr/bin/env python3
"""Normalize agent-authored fixture paths into the isolated case workspace."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath


FIXTURE_MAPS = ("files", "executable_files", "binary_files", "repeat_files", "file_modes")


def safe_path(value: object) -> tuple[str, bool]:
    raw = str(value).replace("\\", "/")
    path = PurePosixPath(raw)
    unsafe = path.is_absolute() or ".." in path.parts or not raw.strip()
    if not unsafe:
        return raw, False
    name = path.name or "fixture"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"fixtures/{digest}_{name}", True


def rewrite_text(value: str, replacements: dict[str, str]) -> str:
    result = value
    for old, new in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        result = result.replace(old, new)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
    retained = []
    rejected = []
    rewrites = []
    for index, raw_case in enumerate(payload.get("cases") or []):
        case = dict(raw_case)
        replacements: dict[str, str] = {}
        valid = True
        reason = ""
        for field in FIXTURE_MAPS:
            value = case.get(field)
            if value is None:
                continue
            if not isinstance(value, dict):
                valid, reason = False, f"{field} must be an object"
                break
            normalized = {}
            for old_path, content in value.items():
                new_path, changed = safe_path(old_path)
                # Agents often describe a text fixture as {content, mode}.
                # The capture runtime tolerates it as text, while generated
                # pytest must receive the text and a separate mode field.
                if field in {"files", "executable_files"} and isinstance(content, dict) and "content" in content:
                    mode = content.get("mode")
                    content = content.get("content")
                    if mode is not None:
                        modes = case.setdefault("file_modes", {})
                        if isinstance(modes, dict):
                            modes[new_path] = mode
                normalized[new_path] = content
                if changed:
                    replacements[str(old_path)] = new_path
            case[field] = normalized
        if not valid:
            rejected.append({"index": index, "name": case.get("name"), "reason": reason})
            continue
        git_fixture = case.get("git")
        if git_fixture is not None:
            if not isinstance(git_fixture, dict):
                rejected.append({"index": index, "name": case.get("name"), "reason": "git must be an object"})
                continue
            git_fixture = dict(git_fixture)
            for field in ("staged_files", "untracked_files"):
                value = git_fixture.get(field)
                if value is None:
                    continue
                if not isinstance(value, dict):
                    valid, reason = False, f"git.{field} must be an object"
                    break
                normalized = {}
                for old_path, content in value.items():
                    new_path, changed = safe_path(old_path)
                    normalized[new_path] = content
                    if changed:
                        replacements[str(old_path)] = new_path
                git_fixture[field] = normalized
            if not valid:
                rejected.append({"index": index, "name": case.get("name"), "reason": reason})
                continue
            case["git"] = git_fixture
        binary_files = case.get("binary_files") or {}
        try:
            for content in binary_files.values():
                base64.b64decode(str(content), validate=True)
        except Exception:
            rejected.append({"index": index, "name": case.get("name"), "reason": "invalid base64 binary fixture"})
            continue
        terminal = case.get("terminal")
        if isinstance(terminal, bool):
            if terminal:
                case["terminal"] = {}
            else:
                case.pop("terminal", None)
        elif terminal is not None and not isinstance(terminal, dict):
            rejected.append({"index": index, "name": case.get("name"), "reason": "terminal must be a boolean or object"})
            continue
        case["args"] = [rewrite_text(str(item), replacements) for item in case.get("args") or []]
        if "observe_files" in case:
            if not isinstance(case["observe_files"], list):
                rejected.append({"index": index, "name": case.get("name"), "reason": "observe_files must be a list"})
                continue
            case["observe_files"] = [safe_path(item)[0] for item in case["observe_files"]]
        if replacements:
            rewrites.append({"index": index, "name": case.get("name"), "paths": replacements})
        retained.append(case)
    result = dict(payload)
    result["cases"] = retained
    result["candidate_case_count"] = len(retained)
    result["sanitization"] = {"input_cases": len(payload.get("cases") or []), "retained_cases": len(retained), "rejected": rejected, "path_rewrites": rewrites}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"input": len(payload.get("cases") or []), "retained": len(retained), "rejected": len(rejected), "rewritten": len(rewrites)}, indent=2))
    return 0 if retained else 1


if __name__ == "__main__":
    raise SystemExit(main())
