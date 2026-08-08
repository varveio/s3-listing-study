# Reader B — Store layer, request behaviour, failure surface

Subject: `swath` @ `cef8ec2` (= v0.2.0), read at `/home/vscode/.s3-listing-study/sources/swath`. All paths below are relative to that root. Labels per the brief: `[SRC]` = read in the pinned checkout, `[DOC]` = the project's own docs at that rev, `[3P-DEP]` = read in a pinned third-party dependency (AWS SDK 2.31.78 from the Gradle cache — I use this label instead of `[SRC]` because it is not swath's own source), `[INFERRED]` = my reasoning, with the basis stated. **No claim here is verified by a run.**

---

## 1. Exact wire shape of every request class

### 1.1 The single call site

`S3PageFetcher.fetchPage` is the only place a `listObjectsV2` is issued; one `fetchPage` = exactly one SDK call, counted before the call `[SRC swath-s3/src/main/java/io/varve/swath/store/s3/S3PageFetcher.java:164 @ cef8ec2]`. `VERSIONS` mode throws `UnsupportedOperationException` — only OBJECTS listing exists `[SRC …/S3PageFetcher.java:161-163 @ cef8ec2]`.

Every request always carries `Bucket`, `MaxKeys`, and `EncodingType=url` `[SRC …/S3PageFetcher.java:166-169 @ cef8ec2]`. Conditionally added:

| param | condition | source |
|---|---|---|
| `FetchOwner=true` | `--fetch-owner` | `[SRC …/S3PageFetcher.java:171-173 @ cef8ec2]` |
| `x-amz-request-payer: requester` | `--requester-pays requester` | `[SRC …/S3PageFetcher.java:174-176 @ cef8ec2]` |
| `Prefix` | `req.prefix() != null && length > 0` | `[SRC …/S3PageFetcher.java:177-179 @ cef8ec2]` |
| `StartAfter` | `req.startAfter() != null` (note: no length check, unlike prefix/delimiter) | `[SRC …/S3PageFetcher.java:180-182 @ cef8ec2]` |
| `Delimiter` | `req.delimiter() != null && length > 0` | `[SRC …/S3PageFetcher.java:183-185 @ cef8ec2]` |

`ContinuationToken` is **never** sent for S3. Pagination is purely `start_after = last emitted key` `[SRC swath-core/src/main/java/io/varve/swath/store/PageRequest.java:11-15 @ cef8ec2]`, `[SRC swath-core/src/main/java/io/varve/swath/engine/RangeScanner.java:294 @ cef8ec2]`. The response's `NextContinuationToken` is carried into `ListPage` but unused on the S3 path `[SRC …/S3PageFetcher.java:331-333 @ cef8ec2]`. The stated reason is that range-stealing needs an arbitrary sub-range lower bound, which an opaque token cannot express `[DOC docs/internals/s3-implementation-compatibility.md:132-134]`.

### 1.2 The four distinct shapes actually emitted in-tree

| # | emitter | shape | classified as |
|---|---|---|---|
| 1 | `RangeScanner` worker page | `prefix=P?`, `start_after=cursor?`, no delimiter, `max_keys=1000` | `worker_page` |
| 2 | `SpeculativeReadahead` guess | identical shape to #1 (`PageRequest.objects(prefix, guess, maxKeys)`) | `worker_page` |
| 3 | `SeedStep` structure probe | `prefix=P?`, `delimiter='/'`, `start_after?`, `max_keys=1000` | `structure_probe` |
| 4a | `Thief` structure probe | `prefix=P`, `delimiter='/'`, `start_after?`, `max_keys=32` | `structure_probe` |
| 4b | `Thief` pivot probe | `prefix=P`, no delimiter, `start_after=m` (or none), `max_keys=1` | `pivot_probe` |

Anchors: worker `[SRC …/RangeScanner.java:216 @ cef8ec2]`; readahead `[SRC swath-core/src/main/java/io/varve/swath/engine/SpeculativeReadahead.java:371 @ cef8ec2]`; seed `[SRC swath-core/src/main/java/io/varve/swath/engine/SeedStep.java:298-299 @ cef8ec2]` with `DELIMITER = {'/'}` and `PROBE_PAGE = 1000` `[SRC swath-core/src/main/java/io/varve/swath/engine/policy/HybridSeedPlanner.java:38,41 @ cef8ec2]`; thief structure `[SRC swath-core/src/main/java/io/varve/swath/engine/Thief.java:400-402 @ cef8ec2]` with `STRUCTURE_PROBE_MAX_KEYS = 32` `[SRC swath-core/src/main/java/io/varve/swath/engine/policy/ThiefPolicy.java:35 @ cef8ec2]`; thief pivot `[SRC …/Thief.java:265, 425 @ cef8ec2]`.

**Page size is not configurable.** `pageMax = 1000` is a hard-coded local in `ListCommand` — there is no `--max-keys`/`--page-size` flag `[SRC swath-cli/src/main/java/io/varve/swath/cli/ListCommand.java:395 @ cef8ec2]`. This matters for the classifier (below).

### 1.3 Classification — request shape only, never engine-aware

```java
if (delimiter != null && length > 0) return "structure_probe";
if (maxKeys <= 1)                    return "pivot_probe";
return "worker_page";
```
`[SRC …/S3PageFetcher.java:472-480 @ cef8ec2]`

Two caveats the source states about itself:
- `structure_probe` means "any `delimiter=/` probe this run issued" — it does **not** separate the seed's probes from the thief's `[SRC …/S3PageFetcher.java:457-463 @ cef8ec2]`.
- `max_keys<=1` is a shape proxy, not a guarantee: a run configured with page size 1 would misclassify every worker page as `pivot_probe`, and this is never validated against the run's configured page size `[SRC …/S3PageFetcher.java:465-470 @ cef8ec2]`. **In practice unreachable in v0.2.0** because page size is the hard-coded 1000 `[INFERRED, from ListCommand.java:395 + the classifier]`.

### 1.4 What drives per-class timeouts

Point vs scan cost shape, not "is it a probe" `[DOC docs/internals/probe-budgets.md §1]`.

| call class | per-attempt budget (level 0) | mechanism |
|---|---|---|
| `worker_page` | 10 s | client-level `apiCallAttemptTimeout`, **no per-request override** |
| `structure_probe` | 10 s | same — deliberately not given the short fuse |
| `pivot_probe` | 3 s | per-request `overrideConfiguration(o -> o.apiCallAttemptTimeout(...))` |

`[SRC …/S3PageFetcher.java:191-203, 496-498 @ cef8ec2]`; constants `DEFAULT_ATTEMPT_TIMEOUT = 10s`, `DEFAULT_PROBE_ATTEMPT_TIMEOUT = 3s`, `DEFAULT_API_CALL_TIMEOUT = 60s` `[SRC swath-s3/src/main/java/io/varve/swath/store/s3/S3Config.java:51,68,74 @ cef8ec2]`. `usesShortProbeBudget` returns true for `pivot_probe` **only**, and the source documents this as a fix for a probe-timeout storm caused by putting a scan-class call behind the 3 s fuse `[SRC …/S3PageFetcher.java:482-498 @ cef8ec2]`, `[DOC docs/internals/probe-budgets.md §2]`. The doc's before/after magnitudes are vendor self-reported; I note only that the split exists and why.

**None of these are CLI-tunable.** `ConnectionOptions.buildConfig()` passes the `S3Config.DEFAULT_*` constants unconditionally `[SRC swath-cli/src/main/java/io/varve/swath/cli/ConnectionOptions.java:187-198 @ cef8ec2]`. The project's own contracts doc confirms there is no `--aws-max-attempts` / timeout option in v1.0 `[DOC docs/internals/contracts.md:1091]`.

---

## 2. Retry, backoff, timeout — and who owns them

### 2.1 SDK-internal retry is disabled — confirmed

```java
var retry = AwsRetryStrategy.standardRetryStrategy().toBuilder()
        .maxAttempts(config.maxAttempts()).build();
```
`[SRC swath-s3/src/main/java/io/varve/swath/store/s3/S3ClientFactory.java:102-105 @ cef8ec2]`, with `maxAttempts` sourced from `S3Config.DEFAULT_MAX_ATTEMPTS = 1` `[SRC …/S3Config.java:80 @ cef8ec2]`, hard-wired at `[SRC …/ConnectionOptions.java:192 @ cef8ec2]`. `maxAttempts=1` = one total attempt, i.e. no SDK retries; the stated reason is that the AIMD gauge must see every real 503 immediately `[SRC …/S3Config.java:75-80 @ cef8ec2]`. The strategy is `standard`, explicitly never `adaptive` `[SRC …/S3ClientFactory.java:26-28 @ cef8ec2]`.

**Defect worth flagging:** `S3FaultClassifier` and `ThrottleException` repeatedly describe faults as arriving "after the SDK's own `RetryStrategy.standard()` retries are exhausted" `[SRC swath-s3/src/main/java/io/varve/swath/store/s3/S3FaultClassifier.java:185-188, 146-148 @ cef8ec2]`, `[SRC swath-core/src/main/java/io/varve/swath/error/ThrottleException.java:9-13 @ cef8ec2]`. With `maxAttempts=1` there are no SDK retries to exhaust. Stale comment, not a behavioural claim about code paths.

### 2.2 Two retry loops, different policies

**`TransientRetryFetcher`** — seed structure probes and the sequential/non-work-stealing path `[SRC swath-core/src/main/java/io/varve/swath/engine/TransientRetryFetcher.java:20-45 @ cef8ec2]`.

**`GaugedFetcher`** — the engine's worker and thief paths `[SRC swath-core/src/main/java/io/varve/swath/engine/GaugedFetcher.java:21-59 @ cef8ec2]`.

Shared constants (one source of truth): `MAX_TRANSIENT_RETRIES = 8`, `BACKOFF_BASE_MILLIS = 100`, `BACKOFF_CAP_MILLIS = 5_000`, `STORM_BACKOFF_CAP_MILLIS = 15_000`, `MAX_ATTEMPT_TIMEOUT_ESCALATION_LEVEL = 2` `[SRC …/TransientRetryFetcher.java:49-74 @ cef8ec2]`, referenced by `[SRC …/GaugedFetcher.java:69 @ cef8ec2]`.

Backoff is AWS full-jitter: `delay ∈ [0, min(cap, 100ms << min(attempt-1,16))]` `[SRC …/TransientRetryFetcher.java:250-254 @ cef8ec2]`.

**Bounded vs ride-out.** `RetryPolicy` is resolved once, at CLI wiring, from whether a real watchdog poller is armed:
```java
retryPolicy = watchdog.isArmed() ? RetryPolicy.RIDE_OUT : RetryPolicy.BOUNDED;
```
`[SRC …/ListCommand.java:472-474 @ cef8ec2]`, `[SRC swath-core/src/main/java/io/varve/swath/engine/RetryPolicy.java:27-32 @ cef8ec2]`.
- `RIDE_OUT` (production default, since the watchdog is on by default): over-cap transients **retry indefinitely** with the ceiling raised to 15 s; the watchdog owns death `[SRC …/GaugedFetcher.java:245-252 @ cef8ec2]`.
- `BOUNDED`: cap exhaustion cancels the run `STUCK` (`CancelSource.TRANSIENT_RETRY_CAP`) and unwinds via `InterruptedException` → resumable exit 75 `[SRC …/GaugedFetcher.java:227-243 @ cef8ec2]`, `[SRC …/TransientRetryFetcher.java:202-219 @ cef8ec2]`.
- No `CancellationToken` at all (embedded/direct construction): count-bounded regardless; the `ThrottleException` escapes as the fatal exit-1 contract `[SRC …/GaugedFetcher.java:221-226 @ cef8ec2]`, `[SRC …/RetryConfig.java:18-29 @ cef8ec2]`.

**An asymmetry between the two loops that matters.** In `GaugedFetcher`, an AIMD-voting throttle (503/5xx) **resets** `transientRetries`, `consecutiveAttemptTimeouts`, and both fault tallies to 0 `[SRC …/GaugedFetcher.java:167-181 @ cef8ec2]` — so the 8-cap only ever bounds a run of *consecutive client-side* transients. In `TransientRetryFetcher`, every `ThrottleException` kind counts toward the cap and is never reset `[SRC …/TransientRetryFetcher.java:156-159, 188-195 @ cef8ec2]`, `[SRC …/RetryPolicy.java:11-12 @ cef8ec2]`. The seed path is therefore strictly less patient with mixed 503/timeout faults than the engine path.

**Probe fail-fast.** Thief probes run `slotGated=false` and get `PROBE_TRANSIENT_RETRY_CAP = 1` for *non-voting* transients, independent of `RetryPolicy` — exactly one retry, then the `ThrottleException` is thrown and re-enters the thief's ordinary non-productive-steal flow `[SRC …/GaugedFetcher.java:71-83, 209-216 @ cef8ec2]`. A *voting* throttle on a probe still retries unbounded, exactly like a worker `[SRC …/GaugedFetcher.java:49-58, 168-181 @ cef8ec2]`.

### 2.3 Attempt-timeout escalation: level → wall-clock

The engine publishes only an integer **level** on `PageRequest.attemptTimeoutEscalationLevel`; the store maps it `[SRC …/PageRequest.java:19-34 @ cef8ec2]`, `[DOC docs/internals/probe-budgets.md §3]`:
```text
budget = base(callClass) × 2^level,  level clamped to [0, MAX_ESCALATION_SHIFT=30]
```
`[SRC …/S3PageFetcher.java:500-535 @ cef8ec2]`

`escalationLevel(n) = min(max(n,0), 2)` on *consecutive* `Kind.ATTEMPT_TIMEOUT` faults; a voting throttle or a `NETWORK` fault breaks the streak and resets to the base `[SRC …/TransientRetryFetcher.java:83-85 @ cef8ec2]`, `[SRC …/GaugedFetcher.java:188-208 @ cef8ec2]`. Both loops treat an incoming level as a **floor**, never a starting point, so escalation only ever buys room `[SRC …/GaugedFetcher.java:137-149 @ cef8ec2]`, `[SRC …/TransientRetryFetcher.java:164-171 @ cef8ec2]`.

Resulting ladders `[DOC docs/internals/probe-budgets.md §3]`, consistent with the constants above:

| class | L0 | L1 | L2 |
|---|---|---|---|
| scan (`worker_page`, `structure_probe`) | 10 s | 20 s | 40 s |
| point (`pivot_probe`) | 3 s | 6 s | 12 s |

**Wall-clock, worked** `[INFERRED — arithmetic over the constants above, not observed]`:
- A worker page wedged in pure attempt-timeouts spends ≈ `10 + 20 + 40×k` seconds of attempt time before retry *k+2*, plus full-jitter backoff whose expectation over the first 8 retries is ≈ 8 s. Reaching the 8-retry cap costs roughly **5 minutes** of wall-clock. Under `RIDE_OUT` (the default) it then continues forever at 40 s/attempt with backoff in `[0, 15 s]`.
- A pivot probe wedged the same way costs `3 s + backoff + 6 s` ≈ **9–10 s** before it gives up (cap = 1).

Note `GaugedFetcher`'s own javadoc says a probe gets "one chance to self-heal at the escalated **20 s** budget" `[SRC …/GaugedFetcher.java:78-81 @ cef8ec2]` — that is a stale figure from the pre-fix era when the engine authored absolute durations; the code now yields 6 s. Flagging as a comment/behaviour drift, not a bug.

### 2.4 The overall ceiling

`apiCallTimeout = 60 s` is set client-level and never overridden per request; `PageRequest`'s javadoc is explicit that the level mechanism does not touch it `[SRC …/S3ClientFactory.java:107-112 @ cef8ec2]`, `[SRC …/PageRequest.java:33-34 @ cef8ec2]`. It is described as the **primary** liveness guarantee, the watchdog as secondary `[SRC …/S3Config.java:69-74 @ cef8ec2]`. At `maxAttempts=1` and level ≤ 2 the per-attempt budget (≤ 40 s) is always below the 60 s ceiling, so the ceiling never truncates an in-tree escalated attempt `[INFERRED, from the two constants + the level cap]`.

---

## 3. Throttling / 503 handling

### 3.1 Two entry points, one recording site

A 503 can reach swath two ways:

1. **As an exception** — `S3FaultClassifier.classify` → `isThrottle` (HTTP 503, `sse.isThrottlingException()`, or error codes `SlowDown`/`ServiceUnavailable`/`RequestThrottled`/`Throttling`) → `ThrottleException.slowDown` with `Kind.SLOWDOWN` `[SRC …/S3FaultClassifier.java:288-300, 190-198 @ cef8ec2]`. Any other 5xx → `Kind.SERVER_5XX` `[SRC …/S3FaultClassifier.java:205-213, 368-374 @ cef8ec2]`.
2. **As a returned page with status 503** — belt-and-suspenders; `fetchPage` records the throttle event here, and the source calls this the single recording site for `swath.throttle.events{type}` `[SRC …/S3PageFetcher.java:314-323 @ cef8ec2]`.

### 3.2 Propagation into the AIMD controller

`ThrottleException.Kind` carries a `votesAimdDown` bit: `SLOWDOWN`/`SERVER_5XX` = true; `ATTEMPT_TIMEOUT`/`NETWORK` = false `[SRC …/ThrottleException.java:42-62 @ cef8ec2]`. In `GaugedFetcher`:

- **Voting fault** → `gauge.reportStatus(ConcurrencyGauge.SLOWDOWN_STATUS)` = multiplicative decrease of T + pause steals, all transient counters reset, retry **unbounded** `[SRC …/GaugedFetcher.java:167-181 @ cef8ec2]`.
- **Non-voting fault** → `gauge.onTransientTimeout(slotGated)` only — the worker growth-freeze / sustained-timeout shed window, never AIMD `[SRC …/GaugedFetcher.java:182-208 @ cef8ec2]`.
- **Success path** → `gauge.reportStatus(page.httpStatus())`, and `gauge.onAttemptLatency(...)` is fed **only** when the status is not `SLOWDOWN_STATUS`, so a returned-503 page is routed as backpressure rather than as a latency observation `[SRC …/GaugedFetcher.java:152-160 @ cef8ec2]`.
- The gauge only casts the AIMD vote; it does not record the throttle event — that stays at the classification point `[SRC …/S3PageFetcher.java:316-320 @ cef8ec2]`.

The design rationale — folding client-side timeouts into the AIMD vote is what previously strangled concurrency to 1 — is stated at `[SRC …/ThrottleException.java:24-32 @ cef8ec2]`.

The thief uses `(slotGated=false, reportSuccess=false)`: probes stay off the concurrency gate and a healthy probe must not nudge AIMD recovery, but a genuine voting throttle on a probe *does* drive the decrease `[SRC …/GaugedFetcher.java:49-58 @ cef8ec2]`.

### 3.3 Sustained 503s

Voting throttles are **retry-until-cancel (unbounded)** on both worker and probe paths — the stated contract is that AIMD paces them (T collapses toward 1) and cancellation/`--max-duration` bounds them `[SRC …/GaugedFetcher.java:41-46, 168-175 @ cef8ec2]`. Because voting faults also reset `transientRetries`, a permanently-503ing endpoint can never trip the 8-retry cap `[SRC …/GaugedFetcher.java:176-181 @ cef8ec2]`.

What actually ends such a run is the watchdog's **second** tripwire, `--no-progress-timeout` (default 10 min): throttle/retry activity keeps `progressSignal` climbing so the 120 s idle tripwire is continually re-armed, but `realProgressSignal` (committed pages only) stays flat `[SRC swath-core/src/main/java/io/varve/swath/runtime/LivenessWatchdog.java:137-153, 224-245 @ cef8ec2]`, `[SRC …/ListCommand.java:460-465 @ cef8ec2]`. Result: `list_no_progress_abort` → cooperative STUCK cancel → exit 75.

### 3.4 `--request-rate` mechanism

Flag: `--request-rate N`, "Cap aggregate S3 requests per second (default/0: unlimited)" `[SRC …/ConnectionOptions.java:86-89 @ cef8ec2]`. Note the internal javadoc still calls it `--rate-limit-api` `[SRC swath-core/src/main/java/io/varve/swath/store/ApiRateLimiter.java:16 @ cef8ec2]` — stale name, the CLI surface is `--request-rate`.

Mechanism: a **Bucket4j local bucket with capacity 1** and greedy refill of 1 token every `round(1e9 / rps)` ns — deliberately zero-burst, fixed-interval spacing, and the source states that pacing shape is part of the public contract `[SRC …/ApiRateLimiter.java:15-19, 60-76 @ cef8ec2]`. `acquire()` uses the **interruptible** `BlockingBucket.consume(long, BlockingStrategy.PARKING)`, never `consumeUninterruptibly`, so a parked worker is woken by the engine's `shutdownNow()` on cancel `[SRC …/ApiRateLimiter.java:21-31, 79-81 @ cef8ec2]`.

Wiring: `RateLimitedPageFetcher` wraps the fetcher **once**, before either the worker or thief path is built, so one limiter gates the run's aggregate rate — never per-thread `[SRC swath-core/src/main/java/io/varve/swath/store/RateLimitedPageFetcher.java:14-32, 46-52 @ cef8ec2]`, `[SRC …/ConnectionOptions.java:160-171 @ cef8ec2]`. When unset the plain fetcher is used with zero added indirection, so `swath.rate_limit.api_wait` is genuinely zero rather than approximated `[SRC …/RateLimitedPageFetcher.java:29-32 @ cef8ec2]`.

Validation: negative, `NaN`, `Infinity` all rejected at exit 2 before any checkpoint is opened `[SRC …/ConnectionOptions.java:140-158 @ cef8ec2]`.

**Fetcher stack order** (outermost first) `[SRC …/ListCommand.java:849-862 @ cef8ec2]`:
`FirstRequestMarkerFetcher → [RateLimitedPageFetcher] → S3PageFetcher`, with `GaugedFetcher` (engine) or `TransientRetryFetcher` (seed/sequential) wrapping that composite further out. Consequence worth stating: **the rate limiter sits inside the retry loops**, so every retry attempt also pays the rate-limit wait `[INFERRED, from the wrapping order at ListCommand.java:849-862 plus the loops delegating through it]`.

---

## 4. Key fidelity — where decoding happens, and what actually survives

This is the question the study cares most about, so I traced it to the byte level, including into the SDK.

### 4.1 The claim swath makes

`encoding-type=url` is set on every request `[SRC …/S3PageFetcher.java:169 @ cef8ec2]`. swath does **no decoding of its own**. `toEntry` converts the SDK's already-decoded `o.key()` straight to bytes with a plain UTF-8 encode, and the source is emphatic that this is not a second percent-decode `[SRC …/S3PageFetcher.java:368-377 @ cef8ec2]`. Common prefixes take the identical path `[SRC …/S3PageFetcher.java:307-310 @ cef8ec2]`.

Outbound: `toRequestParam(byte[]) = new String(raw, UTF_8)` — the raw decoded key, byte-exact **iff `raw` is valid UTF-8** `[SRC …/S3PageFetcher.java:412-433 @ cef8ec2]`. The javadoc argues both bound kinds are valid UTF-8 by construction: real S3 keys are Unicode ≤1024 UTF-8 bytes, and every synthesized pivot is chosen UTF-8-safe by `ByteMidpoint` (property-tested as PROP-2). It also names a residual limitation: XML-illegal real cursor bytes are a documented `start-after` capability gap this conversion cannot fix.

### 4.2 Where decoding actually happens — verified in the SDK

The decode is **not swath's**; it is the SDK's `DecodeUrlEncodedResponseInterceptor`. Disassembling the pinned jar:

- `modifyListObjectsV2Response` decodes `delimiter`, `prefix`, `startAfter`, `contents[].key`, `commonPrefixes[].prefix` — and **not** `nextContinuationToken` `[3P-DEP software.amazon.awssdk:s3:2.31.78 DecodeUrlEncodedResponseInterceptor#modifyListObjectsV2Response]`.
- Every decode goes through `SdkHttpUtils.urlDecode`, which is `java.net.URLDecoder.decode(s, "UTF-8")` `[3P-DEP software.amazon.awssdk:utils:2.31.78 SdkHttpUtils#urlDecode]`.
- **The interceptor is gated**, not unconditional: `shouldHandle` reads the *response's* `EncodingType` field and returns true only when `EncodingType.URL.toString().equals(value)` — a case-sensitive exact match on `"url"` `[3P-DEP …s3:2.31.78 DecodeUrlEncodedResponseInterceptor#shouldHandle / #lambda$shouldHandle$0]`.

That last point **contradicts swath's own documentation**, which states the decode "happens regardless of `encoding-type`, since it is unconditional in the SDK, not driven by the request" `[DOC docs/internals/s3-implementation-compatibility.md:18-21]`. The gate is on the response's echoed `EncodingType` element. `S3PageFetcher` never inspects `resp.encodingType()` — the only `EncodingType` reference in the whole main source tree is the request-side one `[SRC …/S3PageFetcher.java:36,169 @ cef8ec2]` (verified by grep across `swath-core/src/main` and `swath-s3/src/main`).

### 4.3 Verdict per character class

| input | survives byte-exactly? | basis |
|---|---|---|
| non-ASCII (3-byte, 4-byte/supplementary) | **Yes** against a conforming endpoint | round-trip is UTF-8 encode of an SDK-decoded string; pinned by the decode matrix `[SRC swath-s3/src/test/java/io/varve/swath/store/s3/S3PageFetcherKeyDecodeTest.java:133-146 @ cef8ec2]` |
| literal `%XX` in a key (`%20`, `%3D`, `%25`), trailing `%`, incomplete `%2` | **Yes** — no second decode | same matrix, `[SRC …/S3PageFetcherKeyDecodeTest.java:47-131 @ cef8ec2]` |
| real space `0x20` | **Yes** | matrix case |
| literal `+` `0x2B` | **Conditional** — see below | matrix case + `[SRC swath-s3/src/test/java/io/varve/swath/store/s3/PercentEchoLocalStackIT.java:110-148 @ cef8ec2]` |
| control bytes `{0x01,0x02}` | **Yes** through the fetcher | matrix case; but see caveat |

**The `+` caveat — the sharpest correctness edge I found.** `URLDecoder.decode` maps `+` → space. So a literal `+` in a key survives **only if the endpoint percent-encodes it as `%2B`** in the `<Key>` element. swath has no defence here: it never inspects or re-encodes. Real AWS S3 and the tested LocalStack build both encode it (the LocalStack IT stores `pct/plus+sign+key` and `pct/x+y%20z` and asserts byte-exact round-trip through the production fetcher) `[SRC …/PercentEchoLocalStackIT.java:117-148 @ cef8ec2]`. An S3-compatible endpoint that echoes `+` literally while setting `<EncodingType>url</EncodingType>` would silently corrupt every such key to a space, with no error `[INFERRED — from URLDecoder's documented `+`-to-space rule, confirmed in the pinned `SdkHttpUtils#urlDecode` bytecode, plus the absence of any swath-side check]`. Symmetrically, an endpoint that percent-encodes keys but omits or misspells the `<EncodingType>` echo would fail `shouldHandle`, skip the decode entirely, and swath would emit the *percent-encoded* form as if it were the key `[INFERRED — same basis]`. Neither case is covered by any test I found.

**Control characters.** They survive the fetcher byte-exactly (matrix case). But every *logged* rendering of a key or bound goes through `ControlCharEscaper.escape` `[SRC …/S3PageFetcher.java:444-446 @ cef8ec2]` — so log lines are escaped, not raw. Whether the *output* path escapes is reader D's area (there is an `--escape` in the `ListRunner.Spec`).

### 4.4 Two documented endpoint deviations that break fidelity

Both are documented as **endpoint conformance gaps, not swath defects**, and I agree with that reading given the code:

1. **Verbatim-echo crash.** An endpoint that echoes `Prefix`/`StartAfter` verbatim (tested LocalStack build) returns a lone/trailing `%`, which `URLDecoder` rejects with `IllegalArgumentException: URLDecoder: Incomplete trailing escape (%) pattern` — surfacing as an SDK response-unmarshalling failure that aborts the whole listing rather than any error swath's retry logic can reason about `[DOC docs/internals/s3-implementation-compatibility.md:26-36]`, positive control at `[SRC …/PercentEchoLocalStackIT.java:82-106 @ cef8ec2]`. swath's mitigation is to **exclude `0x25`/`U+0025` from every code point it will ever synthesize** — `SeedStep.UNSAFE_SCALAR` and `ByteMidpoint.isSafe` `[DOC …s3-implementation-compatibility.md:46-58]`. This protects only invented bounds; a **user-supplied `--prefix` ending in a lone `%` can still crash against such an endpoint**, and the doc states swath will not work around it by mangling user input `[DOC …s3-implementation-compatibility.md:71-78]`. The tested MinIO build is unaffected.

2. **Double-decoded `start-after` → silent under-count.** LocalStack decodes `start-after` one extra time; a cursor that is itself a real key containing `%25` becomes `%`, sorting past the remaining `%25…` keys and silently skipping them — `errors=0`, `quiescence_reached` logged, keys simply missing `[DOC …s3-implementation-compatibility.md:93-135]`, pinned as a raw-SDK positive control at `[SRC …/PercentEchoLocalStackIT.java:150-193 @ cef8ec2]`. Real S3 and MinIO decode once. **This is the failure mode a study should watch for**: it produces a wrong answer with a clean exit and no warning.

**Bottom line:** against a conforming endpoint the source *guarantees* a byte-exact round trip for everything except the two documented gaps, and the guarantee rests on a single line (`new String(raw, UTF_8)` out, `key.getBytes(UTF_8)` in) plus the SDK doing the decode. Against a non-conforming endpoint swath *attempts* nothing — it has no validation of the decode contract at all.

---

## 5. Protocol-violation defences

Three defences exist; two are in the engine's `RangeScanner`, one in the store.

**Oversized page** (store, `S3PageFetcher`). If `contents.size() + commonPrefixes.size() > req.maxKeys()`, the page is **refused**: records `steal_reason{FATAL,oversized_page}`, logs `s3_oversized_page` at **WARN**, throws `ProtocolViolationException.oversizedPage(...)` `[SRC …/S3PageFetcher.java:283-295 @ cef8ec2]`. The exception type is a plain `ListingException` (exit 1), deliberately **not** a `ThrottleException`, so it is never retried — the retry loops only catch `ThrottleException` `[SRC swath-core/src/main/java/io/varve/swath/error/ProtocolViolationException.java:13-23 @ cef8ec2]`. The stated reasoning: truncating would silently drop keys, retrying would spin, repairing locally would corrupt. It is also the one fault the speculative-readahead path refuses to absorb into its fail-soft "drop the guess and refetch serially" handling `[SRC …/ProtocolViolationException.java:17-22 @ cef8ec2]`. Counts are named in the message so a false positive against a third-party implementation is diagnosable from one line `[SRC …/ProtocolViolationException.java:33-50 @ cef8ec2]`.

**Truncated-but-empty page** (engine, `RangeScanner`). If the page is truncated, the bound was not reached, and the in-range batch is empty → `ListingException("truncated page returned no keys <= hi (prefix=…)")` `[SRC …/RangeScanner.java:269-274 @ cef8ec2]`. Note the guard is deliberately ordered **after** `reachedBound` is folded into `done`, so a legitimately-empty post-narrow page (a thief lowered `hi` under the worker) completes the node instead of tripping this `[SRC …/RangeScanner.java:254-258 @ cef8ec2]`.

**Stuck / looping continuation** (engine, `RangeScanner`). `start_after` is exclusive, so a correct page must advance. If `lastKey <= startAfter` under unsigned byte compare → `ListingException("no forward progress (stuck listing) at …")` `[SRC …/RangeScanner.java:275-280 @ cef8ec2]`. Both guards run **before** the consumer callback, so a broken page is never committed or emitted — preserving exactly-once even on the default sink `[SRC …/RangeScanner.java:265-268 @ cef8ec2]`.

**Out-of-order keys within a page: no defence.** I found no monotonicity check over a page's entries in the store or in `RangeScanner` (grepped `swath-core/src/main` and `swath-s3/src/main` for out-of-order / monotonic / non-monotonic; the only hits are in the `sort` package's segment reader/writer, which is a different concern). The forward-progress guard checks only `lastKey` against the previous cursor, so a page whose *interior* keys are unordered — while its last key still advances — passes through unexamined `[INFERRED, from RangeScanner.java:230-280 and the absence of any per-page ordering check]`. Reader A owns the engine, but flagging it here since it's the protocol-defence question.

**Authoritative truncation signal.** `ListPage` documents that `truncated` is the authoritative "more pages" signal and must never be inferred from `entries.size() == maxKeys` `[SRC swath-core/src/main/java/io/varve/swath/store/ListPage.java:13-19 @ cef8ec2]`; `RangeScanner` respects this `[SRC …/RangeScanner.java:258 @ cef8ec2]`.

**Non-conforming-endpoint posture overall.** The defences cover *quantity* (oversized), *pagination liveness* (no-progress, truncated-empty), and *synthesis safety* (`%` exclusion). They do **not** cover *encoding contract* (§4.2/§4.3) or *intra-page ordering*. An endpoint that lies about encoding produces wrong output silently; an endpoint that over-serves or stalls is refused loudly.

---

## 6. Liveness — `LivenessWatchdog`

**Role.** Explicitly the **secondary** guarantee; the SDK's `apiCallTimeout` (60 s) is the primary unblock `[SRC …/LivenessWatchdog.java:21-26 @ cef8ec2]`, `[SRC …/S3Config.java:69-74 @ cef8ec2]`.

**Two independent tripwires**, each with its own clock, rearmed only while `HEALTHY` `[SRC …/LivenessWatchdog.java:219-246 @ cef8ec2]`:

| tripwire | signal | default | CLI flag |
|---|---|---|---|
| total freeze | `RunMetrics.progressSignal()` (folds in throttle/retry activity) | **120 s** | `--idle-timeout` |
| zero real progress | `RunMetrics.realProgressSignal()` (committed work only) | **10 min** | `--no-progress-timeout` |

`[SRC swath-cli/src/main/java/io/varve/swath/cli/LivenessOptions.java:22-24, 93-107 @ cef8ec2]`, `[SRC swath-cli/src/main/java/io/varve/swath/cli/ListOptionGroups.java:55-67 @ cef8ec2]`, `[SRC …/LivenessWatchdog.java:137-144 @ cef8ec2]`.

**Naming trap for a downstream stage:** the user-facing flag is **`--idle-timeout`**. `--stall-timeout` appears in `LivenessWatchdog`'s and `ListRunner`'s javadoc but is **not a real option** `[SRC …/LivenessWatchdog.java:28 @ cef8ec2]` vs `[SRC …/ListOptionGroups.java:55-59 @ cef8ec2]`. Both windows disabled (`0`/`none`/`off` on each) → `arm()` returns a disarmed no-op, and the CLI then selects `RetryPolicy.BOUNDED` `[SRC …/LivenessWatchdog.java:156-169, 343-351 @ cef8ec2]`.

**Escalation ladder**, time-driven once tripping begins (progress is ignored after the first rung, deliberately, so a dribble of progress cannot postpone halt forever) `[SRC …/LivenessWatchdog.java:210-218 @ cef8ec2]`:

1. **Cooperative cancel** `StopReason.STUCK` / `CancelSource.LIVENESS_WATCHDOG`, first-writer-wins so a prior `max_duration`/signal reason keeps its attribution. Logs `list_stuck_abort` (total freeze) or `list_no_progress_abort` (zero real progress) at **ERROR**, each carrying `error_class` ∈ {`stuck_api_timeouts`, `stuck_throttle`, `stuck_unknown`}, `stall_ms`/`no_real_progress_ms`, `exit_code`, `owned_reason` `[SRC …/LivenessWatchdog.java:263-291 @ cef8ec2]`.
2. After **10 s** grace (`DEFAULT_GRACE_INTERRUPT`): **forensic dump then interrupt**. Logs `list_stuck_escalate step=interrupt`, then `list_stuck_forensics` (in-flight, peak in-flight, objects emitted, progress signal, live thread count) and one `list_stuck_thread` line per thread with `top_frame` `[SRC …/LivenessWatchdog.java:293-306, 426-441 @ cef8ec2]`.
3. After a further **60 s** (`DEFAULT_GRACE_HALT`, ~one `apiCallTimeout`): `Runtime.halt(stuckExitCode)`. Logs `list_stuck_halt` and then `list_stuck_summary` — the latter explicitly because halt bypasses the JSON summary finalizer, so this stderr line is the machine-parseable stand-in `[SRC …/LivenessWatchdog.java:308-341 @ cef8ec2]`.

**Exit code: 75** (`EX_TEMPFAIL`), passed in as `ExitCodes.STUCK` `[SRC …/ListCommand.java:467-468 @ cef8ec2]`, `[SRC swath-cli/src/main/java/io/varve/swath/cli/ExitCodes.java:36-49 @ cef8ec2]`. Deliberately not 130 (signal) and not 1 (crash), so an external runner can distinguish a resumable stuck partial on the exit code alone. Three sources trip 75: the watchdog ladder, transient-retry-cap exhaustion, and a bare seed-time interrupt.

**Honest limits the source itself states** — worth carrying forward, since they bound what "liveness" means here:
- `Thread.getAllStackTraces()` sees only **platform** threads, so a virtual thread's own stack is invisible (only its carrier's top frame) and interrupting a carrier does **not** interrupt the mounted virtual thread. Step 3 (`halt`) is the real guarantee for a vthread wedged in a native socket read; step 2 genuinely helps only the platform-thread lanes (`parquet-writer-*`, `*-encoder-*`) `[SRC …/LivenessWatchdog.java:36-49, 362-381 @ cef8ec2]`.
- SDK connection-pool stats are **not** in the forensic dump — the SDK exposes none without reflection, deliberately avoided `[SRC …/LivenessWatchdog.java:36-41, 429-433 @ cef8ec2]`.
- One residual false-trip gap: a single `fsync` of a multi-GB Parquet part has no intra-call ticks, so a `--sort` finalize whose fsync alone exceeds the idle window can trip `[SRC …/LivenessWatchdog.java:52-60 @ cef8ec2]`.

---

## 7. Request observability without interception infrastructure

This is the practically important one for the downstream stage, so I'll be concrete about what each surface emits and what flag turns it on.

### 7.1 Verbosity control

`-v` is counted: `0 → WARN` (default), `1 → INFO`, `2 → DEBUG`, `3+ → TRACE`; `-q` wins over `-v` (`-q → ERROR`, `-qq → OFF`) `[SRC swath-cli/src/main/java/io/varve/swath/cli/CliLogging.java:72-87 @ cef8ec2]`, `[SRC swath-cli/src/main/java/io/varve/swath/cli/GlobalOptions.java:31 @ cef8ec2]`.

**Critical caveat: `configure()` sets the level on the `io.varve.swath` logger only.** The shipped `logback.xml` pins `software.amazon.awssdk` to **ERROR**, and `-vvv` does not touch it `[SRC swath-core/src/main/resources/logback.xml @ cef8ec2]`, `[SRC …/CliLogging.java:88-96 @ cef8ec2]`. So **no amount of `-v` gives you SDK wire/request logging** — an operator wanting that must supply their own logback configuration `[INFERRED, from those two files]`. All logging goes to **stderr**, keeping stdout clean for piped output `[SRC swath-core/src/main/resources/logback.xml @ cef8ec2]`.

### 7.2 Per-request DEBUG lines (`-vv`), with exact names and fields

All under logger `io.varve.swath.store.s3.S3PageFetcher` unless noted. `S3FaultClassifier` deliberately logs under `S3PageFetcher`'s logger name so the operational filter surface stays stable `[SRC …/S3FaultClassifier.java:44-50 @ cef8ec2]`.

| line | level | fields | anchor |
|---|---|---|---|
| `s3_page_fetched` | DEBUG | `run_id worker_id node_id bucket prefix start_after keys common_prefixes truncated status latency_ms` | `[SRC …/S3PageFetcher.java:324-330 @ cef8ec2]` |
| `slow_probe_exemplar` | DEBUG | `bucket call_class prefix start_after elapsed_ms connect_acquire_ms ttfb_ms attempt_timeout_ms escalation_level exemplar_n` | `[SRC …/S3PageFetcher.java:583-588 @ cef8ec2]` |
| `s3_timeout` | DEBUG | `bucket call_class prefix start_after type` | `[SRC …/S3FaultClassifier.java:128-129 @ cef8ec2]` |
| `s3_abort` | DEBUG | same shape | `[SRC …/S3FaultClassifier.java:112-113 @ cef8ec2]` |
| `s3_network_error` | DEBUG | same shape | `[SRC …/S3FaultClassifier.java:160-161 @ cef8ec2]` |
| `s3_socket_closure` | DEBUG | `… type cause` | `[SRC …/S3FaultClassifier.java:275-277 @ cef8ec2]` |
| `s3_throttle` | DEBUG | `bucket call_class prefix start_after status s3_code request_id` | `[SRC …/S3FaultClassifier.java:194-196 @ cef8ec2]` (also a 2-field variant from the returned-503 path, `[SRC …/S3PageFetcher.java:322 @ cef8ec2]`) |
| `s3_server_error` | DEBUG | same shape | `[SRC …/S3FaultClassifier.java:209-211 @ cef8ec2]` |
| `s3_region_redirect` / `s3_access_denied` / `s3_unauthorized` / `s3_no_such_bucket` | DEBUG | `bucket status s3_code request_id` (+`correct_region`) | `[SRC …/S3FaultClassifier.java:223, 237, 244, 251 @ cef8ec2]` |
| `s3_error` (unclassified) | **WARN** | `bucket status s3_code request_id` | `[SRC …/S3FaultClassifier.java:177, 256 @ cef8ec2]` |
| `s3_oversized_page` | **WARN** | `bucket max_keys keys common_prefixes` | `[SRC …/S3PageFetcher.java:291-292 @ cef8ec2]` |
| `range_page_fetched` | DEBUG (`…engine.RangeScanner`) | `run_id worker_id node_id prefix start_after entries truncated status latency_ms` | `[SRC …/RangeScanner.java:221-224 @ cef8ec2]` |
| `list_first_request_issued` / `list_first_page_returned` | **INFO** (`…store.FirstRequestMarkerFetcher`) | `elapsed_ms` | `[SRC swath-core/src/main/java/io/varve/swath/store/FirstRequestMarkerFetcher.java:43,47 @ cef8ec2]` |

Design notes that matter for a capture harness:
- Retryable fault lines carry **`call_class` + `prefix` + `start_after`**; terminal one-shot faults deliberately do not (a key range is noise there) `[SRC …/S3FaultClassifier.java:60-80 @ cef8ec2]`.
- `slow_probe_exemplar` is **rate-limited**: first 20 unconditionally, then only powers of two (`Long.bitCount(n) == 1`). Worker pages are never logged here. Every slow probe still increments a `PROBE.slow_<call_class>` counter even when its line is thinned `[SRC …/S3PageFetcher.java:90-99, 565-582 @ cef8ec2]`. **So the log undercounts probe slowness by design; the counter does not.**
- The success-path threshold is 1000 ms; any exception path logs regardless (`forceLog=true`) `[SRC …/S3PageFetcher.java:84-92, 348-366 @ cef8ec2]`.
- Prefixes/cursors are control-char escaped before logging `[SRC …/S3PageFetcher.java:444-446 @ cef8ec2]`.

### 7.3 Micrometer meters relevant to request behaviour

| meter | type | tags | fed from |
|---|---|---|---|
| `swath.api.calls` | counter | `strategy` | `metrics.recordApiCall()` immediately before each SDK call `[SRC swath-core/src/main/java/io/varve/swath/observability/RunMetrics.java:771-774 @ cef8ec2]`, `[SRC …/S3PageFetcher.java:214 @ cef8ec2]` |
| `swath.api.latency` | timer | `op=listObjectsV2` | `[SRC …/RunMetrics.java:489 @ cef8ec2]` |
| **`swath.fetch.latency.phase`** | timer | `call_class` × `phase` | `[SRC …/RunMetrics.java:1056-1071 @ cef8ec2]` |
| `swath.throttle.events` | counter | `type` ∈ {`slowdown`,`server5xx`,`attempt_timeout`,`network`} | `[SRC …/RunMetrics.java:479 @ cef8ec2]` |
| `swath.steal_reason` | counter | `outcome` × `reason` | `[SRC …/RunMetrics.java:872-893 @ cef8ec2]` |
| `swath.s3.pool.{leased,idle_available,pending_acquisition,max}` | gauges | — | `S3PoolMetricPublisher` `[SRC …/RunMetrics.java:553-556 @ cef8ec2]`, `[SRC swath-s3/…/S3PoolMetricPublisher.java:38-51 @ cef8ec2]` |
| `swath.s3.pool.connection_aborted` | counter | — | attempt-timeout / network / socket-closure paths `[SRC …/RunMetrics.java:558 @ cef8ec2]` |
| `swath.s3.pool.handshakes` | counter | — | `S3HandshakeCountingSocketFactory` `[SRC …/RunMetrics.java:559 @ cef8ec2]` |
| `swath.s3.socket_closure_recovered` | counter | — | `[SRC …/RunMetrics.java:561 @ cef8ec2]` |
| `swath.rate_limit.wait` | timer | — | AIMD concurrency-slot wait (always on) |
| `swath.rate_limit.api_wait` | timer | — | `--request-rate` cap only; genuinely zero when unset `[SRC …/RateLimitedPageFetcher.java:48-50 @ cef8ec2]`, `[DOC docs/metrics-and-observability.md:58-59]` |

**`swath.fetch.latency.phase{call_class,phase}`** is the richest per-request surface. `call_class` ∈ {`worker_page`, `pivot_probe`, `structure_probe`}; `phase` ∈ {`connect_acquire`, `ttfb`, `sdk_unmarshal`, `total`, `response_parse`} `[SRC …/RunMetrics.java:311-350 @ cef8ec2]`. Provenance and honesty properties:

- `connect_acquire` = SDK `HttpMetric.CONCURRENCY_ACQUIRE_DURATION`; `ttfb` = `CoreMetric.TIME_TO_FIRST_BYTE`; `sdk_unmarshal` is **derived** as `TIME_TO_LAST_BYTE − TIME_TO_FIRST_BYTE` because `UNMARSHALLING_DURATION` is never reported for S3 (structural, per the source's reasoning about `AwsS3ProtocolFactory`) `[SRC swath-s3/…/S3CallClassLatencyPublisher.java:140-161, 74-105 @ cef8ec2]`.
- The first three are **best-effort**: `-1` means "the SDK didn't report it", and it is silently skipped rather than recorded as zero `[SRC …/S3CallClassLatencyPublisher.java:51-54 @ cef8ec2]`.
- `total` is swath's own measured wall-clock and is **always** available `[SRC …/S3PageFetcher.java:270-280 @ cef8ec2]`.
- **Failure-path samples are recorded too** (same `Timer.Sample`), explicitly so a timeout storm is not survivorship-biased out of the distribution `[SRC …/S3PageFetcher.java:217-225, 348-362 @ cef8ec2]`.
- `response_parse` is swath's own client-side conversion cost, measured separately from the SDK's own handling `[SRC …/S3PageFetcher.java:297-312 @ cef8ec2]`.
- The capture is a `ThreadLocal` with `begin()`/`end()` in an **outer** `finally` wrapping the whole attempt, so it fires on every exit path including a non-`SdkException` `RuntimeException`/`Error` `[SRC …/S3PageFetcher.java:204-269 @ cef8ec2]`, `[SRC …/S3CallClassLatencyPublisher.java:22-35 @ cef8ec2]`.
- Deliberate omission: **no dominant-phase counter**. `connect_acquire` and `ttfb` may partially overlap and the SDK does not define the boundary, so any dominance test would be systematically wrong; the source says read the percentiles instead `[SRC …/S3PageFetcher.java:549-560 @ cef8ec2]`.
- **These publishers are only attached when `metrics != null`** in `S3ClientFactory.create` `[SRC …/S3ClientFactory.java:113-120 @ cef8ec2]`. Production passes `ctx.metrics()` `[SRC …/ListCommand.java:848 @ cef8ec2]`, so they are live in a real run — but a harness constructing a client any other way gets nothing.

### 7.4 Built-in API-call counters

Two, and they are **not the same thing**:
- `S3PageFetcher.apiCalls()` — an `AtomicLong` incremented at the top of `fetchPage`, i.e. **counted before the call is made**, so it counts *attempts issued*, including ones that then throw. It drives the cost line and an efficiency guard `[SRC …/S3PageFetcher.java:82, 149-152, 164 @ cef8ec2]`.
  - **Correction (review):** the "drives the cost line and an efficiency guard" half repeats the accessor's *javadoc*, which is wrong. `S3PageFetcher.apiCalls()` has **no caller in any `src/main`** — only `swath-core/src/test` uses it (mock-fetcher assertions, `ApiCallBudget.assertWithinInt8Budget`). `cost.api_calls` comes from `summary.apiCalls()`, the summed `swath.api.calls` meter `[SRC …/JsonRunSummaryWriter.java:588 @ cef8ec2]`, `[SRC …/RunMetrics.java:2169 @ cef8ec2]`. The counter described above is real and increments as stated; it is simply unused in production. Left as written (derivation record); corrected in the report at § 5.4.
- `swath.api.calls{strategy}` — incremented at `metrics.recordApiCall()` immediately inside the try, also pre-call `[SRC …/S3PageFetcher.java:214 @ cef8ec2]`.

Both therefore include retried attempts. `[INFERRED, from the increment position relative to the `s3.listObjectsV2` call]`

### 7.5 Where these surface without any interception

Three no-infrastructure routes, in increasing richness:

1. **`--progress` / `--progress-interval`** → a periodic stderr `progress` line carrying `run_id phase strategy elapsed_ms phase_elapsed_ms api_calls …` `[SRC swath-core/src/main/java/io/varve/swath/observability/LoggingProgressSink.java:32-47 @ cef8ec2]`, `[SRC swath-cli/src/main/java/io/varve/swath/cli/OutputOptions.java:376-379 @ cef8ec2]`.
2. **`--report PATH`** → the machine-readable JSON run report. Contains `cost.api_calls`, `efficiency.api_calls_per_1k_objects`, `shape.regime.api_latency_p50_ms`/`_p99_ms`, and — most usefully — **`probe_latency[]`**, the per-`call_class` × per-`phase` percentile decomposition over the whole run `[SRC swath-cli/src/main/java/io/varve/swath/cli/OutputOptions.java:349-352 @ cef8ec2]`, `[SRC swath-core/src/main/java/io/varve/swath/observability/JsonRunSummaryWriter.java:499-500, 588, 601, 836-837 @ cef8ec2]`. The project's own tuning guidance says a single run's `probe_latency[]` is enough to tell whether a budget is correctly sized `[DOC docs/internals/probe-budgets.md §5]`.
3. **`--metrics-endpoint URL`** → OTLP export of the live meter set `[SRC swath-cli/src/main/java/io/varve/swath/cli/ListOptionGroups.java:114 @ cef8ec2]`.

**Recommended capture recipe for a downstream stage** `[INFERRED, from §7.1–§7.5]`: run with `-vv --report <path>`, capture **stderr** (stdout carries listing output). `--report` gives per-call-class latency percentiles and total API-call counts without parsing anything; `-vv` gives per-request `s3_page_fetched` lines with `prefix`/`start_after`/`truncated`/`status`/`latency_ms` for reconstructing the request sequence, plus every fault line tagged with `call_class`. Caveats to carry: `s3_page_fetched` does **not** print `call_class` (only the fault and exemplar lines do); `slow_probe_exemplar` is thinned after 20; and no verbosity level will produce SDK-level wire logs.

---

## Cross-cutting notes for the study

**Guarantees vs attempts.** Genuinely guaranteed by the source: exactly-one-`listObjectsV2`-per-`fetchPage`; SDK retry disabled; byte-exact key round-trip *given a conforming endpoint*; page never committed or emitted before the two forward-progress guards; oversized page always refused and never retried; `halt` as an unconditional final rung. Merely *attempted*: waking a wedged virtual-thread socket read (interrupt rung is best-effort by the source's own admission); the latency-phase decomposition (three of five phases are best-effort SDK stamps); slow-probe log coverage (rate-limited); and correctness against a non-conforming endpoint's encoding behaviour (no validation at all).

**Discrepancies found between swath's own docs/comments and its code** — five, all minor but worth knowing if the study cites the docs:
1. `s3-implementation-compatibility.md` says the SDK decode is unconditional; the pinned SDK gates it on the response's echoed `EncodingType` `[DOC …:18-21]` vs `[3P-DEP …DecodeUrlEncodedResponseInterceptor#shouldHandle]`.
2. `ApiRateLimiter` javadoc names the flag `--rate-limit-api`; it is `--request-rate`.
3. `LivenessWatchdog`/`ListRunner` javadoc names `--stall-timeout`; it is `--idle-timeout`.
4. `S3FaultClassifier`/`ThrottleException` describe faults as arriving after SDK retries are exhausted; `maxAttempts=1` means there are none.
5. `GaugedFetcher` javadoc cites a "20 s escalated budget" for a probe retry; the current level-based mapping yields 6 s.

**Not covered here** (other readers' areas): the splitting/stealing policy and `ConcurrencyGauge`'s AIMD internals (A); the CLI surface as a whole (C); output/JSON-report structure and the trace sink (D); build/packaging (E).

**No numbers from `probe-budgets.md` or `field-investigations.md` have been reproduced as findings** — the storm's before/after magnitudes and the bimodal latency figures exist in the repo and are vendor self-reported; I have noted only that the split and the escalation ladder exist and what the code does.
