# Operator log: replay-canary-current-20260826

This is the command/output journal for the bounded integration canary. The
target named `runner-replay-canary` is the 2,048-row synthetic fixture served by
the replay sidecar; no AWS bucket was contacted. Exit codes were recorded by the
command executor.

Host-local paths are normalized below as `$STATE_ROOT`; the recorded ledger,
resolved cases, image identities, and provider requests remain bound in the
receipt.

## Submit

```sh
uv run python benchmark/src/benchmark/campaign.py \
  --state "$STATE_ROOT/replay-canary-current.db" \
  submit --suite runner-replay-canary \
  --plan benchmark/plans/canaries/runner-replay-canary.yaml \
  --project varve-oss --location us-east1 \
  --results-bucket s3-listing-study-results-29c02004 \
  --image-set "$STATE_ROOT/images.json" \
  --anonymous-worker-sa s3-listing-study-worker@varve-oss.iam.gserviceaccount.com \
  --authenticated-worker-sa s3-listing-study-auth-worker@varve-oss.iam.gserviceaccount.com \
  --secret-resource projects/varve-oss/secrets/s3-listing-study-aws-credentials/versions/latest \
  --network projects/varve-oss/global/networks/s3-listing-study \
  --subnetwork projects/varve-oss/regions/us-east1/subnetworks/s3-listing-study-us-east1 \
  --zone us-east1-b --provisioning STANDARD \
  --group replay-canary-current-20260826
```

Exit `0`:

```text
campaign: s3-fast-list.90992fe12168.s1 runner-replay-canary-s3-fast-list-90992fe12168-s1
campaign: s3p.276aea2ef416.s1 runner-replay-canary-s3p-276aea2ef416-s1
campaign: s7cmd.ee7094a12693.s1 runner-replay-canary-s7cmd-ee7094a12693-s1
campaign: 3 plan row(s) expand to 3 attempt(s) and 0 slot(s)
campaign: group replay-canary-current-20260826
```

## Poll

```sh
uv run python benchmark/src/benchmark/campaign.py \
  --state "$STATE_ROOT/replay-canary-current.db" \
  poll --watch --interval 10
```

Exit `0`; no stdout or stderr.

## Status

```sh
uv run python benchmark/src/benchmark/campaign.py \
  --state "$STATE_ROOT/replay-canary-current.db" \
  status --group replay-canary-current-20260826
```

Exit `0`:

```text
s3-fast-list.90992fe12168.s1     SUCCEEDED    canary       replay-canary-current-20260826 runner-replay-canary-s3-fast-list-90992fe12168-s1
s3p.276aea2ef416.s1              SUCCEEDED    canary       replay-canary-current-20260826 runner-replay-canary-s3p-276aea2ef416-s1
s7cmd.ee7094a12693.s1            SUCCEEDED    canary       replay-canary-current-20260826 runner-replay-canary-s7cmd-ee7094a12693-s1
```

## Report

```sh
uv run python benchmark/src/benchmark/report.py \
  --state "$STATE_ROOT/replay-canary-current.db" \
  --group replay-canary-current-20260826
```

Exit `0`:

```text
# Campaign report (2026-08-26T11:57:02.439787+00:00)

## runner-replay-canary

| tool | mode | product | fields | concurrency | case_id | attempt | machine_type | vcpus | memory_gb | container_memory_gb | declared_server_allocation | declared_subject_allocation | derived_host_headroom | capacity_status | purpose | statistic | state | evidence_state | replay_state | exit | worker_exit | row_count | wall_seconds | prep_seconds | max_rss_kb | max_rss_floor_kb |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| s3-fast-list | list | parquet | key,size,etag,mtime | - | s3-fast-list.90992fe12168 | 1 | n4-highcpu-4 | 4 | 8 | 2 | cpus=0-1;memory=2GiB | cpus=2;memory=2GiB | vcpus=1;memory=4GiB | UNCALIBRATED | canary | timing | SUCCEEDED | RESULT_BOUND | COMPLETE | 0 | 0 | 2048 | 5.033424 | - | 50048 | 50144 |
| s3p | ls | text | key | 100 | s3p.276aea2ef416 | 1 | n4-highcpu-4 | 4 | 8 | 2 | cpus=0-1;memory=2GiB | cpus=2;memory=2GiB | vcpus=1;memory=4GiB | UNCALIBRATED | canary | timing | SUCCEEDED | RESULT_BOUND | COMPLETE | 0 | 0 | 2048 | 0.734464 | - | 88484 | 49844 |
| s7cmd | recursive-tsv | text | key,size,etag,mtime,storage_class | 64 | s7cmd.ee7094a12693 | 1 | n4-highcpu-4 | 4 | 8 | 2 | cpus=0-1;memory=2GiB | cpus=2;memory=2GiB | vcpus=1;memory=4GiB | UNCALIBRATED | canary | timing | SUCCEEDED | RESULT_BOUND | COMPLETE | 0 | 0 | 2048 | 0.264072 | - | 50300 | 50356 |

**3 attempt(s)** -- 0 successful timing(s); row counts are reported from result.json; no content comparison
```

## Receipt export

```sh
uv run python benchmark/src/benchmark/receipt.py \
  --state "$STATE_ROOT/replay-canary-current.db" \
  --group replay-canary-current-20260826 \
  --output receipts/replay-canary-current-20260826
```

Exit `0`:

```text
receipt: wrote receipts/replay-canary-current-20260826/receipt.json and receipts/replay-canary-current-20260826/README.md
```
