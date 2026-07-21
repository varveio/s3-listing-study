# rclone

[rclone](https://github.com/rclone/rclone) lists an S3 bucket by paging ListObjectsV2 — either as a single flat recursive chain or as a per-directory hierarchical walk — and prints the result as JSON or delimited text through its `ls*` command family (lsjson/lsf/ls/lsl/lsd).
It is the upstream rclone project's own general-purpose multi-cloud sync/transfer tool, not a fork, and S3 is one of roughly 70 backends.
This study's groundwork is complete; no benchmark comparison has been run.

## At a glance

| Question | Current answer |
| --- | --- |
| Tested subject | Upstream's own published image `rclone/rclone@sha256:c619…dc4a1` (tag `1.74.4`), **unpatched**, tool self-reporting `rclone v1.74.4`, source pinned at commit `5bc93a2a7`, run anonymously. Full canonical identity is in [`data/tool.json`](data/tool.json). |
| Exercised coverage | Every S3 request pattern that changes the wire shape: flat `ListR`, the genuine hierarchical walk (`--disable ListR`), delimiter-shallow, legacy v1, and `lsf`. The `ListObjectVersions` API was not smoked (bucket unversioned); scale behaviour was not exercised. |
| Correctness | Every verifier-checked mode PASSed (0 duplicates / missing / extra / field mismatches) and the full bucket re-listed byte-exact against the manifest of 148,917 keys. See [`docs/running.md`](docs/running.md#every-smoked-mode) and claim `smoke-listing-correct-all-modes`. |
| Smoke observation | A receipted full-bucket run listed all 148,917 keys, exited 0 in 16.95 s, and peaked at 69.6 MB RSS. These are facts of single groundwork runs, not benchmark results. |
| Results | No benchmark or comparative result exists. Smoke timing and memory values describe individual groundwork runs only. |

## How it works

rclone's S3 backend has two distinct request patterns: a flat, undelimited
`ListR` that pages the whole keyspace as one serial `continuation-token` chain,
and a per-directory hierarchical walk (`Delimiter=/`) that fans discovered
directories across `--checkers` workers. For the `ls*` listing commands these are
**not** selected by `--fast-list`: a plain `lsjson -R` is already the flat path,
and the walk must be forced with `--disable ListR` or `--max-depth`. Pagination
within any one prefix is serial, and there is no key-range sharding. Default
`lsjson` HEADs every object to compute ModTime/MimeType unless
`--use-server-modtime --no-mimetype` suppress it. Full account:
[`docs/mechanism.md`](docs/mechanism.md).

## Modes and study coverage

Upstream mode surface and this study's actual coverage are shown separately.

| Mode | Upstream purpose | What this study exercised |
| --- | --- | --- |
| Flat `ListR` (`lsjson --fast-list -R` or plain `lsjson -R`) | Recursively list a bucket/prefix as one undelimited ListObjectsV2 chain. | Run and traced against the smoke bucket in a full scope and two prefixes; verified PASS. |
| Hierarchical walk (`lsjson --disable ListR -R`) | List directory-by-directory with `Delimiter=/`, fanning children across `--checkers`. | Forced and run; PASS 9841/9841. A separate header probe traced 13 `delimiter=%2F` page requests across four directory chains. |
| Delimiter-shallow (`lsjson`/`lsf`/`lsd`, no `-R`) | One delimiter level: objects plus `CommonPrefixes`. | Run and verified. |
| Legacy v1 (`--s3-list-version 1`) | `ListObjects` v1 with `Marker` paging. | Run and verified PASS on 2,549 keys. |
| `ListObjectVersions` (`--s3-versions`) | List object versions. | Not run; the smoke bucket is unversioned. |

The upstream tool also exposes `--s3-list-chunk`, `--checkers`, `--tpslimit`, the
pacer, and many other backends and flags; their presence does not mean the study
exercised them. Detailed coverage is in
[`docs/running.md`](docs/running.md#every-smoked-mode).

## What we learned

Each finding links its owning explanation and its canonical claim ID; claim IDs
resolve in [`data/claims.json`](data/claims.json).

- **`--fast-list` does not select the request pattern for `ls*` commands.** A
  plain `lsjson -R` is already the flat `ListR`; the genuine per-directory walk
  must be forced with `--disable ListR` or `--max-depth`. This corrects the
  inherited "default = walk, `--fast-list` = flat" model.
  [`Two request patterns`](docs/mechanism.md#two-request-patterns--and-what-selects-them)
  · `mode-selector-is-not-fast-list`

- **Listing has no key-range parallelism; the only concurrency is across
  directories.** Pagination within a prefix is a serial cursor-chained loop, and
  only the hierarchical walk fans distinct directories across `--checkers`.
  [`Pagination is a serial cursor-chained loop`](docs/mechanism.md#pagination-is-a-serial-cursor-chained-loop)
  · `no-intra-prefix-keyspace-sharding`, `pagination-is-serial-within-prefix`

- **Default `lsjson` silently HEADs every object.** ModTime and MimeType each do
  a HEAD per object unless suppressed, turning a listing into a 148,917-HEAD storm;
  proper listing must pass `--use-server-modtime --no-mimetype`. The suppressed
  correct path is receipt-backed; the storm magnitude is source-only.
  [`The HEAD-per-object footgun`](docs/mechanism.md#the-head-per-object-footgun)
  · `head-per-object-storm-mechanism`, `head-per-object-suppressed-at-smoke`

- **The pacer is error-driven backoff, not "AIMD on delay" concurrency.** The S3
  calculator keys on error/retry state and decays inter-request sleep to zero
  below `minSleep`; it never reacts to latency and never adapts concurrency.
  [`Retries and the pacer`](docs/mechanism.md#retries-and-the-pacer-error-driven-decays-to-zero)
  · `s3-pacer-is-error-driven`, `pacer-adapts-sleep-not-concurrency`

- **The memory/OOM and exit-0 questions stay open at scale.** Smoke peaked near
  70 MB and settles nothing at 10^8 keys; the reported exit-0-after-OOM behaviour
  is an unsettled third-party report about the v1.67-era `sync` path, not the
  pinned v1.74.4 listing path.
  [`The exit-0-on-OOM report and its caveats`](docs/running.md#the-exit-0-on-oom-report-and-its-caveats)
  · `fast-list-memory-at-scale`, `oom-exit-zero-report`

## Limitations and open questions

### Coverage gaps

- Smoke exercised request shape and completeness only; no comparative or
  scale-dependent numbers exist.
- `ListObjectVersions` was not smoked (unversioned bucket), and
  `EDGE_BUCKET=none` defers unicode / weird-key / multipart-ETag fidelity.
- Only one non-default `--checkers` value and only the default `--s3-list-chunk`
  ran; neither was swept.

### Harness and verifier notes

- etag is `-` in every mode by design: rclone's S3 listing path surfaces no raw
  ETag, so the adapter declines it rather than assert a false field.
- A live NOAA bucket drift (mtime-only) was observed mid-session and flagged to
  the manifest owner; it is a third-party event, not a tool finding — claim
  `noaa-bucket-drift-event`.

### Benchmark questions

- Does `--fast-list` stay memory-bounded, or hit an OOM cliff, on a deep or
  enormous keyspace under a cgroup cap?
- Can the exit-0-after-OOM report be reproduced with a sync-shaped workload — and
  what does it say about v1.74.4 versus the reporter's v1.67?
- At what `--checkers` does the forced walk beat or lose to the flat `ListR`, and
  how do `--s3-list-chunk` and v1-vs-v2 trade off at scale?
- Does an interrupted listing leave usable output, given no LIST crash-resume
  state exists in source?

## Navigate this directory

| If you want to… | Go to… |
| --- | --- |
| Understand the request patterns, pagination, pacer, memory, and output model | [`docs/mechanism.md`](docs/mechanism.md) |
| Reproduce the image and see exactly what smoke did or deferred | [`docs/running.md`](docs/running.md) |
| Inspect canonical identity, tested-subject, eligibility, and claim status data | [`data/tool.json`](data/tool.json) and [`data/claims.json`](data/claims.json) |
| Integrate the subject with the shared harness | [`adapter/`](adapter/) |
| Audit how every old ledger row and status-bearing prose claim became atomic current claims | [`research/claims-migration.md`](research/claims-migration.md) and the preserved reconciliation in [`research/`](research/) |
| Read the historical pre-restructure landing page | [`research/tool-page.md`](research/tool-page.md) — frozen historical research, not the current entry point |
| Inspect the immutable smoke receipts and capability probes | [`receipts/`](receipts/) |

## Provenance

**Mixed provenance.** This page combines firsthand source, image, and smoke work
with inherited secondhand notes compiled from public sources.
The seed was not a run record. See
[`research/tool-page.md`](research/tool-page.md) and
[`research/reconciliation.md`](research/reconciliation.md).

## Evidence boundary

Source and documentation explain mechanisms and risks; only a committed receipt
confirms run-dependent study behavior. Smoke observations are single-run
groundwork facts, not benchmark results, and are not bound across different
execution paths.
