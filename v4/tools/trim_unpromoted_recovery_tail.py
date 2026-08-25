from __future__ import annotations

import argparse
import json
from pathlib import Path

from v4.programbench_v4.recovery_tail import (
    apply_unpromoted_tail_repair,
    inspect_unpromoted_tail,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = (
        apply_unpromoted_tail_repair(args.repo_root)
        if args.apply
        else inspect_unpromoted_tail(args.repo_root)
    )
    result.pop("checkpoint", None)
    result.pop("trimmed_checkpoint", None)
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
