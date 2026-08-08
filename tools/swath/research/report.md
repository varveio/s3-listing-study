# `swath` v0.2.0 — source-first groundwork report

**Current record:** [`ERRATA.md`](ERRATA.md) and
[`data/claims.json`](../data/claims.json); this report remains frozen derivation
history.

**Status of this document.** Source-first derivation from the pinned checkout, the project's own docs, the published image, and a small number of direct container observations. **It contains no receipts.** The study's mandatory runner-security profile is not provisioned on this box, so `harness/smoke-run.sh` was never used and no run below is a receipt — see § 8, which states the blocker precisely. Every runtime claim is labelled `[OBS <how>]`, never `[RUN]`.

**Evidence labels** (per `BRIEF.md`): `[SRC <file:line> @ cef8ec2]` read in the pinned checkout · `[DOC <path>]` the project's own docs at that revision · `[3P <source>]` third-party · `[OBS <how>]` directly observed in a run the wrapper could not record · `[INFERRED]` reasoning, with its basis stated. One extension is preserved from reader B: **`[3P-DEP <coords> <symbol>]`** — read in a pinned third-party dependency (the AWS SDK jar from the Gradle cache). It is not swath's source, so it is not `[SRC]`; it is not a published account, so `[3P <url>]` would misdescribe it. Reader anchors are preserved verbatim throughout rather than tidied away.

**Maintainer disclosure, stated plainly.** swath is developed by Varve Systems Ltd `[SRC NOTICE:1-12 @ cef8ec2]`, sole contributor `sagiba` `[3P GitHub contributors API]` — the same organisation and person that runs this study. Nothing in this report is softened or sharpened on that account; where a finding is favourable it is labelled the same way as where it is not. The relevant methodological consequence is recorded in § 9.4.

---

## 1. Metadata

| Field | Value |
| --- | --- |
| Upstream | `github.com/varveio/swath` — canonical, not a fork `[3P GitHub repo API]` |
| Pinned tag | `v0.2.0` (git tag is **`v`-prefixed**) |
| Pinned SHA | `cef8ec24a74ffae14ee6a9462e4b7f6c334fbc32` (short `cef8ec2`) |
| Language | Java 25 (`JavaLanguageVersion.of(25)`, no toolchain auto-provisioning configured) `[SRC build-logic/src/main/kotlin/swath.java-conventions.gradle.kts:23 @ cef8ec2]` |
| Build | Gradle 9.0.0 wrapper, checksum-validated `[SRC gradle/wrapper/gradle-wrapper.properties @ cef8ec2]`, `[SRC .github/workflows/ci.yml:57-58 @ cef8ec2]` |
| License | Apache-2.0; `NOTICE` = "Copyright 2026 Varve Systems Ltd"; `THIRD_PARTY_NOTICES.md` is the authoritative attribution record `[SRC NOTICE:1-12 @ cef8ec2]`, `[3P GitHub repo API: spdx_id Apache-2.0]` |
| Image (index) | `ghcr.io/varveio/swath@sha256:ef1aca9ab473f133acceb5730ff88d52abaaa89e773801cdb62deff51f9909b0` — OCI image index `[OBS anonymous registry manifest fetch, no docker login]` |
| — linux/amd64 | `sha256:c782ad1194463ada4cd15c6c633a07b2abbacc7a9ef27357c85c93f67341c072` `[OBS same]` |
| — linux/arm64 | `sha256:7c60fd25c6ae8f8273bfa24ec2d48dfdf01424b032f9b4a860095e3843a6bf52` `[OBS same]` |
| Image tag | **`0.2.0`** — no `v`. `v0.2.0` returns `manifest unknown` (404) `[OBS anonymous manifest fetch]`. `0.2` and `latest` resolve to the same index `[3P ghcr.io registry API]` |
| Image ↔ source binding | `org.opencontainers.image.revision = cef8ec24a74ffae14ee6a9462e4b7f6c334fbc32`, identical to the pinned SHA `[OBS docker inspect of the pulled arm64 image]` |
| Tool version reported | `swath 0.2.0 (cef8ec24a74f)` `[OBS docker run --network none … --version, exit 0]` |
| Upstream health | Repo created 2026-07-25; last push 2026-08-02T12:07:48Z — roughly ten days old `[3P GitHub repo API]`. Solo maintainer, 48 commits, sole contributor `[3P GitHub contributors API]`. Two releases in six days (v0.1.0 2026-07-27, v0.2.0 2026-08-02) `[3P GitHub Releases API]`. 23 open issues, 4 open PRs (all Dependabot) `[3P GitHub search API]`. **`Nightly deep verification` has failed on every visible run** — 2026-07-30/31, 08-01, 08-02, all on the same stale head `bf0bac8`; PR/`main` CI otherwise green `[3P GitHub Actions runs API]` |
| Report date | 2026-08-02 |

**Release-timing note that matters for reproducibility.** When reader E began, the image did **not** exist: the `Release` workflow for `v0.2.0` (`id=30746967874`, head_sha `cef8ec2`) had `build` = success and `publish` = `waiting`, held on the protected `public-release` environment `[3P GitHub Actions API]`. The environment was approved mid-pass and the run completed at `2026-08-02T12:14:55Z`. Everything recorded above is post-approval and was re-verified. Anyone repeating this work before that timestamp would correctly have concluded "no upstream image".

---

## 2. How it works

swath is a **parallel, work-stealing range scanner over the S3 keyspace**. It is not a paginator with a thread pool bolted on: the keyspace is divided into half-open byte ranges, ranges are subdivided at runtime by synthesizing pivot keys, and every division is committed by a compare-and-swap in a SQLite checkpoint store. **Committed is not always durable:** a directory-dataset run gets an on-disk `<dir>/.swath/checkpoint.sqlite`, but a stdout run (and any `--checkpoint none`) gets `SqliteCheckpointStore.openEphemeral` `[SRC ListCommand.java:627-629 @ cef8ec2]` — the same store, the same CAS machinery, backed by an in-process `jdbc:sqlite::memory:` database that writes nothing to disk and is never resumable `[SRC .../checkpoint/SqliteCheckpointStore.java:85-96 @ cef8ec2]`. This is the substantive part of the tool and gets the substantive treatment.

The code separates an **executor** layer (`engine/*.java` — locks, clocks, RPC) from a **pure policy** layer (`engine/policy/*.java` — no lock, clock or RPC; decides by request→response over probe outcomes). Three seams follow that shape: `Thief`↔`ThiefPolicy`, `OwnerSelfSplit`↔`OwnerSplitGovernor`, `SeedStep`↔`HybridSeedPlanner` `[SRC swath-core/src/main/java/io/varve/swath/engine/Thief.java:56-72 @ cef8ec2]`, `[SRC .../engine/OwnerSelfSplit.java:45-50 @ cef8ec2]`, `[SRC .../engine/SeedStep.java:44-48 @ cef8ec2]`. The policy classes are deterministic state machines driven by a probe-outcome sequence, which is why they are unit-testable with zero I/O `[SRC .../engine/policy/HybridSeedPlanner.java:30-33 @ cef8ec2]`.

### 2.1 Request shapes on the wire

`S3PageFetcher.fetchPage` is the **only** place a `listObjectsV2` is issued; one `fetchPage` = exactly one SDK call, counted before the call `[SRC swath-s3/src/main/java/io/varve/swath/store/s3/S3PageFetcher.java:164 @ cef8ec2]`. `VERSIONS` mode throws `UnsupportedOperationException` — only OBJECTS listing exists `[SRC …/S3PageFetcher.java:161-163 @ cef8ec2]`.

Every request carries `Bucket`, `MaxKeys`, `EncodingType=url` `[SRC …/S3PageFetcher.java:166-169 @ cef8ec2]`; `Prefix`, `StartAfter`, `Delimiter`, `FetchOwner`, `x-amz-request-payer` are conditional `[SRC …/S3PageFetcher.java:171-185 @ cef8ec2]`. Four distinct shapes are emitted in-tree:

| # | emitter | shape | classified as |
|---|---|---|---|
| 1 | `RangeScanner` worker page | `prefix=P?`, `start_after=cursor?`, no delimiter, `max_keys=1000` | `worker_page` |
| 2 | `SpeculativeReadahead` guess | identical to #1 | `worker_page` |
| 3 | `SeedStep` structure probe | `prefix=P?`, `delimiter='/'`, `start_after?`, `max_keys=1000` | `structure_probe` |
| 4a | `Thief` structure probe | `prefix=P`, `delimiter='/'`, `start_after?`, `max_keys=32` | `structure_probe` |
| 4b | `Thief` pivot probe | `prefix=P`, no delimiter, `start_after=m`, `max_keys=1` | `pivot_probe` |

Anchors: `[SRC .../RangeScanner.java:216 @ cef8ec2]`; `[SRC .../SpeculativeReadahead.java:371 @ cef8ec2]`; `[SRC .../SeedStep.java:298-299 @ cef8ec2]` with `DELIMITER={'/'}`, `PROBE_PAGE=1000` `[SRC .../HybridSeedPlanner.java:38,41 @ cef8ec2]`; `[SRC .../Thief.java:400-402 @ cef8ec2]` with `STRUCTURE_PROBE_MAX_KEYS=32` `[SRC .../policy/ThiefPolicy.java:35 @ cef8ec2]`; `[SRC .../Thief.java:265,425 @ cef8ec2]`.

**`ContinuationToken` is never sent.** Pagination is purely `start_after = last emitted key` `[SRC .../store/PageRequest.java:11-15 @ cef8ec2]`, `[SRC .../RangeScanner.java:294 @ cef8ec2]`; the response's `NextContinuationToken` is carried into `ListPage` and unused `[SRC …/S3PageFetcher.java:331-333 @ cef8ec2]`. The stated reason is that range-stealing needs an arbitrary sub-range lower bound, which an opaque token cannot express `[DOC docs/internals/s3-implementation-compatibility.md:132-134]`. This is the single design decision from which most of the rest follows.

**Page size is 1000 and is not configurable** — `int pageMax = 1000;` is a hard-coded local in `ListCommand`, threaded into the specs; no `@Option` feeds it `[SRC swath-cli/src/main/java/io/varve/swath/cli/ListCommand.java:395 @ cef8ec2]`. Confirmed live: `swath list --help` shows no `--max-keys` `[OBS docker run --network none … list --help]`.

### 2.2 Seeding — how the keyspace is first divided

`--tune seed.mode=shallow` (default) runs a bounded `delimiter=/` descent before any worker starts. Budgets are worker-count-derived `[SRC .../HybridSeedPlanner.java:131-142 @ cef8ec2]`:

```java
this.targetSeeds = Math.min(PROBE_PAGE, 4 * w);   // PROBE_PAGE = 1000 → cap 4×W, ≤1000
this.maxProbes   = Math.min(256, Math.max(1, targetSeeds));
```

`targetSeeds` bounds the **final cut count**, not descent reach; the descent stops on `maxProbes` or frontier exhaustion `[SRC .../HybridSeedPlanner.java:363 @ cef8ec2]`. A `SAMPLE_BUDGET = 32` sub-budget is carved **out of** `maxProbes`, never added: `descentCeiling = massAware ? maxProbes - min(SAMPLE_BUDGET, maxProbes/2) : maxProbes` `[SRC .../HybridSeedPlanner.java:56,354 @ cef8ec2]`.

The descent is **serial** — one frontier node polled, probed, children enqueued, then the next `[SRC .../HybridSeedPlanner.java:362-377 @ cef8ec2]`. `[DOC docs/internals/algorithms.md §8]` states seed wall-time ≈ `maxProbes × probe RTT` and that raising `--concurrency` *lengthens* it; the serial structure corroborates that `[INFERRED from SRC .../HybridSeedPlanner.java:362-377]`.

Cuts accumulate in a global `TreeSet<byte[]>` ordered by `Arrays::compareUnsigned` `[SRC .../HybridSeedPlanner.java:167 @ cef8ec2]`; the executor tiles them:

```java
byte[] lo = null;
for (byte[] hi : cuts) { specs.add(seedTile(runId, lo, hi)); lo = hi; }
specs.add(seedTile(runId, lo, null));   // final open range (c_last, null]
```
`[SRC .../SeedStep.java:237-246 @ cef8ec2]`, each tile's cursor starting at its own `lo` `[SRC .../SeedStep.java:254-256 @ cef8ec2]`. **This tiles exactly for any sorted cut set**, so every seed heuristic below can change balance but never coverage `[INFERRED, load-bearing]`. Seeds are inserted all-or-nothing `[SRC .../checkpoint/CheckpointStore.java:61-72 @ cef8ec2]`.

Seed pages' **object contents are discarded** — `SeedStep.toOutcome` promotes only `commonPrefixes`, `truncated`, `entries().size()` (a count, never keys), and `lastSeenKey` `[SRC .../SeedStep.java:263-269 @ cef8ec2]`. No seed page's objects are ever emitted `[SRC .../SeedStep.java:50-54 @ cef8ec2]`, so seed/range double-emit is structurally impossible.

A truncated level is classified in order `[SRC .../HybridSeedPlanner.java:391-420 @ cef8ec2]`: **flat-wide** (no common prefixes, objects present) → pre-cut into leading-byte radix bands over printable ASCII with `'%'` skipped `[SRC .../HybridSeedPlanner.java:715-717, 95-97, 841, 849-882 @ cef8ec2]`; **partition fan-out** (majority of prefixes whose final segment contains `'='`) → tiled from the already-fetched page with zero further probes `[SRC .../HybridSeedPlanner.java:724-756, 796-809 @ cef8ec2]`; **ambiguous** → disambiguated by a bounded child sample (`SAMPLE_WIDTH=3`, dense iff `pageCapped || hasCommonPrefixes || objectCount >= 8`, heavy iff `sampleDense*2 >= sampleSampled`) `[SRC .../HybridSeedPlanner.java:49-63, 457-492 @ cef8ec2]`, heavy → banded `[SRC .../HybridSeedPlanner.java:816-830 @ cef8ec2]`, not heavy → `tinyLeafExplosion`, left whole for work-stealing `[SRC .../HybridSeedPlanner.java:494-503 @ cef8ec2]`. An exploding level does **not** abort the descent `[SRC .../HybridSeedPlanner.java:422-430 @ cef8ec2]`; a truncated **top** level does — one extra top page is read, then the descent is skipped entirely `[SRC .../HybridSeedPlanner.java:267-273, 332-347 @ cef8ec2]`.

Frontier ordering under `mass_aware_seed` (default ON) is a `PriorityQueue` ordered depth-ascending then span-score-descending `[SRC .../HybridSeedPlanner.java:1020-1071 @ cef8ec2]`, span score being the keyspace gap to the next sibling, falling back to the enclosing scope's `prefixCeil` `[SRC .../HybridSeedPlanner.java:944-967 @ cef8ec2]`. Toggle off → plain FIFO `[SRC .../HybridSeedPlanner.java:992-1014 @ cef8ec2]`. Over-cap cut sets are reduced by `massWeightedSubsample` (needs ≥8 weights) else `subsampleEvenly` `[SRC .../HybridSeedPlanner.java:76, 507-545, 555-564, 885-938 @ cef8ec2]`.

### 2.3 Stealing — how ranges are subdivided at runtime

Victim selection walks a driver-curated pool, skipping `unsplittable`, then futility-paced victims, then `est <= 0`, and takes `argmax est`; `NoVictim` carries a discriminated reason `[SRC .../policy/ThiefPolicy.java:98-154 @ cef8ec2]`. The pool is pre-filtered by a **progress gate**: only workers with `emittedSinceSteal == true` `[SRC .../WorkStealingScan.java:625-633 @ cef8ec2]`, set at page-commit and cleared at a successful carve `[SRC .../WorkerState.java:231-234, 458-464 @ cef8ec2]` — bounding re-splitting to ≤1 per emitted page and guaranteeing a fresh child is never carved before its first page. The open frontier scores `+INFINITY` `[SRC .../engine/StealMath.java:107 @ cef8ec2]`, `[SRC .../engine/RateAnchoredEstimator.java:94-96 @ cef8ec2]`, so it always wins the argmax once steal-eligible.

Pivot synthesis is a 7-phase state machine `[SRC .../ThiefPolicy.java:256-264 @ cef8ec2]`:

- **Start** — refuse if the `(cursor,hi)` snapshot is unchanged since the last non-productive steal, or `cursor >= hi`. `f = 0.5` on the open frontier, else `farAheadFraction(densityFraction())` where `densityFraction() = clamp(0.5 + 0.25·min(1, trailingEwmaDensity/averageDensity), 0.5, 0.75)` `[SRC .../ThiefPolicy.java:306-330 @ cef8ec2]`, `[SRC .../WorkerState.java:285-307 @ cef8ec2]`. `m == null` splits two ways: open frontier with `cursor <= lo` → transient `RETRY(UNSTARTED_FRONTIER)`; otherwise terminal cached `SET_UNSPLITTABLE` `[SRC .../ThiefPolicy.java:319-327 @ cef8ec2]`, `[SRC .../StealMath.java:311-315 @ cef8ec2]`.
- **Probe** — exactly one `ListObjectsV2(prefix=P, start_after=m, max_keys=1)`; "empty" iff no key returned or first key `> H` `[SRC .../Thief.java:420-428 @ cef8ec2]`.
- **Step-back** — if the upper half is empty, `hi != null` and `f > 0.5`, re-place at the plain midpoint and re-probe once `[SRC .../ThiefPolicy.java:355-367 @ cef8ec2]`. This is what makes far-ahead never worse than byte-midpoint.
- **Structure discovery** — one `delimiter=/` list at 32 keys; probe prefix differs for `upperEmpty` (the `lo∧c` directory) vs `parentEmptySliver` (coarse→fine back-out from the `c∧hi` divergence directory, ≤3 extra probes) `[SRC .../ThiefPolicy.java:421-499 @ cef8ec2]`, `[SRC .../Thief.java:398-412 @ cef8ec2]`. Boundary = median of in-range common prefixes on a complete page, furthest when the sample was capped `[SRC .../ThiefPolicy.java:451-472 @ cef8ec2]`. Suppressed at `consecutiveZeroFanoutProbes >= 8` or `consecutiveTimedOutStructureProbes >= 2`, with a 1-in-64 escape hatch; both counters are **per-victim** `[SRC .../ThiefPolicy.java:59-65, 392-404 @ cef8ec2]`, `[SRC .../WorkerState.java:75-87 @ cef8ec2]`. A 503 deliberately does **not** feed the timeout counter — store backpressure is not keyspace shape `[SRC .../Thief.java:458-477 @ cef8ec2]`.
- **Density reflection** → **bisection** (budget `ceil(log2(bandWidthBytes)) + 6`) → **flat-leaf fallback** `[SRC .../ThiefPolicy.java:507-598 @ cef8ec2]`.

Pivot primitives operate over Unicode **code points**, decoded and re-encoded, so every synthesized pivot is valid UTF-8 by construction `[SRC swath-model/src/main/java/io/varve/swath/model/ByteMidpoint.java:34-39, 106-126, 168-170, 269 @ cef8ec2]`.

**Owner self-split** runs inside the page-commit lock region with **zero extra API calls** (`interpolate` is pure math) `[SRC .../WorkStealingScan.java:699-701 @ cef8ec2]`, `[SRC .../OwnerSelfSplit.java:144-148 @ cef8ec2]`. Its gate chain refuses the open frontier outright, then applies a remaining-estimate floor (4 pages), a rate limit (32 pages between carves), a demand gate, a child-tail floor, and a confetti feedback gate that suppresses when the realized-mass classifier says carves are producing runt children — with every 16th carve let through as a probe `[SRC .../policy/OwnerSplitGovernor.java:84-259 @ cef8ec2]`. Children are tagged **before** publish (publishing makes them claimable, so tagging after would race a fast drainer) and classified once at completion as confetti iff `keysEmitted <= 2·maxKeys && !hasSplit` `[SRC .../OwnerSelfSplit.java:96-114, 224-229, 348-350 @ cef8ec2]`. All of it is process-local and never checkpointed — a resumed child completes untagged, contributing no classification rather than a wrong one `[SRC .../OwnerSelfSplit.java:110-114 @ cef8ec2]`.

### 2.4 Range scanning and ordering assumptions

Boundary semantics are fixed in one place: strict `k > hi` stops, so `k == m` stays with the victim (boundary-belongs-left) `[SRC .../RangeScanner.java:241-246 @ cef8ec2]`. The bound is re-read **per key** from an `AtomicReference`-backed supplier, not per page `[SRC .../WorkerState.java:351-353 @ cef8ec2]` — so a thief's narrow takes effect mid-page. Completion is `done = reachedBound || !pageTruncated` `[SRC .../RangeScanner.java:258 @ cef8ec2]`, computed **before** the protocol defences so a legitimately-empty post-narrow page completes the node instead of tripping the broken-page check.

The loop **assumes each page arrives in ascending unsigned byte order** and that `start_after` is exclusive; comparisons are byte-exact `Arrays.compareUnsigned` throughout, never `String.compareTo` `[SRC swath-model/.../KeyBytes.java:45-52 @ cef8ec2]`. Reader B checked whether that assumption is defended and found **no intra-page monotonicity check** anywhere in `swath-core/src/main` or `swath-s3/src/main`: the forward-progress guard compares only `lastKey` against the previous cursor, so a page whose *interior* keys are unordered — while its last key still advances — passes unexamined `[INFERRED, from RangeScanner.java:230-280 and the absence of any per-page ordering check]`. **Adjudication:** readers A and B agree on the facts (A states the assumption, B establishes it is unchecked); this is a single finding, and it is a real one — recorded in § 6.2.

### 2.5 Concurrency, AIMD, and termination

`workerCount` (= `--concurrency`) virtual threads are forked once plus a receiver watcher `[SRC .../WorkStealingScan.java:421-433 @ cef8ec2]` on `Executors.newVirtualThreadPerTaskExecutor()` `[SRC .../concurrent/Scope.java:21,45 @ cef8ec2]`. A worker never holds its slot waiting on child work — when nothing is claimable it becomes a thief `[SRC .../WorkStealingScan.java:514-535, 547-603 @ cef8ec2]`.

Termination is a single `ReentrantLock` + `Condition` guarding a ready deque and an `AtomicLong outstanding` `[SRC .../engine/Worklist.java:41-47, 88-172 @ cef8ec2]`. A split child is counted **while the thief still holds the victim lock** `[SRC .../Worklist.java:111-121 @ cef8ec2]`, `[SRC .../WorkStealingScan.java:908-916 @ cef8ec2]`, so quiescence cannot observe a false zero between hand-off and count. Lock order is strictly **victim → gate** `[SRC .../Worklist.java:26-30 @ cef8ec2]`. At most **one steal attempt is in flight fleet-wide**, enforced by a monitor-guarded `attemptInFlight` released in a `finally` covering the whole acquired region `[SRC .../WorkStealingScan.java:565-589 @ cef8ec2]`, `[SRC .../IdleStealBackoff.java:66, 87-98, 133-135 @ cef8ec2]`.

**`--concurrency` is a ceiling, not a setpoint — the most consequential tunable fact in this report.** `ConcurrencyGauge` wraps a resizable `Semaphore` tracked by an `AtomicInteger effectiveT` `[SRC .../ConcurrencyGauge.java:141, 808-822 @ cef8ec2]`. `tMax` (= `workerCount`) appears **only as an upper clamp**: a fresh run starts at `min(4, tMax)` `[SRC .../ConcurrencyGauge.java:62, 274-276 @ cef8ec2]`, grows `min(tMax, T*2)` while slow-starting and `min(tMax, T+1)` after the congestion latch `[SRC .../ConcurrencyGauge.java:754-756 @ cef8ec2]`, and every decrease is `max(1, floor(factor·T))` with no floor above 1 `[SRC .../ConcurrencyGauge.java:626 @ cef8ec2]`. So `T` virtual threads exist but only `effectiveT ≤ tMax` hold a fetch permit at once, and the **steady-state level is set by the store, not by the flag** `[INFERRED from the above]`. A benchmark that treats `--concurrency N` as "N concurrent requests" will be wrong.

Decreases: 503/`SlowDown` → factor 0.7, a real AIMD vote `[SRC .../ConcurrencyGauge.java:57, 324-331, 562-567 @ cef8ec2]`; sustained-timeout shed → factor 0.5, once per jittered 25–40 s window, gated on `timeouts >= max(3, ceil(0.3·T))` **and** `successes <= max(1, T/32)` `[SRC .../ConcurrencyGauge.java:93-109, 489-526 @ cef8ec2]` — the starvation clause is load-bearing, but read it exactly: it permits up to `max(1, T/32)` successes in the window, which is **1** at any `T < 64`. So the shed is hard to trip on a *healthy* run, not impossible on a *progressing* one — at `T=4`, three timeouts plus one completed fetch in the same window still sheds `[INFERRED, from the two gate expressions at :489-500]`. A worker above the new `T` is never killed mid-page `[SRC .../ConcurrencyGauge.java:48-51, 641-642 @ cef8ec2]`. Growth is additionally gated by a hard worker-timeout freeze (≥3 in 10 s) and a latency-inflation freeze (EWMA > 2× a Vegas rolling minimum), the latter demoted to a valve admitting one `+1` per ~30 s `[SRC .../ConcurrencyGauge.java:83-138, 473-535, 690-731 @ cef8ec2]`. Probe-class faults are excluded from the congestion latch and the shed gate `[SRC .../ConcurrencyGauge.java:369-377 @ cef8ec2]`.

### 2.6 Retries, timeouts, throttling

**SDK-internal retry is off.** `AwsRetryStrategy.standardRetryStrategy()` with `maxAttempts = S3Config.DEFAULT_MAX_ATTEMPTS = 1` `[SRC .../S3ClientFactory.java:102-105 @ cef8ec2]`, `[SRC .../S3Config.java:80 @ cef8ec2]`, hard-wired at `[SRC .../ConnectionOptions.java:192 @ cef8ec2]` — so the AIMD gauge sees every real 503 immediately `[SRC .../S3Config.java:75-80 @ cef8ec2]`. One increment of the API counter ≈ one HTTP request, which is what makes `cost.api_calls` meaningful (§ 5.4).

Two retry loops with different policies: `TransientRetryFetcher` (seed probes, sequential path) `[SRC .../TransientRetryFetcher.java:20-45 @ cef8ec2]` and `GaugedFetcher` (engine workers and thieves) `[SRC .../GaugedFetcher.java:21-59 @ cef8ec2]`. Shared constants: `MAX_TRANSIENT_RETRIES=8`, `BACKOFF_BASE=100 ms`, `BACKOFF_CAP=5 s`, `STORM_BACKOFF_CAP=15 s`, `MAX_ESCALATION_LEVEL=2` `[SRC .../TransientRetryFetcher.java:49-74 @ cef8ec2]`. Backoff is AWS full jitter `[SRC .../TransientRetryFetcher.java:250-254 @ cef8ec2]`.

`RetryPolicy` is resolved once at CLI wiring from whether the watchdog is armed: `watchdog.isArmed() ? RIDE_OUT : BOUNDED` `[SRC .../ListCommand.java:472-474 @ cef8ec2]`. Since the watchdog is on by default, **production default is `RIDE_OUT`**: over-cap transients retry indefinitely at a 15 s ceiling and the watchdog owns death `[SRC .../GaugedFetcher.java:245-252 @ cef8ec2]`. `BOUNDED` cancels the run `STUCK` → resumable exit 75 `[SRC .../GaugedFetcher.java:227-243 @ cef8ec2]`.

An asymmetry worth carrying: in `GaugedFetcher` an AIMD-voting throttle **resets** `transientRetries` and both fault tallies `[SRC .../GaugedFetcher.java:167-181 @ cef8ec2]`, so the 8-cap only bounds consecutive *client-side* transients — a permanently-503ing endpoint can never trip it. In `TransientRetryFetcher` every `ThrottleException` counts toward the cap and is never reset `[SRC .../TransientRetryFetcher.java:156-159, 188-195 @ cef8ec2]`. The seed path is strictly less patient than the engine path.

Per-class attempt budgets are driven by cost shape, not "is it a probe" `[DOC docs/internals/probe-budgets.md §1]`: `worker_page` and `structure_probe` get the client-level 10 s `apiCallAttemptTimeout`; only `pivot_probe` gets a 3 s per-request override `[SRC .../S3PageFetcher.java:191-203, 482-498 @ cef8ec2]`, `[SRC .../S3Config.java:51,68,74 @ cef8ec2]`. Escalation is `base × 2^level`, level clamped to [0,2] on *consecutive* attempt-timeouts and reset by any voting throttle or network fault `[SRC .../S3PageFetcher.java:500-535 @ cef8ec2]`, `[SRC .../GaugedFetcher.java:188-208 @ cef8ec2]` — giving 10/20/40 s (scan) and 3/6/12 s (point). Both loops treat an incoming level as a **floor**, never a starting point `[SRC .../GaugedFetcher.java:137-149 @ cef8ec2]`. The client-level `apiCallTimeout = 60 s` is never overridden per request `[SRC .../S3ClientFactory.java:107-112 @ cef8ec2]`, and at level ≤2 the per-attempt budget (≤40 s) never reaches it `[INFERRED, from the two constants + the level cap]`. **None of these are CLI-tunable** `[SRC .../ConnectionOptions.java:187-198 @ cef8ec2]`, `[DOC docs/internals/contracts.md:1091]`.

`--request-rate N` is a Bucket4j local bucket with **capacity 1** and greedy refill of one token every `round(1e9/rps)` ns — deliberately zero-burst, fixed-interval spacing, stated as part of the public contract `[SRC .../store/ApiRateLimiter.java:15-19, 60-76 @ cef8ec2]`. It wraps the fetcher once, before either path is built, so it gates the run's aggregate rate `[SRC .../RateLimitedPageFetcher.java:14-32, 46-52 @ cef8ec2]`. The stack is `FirstRequestMarkerFetcher → [RateLimitedPageFetcher] → S3PageFetcher` with `GaugedFetcher`/`TransientRetryFetcher` outside that `[SRC .../ListCommand.java:849-862 @ cef8ec2]` — so **the rate limiter sits inside the retry loops and every retry attempt also pays the rate-limit wait** `[INFERRED, from the wrapping order]`.

### 2.7 Correctness: disjointness and exhaustiveness

Five mechanisms, each closing a distinct hazard. This is the strongest part of the design and it is worth stating precisely, because it is the property a listing benchmark most needs to trust.

- **I-a. Seed tiling is exact for any cut set** — consecutive half-open intervals ending in `(c_last, null]`, inserted all-or-nothing `[SRC .../SeedStep.java:237-246 @ cef8ec2]`, `[SRC .../CheckpointStore.java:61-72 @ cef8ec2]`.
- **I-b. Boundary belongs left** — the single strict `k > hi` `[SRC .../RangeScanner.java:243 @ cef8ec2]` paired with the child's `cursor = m` (exclusive `start_after`) `[SRC .../checkpoint/SqliteCheckpointStore.java:611 @ cef8ec2]` places `m` in exactly one interval. The wrong convention here is a one-key gap or overlap at *every* split `[DOC docs/internals/algorithms.md §1.2]`.
- **I-c. Two independent narrow defences** — the lock-free per-key `hi` re-read cannot retract a key already batched under the old bound; that residual window is closed by an under-lock re-trim at commit, and **downstream emission uses `inRange`/`kept`, never the raw `batch`** `[SRC .../WorkStealingScan.java:683-685, 844-853, 739-763 @ cef8ec2]`. The second half is what makes the re-trim prevent a double-emit rather than merely correct the cursor.
- **I-d. Durable CAS with three clauses** — `UPDATE listing_node SET range_end=?, generation=generation+1 … WHERE id=? AND (cursor IS NULL OR cursor < ?) AND range_end IS ? AND status<>'COMPLETED'`; rowcount 0 → `SPLIT_ABORTED`, child not inserted; UPDATE + INSERT run in one writer-thread transaction, so a crash mid-split leaves both rows or neither `[SRC .../SqliteCheckpointStore.java:588-591, 599-601, 129, 584-620 @ cef8ec2]`.
- **I-e. Two distinct loser paths, only one restores `hi`** — the early loser does **not** touch `hi` (restoring would clobber the winner's narrow and re-open an overlap); the late loser restores `H`, safe because it was validated under the lock `[SRC .../Thief.java:301-389 @ cef8ec2]`.

**What is not protected by a runtime check.** Strict betweenness of a synthesized pivot (`a < m < b`) is re-verified in only two places inside `ByteMidpoint` `[SRC ByteMidpoint.java:321-334, 439 @ cef8ec2]`; the direct returns in `between`'s Path A/B and in `forwardReflect` carry no assertion, and no `assert` keyword appears in `ByteMidpoint`/`KeyBytes`/`AlphabetDigest`. Consumers partially compensate `[SRC .../ThiefPolicy.java:510-512, 583-587 @ cef8ec2]`, `[SRC .../OwnerSplitGovernor.java:195-197 @ cef8ec2]`, and the CAS's `cursor < pivot` is a durable backstop. **In practice the CAS clause plus the per-key and commit-time trims make a bad pivot a balance bug rather than a coverage bug — the tiling holds for *any* `m` the CAS accepts** `[INFERRED]`.

**Non-snapshot pagination.** `[DOC docs/internals/algorithms.md §6]` claims a key inserted behind a passed cursor is missed (as with any paginated lister) and that overlap is structurally zero. The structural-zero half is corroborated by I-b/I-c/I-d `[INFERRED]`; the insertion behaviour is not something source alone can confirm.

### 2.8 Memory model

Output is **streaming, not accumulate-then-dump**. `OutputStage.consume` receives one `PageBatch` at a time and writes each entry straight through the formatter, holding no per-run collection `[SRC .../output/OutputStage.java:97-113 @ cef8ec2]`; each formatter reuses one `StringBuilder(256)` `[SRC .../TsvFormatter.java:27; .../JsonlFormatter.java:25; .../AlignedFormatter.java:128 @ cef8ec2]`; the only per-run state is `RowTally`'s four longs `[SRC .../output/RowTally.java:141-144 @ cef8ec2]`.

`--object-listing-queue-size` is an **entry** budget, not a slot count: `Channel` is bounded by a weight equal to the batch's entry count, `End`/`Failure` weigh 0, and admission tests `inFlight < capacity` **before** adding the whole item — so in-flight can transiently reach `capacity - 1 + pageSize`, i.e. one S3 page of overshoot `[SRC .../pipeline/Channel.java:18-27, 63-68, 85-96 @ cef8ec2]`. A single page larger than capacity is still admitted on an empty channel, so the pipeline cannot deadlock `[SRC .../Channel.java:23-27 @ cef8ec2]`. The backing `LinkedBlockingQueue` is unbounded; the weight gate is the only bound `[SRC .../Channel.java:36,95 @ cef8ec2]`.

Explicitly **outside** the bounded-memory invariant: Parquet finalized-part metadata is `O(parts)` and the full `files[]` list is re-serialized on every finalize, giving cumulative `O(parts²)` work; `--sort` retained staging metadata is `O(segments)` `[DOC docs/internals/contracts.md §4.1, §7.2]`. Neither applies to the stdout text modes. The split tree and cursors are SQLite-resident, not heap `[DOC contracts.md §7.2]`. `docs/internals/contracts.md §7.2` states PERF-gate ceilings for text and Parquet modes — **existence noted; the numbers are vendor self-reported and are not adopted here as findings.**

`--sort` is an external merge sort that **spills rather than buffering the run**: pages fill an in-memory `SortBuffer` sealed on a byte gate or entry cap, handed to one off-thread encoder, flushed as a `.pageseg` segment into a visible `<output-dir>/_staging/` on the same filesystem `[SRC .../sort/SortLane.java:23-55 @ cef8ec2]`, `[SRC ListCommand.java:122, 1332 @ cef8ec2]`, `[SRC .../sort/CaptureSorter.java:84 @ cef8ec2]`. At most `swath.sort.buffers` (default 2) live buffers, enforced by a `Semaphore(buffers-1)` released only after encoding completes `[SRC .../SortLane.java:35-52 @ cef8ec2]`; `segment-bytes` is heap-adaptive at ≈8 % of `Runtime.maxMemory()` floored at 64 MB `[SRC .../sort/SortConfig.java:79, 109, 117-131 @ cef8ec2]`; merge peak is a function of the budget knob, not the segment count `[SRC .../SortConfig.java:86 @ cef8ec2]`.

### 2.9 Resume story

The output **directory** is the run handle; `swath resume <dir>` opens `<dir>/.swath/checkpoint.sqlite` and refuses an arbitrary SQLite path `[SRC ResumeCommand.java:36-45,131-144,265-278 @ cef8ec2]`. It reloads existing nodes and continues each from `cursor`/`durable_cursor`, and **never runs the seed step** `[SRC ResumeCommand.java:119-194 @ cef8ec2]`, `[SRC .../SeedStep.java:149-151 @ cef8ec2]`. The checkpoint is SQLite at `PRAGMA user_version = 1`, exact-match or refuse — no migration path `[SRC .../checkpoint/CheckpointSchema.java:35,55-62,105-121 @ cef8ec2]` — with three tables `run_meta` / `listing_node` / `part_file` `[SRC .../CheckpointSchema.java:190-214 @ cef8ec2]`. `cursor` is the last **emitted** key (text sinks, at-most-once); `durable_cursor` is the highest key inside a **finalized** part (file sinks, exactly-once) `[SRC .../CheckpointSchema.java:198-207 @ cef8ec2]`, `[SRC .../checkpoint/Node.java:170-176 @ cef8ec2]`. The checkpoint is deleted on clean completion, so resuming a completed dir prints "already complete" and exits 0 `[SRC ResumeCommand.java:136-140 @ cef8ec2]`.

Only a **Parquet directory dataset** is resumable: `--checkpoint auto` resolves to `null`/ephemeral for stdout, is **refused (exit 2)** for a FILE-kind destination, and only yields a durable `<dir>/.swath/checkpoint.sqlite` for a directory dataset `[SRC CheckpointOptions.java:67-77 @ cef8ec2]`, `[SRC ListCommand.java:1554-1580 @ cef8ec2]`. `--checkpoint none` runs the identical work-stealing engine; only durability differs `[SRC EngineOptions.java:60-65 @ cef8ec2]`, `[DOC docs/usage.md:142-146]`.

Resume safety is enforced by a first-class `STICKY`/`IDENTITY`/`FREE` classification carried by a `@Resume` annotation on the *same* program element as `@Option` "so the annotation can never name a wrong option", drift-tested to cover every visible option `[SRC cli/Resume.java:13-31 @ cef8ec2]`, `[SRC cli/ResumeClass.java:8-20 @ cef8ec2]`. `IDENTITY` (refused if changed) covers `--endpoint-url`, `--format`, `-o`, `--output-type`, `--fetch-owner`, `--sort`, all seven filters, `--tune seed.mode`, and the four `args_hash` fields; `STICKY` (soft-restored unless re-passed) covers `--no-sign-request`, `--profile`, `--region`, `--requester-pays`; everything else is `FREE` `[SRC ResumeRegistry.java:65-109 @ cef8ec2]`. The refusal **names the changed column** rather than guessing `[SRC ResumeRegistry.java:130-182 @ cef8ec2]`.

Resume refuses, in the code's own settled order `[SRC ListCommand.java:649-815 @ cef8ec2]`: a recorded FILE-kind text destination; a malformed/absent `output_format`; a resolved FILE-kind output (defensive backstop for foreign checkpoints); `status == FAILED && fatal_error` — deliberately **narrower** than `status == FAILED`, because the broken-pipe path marks FAILED *without* the flag so a truncated stdout run stays resumable; any changed IDENTITY column; and a stale `--sort` staging format.

Both bearer-token flags are deliberately `FREE` and never persisted, for two reasons the source argues explicitly: "*a stored command is a stored secret*" and "*a stored command is executed later* … a checkpoint is data, not a trusted script" `[SRC cli/BearerTokenOptions.java:15-33 @ cef8ec2]`. A resumed run against a bearer-auth endpoint must re-pass the command.

---

## 3. Modes and tunables

A **mode** changes the request pattern or the output contract; a **tunable** changes only magnitude (BRIEF definition, applied verbatim).

### 3.1 Modes

| # | Mode | Selected by | Why it is a mode | Evidence |
|---|---|---|---|---|
| M1 | `table` | `--format table`, or `auto` on a TTY | Distinct output contract: aligned human view, three fields only | `[SRC OutputOptions.java:47-48,115-118 @ cef8ec2]`, `[SRC .../output/OutputFormat.java:9-18 @ cef8ec2]` |
| M2 | `tsv` | `--format tsv` / `-o x.tsv` / `auto` off-TTY | Distinct output encoding | same |
| M3 | `jsonl` | `--format jsonl` / `-o x.jsonl` | Distinct output encoding | same |
| M4 | `parquet` FILE-kind | `-o out.parquet` / `--output-type file` | Collapses to one writer; requires `--checkpoint none`; **non-resumable** | `[SRC OutputOptions.java:417-427 @ cef8ec2]`, `[SRC ListCommand.java:1554-1580 @ cef8ec2]`, `[DOC docs/usage.md:128-140]` |
| M5 | `parquet` DIRECTORY dataset | `-o out/` / `--output-type dir` | Multi-writer pool (2–4); `data/*.parquet` + `manifest.json` + `_SUCCESS` + `symlink.txt`; **the only resumable regime** | `[SRC OutputOptions.java:283-296,461-468 @ cef8ec2]`, `[DOC docs/usage.md:170-190]` |
| M6 | `--sort` | `--sort` (Parquet directory only) | Different output contract (globally sorted, range-disjoint parts) **and** a different run shape: a LISTING→MERGING→PUBLISHED machine whose merge phase issues **zero** LIST calls | `[SRC ListOptionGroups.java:32-36 @ cef8ec2]`, `[SRC ListCommand.java:1516-1534, 1326-1370 @ cef8ec2]`, `[SRC .../checkpoint/SortPhase.java:247-251 @ cef8ec2]` |
| M7 | `swath resume <dir>` | subcommand | Different entry point and request pattern; never runs the seed step | `[SRC ResumeCommand.java:119-194 @ cef8ec2]`, `[SRC SeedStep.java:149-151 @ cef8ec2]` |
| M8 | `--tune seed.mode=shallow` (default) | `--tune` | Issues a bounded up-front `delimiter=/` structure-probe pass | `[SRC TuneOptions.java:25-26,161-168 @ cef8ec2]`, `[SRC SeedStep.java:159 @ cef8ec2]` |
| M9 | `--tune seed.mode=none` | `--tune` | Genuinely different request pattern: one root range `(⊥, null]`, **zero** delimiter probes, stealing only | `[SRC TuneOptions.java:78-81 @ cef8ec2]`, `[SRC SeedStep.java:155-158 @ cef8ec2]`, `[SRC .../engine/SeedMode.java:12-18 @ cef8ec2]` |
| M10 | `--tune seed.mode=hints` | `--tune` | **Declared but unreachable.** CLI validation accepts it; it throws `InvalidConfigException` (exit 2) at seed time — *after* the checkpoint DB is opened and the S3 client built | `[SRC EngineOptions.java:54 @ cef8ec2]`, `[SRC SeedStep.java:160-161 @ cef8ec2]` |

**M8/M9 is the mode inventory's least obvious member and the one most likely to be missed.** It is the only *supported* user control over whether swath issues `delimiter=/` requests at all. It is not a delimiter/shallow *output* mode — output is a full recursive listing either way — but it changes the request pattern, which is exactly what the definition catches.

**Diagnostic mode-shaped surface — explicitly unsupported.** Fourteen `--engine-toggle` values reach the engine and several change the request pattern outright (`structure_probes=off` removes `delimiter=/` probing entirely; `readahead=on` adds speculative fetches). By the letter of the definition these are modes; by the project's own words they are not configurations:

> "**EXPERIMENTAL / DIAGNOSTIC — not a supported configuration.** `DEFAULT` is the only supported configuration, with one documented exception: `rate_anchored_sensing=off` together with `tail_floor=current` is the supported rollback to pre-0.2.0 engine behaviour" `[SRC EngineToggles.java:22-27 @ cef8ec2]`

Full set: ten ablations default-`on` (`owner_split`, `density_ewma`, `radix_bands`, `structure_probes`, `far_ahead`, `alphabet_pivots`, `reflect`, `confetti_feedback`, `reflect_lift`, `fanout_tiling`) `[SRC EngineToggles.java:197-199 @ cef8ec2]`; `readahead` opt-in default-**off** `[SRC :201-202]`; `mass_aware_seed` opt-out default-**on** `[SRC :204-209]`; `rate_anchored_sensing` opt-out default-**on** since 0.2.0 `[SRC :211-220]`; `tail_floor` — the only value-taking toggle, default `reach_floored` `[SRC :222-231]`. Unknown name / bad value / contradictory repeat = exit 2, validated before any I/O `[SRC EngineToggles.java:257-296 @ cef8ec2]`, `[SRC ListCommand.java:312 @ cef8ec2]`.

**Adjudication (readers A and C).** A treats the toggles as engine internals; C notes they satisfy the mode definition. Both are right; the resolution is that they are *diagnostic* surface, and the study should treat the documented pre-0.2.0 rollback pair (`rate_anchored_sensing=off` + `tail_floor=current`) as the single legitimate non-default engine configuration — it is the one the vendor supports and the one that isolates the 0.2.0 delta (§ 9.1).

### 3.2 Tunables (magnitude only)

| Flag | Default | Effect | Resume class | Evidence |
|---|---|---|---|---|
| `--concurrency N` | **64** | AIMD **ceiling** `Tmax`, not a setpoint (§ 2.5); range [1, 100000] | FREE | `[SRC ConnectionOptions.java:78-80 @ cef8ec2]`, `[SRC ConcurrencyGauge.java:62,274-276,754-756 @ cef8ec2]` |
| `--object-listing-queue-size N` | 50000 | In-flight **entry** budget (§ 2.8) | FREE | `[SRC ConnectionOptions.java:82-84 @ cef8ec2]` |
| `--request-rate N` | unset / 0 = uncapped | Aggregate client-side req/s, zero-burst | FREE | `[SRC ConnectionOptions.java:86-89 @ cef8ec2]`, `[SRC ApiRateLimiter.java:60-76 @ cef8ec2]` |
| `--parquet-part-size SIZE` | 256mb | Part rotation target | FREE | `[SRC OutputOptions.java:336-337 @ cef8ec2]` |
| `--part-rotation-interval DUR` | 30s (floor 100ms; `0/none/off` disables) | Part rotation by age | FREE | `[SRC OutputOptions.java:339-342,407 @ cef8ec2]` |
| `--part-rotation-max-rows N` | 2000000 (`0` disables) | Part rotation by rows | FREE | `[SRC OutputOptions.java:344-347 @ cef8ec2]` |
| `--progress-interval DUR` | 1s redraw / 30s appended; floor 1s **rejected, not clamped** | Progress cadence; implies `--progress` | FREE | `[SRC ListOptionGroups.java:39-46 @ cef8ec2]`, `[SRC LivenessOptions.java:36,51-58 @ cef8ec2]` |
| `--idle-timeout DUR` | 120s | Watchdog total-freeze window | FREE | `[SRC ListOptionGroups.java:55-60 @ cef8ec2]` |
| `--no-progress-timeout DUR` | 10m | Zero-real-progress backstop | FREE | `[SRC ListOptionGroups.java:62-67 @ cef8ec2]` |
| `--max-duration DUR` | unset | Timebox → exit 124, resumable | FREE | `[SRC ListOptionGroups.java:48-53 @ cef8ec2]` |
| `--bearer-token-refresh-interval` | 45m | Token re-mint cadence | FREE | `[SRC BearerTokenOptions.java:54-58 @ cef8ec2]` |
| `--tune parquet.writers=N` | 3 (range 2..4) | Writer-pool size; FILE-kind forces 1 | FREE | `[SRC TuneOptions.java:27-28 @ cef8ec2]` |
| `--tune summary.interval=DUR` | `--progress-interval`, else 30s | Sidecar flush cadence | FREE | `[SRC TuneOptions.java:29-31 @ cef8ec2]` |
| `--tune sort.ignore-disk-check` | off | Skips the `--sort` disk guard | FREE (**the only resume-applicable tune key**) | `[SRC TuneOptions.java:32-33 @ cef8ec2]` |
| `--tune engine.readahead=on\|off` | **off** | Alias that appends `readahead=<v>` to `--engine-toggle` | FREE | `[SRC TuneOptions.java:23-24,151-160 @ cef8ec2]` |

Note the aliasing: the "supported" tune registry has one member that is really a diagnostic toggle in disguise `[SRC TuneOptions.java:151-160 @ cef8ec2]`.

**What `FREE` does and does not mean.** `FREE` is the `@Resume` classification — the option is not identity-bearing, so changing it between a run and its resume is never *refused* `[SRC ResumeRegistry.java:65-109 @ cef8ec2]` (§ 3.1). It does **not** mean the flag can be set on the resume command line: `swath resume` exposes only `[<dir>]`, `--bearer-token-command`, `--bearer-token-refresh-interval`, `--color`, `--[no-]progress`, `--[no-]stats`, `--tune=KEY=VALUE`, `-q`/`-v`/`-h`/`-V` `[SRC swath-cli/src/test/resources/help/swath-resume.txt @ cef8ec2]`. So `--concurrency`, `--object-listing-queue-size`, `--request-rate` and the three Parquet rotation knobs are `FREE` in the classification sense and simply **unreachable** on a resume; of the `--tune` keys only `sort.ignore-disk-check` is resume-applicable (row above). A benchmark that plans to vary any of them across a resume boundary cannot.

**Not tunable at all, and this is a finding for the benchmark:** page size (hard-coded 1000, § 2.1), every attempt/API timeout, retry counts, and `maxAttempts` `[SRC ConnectionOptions.java:187-198 @ cef8ec2]`, `[DOC docs/internals/contracts.md:1091]`.

**Flags the benchmark must sweep:** `--concurrency` (with the ceiling-not-setpoint caveat), `--request-rate`, `--object-listing-queue-size`, and — as a mode axis — `seed.mode ∈ {shallow, none}`.

### 3.3 Neither mode nor tunable

Connection: `--region`, `--profile`, `--no-sign-request`, `--endpoint-url`, `--force-path-style`, `--bearer-token-command`, `--requester-pays`, `--fetch-owner`. Filters: `--include/--exclude/--min-size/--max-size/--modified-since/--modified-until/--storage-class`. Observability: `--report`, `--trace`, `--metrics-endpoint`, `--no-metrics`, `--color`, `-v`, `-q`, `--stats`, `--progress`. Lifecycle: `--checkpoint`, `--restart`, `--force`/`--overwrite`.

Two borderline calls, both adjudicated in favour of "not a mode":

- **`--fetch-owner` is request-shape, not a mode.** It sets `FetchOwner=true` on every `ListObjectsV2` and populates `owner_id`/`owner_display_name` `[SRC ConnectionOptions.java:61-63 @ cef8ec2]`, `[SRC S3PageFetcher.java:171-173 @ cef8ec2]`, `[DOC docs/usage.md:450]` — but neither field is in the study's `normalize.sh` contract, so the *normalized* output contract is unchanged. One representative run, not a full mode.
- **Filters are post-listing.** "Filters apply after listing; they do not reduce API calls" `[DOC docs/usage.md:620]`, corroborated in source: they are applied in the fetch worker *after* the checkpoint commit and the in-range clamp, immediately before the batch is packed `[SRC .../WorkStealingScan.java:730-741 @ cef8ec2]`, with identical placement on the sequential producers `[SRC .../runtime/ScanProducer.java:57 @ cef8ec2]`, and nothing in the filter path touches the request `[SRC S3PageFetcher.java:180-186 @ cef8ec2]`. Two consequences worth recording: `--include`/`--exclude` use `Pattern.find()` — **substring, not anchored** — over the U+FFFD-lossy decode `[SRC .../filter/IncludeRegexFilter.java:114-116 @ cef8ec2]`; and **for verification runs, use no filters at all** — a filtered run cannot be checked against a full-scope manifest.

### 3.4 Positive-evidence absences — now confirmed live

Reader C settled four absence questions with positive source evidence rather than "I didn't find it". The `--help` probe **upgrades all four from source-read to observed**:

| Claimed-absent flag | Source evidence | Live confirmation |
|---|---|---|
| `--max-keys` | `int pageMax = 1000;` local, no `@Option` feeds it; the only `--max-keys` in the repo belongs to `swath-replay-server`'s `BenchCommand` `[SRC ListCommand.java:395 @ cef8ec2]`, `[SRC swath-replay-server/.../BenchCommand.java:47 @ cef8ec2]` | absent from the live usage block `[OBS docker run --network none … list --help]` |
| `--delimiter`, `--recursive` | `"--delimiter"` exists only in `BenchCommand` `[SRC BenchCommand.java:53 @ cef8ec2]`; `"--recursive"` appears nowhere. `ListCommand` hard-wires `ListingMode.OBJECTS` `[SRC ListCommand.java:640 @ cef8ec2]`, as does `ListRunner` `[SRC .../runtime/ListRunner.java:391,500,622 @ cef8ec2]` | absent `[OBS same]` |
| `--no-owner-split` | field exists with no `@Option` `[SRC EngineOptions.java:27 @ cef8ec2]`; a dedicated test asserts the spelling is rejected with `UnmatchedArgumentException` `[SRC EngineToggleCliValidationTest.java:67-72 @ cef8ec2]`. Supported spelling is `--engine-toggle owner_split=off` | absent `[OBS same]` |
| `--all-versions` | `ListingMode.VERSIONS` exists in the model, the SQLite CHECK admits it, `S3PageFetcher` branches on it — but **nothing can ever set it**: the sole `RunKey` construction passes the literal `OBJECTS` `[SRC ListCommand.java:640 @ cef8ec2]` and `S3PageFetcher` throws `UnsupportedOperationException` for VERSIONS `[SRC S3PageFetcher.java:161-163 @ cef8ec2]`. Docs concur: "Versioned listing (`ListObjectVersions`) is not implemented" `[DOC docs/operating.md:30-31]` | absent `[OBS same]` |

Also removed and asserted-rejected: `--metrics-export`, `--metrics-interval` `[SRC MetricsExportCliValidationTest.java:62-69 @ cef8ec2]`.

**No config-file surface exists.** Exhaustive grep for `.swathrc`/`swath.toml`/`swath.yaml`/"config file" across `docs/` and `swath-cli/src/main` returns nothing; configuration is flags + AWS-SDK env + `SWATH_OTLP_*` + `-D` JVM properties only `[SRC ListOptionGroups.java:115 @ cef8ec2]`, `[DOC docs/configuration.md:22-31,168-196]`.

**Command surface:** `swath list <s3-uri>` (the only listing verb), `swath resume <dir>`, `swath help`, plus two hidden commands — `swath dump-run <file.pageseg>` (read-only staging-segment inspector) `[SRC DumpRunCommand.java:31-32 @ cef8ec2]` and `swath completion` `[SRC App.java:141-143 @ cef8ec2]`. A bare `swath s3://bucket` is exit 2 with a `did you mean: swath list …` hint `[SRC App.java:156-171 @ cef8ec2]`.

---

## 4. How to run it properly

### 4.1 Quickstart (the shape actually used here)

The image entrypoint is `["java","-jar","/opt/swath/swath.jar"]` with `Cmd: null` `[SRC Dockerfile:97 @ cef8ec2]`, `[OBS docker inspect]`. **Appended argv therefore starts at the subcommand** — do not prepend `swath` or `java -jar`.

```sh
docker run --rm --pull=never --cap-drop ALL --security-opt no-new-privileges:true \
  -e AWS_EC2_METADATA_DISABLED=true -e TZ=UTC \
  ghcr.io/varveio/swath@sha256:ef1aca9ab473f133acceb5730ff88d52abaaa89e773801cdb62deff51f9909b0 \
  list s3://noaa-normals-pds/normals-hourly/ \
    --region us-east-1 --no-sign-request --format jsonl -v --concurrency 8
```

### 4.2 Anonymous access — and the region trap

`--no-sign-request` → `AnonymousCredentialsProvider`, top of a three-branch chain:

```java
private AwsCredentialsProvider resolveCredentials() {
    if (noSignRequest)   return AnonymousCredentialsProvider.create();
    if (profile != null) return ProfileCredentialsProvider.create(profile);
    return DefaultCredentialsProvider.builder().build();
}
```
`[SRC ConnectionOptions.java:248-256 @ cef8ec2]`. Precedence is `--no-sign-request` > `--profile` > SDK default chain; resume class `STICKY`. A fourth, orthogonal path sits above all three: `--bearer-token-command` replaces SigV4 signing entirely `[SRC ConnectionOptions.java:211-223 @ cef8ec2]`, `[DOC docs/usage.md:~490]`.

**The region trap is real and has two halves.**

*Half 1 — no region resolves ⇒ exit 2, even anonymously.* `--no-sign-request` has no effect on region resolution; they are independent code paths `[SRC ConnectionOptions.java:233-246 @ cef8ec2]`. A credential-starved container with no AWS config, no env, and only `--no-sign-request` **fails at exit 2 before the first request**. The invocation must carry `--region REGION`, or `AWS_REGION`/`AWS_DEFAULT_REGION`, or `--endpoint-url` (which short-circuits to `us-east-1`). `install.md`'s anonymous quickstarts omit all three `[DOC docs/install.md:15-18, 70, 82, 139]`; `faq.md` documents the failure correctly `[DOC docs/faq.md:3-14]`, but the copied quickstart does not. **This is the single most likely reason a first containerized run fails** — and it is why `--region us-east-1` is explicit in every invocation in this report.

*Half 2 — wrong region ⇒ fatal exit 1, no auto-retry.* A `301 PermanentRedirect` becomes a typed `RegionRedirectException extends ListingException`, deliberately never retried and never self-corrected because "the fetcher's client is long-lived and shared across every worker and thief thread" `[SRC error/RegionRedirectException.java:8-27 @ cef8ec2]`. The message carries `x-amz-bucket-region` `[SRC S3FaultClassifier.java:219-225 @ cef8ec2]`.

### 4.3 Project's own recommended configuration for large listings

There is no separate "large listing" recipe beyond the defaults and the tunables in § 3.2; `docs/configuration.md` and `docs/usage.md`'s flag/default tables match the golden help and the source field defaults on every entry checked. The project's own tuning guidance is diagnostic rather than prescriptive: `probe_latency[]` from a single `--report` run is stated to be enough to tell whether a budget is correctly sized `[DOC docs/internals/probe-budgets.md §5]`. `docs/performance.md` and the `swath-sim` harness exist and carry measured figures; **those are vendor self-reported and no number from them is transcribed into this report.**

There is **no hinted / two-pass workflow at this revision**: `seed.mode=hints` is declared, accepted by CLI validation, and throws at seed time `[SRC SeedStep.java:160-161 @ cef8ec2]`.

### 4.4 Footguns

1. **No region ⇒ exit 2** (§ 4.2). Always pass `--region`.
2. **`--concurrency` is a ceiling** (§ 2.5). Setting 64 does not mean 64 in-flight requests; the run starts at `min(4, N)` and the store decides the steady state.
3. **Raising `--concurrency` lengthens the seed phase**, since the descent is serial and its probe budget scales with worker count `[DOC docs/internals/algorithms.md §8]`, `[INFERRED from SRC .../HybridSeedPlanner.java:131-142, 362-377]`.
4. **UID 10001 write trap.** The image runs as `10001:10001` `[SRC Dockerfile:94 @ cef8ec2]`, `[OBS docker inspect]`, so a host output directory you own is not writable by the container. Upstream's guidance is `--user "$(id -u):$(id -g)"`, not `chmod 777` `[DOC docs/install.md:128-142]`.
5. **Off a TTY, progress is appended every 30 s**, so a short run looks silent `[DOC docs/install.md:128-149]`.
6. **`--trace` carries real key names on nearly every event** `[DOC docs/usage.md:614-618]`, and `--report` embeds `argv`, `config.target`, `config.filters`, `seed.decisions[].prefix`, and `slow_ranges[].lo/hi/cursor` `[DOC docs/metrics-and-observability.md §7]`. Both are redaction-relevant.
7. **`JAVA_OPTS`/`SWATH_OPTS` have no effect in the container** — those are read by the `installDist` launcher script, which the image does not contain. Use `JAVA_TOOL_OPTIONS` `[SRC Dockerfile:70-73 @ cef8ec2]`, `[DOC docs/packaging-and-docker.md:197-206]`.
8. **`--sort` is refused for every text format (exit 2)** and additionally requires a checkpoint and a directory dataset `[SRC ListCommand.java:824, 1519, 307, 1531 @ cef8ec2]`.
9. **Only a Parquet directory dataset is resumable** (§ 2.9). A `-o out.jsonl` run that dies cannot be resumed, and the resume path says so explicitly: "changing the resume invocation's `-o` cannot recover the already-committed prefix" `[SRC ListCommand.java:655-658, 1916-1930 @ cef8ec2]`.
10. **`--progress-interval` below 1 s is rejected, not clamped** `[SRC LivenessOptions.java:51-58 @ cef8ec2]`.

---

## 5. Output and observability

### 5.1 stdout is clean — verified by exhaustive grep

The only writer of fd 1 in the entire main source tree is the output sink `[SRC OutputOptions.java:488 @ cef8ec2]`. Logging is pinned to Logback's `System.err` console appender `[SRC CliLogging.java:16-17 @ cef8ec2]`, progress renders to fd 2 `[SRC ProgressDisplay.java:20-33 @ cef8ec2]`, the `--stats` block and the resolved-output echo go to `System.err` `[SRC ListCommand.java:280,387 @ cef8ec2]`, and `--report` only ever writes to a `Path` `[SRC .../JsonRunSummaryWriter.java:972-987 @ cef8ec2]`. **A normalizer needs no separation logic** — the one caveat being the TSV header line.

### 5.2 Format contracts

| Format | Header | Fields | Escaping | Faithful? |
|---|---|---|---|---|
| `jsonl` | none `[SRC JsonlFormatter.java:31-34 @ cef8ec2]` | named, **nullable fields OMITTED not null** `[SRC JsonlFormatter.java:84-95 @ cef8ec2]` | JSON, `\` itself escaped ⇒ **invertible** `[SRC output/Json.java:85-107 @ cef8ec2]` | best text format |
| `tsv` | **always**, `key size last_modified etag storage_class row_type` `[SRC TsvFormatter.java:23 @ cef8ec2]` | **six**, and `last_modified` sits *before* `etag` — not the harness order `[SRC TsvFormatter.java:40-55 @ cef8ec2]` | `\xHH` for `<0x20` and `0x7f`, **backslash itself NOT escaped** `[SRC ControlCharEscaper.java:21-49 @ cef8ec2]` | lossy, see below |
| `table` | none `[SRC AlignedFormatter.java:135-138 @ cef8ec2]` | size, time, key only — **no etag, no storage_class**; fixed widths 14/24 `[SRC AlignedFormatter.java:123-124,141-166 @ cef8ec2]` | same `\xHH` | lossy + fragile |
| `parquet` | — | canonical superset schema; `key` is BINARY **raw bytes** `[SRC .../parquet/ListEntryWriteSupport.java:44 @ cef8ec2]`, `[SRC .../ParquetSchema.java:26-42 @ cef8ec2]` | none | **the only byte-exact format** |

Default `--format auto` = `TABLE` on a TTY, `TSV` otherwise `[SRC .../output/OutputFormat.java:159-161 @ cef8ec2]`. The harness `docker create`s without `-t`, so an unset `--format` yields TSV — **pin it explicitly anyway.** In v1.0 only `ObjectEntry` rows are emitted `[DOC docs/internals/contracts.md §1.2]`, so every TSV row is six fully-populated fields with `row_type=OBJECT`.

**Two output findings that change how a study should treat this tool:**

1. **The TSV/table `\xHH` escaping is not invertible.** `ControlCharEscaper` escapes control bytes but does not escape the backslash, so a key literally containing the four characters `\x09` is byte-identical on the wire to a key containing a real TAB `[SRC ControlCharEscaper.java:21-49 @ cef8ec2]`. **An adapter must detect and refuse, never decode.** Escaping is also not bypassable at this revision: `--raw-output` has no `@Option` (it is a resume-restore artifact — *"new runs always escape text"*) `[SRC OutputOptions.java:89-90 @ cef8ec2]` and the CLI hardwires `escape = !output.rawOutput` `[SRC ListCommand.java:1456 @ cef8ec2]`.
2. **All three text formats are lossy for non-UTF-8 keys.** Every one renders the key via `KeyBytes.asString()` = `new String(raw, UTF_8)`, so invalid bytes become **U+FFFD irreversibly** `[SRC swath-model/.../KeyBytes.java:65-72 @ cef8ec2]`, confirmed by a comment explaining why lone surrogates can never reach the writer `[SRC .../output/CountingWriter.java:31-34 @ cef8ec2]`. Only the Parquet `key` column is byte-exact.

**Timestamps.** `Fields.isoMicros(long epochMicros)`: `0` → **empty string** (sentinel for missing, colliding with a real 1970-01-01T00:00:00Z); otherwise `DateTimeFormatter.ISO_INSTANT`, which emits **variable** precision — 0, 3, 6 or 9 fractional digits `[SRC .../output/Fields.java:18-26 @ cef8ec2]`. Because `S3PageFetcher.toEpochMicros` is `getEpochSecond()*1e6 + getNano()/1000` `[SRC S3PageFetcher.java:405-409 @ cef8ec2]` and S3's `LastModified` is second-granularity, the practical output is `YYYY-MM-DDTHH:MM:SSZ` — already the study's canonical shape. **Confirmed live**: the two listing runs produced `last_modified` values in `2025-11-24T20:33:44Z … 2026-07-22T13:20:38Z` with no fractional part `[OBS jsonl rows from the two runs in § 8.3]`. A sub-second-capable endpoint would emit `…SS.ssssssZ`, which the verifier rejects outright as an adapter-contract violation, so **the normalizer must strip a fractional part unconditionally** `[HARNESS verify-listing.sh:51, 65-70]`. Containers run `TZ=UTC` pinned, so a format printing local time with no offset marker is printing UTC by construction — here the formatter emits an explicit `Z` regardless.

### 5.3 `normalize.sh` contract per mode

The verifier invokes `normalize.sh <mode> <prefix>` with the captured payload on stdin and expects exactly 5 tab fields per row `[HARNESS verify-listing.sh:785-798]`; `-` for any field a mode does not expose, asserted only where non-`-` `[HARNESS verify-listing.sh:38-40, 843-847]`; etag compared case-insensitively and unquoted `[HARNESS verify-listing.sh:86]`; size compared as a **string** `[HARNESS verify-listing.sh:85]`; `LC_ALL=C` exported `[HARNESS verify-listing.sh:24]`. `$2` (prefix) is accepted and **unused — swath emits absolute keys.**

| mode | key | size | etag | mtime | storage_class | notes |
|---|---|---|---|---|---|---|
| `jsonl` | ✔ | ✔ | ✔ | ✔ | ✔ | key on field **names**, never position (nullables omitted) |
| `tsv` | ✔ | ✔ | ✔ | ✔ | ✔ | drop header; reorder cols 3↔4; die on `\xHH` |
| `table` | ✔ | ✔ | `-` | ✔ | `-` | fixed-width slice `size[1..14] "  " time[17..40] "  " key[43..]`; **assert the separator spaces and die otherwise** — `pad()` appends unpadded when `gap <= 0` `[SRC AlignedFormatter.java:168-179 @ cef8ec2]` |
| `parquet` | — | — | — | — | — | not stdout-capturable (§ 5.5) |

Design rules the adapter must follow, stated so they are auditable: never un-escape `\xHH`; strip fractional seconds unconditionally; empty ⇒ `-`; use `jq … join("\t")` never `@tsv` (jq's `@tsv` escapes backslash as `\\`); guard against a key containing TAB or newline and die — contract v2 cannot represent one, which is a *contract* limit, not a swath defect. Reader D's full `normalize.sh` implementation is in `research/reader-D-output.md §7` and is the intended deliverable, verbatim.

**ETag note:** swath already stores ETags with quotes stripped `[SRC swath-model/.../ObjectEntry.java:13 @ cef8ec2]` — strip defensively anyway.

### 5.4 Counters, logs, and metrics

**`cost.api_calls` is trustworthy and has exactly one increment site.** `metrics.recordApiCall()` fires immediately *before* `s3.listObjectsV2(...)` `[SRC S3PageFetcher.java:213-214 @ cef8ec2]`, and the reported value is `Math.round(counterTotal("swath.api.calls"))` — the sum across every `strategy` tag series `[SRC .../RunMetrics.java:2169 @ cef8ec2]`, `[SRC .../JsonRunSummaryWriter.java:588 @ cef8ec2]`. Precise semantics:

- Counts **attempts issued, not successes** — timeouts, 503s and connection failures all count.
- Counts **every request class** — worker pages, seed probes, thief pivot probes, `delimiter=/` structure probes; all route through the one fetcher.
- **One increment ≈ one HTTP request**, because SDK-internal retry is disabled (§ 2.6); each swath-level retry re-enters `fetchPage` and increments again `[INFERRED, from the single call site + maxAttempts=1]`.
- **Nothing is hidden**: a repo-wide scan of the S3 module found only `s3.listObjectsV2(...)` and a local `serviceClientConfiguration()` — no `HeadBucket`, no `GetBucketLocation`, no `ListBuckets`. Region redirect is detected from a *failing* `ListObjectsV2`, so it is counted, not hidden `[SRC S3FaultClassifier.java:219-225 @ cef8ec2]`.
- **Use `cost.api_calls`, never a single `meters[]` row** — if the strategy is unknown at the first call the counter fragments into `strategy="unknown"` plus the real strategy and each series undercounts `[DOC docs/metrics-and-observability.md §1]`.
- **On a resume it counts only this process's calls** `[DOC …§2]`.
- `cost_usd = api_calls × 0.005 / 1000`, a hardcoded `us-east-1` reference rate, withheld under `--endpoint-url` `[SRC .../RunMetrics.java:2654-2656 @ cef8ec2]`.

**Adjudication (readers B and D).** B describes two counters — a private `AtomicLong` exposed as `S3PageFetcher.apiCalls()` `[SRC S3PageFetcher.java:82, 149-152, 164 @ cef8ec2]` and the tagged `swath.api.calls` meter `[SRC S3PageFetcher.java:214 @ cef8ec2]`. D traces `cost.api_calls` to the meter sum. **D is the one that describes production.** The `apiCalls()` accessor's own javadoc claims it "drives the cost line and the INT-8 efficiency guard", but it has **no caller in any `src/main`** — a repo-wide grep finds it only in `swath-core/src/test` (mock-fetcher assertions, `ApiCallBudget.assertWithinInt8Budget`) `[SRC S3PageFetcher.java:149-152 @ cef8ec2]`, `[INFERRED, from a repo-wide grep for the accessor across every src/main tree]`. The cost line comes from `summary.apiCalls()`, i.e. the summed `swath.api.calls` meter `[SRC .../JsonRunSummaryWriter.java:588 @ cef8ec2]`, `[SRC .../RunMetrics.java:2169 @ cef8ec2]`. The two are not in conflict as *measurements* — both increment pre-call, within lines of each other, in the same method, so both include retried attempts — but only one is wired. **The value a study should quote is `cost.api_calls` / the `api_calls=` field of `list_run_summary`,** which is the meter sum. (The accessor's javadoc is one more instance of the § 9.2 drift pattern; it is not counted in that set because it misdescribes a test seam rather than a user-facing surface.)

**Getting the count without a writable path.** `--report` writes to a container-local `Path` `[SRC JsonRunSummaryWriter.java:972-987 @ cef8ec2]` and is therefore as uncapturable as a Parquet dataset under a harness that mounts nothing. `-v` emits `list_run_summary run_id=… api_calls=… cost_usd=…` on **stderr** from the same `RunSummary` `[SRC .../runtime/ListRunner.java:1357 @ cef8ec2]` — that is the clean scrape target (the `--stats` block renders the same number with thousands separators, so it is the worse target `[SRC .../SummaryRenderer.java:205-206 @ cef8ec2]`), and it is how the API-call figures in § 8 were obtained `[OBS stderr of the two listing runs]`.

**Per-request DEBUG lines at `-vv`**, all under logger `io.varve.swath.store.s3.S3PageFetcher` (the fault classifier deliberately logs under that name so the operational filter surface stays stable `[SRC S3FaultClassifier.java:44-50 @ cef8ec2]`): `s3_page_fetched` (`run_id worker_id node_id bucket prefix start_after keys common_prefixes truncated status latency_ms`) `[SRC S3PageFetcher.java:324-330 @ cef8ec2]`; `slow_probe_exemplar` (`call_class … escalation_level`) `[SRC S3PageFetcher.java:583-588 @ cef8ec2]`; `s3_timeout` / `s3_abort` / `s3_network_error` / `s3_socket_closure` / `s3_throttle` / `s3_server_error` `[SRC S3FaultClassifier.java:112-129, 160-161, 194-196, 209-211, 275-277 @ cef8ec2]`; `s3_error` and `s3_oversized_page` at **WARN** `[SRC S3FaultClassifier.java:177,256 @ cef8ec2]`, `[SRC S3PageFetcher.java:291-292 @ cef8ec2]`; `range_page_fetched` under `…engine.RangeScanner` `[SRC RangeScanner.java:221-224 @ cef8ec2]`; `list_first_request_issued`/`list_first_page_returned` at **INFO** `[SRC .../store/FirstRequestMarkerFetcher.java:43,47 @ cef8ec2]`.

Three caveats for a capture harness: `s3_page_fetched` does **not** print `call_class` (only the fault and exemplar lines do); `slow_probe_exemplar` is **rate-limited** to the first 20 then powers of two, so the log undercounts probe slowness *by design* while the `PROBE.slow_<call_class>` counter does not `[SRC S3PageFetcher.java:90-99, 565-582 @ cef8ec2]`; and **no verbosity level produces SDK wire logs** — `CliLogging.configure()` sets the level on the `io.varve.swath` logger only, while the shipped `logback.xml` pins `software.amazon.awssdk` to ERROR `[SRC CliLogging.java:88-96 @ cef8ec2]`, `[SRC swath-core/src/main/resources/logback.xml @ cef8ec2]`, `[INFERRED from those two files]`.

Micrometer's richest per-request surface is `swath.fetch.latency.phase{call_class, phase}` with `call_class ∈ {worker_page, pivot_probe, structure_probe}` and `phase ∈ {connect_acquire, ttfb, sdk_unmarshal, total, response_parse}` `[SRC .../RunMetrics.java:311-350, 1056-1071 @ cef8ec2]`. `sdk_unmarshal` is **derived** as `TIME_TO_LAST_BYTE − TIME_TO_FIRST_BYTE` because `UNMARSHALLING_DURATION` is never reported for S3 `[SRC .../S3CallClassLatencyPublisher.java:140-161 @ cef8ec2]`; three of the five phases are best-effort (`-1` means "the SDK didn't report it" and is skipped, not zeroed) `[SRC .../S3CallClassLatencyPublisher.java:51-54 @ cef8ec2]`; `total` is swath's own wall-clock and is always available `[SRC S3PageFetcher.java:270-280 @ cef8ec2]`; and **failure-path samples are recorded too**, explicitly so a timeout storm is not survivorship-biased out of the distribution `[SRC S3PageFetcher.java:217-225, 348-362 @ cef8ec2]`. There is a deliberate omission of any dominant-phase counter, because `connect_acquire` and `ttfb` may partially overlap and the SDK does not define the boundary `[SRC S3PageFetcher.java:549-560 @ cef8ec2]`. These publishers attach only when `metrics != null` in `S3ClientFactory.create` `[SRC .../S3ClientFactory.java:113-120 @ cef8ec2]`; production passes `ctx.metrics()` `[SRC ListCommand.java:848 @ cef8ec2]`.

`--trace PATH` is a JSONL flight recorder, stream-appended (not atomic-renamed) so a crash leaves a readable prefix — **the final line may be torn**, so parse line-by-line and drop a trailing fragment `[SRC .../observability/JsonlTraceSink.java:22-37 @ cef8ec2]`. `--metrics-endpoint URL` swaps `SimpleMeterRegistry` for `OtlpMeterRegistry` and needs network egress `[SRC .../observability/MeterRegistries.java:26-60 @ cef8ec2]` — leave it off under `--network none`.

### 5.5 What this harness can and cannot capture

**Adjudication (reader D vs readers C/E), and it is the most consequential scoping call in this report.** C's mode inventory (M1–M10) describes *the tool*. D's capturability analysis describes *what this study's harness can evidence*. They do not conflict; they must both be stated, because reporting M4/M5/M6 as "untested" without the reason would misattribute a harness limitation to the tool.

The harness never bind-mounts anything — `DOCKER_CMD` contains no `-v`/`--mount` `[HARNESS smoke-run.sh:326-345]` — and evidence is collected exclusively via `docker logs` into `stdout.raw`/`stderr.raw` `[HARNESS smoke-run.sh:571]`; `run.meta` records only `stdout_path`/`stderr_path` `[HARNESS smoke-run.sh:718-722]`. Parquet is a path-based sink that refuses stdout outright ("Parquet output requires `-o <dir>`") `[SRC OutputOptions.java:460-463 @ cef8ec2]` and `Formatters.text()` throws for `PARQUET` `[SRC .../output/Formatters.java:134-142 @ cef8ec2]`. Payload caps are 64 MiB per stream `[HARNESS smoke-run.sh:609-625]` — a 148,917-row JSONL run is far under.

Therefore, under the harness as it stands:

- **Capturable and verifiable:** M1 `table`, M2 `tsv`, M3 `jsonl` — to stdout only.
- **Not capturable without a harness change:** M4 and M5 (Parquet, directory sinks), M6 `--sort` (Parquet-only by construction), and any `-o <file>` text run. These should be recorded as *"not verified — directory/file output; harness captures stdout only"*, not as "untested".
- **Capturable in principle, unexercised here:** M7 `resume` (needs a durable checkpoint, hence a directory dataset, hence a mount), M8/M9 `seed.mode` (fully stdout-capturable — M8 is the default and was exercised; M9 was not), M10 `hints` (one exit-2 capability probe, stdout-capturable).

Closing the Parquet gap needs either a harness bind-mount plus a post-run archive step into the receipt dir, or an out-of-harness run whose dataset is normalized separately. Read-back sketch for whoever does it: `data/` is pure Parquet so `read_parquet('<root>/data/*.parquet')` is a safe glob; `key` is BINARY so it must be emitted as raw bytes, not hex/base64; `last_modified` is TIMESTAMP(MICROS,UTC); guard on `_SUCCESS` existing before trusting the dataset `[SRC .../DatasetLayout.java:32-69 @ cef8ec2]`, `[SRC .../ParquetFormatter.java:310-313 @ cef8ec2]`, `[SRC .../ParquetSchema.java:26-42 @ cef8ec2]`, `[INFERRED, not executed]`. Note also `.swath-state.json` is internal resume identity and never consumer-facing `[SRC .../Manifest.java:39-40, 101-110 @ cef8ec2]`.

---

## 6. Failure surface

### 6.1 Exit codes

| Code | Meaning | Source |
|---|---|---|
| **0** | Success, empty result, already-complete resume, **or a broken pipe** (stdout closed by e.g. `head`) — a *clean* exit | `[SRC ExitCodes.java:24,95-97,135-137 @ cef8ec2]` |
| **1** | Unrecoverable/unclassified: `ListingException` (incl. `AccessDenied`, `RegionRedirect`, `ProtocolViolation`), `OutputException`, `CheckpointException`, or any unmapped throwable | `[SRC ExitCodes.java:25,100 @ cef8ec2]` |
| **2** | Bad args, invalid URI, invalid config, **or a deliberate guarded refusal** (unfinished/foreign output dir, extension/format mismatch, changed resume identity, a directory bucket) | `[SRC ExitCodes.java:27-34 @ cef8ec2]` |
| **75** | `EX_TEMPFAIL` — cooperative `stop_reason=stuck` unwound to a **resumable partial**. Three sources: watchdog escalation, transient-retry cap exhaustion, bare seed interrupt; `error_class ∈ {stuck_api_timeouts, stuck_throttle, stuck_unknown}` | `[SRC ExitCodes.java:36-49 @ cef8ec2]`, `[SRC ListCommand.java:541-556 @ cef8ec2]` |
| **124** | `--max-duration` elapsed, graceful stop, resumable; GNU `timeout(1)` convention | `[SRC ExitCodes.java:51-58 @ cef8ec2]` |
| **130 / 143** | SIGINT / SIGTERM, kept distinct so a supervisor stop is distinguishable from Ctrl-C. Resumable | `[SRC ExitCodes.java:60-73 @ cef8ec2]` |

Two subtleties the published tables do not surface: **`ProtocolViolationException` outranks every cancellation code** — `forThrowable` searches the throwable, its cause chain *and* suppressed exceptions first, because a violation discovered during unwind is attached as suppressed while the cancel stays primary, and every cancellation code claims "resumable partial", which a protocol-violated run is not `[SRC ExitCodes.java:82-89,103-128 @ cef8ec2]`; and the exception hierarchy is `sealed`, making the error→exit mapping compiler-checked for exhaustiveness `[SRC error/SwathException.java:13-16 @ cef8ec2]`. `faq.md` and `usage.md` agree with source on all seven codes `[DOC docs/faq.md:32-42]`, `[DOC docs/usage.md:783-795]`.

### 6.2 Protocol-violation defences — and the two gaps

Three defences exist:

- **Oversized page** (store): if `contents + commonPrefixes > maxKeys`, the page is refused — `steal_reason{FATAL,oversized_page}`, WARN `s3_oversized_page`, throw `ProtocolViolationException` `[SRC S3PageFetcher.java:283-295 @ cef8ec2]`. It is a plain `ListingException` (exit 1), deliberately **not** a `ThrottleException`, so it is never retried — the retry loops catch only `ThrottleException` `[SRC error/ProtocolViolationException.java:13-23 @ cef8ec2]`. Reasoning stated in source: truncating would silently drop keys, retrying would spin, repairing locally would corrupt. Counts are named in the message so a false positive against a third-party implementation is diagnosable from one line.
- **Truncated-but-empty page** (engine) → `ListingException("truncated page returned no keys <= hi")` `[SRC RangeScanner.java:269-274 @ cef8ec2]`.
- **Stuck / looping continuation** (engine) → `ListingException("no forward progress (stuck listing)")` when `lastKey <= startAfter` `[SRC RangeScanner.java:275-280 @ cef8ec2]`.

Both engine guards run **before** the consumer callback, so a broken page is never committed or emitted `[SRC RangeScanner.java:265-268 @ cef8ec2]`, and `truncated` is documented as the authoritative "more pages" signal, never inferred from `entries.size() == maxKeys` `[SRC .../store/ListPage.java:13-19 @ cef8ec2]`.

**The two gaps.** The defences cover *quantity*, *pagination liveness*, and *synthesis safety*. They do **not** cover (a) **intra-page ordering** (§ 2.4) or (b) the **encoding contract** (§ 6.3). An endpoint that over-serves or stalls is refused loudly; **an endpoint that lies about encoding produces wrong output silently.**

### 6.3 Key fidelity, and the sharpest correctness edge in the tool

swath sets `encoding-type=url` on every request `[SRC S3PageFetcher.java:169 @ cef8ec2]` and does **no decoding of its own**: `toEntry` converts the SDK's already-decoded `o.key()` straight to bytes with a plain UTF-8 encode, and the source is emphatic that this is not a second percent-decode `[SRC S3PageFetcher.java:368-377 @ cef8ec2]`. Common prefixes take the identical path `[SRC S3PageFetcher.java:307-310 @ cef8ec2]`. Outbound, `toRequestParam(byte[]) = new String(raw, UTF_8)`, byte-exact **iff `raw` is valid UTF-8** `[SRC S3PageFetcher.java:412-433 @ cef8ec2]` — which the javadoc argues holds by construction for both bound kinds.

The decode is the **SDK's** `DecodeUrlEncodedResponseInterceptor`, and reader B disassembled the pinned jar to check it:

- It decodes `delimiter`, `prefix`, `startAfter`, `contents[].key`, `commonPrefixes[].prefix` — and **not** `nextContinuationToken` `[3P-DEP software.amazon.awssdk:s3:2.31.78 DecodeUrlEncodedResponseInterceptor#modifyListObjectsV2Response]`.
- Every decode goes through `SdkHttpUtils.urlDecode` = `java.net.URLDecoder.decode(s, "UTF-8")` `[3P-DEP software.amazon.awssdk:utils:2.31.78 SdkHttpUtils#urlDecode]`.
- **The interceptor is gated, not unconditional**: `shouldHandle` reads the *response's* `EncodingType` and returns true only on a case-sensitive exact match on `"url"` `[3P-DEP …s3:2.31.78 DecodeUrlEncodedResponseInterceptor#shouldHandle]`.

That last point **contradicts swath's own documentation**, which states the decode "happens regardless of `encoding-type`, since it is unconditional in the SDK, not driven by the request" `[DOC docs/internals/s3-implementation-compatibility.md:18-21]`. `S3PageFetcher` never inspects `resp.encodingType()`; the only `EncodingType` reference in the main source tree is the request-side one `[SRC S3PageFetcher.java:36,169 @ cef8ec2]`.

Consequences, per character class:

| input | survives byte-exactly | basis |
|---|---|---|
| non-ASCII (3-byte, 4-byte/supplementary) | **Yes** against a conforming endpoint | `[SRC swath-s3/src/test/.../S3PageFetcherKeyDecodeTest.java:133-146 @ cef8ec2]` |
| literal `%XX`, trailing `%`, incomplete `%2` | **Yes** — no second decode | `[SRC …S3PageFetcherKeyDecodeTest.java:47-131 @ cef8ec2]` |
| real space `0x20` | **Yes** | matrix case |
| literal `+` `0x2B` | **Conditional** | `[SRC …PercentEchoLocalStackIT.java:110-148 @ cef8ec2]` |
| control bytes | **Yes** through the fetcher; escaped in logs and in tsv/table output | matrix case + `[SRC S3PageFetcher.java:444-446 @ cef8ec2]` |

**The `+` caveat.** `URLDecoder.decode` maps `+` → space, so a literal `+` in a key survives **only if the endpoint percent-encodes it as `%2B`**. The **tested LocalStack build** does: the IT stores `pct/plus+sign+key` and `pct/x+y%20z` and asserts a byte-exact round trip through the production fetcher `[SRC …PercentEchoLocalStackIT.java:110-148 @ cef8ec2]`. That **real AWS S3** also percent-encodes `+` under `encoding-type=url` is assumed here and is **not** established by that anchor or by any other test in-tree — no test in this repository runs against real S3 `[INFERRED]`. A hypothetical S3-compatible endpoint that echoed `+` literally while setting `<EncodingType>url</EncodingType>` would **silently corrupt every such key to a space, with no error**; symmetrically, an endpoint that percent-encodes but omits or misspells the `<EncodingType>` echo would fail `shouldHandle`, skip the decode, and swath would emit the percent-encoded form as if it were the key `[INFERRED — from URLDecoder's documented +-to-space rule confirmed in the pinned SdkHttpUtils#urlDecode bytecode, plus the absence of any swath-side check]`. Neither case is covered by any test found.

Two documented endpoint deviations, both correctly framed by upstream as endpoint conformance gaps rather than swath defects:

1. **Verbatim-echo crash.** An endpoint that echoes `Prefix`/`StartAfter` verbatim returns a lone/trailing `%`, which `URLDecoder` rejects with `IllegalArgumentException: URLDecoder: Incomplete trailing escape (%) pattern`, surfacing as an SDK response-unmarshalling failure that aborts the whole listing rather than any error swath's retry logic can reason about `[DOC docs/internals/s3-implementation-compatibility.md:26-36]`, positive control at `[SRC …PercentEchoLocalStackIT.java:82-106 @ cef8ec2]`. swath's mitigation is to exclude `0x25`/`U+0025` from every code point it will ever synthesize `[DOC …:46-58]` — which protects invented bounds only; a **user-supplied prefix ending in a lone `%` can still crash**, and upstream states it will not mangle user input to work around it `[DOC …:71-78]`. The tested MinIO build is unaffected.
2. **Double-decoded `start-after` → silent under-count.** LocalStack decodes `start-after` one extra time; a cursor that is itself a real key containing `%25` becomes `%`, sorting past the remaining `%25…` keys and silently skipping them — `errors=0`, `quiescence_reached` logged, keys simply missing `[DOC …:93-135]`, pinned as a raw-SDK positive control at `[SRC …PercentEchoLocalStackIT.java:150-193 @ cef8ec2]`. Real S3 and MinIO decode once. **This is the failure mode a study should watch for**: a wrong answer with a clean exit and no warning.

**Bottom line.** Against a conforming endpoint the source *guarantees* a byte-exact round trip except for those two documented gaps, and the guarantee rests on one line each way plus the SDK doing the decode. Against a non-conforming endpoint swath *attempts* nothing — it has no validation of the decode contract at all.

### 6.4 Liveness and what actually kills a stuck run

The watchdog is explicitly the **secondary** guarantee; the SDK's 60 s `apiCallTimeout` is the primary unblock `[SRC .../LivenessWatchdog.java:21-26 @ cef8ec2]`, `[SRC S3Config.java:69-74 @ cef8ec2]`. Two independent tripwires, each with its own clock, rearmed only while HEALTHY `[SRC LivenessWatchdog.java:219-246 @ cef8ec2]`: total freeze on `progressSignal()` (folds in throttle/retry activity), default **120 s**, flag `--idle-timeout`; and zero-real-progress on `realProgressSignal()` (committed work only), default **10 min**, flag `--no-progress-timeout` `[SRC LivenessWatchdog.java:137-144 @ cef8ec2]`, `[SRC ListOptionGroups.java:55-67 @ cef8ec2]`. Disabling both (`0`/`none`/`off` on each) disarms the watchdog and the CLI then selects `RetryPolicy.BOUNDED` `[SRC LivenessWatchdog.java:156-169, 343-351 @ cef8ec2]`.

**Sustained 503s specifically.** Voting throttles retry unbounded on both worker and probe paths, and because they also reset `transientRetries`, a permanently-503ing endpoint can **never** trip the 8-retry cap `[SRC GaugedFetcher.java:168-181 @ cef8ec2]`. What ends such a run is the *second* tripwire: retry activity keeps `progressSignal` climbing so the 120 s idle wire is continually re-armed, while `realProgressSignal` stays flat → `list_no_progress_abort` → cooperative STUCK → exit 75 `[SRC LivenessWatchdog.java:137-153, 224-245 @ cef8ec2]`. **A study that expects a throttled run to die in 120 s will wait 10 minutes.**

Escalation ladder, time-driven once tripping begins (progress is ignored after the first rung, deliberately, so a dribble cannot postpone halt forever) `[SRC LivenessWatchdog.java:210-218 @ cef8ec2]`: cooperative cancel (first-writer-wins so a prior `max_duration`/signal reason keeps its attribution) → after 10 s, forensic dump (`list_stuck_forensics`, one `list_stuck_thread` per thread with `top_frame`) then interrupt → after a further 60 s, `Runtime.halt(75)` plus a `list_stuck_summary` stderr line that exists precisely because halt bypasses the JSON summary finalizer `[SRC LivenessWatchdog.java:263-341, 426-441 @ cef8ec2]`.

**Limits the source itself states**, worth carrying because they bound what "liveness" means here: `Thread.getAllStackTraces()` sees only **platform** threads, so a virtual thread's stack is invisible and interrupting a carrier does not interrupt the mounted virtual thread — step 3 (`halt`) is the real guarantee for a vthread wedged in a native socket read, and step 2 genuinely helps only the platform-thread lanes `[SRC LivenessWatchdog.java:36-49, 362-381 @ cef8ec2]`; SDK connection-pool stats are deliberately absent from the forensic dump (no reflection); and a `--sort` finalize whose single `fsync` exceeds the idle window can false-trip `[SRC LivenessWatchdog.java:52-60 @ cef8ec2]`.

Wall-clock arithmetic over the constants `[INFERRED — arithmetic, not observed]`: a worker page wedged in pure attempt-timeouts spends ≈`10 + 20 + 40k` s of attempt time plus ≈8 s expected jitter over the first 8 retries — roughly **5 minutes** to reach the cap, after which `RIDE_OUT` continues indefinitely at 40 s/attempt with backoff in [0, 15 s]. A pivot probe wedged the same way gives up in **≈9–10 s** (cap = 1).

### 6.5 Memory growth and interruption

Memory is bounded by configured knobs rather than object count for the text/stdout modes (§ 2.8). The two documented `O(N)` growth paths — Parquet `files[]` re-serialization (`O(parts²)` cumulative work) and `--sort` staging metadata (`O(segments)`) — apply only to the Parquet modes `[DOC contracts.md §4.1, §7.2]`. For `--sort`, **disk, not heap, is the real risk**: peak staging is ~2× the final compressed output, enforced by a startup pre-check plus a periodic runtime guard that calls `Runtime.halt` `[SRC .../sort/SortDiskGuard.java:22-58 @ cef8ec2]`.

**None of this is settled by the runs in § 8, and it cannot be** — memory cliffs and OOM behaviour are scale-dependent. Routed to § 10.

Interruption is well-covered by construction: cancellation codes 75/124/130/143 all denote resumable partials, the broken-pipe path marks FAILED *without* the fatal flag so a truncated stdout run stays resumable, and a resume is refused only on `status == FAILED && fatal_error` — deliberately narrower than `status == FAILED` `[SRC ListCommand.java:768-776 @ cef8ec2]`, `[SRC .../RunMeta.java:88-99 @ cef8ec2]`.

---

## 7. Container

### 7.1 What image, and why

**Upstream's own published image, pinned by index digest.**

```
ghcr.io/varveio/swath@sha256:ef1aca9ab473f133acceb5730ff88d52abaaa89e773801cdb62deff51f9909b0
```

It is public and anonymously pullable (verified with **no `docker login`**) `[OBS anonymous ghcr.io token + manifest fetch]`, cosign-keyless-signed (the `sha256-ef1aca….sig` tag returns 200 with a `application/vnd.dev.cosign.simplesigning.v1+json` layer), and carries two BuildKit attestation manifests (SLSA provenance + SBOM from `provenance: mode=max` / `sbom: true`) `[3P ghcr.io registry API]`. GHCR does not serve the OCI `/referrers/` API (404 `MANIFEST_UNKNOWN`), so the GitHub build attestation is verified via `gh attestation verify oci://…` rather than referrers `[3P ghcr.io]`.

The binding to source is not a coincidence of tagging: both per-arch config blobs carry `org.opencontainers.image.revision = cef8ec24a74ffae14ee6a9462e4b7f6c334fbc32` and `image.version = 0.2.0` `[OBS docker inspect / registry config blob]`, and the image's jar is the **exact tested uber-jar** promoted from the release `build` job via a BuildKit `--build-context build=promote` override of the compile-from-source stage, checksum-verified before use `[SRC .github/workflows/release.yml:185 @ cef8ec2]`, `[SRC scripts/ci/verify-container-promotion.sh:22-40 @ cef8ec2]`. The publish path was push-by-digest untagged → deep container smoke against that digest → `imagetools create` the tags onto the smoked digest → cosign sign → attest → draft release → self-verify (`sha256sum --check`, `cosign verify-blob`, `cosign verify`, `gh attestation verify`) → un-draft, all 24 steps success `[SRC .github/workflows/release.yml:179-341 @ cef8ec2]`, `[3P run 30746967874 job publish]`.

**Tag trap:** the image tag is **`0.2.0`**, no `v`. `v0.2.0` is a 404 `[OBS manifest fetch → manifest unknown]`. It follows naturally from the git tag being `v`-prefixed and will catch anyone who copies the git tag. A near-equivalent fallback exists and is *not* needed: `ghcr.io/varveio/swath:main` = `sha-b521167` differs from `cef8ec2` by one line (`version=0.2.0` → `0.2.1-SNAPSHOT`) `[3P git diff cef8ec2 b521167]`.

### 7.2 Architecture matrix (deliverable)

| Channel | linux/amd64 | linux/arm64 | Evidence / caveat |
|---|---|---|---|
| **Upstream GHCR image `0.2.0`** | ✅ native | ✅ native | Both present in the OCI index with real per-arch digests `[OBS registry manifest fetch]` |
| **Uber-jar** `swath-0.2.0.jar` | ✅ | ✅ | Arch-neutral bytecode; the two native-code deps (`sqlite-jdbc`, `zstd-jni`) bundle libraries for every arch and select at runtime `[SRC Dockerfile:12-14 @ cef8ec2]`. Needs a **JDK 25** runtime, no `--enable-preview` `[DOC docs/packaging-and-docker.md:24-28]` |
| **installDist / distZip / distTar** | ✅ | ✅ | Same classes plus a Gradle launcher script; explicitly "not native per-platform binaries" `[DOC docs/install.md:43-45]` |
| **Source build** | ✅ | ✅ | Whatever arch the host JDK 25 is; the Dockerfile build stage is pinned to `$BUILDPLATFORM` so it compiles once natively `[SRC Dockerfile:11-16,34 @ cef8ec2]` |

**Both architectures are native across every channel**, so the benchmark phase's common-denominator choice is unconstrained by swath. The "RUN-free runtime stage ⇒ no QEMU" property is confirmed three ways: the runtime stage contains only `FROM`, four `COPY --from=build --chown`, `USER`, `WORKDIR`, `ENTRYPOINT` — **zero `RUN`** `[SRC Dockerfile:87-97 @ cef8ec2]`; `docker-check` builds both platforms on an amd64 runner with **no `docker/setup-qemu-action` anywhere in the repo**, and the comment says this is deliberately a guard (adding a `RUN` would make the arm64 build start needing QEMU and fail) `[SRC .github/workflows/ci.yml:214-218,229-238 @ cef8ec2]`; and the release publish job does the same `[SRC .github/workflows/release.yml:186 @ cef8ec2]`.

**Caveat carried forward:** arm64 is validated to *build* but is **never runtime-smoked upstream**. `docker-publish` says so outright `[SRC .github/workflows/ci.yml:382-384 @ cef8ec2]` and the release smoke runs `docker run` on an amd64 runner `[SRC .github/workflows/release.yml:210-214 @ cef8ec2]`. Risk is low (identical arch-neutral jar on a stock arm64 JRE) but non-zero for the `sqlite-jdbc` / `zstd-jni` native extraction paths — which are precisely what a smoke exists to cover `[INFERRED]`. **The runs in § 8 only partially close this** (see § 8.6).

### 7.3 What smoke actually ran on

**Native arm64, no emulation.** Box: aarch64, 8 cores, 31 GB RAM, Linux 7.0.5-orbstack, Docker 29.4.0; the arm64 manifest was pulled and run directly `[OBS uname/docker info + docker run]`. `arch=arm64`, `emulated=no`.

Runtime shape, confirmed in the pulled image: entrypoint `["java","-jar","/opt/swath/swath.jar"]`, `Cmd: null`, `User "10001:10001"`, `WorkDir /opt/swath` `[OBS docker inspect]`, matching the Dockerfile `[SRC Dockerfile:94-97 @ cef8ec2]`. The numeric UID with no named user is deliberate — it keeps the stage `RUN`-free (no `useradd`), lets Kubernetes verify `runAsNonRoot: true` at admission, and works under OpenShift's arbitrary-UID model; 10001 is high to avoid host-UID collisions `[SRC Dockerfile:75-83 @ cef8ec2]`. `java` is PID 1 in exec form so SIGTERM/SIGINT reach the JVM directly; no init shim because swath spawns no child processes `[SRC Dockerfile:68-70 @ cef8ec2]`. No `VOLUME`, no `EXPOSE` — an output directory must be bind-mounted by the caller `[OBS docker inspect]`.

**Read-only rootfs is untested by upstream.** `sqlite-jdbc` extracts a native library at runtime (the stated reason shading does no `relocate(...)`) `[SRC swath-cli/build.gradle.kts:161-163 @ cef8ec2]`, targeting `java.io.tmpdir` with nothing in the repo overriding it, so `--read-only` would need `--tmpfs /tmp`. Grep for `read-only`/`readOnlyRootFilesystem`/`tmpfs` across `docs/*.md` returns nothing `[INFERRED, from those anchors]`.

### 7.4 Build route (not needed, recorded for completeness)

`docker build -t swath:dev .` from the repo root; multi-arch via `just docker-build` → `docker buildx build --platform linux/amd64,linux/arm64` with an isolated `DOCKER_CONFIG` to dodge devcontainer credential-helper breakage `[SRC justfile:69-73 @ cef8ec2]`. **Self-contained**: the build stage copies source and runs `./gradlew --no-daemon :swath-cli:shadowJar` itself `[SRC Dockerfile:57-60 @ cef8ec2]`; only CI substitutes the promoted jar `[SRC Dockerfile:18-27 @ cef8ec2]`. Both `FROM`s are digest-pinned — `eclipse-temurin:25-jdk-noble@sha256:3eb81ed94d8c1a34422f19f8188548bdf02cae69c91d0328afdbb7abed90f617` forced to `$BUILDPLATFORM` `[SRC Dockerfile:34 @ cef8ec2]` and `eclipse-temurin:25-jre-noble@sha256:2f1da100788559b397bcf48c736169ea5b070bde84e55f203bbee8e83d87a175` `[SRC Dockerfile:87 @ cef8ec2]`. The build needs Docker Hub, Ubuntu archives (for `python3`, required by `verifyThirdPartyNotices` `[SRC Dockerfile:37-40 @ cef8ec2]`, `[SRC build.gradle.kts:121-145 @ cef8ec2]`), `services.gradle.org` (Gradle 9.0.0, 134,491,514 B, `validateDistributionUrl=true`), and Maven Central + the Gradle Plugin Portal (107 runtime artifacts in the shipped closure alone); there is **no JDK toolchain download** — no foojay resolver is configured anywhere, so a bare host needs a local JDK 25 `[SRC build-logic/…/swath.java-conventions.gradle.kts:23 @ cef8ec2]`. Cost estimate on this class of box: **8–15 min wall cold, ~600–900 MB download, ~4–6 GB peak disk** `[INFERRED, anchored on CI timings of 3m17s for build and 2m55s for the multi-arch buildx build, both with warm caches, [3P runs 30746967874, 30746142981]]`.

---

## 8. Smoke results

### 8.1 The blocker, stated precisely — read this before any number below

The study's mandatory runner-security profile **`s3-listing-study-v1`** `[HARNESS runner-security-lib.sh:9]`, `[HARNESS security/policy.v1.env:3]` is **not provisioned on this box.** `harness/runner-security-check.sh` fails closed at its first substantive assertion:

> `missing regular readiness record: /etc/s3-listing-study/runner-ready.env; provision the runner first`
> `[HARNESS runner-security-check.sh:39]`, `[OBS running runner-security-check.sh]`

This host is a shared devcontainer carrying unrelated workloads and private checkouts, so it **categorically cannot satisfy that profile** — this is not a missing step that could be taken, it is the wrong kind of machine. Because `harness/smoke-run.sh` performs that preflight and owns the receipt format, **the wrapper was never used and no receipt exists for any run.**

Two further consequences, both material:

- **The manifest artifact does not exist on this box**, so `harness/verify-listing.sh` could not be run at all. **There is no verifier verdict for any run below.** Completeness is supported only by count-and-uniqueness against the registry's *recorded figures* `[DOC docs/smoke-bucket.md:51, 71]` — which is strictly weaker than a manifest diff: it can detect a missing or duplicated key in aggregate, but it cannot detect a substituted key, a corrupted key, or compensating errors.
- Runs were executed directly with `docker run --rm --pull=never --cap-drop ALL --security-opt no-new-privileges:true`, credential-starved (`AWS_EC2_METADATA_DISABLED=true`, credential env vars emptied, `AWS_SHARED_CREDENTIALS_FILE`/`AWS_CONFIG_FILE` pointed at `/nonexistent-by-harness`), `TZ=UTC`. That reproduces the wrapper's *credential starvation* and *capability drop*, but not its network confinement, timeout enforcement, payload hygiene pipeline, or receipt schema.

**Every observation below is `[OBS <how>]`. None is a receipt. All of it must be re-run under the wrapper on a provisioned runner before anything is promoted.**

### 8.2 Offline probes (`--network none`)

| Probe | Result |
|---|---|
| `--version` | `swath 0.2.0 (cef8ec24a74f)`, exit 0 `[OBS docker run --network none … --version]` |
| `list --help` | Live usage block confirms the **absence** of `--max-keys`, `--delimiter`, `--recursive`, `--no-owner-split`, `--all-versions` `[OBS docker run --network none … list --help]` |

The `--help` probe is the most valuable thing in this section: it **upgrades reader C's four positive-evidence absence claims from source-read to observed** (§ 3.4). It does not, however, upgrade the *reason* for each absence — that a flag never existed versus was removed remains a `[SRC]` claim.

### 8.3 Listing runs — `noaa-normals-pds`, us-east-1, anonymous

Both runs: `auth=anonymous` (credential-starved, `--no-sign-request`), `--concurrency 8` (well under the campaign's aggregate ≤32 — we are polite guests on a sponsor-paid public bucket), `--format jsonl -v --region us-east-1`, image `sha256:ef1aca9a…`, `arch=arm64`, `emulated=no`, `TZ=UTC`.

**Run 1 — scoped, mode M3 `jsonl` × M8 `seed.mode=shallow` (default)**

```
… list s3://noaa-normals-pds/normals-hourly/ --format jsonl -v --region us-east-1 --no-sign-request --concurrency 8
```

| Field | Value |
|---|---|
| Exit code | 0 `[OBS]` |
| Rows emitted | **2,549** JSONL rows; registry records 2,549 for this prefix `[OBS]`, `[DOC docs/smoke-bucket.md:71,84]` |
| Wall clock | not recorded |
| Verifier verdict | **none — verifier could not be run (§ 8.1)** |
| Tool-reported counters | `api_calls=75`, `steals=3`, `splits=0` `[OBS swath's own end-of-run summary on stderr under -v; the line's source anchor is [SRC .../runtime/ListRunner.java:1357 @ cef8ec2]]` |

**Run 2 — full bucket, mode M3 `jsonl` × M8 `seed.mode=shallow` (default)**

```
… list s3://noaa-normals-pds/ --format jsonl -v --region us-east-1 --no-sign-request --concurrency 8
```

| Field | Value |
|---|---|
| Exit code | 0 `[OBS]` |
| Wall clock | **18 s** (measured outside the tool; not a wrapper `StartedAt→FinishedAt`) `[OBS]` |
| Rows emitted | **148,917**; **0 duplicate keys**; **148,917 unique keys**; registry records 148,917 `[OBS]`, `[DOC docs/smoke-bucket.md:51]` |
| `last_modified` range | `2025-11-24T20:33:44Z` … `2026-07-22T13:20:38Z`, **no fractional seconds anywhere** `[OBS]` |
| Verifier verdict | **none — verifier could not be run (§ 8.1)** |
| Tool-reported counters | `api_calls=240` `[OBS same stderr summary]` |

The zero-duplicate result is the single most useful correctness signal these runs produce: it is consistent with the disjointness invariants of § 2.7 at this scale, on this bucket, with 8 workers. It is **not** a verification of them.

### 8.4 Bucket drift — do not charge this to the tool

The registry snapshot is dated **2026-07-17** with manifest sha256 `c78a82737dd1982a999912afa89f870c013cb22e01e50b8c4835ddb725992adb` `[DOC docs/smoke-bucket.md:49-50]`. **Every object in `normals-hourly/` now reports `last_modified` 2026-07-22 — after the snapshot** `[OBS run 1 output]`, i.e. a re-upload that moved mtimes. The key set looks stable: exact count match at both scopes and zero duplicates at full-bucket scope.

Under the brief's protocol this is a **mid-campaign drift signal that only the orchestrator can adjudicate**, and re-baselining is never an agent's job. It is recorded here as a fact about the third-party bucket, not a finding about swath. The practical consequence for any later `mtime` assertion is direct: **a manifest-based `mtime` comparison against the 2026-07-17 snapshot would now fail on at least the `normals-hourly/` scope**, and would fail *correctly* — the bucket moved, not the tool.

### 8.5 Request-behaviour observation: non-page calls by scope

Presented as an observation about **these two runs on this bucket** — not a benchmark, and not comparable to anything.

**Use the recorded page counts, not a theoretical minimum.** Dividing keys by the 1,000-key page cap gives the page count a *serial* paginator would need; a parallel range-splitting engine does not fill its workers' pages, so that floor understates real paging badly and inflates any "non-page" residue derived from it. Both runs record the actual figure: `pages=165`, `mean_keys_per_page=980.9` (run 2) and `pages=49`, `mean_keys_per_page=765.8` (run 1), in the `list_run_diagnostics` line `[OBS -v stderr, captured in observations/full/stderr.txt and observations/hourly/stderr.txt]`.

**Read the right `pages`, because there are two.** `list_run_diagnostics`'s `pages` is the raw **worker-page fetch** count — `swath.page.raw_count`, incremented once per page returned to a `RangeScanner` (plus adopted readahead pages) `[SRC .../RunMetrics.java:863-865 @ cef8ec2]`, `[SRC .../RangeScanner.java:218 @ cef8ec2]`, `[SRC .../SpeculativeReadahead.java:526 @ cef8ec2]`. `list_run_summary`'s `pages` is a different counter — **committed** pages, incremented at page-commit `[SRC .../RunMetrics.java:793-796 @ cef8ec2]`, `[SRC .../WorkStealingScan.java:771 @ cef8ec2]` — and reads `159` / `18` on the same two runs. The fetch count is the correct denominator for "how many of the API calls were worker pages".

So of run 2's 240 calls, **75 were not worker pages**; of run 1's 75 calls, **26 were not worker pages** (seed probes, structure probes, pivot probes). As a share that is **31 % at full-bucket scope and 35 % at the 2,549-key prefix** — probe overhead is *slightly* higher proportionally at the smaller scope, and it **dominates neither run** `[OBS these two runs on this bucket only; two points, no trend]`.

A partial decomposition, offered with its limits: at `W=8` the seed budget is `targetSeeds = min(1000, 4·W) = 32` and `maxProbes = min(256, targetSeeds) = 32` `[SRC .../HybridSeedPlanner.java:131-142 @ cef8ec2]`, which on the corrected numbers is **large enough to account for run 1's whole 26-call residue on its own** — so nothing here forces a thief-probe explanation. Against that, the same diagnostics line records only `probe_fetches=9` (run 1) and `probe_fetches=30` (run 2) `[OBS same stderr]`, which does not reconcile with 26 and 75 under any reading the captures settle — `probe_fetches` itself was not traced to source in this pass, so what it excludes is unknown. The residue may include post-descent weight sampling `[SRC .../HybridSeedPlanner.java:507-545 @ cef8ec2]`, thief structure and pivot probes, and retried attempts (every attempt increments, § 5.4) — but the captured counters do not decompose it, and `-vv` was not used. **Routed to § 10 as a `-vv` capture question, not resolved here.** Run 1's `steals=3, splits=0` is recorded verbatim; the exact semantics of those two counter names were not traced to source in this pass, so no interpretation of the pair is offered.

### 8.6 Per-mode status — what is and is not evidenced

| Mode | Status | Reason |
|---|---|---|
| M3 `jsonl` | **Observed, twice, exit 0** — no receipt, no verifier verdict | § 8.1 |
| M8 `seed.mode=shallow` | **Observed** (it is the default; both runs used it) — no receipt | § 8.1 |
| M1 `table`, M2 `tsv` | **Unexercised.** Capturable in principle | no wrapper run performed |
| M9 `seed.mode=none` | **Unexercised.** Fully stdout-capturable; a genuine request-pattern mode and a priority for the re-run | no wrapper run performed |
| M10 `seed.mode=hints` | **Unexercised.** Warrants one capability probe showing the exit-2 failure — note it fails *after* the checkpoint DB is opened and the S3 client is built, so it is not a pure-validation refusal `[SRC SeedStep.java:160-161 @ cef8ec2]` | no wrapper run performed |
| M4, M5 Parquet | **Not capturable under the harness as it stands** — directory/file sink, harness mounts nothing (§ 5.5) | harness limitation, not tool limitation |
| M6 `--sort` | **Not capturable** — Parquet-only by construction `[SRC ListCommand.java:824,1519,1531 @ cef8ec2]` | as above |
| M7 `resume` | **Not capturable** — needs a durable checkpoint, hence a directory dataset, hence a mount (§ 2.9) | as above |
| `--fetch-owner` | **Unexercised.** One representative run recommended, not a full mode (§ 3.3) | — |
| Edge-case fidelity (unicode, weird keys, multipart ETag) | **Deferred** — `EDGE_BUCKET=none` `[DOC docs/smoke-bucket.md:91-95]` | fixture not seeded |

**What these runs settle about the container** (§ 7.2's arm64 gap): the JVM, the fat jar, the CLI, and the full listing path all ran **natively on arm64** `[OBS]`. With `-o` unset the destination is stdout, so `--checkpoint auto` resolved to ephemeral `[SRC CheckpointOptions.java:67-77 @ cef8ec2]` — but ephemeral is **not** "no SQLite": it is `SqliteCheckpointStore.openEphemeral` `[SRC ListCommand.java:627-629 @ cef8ec2]`, an in-process `jdbc:sqlite::memory:` database driven through the same store `[SRC .../checkpoint/SqliteCheckpointStore.java:85-96 @ cef8ec2]`. Both runs therefore **did** load `sqlite-jdbc` and its native library on arm64 and completed with exit 0, which **closes the `sqlite-jdbc` native-extraction half of the § 7.2 gap** `[INFERRED, from those two anchors plus the observed exit-0 runs]`. What remains open on arm64 is the *durable on-disk* checkpoint path and, separately, the **`zstd-jni` / Parquet native path**, which no stdout run reaches at all. Routed to § 10.

---

## 9. Notable findings

### 9.1 The 0.1.0 → 0.2.0 engine delta

| commit | change |
|---|---|
| `d18487c` | Adds `TailFloorMode` (`CURRENT`/`EST_DIRECT`/`REACH_FLOORED`), threads it through all three floor-consult sites, adds `TAIL_FLOOR.*` divergence counters. Default still `CURRENT` `[GIT d18487c]` |
| **`3b695c3`** | **The flip**: `EngineToggles.DEFAULT` moves `rate_anchored_sensing` false→true and `tail_floor` `CURRENT`→`REACH_FLOORED`; `parse()`'s fallbacks move identically `[GIT 3b695c3]` |
| `6788d05` | Observability only — `OWNER_SPLIT.open_frontier` reason, plus a `swath.open_frontier.keys_emitted` gauge `[GIT 6788d05]`, `[SRC .../WorkStealingScan.java:743-752 @ cef8ec2]` |
| `00d8528` | Independent seed-time cure: `appendOpenTileSentinel` `[GIT 00d8528]`, `[SRC .../HybridSeedPlanner.java:643-689 @ cef8ec2]` |
| `bf0bac8` | No behavioural change — javadoc and startup log text only `[GIT bf0bac8]` |

The public source establishes the two default flips and the supported rollback
spellings. Benchmark-arm selection belongs to the common methodology; this
public report does not carry maintainer rationale for tuned defaults.

### 9.2 The docs-vs-source drift set — a source-reliability finding, stated plainly

Readers A, B, C and E each found drift independently. **Consolidated and de-duplicated, there are fifteen distinct items, and several are live error messages or javadoc naming flags that do not exist.** Taken together the conclusion is not "some docs are stale" but something sharper:

> **At this revision, swath's own prose documentation and in-source javadoc are not a reliable statement of the shipped surface or the shipped defaults. The reference tables, the golden help captures, and the code are.**

That is a source-reliability datum the study should carry, because it is exactly the failure mode the study was created to catch — and it applies to the tool the study's own organisation builds.

| # | Drift | Doc/javadoc says | Source says | Found by |
|---|---|---|---|---|
| D1 | `--all-versions` named as a flag | `[DOC docs/usage.md:751]` "planned but not built in v1.0"; repeated in `contracts.md:89,95,834,981`, `architecture.md:335`, `ROADMAP.md:11`, and live javadoc at `sort/SortMode.java:11`, `sort/PageBlock.java:50`, `runtime/ArgsHashFields.java:11` | no such `@Option` exists anywhere; `ArgsHashFields` describes `args_hash` as covering "recursive flag, `--all-versions`" — **two** flags that do not exist | C |
| D2 | `--max-keys` described as a run-configurable page size | `[DOC docs/metrics-and-observability.md:34]` "a (never-recommended) `--max-keys=1` run misclassifies every ordinary worker page fetch as `pivot_probe`" | `int pageMax = 1000;` hard-coded `[SRC ListCommand.java:395 @ cef8ec2]`; `--max-keys` exists only on `swath-replay-server`'s `BenchCommand`. **The caveat describes a run that cannot be invoked**, so the classifier hazard it warns about is unreachable in v0.2.0 | B + C (merged) |
| D3 | `--prefix` described as a user-passed flag | `[DOC docs/operating.md:96]` "a `--prefix` **you** pass that ends in a lone `%`" | no `--prefix` flag; the prefix is the path component of the positional `<s3-uri>` `[SRC S3Uri.java:23-46 @ cef8ec2]`. The *behaviour* described is correct; only the spelling is wrong | C |
| D4 | `--seed` and `--hints` named as flags — **in a live runtime error string** | javadoc `[SRC .../engine/SeedMode.java:10,17 @ cef8ec2]`; error string `"--seed hints requires a --hints cut-points file, which is not yet implemented"` `[SRC SeedStep.java:160-161 @ cef8ec2]` | the surface is `--tune seed.mode=…`. **A user who hits this error is told to pass two nonexistent flags** | C |
| D5 | `--no-owner-split` described as a live kill-switch | javadoc `[SRC EngineToggles.java:246,254-255 @ cef8ec2]` plus an **unreachable** error string `"--no-owner-split conflicts with --engine-toggle owner_split=on"` at `:294-295` | rejected with `UnmatchedArgumentException`, asserted by test `[SRC EngineToggleCliValidationTest.java:67-72 @ cef8ec2]` | C |
| D6 | `--metrics-interval` referenced in live javadoc | `[SRC .../runtime/RunContext.java:63 @ cef8ec2]`, `[SRC .../observability/MeterRegistries.java:83 @ cef8ec2]` | removed, asserted rejected `[SRC MetricsExportCliValidationTest.java:62-69 @ cef8ec2]`; cadence is env-only (`SWATH_OTLP_INTERVAL`) | C |
| D7 | `--single-file` referenced as a replaced flag | `[SRC OutputOptions.java:412-413 @ cef8ec2]` | historical, harmless — but confirms the flag surface has churned | C |
| D8 | **`install.md` quickstarts omit region entirely** | `[DOC docs/install.md:15-18,70,82,139]` — every anonymous example | these **fail at exit 2 in a clean container** against `[SRC ConnectionOptions.java:240-245 @ cef8ec2]`. `faq.md:3-14` documents the failure correctly; the quickstart a new user copies does not. **Highest operational value** | C |
| D9 | `install.md`: "No release has been cut yet"; `packaging-and-docker.md`: "No release has been published yet" | `[DOC docs/install.md:8]`, `[DOC docs/packaging-and-docker.md:225]` | the pinned rev **is** tag `v0.2.0`, and a v0.1.0 release + `0.1.0` image had existed since 2026-07-27. `install.md`'s own verification example already reads `TAG=v0.1.0` `[DOC install.md:106]`. Upstream `8311ede` on `main` fixes it but lands **after** the tag, so the v0.2.0 tarball ships the false claim | C + E (merged) |
| D10 | `TailFloorMode` javadoc: "`CURRENT` is the shipped formula and the default" | `[SRC .../TailFloorMode.java:14-15 @ cef8ec2]` | default is `REACH_FLOORED` `[SRC EngineToggles.java:187-189, 310-311 @ cef8ec2]` | A |
| D11 | `RateAnchoredEstimator` javadoc: "**Not the default:** a run steers on this only under `--engine-toggle rate_anchored_sensing=on`" | `[SRC .../RateAnchoredEstimator.java:17-18 @ cef8ec2]` | it **is** the default since `3b695c3` | A |
| D12 | `RemainingWorkEstimator` javadoc: `WINDOW` is "the shipped reading and the default … and the only supported one" | `[SRC .../RemainingWorkEstimator.java:17-19,61-62 @ cef8ec2]` | contradicts `EngineToggles` | A |
| D13 | `s3-implementation-compatibility.md`: the SDK decode "happens regardless of `encoding-type`, since it is unconditional in the SDK" | `[DOC docs/internals/s3-implementation-compatibility.md:18-21]` | the interceptor is **gated** on the response's echoed `EncodingType`, case-sensitively `[3P-DEP …DecodeUrlEncodedResponseInterceptor#shouldHandle]`. **The only drift item with a correctness consequence** — § 6.3 | B |
| D14 | Four stale names/figures in live javadoc | `--rate-limit-api` (is `--request-rate`) `[SRC .../ApiRateLimiter.java:16 @ cef8ec2]`; `--stall-timeout` (is `--idle-timeout`) `[SRC .../LivenessWatchdog.java:28 @ cef8ec2]`; "after the SDK's own `RetryStrategy.standard()` retries are exhausted" `[SRC .../S3FaultClassifier.java:185-188 @ cef8ec2]`, `[SRC .../error/ThrottleException.java:9-13 @ cef8ec2]`; "one chance to self-heal at the escalated **20 s** budget" `[SRC .../GaugedFetcher.java:78-81 @ cef8ec2]` | `--request-rate`; `--idle-timeout`; there are **no** SDK retries (`maxAttempts=1`); the level mapping now yields **6 s** | B |
| D15 | `--force-sort` named as the disk-guard escape hatch — **in a live runtime error message** | the exhaustion marker logged immediately before `Runtime.halt` tells the user "a later `--resume` … or `--force-sort` can continue safely" `[SRC .../sort/SortDiskGuard.java:186-192 @ cef8ec2]` (the flag name is on `:190`), repeated in javadoc at `:52,71,119` | no `--force-sort` `@Option` exists; the real surface is `--tune sort.ignore-disk-check=on` `[SRC TuneOptions.java:32-33 @ cef8ec2]` (§ 3.2). **A user whose `--sort` run just halted on disk exhaustion is told to pass a flag that does not exist** — same failure shape as D4 | review |

**Adjudication note.** D2 was found independently by B (as a classifier caveat unreachable in v0.2.0) and C (as a docs drift). They are the same underlying fact seen from two ends; merged above, with B's "unreachable in practice" conclusion retained because it is the operationally relevant half. D9 was found by C and E; C's half concerns install docs, E's concerns packaging docs — merged, both citations kept. **D15** came from the independent cross-model review of this report rather than from a reader pass (hence "review" in the last column); its anchor was re-verified in the pinned checkout, and it also corrects `reader-D-output.md:175`, which had repeated `--force-sort` as though it were a real flag — annotated inline there rather than overwritten, since the reader files are derivation records.

**Counter-evidence, in fairness:** `docs/configuration.md` and `docs/usage.md`'s flag/default **tables** match the golden help captures and the source field defaults on every entry checked. The drift is concentrated in *prose* and *javadoc*, not in the reference material. And the golden help captures themselves are strong evidence: the three files under `swath-cli/src/test/resources/help/` are exact `CommandLine.getUsageMessage(Ansi.OFF)` captures, asserted byte-equal by `HelpUsageGoldenTest` and regenerable only under `-Dswath.goldens.update=true` `[SRC .../HelpUsageGoldenTest.java:33-46 @ cef8ec2]` — an authoritative statement of the CLI surface, not documentation prose.

### 9.3 Engineering worth remarking on

- **The executor/policy split is real, not aspirational.** Three seams, each with a pure state machine testable with zero I/O (§ 2). This is why `HybridSeedPlanner`, `ThiefPolicy` and `OwnerSplitGovernor` can carry the complexity they do without the code becoming untestable.
- **`parentEmptySliver` is a byte-exact signature test, not a heuristic** — the degenerate case where the pivot is exactly `cursor` with one `0x20` appended, so the child would inherit the entire tail `[SRC .../ThiefPolicy.java:184-188 @ cef8ec2]`. Naming and detecting that specific pathology, rather than papering over it with a retry, is good engineering.
- **The confetti feedback gate closes a loop most tools do not have**: carved children are classified at completion by realized mass, and a high runt rate suppresses further carving — with every 16th carve let through as a probe so the gate can recover `[SRC .../OwnerSplitGovernor.java:166-191 @ cef8ec2]`. The probe slot is a CLAIM resolved by the executor against a run-scoped gate so concurrent owners sharing a snapshot cannot all carve `[SRC .../OwnerSelfSplit.java:189-198 @ cef8ec2]`.
- **A 503 deliberately does not count toward the structure-probe timeout suppressor** — store backpressure is not keyspace shape `[SRC .../Thief.java:458-477 @ cef8ec2]`. Small decision; exactly right.
- **`ProtocolViolationException` outranks cancellation in exit-code resolution** (§ 6.1), because a violated run is not the "come back to it later" that every cancellation code implies. Also the one fault speculative readahead refuses to absorb `[SRC .../ProtocolViolationException.java:17-22 @ cef8ec2]`.
- **Bearer-token flags are never persisted, and the reasoning is explicitly security-shaped** — "a stored command is a stored secret"; "a checkpoint is data, not a trusted script" `[SRC BearerTokenOptions.java:15-33 @ cef8ec2]`.
- **The `--sort` merge phase issues zero LIST calls** `[SRC .../checkpoint/SortPhase.java:247-251 @ cef8ec2]` — worth knowing before anyone times a `--sort` run as if it were a listing benchmark.
- **`readahead` (off by default) is safe by construction**: adopted and serial pages take the *identical* code path — same per-key `hi` check, same protocol guards, the same single `consumer.accept` `[SRC .../RangeScanner.java:234-289 @ cef8ec2]`; readahead never emits or commits anything itself `[SRC .../SpeculativeReadahead.java:38-48 @ cef8ec2]`; guesses use a dedicated off-gauge fetcher so they never take an AIMD permit or cast a growth vote `[SRC .../WorkStealingScan.java:285-294 @ cef8ec2]`; K = 8 in-flight-plus-buffered guesses with a 30 s per-guess budget `[SRC .../ReadaheadConfig.java:56, 89 @ cef8ec2]`; and disengagement is a tumbling 16-page window at a 0.40 adoption threshold `[SRC .../AdoptionWindow.java:49-70 @ cef8ec2]`.
- **Third-party licensing posture is unusually rigorous**: `THIRD_PARTY_NOTICES.md` covers 107 runtime artifacts, machine-generated from the exact shaded closure and **staleness-gated** — `verifyThirdPartyNotices` re-renders and byte-compares, and `shadowJar` `dependsOn` it, so a jar with drifted notices cannot be produced `[SRC build.gradle.kts:121-145 @ cef8ec2]`, `[SRC swath-cli/build.gradle.kts:137 @ cef8ec2]`. A jk1 `checkLicense` allow-list runs on every PR with bare GPL/LGPL/AGPL deliberately absent and no per-module exemptions `[SRC config/license/allowed-licenses.json @ cef8ec2]`. Every GitHub Action is SHA-pinned, both `FROM`s are digest-pinned, the Gradle wrapper is checksum-validated before any other gate, and the DuckDB CLI downloaded in the publish job is SHA256-checked before execution `[SRC .github/workflows/ci.yml:57-58 @ cef8ec2]`, `[SRC .github/workflows/release.yml:196-204 @ cef8ec2]`. Release publishing is double-gated by a protected environment **and** a `PUBLIC_RELEASE_ENABLED` kill-switch `[SRC .github/workflows/release.yml:100-110 @ cef8ec2]`.
- **Version discipline is mechanical**: `verifyReleaseVersion` fails unless the git tag equals `v` + `gradle.properties`'s version, and `just release` produces two commits so `main` never sits on a released version `[SRC build.gradle.kts:22-47 @ cef8ec2]`, `[SRC justfile:107-157 @ cef8ec2]`.

### 9.4 Maturity and maintainer disclosure — the part that needs saying without hedging

swath is **ten days old** (repo created 2026-07-25, last push 2026-08-02), has a **single contributor**, and has cut **two releases in six days** `[3P GitHub repo / contributors / Releases APIs]`. That contributor is also the sole reviewer of the protected `public-release` environment — a single point of failure for releases `[SRC .github/workflows/release.yml:100-110 @ cef8ec2]`, `[3P GitHub Actions API]` — and, as disclosed at the head of this report, is the same person who owns this study.

Three concrete consequences, none of which is a judgement about intent:

1. **The `Nightly deep verification` workflow has failed on every visible run** — 2026-07-30, 07-31, 08-01, 08-02, all pinned to the same stale head `bf0bac8`, while PR/`main` CI is green `[3P GitHub Actions runs API]`. Whatever that suite covers, it is not currently passing on the code near this tag. Cause not investigated.
2. **arm64 is built but never runtime-smoked upstream** (§ 7.2) — a gap that exists because CI runners are amd64 and nobody has closed it.
3. **The docs-drift set of § 9.2 is what a ten-day-old, single-maintainer project looks like**: the code is careful and the prose has not caught up. That is a normal and forgivable state — and it is *exactly* the state in which a study that trusts prose over source produces wrong results, which is the study's founding premise.

The methodological point for this study: **a subject built by the study's own organisation gets no benefit of the doubt and no extra suspicion.** The mitigation already in place is structural rather than attitudinal — the derivation was blind to the inherited tool page, every claim is anchored, the reviewer re-verifies anchors against the pinned checkout, and this report will be compared against a write-up produced independently. That is the right control; it should be stated in the study's methodology rather than left implicit.

### 9.5 Things that exist but are not wired

- `seed.mode=hints` throws `InvalidConfigException` `[SRC SeedStep.java:160-161 @ cef8ec2]`.
- `ListingMode.VERSIONS` is dead code reachable only from a hand-crafted checkpoint DB (§ 3.4).
- Express One Zone is design-of-record only `[DOC docs/internals/algorithms.md §9/§10]`.
- `AlphabetDigest` (rank-space pivots) is structurally biased toward returning `NO_SCALAR` — it tracks only printable ASCII, only 8 positions past the range's birth divergence, and permanently disqualifies a position on the first non-printable scalar `[SRC .../engine/AlphabetDigest.java:53-58, 88-99 @ cef8ec2]`. `[DOC algorithms.md §3.3]` reports it never engaged across the 13-shape matrix; the code's structure is consistent with that `[INFERRED]`.
- Several no-`@Option` fields exist purely as programmatic or resume-restore seams: `EngineOptions.seed`, `MetricsOptions.metricsInterval`, `OutputOptions.rawOutput`, `OutputOptions.noSummaryJson`, `OutputOptions.parquetWriters`, `CheckpointOptions.resume` `[SRC as listed in reader-C-cli.md §3]`.

---

## 10. Open questions for the benchmark phase

**Blocking, and it comes first.** *Everything in § 8 must be re-run under `harness/smoke-run.sh` on a runner provisioned to `s3-listing-study-v1`, and verified against a present manifest.* Until then swath has **no receipts and no verifier verdict**, and nothing on its tool page can be promoted out of `VERIFIED: no`. The re-run must at minimum cover M1, M2, M3, M9 and an M10 capability probe.

**Bucket state.** The registry's 2026-07-17 snapshot no longer matches observed `mtime` on `normals-hourly/` (§ 8.4). The orchestrator must decide whether to re-baseline before the campaign; an `mtime`-asserting verification against the current manifest would fail on that scope, correctly.

**Harness capability gap.** Parquet (M4/M5), `--sort` (M6) and `resume` (M7) are **structurally uncapturable** under a harness that bind-mounts nothing (§ 5.5). This is a study-scope decision, not a swath finding. If Parquet is in scope it needs a bind mount plus a post-run archive step; note that Parquet is also the **only byte-exact** output format, so excluding it means the study never tests swath's faithful path.

**Scale-dependent, not settleable by any smoke run:**

1. **Memory under load.** Streaming output and a bounded entry queue say memory should be flat in N for text modes (§ 2.8), but no cliff was probed. Sweep object counts into the millions; watch `peak_rss` and `cgroup_peak_mem` separately (the JVM heap is not the whole tree). Parquet's `O(parts²)` finalize work and `--sort`'s `O(segments)` staging metadata are the two documented growth paths to target; `--sort`'s disk guard (~2× final output) is the third.
2. **Throughput and its shape.** Not measurable here and deliberately not attempted.
3. **`--concurrency` sweep, with the ceiling caveat.** Proposed range **1, 4, 8, 16, 32, 64, 128**. The essential instrumentation is *effective* `T` versus configured `Tmax` — a run at `--concurrency 128` may sit at effective 6 and the flag would tell you nothing (§ 2.5). Also measure the **seed-phase cost as a function of `W`**, which should *rise* with concurrency `[DOC algorithms.md §8]`.
4. **AIMD steady state against a real store.** How often does the congestion latch engage on a sponsor-paid public bucket at meaningful concurrency? Does the sustained-timeout shed ever fire (its starvation clause makes it hard to trip)?
5. **The 503 path end-to-end.** Confirm that a throttled run dies at `--no-progress-timeout` (10 min) and *not* at `--idle-timeout` (120 s) — § 6.4 predicts this from source and it is a surprising enough operational fact to be worth an explicit test.
6. **`seed.mode=none` (M9) versus `shallow` (M8).** The cleanest experiment swath offers: same output contract, radically different request pattern. Expect very different `api_calls` and steal counts. Also the natural place to see whether the open frontier is the weak spot § 9.1 says it is.
7. **The documented 0.2.0 rollback pair.** `rate_anchored_sensing=off` + `tail_floor=current` is the vendor-supported pre-0.2.0 configuration and the clean A/B for the release's headline change (§ 9.1). The `TAIL_FLOOR.*` divergence counters make the comparison cheap.
8. **Probe overhead as a function of scope.** § 8.5 observed 75/240 non-worker-page calls at full bucket and 26/75 at a 2,549-key prefix — 31 % versus 35 %, a much smaller spread than a keys÷1000 derivation would suggest. Whether that ratio holds, and how it decomposes into seed / structure / pivot classes, needs `-vv` capture plus `--report`'s `probe_latency[]` — and `--report` needs a writable path, i.e. the same harness change as Parquet.
9. **`--request-rate` interaction with retries.** The limiter sits **inside** the retry loops (§ 2.6), so a rate-capped run under throttling pays the wait on every attempt. Worth one deliberate experiment.
10. **Page size is not sweepable** without patching source (§ 3.2). If the study wants a page-size axis across tools, swath is fixed at 1000 and that asymmetry must be stated in the comparison plan rather than silently absorbed.
11. **arm64 native paths — narrowed, not closed.** § 8.6 shows `sqlite-jdbc` and its native library **were** loaded on arm64 (the ephemeral store is a real `:memory:` SQLite database), so that half of the § 7.2 gap is closed. Still unexercised on arm64: the durable on-disk checkpoint and the `zstd-jni` / Parquet writer. One directory-dataset Parquet run on arm64 closes both.
12. **Architecture decision.** Both arches are native for every swath channel (§ 7.2), so swath does not constrain the study's common-denominator choice. amd64 remains the expected denominator; flagging that swath would equally support arm64 if another tool forces it.
13. **Endpoint conformance.** The `+`-to-space and `EncodingType`-echo hazards (§ 6.3) are **conditional, not observed**: each fires only against an endpoint that is *nonconforming in a specific way* — one that echoes `+` literally under `encoding-type=url`, or that omits/misspells the `<EncodingType>` echo. Neither condition was seen in anything tested. The tested LocalStack build preserves `+` `[SRC …PercentEchoLocalStackIT.java:110-148 @ cef8ec2]`, and the tested MinIO build is conformant on both *documented* deviations `[DOC docs/internals/s3-implementation-compatibility.md:26-36,93-135]` (its `+` handling is not tested by anything in-tree either); **Ceph and R2 are untested here in any respect** — naming them is speculation about the class of S3-compatible endpoints, not a finding about those products `[INFERRED]`. What is established is the *shape* of the failure if such an endpoint is ever used: a wrong answer with a clean exit and no warning, and no swath-side validation to catch it (§ 6.3). Cheap insurance: seed one key containing a literal `+` and one containing `%25`, and assert byte-exact round trip on any new endpoint before trusting a run against it. LocalStack's separately *documented* double-decode of `start-after` (§ 6.3) is the one hazard that was actually reproduced, and it is reproduced upstream, not here.
14. **Intra-page ordering.** § 2.4 establishes there is no monotonicity check. Only a replay server (the methodology's Phase 2) can exercise it; noted for that phase, not for this one.

---

## 11. Sources

**Pinned subject.** `github.com/varveio/swath`, tag `v0.2.0`, SHA `cef8ec24a74ffae14ee6a9462e4b7f6c334fbc32`. Every `[SRC]` anchor in this report is against that SHA, read at `/home/vscode/.s3-listing-study/sources/swath` (worktree verified clean at the pinned SHA by reader C).

**Project documentation** (all read in the pinned checkout, access date 2026-08-02): `README.md`, `ROADMAP.md`, `RELEASING.md`, `NOTICE`, `LICENSE`, `THIRD_PARTY_NOTICES.md`, `docs/install.md`, `docs/usage.md`, `docs/configuration.md`, `docs/operating.md`, `docs/faq.md`, `docs/packaging-and-docker.md`, `docs/metrics-and-observability.md`, `docs/performance.md` *(existence noted only — vendor self-reported figures, none transcribed)*, `docs/internals/algorithms.md`, `docs/internals/contracts.md`, `docs/internals/architecture.md`, `docs/internals/probe-budgets.md`, `docs/internals/s3-implementation-compatibility.md`, `docs/internals/field-investigations.md` *(existence noted only — same reason)*.

**Third-party, accessed 2026-08-02:**
- `https://api.github.com/repos/varveio/swath` — repo metadata, license, timestamps
- `https://api.github.com/repos/varveio/swath/contributors` — sole contributor
- `https://api.github.com/repos/varveio/swath/releases` — v0.1.0, v0.2.0 and assets
- `https://api.github.com/repos/varveio/swath/actions/runs/30746967874` — the v0.2.0 Release run
- `https://api.github.com/repos/varveio/swath/actions/runs/30746142981` — the docker-check PR run
- GitHub Actions runs API — `Nightly deep verification` failure history
- `ghcr.io` registry API (anonymous pull token, `repository:varveio/swath:pull`) — index, per-arch manifests, config blobs, cosign signature tag, attestation manifests

**Pinned dependency read (`[3P-DEP]`):** `software.amazon.awssdk:s3:2.31.78` (`DecodeUrlEncodedResponseInterceptor#modifyListObjectsV2Response`, `#shouldHandle`) and `software.amazon.awssdk:utils:2.31.78` (`SdkHttpUtils#urlDecode`), disassembled from the Gradle cache.

**Study inputs (read-only):** `harness/smoke-run.sh`, `harness/verify-listing.sh`, `harness/runner-security-check.sh`, `harness/runner-security-lib.sh`, `harness/security/policy.v1.env`, `harness/README.md`, `docs/smoke-bucket.md` (registry: `noaa-normals-pds`, us-east-1, manifest sha256 `c78a82737dd1982a999912afa89f870c013cb22e01e50b8c4835ddb725992adb`, snapshot 2026-07-17, 148,917 keys, designated scoped prefixes `normals-hourly/` 2,549 · `normals-monthly/1991-2020/` 15,625 · `normals-annualseasonal/1981-2010/access/` ≤9,841; `EDGE_BUCKET=none`), `BRIEF.md`. Workspace staged at 2026-08-02T11:59:04Z from study repo commit `21d9df1eeac83dc1b63057da07bd0b564eb4fa46` `[PROVENANCE.txt]`.

**Reader research files** (the primary anchored derivations this report consolidates, all in this directory): `reader-A-engine.md` (seeding, stealing, ranges, concurrency, 0.1.0→0.2.0 delta, correctness invariants), `reader-B-store.md` (request shapes, retries, 503/AIMD, key fidelity, protocol defences, observability), `reader-C-cli.md` (CLI surface, mode/tunable inventory, auth, resume, exit codes, docs drift), `reader-D-output.md` (output formats, `normalize.sh` design, memory model, `--report`/`cost.api_calls`), `reader-E-build.md` (registry/image provenance, build route, arch matrix, entrypoint, upstream health).

**Receipt index: EMPTY.** No receipts exist. The runner-security profile `s3-listing-study-v1` is not provisioned on this box, `harness/smoke-run.sh` was never invoked, and the manifest artifact is absent so `harness/verify-listing.sh` could not run — see § 8.1. All runtime evidence in this report is labelled `[OBS]` and is a direct observation, never a receipt.
