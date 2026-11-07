# Swath (Go port): how it works

Scope: how the Go port of Swath lists S3, bounds its concurrency, orders and
writes its output, and fails and resumes, at the pinned revision `a2efe16`,
the merge commit of upstream pull request varveio/swath#255 (canonical identity:
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
distribution and a few flag aliases, not its command line — claims
`go-port-is-a-separate-tool`, `static-cgo-free-binary`. It lists a local
directory, an S3 bucket or a GCS bucket; this capsule drives only S3. It has no
release, so the subject is an unreleased main commit — claim
`subject-is-an-unreleased-main-commit` — and the build stamps that
revision into `--version` and the run report — claim
`build-stamps-the-pinned-revision`.

## Listing, ranges and order

An S3 listing is a tree of key ranges. Workers take ranges and expand each into
rows that are already ascending plus child ranges that sort between them; the
order stage walks the tree depth-first, which is global key order, and streams
rows at the frontier to the writer. There is no external sort and no merge
pass, and splits are made by the range's owner when workers are idle rather
than stolen — claim `ranges-expand-in-key-order-without-a-sort`. By default the
scope is first tiled from delimiter listings of the folder hierarchy;
`--seed=false` starts from one range — claim `seed-is-on-by-default`.
Structure probes, which let a split pivot on folder boundaries, run at `auto`;
changing them is a diagnostic flag on `swath diag list` — claim
`structure-is-a-diagnostic-flag`.

Page fetches are `ListObjectsV2` requests issued by a hand-rolled client on the
AWS SDK's authentication [SRC go/docs/architecture.md:47,50,61-62 @ a2efe16].

## Concurrency and the request limiter

`--workers` (default 64, whatever the CPU count, no upper bound) is both the
number of listing goroutines and the maximum of a request limiter that every
HTTP attempt passes through — claim `workers-default-64-is-the-limiter-maximum`.
The limiter starts at that maximum, multiplies the limit by 0.7 on each throttle
answer and grows back one permit per second after 10 s without one;
`--limiter=false` keeps a fixed `--workers` in flight — claim
`limiter-is-aimd-on-throttle-answers`. So `--workers` is a ceiling, not a
setpoint, and the capsule declares it as one.

Two differences from the Java engine matter to anyone comparing them: the port
starts at N rather than `min(4, N)` (the Java ramp is the opt-in
`--limiter-slow-start`, not rendered by this capsule), and splits are
owner-side rather than work-stealing.

## Memory and spill

`--row-budget` (default 128 MiB) is one budget for rows held between listing
and encoding; when it is full, rows of ranges ahead of the frontier spill to
disposable files instead of stalling the workers, up to `--spill-max` — claim
`row-budget-and-spill-bound-the-reorder-stage`. A dataset run spills under
`DIR/.swath/spill/`; a text run, which has no output directory, spills to the
process's temporary directory — claim `text-runs-keep-no-journal`. The budget
bounds rows, not the process; peak memory at scale is a run question — claim
`throughput-and-memory-at-scale`.

## Output

The `-o` name decides the product. A directory name gives a globally key-sorted
Parquet dataset in format 2: dense, disjoint `data/part-NNNNN.parquet` parts
and a `manifest.json` that lists them in order with exact key bounds, row
counts, sizes and checksums — claim `output-is-a-format-2-sorted-dataset`. The
manifest is installed last by an atomic rename, so a directory without one is
not a dataset even when parts are already in `data/` — claim
`manifest-is-the-commit`. Its coverage block is the tool's own statement of
completeness — claim `coverage-states-completeness` — and the S3 schema is the
nine-column current-object schema `swath/s3-objects/v1` — claim
`s3-schema-is-current-object-nine-columns`. Format 2 is not the Java capsule's
format 1: there is no `_SUCCESS`, no `row_type`, and a format-1 reader refuses
it.

A name ending `.tsv`, optionally `.gz` or `.zst`, gives one TSV file instead:
a header naming S3's nine `ListObjectsV2` fields in the API's order and one row
per object, the ETag with its quotes and `LastModified` in the AWS CLI's UTC
`+00:00` spelling — claim `text-output-is-one-file-shaped-as-the-s3-api`. Keys
are written with JSON's string escapes, which are reversible — claim
`text-keys-use-reversible-json-escapes`. The Java tool's TSV directory of parts
has no equivalent here.

## Failure, reports and resume

The exit code classifies the outcome, and the resumable classes leave a
journaled dataset run that `swath resume DIR` continues with the command it
recorded, accepting only runtime and operational flags — claims
`exit-codes-classify-the-outcome`, `resume-replays-the-recorded-command`. A
text run keeps no journal and is run again — claim `text-runs-keep-no-journal`.
`--report FILE` writes the run report when the attempt ends, on failure too —
claim `report-is-written-on-failure-too`. `--max-duration` has no limit by
default, and upstream asks a hosted runner for a finite one — claim
`hosted-runner-needs-a-finite-max-duration`.

## Source-supported risks

- The coverage block is self-reported. Whether a run lists its scope completely
  is `unverified` — claim `lists-a-bucket-completely`.
- Crash-resume is documented and tested upstream but not run here — claim
  `resume-after-kill-completes`.
- The pin is an unreleased commit, not a release — claim
  `subject-is-an-unreleased-main-commit`.
