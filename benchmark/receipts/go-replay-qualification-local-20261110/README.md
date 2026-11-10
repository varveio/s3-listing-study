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
