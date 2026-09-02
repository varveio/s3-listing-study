# Swath — mechanism

How Swath v0.3.1 lists a bucket: how it divides the keyspace, how it paginates
and parallelises, what it keeps in memory, what it emits, and how it fails.

**Tested subject.** Upstream's own published image for `v0.3.1`, whose
`org.opencontainers.image.revision` label equals the pinned source commit
`7b9a5e2fba045c67165c76511f8c40c880406a8a` (short `7b9a5e2`). Canonical identity
is in [`../data/tool.json`](../data/tool.json); how that image was selected and
run is in [`running.md`](running.md). The capsule first described v0.2.0
(`cef8ec2`); every claim below was re-tested against the v0.3.1 tree, and the
per-claim record of what held, moved or changed is
[`../research/v0.3.1/report.md`](../research/v0.3.1/report.md). Where a
behaviour is new since v0.2.0 the text says so, because the study's own
runtime observations of the engine are still the v0.2.0 ones.

**Evidence and reference notation, defined once for this capsule.** A reference
of the form claim `some-id` resolves in the canonical ledger,
[`../data/claims.json`](../data/claims.json), which owns that proposition's
evidence strength, its source, documentation, observation and run anchors, and
its qualification. This page does not repeat those anchors. Statuses use the
canonical vocabulary — `supported`, `unverified`, `unverifiable`, `confirmed` —
as defined in [`../../../docs/methodology.md`](../../../docs/methodology.md)
§ Evidence language. **No claim on this subject is `confirmed`**: later
diagnostic attempt receipts carry no verifier verdict or claim-confirming
evidence. The boundary is owned by
[`running.md`](running.md#diagnostic-attempt-receipts-but-no-verifier-verdict). Narrative
that is not represented by a canonical claim is attributed inline to the
v0.2.0 derivation in [`../research/report.md`](../research/report.md),
whose known anchor defects are listed in
[`../research/ERRATA.md`](../research/ERRATA.md); the readers of the v0.3.1
update found the stealing and seeding core that narrative describes —
`Worklist`, `Thief`, `ThiefPolicy`, `HybridSeedPlanner`, `SeedStep`,
`RateAnchoredEstimator` — byte-identical between the two tags, while the
retry, gauge, scanner and command files changed in the ways this page names.

Everything below is established from the pinned source and the project's own
documentation unless it names a run. Source can establish a mechanism; it cannot
establish that the mechanism behaved as designed at runtime.

## The engine: ranges, workers, thieves

`swath list` drives one engine. The keyspace is divided into half-open byte
ranges; each range is scanned by a virtual-thread worker, and a worker with
nothing claimable becomes a *thief* that subdivides a busy peer's range by
synthesizing a pivot key at runtime — claim `work-stealing-range-engine`, whose
qualification carries the two fleet-wide invariants: at most one steal attempt
is in flight at a time, and lock order is strictly victim then gate. Termination
depends on a third: a split child is counted while the thief still holds the
victim's lock, so quiescence cannot be observed falsely between hand-off and
count — claim `termination-cannot-see-false-quiescence`. All three are read from
source; no run instruments the worklist.

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

## The range model and why no deduplication pass exists

A worker owning `(A, B]` lists with `start_after = A` and emits every returned
key `k` with `A < k <= B`. The boundary key belongs to the left interval. The
engine is *designed* to keep ranges disjoint and covering the keyspace with no
gap and no overlap — claim
`internal-tiling-is-disjoint` — through five distinct mechanisms: exact seed
tiling ending in a final open range, a single strict `k > hi` comparison, a
per-key bound re-read backed by an under-lock re-trim whose downstream emission
uses the trimmed batch, a three-clause durable compare-and-swap, and two split
loser paths of which only the late one restores the bound.

Because those ranges are *designed* disjoint and seed pages emit no objects at
all, no separate deduplication pass exists anywhere in the tree: the design is
meant to make one unnecessary rather than the code removing duplicates after the
fact — claim `no-dedup-pass-by-construction`. Every split is committed by that
three-clause compare-and-swap, whose `UPDATE` and child `INSERT` are issued
inside one checkpoint-writer transaction — claim `split-commit-is-atomic-cas`.
The `cursor < pivot` clause is also the durable backstop that turns a badly
placed pivot into a balance problem rather than a coverage problem.

These are design properties read from source, not runtime guarantees
established here. What source settles is the absence of a deduplication pass and
the disjointness design that is meant to make one unnecessary; it does not
settle that any run emitted every key exactly once, on a clean run or any other.
No run instruments the internal range set; no fault-injection run terminated the
process mid-split, so both-rows-or-neither is the intended consequence of the
single transaction rather than an observed one; and exactly-once across a crash
and resume remains `unverified` — claim `exactly-once-under-crash`.

## Keyspace division: seeding and pivot placement

The default shallow seed runs a serial `delimiter=/` descent before any worker
starts, with a probe budget derived from the worker count (`targetSeeds =
min(1000, 4 × workers)`, `maxProbes = min(256, targetSeeds)`). Because the
frontier is polled one node at a time, raising `--concurrency` raises the seed
probe budget — claim `seed-descent-is-serial`. Upstream states that this
lengthens the seed phase; the frontier can exhaust before the budget is spent,
and no run here measured seed duration against concurrency.

Pivot placement is a multi-phase state machine, not a plain bisection. Its first
placement is a density-derived far-ahead fraction,
`clamp(0.5 + 0.25 × min(1, trailing density / average density), 0.5, 0.75)`, on
a bounded range; the plain byte midpoint is a step-back target re-probed only
when the upper half comes back empty, which bounds that case but leaves a
non-empty yet very sparse upper half committed at the far-ahead pivot;
bisection is a late phase — claim
`pivot-placement-is-multi-phase`. Later phases add structure discovery (one
`delimiter=/` list at 32 keys), density reflection, and a flat-leaf fallback.
Every synthesized pivot is built over Unicode code points, so it is valid UTF-8
by construction.

## Engine defaults and the one supported rollback

Two engine defaults flipped at v0.2.0: rate-anchored sensing moved off to on,
and the tail floor moved from `CURRENT` to `REACH_FLOORED` — claim
`v020-engine-default-flips`. Both are settled in the toggle record and its parse
fallbacks, not only in prose.

The pre-0.2.0 tail-floor reading was structurally blind rather than merely
noisy: its product is exactly zero for *any* estimate once the reach term is
non-positive, so an honest large estimate was multiplied away before the
comparison. The shipped reading floors reach at one sixteenth, so geometry
shrinks a child's share instead of erasing it. Rate-anchored sensing replaces an
estimator that degenerated to raw range width with one whose magnitude is the
range's own proven mass and whose geometry is measured in a window anchored at
the cursor's divergence. Each consult site additionally evaluates the older
verdict and records the divergence, so the tool ships instrumentation for its
own default flip.

The documented pre-0.2.0 rollback is the pair `rate_anchored_sensing=off` with
`tail_floor=current`, and it is the only non-default engine configuration the
project supports — claim `engine-toggles-are-diagnostic`. The other twelve
`--engine-toggle` values are declared experimental diagnostic surface, even
though several of them change the request pattern outright. Since 0.3.0 the
flag is hidden from `swath list --help` and shell completion while still
parsing with the same fourteen names; its documentation moved to the
diagnostic section of upstream's configuration page.

## Requests, pagination, and page size

One page fetch is exactly one `ListObjectsV2` call. Every request carries
`encoding-type=url`; prefix, `start_after`, delimiter, fetch-owner and
requester-pays are conditional. Four request shapes exist in tree: worker pages,
readahead guesses (off by default), `delimiter=/` structure probes from the seed
and from a thief, and single-key thief pivot probes (report § 2.1). Since 0.2.4
every request also carries a `swath/<version>` prefix in its HTTP User-Agent,
ahead of the SDK's own markers, so the tool is attributable in server access
logs — claim `user-agent-identifies-swath`.

Since 0.3.0 the *response* side is Swath's own: a Swath-owned execution
interceptor streams each successful `ListObjectsV2` body through a StAX parser,
builds the page directly from the wire XML, and hands the SDK an empty result,
so the SDK's element tree and response model are bypassed on the production
path; the SDK still owns marshalling, HTTP, authentication, timeouts and error
bodies — claim `listobjects-response-streamed-by-swath-interceptor`. The
consequences for key decoding are in the key-fidelity section below.

Pagination is purely `start_after` set to the last emitted key.
`ContinuationToken` is never sent, and the response's `NextContinuationToken` is
carried and unused — claim `pagination-uses-start-after`. Upstream's stated
reason is that range stealing needs an arbitrary sub-range lower bound, which an
opaque token cannot express; most of the rest of the engine design follows from
that one decision.

Page size is a hard-coded 1000 with no `--max-keys` flag, so it is not sweepable
without patching source — claim `page-size-fixed-no-max-keys`.

SDK-internal retry is disabled at `maxAttempts = 1`, so one increment of Swath's
API counter corresponds to one SDK list-call attempt — not to a call site that
retries internally, and not to a wire request either, since the increment fires
before the request is built and so counts an attempt that dies in DNS or connect
setup — and the concurrency gauge sees every real 503 immediately — claim
`sdk-internal-retry-disabled`. That is also what
makes the reported `cost.api_calls` a request count rather than a call-site
count: it has exactly one increment site, fires immediately before the SDK call,
and counts attempts issued across every request class — claim
`api-calls-counter-is-trustworthy`.

## Concurrency, AIMD, and flow control

`--concurrency N` is an AIMD **ceiling**, not a setpoint. A run starts at
`min(4, N)` permits; growth is `min(tMax, T × 2)` while slow-starting and
`min(tMax, T + 1)` afterwards; every decrease is `max(1, floor(factor × T))`.
The store, not the flag, sets the steady-state level — claim
`concurrency-flag-is-aimd-ceiling`. Upstream now says the same thing in the
flag's own help text ("AIMD ceiling for concurrent listing requests (default:
64)") and documents that the controller is reactive backpressure only: a 503 or
timeout storm lowers `T` and latency inflation holds growth, but nothing ever
searches `T` back down for efficiency, so a ceiling that was never hit is not a
capacity finding — claim `aimd-does-not-search-down`. A benchmark that reads
`--concurrency N` as "N concurrent requests" will be wrong, and must instrument
effective concurrency separately.

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
never reset. Since 0.3.0 the seed path has one fail-fast exception: a seed probe
whose fault chain holds a connection refusal or an unknown host is fatal on the
first attempt, recorded as `seed_endpoint_unreachable`, instead of consuming the
retry budget; worker and thief fetches keep retrying network faults as
transient — claim `seed-endpoint-unreachable-fails-fast`.

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

Failures map onto eight exit codes — 0, 1, 2, 74, 75, 124, and 130/143 — over
a sealed exception hierarchy, so the mapping is compiler-checked for
exhaustiveness; 74 (`EX_IOERR`, a full output filesystem, added in 0.2.2) is
derived from the cause chain rather than being a new exception type — claim
`exit-code-map`.

Four protocol-violation defences refuse a malformed page loudly: an oversized
page and an unparseable or wrongly-rooted success body in the store, and a
truncated-but-empty page and a stuck cursor with no forward progress in the
engine. Both engine guards run before the consumer callback, so a broken page is
never committed or emitted — claim `protocol-violation-defences`. Since 0.3.0
the forward-progress guard also covers the terminal, non-truncated page: a last
page that repeats or regresses the cursor is now fatal where v0.2.0 accepted it,
which a replay server must honour — claim
`forward-progress-guard-covers-terminal-page`. The defences cover quantity,
parseability, pagination liveness and synthesis safety only.

## Key fidelity and the encoding contract

Swath sets `encoding-type=url` on every request. At v0.2.0 it performed no
percent-decoding of its own and relied entirely on the AWS SDK's response
interceptor; since 0.3.0 that is reversed, and the claim is recorded as
contradicted under its original ID: Swath's own streaming interceptor
percent-decodes each `Key` and `CommonPrefixes/Prefix` with the SDK's
`URLDecoder`-backed utility, and the SDK's decode path survives only as a
metered fallback for pages the interceptor did not populate — claim
`encoding-type-url-no-local-decode`. Inbound, the decoded string is converted
straight to bytes with a plain UTF-8 encode, and outbound a bound is rendered
byte-exactly because synthesized pivots are valid UTF-8 by construction. The
decode call is the same one the SDK used, so against a conforming endpoint the
byte-exact round trip for non-ASCII keys, literal percent sequences and control
bytes is preserved; what moved is where the hazards live, from a pinned jar
into Swath's main source tree.

Two conditional hazards sit under that guarantee, and neither was observed here.
A literal `+` survives only if the endpoint percent-encodes it, because the
decode goes through `URLDecoder`, which maps `+` to space; the tested LocalStack
build does percent-encode it, and that real AWS S3 does so too is assumed and
established by no in-tree test — claim `plus-to-space-conditional-hazard`. And
the decode is gated on a case-sensitive exact match of the response's echoed
`EncodingType`, which Swath never validates: an endpoint that percent-encodes
but omits or misspells the echo fails the gate, skips the decode, and Swath
emits the percent-encoded form as if it were the key — claim
`encoding-contract-not-validated`. At v0.2.0 that gate was established by
disassembling the SDK; at 0.3.1 it is visible in Swath's own interceptor, and
upstream's compatibility note still describes the decode as unconditional. The
failure shape in both cases is the dangerous one: a wrong answer with a clean
exit and no warning.

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
`output-is-streaming`. During a listing run the only writer of file descriptor
1 in the main source tree is the output sink; logging, progress, the stats block
and the resolved-output echo all go to stderr, so a normalizer needs no
stream-separation logic — with two 0.3.0 caveats: `--compression gzip|zstd`
makes the stdout bytes a compressed stream, and `--format discard` writes no
rows at all — claim `stdout-is-clean`. The discard sink runs the whole listing
pipeline with the material output path removed, so a run report from it
isolates listing cost from output cost — claim
`discard-sink-measures-listing-engine`.

Text output can also be a partitioned directory dataset since 0.3.0: a TSV or
JSONL directory destination is written by a bounded pool of writers
(`--text-writers`, 2 to 64, default 3), each TSV part opening with its own
header, with `manifest.json` written once at completion and `_SUCCESS` last —
claims `partitioned-text-datasets`, `manifests-published-only-at-completion`.
Table, TSV and JSONL streams, files and parts can be gzip- or zstd-compressed —
claim `text-compression-flag`.

There is no shallow listing mode: `swath list` always fully enumerates objects,
and `delimiter=/` is used only internally for seed and thief probes. There is no
`--delimiter` and no `--recursive` flag — claim `no-shallow-listing-mode`.

Per format:

- **JSONL** is the only text format whose escaping is invertible, because its
  JSON escaping also escapes the backslash — and only for keys that are valid
  UTF-8, since invalid bytes become U+FFFD before the formatter sees them. It
  emits no header and omits nullable fields rather than emitting nulls, so an
  adapter must key on field names and never on position —
  claim `jsonl-escaping-is-invertible`.
- **TSV** always writes a header line and emits six fields with `last_modified`
  before `etag`, so a normalizer must drop the header and swap columns three and
  four — claim `tsv-header-and-field-order`.
- **table** writes size and time at fixed widths of fourteen and twenty-four with
  no etag and no storage class, and its padding helper appends without padding
  when the gap is not positive, so an adapter must assert the separator spaces
  and refuse otherwise — claim `aligned-fixed-column-timestamp-assumption`.
- **Parquet** stores the key's raw bytes in physical `BINARY` with no string
  decode, so it is byte-exact for every key it accepts; since 0.3.0 the column
  is annotated `STRING`, DuckDB reads it as `VARCHAR`, and the writer refuses a
  key that is not well-formed UTF-8 with a typed error, so a Swath Parquet
  dataset can only ever hold valid UTF-8 keys — claims
  `parquet-key-column-is-byte-exact`, `parquet-key-is-string-annotated-utf8-only`.
  Sorted parts are numbered from `part-00000.parquet` while the footer's
  `file_index` stays one-based — claim
  `sorted-parts-zero-based-file-index-one-based`.

None of the three text formats round-trips every key, and printable ASCII is not
a safe domain. TSV and table escape control bytes as `\xHH` without escaping the
backslash itself, so a key literally containing the four characters `\x09` — all
printable ASCII — is indistinguishable on the wire from a key containing a real
tab; and all three render the key through a UTF-8 string decode that turns
invalid bytes into U+FFFD irreversibly. Only a key with no control byte, no
literal `\xHH` sequence, and valid UTF-8 survives unambiguously.
An adapter must detect and refuse, never decode; escaping is
not bypassable, because `--raw-output` has no option binding — claim
`text-sink-key-encoding-is-lossy`. Whether the native output preserves
control-character keys faithfully is `unverified` — claim
`control-char-key-fidelity-untested`.

Timestamps are the endpoint's own spelling since 0.3.0: the text sinks pass the
`LastModified` element text through verbatim, so a live S3 row reads
`YYYY-MM-DDTHH:MM:SS.000Z` on every row, where v0.2.0 rendered `ISO_INSTANT`
and S3's whole seconds came out without a fraction; only an entry rebuilt from
epoch micros (sort staging, fixtures, the SDK fallback path) is rendered by
Swath with zero, three or six fractional digits, and a zero timestamp still
renders as an empty string — claims `timestamp-precision-is-variable`,
`last-modified-text-is-endpoint-spelling`. A normalizer must strip the fraction
unconditionally; the study's does, and its aligned-table query had to be fixed
to do so when the 0.3.1 round-trip first normalized that mode to zero rows.

Filters are applied after listing and do not reduce API calls. Two consequences:
the include and exclude patterns are unanchored substring matches over the lossy
string decode, and a verification run must use no filters at all, because a
filtered run cannot be checked against a full-scope manifest — claim
`filters-are-post-listing`.

## State, checkpoints, and resume

The resume design uses a SQLite checkpoint whose `listing_node` table *is* the
worklist, written by a single checkpoint-writer thread; the schema is pinned at
`user_version 1` with exact-match-or-refuse and no migration path. The `cursor`
column is the last emitted key; for a file sink the durable cursor is instead
the highest key inside a finalized part. Those two definitions are the design's
intended basis for at-most-once text output and exactly-once file output, and
they are design intent only: nothing here establishes that part creation,
finalization, metadata persistence, cursor update and checkpoint deletion are
atomically coordinated under a crash, and the guarantee itself stays
`unverified` — claims `checkpoint-resume-design-exists`,
`exactly-once-under-crash`. The checkpoint is deleted on clean completion.

Only a managed Parquet directory dataset is resumable. A stdout run, a discard
run or `--checkpoint none` opens an in-process memory-backed SQLite store that
writes nothing to disk; a single-file destination is refused outright, and a
partitioned TSV or JSONL dataset must be created with `--checkpoint none` and is
refused at resume — claims `only-parquet-directory-is-resumable`,
`text-datasets-require-checkpoint-none`. Ephemeral is not the absence of SQLite:
it is the same store and the same compare-and-swap machinery over an in-memory
database, so committed is not always durable. Sorted staging is not portable
across versions: a `--sort` run interrupted under 0.2.x is refused by 0.3.x
before any staging file is touched and told to `--restart` — claim
`sorted-staging-version-refusal`. The sorted merge's encoder count is
`--tune sort.merge-parallelism` (1 to 16), whose default is core-derived, so
the `4` the published image prints under `--tune help` is not a constant —
claim `sort-merge-parallelism-tune`.

Every resumable option carries a first-class resume classification — identity,
sticky or free — and a resume that changes an identity option is refused by name;
both bearer-token flags are deliberately free and never persisted — claim
`resume-identity-classification`. The `resume` subcommand itself exposes only the
directory, bearer-token options, colour, progress, stats, tune and verbosity
flags, so concurrency, queue size, request rate and the Parquet rotation knobs
cannot be varied across a resume boundary — claim `resume-cli-surface-is-restricted`.

Memory should be flat in object count for the text modes given the streaming
output stage and the bounded entry queue, but memory cliffs and OOM behaviour are
scale-dependent and stay `unverified` — claim `bounded-memory-at-scale`. At
v0.3.1 the growth paths outside the invariant are the bounded dataset writer
pool, whose queue is capped and whose writers above four are admitted against
heap, and the sorted finalization pipeline, whose readers and encoders are
admitted against heap and file-descriptor budgets — claims
`parquet-writers-range-widened`, `sort-merge-parallelism-tune`.

## Absences, dead code, and documentation drift

Several things a caller or a benchmark would reach for are not there:

- No hinted seed mode. `--tune seed.mode=hints` is accepted by CLI validation and
  then throws an invalid-config error at seed time, after the checkpoint database
  is opened and the S3 client is built — claim `seed-hints-unimplemented`.
- No `inspect` or `diff` subcommand. The surface is `list`, `resume` and `help`,
  plus the hidden `dump-run` and `completion` — claim `no-inspect-or-diff-subcommand`.
- No `--no-owner-split` flag; the owner-split kill switch is spelled
  `--engine-toggle owner_split=off`, behind an option that is itself hidden
  since 0.3.0, and a dedicated test asserts the flag spelling is rejected —
  claim `no-owner-split-flag-absent`.
- No versioned listing. The `VERSIONS` mode exists in the model and the
  checkpoint schema admits it, but nothing can set it and the page fetcher throws
  for it, so the branch is dead code reachable only from a hand-crafted
  checkpoint database — claim `versions-listing-is-dead-code`.

Those absences sit beside a broader source-reliability finding: at v0.3.1, as
at v0.2.0, Swath's in-source javadoc and older prose are not a reliable
statement of the shipped surface or the shipped defaults, while its reference
tables, golden help captures, the code and — new in 0.3.0 — a supported-surface
page whose completeness against the visible option set is enforced by a golden
test are — claims `docs-and-javadoc-drift`, `supported-cli-surface-page-is-tested`.
Fifteen distinct drift items were consolidated at v0.2.0 across four
independent readers plus a cross-model review. Of those re-checked at 0.3.1,
the three that state engine defaults backwards persist verbatim, as do both
live error messages below; one item left the usage prose but survives in the
roadmap and javadoc, and one was reworded rather than retracted. The encoding
item has a correctness consequence and is discussed above. In fairness, the
flag and default tables matched the golden help and the source field defaults
on every entry checked at both versions.

Two of the drift items are live runtime error messages that tell a user to pass
flags that do not exist — the unimplemented seed mode names `--seed` and
`--hints`, and the sorted-staging disk guard names `--force-sort`, printed
immediately before the guard halts the process — claim
`live-error-messages-name-absent-flags`. Both persist verbatim at 0.3.1. The
real surfaces are `--tune seed.mode` and `--tune sort.ignore-disk-check`.
