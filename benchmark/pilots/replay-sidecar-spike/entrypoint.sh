#!/usr/bin/env bash
# Report the cgroup this container actually got, then serve.
#
# The one fact Google's documentation does not settle is whether Batch's
# `container.options` passthrough reaches Docker for a *background* runnable —
# whether `--cpuset-cpus` and `--memory` bind, and whether `--network host`
# leaves the loopback endpoint reachable from the sibling container. Reading
# `/sys/fs/cgroup` from inside is the only place that question has an answer,
# so it is answered on every start rather than in a one-off probe.
set -uo pipefail

read_cgroup() {
  local name=$1 path
  for path in "/sys/fs/cgroup/$name" "/sys/fs/cgroup/unified/$name"; do
    if [[ -r $path ]]; then
      printf '%s' "$(<"$path")"
      return
    fi
  done
  printf 'unreadable'
}

printf 'replay_sidecar_cgroup role=server nproc=%s cpuset=%s cpu_max=%s memory_max=%s memory_high=%s\n' \
  "$(nproc)" \
  "$(read_cgroup cpuset.cpus.effective)" \
  "$(read_cgroup cpu.max)" \
  "$(read_cgroup memory.max)" \
  "$(read_cgroup memory.high)"

# Optional: pull the fixture through the page cache before serving a byte of it.
#
# A measured attempt gets a server that has never read its own fixture, on a VM
# whose page cache is empty, and the first request to touch a region of the file
# pays for the disk. That cost lands unevenly: an ordinary listing page is one of
# ten thousand and amortizes it away, while a rollup is one of a hundred and does
# not — which is exactly the shape whose tail overran. Reading the file once,
# sequentially, is the cheapest way to make the measured window start warm, and
# sequential is the access pattern a disk is best at.
#
# A flag rather than a default, so a run can be compared against one without it.
if [[ ${REPLAY_WARM_FIXTURE:-0} == 1 ]]; then
  warm_started=$(date +%s.%N)
  warm_bytes=0
  for file in /fixtures/*/*.parquet; do
    [[ -r $file ]] || continue
    cat -- "$file" > /dev/null
    warm_bytes=$((warm_bytes + $(stat -c %s -- "$file")))
  done
  printf 'replay_fixture_warm bytes=%s wall_s=%s\n' "$warm_bytes" \
    "$(awk -v a="$warm_started" -v b="$(date +%s.%N)" 'BEGIN{printf "%.1f", b-a}')"
fi

exec /opt/swath-replay-server/bin/swath-replay-server "$@"
