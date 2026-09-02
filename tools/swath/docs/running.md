# Swath — running

What this study selected as its v0.3.1 subject, what it ran, what it could not
run, what could not be checked, and how to reproduce the work that exists. The
two instrumented listing runs below were made on the previous subject, v0.2.0,
and are retained as observations of that image; the v0.3.1 image has been
exercised through the adapter round-trip described under
[What ran on v0.3.1](#what-ran-on-v031-the-adapter-round-trip).

Canonical tested identity — repository, version, pinned revision, and study
states — lives in [`../data/tool.json`](../data/tool.json). The claim `some-id`
reference notation and the evidence vocabulary are defined once in
[`mechanism.md`](mechanism.md); this page does not repeat them. Mechanism
explanation lives there too.

## Toolbox image selection (not run evidence)

[`image.json`](../build/image.json) pins upstream's published v0.3.1 release
image by index digest, and both the capsule's
[`build/Dockerfile`](../build/Dockerfile) and the benchmark toolbox recipe copy
its jar and Temurin 25 JRE from that digest without recompiling Swath. Shared
assembly is defined once in
[tool-structure.md](../../../docs/operating/tool-structure.md). Earlier
diagnostic receipts below describe the images and artifacts they name at the
time (a 0.2.4 release jar, a 0.2.5-SNAPSHOT main image).

## Tested subject: upstream's published image

The subject is upstream's own published image, an OCI index at
`ghcr.io/varveio/swath@sha256:776e788200a1e70f30206897303a34e4faabd56c591e1c9562277677085c4f60`,
pulled anonymously with no `docker login` — claim
`published-image-is-anonymously-pullable`. Nothing was built locally for this
subject. The index carries `linux/amd64` and `linux/arm64` children plus two
attestation manifests, and a cosign signature tag for the index digest is
present in the registry.

**Tag trap.** The registry tag is `0.3.1`, with no `v` prefix. `v0.3.1` returns
`manifest unknown`, which will catch anyone who copies the git tag — the git tag
*is* `v`-prefixed, and release version discipline is mechanical: a release fails
unless the git tag equals `v` plus the Gradle version — claim
`upstream-publishes-tagged-releases`. Eight such tags exist, from v0.1.0
(2026-07-27) to v0.3.1 (2026-09-01); v0.3.0 and v0.3.1 were published an hour
apart, and the 0.3.1 notes say the `swath` CLI is unchanged from 0.3.0.

The image binds itself to source: both per-arch config blobs carry
`org.opencontainers.image.revision` equal to the pinned commit `7b9a5e2`, so the
source-to-image link is embedded in the artifact rather than recorded only in a
build receipt — claim `image-label-binds-to-source-commit`. The jar inside it is
the release build job's own uber-jar, promoted by a build-context override and
checksum-verified before use; the image is pushed by digest, smoked, and only
then tagged. The running image self-reports `swath 0.3.1`, `Commit:
7b9a5e2fba04`, `Runtime: 25.0.4+7-LTS` — claim `image-self-reports-v020`. The
label read, the manifest fetch and the `--version` probe were all direct
container observations on 2026-09-02 with no receipt, recorded in
[`../research/v0.3.1/report.md`](../research/v0.3.1/report.md).

At this revision the repository is public, carries an Apache-2.0 licence and a
`NOTICE` naming Varve Systems Ltd, and publishes tagged releases — claims
`repo-is-public-at-v020`, `license-is-apache-2-0-with-notice`,
`upstream-publishes-tagged-releases`.

**Build route, if you need one.** Not used here. Swath is Java on a JDK 25
toolchain, and neither settings file registers a toolchain resolver — the
mechanism Gradle needs before it can auto-provision — so on the build files
inspected a bare host needs a local JDK 25 already installed — claim
`language-is-java`. `docker build .` from the repo root is
self-contained; only CI substitutes the promoted jar.

## Diagnostic attempt receipts, but no verifier verdict

**This is the owning statement for every "no verifier verdict, no confirmed
claim" clause elsewhere in this capsule.**

The v0.2.0 derivation ran on a shared devcontainer carrying unrelated workloads
and private checkouts, not a runner provisioned to the study's mandatory
security profile. The retired wrapper-era evidence path was therefore never
used, and no wrapper-era receipt exists for those runs. The v0.3.1 adapter
round-trip of 2026-09-02 is likewise a direct `docker run` observation on the
maintainer's workstation, preserved under
[`../receipts/observations-v0.3.1/`](../receipts/observations-v0.3.1/) and
labelled as such.

Those v0.2.0 runs under "What ran" below remain direct container observations:
`docker run` with `--cap-drop ALL`, `--security-opt no-new-privileges:true`, and
credential starvation (metadata service disabled, credential environment emptied,
AWS config and credential files pointed at a nonexistent path). That reproduces
the wrapper's credential starvation and capability drop, but not its network
confinement, timeout enforcement, payload hygiene pipeline, or receipt schema.
The captured stderr, a stdout sample and payload hashes are preserved under
[`../receipts/observations-v0.2.0/`](../receipts/observations-v0.2.0/) and are
labelled as observations, not receipts.

Later derived-image diagnostic attempt receipts do exist. The latest,
[`attempt-3`](../receipts/smoke/recursive-tsv/attempt-3/), ran Swath 0.2.4 on
amd64, anonymously listing `normals-hourly/` as recursive TSV. It completed with
exit 0, passed the secret scan, and counted 2,549 rows. It carries no verifier
verdict or completeness result, so it is not a benchmark result and cannot
confirm any canonical v0.2.0 claim. Earlier attempt receipts remain historical.
No claim on this subject is `confirmed`; promotion still requires the required
committed run evidence specified by
[`Run records (receipts)`](../../../docs/methodology.md#run-records-receipts),
produced and bound through the current execution, verification, and reporting
path in [`benchmark/README.md`](../../../benchmark/README.md). A verifier `PASS`
establishes cross-attempt agreement, not independent ground truth, as documented
under [`Replay reporting is row-count-only`](../../../benchmark/README.md#replay-reporting-is-row-count-only).

## What the verifier could not check

**No verifier verdict exists for any run of this subject**, for two reasons that
compound.

First, the reference manifest artifact is absent from this box, so
the retired `harness/verify-listing.sh` could not be run at all. **No completeness check was
performed**; the only cross-check is count-and-uniqueness against the registry's
recorded figures in
[`../../../docs/smoke-bucket.md`](../../../docs/smoke-bucket.md). That does not
establish completeness at all: a substituted key, a corrupted key, or a missing
key compensated by an extra one leaves both the count and the uniqueness intact
— claim `smoke-output-count-and-uniqueness`. Cross-mode
agreement does not substitute for it. The preserved four-mode adapter summary
cannot substitute for completeness and,
because its exact commands and raw normalized outputs were not retained, it
does not support a canonical runtime claim.

Second, the registered smoke bucket has drifted since its 2026-07-17 snapshot,
and the drift is bucket-wide rather than localised. Comparing the full-bucket
listing against that snapshot, **129,227 of 148,917 objects — 87 percent — now
report a `last_modified` later than the snapshot date**, the newest
`2026-07-22T13:20:38Z`; under `normals-hourly/` it is every object. The key set
itself is unchanged: 148,917 keys, exact count match at both scopes, zero
duplicates at full-bucket scope. So the key column looks stable while the mtime
column is stale for most of the bucket. This is a fact about the third-party
bucket, not a finding about Swath; the figures are those of the study's own
drift note in
[`../../../docs/smoke-bucket.md`](../../../docs/smoke-bucket.md), and
re-baselining is the orchestrator's decision. The practical
consequence is direct and large: an mtime-asserting verification against the
2026-07-17 manifest would now report mismatches on most of the bucket, and would
report them correctly.

Elsewhere in this capsule this caveat appears as a short clause with a link back
here, never re-derived.

## What ran on v0.2.0: the two instrumented runs

Two instrumented listing runs on 2026-08-02 on the v0.2.0 image, both anonymous
(`--no-sign-request` from a credential-starved container), both
`--concurrency 8`, `--format jsonl -v`, `--region us-east-1`, against
`noaa-normals-pds`, on the index's arm64 child manifest with no emulation. Four
further single-mode runs through the rewritten adapter are recorded under
[Adapter and harness contract](#adapter-and-harness-contract); they captured
output, not counters, so no figure below comes from them.

| Scope | Keys emitted | Exit | Wall | API calls (per 1k) | Pages fetched | Peak in flight | Splits / steals | Peak RSS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `normals-hourly/` | 2,549 | 0 | 4.1 s listing, 8.4 s session | 75 (29.42) | 49 | 8 at 246 ms | 0 / 3 | 415.1 MB |
| full bucket | 148,917 | 0 | 10.4 s listing, 16.9 s session; 18 s measured outside the tool | 240 (1.61) | 165 | 8 at 1,922 ms | 19 / 25 | 508.8 MB |

Every figure in that table except the externally measured 18 seconds and the row
counts is Swath's own self-reported counter from its `list_run_summary` and
`list_run_diagnostics` stderr lines — the tool's account of its own behaviour,
not an independent wire capture. **These are two single unreplicated runs. They
are not benchmark results and they are not comparable to anything.**

What they settle, each with its own limits:

- **Anonymous listing works from a credential-starved container.**
  `--no-sign-request` sits at the top of a three-branch credential chain; both
  runs listed anonymously and exited 0 — claim `anonymous-listing-supported`.
- **The full run emitted 148,917 JSONL rows with zero duplicate keys**, matching
  the registry's recorded count for the bucket — claim
  `smoke-output-count-and-uniqueness`, with the verification limits above. The
  zero-duplicate result is consistent with the disjointness design of
  [`mechanism.md`](mechanism.md#the-range-model-and-why-no-deduplication-pass-exists)
  but does not verify it.
- **The LISTs ran in parallel.** The full run reported eight concurrent listings
  in flight with nineteen splits and twenty-five steals — claim
  `full-run-reported-parallel-listings`. Both runs reached the same figure: peak
  in flight was eight, the configured ceiling, at both scopes — claim
  `peak-in-flight-reached-ceiling-at-both-scopes`. What differed was how they got
  there, not how far they got: the prefix reached eight after 246 ms from
  forty-eight seed ranges with zero splits, the full bucket after 1,922 ms from
  five seed ranges with nineteen splits. Two unreplicated runs at one
  `--concurrency` setting settle two split histories, not a relationship between
  scope and peak concurrency.
- **The AIMD controller never engaged.** Both runs recorded zero throttle events,
  zero transient events, zero AIMD votes and zero errors against this clean
  public bucket — claim `aimd-idle-at-smoke`. That settles that AIMD did not fire
  in these two runs, not whether it is dead weight.
- **Probe overhead was far higher on the small prefix** — 29.42 API calls per
  thousand objects on the 2,549-key prefix against 1.61 on the full bucket —
  claim `probe-overhead-higher-on-small-prefix`. The two runs vary both size and
  keyspace shape with no repeats, so they settle two ratios, not a law.
- **Non-worker-page calls were a minority of both runs**: 26 of the hourly run's
  75 calls and 75 of the full run's 240, computed from each run's recorded
  page-fetch count rather than a theoretical keys-divided-by-page-size floor —
  claim `non-worker-page-call-share`. The captured counters do not decompose that
  residue into seed, structure and pivot classes, and settling it needs `-vv`
  capture.
- **Timestamps came out second-precision with an explicit `Z`.** The retained
  evidence is a three-row sample from each run — six rows spanning 2025-11-24 to
  2026-07-22, none with a fractional part. The complete outputs were not kept, so
  nothing here covers either full output — claim
  `timestamp-precision-is-variable`.
- **Both runs executed natively on arm64** and, because a stdout run still opens
  an in-process SQLite database, they loaded the SQLite JDBC driver and its
  native library there — claim `runs-executed-natively-on-arm64`. That closes the
  native-extraction half of the arm64 gap; the durable on-disk checkpoint and the
  Zstd and Parquet native paths remain unexercised on arm64.

Two offline probes under `--network none` also ran: `--version`, and `list
--help`, whose usage block shows the absence of `--max-keys`, `--delimiter`,
`--recursive`, `--no-owner-split` and `--all-versions`. Those absences are
established from source and persist at v0.3.1, whose captured help is diffed
against v0.2.0's in the v0.3.1 record; the help probes observed them live
without a receipt — claims `page-size-fixed-no-max-keys`,
`no-shallow-listing-mode`, `no-owner-split-flag-absent`,
`versions-listing-is-dead-code`.

## What ran on v0.3.1: the adapter round-trip

On 2026-09-02 every mode `../adapter/command.py` declares was compiled by the
adapter, executed on the pinned v0.3.1 image (amd64 child, native,
`--cap-drop ALL`, `no-new-privileges`, metadata service disabled) against
`s3://noaa-normals-pds/normals-hourly/` anonymously, and read back through
`../adapter/normalize.py`. This is the capsule-authoring verification loop, not
a benchmark and not a receipt; its record is
[`../receipts/observations-v0.3.1/adapter-modes/`](../receipts/observations-v0.3.1/adapter-modes/).
No `concurrency` was configured, so the adapter rendered its declared default
of 64.

| Mode | Exit | Normalized rows | Distinct keys | Same key set as every other mode |
| --- | --- | --- | --- | --- |
| `recursive-tsv` | 0 | 2,549 | 2,549 | yes |
| `recursive-jsonl` | 0 | 2,549 | 2,549 | yes |
| `recursive-table` | 0 | 2,549 | 2,549 | yes, after a normalizer fix |
| `seed-none` | 0 | 2,549 | 2,549 | yes |
| `recursive-tsv-dataset` | 0 | 2,549 | 2,549 | yes |
| `recursive-tsv-zstd` | 0 | 2,549 | 2,549 | yes |
| `recursive-parquet` | 0 | 2,549 | 2,549 | yes |
| `recursive-parquet-sorted` | 0 | 2,549 | 2,549 | yes |

The count matches the registry's figure for the prefix, and the eight key-set
digests are identical. That is count-and-uniqueness plus cross-mode agreement
over one prefix in single runs: not a completeness check, not a full-bucket
run, and not comparable to anything — the caveats of
[What the verifier could not check](#what-the-verifier-could-not-check) apply
in full. Two things it does settle: every argv the adapter emits parses and
completes on 0.3.1, and every native output shape — stdout streams, compressed
and plain text dataset parts, direct and sorted Parquet datasets with their
`_SUCCESS` markers — is read by the normalizer. One defect it found was the
study's: the aligned table sink now prints millisecond fractions on every
timestamp, which the normalizer's table query did not strip, so that mode
first normalized to zero rows. The retained TSV stderr carries Swath's own
`list_run_summary` line for that run (144 API calls, peak 18 in flight under
the ceiling of 64, 13 steals, 1 split), quoted here only to say what is there;
one unreplicated prefix-scale run at one setting measures nothing.

**Region is required, even anonymously.** A run with no resolvable region exits 2
before the first request, because region resolution and credential resolution are
independent code paths — claim `region-required-even-anonymously`. This is the
single most likely reason a first containerized run fails, and it is why
`--region us-east-1` is explicit in every invocation below. Upstream's v0.2.0
quickstarts omitted it; since the 0.3.0 documentation rewrite they carry it.

## Adapter and harness contract

`../adapter/command.py` implements the shared typed command contract without
shell or NUL transport and never runs Docker or the tool. The subject image
entrypoint is `["java","-jar","/opt/swath/swath.jar"]`; the adapter spells the
same launcher with the absolute JRE path, because the attempt engine's
environment does not carry Temurin's `bin` on `PATH`. `../adapter/normalize.py`
converts native output into the five-field comparison stream.

**Both adapter modules were validated against v0.3.1 by execution.** Their
typed contracts and argv are covered by repository tests, and every declared
mode round-tripped on the pinned image on 2026-09-02 (above). Every option the
adapter emits appears in the 0.3.1 installed help — `--compression`,
`--text-writers`, `--text-part-size`, `--writeback-size` and the
`sort.merge-parallelism` tune key are 0.3.0 additions the adapter already
carried — and it emits `--tune seed.mode=none` and, for the sorted-staging
disk guard, `--tune sort.ignore-disk-check=on`, there being no `--force-sort`
option at all even though the guard's own error message still names one
(claims `mode-inventory-v020`, `live-error-messages-name-absent-flags`). The
normalizer's modes are `recursive-tsv`, `recursive-jsonl`, `recursive-table`,
`seed-none`, `recursive-tsv-dataset`, `recursive-tsv-zstd`,
`recursive-parquet` and `recursive-parquet-sorted`. Two 0.3.0 output changes
reach it: every text sink now prints `last_modified` as S3 spells it, with a
millisecond fraction, which the normalizer strips on every row (claims
`timestamp-precision-is-variable`, `last-modified-text-is-endpoint-spelling`);
and the Parquet `key` column is annotated `STRING`, so DuckDB returns it as
text and the normalizer re-encodes it as UTF-8, which is byte-exact because
Swath refuses to write a non-UTF-8 key (claim
`parquet-key-is-string-annotated-utf8-only`).

**The concurrency the capsule declares.** `command.py` declares `concurrency`
as a ceiling of `64` — Swath's own width when unsilenced,
`S3Config.DEFAULT_MAX_PARALLEL` (`S3Config.java:81`, bound by
`ConnectionOptions.java:79-81 @7b9a5e2`) — and renders it from the run's config
rather than pinning it, so the number reaches the record and a plan can sweep
it. The 0.3.1 image's own `list --help` prints `--concurrency=N  AIMD ceiling
for concurrent listing requests (default: 64)`, which settles the receipt the
v0.2.0 page owed on this number. What this study *asks* for is plan content:
every swath row in `benchmark/plans/buckets/` states `concurrency: 8`, which is
the historical frozen cap under the prose-only `CONCURRENCY_CAP=8` convention,
now visible and reviewable in the plan rather than buried in the capsule. The
capsule also renders the harness's heap share into
`JAVA_TOOL_OPTIONS=-XX:MaxRAMPercentage=<percent>`, since which variable a JVM
reads is Swath's business and the share is the harness's.

A four-mode adapter summary from 2026-08-02 is preserved as an
[observation note](../receipts/observations-v0.2.0/adapter-modes/observation.md),
but its exact expanded commands and raw normalized outputs were not retained.
The summary is therefore not independently auditable and supports no canonical
runtime or cross-mode-agreement claim. Of those modes, only `recursive-tsv` now
has later diagnostic-attempt runtime coverage; the others still need registered
benchmark attempts.

**What the current benchmark can publish.** Stream modes flow through captured
raw streams. For the Parquet and text-dataset modes, `command.py` directs the
dataset into the benchmark worker's native sink directory; the worker
recursively retains, hashes, secret-scans, and uploads that tree. The retired
wrapper's `file-sinks-not-harness-capturable` limitation therefore describes
historical smoke evidence, not the current benchmark. The 2026-09-02
round-trip exercised that sink shape outside the worker; no comparative
Parquet attempt has yet established the path end to end through the worker.

The current adapter does not declare a `--report` mode. In the historical runs,
the clean scrape target was the `list_run_summary` line on stderr at `-v`, which
is where every counter above came from — claim `api-calls-counter-is-trustworthy`.

## Mode-by-mode coverage

Swath v0.3.1 offers at least thirteen modes — three text formats to a stream
or file, partitioned TSV and JSONL directory datasets, a diagnostic discard
sink, Parquet as a single-file sink and as a directory dataset, sorted Parquet,
the `resume` subcommand, and two reachable seed modes — with a compression
axis on the text outputs, plus `seed.mode=hints`, which is declared and
rejected at seed time and so is not an executable mode at all — claim
`mode-inventory-v020`.

| Mode | Status at v0.3.1 | Why |
| --- | --- | --- |
| `--format jsonl` | Round-tripped on the prefix, exit 0; no receipt, no verifier verdict | Also observed twice on v0.2.0, once at full-bucket scope |
| `--format tsv` | Round-tripped on the prefix, exit 0; no verifier verdict | Also the 0.2.4 diagnostic attempt 3 (2,549 rows) |
| `--format table` | Round-tripped on the prefix, exit 0 after a normalizer fix | Millisecond timestamps overflowed nothing but were not stripped; fixed in `normalize.py` |
| `--tune seed.mode=shallow` (default) | Exercised by every round-trip mode and both v0.2.0 runs | Default |
| `--tune seed.mode=none` | Round-tripped on the prefix, exit 0 | Output captured, counters not compared; the seed-cost arms remain uncompared — claim `seed-cost-direction-at-smoke` |
| `--tune seed.mode=hints` | Unexercised | Declared but unreachable: it throws at seed time, after the checkpoint database is opened and the S3 client is built — claim `seed-hints-unimplemented` |
| TSV directory dataset, plain and zstd | Round-tripped on the prefix, exit 0 | 0.3.0 mode; `--checkpoint none` required — claim `text-datasets-require-checkpoint-none` |
| `--format parquet` directory | Round-tripped on the prefix, exit 0 | Native sink retained and normalized; no comparative result exists |
| `--sort` | Round-tripped on the prefix, exit 0 | Parquet-only by construction; one `part-00000.parquet` published with manifest and `_SUCCESS` |
| `--format discard` | Unexercised | 0.3.0 diagnostic sink; not a declared adapter mode — claim `discard-sink-measures-listing-engine` |
| JSONL directory dataset, gzip compression | Unexercised | Not declared adapter modes |
| `swath resume <dir>` | Unexercised | Needs a durable checkpoint and is not a declared adapter mode — claim `only-parquet-directory-is-resumable` |
| `--fetch-owner` | Unexercised | Request-shape variant rather than a mode; one representative run recommended |

"Round-tripped" means exit 0, 2,549 normalized rows and the shared key-set
digest in the 2026-09-02 observation; it is not verification.

Edge-key fidelity was not exercised at all: the study registry configures no edge
bucket and the corpus listed carries no control-character keys — claim
`control-char-key-fidelity-untested`.

## Reproducing the runs

The v0.3.1 round-trip argv for each mode is in the observation's table; the
stream modes reduce to, for example:

```sh
docker run --rm --cap-drop ALL --security-opt no-new-privileges:true \
  -e AWS_EC2_METADATA_DISABLED=true -e TZ=UTC -e HOME=/nonexistent \
  -e JAVA_TOOL_OPTIONS=-XX:MaxRAMPercentage=75 \
  --entrypoint /opt/java/openjdk/bin/java \
  ghcr.io/varveio/swath@sha256:776e788200a1e70f30206897303a34e4faabd56c591e1c9562277677085c4f60 \
  -jar /opt/swath/swath.jar -v --color never \
  list s3://noaa-normals-pds/normals-hourly/ --region us-east-1 --no-sign-request \
  --concurrency 64 --checkpoint none --format tsv
```

The image must be pulled by digest first; the tag, if you use one, is `0.3.1`.

The two v0.2.0 runs were direct `docker run` invocations. This is what was
executed, not a reconstruction:

```sh
docker run --rm --pull=never --cap-drop ALL --security-opt no-new-privileges:true \
  -e AWS_EC2_METADATA_DISABLED=true -e TZ=UTC \
  -e AWS_ACCESS_KEY_ID= -e AWS_SECRET_ACCESS_KEY= -e AWS_SESSION_TOKEN= \
  -e AWS_SECURITY_TOKEN= -e AWS_CONTAINER_CREDENTIALS_RELATIVE_URI= \
  -e AWS_CONTAINER_CREDENTIALS_FULL_URI= -e AWS_CONTAINER_AUTHORIZATION_TOKEN= \
  -e AWS_ROLE_ARN= -e AWS_SHARED_CREDENTIALS_FILE=/nonexistent-by-harness \
  -e AWS_CONFIG_FILE=/nonexistent-by-harness -e AWS_WEB_IDENTITY_TOKEN_FILE=/nonexistent-by-harness \
  -e AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE=/nonexistent-by-harness \
  ghcr.io/varveio/swath@sha256:ef1aca9ab473f133acceb5730ff88d52abaaa89e773801cdb62deff51f9909b0 \
  list s3://noaa-normals-pds/normals-hourly/ \
    --region us-east-1 --no-sign-request --format jsonl -v --concurrency 8
```

The full-bucket run is the same invocation with `s3://noaa-normals-pds/`. That
image's tag is `0.2.0`; it is no longer the tested subject.

**A claim-confirming re-run is a different procedure.** The capsule records the
Swath build and adapter declarations used by the unified benchmark toolbox.
Comparative execution and verdicts belong to `benchmark/`; a capsule research
smoke remains study evidence, not a benchmark result. The other text modes and
`seed.mode=none` still need current attempts, and `seed.mode=hints` remains a
capability probe.

[`../receipts/observations-v0.2.0/`](../receipts/observations-v0.2.0/) preserves
the v0.2.0 observations and
[`../receipts/observations-v0.3.1/`](../receipts/observations-v0.3.1/) the
v0.3.1 round-trip. [`../receipts/smoke/`](../receipts/smoke/) holds the
diagnostic attempt receipts of 2026-08-10 (0.2.2 and 0.2.4); none of these
supersede one another or confirm a claim.

## Deferred coverage

Each of these stays `unverified`, with its own reason:

- **Crash-resume and exactly-once under kill** — no crash, SIGKILL or resume run
  was performed, and neither observation run could have exercised it: a stdout
  run gets an in-process memory-backed checkpoint (claims `crash-resume-works`,
  `exactly-once-under-crash`).
- **Parquet and sorted-Parquet execution and fidelity** — both modes completed
  on the prefix in the v0.3.1 round-trip and normalized to the shared key set,
  which is an observation, not a receipt, and says nothing about fidelity
  beyond the 2,549 ASCII keys it listed; the benchmark route through the worker
  has not produced comparative evidence (claims `parquet-modes-execute`,
  `parquet-output-byte-exact`).
- **Bounded memory at scale** — the observed peak RSS figures are
  JVM-baseline-dominated at this scale and probe no cliff (claim
  `bounded-memory-at-scale`).
- **Edge-key fidelity** — no edge corpus is configured (claim
  `control-char-key-fidelity-untested`).
- **The seed-mode comparison** — both instrumented runs used the default shallow
  seed, and the one `seed.mode=none` run captured output only, with no
  `list_run_summary` counters, so no cost comparison of the two arms exists at
  v0.2.0 (claim `seed-cost-direction-at-smoke`).
- **arm64 at v0.3.1** — the v0.3.1 round-trip and the 2026-08-10 diagnostic
  attempts ran the amd64 child; the only arm64 runtime observations are the two
  v0.2.0 runs, and upstream still does not runtime-smoke arm64 (claims
  `amd64-built-and-smoked-upstream`, `arm64-not-runtime-smoked-at-v020`,
  `runs-executed-natively-on-arm64`).
- **Concurrency above 8, AIMD necessity, and JVM cost at high list rates** — the
  instrumented runs used `--concurrency 8`; the v0.3.1 round-trip ran at the
  adapter's default ceiling of 64 and self-reported peak 18 in flight on a
  2,549-key prefix, which is one unreplicated number, not a ratio; nothing
  throttled, and no run was a high-rate one (claims
  `parallelism-ratio-at-higher-concurrency`, `aimd-necessity`,
  `java-handicap-at-high-rates`).
- **Endpoint conformance and intra-page ordering** — the encoding hazards and the
  missing ordering check are source-established and need a replay server or a
  seeded edge corpus, not another listing run (claims
  `plus-to-space-conditional-hazard`, `encoding-contract-not-validated`,
  `no-intra-page-ordering-check`, `non-snapshot-pagination-misses-late-inserts`).

Comparative arms are deliberately not listed here. A question that spans several
tools — how Swath's throughput or its seeding cost compares with another
lister's, or which tools combine which features — has one owning location,
[`../../../docs/open-questions.md`](../../../docs/open-questions.md), and is not
restated on a tool's page.
