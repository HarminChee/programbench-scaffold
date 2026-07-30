#!/usr/bin/env python3
"""Generate source-aware candidate CLI cases for ProgramBench command-line tasks.

This script proposes executable-level CLI cases from a private builder
workspace. It may inspect the target source tree, docs, and native tests, but it
does not read target ProgramBench oracle tests or target test blobs.

The output is a `--cases-json` file for
`tools/programbench_generate_cli_oracle_bundle.py`; that later stage captures
gold returncode/stdout/stderr from the cleanroom reference executable.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASKS_ROOTS = [
    Path("/home/harminchee/codex-workspaces/ProgramBench/src/programbench/data/tasks"),
    REPO_ROOT / "external/ProgramBench/src/programbench/data/tasks",
]
DOC_NAMES = {"README.md", "README.rst", "README.txt", "USAGE.md", "docs"}
TEXT_SUFFIXES = {
    ".md", ".rst", ".txt", ".go", ".rs", ".c", ".cc", ".cpp", ".cxx",
    ".h", ".hh", ".hpp", ".sh", ".yaml", ".yml", ".toml", ".json",
}
FLAG_RE = re.compile(r"(?<![A-Za-z0-9_])--?[A-Za-z][A-Za-z0-9_-]*")
STRING_RE = re.compile(r'"((?:\\.|[^"\\])*)"')
CLI_SOURCE_MARKERS = (
    "getopt", "getopt_long", "argc", "argv", "clap::", "#[arg", "#[command",
    "arg::", "args::", "argparse", "optionparser", "pflag", "flag.", "parse_args",
)
BUILD_ONLY_FLAGS = {"--build", "--config", "--install", "--parallel", "--target"}


STDIN_SAMPLES = {
    "json": '{"name":"alice","count":2,"items":["red","blue"],"nested":{"ok":true}}\n',
    "yaml": "name: alice\ncount: 2\nitems:\n  - red\n  - blue\nnested:\n  ok: true\n",
    "toml": 'name = "alice"\ncount = 2\nitems = ["red", "blue"]\n[nested]\nok = true\n',
    "hcl": 'resource "demo" "alpha" {\n  name = "alice"\n  count = 2\n}\n',
    "csv": "name,count\nalice,2\nbob,3\n",
    "xml": "<root><name>alice</name><count>2</count></root>\n",
    "text": "alpha\nbeta\ngamma\n",
    "empty": "",
    "invalid_json": '{"name": "alice",\n',
    "invalid_yaml": "name: [alice\n",
}


def parse_simple_yaml(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value.startswith("[") or value.startswith("{"):
            data[key.strip()] = value
        else:
            data[key.strip()] = value.strip("'\"")
    return data


def resolve_tasks_root(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    for root in DEFAULT_TASKS_ROOTS:
        if root.exists():
            return root.resolve()
    raise FileNotFoundError("ProgramBench tasks root not found; pass --tasks-root")


def run_command(cmd: list[str], *, cwd: Path | None = None, timeout: int = 300) -> dict[str, Any]:
    started = dt.datetime.now(dt.timezone.utc)
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        returncode = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", "replace")
        timed_out = True
    ended = dt.datetime.now(dt.timezone.utc)
    return {
        "cmd": cmd,
        "cwd": str(cwd) if cwd else None,
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_seconds": (ended - started).total_seconds(),
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
    }


def clone_source(repository: str, commit: str, dest: Path, logs_dir: Path, overwrite: bool) -> dict[str, Any]:
    if dest.exists():
        if overwrite:
            shutil.rmtree(dest)
        else:
            return {"source_dir": str(dest), "reused": True}
    logs_dir.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{repository}.git"
    clone = run_command(["git", "clone", "--filter=blob:none", url, str(dest)], timeout=900)
    (logs_dir / "git_clone.json").write_text(json.dumps(clone, indent=2) + "\n", encoding="utf-8")
    if clone["returncode"] != 0:
        raise RuntimeError(f"git clone failed for {repository}: {clone['stderr_tail']}")
    checkout = run_command(["git", "checkout", commit], cwd=dest, timeout=300)
    (logs_dir / "git_checkout.json").write_text(json.dumps(checkout, indent=2) + "\n", encoding="utf-8")
    if checkout["returncode"] != 0:
        raise RuntimeError(f"git checkout failed for {repository}@{commit}: {checkout['stderr_tail']}")
    return {"source_dir": str(dest), "reused": False, "clone": clone, "checkout": checkout}


def iter_text_files(source_dir: Path, max_files: int = 600) -> list[Path]:
    files: list[Path] = []
    ignored_dirs = {".git", "vendor", "node_modules", ".venv", "dist", "build"}
    for path in source_dir.rglob("*"):
        if any(part in ignored_dirs for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in DOC_NAMES or path.name.endswith("_test.go"):
            files.append(path)
    def priority(path: Path) -> tuple[int, str]:
        parts = {part.casefold() for part in path.relative_to(source_dir).parts}
        stem = path.stem.casefold()
        if path.name in DOC_NAMES or stem in {"main", "cli", "args", "options", "command", "app"}:
            return (0, str(path))
        if parts & {"cmd", "tools", "tool", "cli"}:
            return (1, str(path))
        if path.suffix.lower() in {".c", ".cc", ".cpp", ".cxx", ".rs", ".go"}:
            return (2, str(path))
        return (3, str(path))
    return sorted(files, key=priority)[:max_files]


def safe_read(path: Path, limit: int = 200_000) -> str:
    try:
        data = path.read_bytes()[:limit]
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def binary_names(repository: str, source_dir: Path) -> list[str]:
    repo_name = repository.split("/")[-1]
    names = [repo_name]
    for path in source_dir.glob("cmd/*"):
        if path.is_dir():
            names.append(path.name)
    for candidate in ["main", "app"]:
        if (source_dir / candidate).exists():
            names.append(candidate)
    result: list[str] = []
    for name in names:
        if name and name not in result:
            result.append(name)
    return result


def extract_doc_commands(text: str, names: list[str]) -> list[list[str]]:
    commands: list[list[str]] = []
    name_set = set(names)
    for raw in text.splitlines():
        line = raw.strip()
        prompted = line.startswith(("$ ", "> "))
        if not line:
            continue
        if line.startswith("$ "):
            line = line[2:].strip()
        elif line.startswith("> "):
            line = line[2:].strip()
        if "|" in line or ">" in line or "<" in line:
            continue
        try:
            parts = shlex.split(line)
        except ValueError:
            continue
        if not parts:
            continue
        first = Path(parts[0]).name
        # A prose sentence can begin with the project name ("Halite is ...").
        # Treat it as a command only when it is shell-prompted or has a flag;
        # the generic no-args seed already covers bare invocations.
        if first in name_set and (prompted or any(item.startswith("-") for item in parts[1:])):
            commands.append(parts[1:])
    return commands


def extract_runtime_flags(text_files: list[Path], docs: list[Path], names: list[str]) -> list[str]:
    """Extract likely executable flags without treating build commands as CLI syntax."""

    documented: set[str] = set()
    for path in docs:
        for command in extract_doc_commands(safe_read(path), names):
            documented.update(item.split("=", 1)[0] for item in command if FLAG_RE.fullmatch(item.split("=", 1)[0]))

    source_flags: set[str] = set()
    source_suffixes = {".go", ".rs", ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp"}
    for path in text_files:
        if path.suffix.lower() not in source_suffixes:
            continue
        lines = safe_read(path).splitlines()
        path_hint = path.stem.lower() in {"main", "cli", "args", "options", "command", "app"} or {"cmd", "tools", "tool", "cli"} & {
            part.lower() for part in path.parts
        }
        for index, line in enumerate(lines):
            window = "\n".join(lines[max(0, index - 2): index + 3]).lower()
            if not path_hint and not any(marker in window for marker in CLI_SOURCE_MARKERS):
                continue
            source_flags.update(FLAG_RE.findall(line))
        if path.suffix.lower() == ".rs":
            pending_attr = ""
            for line in lines:
                stripped = line.strip()
                if stripped.startswith(("#[arg(", "#[clap(")):
                    pending_attr = stripped
                    continue
                field_match = re.match(r"(?:pub\s+)?([A-Za-z][A-Za-z0-9_]*?)\s*:\s*[^:]", stripped)
                if pending_attr and field_match:
                    field = field_match.group(1)
                    if re.search(r"(?:^|[, (])long(?:[, )=]|$)", pending_attr):
                        source_flags.add("--" + field.replace("_", "-"))
                    short_match = re.search(r"short\s*=\s*['\"]([A-Za-z0-9])", pending_attr)
                    if short_match:
                        source_flags.add("-" + short_match.group(1))
                    elif re.search(r"(?:^|[, (])short(?:[, )]|$)", pending_attr):
                        source_flags.add("-" + field[0])
                    pending_attr = ""
                elif stripped and not stripped.startswith("#"):
                    pending_attr = ""
        if path.suffix.lower() in {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp"}:
            for line in lines:
                # TCLAP declares short and long names as the first two string
                # arguments to SwitchArg/ValueArg/MultiArg constructors.
                if "TCLAP::Unlabeled" in line:
                    continue
                match = re.search(
                    r"TCLAP::\w*Arg[^;(]*\(\s*\"([^\"]*)\"\s*,\s*\"([^\"]+)\"",
                    line,
                )
                if not match:
                    continue
                short_name, long_name = match.groups()
                if re.fullmatch(r"[A-Za-z0-9]", short_name):
                    source_flags.add("-" + short_name)
                if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", long_name):
                    source_flags.add("--" + long_name)

    result = documented | source_flags
    result = {
        flag for flag in result
        if flag not in {"--", "-"}
        and not flag.startswith("---")
        and not flag.startswith("-D")
        and not flag.startswith("-W")
        and (flag not in BUILD_ONLY_FLAGS or flag in documented)
    }
    return sorted(result)


def extract_c_subcommands(text_files: list[Path]) -> list[str]:
    commands: set[str] = set()
    pattern = re.compile(r"strcmp\s*\(\s*argv\s*\[\s*1\s*\]\s*,\s*\"([A-Za-z0-9_-]+)\"\s*\)")
    for path in text_files:
        if path.suffix.lower() not in {".c", ".cc", ".cpp", ".cxx"}:
            continue
        commands.update(command for command in pattern.findall(safe_read(path)) if not command.startswith("-"))
    return sorted(commands)


def extract_native_test_command_args(text: str) -> list[list[str]]:
    args: list[list[str]] = []
    for line in text.splitlines():
        if "exec.Command" not in line and "command" not in line.lower() and "args" not in line.lower():
            continue
        literals = [bytes(s, "utf-8").decode("unicode_escape", errors="replace") for s in STRING_RE.findall(line)]
        if len(literals) >= 2:
            tail = [item for item in literals[1:] if not item.endswith(".go")]
            if tail:
                args.append(tail)
    return args


def detect_formats(all_text: str) -> list[str]:
    lowered = all_text.lower()
    formats = []
    for fmt in ["json", "yaml", "toml", "hcl", "csv", "xml"]:
        if fmt in lowered:
            formats.append(fmt)
    if not formats:
        formats.append("text")
    return formats


def normalize_case(name: str, args: list[str], stdin: str, area: str, origin: str) -> dict[str, Any]:
    clean = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_").lower() or "case"
    return {"name": clean[:120], "area": area, "args": args, "stdin": stdin, "origin": origin}


def harvest_testdata_cases(source_dir: Path, flags: list[str], max_input_bytes: int = 60_000) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    input_re = re.compile(r"(?P<prefix>.+)_in\.(?P<ext>json|ya?ml|toml|hcl|xml|csv|txt)$", re.IGNORECASE)
    output_re = re.compile(r"(?P<prefix>.+)_out_(?P<flag>[A-Za-z]+)\.", re.IGNORECASE)
    format_flags = [f for f in flags if re.fullmatch(r"-[A-Za-z]{2,4}", f)]
    for input_path in sorted(source_dir.rglob("*_in.*"))[:80]:
        if not input_path.is_file() or "testdata" not in input_path.parts:
            continue
        match = input_re.match(input_path.name)
        if not match:
            continue
        stdin = safe_read(input_path, max_input_bytes)
        if len(stdin) >= max_input_bytes:
            continue
        prefix = match.group("prefix")
        sibling_flags: list[str] = []
        for sibling in sorted(input_path.parent.glob(f"{prefix}_out_*")):
            out_match = output_re.match(sibling.name)
            if out_match:
                flag = "-" + out_match.group("flag")
                if flag not in sibling_flags:
                    sibling_flags.append(flag)
        candidate_flags = sibling_flags or format_flags[:12]
        cases.append(
            normalize_case(
                f"fixture_{prefix}_default",
                [],
                stdin,
                "native_fixture_harvest",
                str(input_path.relative_to(source_dir)),
            )
        )
        for flag in candidate_flags[:24]:
            cases.append(
                normalize_case(
                    f"fixture_{prefix}_{flag.strip('-')}",
                    [flag],
                    stdin,
                    "native_fixture_harvest",
                    str(input_path.relative_to(source_dir)),
                )
            )
    return cases


def flag_needs_value(flag: str, all_text: str) -> bool:
    value_markers = [
        f"{flag} <",
        f"{flag}=",
        f"{flag} string",
        f"{flag} file",
        f"{flag} path",
        f"{flag} output",
        f"{flag} format",
    ]
    lowered = all_text.lower()
    return any(marker.lower() in lowered for marker in value_markers)


def propose_cases(repository: str, source_dir: Path, max_cases: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    names = binary_names(repository, source_dir)
    text_files = iter_text_files(source_dir)
    docs = [p for p in text_files if p.name in DOC_NAMES or p.suffix.lower() in {".md", ".rst", ".txt"}]
    native_tests = [p for p in text_files if p.name.endswith("_test.go")]
    all_text_parts = [safe_read(p) for p in text_files]
    all_text = "\n".join(all_text_parts)
    flags = extract_runtime_flags(text_files, docs, names)
    subcommands = extract_c_subcommands(text_files)
    formats = detect_formats(all_text)

    cases: list[dict[str, Any]] = []
    cases.append(normalize_case("no_args", [], "", "args", "standard_cli_probe"))
    for flag in ["-h", "--help", "-help", "-v", "--version", "-version"]:
        cases.append(normalize_case(f"standard_{flag.strip('-')}", [flag], "", "help_version", "standard_cli_probe"))
    cases.append(normalize_case("invalid_flag_long", ["--programbench-invalid-flag"], "", "error_handling", "standard_cli_probe"))
    for command in subcommands[:80]:
        cases.append(normalize_case(f"subcommand_{command}_no_args", [command], "", "subcommand", "source_dispatch_scan"))

    for path in docs[:30]:
        for args in extract_doc_commands(safe_read(path), names):
            cases.append(normalize_case(f"doc_{path.stem}_{'_'.join(args[:4])}", args, "", "docs", str(path.relative_to(source_dir))))

    for path in native_tests[:80]:
        for args in extract_native_test_command_args(safe_read(path)):
            cases.append(
                normalize_case(f"native_{path.stem}_{'_'.join(args[:4])}", args, "", "native_test_harvest", str(path.relative_to(source_dir)))
            )

    cases.extend(harvest_testdata_cases(source_dir, flags))

    value = "out"
    for flag in flags[:80]:
        if flag in {"--output", "--out"} or (flag == "-o" and flag_needs_value(flag, all_text)):
            continue
        args = [flag, value] if flag_needs_value(flag, all_text) else [flag]
        cases.append(normalize_case(f"flag_{flag.strip('-')}", args, "", "flag_surface", "source_and_docs_flag_scan"))

    format_flags = [f for f in flags if any(ch in f.lower() for ch in ["json", "yaml", "toml", "hcl", "csv", "xml"])]
    if not format_flags:
        format_flags = [f for f in flags if re.fullmatch(r"-[A-Za-z]{2,4}", f)]
    for fmt in formats:
        stdin = STDIN_SAMPLES.get(fmt, STDIN_SAMPLES["text"])
        cases.append(normalize_case(f"stdin_default_{fmt}", [], stdin, "io", f"synthetic_{fmt}_sample"))
        for flag in format_flags[:40]:
            cases.append(normalize_case(f"stdin_{fmt}_{flag.strip('-')}", [flag], stdin, "io", f"synthetic_{fmt}_sample_with_source_flag"))

    for bad_name in ["invalid_json", "invalid_yaml"]:
        if bad_name.split("_", 1)[1] in formats or repository.endswith("/yj"):
            cases.append(normalize_case(bad_name, [], STDIN_SAMPLES[bad_name], "error_handling", "synthetic_invalid_input"))

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, ...], str]] = set()
    name_counts: dict[str, int] = {}
    for case in cases:
        key = (tuple(map(str, case["args"])), str(case["stdin"]))
        if key in seen:
            continue
        seen.add(key)
        base_name = str(case["name"])
        name_counts[base_name] = name_counts.get(base_name, 0) + 1
        if name_counts[base_name] > 1:
            case = dict(case)
            case["name"] = f"{base_name}_{name_counts[base_name]}"[:120]
        deduped.append(case)
        if len(deduped) >= max_cases:
            break

    inventory = {
        "binary_names": names,
        "text_file_count": len(text_files),
        "doc_files": [str(p.relative_to(source_dir)) for p in docs[:50]],
        "native_test_files": [str(p.relative_to(source_dir)) for p in native_tests[:50]],
        "flag_count": len(flags),
        "sample_flags": flags[:80],
        "sample_subcommands": subcommands[:80],
        "detected_formats": formats,
        "native_fixture_case_count": sum(1 for case in deduped if case.get("area") == "native_fixture_harvest"),
        "candidate_count_before_dedupe": len(cases),
        "case_count": len(deduped),
    }
    return deduped, inventory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance_id")
    parser.add_argument("--tasks-root", type=Path)
    parser.add_argument("--work-root", type=Path, default=Path("/tmp/programbench_pb_style_go_case_miner"))
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--profile", default="pb_source_aware_go_v1")
    parser.add_argument("--max-cases", type=int, default=200)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    tasks_root = resolve_tasks_root(args.tasks_root)
    task_dir = tasks_root / args.instance_id
    task_yaml = parse_simple_yaml(task_dir / "task.yaml")
    repository = str(task_yaml["repository"])
    commit = str(task_yaml["commit"])
    work_root = args.work_root.expanduser().resolve() / args.instance_id
    logs_dir = work_root / "logs"
    source_dir = work_root / "source"
    clone_info = clone_source(repository, commit, source_dir, logs_dir, args.overwrite)
    cases, inventory = propose_cases(repository, source_dir, args.max_cases)

    output_json = args.output_json if args.output_json.is_absolute() else (REPO_ROOT / args.output_json).resolve()
    output_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "profile": args.profile,
        "generator": "programbench_generate_source_aware_cli_cases.py",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "instance_id": args.instance_id,
        "repository": repository,
        "commit": commit,
        "language": task_yaml.get("language"),
        "source_policy": "source/docs/native tests allowed; target official ProgramBench oracle tests and target test blobs forbidden",
        "clone_info": clone_info,
        "inventory": inventory,
        "cases": cases,
    }
    output_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output_json": str(output_json), "case_count": len(cases), "inventory": inventory}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
