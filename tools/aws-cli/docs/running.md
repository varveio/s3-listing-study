# aws-cli — running

How to reproduce the environment used for this tool page, every mode we tried
with its invocation, and how to
re-run any receipt or the fan-out union from scratch. Groundwork pass:
2026-07-17, aws-cli **2.36.1**, bucket `noaa-normals-pds`, all anonymous.

## Install / image

**Image chosen: the official upstream image**, pinned by digest —
`amazon/aws-cli:2.36.1` @
`sha256:406ca32d31e640a56e8d52921b40528cc64bfa59ec9cb4ee1456db6746cb7292`.
This is what AWS publishes and what users commonly run, so no self-authored
Dockerfile is needed. The image entrypoint is `/usr/local/bin/aws`
`[RUN docker inspect]`, so every invocation below (and every `run.sh` argv)
starts at the subcommand (`s3api` / `s3`), not the `aws` binary name.

In-container version confirmed:
`aws-cli/2.36.1 Python/3.14.6 Linux/6.17.0-1020-gcp docker/aarch64.amzn.2023`
`[RUN receipts/smoke/_build/version-help.md]`.

**Note on co-identity.** This same tool is also the study's pinned harness
client (`amazon/aws-cli@sha256:eb85b2c7…`, version 2.36.0, one patch older
than the 2.36.1 smoked here). aws-cli's own manifest/pre-flight listings are
what every other tool in this study gets checked against — see
`research/report.md` § Notable findings for the bias this creates.

Outside a container, aws-cli is already installed on most boxes; otherwise
`pip install awscli` (v1) or the official v2 installer.

## Modes smoked, with invocation

All commands below add `--no-sign-request` (anonymous) and `--region
us-east-1`; `<bucket>` is `noaa-normals-pds`, `<prefix>` per row. The
mode-name column matches `tools/aws-cli/adapter/run.sh` and `run.meta`'s `mode=`
field.

| Mode | Invocation (inside the container) | Scope smoked | Receipt |
| --- | --- | --- | --- |
| `s3api-v2-text` | `s3api list-objects-v2 --bucket <bucket> --region us-east-1 --no-sign-request [--prefix <prefix>] --query 'Contents[].[Key,Size,ETag,LastModified,StorageClass]' --output text` | full bucket (148,917) + 3 named prefixes | `receipts/smoke/s3api-v2-text/`, `-hourly/`, `-monthly1991/`, `-annualaccess/` |
| `s3api-v2-json` | same, `--output json` | prefix `normals-hourly/` (2,549) | `receipts/smoke/s3api-v2-json-hourly/` |
| `s3api-v2-yamlstream` | same, `--output yaml-stream` | prefix `normals-hourly/` (2,549) | `receipts/smoke/s3api-v2-yamlstream-hourly/` |
| `s3api-v1-text` | `s3api list-objects --bucket <bucket> --region us-east-1 --no-sign-request --prefix <prefix> --query 'Contents[].[Key,Size,ETag,LastModified,StorageClass]' --output text` (legacy V1, Marker pagination) | prefix `normals-hourly/` (2,549) | `receipts/smoke/s3api-v1-text-hourly/` |
| `s3api-versions-text` | `s3api list-object-versions --bucket <bucket> --region us-east-1 --no-sign-request --prefix <prefix> --query 'Versions[].[Key,Size,ETag,LastModified,StorageClass]' --output text` | prefix `normals-hourly/` (2,549) | `receipts/smoke/s3api-versions-text-hourly/` |
| `s3-ls-recursive` | `s3 ls s3://<bucket>/ --recursive --region us-east-1 --no-sign-request` | full bucket (148,917) | `receipts/smoke/s3-ls-recursive/` |
| `s3-ls-delimiter` | `s3 ls s3://<bucket>/ --region us-east-1 --no-sign-request` (no `--recursive`) | delimiter, root (5 entries) | `receipts/smoke/s3-ls-delimiter/` |
| `s3api-v2-delimiter` | `s3api list-objects-v2 --bucket <bucket> --region us-east-1 --no-sign-request --delimiter / --output json` | delimiter, root (5 entries) | `receipts/smoke/s3api-v2-delimiter/` |
| `s3api-v2-remainder` | `s3api list-objects-v2 --bucket <bucket> --region us-east-1 --no-sign-request --delimiter / --query 'Contents[].[Key,Size,ETag,LastModified,StorageClass]' --output text` (Contents-only root run, used as the fan-out remainder shard) | root orphan key `index.html` (1) | `receipts/smoke/fanout/remainder/` |

Each mode's exact argv-building logic lives in `adapter/run.sh` (do not edit; it's
immutable per this consolidation's scope) — it prints the argv the wrapper
executes, never runs anything itself.

## Reproducing a receipt via `harness/smoke-run.sh`

Every receipt above was produced by the shared wrapper, never by running
aws-cli directly on the host (methodology § Run records (receipts)). To reproduce one:

```sh
harness/smoke-run.sh \
  --tool aws-cli --mode s3api-v2-text \
  --image amazon/aws-cli@sha256:406ca32d31e640a56e8d52921b40528cc64bfa59ec9cb4ee1456db6746cb7292 \
  --run-script tools/aws-cli/adapter/run.sh \
  --bucket noaa-normals-pds --region us-east-1 [--prefix normals-hourly/] \
  --auth anonymous \
  --out <output-dir> --timeout 300 --tool-version 2.36.1
```

`smoke-run.sh` owns `docker run` entirely — image, mounts, network,
credential injection or starving, timeout, cleanup, RSS/cgroup sampling, and
the receipt (`receipt.md` + `run.meta`). It refuses a non-digest-pinned
image and enforces the 300s/mode guardrail regardless of what `--timeout` is
asked for. Output is checked against the registered smoke-bucket manifest
(`docs/smoke-bucket.md`) by `harness/verify-listing.sh` via
`tools/aws-cli/adapter/normalize.sh`, which projects each mode's raw output into the
common `key\tsize\tetag\tmtime\tstorage_class` shape the verifier compares.
Do not edit `adapter/normalize.sh`; it's an immutable input to this consolidation.

## Fan-out union procedure

aws-cli has no built-in parallel listing; "fan-out" is the caller manually
running N disjoint-prefix invocations and unioning the results. What was
actually run, 2026-07-17:

1. Four prefix shards, each a normal `s3api-v2-text` mode run scoped to one
   top-level prefix: `normals-monthly/` (48,796 keys),
   `normals-daily/` (48,787), `normals-annualseasonal/` (48,784), and
   `normals-hourly/` (2,549 — the same receipt as the `s3api-v2-text-hourly`
   scoped run above, reused as a shard).
2. One **remainder** shard for keys outside every top-level prefix: the
   `s3api-v2-remainder` mode above, a delimiter-`/` root run projecting only
   `Contents` (not `CommonPrefixes`), returning exactly the orphan key
   `index.html`.
3. Shards were run **serially** here (each invocation's own listing
   concurrency is 1 regardless; aws-cli has no internal listing concurrency
   to bound, so the study's `CONCURRENCY_CAP=4` was never approached).
4. `harness/verify-listing.sh --scope union` merges the shard outputs,
   checks for cross-shard duplicates, and confirms the union against the
   manifest: 148,917 distinct, 0 duplicates, 0 missing, 0 extra, structurally
   complete. Result: `receipts/smoke/fanout/union/union-verify.md`; per-shard
   receipts: `receipts/smoke/fanout/{shard-monthly,shard-daily,shard-annualseasonal,remainder}/`
   (the `normals-hourly/` shard reuses `s3api-v2-text-hourly/`).

To reproduce: run each shard through `smoke-run.sh` exactly as in
"Reproducing a receipt" above with the shard's `--prefix` (or the delimiter
flags for the remainder), then invoke
`harness/verify-listing.sh --scope union` pointed at the shard receipt
directories — see `receipts/smoke/fanout/union/union-verify.md` for the
exact shard list it was pointed at.

## Capability probes (not wrapper-measured modes)

Two probes exist outside the mode table above because their invocations are
either non-repeatable-by-`run.sh` (a dynamic resume token) or diagnostic
rather than a listing mode (`--debug`). Both are documented in full,
including the exact `docker run` invocation, under
`receipts/smoke/_capability/`:

- **`--debug` request-behavior probe** (`_capability/README.md`) — one
  `s3api list-objects-v2` invocation against `normals-hourly/` with `--debug`
  added, to observe request count, threading, and continuation-token
  chaining directly off the wire log. Scope: this is one invocation, not an
  exhaustive per-flag or per-surface sweep — see
  [`mechanism.md`](mechanism.md#concurrency--serial-listing-supported-by-source-and-one-probe).
- **Resume round-trip probe** (`_capability/resume-README.md`) — two
  invocations: `--max-items 1000` against `normals-hourly/` (emits a
  `NextToken`), then `--starting-token <that token>` to resume. This is
  deliberate chunked continuation, not a kill-and-resume crash test — no
  process was killed in producing this receipt.

## Reproduction reference

- Anonymous access: add `--no-sign-request` to any command above; it's a
  global flag, applies to both `s3` and `s3api`.
- Region: `--region us-east-1` (the bucket's region); otherwise falls back to
  config/env like any aws-cli invocation.
- Manifest / registry for verification: `docs/smoke-bucket.md` (bucket
  identity, region, manifest path + sha256, measured shape).
- Full receipt index and source-first report: see
  [`../README.md`](../README.md) and [`../research/report.md`](../research/report.md).
