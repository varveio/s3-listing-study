# Proposed DataChain onboarding

Research prepared 2026-10-15. Execution is deferred at the owner's request.
This document does not authorize builds, smoke tests, cloud resources or runs.

## Fit with this study's process

The [onboarding sequence](../../../docs/operating/tool-onboarding.md) starts with
scope, then pinned documentation/source research, then a canonical capsule,
independent review and an owner-reviewed PR. Capsule authoring builds the claims
ledger before prose and validates the real API/CLI before calling integration
complete. The [authoring procedure](../../../docs/operating/capsule-authoring.md)
keeps source evidence separate from runtime receipts.

DataChain fits that sequence as a library with a thin, disclosed launcher. It
should not receive a custom listing implementation in the adapter. The launcher
would call upstream APIs and serialize metadata, with its work included in the
measured boundary. Source-based command preparation cannot substitute for real
binary qualification, which remains deferred.

## Keep three subjects separate

1. **Released 0.60.0:** full cold `read_storage` plus metadata export. Use fresh
   catalog/cache/tmp roots; `update=True`; explicit anonymous or authenticated
   mode; `client_config={"version_aware": False}` for current objects. Public
   `to_iter` exposes `file.path`, `file.size`, `file.etag`, `file.last_modified`.
   `File` does not preserve S3 StorageClass. This includes catalog insertion and
   export, not just S3 pagination.
2. **PR #2027:** pin `e5854d5b55b8e56ad213b7bfd9bff4bd67db614c` separately;
   preserve the same cold catalog workload and current-object semantics. Its
   [public API](https://github.com/datachain-ai/datachain/blob/e5854d5b55b8e56ad213b7bfd9bff4bd67db614c/src/datachain/lib/dc/storage.py#L69-L92)
   also accepts `versions=False`. Internal range/process limits are
   not public tuning knobs. Do not run it under the release's identity or serial
   concurrency declaration.
3. **Draft #2051:** pin `4edd2212ab080b38b32703849a2a909ac1ba30fe` if investigating
   database-free enumeration. This is a separate product and unstable API, not
   a transparent replacement for a released `read_storage` comparison.

The source-defined file-path filtering and glob/prefix semantics need explicit
scope. Leading slash, trailing slash and consecutive slash keys cannot silently
be omitted while the result is called complete. Historical versions, current
objects and delete markers also require separate products.

## Preparation questions to resolve before execution

- Pin the official wheel/source, complete dependency closure, launcher and image
  identities. Preserve each development revision independently.
- Isolate both `DATACHAIN_DIR` and `DATACHAIN_ROOT_DIR`. The latter controls
  cache/tmp roots; changing only the catalog database root is insufficient.
  See [utils.py](https://github.com/datachain-ai/datachain/blob/529a5ac2330a15a46ba64d446b075d01cd9daed4/src/datachain/utils.py)
  and [catalog initialization](https://github.com/datachain-ai/datachain/blob/529a5ac2330a15a46ba64d446b075d01cd9daed4/src/datachain/catalog/catalog.py).
- Explicitly disable analytics through `DATACHAIN_NO_ANALYTICS` and bind that
  functional environment to the invocation; see
  [telemetry.py](https://github.com/datachain-ai/datachain/blob/529a5ac2330a15a46ba64d446b075d01cd9daed4/src/datachain/telemetry.py).
- Validate signing without silent anonymous fallback, region/custom endpoint
  behavior, fresh state, exact field types and empty-bucket output.
- Decide whether four common metadata fields suffice for comparison; do not
  invent StorageClass. Independently compare keys and common fields, including
  duplicates and unusual legal keys. Count and byte-sum agreement are weaker.
- Explore supported configuration controls with comparable effort for every
  subject and disclose each setting. Internal constants must not be presented
  as public knobs, and unrelated downstream compute settings must not be
  described as listing concurrency.
- Require independent review plus real container/API qualification before saying
  the capsule is runnable. No such qualification is claimed by this research.

## Questions a later comparison could answer

Use equal CPU/memory allocations and the same current-object workload; retain
failed attempts and all setup asymmetries. Compare end-to-end products first,
then isolate listing-only work if a public API permits it. Test small buckets,
broad hierarchies, flat keys, a dominant hot prefix and unusual valid keys.
Repeated live-S3 observations and calibrated replay have different evidence
limits; the current diagnostic release cannot supply a ranking.

The hypotheses are deliberately two-sided: DataChain's compact policy may avoid
seeding overhead on small or straightforward shapes; Swath's extra seeding and
split policies may help skew or sparse/irregular keys. Catalog materialization
may dominate DataChain even when its enumeration is competitive; initialization
and output serialization may also materially affect Swath's end-to-end result. None of these
is an observed result, and upstream PR timing tables cannot settle them here.
