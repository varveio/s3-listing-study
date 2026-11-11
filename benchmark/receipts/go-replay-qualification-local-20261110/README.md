# Go replay local endpoint compatibility receipt

Varve maintains this study and builds Swath. These are bounded local endpoint
probes, not performance measurements or a calibrated benchmark.

`attempts.jsonl` records nine successful scoped tools and one explicit ps3 skip.
Each scoped invocation exited zero and returned 2,922 objects, matching a
DuckDB count of the prefix in the unchanged release FourCast fixture. This
checks cardinality, not key-by-key equivalence. ps3 cannot scope this pilot to
its prefix and was not invoked. The retired s4cmd was outside the requested
roster.

The server was Go replay at the recorded immutable image digest, with no
latency injection. Subjects used the retained immutable study toolbox recorded
in each row. Its tool versions and artifact hashes match all current capsule
pins; the rebuilt cloud worker differs in harness code, not subject artifacts.
The pilot script and probe recorder are under
`benchmark/pilots/replay-endpoint-compat/`. Exact subject arguments, image,
fixture, machine, exit, wall and peak child RSS are in each completed record.
The local host has four CPUs; server quota is two CPUs. Wall and RSS describe
this probe only and must not be used for cross-tool performance comparisons.

A loopback HTTP proxy captured query parameters and response status. The runs
exercised `start-after`, `encoding-type=url`, and MinIO's `fetch-owner=true`.
No response contained an Owner element on this fixture, so Owner ordering is
unverified. This was a Go-only pilot; Java/Go wire equivalence and error-document
compatibility were not measured. Raw outputs and captures remain in the private
qualification report directory; completed records include stdout SHA-256.

`wrapper-failures.jsonl` preserves the initial nine attempts whose recorder
could not write to the scratch directory. Missing subject exit, wall, count and
RSS are null, not guessed. The directory permission was corrected before the
completed pilot. `<pilot-output>` replaces the private host scratch path in
those commands; arguments inside containers are unchanged. These are recorder
failures, not evidence of tool incompatibility.

Public projection (2026-11-11): the receipt files were first committed as
raw exports carrying the study's estate. Every JSON and JSONL file here is now
a projection of that export, checked with the release exporter's
forbidden-pattern list (`benchmark.public_export.FORBIDDEN_PATTERNS`) plus
cloud project, network and secret resource paths, the results bucket and Batch
job UIDs.
Dropped wherever they occur: service accounts, secret resources, project,
network, subnetwork and zone, Batch job names and UIDs (including the worker's
job argument), result-store prefixes and URIs, and the executor
environment apart from boot disk, provisioning and product retention.
Substituted in every string: the results bucket becomes `<result-store>/` or
`<fixture-store>/`, the study's registry path `<study-registry>/` (digests
kept). Everything else is verbatim, including each `result.json` SHA-256.
Where s7cmd ran, the only remaining pattern hit is the s7cmd container's
public subject working directory (`subject_workdir` in
`tools/s7cmd/build/image.json`), which the home-directory pattern matches. Case ids are
carried from the ledger and are not recomputable from the projected case
documents. The raw exports remain in this branch's earlier commits until the
squash merge.
