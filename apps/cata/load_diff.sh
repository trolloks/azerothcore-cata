#!/usr/bin/env bash
# Start worldserver on a control world DB and on the landing world DB, then print the loader messages that only the
# landing produces (#203). Static data only: nothing logs in, nothing is built. Usage:
#   load_diff.sh <worldserver binary> <work dir>
# <work dir> holds base/worldserver.conf (control) and land/worldserver.conf (landing): different WorldServerPort,
# Ra.Port and SOAP.Port, LogsDir inside the work dir, WorldDatabaseInfo pointing at the matching schema.
set -euo pipefail

BIN=${1:?worldserver binary}
WORK=${2:?work dir}
TIMEOUT=${TIMEOUT:-900}

pids=()
for n in base land; do
  rm -rf "$WORK/$n/logs" "$WORK/$n/stdout.txt"
  mkdir -p "$WORK/$n/logs"
  (cd "$WORK/$n" && exec timeout "$TIMEOUT" "$BIN" -c "$WORK/$n/worldserver.conf" > "$WORK/$n/stdout.txt" 2>&1) &
  pids+=($!)
done
trap 'kill "${pids[@]}" 2>/dev/null || true' EXIT

for _ in $(seq 1 $((TIMEOUT / 5))); do
  if grep -aq "World Initialized" "$WORK/base/stdout.txt" && grep -aq "World Initialized" "$WORK/land/stdout.txt"; then
    break
  fi
  sleep 5
done
grep -aq "World Initialized" "$WORK/land/stdout.txt" || { echo "landing server did not finish loading" >&2; exit 1; }
grep -aq "World Initialized" "$WORK/base/stdout.txt" || { echo "control server did not finish loading" >&2; exit 1; }
kill "${pids[@]}" 2>/dev/null || true
sleep 3

for n in base land; do
  sed 's/\x1b\[[0-9;]*m//g' "$WORK/$n/stdout.txt" |
    grep -av 'MoveSplineInitArgs\|^Config\|acore_world_\|PID\|in [0-9]* ms' | sed 's/^[[:space:]]*//' | sort -u > "$WORK/$n/lines.txt"
done
comm -13 "$WORK/base/lines.txt" "$WORK/land/lines.txt" > "$WORK/only_land.txt"
# Collapse ids so each message kind prints once with a count.
sed -E 's/-?[0-9]+/N/g' "$WORK/only_land.txt" | sort | uniq -c | sort -rn |
  grep -av '>> Loaded\|Load\|Calc\|uptime\|Percentiles\|Mean\|Update time\|ms$\|ready\|shut down\|rank. is empty' |
  cut -c1-220
