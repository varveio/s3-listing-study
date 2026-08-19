#!/usr/bin/env python3
"""Render one two-runnable Batch job: replay server beside a listing subject.

A pilot renderer, deliberately outside the harness. The harness's own
`render_batch_job` will grow a second runnable once this settles what Batch
actually honors, and it should grow it against observed behaviour rather than
against Google's documentation, which does not answer the cgroup question at
all.

What this job exists to observe, in one run:

1. that a `background: true` runnable and a foreground one land on the same
   instance and can reach each other over loopback with `--network host`;
2. that `container.options` per runnable is honored -- each container reads its
   own `cpuset.cpus.effective` and `memory.max` back and says what it got;
3. how long the sorted-serving index derive takes on a 9.9M-row fixture, which
   is the readiness bound every later attempt has to allow for;
4. one first sizing datapoint: the server's own per-shape latency and overrun
   counters, scraped before and after a real listing, against the injected
   profile the campaign intends to use.

Everything it prints goes to Cloud Logging. A pilot receipt is transcribed from
there; nothing here uploads to the results bucket, because nothing here is an
attempt.
"""

from __future__ import annotations

import argparse
import json
import shlex

PROJECT = "varve-oss"
LOCATION = "us-east1"
ZONE = "us-east1-b"
NETWORK = f"projects/{PROJECT}/global/networks/s3-listing-study"
SUBNETWORK = f"projects/{PROJECT}/regions/{LOCATION}/subnetworks/s3-listing-study-{LOCATION}"
WORKER_SA = f"s3-listing-study-worker@{PROJECT}.iam.gserviceaccount.com"
N4_BOOT_DISK = {"type": "hyperdisk-balanced", "image": "batch-cos"}

DEFAULT_BUCKET = "sorel-20m"
DEFAULT_REGION = "us-west-2"
PORT = 19090
METRICS_PORT = 19192

# The subject's side of the run, as a script rather than as argv: it has to
# wait for a server that is still deriving its index, and it has to scrape the
# meters on both sides of the listing. Both are the worker's job in the real
# harness (measure.py), and both are written here the way they will be written
# there.
SUBJECT_SCRIPT = r"""
set -uo pipefail

report_cgroup() {
  printf 'replay_sidecar_cgroup role=subject nproc=%s cpuset=%s cpu_max=%s memory_max=%s\n' \
    "$(nproc)" \
    "$(cat /sys/fs/cgroup/cpuset.cpus.effective 2>/dev/null || echo unreadable)" \
    "$(cat /sys/fs/cgroup/cpu.max 2>/dev/null || echo unreadable)" \
    "$(cat /sys/fs/cgroup/memory.max 2>/dev/null || echo unreadable)"
}

scrape() {
  /usr/bin/python3 - "$1" "$2" <<'PY'
import json, sys, urllib.request
label, url = sys.argv[1], sys.argv[2]
with urllib.request.urlopen(url, timeout=30) as response:
    document = json.load(response)
print(f"replay_metrics label={label} " + json.dumps(document, separators=(",", ":")))
PY
}

wait_ready() {
  /usr/bin/python3 - "$1" "$2" <<'PY'
import sys, time, urllib.error, urllib.request
url, bound = sys.argv[1], float(sys.argv[2])
started = time.monotonic()
while time.monotonic() - started < bound:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            if response.status == 200:
                print(f"replay_ready wait_ms={int((time.monotonic() - started) * 1000)}")
                sys.exit(0)
    except (urllib.error.URLError, OSError):
        pass
    time.sleep(1)
print(f"replay_not_ready bound_s={bound}", file=sys.stderr)
sys.exit(1)
PY
}

report_cgroup
wait_ready "http://127.0.0.1:__METRICS_PORT__/healthz" 600 || exit 1
scrape before "http://127.0.0.1:__METRICS_PORT__/metrics"

start=$(date +%s.%N)
/opt/java/openjdk/bin/java -jar /opt/swath/swath.jar \
  -v --color never list "s3://__BUCKET__" \
  --region __REGION__ \
  --no-sign-request \
  --endpoint-url "http://127.0.0.1:__PORT__" \
  --concurrency __CONCURRENCY__ \
  --checkpoint none \
  --format tsv \
  --report /tmp/swath-summary.json \
  __SINK__ 2>/tmp/swath.log
status=${PIPESTATUS[0]:-$?}
end=$(date +%s.%N)

rows=$(cat /tmp/rowcount 2>/dev/null || wc -l < /tmp/listing.tsv)
printf 'subject_result exit=%s rows=%s wall_s=%s\n' "$status" "$rows" \
  "$(awk -v a="$start" -v b="$end" 'BEGIN{printf "%.1f", b-a}')"
echo "--- swath stderr (tail) ---"
tail -40 /tmp/swath.log
echo "--- swath report ---"
cat /tmp/swath-summary.json 2>/dev/null || echo "(no report)"

scrape after "http://127.0.0.1:__METRICS_PORT__/metrics"
exit "$status"
"""


def render(args: argparse.Namespace) -> dict:
    server_command = [
        "serve",
        "--fixture",
        f"/fixtures/{args.bucket}",
        "--bucket",
        args.bucket,
        "--host",
        "127.0.0.1",
        "--port",
        str(PORT),
        "--metrics-port",
        str(METRICS_PORT),
        "--serving-mode",
        "sorted",
        "--parquet-connections",
        str(args.parquet_connections),
        "--max-concurrent-requests",
        str(args.max_concurrent_requests),
    ]
    if args.inject_latency:
        server_command += ["--inject-latency", args.inject_latency]
        server_command += ["--latency-scale", str(args.latency_scale)]

    # The server is a JVM in a capped container, so its heap share is stated
    # rather than left to the runtime's own quarter-of-what-it-sees default:
    # at 512 read permits that default OOMs before the first request. Prefetch
    # is a stated property of the backend for the same reason the serving mode
    # is -- a run must say which backend it measured against.
    server_java_opts = f"-XX:MaxRAMPercentage={args.heap_percent}"
    server_java_opts += f" -Dswath.replay.prefetch.enabled={'true' if args.prefetch else 'false'}"
    server_env = {"JAVA_TOOL_OPTIONS": server_java_opts}
    if args.warm_fixture:
        server_env["REPLAY_WARM_FIXTURE"] = "1"

    # Where the listing lands. Writing it to a file is the faithful thing when a
    # deadline is what bounds the run: the subject does the work a real one does.
    # It is the WRONG thing for a ceiling probe. With no injected latency the
    # server answers in single-digit milliseconds, so a subject that sustains a
    # thousand pages a second is writing well over a hundred megabytes a second
    # of text to a container filesystem -- and what gets measured is the boot
    # disk, not the listing engine. Counting the rows as they stream keeps the
    # completeness check (a listing that dropped keys is not a fast listing) and
    # pays no disk for it.
    sink = (
        "| wc -l > /tmp/rowcount"
        if args.discard_output
        else "> /tmp/listing.tsv"
    )
    subject_script = (
        SUBJECT_SCRIPT.replace("__SINK__", sink)
        .replace("__METRICS_PORT__", str(METRICS_PORT))
        .replace("__PORT__", str(PORT))
        .replace("__BUCKET__", args.bucket)
        .replace("__REGION__", args.region)
        .replace("__CONCURRENCY__", str(args.concurrency))
    )

    def options(cpuset: str, memory_gb: int) -> str:
        return shlex.join(
            (
                "--network",
                "host",
                f"--cpuset-cpus={cpuset}",
                f"--memory={memory_gb}g",
                f"--memory-swap={memory_gb}g",
            )
        )

    return {
        "labels": {"suite": "replay-sidecar-spike"},
        "taskGroups": [
            {
                "taskCount": "1",
                "parallelism": "1",
                "taskSpec": {
                    "runnables": [
                        {
                            "background": True,
                            "container": {
                                "imageUri": args.server_image,
                                "commands": server_command,
                                "options": options(args.server_cpuset, args.server_memory_gb),
                            },
                            "environment": {"variables": server_env},
                        },
                        {
                            "container": {
                                "imageUri": args.toolbox_image,
                                "entrypoint": "/bin/bash",
                                "commands": ["-c", subject_script],
                                "options": options(args.subject_cpuset, args.subject_memory_gb),
                            },
                            "environment": {
                                "variables": {
                                    "JAVA_TOOL_OPTIONS": f"-XX:MaxRAMPercentage={args.heap_percent}",
                                    "AWS_REGION": args.region,
                                    "AWS_DEFAULT_REGION": args.region,
                                    "AWS_EC2_METADATA_DISABLED": "true",
                                }
                            },
                        },
                    ],
                    "computeResource": {
                        "cpuMilli": str(args.vcpus * 1000),
                        "memoryMib": str(args.memory_gb * 1024),
                    },
                    "maxRetryCount": 0,
                    "maxRunDuration": f"{args.max_run_duration}s",
                },
            }
        ],
        "allocationPolicy": {
            "instances": [
                {
                    "policy": {
                        "machineType": args.machine_type,
                        "provisioningModel": "STANDARD",
                        "bootDisk": dict(N4_BOOT_DISK),
                    }
                }
            ],
            "serviceAccount": {"email": WORKER_SA},
            "location": {"allowedLocations": [f"zones/{ZONE}"]},
            "network": {
                "networkInterfaces": [{"network": NETWORK, "subnetwork": SUBNETWORK}]
            },
        },
        "logsPolicy": {"destination": "CLOUD_LOGGING"},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-image", required=True)
    parser.add_argument("--toolbox-image", required=True)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--machine-type", default="n4-standard-4")
    parser.add_argument("--vcpus", type=int, default=4)
    parser.add_argument("--memory-gb", type=int, default=16)
    parser.add_argument("--server-cpuset", default="0-1")
    parser.add_argument("--server-memory-gb", type=int, default=4)
    parser.add_argument("--subject-cpuset", default="2-3")
    parser.add_argument("--subject-memory-gb", type=int, default=8)
    parser.add_argument("--parquet-connections", type=int, default=256)
    parser.add_argument("--max-concurrent-requests", type=int, default=512)
    parser.add_argument("--concurrency", type=int, default=64)
    parser.add_argument("--inject-latency", default="prod-commoncrawl")
    parser.add_argument("--latency-scale", type=float, default=1.0)
    parser.add_argument("--prefetch", action="store_true")
    parser.add_argument(
        "--discard-output",
        action="store_true",
        help="count the listing as it streams instead of landing it on disk; for a "
        "ceiling probe, where the subject's own output writes would be the limit",
    )
    parser.add_argument(
        "--warm-fixture",
        action="store_true",
        help="read the fixture through the page cache before serving, so the "
        "measured window does not start cold",
    )
    parser.add_argument("--heap-percent", type=int, default=75)
    parser.add_argument("--max-run-duration", type=int, default=1800)
    args = parser.parse_args()
    print(json.dumps(render(args), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
