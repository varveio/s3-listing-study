# s4cmd — independent research report

Groundwork for the s3-listing-study. Derived independently from primary sources
(the tool's own docs, its source at a pinned commit, third-party accounts, and
my own runs). Every behavioral claim carries an evidence label; unlabeled
behavioral claims are defects.

Phase: workspace phase (Stages A-C). **Status: finalize-early - capability
block.** s4cmd cannot make unsigned/anonymous requests, and `CREDS=none`, so
every listing mode is *blocked, not skipped*. The block is demonstrated under
the harness wrapper and its cause is anchored in source. See section 8.

---

## 1. Metadata

| | |
| --- | --- |
| Tool | s4cmd - "Super S3 command line tool" |
| Upstream | https://github.com/bloomreach/s4cmd (canonical; author Chou-han Yang, maintained under the bloomreach org) |
| Pinned tag | `2.1.0` (latest release tag; `2.0.1` precedes it) |
| Pinned commit | `80059bfa4451f513a8f314fb6300e5ecc51587b2` |
| Language | Python (single file `s4cmd.py`, ~1950 lines) `[SRC s4cmd.py @ 80059bf]` |
| License | Apache-2.0 `[SRC LICENSE @ 80059bf]` |
| Runtime deps | `boto3>=1.3.1`, `pytz>=2016.4` `[SRC setup.py:52 @ 80059bf]` |
| Image | self-built (`tools/s4cmd/Dockerfile`); `localhost:5000/s4cmd-study@sha256:d458ef5096180e517840712e29b0b8705ec97cebf48f717cad2fea3805105813`, arm64 |
| Date | 2026-07-17 |

**Upstream health.** The `2.1.0` tag is dated 2018-08-14 `[SRC git tag @ 80059bf]`
and its history holds 132 commits. The default branch is only 14 commits further
(146), the newest dated 2024-07-21 (`Add --ignore-certificate`), with sparse
activity in between — a mix of dependabot bumps, a repology badge, and a
maintainer-list edit `[SRC git log]`. The project is effectively dormant: the last
*released* version is ~8 years old. (It still installs and runs under a current
boto3 — see section 7's retraction — so "dormant" is about cadence, not
brokenness.) Treat it as legacy/maintenance-mode.

---

## 2. How it works

**Listing command.** `s4cmd ls [path]` lists a path; `du [path]` aggregates size
over the same walk. The listing engine is `S3Handler.s3walk` (the driver,
s4cmd.py:704) which dispatches into the thread-pool worker
`ThreadUtil.s3walk` (s4cmd.py:1167). `[SRC s4cmd.py:704,1167 @ 80059bf]`

**Listing IS parallelized - and by client-side directory recursion, not
sharding.** The worker issues, per node:

```python
paginator = self.s3.get_paginator('list_objects')
for page in paginator.paginate(Bucket=..., Prefix=s3dir, Delimiter=PATH_SEP,
                               PaginationConfig={'PageSize': 1000}):
    for obj in page.get('CommonPrefixes') or []:  # subdirs -> recurse
        ... self.pool.s3walk(s3url, obj_name, filter_path, result)
    for obj in page.get('Contents') or []:        # files at this level
        ...
```
`[SRC s4cmd.py:1173-1206 @ 80059bf]`

Two load-bearing consequences:

1. **It always sends `Delimiter='/'`, even in recursive mode.** `ls -r` does not
   issue a flat delimiter-less scan. It walks the pseudo-directory tree: each
   `CommonPrefix` it discovers is re-queued as a **new task on the thread pool**
   (`self.pool.s3walk(...)`, s4cmd.py:1185), so a recursive listing issues **one
   paginated `list_objects` per pseudo-directory** in the subtree.
   `[SRC s4cmd.py:1176,1184-1185 @ 80059bf]` For a broad tree (e.g. the smoke
   bucket: 4 top prefixes -> date-range dirs -> `access/` leaves) this is
   **hundreds to thousands** of LIST requests, versus ~149 for a flat
   delimiter-less scan of the same 148,917 keys `[DOC registry]`. It trades
   request *count* for parallelism. `[INFERRED from SRC]`
2. **Parallelism granularity = the tree's branching.** Concurrency is bounded by
   how many distinct directories exist to fan out over, not by a fixed shard
   count. A prefix holding a million keys under no sub-delimiter is **one serial
   paginated scan on one thread**; a wide/deep tree parallelizes across
   `num_threads` workers. `[INFERRED from SRC s4cmd.py:1184-1185]`

**API version.** Legacy `list_objects` (**v1**), not `list_objects_v2`
`[SRC s4cmd.py:1173 @ 80059bf]`.

**Page size.** Fixed `PageSize=1000`, hardcoded in the paginate call; there is no
CLI flag to change it `[SRC s4cmd.py:1176 @ 80059bf]` (confirmed absent from
`--help` `[RUN _build/build.md]`).

**Pagination / truncation.** Delegated entirely to boto3's paginator, which
follows `NextMarker`/`IsTruncated` internally; s4cmd consumes pages as an
iterator `[SRC s4cmd.py:1176 @ 80059bf]`.

**Memory model - accumulate-then-sort-then-dump.** Every matching object is
appended to a shared `result` list (`conditional`, s4cmd.py:1208-1223); after the
whole walk joins, `pretty_print` sorts the entire list (directories first, then
by name) and only then prints `[SRC s4cmd.py:722-741,1592 @ 80059bf]`. Nothing is
streamed; peak memory scales with the **total number of keys returned**. The
README states this plainly: *"Listing large number of files with S3 pagination,
with memory is the limit."* `[DOC README.md]` This is the headline scale risk
(section 6, section 10). `[INFERRED from SRC + DOC]`

**Ordering.** Output is **sorted client-side** (dirs-first, then lexicographic by
full `s3://` name), so it does not reflect S3's return order
`[SRC s4cmd.py:735-741 @ 80059bf]`.

**Retry / backoff / timeout.** The pool worker retries **only**
`S3RetryableErrors` = `socket.timeout`, `ConnectionError`,
`urllib3 ReadTimeoutError`, `botocore IncompleteReadError`
`[SRC s4cmd.py:271-276 @ 80059bf]`, up to `--retry` (default **3**) times with
`--retry-delay` (default **10s**) between, by re-queuing the task with an
incremented retry counter `[SRC s4cmd.py:529-539,1853,1856 @ 80059bf]`. Any other
exception (including credential errors and, notably, **HTTP 503 SlowDown**, which
is a `ClientError`, not in that tuple) terminates the pool immediately
`[SRC s4cmd.py:520-542 @ 80059bf]`. Throttling retries, if any, come only from
botocore's own built-in retry layer, not from s4cmd. `[INFERRED from SRC]`

**Resume / checkpoint.** None for listing. A killed `ls` starts from scratch;
there is no marker/continuation persistence `[INFERRED - no resume code in the
listing path]`.

---

## 3. Modes and tunables

A *mode* changes the request pattern or output contract; a *tunable* changes only
magnitude.

### Listing modes

| Mode | Invocation | Request pattern / output contract | Evidence |
| --- | --- | --- | --- |
| recursive | `ls -r <url>` | Full subtree; delimiter walk with per-directory fan-out; prints one line per object | `[SRC s4cmd.py:1184 @ 80059bf]` |
| shallow (delimiter) | `ls <url>` | One level: immediate objects + subdirs shown as `DIR` | `[SRC s4cmd.py:1200,1184 @ 80059bf]` |
| show-directory | `ls -d <url>` | The directory entry itself instead of its contents | `[SRC s4cmd.py:704-710,1187 @ 80059bf]` `[DOC README]` |
| du | `du -r <url>` | Same recursive walk; output is aggregate size, not per-key | `[SRC s4cmd.py:1734 @ 80059bf]` `[DOC README]` |

All four ride the same `s3walk`/`list_objects` engine; they differ in recursion
depth (`-r`), whether directories are emitted (`-d`), and output contract (`du`).
Wildcards (`*`, `?`, multi-level) are supported and expand through the same walk
`[DOC README]` - a wildcard is a filter over the walk, not a distinct request
mode `[SRC s4cmd.py:1156-1164 @ 80059bf]`.

### Tunables (benchmark must sweep)

| Flag | Default | Effect | Evidence |
| --- | --- | --- | --- |
| `-c/--num-threads` | **`cpu_count * 4`** | Thread-pool size = max concurrent `list_objects` calls. **Sweep this.** | `[SRC s4cmd.py:121,1859 @ 80059bf]` |
| `S4CMD_NUM_THREADS` (env) | - | Same knob via env | `[SRC s4cmd.py:121 @ 80059bf]` |
| `-t/--retry` | 3 | Retry count for socket/timeout errors | `[SRC s4cmd.py:1853 @ 80059bf]` |
| `--retry-delay` | 10 | Seconds between retries | `[SRC s4cmd.py:1856 @ 80059bf]` |
| `--endpoint-url` | none | boto3 endpoint override (non-AWS stores) | `[SRC s4cmd.py:1867 @ 80059bf]` |
| `--last-modified-before/-after` | none | Client-side time filter over walk results | `[SRC s4cmd.py:1210-1221 @ 80059bf]` |
| PageSize | 1000 (hardcoded) | LIST page size - **not a flag** | `[SRC s4cmd.py:1176 @ 80059bf]` |

**Concurrency footgun for this campaign:** the default `cpu_count*4` is **32** on
the 8-core runner - 4x my `CONCURRENCY_CAP=8`. Any real run must pin `-c <= 8`
(the smoke `run.sh` pins `-c 4`). `[SRC s4cmd.py:121 @ 80059bf]`

---

## 4. How to run it properly

**Quickstart (credentialed - the only working mode):**
```sh
export S3_ACCESS_KEY=... S3_SECRET_KEY=...   # or ~/.s3cfg, or an EC2 IAM role
s4cmd ls -r s3://bucket/prefix/
```
`[DOC README.md]`

**Auth setup.** Credentials resolve in order: `--access-key/--secret-key` ->
`S3_ACCESS_KEY`/`S3_SECRET_KEY` env -> `~/.s3cfg` (s3cmd's file)
`[SRC s4cmd.py:664-668,624-659 @ 80059bf]`. If none of those are set, the boto3
client is built with defaults and boto3's own credential chain (env/config/IAM
role) applies `[SRC s4cmd.py:385-386 @ 80059bf]`.

**Anonymous / unsigned access: NOT SUPPORTED.** There is no `--no-sign-request`
equivalent, no config, no env. The boto3 client is constructed without
`signature_version=UNSIGNED`, so a credential-less run cannot list a public
bucket - it fails (section 8). `[SRC s4cmd.py:380-386 @ 80059bf]` `[3P
github.com/bloomreach/s4cmd/issues/139]`

**Best-practice large-listing config (per the project's own guidance).** The docs
offer no explicit large-listing tuning beyond "memory is the limit" and the
thread count `[DOC README.md]`. Practically: raise `-c` to widen fan-out, and
expect memory to scale with key count. There is **no** hinted/two-pass workflow
and **no** resume `[INFERRED - none in docs or source]`.

**Debugging.** `s4cmd --debug ls ... 2>&1 >/dev/null | grep S3APICALL` prints the
tool-wrapped boto3 calls with their parameters `[DOC README.md]` `[SRC
s4cmd.py:400-401 @ 80059bf]`. **Important limit (see section 5):** for listing
this logs `get_paginator` (once per pseudo-directory) — **not** the per-page
`list_objects` requests, which the paginator issues against the raw boto3 client,
bypassing the wrapper. `--debug` also does not enable botocore's own wire logging
(it raises only s4cmd's own logger, s4cmd.py:102), so actual LIST *pages* are not
observable this way `[SRC s4cmd.py:102,393-408,1173-1176 @ 80059bf]`.

**Footguns.** (a) Prefix matching is Unix-shell-like, **not** s3cmd
prefix-matching: `s4cmd ls s3://b/ch` returns nothing; use `ch*` `[DOC README.md]`.
(b) `-r` still uses delimiter recursion, so it is request-heavy on deep trees
(section 2). (c) Default thread count is host-CPU-dependent (`cpu_count*4`) - non-obvious
and can be aggressive `[SRC s4cmd.py:121]`.

---

## 5. Output and observability

**`ls` output format** (`pretty_print`, s4cmd.py:1592): space-aligned columns
`<mtime> <size> <name>`, where `<name>` is the full `s3://bucket/key` URL
(left-justified, last column), `<size>` is bytes or the literal `DIR`, and
`<mtime>` is `TIMESTAMP_FORMAT = "%04d-%02d-%02d %02d:%02d"` - **minute
precision, no seconds, no zone marker** `[SRC s4cmd.py:55,1592-1622 @ 80059bf]`.
There is **no** JSON/CSV/other output format `[SRC s4cmd.py:1592 @ 80059bf]`.

**`normalize.sh` contract** (`tools/s4cmd/normalize.sh`), per mode:

| Mode | key | size | etag | mtime | storage_class |
| --- | --- | --- | --- | --- | --- |
| recursive | yes (from `s3://b/key`) | yes (bytes) | `-` | `-` | `-` |
| shallow | yes | yes / `DIR`->`-` | `-` | `-` | `-` |
| show-directory | yes | yes / `DIR`->`-` | `-` | `-` | `-` |
| du | (aggregate size - no per-key output; normalize is a no-op) | | | | |

`etag` and `storage_class` are **never** printed by `ls`, so both are `-`.
**`mtime` is `-` deliberately:** `ls` exposes only minute precision, so the
contract-v2 canonical `...:SSZ` value is not derivable - emitting a fabricated
`:00` would risk a false verifier verdict. The printed value is UTC only because
botocore hands `pretty_print` a tz-aware UTC datetime whose fields are formatted
as-is — there is **no** timezone conversion, and `TZ=UTC` does not affect this
field `[SRC s4cmd.py:1602 @ 80059bf]`. The adapter parses by locating the first
`s3://` in each line (keys are absolute, so `prefix` is unused) and taking the
last whitespace token before it as size. It was exercised on **synthetic
fixtures** with checked-in expected outputs (`receipts/smoke/_adapter/`,
`expected-*.tsv`); this is a construction check of the parser, **not** a `[RUN]`
against real tool output — no listing mode could be executed (section 8). Adapter
fidelity is bounded by the tool: s4cmd `rstrip()`s each line (s4cmd.py:1622), so
trailing-whitespace and newline-bearing keys are not representable `[SRC
s4cmd.py:1622 @ 80059bf]`.

**Metrics/counters.** No built-in listing counter or throughput metric. Progress
is a stderr heartbeat `[N task(s) completed, M remaining, K thread(s)]`
`[SRC s4cmd.py:605-613 @ 80059bf]`. `--debug`'s `S3APICALL` lines expose only the
**tool-wrapped** calls (`get_paginator`, `head_object`, …), so for listing they
count **pseudo-directories walked**, not LIST pages fetched; the per-page
`list_objects` requests bypass the wrapper and are invisible to `--debug`
`[SRC s4cmd.py:102,393-408,1173-1176 @ 80059bf]`. Per-page LIST counting is
deferred to the study's replay-server phase.

---

## 6. Failure surface

- **Memory growth (scale risk).** Accumulate-then-sort-then-dump means an `ls -r`
  of a very large keyspace holds every key in memory before printing; the docs
  concede "memory is the limit" `[DOC README.md]` `[SRC s4cmd.py:722-741,1208-1223
  @ 80059bf]`. Whether this OOMs at N keys is scale-dependent - an **open
  question**, not settleable at smoke scale.
- **Request amplification on deep trees.** Delimiter recursion issues one LIST
  per pseudo-directory; a pathological deep/wide tree can produce far more LIST
  calls (and more throttling exposure) than a flat scan `[INFERRED from SRC
  s4cmd.py:1184-1185]`.
- **Throttling handling.** HTTP 503 SlowDown is not in s4cmd's retryable set
  `[SRC s4cmd.py:271-276 @ 80059bf]`; only botocore's built-in retry would absorb
  it. Under heavy fan-out this could surface as a hard failure `[INFERRED]`.
- **Interruption.** No resume; the README claims clean interruption without
  corrupt state for transfers `[DOC README.md]`, but for `ls` an interrupt simply
  loses all progress `[INFERRED]`.
- **Retry can duplicate keys.** The pool retries a failed task by re-queuing the
  whole directory from page one (`retry+1`, s4cmd.py:538), but objects already
  appended to the shared `result` and child-directory tasks already queued from
  earlier pages are **not rolled back** `[SRC s4cmd.py:1195-1206,1184-1185,529-539
  @ 80059bf]`. A retryable error mid-directory followed by a successful retry can
  therefore emit duplicate keys and re-walk subtrees `[INFERRED from SRC]`. (The
  verifier counts duplicates, so this would surface as a `FAIL` at benchmark
  scale — an open question, not observed at smoke.)
- **Endpoint quirks.** `--endpoint-url` exists for non-AWS stores `[SRC
  s4cmd.py:1867 @ 80059bf]`; region has **no flag** and is left to boto3 (section 7).

---

## 7. Container

**What image, and why.** Upstream ships **no published image and no Dockerfile**:
the repo at the pinned commit contains none `[SRC — repo file listing @ 80059bf]`,
and PyPI/GitHub/Docker Hub show only community images, not a bloomreach one
(`victorlap/s4cmd`, `poldracklab/s4cmd`; `graymic/s4cmd`) `[3P — see Sources for
URLs]`. Per the brief's "neither image nor Dockerfile" case I wrote
`tools/s4cmd/Dockerfile`: base `python:3.7-slim` pinned by digest
`sha256:b53f496ca43e5af6994f8e316cf03af31050bf7944e0e4a308ad86c001cf028b`, s4cmd
installed from the exact pinned commit, `ENTRYPOINT ["s4cmd"]`. Built digest
`sha256:d458ef5096180e517840712e29b0b8705ec97cebf48f717cad2fea3805105813`
(`receipts/smoke/_build/build.md`).

**Correction — the boto3 pin is NOT required (an earlier claim of mine,
retracted).** My Stage A notes asserted s4cmd 2.1.0 "won't import under a current
boto3" because `s4cmd.py:274` references `botocore.vendored.requests`, widely
reported removed in botocore 1.13.0 (2019). **That claim is false** — I never
tested it in Stage A, and on verification it does not hold: `pip install s4cmd`
imports and runs `s4cmd --version` cleanly under **botocore 1.33.13** (Py 3.7) and
the **latest botocore 1.43.50** (Py 3.12) — `botocore.vendored.requests` is still
present `[RUN receipts/smoke/_build/modern-boto3-import/{transcript,transcript-py312}.txt]`.
The image's `boto3==1.9.253`/`botocore==1.12.253` pin is therefore a
**reproducibility choice, not a necessity**; it does pin the smoke run to a
2018-era boto3, which the benchmark phase should reconsider (see section 10).
`[SRC s4cmd.py:274 @ 80059bf]` `[RUN _build/modern-boto3-import/]`

**Architecture matrix.**

| Channel | amd64 | arm64 | Notes |
| --- | --- | --- | --- |
| Upstream image | - | - | none published |
| Upstream Dockerfile | - | - | none |
| Prebuilt binary | - | - | none (pure Python) |
| Source (pip) | native | native | arch-independent; rides the multi-arch Python base image |

s4cmd is pure Python, so it is architecture-neutral: it runs natively on whichever
arch the Python base supports (both). **Smoke ran on arm64, native, no emulation**
(runner is aarch64). amd64 is the expected benchmark common denominator and s4cmd
supports it natively - flagged in Open questions only for consistency.

**First execution** (in-container): `--version` -> `s4cmd version 2.1.0` (exit 0);
`--help` matches the Stage A doc reading; no unsigned flag, no page-size flag
`[RUN receipts/smoke/_build/build.md]`.

---

## 8. Smoke results

**Auth reality: every listing mode is BLOCKED (not skipped).** s4cmd has no
unsigned/anonymous mechanism and `CREDS=none`, so no listing mode can run against
the anonymous smoke bucket. This is a capability finding, demonstrated under the
wrapper, with source cause.

**Pre-flight (bucket is not at fault).** An anonymous scoped list of
`normals-hourly/` with the pinned harness client (`--no-sign-request`) returns
keys, exit 0 `[RUN receipts/smoke/_capability/preflight-anon/ — meta.md + stdout.txt]`.
The bucket is live and anonymously listable; the failure below is s4cmd's. (A full-manifest pre-flight
diff was not run because no s4cmd mode can consume the manifest - the tool cannot
list anonymously at all.)

**Capability probe (canonical receipt).**
`receipts/smoke/_capability/anon-nocredentials/` -

| | |
| --- | --- |
| Invocation | `s4cmd ls -r -c 4 s3://noaa-normals-pds/normals-hourly/` (via `run.sh recursive`) |
| Auth | `anonymous` (credential-starved, enforced by wrapper) |
| Exit | **1** |
| Wall-clock | 0.212 s |
| Verifier | not run - capability probe, s4cmd produced **no** keys |
| Failure | `botocore.exceptions.InvalidConfigError` at `BotoClient.__init__` (s4cmd.py:386), i.e. **before any S3 request** |

The exact exception text ("assume role with web identity but has no role ARN
configured") is an interaction with the wrapper's credential neutralization,
which sets `AWS_WEB_IDENTITY_TOKEN_FILE=/nonexistent-by-harness` and empties
`AWS_ROLE_ARN`, activating botocore's web-identity provider. The root cause is
s4cmd's: with no `signature_version=UNSIGNED`, boto3 resolves credentials eagerly
at client construction and s4cmd cannot even build a client credential-starved.

**General bare-env case (`[OBS]`, non-wrapper).**
`receipts/smoke/_capability/direct-bare-env.stderr.txt` - same image, only
`AWS_EC2_METADATA_DISABLED=true` set. The client constructs, then the
`list_objects` call inside the s3walk worker fails with `Unable to locate
credentials` (botocore `NoCredentialsError`), surfaced as `[Thread Failure]`,
exit 1. The `list_objects` call runs inside the s3walk **worker thread**, so the
`NoCredentialsError` is caught by the worker's generic `except Exception`
(s4cmd.py:540) and surfaced as `[Thread Failure]` (s4cmd.py:469) — it does **not**
reach the main-thread `except BotoClient.NoCredentialsError` at line 1933 (that
handler only fires for errors raised on the main thread). Recorded `[OBS]` because
it did not go through `smoke-run.sh`; binding metadata in
`receipts/smoke/_capability/OBS-probes.md`. `[SRC s4cmd.py:540,469,1933 @ 80059bf]`

**Per-mode status.** recursive is **settled by run** (the committed capability
receipt). shallow / show-directory / du are **blocked by source inference**: each
`*_handler` builds the same credential-less `BotoClient` via the shared
`s3handler()`/`S3Handler.connect()` path (s4cmd.py:1557,1563,674-688), so the same
construction failure applies — an `[INFERRED]` extension, not four independent
receipts.

**Edge-case checks:** `EDGE_BUCKET=none` -> **deferred** (unicode/weird-key/multipart-ETag).

**Request-behavior observations:** none capturable - s4cmd issued **zero** S3
API calls (failed before the first request). Where it *can* run credentialed,
`--debug`'s `S3APICALL` lines expose only tool-wrapped calls (`get_paginator`
once per pseudo-directory, not per-page `list_objects`) — see section 5
`[SRC s4cmd.py:102,393-408 @ 80059bf]`.

---

## 9. Notable findings

- **`ls -r` is a delimiter walk, not a flat scan.** The single most important
  architectural fact: s4cmd always sends `Delimiter='/'` and recurses
  client-side, re-queuing each discovered directory onto the thread pool. It
  parallelizes listing (unlike many serial-paginator CLIs) but pays in LIST
  request count on deep trees - one paginated LIST per pseudo-directory. `[SRC
  s4cmd.py:1176,1184-1185 @ 80059bf]`
- **Legacy `list_objects` (v1)**, not v2 - a 2018-era choice never updated `[SRC
  s4cmd.py:1173 @ 80059bf]`.
- **Retry-induced duplication (hypothesis).** Whole-directory requeue on a
  retryable error without rolling back already-appended objects/children can
  duplicate keys on successful retry — see section 6 `[SRC s4cmd.py:529-539,1195-1206
  @ 80059bf]`.
- **No unsigned mode**, ~8 years after users asked (issue #139, opened 2018-10)
  `[3P]`.
- **A near-miss false finding (self-corrected).** I initially believed the
  `botocore.vendored.requests` reference (s4cmd.py:274) broke import under modern
  boto3; testing showed it does not (imports/runs under botocore 1.43.50). Logged
  as a caution: the study exists to catch exactly this kind of untested inherited
  assumption. `[RUN _build/modern-boto3-import/]`
- **Client-side sort** means `ls` output is not in S3 return order, and the whole
  result set is materialized to sort it `[SRC s4cmd.py:735-741 @ 80059bf]`.
- **Default concurrency is host-dependent** (`cpu_count*4`) - reproducibility and
  politeness footgun `[SRC s4cmd.py:121 @ 80059bf]`.
- **No region flag.** Region is entirely boto3's to resolve; unusual for a
  purpose-built S3 CLI `[SRC s4cmd.py - no region option @ 80059bf]`.
- **`mtime` at minute precision only** in `ls` - coarser than the S3
  `LastModified` it comes from, an information loss in the output layer `[SRC
  s4cmd.py:55,1602 @ 80059bf]`.

---

## 10. Open questions for the benchmark phase

Answerable only with credentials + scale (all **require credentials** - s4cmd
cannot be benchmarked anonymously):

1. **Memory ceiling.** At what key count does accumulate-then-sort OOM, and how
   does peak RSS scale with N? (Docs say "memory is the limit.") Sweep listings
   of increasing prefix size; capture `peak_rss` and `cgroup_peak_mem`.
2. **Fan-out parallelism vs request amplification.** How does wall-clock scale
   with `-c` on a broad tree, and how many LIST *pages* does `ls -r` actually issue
   vs a flat scan? Sweep `-c` in {1,2,4,8,16,32} (respecting the aggregate cap).
   **Note:** `grep S3APICALL` will **not** count LIST pages (it logs
   `get_paginator` once per directory, not per-page `list_objects`; s4cmd's
   `--debug` does not raise botocore's logger) — use the replay-server phase or a
   network capture for true page counts.
   Also revisit the **boto3 version**: the smoke image pins 2018-era
   boto3/botocore for reproducibility only (not a compatibility need); the
   benchmark should run a current boto3, whose retry/pooling behavior differs.
3. **Parallelism on a flat prefix.** Confirm the hypothesis that a single
   sub-delimiter-free prefix collapses to one serial thread regardless of `-c`.
4. **Throttling behavior** under aggressive fan-out (503 SlowDown is not in
   s4cmd's own retry set).
5. **Client-CPU cost** of per-line formatting + full sort at large N (capture CPU
   time alongside wall-clock, per the harness's cross-internet caveat).
6. **Architecture:** run on amd64 (native support confirmed) for the common
   denominator; no s4cmd-specific arch risk expected (pure Python).

---

## 11. Sources

**Primary (pinned checkout, commit `80059bfa4451f513a8f314fb6300e5ecc51587b2`):**
- `s4cmd.py`, `setup.py`, `README.md`, `CHANGELOG.md`, `LICENSE` - accessed 2026-07-17.

**Docs:**
- `[DOC]` README.md (in-repo) - https://github.com/bloomreach/s4cmd/blob/2.1.0/README.md - accessed 2026-07-17.

**Third-party:**
- `[3P]` s4cmd issue #139 - `--no-sign-request` equivalent request - opened
  2018-10-23 - https://github.com/bloomreach/s4cmd/issues/139 - accessed 2026-07-17.
- `[3P]` community images (no bloomreach/upstream image): https://hub.docker.com/r/victorlap/s4cmd ,
  https://hub.docker.com/r/poldracklab/s4cmd (and `graymic/s4cmd`) - accessed 2026-07-17.
- `[3P]` botocore's 2019 removal of the *vendored requests library*
  (https://aws.amazon.com/blogs/developer/removing-the-vendored-version-of-requests-from-botocore/) —
  cited only for context. NOTE: empirically the `botocore.vendored.requests`
  attribute path still resolves in current botocore (1.43.50), and s4cmd 2.1.0
  imports/runs; the earlier "import breakage" claim is **retracted** (see section 7).

**Receipts (this tree, `receipts/smoke/`):**
- `_build/build.md` - image build + first-execution checks.
- `_build/modern-boto3-import/{transcript,transcript-py312}.txt` - `[RUN]`
  s4cmd installs and runs under current boto3 (retraction evidence).
- `_capability/anon-nocredentials/` - wrapper receipt of the credential-starved
  block (`receipt.md`, `run.meta`, `stdout.txt`, `stderr.txt`).
- `_capability/preflight-anon/` - `[RUN]` harness-client anonymous list proving
  bucket accessibility (`meta.md`, `stdout.txt`).
- `_capability/direct-bare-env.stderr.txt`, `_capability/obs-multiprefix.stderr.txt`
  - `[OBS]` probes, bound in `_capability/OBS-probes.md`.
- `_capability/NOTES.md` - capability finding narrative.
- `_adapter/` - synthetic normalize.sh fixtures with expected outputs.

**Pinned image:** `localhost:5000/s4cmd-study@sha256:d458ef5096180e517840712e29b0b8705ec97cebf48f717cad2fea3805105813`.
