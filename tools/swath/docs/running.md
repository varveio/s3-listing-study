# Swath — running

What this study selected as its v0.2.0 subject, what it ran, what it could not
run, what could not be checked, and how to reproduce the work that exists.

Canonical tested identity — repository, version, pinned revision, and study
states — lives in [`../data/tool.json`](../data/tool.json). The claim `some-id`
reference notation and the evidence vocabulary are defined once in
[`mechanism.md`](mechanism.md); this page does not repeat them. Mechanism
explanation lives there too.

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
`no-releases-or-tags`.

The image binds itself to source: both per-arch config blobs carry
`org.opencontainers.image.revision` equal to the pinned commit `cef8ec2`, so the
source-to-image link is embedded in the artifact rather than recorded only in a
build receipt — claim `image-source-binding-agent-asserted`. The jar inside it is
the release build job's own uber-jar, promoted by a build-context override and
checksum-verified before use. The running image self-reports `swath 0.2.0
(cef8ec24a74f)` — claim `reported-version-is-snapshot`. The label read, the
manifest fetch and the `--version` probe were all direct container observations
with no receipt.

At this revision the repository is public, carries an Apache-2.0 licence and a
`NOTICE` naming Varve Systems Ltd, and publishes tagged releases — claims
`repo-is-private-prerelease`, `no-license-dangling-reference`,
`no-releases-or-tags`.

**Build route, if you need one.** Not used here. Swath is Java on a JDK 25
toolchain with no toolchain auto-provisioning configured, so a bare host needs a
local JDK 25 — claim `language-is-java`. `docker build .` from the repo root is
self-contained; only CI substitutes the promoted jar.

## No receipts: the runner-security blocker

**This is the owning statement for every "no receipt, no verdict" clause
elsewhere in this capsule.**

The study's mandatory runner-security profile is not provisioned on the machine
this pass ran on, and that machine categorically cannot satisfy it: it is a
shared devcontainer carrying unrelated workloads and private checkouts, not a
provisioned runner. `harness/smoke-run.sh` performs that preflight and owns the
receipt format, so **the wrapper was never used and no receipt exists for any
run of this subject.** No claim about it is `confirmed`, and none can be until
the work is re-run under the wrapper on a provisioned runner.

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
`harness/verify-listing.sh` could not be run at all. Completeness rests only on
count-and-uniqueness against the registry's recorded figures in
[`../../../docs/smoke-bucket.md`](../../../docs/smoke-bucket.md). That is
strictly weaker than a manifest diff: it can detect a missing or duplicated key
in aggregate, but it cannot detect a substituted key, a corrupted key, or
compensating errors — claim `smoke-output-complete-no-duplicates`. Cross-mode
agreement does not substitute for it either: four listing modes normalizing to
the same key set (claim `cross-mode-key-set-agreement`) constrains the engine
and the adapter against each other, never against ground truth.

Second, the registered smoke bucket has drifted since its 2026-07-17 snapshot.
Every object under `normals-hourly/` now reports `last_modified` 2026-07-22 — a
re-upload that moved mtimes while leaving the key set intact (exact count match
at both scopes, zero duplicates at full-bucket scope). This is a fact about the
third-party bucket, not a finding about Swath, and re-baselining is the
orchestrator's decision. The practical consequence is direct: an mtime-asserting
verification against the 2026-07-17 manifest would now fail on that scope, and
would fail correctly.

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
  `smoke-output-complete-no-duplicates`, with the verification limits above. The
  zero-duplicate result is consistent with the disjointness design of
  [`mechanism.md`](mechanism.md#the-range-model-and-why-output-is-exactly-once-by-construction)
  but does not verify it.
- **The LISTs ran in parallel.** The full run reported eight concurrent listings
  in flight with nineteen splits and twenty-five steals — claim
  `full-run-reported-parallel-listings`. Both scopes reached the configured
  ceiling of eight, the small prefix from forty-eight seed ranges with zero
  splits — claim `peak-concurrency-is-scope-dependent`.
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
- **Timestamps came out second-precision with an explicit `Z`**, spanning
  2025-11-24 to 2026-07-22 with no fractional part — claim
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

`../adapter/run.sh` implements the shared harness's argv contract: it prints a
NUL-delimited argv and never runs Docker or the tool. Because the image
entrypoint is `["java","-jar","/opt/swath/swath.jar"]`, that argv starts at the
top-level option or the `list` subcommand, not at a binary name.
`../adapter/normalize.sh` converts native output into the frozen smoke harness's
five-field normalized stream.

**Both adapter scripts are written for v0.2.0 and validated by execution.** They
emit `--concurrency`, `--format table`, `--tune seed.mode=none` and — for the
sort disk guard — `--tune sort.ignore-disk-check=on`, there being no
`--force-sort` option at all even though the guard's own error message names one
(claims `mode-inventory-v020`, `concurrency-flag-is-aimd-ceiling`,
`live-error-messages-name-absent-flags`). The normalizer's modes are
`recursive-tsv`, `recursive-jsonl`, `recursive-table`, `seed-none`,
`parquet-probe` and `sort-probe`, named to match v0.2.0's own format names.

Validation was execution, not reading. All four stdout modes were driven through
`run.sh` and `normalize.sh` against `s3://noaa-normals-pds/normals-hourly/` on
2026-08-02, each exiting 0 and each normalizing to 2,549 rows of exactly five
fields — claim `adapter-v020-modes-execute`. All four normalized to a
byte-identical key set — claim `cross-mode-key-set-agreement`. Both are recorded
in the adapter-modes
[observation](../receipts/observations-v0.2.0/adapter-modes/observation.md).
That agreement spans two text encoders, a fixed-width parser and one arm whose
request pattern differs — `seed.mode=none` issues no `delimiter=/` probes at all
— so it is evidence that the engine and the adapter are consistent with each
other. **It is not a completeness check**: nothing here was compared against a
reference manifest, so all four arms could agree and still be wrong in the same
way, and the caveat above stands unchanged. These were direct container
observations under the same credential starvation as the two runs above: still
no receipt, still no verifier verdict, and the `parquet-probe` and `sort-probe`
modes were not run.

**What the harness can capture.** Only the three text formats are capturable:
Parquet as a file sink, Parquet as a directory dataset, sorted Parquet, and
resume are structurally uncapturable because the harness bind-mounts nothing and
Parquet refuses stdout outright — claim `file-sinks-not-harness-capturable`. That
is a harness limitation, not a tool limitation, and those modes should be
recorded as not verified for that reason rather than as untested. Closing the gap
needs a bind mount plus a post-run archive step, or an out-of-harness run
normalized separately. Note the cost of leaving it open: Parquet is Swath's only
byte-exact output path (claim `parquet-key-column-is-byte-exact`), so excluding
it means the study never exercises that path.

The same limitation applies to `--report`, which writes to a container-local
path. The clean scrape target under this harness is the `list_run_summary` line
on stderr at `-v`, which is where every counter above came from — claim
`api-calls-counter-is-trustworthy`.

## Mode-by-mode coverage

Swath v0.2.0 offers ten modes — three text formats, Parquet as a single-file sink
and as a directory dataset, sorted Parquet, the `resume` subcommand, and three
seed modes — claim `mode-inventory-v020`.

| Mode | Status in this pass | Why |
| --- | --- | --- |
| `--format jsonl` | Observed twice directly and once through the adapter, exit 0; no receipt, no verifier verdict | Runner-security blocker |
| `--tune seed.mode=shallow` (default) | Observed; both direct runs and three of the four adapter modes used it | Default; no receipt |
| `--format tsv`, `--format table` | Each exercised once through the rewritten adapter, exit 0; no receipt, no verifier verdict | 2,549 normalized rows each on `normals-hourly/` — [adapter-modes observation](../receipts/observations-v0.2.0/adapter-modes/observation.md), claim `adapter-v020-modes-execute` |
| `--tune seed.mode=none` | Exercised once through the rewritten adapter, exit 0; no receipt, no verifier verdict | A genuine request-pattern change — no `delimiter=/` probes at all — and it agreed key-for-key with the three seeded arms on that one prefix ([observation](../receipts/observations-v0.2.0/adapter-modes/observation.md)). No counters were captured, so the seed-cost arms are still uncompared — claim `seed-cost-direction-at-smoke` |
| `--tune seed.mode=hints` | Unexercised | Declared but unreachable: it throws at seed time, after the checkpoint database is opened and the S3 client is built — claim `seed-hints-unimplemented`. Worth one capability probe of the exit-2 failure |
| `--format parquet` (file and directory) | Not capturable | Directory or file sink; harness mounts nothing — claim `file-sinks-not-harness-capturable` |
| `--sort` | Not capturable | Parquet-only by construction |
| `swath resume <dir>` | Not capturable | Needs a durable checkpoint, hence a directory dataset, hence a mount — claim `only-parquet-directory-is-resumable` |
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
  ghcr.io/varveio/swath@sha256:ef1aca9ab473f133acceb5730ff88d52abaaa89e773801cdb62deff51f9909b0 \
  list s3://noaa-normals-pds/normals-hourly/ \
    --region us-east-1 --no-sign-request --format jsonl -v --concurrency 8
```

The full-bucket run is the same invocation with `s3://noaa-normals-pds/`. The
image must be pulled by digest first; the tag, if you use one, is `0.2.0`.

**A receipted re-run is a different procedure, and it is now blocked on two
things**: a runner provisioned to the study's security profile, so
`harness/smoke-run.sh` can own execution, timeouts and measurement; and the
reference manifest present, so `harness/verify-listing.sh` can produce a verdict.
The third blocker, a v0.2.0 adapter, is closed — `tsv`, `table`, `jsonl` and
`seed.mode=none` all run through it already, so the re-run is a matter of
executing those four under the wrapper and adding a `seed.mode=hints` capability
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
  here was native arm64 (claims `amd64-support-inferred`,
  `arm64-never-runtime-smoked-upstream`).
- **Concurrency above 8, AIMD necessity, and every comparative arm** — claims
  `parallelism-ratio-at-higher-concurrency`, `aimd-necessity`,
  `no-tool-combines-all-features`, `throughput-within-10pct-of-s3-fast-list`,
  `s3-fast-list-published-throughput`, `may-lose-to-s3-fast-list-hinted`,
  `java-handicap-at-high-rates`, `seed-cost-comparison`.
- **Endpoint conformance and intra-page ordering** — the encoding hazards and the
  missing ordering check are source-established and need a replay server or a
  seeded edge corpus, not another listing run (claims
  `plus-to-space-conditional-hazard`, `encoding-contract-not-validated`,
  `no-intra-page-ordering-check`, `non-snapshot-pagination-misses-late-inserts`).
