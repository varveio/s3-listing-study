# Tools

One directory per tool, and everything we know about a tool lives in its
directory: what it is, how its listing works, how we ran it, what we observed,
and the evidence behind every observation. This page is the roster — who is in
the study, in what role, and where each subject stands.

**Where the study stands:** groundwork is complete for every subject — pinned
builds or source checkouts, anonymous smoke runs where the tool could list,
source-anchored mechanism reports, and a claim-by-claim reconciliation of the
inherited notes. Comparative runs have since happened and are published as a
**diagnostic** release, `2026-09-scale-diagnostics`: see
[`../RESULTS.md`](../RESULTS.md) and the release's
[report](../results/2026-09-scale-diagnostics/REPORT.md). No attempt in that
release carries `purpose = measurement`, so it establishes where each approach
stops and by what mechanism — not a ranking, and not a calibrated benchmark.
The "current release outcome" column below summarises each subject's standing
in it; the report holds the attempt ids behind every figure.

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

Groundwork split the roster by how a tool can be reached at all:

- **Smoked anonymously** — aws-cli, s5cmd, s7cmd, rclone, minio-mc,
  s3-fast-list, and Swath.
- **Requires a credential** — s3p, s3kor, s4cmd, and ps3 expose no unsigned
  request path. They are now smoked under a scoped list-only credential; ps3
  additionally runs natively for the first time, having previously only ever
  been built under emulation.

Every subject has now run at smoke through the attempt engine. None of those
engine attempts carries a verifier verdict: auditing one against a reference
manifest is not implemented, so "ran" and "verified" remain separate facts here,
as everywhere else in this repository.

The tables below describe each tool's listing approach in one line. The
tool's own page and claims ledger say how each statement stands — evidenced by
a committed run, pinned source, or documentation, or still unverified with the
reason recorded. The tables are summaries, not the evidence record.

### Included in the planned comparative runs (Tier 1)

The tier label is a study-scope identifier, not a ranking of the projects.

| Tool | How it lists | What we want to learn | Current release outcome |
| --- | --- | --- | --- |
| [`aws-cli`](aws-cli/) | One serial page-by-page chain of ListObjectsV2 calls | A familiar reference point; memory behavior differs by output *format*, not command surface | Completed the 4.08M rung, exact, in 700.0 s. Serial by construction; kept as the reference point and not carried further. |
| [`PS3`](ps3/) | Brute-force character expansion of the keyspace | How its published comparisons with aws-cli and s5cmd translate to our setup | Completed 4.08M, exact, in 369.3 s. The wider fairness arm reached the 1,800 s cap without a count; not carried further. |
| [`rclone`](rclone/) | A flat single-sweep ListR chain, or a per-directory walk that fans directories across `--checkers` workers | Memory and exit behavior under constrained runs | Reached 143M in 667.0 s at c64, nine rows short of the fixture count. Killed at the 8 GiB container limit on the flat 13.9M fixture. |
| [`S3P`](s3p/) | Recursive bisection of the keyspace using synthetic midpoint keys | Whether recursive bisection translates to our setup | Reached 143M, exact, in 4,238.8 s at c16. CPU-bound in its cheapest mode; width does not help it. |
| [`s3-fast-list`](s3-fast-list/) | Splits the keyspace at user-supplied cut-points (`-k` hints); serial without them | How hint-based splitting behaves for throughput and correctness; two correctness hypotheses are queued on its page | Reached 66.4M, exact, in 333.8 s with harness-supplied cut-points and 11,347,320 KiB peak RSS. Two attempts died at the container memory limit. |
| [`s5cmd`](s5cmd/) | One serial ListObjectsV2 chain; users can fan out per-prefix `ls` jobs through its worker pool | How its transfer-oriented concurrency relates to listing workloads | Reached 66.4M, exact, in 352.2 s, but only with harness-supplied shards; it has no listing fanout of its own. |
| [`s7cmd`](s7cmd/) | Umbrella CLI over the s3ls-rs engine: parallel prefix discovery, then sequential pagination per leaf | The planned representative of the s3ls-rs family | Exact at 13.5M in 601.0 s. Returned no count at 66.4M and reached the 7,200 s cap on the skewed 143M fixture. |
| [`Swath`](swath/) | Splits the keyspace into ranges and lists them in parallel with work stealing | The tool we build, included with the same run-record requirements as the other tools | Reached 143M, exact, in 173.7 s at c256. Every replay row on the small-directory fixtures is penalised by the instrument's structure-probe defect. |

`s3ls-rs` is not listed separately: `s7cmd ls` **is** that crate (pinned
`=1.0.3`), so s7cmd represents the family and engine results generalize. Its
inherited hypothesis sheet is inherited background held in internal notes and
is not included in this public repository.

### Included when the setup permits (Tier 2)

These tools are in scope when the harness and credential setup make a useful
run practical. The grouping describes study scope, not project quality.

| Tool | How it lists | Current release outcome |
| --- | --- | --- |
| [`s4cmd`](s4cmd/) | Client-side `delimiter=/` recursion over the legacy v1 API: each discovered pseudo-directory becomes a new thread-pool task | No attempt in the current release. |
| [`MinIO mc`](minio-mc/) | A serial client-side iterator | Completed 4.08M, exact, in 419.7 s. Serial iterator, flat in memory; not carried further. |
| [`s3kor`](s3kor/) | Serial listing; its "parallel" reputation is transfer-only | Completed 4.08M, exact, in 411.0 s. Serial listing; not carried further. |

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
