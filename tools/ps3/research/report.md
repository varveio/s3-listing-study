# ps3 (pS3) — independent research report

Groundwork for the s3-listing-study. Workspace phase (Stages A–C). Every
behavioral claim carries an evidence label; see the study brief for the label
table. `@ 9428492` is short for the pinned commit
`9428492291ef3aa824dba0b495583279c3d33760`.

---

## 1. Metadata

| | |
| --- | --- |
| Tool | **pS3** ("parallel S3") |
| Repo | https://github.com/jboothomas/ps3 (confirmed canonical: `Copyright © 2023 Jean-Baptiste Thomas <jboothomas@gmail.com>`, matches blog author) |
| Pinned commit | `9428492291ef3aa824dba0b495583279c3d33760` — **default-branch HEAD** (`main`); the project cuts **no releases and no tags**, so HEAD is pinned |
| Commit date | 2024-01-02 |
| Version | `0.1.16` ([SRC cmd/root.go:16 @ 9428492]; `pS3 version 0.1.16` [RUN receipts/smoke/_capability/list-anon]) |
| Language | Go (built with go1.20.3; deps `aws-sdk-go v1.44.249`, `cobra v1.7.0`, `viper v1.15.0` — read from the shipped binary's build metadata) |
| License | GPL-3.0 ([SRC LICENSE @ 9428492]) |
| Upstream health | **Dormant.** 16 commits total, last 2024-01-02 (~2.5 yr stale as of 2026-07-17). No releases, no tags, no issues/PRs surface, no README, no docs, no `go.mod`. Single author. |
| Image | self-built `ps3-study@sha256:c0d7b655163832bf769af0dd5da037c17f6b7b1b519724b8291297b5ae539663` (debian-slim + upstream's committed prebuilt binary; see § 7) |
| Date | 2026-07-17 |

**Headline: pS3 is effectively unusable for this study, on three independent
grounds, any one of which is a finalize-early blocker.**
1. **No anonymous/unsigned request path** + campaign `CREDS=none` → every
   listing mode is **blocked, not skipped** ([RUN], [SRC], § 4/§ 8).
2. **Listing concurrency is unconfigurable, and partly unbounded** — the
   parallel pager is capped by a package `var maxSemaphore = 256` (never
   reassigned, no flag, [SRC cmd/root.go:44 @ 9428492]), while prefix
   *discovery* spawns **unbounded** goroutines (`go discoverPrefixes`, [SRC
   cmd/listObjectsV2.go:241]). Neither can be brought within
   `CONCURRENCY_CAP=8`; even with credentials the mode would be
   **blocked-and-recorded** per the guardrail.
3. **The shipped source does not compile** and does not match the shipped
   binary; the runnable artifact is amd64-only and this runner is arm64 (smoke
   used qemu) (§ 7, `_build/`).

---

## 2. How it works

pS3's premise ([3P author blog]): the only way to parallelize S3 LIST is to
issue concurrent LISTs against different **prefixes**, but you usually don't
know the prefixes — so pS3 **discovers them by brute force**, then fans out.

**Keyspace division — brute-force character walk** ([SRC cmd/listObjectsV2.go
`findPrefixes`/`discoverPrefixes`:190-289 @ 9428492], corroborated
[3P author blog]). Starting from prefix `""`, for each character `c` in a fixed
**81-element** alphabet (a package `var`, [SRC cmd/root.go:36-39 @ 9428492] —
`space ! & ' ( ) + , - . /` digits `: ; = ? @` `A–Z _ a–z * $`) it issues
`ListObjectsV2(Prefix = current+c, MaxKeys=1000)` and branches on the count:
- **> 999 keys** (a full 1000-key page — the code tests `len(Contents) > 999`
  and **never** `IsTruncated` [SRC :222-224], so an exactly-1000-key
  *non-truncated* prefix is also treated as "large" and needlessly recursed):
  treat `current+c` as a "large"
  prefix, increment `processedCount`, and **recurse** `go
  discoverPrefixes(current+c)` — a new goroutine. (Special case: if the first
  returned key equals the prefix exactly, that single key is emitted directly
  [SRC :226-231].)
- **1..999 keys**: a "small" prefix — **all** its keys are emitted straight from
  the discovery response [SRC :252-254] (they are complete: the page was not
  truncated).
- **0 keys**: skip.

A cutoff bounds the discovery: once `processedCount >= target` (the
`--prefix-count` flag, default 500), any further `discoverPrefixes` call
immediately appends its current prefix to the "large" list and returns
[SRC :205-212], stopping fine-grained descent. There is a rebalancing loop that,
if too few large prefixes were found, shrinks `processedCount` by ¼ and re-runs,
up to 10 times [SRC :265-288].

**Parallel phase** ([SRC `listObjectsInParallel`:291-327 @ 9428492]). The
accumulated "large" prefixes are each fully paginated by
`s3ListAllObjectsWithBackoff` ([SRC cmd/s3SDKfunctions.go:102-164 @ 9428492] —
ListObjectsV2 with `ContinuationToken`, `MaxKeys=1000`, streaming each page's
objects into a channel), under a **semaphore of `maxSemaphore=256`** (a package
`var`, never reassigned) [SRC cmd/root.go:44]. **This semaphore bounds only the
pager, not discovery** — the `go discoverPrefixes` recursion at [SRC :241] fans
out with no semaphore. Objects flow through an unbuffered channel to a pool of
**256 worker goroutines** [SRC `readObjectsV2`:155-188, `numWorkers := maxSemaphore`]
that print them.

- **Parallelism**: listing itself is parallel (both discovery recursion and the
  large-prefix pagination fan out via goroutines); this is genuinely a parallel
  *listing*, not just parallel transfers. Confirmed [SRC], [3P].
- **Pagination / page size**: ListObjectsV2, `MaxKeys=1000` (a package `var`,
  never reassigned, [SRC cmd/root.go:42]); continuation-token loop for large prefixes.
- **Retry/backoff**: `s3ListAllObjectsWithBackoff` has an exponential-backoff
  loop (`2^i` seconds, `maxRetries=10`) [SRC s3SDKfunctions.go:146-148]. But the
  discovery-path helper `s3ListObjectsWithBackOff` is **broken**: `if err != nil
  { return resp, nil }` [SRC s3SDKfunctions.go:74-77] returns immediately on any
  error with a **nil error and a nil/partial resp**, making the backoff below it
  dead code and handing callers a nil response they then dereference. [SRC]
- **Timeouts**: custom HTTP client — 5 s connect, 5 s TLS, 5 s response-header,
  HTTP/2 enabled, 100 idle conns/host [SRC cmd/httpUtils.go:24-51].
- **Ordering assumptions**: none for correctness — keys are streamed as pages
  arrive across many prefixes concurrently, so **output order is
  non-deterministic** [SRC readObjectsV2 + concurrent producers]. The verifier
  must treat pS3 output as an unordered set.
- **Memory model**: streaming, not accumulate-then-dump — objects go producer →
  channel → printer, never a full in-memory slice of all keys [SRC]. The
  discovered *prefix* list is held in memory (bounded ~ `--prefix-count`).
- **Resume/checkpoint**: none. [SRC — no such mechanism]

**Coverage limitation (correctness, [SRC])**: because discovery only extends a
prefix with bytes from the 81-char alphabet, any key whose next distinguishing
byte is **outside** that set — e.g. `" # % < > [ \ ] ^ ` { | } ~`, or any
non-ASCII/UTF-8 lead byte — is **never discovered and silently dropped**. Keys
under an already-emitted "small" prefix are safe (whole page returned); the gap
is for keyspaces broad enough to require descent through an out-of-alphabet
byte. Untestable here (`EDGE_BUCKET=none` → deferred); for `noaa-normals-pds`
all keys are ASCII within the alphabet, so coverage would be complete *if it
could authenticate*.

---

## 3. Modes and tunables

The shipped **binary** exposes four object subcommands ([RUN --help, image
above]); note only `list-objects-v2` has source in the checkout — the others'
source files are **absent** (§ 7, a provenance finding).

| Subcommand | Kind | Request pattern / output contract | Evidence |
| --- | --- | --- | --- |
| `list-objects-v2` | **MODE** (primary) | Brute-force prefix fan-out over ListObjectsV2, recursive, **no delimiter**, full-bucket only | [SRC], [RUN] |
| `list-object-versions` | **MODE** | ListObjectVersions API (`s3:ListBucketVersions`); different API + version-id output contract | [RUN --help receipts/smoke/_capability/help/]; helpers exist [SRC cmd/s3SDKfunctions.go:198-255], command source absent |
| `head-objects` | mode-ish | "List Head information for all objects" — likely lists then HEADs each key ([INFERRED] from name + `s3headObject` helper [SRC cmd/s3SDKfunctions.go:13-28]; command source absent, so unconfirmed) | [RUN --help help/] |
| `list-test` | not a real mode | Help body is the **unmodified cobra scaffold** ("A longer description that spans multiple lines…"); dev/placeholder | [RUN --help help/] |

**Tunables.** In HEAD source `--prefix-count` is a **local** flag on
`list-objects-v2` only [SRC listObjectsV2.go:43-45], not persistent; the shipped
binary's other subcommands each declare their own `--prefix-count` in `--help`
[RUN help/]. The remaining flags below are persistent root flags:

| Flag | Default | Effect | Sweep? | Evidence |
| --- | --- | --- | --- | --- |
| `--prefix-count int` | 500 | Discovery cutoff: point at which prefixes stop being subdivided and are handed to the parallel pager | **Yes** (benchmark) — primary knob | [SRC listObjectsV2.go:45; findPrefixes:205] |
| `--output {json,text}` | text | **Inert in HEAD source** — `readObjectsV2` ignores `fOutput` and always `fmt.Printf`s one fixed line [SRC :155-188]. Binary may differ (untested — blocked). | verify in benchmark | [SRC] |
| `--region` | "" | Passed to SDK; interacts with a buggy GetBucketLocation fallback (§ 6) | n/a | [SRC :86-117] |
| `--endpoint-url` | "" | Override S3 endpoint | n/a | [SRC root.go:90] |
| `--no-verify-ssl` | false | `InsecureSkipVerify` + `DisableSSL` | n/a | [SRC listObjectsV2.go:63,71] |
| `--profile` | "" | Named profile from shared credentials file | n/a | [SRC :92,121] |
| `--verbose`/`--debug`(hidden)/`--trace`(hidden) | off | Logging. **`--debug`/`--trace` SUPPRESS object output** and print only a count [SRC readObjectsV2:164-169] — you cannot get both objects and request-level logging | n/a | [SRC] |

**Not present**: no `--prefix` / delimiter / max-keys / concurrency flag; no
`--no-sign-request`. Pager concurrency (256) and page size (1000) are
**package `var`s never exposed as flags** (and discovery concurrency is
unbounded), not tunables [SRC root.go:42,44; listObjectsV2.go:241]. This is why
the concurrency cannot be capped to 8 (blocker #2).

---

## 4. How to run it properly

**Quickstart (the only working artifact)** — the upstream prebuilt binary
`pS3.0-1-16`, credentials present:
```
pS3 list-objects-v2 --bucket <BUCKET> --region <REGION>
```
Output streams to stdout as `Object: <mtime> \t <size> \t <key>` lines
([SRC readObjectsV2:167] / help). **Not observed running successfully** — no
credentialed run was possible (blocked); this is the intended invocation, not a
verified one.

**Best-practice for large listings** (author's [3P blog]): the brute-force
discovery is the whole point — no two-pass or hint file. The author documents
**no tunable at all** (the blog does not mention `prefix-count`); he reports 160 s to
list a **15-million-object** *local (non-AWS) S3* bucket vs 1110 s for `aws
s3api` and 733 s for `s5cmd` [3P blog] — **author-claimed, un-reproduced, on
local S3, not AWS; treat as context only**.

**Auth setup — there is NO unsigned/anonymous mechanism.** [SRC + RUN]
- No `--no-sign-request`, no `AnonymousCredentials`. The session is built with
  `session.SharedConfigEnable` and the default credential chain only [SRC
  listObjectsV2.go:90-99,119-128]. (`credentials.AnonymousCredentials` appears
  in the binary only because the SDK package is linked, not because pS3 uses
  it.)
- Credentials come solely from `--profile` (shared credentials file) or the
  ambient AWS chain (env/role).
- **Consequence for this campaign (`CREDS=none`)**: every listing mode is
  **BLOCKED** — see § 8.

**Footguns**: (a) unconditional `GetBucketLocation` on every run — needs
credentials/permission and a **buggy** empty-region fallback (§ 6); (b)
`--debug`/`--trace` hide the actual object output; (c) `--output json` does
nothing in HEAD source; (d) no prefix scoping at all.

---

## 5. Output and observability

**Format** (single, fixed): `readObjectsV2` prints
`Object: %v \t %d \t %s\n` = `Object: <LastModified> <TAB> <size> <TAB> <key>`,
where `<LastModified>` is a Go `time.Time` via `%v`
(`2006-01-02 15:04:05.999999999 -0700 MST`) [SRC listObjectsV2.go:167]. Fields
exposed: **key, size, mtime**. **Not exposed: ETag, storage class.** The
`printf` inserts exactly **one** space after the final tab, so `normalize.sh`
strips that single delimiter space and **genuine leading spaces survive** — the
format is *not* lossy for leading-space keys (an earlier draft wrongly claimed
it was). The real adapter gap is **embedded newlines**: S3 keys may legally
contain `\n` [DOC https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-keys.html],
which pS3 prints into a `\n`-terminated line; the line-oriented adapter would
then split/drop such a key. Untested (the fixture has no newline key).

**`normalize.sh` contract** (this repo, `tools/ps3/normalize.sh`): parses
`^Object: ` lines → `key<TAB>size<TAB>-<TAB>mtime<TAB>-` (etag/storage_class
`-`; mtime canonicalized to `YYYY-MM-DDTHH:MM:SSZ`, UTC by construction since
`TZ=UTC`). Validated only against a synthetic fixture
(`receipts/smoke/_adapter/list-sample.txt`) because **no live pS3 listing could
be produced** (blocked). `list-object-versions`/`head-objects` line formats are
unverified (source absent); the adapter assumes the `list-objects-v2` format.

**Metrics/counters/logs**: an object count under `--debug`/`--trace`
(`debug: item count`), but that mode suppresses the listing itself. Per-request
visibility only via `--trace` (SDK-level errors, prefix trace lines). No
built-in API-call counter for the LIST path. Request-shape capture defers to the
study's replay-server phase.

---

## 6. Failure surface

- **Auth failure is silent/false-success in the common case** [OBS
  receipts/smoke/_capability/silent-empty-obs.md]: with no credentials and no
  config-file redirection, pS3 exits **0 with zero objects and no error** — a
  caller cannot distinguish this from an empty bucket. Under the harness's
  stricter starvation (config/creds files pointed at a nonexistent path) it
  instead fails at session creation, exit 1 (§ 8). [RUN]/[OBS]
- **GetBucketLocation region bug** [SRC listObjectsV2.go:107-117]: every run
  first calls `GetBucketLocation` (needs creds/permission). On error or a
  us-east-1 bucket, `getBucketLocation` returns `""`; the fallback then does
  `if location != fRegion { region = "" } else { region = "us-west-1" }` — so
  passing `--region us-east-1` sets the region to **empty**, and passing nothing
  yields **us-west-1** for a us-east-1 bucket. Confirmed in the trace: with
  `--region us-east-1`, `GetBucketLocation` → `NoCredentialProviders`, then
  `trace: bucket region is: ` (empty) [OBS silent-empty-obs.md].
- **Error-swallowing nil-deref risk** [SRC s3SDKfunctions.go:74-77]: the
  discovery helper returns `(resp, nil)` on error and callers dereference
  `resp.Contents` — a nil-pointer panic path. (Not triggered here; the run died
  earlier at session creation.)
- **Memory growth**: streaming producer→channel→printer bounds object memory;
  the prefix list is the only accumulation, bounded by `--prefix-count`. No OOM
  hypothesis at smoke scale; scale behavior is an open question. [SRC/INFERRED]
- **Inconsistent error handling** [SRC]: the *parallel pager* aborts the whole
  process on error (`log.Fatalln`, exit 1, [SRC listObjectsV2.go:316]); the
  *discovery* helper instead **swallows** errors (`return resp, nil`, [SRC
  s3SDKfunctions.go:74-77]). Neither path checkpoints partial progress.

All scale-dependent items above are **hypotheses** carried to the benchmark
phase (§ 10); nothing here settles them.

---

## 7. Container

**Distribution channels & architecture matrix** (first-class deliverable):

| Channel | Ships? | amd64 | arm64 |
| --- | --- | --- | --- |
| Upstream published image | **No** | — | — |
| Upstream Dockerfile | **No** | — | — |
| Prebuilt binary (`pS3.0-1-16`, committed) | **Yes** | **native** (ELF x86-64, static) | **no** |
| Source build | ships source but **does not compile** (§ `_build/`) and does not match the shipped binary | n/a | n/a |

Upstream ships neither image nor Dockerfile, so per the brief a Dockerfile is
written (`tools/ps3/Dockerfile`). Source build was attempted and **fails**
(missing `log`/`atomic` imports, unused `os`, and a `.`-for-`,` at
listObjectsV2.go:186 that makes `"debug: item count=".atomic` parse as a selector
and fail type-checking — a **compile error**, not strictly a syntax error — [RUN
receipts/smoke/_build/]); the binary additionally exposes subcommands whose
source is absent from the checkout ([RUN help/]), so the repo cannot reproduce it. The only working artifact is upstream's **committed prebuilt
binary**, so the image installs that (fetched by content hash
`sha256:3bc7bbbb…a9c2` from the pinned commit) onto
`debian@sha256:7b140f37…5818` (base pinned by digest) with `ca-certificates`,
`tzdata`, `TZ=UTC`, entrypoint `/usr/local/bin/pS3`.

- Built image: `ps3-study@sha256:c0d7b655163832bf769af0dd5da037c17f6b7b1b519724b8291297b5ae539663`, arch **amd64**.
- Build: `docker build --platform linux/amd64 -t ps3-study:0.1.16 tools/ps3/`.
- **Smoke ran the amd64 image under qemu emulation** on this arm64 runner
  (`emulated=yes` in every receipt). amd64 binfmt/qemu was not pre-registered on
  the box; it was enabled via `tonistiigi/binfmt --install amd64` before smoke.
  Emulation is smoke-only and **must not** carry into the benchmark phase, which
  needs one natively-common architecture — pS3 supports only **amd64** natively,
  a constraint for the cross-tool architecture decision (flag in § 10).

---

## 8. Smoke results

Pre-flight against the drift-checked manifest was **not run**: pS3 cannot
authenticate anonymously, so it can produce no listing to diff. The manifest
digest (`c78a8273…2adb`) is cited in the receipt regardless.

| Mode | Auth | Invocation | Exit | Wall | Verifier | Receipt |
| --- | --- | --- | --- | --- | --- | --- |
| `list` | anonymous (credential-starved) | `pS3 list-objects-v2 --bucket noaa-normals-pds --region us-east-1` | **1** | 0.271 s | n/a — **BLOCKED** (no output) | `receipts/smoke/_capability/list-anon/` |
| `list-object-versions` | anonymous | — | — | — | **BLOCKED** (same auth wall, [SRC] shared session path) | not run |
| `head-objects` | anonymous | — | — | — | **BLOCKED** (same auth wall) | not run |

**Capability finding [RUN receipts/smoke/_capability/list-anon/receipt.md]**:
under the harness's credential starvation the primary `list` mode exits **1**
with stderr `error: S3 session creation failed` and **zero** stdout — it fails
at S3 session creation **before issuing a single S3 API call** ([INFERRED] from
the error message + `session.NewSessionWithOptions` failing on the redirected
config path [SRC listObjectsV2.go:90-103] — no API observer was attached). The
wrapper receipt is **one** run [RUN]; determinism across **5/5** non-trace +
**3/3** `--trace` repeats is [OBS] from manual emulated re-runs. Measured process
`pS3` (not a shell — the RSS figure, 30.9 MB, is the tool's). `auth=anonymous`
enforced by the wrapper (credential values emptied, config/creds files →
nonexistent path).

**[OBS receipts/smoke/_capability/silent-empty/ + silent-empty-obs.md]**: with a
*bare* no-credentials env (only `AWS_EC2_METADATA_DISABLED=true`, no file
redirect) pS3 instead exits **0 with an empty listing and no error** — false
success (raw capture: `silent-empty/` — exit 0, 0-byte stdout, 0-byte stderr).
Not a wrapper receipt (the wrapper always applies its full starvation env, and
`[OBS]` is never a receipt); recorded as an honest observation of the emulated
binary. The exit-1 half (§ above) *is* a committed wrapper receipt; the exit-0
half is [OBS] only.

With `CREDS=none`, all three modes are **BLOCKED (untested-for-this-reason)**,
not skipped. No credentialed pass was run (`CREDS=none`, and the harness's
`--auth credentialed` is deliberately unimplemented).

**Edge-case fidelity checks: DEFERRED** (`EDGE_BUCKET=none`). The alphabet
coverage-gap (§ 2) and the embedded-newline adapter gap (§ 5) are exactly what an
edge fixture would exercise.

---

## 9. Notable findings

- **Novel-ish algorithm, dubious engineering.** The brute-force character-walk
  prefix discovery is a real, interesting idea for prefix-blind buckets [3P] —
  but the shipped code around it is rough: an error-swallowing helper that
  returns nil ([SRC s3SDKfunctions.go:74-77]), a region fallback that zeroes the
  region you asked for ([SRC :112-117]), an inert `--output json`, `--debug`
  that hides the very output you want, and a completeness gap for any key with an
  out-of-alphabet byte ([SRC root.go:36-39]).
- **The repo cannot build the tool it ships.** HEAD source has compile errors
  *and* is missing the source of three of the four subcommands present in the
  committed binary ([RUN help/, _build/]). The file-set mismatch is the fact;
  that the binary's tree is *newer* and was *never pushed* is [INFERRED]. The
  committed 18 MB binary is the real deliverable.
- **Silent false-success on auth failure** is the most dangerous behavior [OBS]:
  in the bare "forgot to set credentials" case pS3 prints nothing and returns 0.
  A pipeline consuming its output would silently process an empty set.
- **Concurrency is uncappable** — a 256 pager semaphore plus *unbounded*
  discovery goroutines, no throttle flag — polite-guest-hostile and uncappable,
  independent of the auth problem.
- **Provenance oddity**: `characters` includes `*` and `$` and even `space`,
  suggesting the author intended it to also catch keys starting with unusual
  bytes, yet the set still omits the majority of ASCII punctuation and all
  non-ASCII.

---

## 10. Open questions for the benchmark phase

Answerable only with credentials, at scale, natively — none settleable here:
1. **Does `--output json` work in the binary?** (Inert in HEAD source; binary
   diverges.) Verify before treating text/json as two output modes.
2. **`--prefix-count` sweep** (the primary knob). Proposed: 100 / 500 (default) /
   2000 / 10000 against a multi-million-key bucket — measure discovery-LIST
   overhead vs parallelism gained, and completeness at each.
3. **Correctness at scale**: does the 81-char alphabet drop keys on a bucket with
   out-of-alphabet/non-ASCII keys? Needs the edge fixture (currently `none`).
4. **Concurrency**: uncappable via flags (256 pager + unbounded discovery) — the
   benchmark must either patch the package vars or run pS3 out-of-band from the
   ≤8-capped tools, and cannot fairly compare it under the shared cap. Decide policy.
5. **Memory/throughput at scale** — the streaming model suggests bounded memory,
   but the in-memory prefix list and 256 workers are untested at millions of
   keys.
6. **Architecture**: pS3 is amd64-only natively. If the benchmark standardizes on
   arm64, pS3 needs a working source build first (currently impossible) or
   permanent emulation (disqualifying for timing).

---

## 11. Sources

**Primary (pinned checkout `@ 9428492`)** — `cmd/root.go`,
`cmd/listObjectsV2.go`, `cmd/s3SDKfunctions.go`, `cmd/httpUtils.go`,
`cmd/fmtUtils.go`, `main.go`, `LICENSE`.

**Docs**: none exist (no README/site/man). Shipped `--help` text (via [RUN]) is
the only in-tool documentation.

**Third-party**:
- Author blog — J-B Thomas, "Fast listing S3 objects from buckets with
  millions/billions of items", medium.com/@jboothomas,
  https://jboothomas.medium.com/fast-listing-s3-objects-from-buckets-with-millions-billions-of-items-380052fb6faf
  (accessed 2026-07-17). Algorithm description + author-claimed local-S3
  benchmark (160 s vs aws 1110 s vs s5cmd 733 s, 15 M objects).

**Pinned SHA**: `9428492291ef3aa824dba0b495583279c3d33760`.
**Image**: `ps3-study@sha256:c0d7b655163832bf769af0dd5da037c17f6b7b1b519724b8291297b5ae539663` (amd64).
**Binary sha256**: `3bc7bbbb0d45d6f96b0130f98859ea7cfe693b13a8438461240423511322a9c2`.

**Receipt index**:
- `receipts/smoke/_capability/list-anon/` — [RUN] credential-starved `list`, exit 1, wrapper receipt.
- `receipts/smoke/_capability/help/help.txt` — [RUN] `--version` + all `--help` output (backs the subcommand/flag-surface claims).
- `receipts/smoke/_capability/silent-empty/` — [OBS] raw exit-0 capture (bare no-creds).
- `receipts/smoke/_capability/silent-empty-obs.md` — [OBS] credential-absent behavior matrix + trace evidence.
- `receipts/smoke/_build/` — [RUN] source compile-failure attempt.
- `receipts/smoke/_adapter/list-sample.txt` — normalize.sh fixture.

---

### Evidence-label ledger (quick audit)
- `[RUN]`: version/help output; credential-starved `list` (exit 1); source build failure.
- `[OBS]`: credential-absent silent-empty (exit 0) and trace-level region/GetBucketLocation behavior — real emulated runs the wrapper did not record.
- `[SRC]`: algorithm, tunables, consts, bugs — all against `@ 9428492`.
- `[3P]`: author blog only.
- `[INFERRED]`: session-creation failure mechanism; coverage gap consequences.
