#!/usr/bin/env bash
set -euo pipefail

workspace=/mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold
campaign=/home/programbench/research/pb-v4-external20-20260816
output="$campaign/output"
source_config="$campaign/recovery_20260822/campaign.remaining10.parallel10x8.json"
recovery="$campaign/recovery_20260823"
air="$output/repositories/air-verse__air.9f19e52"
controller_pid=2151791
log="$recovery/settlement-handoff.log"

mkdir -p "$recovery"
exec >>"$log" 2>&1
echo "$(date -u +%FT%TZ) waiting for Air iteration 74 checkpoint"

while kill -0 "$controller_pid" 2>/dev/null; do
  command_line=$(tr '\0' ' ' <"/proc/$controller_pid/cmdline" || true)
  if [[ "$command_line" != *"campaign.remaining10.parallel10x8.json"* ]]; then
    echo "controller PID identity changed; refusing intervention"
    exit 2
  fi
  completed=$(python3 - "$air/recovery_checkpoint.json" <<'PY'
import json, sys
try:
    print(int(json.load(open(sys.argv[1]))["last_completed_iteration"]))
except Exception:
    print(0)
PY
)
  attempt=$(python3 - "$air/status.json" <<'PY'
import json, sys
try:
    print(int(json.load(open(sys.argv[1])).get("iteration") or 0))
except Exception:
    print(0)
PY
)
  if (( completed >= 74 || attempt >= 76 )); then
    echo "$(date -u +%FT%TZ) Air checkpoint $completed is durable"
    break
  fi
  sleep 10
done

if kill -0 "$controller_pid" 2>/dev/null; then
  # Stop only the superseded remaining10 controller.  The accepted Air
  # checkpoint is already durable; a partly-started next tranche is disposable.
  kill -TERM "$controller_pid" || true
  for _ in $(seq 1 12); do
    kill -0 "$controller_pid" 2>/dev/null || break
    sleep 5
  done
  if kill -0 "$controller_pid" 2>/dev/null; then
    kill -KILL "$controller_pid" || true
  fi
fi

# Remove only containers whose recorded command belongs to Air in this campaign.
while read -r container; do
  [[ -n "$container" ]] && docker rm -f "$container" || true
done < <(docker ps --no-trunc --format '{{.ID}} {{.Command}}' | awk '/air-verse__air[.]9f19e52/{print $1}')

cd "$workspace"
python3 -m v4.tools.trim_unpromoted_recovery_tail \
  --repo-root "$output/repositories/watchexec__watchexec.db427ba" --apply
python3 -m v4.tools.trim_unpromoted_recovery_tail \
  --repo-root "$output/repositories/starship__starship.6bf659e" --apply

subset="$recovery/campaign.saturation2.subset.json"
settlement="$recovery/campaign.saturation2.fast-settlement.json"
python3 -m v4.tools.build_recovery_subset_config \
  --input "$source_config" --output "$subset" \
  --campaign-id pb-v4-external20-20260823-saturation2 \
  --repo-workers 2 --generation-workers 1 \
  --repo watchexec__watchexec.db427ba \
  --repo starship__starship.6bf659e
python3 -m v4.tools.build_fast_settlement_config \
  --input "$subset" --output "$settlement" \
  --saturation-request watchexec__watchexec.db427ba=49.1946521 \
  --saturation-request starship__starship.6bf659e

if pgrep -af 'v4[.]programbench_v4[.]controller' | grep -F 'pb-v4-external20-20260816'; then
  echo "another controller still references the external20 output; refusing duplicate"
  exit 3
fi

echo "$(date -u +%FT%TZ) starting two-repository full revalidation settlement"
nohup python3 -m v4.programbench_v4.controller \
  --config "$settlement" \
  --run-id v4-external20-saturation2-20260823 \
  >"$recovery/saturation2.controller.log" 2>&1 &
echo "$!" >"$recovery/saturation2.controller.pid"
echo "$(date -u +%FT%TZ) controller PID $!"
