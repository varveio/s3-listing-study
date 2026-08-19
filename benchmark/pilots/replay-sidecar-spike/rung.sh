#!/usr/bin/env bash
# One rung of the sizing ladder: submit, wait, fetch the logs, print the verdict.
#
# A rung is one command because a sizing sweep read by hand is a sizing sweep
# whose answer moves with whoever reads it. Everything after `--` is passed to
# `job.py`, so the shape being tried is visible in the shell history that ran it.
#
#   ./rung.sh a2 --machine-type n4-standard-8 --vcpus 8 --memory-gb 32 \
#     --server-cpuset 0-5 --server-memory-gb 12 --subject-cpuset 6-7 \
#     --parquet-connections 128 --concurrency 64 --latency-scale 2.23
set -euo pipefail

RUNG=${1:?usage: rung.sh <rung-name> [job.py flags...]}
shift

: "${SERVER_IMAGE:?set SERVER_IMAGE to a digest-pinned replay-server image}"
: "${TOOLBOX_IMAGE:?set TOOLBOX_IMAGE to a digest-pinned benchmark-toolbox image}"
: "${WORK_DIR:=${TMPDIR:-/tmp}/replay-sidecar-spike}"
LOCATION=${LOCATION:-us-east1}
PROJECT=${PROJECT:-varve-oss}
ROWS_EXPECTED=${ROWS_EXPECTED:-9919143}

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
mkdir -p "$WORK_DIR"

job="replay-sidecar-spike-${RUNG}-$(date -u +%Y%m%d-%H%M%S)"
config="$WORK_DIR/$job.json"
log="$WORK_DIR/$job.log"

python3 "$here/job.py" \
  --server-image "$SERVER_IMAGE" \
  --toolbox-image "$TOOLBOX_IMAGE" \
  "$@" > "$config"

# The window the log query opens, taken before submission so nothing this run
# printed can fall outside it.
since=$(date -u -d '1 minute ago' +%Y-%m-%dT%H:%M:%SZ)

echo "== $job"
gcloud batch jobs submit "$job" --location "$LOCATION" --config "$config" \
  --format='value(status.state)'

while true; do
  state=$(gcloud batch jobs describe "$job" --location "$LOCATION" \
    --format='value(status.state)' 2>/dev/null || echo UNKNOWN)
  case $state in
    SUCCEEDED | FAILED | CANCELLED) break ;;
  esac
  sleep 20
done
echo "== $job $state"

# Cloud Logging is eventually consistent enough that a scrape written in the
# task's last second can arrive after the job is marked done.
sleep 30
gcloud logging read \
  "logName=\"projects/$PROJECT/logs/batch_task_logs\" AND timestamp>=\"$since\"" \
  --project "$PROJECT" --format='value(textPayload)' --limit 5000 --order asc > "$log"

profile=""
for ((i = 1; i <= $#; i++)); do
  if [[ ${!i} == --latency-scale ]]; then
    next=$((i + 1))
    scale=${!next}
    profile=$(python3 - "$scale" <<'PY'
import sys
scale = float(sys.argv[1])
base = {"worker_page": 223.0, "pivot_probe": 121.0, "structure_probe": 223.0}
print(",".join(f"{shape}={value / scale:.1f}ms" for shape, value in base.items()))
PY
)
  fi
done

python3 "$here/verdict.py" --rows-expected "$ROWS_EXPECTED" --log "$log" \
  ${profile:+--profile "$profile"}
