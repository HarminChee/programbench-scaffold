#!/usr/bin/env python3
"""Generate executable black-box CLI oracle tests from a reference binary.

Profiles provide candidate CLI cases. The capture engine is generic: it copies
the ProgramBench cleanroom executable, runs each case against that reference
binary, and materializes exact process observations as pytest fixtures.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import textwrap
import time
from pathlib import Path
from typing import Any


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
    started = dt.datetime.now(dt.UTC)
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
    ended = dt.datetime.now(dt.UTC)
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


def run_reference_case(reference_binary: Path, case: dict[str, Any], timeout: int) -> dict[str, Any]:
    env = os.environ.copy()
    env["TZ"] = "UTC"
    env.update({str(k): str(v) for k, v in dict(case.get("env", {})).items()})
    argv0 = case.get("argv0", "/workspace/executable")
    try:
        proc = subprocess.run(
            [argv0, *map(str, case.get("args", []))],
            executable=str(reference_binary),
            input=str(case.get("stdin", "")).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=env,
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, bytes) else (exc.stdout or "").encode("utf-8", errors="replace")
        stderr = exc.stderr if isinstance(exc.stderr, bytes) else (exc.stderr or "").encode("utf-8", errors="replace")
        return {
            "returncode": 124,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": True,
        }


def observed_signature(observed: dict[str, Any]) -> tuple[int, bool, bytes, bytes]:
    return (
        int(observed["returncode"]),
        bool(observed["timed_out"]),
        observed["stdout"],
        observed["stderr"],
    )


def has_volatile_output(observed: dict[str, Any]) -> bool:
    return bool(VOLATILE_OUTPUT_RE.search(observed["stdout"]) or VOLATILE_OUTPUT_RE.search(observed["stderr"]))


def write_generated_pytest(bundle_root: Path, profile: str) -> None:
    test_path = bundle_root / "eval" / "tests" / "test_generated_cli_oracle.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(
        '''"""Generated black-box CLI oracle tests."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


WORKSPACE = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parents[1]
MANIFEST_PATH = EVAL_DIR / "generated_cli_manifest.json"
if not MANIFEST_PATH.exists():
    MANIFEST_PATH = EVAL_DIR / "generated_yj_manifest.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
FIXTURE_DIR = EVAL_DIR / "fixtures" / MANIFEST.get("fixture_subdir", "generated_cli")
CASES = MANIFEST["cases"]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_generated_black_box_behavior(case: dict, tmp_path: Path) -> None:
    executable = WORKSPACE / "executable"
    assert executable.exists(), f"missing executable at {executable}"

    stdin = (FIXTURE_DIR / case["stdin_file"]).read_bytes()
    expected_stdout = (FIXTURE_DIR / case["stdout_file"]).read_bytes()
    expected_stderr = (FIXTURE_DIR / case["stderr_file"]).read_bytes()
    env = os.environ.copy()
    env["TZ"] = "UTC"
    env.update(case.get("env", {}))

    argv0 = case.get("argv0", "/workspace/executable")
    proc = subprocess.run(
        [argv0, *case["args"]],
        executable=str(executable),
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=tmp_path,
        env=env,
        timeout=case.get("timeout", 5),
    )

    assert proc.returncode == case["returncode"]
    assert proc.stdout == expected_stdout
    assert proc.stderr == expected_stderr
''',
        encoding="utf-8",
    )


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
    reference_binary.chmod(reference_binary.stat().st_mode | stat.S_IXUSR)

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
        observed = run_reference_case(reference_binary, case, args.case_timeout)
        if observed["timed_out"] and args.skip_timed_out_cases:
            skipped_cases.append(
                {
                    "name": name,
                    "area": case.get("area", "unknown"),
                    "args": list(case.get("args", [])),
                    "reason": "reference_timeout",
                    "timeout": args.case_timeout,
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
                    "timeout": args.case_timeout,
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
            rerun = run_reference_case(reference_binary, case, args.case_timeout)
            if observed_signature(rerun) != observed_signature(observed):
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
                    "timeout": args.case_timeout,
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
                "timeout": args.case_timeout,
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
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
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
    write_generated_pytest(bundle_root, profile)
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
