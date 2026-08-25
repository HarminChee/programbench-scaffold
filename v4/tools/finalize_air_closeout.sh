#!/usr/bin/env bash
set -euo pipefail

pid=489753
campaign=/home/programbench/research/pb-v4-external20-20260816
repo="$campaign/output/repositories/air-verse__air.9f19e52"
recovery="$campaign/recovery_20260824"
log="$recovery/air-closeout.guardian.log"

exec >>"$log" 2>&1
echo "$(date -u +%FT%TZ) waiting for Air closeout PID $pid"
while kill -0 "$pid" 2>/dev/null; do sleep 15; done

if python3 - "$repo" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
status = json.loads((p / "status.json").read_text())
summary_path = p / "frozen/settlement_summary.json"
summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
ok = (
    status.get("state") == "completed"
    and status.get("stage") == "frozen"
    and summary.get("successful") is True
)
raise SystemExit(0 if ok else 1)
PY
then
  echo "$(date -u +%FT%TZ) Air frozen successfully; preserving repository output"
  exit 0
fi

python3 - "$repo" "$recovery/air-abandoned-summary.json" <<'PY'
import hashlib, json, os, pathlib, sys, tempfile
repo, target = map(pathlib.Path, sys.argv[1:])
def load(path):
    try: return json.loads(path.read_text())
    except Exception: return None
status = load(repo / "status.json")
checkpoint = load(repo / "recovery_checkpoint.json")
candidate = repo / "candidates/current.json"
value = {
    "schema": "programbench_v4_abandoned_repository_v1",
    "instance_id": repo.name,
    "reason": "final_closeout_revalidation_failed",
    "status": status,
    "checkpoint": {
        "last_completed_iteration": (checkpoint or {}).get("last_completed_iteration"),
        "retained_suite_cases": (checkpoint or {}).get("retained_suite_cases"),
        "candidate_state_sha256": (checkpoint or {}).get("candidate_state_sha256"),
    },
    "candidate_file_sha256": (
        hashlib.sha256(candidate.read_bytes()).hexdigest() if candidate.exists() else None
    ),
}
target.parent.mkdir(parents=True, exist_ok=True)
fd, tmp = tempfile.mkstemp(prefix=".air-abandoned.", dir=target.parent)
with os.fdopen(fd, "w") as handle:
    json.dump(value, handle, indent=2, sort_keys=True)
    handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
os.replace(tmp, target)
PY

resolved=$(readlink -f "$repo")
expected="$campaign/output/repositories/air-verse__air.9f19e52"
if [[ "$resolved" != "$expected" ]]; then
  echo "resolved Air path is outside the exact expected path; refusing deletion"
  exit 3
fi
rm -rf -- "$resolved"
echo "$(date -u +%FT%TZ) Air failed closeout and was abandoned/deleted"
