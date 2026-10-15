# Daft: metadata discovery as a listing subject

Research and onboarding proposal, checked **2026-10-15**, using public sources.
This is **not a run record**. No subject execution, installation, benchmark,
container build, cloud resource or harness change is part of this proposal.
Varve maintains this study and builds Swath; the evidence rules apply equally.

## Decision and frozen subjects

**Daft can be a listing subject through its metadata discovery API.** Calling a
full data reader, fetching object bodies, decoding Parquet, or measuring a
multimodal pipeline would answer a different question. The candidate boundary
is public metadata discovery plus complete metadata export, with its own
filtering and execution costs disclosed. Admission to the runnable roster is a
later owner decision after qualification.

| Reference | Identity checked | Role |
| --- | --- | --- |
| [Latest stable release v0.7.26](https://github.com/Eventual-Inc/Daft/releases/tag/v0.7.26) | Published 2026-10-06; commit `aad22b926e340b09449b4bcc61d95d9964473d31` | Proposed first subject |
| [Main snapshot](https://github.com/Eventual-Inc/Daft/tree/d3cd974150ba93dbbe8efef8370b129538fcee4b) | `d3cd974150ba93dbbe8efef8370b129538fcee4b` | Separate development identity; not silently substituted |

The release date/latest status was checked through GitHub's release API and the
release tag resolved through its commit API. The source discussion below refers
to the stable commit unless a different reference is explicitly named. No wheel,
platform tag, artifact checksum or dependency closure has been selected yet;
a source commit alone is insufficient to identify a future executed subject.

## Public operation and content boundary

The released [Python public API](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/daft/io/file_path.py#L12-L58)
is `daft.from_glob_path(path, io_config=None)`, also exported at the top level.
It accepts one pattern or a list and builds a lazy glob scan DataFrame. For
whole-bucket recursive discovery, `s3://bucket/**` is the proposed pattern;
`s3://bucket/` without a wildcard is a shallow directory listing in the glob
implementation, so these cannot be exchanged as equivalent commands.

The [native glob scan source](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/src/daft-local-execution/src/sources/glob_scan.rs#L60-L132)
invokes metadata globbing, makes path and size arrays, and fills `num_rows` with
nulls. It does not call a file-format reader. The Python docstring describes a
`rows` field and Parquet counts, but that prose is not a reason to fetch Parquet
footers or promise a populated row count: the selected native source constructs
`num_rows` as null. Future qualification must record the actual public schema.
The source's [FileMetadata](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/src/daft-io/src/object_io.rs#L269-L280)
holds filepath, optional size and file type, without ETag, modification time,
version ID or storage class. The candidate's comparable fields are key and size,
with an explicit URI-to-key mapping to be qualified.

A proposed public flow is to create the DataFrame with an explicit S3 IOConfig,
then drain `iter_rows()` or `to_arrow_iter()` into a retained listing product.
The public [iterator methods](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/daft/dataframe/dataframe.py#L528-L650)
expose a results buffer measured in partitions. Calling `show()`, `.limit()`,
constructing the DataFrame, or counting only a preview cannot establish a complete
listing. `collect()` explicitly materializes the entire DataFrame. The private
`daft.daft.io_glob` binding is another product: it
[collects the entire Rust stream into a Vec and Python list](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/src/daft-io/src/python.rs#L12-L66).
It exposes extra knobs but is not the proposed public entrypoint.

Metadata-only does not mean LIST-only. An exact non-wildcard file path can cause
a size/HEAD lookup; the S3 directory path also has an exact-file fallback. A
future request trace must distinguish LIST/HEAD and establish absence of object
body reads for the selected recursive pattern. Neither inference from source
nor the name of the API replaces that qualification.

## Backend, pagination and fanout

Daft has its own Rust IO layer. The released
[IO dependency declaration](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/src/daft-io/Cargo.toml#L1-L20)
uses AWS's Rust SDK for S3 (`aws-sdk-s3` requirement 1.119.0), not Python s3fs
or obstore's object_store list stream. These are manifest requirements, not a
claim to have verified an installed dependency set. The
[Python package declaration](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/pyproject.toml#L5-L35)
requires Python >=3.10 with PyArrow, tqdm, packaging and conditional
typing-extensions; the optional AWS extra names boto3/Glue types, but that does
not turn native S3 listing into a boto3 listing loop. Exact wheel/platform and
complete dependency pins remain future build work.

The [S3 list implementation](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/src/daft-io/src/s3_like.rs#L963-L1033)
uses `ListObjectsV2`, prefix, optional delimiter and continuation token. A
[generic directory iterator](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/src/daft-io/src/object_io.rs#L351-L383)
requests each subsequent page from the previous token. One directory's pages
therefore form a dependent chain; that does not make the whole recursive glob
serial.

The [glob engine](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/src/daft-io/src/object_store_glob.rs#L219-L302)
first lists a directory with delimiter semantics. `ls_with_prefix_fallback`
buffers the directory results while counting child directories. If the count
exceeds the supplied threshold, it discards that buffer and starts a flat,
non-delimited prefix listing instead. Recursive `**`
[visits child directories through spawned tasks](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/src/daft-io/src/object_store_glob.rs#L432-L490);
the remaining threshold is the fanout limit divided by accumulated fanout.
The [S3 entrypoint](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/src/daft-io/src/s3_like.rs#L1395-L1417)
supplies a default fanout limit of 1,024 and default page size 1,000.

This is directory discovery with a flat-prefix fallback, not synthetic key-range
partitioning. A flat directory with many files but few child directories does
not trip the directory-count threshold; its directory scan can buffer many file
records. Falling back can repeat work already done before discarding the
buffer. These are source-derived work/memory risks, not measured costs or an
observed scale limit.

## Concurrency and memory boundaries

There is no single truthful listing-width knob in the selected public signature.
`S3Config.max_connections_per_io_thread` defaults to eight in the
[configuration source](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/src/common/io-config/src/s3.rs).
The [S3 connection semaphore](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/src/daft-io/src/s3_like.rs#L740-L747)
has that value multiplied by IO pool thread count; the
[runtime](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/src/common/runtime/src/lib.rs#L25-L28)
uses `min(8, available_parallelism)` for its threaded IO pool. Thus default
potential connections depend on the CPU environment. Connections, active
recursive tasks, fanout threshold and dependent page chains are different
quantities. A future adapter must record the actual configuration and CPU
visibility instead of labeling the default as fixed width one or treating a
connection setting as proof of actual concurrent LIST requests.

The public DataFrame path passes no explicit fanout/page override, so S3
backend defaults apply. The private list binding's fanout/page/multithreaded
arguments are not automatically public DataFrame options. Transfer multipart
concurrency is unrelated to enumeration width. A fair knob exploration must
use supported knobs for each compared subject and disclose the different
mechanisms rather than modify Daft's engine for an advantageous variant.

The native glob source batches metadata and limits the output channel, but
also maintains a `seen_paths` DashSet across the input batch to deduplicate
paths. This retains strings proportional to distinct discovered paths, even
when the consumer uses an iterator. Add per-directory buffering, the glob
result channel (default 16,384 entries), in-flight tasks, Arrow batches, public
iterator partition buffering, imports and serialization. A streaming consumer
does not establish bounded total memory. No memory cliff or OOM was observed
here. A future measured boundary should include initialization, discovery,
deduplication and complete export, with no warm result reuse or accidental
full `collect()` unless materialization is an explicitly different mode.

## Semantics that need qualification

The [glob filter](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/src/daft-io/src/object_store_glob.rs#L304-L340)
lowercases a copy of each path for marker matching. It excludes zero-byte keys
ending in `/`; names `_metadata`, `_common_metadata`, `_success`; suffix `.crc`;
and names beginning `_started` or `_committed`. Directory results are excluded.
These are valid S3 objects, so even a successful complete glob is not an
unfiltered all-object inventory. Lowercasing for filtering does not mean emitted
paths themselves are lowercased. Marker-policy differences must not be hidden
by reporting only matched counts.

Patterns support `*`, `?`, character classes, recursive `**`, braces and numeric
range expansion. The matcher uses literal `/` separators and backslash escapes.
A literal S3 prefix is not interchangeable with a glob or a directory. The
[fragment parser](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/src/daft-io/src/object_store_glob.rs#L155-L217)
discards empty path fragments, while the
[object URL parser](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/src/daft-io/src/utils.rs#L21-L52)
uses the raw URI key to avoid URL escaping changes but trims leading `/` from
that key. Directory listing also trims trailing slashes before adding a
separator. These transformations motivate explicit fixtures for leading slash,
repeated slash, trailing slash, literal glob metacharacters/backslash, percent
escapes, `?`/`#`, non-ASCII and supplementary Unicode, and canonically equivalent
but byte-distinct names. This review establishes no arbitrary-key completeness
or Unicode failure. The existing
[upstream S3/MinIO tests](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/tests/integration/io/test_list_files_s3_minio.py)
cover recursive patterns, escaping and fallback, but source tests are not this
study's execution evidence.

The reviewed S3 list path uses current-object `ListObjectsV2`; no all-versions
or delete-marker mode is exposed by the selected metadata API. The FileMetadata
surface would not preserve version IDs anyway. Compare current objects only,
and leave all-version enumeration to a separately derived subject if upstream
adds one.

The [S3 configuration](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/src/common/io-config/src/s3.rs)
exposes region, endpoint URL, anonymous mode, key/access-key/session-token,
profile and credentials provider, requester-pays and addressing/TLS controls.
The [client constructor](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/src/daft-io/src/s3_like.rs#L503-L540)
selects no credentials provider when anonymous is true and otherwise considers
explicit/provider/default credentials. Future qualification should explicitly
set anonymous/region for public S3, prove request signing behavior, and qualify
custom endpoint/addressing for a replay target. Defaults must not be treated as
proof that a credential-free run or an arbitrary emulator will work.

## Upstream reports and open work

The public [DataChain comparison comment](https://github.com/datachain-ai/datachain/pull/2027#issuecomment-6128223196)
reports a Daft hang on keys containing `//`, an interrupted larger S3 listing,
and an empty result for an anonymous Azure container. It also broadly groups
Daft among single-chain tools. These are upstream reports, not study results.
The comment does not give a pinned Daft version and exact API/options sufficient
to identify a reproduced subject. This source read shows a recursive directory
fanout path, so the broad serial characterization cannot establish Daft's general
mechanism. The reports motivate repeated-slash, cancellation and timeout cases;
they do not establish a hang, defect, current release limitation or ranking here.

The same comment's prose says other tools report the folder markers that some
subjects skip, while the selected Daft source explicitly filters zero-byte
slash markers. Without the original version and invocation, that tension cannot
be resolved into a claim about what ran. Counts/byte sums in an upstream table
also cannot establish key-by-key completeness, and DataChain's catalog writes
are a different product from enumeration alone. No upstream timing is adopted.

A bounded public issue/PR search on 2026-10-15 found these relevant open threads:

| Thread | Why it matters; limit of the evidence |
| --- | --- |
| [#7175, local glob/read_text_files hang](https://github.com/Eventual-Inc/Daft/issues/7175) | A report about a local filesystem special-file panic in a development version on macOS. It is not the same evidence as the upstream S3 repeated-slash report. |
| [#6344, iterative scan task generation](https://github.com/Eventual-Inc/Daft/issues/6344) | Discusses eager scan-task generation and limit behavior for file-reading plans. Do not attribute that entire reader path to the separate metadata glob DataFrame. |
| [#6400, eliminate GlobScan parallel code path](https://github.com/Eventual-Inc/Daft/issues/6400) | Proposes consolidating the metadata scan with the physical scan path. Future releases could change the memory/execution boundary; a moving main cannot inherit this release's derivation. |
| [PR #7571, object-storage key separators on Windows](https://github.com/Eventual-Inc/Daft/pull/7571) | Proposes writer-side object-key joining fixes with raw-key preservation tests. It concerns Windows writers and does not establish that the selected Linux metadata API handles every key. |

These are date-checked development signals, not merged-release guarantees or a
complete issue census. Any later issue-state change must be checked again when
selecting the subject; the stable pinned source links remain the mechanism
reference.

## Distinct coverage and future qualification

Daft deserves separate candidate research despite overlapping the prefix-walk
family represented by s7cmd and rclone. The selected pipeline combines a native
AWS SDK, recursive glob traversal, adaptive directory-to-flat fallback, filtering,
deduplication and an analytics-facing DataFrame export. That is an end-to-end
product boundary worth exposing. It is not evidence for a new range-splitting
algorithm or an expectation of better performance.

Compared with obstore, Daft is a different S3 backend and a different public
execution layer, so it is not merely another Python wrapper over the same
object_store stream. Compared with released DataChain, it does not require a
persistent listing catalog for this proposed API, but still has deduplication
and buffering costs. Comparing these complete products would not isolate their
underlying pagination engines. s7cmd and rclone remain useful directory-walk
references; having all three is meaningful only if the study records their
actual modes, filter policies, controls and costs rather than treating their
shared broad family as identical implementations.

A future owner-authorized qualification should:

1. Select the official stable wheel/platform and hash-lock its dependency
   closure, bind the exact public API and native runner, and independently
   review a runnable capsule. Main or private `io_glob` would need separate
   identity and product disclosure.
2. Validate a complete public iterator export and schema without reading object
   contents. Record LIST/HEAD/GET request shape, auth/endpoint handling, exit,
   row count, elapsed time and memory through the ordinary worker. The timer
   must include initialization and final output drain.
3. Use independently known keys/sizes across empty, shallow, deeply nested,
   bushy, flat and skewed fixtures. Include marker names with case variants,
   nonzero slash keys, escaping, Unicode and repeated/leading-slash cases.
   Compare normalized content, duplicates and explicit exclusions; a successful
   count cannot silently turn a filtered glob into a complete S3 inventory.
4. Explore supported connection controls under matched CPU/memory budgets for
   every subject. Record CPU visibility, retry policy, iterator buffer and
   actual request concurrency; retain failed/timeout attempts. Do not give one
   subject a supplied partition map or patched traversal without disclosing a
   separate arm and comparable preparation effort.
5. Predeclare diagnostic workloads and interpretation before any comparative
   runs, separate current objects from versions, and compare the common
   key/size surface. Other subjects' ETags/mtime cannot be invented for Daft.
   Keep end-to-end listing products distinct from engine-only hypotheses.

This proposal schedules none of those actions. There are no observed Daft speed,
memory, completeness or scale results in this study, and no changes to the
published diagnostic release.
