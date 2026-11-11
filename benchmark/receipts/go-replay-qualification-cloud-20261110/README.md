# Go replay qualification receipt

Varve maintains this study and builds Swath. This is diagnostic instrument
qualification, not a calibrated benchmark or tool ranking.

One parallel wave of 21 Spot jobs completed on 2026-11-10, using the three
release fixtures in `benchmark/plans/campaigns/go-replay-qualification/`.
Every subject exited zero and returned the fixture's expected row count. All
result markers bind to their recorded requests. There were no preemptions,
retries or cancellations, and no follow-up wave. Each group JSON is exported
through `benchmark.receipt`; it records requests, result URI/SHA-256, command,
immutable images, machine, fixture, exits, wall, row count and subject memory.
`metrics.jsonl` adds timing assessments and server headroom derived from those
same bound markers; CPU p90 is nearest rank over 10-second samples, server RSS
is lifetime VmHWM, and active request/response maxima are sampled, not true peaks.

The unchanged timing gate returns 10 TIMING_VALID, 6 CAPACITY_FAILED and
5 PRESSURE_DEGRADED attempts. Go passes both Swath NARA and both s7cmd NARA
repetitions and all three serial s5cmd FourCast repetitions. All five Go
blockchain runs (64/20 twice, 32/10, 16/5, 8/4 once each) are PRESSURE_DEGRADED
because structure-probe overruns exceed the validity threshold. No blockchain
allocation therefore qualifies for downsizing. All ladder runs used
n4-highcpu-80; no smaller VM was measured. NARA and FourCast were qualified
only at their tested release allocations, without an allocation ladder.
The s5cmd median wall difference is −1.57%, inside the predeclared ±10% band.
These results do not qualify a blanket instrument switch.

The s7cmd Java side uses the explicitly authorized retained release image
`<study-registry>/swath-replay@sha256:f51c4d12ac0e1f52fd02f1a77a74e962d610d518e64ab8425635512370973612`.
Its original image
`ghcr.io/varveio/swath-replay@sha256:8fb53a327f946f10db17d4fd082a7b2e06bc678a1e828a197a1ef5717473a46a`
returned manifest unknown and was absent from the project registry. This pair
grades server-side timing on that cell, not reproduction of its exact release
server. All other Java server digests are the source row's exact image.

All pairs share worker digest
`sha256:7e6534d514f1a59318ffc397997c44cef87c0e1aa76d5b5ab32d386fa8ec95e2`,
built at `35f1d9fde4c656dea6aff1fee9370a56eb9f99dc`. Go replay is digest
`sha256:38ea98e22d01d421f5965cf87a3bd4f483d713b6f8d543999247b83dd38eafed`,
from Swath main `a93035d26dd0c25ceed434a97143f15f8f43c6e1`. The listing subject
is Swath Java 0.3.2, never Swath-Go. Declared warm-up is unchanged within pairs.

`cost-observed.json` applies the published us-east1 N4 Spot hourly rates
($1.80688 / highcpu-80, $0.722752 / highcpu-32, $0.361376 / highcpu-16) to
observed provider durations. The runDuration proxy is $1.7190; conservatively
charging API creation through terminal update gives $2.0457 compute. Adding
the full $1 non-compute planning reserve gives $3.0457, below the $10 cap.
These are execution-based estimates, not a reconciled invoice. The pre-submit
estimate was $4.2055 compute, $9.4111 including preemption and other reserves.
Rates: [Google Spot VM pricing](https://cloud.google.com/spot-vms/pricing),
us-east1 table retrieved 2026-11-10. Raw worker evidence remains at the immutable
result paths referenced in each receipt; the diagnostic release was not edited.

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
