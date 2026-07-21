# rclone — running it

What was selected and built, how the image was pinned, every mode that ran or was
blocked, what the harness verified, and how to reproduce it. Canonical tested
identity lives in [`../data/tool.json`](../data/tool.json); evidence labels and
claim references are as in [`mechanism.md`](mechanism.md).

## Image

Upstream `rclone/rclone`, pinned by **manifest-list digest**:

```
rclone/rclone@sha256:c61954aaa32328a5486715dd063a81c7879f5195ad3505cd362deddd509dc4a1
```

This is upstream's own published multi-arch image (tag `1.74.4`) [DOC
hub.docker.com/r/rclone/rclone], matching the normal packaged setup, so no
self-built Dockerfile is staged (this capsule has no `build/`). Entrypoint is
`["rclone"]`, so every `../adapter/run.sh` argv below starts at the subcommand,
never at the binary.

**Tool version:** `rclone v1.74.4` in the image (`os/version: alpine 3.24.1`,
`go/version: go1.26.5`, static) [RUN `../receipts/smoke/_build/version.md`] — the
tool's own self-report, which is the canonical provenance for the tested version
in [`../data/tool.json`](../data/tool.json); it links the image bytes to the pinned
checkout `5bc93a2a7`.

**Architecture:** native on **both amd64 and arm64** on every channel — upstream
image (manifest list `c619…`: amd64 `sha256:cdbecba0…`, arm64 `sha256:7d8906d4…`,
plus `386`/`arm/v6`/`arm/v7`), prebuilt release binaries, and a pure-Go source build
[RUN `docker buildx imagetools inspect`; DOC rclone.org/downloads; SRC Dockerfile @
5bc93a2a7]. Smoke ran on **arm64** (image arm64 on host aarch64, not emulated) [RUN
`../receipts/smoke/*`]. amd64 is the expected benchmark choice — no
common-denominator problem.

## Avoiding per-object HEAD requests

Every `lsjson` mode carries `--use-server-modtime --no-mimetype`. Without them,
rclone HEADs **every object** to compute `ModTime`/`MimeType` [SRC `backend/s3/s3.go`
ModTime/MimeType, `fs/operations/lsjson.go:181-185` @ 5bc93a2a7] — turning a
~149-page listing into 148,917 HEADs (claim `head-per-object-storm-mechanism`; see
[`mechanism.md`](mechanism.md#the-head-per-object-footgun)). `lsf` avoids it by
construction as long as the `t`/`h` format codes are absent. Measuring rclone
"listing" without these flags measures a HEAD storm, not a listing. The suppressed
correct path is receipt-backed on every lsjson receipt (`fields=0`; claim
`head-per-object-suppressed-at-smoke`).

## Every smoked mode

All modes ran **unsigned** (`auth=anonymous` — the wrapper starves every `AWS_*`
credential and points config/credential files at a nonexistent path; rclone installs
`aws.AnonymousCredentials{}` when no keys are set and `env_auth` is false [SRC
`backend/s3/s3.go:1508-1511`], claim `anonymous-is-absence-of-credentials`) against
`noaa-normals-pds` (us-east-1) at its 2026-07-17 snapshot (148,917 keys, manifest
sha256 `c78a827…992adb`). `EDGE_BUCKET=none`, so unicode/weird-key/multipart-ETag
fidelity checks are deferred. Argv is shown after the `rclone` entrypoint; the
connection-string remote is `:s3,provider=AWS,region=us-east-1:BUCKET[/PREFIX]`.

| Mode | Invocation (argv after `rclone`) | Scope | Exit | Wall | Verdict | Receipt |
| --- | --- | --- | --- | --- | --- | --- |
| recursive-fastlist (flat `ListR`) | `lsjson --fast-list --files-only --use-server-modtime --no-mimetype -R :s3,…:BUCKET` | full | 0 | 16.95s | PASS 148917/148917 | `../receipts/smoke/recursive-fastlist` |
| recursive-fastlist | `… -R :s3,…:BUCKET/normals-monthly/1991-2020/` | prefix | 0 | 1.74s | PASS 15625 | `../receipts/smoke/recursive-fastlist-monthly1991` |
| recursive-fastlist | `… -R :s3,…:BUCKET/normals-annualseasonal/1981-2010/access/` | prefix | 0 | 1.26s | PASS 9839 | `../receipts/smoke/recursive-fastlist-access` |
| **recursive-walk** (genuine hierarchical walk) | `lsjson --files-only --use-server-modtime --no-mimetype --disable ListR --checkers 4 -R :s3,…:BUCKET/normals-annualseasonal/1981-2010/` | prefix | 0 | 1.39s | **PASS 9841/9841** | `../receipts/smoke/recursive-walk` |
| ~~recursive-hierarchical~~ (**mislabeled** — a flat `ListR`, `--checkers` inert) | `lsjson --files-only --use-server-modtime --no-mimetype --checkers 4 -R :s3,…:BUCKET/normals-hourly/` | prefix | 0 | 0.53s | PASS 2549 — **but see correction block** | `../receipts/smoke/recursive-hierarchical` |
| delimiter-shallow | `lsjson --use-server-modtime --no-mimetype :s3,…:BUCKET` | delimiter `/` | 0 | 0.17s | PASS 5 (4 CommonPrefixes + `index.html`) | `../receipts/smoke/delimiter-shallow` |
| listv1 (legacy API) | `lsjson --fast-list --files-only --use-server-modtime --no-mimetype -R :s3,…,list_version=1:BUCKET/normals-hourly/` | prefix | 0 | 1.36s | PASS 2549 | `../receipts/smoke/listv1` |
| lsf | `lsf --fast-list --files-only --format ps --separator ";" -R :s3,…:BUCKET/normals-hourly/` | prefix | 0 | 0.52s | PASS 2549 | `../receipts/smoke/lsf` |

Every verifier verdict: `dups=0 missing=0 extra=0 fields=0` (all contract fields
asserted where the mode exposed them; etag exempt by adapter policy; `lsf` checked
key+size only). The full-bucket recursive-fastlist re-listed **byte-exact** against
the manifest (claim `smoke-listing-correct-all-modes`). Designated registry prefixes
covered: `normals-hourly/` (listv1, lsf, and the mislabeled hierarchical run),
`normals-monthly/1991-2020/` (fastlist), `normals-annualseasonal/1981-2010/access/`
(fastlist), `normals-annualseasonal/1981-2010/` (genuine walk). The full-bucket
fast-list peaked at 69.6 MB RSS (claim `fastlist-smoke-peak-rss`).

**Capability probes (verifier-exempt, `_capability/` — exit code + request trace
only, no `verify.md`):**

| Probe | Invocation (extra flags) | Scope | What it shows | Receipt |
| --- | --- | --- | --- | --- |
| flat `ListR` trace | `lsjson --fast-list … -R -vv --dump headers` | `normals-hourly/` | single **undelimited** `list-type=2` `continuation-token` chain, unsigned | `../receipts/smoke/_capability/debug` |
| genuine walk trace | `lsjson … --disable ListR --checkers 4 -R -vv --dump headers` | `normals-annualseasonal/1981-2010/` | **13 page requests, every one `delimiter=%2F`**, one continuation chain per directory across four chains (`access/` = 10 serial pages), unsigned | `../receipts/smoke/_capability/walk-debug` |

### The former "hierarchical" receipt is preserved, not deleted

`../receipts/smoke/recursive-hierarchical/` (`lsjson -R`, no `--fast-list`) was
smoked believing it exercised a per-directory walk. The source check showed it was a
**third flat `ListR`** — `lsjson -R` calls `walk.ListR` directly, which ignores
`--fast-list` and selects the flat backend `ListR` on unbounded recursion, so
`--checkers 4` was inert (0 `delimiter=` requests; verified by re-running the exact
argv, claim `plain-recursive-r-is-flat-obs`). The receipt is left **annotated with a
correction block** rather than rewritten — it did list `normals-hourly/` completely
and correctly, just via the flat path, not the walk it claimed. The genuine walk was
then run separately as `recursive-walk` (PASS 9841/9841) with the wire trace in
`_capability/walk-debug` (claim `forced-walk-under-disable-listr-traced`). This is why
`../adapter/run.sh` carries a distinct `recursive-walk` mode (with `--disable ListR`)
and a `walk-debug` probe, and the `recursive-hierarchical` case carries a warning
comment.

### Live bucket drift observed mid-session (not a tool finding)

While producing the genuine-walk receipt (~13:2x UTC, 2026-07-17), NOAA began
re-uploading objects under `normals-hourly/` and `normals-monthly/` — their
`LastModified` advanced to today's date. An initial `recursive-walk` run scoped to
`normals-hourly/` therefore returned `DRIFT` (2549/2549 keys present, size and
storage_class unchanged, **mtime-only** divergence from the 2026-03-16-mtime
manifest), independently confirmed by the harness aws-cli re-list [SRC
`harness/verify-listing.sh` drift path]. Per methodology this is the third-party
bucket moving under us — **not a tool finding** (claim `noaa-bucket-drift-event`).
The walk was re-run and verified on the still-un-drifted
`normals-annualseasonal/1981-2010/` scope (0/9,841 keys re-uploaded), where it PASSes
byte-exact. The earlier `normals-hourly/`-scoped receipts (run ~12:0x, before the
re-upload) predate the drift and remain valid as recorded. The mtime drift is flagged
to the manifest owner for a re-baseline decision.

## Anonymous / unsigned access

rclone has **no `--no-sign-request` flag** — absence of credentials *is* the
anonymous mode. The on-the-fly connection-string remote
`:s3,provider=AWS,region=R:BUCKET[/PREFIX]` supplies provider+region with no config
file; the wrapper additionally starves every `AWS_*` credential, so `auth=anonymous`
is enforced, not merely configured. Confirmed unsigned on the wire: no
`Authorization` header on any request [RUN `../receipts/smoke/_capability/debug`,
`../receipts/smoke/_capability/walk-debug`] (claim
`anonymous-is-absence-of-credentials`). Region defaults to `us-east-1` when unset
[SRC `backend/s3/s3.go:1521-1522`]; AWS anonymous listing tolerates a region
mismatch.

## The exit-0-on-OOM report and its caveats

This is the owning location for the exit-0/OOM caveat (claim
`oom-exit-zero-report`, `unverified`). The report is that OOM-killed rclone runs
exit with status 0 — a silent failure reported as success. Its provenance was
corrected during groundwork and is conserved exactly:

- It rests on **two distinct GitHub issue records by the same reporter**, rclone
  #7966 and #7974, filed five days apart about the same S3 datalake /
  directory-reorganization scenario, and **both allege exit 0**. #7966 ("rclone
  returning exit 0 for out of memory OS kill", closed 2024-07-20, on v1.67.0) is the
  primary exit-0 report; #7974 ("Excess memory use when syncing millions of files in
  one directory", closed 2024-07-25) is chiefly an excess-memory report that
  **explicitly repeats the exit-0 allegation** and links #7966 [3P
  api.github.com/repos/rclone/rclone/issues/7966 & /7974, accessed 2026-07-17].
- Both concern the **`sync`** path on **v1.67-era** code. The pinned v1.74.4
  postdates that and carries the v1.70 `--list-cutoff` external-sort fix (claim
  `list-cutoff-external-sort`), so a pure-`lsjson` cgroup test of this version cannot
  speak to the sync-path report.
- Every run this phase exited 0 normally (no OOM induced); nothing here reproduces or
  refutes the report. Settling it needs a **sync-shaped** workload on a **scaled
  bucket** under a cgroup memory cap with a recorded exit code, and even a faithful
  reproduction speaks to v1.74.4, not the reporter's v1.67.

Only the citation provenance was corrected; the behavioral claim stays `unverified`.
The related at-scale memory claim (`fast-list-memory-at-scale`) carries the same
version-delta caveat by reference.

## Reproduction via `harness/smoke-run.sh`

Every receipt above was produced by the shared wrapper, never a bare `docker run`.
`../adapter/run.sh` only *prints* the argv (NUL-delimited) that the wrapper appends
to the pinned image's entrypoint; the wrapper owns `docker run`, mounts, credential
starving, the timeout, and measurement.

```sh
harness/smoke-run.sh \
  --tool rclone --mode recursive-walk \
  --image rclone/rclone@sha256:c61954aaa32328a5486715dd063a81c7879f5195ad3505cd362deddd509dc4a1 \
  --run-script tools/rclone/adapter/run.sh \
  --bucket noaa-normals-pds --region us-east-1 \
  --prefix normals-annualseasonal/1981-2010/ \
  --auth anonymous --tool-version "rclone v1.74.4" \
  --out tools/rclone/receipts/smoke/recursive-walk
```

Then verify (writes `verify.md` and the receipt verdict):

```sh
harness/verify-listing.sh \
  --tool rclone --mode recursive-walk \
  --normalize tools/rclone/adapter/normalize.sh \
  --bucket noaa-normals-pds --scope prefix --scope-prefix normals-annualseasonal/1981-2010/ \
  --input <stdout_path from run.meta> \
  --receipt tools/rclone/receipts/smoke/recursive-walk
```

Swap `--mode` for any row in `../adapter/run.sh`'s case statement
(`recursive-fastlist`, `recursive-walk`, `recursive-hierarchical`,
`delimiter-shallow`, `listv1`, `lsf`) and adjust `--prefix`/`--scope-prefix`. The two
capability probes use the `debug` and `walk-debug` modes and are **not** verified
(they are `_capability/` request-shape probes). `../receipts/` and `../research/` are
immutable inputs to this page; a rerun adds a new receipt rather than editing one.

## Deferred coverage

The versions API (`ListObjectVersions`) was not smoked (bucket unversioned), and
`EDGE_BUCKET=none` defers unicode/weird-key/multipart-ETag fidelity. Every
scale-dependent question — the constrained-memory `--fast-list` run (claim
`constrained-memory-fastlist-run`), `--fast-list` memory at scale (claim
`fast-list-memory-at-scale`), the `--checkers` and `--s3-list-chunk` sweeps (claims
`checkers-nondefault-timing-unsmoked`, `list-chunk-effect-unsmoked`), 7 GB RSS
(claim `seven-gb-rss-large-listings`), and SIGKILL crash-resume (claim
`list-crash-resume-run`) — is deferred to the benchmark phase.
