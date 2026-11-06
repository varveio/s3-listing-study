# Swath (Go port): how it works

Scope: how the Go port of Swath lists S3, bounds its concurrency, orders and
writes its output, and fails and resumes, at the pinned revision
`63a4256` of the unmerged `spike/go-local-lister` branch (canonical identity:
[`../data/tool.json`](../data/tool.json)). Everything here is read from
upstream's public source and documentation. None of it is a run observation:
no run of this subject exists in the study, so every runtime question stays
`unverified` ([`running.md`](running.md)).

Notation: claim `some-id` resolves in [`../data/claims.json`](../data/claims.json),
which owns each claim's source anchors. An inline SRC label gives the path, lines and
revision for narrative that no claim represents.

## What the subject is

The port is its own module, `github.com/varveio/swath/go`, built as one static
pure-Go binary with CGO disabled; it shares the `swath` name with the Java
distribution and nothing on the command line — claims
`go-port-is-a-separate-tool`, `static-cgo-free-binary`. It lists a local
directory, an S3 bucket or a GCS bucket; this capsule drives only S3. It has no
release, so the subject is a branch head — claim
`subject-is-an-unmerged-branch-head` — and a binary built from the pinned source
archive reports `swath-go devel` with no revision — claim
`source-archive-build-has-no-revision`.

## Listing, ranges and order

An S3 listing is a tree of key ranges. Workers take ranges from a stack and
expand each into rows that are already ascending plus child ranges that sort
between them; one drainer walks the tree depth-first, which is global key
order, and streams rows at the frontier into the Parquet writer. There is no
external sort, staging or merge, and splits are made by the range's own worker
rather than stolen — claim `ranges-expand-in-key-order-without-a-sort`. With
`--seed` the scope is first tiled from delimiter probes of the folder
hierarchy; it is off by default — claim `seed-is-off-by-default`.

Page fetches are `ListObjectsV2` requests issued by a hand-rolled client on the
AWS SDK's authentication [SRC go/docs/architecture.md:32,35,46-47 @ 63a4256].

## Concurrency and the request limiter

`--workers` (default 64, whatever the CPU count) is both the number of listing
goroutines and the maximum of a request limiter that every HTTP attempt passes
through — claim `workers-default-64-is-the-limiter-maximum`. The limiter starts
at that maximum, multiplies the limit by 0.7 on each throttle answer and grows
back one permit per second after 10 s without one; `--limiter=false` keeps a
fixed `--workers` in flight — claim `limiter-is-aimd-on-throttle-answers`. So
`--workers` is a ceiling, not a setpoint, and the capsule declares it as one.

Two differences from the Java engine matter to anyone comparing them: the port
starts at N rather than `min(4, N)` (the Java ramp is the opt-in
`--limiter-slow-start`, not rendered by this capsule), and splits are
owner-side rather than work-stealing.

## Memory and spill

`--memory` (default 256 MiB) is one budget for rows held between listing and
encoding; when it is full, rows of ranges ahead of the frontier spill to
disposable files under `DIR/.swath/spill/` instead of stalling the workers
[SRC go/cmd/swath/main.go:309-310 @ 63a4256] [SRC go/docs/cli.md:266-268 @ 63a4256]. It bounds those rows, not the process; peak
memory at scale is a run question — claim `throughput-and-memory-at-scale`.

## Output

The only S3 output is a globally key-sorted Parquet dataset in format 2: dense,
disjoint `data/part-NNNNN.parquet` parts and a `manifest.json` that lists them
in order with exact key bounds, row counts, sizes and checksums — claim
`output-is-a-format-2-sorted-dataset`. The manifest is installed last by an
atomic rename, so a directory without one is not a dataset even when parts are
already in `data/` — claim `manifest-is-the-commit`. Its coverage block is the
tool's own statement of completeness — claim `coverage-states-completeness` —
and the S3 schema is the nine-column current-object schema
`swath/s3-objects/v1` — claim `s3-schema-is-current-object-nine-columns`.

Format 2 is not the Java capsule's format 1: there is no `_SUCCESS`, no
`row_type`, and a format-1 reader refuses it.

## Failure, reports and resume

The exit code classifies the outcome, and the resumable classes leave a
journaled run that `swath resume DIR` continues with the command it recorded,
accepting only runtime and operational flags — claims
`exit-codes-classify-the-outcome`, `resume-replays-the-recorded-command`.
`--report FILE` writes the run report when the attempt ends, on failure too —
claim `report-is-written-on-failure-too`. `--max-duration` has no limit by
default, and upstream asks a hosted runner for a finite one — claim
`hosted-runner-needs-a-finite-max-duration`.

## Source-supported risks

- The coverage block is self-reported. Whether a run lists its scope completely
  is `unverified` — claim `lists-a-bucket-completely`.
- Crash-resume is documented and tested upstream but not run here — claim
  `resume-after-kill-completes`.
- A source-archive build loses the self-reported revision — claim
  `source-archive-build-has-no-revision`.
