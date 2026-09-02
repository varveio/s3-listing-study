# s3kor

[s3kor](https://github.com/sethkor/s3kor) is a Go re-implementation of a subset of the `aws s3` CLI whose `ls` subcommand lists an S3 bucket and prints one plain-text key per line.
Its "parallel" reputation is transfer-side only: `ls` is a single serial `ListObjectsV2Pages` pagination chain, and the per-page goroutines merely format already-fetched pages rather than issuing concurrent LIST requests; the study tested upstream directly (no fork), under a GPL-3.0 license, against a project that has been dormant since 2022.

> **Study status (2026-09-scale-diagnostics).** This tool's standing in the current release:
> Completed the 4.08M-object fixture with a count that matched it, in 411.0 s (`s3kor.8514c3397199.s1`); serial listing, not carried further.
> The release is diagnostic: no attempt in it carries `purpose = measurement`, so
> nothing here is a calibrated benchmark or a ranking. Report and data:
> [`results/2026-09-scale-diagnostics/REPORT.md`](../../results/2026-09-scale-diagnostics/REPORT.md).

## In the current release

The release `2026-09-scale-diagnostics` is diagnostic: it settles what ran, what each run returned, and how much memory it used; no row in it is a calibrated measurement, and nothing here is a ranking.
In the release s3kor ran as `v0.0.37` in one adapter mode, `list`, under a scoped list-only credential.

| fixture | attempts | outcomes | timing grades of completed rows | row cited in the report |
| --- | ---: | --- | --- | --- |
| FourCast 4.08M | 9 | SUCCEEDED 9 | `NOT_APPLICABLE` 2, `TIMING_VALID` 7 | `s3kor.8514c3397199.s1` |

Three of the nine rows returned 4,081,171 rows and six returned 4,081,170; the capture report's count is 4,081,170 (from the study's working notes; no count was staged for FourCast, and `fixtures.json` records both observed values), so the cited row matched it and three rows carried a one-row surplus.

Largest fixture attempted: FourCast 4.08M. No larger one was scheduled because s3kor's listing is serial and its parallelism is in transfers (a reading of the tool's source, not a release field); "not carried further" is a study decision, not a measured limit of the tool.

A timing grade (`TIMING_VALID`, `PRESSURE_DEGRADED`, `CAPACITY_FAILED`, `INSUFFICIENT_EVIDENCE`, `NOT_APPLICABLE`) describes how well the replay instrument delivered its declared latency treatment on that row, not whether the tool succeeded; the definitions are in [`docs/instrument.md`](../../docs/instrument.md#how-a-release-grades-the-delivered-treatment).

Report: [`results/2026-09-scale-diagnostics/REPORT.md`](../../results/2026-09-scale-diagnostics/REPORT.md); findings page: [`RESULTS.md`](../../RESULTS.md); rows: [`results/2026-09-scale-diagnostics/attempts.jsonl`](../../results/2026-09-scale-diagnostics/attempts.jsonl).

The rows are an allowlisted public projection of the campaign ledger; the original result files and logs are private. The receipts under `receipts/` in this directory are groundwork evidence and do not cover the release rows.

## At a glance

Groundwork subject: the pinned build, smoke runs and source study from August 2026. The current release's rows are in the section above.

| Question | Current answer |
| --- | --- |
| Tested subject | Upstream s3kor at pinned commit `844fe3d` (release tag `v0.0.37`), built from source because upstream ships no image or Dockerfile. The go-install binary self-reports `dev-local-version none unknown`; canonical identity is in [`data/tool.json`](data/tool.json). |
| Exercised coverage | During groundwork: `list` has run under a scoped list-only credential and produced a listing. Anonymously both modes are **blocked**: s3kor has no unsigned listing path. `list-versions` was not exercised during groundwork or in the release. Scale behaviour was not exercised during groundwork; the release ran `list` on fixtures to 4.08M objects (section above). |
| Correctness | No groundwork verdict. The credentialed `list` attempt produced a listing, but auditing an attempt against a reference manifest is not implemented, so nothing has checked it. In the release the cited row's count matched the fixture (section above); that is cardinality agreement, not key-by-key verification. |
| Smoke observation | Both listing modes were blocked under `CREDS=none` with a startup panic at AWS session construction, exit 2, and zero S3 requests. This is a single-run capability finding, not a listing or benchmark result. |
| Results | No calibrated benchmark or comparative result exists in this study. The current release's rows for this tool (section above) are diagnostic; smoke timing and memory values in this table describe single groundwork runs. |

## How it works

s3kor's `ls` builds a `BucketLister` and calls the AWS SDK-for-Go v1
`ListObjectsV2Pages` auto-paginator with only a bucket and optional prefix — a
serial continuation-token chain with no keyspace division, delimiter, or
`MaxKeys` control. Each returned page is handed to its own goroutine that only
drains and formats the page onto a 50-slot channel, so the concurrency is in
output formatting, not in issuing LIST requests. Listing always uses the signing
credential chain; there is no `--no-sign-request` equivalent. Full detail:
[`docs/mechanism.md`](docs/mechanism.md).

## Modes and study coverage

| Mode | Upstream purpose | What this study exercised |
| --- | --- | --- |
| `list` | Recursively list a bucket or prefix through `ListObjectsV2`, printing one key per line. | Attempted anonymously against one public bucket; **blocked** because s3kor cannot list without credentials. Mechanism was read from source. No groundwork receipt; release rows exist (section above). **Release:** ran in `2026-09-scale-diagnostics` as adapter mode `list` (fixtures to 4.08M objects; section above). |
| `list-versions` | List every object version and delete marker via `ListObjectVersions` (`--all-versions`), printing `<versionId> <key>`. | Attempted anonymously; **blocked** by the same capability gap. Its flag name and output contract were corrected from source and live `--help`. |

s3kor's transfer subcommands (`cp`, `sync`, `rm`) expose the concurrency the
listing path lacks, but they are out of listing scope and were not exercised.

## What we learned

Each finding links its owning explanation and its canonical claim ID; claim IDs
resolve in [`data/claims.json`](data/claims.json).

- **s3kor cannot list a bucket without credentials.** The listing client always
  uses the signing credential chain; the two `AnonymousCredentials` uses in the
  codebase (region detection and the S3-to-S3 copy download) are not the listing
  client, so there is no unsigned listing path — a source finding confirmed
  behaviorally by the blocked smoke runs.
  [`No unsigned path for listing`](docs/mechanism.md#no-unsigned-path-for-listing)
  · `no-unsigned-listing-path`, `credential-starved-listing-blocked`

- **Listing is one serial paginator, not parallel.** `ls` walks a single
  `ListObjectsV2Pages` chain with only a bucket and prefix; the "threads" only
  format already-fetched pages and issue no requests, so the tool's parallel
  reputation does not apply to listing. Whether serial listing is a scale
  weakness is a benchmark question source cannot settle.
  [`Listing is one serial paginator`](docs/mechanism.md#listing-is-one-serial-paginator)
  · `listing-is-serial-paginator`, `serial-listing-scale-cost-unverified`
  **Release update:** the cited row `s3kor.8514c3397199.s1` issued 4,082 replay requests for the 4,082-page fixture (`replay.requests` in the row), consistent with one serial chain; the release does not establish the scale cost.

- **`ls` carries a source-visible concurrency race.** Per-page goroutines race a
  shared channel and `List` reassigns that channel after starting the printer
  and mis-sequences the `WaitGroup`, giving an abandoned-channel hang or a
  `Done`-before-`Add` panic. This was read from source and never observed,
  because every listing was credential-blocked.
  [`The page-vs-format goroutine race`](docs/mechanism.md#the-page-vs-format-goroutine-race)
  · `ls-concurrency-race-source`
  **Release update:** all nine release attempts on the 4.08M-object fixture completed (section above); the row schema does not record whether the race occurred.

- **Memory is streaming but not back-pressured.** The paginator callback spawns a
  goroutine per page and returns immediately, so the 50-slot channel caps blocked
  sends, not blocked sender goroutines, and peak memory can grow with in-flight
  pages. This is a source structural read; whether it OOMs at scale is unverified.
  [`Memory model`](docs/mechanism.md#memory-model)
  · `memory-streaming-not-backpressured`, `memory-oom-at-scale-unverified`
  **Release update:** peak RSS was 50,176 KiB on the 4.08M-object fixture in `s3kor.8514c3397199.s1`; no larger fixture was attempted.

- **The documented `--auto-region` flag does not exist.** The upstream README
  names `--auto-region`, but the actual region auto-detection flag is
  `--detect-region`, confirmed by source and live `--help`; the project is
  dormant, so the drift is unlikely to be fixed.
  [`docs/mechanism.md`](docs/mechanism.md) · `region-flag-doc-drift`

## Limitations and open questions

### Coverage gaps

- Every listing mode is blocked without credentials. A scoped, list-only
  credential against the smoke bucket is the minimal grant needed to run at all.
  **Release update:** the release ran `list` under such a credential; `list-versions` did not run.
- `list-versions` is manifest-comparable only on an unversioned bucket; the smoke
  bucket's versioning state is unrecorded. Canonical claim
  `list-versions-manifest-comparable-only-unversioned`.
- Only an arm64 image was built and run during groundwork; amd64 is native on
  every channel and is the expected benchmark denominator. **Release update:**
  the release rows ran on `n4-highcpu` machines (the `machine.type` field), not
  under emulation. Canonical claims `amd64-native-support`,
  `smoke-ran-arm64-native`.

### Harness and verifier blocker

- s3kor cannot participate in an anonymous-only (`CREDS=none`) benchmark. Its
  benchmark eligibility is `conditional` in [`data/tool.json`](data/tool.json) —
  a decision for the owner about whether to grant a scoped credential.
  **Release update:** a scoped credential was granted for the release rows (section above).
- The credential-starved harness makes s3kor panic at AWS session construction
  (exit 2, zero requests); the panic is specific to that session-build-error
  condition, while a bare empty-credential environment would fail at request
  time instead. Canonical claim `session-build-panic`.

### Benchmark questions

- How does serial-list wall-clock scale with page count versus tools that shard
  prefixes, given list concurrency fixed at 1 and `MaxRetries` 30?
- Does the non-back-pressured, goroutine-per-page design stay bounded on
  million-object listings, or grow goroutine and heap unboundedly?
- Does `list-versions` differ in performance from plain `list`?

### Tool risks to test

- Reproduce or falsify the `ls` concurrency-race hang or panic in a real run.
- Confirm whether output-order non-determinism matters for any downstream
  consumer that assumes sorted output.

## Navigate this directory

| If you want to… | Go to… |
| --- | --- |
| Understand the listing, concurrency, memory, and failure model | [`docs/mechanism.md`](docs/mechanism.md) |
| See how the image was built and exactly what smoke did and could not do | [`docs/running.md`](docs/running.md) |
| Inspect canonical identity, tested-subject, eligibility, and claim status data | [`data/tool.json`](data/tool.json) and [`data/claims.json`](data/claims.json) |
| Integrate the subject with the shared harness | [`adapter/`](adapter/) |
| Build the local subject image | [`build/Dockerfile`](build/Dockerfile) |
| Audit how every old ledger row became atomic current claims | [`research/claims-migration.md`](research/claims-migration.md) and the preserved reconciliation in [`research/`](research/) |
| Read the historical pre-restructure landing page | [`research/tool-page.md`](research/tool-page.md) — frozen historical research, not the current entry point |
| Inspect the immutable observations and run records | [`receipts/`](receipts/) |

## Provenance

**Mixed provenance.** This page combines firsthand source review and a blocked
smoke attempt with an inherited secondhand seed — a one-line prior-art catalog
entry compiled from public sources. The seed was **not a run record**. The
dormancy fact is a dated third-party observation, not a firsthand source read.
See [`research/tool-page.md`](research/tool-page.md) and
[`research/reconciliation.md`](research/reconciliation.md).

## Evidence boundary

Source and documentation explain mechanisms and risks; only a committed receipt
confirms run-dependent study behavior. Here the receipts confirm a capability
finding — the tool cannot list unsigned — not any listing, correctness, or
benchmark result. Rows in `results/` are the public projection of the campaign ledger, separate from the receipts here; neither is a benchmark result.
