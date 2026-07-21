# Swath — running

How the image was built, every mode that was smoked with its receipt, the
parquet capability probes and the wrapper boundary that limits them, the
concurrency cap, and the supported-architecture matrix. Canonical tested
identity (pinned SHA, version, study states) lives in
[`../data/tool.json`](../data/tool.json); this page supplies the operational
detail. Evidence labels and the claim `some-id` reference notation are as
defined in [`mechanism.md`](mechanism.md); pinned commit `f1009db`.

## Image — built by the agent, not published

**No image is published upstream.** Swath ships a self-contained multi-stage
`Dockerfile` (`docker build .` needs no prior host Gradle run) `[SRC
Dockerfile:1-49]`. The image used for every receipt on this page was built by
the research agent from **upstream's own `Dockerfile` at the pinned SHA**:

```
docker build -t swath:groundwork .        # in <sources>/swath @ f1009db
# manifest digest: sha256:1dc6d1e60d4f9aabffcde8b789e49688938cbabcf93b3e35a1c53fc73ea8f9d1
# (Docker 29 containerd image store: image ID == manifest digest)
```

The digest-pinned ref the smoke wrapper requires was produced by pushing to a
**throwaway local registry**: `localhost:5000/swath@sha256:1dc6d1e6…`
(digest-pinned). Entrypoint is `["java","-jar","/opt/swath/swath.jar"]` (exec
form, java as PID 1) `[SRC Dockerfile:80]`, so `../adapter/run.sh` argv starts at the
top-level option / `list` subcommand. Reported `--version`: `swath
0.1.0-SNAPSHOT`; host/build arch arm64, native.

**Image↔source binding is an agent-asserted build fact** (claim
`image-source-binding-agent-asserted`). Stated exactly so per
`../receipts/smoke/_build/build.md`: the source→image link is recorded in that build
receipt, **not** cryptographically embedded in each run receipt. The image
carries no OCI `revision`/source-SHA label, so a `run.meta` read in isolation
shows only "this digest, `--version 0.1.0-SNAPSHOT`" — **not** "built from
`f1009db`". A future build should stamp the source SHA into an image label so the
binding is receipt-checkable. To rebuild: check out the pinned SHA
`f1009db599861a7e905a539778d915f1bb5426eb`, `docker build -t swath:groundwork .`,
then push the resulting digest to a local registry
(`localhost:5000`, the `swath-registry` throwaway) for the wrapper's digest-pin.

## Concurrency cap

Every run was pinned to `--max-parallel-listings 8`. Swath's own default is
**64** `[SRC ListCommand.java:114-115]`; 8 is this subject's smoke cap, applied
so the smoke pass exercises the parallel path without opening a wide concurrency
sweep (that sweep is the primary benchmark-phase knob — see
[`../README.md`](../README.md) § Limitations and open questions). The cap is why
`peak_in_flight` tops out at 8 where scope allows; it also means the parallelism
ratio at Swath's real default is **unmeasured** here (claim
`parallelism-ratio-at-higher-concurrency`).

## Every smoked mode

All runs **anonymous** (`--no-sign-request`, `auth=anonymous` enforced by the
credential-starved wrapper) against `noaa-normals-pds` (us-east-1) at its
2026-07-17 snapshot (148,917 keys, manifest sha256 `c78a…2adb`),
`--max-parallel-listings 8`, `--checkpoint none` for the text modes. Pre-flight
PASS: the pinned harness client's re-list is byte-identical to the manifest
(`sha256 8b5b584e…` both sides — no drift; `../receipts/smoke/_preflight`).
`CREDS=none` (no credentialed pass); `EDGE_BUCKET=none` (unicode/weird-key/
multipart-ETag fidelity deferred — see `mechanism.md`).

| Mode | Scope | Keys | Exit | Wall | api_calls (per 1k) | peak_in_flight | splits / steals | Verdict | Receipt |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| recursive-tsv | full | 148,917 | 0 | 7.17s | 339 (2.28) | 8 | 7 / 98 | **PASS** | `recursive-tsv/full` |
| recursive-tsv | normals-hourly/ | 2,549 | 0 | 3.99s | 112 (43.9) | 8 | **0** / 6 | **PASS** | `recursive-tsv/hourly` |
| recursive-tsv | normals-monthly/1991-2020/ | 15,625 | 0 | 3.83s | 123 (7.87) | 8 | 7 / 59 | **PASS** | `recursive-tsv/monthly1991` |
| recursive-tsv | normals-annualseasonal/1981-2010/access/ | 9,839 | 0 | 3.44s | 82 (8.33) | 8 | 4 / 37 | **PASS** | `recursive-tsv/asaccess1981` |
| recursive-jsonl | full | 148,917 | 0 | 7.54s | 319 (2.14) | 8 | 8 / 102 | **PASS** | `recursive-jsonl/full` |
| recursive-jsonl | normals-monthly/1991-2020/ | 15,625 | 0 | 4.34s | 124 (7.94) | 8 | 7 / 62 | **PASS** | `recursive-jsonl/monthly1991` |
| recursive-aligned | full | 148,917 | 0 | 7.58s | 334 (2.24) | 8 | 9 / 100 | **PASS** | `recursive-aligned/full` |
| recursive-aligned | normals-monthly/1991-2020/ | 15,625 | 0 | 3.87s | 119 (7.62) | 8 | 7 / 56 | **PASS** | `recursive-aligned/monthly1991` |
| seed-none | full | 148,917 | 0 | 9.16s | **516** (3.47) | **6** | 22 / 284 | **PASS** | `seed-none/full` |
| seed-none | normals-monthly/1991-2020/ | 15,625 | 0 | 3.65s | 116 (7.42) | **4** | 7 / 80 | **PASS** | `seed-none/monthly1991` |

Every verifier verdict: `dups=0 missing=0 extra=0`, fields match where the mode
exposes them (`verify.md` in each receipt; claim
`smoke-output-complete-no-duplicates`). Three facts to read off the table,
none generalized past smoke:

- **Behaviour is scope-dependent (not "small = less parallel").** `peak_in_flight`
  reached the `8` cap on the full, 15k, 9.8k, and even the 2,549-key `hourly`
  recursive runs — `hourly` did it through steals alone (`splits=0`). Only the
  un-seeded `seed-none` runs peaked lower (6 full / 4 monthly). So splitting is
  scope-dependent and the un-seeded path reaches lower peak concurrency; scope
  size alone does not predict parallelism (claim
  `peak-concurrency-is-scope-dependent`). (These are Swath's own self-reported
  counters, not an independent wire capture.)
- **The un-seeded run recorded MORE, not fewer, API calls:** 516 (`--seed none`)
  vs 339 (`--seed shallow`) on the full bucket — the run *with* the up-front seed
  made fewer calls. Both PASS; one run per arm settles the observed counts, not a
  causal effect of the seed choice (claim `seed-cost-direction-at-smoke`). These
  api_calls are Swath's own self-reported counters, not an independent wire capture.
- **Probe overhead was far higher on the small prefix:** 43.9 api_calls/1k on the
  2,549-key `hourly` prefix vs 2.28/1k full-bucket. These two runs differ in both
  size and keyspace shape with no repeats, so they settle the two ratios, not a
  general overhead-vs-scale law (claim `probe-overhead-higher-on-small-prefix`, an
  open benchmark question). These api_calls-per-1k ratios derive from Swath's own
  self-reported counters, not an independent wire capture.

All runs recorded `throttle_events=0`, `aimd_votes=0`, `errors=0` (clean public
bucket; AIMD never engaged; claim `aimd-idle-at-smoke`) — all Swath's own
self-reported counters, with `aimd_votes` in each run's `list_run_diagnostics`
stderr line, not an independent wire capture. Peak RSS held ~320–560 MB across
every scope, JVM baseline-dominated — not a scale claim.

## Parquet capability probes and the wrapper boundary

| Probe | Scope | Keys | Exit | Wall | api_calls | Result | Receipt |
| --- | --- | --- | --- | --- | --- | --- | --- |
| parquet | normals-monthly/1991-2020/ | 15,625 | 0 | 4.02s | 120 | run OK, `output_files=3`, output uncapturable | `_capability/parquet-probe` |
| sorted-parquet | normals-monthly/1991-2020/ | 15,625 | 0 | 4.82s | 135 | run OK, `output_files=1` after k-way merge, output uncapturable | `_capability/sort-probe` |

**Structural harness boundary — fidelity is NOT settled.** These modes write a
dataset **directory** (`-o`), but the smoke wrapper captures only container
stdout/stderr (`docker logs`) and mounts no output volume, so the dataset is
**destroyed with the container**. The probes show the paths *execute* to exit 0
with a **self-reported** `objects=15625` in each probe's `list_run_summary`
stderr line (claim `parquet-modes-execute`), not an independent wire capture —
that does **not** establish the dataset actually contains all 15,625 keys (no
verifier ran on the output). Parquet byte-exactness
stays `unverified` (claim `parquet-output-byte-exact`); verifying it needs a
volume-mounting harness path, deferred to the benchmark phase.

**Footguns for the parquet modes** (per the project's docs): Parquet/sorted
output requires `-o <directory>` — it never goes to stdout; `--sort` is
Parquet-only and refuses `--checkpoint none` `[SRC ListCommand.java:355-359]`;
`--sort` needs ~`2× objects × bytes/object` staging disk and a raised `ulimit -n`
`[DOC usage.md:200-232]`.

## Auth and quickstart

Anonymous is a first-class flag: `--no-sign-request` selects
`AnonymousCredentialsProvider` `[SRC ListCommand.java:87-88,2034]`; otherwise the
SDK default chain, or `--profile`. `--region` overrides the SDK chain; a
wrong-region bucket surfaces a typed `RegionRedirectException` naming the correct
region `[SRC S3PageFetcher.java:333-347]`. As smoked:

```
swath list s3://<bucket>[/<prefix>] --region <r> --no-sign-request \
  --format tsv --checkpoint none --max-parallel-listings 8
```

## Reproduction via `harness/smoke-run.sh`

Every receipt was produced by the shared wrapper, never a bare `docker run`.
`../adapter/run.sh` only *prints* the argv the wrapper appends to the pinned image's
entrypoint; the wrapper owns `docker run`, mounts, credential injection/starving,
the timeout, and measurement. To reproduce a row:

```sh
harness/smoke-run.sh \
  --tool swath --mode recursive-tsv \
  --image localhost:5000/swath@sha256:1dc6d1e60d4f9aabffcde8b789e49688938cbabcf93b3e35a1c53fc73ea8f9d1 \
  --run-script tools/swath/adapter/run.sh \
  --bucket noaa-normals-pds --region us-east-1 \
  --auth anonymous \
  --out tools/swath/receipts/smoke/recursive-tsv/full
```

Swap `--mode` for any row in `../adapter/run.sh`'s case statement and add `--prefix <p>`
for a scoped listing. Rebuilding the image first requires the pinned checkout at
`f1009db` and the `docker build` above. `../adapter/run.sh`/`../adapter/normalize.sh` and
everything under `../research/` and `../receipts/` are **immutable** inputs — they
were not modified for this consolidation; a rerun adds a new receipt rather than
editing one.

## Architecture matrix

| Channel | amd64 | arm64 | Note |
| --- | --- | --- | --- |
| Upstream published image | — | — | none published |
| Prebuilt binaries | — | — | none (no releases/tags) |
| Source/Docker build | **[INFERRED]** | **built & smoked (native)** | uber-jar is arch-neutral bytecode; native deps (sqlite-jdbc, zstd-jni) bundle libs for every arch; runtime base `eclipse-temurin:25-jre-noble` is multi-arch `[SRC Dockerfile:9-16,73]` |

Smoke ran **natively on arm64** (host `aarch64`, 8 cores, 31 GB, gcp:us-east1-b),
**no emulation** (`arch=arm64 emulated=no` in every receipt). **amd64 was neither
built nor run here:** amd64 support is `[INFERRED]` from the Dockerfile
(arch-neutral bytecode, all-arch native deps, multi-arch base) `[SRC
Dockerfile:9-16,73]`, **not demonstrated** (claim `amd64-support-inferred`). The
benchmark should confirm an actual amd64 build+run before settling on it as the
common-denominator arch, and must not silently compare Swath's arm64 smoke
numbers against amd64 runs of other tools (see
[`../README.md`](../README.md) § Limitations and open questions). Smoke produces
no comparative numbers, so the arch choice here is immaterial to anything on this
page.

## Deferred coverage

The following facets were not exercised and stay `unverified`; each bullet gives
its own reason:

- **Crash-resume and exactly-once under kill** — smoke used `--checkpoint none`;
  no SIGKILL, mid-checkpoint, or resume run (claims `crash-resume-works`,
  `exactly-once-under-crash`).
- **Parquet / sorted-parquet fidelity** — needs a volume-mounting harness path to
  capture and verify the dataset (claim `parquet-output-byte-exact`).
- **Bounded memory at scale**, including `--sort` staging-disk and sort-memory
  behavior (claim `bounded-memory-at-scale`).
- **Edge-key fidelity** — `EDGE_BUCKET=none`; byte-exactness is proven only for
  control-character-free keys (claims `text-sink-key-fidelity-ascii-only`,
  `control-char-key-fidelity-untested`).
- **amd64 build and run** — only arm64 has build+run evidence (claim
  `amd64-support-inferred`).
- **Comparative and high-concurrency arms** — the `--max-parallel-listings` sweep,
  AIMD necessity, and every cross-tool comparison (claims
  `parallelism-ratio-at-higher-concurrency`, `aimd-necessity`,
  `no-tool-combines-all-features`, `throughput-within-10pct-of-s3-fast-list`,
  `may-lose-to-s3-fast-list-hinted`, `java-handicap-at-high-rates`,
  `seed-cost-comparison`).
