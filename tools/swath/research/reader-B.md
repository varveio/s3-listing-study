# Reader B (store area): v0.2.0 (cef8ec2) -> v0.3.1 (7b9a5e2)

All line numbers below were checked with `sed -n` on the frozen v0.3.1 tree. `[SRC path:lines @ 7b9a5e2]`.

## What changed

**1. Swath now parses and percent-decodes ListObjectsV2 responses itself (0.3.0, PR #135).**
`S3ClientFactory.create` registers a swath-owned `StreamingListObjectsV2Interceptor` on every client
`[SRC swath-s3/src/main/java/io/varve/swath/store/s3/S3ClientFactory.java:120 @ 7b9a5e2]`, and every
`S3PageFetcher.fetchPage` attaches a `DirectPageCarrier` execution attribute to its request
`[SRC swath-s3/.../S3PageFetcher.java:187-197 @ 7b9a5e2]`. For a 2xx body the interceptor streams the
XML with StAX (Woodstox 7.2.2, pinned `[SRC swath-s3/build.gradle.kts:24-27 @ 7b9a5e2]`), builds swath's
`ListEntry`/`KeyBytes` page directly, and hands the SDK an empty `<ListBucketResult/>`
`[SRC swath-s3/.../StreamingListObjectsV2Interceptor.java:77-123 @ 7b9a5e2]`. The decode is
`decode = EncodingType.URL.toString().equals(encodingType)` and then `SdkHttpUtils.urlDecode(key)`
`[SRC .../StreamingListObjectsV2Interceptor.java:533,540,551 @ 7b9a5e2]`. `SdkHttpUtils.urlDecode` is
`java.net.URLDecoder.decode(s, "UTF-8")` in the still-pinned SDK 2.31.78
`[SRC gradle/libs.versions.toml:6 @ 7b9a5e2]`, so the plus-to-space and case-sensitive-echo-gate
properties are unchanged, but they are now swath's own code rather than a disassembled SDK fact.
The old SDK-model conversion (`toEntry`) survives only as a metered fallback (`direct_page_absent`)
`[SRC .../S3PageFetcher.java:305-320 @ 7b9a5e2]`; the `S3PageFetcherKeyDecodeTest` still drives a
fake client and therefore covers only that fallback. The LocalStack IT builds its client through
`S3ClientFactory.create` `[SRC swath-s3/src/testFixtures/.../LocalStackSupport.java:57-59 @ 7b9a5e2]`, so
the `%`/`+` round-trip assertion `[SRC .../PercentEchoLocalStackIT.java:110-148 @ 7b9a5e2]` now
exercises the production interceptor path. New store-side guards: unexpected root element
`[SRC ...Interceptor.java:155-162]`, unparseable 2xx body -> `SdkClientException "Unable to stream
ListObjectsV2 XML response"` + `FATAL/streaming_xml_unparseable` `[SRC ...Interceptor.java:114-122]`,
`<Error>` under HTTP 200 re-serialized for the SDK's error handler `[SRC ...Interceptor.java:98-103]`.
The classifier treats that `SdkClientException` as fatal unless an `IOException` is in its cause chain
`[SRC swath-s3/.../S3FaultClassifier.java:317-320 @ 7b9a5e2]`.

Effect on claims: `encoding-type-url-no-local-decode` is **contradicted** (successor text proposed);
`plus-to-space-conditional-hazard` and `encoding-contract-not-validated` are **changed** in wording and
provenance only; `protocol-violation-defences` gains a fourth (store-side parse) defence.

**2. User-Agent (0.2.4, PR #104).** `swath/<Implementation-Version>` or `swath/development` is prepended
to the SDK's User-Agent `[SRC .../S3ClientFactory.java:66-72,119 @ 7b9a5e2]`, asserted against a real
Apache request `[SRC swath-s3/src/test/.../S3ClientFactoryTest.java:87-103 @ 7b9a5e2]`. New fact.

**3. Seed fail-fast (0.3.0, PR #165).** `TransientRetryFetcher.forSeed` builds a decorator that turns a
`ThrottleException` with `ConnectException`/`UnknownHostException` in its cause chain into a fatal
`ListingException` on the first attempt, metered `FATAL/seed_endpoint_unreachable`
`[SRC swath-core/.../TransientRetryFetcher.java:151-160,205-211,264-275 @ 7b9a5e2]`; wired only for the
seed `[SRC swath-cli/.../ListCommand.java:1350-1351 @ 7b9a5e2]`; the single attempt still counts one
API call `[SRC swath-s3/src/test/.../SeedDeadEndpointFailFastTest.java:67 @ 7b9a5e2]`. SDK
`maxAttempts` stays 1 `[SRC swath-s3/.../S3Config.java:80 @ 7b9a5e2]`. New fact.

**4. Forward-progress guard widened.** `RangeScanner` now applies `lastKey <= startAfter -> "no forward
progress"` to every non-empty page including the terminal one; v0.2.0 ran it only under `!done`
`[SRC swath-core/.../RangeScanner.java:274-281 @ 7b9a5e2]`, tested by
`terminalPageRepeatingStartAfterIsAnErrorBeforeEmit` `[SRC swath-core/src/test/.../RangeScannerTest.java:95-113]`.
Replay authors: a last page repeating the cursor key is now fatal. Folded into
`protocol-violation-defences` (changed) and listed as a new fact. No intra-page ordering check was
added anywhere (`no-intra-page-ordering-check` holds; `KeyBytes.compareUnsigned` anchor narrows to
45-47 because `isValidUtf8` was inserted at 49-96).

**5. Bearer-token subprocess bounds (0.3.0).** 64 KiB stdout cap, 4096-char stderr diagnostic, 2 s
stream-close timeout, descendant tree killed `[SRC swath-s3/.../ProcessBearerTokenSupplier.java:37-40,127,143-147,196-197 @ 7b9a5e2]`.
Credential precedence itself is unchanged. New fact.

**6. Docs.** The FAQ was rewritten; the region entry moved to `[SRC docs/faq.md:61-67 @ 7b9a5e2]`.
README quickstarts now carry `--no-sign-request --region us-east-1` `[SRC README.md:42,54 @ 7b9a5e2]`
and operating.md documents exit 2 before any request `[SRC docs/operating.md:25-31 @ 7b9a5e2]`, so the
`region-required-even-anonymously` qualification ("quickstarts omit all three") is revised. The
compatibility note now describes the interceptor decode path `[SRC docs/internals/s3-implementation-compatibility.md:18-25,36-41 @ 7b9a5e2]`;
its continuation-token paragraph moved to 139-141.

## Unchanged (holds / holds-reanchored)

Pagination purely by `start_after` (`RangeScanner:294` unchanged line; `S3PageFetcher:179-181,343-346`;
`PageRequest:11-15` file unchanged). SDK retry disabled (`S3ClientFactory:111-114`, `S3Config:75-80`,
`ConnectionOptions:188-199`). Error classification and region-redirect handling: `S3FaultClassifier`,
`RegionRedirectException`, `SwathException`, `ProtocolViolationException` are byte-identical between
tags. Anonymous listing and region resolution (`ConnectionOptions:212-224,234-247,249-257`, all +1 from
the two-line `--concurrency` option at 79-80). `api_calls` single increment site
(`S3PageFetcher:211-212`; `RunMetrics:847-852,2477`; `JsonRunSummaryWriter:604`). Page size hard-coded
(`ListCommand:437`; the only `--max-keys` is `swath-replay/.../BenchCommand.java:46`). Requester-pays
wiring unchanged (`S3PageFetcher:173-174`). `non-snapshot-pagination-misses-late-inserts` remains
unverified for the same reason: no mutation or replay experiment exists.

## Verdict counts
holds 3 (error-classification-is-specific, wrong-region-is-fatal, non-snapshot-pagination-misses-late-inserts [still unverified]);
holds-reanchored 6; changed 4 (plus-to-space-conditional-hazard, encoding-contract-not-validated,
protocol-violation-defences, region-required-even-anonymously); contradicted 1
(encoding-type-url-no-local-decode); gone 0. New facts 5.
