from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from v4.programbench_v4.controller import validate_config


def parse_request(value: str) -> dict[str, object]:
    try:
        instance_id, native = value.rsplit("=", 1)
        native_value = float(native)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError(
            "request must be INSTANCE=NATIVE_PRIMARY_COVERAGE"
        ) from exc
    if not instance_id:
        raise argparse.ArgumentTypeError("request instance ID is empty")
    return {
        "instance_id": instance_id,
        "native_primary_coverage": native_value,
        "minimum_margin_pp": 0.0,
    }


def parse_saturation_request(value: str) -> dict[str, object]:
    request = (
        parse_request(value)
        if "=" in value
        else {
            "instance_id": value,
            "native_primary_coverage": None,
            "minimum_margin_pp": 0.0,
        }
    )
    if not request["instance_id"]:
        raise argparse.ArgumentTypeError("saturation request instance ID is empty")
    request["saturation_revalidation"] = True
    return request


def parse_closeout_request(value: str) -> dict[str, object]:
    request = (
        parse_request(value)
        if "=" in value
        else {
            "instance_id": value,
            "native_primary_coverage": None,
            "minimum_margin_pp": 0.0,
        }
    )
    if not request["instance_id"]:
        raise argparse.ArgumentTypeError("closeout request instance ID is empty")
    request["checkpoint_closeout_revalidation"] = True
    return request


def atomic_write(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--request", action="append", type=parse_request, default=[])
    parser.add_argument(
        "--saturation-request",
        action="append",
        type=parse_saturation_request,
        default=[],
    )
    parser.add_argument(
        "--closeout-request",
        action="append",
        type=parse_closeout_request,
        default=[],
    )
    args = parser.parse_args()

    args.request.extend(args.saturation_request)
    args.request.extend(args.closeout_request)
    if not args.request:
        parser.error("at least one --request or --saturation-request is required")

    config = json.loads(args.input.resolve().read_text(encoding="utf-8"))
    config["fast_settlement"] = {"repositories": args.request}
    requested = [str(row["instance_id"]) for row in args.request]
    by_id = {str(repo["instance_id"]): repo for repo in config["repositories"]}
    config["repositories"] = [by_id[instance] for instance in requested] + [
        repo
        for repo in config["repositories"]
        if str(repo["instance_id"]) not in set(requested)
    ]
    validate_config(config)
    atomic_write(args.output.resolve(), config)
    print(
        json.dumps(
            {
                "valid": True,
                "output": str(args.output.resolve()),
                "repositories": [row["instance_id"] for row in args.request],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
