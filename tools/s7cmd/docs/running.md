# s7cmd — running

How the pinned image was built, every mode that has been smoked, how to
reproduce a run through the shared harness, and the supported-architecture
matrix. Canonical tested identity (pinned SHAs, versions, study states) lives
in [`../data/tool.json`](../data/tool.json); this page supplies the operational
detail. Evidence labels and claim references are as defined in the
[`mechanism.md`](mechanism.md) preamble, and claim IDs resolve in
[`../data/claims.json`](../data/claims.json).

## Build

**No upstream container-image channel was found.** No image is referenced in
the README, release assets, or on Docker Hub (404 checked)
[DOC s7cmd README §Installation]. **GitHub Packages/GHCR could not be
enumerated** — the API returned 403 for missing `read:packages`, and
anonymous GHCR token acquisition was denied — so this channel check is
**incomplete**, not a confirmed "no image published anywhere" (claim
`no-container-image-found-check-incomplete`, a `corrected` scoping softened
from a categorical negative; derivation in
[`../research/codex-review.md`](../research/codex-review.md)).

`s7cmd` does ship its own `Dockerfile`, so the image used for every smoke
receipt on this page was built from **upstream's own `Dockerfile` at the
pinned SHA** (`d589df7ce691edbede05fc9a691ab1787cdb6b9e`, tag `v1.5.0`) — the
artifact that most closely matches what the project intends users to run:

- Multi-stage build: `rust:1-trixie` builder runs
  `cargo build --release` (fat LTO, `codegen-units=1`, `strip=symbols`) ->
  `debian:trixie-slim` runtime with `ca-certificates`; non-root `s7cmd` user;
  `ENTRYPOINT ["/usr/local/bin/s7cmd"]`
  [SRC s7cmd Dockerfile @ d589df7].
- Build command (in the pinned checkout):
  `docker build -t s7cmd:groundwork-v1.5.0 .`
- Built image digest (local — never pushed, so there is no registry repo
  digest): `sha256:07091182512e74cde4bb897a97b1fc9a586757560c5008ae8c701d7fdb6974da`,
  arch **arm64**. Referenced to the harness wrapper as
  `s7cmd@sha256:0709…da`.
- In-image version: `s7cmd 1.5.0 (aarch64-unknown-linux-gnu), rustc 1.97.1`
  [RUN receipts/smoke/_build/help-and-version.txt]. The `--version` git-hash
  field is blank because upstream's own `.dockerignore` excludes `.git/`, so
  `shadow-rs` has no repo to read at build time — cosmetic only; the version
  number itself is correct [OBS from the build].
- Live `ls --help` matches the `s3ls-rs`-documented listing/tunable option
  set, **except** `--auto-complete-shell`, which `s7cmd` deliberately hides
  on every subcommand in favor of one top-level flag
  [SRC s7cmd src/cli.rs:400-449 @ d589df7] — the receipt confirms it is
  absent from the captured `--help` text
  [RUN receipts/smoke/_build/help-and-version.txt].

## Smoked modes

All 12 mode/scope receipts below are committed under
[`../receipts/smoke/`](../receipts/smoke/), plus `_build/` (version + help
capture) and `_capability/bucket-list` (a probe, not a listing mode). Bucket
`noaa-normals-pds` (us-east-1), manifest snapshot 2026-07-17
(sha256 `c78a…2adb`, 148,917 keys). Every run **anonymous**
(`--target-no-sign-request`, credential-starved wrapper mode,
`auth=anonymous` enforced). Image `s7cmd@sha256:0709…da`, arch arm64 native.
Concurrency pinned to `--max-parallel-listings 16` (the tool's own default,
64, exceeds this subject's `CONCURRENCY_CAP=16`) on every recursive mode;
non-recursive modes are sequential by construction regardless of the flag.
All verifier verdicts **PASS** (claim `smoke-modes-all-pass`, `confirmed`).

| Mode | Scope | Invocation (argv appended to the image entrypoint) | Exit | Wall (s) | api_calls | Receipt |
| --- | --- | --- | --- | --- | --- | --- |
| recursive-tsv | full (148,917 keys) | `ls -r -vv --disable-color-tracing --tsv --show-storage-class --show-etag --max-parallel-listings 16 --target-no-sign-request --target-region us-east-1 s3://noaa-normals-pds/` | 0 | 2.627 | 204 | `receipts/smoke/recursive-tsv/full` |
| recursive-tsv | `normals-monthly/1991-2020/` (15,625) | same flags, scoped `TARGET` | 0 | 1.928 | 19 | `receipts/smoke/recursive-tsv/normals-monthly-1991-2020` |
| recursive-tsv | `normals-annualseasonal/1981-2010/access/` (9,839) | same flags, scoped `TARGET` | 0 | 1.138 | 10 | `receipts/smoke/recursive-tsv/normals-annualseasonal-1981-2010-access` |
| recursive-tsv | `normals-hourly/` (2,549) | same flags, scoped `TARGET` | 0 | 0.495 | 17 | `receipts/smoke/recursive-tsv/normals-hourly` |
| recursive-tsv-nosort | `normals-hourly/` (2,549) *(tunable)* | adds `--no-sort` | 0 | 0.579 | 17 | `receipts/smoke/recursive-tsv-nosort/normals-hourly` |
| recursive-aligned | `normals-hourly/` (2,549) | drops `--tsv`/`--show-*` (default aligned formatter) | 0 | 0.458 | 17 | `receipts/smoke/recursive-aligned/normals-hourly` |
| recursive-json | `normals-hourly/` (2,549) | `--json` in place of `--tsv` | 0 | 0.518 | 17 | `receipts/smoke/recursive-json/normals-hourly` |
| recursive-one | `normals-hourly/` (2,549) | `-1` in place of `--tsv` | 0 | 0.489 | 17 | `receipts/smoke/recursive-one/normals-hourly` |
| all-versions | `normals-hourly/` (2,549) | adds `--all-versions` (switches API to `ListObjectVersions`) | 0 | 0.990 | 17 | `receipts/smoke/all-versions/normals-hourly` |
| max-depth | root, `--max-depth 1` (5 = 4 PRE + `index.html`) | `-r --max-depth 1 ...` | 0 | 0.182 | 1 | `receipts/smoke/max-depth/root` |
| shallow-tsv | root, delimiter `/` (5 = 4 PRE + `index.html`) | *(no `-r`)*: `ls -vv --disable-color-tracing --tsv --show-storage-class --show-etag --target-no-sign-request --target-region us-east-1 s3://noaa-normals-pds/` | 0 | 0.174 | 1 | `receipts/smoke/shallow-tsv/root` |
| shallow-tsv | `normals-hourly/`, delimiter `/` (6 PRE) | same, scoped `TARGET` | 0 | 0.177 | 1 | `receipts/smoke/shallow-tsv/normals-hourly` |
| _capability_ bucket-list | no target -> `ListBuckets` | `ls -vv --disable-color-tracing --target-no-sign-request --target-region us-east-1` | 1 | 0.194 | — | `receipts/smoke/_capability/bucket-list` |

**Capability finding.** `s7cmd ls` with no target calls `ListBuckets`;
anonymously S3 returns a **307 redirect** and the tool exits **1**
(`Failed to list buckets`) [RUN ../receipts/smoke/_capability/bucket-list].
Under `CREDS=none` this mode is **blocked, not skipped** — untested for that
reason, receipt attached (claim `bucket-listing-blocked-anonymously`). The same
run's debug output confirms
`no-sign-request: disabling credential loading and request signing`
[OBS/RUN same receipt].

**Edge-case fidelity checks deferred.** Unicode/URL-special keys, directory
markers, and multipart-ETag fixtures were **not** exercised —
`EDGE_BUCKET=none` for this pass. The primary bucket's keys are plain ASCII,
so control-char escaping and byte-fidelity paths were not stressed. See also
[`mechanism.md`](mechanism.md) §`all-versions` limitation for the related
versioned-bucket-fidelity gap.

## Reproduction

Every receipt above was produced by the shared harness, never by a bespoke
script — `run.sh` only prints the argv (see its header for the exact
contract); `harness/smoke-run.sh` owns `docker run`, mounts, credential
injection/starving, timeout, measurement, and receipt-writing. To reproduce
any row:

```sh
harness/smoke-run.sh \
  --tool s7cmd --mode <mode> \
  --image 's7cmd@sha256:07091182512e74cde4bb897a97b1fc9a586757560c5008ae8c701d7fdb6974da' \
  --run-script tools/s7cmd/adapter/run.sh \
  --bucket noaa-normals-pds --region us-east-1 [--prefix <scope>] \
  --auth anonymous \
  --out <out-dir>
```

`<mode>` is one of `recursive-tsv`, `recursive-tsv-nosort`,
`recursive-aligned`, `recursive-json`, `recursive-one`, `all-versions`,
`max-depth`, `shallow-tsv`, `bucket-list` — see
`tools/s7cmd/adapter/run.sh`'s `case` statement for the exact flags each maps
to. Rebuilding the image first
requires the pinned `s7cmd` checkout at `d589df7ce691edbede05fc9a691ab1787cdb6b9e`
and `docker build -t s7cmd:groundwork-v1.5.0 .` in it (see Build above);
`receipts/` and `research/` themselves are immutable evidence and are never
edited to "fix" a rerun — a new receipt is added instead.

## Architecture matrix

| Distribution channel | amd64 | arm64 | Source |
| --- | --- | --- | --- |
| Upstream container image | — | — | none found in README/releases/Docker Hub; GitHub Packages/GHCR unverifiable (403 / anonymous token denied) [DOC] |
| Prebuilt binaries (GitHub Releases) | yes (glibc + musl) | yes (glibc + musl) | [DOC §Pre-built binaries] |
| Source build (this image) | yes (native on an amd64 host) | yes (**built & smoked here, arm64**) | [RUN] |

The runner is **arm64 (aarch64)**, so the image above was built and smoked
**natively** (emulated = no). Both architectures are supported natively
across every channel that exists (prebuilt binaries and source build — there
is no published container channel, though GHCR could not be fully ruled
out), so the benchmark phase can pick either; **amd64 is the expected
cross-tool common denominator** and is flagged as an open item for that
phase. Smoke produces no comparative numbers, so the arch choice here is
immaterial to anything in this tool page.
