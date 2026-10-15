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
until a complete key is known; the source is designed to resume after that key
when a child dies while the coordinator survives. This also matters for multiple versions sharing one key.
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

## Recovery and output responsibilities

The comparison above covers scheduling policy, not the whole output contract.
At the pinned #2027 head, the [range scheduler](https://github.com/datachain-ai/datachain/blob/e5854d5b55b8e56ad213b7bfd9bff4bd67db614c/src/datachain/client/fsspec.py#L430-L565)
creates its work queue, observed alphabet, active-range count and probe tasks
in memory. [Bound objects](https://github.com/datachain-ai/datachain/blob/e5854d5b55b8e56ad213b7bfd9bff4bd67db614c/src/datachain/client/listing_worker.py#L26-L44)
keep the current range end and page observations there too. The
[child-recovery path](https://github.com/datachain-ai/datachain/blob/e5854d5b55b8e56ad213b7bfd9bff4bd67db614c/src/datachain/client/fsspec.py#L570-L617)
retains a last-complete-key variable and a held key group, then catches
`ChildDied` and resumes fetching within the surviving coordinator. This is not
by itself a persisted range-worklist or an output-commit cursor that survives
loss of that coordinator. The claim is confined to this listing engine:
[`read_storage` still saves its listing dataset](https://github.com/datachain-ai/datachain/blob/e5854d5b55b8e56ad213b7bfd9bff4bd67db614c/src/datachain/lib/dc/storage.py#L274-L300),
and this is not an assertion that DataChain has no persistent data or other
checkpoint facilities.

Swath's pinned public [durability documentation](https://github.com/varveio/swath/blob/69cb809112a8b3d300a6e6f40967594e59763212/docs/internals/overview.md#durability-model)
and [checkpoint interface](https://github.com/varveio/swath/blob/69cb809112a8b3d300a6e6f40967594e59763212/swath-core/src/main/java/io/varve/swath/checkpoint/CheckpointStore.java#L99-L176)
assign additional responsibilities to its work tracker and managed outputs:

| Boundary | DataChain #2027 listing path | Swath at the public source pin |
| --- | --- | --- |
| Work state | In-memory queue and range bounds in the reviewed scheduler | SQLite-backed worklist; page commits and range splits have transactional contracts |
| Recovery | Last-complete-key recovery from a child failure while the coordinator survives | Reopen incomplete work; for managed Parquet, reset listing progress to the output's durable cursor |
| Output completion | The listing generator feeds catalog writes; the reviewed range state has no durable-output acknowledgement cursor | Managed Parquet distinguishes listing cursor from durable cursor, advanced after finalized parts are durable |
| Ordering/publication | A dataset-backed API and its export are separate from the range scheduler; no equivalent managed-Parquet publisher is established by this scheduler read | Optional external sorting plus a publication stage for prepared dataset parts |

The [sorted publisher implementation](https://github.com/varveio/swath/blob/69cb809112a8b3d300a6e6f40967594e59763212/swath-core/src/main/java/io/varve/swath/output/sorted/SortedDatasetPublisher.java#L87-L115)
validates a prepared set, renames parts, syncs the directory and invokes the
publication callback. These are concrete responsibilities beyond pivot choice.
They explain part of the difference in implementation scope; they do not prove
that every extra policy or line of code is necessary, or establish measured
recovery correctness or speed. Swath's own documentation explicitly excludes
one-shot stdout/text sinks from resumability, so its managed-Parquet contract
must not be attributed to a text-mode comparison.

A useful future design question is whether DataChain's compact adaptive policy
and lack of a separate initial seed phase suit a clearly scoped one-shot,
unordered product. Swath already offers non-resumable text output; any proposed
simplification must say which additional work it removes and which contract it
changes. Total request/probe counts require traces, and performance requires
matched runs. This research supports neither removing guarantees from Swath's
resumable outputs nor declaring a speed winner.

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

## What the upstream comparison reports against Swath

These are **upstream claims**, read from the
[comparison comment](https://github.com/datachain-ai/datachain/pull/2027#issuecomment-6128223196)
on 2026-10-15, not study run records. Varve maintains this study and builds
Swath; its upstream-reported limitations receive the same treatment.

The source's table and Findings prose disagree. Bare table values appear to be
seconds, inferred from its inline minute conversions; the values below preserve
both accounts rather than choose one:

| Reported S3 workload | DataChain table value | DataChain Findings prose | Swath table entry |
| --- | --- | --- | --- |
| 257K objects | 17 | 18.3 s | 11 |
| 2.05M current objects | 47 | 171 s | 45 |
| 10.5M object versions | 174 (2.9 min) | 14.8 min | "no versioning" |

The DataChain arm is described as `read_storage(...)` at the PR #2027
tip (unmerged as of 2026-10-15) plus the #2037 key-filter fix, writing each entry into a local listing
dataset. It is not a released subject or necessarily the exact candidate head
pinned elsewhere in this report. The large current-object arm explicitly uses
`version_aware=False`, citing issue #2036 and PR #2038 without specifying the
exact assembled revision. The author says DataChain skips 55 folder markers
and that the other tools report them, which implies Swath includes them; even
the described emitted populations differ. For the
version-history arm, the author explicitly says DataChain's version count was
not checked. The Swath entry is the author's unsupported-versioning claim;
this section cites no study run record that confirms or refutes it.

The comment does not pin a Swath version or provide complete commands and
uncertainty estimates. These inconsistent numbers establish no ranking or equivalence in either
direction, including no Swath advantage or DataChain advantage. They identify comparisons to investigate after the subjects,
products, key populations and upstream discrepancies are resolved.

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
