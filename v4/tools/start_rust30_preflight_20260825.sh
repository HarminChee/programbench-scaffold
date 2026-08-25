#!/usr/bin/env bash
set -euo pipefail

workspace=/mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold
root=/home/programbench/research/pb-v4-rust30-preflight-20260825
cd "$workspace"

if [[ -f "$root/preflight.pid" ]] && kill -0 "$(<"$root/preflight.pid")" 2>/dev/null; then
  echo "already_running pid=$(<"$root/preflight.pid")"
  exit 0
fi

nohup env PYTHONPATH=. \
  /home/programbench/research/programbench-scaffold/.venv/bin/python \
  v4/tools/preflight_rust_candidate_sources.py \
  --candidate-manifest "$root/candidate_manifest.json" \
  --source-summary "$root/source_summary.json" \
  --cache-root "$root/preflight-cache" \
  --report "$root/preflight_report.json" \
  --workers 1 >"$root/preflight.log" 2>&1 &
pid=$!
printf '%s\n' "$pid" >"$root/preflight.pid"
echo "started pid=$pid"
