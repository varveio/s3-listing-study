# ps3 — reconciliation with the inherited dossier

Walks every claim in `tools/ps3/README.md` (the seeded dossier) against my
independent groundwork (`research/report.md`, receipts under `receipts/smoke/`).
Pinned checkout `@ 9428492` = `9428492291ef3aa824dba0b495583279c3d33760`
(default-branch HEAD; project cuts no releases). All runtime statements derive
from the amd64 image run under **qemu emulation** on an arm64 runner
(`emulated=yes` in every receipt) — that caveat rides every [RUN]/[OBS] row.

Promotion rule honored: **only a committed receipt promotes past `VERIFIED: no`.**
Source reading corrects bookkeeping (editorial) but never verifies behavior.

## Metadata / bookkeeping claims

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| M1 | License **MIT** | **Contradicted** (editorial) | `[SRC LICENSE @ 9428492]` — file is verbatim **GNU GPL v3** ("GNU GENERAL PUBLIC LICENSE / Version 3, 29 June 2007"); `main.go`/source headers say GPL. The MIT cell is wrong. |
| M2 | Version "Unknown — no revision recorded" | **Contradicted** (editorial) | `[SRC cmd/root.go:16 @ 9428492]` `pS3Version = "0.1.16"`; `[RUN _capability/list-anon]` `pS3 version 0.1.16`. |
| M3 | "version-less until we pin one" (What-to-verify #1) | **Settled** (editorial) | Pinned to `9428492…` (HEAD; no tags/releases exist). |
| M4 | Repo `https://github.com/jboothomas/ps3`, Go | **Corroborated** | `[SRC @ 9428492]`; cloned and read. |
| M5 | "Testability: needs a Go toolchain. Otherwise straightforward." | **Contradicted** | `[RUN receipts/smoke/_build/]` — source does **not** compile (missing `log`/`atomic` imports, unused `os`, syntax error at `listObjectsV2.go:186`). Not straightforward; the only working artifact is the committed prebuilt binary. |
| M6 | PS3 exists, is public, was obtained, source was read; anchors are "a real read of real source" | **Corroborated** | Confirmed by independent clone + full source read. |

## Mechanism claims

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| A1 | Brute-force character-by-character prefix expansion; not S3P-style bisection | **Corroborated** | `[SRC cmd/listObjectsV2.go findPrefixes/discoverPrefixes:190-289 @ 9428492]`; `[3P author blog]`. No key-midpoint computation anywhere. |
| A2 | Per character, start a listing with that 1-char prefix; recurse to (N+1)-char extensions (`a*`→`aa*`,`ab*`…) when a prefix overflows one page | **Corroborated** | `[SRC :213-241 @ 9428492]` `nextPrefix := currentPrefix + c` then `go discoverPrefixes(nextPrefix)` on overflow. |
| A3 | Recursion threshold = 1000 (ListObjectsV2 page size); ≤1000 ⇒ leaf, accept without recursion | **Corroborated, with a correction** | `[SRC :222-224,243 @ 9428492]` branch tests `len(Contents) > 999` and **never** `IsTruncated`; so `1..999` ⇒ emit directly, but an exactly-1000-key *non-truncated* prefix is misclassified as "large" and needlessly recursed — "≤1000 ⇒ leaf" is only *almost* right. Page size is `maxKeys=1000` (a package `var`, `[SRC root.go:42]`). |
| A4 | Alphabet is "each printable byte, or each character class in some variants"; exact set not pinned; possibly configurable ("character set … controls branching factor" tunable) | **Contradicted** | `[SRC cmd/root.go:36-39 @ 9428492]` — a **fixed 81-element** slice (`space ! & ' ( ) + , - . /`, digits, `: ; = ? @`, `A–Z _ a–z * $`), a **package `var` (not a const), not configurable**, and **not** "each printable byte": it omits `" # % < > [ \ ] ^ \` { | } ~` and all non-ASCII. `[RUN --help receipts/smoke/_capability/help/]` shows no flag for it. |
| A5 | Code anchor: `cmd/listObjectsV2.go` fn `discoverPrefixes` lines **196-241** (and 213-241 for the recursion sketch) | **Corroborated** | `[SRC @ 9428492]` — the closure `discoverPrefixes` is defined at line 196; the char-loop + `nextPrefix` + recurse body is 213-241 exactly. The inherited anchor is accurate (rare — most such anchors were reconstructions). |
| A6 | (Implied) needs no bootstrap/hints/prior keyspace knowledge | **Corroborated** | `[SRC]` — discovery starts from `""` with no external input. |

## Tunables / modes claims

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| T1 | "Concurrency / worker-count knobs — Unknown; presumably exists given claimed throughput. Read from source before benchmarking." | **Contradicted** | `[SRC cmd/root.go:44 @ 9428492]` `maxSemaphore int = 256` — a package **`var`** (never reassigned) used as the pager fan-out semaphore *and* the printer-worker count; prefix **discovery** goroutines are **unbounded** (`go discoverPrefixes` `[SRC listObjectsV2.go:241]`). **No flag** exposes any of it (`[RUN --help receipts/smoke/_capability/help/]`). The knob does not exist; neither concurrency source can be capped to `CONCURRENCY_CAP=8`. |
| T2 | "Output mode(s) — Unknown" | **Contradicted / refined** | `[RUN --help]` a `--output {json,text}` flag exists, **but** `[SRC readObjectsV2:155-188 @ 9428492]` ignores `fOutput` and always prints one fixed `Object: …` line — inert in HEAD source (binary may differ; untested — blocked). |
| T3 | "Page-fit threshold for recursion = 1000" | **Corroborated** | `[SRC root.go:42 / listObjectsV2.go:224]`. |
| T4 | "This table is almost certainly incomplete … full flag surface needs a real --help" | **Corroborated & completed** | `[RUN --help]` — full surface now captured in `report.md` §3: `--prefix-count`(500), `--output`, `--region`, `--endpoint-url`, `--no-verify-ssl`, `--profile`, `--verbose/--debug/--trace`, plus subcommands `list-objects-v2`, `list-object-versions`, `head-objects`, `list-test`. |

## Claimed numbers (author's [3P] blog — provenance kept exact)

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| N1 | 15,000,000 objects in 160 s (~94K obj/s) | **Unaddressed** | No benchmark run (blocked: no anonymous mode, `CREDS=none`). Traces to one Medium post, one bucket, one run — `[3P author blog]`. Not reproduced; not reproducible in this phase. |
| N2 | ~7× vs `aws s3api` (1110 s) | **Unaddressed** | Same `[3P]` single data point. Internally checkable in principle (aws-cli is a study subject) — flagged for the benchmark phase; **promoted nowhere**. |
| N3 | ~5× vs `s5cmd` (733 s) | **Unaddressed** | Same `[3P]` single data point; s5cmd is a study subject — internally checkable later; **promoted nowhere**. On *local* (non-AWS) S3 per the blog. |

## Weakness hypotheses (to test)

| # | Inherited claim | Verdict | Evidence |
| --- | --- | --- | --- |
| W1 | Discovery tax up to `alphabet^N` speculative LISTs; worst on sparse deep-shared-prefix keyspaces | **Unaddressed** (mechanism plausible) | `[SRC]` confirms speculative per-character LISTs exist; the *cost at scale* is a benchmark question, unrun. |
| W2 | Alphabet expansion breaks on non-ASCII / arbitrary-byte keyspaces; "rare in practice" is an assertion | **Corroborated at source; runtime Unaddressed** | `[SRC root.go:36-39]` proves the 81-char set cannot express non-ASCII or most punctuation lead bytes, so such keys are **silently dropped** by construction — the mechanism for breakage is confirmed. A live demonstration needs the edge fixture (`EDGE_BUCKET=none` → deferred) and credentials (blocked). |
| W3 | Headline throughput/ratios may not reproduce off the author's box | **Unaddressed** | Blocked; the benchmark phase's job. |
| W4 | Multipliers are internally falsifiable (our own aws-cli/s5cmd numbers) | **Unaddressed** (noted) | Correct in principle; deferred to benchmark. Promote nothing now. |

## New independent findings NOT in the dossier (for the report/owner, no dossier verdict)

- **No unsigned/anonymous request path** — `[SRC listObjectsV2.go:90-99]` (no `AnonymousCredentials`, no `--no-sign-request`; `session.SharedConfigEnable` only) plus `[OBS silent-empty]` (`GetBucketLocation` → `NoCredentialProviders` in trace) show the *binary* also has no unsigned path. The committed `[RUN _capability/list-anon]` receipt settles narrowly: **for v0.1.16, this `list-objects-v2` invocation, under the harness anonymous env**, exit 1, no listing. With `CREDS=none` every mode is **blocked** (the two unrun modes by shared-session inference, not by their own receipt).
- **Silent exit-0 empty output on bare no-creds** — `[OBS _capability/silent-empty-obs.md]`: env matters — the harness's full starvation (config/creds files → nonexistent path) yields exit 1 at session creation; a *bare* env (`AWS_EC2_METADATA_DISABLED=true` only) yields **exit 0, zero objects, no error** (false success). Both recorded.
- **Binary not reproducible from repo** — the shipped `pS3.0-1-16` exposes `head-objects`/`list-object-versions`/`list-test`, whose source is **absent** from the checkout; combined with the compile errors, HEAD cannot rebuild the binary. `[RUN _build]`, `[RUN --help]`.
- **GetBucketLocation region bug** `[SRC listObjectsV2.go:107-117]`; **error-swallowing nil-deref** in `s3ListObjectsWithBackOff` `[SRC s3SDKfunctions.go:74-77]`; **`--debug`/`--trace` suppress object output** `[SRC readObjectsV2:164-169]`.

## Cross-tool / S3 claims surfaced
None. `docs/open-questions.md` contains no claim naming ps3, and my
research produced no new cross-cutting claim about other tools or S3 itself
(the aws-cli/s5cmd figures are the author's, already in this tool's dossier).

## Verdict counts
Corroborated 10 (A1,A2,A3,A5,A6,M4,M6,T3,T4,W2) · Contradicted 6
(A4,M1,M2,M5,T1,T2 — all editorial/source-level, none promoting behavior past
`VERIFIED: no`; M5's does-not-build is receipt-backed `[RUN _build]`) · Settled 1
(M3 — editorial SHA pin) · Unaddressed 6 (N1,N2,N3,W1,W3,W4) · plus the 4
new-finding bullets above.
