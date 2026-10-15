# DataChain: released and proposed S3 listing

Public documentation and source read on 2026-10-15. This is **not a run record**.
This is research for a prospective capsule, not a registered runnable subject.
Source establishes mechanisms; upstream benchmark reports remain upstream
claims. No speed ranking or arbitrary-key completeness follows from this report.
Varve maintains this study and builds Swath; Swath receives the same evidence rules.

## Can it be a listing subject?

Yes, if the product is explicit. Released `read_storage` enumerates S3 object
metadata and persists a catalog; it does not need to download object contents
for this task. A cold listing-plus-catalog export is a legitimate end-to-end
subject, but its database work makes it different from a standalone lister.
Draft #2051 proposes a listing-only public iterator, which would be a separate
experimental subject. Neither has been benchmarked by this study.

## Subjects and release history

| Subject | Frozen identity | State checked 2026-10-15 |
| --- | --- | --- |
| [0.60.0](https://github.com/datachain-ai/datachain/releases/tag/0.60.0) | `529a5ac2330a15a46ba64d446b075d01cd9daed4` | Latest GitHub release, published October 4; selected released subject |
| Main | `df8246467fce0f78a1477019c9629c5c0dea65a9` | Contains changes after the release; a different subject |
| [PR #2027, Speed up reading buckets](https://github.com/datachain-ai/datachain/pull/2027) | `e5854d5b55b8e56ad213b7bfd9bff4bd67db614c`, `wt-listing` | Open, unmerged; proposed parallel listing engine |
| [PR #2045, Speedup listing 2 - swath ideas](https://github.com/datachain-ai/datachain/pull/2045) | `f650c3aac806ed670415dcac839a167dd0a0b96f`, `wt-swath` | Closed, unmerged; author says [all moved to #2027](https://github.com/datachain-ai/datachain/pull/2045#issuecomment-6182395379) |
| [PR #2051, dc.listing](https://github.com/datachain-ai/datachain/pull/2051) | `4edd2212ab080b38b32703849a2a909ac1ba30fe`, `wt-listing-api` | Open draft based on #2027; database-free API proposal |
| [PR #2052, delete markers](https://github.com/datachain-ai/datachain/pull/2052) | `38af9e370a225ccaf655da578e69578b38ba45f7` | Open, based on #2027; proposes `versions="all"` |

The source and release identities must remain separate. In particular, closing
#2045 did not merge its algorithm into a release. 

## Released API and mechanism

The released public entrypoint is `datachain.read_storage`. Its
[storage module](https://github.com/datachain-ai/datachain/blob/529a5ac2330a15a46ba64d446b075d01cd9daed4/src/datachain/lib/dc/storage.py)
resolves listings into saved catalog datasets. A cold operation therefore includes
database insertion. `update=True` refreshes a
listing; an isolated catalog also avoids reuse between attempts.
The existing `dc.listings()` enumerates cached listing datasets, not bucket keys.

The released [S3 backend](https://github.com/datachain-ai/datachain/blob/529a5ac2330a15a46ba64d446b075d01cd9daed4/src/datachain/client/s3.py)
uses one flat pagination chain by default. Version awareness
selects `ListObjectVersions`; disabling it selects `ListObjectsV2`. The [shared client configuration](https://github.com/datachain-ai/datachain/blob/529a5ac2330a15a46ba64d446b075d01cd9daed4/src/datachain/client/fsspec.py#L160-L164)
defaults version awareness to true. On this release, pass `client_config={"version_aware": False}`
for current-object listing. The convenience `read_storage(..., versions=False)`
arrived through [#2038](https://github.com/datachain-ai/datachain/pull/2038), merged
October 12, after 0.60.0. Anonymous access should be explicit for public fixtures;
endpoint configuration belongs in the backend's client configuration.

The shared [client module](https://github.com/datachain-ai/datachain/blob/529a5ac2330a15a46ba64d446b075d01cd9daed4/src/datachain/client/fsspec.py)
rejects keys starting or ending in slash, or containing `//`.
The released result queue is unbounded while the S3 page queue holds two pages; this is a source-level buffering risk, not an observed
memory limit. S3 `File` construction carries key, size, ETag and modification
time, without passing through storage class.

## Proposed range engine

The following describes #2027's pinned
[shared scheduler](https://github.com/datachain-ai/datachain/blob/e5854d5b55b8e56ad213b7bfd9bff4bd67db614c/src/datachain/client/fsspec.py),
[S3 backend](https://github.com/datachain-ai/datachain/blob/e5854d5b55b8e56ad213b7bfd9bff4bd67db614c/src/datachain/client/s3.py)
and [process transport](https://github.com/datachain-ai/datachain/blob/e5854d5b55b8e56ad213b7bfd9bff4bd67db614c/src/datachain/client/listing_worker.py),
not the selected release.

It begins with one `(lo, hi]` range, with an open high end. A one-page listing
avoids splitting. As pages arrive, active ranges offer upper pieces while fewer
than the configured internal number of ranges are open. The scheduler uses up to 16
in-flight ranges without the process pool, or up to 64 with it.

`key_midpoint` integer-encodes strings using a sorted alphabet augmented by
observed characters. It chooses a shortest strict interior key. Young ranges,
through four inherited pages, limit speculative distance using a page's observed
first-to-last span. Older bounded ranges bisect their remaining space; older open
ranges extrapolate using pages squared. Far splits may probe one key while the
original range continues reading. Empty probes tighten the bound and can propose
a nearer dense split; bounded S3 splits skip probing.

Child processes request and parse pages. Migration holds the last key's group
until a complete key is known, so a range can resume after that key, including
when a child dies. This also matters for multiple versions sharing one key.
GCS can send an upper `endOffset`; S3 and Azure filter results beyond the upper
bound after receiving a page. Crossing-page work is therefore possible even when
emitted ranges are disjoint. [#2050](https://github.com/datachain-ai/datachain/issues/2050)
proposes reducing that over-read; its reported amplification ratios are upstream
observations, not study measurements.

The PR also changes response parsing, schema conversion, warehouse writes and
tuple rows. Any future end-to-end timing difference would include those changes,
not just the range algorithm. At the exact #2027 head above, `_is_valid_key` still
rejects leading slash, trailing slash and `//`. The comparison comment describes
using a separate key-filter fix; that does not make the fix part of this head.

## Public Swath relationship

#2045 explicitly credits [Swath](https://github.com/varveio/swath) for better
midpoints, and its title names Swath ideas. This establishes
public attribution, not invention priority, equivalent invariants or code copying.
The upstream comparison also names Swath as a subject. The reviewed #2027 and #2045 descriptions and their discussion explicitly name
Swath; this report makes no broader claim about attribution to the study.

For a public-source comparison, Swath was read at
`69cb809112a8b3d300a6e6f40967594e59763212` (a public source snapshot committed
2026-09-03), independently of this study's Swath capsule. This is a source
comparison, not the identity of a newly executed Swath subject.
Its [ByteMidpoint](https://github.com/varveio/swath/blob/69cb809112a8b3d300a6e6f40967594e59763212/swath-model/src/main/java/io/varve/swath/model/ByteMidpoint.java)
contains pivot-construction code using UTF-8 scalars, strict-interior checks,
a 1,024-byte cap, fractional splits and observed-per-position alphabets. These
are implementation features, not verified correctness guarantees. This review
did not audit DataChain and Swath against the same Unicode/key-length edge
cases, so it draws no comparative correctness conclusion from these details. A separate
[SeedPlanner](https://github.com/varveio/swath/blob/69cb809112a8b3d300a6e6f40967594e59763212/swath-core/src/main/java/io/varve/swath/engine/policy/SeedPlanner.java)
defines budgeted initial descent, while
[StealMath](https://github.com/varveio/swath/blob/69cb809112a8b3d300a6e6f40967594e59763212/swath-core/src/main/java/io/varve/swath/engine/StealMath.java)
provides interpolation and observed-mass split arithmetic.

DataChain's visible scheduling policy is smaller: one initial range, shared
observed-character rank arithmetic, page-age rules and conditional probes.
Swath separates seeding, pivot construction and split policies. Both use range
partitioning and density-sensitive proposals. “Simpler” describes that visible
policy structure; it establishes no performance or correctness advantage. This comparison does not reconstruct which historical
Swath revision the DataChain author read.

## Correctness and API follow-ups

[#2037](https://github.com/datachain-ai/datachain/pull/2037) is an open fix for
interior double slashes; leading slash handling remains a separate
[#2040](https://github.com/datachain-ai/datachain/issues/2040) concern involving
export collisions, GCS reads and root paths. Destination validation is tracked in
[#2041](https://github.com/datachain-ai/datachain/issues/2041). These reports motivate
specific fixtures; they are not this study's execution evidence.

[#2051](https://github.com/datachain-ai/datachain/pull/2051) proposes single-use
`dc.listing(uri)` iteration and conversions to objects, Arrow, pandas or a saved
snapshot. That API would permit separating enumeration
from catalog insertion, but it is an additional experimental subject.
[#2052](https://github.com/datachain-ai/datachain/pull/2052) proposes retained history
including deletion markers; it must not be confused with ordinary current-object
listing or the default retained object versions.

Historical [#1836](https://github.com/datachain-ai/datachain/issues/1836) concerned
duplicated local-plus-Studio CLI display, fixed by merged
[#1841](https://github.com/datachain-ai/datachain/pull/1841). That is a different
problem from #2050's duplicate reads and does not establish duplicate emitted
keys in the range engine.

## Other tools in the upstream comparison

The [comparison comment](https://github.com/datachain-ai/datachain/pull/2027#issuecomment-6128223196)
names the following subjects. Recommendations concern study coverage, not speed.
Membership is checked against [`../../README.md`](../../README.md).

| Subject | Existing coverage and recommendation |
| --- | --- |
| Swath, s3p, s5cmd, rclone, AWS CLI | Already included; assess their actual modes rather than importing the comment's broad serial characterization. |
| s3ls-rs | Its engine is represented by s7cmd. The [pinned README](https://github.com/nidor1998/s3ls-rs/blob/2993e4f8fd23316c63f63d04a6b6f2102cfdd41b/README.md) explicitly describes shared underlying code and behavior; no separate default onboarding recommended. |
| obstore | Strong new S3 library candidate. Its [Rust wrapper](https://github.com/developmentseed/obstore/blob/abe772d39f446a7850fa61060bc7b0c3c9792cad/obstore/src/list.rs) delegates to an object_store list stream. The [Python contract](https://github.com/developmentseed/obstore/blob/abe772d39f446a7850fa61060bc7b0c3c9792cad/obstore/python/obstore/_list.pyi) supports chunk iteration and Arrow; `collect()` materializes remaining results. Prefix path-segment semantics require review against literal S3 prefixes. |
| Daft | Worth separate scoping after obstore. Its [glob implementation](https://github.com/Eventual-Inc/Daft/blob/d3cd974150ba93dbbe8efef8370b129538fcee4b/src/daft-io/src/object_store_glob.rs) contains directory fanout and flat-prefix fallback, so a universal serial label would be inaccurate. Exact public API/options need their own derivation. |
| Lance | Lower priority pending recovery of the exact metadata-list entrypoint and command. The upstream comment says paths lack sizes and attributes key failures to underlying object_store; those are reports to investigate. |
| Dataflux (Google, Go) | GCS only; useful future cross-cloud range/work-stealing subject, outside this S3 cohort. Correct source is [google-cloud-go/storage/dataflux](https://github.com/googleapis/google-cloud-go/tree/a9a05e0dc5b806d7aceddb2a54a9848d778eb074/storage/dataflux), whose README documents `NewLister` and `NextBatch`. The similarly named Python client is a different implementation. |
| azcopy | Azure subject; defer unless the owner expands scope. |
| gcloud | GCS baseline; defer unless the owner expands scope. |
| “Our ceiling” | An upstream prototype description without an identifiable published artifact; not an onboardable tool identity. |

The comment provides neither pinned tool versions nor exact commands. Its table
and Findings prose contain inconsistent updated timings: the DataChain
10.5M-version table cell says 174 seconds (2.9 minutes), while Finding 5 says
14.8 minutes, as read on 2026-10-15. DataChain's row includes
catalog writes while others list only; some version-listing tools run concurrently. Checks
use counts and byte sums, with count only for Lance, and marker handling differs.
The comment explicitly says DataChain's all-version count is not checked using
`count()`. These are material limits: the table cannot become study run evidence,
and count agreement cannot establish key-by-key completeness. Upstream reports of
obstore/Lance failures or a Daft hang remain unverified here.
