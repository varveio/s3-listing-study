# Tools

One directory per tool, and everything we know about a tool lives in its
directory: what it is, how its listing works, how we ran it, what we observed,
and the evidence behind every observation. This page is the map: who is in
the study, which listing strategy each one uses, and where each stands in the
current release, `2026-09-scale-diagnostics`.

Two things to hold onto while reading. The release is diagnostic, not a
ranking: it says what each tool completed and what happened at the largest
fixture it was taken to, and "not carried further" is a study decision, not
a measured limit. And "count matched" means the row count agreed with the
staged fixture count, or with the capture's count where none was staged; it
is not key-by-key verification. The short version of the findings is
[`../RESULTS.md`](../RESULTS.md); the numbers and attempt ids are in the
[report](../results/2026-09-scale-diagnostics/REPORT.md).

## Start here

| You want | Where to look |
| --- | --- |
| What a tool is and what we observed | `<tool>/README.md`, the tool page; its "In the current release" section carries the release rows |
| How its listing works under the hood | `<tool>/docs/mechanism.md` |
| To run it the way the study ran it | `<tool>/docs/running.md` |
| The evidence behind a claim | `<tool>/data/claims.json`; raw groundwork run records live in `<tool>/receipts/` |
| The full directory contract | [`../docs/operating/tool-structure.md`](../docs/operating/tool-structure.md) |
| To add a new tool | [`../docs/operating/tool-onboarding.md`](../docs/operating/tool-onboarding.md) |

## The tools, by listing strategy

Four strategies cover every tool here. The strategy predicts where a tool
struggles better than its language or its age does.

### Serial pagination

One `ListObjectsV2` continuation chain: request a page, wait, request the
next. Works on any bucket shape; the wall is time, since a billion objects is
a million sequential pages.

| Tool | How it lists | In the current release |
| --- | --- | --- |
| [`aws-cli`](aws-cli/) | `s3api list-objects-v2`, one page at a time | Completed the 4.08M fixture, count matched, in 700.0 s. Not carried further. |
| [`MinIO mc`](minio-mc/) | A serial client-side iterator | Completed 4.08M, count matched, in 419.7 s. Not carried further. |
| [`s3kor`](s3kor/) | Serial listing; its parallelism is in transfers | Completed 4.08M, count matched, in 411.0 s; three other rows returned one row more. Not carried further. |
| [`s5cmd`](s5cmd/) `ls` | One serial chain; see also "supplied partitions" below | Its serial arm ran on 4.08M only. |

### Prefix discovery

Use the bucket's `/`-separated structure to create parallel jobs, one per
directory. Fast on a bushy namespace; collapses on a flat one or on one
dominant directory, because pagination inside a directory is still serial.

| Tool | How it lists | In the current release |
| --- | --- | --- |
| [`rclone`](rclone/) | A per-directory walk that fans directories across `--checkers` workers, or a flat single-sweep chain | Completed 143M in 667.0 s at c64, nine rows short of the fixture count. The walk was killed at the 8 GiB limit on the flat 13.9M fixture and completed at 16 GiB with 7.7 GiB resident; its flat single-sweep mode did the same cell in 72 MiB. |
| [`s7cmd`](s7cmd/) | Parallel prefix discovery, then sequential pagination per leaf (the s3ls-rs engine) | Count matched at 13.5M in 601.0 s. Returned no count at 66.4M and reached the 7,200 s cap on the skewed 143M fixture; on the flat fixture it drained the whole bucket serially in 1,289.5 s. |
| [`s4cmd`](s4cmd/) | Client-side `delimiter=/` recursion over the legacy v1 API | No attempt: the replay server serves `ListObjectsV2` only, and s4cmd needs credentials on live S3. |

### Speculative range splitting

Invent boundaries in the keyspace and list the ranges in parallel. Needs no
directories; the cost is probe requests and guesses that can land badly.

| Tool | How it lists | In the current release |
| --- | --- | --- |
| [`S3P`](s3p/) | Recursive bisection of the keyspace using synthetic midpoint keys | Completed 143M, count matched, in 4,238.8 s at c16 in its key-only mode. The release does not establish CPU or width effects. |
| [`PS3`](ps3/) | Brute-force character expansion of the keyspace | Completed 4.08M, count matched, in 369.3 s. It issues about 115 replay requests per page; the wider arm reached the 1,800 s cap without a count. |
| [`Swath`](swath/) | Range splitting with work stealing: a cheap initial division, then idle workers steal and re-split busy ranges while the run proceeds. The tool we build; same rules as every other subject. | Completed 143M, count matched, in 173.7 s at c256 on a larger replay server than the other tools had. Every replay row on the small-directory fixtures failed the timing gate because of the instrument's structure-probe defect. On live S3, once each: 1.07 billion rows in 5 m 41 s, 960 million in 9 m 45 s. |

### Supplied partitions

List ranges someone else provides: an inventory, a previous listing, a shard
file. Parallelises well once the cuts are known; cannot help on an unfamiliar
bucket.

| Tool | How it lists | In the current release |
| --- | --- | --- |
| [`s3-fast-list`](s3-fast-list/) | Splits the keyspace at user-supplied cut-points (`-k` hints); serial without them; holds every key in memory until one Parquet write at the end | Completed 66.4M, count matched, in 333.8 s on cut-points the harness generated, with 11,347,320 KiB peak RSS. One other attempt was killed at 8 GiB; one failed at 16 GiB with exit 0. |
| [`s5cmd`](s5cmd/) `run` | Fans per-prefix `ls` jobs through its worker pool from a shard list | Completed 66.4M, count matched, in 352.2 s, on shard lists the harness supplied. Not carried further. |

### Not run, kept for context

These are not single-bucket listers and do not enter the runs. They are here
because they show where listing work lands in practice; see
[`../docs/open-questions.md`](../docs/open-questions.md).

Hadoop S3A, Spark `InMemoryFileIndex`, Iceberg and Delta maintenance
operations, `s3pd` (a downloader), the legacy Python `s3p`, and
`s3sync`/`s3s3mirror`.

| Subject | Why it has a page |
| --- | --- |
| [Pure Storage 67B-object result](pure-storage/) | Ran on FlashBlade hardware, not AWS; `unverifiable` with the resources we have, so it stays separate from AWS results. |
| [S3 Inventory / S3 Metadata](s3-inventory/) | Not a tool under test: the baseline that makes this whole category conditional. |

## How the tools were reached

Groundwork split the roster by how a tool can be reached at all: aws-cli,
s5cmd, s7cmd, rclone, minio-mc, s3-fast-list and Swath list public buckets
anonymously; s3p, s3kor, s4cmd and ps3 expose no unsigned request path and
ran under a scoped list-only credential. Groundwork receipts under each
`receipts/` directory are single smoke runs against one small public bucket,
with no verifier verdict; they are not comparable to anything. The release
rows are a separate layer, and no row in either layer carries a verifier
verdict, so "ran" and "verified" remain separate facts.

## What the evidence labels mean

| State | Meaning |
| --- | --- |
| `unverified` | Testable, but the available evidence does not settle it. |
| `supported` | Public source, documentation, or a bounded observation supports it; this does not claim the study reproduced the behavior. |
| `confirmed` | Reproduced in this repo with a committed receipt containing the invocation, environment, and output or its hash-bound location. |
| `unverifiable` | Cannot be settled with surviving public evidence or resources available to the study. The reason is recorded. |

A reputable source, including AWS's own documentation, can help us understand a
tool but does not show that we ran it. Only an exact run recorded in this repo
can make a run-dependent observation `confirmed`.

Editorial disposition is a separate ledger field: `retained`, `corrected`, or
`contradicted` describes how current wording relates to the inherited seed and
does not imply a stronger evidence state. Frozen research and repository law
still use the legacy uppercase forms: `VERIFIED: no` maps to `unverified`,
`CONFIRMED` to receipt-backed `confirmed`, and `UNVERIFIABLE` to
`unverifiable`. A legacy runtime `CORRECTED` promotion requires a receipt;
current ledgers carry correction separately so a source-supported correction is
not mislabeled as a reproduced run.

## Inside a tool directory

Every runnable tool uses the same layout:

- `README.md` — the tool page: what it is and what we observed
- `data/` — machine-readable identity (`tool.json`) and the claims ledger
  (`claims.json`)
- `docs/` — how it works (`mechanism.md`) and how to run it (`running.md`)
- `adapter/` — typed Python command compilation, native row counting, and
  explicit verifier normalization, with `fixtures/` where a tool has synthetic
  adapter QA
- `build/` — the pinned tool-payload recipe and image registration
- `research/` — the frozen research trail the page was derived from
- `receipts/` — immutable run records

The purpose and content contract for every layer is defined in
[`../docs/operating/tool-structure.md`](../docs/operating/tool-structure.md). Contextual entries
(`pure-storage`, `s3-inventory`) are README-only directories.

Nothing is run on the host — see
[`../docs/methodology.md`](../docs/methodology.md) § 3a. Comparative images use
one published study base and one final image per tool. Each payload prefers the
selected release's checksum-pinned official binary, archive, or package.
