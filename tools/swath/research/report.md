# swath — independent groundwork report

Independent, primary-source report for the s3-listing-study. Derived without
reading the inherited dossier (Stages A–C, workspace phase). Every behavioural
claim carries an evidence label: `[DOC url]`, `[SRC file:line @ sha]`,
`[RUN receipt]`, `[3P url]`, `[INFERRED]`, `[OBS how]`.

Pinned short SHA for all `[SRC]` anchors: **`f1009db`**
(`f1009db599861a7e905a539778d915f1bb5426eb`).

---

## 1. Metadata

| | |
| --- | --- |
| Repo | `github.com/varveio/swath` (canonical upstream; confirmed via GitHub API — org `varveio`, not a fork) |
| Visibility | **private / pre-release** at research time (GitHub `visibility=private`, `open_issues=66` — mixed issues+PRs) `[3P api.github.com/repos/varveio/swath, accessed 2026-07-17]` |
| Pinned ref | **default-branch HEAD** — the project cuts **no releases and no version tags** (`gh release list` / `GET /releases` empty; only `backup/*` tags exist) `[3P github api]`, so HEAD of `main` is pinned per BRIEF |
| Commit SHA | `f1009db599861a7e905a539778d915f1bb5426eb` (2026-07-16) |
| Language | **Java** (JDK **25** toolchain `[SRC build-logic/src/main/kotlin/swath.java-conventions.gradle.kts:24]`); ~593 `.java` files; Gradle multi-module build (Kotlin DSL) |
| License | **None present** — no `LICENSE`/`COPYING` file exists in the tree at `f1009db` (`ls LICENSE*` fails) `[OBS checkout listing]` and GitHub reports `license=null` `[3P github api]`. Sharper still (codex F9): `THIRD_PARTY_NOTICES.md:13` **claims** swath's modules are *"covered by the repository's own LICENSE"* `[SRC THIRD_PARTY_NOTICES.md:13]` — a **dangling reference to a LICENSE file that does not exist**. So swath ships no OSS license yet — a real finding for a repo slated to go public. |
| Upstream health | Very fresh/active: repo created 2026-06-29, last push 2026-07-16 (research day −1). README status: *"implementation in progress. Phases 0–7 built (Gate 2 passed); Phase 8 partial."* `[DOC README.md:2]` |
| Image | **Built by the agent** from upstream `Dockerfile` in the checkout at `f1009db` (no image is published); manifest digest `sha256:1dc6d1e60d4f9aabffcde8b789e49688938cbabcf93b3e35a1c53fc73ea8f9d1`, run via a throwaway local registry as `localhost:5000/swath@sha256:1dc6d1e6…` (digest-pinned). **Provenance caveat (codex F1):** the source→image link is an agent-asserted build fact (`receipts/smoke/_build/build.md`), *not* a binding embedded in each receipt — a run.meta proves only "this digest, `--version 0.1.0-SNAPSHOT`", not "built from `f1009db`". |
| Tool version | `swath 0.1.0-SNAPSHOT` `[RUN]` (the version *string* only — not proof of the source SHA; see caveat) |
| Report date | 2026-07-17 (UTC) |

swath describes itself as *"a fast, crash-resumable object-store lister that
enumerates billion-object buckets of any shape"* and, in-repo, as a *"Design +
implementation handoff pack"* — the repository carries an unusually large design
corpus (`docs/design/`, `docs/impl/`) alongside the implementation.

---

## 2. How it works

**One engine, `WorkStealingScan`.** `swath list` always drives a single
work-stealing parallel-scan engine; there are no alternate list strategies and no
router `[DOC docs/design/algorithms.md:12-20]` `[SRC ListCommand.java:62-63]`
(`STRATEGY_WORK_STEALING` is the only strategy the command emits). Confirmed at
runtime: every run logs `strategy=WORK_STEALING` `[RUN receipts/smoke/recursive-tsv/full]`.

**The range model — half-open `(A, B]`.** The keyspace is tiled into adjacent
half-open key intervals. A worker owns `(A, B]`: it issues `ListObjectsV2` with
`start-after = A` and emits every returned key `k` with `A < k <= B`; the boundary
key belongs to the **left** interval, which is the load-bearing no-gap/no-overlap
invariant `[DOC algorithms.md:53-70]`. Keys are handled as **raw bytes**
(`KeyBytes`, unsigned-byte lexicographic order) end-to-end, because S3 orders keys
in UTF-8 binary order which diverges from Java `String`/UTF-16 order for
supplementary code points `[DOC algorithms.md:25-46]`.

**Request-level behaviour**

- **One `fetchPage` = one `ListObjectsV2` call** `[SRC S3PageFetcher.java:56,157,215]`.
  AWS SDK v2 **sync** client. `encoding-type=url` is always set; the SDK's own
  interceptor url-decodes the response, and swath re-encodes the decoded string to
  raw bytes via UTF-8 (a single decode, not double) `[SRC S3PageFetcher.java:53-56,162,694-695]`.
- **Pagination is by `start-after = last emitted key`, not the continuation
  token** — "there is no `continuation_token` in the model" `[DOC algorithms.md:129-133]`
  `[SRC S3PageFetcher.java:173-175]`. The key is the single source of truth for both
  pagination and resume (survives token expiry and re-splitting; portable across
  hosts).
- **Page size:** `maxKeys=1000`, hardcoded as the S3 page cap
  `[SRC ListCommand.java:381]` (`int pageMax = 1000; // S3 page cap`). Not a CLI knob.
- **Parallelism is of the LISTs themselves**, not just of transfers/output.
  Idle workers become *thieves* and steal the upper half of the busiest peer's
  range by probing a midpoint key (`ListObjectsV2 start_after=m max_keys=1`) and
  atomically splitting the range at a page boundary `[DOC algorithms.md:146-234]`.
  Confirmed on the full-bucket `recursive-tsv` run: `peak_in_flight=8` at
  `--max-parallel-listings 8`, `splits=7`, `steals=98`
  `[RUN receipts/smoke/recursive-tsv/full]`. Parallelism is **scope-dependent, not
  uniform** (codex F6): the 2,549-key `hourly` run had `splits=0`, and `seed-none`
  peaked at `peak_in_flight=6` (full) / `4` (monthly) — smaller or un-seeded scopes
  parallelise less `[RUN hourly, seed-none/*]`.
- **Keyspace division:** demand-driven range stealing with a **byte-midpoint**
  pivot computed over Unicode code points (`ByteMidpoint`), constrained to a
  "safe set" of scalars so the synthesized boundary is valid UTF-8 **and** an
  XML-1.0-legal `start-after` that real S3 will not 400 on `[DOC algorithms.md:235-380]`.
  The seed step (`--seed shallow`, default) runs one `delimiter=/` pass to discover
  top-level prefixes and create initial parallel ranges `[DOC usage.md:322]`
  `[SRC ListCommand.java:257-260]`.
- **Retries / backoff:** the **AWS SDK's internal retry is disabled**
  (`maxAttempts=1`) `[SRC S3Config.java:66-71]` `[SRC S3ClientFactory.java:23,105-107]`
  — swath's own gauge-aware loop is the sole retrier, so the AIMD controller sees
  every real 503 immediately. Transient classes (503 SlowDown, 5xx, attempt-timeout,
  network, socket-closure) are individually classified and retried
  `[SRC S3PageFetcher.java:300-478]`; specific permanent failures get a **typed**
  fatal subtype only on an exact (status, code) pair — `(403,AccessDenied)`,
  `(404,NoSuchBucket)`, `401`, `301 PermanentRedirect`
  `[SRC S3PageFetcher.java:633-671]` — and any other 4xx falls through to the
  generic fatal `ListingException` arm (codex F10).
- **Timeouts:** per-attempt `apiCallAttemptTimeout=10s` (worker pages), a shorter
  `3s` for speculative probe calls, and an overall per-call `apiCallTimeout=60s`
  liveness ceiling `[SRC S3Config.java:48,59,65]`. In-JVM watchdogs bound a wedged
  run: `--stall-timeout` (120s no-progress) and `--no-progress-timeout` (10m
  active-but-zero-progress) `[SRC ListCommand.java:129-148]`.
- **Adaptive concurrency (AIMD):** live concurrency `T` is a resizable permit
  gauge in `[1, --max-parallel-listings]`. Slow-start ramps `min(4,Tmax)` →
  multiplicatively until the first congestion signal, then additive `+1` per clean
  10s window; decrease `T:=max(1,floor(0.7·T))` on a 503/`Retry-After`, plus a
  distinct sustained-attempt-timeout shed `[DOC algorithms.md:752-831]`. Observed
  ramp to `peak_in_flight=8` with `aimd_votes=0` on this clean public bucket `[RUN]`.

**Ordering assumptions.** Within a node's pages, rows are in S3's returned
(unsigned-byte key) order. Across the parallel writers a Parquet part is **not**
key-sorted and there is no global order unless `--sort` is used `[DOC usage.md:517-527]`.

**Memory model.** Streaming, "bounded by configuration, not bucket size" — no
accumulate-then-dump; memory is bounded by the queue sizes and the Parquet writer
pool `[DOC README.md:22-23]` `[DOC usage.md:604-617]`. Smoke peak RSS held
~320–560 MB across all runs regardless of scope size `[RUN]` (JVM baseline
dominates at this scale; not a scale claim).

**Resume story.** Crash/Ctrl-C resumable via an SQLite checkpoint whose
`listing_node` table **is** the worklist; a single checkpoint-writer thread
serialises `commitPage` and the CAS-guarded `splitTxn` (SQLite WAL is
single-writer) `[DOC algorithms.md:611-748]`. Parquet resume is **exactly-once**
(finalized parts never rewritten; re-list from `durable_cursor`); stdout/text is
**at-most-once** `[DOC usage.md:477-481]`. `--checkpoint none` runs the identical
engine against an ephemeral in-memory store (not resumable) `[SRC ListCommand.java:244-249]`.

---

## 3. Modes and tunables

**Modes** (change the request pattern or the output contract):

| Mode | Flag | Request/output contract | Smoked | Evidence |
| --- | --- | --- | --- | --- |
| recursive-tsv | `list --format tsv` | recursive `ListObjectsV2`; TSV `key/size/last_modified/etag/storage_class/row_type` | verified | `[RUN]` `[SRC TsvFormatter.java:19]` |
| recursive-jsonl | `--format jsonl` | same requests; one JSON object/line | verified | `[RUN]` `[SRC JsonlFormatter.java:33-63]` |
| recursive-aligned | `--format aligned` | same requests; fixed-width text (size/time/key **only** — no etag/storage_class) | verified | `[RUN]` `[SRC AlignedFormatter.java:36-61]` |
| parquet | `--format parquet -o DIR` | same requests; multi-part Parquet dataset dir | capability probe only (see §7/§8) | `[RUN _capability]` `[DOC usage.md:66-136]` |
| sorted-parquet | `--sort --format parquet -o DIR` | same requests; globally key-sorted Parquet via staged k-way merge | capability probe only | `[RUN _capability]` `[DOC usage.md:138-232]` |
| seed-none | `--seed none` | **no up-front `delimiter=/` seed probe**; single root range parallelised by stealing alone | verified (as tsv) | `[RUN]` `[SRC ListCommand.java:257-260]` |

There is **no shallow/delimiter *output* mode**: `swath list` always fully
enumerates objects. `delimiter=/` is used only *internally* (seeding, structure
probes). The `inspect` (shape probe, no listing) and `diff` subcommands appear in
`--help` but are **stubs** that print *"not yet implemented"* `[SRC InspectCommand.java:25]`
`[SRC DiffCommand.java:28]` — so swath exposes no CommonPrefix/`ls`-style listing and
no fan-out workaround (it parallelises in-process, so none is needed).

**Tunables** (change magnitude only — flag for the benchmark sweep):

| Flag | Default | Effect | Sweep? | Evidence |
| --- | --- | --- | --- | --- |
| `--max-parallel-listings N` | **64** | AIMD ceiling `T` on concurrent LISTs | **yes — primary** (capped to 8 for smoke) | `[SRC ListCommand.java:114-115]` |
| `--seed shallow\|none\|hints` | shallow | seed tiling (`hints` throws — unimplemented) | yes (modes above) | `[DOC usage.md:322]` |
| `--object-listing-queue-size N` | 50000 | in-flight entry budget | maybe | `[SRC ListCommand.java:179-180]` |
| `--rate-limit-api N` | unset | proactive client-side req/s cap | maybe | `[SRC ListCommand.java:182-187]` |
| `--parquet-writers N` | 3 (2–4) | parallel Parquet writers | parquet only | `[DOC usage.md:73]` |
| `--engine-toggle NAME=on\|off` | all on (readahead off) | **diagnostic ablation** namespace (12 mechanisms) | ablation study only | `[DOC usage.md:334-389]` |
| `--fetch-owner` | off | adds `FetchOwner=true` (extra fields, same request count) | no | `[SRC ListCommand.java:105]` |
| `--request-payer requester` | off | requester-pays header | n/a public | `[SRC ListCommand.java:90-93]` |

`--max-parallel-listings` is the knob the benchmark must sweep; the `--engine-toggle`
namespace is explicitly *"not a supported configuration"* — a per-mechanism A/B
measurement tool, defaults are the supported config `[DOC usage.md:336-343]`.

---

## 4. How to run it properly

**Quickstart (anonymous public bucket, as smoked):**

```
swath list s3://<bucket>[/<prefix>] --region <r> --no-sign-request \
  --format tsv --checkpoint none --max-parallel-listings 8
```

**Recommended config for a large listing (per the project's own docs):** the
defaults are the supported configuration — `--seed shallow` (recommended for all
bucket shapes) and the default `T=64`, adjusted adaptively `[DOC usage.md:322,329]`.
For maximum wall-clock on drain-heavy buckets the docs suggest layering
`--engine-toggle readahead=on` on top of the now-default `mass_aware_seed`, at a
documented cost of +19–29% API calls and up to +71% peak RSS `[DOC usage.md:376-389]`
(vendor's own measured numbers — `[DOC]` context, not independent evidence).

**Auth.** Anonymous is a first-class flag: `--no-sign-request` selects
`AnonymousCredentialsProvider` `[SRC ListCommand.java:87-88,2034]`; otherwise the
SDK default chain, or `--profile`. swath bundles the STS module for OIDC
web-identity (GKE/EKS) `[DOC usage.md:313-316]`. Region: `--region` overrides the
SDK chain; a wrong-region bucket surfaces a typed `RegionRedirectException` naming
the correct region `[SRC S3PageFetcher.java:333-347]`.

**Footguns.** (a) Parquet/sorted output requires `-o <directory>` — it never goes
to stdout. (b) `--sort` is Parquet-only and needs a checkpoint (`--checkpoint none`
is refused) `[SRC ListCommand.java:355-359]`. (c) `--sort` needs ~`2× objects ×
bytes/object` staging disk and a raised `ulimit -n` `[DOC usage.md:200-232]`. (d)
`hints` seed is unimplemented (throws) `[DOC usage.md:322]`.

---

## 5. Output and observability

**Formats.** `parquet | jsonl | tsv | aligned`; default **aligned on a TTY, jsonl
otherwise** `[SRC OutputFormat.java:11-13]`. Control-character escaping is on by
default for text sinks (`--raw-output` disables); JSONL is always validly escaped
regardless `[SRC TsvFormatter.java:53-55]` `[SRC JsonlFormatter.java:13-17]`.
Timestamps render via `DateTimeFormatter.ISO_INSTANT` (`Fields.isoMicros`); S3
`LastModified` is second-precision, so the value is `YYYY-MM-DDTHH:MM:SSZ` with no
fractional part, already the contract-canonical UTC form `[SRC Fields.java:13-21]`.
ETag quotes are stripped; multipart `hex-N` kept verbatim `[SRC S3PageFetcher.java:714-722]`.

**`normalize.sh` contract per mode** (emits `key/size/etag/mtime/storage_class`):

| Mode | Exposes | Adapter |
| --- | --- | --- |
| recursive-tsv, seed-none | all 5 | skip header, drop non-OBJECT rows, reorder `key,size,etag,mtime,storage_class` |
| recursive-jsonl | all 5 | `jq` per line, OBJECT-only |
| recursive-aligned | key,size,mtime (etag/storage_class -> `-`) | fixed-width substr parse (size[0,14), time[16,40), key[42,)) |

swath emits **full keys** (not path-relative), so the adapter ignores the scope
prefix arg. Every mode verified byte-exact against the manifest `[RUN]` — but
**scoped to this ASCII-keyed corpus** (codex F2): the text sinks escape control
bytes as `\xHH` by default (`--raw-output` off) and JSONL's `jq @tsv` escapes
embedded tabs/newlines, and none of the adapters *de-escapes*. On noaa-normals-pds
this is the identity, but the adapters are **not** proven byte-exact for keys
containing control characters — a `--raw-output` + de-escaping hardening the
benchmark phase must add before trusting weird-key fidelity (`EDGE_BUCKET=none`
here). The aligned adapter additionally assumes S3's second-precision timestamp
(20 chars, fixed column offsets); a sub-second `ISO_INSTANT` would shift columns
(codex F8) — safe for S3 today, flagged for hardening.

**Counters/logs swath exposes** (stderr, at `-v`): a `list_run_summary` line with
`objects`, `api_calls`, `api_calls_per_1k_objects`, `pages`, `peak_in_flight`,
`steals`, `splits`, `errors`, `keys_per_sec`, `peak_rss_bytes`, `peak_heap_bytes`,
`cpu_seconds`, `cpu_efficiency`; and a `list_run_diagnostics` line with
`steal_reasons{}`, `probe_fetches`, `empty_upper_bisections`, `throttle_events`,
`transient_events`, `peak_in_flight` `[RUN]` `[DOC usage.md:555-573]`. Micrometer
meters and an optional OTLP export exist; a Prometheus scrape port is v1.1-planned
`[DOC usage.md:576-592]`. This is a strong native API-call counter — the benchmark
can read request counts directly from the tool.

---

## 6. Failure surface

- **Memory growth under `--sort`:** how sort-memory behavior changes with listing
  scale is a study-owned open hypothesis. It is **VERIFIED: no** and must be
  reproduced under this harness before the study classifies its growth or failure
  behavior.
- **Interruption/OOM:** crash-only design; Parquet resume is exactly-once, text is
  at-most-once `[DOC usage.md:477-481]`. In-JVM watchdogs turn a wedged run into a
  resumable exit-75 rather than a hang `[SRC ListCommand.java:129-148]`.
- **Error handling:** transient S3 faults (503/5xx/attempt-timeout/network/
  socket-closure) are classified and retried under AIMD rather than crashing the
  run `[SRC S3PageFetcher.java:300-478]`; the specific permanent pairs above are
  typed fatal with a greppable `error_class`, others take the generic fatal arm
  (codex F10).
- **Endpoint quirks:** `%`/C1/noncharacter code points are excluded from
  *synthesized* pivots for LocalStack/MinIO and real-S3 XML compatibility
  `[DOC algorithms.md:242-269]`. **Capability limitation:** buckets whose real keys
  contain XML-illegal control bytes cannot be fully paginated via `start-after`
  (the cursor itself would 400) — documented, mitigation is future
  `ContinuationToken` work `[DOC algorithms.md:382-387]`. Not exercised here
  (noaa-normals-pds keys are clean ASCII; `EDGE_BUCKET=none`).

All memory/throughput/high-concurrency behaviour is scale-dependent and stays
hypothesis-only from smoke.

---

## 7. Container

**No image is published.** Upstream ships a self-contained multi-stage
`Dockerfile` (`docker build .` needs no prior host Gradle run) `[SRC Dockerfile:1-49]`.
Per BRIEF's middle case I built from **upstream's own Dockerfile at `f1009db`**:

```
docker build -t swath:groundwork .        # in <sources>/swath @ f1009db
# manifest digest: sha256:1dc6d1e60d4f9aabffcde8b789e49688938cbabcf93b3e35a1c53fc73ea8f9d1
# (Docker 29 containerd image store: image ID == manifest digest)
```

The digest-pinned ref required by the smoke wrapper was produced by pushing to a
throwaway local registry: `localhost:5000/swath@sha256:1dc6d1e6…`. Entrypoint is
`["java","-jar","/opt/swath/swath.jar"]` (exec form, java as PID 1) `[SRC Dockerfile:80]`
— so `run.sh` argv starts at the top-level option / `list` subcommand.

**Architecture matrix:**

| Channel | amd64 | arm64 | Note |
| --- | --- | --- | --- |
| Upstream published image | — | — | none published |
| Prebuilt binaries | — | — | none (no releases/tags) |
| Source/Docker build | native | native | uber-jar is arch-neutral bytecode; native deps (sqlite-jdbc, zstd-jni) bundle libs for every arch; runtime base `eclipse-temurin:25-jre-noble` is multi-arch `[SRC Dockerfile:9-16,73]` |

**Smoke ran natively on arm64** (host `aarch64`, 8 cores, 31 GB, gcp:us-east1-b),
**no emulation** (`arch=arm64 emulated=no` in every receipt). **amd64 was neither
built nor run here** (codex F11): amd64 support is `[INFERRED]` from the Dockerfile
(arch-neutral uber-jar bytecode; native deps bundle all-arch libs; multi-arch
`eclipse-temurin:25-jre-noble` base) `[SRC Dockerfile:9-16,73]`, not demonstrated.
The benchmark should confirm an actual amd64 build+run before settling on it as
the common-denominator arch (see Open questions).

---

## 8. Smoke results

All runs anonymous (`--no-sign-request`, `auth=anonymous` enforced by the
credential-starved wrapper), `--max-parallel-listings 8` (subject-card cap),
`--checkpoint none` for the text modes. Pre-flight PASS: the pinned harness client
re-list of the bucket is byte-identical to the manifest (`sha256 8b5b584e…` both
sides — no drift; `receipts/smoke/_preflight/preflight.md`). Manifest
`c78a8273…`, snapshot 2026-07-17, 148,917 keys.

| Mode | Scope | Keys | Exit | Wall | api_calls | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| recursive-tsv | full | 148,917 | 0 | 7.17s | 339 (2.28/1k) | **PASS** |
| recursive-tsv | normals-hourly/ | 2,549 | 0 | 3.99s | 112 (43.9/1k) | **PASS** |
| recursive-tsv | normals-monthly/1991-2020/ | 15,625 | 0 | 3.83s | 123 | **PASS** |
| recursive-tsv | normals-annualseasonal/1981-2010/access/ | 9,839 | 0 | 3.44s | 82 | **PASS** |
| recursive-jsonl | full | 148,917 | 0 | 7.54s | 319 | **PASS** |
| recursive-jsonl | normals-monthly/1991-2020/ | 15,625 | 0 | 4.34s | 124 | **PASS** |
| recursive-aligned | full | 148,917 | 0 | 7.58s | 334 | **PASS** |
| recursive-aligned | normals-monthly/1991-2020/ | 15,625 | 0 | 3.87s | 119 | **PASS** |
| seed-none | full | 148,917 | 0 | 9.16s | **516** | **PASS** |
| seed-none | normals-monthly/1991-2020/ | 15,625 | 0 | 3.65s | 116 | **PASS** |
| parquet (probe) | normals-monthly/1991-2020/ | 15,625 | 0 | 4.02s | 120 | run OK, output uncapturable |
| sorted-parquet (probe) | normals-monthly/1991-2020/ | 15,625 | 0 | 4.82s | 135 | run OK, output uncapturable |

Every verifiable mode is **PASS** (complete, no dups/missing/extra; fields match
where the mode exposes them), full-bucket and per designated prefix. Verdicts are
durable in each receipt's `verify.md`.

**Request-behaviour observations** (`[RUN]`, from the tool's own counters):
- LISTs are genuinely **parallel** — `peak_in_flight=8` at `T=8`, with
  `steals`/`splits` > 0 (7 splits, 98 steals on the full bucket). Not a serial
  paginator.
- Zero throttling on this public bucket (`throttle_events=0`, `aimd_votes=0`,
  `errors=0`) across every run.
- **Probe overhead is real and scale-sensitive:** full-bucket overhead is
  2.28 api_calls/1k objects (~172 pages + ~167 probe/seed calls), but on the tiny
  2,549-key prefix it is 43.9/1k — the parallelisation probes don't amortise on a
  small keyspace.

**File-sink modes (parquet, sorted-parquet):** structural harness boundary — the
smoke wrapper captures only container stdout/stderr (`docker logs`) and mounts no
output volume, while these modes write a dataset **directory** (`-o`) that is
destroyed with the container. So their output is **not capturable or verifiable
here**. The capability probes prove the paths *execute* to exit 0 (parquet:
`output_files=3`; sorted: `output_files=1` after a k-way merge, `merge` logged),
with the tool's **self-reported** `objects=15625` in the summary
`[RUN _capability]`. That is not proof the dataset actually contains all 15,625
keys (codex F7): the output was destroyed with the container and no verifier ran.
Verifying their fidelity needs a volume-mounting harness path — deferred to the
benchmark phase.

**Edge-case checks (unicode / weird keys / multipart ETag): deferred** —
`EDGE_BUCKET=none` (registry: fixture not seeded).

---

## 9. Notable findings

- **A LIST-only tool that refuses to be an inventory substitute.** swath is
  explicitly scoped to buckets where S3 Inventory / S3 Metadata tables aren't
  available; it *"never uses, ships, or recommends those as an answer"*
  `[DOC README.md:28-32]`. Unusual self-limiting scope.
- **`start-after` pagination instead of the continuation token** is the crux design
  choice: the last emitted key is the only cursor, which is what makes ranges
  splittable and resume portable — but it also creates the documented XML-illegal-key
  pagination limitation `[DOC algorithms.md:129-133,382-387]`.
- **Enormous diagnostic surface.** `--engine-toggle` exposes 12 named ablation
  mechanisms and `--trace` a JSONL flight recorder — this is a research instrument
  as much as a CLI. The perf history in `docs/design/algorithms.md` is candid to the
  point of self-refutation (mechanisms *"general, but overfit triggers"*; several
  measured wins later *"REFUTED"*) `[DOC algorithms.md:489-607]`.
- **`--seed none` measurably costs more API calls** than the default seed on a flat
  root: full-bucket `api_calls` 516 (none) vs 339 (shallow) — without the up-front
  `delimiter=/` tiling, the engine must discover parallelism entirely through
  speculative stealing/probing `[RUN seed-none/full vs recursive-tsv/full]`. Correct
  either way (both PASS), a balance/efficiency difference.
- **AWS SDK retries are deliberately turned off** (`maxAttempts=1`) so swath's own
  AIMD controller is the single adaptive loop — a subtle but load-bearing choice
  `[SRC S3Config.java:66-71]`.
- **Pre-release posture:** no license, no releases, `0.1.0-SNAPSHOT`, Phase 8
  partial, two subcommands still stubs — the tool builds and lists correctly, but is
  not a shipped product.

---

## 10. Open questions for the benchmark phase

1. **`--max-parallel-listings` sweep.** The primary knob (default 64). Suggested
   sweep: 1, 8, 16, 32, 64, 128 — measure api_calls/1k, wall-clock, peak RSS, and
   the AIMD `T` trajectory. Smoke was capped at 8; the parallelism ratio at higher
   `T` is unmeasured.
2. **Probe-overhead vs scale.** Does api_calls/1k converge toward ~1.0 on very large
   buckets, and how does it behave on skewed/flat shapes vs this broad-shallow one?
3. **Parquet & sorted-parquet fidelity + cost.** Need a volume-mounting harness path
   to capture and verify the dataset; measure staging-disk and sort-memory behavior
   at the study's planned scales.
4. **`--seed none` vs `shallow` vs (future) `hints`** as a request-pattern axis on
   deep-tree vs flat buckets.
5. **Common-denominator architecture:** swath supports amd64 and arm64 natively;
   confirm the campaign settles on amd64 and that swath's arm64 smoke numbers are
   not silently compared against amd64 runs of other tools.
---

## 11. Sources

**Primary — pinned checkout @ `f1009db599861a7e905a539778d915f1bb5426eb`**
(`<sources>/swath`): `README.md`, `Dockerfile`, `docs/usage.md`,
`docs/design/algorithms.md`, `swath-cli/.../ListCommand.java`,
`swath-s3/.../S3PageFetcher.java`, `S3Config.java`, `S3ClientFactory.java`,
`swath-core/.../output/{TsvFormatter,JsonlFormatter,AlignedFormatter,Fields,OutputFormat}.java`,
`InspectCommand.java`, `DiffCommand.java`, `THIRD_PARTY_NOTICES.md`,
`build-logic/src/main/kotlin/swath.java-conventions.gradle.kts`. Accessed 2026-07-17.

**Third-party:** GitHub REST API `repos/varveio/swath` (visibility, health, no
releases). Accessed 2026-07-17.

**Runs:** receipts under `tools/swath/receipts/smoke/` (each with `run.meta`,
`stderr.txt`, `verify.md`; stdout payloads external under
`<data>/receipts/swath/` with sha256 in each receipt). Pre-flight:
`receipts/smoke/_preflight/preflight.md`.

**Vendor self-published numbers** in `docs/usage.md` / `docs/design/algorithms.md`
are the vendor's own claims — used as `[DOC]` context only, never as comparative
evidence.
