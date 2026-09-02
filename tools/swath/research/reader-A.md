# Reader A — engine, concurrency, retry, watchdog: v0.2.0 (cef8ec2) → v0.3.1 (7b9a5e2)

Read-only re-verification of 17 supported claims and 3 unverified ones. Every line number below was
checked with sed/awk on `/home/sagi_varve_io/workspaces/swath-v0.3.1`. Companion ledger: `reader-A.json`.

## Verdict counts

holds 10 · holds-reanchored 7 · changed 3 · contradicted 0 · gone 0. All three unverified claims stay
unverified for the same reasons as before.

## What did not change

The heart of the engine is byte-identical between the two tags: `Worklist.java`, `policy/ThiefPolicy.java`,
`policy/HybridSeedPlanner.java`, `RateAnchoredEstimator.java`, `WorkerState.java`, `SeedStep.java`, `Thief.java`,
`pipeline/Channel.java`, `store/ApiRateLimiter.java`, `store/RateLimitedPageFetcher.java` and the CLI's
`EngineOptions.java`. `EngineToggles.java`, `TailFloorMode.java`, `OwnerSplitGovernor.java` and
`StealMath.java` changed only in comments (doc path `docs/usage.md` → `docs/configuration.md`) or by a moved
helper (`ByteMidpoint.isValidUtf8` became `KeyBytes.isValidUtf8`, called at
[SRC swath-core/src/main/java/io/varve/swath/engine/StealMath.java:322 @ 7b9a5e2]). `LivenessWatchdog.java`
changed only in javadoc; both windows (120 s idle, 10 m no-progress) and the escalation ladder are identical
[SRC swath-core/src/main/java/io/varve/swath/runtime/LivenessWatchdog.java:263-341 @ 7b9a5e2],
[SRC swath-cli/src/main/java/io/varve/swath/cli/ListOptionGroups.java:55-67 @ 7b9a5e2]. The split CAS is
identical text, shifted five lines [SRC swath-core/src/main/java/io/varve/swath/checkpoint/SqliteCheckpointStore.java:589-625 @ 7b9a5e2].
The 0.3.1 release itself touches only `swath-replay`; the changelog says the `swath` CLI is unchanged in 0.3.1.

Consequences: `listing-is-adaptive-density-aware`, `pivot-placement-is-multi-phase`, `seed-descent-is-serial`,
`no-dedup-pass-by-construction`, `v020-engine-default-flips`, `seed-hints-unimplemented`, `watchdog-two-tripwires`
hold at the same anchors. `work-stealing-range-engine`, `termination-cannot-see-false-quiescence`,
`internal-tiling-is-disjoint`, `split-commit-is-atomic-cas`, `queue-size-is-entry-budget`,
`request-rate-limiter-inside-retry-loops` and `aimd-adapts-to-503` hold with moved anchors (WorkStealingScan +2
lines up to the emission block and +28 after it; ConcurrencyGauge +23/+26 after line 331; ListCommand
retry-policy selection 472-474 → 514-516 and fetcher wiring 849-862 → 944-957).

## What changed (the three `changed` verdicts)

**1. `--concurrency` is now called a ceiling by upstream itself.** The help string reads "AIMD ceiling for
concurrent listing requests (default: 64)" [SRC swath-cli/src/main/java/io/varve/swath/cli/ConnectionOptions.java:78-81 @ 7b9a5e2]
(0.2.0 said "Maximum concurrent listing requests"). `docs/cli.md` line 53 says "Set the adaptive
listing-concurrency ceiling", `docs/configuration.md` gained a "Choosing `--concurrency`" section
[SRC docs/configuration.md:58-67 @ 7b9a5e2], and algorithms.md §5 now opens with "`--concurrency` supplies the
ceiling `Tmax`" [SRC docs/internals/algorithms.md:1085-1086 @ 7b9a5e2] and adds a "Capacity boundary" paragraph:
AIMD is reactive backpressure, never searches `T` back down for efficiency, a clean endpoint can reach a high `T`,
`Tmax` is an operator resource ceiling, and "a replay experiment on one bucket or latency regime is not a universal
S3 cap" [SRC docs/internals/algorithms.md:1185-1194 @ 7b9a5e2]. The gauge itself is unchanged
(`min(4,Tmax)` start [SRC .../engine/ConcurrencyGauge.java:274-276 @ 7b9a5e2], growth
[SRC .../engine/ConcurrencyGauge.java:780-782 @ 7b9a5e2], decrease [SRC .../engine/ConcurrencyGauge.java:649 @ 7b9a5e2]).
Claim `concurrency-flag-is-aimd-ceiling` keeps its substance; its qualification should stop implying the ceiling
reading is a first-party discovery against upstream's wording.

**2. Engine toggles are hidden from help.** `--engine-toggle` is `hidden = true` but still parses
[SRC swath-cli/src/main/java/io/varve/swath/cli/ListOptionGroups.java:100-101 @ 7b9a5e2]; `docs/cli.md`
108-112 says hidden options still parse for controlled investigations; the toggle table and the one supported
rollback pair (`rate_anchored_sensing=off` + `tail_floor=current`) moved to "Diagnostic engine toggles"
[SRC docs/configuration.md:147-183 @ 7b9a5e2]. `NAMES` (fourteen), defaults and parser are identical
[SRC swath-core/src/main/java/io/varve/swath/engine/EngineToggles.java:197-231,257-296 @ 7b9a5e2]. Claim
`engine-toggles-are-diagnostic` needs a qualification sentence, nothing more.

**3. Seed fail-fast on an unreachable endpoint.** `TransientRetryFetcher.forSeed` builds the seed decorator
with `failFastUnreachableEndpoint = true` [SRC swath-core/src/main/java/io/varve/swath/engine/TransientRetryFetcher.java:155-160 @ 7b9a5e2];
in the catch block, before `transientRetries++`, a `ThrottleException` whose cause chain contains
`ConnectException` or `UnknownHostException` throws `ListingException("seed probe failed because the object-store
endpoint is unreachable")` and records `steal_reason{FATAL, seed_endpoint_unreachable}`
[SRC .../engine/TransientRetryFetcher.java:205-211 @ 7b9a5e2], [SRC .../engine/TransientRetryFetcher.java:264-275 @ 7b9a5e2].
`seedFreshRun` wires it [SRC swath-cli/src/main/java/io/varve/swath/cli/ListCommand.java:1350-1351 @ 7b9a5e2] and runs
for a fresh run or a zero-node resume [SRC .../cli/ListCommand.java:970-982 @ 7b9a5e2]. The engine fetcher
(`GaugedFetcher`) and mid-run structure probes keep the old loops. So `retry-default-is-ride-out` is still true
for over-cap transient faults, with this one seed-path carve-out added to its qualification. Changelog:
[SRC CHANGELOG.md:75-76 @ 7b9a5e2]; registry row [SRC docs/internals/metrics-internals.md:928 @ 7b9a5e2].

## Smaller engine edits worth knowing

- **AIMD success-path ordering.** `ConcurrencyGauge.reportCompletedAttempt` applies a successful page's latency
  sample before `reportStatus` can claim a paced growth step; 503 pages keep the throttle path and are not
  latency-sampled [SRC .../engine/ConcurrencyGauge.java:340-349 @ 7b9a5e2]; `GaugedFetcher` calls it and records a
  new per-success AIMD metric [SRC .../engine/GaugedFetcher.java:157-165 @ 7b9a5e2]; documented at
  [SRC docs/internals/algorithms.md:1179-1183 @ 7b9a5e2]. Factor 0.7, the 0.5 timeout shed and both growth
  freezes are unchanged [SRC .../engine/ConcurrencyGauge.java:57,83-138,585-590,716-757 @ 7b9a5e2]. algorithms.md
  also dropped its stale sentence about an optional latency-EWMA *decrease* trigger; the source never had one.
- **Terminal-page regression guard.** `RangeScanner`'s "no forward progress" check now fires on any non-empty
  page, including the terminal one (`lastKey != null` instead of `!done`)
  [SRC .../engine/RangeScanner.java:274-281 @ 7b9a5e2]; an empty terminal page stays legal. Relevant to replay
  fidelity, invisible on well-behaved S3.
- **Sort-mode completion marker.** When a node's completing page retains no rows (empty after the hi trim or
  after filters), `WorkStealingScan` now sends `PageBatch.completion` to the sort tripwire
  [SRC .../engine/WorkStealingScan.java:738-750 @ 7b9a5e2], [SRC .../engine/WorkStealingScan.java:835-846 @ 7b9a5e2].
  Sort-only, emits no keys; disjointness and dedup claims unaffected.
- **`--tune` registry.** Engine-related keys unchanged (`engine.readahead` experimental, `seed.mode` stable with
  `hints` reserved) [SRC swath-cli/src/main/java/io/varve/swath/cli/TuneOptions.java:24-43 @ 7b9a5e2]; the new keys
  (`sort.merge-parallelism`, `sort.keep-staging`, `parquet.writers` 2..64) are sort/output area.

## Unverified claims

- `aimd-necessity`: still no throttle or high-concurrency run; upstream's new "Capacity boundary" prose is
  consistent with the hypothesis but is not a measurement.
- `seed-cost-direction-at-smoke`: still no instrumented shallow-vs-none comparison; the seed mechanism is
  byte-identical, so a 0.3.1 comparison measures the same thing.
- `exactly-once-under-crash`: still no crash/resume run; memory-backed `--checkpoint auto` on stdout still applies.
  Note for a future test: 0.3.0's page-run staging is format v4 with no legacy read path (0.2.x sorted runs cannot
  be resumed by 0.3.x).
