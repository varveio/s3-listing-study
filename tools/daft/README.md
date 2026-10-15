# Daft

Daft is a data engine with a public metadata-only file discovery operation.
That operation is a plausible S3 listing subject: a data tool can be studied as
a lister when its selected public API enumerates object metadata without
reading object contents. The engine's broader analytics capabilities are a
different workload.

**Research candidate only, checked 2026-10-15.** No Daft subject was executed,
installed, built, or benchmarked for this proposal. There is no runnable capsule,
build registration, adapter, qualification receipt, or calibrated result.
Daft has no rows in `2026-09-scale-diagnostics`; that release remains diagnostic
and supplies no Daft ranking.

The proposed first subject is upstream **v0.7.26**, commit
`aad22b926e340b09449b4bcc61d95d9964473d31`. Its public
`daft.from_glob_path("s3://bucket/**", io_config=...)` creates a lazy metadata
DataFrame; draining a public iterator would include discovery and metadata
export. This is a proposed workload, not an executed command. The proposal
explicitly selects `daft.set_runner_native()` before runner initialization.
In the pinned v0.7.26 native-runner source, metadata has path, optional size and
`num_rows` built as null, without ETag, modification time or storage class.
Source shows recursive directory tasks and a flat-prefix fallback; actual
concurrent LIST requests remain unverified. The proposed export is a full
`iter_rows()` drain; Arrow export would be a separate arm.

The pinned [glob filter](https://github.com/Eventual-Inc/Daft/blob/aad22b926e340b09449b4bcc61d95d9964473d31/src/daft-io/src/object_store_glob.rs#L302-L340)
excludes valid S3 objects: zero-byte slash markers, case-insensitive Spark
marker names/prefixes and `.crc` suffixes. A complete glob is therefore not an
unfiltered object inventory; exclusions must be reported.

The [continuation checklist](research/onboarding.md) maps the remaining capsule,
Docker and qualification work for a later authorized session.

The [source-anchored research and onboarding proposal](research/report.md)
covers identities, API boundaries, concurrency, marker filtering, path semantics,
memory risks, upstream reports and future qualification requirements. Daft adds
an analytics-facing directory/fallback discovery pipeline to the candidate set;
its value is distinct from adding another wrapper over the same library.

Varve maintains this study and builds Swath. The same evidence rules apply to
Daft and Swath. This source research is **not a run record**; a later runnable
capsule needs separately authorized implementation, execution and independent
review through the [onboarding workflow](../../docs/operating/tool-onboarding.md).
