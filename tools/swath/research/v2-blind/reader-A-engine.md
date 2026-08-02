# Reader A — Engine algorithm & concurrency (`swath` @ `cef8ec2` / v0.2.0)

All paths relative to `SOURCE_ROOT=/home/vscode/.s3-listing-study/sources/swath`. Claims are labelled `[SRC]` (read in source), `[DOC]` (vendor doc, unverified against source unless a `[SRC]` accompanies it), or `[INFERRED]`.

## 0. Shape of the area

The engine is split into an **executor** layer (`engine/*.java`, holds locks/clocks/RPC) and a **pure policy** layer (`engine/policy/*.java`, no lock/clock/RPC, decides by request→response over probe outcomes). Three seams follow this shape: `Thief`↔`ThiefPolicy`, `OwnerSelfSplit`↔`OwnerSplitGovernor`, `SeedStep`↔`HybridSeedPlanner` `[SRC swath-core/src/main/java/io/varve/swath/engine/Thief.java:56-72 @ cef8ec2]`, `[SRC .../engine/OwnerSelfSplit.java:45-50 @ cef8ec2]`, `[SRC .../engine/SeedStep.java:44-48 @ cef8ec2]`. The policy classes are deterministic state machines driven by a probe-outcome sequence, which is why they are unit-testable with zero I/O `[SRC .../engine/policy/HybridSeedPlanner.java:30-33 @ cef8ec2]`.

---

## 1. Keyspace division at seed time

### Budgets (all worker-count-derived)
`[SRC .../engine/policy/HybridSeedPlanner.java:131-142 @ cef8ec2]`:
```java
this.targetSeeds = Math.min(PROBE_PAGE, 4 * w);          // PROBE_PAGE = 1000 → cap 4×W, ≤1000
this.maxProbes   = Math.min(256, Math.max(1, targetSeeds));
```
So W=64 → `targetSeeds=256`, `maxProbes=256`. `targetSeeds` bounds the **final cut count**, not how far the descent reaches; the descent stops only on `maxProbes` or frontier exhaustion `[SRC .../HybridSeedPlanner.java:363 @ cef8ec2]`. A sub-budget `SAMPLE_BUDGET = 32` is carved **out of** `maxProbes` (never added on top) for mass-sampling probes; the descent loop's own ceiling is reduced accordingly: `descentCeiling = massAware ? maxProbes - min(SAMPLE_BUDGET, maxProbes/2) : maxProbes` `[SRC .../HybridSeedPlanner.java:56, 354 @ cef8ec2]`.

The descent is **serial** — one frontier node polled, probed, children enqueued, then the next `[SRC .../HybridSeedPlanner.java:362-377 @ cef8ec2]`. `[DOC docs/internals/algorithms.md §8]` states seed wall-time ≈ `maxProbes × probe RTT` and that raising `--concurrency` *lengthens* it; the serial structure in source corroborates this `[INFERRED from SRC .../HybridSeedPlanner.java:362-377]`.

### CommonPrefixes → half-open ranges
Cuts accumulate in a global `TreeSet<byte[]>` ordered by `Arrays::compareUnsigned` `[SRC .../HybridSeedPlanner.java:167 @ cef8ec2]`. Tiling is done by the executor:
```java
byte[] lo = null;
for (byte[] hi : cuts) { specs.add(seedTile(runId, lo, hi)); lo = hi; }
specs.add(seedTile(runId, lo, null));   // final open range (c_last, null]
```
`[SRC .../engine/SeedStep.java:237-246 @ cef8ec2]`. Each tile's cursor starts at its own `lo` `[SRC .../SeedStep.java:254-256 @ cef8ec2]`. **This construction tiles exactly for *any* sorted cut set** — the subsampling, banding, and sentinel machinery below can therefore only change balance, never coverage `[INFERRED, load-bearing]`. Seeds are inserted all-or-nothing via `insertNodes` `[SRC .../checkpoint/CheckpointStore.java:61-72 @ cef8ec2]`.

### Contents from the probe
Discarded. `SeedStep.toOutcome` promotes only `commonPrefixes`, `truncated`, `entries().size()` (a *count*, never the keys), and `lastSeenKey` `[SRC .../SeedStep.java:263-269 @ cef8ec2]`. `lastSeenKey` is the max over objects **and** common prefixes `[SRC .../SeedStep.java:272-287 @ cef8ec2]` — used only for +1-page pagination and the sentinel guard. So no seed/range double-emit is structurally possible: no seed page's objects are ever emitted `[SRC .../SeedStep.java:50-54 @ cef8ec2]`.

### Truncated-level classification (`pageCapped`)
Order of tests in `classifyTruncatedLevel` `[SRC .../HybridSeedPlanner.java:391-420 @ cef8ec2]`:

1. **flat-wide** — `commonPrefixes.isEmpty() && objectCount > 0` `[SRC .../HybridSeedPlanner.java:715-717 @ cef8ec2]`. Recorded as `flatWideRegion`; at finalize it is pre-cut into leading-byte radix bands. Band count `perRegion = min(SPAN, max(MIN_BANDS, targetSeeds))` with `SPAN = 0x7E-0x21 = 93`, `MIN_BANDS = 8` `[SRC .../HybridSeedPlanner.java:95-97, 841 @ cef8ec2]`. Cuts are `dir·<scalar>` with scalars spread evenly over printable ASCII, `'%'` skipped by index shift `[SRC .../HybridSeedPlanner.java:849-882 @ cef8ec2]`. Blind (mass-unaware) by construction — no keys are observed at seed time.
2. **partition fan-out** — majority of common prefixes whose *final path segment* contains `'='` `[SRC .../HybridSeedPlanner.java:724-756 @ cef8ec2]`. Tiled from the already-fetched page, evenly subsampled to `workerCount`, **not** added to the frontier (zero further probes) `[SRC .../HybridSeedPlanner.java:796-809 @ cef8ec2]`.
3. **ambiguous (tiny-leaf explosion vs heavy subtree)** — disambiguated by a bounded child sample when `mass_aware_seed` is on: `SAMPLE_WIDTH = 3` children probed at evenly spread indices; a child is "dense" iff `pageCapped || has commonPrefixes || objectCount >= SAMPLE_DENSE_MIN_OBJECTS (8)`; heavy iff `sampleDense*2 >= sampleSampled` `[SRC .../HybridSeedPlanner.java:49-63, 457-492 @ cef8ec2]`. Heavy → banded along the page's own child prefixes (`bandHeavyCut`, capped at `workerCount`) `[SRC .../HybridSeedPlanner.java:816-830 @ cef8ec2]`; not heavy → `tinyLeafExplosion`, **left whole**, handed to work-stealing `[SRC .../HybridSeedPlanner.java:494-503 @ cef8ec2]`.
4. Sample budget exhausted but siblings already sampled → an **empirical prior** carries the sibling verdict (`heavySamples*2 >= sampledLevels`) rather than defaulting to left-whole `[SRC .../HybridSeedPlanner.java:410-419 @ cef8ec2]`.

Crucially, an exploding level **does not abort the descent** — `finishClassification` returns to the frontier loop `[SRC .../HybridSeedPlanner.java:422-430 @ cef8ec2]`.

A **truncated TOP level** is different: with `mass_aware_seed` on and cuts non-empty, exactly one extra top page is read (`TOP_EXTRA`) `[SRC .../HybridSeedPlanner.java:267-273 @ cef8ec2]`; then the top is flagged `tinyLeafExplosion` and the descent is **skipped entirely** (`if (!topPageCapped) return enterDescent(); return finalizePlan();`) `[SRC .../HybridSeedPlanner.java:332-347 @ cef8ec2]`.

### Frontier ordering (`mass_aware_seed`, default ON)
`SpanPriorityFrontier` is a `PriorityQueue` ordered **depth ascending, then span score descending**, maintained across insertions `[SRC .../HybridSeedPlanner.java:1020-1071 @ cef8ec2]`. `spanScore` is the keyspace gap from a cut to its next sibling in the global cut set, falling back to the *enclosing scope's* `prefixCeil` (not the whole keyspace) when there is no successor yet `[SRC .../HybridSeedPlanner.java:944-967 @ cef8ec2]`. Cuts are added to `cuts` first, then offered to the frontier, so a page's cuts score against their own siblings `[SRC .../HybridSeedPlanner.java:771-788 @ cef8ec2]`. Toggle off → plain FIFO `[SRC .../HybridSeedPlanner.java:992-1014 @ cef8ec2]`.

### Over-cap reduction
If the descent produced more than `targetSeeds` cuts, they are reduced by `massWeightedSubsample` (weights from a bounded post-descent `WEIGHT_SAMPLE` pass, requiring ≥ `MIN_WEIGHT_SAMPLES = SAMPLE_BUDGET/4 = 8` weights) else by `subsampleEvenly` `[SRC .../HybridSeedPlanner.java:76, 507-545, 555-564, 885-938 @ cef8ec2]`.

---

## 2. Keyspace division at steal time

### Victim selection (`ThiefPolicy.selectVictim`)
`[SRC .../engine/policy/ThiefPolicy.java:98-154 @ cef8ec2]`. Over the driver-curated eligible pool, skip in order: `unsplittable` → `pacingSkipAvailable()` (futility cooldown, consuming one skip) → `est <= 0.0`; otherwise `argmax est`. `NoVictim` carries a discriminated reason (`POOL_EMPTY` / `ALL_NO_REMAINING_SPAN` / `ALL_FUTILITY_PACED` / `ALL_UNSPLITTABLE` / `MIXED_SKIPS`).

The pool handed in is already filtered by the **progress gate**: only workers with `emittedSinceSteal == true` `[SRC .../engine/WorkStealingScan.java:625-633 @ cef8ec2]`, set by `setCursor` at page-commit and cleared by `markStolen` at a successful carve `[SRC .../engine/WorkerState.java:231-234, 458-464 @ cef8ec2]`. This bounds re-splitting to ≤1 per emitted page and means a fresh child is never carved before its first page.

`est` comes from the run's single `RemainingWorkEstimator` `[SRC .../WorkStealingScan.java:245 @ cef8ec2]`. Open frontier scores `+INFINITY` under both readings `[SRC .../engine/StealMath.java:107 @ cef8ec2]`, `[SRC .../engine/RateAnchoredEstimator.java:94-96 @ cef8ec2]`, so the frontier always wins the argmax **once it is steal-eligible**.

### Pivot synthesis — the cascade
`ThiefPolicy.Attempt` is a 7-phase state machine `[SRC .../ThiefPolicy.java:256-264 @ cef8ec2]`. Precisely:

**Start** `[SRC .../ThiefPolicy.java:306-330 @ cef8ec2]`
- Refuse immediately if the `(cursor,hi)` snapshot is unchanged since the last non-productive steal, or `cursor >= hi`.
- `f = (hi == null) ? 0.5 : toggles.farAheadFraction(view.densityFraction())`, where `densityFraction()` returns `clamp(0.5 + 0.25·min(1, trailingEwmaDensity/averageDensity), 0.5, 0.75)`, defaulting to 0.5 with no density signal `[SRC .../WorkerState.java:285-307 @ cef8ec2]`.
- `m = hi==null ? StealMath.extrapolate(lo, c, prefixCeil(P)) : toggles.interpolate(c, hi, f, alphabetDigest, …)`.
- `m == null` splits two ways: open frontier with `cursor <= lo` → `RETRY(UNSTARTED_FRONTIER)` (**transient, not cached**); otherwise `SET_UNSPLITTABLE` + `MarkUnsplittable(NO_PIVOT)` (**terminal, cached**) `[SRC .../ThiefPolicy.java:319-327 @ cef8ec2]`, `[SRC .../StealMath.java:311-315 @ cef8ec2]`.

**Probe** — exactly one 1-key call: `ListObjectsV2(prefix=P, start_after=m, max_keys=1)`, "empty" iff no returned key or first key `> H` `[SRC .../engine/Thief.java:420-428 @ cef8ec2]`.

**Step-back** — if the upper half is empty, `hi != null`, and `f > 0.5`, re-place at the plain midpoint `interpolate(c, hi, 0.5)` and re-probe once `[SRC .../ThiefPolicy.java:355-367 @ cef8ec2]`. This is what makes far-ahead never worse than byte-midpoint.

**Two degenerate cases feed the structure probe** `[SRC .../ThiefPolicy.java:376-390 @ cef8ec2]`:
- `upperEmpty` — the pivot landed above the mass.
- `parentEmptySliver` — the probe found keys, but `m` is exactly `c` with one `0x20` byte appended (`isCursorAdjacentSliver`) `[SRC .../ThiefPolicy.java:184-188 @ cef8ec2]`, i.e. the child would inherit the entire tail. Byte-exact signature test, not a heuristic.
If neither, commit immediately.

**Structure discovery** — one `delimiter=/` list at `STRUCTURE_PROBE_MAX_KEYS = 32`, `start_after = cursor` `[SRC .../ThiefPolicy.java:35, 615-618 @ cef8ec2]`, `[SRC .../Thief.java:398-412 @ cef8ec2]`. The probe prefix differs by case: `upperEmpty` uses the `lo∧c` directory `[SRC .../ThiefPolicy.java:421-432 @ cef8ec2]`; `parentEmptySliver` uses a coarse→fine back-out from the `c∧hi` divergence directory, at most `MAX_STRUCTURE_BACKOUT_LEVELS = 3` extra probes `[SRC .../ThiefPolicy.java:47, 438-499 @ cef8ec2]`. Boundary choice: filter common prefixes to strictly `(c, hi)`, sort, take the **median** on a complete page or the **furthest** when the fan-out sample was capped `[SRC .../ThiefPolicy.java:451-472 @ cef8ec2]`.
Suppression: `structureProbingEnabled()` refuses when `consecutiveZeroFanoutProbes >= 8` **or** `consecutiveTimedOutStructureProbes >= 2`, with a 1-in-64 `DecisionRng` escape hatch `[SRC .../ThiefPolicy.java:59-65, 392-404 @ cef8ec2]`. Both counters are **per-victim**, not global `[SRC .../WorkerState.java:75-87 @ cef8ec2]`. The timeout counter is fed by the executor, which attributes an `ATTEMPT_TIMEOUT` to the victim before rethrowing `[SRC .../Thief.java:458-477 @ cef8ec2]` — a 503 deliberately does **not** count (store backpressure ≠ keyspace shape).

**Density reflection** (`reflect`, default on) `[SRC .../ThiefPolicy.java:507-529 @ cef8ec2]` — `m_r = extrapolate(lo, c, hi)`, accepted only if `c < m_r < m`; one probe; hit → commit at `m_r`, miss → bisect the shorter interval `(c, m_r]`.

**Bisection** `[SRC .../ThiefPolicy.java:531-558 @ cef8ec2]` — budget `B = ceil(log2(bandWidthBytes)) + 6` where `bandWidthBytes` reads 2 bytes past the `c∧hi` divergence (open frontier falls back to 40) `[SRC .../ThiefPolicy.java:50-56, 229-249 @ cef8ec2]`. Each step is `m = ByteMidpoint.between(c, m)` (walking back toward the cursor) + one probe. Budget exhaustion → `RETRY(BISECT_BUDGET_EXHAUSTED)` + futility mutation; `m == null` mid-bisection → `RETRY(RETRY_PIVOT_ADJACENT)` — **not** cached unsplittable.

**Flat-leaf fallback** (parent-empty sliver with no structure) `[SRC .../ThiefPolicy.java:560-598 @ cef8ec2]` — clamp ceiling to `min(hi, prefixCeil(leafDir))`; reflect from either the range's own `lo` (if it is a real deep key inside the leaf, zero probes) or from a one-key floor probe of the leaf directory; the reflected pivot is accepted only if it is not itself the adjacent sliver and lies strictly in `(c, hi)`. A rejected pivot falls through to the byte-exact sliver unchanged.

The underlying pivot primitives (`ByteMidpoint`, safe scalar set `E`, `MIN_SAFE = U+0020`, `MAX_KEY_LEN = 1024` cap-fallback, `forwardReflect`'s `2·idx(c[i]) − idx(lo[i])`) operate over **Unicode code points**, decoded/re-encoded, so every synthesized pivot is valid UTF-8 by construction `[SRC swath-model/src/main/java/io/varve/swath/model/ByteMidpoint.java:34-39, 106-126, 168-170, 269 @ cef8ec2]`.

`AlphabetDigest` (rank-space pivots) tracks only printable ASCII, only 8 positions past the range's birth divergence, and permanently disqualifies a position on the first non-printable/multi-byte scalar `[SRC .../engine/AlphabetDigest.java:53-58, 88-99 @ cef8ec2]`. `[DOC algorithms.md §3.3]` reports it never engaged across the 13-shape matrix; the code's structural bias toward `NO_SCALAR` is consistent with that `[INFERRED]`. Any scalar it returns is re-validated for safety and strict betweenness by `ByteMidpoint.pickScalar` `[SRC ByteMidpoint.java:321-334 @ cef8ec2]`.

### Owner self-split (`OwnerSelfSplit` + `OwnerSplitGovernor`)
Runs **inside the page-commit lock region**, after the cursor advanced and the commit was enqueued, only when `toggles.ownerSplit() && madeProgress && !completed` `[SRC .../WorkStealingScan.java:699-701 @ cef8ec2]`. Because the owner holds its own lock and picks `m > cursorTo`, the CAS holds by construction `[SRC .../OwnerSelfSplit.java:144-148 @ cef8ec2]`. Zero extra API calls (`interpolate` is pure math).

Gate chain, in order `[SRC .../engine/policy/OwnerSplitGovernor.java:84-259 @ cef8ec2]`:
1. `hi == null` → `OPEN_FRONTIER` skip (**the open frontier is never owner-carved**).
2. `est <= SELF_SPLIT_MIN_REMAINING_PAGES(4) × maxKeys` → `REMAINING_EST_FLOOR`.
3. `pagesSinceLastSelfSplit < SELF_SPLIT_MIN_PAGES_BETWEEN(32)` → `RATE_LIMITED`.
4. `workerCount > 1 && outstanding >= workerCount` → `DEMAND_GATED` (threshold is `Tmax`, not the gauge's effective `T`).
5. child-tail floor (`tail_floor` mode, §6 below) → `FLOOR_REFLECTED_BLOCKED`.
6. confetti feedback gate — once `taggedTotal >= MIN_SAMPLE`, a confetti rate `> 0.5` suppresses, with every `PROBE_K = 16`-th carve let through as a probe `[SRC .../OwnerSplitGovernor.java:58-59, 166-191 @ cef8ec2]`. The probe slot is a **CLAIM** resolved by the executor against a run-scoped gate so concurrent owners sharing a snapshot cannot all carve `[SRC .../OwnerSelfSplit.java:189-198 @ cef8ec2]`.
7. pivot synthesis at `f`, then reject if `m == null || cursorTo >= m || m > H`.
8. **reflection clamp** down to `m_r` when the interpolate overshot and the clamped tail still clears the floor; then **reflect-lift** up to `m_r` when the owner's kept share is under one page `[SRC .../OwnerSplitGovernor.java:226-256 @ cef8ec2]`, `[SRC .../StealMath.java:437-511 @ cef8ec2]`.

Realized-mass feedback: a carved child is **tagged before publish** (publishing makes it claimable, so tagging after would race a fast drainer) `[SRC .../OwnerSelfSplit.java:96-114, 224-229 @ cef8ec2]`; classified exactly once at completion as confetti iff `keysEmitted <= 2·maxKeys && !hasSplit` `[SRC .../OwnerSelfSplit.java:348-350 @ cef8ec2]`. All of it is process-local and never checkpointed — a resumed child completes untagged, contributing no classification rather than a wrong one `[SRC .../OwnerSelfSplit.java:110-114 @ cef8ec2]`.

---

## 3. Range scanning (`RangeScanner`)

Boundary semantics are fixed in one place: **strict** `k > hi` stops, so `k == m` stays with the victim (boundary-belongs-left):
```java
byte[] keyHiNow = hiSupplier.get();
byte[] k = e.key().rawUnsafe();
if (keyHiNow != null && KeyBytes.compareUnsigned(k, keyHiNow) > 0) {
    reachedBound = true;   // do NOT emit k
    break;
}
```
`[SRC .../engine/RangeScanner.java:241-246 @ cef8ec2]`. The bound is re-read **per key**, not per page, from a `Supplier` that `WorkerState` backs with an `AtomicReference` `[SRC .../WorkerState.java:351-353 @ cef8ec2]` — so a thief's narrow takes effect mid-page.

Completion is `done = reachedBound || !pageTruncated` `[SRC .../RangeScanner.java:258 @ cef8ec2]`, computed **before** the I9 defenses so a legitimately-empty post-narrow page completes the node instead of tripping the broken-page check. `lastKey` stays null on an empty batch, so the cursor is left unchanged `[SRC .../RangeScanner.java:254-257, 283-289 @ cef8ec2]`.

I9 pagination defenses, run **before** the consumer (so nothing broken is committed or emitted) `[SRC .../RangeScanner.java:265-281 @ cef8ec2]`:
- `!done && batch.isEmpty()` → `ListingException("truncated page returned no keys <= hi")`.
- `startAfter != null && lastKey <= startAfter` → `ListingException("no forward progress (stuck listing)")`.

Pagination is purely `startAfter = lastKey` — no continuation token in the model `[SRC .../RangeScanner.java:294 @ cef8ec2]`.

**Ordering assumptions.** The loop depends on each page arriving in ascending unsigned byte order (it `break`s at the first out-of-range key and takes the last as the next cursor) and on `start_after` being exclusive. Comparisons are unsigned byte-exact via `Arrays.compareUnsigned` throughout `[SRC swath-model/.../KeyBytes.java:45-52 @ cef8ec2]` — `String.compareTo` is never used for ordering.

One precision worth recording: after a narrow lands mid-page, `RangeScanner` continues from its **pre-trim** `lastKey`, while the committed cursor is the **post-trim** last in-range key (§5). The next fetch then returns only keys `> m`, all of which fail the per-key check, so the node completes with the earlier cursor and the child covers the remainder — no key is lost, one page of fetch is wasted `[INFERRED from SRC .../RangeScanner.java:288-294 and .../WorkStealingScan.java:683-693]`.

---

## 4. Concurrency & termination

### Pool shape
`workerCount` (= configured `--concurrency`) virtual threads are forked once, plus one receiver watcher `[SRC .../WorkStealingScan.java:421-433 @ cef8ec2]`; `Scope` is `Executors.newVirtualThreadPerTaskExecutor()` `[SRC .../concurrent/Scope.java:21, 45 @ cef8ec2]`. Each worker loops claim → run → repeat, and **never holds its slot waiting on child work** — when nothing is claimable it becomes a thief `[SRC .../WorkStealingScan.java:514-535, 547-603 @ cef8ec2]`. Each worker builds its own `RangeScanner` wrapping a slot-gated `GaugedFetcher`, plus the shared off-gauge speculative fetcher and the gauge's stealing gate `[SRC .../WorkStealingScan.java:506-511 @ cef8ec2]`.

### Termination ledger (`Worklist`)
A single `ReentrantLock gate` + `Condition work` guards a ready `ArrayDeque` and an `AtomicLong outstanding`; all four transitions and the timed park run under it `[SRC .../engine/Worklist.java:41-47, 88-172 @ cef8ec2]`.
- Increment: seeds via `initOutstanding`; a split child inside `enqueue`, which the thief/owner calls **while still holding the victim lock** `[SRC .../Worklist.java:111-121 @ cef8ec2]`, `[SRC .../WorkStealingScan.java:908-916 @ cef8ec2]` — so quiescence can never observe a false zero between hand-off and count.
- Decrement: once per COMPLETED node `[SRC .../WorkStealingScan.java:814-817 @ cef8ec2]`.
- `poll()` returns `quiescent` only when the queue is empty **and** `outstanding == 0`, broadcasting so peers terminate too `[SRC .../Worklist.java:88-103 @ cef8ec2]`.
- `park()` re-checks the condition and awaits under the same gate hold — the lost-wakeup guard `[SRC .../Worklist.java:158-172 @ cef8ec2]`.
- Lock order is strictly **victim → gate**; idle workers always release the gate before stealing `[SRC .../Worklist.java:26-30 @ cef8ec2]`.

### One fleet-wide steal attempt
`IdleStealBackoff` holds a monitor-guarded `attemptInFlight` boolean; ownership is enforced by contract — the acquirer releases in a `finally` covering the whole acquired region, then broadcasts on the ledger `[SRC .../WorkStealingScan.java:565-589 @ cef8ec2]`, `[SRC .../IdleStealBackoff.java:66, 87-98, 133-135 @ cef8ec2]`. Pacing (base 5 ms, cap 50 ms, exponential in consecutive non-productive outcomes) is separate from slot ownership; `reset()` never touches the slot, so a reset by an unrelated worker cannot admit a second concurrent attempt `[SRC .../IdleStealBackoff.java:148-157 @ cef8ec2]`, `[SRC .../policy/IdleStealPacingPolicy.java:36-73 @ cef8ec2]`. A denied worker parks on a **1 s** backstop, not the 5 ms pacing base `[SRC .../WorkStealingScan.java:86-93 @ cef8ec2]`.

### AIMD makes `--concurrency` a ceiling
`ConcurrencyGauge` wraps a `Semaphore` subclass whose permits are resized in lockstep with an `AtomicInteger effectiveT` `[SRC .../ConcurrencyGauge.java:141, 808-822 @ cef8ec2]`. `tMax` (= `workerCount`) appears **only as an upper clamp**: a fresh run starts at `min(4, tMax)` `[SRC .../ConcurrencyGauge.java:62, 274-276 @ cef8ec2]`, growth is `min(tMax, T*2)` while slow-starting and `min(tMax, T+1)` after the congestion latch `[SRC .../ConcurrencyGauge.java:754-756 @ cef8ec2]`, and every decrease is `max(1, floor(factor·T))` with no floor above 1 `[SRC .../ConcurrencyGauge.java:626 @ cef8ec2]`. So `T` virtual-thread workers exist, but only `effectiveT ≤ tMax` of them can hold a fetch permit at once, and the steady-state level is set by the store, not by the flag `[INFERRED from the above]`.

Decrease triggers, both funnelling through `multiplicativeDecrease` `[SRC .../ConcurrencyGauge.java:594-651 @ cef8ec2]`:
- 503/`SlowDown` → factor `0.7`, records a real AIMD vote `[SRC .../ConcurrencyGauge.java:57, 324-331, 562-567 @ cef8ec2]`.
- Sustained-timeout shed → factor `0.5`, once per rolling ~30 s window (jittered 25–40 s), gated on `timeouts >= max(3, ceil(0.3·T))` **and** `successes <= max(1, T/32)` `[SRC .../ConcurrencyGauge.java:93-109, 489-526 @ cef8ec2]`. The starvation clause is load-bearing: a timeout tail on a progressing run never sheds.
Both set `stealingAllowed = false` unconditionally; the 10 s clean-window re-arm (`lastThrottleNs`) is set **only on a real reduction**, not on a floor no-op `[SRC .../ConcurrencyGauge.java:608, 624-644 @ cef8ec2]`. A worker above the new `T` is never killed mid-page — `reducePermits` only defers future acquires `[SRC .../ConcurrencyGauge.java:48-51, 641-642 @ cef8ec2]`.

Growth is additionally gated by two freezes: a hard worker-timeout freeze (≥3 in 10 s) and a latency-inflation freeze (EWMA > 2× a Vegas rolling-minimum baseline). The latter is demoted to a paced valve admitting one `+1` per ~30 s while the run is not starved; the hard freeze is checked first and the valve never relaxes it `[SRC .../ConcurrencyGauge.java:83-138, 473-535, 690-731 @ cef8ec2]`.

Probe-class faults are excluded from the congestion latch and from the shed gate `[SRC .../ConcurrencyGauge.java:369-377 @ cef8ec2]` — thief probes and speculative readahead both fetch through `slotGated=false, reportSuccess=false` fetchers `[SRC .../WorkStealingScan.java:268-269, 293-294 @ cef8ec2]`.

---

## 5. Correctness invariants (disjointness & exhaustiveness)

This is the strongest part of the design. Five mechanisms, each closing a distinct hazard:

**I-a. Seed tiling is exact for any cut set.** `tile()` emits consecutive half-open intervals ending in `(c_last, null]` `[SRC .../SeedStep.java:237-246 @ cef8ec2]`, inserted all-or-nothing `[SRC .../CheckpointStore.java:61-72 @ cef8ec2]`. Every seed heuristic (subsample, banding, partition tiling, sentinel) only chooses *which* cuts exist — coverage is structurally independent of them.

**I-b. Boundary belongs left.** The single strict `k > hi` comparison `[SRC .../RangeScanner.java:243 @ cef8ec2]` paired with the child's `cursor = m` (exclusive `start_after`) `[SRC .../checkpoint/SqliteCheckpointStore.java:611 @ cef8ec2]` places `m` in exactly one interval. The wrong convention here is a one-key gap or overlap at *every* split `[DOC algorithms.md §1.2]`.

**I-c. Two independent narrow defenses.** The lock-free per-key `hi` re-read catches keys not yet pulled from an in-flight page. It **cannot** retract a key already batched under the old bound — that residual window is closed by an under-lock re-trim at commit:
```java
hiAtCommit = ws.hiSupplier().get();
inRange = inRange(batch, hiAtCommit);
cursorTo = inRange.isEmpty() ? null : inRange.getLast().key().rawUnsafe();
```
`[SRC .../WorkStealingScan.java:683-685 @ cef8ec2]`, with `inRange` dropping the trailing keys now above `hi` `[SRC .../WorkStealingScan.java:844-853 @ cef8ec2]`. Downstream emission uses `inRange`/`kept`, never the raw `batch` `[SRC .../WorkStealingScan.java:739-763 @ cef8ec2]` — this is what makes the re-trim actually prevent a double-emit rather than merely correct the cursor.

**I-d. Durable CAS with three clauses.** Verified in source, matching the doc:
```sql
UPDATE listing_node SET range_end=?, generation=generation+1, updated_at=?
 WHERE id=? AND (cursor IS NULL OR cursor < ?) AND range_end IS ? AND status<>'COMPLETED'
```
`[SRC .../SqliteCheckpointStore.java:588-591 @ cef8ec2]`; rowcount 0 → `SPLIT_ABORTED`, child not inserted `[SRC .../SqliteCheckpointStore.java:599-601 @ cef8ec2]`. UPDATE + INSERT run in one writer-thread `SqlOp` on a connection in explicit-transaction mode `[SRC .../SqliteCheckpointStore.java:129, 584-620 @ cef8ec2]`, so a crash mid-split leaves both rows or neither.

**I-e. Two distinct loser paths, and only one restores `hi`.** `[SRC .../Thief.java:301-389 @ cef8ec2]`:
- Early loser (`victim.hi() != H` under the lock) → **does not touch `hi`**; restoring would clobber the winner's narrow and re-open an overlap `[SRC .../Thief.java:316-324 @ cef8ec2]`.
- Late loser (narrowed, then CAS aborted) → `restoreHi(H)`, safe because `H` was validated under the lock `[SRC .../Thief.java:343-351 @ cef8ec2]`.
- Success → narrow, durable split, `childSink.accept` (increments `outstanding` under the lock), `markStolen()` `[SRC .../Thief.java:337-361 @ cef8ec2]`.
The owner-split path mirrors the late-loser restore for a CAS that "cannot happen by construction" `[SRC .../OwnerSelfSplit.java:216-223 @ cef8ec2]`.

**What would break it.** From the code's own structure `[INFERRED]`:
- Dropping the `volatile`/`AtomicReference` on `hi` (the per-key reader is lock-free — it needs the happens-before with the thief's write under `victim.lock`) `[SRC .../WorkerState.java:29-33 @ cef8ec2]`.
- Emitting `batch` instead of `inRange` downstream — the per-key check alone is provably insufficient.
- Making the stop check non-strict (`k >= hi`) or the child's cursor anything other than `m`.
- Reversing the lock order (gate→victim) — the current order is victim→gate everywhere `[SRC .../Worklist.java:26-30 @ cef8ec2]`.
- Advancing the victim's `cursor` on an empty batch — the cursor is only set when `!inRange.isEmpty()` `[SRC .../WorkStealingScan.java:686-692 @ cef8ec2]`, and `commitPage` uses a cursor-less SQL variant when `advanceTo == null` `[SRC .../SqliteCheckpointStore.java:562-566 @ cef8ec2]`.

**What is *not* protected by a runtime check.** Strict betweenness of a synthesized pivot (`a < m < b`) is re-verified in only two places — `pickScalar`'s scalar-index check and `capFallback`'s `compareUnsigned(enc, b) < 0` `[SRC ByteMidpoint.java:321-334, 439 @ cef8ec2]`. The direct returns in `between`'s Path A/B and in `forwardReflect` carry no runtime assertion against the actual byte-array bounds (no `assert` keyword appears in `ByteMidpoint`/`KeyBytes`/`AlphabetDigest`). The consumers do partially compensate: `ThiefPolicy.flatLeafPivot` and `beginReflectOrBisect` re-check betweenness explicitly `[SRC .../ThiefPolicy.java:510-512, 583-587 @ cef8ec2]`, `OwnerSplitGovernor` re-checks `cursorTo < m <= H` `[SRC .../OwnerSplitGovernor.java:195-197 @ cef8ec2]`, and the CAS's `cursor < pivot` is a final durable backstop. But the plain interpolate path in `Attempt.start()` commits `m` without an explicit betweenness re-check before the lock. **In practice the CAS clause and the per-key/commit-time trims still make a bad pivot a balance bug rather than a coverage bug** — the tiling holds for *any* `m` the CAS accepts `[INFERRED]`.

**Non-snapshot pagination.** `[DOC algorithms.md §6]` claims a key inserted behind a passed cursor is missed (same as any paginated lister) and that overlap is structurally zero. The structural-zero half is corroborated by I-b/I-c/I-d above `[INFERRED]`; the insertion behaviour is not something source alone can confirm.

---

## 6. The 0.1.0 → 0.2.0 engine delta

| commit | what it changed |
|---|---|
| `d18487c` | Adds `TailFloorMode` (`CURRENT`/`EST_DIRECT`/`REACH_FLOORED`) and threads the mode through all three floor consult sites, plus `TAIL_FLOOR.*` divergence counters. Default stays `CURRENT` at this commit `[GIT d18487c]` |
| `3b695c3` | **The actual flip**: `EngineToggles.DEFAULT` moves `rate_anchored_sensing` false→true and `tail_floor` `CURRENT`→`REACH_FLOORED`; `parse()`'s fallbacks move identically `[GIT 3b695c3]` |
| `6788d05` | Observability only — the governor's `hi == null` early-out now emits `OWNER_SPLIT.open_frontier`, and a new `swath.open_frontier.keys_emitted` gauge attributes post-filter keys drained on the open frontier `[GIT 6788d05]`, `[SRC .../WorkStealingScan.java:743-752 @ cef8ec2]` |
| `00d8528` | Independent seed-time cure: `appendOpenTileSentinel` in `HybridSeedPlanner` `[GIT 00d8528]`, `[SRC .../HybridSeedPlanner.java:643-689 @ cef8ec2]` |
| `bf0bac8` | No behavioural change to `EngineToggles`/`TailFloorMode` — javadoc and startup log-text only, plus test-only fixes `[GIT bf0bac8]` |

### `rate_anchored_sensing` (default ON)
New reading `[SRC .../RateAnchoredEstimator.java:92-103 @ cef8ec2]`:
```java
if (hi == null) return +INF;
if (cursor >= hi) return 0.0;
geometry = StealMath.anchoredGeometricFactor(cursor, lo, hi);
banded   = min(GEOMETRY_BAND=16.0, max(minGeometry=1/4, geometry));
return max(keysEmitted, pageSize) * banded;
```
Magnitude is the range's own proven mass (Pareto-2 mean residual life), floored at one page so an un-started range is not scored zero and dropped from selection. Geometry is measured in a window anchored at the **cursor's own divergence from `lo`** rather than at `[lo,hi]`'s `[SRC .../StealMath.java:135-145 @ cef8ec2]`, which is precisely the term the old reading loses to a deep shared prefix.

Pre-0.2.0 (`=off`) is `StealMath.estRemaining` — `(keysEmitted/consumed) × remaining`, both spans read over `[lo,hi]`'s divergence window, degenerating to a raw width when `consumed` underflows to 0.0 `[SRC .../StealMath.java:106-112 @ cef8ec2]`.

Consumers of the seam (one estimator per run, shared): `Thief`'s victim selection `[SRC .../ThiefPolicy.java:119 @ cef8ec2]`, `OwnerSplitGovernor`'s gate chain `[SRC .../OwnerSplitGovernor.java:102 @ cef8ec2]`, and the `slow_ranges[]` diagnostic `[SRC .../WorkStealingScan.java:362 @ cef8ec2]`. **Deliberately outside the seam**: `RangeScanner`'s readahead engage gate, which calls `StealMath.estRemaining` directly because its frame is the drain streak's own `lo`, with an explicit call-site rationale `[SRC .../RangeScanner.java:343-347, 363 @ cef8ec2]`.

### `tail_floor` (default `reach_floored`)
One function, three arms `[SRC .../StealMath.java:410-420 @ cef8ec2]`:
```java
double floor = 2.0 * maxKeys;
if (mode == EST_DIRECT) return est <= floor;
double reach = Math.min(1.0, densityRatio) - f;
double minReach = (mode == REACH_FLOORED) ? TAIL_REACH_MIN : 0.0;   // TAIL_REACH_MIN = 1/16
return est * Math.max(minReach, reach) <= floor;
```
The `CURRENT` blindness is structural, not statistical: when `min(1, densityRatio) - f <= 0` the product is exactly `0` **for any `est`**, so an honest large estimate is multiplied away before the comparison. `REACH_FLOORED` floors the reach term so geometry shrinks the child's share instead of erasing it; where real reach exceeds 1/16 the two arms are byte-identical. Both cures are monotonically more permissive than `CURRENT`. The mode is consulted at three sites — the gate, the reflection clamp, the reflect-lift — each of which additionally evaluates `CURRENT`'s verdict and records the divergence under its own reason prefix `[SRC .../OwnerSplitGovernor.java:268-326 @ cef8ec2]`.

### Both defaults confirmed in code (not just docs)
```java
public static final EngineToggles DEFAULT =
    new EngineToggles(true,…,true, TailFloorMode.REACH_FLOORED);
…
values.getOrDefault(RATE_ANCHORED_SENSING_NAME, true),
tailFloor == null ? TailFloorMode.REACH_FLOORED : tailFloor);
```
`[SRC .../EngineToggles.java:187-189, 310-311 @ cef8ec2]`, selected at `remainingWorkEstimator(maxKeys)` `[SRC .../EngineToggles.java:456-460 @ cef8ec2]`.

### ⚠ Docs-drift finding (three stale source javadocs)
Post-`3b695c3`, three class-level javadocs still describe the pre-flip defaults and were not swept by `bf0bac8`:
- `TailFloorMode`: "`CURRENT` is the shipped formula and the default" `[SRC .../TailFloorMode.java:14-15 @ cef8ec2]`
- `RateAnchoredEstimator`: "**Not the default:** a run steers on this only under `--engine-toggle rate_anchored_sensing=on`" `[SRC .../RateAnchoredEstimator.java:17-18 @ cef8ec2]`
- `RemainingWorkEstimator`: `WINDOW` is "the shipped reading and the default … and the only supported one" `[SRC .../RemainingWorkEstimator.java:17-19, 61-62 @ cef8ec2]`

All three contradict `EngineToggles`. Anyone reading the engine from javadoc alone would model 0.2.0's defaults backwards. Worth flagging to the study as a source-reliability datum: **the vendor's own in-source documentation is not a reliable statement of shipped defaults at this revision.**

---

## 7. `readahead` (off by default)

Toggle `engine.readahead` / `--engine-toggle readahead=on`; `EngineToggles` defaults it to `false` and `ReadaheadConfig.off()` is what a non-opting run gets `[SRC .../EngineToggles.java:202, 308 @ cef8ec2]`, `[SRC .../ReadaheadConfig.java:92-96 @ cef8ec2]`, `[SRC .../WorkStealingScan.java:510 @ cef8ec2]`. Off, `RangeScanner` never constructs a `SpeculativeReadahead` and emits no `READAHEAD.*` counters.

**Engage gate** — five AND-ed conditions, evaluated once per completed page `[SRC .../RangeScanner.java:333-335 @ cef8ec2]`: toggle on; `hi != null` (**never the open frontier**); both page endpoints known; `consecutiveFullSerialPages >= engageAfterFullPages` (default 6, where a "full page" is `!adopted && pageTruncated && batch.size() >= maxKeys` `[SRC .../RangeScanner.java:315 @ cef8ec2]`, `[SRC .../ReadaheadConfig.java:64 @ cef8ec2]`); and `firstKey < lastKey`. The streak resets to 0 on **any** `hi` narrow `[SRC .../RangeScanner.java:195-199 @ cef8ec2]`. A further remaining-pages floor exists but is disabled by default (`DEFAULT_MIN_ENGAGE_REMAINING_PAGES = 0.0`) `[SRC .../ReadaheadConfig.java:81 @ cef8ec2]`, `[SRC .../RangeScanner.java:361-369 @ cef8ec2]`.

**Guess placement** — `StealMath.extrapolate(stepLo, stepHi, hi)` over a trailing one-page reflection window, i.e. `G = c + (c − lo)` in code-point space, re-checked to lie strictly in `(stepHi, hi)` `[SRC .../SpeculativeReadahead.java:360-367, 342-343 @ cef8ec2]`.

**Adoption dispositions** `[SRC .../SpeculativeReadahead.java @ cef8ec2]`: ADOPT only when `buffer.floorKey(cursor)` yields a guess `G <= cursor`, trimmed to keys `> cursor` (`:448-466`); HOLD is the residual (`G > cursor` stays buffered); DISCARD when the page is fully overlapped `last <= cursor` (`:442-446`); CANCEL when `G >= hi` after a narrow (`:236, 249, 420-423, 437-440`); FAULT absorbed and dropped, except `ProtocolViolationException` which propagates (`:291-307, 390-409`).

**Safety.** Adopted and serial pages take the **identical** code path: same per-key `hi` check, same I9 defenses, the same single `consumer.accept(batch, lastKey, done)` `[SRC .../RangeScanner.java:234-289 @ cef8ec2]`. Readahead never emits or commits anything itself; the committed cursor still advances only through contiguous fetched keys. So I1 commit-before-emit and byte-exact in-order emission hold by construction `[SRC .../SpeculativeReadahead.java:38-48 @ cef8ec2]`.

**Bounds.** `K = 8` guesses (in-flight + buffered ≤ K) `[SRC .../ReadaheadConfig.java:56 @ cef8ec2]`, `[SRC .../SpeculativeReadahead.java:337 @ cef8ec2]`; dedicated off-gauge fetcher (`slotGated=false, reportSuccess=false`) so a guess never takes an AIMD permit, never casts a happy-path growth vote, and cannot reach the worker fetcher's run-cancelling give-up path `[SRC .../WorkStealingScan.java:285-294 @ cef8ec2]`; 30 s wall-clock budget per guess, enforced both by a timed `Future#get` and a proactive reclaim `[SRC .../ReadaheadConfig.java:89 @ cef8ec2]`, `[SRC .../SpeculativeReadahead.java:389-394, 512-522 @ cef8ec2]`; new launches suppressed while `stealingAllowed` is false `[SRC .../SpeculativeReadahead.java:334-336 @ cef8ec2]`.

**Disengage.** A **tumbling** window of `2·K = 16` pages; if adopted ≤ `0.40 × window` the range disengages and reverts to plain serial, resetting the streak so a later drain can re-engage `[SRC .../ReadaheadConfig.java:70, 75 @ cef8ec2]`, `[SRC .../AdoptionWindow.java:49-70 @ cef8ec2]`, `[SRC .../RangeScanner.java:393-407 @ cef8ec2]`.

All readahead state is process-local and never durable; a crash discards it and resume re-lists forward `[DOC algorithms.md §2.1]`, consistent with the absence of any checkpoint write in `SpeculativeReadahead` `[INFERRED]`.

---

## 8. Notes for the wider study

- **Not yet wired, confirmed in source**: `seed.mode=hints` throws `InvalidConfigException` `[SRC .../SeedStep.java:160-161 @ cef8ec2]`. `VERSIONS` mode and Express One Zone are `[DOC algorithms.md §9/§10]` design-of-record only.
- **Per-attempt probe cost is bounded, not O(1)**: 1 initial + ≤1 step-back + ≤4 structure (1 + 3 back-out) + ≤1 reflect + ≤`ceil(log2(band))+6` bisect + ≤1 floor probe. All within one steal attempt, and at most one steal attempt is in flight fleet-wide.
- **The open frontier is the engine's structural weak spot**, and 0.2.0 attacks it from two sides: the owner-split governor refuses it outright `[SRC .../OwnerSplitGovernor.java:86-93 @ cef8ec2]`, readahead refuses it `[SRC .../RangeScanner.java:333 @ cef8ec2]`, and only a thief's `extrapolate` can carve it — which is exactly why `00d8528`'s seed sentinel (giving the mass-bearing tile a finite `hi`) and `6788d05`'s attribution counter both exist.
- I did **not** transcribe any performance/benchmark figures. `docs/performance.md` and `swath-sim` outputs exist and are vendor self-reported; several code comments and `algorithms.md` embed specific measured numbers (e.g. steal-success rates, per-bucket tail statistics) which should be treated as unverified claims, not results.
