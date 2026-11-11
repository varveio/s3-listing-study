# Swath (Go port): how it works

Scope: how the Go port of Swath lists S3, bounds its concurrency, orders and
writes its output, and fails and resumes, at the pinned revision `8c8f870`,
an unreleased commit on upstream's main (canonical identity:
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
distribution, not its command line, and upstream means it to replace the Java
tool at release — claims `go-port-is-a-separate-tool`, `static-cgo-free-binary`.
It lists a local directory, an S3 bucket, a GCS bucket or an Azure Blob
container; this capsule drives only S3. It has a release candidate but no
release, and the subject is an unreleased main commit newer than that candidate
— claim `subject-is-an-unreleased-main-commit` — and the build stamps that
revision into `--version` and the run report — claim
`build-stamps-the-pinned-revision`.

## Listing, ranges and order

An S3 listing is a tree of key ranges. Workers take ranges and expand each into
rows that are already ascending plus child ranges that sort between them; the
order stage walks the tree depth-first, which is global key order, and streams
rows at the frontier to the writer. There is no external sort and no merge
pass, and splits are made by the range's owner when workers are idle rather
than stolen — claim `ranges-expand-in-key-order-without-a-sort`. By default the
scope is first tiled from delimiter listings of the folder hierarchy; turning
that off is the diagnostic `swath diag list --no-seed` — claim
`seed-is-on-by-default`. Structure probes, which let a split pivot on folder
boundaries, run at `auto`; changing them is a diagnostic flag on `swath diag
list` too — claim `structure-is-a-diagnostic-flag`.

Page fetches are `ListObjectsV2` requests sent through the AWS SDK's S3 service
client, which also owns the credential chain and SigV4 signing
[SRC go/docs/internals/clients.md:3-4,10-17 @ 8c8f870]; at the capsule's
earlier pins they went through a client of the port's own on the SDK's
authentication.

## Concurrency and the request limiter

`--workers` (default 64 for S3, whatever the CPU count, no upper bound) is both
the number of listing workers and the maximum of a request limiter that every
HTTP attempt passes through — claim `workers-default-64-is-the-limiter-maximum`.
The limiter starts at that maximum and multiplies the limit by 0.7 when a
throttle answer arrives while at least 15 % of a sliding window of recent
answers are throttles; it grows back one permit per second while the window
holds under 7.5 % or after 10 s without a throttle. `--no-adaptive-limit`
removes the limiter: the workers still wait on one page each, but seed and
probe requests run beside them, so `--workers` no longer bounds the requests in
flight — claim `limiter-is-aimd-on-throttle-answers`. So `--workers` is a
ceiling, not a setpoint, and the capsule declares it as one.

Two differences from the Java engine matter to anyone comparing them: the port
starts at N rather than `min(4, N)` (the Java ramp is the diagnostic
`--adaptive-limit-slow-start`, not rendered by this capsule), and splits are
owner-side rather than work-stealing. A third matters to anyone comparing this
pin with the capsule's earlier ones: there, every throttle answer cut the limit.

## Memory and spill

`--sort-buffer` is one budget for rows and scheduler metadata held between
listing and encoding. Its default is 1/16 of the least of physical memory, the
cgroup's limit and `GOMEMLIMIT`, at most 1 GiB (128 MiB when none is found), so
it follows the box: 512 MiB in a subject container capped at 8 GiB, 1 GiB on an
uncapped 64 GiB machine, where the capsule's earlier pins had a fixed 128 MiB
`--row-budget`. When it is full, rows of ranges ahead of the frontier spill to
disposable files instead of stalling the workers, up to `--spill-budget` —
claim `sort-buffer-and-spill-budget-bound-the-reorder-stage`. A dataset run
spills under `DIR/.swath/spill/`; a text run spills to a scratch directory
beside its file, on the same filesystem — claim `text-runs-keep-no-journal`.
The budget bounds rows, not the process; peak memory at scale is a run
question — claim `throughput-and-memory-at-scale`.

## Output

The `-o` name decides the product. A directory name gives a globally key-sorted
swath dataset, which is a SeekParquet dataset: dense, disjoint
`data/part-NNNNN.parquet` parts and a `manifest.json` (`format` `seekparquet`,
`format_version` 0, `draft_revision` 1) that lists them in order with exact key
bounds, row counts, sizes and SHA-256 digests, with the source, scope and
coverage under `extensions.swath` — claim `output-is-a-seekparquet-sorted-dataset`.
The manifest is installed last by an atomic rename, so a directory without one
is not a dataset even when parts are already in `data/` — claim
`manifest-is-the-commit`. Its coverage is the tool's own statement of
completeness — claim `coverage-states-completeness` — and the S3 schema is the
nine-column current-object schema `swath/s3-objects/v1` — claim
`s3-schema-is-current-object-nine-columns`. This is neither the Java capsule's
format 1 (there is no `_SUCCESS` and no `row_type`) nor the `swath-dataset`
format 2 the port wrote at the capsule's earlier pins, and the normalizer reads
only this one.

A name ending `.tsv`, optionally `.gz` or `.zst`, gives one TSV file instead:
a header of the schema's column names (`key`, `size`, `last_modified`, `etag`,
...) and one row per object with the dataset's values, the ETag without its
quotes and times in UTC with six fraction digits and `Z` — claim
`text-output-is-one-file-of-the-dataset-columns`. Fields are written in the
port's TSV escape grammar (`\\`, `\t`, `\n`, `\r`, `\xHH`) with `\N` for a
null, which is reversible — claim `text-fields-use-reversible-tsv-escapes`. At
the capsule's earlier pins the file was shaped as the S3 API's object instead
(`Key`, `LastModified`, a quoted `ETag`) with JSON string escapes. The Java
tool's TSV directory of parts has no equivalent here.

## Failure, reports and resume

The exit code classifies the outcome, and the resumable classes leave a
journaled dataset run that `swath resume DIR` continues with the command it
recorded, accepting only runtime and operational flags, a recorded switch
undone by its other spelling (`--no-anonymous`, `--adaptive-limit`) — claims
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
- An unstated endpoint defaults to `AWS_ENDPOINT_URL_S3`, else
  `AWS_ENDPOINT_URL`, so a worker environment that sets either sends a live S3
  row elsewhere — claim `s3-flag-surface`.
