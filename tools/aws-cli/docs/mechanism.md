# aws-cli — mechanism

Architecture and behavior, as established by reading the pinned source
(`12d962d239b9fd0669951c4d27dc366388abba2d`, tag `2.36.1`) and by the smoke
receipts under [`receipts/smoke/`](../receipts/smoke/). This page carries the
source anchors and evidence labels from the source-first groundwork; the full
derivation (with the sources it was checked against) is
[`research/report.md`](../research/report.md), and the claim-by-claim check
against the inherited tool page is
[`research/reconciliation.md`](../research/reconciliation.md).

Labels: `[DOC]` official docs · `[SRC file:line @sha]` pinned checkout ·
`[RUN receipt]` a committed smoke run · `[OBS how]` observed in a run the
wrapper itself couldn't record (e.g. `--debug`) · `[INFERRED]` reasoning from
the above, not itself run.

Canonical claim IDs referenced below as claim `some-id` resolve in
[`../data/claims.json`](../data/claims.json), which carries each proposition's
current evidence status (`confirmed`, `supported`, `unverified`, or
`unverifiable`) and its derivation. This page uses those IDs rather than
review-round labels; review history lives in [`../research/`](../research/).

## Two S3 surfaces

aws-cli exposes S3 listing through two independently-implemented commands
that share nothing but the underlying botocore paginator:

- **`s3api`** — a thin, model-generated wrapper over the S3 API.
  `list-objects-v2`, `list-objects`, and `list-object-versions` are generated
  from the botocore service model, not hand-written: `_create_command_table`
  iterates `service_model.operation_names` and builds one command each
  `[SRC awscli/clidriver.py:744-748]` — there is no per-operation Python file.
- **`s3 ls`** — a hand-written high-level command, `class ListCommand`
  `[SRC awscli/customizations/s3/subcommands.py:782]`.

## Request patterns

Listing is serial pagination in **both** surfaces:

- `s3 ls` obtains a botocore paginator and consumes it in a plain `for` loop:
  `paginator = self.client.get_paginator('list_objects_v2')` then
  `for response_data in iterator: self._display_page(...)`
  `[SRC subcommands.py:852,865]`. Recursive listing uses a second paginator
  the same way `[SRC subcommands.py:917]`. No threads are spawned around it.
- `s3api` issues one paginated call: `if client.can_paginate(...) ...
  paginator = client.get_paginator(...); response = paginator.paginate(**parameters)`
  `[SRC awscli/clidriver.py:1105-1107]` — a lazy iterator consumed serially by
  the formatter.
- The only parallelism aws-cli has anywhere is in **transfers** (`s3 cp`/`sync`,
  via s3transfer / the optional CRT client `[SRC awscli/customizations/s3/factory.py]`)
  — never in listing. "Parallel S3 listing" with aws-cli means manually
  fanning out N separate CLI invocations over disjoint prefixes; the caller
  owns the prefix segmentation and concurrency
  `[3P https://blog.rasc.ch/2025/07/s3-fast-list.html][INFERRED]`. Smoked as
  the `fanout` mode — see [`running.md`](running.md#fan-out-union-procedure).

**API / request shape.** `s3 ls` always calls `ListObjectsV2`
`[SRC subcommands.py:852,917]`. `s3api` calls whichever operation you named
(`list-objects-v2` = ListObjectsV2, `list-objects` = the legacy V1
ListObjects, `list-object-versions` = ListObjectVersions). Observed URL:
`GET …/?list-type=2&prefix=…&encoding-type=url`, virtual-hosted style
`[OBS --debug]`.

**Keyspace division.** None built in. Non-recursive `s3 ls` sets
`Delimiter='/'` so S3 rolls sub-trees into `CommonPrefixes`
`[SRC subcommands.py:853-857]`; recursive **omits** the delimiter and returns
every key flat `[SRC subcommands.py:917-921]`. There is no bisection,
cut-point, or automatic prefix segmentation anywhere in aws-cli.

**Page size.** `--page-size` maps to `PaginationConfig.PageSize`
`[SRC subcommands.py:857; paginate.py:182-193]`, sent to the server as
`MaxKeys`. The S3 server cap is 1,000 keys/page; `--page-size` is an upper
bound, not a guarantee `[DOC ListObjectsV2 API]`.

**Retries / backoff.** Delegated to vendored botocore's standard retry mode;
default `max_attempts=3`, overridable via `AWS_MAX_ATTEMPTS` / config
`max_attempts` `[SRC awscli/botocore/configprovider.py:153]`. aws-cli itself
adds no `--max-attempts` flag.

**Timeouts.** aws-cli owns two flags: `--cli-read-timeout` and
`--cli-connect-timeout`, default 60s, `0` = no timeout
`[SRC awscli/data/cli.json:61; awscli/customizations/globalargs.py:117]`.

**Ordering.** aws-cli applies **no client-side sort** — keys are printed in
exactly the order S3 returns them `[SRC subcommands.py:862-889]`. S3
general-purpose buckets return keys in UTF-8 binary (lexicographic) order
`[DOC ListObjectsV2]` (directory/S3-Express buckets do **not** guarantee
this). The smoke `s3api-v2-text` output matched the byte-ordered manifest
exactly, which confirms completeness and fields — not, by itself, ordering
`[RUN receipts/smoke/s3api-v2-text]`.

## Concurrency — serial listing, supported by source and one probe

**Scope this observation carefully — it is not established across all smoke modes.** A `--debug`
probe of **one** `s3api list-objects-v2` invocation against a 3-page prefix
shows 3 `ListObjectsV2` requests, all from a single `MainThread`, each
request carrying the previous response's `NextContinuationToken`, timestamps
strictly increasing `[OBS --debug, receipts/smoke/_capability/README.md]`.
That is one s3api invocation, not an exhaustive per-flag or per-surface
sweep. `s3 ls`'s serial-ness was **not** separately `--debug`-probed; it
rests entirely on the source reading above
`[SRC subcommands.py:852,865]` (a synchronous paginator loop, no threads).
Every smoked mode passing the completeness verifier shows that each run
returned the right keys — it does not establish request concurrency, because the
verifier never counted in-flight requests.

So: "no parallelism anywhere in either command" is `[SRC]`-primary and
corroborated at the request level for the one probed invocation; "regardless
of flags" is an extrapolation from source, not an exhaustive smoke result
(claims `no-listing-parallelism`, `serial-single-thread-one-probe`).

## Pagination and continuation (resume)

- `s3api` can resume via `--starting-token` (feed a prior `NextToken`)
  `[SRC awscli/customizations/paginate.py:155-165]`. `--max-items` caps total
  items returned before emitting a `NextToken`
  `[SRC paginate.py:195-205]`.
- `s3 ls` exposes **only** `--page-size` — no `--starting-token`/`--max-items`
  in its `ARG_TABLE` `[SRC subcommands.py:790-805][RUN _build/help-s3_ls.txt]`.
  It has **no** resume/checkpoint: its paginator runs to exhaustion with no
  exposed token `[SRC subcommands.py:790-805]`.
- The token is emitted only to stdout as `NextToken`; nothing in aws-cli
  persists it `[SRC paginate.py:155-165]`. If an unbounded run is killed, the
  in-flight token was never captured, so nothing is available to resume from
  — this half of the claim rests on source, not a probe, because killing a
  process cannot demonstrate the absence of something to save.
- **What was actually run**, and it round-trips clean: `--max-items 1000` on
  `normals-hourly/` (2,549 keys) returned 1,000 keys plus an opaque
  `NextToken`; feeding it to `--starting-token` resumed and returned the
  remaining 1,549 — union 2,549 distinct, 0 cross-leg duplicates, no gap
  `[RUN receipts/smoke/_capability/resume-README.md]`. This is deliberate
  **chunked continuation** — leg 1 stopped itself with `--max-items`, nothing
  was killed. It is **not** the inherited tool page's "kill a run mid-flight
  and resume" crash test; that scenario was never exercised (claims
  `resume-primitive-round-trips`, `resume-token-not-persisted`).

## Memory behavior by output format

**Key observation: memory behavior is a decision made
by output *format*, not by which command surface you used.**

- `s3 ls` **streams**: `_display_page` prints each page as it arrives and
  keeps only running counters (`_size_accumulator`, `_total_objects`), never
  the key set `[SRC subcommands.py:865-889]`. Memory is bounded regardless of
  bucket size `[INFERRED from source]`.
- `s3api` **streams** under `--output text` and `--output yaml-stream`:
  `TextFormatter` iterates `for i, page in enumerate(response)`, formatting
  and flushing each page as it arrives `[SRC awscli/formatter.py:330-355]`,
  and `StreamedYAMLFormatter` does the same `[SRC formatter.py:154]`. Under
  these, a `--query` is applied **per page** (via the paginator's
  `result_keys` `[SRC formatter.py:337-346]`), not against a full buffer.
- `s3api` **buffers** under `--output json` (the default), `--output yaml`,
  and `--output table`: all three inherit `FullyBufferedFormatter`, which
  calls `response.build_full_result()` and accumulates the entire result set
  in memory before emitting anything
  `[SRC formatter.py:65,76,94,141,197]`.

So the streaming set is **`s3 ls` + `s3api --output text` + `s3api --output
yaml-stream`**; the buffered set is **`s3api --output json`/`yaml`/`table`**
(claim `memory-format-split`).
Whether the buffered path actually OOMs on a many-million-key bucket is
scale-dependent and **not settled by smoke** — open question for the
benchmark phase `[SRC formatter.py:65,76,141,197]`.

> **Errata — merge commit `85e561e` misstates this finding, reversed.** Its
> message lists `text` among the *buffering* formats and calls the pilot's
> text-streams claim wrong. Both are backwards. The source-anchored record
> is the one above: `s3 ls`, `s3api --output text`, and `s3api --output
> yaml-stream` **stream**; `s3api --output json`/`yaml`/`table` **buffer**
> via `build_full_result()`. The pilot had it right; a draft of
> `research/report.md` briefly had it backwards, Stage E codex review caught
> the draft (see [`research/codex-review.md`](../research/codex-review.md) §1),
> and the merge commit message inherited the backwards draft version before
> the fix landed. Commit messages are immutable history — this page and
> `research/report.md` are the corrected record. **Do not quote `git log`
> for this claim.**

## Failure surface

- **Memory growth (s3api buffered output).** `--output json`/`yaml`/`table`
  accumulate the whole result via `build_full_result()`
  `[SRC formatter.py:65,76,141,197]`; memory scales with key count. Whether
  this OOMs on a many-million-key bucket is scale-dependent and not settled
  by smoke. `s3 ls`, `--output text`, and `--output yaml-stream` avoid it by
  streaming `[SRC formatter.py:154,330; subcommands.py:865][INFERRED]`.
- **Interruption / resume.** No checkpoint for `s3 ls`; an interrupted full
  listing restarts from scratch. `s3api` can resume with `--starting-token`
  if the caller captured the last `NextToken` `[SRC paginate.py:155]`.
- **Timeouts on pathological buckets.** Thousands of delete markers on a
  versioned bucket can make a LIST time out
  `[3P https://repost.aws/knowledge-center/s3-troubleshoot-unresponsive-list]`;
  `--cli-read-timeout` governs the per-request HTTP read window
  `[SRC globalargs.py:117]`.
- **Retries.** botocore standard mode, 3 attempts default, exponential
  backoff on throttling/5xx `[SRC configprovider.py:153; awscli/botocore/retries/standard.py]`.
- **Truncated-response handling.** The paginator loops on `IsTruncated` /
  `NextContinuationToken`; a mid-list error surfaces as a non-zero exit after
  botocore's retries are exhausted `[INFERRED from paginator model]` — not
  exercised at smoke (every smoke run exits 0).
- **~12M-object mid-run failure (aws/aws-cli#1118).** A third-party report of
  a 1–2h run failing mid-way. Not reproduced here — scale/fault-injection
  scope; the claim stays `unverified`. Per this repo's evidence rule, a negative claim
  about someone else's software ships with the exact invocation, version,
  box, bucket, exit code, and raw output, or it doesn't ship — this one isn't
  reproduced yet, so it stays a hypothesis, not a finding.

## Source anchors

Carried over from `research/report.md` §11, pinned to
`12d962d239b9fd0669951c4d27dc366388abba2d` (tag `2.36.1`):

| Anchor | What it's for |
| --- | --- |
| `awscli/customizations/s3/subcommands.py` — `ListCommand` :782, `get_paginator` :852, `_display_page` :865, recursive listing :917-921, `ARG_TABLE` :790-805, delimiter :853-857, ordering :862-889, timestamp localization :936-952 | `s3 ls` mechanism, streaming, no-resume, no-sort |
| `awscli/clidriver.py` — `_create_command_table` :744-748, paginate call :1105-1107, region resolution :1091 | `s3api` command generation and pagination |
| `awscli/customizations/paginate.py` — `StartingToken` :155-165, `MaxItems` :195-205, `PageSize` :182-193 | resume / chunking primitives |
| `awscli/formatter.py` — `FullyBufferedFormatter` :65/76, `JSONFormatter` :94, `YAMLFormatter` :141, `TableFormatter` :197, `StreamedYAMLFormatter` :154, `TextFormatter` :330-355, per-page `--query` :337-346 | memory model by output format |
| `awscli/customizations/globalargs.py` — unsigned/`--no-sign-request` :99-104, `--cli-read-timeout`/`--cli-connect-timeout` :117 | anonymous access, timeouts |
| `awscli/data/cli.json` — `--output` choices :23-33, `--no-sign-request` :52, timeout defaults :61 | output formats, global flags |
| `awscli/botocore/configprovider.py` :153 | retry defaults |
| `awscli/customizations/s3/factory.py` | transfer-only concurrency (not listing) |

Full evidence-labelled writeup: [`research/report.md`](../research/report.md).
Claim-by-claim check against the inherited tool page:
[`research/reconciliation.md`](../research/reconciliation.md). Separate critical
review: [`research/codex-review.md`](../research/codex-review.md).
