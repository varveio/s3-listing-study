# s4cmd — mechanism

Source-anchored architecture of `s4cmd`'s listing engine, drawn from the
groundwork report ([`../research/report.md`](../research/report.md)) and its
independent cross-check ([`../research/codex-review.md`](../research/codex-review.md)).
Evidence labels are `[DOC]` the tool's own docs, `[SRC file:line @ 80059bf]`
pinned source, `[RUN receipt]` a committed run, `[OBS]` observed but not
wrapper-recorded, `[3P]` third-party, `[INFERRED]` a reasoned inference. Every
`[SRC]` anchor is the pinned checkout
**`80059bfa4451f513a8f314fb6300e5ecc51587b2`** (tag `2.1.0`); canonical
tested identity lives in [`../data/tool.json`](../data/tool.json). References of
the form claim `some-id` resolve in the canonical ledger,
[`../data/claims.json`](../data/claims.json). s4cmd is a single ~1,950-line
Python file, `s4cmd.py`.

Where a proposition rests on source reading it stays `supported`, not
`confirmed`: no listing mode could execute, because s4cmd has no anonymous path
and this campaign was `CREDS=none` (see [`running.md`](running.md)). Corrections
accepted during review are stated below as current truth; their history lives in
each claim's `disposition` and in [`../research/`](../research/).

## Listing is parallel — but by client-side delimiter recursion, not sharding

The inherited tool page modeled s4cmd as a "threadpool across distinct
CLI-supplied prefixes, serial per prefix." Source contradicts both halves:
parallelism is real, but its unit is the **pseudo-directory discovered by
delimiter recursion**, and a single prefix with `/`-substructure parallelizes
with no caller sharding at all — claim `parallelism-unit-is-delimiter-recursion`.

The driver is `S3Handler.s3walk` (`s4cmd.py:704`), which dispatches into the
thread-pool worker `ThreadUtil.s3walk` (`s4cmd.py:1167`)
[SRC `s4cmd.py:704,1167 @ 80059bf`]. Per node the worker issues:

```python
paginator = self.s3.get_paginator('list_objects')
for page in paginator.paginate(Bucket=..., Prefix=s3dir, Delimiter=PATH_SEP,
                               PaginationConfig={'PageSize': 1000}):
    for obj in page.get('CommonPrefixes') or []:  # subdirs -> recurse
        ... self.pool.s3walk(s3url, obj_name, filter_path, result)
    for obj in page.get('Contents') or []:        # files at this level
        ...
```
[SRC `s4cmd.py:1173-1206 @ 80059bf`]

Two consequences:

1. **`Delimiter='/'` is always sent, even in recursive mode** — claim
   `ls-always-sends-delimiter`. `ls -r` does not issue a flat delimiter-less
   scan; it walks the pseudo-directory tree. Each `CommonPrefix` it discovers is
   re-queued as a **new task on the thread pool** (`s4cmd.py:1185`), so a
   recursive listing performs **one paginated legacy `list_objects` (v1)
   traversal (a continuation chain) per pseudo-directory** in the subtree — claim
   `one-list-objects-v1-per-pseudo-directory` [SRC `s4cmd.py:1173,1176,1184-1185
   @ 80059bf`]. For a broad tree (the smoke bucket: 4 top prefixes → date-range
   dirs → `access/` leaves) that is **hundreds to thousands** of LIST requests,
   versus ~149 for a flat delimiter-less scan of the same 148,917 keys — it
   trades request *count* for parallelism (claim
   `request-amplification-on-deep-trees`) [INFERRED from SRC].
2. **Parallelism granularity = the tree's branching**, not a fixed shard count. A
   prefix holding a million keys under no sub-delimiter is **one serial paginated
   scan on one thread** (claim `flat-prefix-collapses-to-serial`); a wide/deep
   tree parallelizes across `num_threads` workers (claim
   `single-prefix-substructure-parallelizes`) [INFERRED from SRC
   `s4cmd.py:1184-1185`]. This is why the tool page's "serial within any single
   prefix" is false in general and true only for the degenerate
   delimiter-free-flat prefix.

`ls` accepts **exactly one** path argument (`args[1]`, guarded by
`validate('cmd|s3')`) [SRC `s4cmd.py:1625-1632 @ 80059bf`], so the tool page's
"multiple distinct prefixes on one invocation" mode is not a supported
invocation at all — s4cmd rejects it with `[Invalid Argument] Invalid number of
parameters`, exit 1 [OBS
`../receipts/smoke/_capability/obs-multiprefix.stderr.txt`] — claim
`ls-accepts-exactly-one-path`.

**Page size.** Fixed `PageSize=1000`, hardcoded in the `paginate` call; there is
no CLI flag to change it [SRC `s4cmd.py:1176 @ 80059bf`] (confirmed absent from
`--help` [RUN `../receipts/smoke/_build/build.md`]).

**Pagination / truncation.** Delegated entirely to boto3's paginator, which
follows `NextMarker`/`IsTruncated` internally; s4cmd consumes pages as an
iterator [SRC `s4cmd.py:1176 @ 80059bf`].

## Memory model — accumulate-then-sort-then-dump

Every matching object is appended to a shared `result` list (`conditional`,
`s4cmd.py:1208-1223`); after the whole walk joins, `pretty_print` sorts the
entire list (directories first, then by name) and only then prints
[SRC `s4cmd.py:722-741,1592 @ 80059bf`]. Nothing is streamed; peak memory scales
with the **total number of keys returned**, and output is sorted client-side so
it does **not** reflect S3's return order — claim `accumulate-then-sort-then-dump`.
The README states it plainly: *"Listing large number of files with S3
pagination, with memory is the limit."* [DOC README.md]. Whether this exhausts
memory at N keys is scale-dependent and unsettled at smoke scale — claim
`memory-ceiling-oom-unverified`.

## Retry / backoff / timeout

The pool worker retries **only** `S3RetryableErrors` = `socket.timeout`,
`ConnectionError`, `urllib3 ReadTimeoutError`, `botocore IncompleteReadError`
[SRC `s4cmd.py:271-276 @ 80059bf`], up to `--retry` (default **3**) times with
`--retry-delay` (default **10 s**) between, by re-queuing the task with an
incremented retry counter [SRC `s4cmd.py:529-539,1853,1856 @ 80059bf`]. Any other
exception — including credential errors and **HTTP 503 SlowDown**
(a `ClientError`, **not** in that tuple) — terminates the pool immediately
[SRC `s4cmd.py:520-542 @ 80059bf`] — claim `http-503-not-in-retryable-set`.
Throttling retries, if any, come only from botocore's own built-in retry layer,
not from s4cmd [INFERRED from SRC]; how that behaves under aggressive fan-out is
unsettled — claim `throttling-behavior-unverified`.

**Retry can duplicate keys.** The pool retries a failed task by re-queuing the
whole directory from page one (`s4cmd.py:538`), but objects already appended to
the shared `result` and child-directory tasks already queued from earlier pages
are **not** rolled back [SRC `s4cmd.py:529-539,1184-1185,1195-1206 @ 80059bf`]. A
retryable error mid-directory followed by a successful retry can therefore emit
duplicate keys and re-walk subtrees [INFERRED from SRC] — claim
`retry-can-duplicate-keys`. The verifier counts duplicates, so this would
surface as a `FAIL` at benchmark scale; it is a source-derived hypothesis, not
observed (claim `retry-duplication-run-unverified`).

## No unsigned path

There is **no** `--no-sign-request` equivalent, no config, no env. The boto3
client is constructed **without** `signature_version=UNSIGNED`
[SRC `s4cmd.py:380-386 @ 80059bf`], so a credential-less run cannot list — claim
`no-unsigned-request-support`. The **failure point depends on the credential
environment**: under the harness's web-identity neutralization the client cannot
be built and it fails at `BotoClient.__init__` before any S3 request (claim
`recursive-blocked-without-credentials`); in a plain bare environment the client
*does* construct and boto3 defers the credential error to the first
`list_objects` call in the s3walk worker thread (`NoCredentialsError` →
`[Thread Failure]`, claim `bare-env-fails-at-first-list-call`). Both paths
produce zero keys — see [`running.md`](running.md) for both receipts. Users asked
for unsigned access ~8 years ago and it was never added
[3P `github.com/bloomreach/s4cmd/issues/139`, opened 2018-10]. Scope: this is
s4cmd **2.1.0** at `80059bf`; the negative is pinned there.

## No resume

None for listing. A killed `ls` starts from scratch; there is no
marker/continuation persistence [INFERRED — no resume code in the listing path
@ 80059bf]. The README claims clean interruption without corrupt state for
*transfers* [DOC README.md]; for `ls` an interrupt simply loses all progress
[INFERRED].

## Observability — `S3APICALL` cannot count LIST pages

`--debug` raises **only** s4cmd's own logger (`s4cmd.py:102`), not botocore's
wire logging. Its `S3APICALL` lines expose only the **tool-wrapped** boto3 calls
(`get_paginator`, `head_object`, …) [SRC `s4cmd.py:393-408 @ 80059bf`]. For
listing, `get_paginator` is called **once per pseudo-directory** — the actual
per-page `list_objects` requests are issued by the returned paginator against the
raw boto3 client, **bypassing** the wrapper [SRC `s4cmd.py:102,393-408,1173-1176
@ 80059bf`]. So `grep S3APICALL` counts **pseudo-directories walked, not LIST
pages fetched** — claim `s3apicall-cannot-count-list-pages`; true per-page
counting is deferred to the study's replay-server phase.

Progress is otherwise a stderr heartbeat `[N task(s) completed, M remaining, K
thread(s)]` [SRC `s4cmd.py:605-613 @ 80059bf`]. There is no built-in listing
counter or throughput metric.

## Modes and tunables

A *mode* changes the request pattern or output contract; a *tunable* changes only
magnitude. All four listing modes ride the same `s3walk`/`list_objects` engine
above; they were **blocked, not skipped** at smoke (no anonymous path) — see
[`running.md`](running.md).

| Mode | Invocation | Request pattern / output contract | Evidence |
| --- | --- | --- | --- |
| recursive | `ls -r <url>` | Full subtree; delimiter walk with per-directory fan-out; one line per object | [SRC `s4cmd.py:1184 @ 80059bf`] |
| shallow (delimiter) | `ls <url>` | One level: immediate objects + subdirs shown as `DIR` | [SRC `s4cmd.py:1200,1184 @ 80059bf`] |
| show-directory | `ls -d <url>` | The directory entry itself instead of its contents | [SRC `s4cmd.py:704-710,1187 @ 80059bf`] [DOC README] |
| du | `du -r <url>` | Same recursive walk; output is aggregate size, not per-key | [SRC `s4cmd.py:1734 @ 80059bf`] [DOC README] |

Wildcards (`*`, `?`, multi-level) are a filter over the walk, not a distinct
request mode [SRC `s4cmd.py:1156-1164 @ 80059bf`] [DOC README].

| Tunable | Default | Effect | Evidence |
| --- | --- | --- | --- |
| `-c/--num-threads` | **`cpu_count * 4`** | Thread-pool size = max concurrent `list_objects` calls. Sweep this. | [SRC `s4cmd.py:121,1859 @ 80059bf`] |
| `S4CMD_NUM_THREADS` (env) | — | Same knob via env | [SRC `s4cmd.py:121 @ 80059bf`] |
| `-t/--retry` | 3 | Retry count for socket/timeout errors | [SRC `s4cmd.py:1853 @ 80059bf`] |
| `--retry-delay` | 10 | Seconds between retries | [SRC `s4cmd.py:1856 @ 80059bf`] |
| `--endpoint-url` | none | boto3 endpoint override (non-AWS stores) | [SRC `s4cmd.py:1867 @ 80059bf`] |
| `--last-modified-before/-after` | none | Client-side time filter over walk results | [SRC `s4cmd.py:1210-1221 @ 80059bf`] |
| PageSize | 1000 (hardcoded) | LIST page size — **not a flag** | [SRC `s4cmd.py:1176 @ 80059bf`] |

The `-c/--num-threads` flag is confirmed present in the built image's `--help`
(claim `num-threads-flag-present`), and its default `cpu_count*4` is **32** on
the 8-core runner — 4× this campaign's `CONCURRENCY_CAP=8`, so any real run must
pin `-c <= 8` (claim `num-threads-default-is-cpu-count-times-four`); the smoke
`run.sh` pins `-c 4` and rejects any override outside `1..8`. There is **no
region flag** — region is entirely boto3's to resolve (S3 default us-east-1 plus
bucket-region redirect) [SRC `s4cmd.py` — no region option @ 80059bf].

## Output contract per mode (`normalize.sh`)

`ls` output is `pretty_print` (`s4cmd.py:1592`): space-aligned columns
`<mtime> <size> <name>`, where `<name>` is the full `s3://bucket/key` URL,
`<size>` is bytes or the literal `DIR`, and `<mtime>` is
`TIMESTAMP_FORMAT = "%04d-%02d-%02d %02d:%02d"` — **minute precision, no seconds,
no zone marker** [SRC `s4cmd.py:55,1592-1622 @ 80059bf`]. There is **no**
JSON/CSV output [SRC `s4cmd.py:1592 @ 80059bf`].

| Mode | key | size | etag | mtime | storage_class |
| --- | --- | --- | --- | --- | --- |
| recursive | yes (from `s3://b/key`) | yes (bytes) | `-` | `-` | `-` |
| shallow | yes | yes / `DIR`→`-` | `-` | `-` | `-` |
| show-directory | yes | yes / `DIR`→`-` | `-` | `-` | `-` |
| du | aggregate size — no per-key output; normalize is a no-op | | | | |

`etag` and `storage_class` are **never** printed, so both are `-`. **`mtime` is
`-` deliberately:** `ls` exposes only minute precision, so the contract-v2
canonical `…:SSZ` value is not derivable — emitting a fabricated `:00` would risk
a misleading verifier result. The printed value is UTC only because botocore hands
`pretty_print` a tz-aware UTC datetime whose fields are formatted **as-is** —
there is **no** timezone conversion, and `TZ=UTC` does not affect this field
[SRC `s4cmd.py:1602 @ 80059bf`].

**Key-byte fidelity (tool-side, not adapter).** s4cmd `rstrip()`s each output
line (`s4cmd.py:1622`), so a key with **trailing whitespace** loses it before the
adapter ever sees it, and a key containing a **newline** is split across lines by
the line-oriented formatter [SRC `s4cmd.py:1622 @ 80059bf`] — claim
`key-byte-fidelity-tool-side-loss`. Such keys cannot be faithfully normalized — a
limit of the tool's output, not adapter fidelity. The adapter was exercised on
**synthetic fixtures** with checked-in expected outputs
([`../adapter/fixtures/`](../adapter/fixtures/)) — a construction check of the
parser, **not** a run against real tool output (claim
`adapter-fixtures-are-synthetic`). Full weird-key fidelity is deferred
(`EDGE_BUCKET=none`; claim `edge-key-fidelity-deferred`).

## Source anchors

- `s4cmd.py:704,1167` — `S3Handler.s3walk` driver → `ThreadUtil.s3walk` worker.
- `s4cmd.py:1173-1206,1176,1184-1185` — the delimiter-recursion loop; `list_objects`
  v1 paginator; `CommonPrefix` re-queued as a new pool task.
- `s4cmd.py:1625-1632` — `ls` takes exactly one path argument.
- `s4cmd.py:722-741,1208-1223,1592` — accumulate-then-sort-then-dump memory model.
- `s4cmd.py:271-276,520-542,529-539` — retryable-error set; pool-terminating
  errors; whole-directory requeue (the duplication surface).
- `s4cmd.py:380-386` — boto3 client built without `signature_version=UNSIGNED`.
- `s4cmd.py:102,393-408` — `--debug` raises only s4cmd's logger; `S3APICALL`
  wraps `get_paginator`, not per-page `list_objects`.
- `s4cmd.py:121,1859,1853,1856,1867` — tunables (`-c`, retry, retry-delay,
  endpoint-url).
- `s4cmd.py:55,1592-1622,1602` — `pretty_print` output format; minute-precision
  mtime; as-is UTC formatting.

## Deferred / open questions

The unverified propositions above — memory ceiling, delimiter-recursion scaling
and true LIST-page counts, throttling behavior, client-CPU cost, retry
duplication, and current-S3 v1 compatibility — are unresolved by smoke and out of
this consolidation's scope. All require **credentials + scale**: s4cmd cannot be
benchmarked anonymously. The full verbatim benchmark list is preserved in
[`../research/tool-page.md`](../research/tool-page.md) § "Open hypotheses for the
benchmark"; coverage is detailed in [`running.md`](running.md).
