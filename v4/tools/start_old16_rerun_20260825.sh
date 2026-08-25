#!/bin/bash
set -euo pipefail

ROOT=/home/programbench/research/pb-v4-old16-rerun-20260825
CONFIG="$ROOT/campaign.old16.v4-polished.10x8.json"
LOG="$ROOT/controller.log"
PIDFILE="$ROOT/controller.pid"
SCAFFOLD=/mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold
PYTHON=/home/programbench/research/programbench-scaffold/.venv/bin/python

if pgrep -af 'v4.programbench_v4.controller' | grep -v grep >/dev/null; then
  echo "refusing duplicate V4 controller" >&2
  exit 2
fi
test -f "$CONFIG"
cd "$SCAFFOLD"
nohup "$PYTHON" -m v4.programbench_v4.controller --config "$CONFIG" \
  >"$LOG" 2>&1 </dev/null &
pid=$!
printf '%s\n' "$pid" >"$PIDFILE"
sleep 2
kill -0 "$pid"
printf 'started pid=%s log=%s\n' "$pid" "$LOG"
