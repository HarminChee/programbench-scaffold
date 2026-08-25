#!/usr/bin/env bash
set -euo pipefail

workspace=/mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold
campaign=/home/programbench/research/pb-v4-rust20-20260824
output="$campaign/output"
recovery="$campaign/recovery_20260824"
old_pid=539817
closeout_config="$recovery/campaign.freeze5.closeout.json"
remaining_config="$recovery/campaign.remaining12.parallel10x8.json"
log="$recovery/freeze8-then-remaining12.handoff.log"
guard="$recovery/freeze8-then-remaining12.handoff.lock"

exec 9>"$guard"
flock -n 9 || exit 0
exec >>"$log" 2>&1

timestamp() { date -u +%FT%TZ; }

controller_count() {
  pgrep -af 'python3 -m v4\.programbench_v4\.controller' | wc -l || true
}

wait_for_no_controller() {
  while [[ "$(controller_count)" -ne 0 ]]; do
    sleep 5
  done
  # CampaignLock validates PID/create-time identity and quarantines a dead
  # owner's record atomically.  Do not deadlock the handoff merely because an
  # interrupted predecessor could not unlink its lock file.
}

archive_controls() {
  local phase="$1"
  shift
  local archive="$recovery/control_archive/$phase"
  mkdir -p "$archive"
  local instance request
  for instance in "$@"; do
    request="$output/repositories/$instance/control/request.json"
    if [[ -f "$request" ]]; then
      mkdir -p "$archive/$instance"
      mv "$request" "$archive/$instance/request.json"
    fi
  done
}

selected=(
  lukas-reineke__cbfmt.88a3e46
  greymd__teip.07a1777
  juan-leon__lowcharts.3e47c2c
  mike-engel__jwt-cli.6b203a2
  theryangeary__choose.f1c53ee
)

remaining=(
  bgreenwell__doxx.062819a
  bgreenwell__lstr.722cb63
  crate-ci__committed.800a04e
  ikanago__omekasy.a54c47a
  mufeedvh__code2prompt.ab4fa06
  sigoden__projclean.2135f41
  facebookincubator__fastmod.974e3ef
  alexhallam__tv.f71935e
  medialab__xan.60a89e5
  pamburus__hl.6164b42
  ribbondz__rsv.b2f647f
  the-lean-crate__cargo-diet.fadfa31
)

echo "$(timestamp) waiting for original controller PID $old_pid"
while kill -0 "$old_pid" 2>/dev/null; do
  sleep 5
done
wait_for_no_controller

cd "$workspace"
archive_controls freeze5 "${selected[@]}"
echo "$(timestamp) starting five-repository checkpoint closeout"
set +e
python3 -m v4.programbench_v4.controller \
  --config "$closeout_config" \
  --run-id v4-rust20-freeze8-closeout-20260824
closeout_rc=$?
set -e
echo "$(timestamp) closeout controller exited rc=$closeout_rc"

wait_for_no_controller
archive_controls remaining12 "${remaining[@]}"
echo "$(timestamp) starting remaining twelve repositories at 10x8 logical concurrency"
exec python3 -m v4.programbench_v4.controller \
  --config "$remaining_config" \
  --run-id v4-rust20-remaining12-20260824
