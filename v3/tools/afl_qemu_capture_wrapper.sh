#!/bin/bash
set -euo pipefail

: "${PROGRAMBENCH_AFL_REAL_EXECUTABLE:?missing real executable}"
: "${PROGRAMBENCH_AFL_MAP_DIR:?missing map directory}"

AFL_ROOT="${PROGRAMBENCH_AFL_ROOT:-/home/programbench/research/tools/afl-src/aflplusplus-4.00c}"
/bin/mkdir -p "$PROGRAMBENCH_AFL_MAP_DIR"

# A test may intentionally select a meaningful argv[0].  qemu-user's -0
# equivalent is exposed through this environment variable by qemuafl.
export QEMU_ARGV0="$0"
export AFL_PATH="$AFL_ROOT/qemu_mode"
export AFL_NO_UI=1
export AFL_QUIET=1
if [[ -n "${PROGRAMBENCH_AFL_INST_RANGES:-}" ]]; then
  # qemuafl 4.00c keeps the main ELF .text window enabled even when explicit
  # include ranges are present.  Collapse that default window so the whitelist
  # below is the only instrumented code.  Without this, a statically linked Go
  # binary still records runtime/GC/scheduler edges from the whole executable.
  export AFL_CODE_START=1
  export AFL_CODE_END=1
  export AFL_QEMU_INST_RANGES="$PROGRAMBENCH_AFL_INST_RANGES"
fi

counter="$PROGRAMBENCH_AFL_MAP_DIR/.counter"
call_counter="$PROGRAMBENCH_AFL_MAP_DIR/.call_counter"
exec 9>"$PROGRAMBENCH_AFL_MAP_DIR/.counter.lock"
/usr/bin/flock 9
call_value=0
if [[ -s "$call_counter" ]]; then
  read -r call_value < "$call_counter"
fi
call_value=$((call_value + 1))
printf '%s\n' "$call_value" > "$call_counter"

capture=1
if [[ -n "${PROGRAMBENCH_AFL_CAPTURE_INDICES:-}" ]]; then
  capture=0
  case ",${PROGRAMBENCH_AFL_CAPTURE_INDICES}," in
    *,"$call_value",*) capture=1 ;;
  esac
fi
if [[ -n "${PROGRAMBENCH_AFL_CAPTURE_EVERY:-}" ]] && (( call_value % PROGRAMBENCH_AFL_CAPTURE_EVERY != 1 % PROGRAMBENCH_AFL_CAPTURE_EVERY )); then
  capture=0
fi
if [[ "$capture" == "0" ]]; then
  /usr/bin/flock -u 9
  exec "$PROGRAMBENCH_AFL_REAL_EXECUTABLE" "$@"
fi

value=0
if [[ -s "$counter" ]]; then
  read -r value < "$counter"
fi
value=$((value + 1))
printf '%s\n' "$value" > "$counter"
/usr/bin/flock -u 9

map="$PROGRAMBENCH_AFL_MAP_DIR/$(printf '%08d' "$value").map"
meta="$PROGRAMBENCH_AFL_MAP_DIR/$(printf '%08d' "$value").argv"
printf '%q ' "$0" "$@" > "$meta"
printf '\n' >> "$meta"

showmap_args=(-Q -m none)
if [[ "${PROGRAMBENCH_AFL_EDGE_ONLY:-0}" == "1" ]]; then
  showmap_args+=(-e)
fi

exec "$AFL_ROOT/afl-showmap" "${showmap_args[@]}" \
  -o "$map" -- "$PROGRAMBENCH_AFL_REAL_EXECUTABLE" "$@"
