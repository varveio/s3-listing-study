# Tools

One directory per tool, and everything we know about a tool lives in its
directory: what it is, how its listing works, how we ran it, what we observed,
and the evidence behind every observation. This page is the roster — who is in
the study, in what role, and where each subject stands.

**Where the study stands:** groundwork is complete for every subject — pinned
builds or source checkouts, anonymous smoke runs where the tool could list,
source-anchored mechanism reports, and a claim-by-claim reconciliation of the
inherited notes. **No benchmark comparison has been run**, and everything
scale-dependent or comparative stays unverified until the benchmark phase.

## Start here

| You want | Where to look |
| --- | --- |
| What a tool is and what we observed | `<tool>/README.md` — the tool page |
| How its listing works under the hood | `<tool>/docs/mechanism.md` |
| To run it the way the study ran it | `<tool>/docs/running.md` |
| The evidence behind a claim | `<tool>/data/claims.json` — every claim lists its evidence, or the recorded reason none exists yet; raw run records live in `<tool>/receipts/` |
| The full directory contract | [`../docs/operating/tool-structure.md`](../docs/operating/tool-structure.md) |
| To add a new tool | [`../docs/operating/tool-onboarding.md`](../docs/operating/tool-onboarding.md) |

## The roster

Groundwork split the roster into two cohorts:

- **Ran anonymously at smoke** — aws-cli, s5cmd, s7cmd, rclone, minio-mc,
  s3-fast-list, and Swath.
- **Blocked without credentials** — s3p, s3kor, s4cmd, and ps3 expose no
  unsigned request path (ps3 also has no working native build). Whether they
  participate with scoped list-only credentials is an open decision.

The tables below describe each tool's listing approach in one line. The
tool's own page and claims ledger say how each statement stands — evidenced by
a committed run, pinned source, or documentation, or still unverified with the
reason recorded. The tables are summaries, not the evidence record.

### Included in the planned comparative runs (Tier 1)

The tier label is a study-scope identifier, not a ranking of the projects.

| Tool | How it lists | What we want to learn |
| --- | --- | --- |
| [`aws-cli`](aws-cli/) | One serial page-by-page chain of ListObjectsV2 calls | A familiar reference point; memory behavior differs by output *format*, not command surface |
| [`PS3`](ps3/) | Brute-force character expansion of the keyspace | How its published comparisons with aws-cli and s5cmd translate to our setup |
| [`rclone`](rclone/) | A flat single-sweep ListR chain, or a per-directory walk that fans directories across `--checkers` workers | Memory and exit behavior under constrained runs |
| [`S3P`](s3p/) | Recursive bisection of the keyspace using synthetic midpoint keys | Whether recursive bisection translates to our setup |
| [`s3-fast-list`](s3-fast-list/) | Splits the keyspace at user-supplied cut-points (`-k` hints); serial without them | How hint-based splitting behaves for throughput and correctness; two correctness hypotheses are queued on its page |
| [`s5cmd`](s5cmd/) | One serial ListObjectsV2 chain; users can fan out per-prefix `ls` jobs through its worker pool | How its transfer-oriented concurrency relates to listing workloads |
| [`s7cmd`](s7cmd/) | Umbrella CLI over the s3ls-rs engine: parallel prefix discovery, then sequential pagination per leaf | The planned representative of the s3ls-rs family |
| [`Swath`](swath/) | Splits the keyspace into ranges and lists them in parallel with work stealing | The tool we build, included with the same run-record requirements as the other tools |

`s3ls-rs` is not listed separately: `s7cmd ls` **is** that crate (pinned
`=1.0.3`), so s7cmd represents the family and engine results generalize. Its
inherited hypothesis sheet is inherited background held in internal notes and
is not included in this public repository.

### Included when the setup permits (Tier 2)

These tools are in scope when the harness and credential setup make a useful
run practical. The grouping describes study scope, not project quality.

| Tool | How it lists |
| --- | --- |
| [`s4cmd`](s4cmd/) | Client-side `delimiter=/` recursion over the legacy v1 API: each discovered pseudo-directory becomes a new thread-pool task |
| [`MinIO mc`](minio-mc/) | A serial client-side iterator |
| [`s3kor`](s3kor/) | Serial listing; its "parallel" reputation is transfer-only |

### Related approaches documented for context (Tier 3)

These are not single-bucket listers. We document them because they show where
listing work lands in practice, but we do not put them in the same comparative
runs. They do not have a tool-page directory; see
[`../docs/open-questions.md`](../docs/open-questions.md).

Hadoop S3A · Spark `InMemoryFileIndex` · Iceberg / Delta maintenance ops ·
`s3pd` (a downloader, not a lister) · legacy Python `s3p` (distinct from
`generalui/s3p`) · `s3sync` / `s3s3mirror`

### Related subjects outside the runnable study (Tier 4)

| Subject | Why |
| --- | --- |
| [Pure Storage 67B-object result](pure-storage/) | Ran on FlashBlade hardware, not AWS. `unverifiable` with the resources we have, so we keep it separate from AWS results. |
| [S3 Inventory / S3 Metadata](s3-inventory/) | Not a tool under test — the baseline that makes this whole category conditional. |

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
- `adapter/` — harness integration, with `fixtures/` where a tool has
  synthetic adapter QA
- `build/` — the study's local image recipe, only where a tool needs one
- `research/` — the frozen research trail the page was derived from
- `receipts/` — immutable run records

The purpose and content contract for every layer is defined in
[`../docs/operating/tool-structure.md`](../docs/operating/tool-structure.md). Contextual entries
(`pure-storage`, `s3-inventory`) are README-only directories.

Nothing is run on the host — see
[`../docs/methodology.md`](../docs/methodology.md) § 3a. We prefer a tool's own
upstream image over a Dockerfile of ours: it's what users actually run, and it
reduces the chance that our setup differs from a normal installation.
