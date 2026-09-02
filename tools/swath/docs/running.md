# Swath — running

What this study selected as its v0.3.1 subject, what it ran, what it could not
run, what could not be checked, and how to reproduce the work that exists.

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
[tool-structure.md](../../../docs/operating/tool-structure.md).

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
[`../research/report.md`](../research/report.md).

At this revision the repository is public, carries an Apache-2.0 licence and a
`NOTICE` naming Varve Systems Ltd, and publishes tagged releases — claims
`repo-is-public-at-v020`, `license-is-apache-2-0-with-notice`,
`upstream-publishes-tagged-releases`.

**Build route, if you need one.** Not used here. Swath is Java on a JDK 25
toolchain, and neither settings file registers a toolchain resolver — the
mechanism Gradle needs before it can auto-provision — so on the build files
inspected a bare host needs a local JDK 25 already installed — claim
`language-is-java`. `docker build .` from the repo root is self-contained; only
CI substitutes the promoted jar.

## No receipts, and no verifier verdict

**This is the owning statement for every "no verifier verdict, no confirmed
claim" clause elsewhere in this capsule.**

No run of this subject has produced a run record in the harness sense. The one
runtime exercise, the adapter round-trip below, was a direct `docker run` on the
maintainer's workstation: `--cap-drop ALL`, `no-new-privileges`, the metadata
service disabled and no credentials, but none of the benchmark worker's
attempt record, network confinement, secret-scan pipeline or receipt schema. It
is preserved under
[`../receipts/observations-v0.3.1/`](../receipts/observations-v0.3.1/) and
labelled as an observation. No claim on this subject is `confirmed`; promotion
requires the committed run evidence specified by
[`Run records (receipts)`](../../../docs/methodology.md#run-records-receipts),
produced and bound through the current execution, verification, and reporting
path in [`benchmark/README.md`](../../../benchmark/README.md). A verifier `PASS`
establishes cross-attempt agreement, not independent ground truth, as documented
under [`Replay reporting is row-count-only`](../../../benchmark/README.md#replay-reporting-is-row-count-only).

## What the verifier could not check

No verifier ran, so **no completeness check was performed**. The only
cross-checks are count against the registry's recorded figure for the prefix in
[`../../../docs/smoke-bucket.md`](../../../docs/smoke-bucket.md) and agreement
between the eight modes' key sets. Neither establishes completeness: a
substituted key, a corrupted key, or a missing key compensated by an extra one
leaves both the count and the agreement intact, and every mode can agree and be
wrong the same way — claim `round-trip-count-and-cross-mode-agreement`. The
registry also records that the smoke bucket's `last_modified` values have
drifted since its 2026-07-17 snapshot while its key set has not, so an
mtime-asserting verification against that snapshot would report mismatches, and
report them correctly; re-baselining is the orchestrator's decision.

Elsewhere in this capsule this caveat appears as a short clause with a link back
here, never re-derived.

## What ran: the adapter round-trip

On 2026-09-02 every mode `../adapter/command.py` declares was compiled by the
adapter, executed on the pinned image (amd64 child, native) against
`s3://noaa-normals-pds/normals-hourly/` anonymously, and read back through
`../adapter/normalize.py`. This is the capsule-authoring verification loop —
"the adapter round-trips real output" — not a benchmark and not a receipt; its
record is
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

What it settles, each with its own limits:

- **Every argv the adapter emits parses and completes on 0.3.1**, and every
  native output shape — stdout streams, plain and zstd text dataset parts,
  direct and sorted Parquet datasets with their `_SUCCESS` markers — is read by
  the normalizer to the same 2,549-key set, matching the registry's figure for
  the prefix — claim `round-trip-count-and-cross-mode-agreement`, with the
  limits above.
- **Anonymous listing works from a credential-starved container.**
  `--no-sign-request` sits at the top of the credential chain; every mode
  listed anonymously and exited 0 — claim `anonymous-listing-supported`.
- **Timestamps came out as S3 spells them**, `YYYY-MM-DDTHH:MM:SS.000Z` on
  every retained sample row, which is what the 0.3.0 pass-through predicts —
  claims `last-modified-text-is-endpoint-spelling`,
  `timestamp-precision-is-variable`. That is also the one defect it found, and
  it was the study's: the aligned table sink's fraction was not stripped by the
  normalizer's table query, so that mode first normalized to zero rows.
- **The retained TSV stderr carries Swath's own `list_run_summary` line**: 144
  API calls, 20 pages, peak 18 in flight under the ceiling of 64, average 6.98,
  13 steals, 1 split, zero errors, 287 MB peak RSS, a 1.5 second listing phase
  in a 2.7 second session. Quoted to say what is there: one unreplicated
  prefix-scale run at one setting measures nothing, and the AIMD controller
  never engaged — claims `parallelism-ratio-at-higher-concurrency`,
  `aimd-necessity`, `java-handicap-at-high-rates`, all `unverified`.

Offline, `--version`, `--help`, `list --help`, `resume --help` and
`list --tune help` were captured from both the 0.3.1 and 0.2.0 images and
diffed in [`../research/report.md`](../research/report.md). The 0.3.1 usage
block shows the absence of `--max-keys`, `--delimiter`, `--recursive`,
`--no-owner-split` and `--all-versions`; those absences are established from
source and the help probe observed them live — claims
`page-size-fixed-no-max-keys`, `no-shallow-listing-mode`,
`no-owner-split-flag-absent`, `versions-listing-is-dead-code`.

**Region is required, even anonymously.** A run with no resolvable region exits 2
before the first request, because region resolution and credential resolution are
independent code paths — claim `region-required-even-anonymously`. This is the
single most likely reason a first containerized run fails, and it is why
`--region us-east-1` is explicit in every invocation here; upstream's 0.3.0
quickstarts carry it too.

## Adapter and harness contract

`../adapter/command.py` implements the shared typed command contract without
shell or NUL transport and never runs Docker or the tool. The subject image
entrypoint is `["java","-jar","/opt/swath/swath.jar"]`; the adapter spells the
same launcher with the absolute JRE path, because the attempt engine's
environment does not carry Temurin's `bin` on `PATH`. `../adapter/normalize.py`
converts native output into the five-field comparison stream.

**Both adapter modules were validated against v0.3.1 by execution.** Their
typed contracts and argv are covered by repository tests, and every declared
mode round-tripped on the pinned image (above). Every option the adapter emits
appears in the 0.3.1 installed help, including the 0.3.0 additions
`--compression`, `--text-writers`, `--text-part-size`, `--writeback-size` and
the `sort.merge-parallelism` tune key; it emits `--tune seed.mode=none` and,
for the sorted-staging disk guard, `--tune sort.ignore-disk-check=on`, there
being no `--force-sort` option at all even though the guard's own error message
names one (claims `mode-inventory-v020`,
`live-error-messages-name-absent-flags`). The normalizer's modes are
`recursive-tsv`, `recursive-jsonl`, `recursive-table`, `seed-none`,
`recursive-tsv-dataset`, `recursive-tsv-zstd`, `recursive-parquet` and
`recursive-parquet-sorted`. Two 0.3.0 output changes reach it: every text sink
prints `last_modified` as S3 spells it, with a millisecond fraction, which the
normalizer strips on every row; and the Parquet `key` column is annotated
`STRING`, so DuckDB returns it as text and the normalizer re-encodes it as
UTF-8, which is byte-exact because Swath refuses to write a non-UTF-8 key
(claim `parquet-key-is-string-annotated-utf8-only`).

**The concurrency the capsule declares.** `command.py` declares `concurrency`
as a ceiling of `64` — Swath's own width when unsilenced,
`S3Config.DEFAULT_MAX_PARALLEL` (`S3Config.java:81`, bound by
`ConnectionOptions.java:79-81 @7b9a5e2`) — and renders it from the run's config
rather than pinning it, so the number reaches the record and a plan can sweep
it; the image's own `list --help` prints `--concurrency=N  AIMD ceiling for
concurrent listing requests (default: 64)`. What this study *asks* for is plan
content: every swath row in `benchmark/plans/examples/` states `concurrency: 8`,
visible and reviewable in the plan rather than buried in the capsule. The
capsule also renders the harness's heap share into
`JAVA_TOOL_OPTIONS=-XX:MaxRAMPercentage=<percent>`, since which variable a JVM
reads is Swath's business and the share is the harness's.

**What the benchmark can publish.** Stream modes flow through captured raw
streams. For the Parquet and text-dataset modes, `command.py` directs the
dataset into the benchmark worker's native sink directory; the worker
recursively retains, hashes, secret-scans, and uploads that tree. The
round-trip exercised that sink shape outside the worker; no comparative attempt
has yet established the path end to end through the worker — claims
`file-sinks-not-harness-capturable`, `parquet-modes-execute`.

The adapter does not declare a `--report` mode. The clean scrape target is the
`list_run_summary` line on stderr at `-v`, which is where the counters above
came from — claim `api-calls-counter-is-trustworthy`.

## Mode-by-mode coverage

Swath v0.3.1 offers at least thirteen modes — three text formats to a stream
or file, partitioned TSV and JSONL directory datasets, a diagnostic discard
sink, Parquet as a single-file sink and as a directory dataset, sorted Parquet,
the `resume` subcommand, and two reachable seed modes — with a compression
axis on the text outputs, plus `seed.mode=hints`, which is declared and
rejected at seed time and so is not an executable mode at all — claim
`mode-inventory-v020`.

| Mode | Status | Why |
| --- | --- | --- |
| `--format jsonl \| tsv \| table` | Round-tripped on the prefix, exit 0; no verifier verdict | `table` needed the normalizer fix above |
| `--tune seed.mode=shallow` (default) | Exercised by every round-trip mode | Default |
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
digest in the 2026-09-02 observation; it is not verification. Edge-key
fidelity was not exercised at all: the study registry configures no edge
bucket and the corpus listed carries no control-character keys — claim
`control-char-key-fidelity-untested`.

## Reproducing the round-trip

The exact argv for each mode is in the observation's table; the stream modes
reduce to, for example:

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
Dataset modes add `-v <dir>:/sink` and the `-o /sink/listing` argv the adapter
renders. A claim-confirming run is a different procedure: comparative execution
and verdicts belong to `benchmark/`, and a capsule research smoke remains study
evidence, not a benchmark result.

## Deferred coverage

Each of these stays `unverified`, with its own reason:

- **Crash-resume and exactly-once under kill** — no crash, SIGKILL or resume run
  was performed, and no round-trip mode could have exercised it: the stream
  modes run `--checkpoint none`, and the sorted run completed cleanly (claims
  `crash-resume-works`, `exactly-once-under-crash`).
- **Parquet and sorted-Parquet fidelity** — both modes completed on the prefix
  and normalized to the shared key set, which says nothing about fidelity
  beyond 2,549 ASCII keys; no verifier ran and no edge corpus exists (claims
  `parquet-modes-execute`, `parquet-output-byte-exact`).
- **Bounded memory at scale** — the one peak-RSS figure is JVM-baseline-dominated
  at prefix scale and probes no cliff (claim `bounded-memory-at-scale`).
- **Edge-key fidelity** — no edge corpus is configured (claim
  `control-char-key-fidelity-untested`).
- **The seed-mode comparison** — the `seed.mode=none` run captured output only;
  its counters were not compared with the default arm's (claim
  `seed-cost-direction-at-smoke`).
- **arm64** — the round-trip ran the amd64 child; this study holds no arm64
  runtime observation of v0.3.1, and upstream still does not runtime-smoke
  arm64 because its runners are amd64 (claims `amd64-built-and-smoked-upstream`,
  `arm64-not-runtime-smoked-at-v020`).
- **Concurrency sweeps, AIMD necessity, and JVM cost at high list rates** — one
  prefix-scale run at ceiling 64 that reported peak 18 in flight is not a ratio;
  nothing throttled, and no run was a high-rate one (claims
  `parallelism-ratio-at-higher-concurrency`, `aimd-necessity`,
  `java-handicap-at-high-rates`).
- **Endpoint conformance and intra-page ordering** — the encoding hazards and the
  missing ordering check are source-established and need a replay server or a
  seeded edge corpus, not another listing run (claims
  `plus-to-space-conditional-hazard`, `encoding-contract-not-validated`,
  `no-intra-page-ordering-check`, `non-snapshot-pagination-misses-late-inserts`).

Comparative arms are deliberately not listed here. A question that spans several
tools has one owning location,
[`../../../docs/open-questions.md`](../../../docs/open-questions.md), and is not
restated on a tool's page.
