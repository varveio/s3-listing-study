# Swath — running

What this study selected as its v0.2.0 subject, what it ran, what it could not
run, what could not be checked, and how to reproduce the work that exists.

Canonical tested identity — repository, version, pinned revision, and study
states — lives in [`../data/tool.json`](../data/tool.json). The claim `some-id`
reference notation and the evidence vocabulary are defined once in
[`mechanism.md`](mechanism.md); this page does not repeat them. Mechanism
explanation lives there too.

## Comparative image prepared 2026-08-10 (not run evidence)

[image.json](../build/image.json) selects official Swath 0.2.2 JAR plus runtime tree from digest-pinned Temurin 25 JRE. Shared assembly is defined once in [tool-structure.md](../../../docs/operating/tool-structure.md). Historical receipts below continue to describe the images and artifacts they name.

## Tested subject: upstream's published image

The subject is upstream's own published image, an OCI index at
`ghcr.io/varveio/swath@sha256:ef1aca9ab473f133acceb5730ff88d52abaaa89e773801cdb62deff51f9909b0`,
pulled anonymously with no `docker login` — claim
`published-image-is-anonymously-pullable`. Nothing was built locally for this
subject.

**Tag trap.** The registry tag is `0.2.0`, with no `v` prefix. `v0.2.0` returns
`manifest unknown`, which will catch anyone who copies the git tag — the git tag
*is* `v`-prefixed, and release version discipline is mechanical: a release fails
unless the git tag equals `v` plus the Gradle version — claim
`upstream-publishes-tagged-releases`.

The image binds itself to source: both per-arch config blobs carry
`org.opencontainers.image.revision` equal to the pinned commit `cef8ec2`, so the
source-to-image link is embedded in the artifact rather than recorded only in a
build receipt — claim `image-label-binds-to-source-commit`. The jar inside it is
the release build job's own uber-jar, promoted by a build-context override and
checksum-verified before use. The running image self-reports `swath 0.2.0
(cef8ec24a74f)` — claim `image-self-reports-v020`. The label read, the
manifest fetch and the `--version` probe were all direct container observations
with no receipt.

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

## No receipts: the runner-security blocker

**This is the owning statement for every "no receipt, no verdict" clause
elsewhere in this capsule.**

The study's mandatory runner-security profile is not provisioned on the machine
this pass ran on, and that machine categorically cannot satisfy it: it is a
shared devcontainer carrying unrelated workloads and private checkouts, not a
provisioned runner. The retired wrapper-era evidence path was therefore never used
and no receipt exists for any run of this subject. No claim about it is
`confirmed`, and none can be until the work is re-run on a provisioned runner
through the single derived-image attempt contract in
[`../../../harness/README.md`](../../../harness/README.md). A Swath derived image
has not been implemented yet.

Everything under "What ran" below is therefore a direct container observation:
`docker run` with `--cap-drop ALL`, `--security-opt no-new-privileges:true`, and
credential starvation (metadata service disabled, credential environment emptied,
AWS config and credential files pointed at a nonexistent path). That reproduces
the wrapper's credential starvation and capability drop, but not its network
confinement, timeout enforcement, payload hygiene pipeline, or receipt schema.
The captured stderr, a stdout sample and payload hashes are preserved under
[`../receipts/observations-v0.2.0/`](../receipts/observations-v0.2.0/) and are
labelled as observations, not receipts.

## What the verifier could not check

**No verifier verdict exists for any run of this subject**, for two reasons that
compound.

First, the reference manifest artifact is absent from this box, so
`harness/verify-listing.sh` could not be run at all. **No completeness check was
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

## What ran

Two instrumented listing runs on 2026-08-02, both anonymous
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
established from source; the help probe observed them live without a receipt —
claims `page-size-fixed-no-max-keys`, `no-shallow-listing-mode`,
`no-owner-split-flag-absent`, `versions-listing-is-dead-code`.

**Region is required, even anonymously.** A run with no resolvable region exits 2
before the first request, because region resolution and credential resolution are
independent code paths — claim `region-required-even-anonymously`. This is the
single most likely reason a first containerized run fails, and it is why
`--region us-east-1` is explicit in every invocation below. Upstream's own
anonymous quickstarts omit it.

## Adapter and harness contract

`../adapter/command.py` implements the shared typed command contract without
shell or NUL transport and never runs Docker or the tool. The subject image
entrypoint is `["java","-jar","/opt/swath/swath.jar"]`, and the adapter preserves that
top-level option or the `list` subcommand, not at a binary name.
`../adapter/normalize.py` converts native output into the historical verifier's
five-field normalized stream.

**Both adapter modules target v0.2.0.** Their typed contracts and argv are
covered by repository tests; the historical observations below do not by
themselves prove the newly cut-over modules execute in-image. They
emit `--concurrency`, `--format table`, `--tune seed.mode=none` and — for the
sort disk guard — `--tune sort.ignore-disk-check=on`, there being no
`--force-sort` option at all even though the guard's own error message names one
(claims `mode-inventory-v020`, `concurrency-flag-is-aimd-ceiling`,
`live-error-messages-name-absent-flags`). The normalizer's modes are
`recursive-tsv`, `recursive-jsonl`, `recursive-table`, `seed-none`,
`parquet-probe` and `sort-probe`, named to match v0.2.0's own format names.

A four-mode adapter summary from 2026-08-02 is preserved as an
[observation note](../receipts/observations-v0.2.0/adapter-modes/observation.md),
but its exact expanded commands and raw normalized outputs were not retained.
The summary is therefore not independently auditable and supports no canonical
runtime or cross-mode-agreement claim. The adapter must be exercised again in a
registered derived-image attempt before those modes receive runtime coverage.

**What the current attempt path can publish.** The text modes flow through the
captured raw streams. `command.py` directs both Parquet probes to `/tmp/swout`,
while the current minimal attempt contract publishes only `result.json` and the
two raw streams; it has no native-output collection stage. Parquet also refuses
stdout outright — claim `file-sinks-not-harness-capturable`. This is a current
driver/publication limitation, not a tool limitation or a claim that file sinks
are permanently uncapturable. Note the cost of leaving it open: Parquet is Swath's only
byte-exact output path (claim `parquet-key-column-is-byte-exact`), so excluding
it means the study never exercises that path.

The same limitation applies to `--report`, which writes to a container-local
path. The clean scrape target under this harness is the `list_run_summary` line
on stderr at `-v`, which is where every counter above came from — claim
`api-calls-counter-is-trustworthy`.

## Mode-by-mode coverage

Swath v0.2.0 offers nine executable modes — three text formats, Parquet as a
single-file sink and as a directory dataset, sorted Parquet, the `resume`
subcommand, and two reachable seed modes — plus `seed.mode=hints`, which is
declared and rejected at seed time and so is not an executable mode at all —
claim `mode-inventory-v020`.

| Mode | Status in this pass | Why |
| --- | --- | --- |
| `--format jsonl` | Observed twice directly, exit 0; no receipt, no verifier verdict | Runner-security blocker |
| `--tune seed.mode=shallow` (default) | Observed in both direct runs | Default; no receipt |
| `--format tsv`, `--format table` | Unverified | Present only in an unauditable historical adapter summary; re-run required |
| `--tune seed.mode=none` | Unverified | Present only in an unauditable historical adapter summary; the seed-cost arms remain uncompared — claim `seed-cost-direction-at-smoke` |
| `--tune seed.mode=hints` | Unexercised | Declared but unreachable: it throws at seed time, after the checkpoint database is opened and the S3 client is built — claim `seed-hints-unimplemented`. Worth one capability probe of the exit-2 failure |
| `--format parquet` probes | Not published by current attempt path | Driver writes `/tmp/swout`; minimal artifacts omit native outputs — claim `file-sinks-not-harness-capturable` |
| `--sort` | Not published by current attempt path | Parquet-only by construction; driver writes `/tmp/swout` |
| `swath resume <dir>` | Unexercised | Needs a durable checkpoint and is not a declared current driver mode — claim `only-parquet-directory-is-resumable` |
| `--fetch-owner` | Unexercised | Request-shape variant rather than a mode; one representative run recommended |

Edge-key fidelity was not exercised at all: the study registry configures no edge
bucket and the corpus listed carries no control-character keys — claim
`control-char-key-fidelity-untested`.

## Reproducing the two runs

Both runs were direct `docker run` invocations. This is what was executed, not a
reconstruction:

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

The full-bucket run is the same invocation with `s3://noaa-normals-pds/`. The
image must be pulled by digest first; the tag, if you use one, is `0.2.0`.

**A receipted re-run is a different procedure, and it is now blocked on two
things**: a runner provisioned to the study's security profile plus a Swath
derived image using the shared Python attempt engine; and the
reference manifest present, so the shared verifier can produce a verdict.
The third blocker, a v0.2.0 adapter, is closed — `tsv`, `table`, `jsonl` and
`seed.mode=none` all run through it already, so the re-run is a matter of
executing those four through the derived-image attempt path and adding a `seed.mode=hints` capability
probe.

Everything under [`../receipts/`](../receipts/) is about v0.2.0, and all of it is
observation rather than receipt.

## Deferred coverage

Each of these stays `unverified`, with its own reason:

- **Crash-resume and exactly-once under kill** — no crash, SIGKILL or resume run
  was performed, and neither observation run could have exercised it: a stdout
  run gets an in-process memory-backed checkpoint (claims `crash-resume-works`,
  `exactly-once-under-crash`).
- **Parquet and sorted-Parquet execution and fidelity** — no such run was made at
  v0.2.0, and none is capturable under a harness that mounts nothing (claims
  `parquet-modes-execute`, `parquet-output-byte-exact`).
- **Bounded memory at scale** — the observed peak RSS figures are
  JVM-baseline-dominated at this scale and probe no cliff (claim
  `bounded-memory-at-scale`).
- **Edge-key fidelity** — no edge corpus is configured (claim
  `control-char-key-fidelity-untested`).
- **The seed-mode comparison** — both instrumented runs used the default shallow
  seed, and the one `seed.mode=none` run captured output only, with no
  `list_run_summary` counters, so no cost comparison of the two arms exists at
  v0.2.0 (claim `seed-cost-direction-at-smoke`).
- **amd64 execution** — amd64 is supported across every channel, including a real
  child manifest in the published index and dual-platform CI builds, but every run
  here was native arm64 (claims `amd64-built-and-smoked-upstream`,
  `arm64-not-runtime-smoked-at-v020`).
- **Concurrency above 8, AIMD necessity, and JVM cost at high list rates** — no
  run here went above `--concurrency 8`, nothing throttled, and neither run was a
  high-rate one (claims `parallelism-ratio-at-higher-concurrency`,
  `aimd-necessity`, `java-handicap-at-high-rates`).
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
