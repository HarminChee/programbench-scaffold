#!/usr/bin/env python3
"""Run ProgramBench with sanitized executable tests injected before coding."""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import time
import traceback
from pathlib import Path
from typing import Any


_IMAGE_TAG = "task_cleanroom_v6"


class NullProgressManager:
    def update_instance_status(self, instance_id: str, message: str) -> None:
        print(f"[{instance_id}] {message}", flush=True)


def default_workspace_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_base_config() -> Path:
    from minisweagent.config import builtin_config_dir

    return builtin_config_dir / "benchmarks" / "programbench.yaml"


def exact_filter(task_id: str) -> str:
    return "^" + task_id.replace(".", r"\.") + "$"


def docker_exec_root(env: Any, command: str) -> None:
    container_id = getattr(env, "container_id", None)
    executable = getattr(getattr(env, "config", None), "executable", None)
    if not container_id or not executable:
        raise RuntimeError("Docker root exec requires a Docker environment with container_id")
    subprocess.run(
        [executable, "exec", "-u", "root", container_id, "bash", "-lc", command],
        check=True,
        capture_output=True,
        text=True,
    )


def docker_cp_into(env: Any, src: Path, dest: str) -> None:
    container_id = getattr(env, "container_id", None)
    executable = getattr(getattr(env, "config", None), "executable", None)
    if not container_id or not executable:
        raise RuntimeError("Docker copy requires a Docker environment with container_id")
    subprocess.run(
        [executable, "cp", str(src), f"{container_id}:{dest}"],
        check=True,
        capture_output=True,
        text=True,
    )


def inject_test_bundle(env: Any, bundle_dir: Path) -> None:
    if not bundle_dir.exists():
        raise FileNotFoundError(f"Missing sanitized test bundle: {bundle_dir}")
    docker_cp_into(env, bundle_dir, "/workspace/")
    docker_exec_root(
        env,
        "if id agent >/dev/null 2>&1; then "
        "chown -R agent:agent /workspace/oracle_tests; "
        "else chmod -R a+rwX /workspace/oracle_tests; fi",
    )


def hide_reference_executable(env: Any) -> None:
    docker_exec_root(
        env,
        "if [ -f /workspace/executable ]; then "
        "mkdir -p /opt/programbench-reference-hidden && "
        "mv /workspace/executable /opt/programbench-reference-hidden/reference_executable; "
        "fi",
    )


def load_config(config_specs: list[Path], *, model: str | None, model_class: str | None) -> dict[str, Any]:
    from minisweagent.config import get_config_from_spec
    from minisweagent.utils.serialize import UNSET, recursive_merge

    configs = [get_config_from_spec(str(path)) for path in config_specs]
    configs.append(
        {
            "model": {
                "model_name": model or UNSET,
                "model_class": model_class or UNSET,
            }
        }
    )
    return recursive_merge(*configs)


def copy_submission(env: Any, dest: Path, *, src: str = "/workspace") -> None:
    container_id = getattr(env, "container_id", None)
    executable = getattr(getattr(env, "config", None), "executable", None)
    if not container_id or not executable:
        raise RuntimeError("copy_submission requires a Docker environment with container_id")
    dest.parent.mkdir(parents=True, exist_ok=True)
    container_tar = "/tmp/_submission.tar.gz"
    excludes = " ".join(
        [
            "--exclude=./oracle_tests",
            "--exclude=./oracle_tests/*",
            "--exclude=./.git",
            "--exclude=./.git/*",
        ]
    )
    env.execute({"command": f"tar {excludes} -czf {container_tar} -C {src} ."})
    subprocess.run(
        [executable, "cp", f"{container_id}:{container_tar}", str(dest)],
        check=True,
        capture_output=True,
        text=True,
    )


def process_instance(
    *,
    instance: dict[str, Any],
    output_dir: Path,
    config: dict[str, Any],
    test_bundle_root: Path,
    hide_reference: bool,
    redo_existing: bool,
) -> None:
    from minisweagent.run.benchmarks.programbench import ProgramBenchAgent
    from minisweagent.environments import get_environment
    from minisweagent.models import get_model

    iid = instance["instance_id"]
    instance_dir = output_dir / iid
    submission_path = instance_dir / "submission.tar.gz"
    traj_path = instance_dir / f"{iid}.traj.json"
    if submission_path.exists() and not redo_existing:
        print(f"[{iid}] skipping existing submission: {submission_path}", flush=True)
        return

    instance_dir.mkdir(parents=True, exist_ok=True)
    traj_path.unlink(missing_ok=True)

    inst_config = copy.deepcopy(config)
    inst_config.setdefault("environment", {})["image"] = f"{instance['image_name']}:{_IMAGE_TAG}"

    env = None
    agent = None
    exit_status = None
    extra_info: dict[str, Any] = {}
    injected_bundle = test_bundle_root / iid / "oracle_tests"

    try:
        print(f"[{iid}] starting environment", flush=True)
        model = get_model(config=inst_config.get("model", {}))
        env = get_environment(inst_config.get("environment", {}), default_type="docker")
        env.execute(
            {"command": 'git config user.name "mini-swe-agent" && git config user.email "mini-swe-agent@proton.me"'}
        )

        print(f"[{iid}] injecting sanitized tests from {injected_bundle}", flush=True)
        inject_test_bundle(env, injected_bundle)
        if hide_reference:
            print(f"[{iid}] hiding original reference executable", flush=True)
            hide_reference_executable(env)

        agent_config = dict(inst_config.get("agent", {}))
        agent_config["output_path"] = str(traj_path)
        agent = ProgramBenchAgent(
            model,
            env,
            progress_manager=NullProgressManager(),
            instance_id=iid,
            **agent_config,
        )
        agent.extra_template_vars = {"instance": instance}
        info = agent.run()
        exit_status = info.get("exit_status")
    except Exception as exc:  # noqa: BLE001 - preserve trace in trajectory
        print(f"[{iid}] error: {type(exc).__name__}: {exc}", flush=True)
        exit_status = type(exc).__name__
        extra_info = {"traceback": traceback.format_exc(), "exception_str": str(exc)}
    finally:
        if agent is not None:
            try:
                copy_submission(agent.env, submission_path)
            except Exception as exc:  # noqa: BLE001
                extra_info["submission_copy_error"] = str(exc)
            agent.save(
                traj_path,
                {
                    "info": {
                        "exit_status": exit_status,
                        "test_bundle": str(injected_bundle),
                        "hide_reference_executable": hide_reference,
                        **extra_info,
                    },
                    "instance_id": iid,
                },
            )
            print(f"[{iid}] saved trajectory to {traj_path}", flush=True)
        elif env is not None:
            try:
                copy_submission(env, submission_path)
            except Exception as exc:  # noqa: BLE001
                print(f"[{iid}] failed to copy partial submission: {exc}", flush=True)
        if env is not None:
            env.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, default=default_workspace_root())
    parser.add_argument("--filter", default="", help="Regex filter over ProgramBench instance ids")
    parser.add_argument("--task", action="append", help="Exact task id; repeatable")
    parser.add_argument("--slice", default="", dest="slice_spec")
    parser.add_argument("--shuffle", action="store_true")
    parser.add_argument("--output", type=Path, default=Path(f"reports/programbench_greenfield_test_runs_{int(time.time())}"))
    parser.add_argument("--test-bundle-root", type=Path, default=Path("reports/test_bundles"))
    parser.add_argument("--config", action="append", type=Path, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--model-class", default=None)
    parser.add_argument("--redo-existing", action="store_true")
    parser.add_argument("--keep-reference-executable", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--yes-run-agent",
        action="store_true",
        help="Required unless --dry-run is set, because running the agent spends model budget.",
    )
    args = parser.parse_args()

    workspace_root = args.workspace_root.resolve()
    output_dir = (workspace_root / args.output).resolve() if not args.output.is_absolute() else args.output.resolve()
    test_bundle_root = (
        (workspace_root / args.test_bundle_root).resolve()
        if not args.test_bundle_root.is_absolute()
        else args.test_bundle_root.resolve()
    )

    from programbench.utils.instance_filters import filter_instances
    from programbench.utils.load_data import load_all_instances

    filter_spec = args.filter
    if args.task:
        escaped = [exact_filter(task)[1:-1] for task in args.task]
        filter_spec = "(" + "|".join(escaped) + ")"

    config_specs = args.config or [
        default_base_config(),
        workspace_root / "configs/programbench_greenfield_test_only.yaml",
        workspace_root / "configs/programbench_mac_smoke.yaml",
    ]
    config_specs = [path.resolve() for path in config_specs]
    config = load_config(config_specs, model=args.model, model_class=args.model_class)

    instances = load_all_instances(include_tests=True)
    instances = filter_instances(
        instances,
        filter_spec=filter_spec,
        slice_spec=args.slice_spec,
        shuffle=args.shuffle,
        has_test_branch=True,
    )

    metadata = {
        "output": str(output_dir),
        "test_bundle_root": str(test_bundle_root),
        "config_specs": [str(path) for path in config_specs],
        "tasks": [instance["instance_id"] for instance in instances],
        "hide_reference_executable": not args.keep_reference_executable,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, indent=2), flush=True)

    missing = [iid for iid in metadata["tasks"] if not (test_bundle_root / iid / "oracle_tests").exists()]
    if missing:
        raise SystemExit(f"Missing sanitized test bundles for: {', '.join(missing)}")

    if args.dry_run:
        return 0
    if not args.yes_run_agent:
        raise SystemExit("Refusing to spend model budget without --yes-run-agent. Use --dry-run to validate setup.")

    for instance in instances:
        process_instance(
            instance=instance,
            output_dir=output_dir,
            config=config,
            test_bundle_root=test_bundle_root,
            hide_reference=not args.keep_reference_executable,
            redo_existing=args.redo_existing,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
