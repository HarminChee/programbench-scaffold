#!/usr/bin/env python3
"""Generate executable black-box CLI oracle tests from a reference binary.

Profiles provide candidate CLI cases. The capture engine is generic: it copies
the ProgramBench cleanroom executable, runs each case against that reference
binary, and materializes exact process observations as pytest fixtures.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import hashlib
import http.server
import inspect
import json
import os
import re
import signal
import shutil
import stat
import subprocess
import tempfile
import textwrap
import threading
import time
from pathlib import Path
from typing import Any, Iterator


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "generated_cli_manifest.json"
VOLATILE_OUTPUT_RE = re.compile(
    rb"\b\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}\b|\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
)


def image_name_from_instance_id(instance_id: str) -> str:
    return f"programbench/{instance_id.replace('__', '_1776_')}:task_cleanroom_v6"


def slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_").lower()
    return cleaned or "case"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def short_text(value: str, limit: int = 5000) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_command(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
    log_path: Path | None = None,
) -> dict[str, Any]:
    started = dt.datetime.now(dt.timezone.utc)
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        timed_out = False
        returncode = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", errors="replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", errors="replace")
    ended = dt.datetime.now(dt.timezone.utc)
    payload = {
        "cmd": cmd,
        "cwd": str(cwd) if cwd else None,
        "returncode": returncode,
        "timed_out": timed_out,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_seconds": (ended - started).total_seconds(),
        "stdout_tail": short_text(stdout),
        "stderr_tail": short_text(stderr),
    }
    if log_path is not None:
        write_json(log_path, {**payload, "stdout": stdout, "stderr": stderr})
        payload["log_path"] = str(log_path)
    return payload


def materialize_cleanroom_file(
    *,
    image: str,
    docker: str,
    source_path: str,
    dest: Path,
    logs_dir: Path,
) -> dict[str, Any]:
    inspect = run_command([docker, "image", "inspect", image], timeout=60, log_path=logs_dir / "docker_image_inspect.json")
    if inspect["returncode"] != 0:
        pull = run_command([docker, "pull", image], timeout=1800, log_path=logs_dir / "docker_pull_cleanroom.json")
        if pull["returncode"] != 0:
            return {"returncode": pull["returncode"], "error": pull["stderr_tail"], "log_path": pull.get("log_path")}
    create = run_command([docker, "create", image], timeout=120, log_path=logs_dir / f"docker_create_{dest.name}.json")
    if create["returncode"] != 0:
        return {"returncode": create["returncode"], "error": create["stderr_tail"], "log_path": create.get("log_path")}
    container_id = create["stdout_tail"].strip().splitlines()[-1]
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        cp = run_command(
            [docker, "cp", f"{container_id}:{source_path}", str(dest)],
            timeout=300,
            log_path=logs_dir / f"docker_cp_{dest.name}.json",
        )
        if cp["returncode"] != 0:
            return {"returncode": cp["returncode"], "container_id": container_id, "error": cp["stderr_tail"]}
        return {"returncode": 0, "container_id": container_id, "path": str(dest)}
    finally:
        run_command([docker, "rm", "-f", container_id], timeout=120, log_path=logs_dir / f"docker_rm_{dest.name}.json")


def generic_cli_smoke_cases() -> list[dict[str, Any]]:
    """Small black-box CLI probe set that does not assume repo-specific docs."""
    return [
        {"name": "no_args_empty_stdin", "area": "generic_cli_smoke", "args": [], "stdin": ""},
        {"name": "help_short", "area": "generic_cli_smoke", "args": ["-h"], "stdin": ""},
        {"name": "help_long", "area": "generic_cli_smoke", "args": ["--help"], "stdin": ""},
        {"name": "version_short", "area": "generic_cli_smoke", "args": ["-v"], "stdin": ""},
        {"name": "version_long", "area": "generic_cli_smoke", "args": ["--version"], "stdin": ""},
        {"name": "invalid_long_flag", "area": "generic_cli_smoke", "args": ["--programbench-invalid-flag"], "stdin": ""},
    ]


def yj_cases() -> list[dict[str, Any]]:
    yaml_doc = textwrap.dedent(
        """\
        name: programbench
        enabled: true
        count: 3
        ratio: 1.25
        tags:
          - alpha
          - beta
        nested:
          keep_order: yes
          message: "hello: world"
        """
    )
    yaml_special = textwrap.dedent(
        """\
        positive: .inf
        negative: -.inf
        missing: .nan
        quoted: ".inf"
        """
    )
    json_doc = json.dumps(
        {
            "name": "programbench",
            "enabled": True,
            "count": 3,
            "tags": ["alpha", "beta"],
            "nested": {"message": "hello: world", "angle": "<tag>&value"},
        },
        separators=(",", ":"),
    ) + "\n"
    json_array = '[{"name":"alpha","score":1},{"name":"beta","score":2}]\n'
    toml_doc = textwrap.dedent(
        """\
        title = "ProgramBench"
        enabled = true
        count = 3
        tags = ["alpha", "beta"]
        timestamp = 2020-01-02T03:04:05Z

        [nested]
        message = "hello: world"
        value = 1.25
        """
    )
    hcl_doc = textwrap.dedent(
        """\
        name = "programbench"
        enabled = true
        count = 3
        tags = ["alpha", "beta"]

        nested {
          message = "hello: world"
          value = 1.25
        }
        """
    )
    yaml_merge_doc = textwrap.dedent(
        """\
        defaults: &defaults
          adapter: postgres
          host: localhost
        development:
          <<: *defaults
          database: dev
        production:
          <<: *defaults
          database: prod
        """
    )
    yaml_tags_doc = textwrap.dedent(
        """\
        as_int: !!int "3"
        as_str: !!str 3
        as_bool: !!bool "true"
        as_float: !!float "1.5"
        """
    )
    toml_tables_doc = textwrap.dedent(
        """\
        title = "Array Tables"

        [owner]
        name = "ProgramBench"

        [[products]]
        name = "Hammer"
        sku = 738594937

        [[products]]
        name = "Nail"
        sku = 284758393
        color = "gray"
        """
    )
    toml_dotted_doc = textwrap.dedent(
        """\
        name = "Orange"
        physical.color = "orange"
        physical.shape = "round"
        site."google.com" = true
        """
    )
    hcl_repeated_doc = textwrap.dedent(
        """\
        server "web" {
          port = 80
          tags = ["edge", "blue"]
        }

        server "api" {
          port = 8080
          tags = ["internal"]
        }
        """
    )
    yaml_complex_keys_doc = textwrap.dedent(
        """\
        ? [alpha, beta]
        : list key
        ? {nested: value}
        : map key
        ? true
        : bool key
        ? 3.14
        : number key
        """
    )
    yaml_merge_sequence_doc = textwrap.dedent(
        """\
        defaults_a: &defaults_a
          adapter: postgres
          host: localhost
        defaults_b: &defaults_b
          host: db.local
          pool: 5
        development:
          <<: [*defaults_a, *defaults_b]
          database: dev
        """
    )
    yaml_large_alias_doc = (
        "base: &base\n"
        "  name: item\n"
        "  enabled: true\n"
        "items:\n"
        + "  - *base\n" * 1200
    )
    yaml_nulls_doc = textwrap.dedent(
        """\
        unset:
        explicit_null: null
        list:
          - alpha
          -
          - beta
        nested:
          keep:
        """
    )
    json_key_objects_doc = json.dumps(
        {
            "[1,2]": "array key",
            '{"nested":true}': "object key",
            "null": "null key",
            '"quoted"': "quoted key",
            "3.0": "float key",
        },
        separators=(",", ":"),
    ) + "\n"
    json_numbers_doc = json.dumps(
        {
            "integerish": 1.0,
            "precise": 1.25,
            "items": [0.0, 2.5, 10.0],
            "nested": {"answer": 42.0},
        },
        separators=(",", ":"),
    ) + "\n"
    json_nulls_doc = json.dumps(
        {
            "name": "programbench",
            "missing": None,
            "items": ["alpha", None, "beta"],
            "nested": {"keep": None, "value": 1},
        },
        separators=(",", ":"),
    ) + "\n"
    json_many_keys_doc = json.dumps(
        {f"key_{i:02d}": i for i in range(34)} | {"empty": None},
        separators=(",", ":"),
    ) + "\n"
    toml_special_doc = textwrap.dedent(
        """\
        pos = inf
        neg = -inf
        missing = nan
        list = [1.0, inf, -inf, nan]
        """
    )
    toml_nested_arrays_doc = textwrap.dedent(
        """\
        title = "Nested Tables"

        [[services]]
        name = "web"
        ports = [80, 443]

        [[services.routes]]
        path = "/"
        upstream = "root"

        [[services.routes]]
        path = "/api"
        upstream = "api"

        [[services]]
        name = "worker"
        ports = [9000]
        """
    )
    hcl_nested_repeated_doc = textwrap.dedent(
        """\
        service "web" {
          endpoint "root" {
            path = "/"
          }

          endpoint "api" {
            path = "/api"
          }
        }

        service "worker" {
          endpoint "jobs" {
            path = "/jobs"
          }
        }
        """
    )
    hcl_duplicate_attr_doc = textwrap.dedent(
        """\
        name = "first"
        name = "second"
        enabled = true
        """
    )
    hcl_block_then_scalar_doc = textwrap.dedent(
        """\
        server "web" {
          port = 80
        }
        server = "scalar"
        """
    )
    invalid_yaml = "name: [unterminated\n"
    invalid_json = '{"name": "unterminated"\n'
    invalid_toml = 'name = "unterminated\n'
    invalid_hcl = 'server "web" {\n  port = \n'
    invalid_yaml_merge = textwrap.dedent(
        """\
        broken:
          <<: 1
        """
    )

    return [
        {"name": "help_short", "area": "help_usage", "args": ["-h"], "stdin": ""},
        {"name": "version_short", "area": "help_usage", "args": ["-v"], "stdin": ""},
        {"name": "invalid_short_flag", "area": "errors", "args": ["-Z"], "stdin": ""},
        {"name": "invalid_long_help_flag", "area": "errors", "args": ["--help"], "stdin": ""},
        {"name": "split_flags_yaml_json_indent", "area": "flag_matrix", "args": ["-y", "j", "i"], "stdin": yaml_doc},
        {"name": "split_flags_json_yaml_keys", "area": "flag_matrix", "args": ["-j", "y", "k"], "stdin": json_key_objects_doc},
        {"name": "invalid_json_keys_for_json_output", "area": "flag_matrix", "args": ["-jjk"], "stdin": json_doc},
        {"name": "invalid_escape_for_yaml_output", "area": "flag_matrix", "args": ["-jye"], "stdin": json_doc},
        {"name": "invalid_indent_for_hcl_output", "area": "flag_matrix", "args": ["-jci"], "stdin": json_doc},
        {"name": "yaml_to_json_default", "area": "yaml_to_json", "args": [], "stdin": yaml_doc},
        {"name": "yaml_to_json_explicit", "area": "yaml_to_json", "args": ["-yj"], "stdin": yaml_doc},
        {"name": "yaml_to_json_short_alias", "area": "alias_flags", "args": ["-y"], "stdin": yaml_doc},
        {"name": "yaml_to_toml_nodash_flags", "area": "alias_flags", "args": ["yt"], "stdin": yaml_doc},
        {"name": "yaml_to_json_indented", "area": "yaml_to_json", "args": ["-yji"], "stdin": yaml_doc},
        {"name": "yaml_to_yaml_roundtrip", "area": "yaml_to_yaml", "args": ["-yy"], "stdin": yaml_doc},
        {"name": "yaml_to_toml", "area": "yaml_to_toml", "args": ["-yt"], "stdin": yaml_doc},
        {"name": "yaml_to_toml_indented", "area": "yaml_to_toml", "args": ["-yti"], "stdin": yaml_doc},
        {"name": "yaml_to_hcl", "area": "yaml_to_hcl", "args": ["-yc"], "stdin": yaml_doc},
        {"name": "yaml_special_default", "area": "yaml_special_values", "args": ["-yj"], "stdin": yaml_special},
        {"name": "yaml_special_no_convert", "area": "yaml_special_values", "args": ["-yjn"], "stdin": yaml_special},
        {"name": "yaml_merge_to_json", "area": "yaml_alias_merge", "args": ["-yj"], "stdin": yaml_merge_doc},
        {"name": "yaml_merge_to_yaml", "area": "yaml_alias_merge", "args": ["-yy"], "stdin": yaml_merge_doc},
        {"name": "yaml_tags_to_json", "area": "yaml_tags", "args": ["-yj"], "stdin": yaml_tags_doc},
        {"name": "yaml_tags_to_yaml", "area": "yaml_tags", "args": ["-yy"], "stdin": yaml_tags_doc},
        {"name": "yaml_complex_keys_to_json", "area": "yaml_complex_keys", "args": ["-yj"], "stdin": yaml_complex_keys_doc},
        {"name": "yaml_complex_keys_to_yaml", "area": "yaml_complex_keys", "args": ["-yy"], "stdin": yaml_complex_keys_doc},
        {"name": "yaml_merge_sequence_to_json", "area": "yaml_alias_merge", "args": ["-yj"], "stdin": yaml_merge_sequence_doc},
        {"name": "yaml_merge_sequence_to_toml", "area": "yaml_alias_merge", "args": ["-yt"], "stdin": yaml_merge_sequence_doc},
        {"name": "yaml_large_alias_to_json", "area": "yaml_alias_merge", "args": ["-yj"], "stdin": yaml_large_alias_doc},
        {"name": "yaml_nulls_to_toml", "area": "toml_nulls", "args": ["-yt"], "stdin": yaml_nulls_doc},
        {"name": "yaml_special_to_toml", "area": "toml_special_values", "args": ["-yt"], "stdin": yaml_special},
        {"name": "yaml_special_to_toml_no_convert", "area": "toml_special_values", "args": ["-ytn"], "stdin": yaml_special},
        {"name": "json_to_json", "area": "json_to_json", "args": ["-jj"], "stdin": json_doc},
        {"name": "json_to_json_escape_html", "area": "json_to_json", "args": ["-jje"], "stdin": json_doc},
        {"name": "json_to_yaml", "area": "json_to_yaml", "args": ["-jy"], "stdin": json_doc},
        {"name": "json_to_yaml_short_alias", "area": "alias_flags", "args": ["-r"], "stdin": json_doc},
        {"name": "json_to_yaml_nodash_flags", "area": "alias_flags", "args": ["jy"], "stdin": json_doc},
        {"name": "json_array_to_yaml", "area": "json_to_yaml", "args": ["-jy"], "stdin": json_array},
        {"name": "json_to_yaml_parse_keys", "area": "json_to_yaml", "args": ["-jyk"], "stdin": '{"1":"one","true":"yes","3.14":"pi"}\n'},
        {"name": "json_to_yaml_parse_object_keys", "area": "yaml_json_keys", "args": ["-jyk"], "stdin": json_key_objects_doc},
        {"name": "json_numbers_to_yaml", "area": "yaml_number_encoding", "args": ["-jy"], "stdin": json_numbers_doc},
        {"name": "json_to_toml", "area": "json_to_toml", "args": ["-jt"], "stdin": json_doc},
        {"name": "json_array_to_toml_error", "area": "errors", "args": ["-jt"], "stdin": json_array},
        {"name": "json_nulls_to_toml", "area": "toml_nulls", "args": ["-jt"], "stdin": json_nulls_doc},
        {"name": "json_many_keys_to_toml", "area": "toml_many_keys", "args": ["-jt"], "stdin": json_many_keys_doc},
        {"name": "json_many_keys_to_toml_indented", "area": "toml_many_keys", "args": ["-jti"], "stdin": json_many_keys_doc},
        {"name": "json_to_hcl", "area": "json_to_hcl", "args": ["-jc"], "stdin": json_doc},
        {"name": "toml_to_json", "area": "toml_to_json", "args": ["-tj"], "stdin": toml_doc},
        {"name": "toml_to_json_short_alias", "area": "alias_flags", "args": ["-t"], "stdin": toml_doc},
        {"name": "toml_to_json_indented", "area": "toml_to_json", "args": ["-tji"], "stdin": toml_doc},
        {"name": "toml_to_yaml", "area": "toml_to_yaml", "args": ["-ty"], "stdin": toml_doc},
        {"name": "toml_to_toml", "area": "toml_to_toml", "args": ["-tt"], "stdin": toml_doc},
        {"name": "toml_to_hcl", "area": "toml_to_hcl", "args": ["-tc"], "stdin": toml_doc},
        {"name": "toml_tables_to_json", "area": "toml_tables", "args": ["-tj"], "stdin": toml_tables_doc},
        {"name": "toml_tables_to_yaml", "area": "toml_tables", "args": ["-ty"], "stdin": toml_tables_doc},
        {"name": "toml_tables_to_hcl", "area": "toml_tables", "args": ["-tc"], "stdin": toml_tables_doc},
        {"name": "toml_dotted_to_json", "area": "toml_dotted_keys", "args": ["-tj"], "stdin": toml_dotted_doc},
        {"name": "toml_dotted_to_yaml", "area": "toml_dotted_keys", "args": ["-ty"], "stdin": toml_dotted_doc},
        {"name": "toml_special_to_json", "area": "toml_special_values", "args": ["-tj"], "stdin": toml_special_doc},
        {"name": "toml_special_to_json_no_convert", "area": "toml_special_values", "args": ["-tjn"], "stdin": toml_special_doc},
        {"name": "toml_special_to_yaml", "area": "toml_special_values", "args": ["-ty"], "stdin": toml_special_doc},
        {"name": "toml_nested_arrays_to_json", "area": "toml_nested_arrays", "args": ["-tj"], "stdin": toml_nested_arrays_doc},
        {"name": "toml_nested_arrays_to_yaml", "area": "toml_nested_arrays", "args": ["-ty"], "stdin": toml_nested_arrays_doc},
        {"name": "toml_nested_arrays_to_hcl", "area": "toml_nested_arrays", "args": ["-tc"], "stdin": toml_nested_arrays_doc},
        {"name": "hcl_to_json", "area": "hcl_to_json", "args": ["-cj"], "stdin": hcl_doc},
        {"name": "hcl_to_json_short_alias", "area": "alias_flags", "args": ["-c"], "stdin": hcl_doc},
        {"name": "hcl_to_json_indented", "area": "hcl_to_json", "args": ["-cji"], "stdin": hcl_doc},
        {"name": "hcl_to_yaml", "area": "hcl_to_yaml", "args": ["-cy"], "stdin": hcl_doc},
        {"name": "hcl_to_toml", "area": "hcl_to_toml", "args": ["-ct"], "stdin": hcl_doc},
        {"name": "hcl_to_hcl", "area": "hcl_to_hcl", "args": ["-cc"], "stdin": hcl_doc},
        {"name": "hcl_repeated_to_json", "area": "hcl_repeated_blocks", "args": ["-cj"], "stdin": hcl_repeated_doc},
        {"name": "hcl_repeated_to_yaml", "area": "hcl_repeated_blocks", "args": ["-cy"], "stdin": hcl_repeated_doc},
        {"name": "hcl_repeated_to_toml", "area": "hcl_repeated_blocks", "args": ["-ct"], "stdin": hcl_repeated_doc},
        {"name": "hcl_nested_repeated_to_json", "area": "hcl_repeated_blocks", "args": ["-cj"], "stdin": hcl_nested_repeated_doc},
        {"name": "hcl_nested_repeated_to_yaml", "area": "hcl_repeated_blocks", "args": ["-cy"], "stdin": hcl_nested_repeated_doc},
        {"name": "hcl_duplicate_attr_to_json", "area": "hcl_duplicate_keys", "args": ["-cj"], "stdin": hcl_duplicate_attr_doc},
        {"name": "hcl_block_then_scalar_error", "area": "errors", "args": ["-cj"], "stdin": hcl_block_then_scalar_doc},
        {"name": "empty_stdin_default", "area": "edge_cases", "args": [], "stdin": ""},
        {"name": "empty_json_to_yaml", "area": "edge_cases", "args": ["-jy"], "stdin": ""},
        {"name": "invalid_yaml_to_json", "area": "errors", "args": ["-yj"], "stdin": invalid_yaml},
        {"name": "invalid_yaml_merge_to_json", "area": "errors", "args": ["-yj"], "stdin": invalid_yaml_merge},
        {"name": "invalid_json_to_yaml", "area": "errors", "args": ["-jy"], "stdin": invalid_json},
        {"name": "invalid_toml_to_json", "area": "errors", "args": ["-tj"], "stdin": invalid_toml},
        {"name": "invalid_hcl_to_json", "area": "errors", "args": ["-cj"], "stdin": invalid_hcl},
        {"name": "invalid_hcl_to_yaml", "area": "errors", "args": ["-cy"], "stdin": invalid_hcl},
    ]


PROFILE_BUILDERS = {
    "generic-cli-smoke": generic_cli_smoke_cases,
    "yj": yj_cases,
}


def cases_for_profile(profile: str) -> list[dict[str, Any]]:
    try:
        return PROFILE_BUILDERS[profile]()
    except KeyError as exc:
        raise ValueError(f"Unsupported profile: {profile}") from exc


def load_cases_json(path: Path) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Load generated/sampled candidate CLI cases from a JSON file."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    metadata: dict[str, Any] = {}
    if isinstance(payload, list):
        profile = path.stem
        cases = payload
    elif isinstance(payload, dict):
        profile = str(payload.get("profile") or path.stem)
        raw_cases = payload.get("cases")
        if not isinstance(raw_cases, list):
            raise ValueError(f"Expected `cases` list in {path}")
        cases = raw_cases
        metadata = {key: value for key, value in payload.items() if key != "cases"}
    else:
        raise ValueError(f"Expected JSON list or object in {path}")

    normalized: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"Case {index} in {path} is not an object")
        normalized_case = dict(case)
        normalized_case.setdefault("name", f"case_{index:03d}")
        normalized_case.setdefault("area", "external_case_spec")
        normalized_case.setdefault("args", [])
        normalized_case.setdefault("stdin", "")
        if not isinstance(normalized_case["args"], list):
            raise ValueError(f"Case {normalized_case['name']} has non-list args")
        normalized.append(normalized_case)
    return profile, normalized, metadata


@contextlib.contextmanager
def case_runtime(case: dict[str, Any]) -> Iterator[tuple[Path, str]]:
    with tempfile.TemporaryDirectory(prefix="programbench-case-") as temp:
        cwd = Path(temp).resolve()
        for relative, content in dict(case.get("files") or {}).items():
            target = (cwd / str(relative)).resolve()
            if cwd not in target.parents:
                raise ValueError(f"unsafe case fixture path: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")
        for relative, content in dict(case.get("executable_files") or {}).items():
            target = (cwd / str(relative)).resolve()
            if cwd not in target.parents:
                raise ValueError(f"unsafe executable fixture path: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")
            target.chmod(0o755)
        for relative, content in dict(case.get("binary_files") or {}).items():
            target = (cwd / str(relative)).resolve()
            if cwd not in target.parents:
                raise ValueError(f"unsafe binary fixture path: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(str(content), validate=True))
        for relative, spec in dict(case.get("repeat_files") or {}).items():
            target = (cwd / str(relative)).resolve()
            if cwd not in target.parents:
                raise ValueError(f"unsafe repeated fixture path: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            segments = spec.get("segments")
            content = (
                "".join(str(segment.get("row") or "") * int(segment.get("count") or 0) for segment in segments)
                if isinstance(segments, list)
                else str(spec.get("prefix") or "")
                + str(spec.get("row") or "") * int(spec.get("count") or 0)
                + str(spec.get("suffix") or "")
            )
            encoding = str(spec.get("encoding") or "utf-8").lower()
            if encoding in {"latin-1", "latin1"}:
                target.write_bytes(content.encode("latin-1"))
            else:
                target.write_text(content, encoding="utf-8")
        git_fixture = case.get("git") if isinstance(case.get("git"), dict) else None
        if git_fixture and git_fixture.get("init") is True:
            git_env = {
                **os.environ,
                "GIT_AUTHOR_NAME": "ProgramBench",
                "GIT_AUTHOR_EMAIL": "programbench@example.invalid",
                "GIT_COMMITTER_NAME": "ProgramBench",
                "GIT_COMMITTER_EMAIL": "programbench@example.invalid",
                "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
                "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
            }
            branch = str(git_fixture.get("branch") or "main")
            subprocess.run(["git", "init", "-q", "-b", branch], cwd=cwd, env=git_env, check=True)
            if git_fixture.get("commit_all") is not False:
                subprocess.run(["git", "add", "-A"], cwd=cwd, env=git_env, check=True)
                subprocess.run(
                    ["git", "commit", "-q", "--allow-empty", "-m", "initial"],
                    cwd=cwd,
                    env=git_env,
                    check=True,
                )
            for relative, content in dict(git_fixture.get("staged_files") or {}).items():
                target = (cwd / str(relative)).resolve()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(content), encoding="utf-8")
            if git_fixture.get("staged_files"):
                subprocess.run(["git", "add", "-A"], cwd=cwd, env=git_env, check=True)
            for relative, content in dict(git_fixture.get("untracked_files") or {}).items():
                target = (cwd / str(relative)).resolve()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(content), encoding="utf-8")
        for relative, mode in dict(case.get("file_modes") or {}).items():
            target = (cwd / str(relative)).resolve()
            if cwd not in target.parents or not target.exists():
                raise ValueError(f"invalid file mode fixture path: {relative}")
            target.chmod(int(mode) & 0o777)
        http_fixture = case.get("http") if isinstance(case.get("http"), dict) else None
        server: http.server.ThreadingHTTPServer | None = None
        thread: threading.Thread | None = None
        url = ""
        if http_fixture:
            response = http_fixture

            class Handler(http.server.BaseHTTPRequestHandler):
                def do_GET(self) -> None:  # noqa: N802
                    if self.path != str(response.get("path") or "/"):
                        self.send_error(404)
                        return
                    body = str(response.get("body") or "").encode("utf-8")
                    self.send_response_only(int(response.get("status", 200)))
                    for key, value in dict(response.get("headers") or {}).items():
                        self.send_header(str(key), str(value))
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(body)

                do_POST = do_GET
                do_PUT = do_GET
                do_PATCH = do_GET
                do_DELETE = do_GET
                do_OPTIONS = do_GET
                do_HEAD = do_GET

                def log_message(self, format: str, *args: object) -> None:
                    return

                def date_time_string(self, timestamp: float | None = None) -> str:
                    return "Thu, 01 Jan 1970 00:00:00 GMT"

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}{str(response.get('path') or '/')}"
        try:
            yield cwd, url
        finally:
            if server:
                server.shutdown()
                server.server_close()
            if thread:
                thread.join(timeout=2)


def capture_observed_files(cwd: Path, case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Capture bounded post-execution filesystem behavior for strong silent-command oracles."""

    observations: dict[str, dict[str, Any]] = {}
    for relative in case.get("observe_files") or []:
        target = cwd / str(relative)
        if not target.exists() and not target.is_symlink():
            observations[str(relative)] = {"exists": False}
            continue
        resolved = target.resolve()
        if cwd != resolved and cwd not in resolved.parents:
            raise ValueError(f"observed file escaped case workspace: {relative}")
        if target.is_dir():
            observations[str(relative)] = {"exists": True, "kind": "directory"}
            continue
        if not target.is_file():
            observations[str(relative)] = {"exists": True, "kind": "other"}
            continue
        content = target.read_bytes()
        if len(content) > 1_000_000:
            raise ValueError(f"observed file exceeds 1 MB: {relative}")
        observations[str(relative)] = {
            "exists": True,
            "kind": "file",
            "content_base64": base64.b64encode(content).decode("ascii"),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    return observations


def attach_observed_files(observed: dict[str, Any], cwd: Path, case: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(observed)
    # Every case runs in a fresh temporary directory. Programs that report an
    # absolute form of their current directory would otherwise look
    # nondeterministic even when their behavior is identical. Preserve the
    # semantic fact that the program reported its work directory while
    # removing the runner-assigned path.
    encoded_cwd = str(cwd).encode("utf-8")
    for stream in ("stdout", "stderr"):
        enriched[stream] = bytes(enriched.get(stream) or b"").replace(
            encoded_cwd, b"{workdir}"
        )
    enriched["observed_files"] = capture_observed_files(cwd, case)
    return enriched


@contextlib.contextmanager
def fixed_workspace_reference_alias(reference_binary: Path) -> Iterator[None]:
    """Serialize and materialize nested `/workspace/executable` probes."""

    if os.name == "nt":
        yield
        return
    import fcntl

    lock = Path("/tmp/programbench-fixed-workspace.lock").open("a+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    alias = Path("/workspace/executable")
    owns_alias = reference_binary.resolve() != alias.resolve()
    try:
        alias.parent.mkdir(parents=True, exist_ok=True)
        if owns_alias:
            if alias.is_dir() and not alias.is_symlink():
                raise IsADirectoryError(alias)
            if alias.exists() or alias.is_symlink():
                alias.unlink()
            shutil.copy2(reference_binary, alias)
            alias.chmod(alias.stat().st_mode | 0o555)
        yield
    finally:
        if owns_alias and (alias.exists() or alias.is_symlink()) and not alias.is_dir():
            alias.unlink()
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def run_reference_case(reference_binary: Path, case: dict[str, Any], timeout: int) -> dict[str, Any]:
    with fixed_workspace_reference_alias(reference_binary):
        return _run_reference_case_unlocked(reference_binary, case, timeout)


def _run_reference_case_unlocked(reference_binary: Path, case: dict[str, Any], timeout: int) -> dict[str, Any]:
    env = os.environ.copy()
    env["TZ"] = "UTC"
    with case_runtime(case) as (cwd, http_url):
        if case.get("isolate_home_tmp") is True:
            isolated_home = cwd / ".case-home"
            isolated_tmp = cwd / ".case-tmp"
            isolated_home.mkdir()
            isolated_tmp.mkdir()
            env.update({"HOME": str(isolated_home), "TMPDIR": str(isolated_tmp)})
        env.update(
            {str(k): str(v).replace("{http_url}", http_url) for k, v in dict(case.get("env", {})).items()}
        )
        argv0 = str(case.get("argv0", "/workspace/executable")).replace("{http_url}", http_url)
        case_args = [str(item).replace("{http_url}", http_url) for item in case.get("args", [])]
        stdin = str(case.get("stdin", "")).replace("{http_url}", http_url).encode("utf-8")
        if case.get("terminal"):
            return normalize_http_runtime_observation(
                attach_observed_files(
                    run_terminal_case(
                        reference_binary=reference_binary,
                        argv0=argv0,
                        case_args=case_args,
                        stdin=stdin,
                        cwd=cwd,
                        env=env,
                        timeout=timeout,
                        terminal=dict(case["terminal"]),
                    ),
                    cwd,
                    case,
                ),
                http_url,
            )
        stdin_file = None
        try:
            if case.get("stdin_regular_file") is True:
                stdin_path = cwd / ".programbench-stdin"
                stdin_path.write_bytes(stdin)
                stdin_file = stdin_path.open("rb")
            proc = subprocess.Popen(
                [argv0, *case_args],
                executable=str(reference_binary),
                stdin=stdin_file if stdin_file is not None else subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=env,
                start_new_session=os.name != "nt",
            )
        except (OSError, ValueError) as exc:
            if stdin_file is not None:
                stdin_file.close()
            return attach_observed_files(
                {
                    "returncode": 127,
                    "stdout": b"",
                    "stderr": str(exc).encode("utf-8", errors="replace"),
                    "timed_out": False,
                    "launch_error": str(exc),
                },
                cwd,
                case,
            )
        try:
            stdout, stderr = proc.communicate(
                input=None if stdin_file is not None else stdin, timeout=timeout
            )
            if stdin_file is not None:
                stdin_file.close()
            return normalize_http_runtime_observation(
                attach_observed_files(
                    {
                        "returncode": proc.returncode,
                        "stdout": stdout,
                        "stderr": stderr,
                        "timed_out": False,
                    },
                    cwd,
                    case,
                ),
                http_url,
            )
        except subprocess.TimeoutExpired as exc:
            if stdin_file is not None:
                stdin_file.close()
            if os.name != "nt":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                proc.kill()
            try:
                final_stdout, final_stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                final_stdout, final_stderr = proc.communicate()
            stdout = final_stdout or (exc.stdout if isinstance(exc.stdout, bytes) else (exc.stdout or "").encode("utf-8", errors="replace"))
            stderr = final_stderr or (exc.stderr if isinstance(exc.stderr, bytes) else (exc.stderr or "").encode("utf-8", errors="replace"))
            return normalize_http_runtime_observation(
                attach_observed_files(
                    {
                        "returncode": 124,
                        "stdout": stdout,
                        "stderr": stderr,
                        "timed_out": True,
                    },
                    cwd,
                    case,
                ),
                http_url,
            )


def normalize_http_runtime_observation(observed: dict[str, Any], http_url: str) -> dict[str, Any]:
    if not http_url:
        return observed
    normalized = dict(observed)
    encoded_url = http_url.encode("utf-8")
    encoded_host = http_url.split("://", 1)[-1].split("/", 1)[0].encode("utf-8")
    for stream in ("stdout", "stderr"):
        data = bytes(observed.get(stream) or b"")
        normalized[stream] = data.replace(encoded_url, b"{http_url}").replace(encoded_host, b"{http_host}")
    return normalized


def run_terminal_case(
    *,
    reference_binary: Path,
    argv0: str,
    case_args: list[str],
    stdin: bytes,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    terminal: dict[str, Any],
) -> dict[str, Any]:
    """Run a CLI with stdout/stderr attached to a deterministic pseudo-terminal."""

    if os.name == "nt":
        raise RuntimeError("terminal cases require a Unix pseudo-terminal")
    import fcntl
    import pty
    import select
    import struct
    import termios

    kind = str(terminal.get("kind") or "generic")
    if kind not in {"generic", "iterm2", "kitty", "sixel"}:
        raise ValueError("terminal.kind must be generic, iterm2, kitty, or sixel")
    rows = max(2, min(200, int(terminal.get("rows", 24))))
    cols = max(2, min(400, int(terminal.get("cols", 80))))
    cell_width = max(1, min(100, int(terminal.get("cell_width", 10))))
    cell_height = max(1, min(100, int(terminal.get("cell_height", 20))))
    max_output = max(1024, min(16 * 1024 * 1024, int(terminal.get("max_output_bytes", 4 * 1024 * 1024))))
    stdin_delay = max(0.0, min(5.0, float(terminal.get("stdin_delay_seconds", 0))))
    stdin_mode = "pipe" if terminal.get("stdin_mode") == "pipe" else "pty"
    run_env = dict(env)
    run_env.update({
        "TERM_PROGRAM": "iTerm.app" if kind == "iterm2" else "ProgramBench",
        "TERM": str(terminal.get("term") or "xterm-256color"),
    })
    master, slave = pty.openpty()
    attrs = termios.tcgetattr(slave)
    attrs[3] &= ~termios.ECHO
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    proc = subprocess.Popen(
        [argv0, *case_args],
        executable=str(reference_binary),
        stdin=subprocess.PIPE if stdin_mode == "pipe" else slave,
        stdout=slave,
        stderr=slave,
        cwd=cwd,
        env=run_env,
        start_new_session=True,
        close_fds=True,
    )
    os.close(slave)
    def feed_stdin() -> None:
        if stdin_delay:
            time.sleep(stdin_delay)
        with contextlib.suppress(BrokenPipeError, OSError):
            if stdin_mode == "pipe":
                if proc.stdin is not None:
                    if stdin:
                        proc.stdin.write(stdin)
                    for event in terminal.get("input_events") or []:
                        delay = max(0.0, min(5.0, float(event.get("after_seconds", 0))))
                        if delay:
                            time.sleep(delay)
                        proc.stdin.write(str(event.get("data") or "").encode("utf-8"))
                    proc.stdin.close()
            else:
                if stdin:
                    os.write(master, stdin)
                for event in terminal.get("input_events") or []:
                    delay = max(0.0, min(5.0, float(event.get("after_seconds", 0))))
                    if delay:
                        time.sleep(delay)
                    os.write(master, str(event.get("data") or "").encode("utf-8"))
                if terminal.get("send_eof") is True:
                    os.write(master, b"\x04")
    feeder = threading.Thread(target=feed_stdin, daemon=True)
    feeder.start()
    output = bytearray()
    responses = {b"\x1b[14t": f"\x1b[4;{rows * cell_height};{cols * cell_width}t".encode("ascii")}
    if kind == "iterm2":
        responses[b"\x1b]1337;ReportCellSize\x07"] = f"\x1b]1337;ReportCellSize={cell_height};{cell_width}\x1b\\".encode("ascii")
    if kind == "kitty":
        responses[b"\x1b_Gi=1,a=q,t=d,f=24\x1b\\"] = b"\x1b_Gi=1;OK\x1b\\"
    if kind == "sixel":
        responses[b"\x1b[c"] = b"\x1b[?62;4;c"
    responded: set[bytes] = set()
    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        while proc.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                break
            ready, _, _ = select.select([master], [], [], min(0.1, remaining))
            if not ready:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            output.extend(chunk)
            if len(output) > max_output:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                raise RuntimeError("terminal case exceeded max_output_bytes")
            for query, response in responses.items():
                if query not in responded and query in output:
                    os.write(master, response)
                    responded.add(query)
        proc.wait(timeout=5)
        while True:
            ready, _, _ = select.select([master], [], [], 0.02)
            if not ready:
                break
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            output.extend(chunk)
    finally:
        feeder.join(timeout=6)
        os.close(master)
    return {
        "returncode": 124 if timed_out else proc.returncode,
        "stdout": bytes(output),
        "stderr": b"",
        "timed_out": timed_out,
    }


def observed_signature(
    observed: dict[str, Any], stdout_mode: str = "exact"
) -> tuple[int, bool, bytes, bytes, str]:
    stdout = observed["stdout"]
    if stdout_mode == "lines_unordered":
        stdout = b"\n".join(sorted(stdout.splitlines()))
    return (
        int(observed["returncode"]),
        bool(observed["timed_out"]),
        stdout,
        observed["stderr"],
        json.dumps(observed.get("observed_files") or {}, sort_keys=True, separators=(",", ":")),
    )


GO_LOG_PREFIX_RE = re.compile(
    rb"(?m)^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?(?: [^ \r\n]+\.go:\d+:)? "
)
BENCH_DURATION_RE = re.compile(
    rb"(?m)^(\s*(?:Total|Slowest|Fastest|Average):\s*)[0-9.]+(\s+secs\.)$"
)
BENCH_RATE_RE = re.compile(rb"(?m)^(\s*Requests/sec:\s*)[0-9.]+$")
BENCH_HISTOGRAM_RE = re.compile(rb"(?m)^(\s*)[0-9.]+(\s+\[\d+\]\s*\|.*)$")
BENCH_LATENCY_RE = re.compile(rb"(?m)^(\s*\d+% in\s*)[0-9.]+(\s+secs\.)$")
SEVENZIP_BENCH_NUMBER_RE = re.compile(rb"(?<![A-Za-z])[+-]?\d+(?:\.\d+)?(?:[A-Za-z/%]+)?")


EPOCH_NUMBER_RE = re.compile(rb"(?<!\d)[1-3]\d{9}(?!\d)")
ANSI_ESCAPE_RE = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*[@-~]|.)", re.DOTALL)
NINJA_GRAPHVIZ_ID_RE = re.compile(rb"\b0x[0-9a-fA-F]+\b")
NINJA_COMPDB_DIRECTORY_RE = re.compile(rb'("directory"\s*:\s*")[^"]*(")')
NINJA_STATS_NUMBER_RE = re.compile(rb"(?<![A-Za-z])\d+(?:\.\d+)?")


def normalize_ninja_graphviz_ids(value: bytes) -> bytes:
    return NINJA_GRAPHVIZ_ID_RE.sub(b"0xNODE", value)


def normalize_ninja_compdb_directory(value: bytes) -> bytes:
    return NINJA_COMPDB_DIRECTORY_RE.sub(rb'\g<1><WORKDIR>\g<2>', value)


def normalize_ninja_stats(value: bytes) -> bytes:
    return NINJA_STATS_NUMBER_RE.sub(b"<N>", value)


def normalize_terminal_control_tail(value: bytes) -> bytes:
    """Drop only timing-dependent trailing ANSI controls after visible output."""

    cursor = 0
    last_visible_end = 0
    for match in ANSI_ESCAPE_RE.finditer(value):
        chunk = value[cursor:match.start()]
        if chunk.strip():
            last_visible_end = match.start()
        cursor = match.end()
    if value[cursor:].strip():
        last_visible_end = len(value)
    if not last_visible_end or last_visible_end == len(value):
        return value
    return value[:last_visible_end] + b"\n<TERMINAL_CONTROL_TAIL>\n"


def normalize_terminal_final_screen(value: bytes, *, rows: int, cols: int) -> bytes:
    """Reconstruct the final visible ANSI terminal screen for exact comparison."""

    rows = max(2, min(200, int(rows)))
    cols = max(2, min(400, int(cols)))
    screen = [[" "] * cols for _ in range(rows)]
    row = col = 0
    saved = (0, 0)
    last_screen: list[list[str]] | None = None
    text = value.decode("utf-8", "replace")

    def clear_all() -> None:
        nonlocal screen
        screen = [[" "] * cols for _ in range(rows)]

    def snapshot() -> None:
        nonlocal last_screen
        last_screen = [line[:] for line in screen]

    index = 0
    while index < len(text):
        char = text[index]
        if char == "\x1b":
            if index + 1 < len(text) and text[index + 1] == "[":
                match = re.match(r"\x1b\[([0-?]*)([ -/]*)([@-~])", text[index:])
                if match:
                    params, _, command = match.groups()
                    index += len(match.group(0))
                    private = params.startswith("?")
                    raw_params = params[1:] if private else params
                    numbers = [int(item) if item else 0 for item in raw_params.split(";")] if raw_params else []
                    first = numbers[0] if numbers else 0
                    if private and first == 1049 and command == "h":
                        clear_all()
                        row = col = 0
                    elif private and first == 1049 and command == "l":
                        snapshot()
                    elif command in {"H", "f"}:
                        row = max(0, min(rows - 1, (numbers[0] if numbers else 1) - 1))
                        col = max(0, min(cols - 1, (numbers[1] if len(numbers) > 1 else 1) - 1))
                    elif command == "A":
                        row = max(0, row - (first or 1))
                    elif command == "B":
                        row = min(rows - 1, row + (first or 1))
                    elif command == "C":
                        col = min(cols - 1, col + (first or 1))
                    elif command == "D":
                        col = max(0, col - (first or 1))
                    elif command == "E":
                        row, col = min(rows - 1, row + (first or 1)), 0
                    elif command == "F":
                        row, col = max(0, row - (first or 1)), 0
                    elif command in {"G", "`"}:
                        col = max(0, min(cols - 1, (first or 1) - 1))
                    elif command == "d":
                        row = max(0, min(rows - 1, (first or 1) - 1))
                    elif command == "J":
                        if first in {2, 3}:
                            clear_all()
                        elif first == 0:
                            screen[row][col:] = [" "] * (cols - col)
                            for target in range(row + 1, rows):
                                screen[target] = [" "] * cols
                        elif first == 1:
                            for target in range(row):
                                screen[target] = [" "] * cols
                            screen[row][: col + 1] = [" "] * (col + 1)
                    elif command == "K":
                        if first == 0:
                            screen[row][col:] = [" "] * (cols - col)
                        elif first == 1:
                            screen[row][: col + 1] = [" "] * (col + 1)
                        elif first == 2:
                            screen[row] = [" "] * cols
                    elif command == "s":
                        saved = (row, col)
                    elif command == "u":
                        row, col = saved
                    continue
            if index + 1 < len(text) and text[index + 1] in {"7", "8"}:
                if text[index + 1] == "7":
                    saved = (row, col)
                else:
                    row, col = saved
                index += 2
                continue
            if index + 1 < len(text) and text[index + 1] == "]":
                end_bel = text.find("\x07", index + 2)
                end_st = text.find("\x1b\\", index + 2)
                endings = [item for item in (end_bel, end_st) if item >= 0]
                index = (min(endings) + (2 if min(endings) == end_st else 1)) if endings else len(text)
                continue
            index += 2
            continue
        if char == "\r":
            col = 0
        elif char == "\n":
            row = min(rows - 1, row + 1)
        elif char == "\b":
            col = max(0, col - 1)
        elif char >= " " and char != "\x7f":
            screen[row][col] = char
            col += 1
            if col >= cols:
                col = 0
                row = min(rows - 1, row + 1)
        index += 1

    selected = last_screen or screen
    lines = ["".join(line).rstrip() for line in selected]
    while lines and not lines[-1]:
        lines.pop()
    return ("<TERMINAL_FINAL_SCREEN>\n" + "\n".join(lines) + "\n").encode("utf-8")


TUI_WPM_METRIC_RE = re.compile(rb"(\b(?:avg\.|last)\s*)-?\d+(?=WPM\b)")
TUI_ACC_METRIC_RE = re.compile(rb"(\b(?:avg\.|last)\s*)-?\d+(?=% Acc\b)")


def normalize_tui_metrics(value: bytes) -> bytes:
    value = TUI_WPM_METRIC_RE.sub(rb"\g<1><WPM>", value)
    return TUI_ACC_METRIC_RE.sub(rb"\g<1><ACC>", value)


def normalize_benchmark_output(value: bytes) -> bytes:
    value = BENCH_DURATION_RE.sub(rb"\g<1>0.0000\g<2>", value)
    value = BENCH_RATE_RE.sub(rb"\g<1>0.0000", value)
    value = BENCH_HISTOGRAM_RE.sub(rb"\g<1>0.000\g<2>", value)
    value = BENCH_LATENCY_RE.sub(rb"\g<1>0.0000\g<2>", value)
    if b"Compressing  |" not in value:
        return value
    lines = value.splitlines(keepends=True)
    table_index = next(i for i, line in enumerate(lines) if b"Compressing  |" in line)
    system_index = next(
        (
            i
            for i, line in enumerate(lines[:table_index])
            if line.strip().startswith((b"Compiler:", b"Linux :", b"PageSize:"))
        ),
        table_index,
    )
    # 7-Zip emits a variable number of host-calibration lines before the
    # benchmark table.  Replacing each line independently is insufficient:
    # the line count itself can change under transient scheduler load.
    lines = lines[:system_index] + [b"<SYSTEM>\n", b"\n"] + lines[table_index:]
    normalized: list[bytes] = []
    in_table = False
    for line in lines:
        if b"Compressing  |" in line:
            in_table = True
        if in_table and any(48 <= byte <= 57 for byte in line):
            line = SEVENZIP_BENCH_NUMBER_RE.sub(b"<N>", line)
            line = b" ".join(line.split()) + b"\n"
        normalized.append(line)
    return b"".join(normalized)


def normalize_observed(case: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(observed)
    if case.get("normalize_go_log_prefix") is True:
        normalized["stderr"] = GO_LOG_PREFIX_RE.sub(b"", bytes(observed.get("stderr") or b""))
    if case.get("normalize_benchmark_output") is True:
        normalized["stdout"] = normalize_benchmark_output(bytes(observed.get("stdout") or b""))
        normalized["stderr"] = normalize_benchmark_output(bytes(normalized.get("stderr") or b""))
    if case.get("normalize_epoch_numbers") is True:
        normalized["stdout"] = EPOCH_NUMBER_RE.sub(b"<EPOCH>", bytes(normalized.get("stdout") or b""))
        normalized["stderr"] = EPOCH_NUMBER_RE.sub(b"<EPOCH>", bytes(normalized.get("stderr") or b""))
    if (case.get("terminal") or {}).get("output_mode") == "control_tail_trim":
        normalized["stdout"] = normalize_terminal_control_tail(bytes(normalized.get("stdout") or b""))
    if (case.get("terminal") or {}).get("output_mode") == "final_screen":
        terminal = case.get("terminal") or {}
        normalized["stdout"] = normalize_terminal_final_screen(
            bytes(normalized.get("stdout") or b""), rows=int(terminal.get("rows", 24)), cols=int(terminal.get("cols", 80))
        )
    if case.get("normalize_tui_metrics") is True:
        normalized["stdout"] = normalize_tui_metrics(bytes(normalized.get("stdout") or b""))
    if case.get("normalize_ninja_graphviz_ids") is True:
        normalized["stdout"] = normalize_ninja_graphviz_ids(bytes(normalized.get("stdout") or b""))
    if case.get("normalize_ninja_compdb_directory") is True:
        normalized["stdout"] = normalize_ninja_compdb_directory(bytes(normalized.get("stdout") or b""))
    if case.get("normalize_ninja_stats") is True:
        normalized["stdout"] = normalize_ninja_stats(bytes(normalized.get("stdout") or b""))
    return normalized


def has_volatile_output(observed: dict[str, Any]) -> bool:
    return bool(VOLATILE_OUTPUT_RE.search(observed["stdout"]) or VOLATILE_OUTPUT_RE.search(observed["stderr"]))


def write_generated_pytest(bundle_root: Path, profile: str, cases: list[dict[str, Any]]) -> None:
    test_path = bundle_root / "eval" / "tests" / "test_generated_cli_oracle.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    header = '''"""Generated black-box CLI oracle tests."""

from __future__ import annotations

import contextlib
import base64
import http.server
import json
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parents[1]
MANIFEST_PATH = EVAL_DIR / "generated_cli_manifest.json"
if not MANIFEST_PATH.exists():
    MANIFEST_PATH = EVAL_DIR / "generated_yj_manifest.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
FIXTURE_DIR = EVAL_DIR / "fixtures" / MANIFEST.get("fixture_subdir", "generated_cli")
CASES = MANIFEST["cases"]


@contextlib.contextmanager
def _case_runtime(case: dict, tmp_path: Path):
    for relative, content in case.get("files", {}).items():
        target = (tmp_path / relative).resolve()
        if tmp_path.resolve() not in target.parents:
            raise AssertionError(f"unsafe fixture path: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    for relative, content in case.get("executable_files", {}).items():
        target = (tmp_path / relative).resolve()
        if tmp_path.resolve() not in target.parents:
            raise AssertionError(f"unsafe executable fixture path: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        target.chmod(0o755)
    for relative, content in case.get("binary_files", {}).items():
        target = (tmp_path / relative).resolve()
        if tmp_path.resolve() not in target.parents:
            raise AssertionError(f"unsafe binary fixture path: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(content, validate=True))
    for relative, spec in case.get("repeat_files", {}).items():
        target = (tmp_path / relative).resolve()
        if tmp_path.resolve() not in target.parents:
            raise AssertionError(f"unsafe repeated fixture path: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        segments = spec.get("segments")
        content = (
            "".join(segment.get("row", "") * int(segment.get("count", 0)) for segment in segments)
            if isinstance(segments, list)
            else spec.get("prefix", "") + spec.get("row", "") * int(spec.get("count", 0)) + spec.get("suffix", "")
        )
        encoding = str(spec.get("encoding") or "utf-8").lower()
        if encoding in {"latin-1", "latin1"}:
            target.write_bytes(content.encode("latin-1"))
        else:
            target.write_text(content, encoding="utf-8")
    git_fixture = case.get("git") or None
    if git_fixture and git_fixture.get("init") is True:
        git_env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "ProgramBench",
            "GIT_AUTHOR_EMAIL": "programbench@example.invalid",
            "GIT_COMMITTER_NAME": "ProgramBench",
            "GIT_COMMITTER_EMAIL": "programbench@example.invalid",
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
        }
        subprocess.run(["git", "init", "-q", "-b", git_fixture.get("branch", "main")], cwd=tmp_path, env=git_env, check=True)
        if git_fixture.get("commit_all") is not False:
            subprocess.run(["git", "add", "-A"], cwd=tmp_path, env=git_env, check=True)
            subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "initial"], cwd=tmp_path, env=git_env, check=True)
        for relative, content in git_fixture.get("staged_files", {}).items():
            target = (tmp_path / relative).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        if git_fixture.get("staged_files"):
            subprocess.run(["git", "add", "-A"], cwd=tmp_path, env=git_env, check=True)
        for relative, content in git_fixture.get("untracked_files", {}).items():
            target = (tmp_path / relative).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    for relative, mode in case.get("file_modes", {}).items():
        target = (tmp_path / relative).resolve()
        if tmp_path.resolve() not in target.parents or not target.exists():
            raise AssertionError(f"invalid file mode fixture path: {relative}")
        target.chmod(int(mode) & 0o777)
    response = case.get("http") or None
    server = None
    thread = None
    url = ""
    if response:
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != response.get("path", "/"):
                    self.send_error(404)
                    return
                body = response.get("body", "").encode("utf-8")
                self.send_response_only(response.get("status", 200))
                for key, value in response.get("headers", {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            do_POST = do_GET
            do_PUT = do_GET
            do_PATCH = do_GET
            do_DELETE = do_GET
            do_OPTIONS = do_GET
            do_HEAD = do_GET

            def log_message(self, format, *args):
                return

            def date_time_string(self, timestamp=None):
                return "Thu, 01 Jan 1970 00:00:00 GMT"

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}{response.get('path', '/')}"
    try:
        yield url
    finally:
        if server:
            server.shutdown()
            server.server_close()
        if thread:
            thread.join(timeout=2)


def _case_timeout(case: dict) -> float:
    timeout = float(case.get("timeout", 5))
    cap = os.environ.get("PROGRAMBENCH_CASE_TIMEOUT_CAP", "").strip()
    if cap:
        timeout = min(timeout, max(0.1, float(cap)))
    return timeout


def _execute_case(index: int, tmp_path: Path) -> tuple[dict, Path, int, bytes, bytes, bytes, bytes]:
    case = CASES[index]
    executable = WORKSPACE / "executable"
    if not executable.exists():
        raise AssertionError(f"missing executable at {executable}")

    stdin_template = (FIXTURE_DIR / case["stdin_file"]).read_bytes()
    expected_stdout = (FIXTURE_DIR / case["stdout_file"]).read_bytes()
    expected_stderr = (FIXTURE_DIR / case["stderr_file"]).read_bytes()
    env = os.environ.copy()
    env["TZ"] = "UTC"
    with _case_runtime(case, tmp_path) as http_url:
        if case.get("isolate_home_tmp") is True:
            isolated_home = tmp_path / ".case-home"
            isolated_tmp = tmp_path / ".case-tmp"
            isolated_home.mkdir(exist_ok=True)
            isolated_tmp.mkdir(exist_ok=True)
            env.update({"HOME": str(isolated_home), "TMPDIR": str(isolated_tmp)})
        env.update({key: value.replace("{http_url}", http_url) for key, value in case.get("env", {}).items()})
        argv0 = case.get("argv0", "/workspace/executable").replace("{http_url}", http_url)
        args = [item.replace("{http_url}", http_url) for item in case["args"]]
        stdin = stdin_template.replace(b"{http_url}", http_url.encode("utf-8"))
        if case.get("terminal"):
            returncode, stdout, stderr = _run_terminal(
                executable=executable,
                argv0=argv0,
                args=args,
                stdin=stdin,
                cwd=tmp_path,
                env=env,
                timeout=_case_timeout(case),
                terminal=case["terminal"],
            )
        else:
            stdin_file = None
            if case.get("stdin_regular_file") is True:
                stdin_path = tmp_path / ".programbench-stdin"
                stdin_path.write_bytes(stdin)
                stdin_file = stdin_path.open("rb")
            proc = subprocess.Popen(
                [argv0, *args],
                executable=str(executable),
                stdin=stdin_file if stdin_file is not None else subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=tmp_path,
                env=env,
                start_new_session=os.name != "nt",
            )
            try:
                stdout, stderr = proc.communicate(
                    input=None if stdin_file is not None else stdin,
                    timeout=_case_timeout(case),
                )
                if stdin_file is not None:
                    stdin_file.close()
            except subprocess.TimeoutExpired:
                if stdin_file is not None:
                    stdin_file.close()
                if os.name != "nt":
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
                proc.communicate()
                raise
            returncode = proc.returncode
        if http_url:
            encoded_url = http_url.encode("utf-8")
            encoded_host = http_url.split("://", 1)[-1].split("/", 1)[0].encode("utf-8")
            stdout = stdout.replace(encoded_url, b"{http_url}").replace(encoded_host, b"{http_host}")
            stderr = stderr.replace(encoded_url, b"{http_url}").replace(encoded_host, b"{http_host}")
        encoded_workdir = str(tmp_path).encode("utf-8")
        stdout = stdout.replace(encoded_workdir, b"{workdir}")
        stderr = stderr.replace(encoded_workdir, b"{workdir}")

    return case, executable, returncode, stdout, stderr, expected_stdout, expected_stderr


def _run_terminal(*, executable: Path, argv0: str, args: list[str], stdin: bytes, cwd: Path,
                  env: dict[str, str], timeout: int, terminal: dict) -> tuple[int, bytes, bytes]:
    if os.name == "nt":
        raise RuntimeError("terminal cases require a Unix pseudo-terminal")
    import fcntl
    import pty
    import select
    import struct
    import termios

    kind = str(terminal.get("kind") or "generic")
    if kind not in {"generic", "iterm2", "kitty", "sixel"}:
        raise ValueError("terminal.kind must be generic, iterm2, kitty, or sixel")
    rows = max(2, min(200, int(terminal.get("rows", 24))))
    cols = max(2, min(400, int(terminal.get("cols", 80))))
    cell_width = max(1, min(100, int(terminal.get("cell_width", 10))))
    cell_height = max(1, min(100, int(terminal.get("cell_height", 20))))
    max_output = max(1024, min(16 * 1024 * 1024, int(terminal.get("max_output_bytes", 4 * 1024 * 1024))))
    stdin_delay = max(0.0, min(5.0, float(terminal.get("stdin_delay_seconds", 0))))
    stdin_mode = "pipe" if terminal.get("stdin_mode") == "pipe" else "pty"
    run_env = dict(env)
    run_env.update({
        "TERM_PROGRAM": "iTerm.app" if kind == "iterm2" else "ProgramBench",
        "TERM": str(terminal.get("term") or "xterm-256color"),
    })
    master, slave = pty.openpty()
    attrs = termios.tcgetattr(slave)
    attrs[3] &= ~termios.ECHO
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    proc = subprocess.Popen(
        [argv0, *args], executable=str(executable),
        stdin=subprocess.PIPE if stdin_mode == "pipe" else slave, stdout=slave, stderr=slave,
        cwd=cwd, env=run_env, start_new_session=True, close_fds=True,
    )
    os.close(slave)
    def feed_stdin() -> None:
        if stdin_delay:
            time.sleep(stdin_delay)
        with contextlib.suppress(BrokenPipeError, OSError):
            if stdin_mode == "pipe":
                if proc.stdin is not None:
                    if stdin:
                        proc.stdin.write(stdin)
                    for event in terminal.get("input_events") or []:
                        delay = max(0.0, min(5.0, float(event.get("after_seconds", 0))))
                        if delay:
                            time.sleep(delay)
                        proc.stdin.write(str(event.get("data") or "").encode("utf-8"))
                    proc.stdin.close()
            else:
                if stdin:
                    os.write(master, stdin)
                for event in terminal.get("input_events") or []:
                    delay = max(0.0, min(5.0, float(event.get("after_seconds", 0))))
                    if delay:
                        time.sleep(delay)
                    os.write(master, str(event.get("data") or "").encode("utf-8"))
                if terminal.get("send_eof") is True:
                    os.write(master, b"\\x04")
    feeder = threading.Thread(target=feed_stdin, daemon=True)
    feeder.start()
    output = bytearray()
    responses = {b"\\x1b[14t": f"\\x1b[4;{rows * cell_height};{cols * cell_width}t".encode("ascii")}
    if kind == "iterm2":
        responses[b"\\x1b]1337;ReportCellSize\\x07"] = f"\\x1b]1337;ReportCellSize={cell_height};{cell_width}\\x1b\\\\".encode("ascii")
    if kind == "kitty":
        responses[b"\\x1b_Gi=1,a=q,t=d,f=24\\x1b\\\\"] = b"\\x1b_Gi=1;OK\\x1b\\\\"
    if kind == "sixel":
        responses[b"\\x1b[c"] = b"\\x1b[?62;4;c"
    responded: set[bytes] = set()
    deadline = time.monotonic() + timeout
    try:
        while proc.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
                raise subprocess.TimeoutExpired([argv0, *args], timeout, output=bytes(output))
            ready, _, _ = select.select([master], [], [], min(0.1, remaining))
            if not ready:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            output.extend(chunk)
            if len(output) > max_output:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                raise RuntimeError("terminal case exceeded max_output_bytes")
            for query, response in responses.items():
                if query not in responded and query in output:
                    os.write(master, response)
                    responded.add(query)
        proc.wait(timeout=5)
        while True:
            ready, _, _ = select.select([master], [], [], 0.02)
            if not ready:
                break
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            output.extend(chunk)
    finally:
        feeder.join(timeout=6)
        os.close(master)
    return proc.returncode, bytes(output), b""


GO_LOG_PREFIX_RE = re.compile(
    rb"(?m)^\\d{4}/\\d{2}/\\d{2} \\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?: [^ \\r\\n]+\\.go:\\d+:)? "
)
BENCH_DURATION_RE = re.compile(
    rb"(?m)^(\\s*(?:Total|Slowest|Fastest|Average):\\s*)[0-9.]+(\\s+secs\\.)$"
)
BENCH_RATE_RE = re.compile(rb"(?m)^(\\s*Requests/sec:\\s*)[0-9.]+$")
BENCH_HISTOGRAM_RE = re.compile(rb"(?m)^(\\s*)[0-9.]+(\\s+\\[\\d+\\]\\s*\\|.*)$")
BENCH_LATENCY_RE = re.compile(rb"(?m)^(\\s*\\d+% in\\s*)[0-9.]+(\\s+secs\\.)$")
SEVENZIP_BENCH_NUMBER_RE = re.compile(rb"(?<![A-Za-z])[+-]?\\d+(?:\\.\\d+)?(?:[A-Za-z/%]+)?")


EPOCH_NUMBER_RE = re.compile(rb"(?<!\\d)[1-3]\\d{9}(?!\\d)")
ANSI_ESCAPE_RE = re.compile(rb"\\x1b(?:\\[[0-?]*[ -/]*[@-~]|.)", re.DOTALL)
NINJA_GRAPHVIZ_ID_RE = re.compile(rb"\\b0x[0-9a-fA-F]+\\b")
NINJA_COMPDB_DIRECTORY_RE = re.compile(rb'("directory"\\s*:\\s*")[^"]*(")')
NINJA_STATS_NUMBER_RE = re.compile(rb"(?<![A-Za-z])\\d+(?:\\.\\d+)?")


def _normalize_ninja_graphviz_ids(value: bytes) -> bytes:
    return NINJA_GRAPHVIZ_ID_RE.sub(b"0xNODE", value)


def _normalize_ninja_compdb_directory(value: bytes) -> bytes:
    return NINJA_COMPDB_DIRECTORY_RE.sub(rb'\\g<1><WORKDIR>\\g<2>', value)


def _normalize_ninja_stats(value: bytes) -> bytes:
    return NINJA_STATS_NUMBER_RE.sub(b"<N>", value)


def _normalize_terminal_control_tail(value: bytes) -> bytes:
    cursor = 0
    last_visible_end = 0
    for match in ANSI_ESCAPE_RE.finditer(value):
        chunk = value[cursor:match.start()]
        if chunk.strip():
            last_visible_end = match.start()
        cursor = match.end()
    if value[cursor:].strip():
        last_visible_end = len(value)
    if not last_visible_end or last_visible_end == len(value):
        return value
    return value[:last_visible_end] + b"\\n<TERMINAL_CONTROL_TAIL>\\n"


def _normalize_benchmark_output(value: bytes) -> bytes:
    value = BENCH_DURATION_RE.sub(rb"\\g<1>0.0000\\g<2>", value)
    value = BENCH_RATE_RE.sub(rb"\\g<1>0.0000", value)
    value = BENCH_HISTOGRAM_RE.sub(rb"\\g<1>0.000\\g<2>", value)
    value = BENCH_LATENCY_RE.sub(rb"\\g<1>0.0000\\g<2>", value)
    if b"Compressing  |" not in value:
        return value
    lines = value.splitlines(keepends=True)
    table_index = next(i for i, line in enumerate(lines) if b"Compressing  |" in line)
    system_index = next(
        (
            i
            for i, line in enumerate(lines[:table_index])
            if line.strip().startswith((b"Compiler:", b"Linux :", b"PageSize:"))
        ),
        table_index,
    )
    lines = lines[:system_index] + [b"<SYSTEM>\\n", b"\\n"] + lines[table_index:]
    normalized = []
    in_table = False
    for line in lines:
        if b"Compressing  |" in line:
            in_table = True
        if in_table and any(48 <= byte <= 57 for byte in line):
            line = SEVENZIP_BENCH_NUMBER_RE.sub(b"<N>", line)
            line = b" ".join(line.split()) + b"\\n"
        normalized.append(line)
    return b"".join(normalized)


TUI_WPM_METRIC_RE = re.compile(rb"(\\b(?:avg\\.|last)\\s*)-?\\d+(?=WPM\\b)")
TUI_ACC_METRIC_RE = re.compile(rb"(\\b(?:avg\\.|last)\\s*)-?\\d+(?=% Acc\\b)")


def _normalize_tui_metrics(value: bytes) -> bytes:
    value = TUI_WPM_METRIC_RE.sub(rb"\\g<1><WPM>", value)
    return TUI_ACC_METRIC_RE.sub(rb"\\g<1><ACC>", value)


def _assert_stdout(case: dict, stdout: bytes, expected_stdout: bytes) -> None:
    if case.get("stdout_mode") == "lines_unordered":
        assert sorted(stdout.splitlines()) == sorted(expected_stdout.splitlines())
    else:
        assert stdout == expected_stdout


def _assert_observed_files(case: dict, tmp_path: Path) -> None:
    root = tmp_path.resolve()
    for relative, expected in case.get("observed_files", {}).items():
        target = tmp_path / relative
        exists = target.exists() or target.is_symlink()
        assert exists is bool(expected.get("exists")), f"post-run existence mismatch: {relative}"
        if not exists:
            continue
        resolved = target.resolve()
        assert resolved == root or root in resolved.parents, f"post-run path escaped workspace: {relative}"
        kind = expected.get("kind")
        if kind == "directory":
            assert target.is_dir(), f"expected post-run directory: {relative}"
        elif kind == "file":
            assert target.is_file(), f"expected post-run file: {relative}"
            expected_bytes = base64.b64decode(expected.get("content_base64", ""), validate=True)
            assert target.read_bytes() == expected_bytes, f"post-run bytes mismatch: {relative}"
'''
    header += "\n\n" + inspect.getsource(normalize_terminal_final_screen).replace(
        "def normalize_terminal_final_screen", "def _normalize_terminal_final_screen", 1
    )
    functions: list[str] = []
    for index, case in enumerate(cases):
        name = slug(str(case.get("name") or f"case_{index:04d}"))
        functions.append(
            f'''

def test_{index:04d}_{name}(tmp_path: Path) -> None:
    """CATCHES: implementations whose exact exit status, stdout, or stderr differs from the reference behavior."""
    case, executable, returncode, stdout, stderr, expected_stdout, expected_stderr = _execute_case({index}, tmp_path)
    assert executable.exists(), f"missing executable at {{executable}}"
    assert returncode == case["returncode"]
    if case.get("normalize_benchmark_output") is True:
        stdout = _normalize_benchmark_output(stdout)
        stderr = _normalize_benchmark_output(stderr)
    if case.get("normalize_epoch_numbers") is True:
        stdout = EPOCH_NUMBER_RE.sub(b"<EPOCH>", stdout)
        stderr = EPOCH_NUMBER_RE.sub(b"<EPOCH>", stderr)
    if (case.get("terminal") or {{}}).get("output_mode") == "control_tail_trim":
        stdout = _normalize_terminal_control_tail(stdout)
    if (case.get("terminal") or {{}}).get("output_mode") == "final_screen":
        terminal = case.get("terminal") or {{}}
        stdout = _normalize_terminal_final_screen(
            stdout, rows=int(terminal.get("rows", 24)), cols=int(terminal.get("cols", 80))
        )
    if case.get("normalize_tui_metrics") is True:
        stdout = _normalize_tui_metrics(stdout)
    if case.get("normalize_ninja_graphviz_ids") is True:
        stdout = _normalize_ninja_graphviz_ids(stdout)
    if case.get("normalize_ninja_compdb_directory") is True:
        stdout = _normalize_ninja_compdb_directory(stdout)
    if case.get("normalize_ninja_stats") is True:
        stdout = _normalize_ninja_stats(stdout)
    _assert_stdout(case, stdout, expected_stdout)
    if case.get("normalize_go_log_prefix") is True:
        stderr = GO_LOG_PREFIX_RE.sub(b"", stderr)
    assert stderr == expected_stderr
    _assert_observed_files(case, tmp_path)
'''
        )
    test_path.write_text(header + "".join(functions), encoding="utf-8")


def write_readme(bundle_root: Path, instance_id: str, profile: str, case_count: int) -> None:
    (bundle_root / "README.md").write_text(
        f"""# Generated CLI Oracle Tests

Instance: `{instance_id}`
Profile: `{profile}`
Cases: `{case_count}`

These tests were generated from cleanroom-visible behavior: the ProgramBench
reference executable was copied from the cleanroom image, then profile-provided
CLI cases were executed against it to capture exact stdout, stderr, and exit
codes. The bundle does not include ProgramBench official test blobs or upstream
source code.

Run with a workspace-level `./executable`:

```bash
python3 -m pytest -q eval/tests
```
""",
        encoding="utf-8",
    )


def generate_bundle(args: argparse.Namespace) -> dict[str, Any]:
    image = args.image or image_name_from_instance_id(args.instance_id)
    safe_instance = args.instance_id.replace("/", "_")
    suite_label = args.suite_label or f"generated_{args.profile}_oracle"
    work_dir = args.work_root / safe_instance / suite_label
    out_dir = args.output_root / safe_instance / suite_label
    logs_dir = work_dir / "logs"
    reference_binary = work_dir / "reference_executable"
    readme_copy = work_dir / "README.md"
    bundle_root = out_dir / "oracle_tests"

    if work_dir.exists():
        if not args.overwrite:
            raise FileExistsError(f"Work dir exists: {work_dir}")
        shutil.rmtree(work_dir)
    if bundle_root.exists():
        if not args.overwrite:
            raise FileExistsError(f"Bundle exists: {bundle_root}")
        shutil.rmtree(bundle_root)
    logs_dir.mkdir(parents=True, exist_ok=True)

    binary_result = materialize_cleanroom_file(
        image=image,
        docker=args.docker,
        source_path="/workspace/executable",
        dest=reference_binary,
        logs_dir=logs_dir,
    )
    if binary_result["returncode"] != 0:
        raise RuntimeError(f"cleanroom executable materialization failed: {binary_result}")
    # Docker preserves the PB cleanroom executable's execute-only mode. The
    # fixed-workspace alias must be copied, so grant the owning experiment
    # process read permission without broadening group/other access.
    reference_binary.chmod(reference_binary.stat().st_mode | stat.S_IRUSR | stat.S_IXUSR)

    readme_result = materialize_cleanroom_file(
        image=image,
        docker=args.docker,
        source_path="/workspace/README.md",
        dest=readme_copy,
        logs_dir=logs_dir,
    )

    case_spec_metadata: dict[str, Any] = {}
    case_spec_path: Path | None = None
    if args.cases_json:
        case_spec_path = args.cases_json.expanduser().resolve()
        profile, cases, case_spec_metadata = load_cases_json(case_spec_path)
        case_source = "cases_json"
    else:
        profile = args.profile
        cases = cases_for_profile(profile)
        case_source = "builtin_profile"
    fixture_subdir = slug(profile).replace("_", "-")
    fixture_dir = bundle_root / "eval" / "fixtures" / fixture_subdir
    fixture_dir.mkdir(parents=True, exist_ok=True)
    manifest_cases: list[dict[str, Any]] = []
    skipped_cases: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        name = slug(case["name"])
        case_timeout = max(1, min(60, int(case.get("timeout_seconds", args.case_timeout))))
        observed = normalize_observed(case, run_reference_case(reference_binary, case, case_timeout))
        if observed.get("launch_error"):
            skipped_cases.append(
                {
                    "name": case["name"],
                    "reason": "reference_launch_error",
                    "error": observed["launch_error"],
                }
            )
            continue
        if observed["timed_out"] and args.skip_timed_out_cases:
            skipped_cases.append(
                {
                    "name": name,
                    "area": case.get("area", "unknown"),
                    "args": list(case.get("args", [])),
                    "reason": "reference_timeout",
                    "timeout": case_timeout,
                    "stdout_sha256": sha256_bytes(observed["stdout"]),
                    "stderr_sha256": sha256_bytes(observed["stderr"]),
                }
            )
            continue
        if args.skip_volatile_output_cases and has_volatile_output(observed):
            skipped_cases.append(
                {
                    "name": name,
                    "area": case.get("area", "unknown"),
                    "args": list(case.get("args", [])),
                    "reason": "volatile_reference_output",
                    "timeout": case_timeout,
                    "stdout_sha256": sha256_bytes(observed["stdout"]),
                    "stderr_sha256": sha256_bytes(observed["stderr"]),
                }
            )
            continue
        deterministic = True
        mismatch_index = None
        for rerun_index in range(args.determinism_reruns):
            if args.determinism_rerun_delay > 0:
                time.sleep(args.determinism_rerun_delay)
            rerun = normalize_observed(case, run_reference_case(reference_binary, case, case_timeout))
            stdout_mode = str(case.get("stdout_mode") or "exact")
            if observed_signature(rerun, stdout_mode) != observed_signature(observed, stdout_mode):
                deterministic = False
                mismatch_index = rerun_index
                break
        if not deterministic and args.skip_nondeterministic_cases:
            skipped_cases.append(
                {
                    "name": name,
                    "area": case.get("area", "unknown"),
                    "args": list(case.get("args", [])),
                    "reason": "nondeterministic_reference_output",
                    "rerun_index": mismatch_index,
                    "timeout": case_timeout,
                    "stdout_sha256": sha256_bytes(observed["stdout"]),
                    "stderr_sha256": sha256_bytes(observed["stderr"]),
                }
            )
            continue
        stdin_bytes = str(case.get("stdin", "")).encode("utf-8")
        stdout = observed["stdout"]
        stderr = observed["stderr"]
        stdin_name = f"{index:03d}_{name}.stdin"
        stdout_name = f"{index:03d}_{name}.stdout"
        stderr_name = f"{index:03d}_{name}.stderr"
        (fixture_dir / stdin_name).write_bytes(stdin_bytes)
        (fixture_dir / stdout_name).write_bytes(stdout)
        (fixture_dir / stderr_name).write_bytes(stderr)
        manifest_cases.append(
            {
                "name": name,
                "area": case.get("area", "unknown"),
                "args": list(case.get("args", [])),
                "argv0": case.get("argv0", "/workspace/executable"),
                "env": case.get("env", {}),
                "files": case.get("files", {}),
                "executable_files": case.get("executable_files", {}),
                "binary_files": case.get("binary_files", {}),
                "repeat_files": case.get("repeat_files", {}),
                "observe_files": case.get("observe_files", []),
                "observed_files": observed.get("observed_files", {}),
                "file_modes": case.get("file_modes", {}),
                "git": case.get("git", {}),
                "http": case.get("http", {}),
                "terminal": case.get("terminal", {}),
                "isolate_home_tmp": case.get("isolate_home_tmp") is True,
                "stdin_regular_file": case.get("stdin_regular_file") is True,
                "normalize_go_log_prefix": case.get("normalize_go_log_prefix") is True,
                "normalize_benchmark_output": case.get("normalize_benchmark_output") is True,
                "normalize_epoch_numbers": case.get("normalize_epoch_numbers") is True,
                "normalize_tui_metrics": case.get("normalize_tui_metrics") is True,
                "normalize_ninja_graphviz_ids": case.get("normalize_ninja_graphviz_ids") is True,
                "normalize_ninja_compdb_directory": case.get("normalize_ninja_compdb_directory") is True,
                "normalize_ninja_stats": case.get("normalize_ninja_stats") is True,
                "stdout_mode": case.get("stdout_mode", "exact"),
                "timeout": case_timeout,
                "returncode": observed["returncode"],
                "timed_out": observed["timed_out"],
                "stdin_file": stdin_name,
                "stdout_file": stdout_name,
                "stderr_file": stderr_name,
                "stdin_sha256": sha256_bytes(stdin_bytes),
                "stdout_sha256": sha256_bytes(stdout),
                "stderr_sha256": sha256_bytes(stderr),
                "stdout_bytes": len(stdout),
                "stderr_bytes": len(stderr),
            }
        )

    manifest = {
        "instance_id": args.instance_id,
        "profile": profile,
        "suite_label": suite_label,
        "method": "cleanroom_reference_binary_black_box_capture",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "image": image,
        "case_source": case_source,
        "case_spec_path": str(case_spec_path) if case_spec_path else None,
        "case_spec_metadata": case_spec_metadata,
        "case_count": len(manifest_cases),
        "candidate_case_count": len(cases),
        "skipped_case_count": len(skipped_cases),
        "determinism_reruns": args.determinism_reruns,
        "skipped_cases": skipped_cases,
        "fixture_subdir": fixture_subdir,
        "readme_copied": readme_result["returncode"] == 0,
        "cases": manifest_cases,
    }
    write_generated_pytest(bundle_root, profile, manifest_cases)
    write_readme(bundle_root, args.instance_id, profile, len(manifest_cases))
    write_json(bundle_root / "eval" / MANIFEST_NAME, manifest)
    if args.profile == "yj":
        write_json(bundle_root / "eval" / "generated_yj_manifest.json", manifest)
    write_json(out_dir / "quality_report.json", {"bundle_root": str(bundle_root), **manifest})
    return {"bundle_root": str(bundle_root), "quality_report": str(out_dir / "quality_report.json"), **manifest}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance_id")
    parser.add_argument("--profile", default="yj", help=f"Built-in profile name. Available: {', '.join(sorted(PROFILE_BUILDERS))}")
    parser.add_argument("--cases-json", type=Path, help="JSON list/object of candidate CLI cases to capture with the same engine.")
    parser.add_argument("--suite-label")
    parser.add_argument("--image")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--work-root", type=Path, default=Path("/tmp/programbench_generated_cli_oracles"))
    parser.add_argument("--output-root", type=Path, default=Path("reports/programbench_generated_oracles"))
    parser.add_argument("--case-timeout", type=int, default=5)
    parser.add_argument("--skip-timed-out-cases", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--determinism-reruns", type=int, default=0)
    parser.add_argument("--determinism-rerun-delay", type=float, default=0.0)
    parser.add_argument("--skip-nondeterministic-cases", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--skip-volatile-output-cases", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    args.output_root = args.output_root if args.output_root.is_absolute() else (REPO_ROOT / args.output_root).resolve()
    result = generate_bundle(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
