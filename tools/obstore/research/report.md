# obstore listing: source research and onboarding proposal

Read on 2026-10-15. This is **not a run record**. No package installation,
subject execution, Docker build, harness integration or cloud run was performed.
All behavioral descriptions below are source contracts or upstream reports;
study observations of runtime behavior remain unverified.

## Identity and scope

The [latest GitHub release](https://github.com/developmentseed/obstore/releases/tag/py-v0.12.0)
is `py-v0.12.0`, published 2026-10-15 at 15:01:29 UTC, commit
`abe772d39f446a7850fa61060bc7b0c3c9792cad`. Main resolved to that same commit when
checked. Earlier DataChain comparisons do not identify their obstore version,
so their claims cannot be attributed to this newly released version.

The [package manifest](https://github.com/developmentseed/obstore/blob/abe772d39f446a7850fa61060bc7b0c3c9792cad/obstore/pyproject.toml)
requires Python 3.11 or newer and uses maturin. The
[Rust manifest](https://github.com/developmentseed/obstore/blob/abe772d39f446a7850fa61060bc7b0c3c9792cad/Cargo.toml)
allows `object_store` 0.14.x; the committed
[lockfile](https://github.com/developmentseed/obstore/blob/abe772d39f446a7850fa61060bc7b0c3c9792cad/Cargo.lock#L1422)
resolves **0.14.2**, registry checksum
`f1796bc93603f78c5760a69f2d58badc9618d22adade0a95385bb2adbae4eb94`.
The corresponding upstream dependency tag `v0.14.2` points to
`279572ea60f3a7a6e5237e066a6f4a35eee611e0`. This locks the source derivation;
an eventual wheel's compiled dependency identity still needs artifact provenance.

Obstore is in scope as a potential current-object S3 metadata lister. It also
supports other backends, but this proposal does not expand the study to them.
It offers no user-facing adaptive key-range scheduler in the selected list API.

## Public API

The [Python contract](https://github.com/developmentseed/obstore/blob/abe772d39f446a7850fa61060bc7b0c3c9792cad/obstore/python/obstore/_list.pyi#L136)
and [Rust binding](https://github.com/developmentseed/obstore/blob/abe772d39f446a7850fa61060bc7b0c3c9792cad/obstore/src/list.rs#L350)
give this signature:

```python
obstore.list(store, prefix=None, *, offset=None, chunk_size=50, return_arrow=False)
```

It returns a `ListStream`: synchronous or asynchronous iteration yields chunks
of metadata dictionaries, or Arrow record batches with `return_arrow=True`.
`prefix` and `offset` are optional strings. `chunk_size` is Python emission
batch size, not a request-page size or network-concurrency setting. `collect()`
and `collect_async()` collect all remaining rows into one result and ignore
chunk size. Ordering is explicitly not guaranteed by the Python contract.

`list_with_delimiter(store, prefix=None, *, return_arrow=False)` and its async
counterpart return objects and common prefixes for one directory level; the
dependency implementation collects its paginated directory results before
returning. It should not be assumed to be a streaming recursive alternative.

The [S3 constructor contract](https://github.com/developmentseed/obstore/blob/abe772d39f446a7850fa61060bc7b0c3c9792cad/obstore/python/obstore/_store/_aws.pyi#L539)
is:

```python
S3Store(bucket=None, *, prefix=None, config=None, client_options=None,
        retry_config=None, credential_provider=None, **kwargs)
S3Store.from_url(url, *, config=None, client_options=None,
                 retry_config=None, credential_provider=None, **kwargs)
```

`kwargs` accepts the typed S3 configuration fields. A URL path becomes a store
prefix and subsequent operation paths are relative to it. Prefer an unprefixed
bucket store for a future full-key output contract, so store-prefix stripping
does not need to be reversed by an adapter.

This hypothetical example has **not been executed**; it is an API proposal,
not a tested command or harness adapter:

```python
import json
import obstore
from obstore.store import S3Store

store = S3Store("example-bucket", region="us-east-1", skip_signature=True)
for batch in obstore.list(store, prefix="example-folder", chunk_size=1000):
    for row in batch:
        print(json.dumps({
            "key": row["path"], "size": row["size"],
            "etag": row["e_tag"],
            "last_modified": row["last_modified"].isoformat(),
        }, ensure_ascii=True))
```

For a whole bucket, omit `prefix`. `example-folder` means a directory segment,
not a literal S3 string prefix such as the start of a filename. A future timed
invocation would include formatting and output costs if this output arm is used.

## S3 request mechanism

The binding creates one `store.list()` or `store.list_with_offset()` stream.
The dependency's [AmazonS3 implementation](https://github.com/apache/arrow-rs-object-store/blob/279572ea60f3a7a6e5237e066a6f4a35eee611e0/src/aws/mod.rs#L379)
delegates to its list client. The
[shared list client](https://github.com/apache/arrow-rs-object-store/blob/279572ea60f3a7a6e5237e066a6f4a35eee611e0/src/client/list.rs#L63)
passes the previous page token into the next paginated request and flattens each
page's objects into the returned stream. There is no range splitting or recursive
prefix discovery in this `list()` path. Async iteration does not create independent
continuation chains, and the binding serializes access to a stream with a mutex.

The [S3 request implementation](https://github.com/apache/arrow-rs-object-store/blob/279572ea60f3a7a6e5237e066a6f4a35eee611e0/src/aws/client.rs#L980)
sends `list-type=2`, optional `continuation-token`, `prefix` and `start-after`.
It uses GET listing responses, without per-object HEAD or GET enrichment in
this path. This is current-object `ListObjectsV2`, not `ListObjectVersions`:
the metadata `version` field does not mean the API enumerates history or delete
markers. The Python list API has no all-versions switch.

For S3 Express, the dependency's offset path cannot use `start-after`; it lists
then filters by key. Directory-bucket behavior deserves its own qualification
instead of importing general-purpose bucket assumptions.

## Output, prefixes and unusual keys

The binding's [metadata conversion](https://github.com/developmentseed/obstore/blob/abe772d39f446a7850fa61060bc7b0c3c9792cad/obstore/src/list.rs#L42)
exposes `path`, `last_modified`, `size`, nullable `e_tag` and nullable `version`.
Arrow carries the same information; neither form exposes storage class here.
Null version/ETag must remain null rather than invented identifiers. An eventual
normalizer must decide ETag quotation and timestamp normalization explicitly.

Prefix matching is by path segment: `foo/bar` includes `foo/bar/x` and descendants,
not `foo/bar_baz/x`. The shared client appends `/` to nonempty parsed prefixes.
An offset restricts starting position; it is not an upper bound and cannot by
itself define a disjoint bounded partition. Multiple manually constructed offset
streams would be an external partitioning design, not native range splitting.

Python paths go through
[PyPath / Path::parse](https://github.com/developmentseed/obstore/blob/abe772d39f446a7850fa61060bc7b0c3c9792cad/pyo3-object_store/src/path.rs#L11).
The dependency's [Path implementation](https://github.com/apache/arrow-rs-object-store/blob/279572ea60f3a7a6e5237e066a6f4a35eee611e0/src/path/mod.rs#L93)
documents UTF-8 paths, no relative `.`/`..` segments, no empty interior segments
and no ASCII controls. Parsing strips a leading and trailing slash; it does not
preserve every valid S3 key representation. Unicode strings are supported, but
that is not a measured Unicode round-trip guarantee or a normalization policy.
Literal percent sequences and canonically equivalent Unicode keys need byte-exact
future fixtures rather than URL-decoding assumptions.

Open [#429](https://github.com/developmentseed/obstore/issues/429) reports that
double-delimiter keys stop iteration; comments include an S3 example as well as
Azure. This is an upstream report, not a reproduced result for 0.12.0. The source
path restrictions explain the risk. A future attempt must retain partial output
and failure status rather than silently skip the key or publish partial success.
Leading/trailing slashes and folder-marker objects also need explicit identity
checks; this proposal does not claim their observed behavior.

## Memory and concurrency

The [binding stream implementation](https://github.com/developmentseed/obstore/blob/abe772d39f446a7850fa61060bc7b0c3c9792cad/obstore/src/list.rs#L80)
holds one fused stream and accumulates up to `chunk_size` metadata entries for a
normal next call. It does not collect the entire bucket in this mode. The network
page, Rust-to-Python conversion and Arrow conversion also occupy memory; no peak
RSS bound has been measured. `collect_stream` instead appends until exhaustion,
so total retained metadata grows with remaining listing size. Arrow conversion
does not turn collection into bounded streaming.

Both sync and async stream consumption are available; multiple consumers of one
stream share its mutex. The release includes
[#787](https://github.com/developmentseed/obstore/pull/787), which detaches from
the Python interpreter while constructing a stream. That change is not evidence
that concurrent calls increase S3 listing request parallelism.

## Authentication, endpoints and relevant open work

The S3 constructor reads environment configuration. Explicit `config` overrides
environment values; typed keyword fields cover region, access key, secret, token,
requester pays and endpoint. A custom sync/async credential provider is supported.
`skip_signature=True` avoids credential lookup and signing for anonymous access.
Public fixtures should use it explicitly. Authenticated qualification needs a
separate scoped configuration and must not log secrets.

Custom `endpoint` and `virtual_hosted_style_request` must agree. Path-style is
appropriate to consider for a local S3 endpoint; HTTP requires
`client_options={"allow_http": True}`. The service-specific `aws_endpoint_url_s3`
setting takes precedence over generic `endpoint`. Open
[#754](https://github.com/developmentseed/obstore/issues/754) reports confusion
because virtual-hosted mode expects the bucket in the endpoint, unlike boto3's
service-root convention; the selected source contract documents that requirement.
These options make endpoint qualification plausible, not proven compatible.

Other relevant open work, checked 2026-10-15:

- [#545](https://github.com/developmentseed/obstore/pull/545) proposes paginated
  list support for substring prefixes; it is unmerged at
  `231ac4d394be7894445a39efaccb10e04d263e19`. Current path-segment semantics remain.
  It addresses [#494](https://github.com/developmentseed/obstore/issues/494).
- [#791](https://github.com/developmentseed/obstore/pull/791) proposes preserving
  modification time and version in fsspec `ls(detail=True)`. It is unmerged.
  Direct native metadata conversion already exposes those fields; do not conflate
  the fsspec wrapper with the proposed native listing subject.
- [#762](https://github.com/developmentseed/obstore/pull/762) proposes replacing
  store prefixes while sharing the connection pool. It is unmerged; avoid
  treating it as an available knob.
- [#577](https://github.com/developmentseed/obstore/issues/577) questions runtime
  selection for object_store Tokio tasks; worth tracking for a future async arm.

## Onboarding recommendation and future decisions

Recommend a separate released obstore subject using native `list()` streaming.
Its direct metadata boundary differs from DataChain's cold catalog materialization
and adds a Rust library baseline alongside CLI tools. The selected list path is
serial pagination; Swath's range-splitting strategy is different. Neither source
structure nor the upstream DataChain table establishes a faster subject. Varve
maintains this study and builds Swath, which remains subject to identical rules.

Before any runnable capsule or execution is authorized:

1. Select and checksum-pin the official 0.12.0 wheel for the declared Python,
   architecture and platform; inspect wheel availability and dependency provenance.
   Prefer that artifact to a new source build. Record artifact and image identities
   only when actually obtained; none are fabricated here.
2. Derive a claims ledger and full capsule under the existing onboarding workflow,
   including independent review. Choose native dict streaming as the base output
   arm and an optional Arrow arm with a pinned `arro3-core` dependency; disclose
   format costs and identical field coverage.
3. Qualify full-key identity on an unprefixed store, exact output escaping,
   nullable fields, errors after partial results and endpoint/auth behavior.
   Do not add manufactured substring prefixes or parallel ranges to the base mode.
4. Predeclare comparable batch-size exploration; batch size is a conversion knob,
   not S3 concurrency. An optional collect arm must be labeled as materializing
   and given the same memory budget as other materializing products.

Possible later workloads, subject to separate execution authorization, include a
flat namespace, a bushy namespace, a highly skewed directory, small buckets where
initialization dominates, directory-segment and partial-filename prefix queries,
and adversarial identity fixtures (interior double slash, leading/trailing slash,
folder markers, percent literals, Unicode and controls). Version-history listing
is unsupported by this chosen API and should not become a failed current-object
comparison. Future correctness checks should compare exact expected keys and
metadata, not only counts or byte sums. Any measured result requires a committed
run record; this candidate contributes none to the published release.
