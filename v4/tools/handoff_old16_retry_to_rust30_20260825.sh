#!/usr/bin/env bash
set -euo pipefail

old_root=/home/programbench/research/pb-v4-old16-rerun-20260825
old_pid_file="$old_root/controller.pid"
old_status="$old_root/output/campaign_status.json"
retry_config="$old_root/recovery_20260825/campaign.old16.retry3.clean.3x3.json"
retry_pid_file="$old_root/recovery_20260825/controller.retry3.pid"
retry_log="$old_root/recovery_20260825/controller.retry3.log"

rust_root=/home/programbench/research/pb-v4-rust30-generation-20260825
rust_config="$rust_root/campaign.rust30.v4-polished.10x8.json"
rust_pid_file="$rust_root/controller.pid"
rust_log="$rust_root/controller.log"

worktree=/home/programbench/research/worktrees/pb-v4-rust30-20260825
python=/home/programbench/research/programbench-scaffold/.venv/bin/python
handoff_log="$rust_root/logs/handoff.log"

mkdir -p "$(dirname "$retry_log")" "$rust_root/logs"
exec >>"$handoff_log" 2>&1

stamp() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
note() { printf '%s %s\n' "$(stamp)" "$*"; }

status_field() {
  local path=$1 field=$2
  "$python" - "$path" "$field" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
if not p.exists():
    print("")
else:
    try:
        print(json.loads(p.read_text(encoding="utf-8")).get(sys.argv[2], ""))
    except Exception:
        print("")
PY
}

wait_for_pid_exit() {
  local pid=$1 label=$2 status_path=$3
  while kill -0 "$pid" 2>/dev/null; do
    note "$label active pid=$pid state=$(status_field "$status_path" state) stage=$(status_field "$status_path" stage) completed=$(status_field "$status_path" completed_repositories)"
    sleep 60
  done
  note "$label exited pid=$pid"
}

wait_for_clean_boundary() {
  local root=$1 status_path=$2 label=$3
  local deadline=$((SECONDS + 900))
  while :; do
    local state stage
    state=$(status_field "$status_path" state)
    stage=$(status_field "$status_path" stage)
    if [[ "$stage" == campaign_terminal && ( "$state" == completed || "$state" == needs_attention ) ]] \
      && [[ ! -e "$root/output/campaign.lock" ]] \
      && ! pgrep -af 'v4\.programbench_v4\.controller' >/dev/null \
      && ! docker ps --no-trunc --format '{{.Command}}' | grep -F "$root" >/dev/null; then
      note "$label clean boundary state=$state"
      return 0
    fi
    if (( SECONDS >= deadline )); then
      note "$label boundary timeout state=$state stage=$stage lock=$([[ -e "$root/output/campaign.lock" ]] && echo present || echo absent)"
      return 1
    fi
    sleep 15
  done
}

wait_for_capacity() {
  while :; do
    local c_free_kb mem_available_kb
    c_free_kb=$(df -Pk /mnt/c | awk 'NR==2 {print $4}')
    mem_available_kb=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
    if (( c_free_kb >= 1048576 && mem_available_kb >= 8388608 )); then
      note "capacity accepted c_free_kb=$c_free_kb mem_available_kb=$mem_available_kb"
      return 0
    fi
    note "capacity waiting c_free_kb=$c_free_kb mem_available_kb=$mem_available_kb"
    sleep 300
  done
}

if [[ ! -s "$old_pid_file" ]]; then
  note "missing old controller pid file: $old_pid_file"
  exit 1
fi

old_pid=$(<"$old_pid_file")
wait_for_pid_exit "$old_pid" old16-main "$old_status"
wait_for_clean_boundary "$old_root" "$old_status" old16-main
wait_for_capacity

if pgrep -af "controller --config $retry_config" >/dev/null; then
  note "old16 retry already running"
  exit 1
fi

note "starting old16 retry config=$retry_config"
(
  cd "$worktree"
  exec env PYTHONPATH="$worktree" "$python" -m v4.programbench_v4.controller --config "$retry_config"
) >"$retry_log" 2>&1 &
retry_pid=$!
printf '%s\n' "$retry_pid" >"$retry_pid_file"
note "old16 retry started pid=$retry_pid"
wait_for_pid_exit "$retry_pid" old16-retry "$old_status"
wait_for_clean_boundary "$old_root" "$old_status" old16-retry
wait_for_capacity

if pgrep -af "controller --config $rust_config" >/dev/null; then
  note "rust30 already running"
  exit 0
fi
if pgrep -af 'v4\.programbench_v4\.controller' >/dev/null; then
  note "refusing Rust30 launch because another controller exists"
  exit 1
fi

note "starting Rust30 config=$rust_config"
(
  cd "$worktree"
  exec env PYTHONPATH="$worktree" "$python" -m v4.programbench_v4.controller --config "$rust_config"
) >"$rust_log" 2>&1 &
rust_pid=$!
printf '%s\n' "$rust_pid" >"$rust_pid_file"
note "Rust30 started pid=$rust_pid"

