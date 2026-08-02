# Swath — mechanism

How Swath v0.2.0 lists a bucket: how it divides the keyspace, how it paginates
and parallelises, what it keeps in memory, what it emits, and how it fails.

**Tested subject.** Upstream's own published image for `v0.2.0`, whose
`org.opencontainers.image.revision` label equals the pinned source commit
`cef8ec24a74ffae14ee6a9462e4b7f6c334fbc32` (short `cef8ec2`). Canonical identity
is in [`../data/tool.json`](../data/tool.json); how that image was selected and
run is in [`running.md`](running.md).

**Evidence and reference notation, defined once for this capsule.** A reference
of the form claim `some-id` resolves in the canonical ledger,
[`../data/claims.json`](../data/claims.json), which owns that proposition's
evidence strength, its source, documentation, observation and run anchors, and
its qualification. This page does not repeat those anchors. Statuses use the
canonical vocabulary — `supported`, `unverified`, `unverifiable`, `confirmed` —
as defined in [`../../../docs/methodology.md`](../../../docs/methodology.md)
§ Evidence language. **No claim on this subject is `confirmed`**, because no run
of it produced a receipt; the reason is owned by
[`running.md`](running.md#no-receipts-the-runner-security-blocker). Narrative
that is not represented by a canonical claim is attributed inline to the
derivation in [`../research/v2-blind/report.md`](../research/v2-blind/report.md),
whose known anchor defects are listed in
[`../research/v2-blind/ERRATA.md`](../research/v2-blind/ERRATA.md).

Everything below is established from the pinned source and the project's own
documentation unless it names a run. Source can establish a mechanism; it cannot
establish that the mechanism behaved as designed at runtime.

## The engine: ranges, workers, thieves

`swath list` drives one engine. The keyspace is divided into half-open byte
ranges; each range is scanned by a virtual-thread worker, and a worker with
nothing claimable becomes a *thief* that subdivides a busy peer's range by
synthesizing a pivot key at runtime — claim `work-stealing-range-engine`. At
most one steal attempt is in flight across the whole fleet, and lock order is
strictly victim then gate, so termination cannot observe a false quiescence
between hand-off and count.

The code separates an executor layer (locks, clocks, RPC) from a pure policy
layer that decides by request-and-response over probe outcomes, across three
seams — `Thief`/`ThiefPolicy`, `OwnerSelfSplit`/`OwnerSplitGovernor`,
`SeedStep`/`HybridSeedPlanner` (report § 2). The policy classes are
deterministic state machines with no I/O, which is why they carry the
complexity they do.

Range placement is adaptive and density-aware: the seed frontier is mass-ordered
by default, pivot placement is driven by a trailing density EWMA, and the
default remaining-work estimator is rate-anchored — claim
`listing-is-adaptive-density-aware`. That the resulting placement is
density-proportional *in effect* at runtime is not something source settles.

Owner self-split runs inside the page-commit lock with zero extra API calls; its
gate chain includes a confetti-feedback gate that suppresses carving when
completed children come back as runts, letting every sixteenth carve through as
a probe (report § 2.3).

## The range model and why output is exactly-once by construction

A worker owning `(A, B]` lists with `start_after = A` and emits every returned
key `k` with `A < k <= B`. The boundary key belongs to the left interval. Ranges
are disjoint and cover the keyspace with no gap and no overlap — claim
`internal-tiling-is-disjoint` — through five distinct mechanisms: exact seed
tiling ending in a final open range, a single strict `k > hi` comparison, a
per-key bound re-read backed by an under-lock re-trim whose downstream emission
uses the trimmed batch, a three-clause durable compare-and-swap, and two split
loser paths of which only the late one restores the bound.

Because ranges are disjoint and seed pages never emit objects, output is
exactly-once by construction with no separate deduplication pass — claim
`no-dedup-pass-by-construction`. Every split is committed by that three-clause
compare-and-swap, whose `UPDATE` and child `INSERT` run in one
checkpoint-writer transaction, so a crash mid-split leaves both rows or neither
— claim `split-commit-is-atomic-cas`. The `cursor < pivot` clause is also the
durable backstop that turns a badly placed pivot into a balance problem rather
than a coverage problem.

These are design properties established from source. No run instruments the
internal range set, and exactly-once across a crash and resume remains
`unverified` — claim `exactly-once-under-crash`.

## Keyspace division: seeding and pivot placement

The default shallow seed runs a serial `delimiter=/` descent before any worker
starts, with a probe budget derived from the worker count (`targetSeeds =
min(1000, 4 × workers)`, `maxProbes = min(256, targetSeeds)`). Because the
frontier is polled one node at a time, raising `--concurrency` *lengthens* the
seed phase — claim `seed-descent-is-serial`.

Pivot placement is a multi-phase state machine, not a plain bisection. Its first
placement is a density-derived far-ahead fraction,
`clamp(0.5 + 0.25 × min(1, trailing density / average density), 0.5, 0.75)`, on
a bounded range; the plain byte midpoint is a step-back target re-probed only
when the upper half comes back empty, which is what makes far-ahead never worse
than the midpoint; bisection is a late phase — claim
`sampling-replaces-blind-midpoints`. Later phases add structure discovery (one
`delimiter=/` list at 32 keys), density reflection, and a flat-leaf fallback.
Every synthesized pivot is built over Unicode code points, so it is valid UTF-8
by construction.

## What changed between v0.1.0 and v0.2.0

The engine delta is two default flips: rate-anchored sensing moved off to on,
and the tail floor moved from `CURRENT` to `REACH_FLOORED` — claim
`v020-engine-default-flips`. Both are settled in the toggle record and its parse
fallbacks, not only in prose.

The old tail-floor reading was structurally blind rather than merely noisy: its
product is exactly zero for *any* estimate once the reach term is non-positive,
so an honest large estimate was multiplied away before the comparison. The new
reading floors reach at one sixteenth, so geometry shrinks a child's share
instead of erasing it. Rate-anchored sensing replaces an estimator that
degenerated to raw range width with one whose magnitude is the range's own
proven mass and whose geometry is measured in a window anchored at the cursor's
divergence. Each consult site additionally evaluates the old verdict and records
the divergence, so the tool ships instrumentation for its own default flip.

The documented pre-0.2.0 rollback is the pair `rate_anchored_sensing=off` with
`tail_floor=current`, and it is the only non-default engine configuration the
project supports — claim `engine-toggles-are-diagnostic`. The other thirteen
`--engine-toggle` values are declared experimental diagnostic surface, even
though several of them change the request pattern outright.

## Requests, pagination, and page size

One page fetch is exactly one `ListObjectsV2` call. Every request carries
`encoding-type=url`; prefix, `start_after`, delimiter, fetch-owner and
requester-pays are conditional. Four request shapes exist in tree: worker pages,
readahead guesses (off by default), `delimiter=/` structure probes from the seed
and from a thief, and single-key thief pivot probes (report § 2.1).

Pagination is purely `start_after` set to the last emitted key.
`ContinuationToken` is never sent, and the response's `NextContinuationToken` is
carried and unused — claim `pagination-uses-start-after`. Upstream's stated
reason is that range stealing needs an arbitrary sub-range lower bound, which an
opaque token cannot express; most of the rest of the engine design follows from
that one decision.

Page size is a hard-coded 1000 with no `--max-keys` flag, so it is not sweepable
without patching source — claim `page-size-fixed-no-max-keys`.

SDK-internal retry is disabled at `maxAttempts = 1`, so one increment of Swath's
API counter corresponds to one HTTP request and the concurrency gauge sees every
real 503 immediately — claim `sdk-internal-retry-disabled`. That is also what
makes the reported `cost.api_calls` a request count rather than a call-site
count: it has exactly one increment site, fires immediately before the SDK call,
and counts attempts issued across every request class — claim
`api-calls-counter-is-trustworthy`.

## Concurrency, AIMD, and flow control

`--concurrency N` is an AIMD **ceiling**, not a setpoint. A run starts at
`min(4, N)` permits; growth is `min(tMax, T × 2)` while slow-starting and
`min(tMax, T + 1)` afterwards; every decrease is `max(1, floor(factor × T))`.
The store, not the flag, sets the steady-state level — claim
`concurrency-flag-is-aimd-ceiling`. A benchmark that reads `--concurrency N` as
"N concurrent requests" will be wrong, and must instrument effective concurrency
separately.

The controller multiplicatively decreases the permit gauge on a 503 or
`SlowDown` at factor 0.7 and additively re-increases it across clean windows; a
separate sustained-timeout shed uses factor 0.5 at most once per jittered 25-to-40
second window, and growth is additionally gated by a hard worker-timeout freeze
and a latency-inflation freeze — claim `aimd-adapts-to-503`. Whether the
controller is dead weight when listing is latency-bound rather than
throttle-bound is `unverified` — claim `aimd-necessity`.

`--request-rate` is a zero-burst, capacity-one bucket refilling one token every
`round(1e9 / rps)` nanoseconds. It wraps the page fetcher *inside* the retry
loops, so every retry attempt also pays the rate-limit wait — claim
`request-rate-limiter-inside-retry-loops`.

`--object-listing-queue-size` is an in-flight **entry** budget rather than a slot
count, and admission tests the budget before adding a whole batch, so in-flight
entries can transiently exceed it by one S3 page; the backing queue is unbounded
and the weight gate is the only bound — claim `queue-size-is-entry-budget`.

## Errors, retries, and liveness

Because the liveness watchdog is armed by default, the production retry policy
is `RIDE_OUT`: over-cap transient faults retry indefinitely at a 15 second
backoff ceiling and the watchdog owns termination — claim
`retry-default-is-ride-out`. The two retry loops differ in a way that matters
under sustained throttling: on the engine path a voting throttle resets the
transient counter, so a permanently throttling endpoint can never trip the
eight-retry cap, while on the seed path every throttle counts toward it and is
never reset.

The watchdog carries two independent tripwires on separate clocks: a 120 second
total-freeze window fed by any progress signal including retry activity, and a
10 minute zero-real-progress window fed only by committed work — claim
`watchdog-two-tripwires`. Read from source, the operational consequence is that
a sustained-throttling run keeps re-arming the 120 second wire and ends at the
10 minute one instead. Escalation is cooperative cancel, then a forensic dump
plus interrupt after 10 seconds, then `Runtime.halt(75)` after a further 60.

Transient S3 error classes are individually classified and retried; only exact
status-and-code pairs get a typed fatal subtype, and any other 4xx falls through
to the generic fatal listing exception — claim `error-classification-is-specific`.
A wrong region is fatal rather than self-correcting: a 301 permanent redirect
becomes a typed error at exit 1 that is deliberately never retried, because the
fetcher's client is long-lived and shared across every worker and thief thread —
claim `wrong-region-is-fatal`.

Failures map onto seven exit codes — 0, 1, 2, 75, 124, and 130/143 — over a
sealed exception hierarchy, so the mapping is compiler-checked for
exhaustiveness — claim `exit-code-map`.

Three protocol-violation defences refuse a malformed page loudly: an oversized
page in the store, a truncated-but-empty page in the engine, and a stuck
continuation with no forward progress. Both engine guards run before the consumer
callback, so a broken page is never committed or emitted — claim
`protocol-violation-defences`. They cover quantity, pagination liveness and
synthesis safety only.

## Key fidelity and the encoding contract

Swath sets `encoding-type=url` on every request and performs no percent-decoding
of its own, relying entirely on the AWS SDK's response interceptor; inbound, the
already-decoded key is converted straight to bytes, and outbound a bound is
rendered byte-exactly because synthesized pivots are valid UTF-8 by construction
— claim `encoding-type-url-no-local-decode`. Against a conforming endpoint that
gives a byte-exact round trip for non-ASCII keys, literal percent sequences, and
control bytes.

Two conditional hazards sit under that guarantee, and neither was observed here.
A literal `+` survives only if the endpoint percent-encodes it, because the SDK
decodes through `URLDecoder`, which maps `+` to space; the tested LocalStack
build does percent-encode it, and that real AWS S3 does so too is assumed and
established by no in-tree test — claim `plus-to-space-conditional-hazard`. And
the SDK's decode interceptor is gated on a case-sensitive exact match of the
response's echoed `EncodingType`, which Swath never validates: an endpoint that
percent-encodes but omits or misspells the echo fails the gate, skips the decode,
and Swath emits the percent-encoded form as if it were the key — claim
`encoding-contract-not-validated`. That gating contradicts Swath's own
documentation, which states the decode is unconditional. The failure shape in
both cases is the dangerous one: a wrong answer with a clean exit and no
warning.

A third gap is structural rather than conditional. The scan loop assumes each
page arrives in ascending unsigned byte order and never checks it; the
forward-progress guard compares only the last key against the previous cursor,
so a page whose interior keys are unordered passes unexamined — claim
`no-intra-page-ordering-check`. Only a replay server can exercise it.

A key inserted behind a cursor the scan has already passed is missed, as with any
paginated lister; that half is `unverified` because source cannot settle
behaviour against a bucket that changes mid-listing — claim
`non-snapshot-pagination-misses-late-inserts`.

## Output and what a normalizer must do

Output is streaming rather than accumulate-then-dump: the output stage receives
one page batch at a time and writes each entry straight through the formatter,
holding no per-run collection; the only per-run state is a four-counter tally,
and the split tree lives in SQLite rather than on the heap — claim
`output-is-streaming`. The only writer of file descriptor 1 in the main source
tree is the output sink; logging, progress, the stats block and the
resolved-output echo all go to stderr, so a normalizer needs no stream-separation
logic — claim `stdout-is-clean`.

There is no shallow listing mode: `swath list` always fully enumerates objects,
and `delimiter=/` is used only internally for seed and thief probes. There is no
`--delimiter` and no `--recursive` flag — claim `no-shallow-listing-mode`.

Per format:

- **JSONL** is the only invertible text format, because its JSON escaping also
  escapes the backslash. It emits no header and omits nullable fields rather than
  emitting nulls, so an adapter must key on field names and never on position —
  claim `jsonl-escaping-is-invertible`.
- **TSV** always writes a header line and emits six fields with `last_modified`
  before `etag`, so a normalizer must drop the header and swap columns three and
  four — claim `tsv-header-and-field-order`.
- **table** writes size and time at fixed widths of fourteen and twenty-four with
  no etag and no storage class, and its padding helper appends without padding
  when the gap is not positive, so an adapter must assert the separator spaces
  and refuse otherwise — claim `aligned-fixed-column-timestamp-assumption`.
- **Parquet** writes the key column as raw `BINARY` rather than through a string
  decode, which makes it the only byte-exact key representation Swath produces —
  claim `parquet-key-column-is-byte-exact`.

All three text formats are lossy for keys outside plain UTF-8 printable bytes.
TSV and table escape control bytes as `\xHH` without escaping the backslash
itself, so a key literally containing the four characters `\x09` is
indistinguishable on the wire from a key containing a real tab; and all three
render the key through a UTF-8 string decode that turns invalid bytes into
U+FFFD irreversibly. An adapter must detect and refuse, never decode; escaping is
not bypassable, because `--raw-output` has no option binding — claim
`text-sink-key-fidelity-ascii-only`. Whether the native output preserves
control-character keys faithfully is `unverified` — claim
`control-char-key-fidelity-untested`.

Timestamps render through `ISO_INSTANT`, which emits zero, three, six or nine
fractional digits depending on the value, and a zero timestamp renders as an
empty string that collides with a real `1970-01-01T00:00:00Z`. Because S3 reports
second granularity the practical output is second-precision with an explicit `Z`,
but a sub-second-capable endpoint would emit fractional digits, so a normalizer
must strip them unconditionally — claim `timestamp-precision-is-variable`.

Filters are applied after listing and do not reduce API calls. Two consequences:
the include and exclude patterns are unanchored substring matches over the lossy
string decode, and a verification run must use no filters at all, because a
filtered run cannot be checked against a full-scope manifest — claim
`filters-are-post-listing`.

## State, checkpoints, and resume

The resume design uses a SQLite checkpoint whose `listing_node` table *is* the
worklist, written by a single checkpoint-writer thread; the schema is pinned at
`user_version 1` with exact-match-or-refuse and no migration path. The `cursor`
column is the last emitted key, giving at-most-once for text sinks, while the
durable cursor is the highest key inside a finalized part, giving exactly-once
for file sinks. The checkpoint is deleted on clean completion — claim
`checkpoint-resume-design-exists`.

Only a Parquet directory dataset is resumable. A stdout run or `--checkpoint
none` opens an in-process memory-backed SQLite store that writes nothing to disk,
and a single-file destination is refused outright — claim
`only-parquet-directory-is-resumable`. Ephemeral is not the absence of SQLite: it
is the same store and the same compare-and-swap machinery over an in-memory
database, so committed is not always durable.

Every resumable option carries a first-class resume classification — identity,
sticky or free — and a resume that changes an identity option is refused by name;
both bearer-token flags are deliberately free and never persisted — claim
`resume-identity-classification`. The `resume` subcommand itself exposes only the
directory, bearer-token options, colour, progress, stats, tune and verbosity
flags, so concurrency, queue size, request rate and the Parquet rotation knobs
cannot be varied across a resume boundary — claim `resume-cli-surface-is-restricted`.

Memory should be flat in object count for the text modes given the streaming
output stage and the bounded entry queue, but memory cliffs and OOM behaviour are
scale-dependent and stay `unverified` — claim `bounded-memory-at-scale`. The two
documented growth paths outside the invariant are Parquet part-metadata
re-serialization and `--sort` staging metadata.

## Absences, dead code, and documentation drift

Several things a caller or a benchmark would reach for are not there:

- No hinted seed mode. `--tune seed.mode=hints` is accepted by CLI validation and
  then throws an invalid-config error at seed time, after the checkpoint database
  is opened and the S3 client is built — claim `seed-hints-unimplemented`.
- No `inspect` or `diff` subcommand. The surface is `list`, `resume` and `help`,
  plus the hidden `dump-run` and `completion` — claim `inspect-diff-are-stubs`.
- No `--no-owner-split` flag; the owner-split kill switch is spelled
  `--engine-toggle owner_split=off`, and a dedicated test asserts the flag
  spelling is rejected — claim `no-owner-split-flag-absent`.
- No versioned listing. The `VERSIONS` mode exists in the model and the
  checkpoint schema admits it, but nothing can set it and the page fetcher throws
  for it, so the branch is dead code reachable only from a hand-crafted
  checkpoint database — claim `versions-listing-is-dead-code`.

Those absences sit beside a broader source-reliability finding: at v0.2.0 Swath's
prose documentation and in-source javadoc are not a reliable statement of the
shipped surface or the shipped defaults, while its reference tables, golden help
captures and code are — claim `docs-and-javadoc-drift`. Fifteen distinct drift
items were consolidated across four independent readers plus a cross-model
review. Three of them state engine defaults that are the opposite of what ships,
including javadoc still describing the pre-0.2.0 tail-floor and rate-anchored
defaults as current; one has a correctness consequence, the encoding-decode
contract above. In fairness, the flag and default tables in the configuration and
usage documents matched the golden help and the source field defaults on every
entry checked.

Two of the drift items are live runtime error messages that tell a user to pass
flags that do not exist — the unimplemented seed mode names `--seed` and
`--hints`, and the sort disk guard names `--force-sort`, printed immediately
before the guard halts the process — claim `live-error-messages-name-absent-flags`.
The real surfaces are `--tune seed.mode` and `--tune sort.ignore-disk-check`.
