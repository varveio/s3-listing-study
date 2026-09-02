# rclone

[rclone](https://github.com/rclone/rclone) lists an S3 bucket by paging ListObjectsV2 — either as a single flat recursive chain or as a per-directory hierarchical walk — and prints the result as JSON or delimited text through its `ls*` command family (lsjson/lsf/ls/lsl/lsd).
It is the upstream rclone project's own general-purpose multi-cloud sync/transfer tool, not a fork, and S3 is one of roughly 70 backends.

> **Study status (2026-09-scale-diagnostics).** This tool's standing in the current release:
> Completed the 143M-object fixture in 667.0 s at c64 (`rclone.6319ec57665d.s1`), nine rows short of the fixture count; its walk was killed at the 8 GiB limit on the flat 13.9M fixture.
> The release is diagnostic: no attempt in it carries `purpose = measurement`, so
> nothing here is a calibrated benchmark or a ranking. Report and data:
> [`results/2026-09-scale-diagnostics/REPORT.md`](../../results/2026-09-scale-diagnostics/REPORT.md).

## In the current release

The release `2026-09-scale-diagnostics` is diagnostic: it settles what ran, what
each run returned, and how much memory it used; no row in it is a calibrated
measurement, and nothing here is a ranking. rclone `1.74.4` ran in the release
in adapter modes `recursive-walk`, `recursive-fastlist` and
`recursive-hierarchical`.

| fixture | attempts | outcomes | timing grades of completed rows | row cited in the report |
| --- | ---: | --- | --- | --- |
| FourCast 4.08M | 17 | SUCCEEDED 16, CANCELLED 1 | INSUFFICIENT_EVIDENCE 9, CAPACITY_FAILED 5, NOT_APPLICABLE 1, TIMING_VALID 1 | none cited |
| NARA 13.5M | 6 | SUCCEEDED 6 | PRESSURE_DEGRADED 6 | none cited |
| real-changesets 13.9M (flat) | 5 | FAILED 3, SUCCEEDED 2 | TIMING_VALID 2 | `rclone.795fbd66217b.s1`, `rclone.d92a513cb0f2.s1`, `rclone.997236778cca.s3` |
| NBM 66.4M | 1 | SUCCEEDED 1 | TIMING_VALID 1 | none cited |
| blockchain 143M | 2 | SUCCEEDED 2 | PRESSURE_DEGRADED 1, TIMING_VALID 1 | `rclone.6319ec57665d.s1`, `rclone.3eff2ab4f661.s1` |

Only `recursive-walk` went above the 4.08M fixture, except on the flat fixture,
where `recursive-fastlist` also ran. On the 13.5M fixture none of its six rows
matched the capture report's count (no count was staged for that fixture, so
the reference is in the study's working notes, not a release field); the
report's standing notes give the two counts returned.

Largest fixture attempted: 143M, the largest replay fixture in the release.
The c64 arm completed in 667.0 s (`rclone.6319ec57665d.s1`). Its row count
was 143,008,665. That is nine rows short of the fixture count; the
directory-marker explanation for the shortfall is a diagnosis from the runner
log and working notes, not a release field. The c128 arm took 821.0 s
(`rclone.3eff2ab4f661.s1`); both are single rows and the release does not
establish a width effect.

Setup asymmetry named in the report: its blockchain row ran against a
20-vCPU replay server, while Swath's fastest blockchain row ran against a
64-vCPU server; subject allocations were equal at 8 vCPU / 8 GiB.

On timing grades: what `TIMING_VALID` / `PRESSURE_DEGRADED` /
`CAPACITY_FAILED` / `INSUFFICIENT_EVIDENCE` / `NOT_APPLICABLE` mean is on
[`docs/instrument.md`](../../docs/instrument.md); a grade describes the replay
instrument, not whether the tool succeeded.

Report: [`results/2026-09-scale-diagnostics/REPORT.md`](../../results/2026-09-scale-diagnostics/REPORT.md).
Findings page: [`RESULTS.md`](../../RESULTS.md). Rows:
[`results/2026-09-scale-diagnostics/attempts.jsonl`](../../results/2026-09-scale-diagnostics/attempts.jsonl).

The rows are an allowlisted public projection of the campaign ledger; the
original result files and logs are private. The receipts under `receipts/` in
this directory are groundwork evidence and do not cover the release rows.

## At a glance

Groundwork subject: the pinned build, smoke runs and source study from August
2026. The current release's rows are in the section above.

| Question | Current answer |
| --- | --- |
| Tested subject | Upstream's own published image `rclone/rclone@sha256:c619…dc4a1` (tag `1.74.4`), **unpatched**, tool self-reporting `rclone v1.74.4`, source pinned at commit `5bc93a2a7`, run anonymously. Full canonical identity is in [`data/tool.json`](data/tool.json). |
| Exercised coverage | During groundwork: every S3 request pattern that changes the wire shape: flat `ListR`, the genuine hierarchical walk (`--disable ListR`), delimiter-shallow, legacy v1, and `lsf`. The `ListObjectVersions` API was not smoked during groundwork or in the release (bucket unversioned); scale behaviour was not exercised during groundwork; the release ran `recursive-walk`, `recursive-fastlist` and `recursive-hierarchical` on fixtures to 143M objects (section above). |
| Correctness | Every smoke-era verifier-checked mode PASSed (0 duplicates / missing / extra / field mismatches) and the full bucket re-listed key-by-key against the manifest of 148,917 keys. The newer directory-preserving arm has parser coverage but no groundwork receipt and no release row. See [`docs/running.md`](docs/running.md#every-smoked-mode) and claim `smoke-listing-correct-all-modes`. |
| Smoke observation | A receipted full-bucket run listed all 148,917 keys, exited 0 in 16.95 s, and peaked at 69.6 MB RSS. These are facts of single groundwork runs, not benchmark results. |
| Results | No calibrated benchmark or comparative result exists in this study. The current release's rows for this tool (section above) are diagnostic; smoke timing and memory values in this table describe single groundwork runs. |

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
| Flat `ListR` (`lsjson --fast-list -R` or plain `lsjson -R`) | Recursively list a bucket/prefix as one undelimited ListObjectsV2 chain. | Run and traced against the smoke bucket in a full scope and two prefixes; verified PASS. **Release:** ran in `2026-09-scale-diagnostics` as adapter mode `recursive-fastlist` (`lsjson --fast-list -R`; the flat 13.9M fixture only) and as adapter mode `recursive-hierarchical` (plain `lsjson -R`; the 4.08M fixture only) (section above). |
| Hierarchical walk (`lsjson --disable ListR -R`) | List directory-by-directory with `Delimiter=/`, fanning children across `--checkers`. | Forced and run; PASS 9841/9841. A separate header probe traced 13 `delimiter=%2F` page requests across four directory chains. **Release:** ran in `2026-09-scale-diagnostics` as adapter mode `recursive-walk` (fixtures to 143M objects; section above). |
| Directory-preserving hierarchical walk | The same walk without `--files-only`, retaining `IsDir` entries. | Capsule and parser fixture only. No groundwork receipt and no release row; key-by-key verification must establish whether it restores trailing-slash objects, adds synthesized directory extras, or both. |
| Delimiter-shallow (`lsjson`/`lsf`/`lsd`, no `-R`) | One delimiter level: objects plus `CommonPrefixes`. | Run and verified during groundwork; not run in the release. |
| Legacy v1 (`--s3-list-version 1`) | `ListObjects` v1 with `Marker` paging. | Run and verified PASS on 2,549 keys during groundwork; not run in the release. |
| `ListObjectVersions` (`--s3-versions`) | List object versions. | Not run during groundwork or in the release; the smoke bucket is unversioned. |

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
  **Release update:** on the flat 13.9M fixture, which has no directories to
  fan across, the walk and the flat mode each issued the same number of pages
  (the report's flat-namespace section); the page count is from the runner
  log, not a release field.

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
  **Release update:** on the flat 13.9M fixture the walk was killed at the
  8 GiB limit with subject exit -9 (`rclone.795fbd66217b.s1`). At 16 GiB it
  completed with a peak RSS of 8,121,012 KiB (`rclone.d92a513cb0f2.s1`). The
  flat `--fast-list` arm completed the same fixture at 73,764 KiB
  (`rclone.997236778cca.s3`). The killed row's exit code was -9, not 0; that
  the kill was the memory limit acting is a diagnosis from the runner log, not
  a release field.

## Limitations and open questions

### Coverage gaps

- Smoke exercised request shape and completeness only; no comparative numbers
  exist; the scale rows are in the release section above and are diagnostic.
- `ListObjectVersions` was not smoked during groundwork or in the release
  (unversioned bucket), and `EDGE_BUCKET=none` defers unicode / weird-key /
  multipart-ETag fidelity.
- During groundwork only one non-default `--checkers` value and only the
  default `--s3-list-chunk` ran; neither was swept. The release rows carry
  `recursive-walk` arms at several `--checkers` values (section above), as
  single runs, not a sweep.
- The directory-preserving recursive arm has no groundwork receipt and no
  release row. Rclone's `IsDir` output does not by itself distinguish a real
  trailing-slash object from synthesized hierarchy, so the mode is useful only
  with key-by-key missing/extra verification.

### Harness and verifier notes

- etag is `-` in every mode by design: rclone's S3 listing path surfaces no raw
  ETag, so the adapter declines it rather than assert a false field.
- A live NOAA bucket drift (mtime-only) was observed mid-session and flagged to
  the manifest owner; it is a third-party event, not a tool finding — claim
  `noaa-bucket-drift-event`.

### Benchmark questions

- Does `--fast-list` stay memory-bounded, or hit an OOM cliff, on a deep or
  enormous keyspace under a cgroup cap?
  **Release update:** one `recursive-fastlist` row completed the flat 13.9M
  fixture at 73,764 KiB peak RSS under an 8 GiB limit
  (`rclone.997236778cca.s3`); no larger fixture ran in that mode.
- Can the exit-0-after-OOM report be reproduced with a sync-shaped workload — and
  what does it say about v1.74.4 versus the reporter's v1.67?
- At what `--checkers` does the forced walk beat or lose to the flat `ListR`, and
  how do `--s3-list-chunk` and v1-vs-v2 trade off at scale?
  **Release update:** not answered; the release does not establish width
  effects, and no arm swept `--s3-list-chunk` or list-version.
- Does an interrupted listing leave usable output, given no LIST crash-resume
  state exists in source?
  **Release update:** the walk row killed at 8 GiB on the flat fixture
  returned no row count (`rclone.795fbd66217b.s1`); that it had issued every
  page and written nothing is a diagnosis from the runner log, not a release
  field.

## Navigate this directory

| If you want to… | Go to… |
| --- | --- |
| Understand the request patterns, pagination, pacer, memory, and output model | [`docs/mechanism.md`](docs/mechanism.md) |
| Reproduce the image and see exactly what smoke did or deferred | [`docs/running.md`](docs/running.md) |
| Inspect canonical identity, tested-subject, eligibility, and claim status data | [`data/tool.json`](data/tool.json) and [`data/claims.json`](data/claims.json) |
| Integrate the subject with the shared harness | [`adapter/`](adapter/) |
| Inspect registered inputs for the shared derived-image build | [`build/image.json`](build/image.json) |
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
execution paths. Rows in `results/` are the public projection of the campaign
ledger, separate from the receipts here; neither is a benchmark result.
