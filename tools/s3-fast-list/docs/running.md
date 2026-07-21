# s3-fast-list — running

How the pinned image was built, every mode that has (and has not) been smoked,
the direct-capture procedure the harness forced, how to reproduce a run, and the
architecture matrix. Canonical identity and tested-revision data lives in
[`data/tool.json`](../data/tool.json); this page supplies the operational detail.
Evidence labels and claim references are as in
[`mechanism.md`](mechanism.md).

## Build

**No upstream container image is published** — upstream ships only a
`Dockerfile` [SRC Dockerfile @ 6c72f59]. **Two things distinguish the built
artifact from stock upstream:**

1. **The source is the fork, not upstream.** The pinned checkout `6c72f59` is the
   fork branch `feat/no-sign-request` = upstream `b11e385` **+ the 51-line
   `--no-sign-request` behavioral patch** [SRC main.rs:56-58 @ 6c72f59] — the
   patch that makes anonymous smoke possible and that is not merged upstream (see
   [`data/tool.json`](../data/tool.json)). The binary in this image carries that patch;
   the benchmark phase measures only what upstream ships.
2. **A toolchain deviation in the Dockerfile itself** (below).

Subject to both, the image was built from **upstream's own `Dockerfile` (as
carried on the fork branch) at the pinned SHA**, staged as
`tools/s3-fast-list/build/Dockerfile`.

**The deviation (a reproducibility defect in upstream).** Upstream pins `FROM
rust:1.86-slim`, and **it did not build at the pinned SHA in the captured
groundwork attempt**: `Cargo.lock` is `.gitignored` (no committed lockfile)
and `Cargo.toml` uses loose semver, so
`cargo build` resolves the newest compatible transitive deps, which now demand
**rustc ≥ 1.94.1** (`aws-smithy-*`, `aws-types@1.4.0`). Under rust:1.86 the
cargo dependency resolution failed with exit 101 [OBS
`../receipts/smoke/_build/build-rust1.86-FAIL.txt` — a build-log tail; its
provenance header records the reconstructed context and states what is not
recorded; claim `rust-1-86-build-failure-observed`].

**The fix** is the minimal faithful one — advance the *builder toolchain* to
satisfy the floated deps, exactly what an upstream user running this Dockerfile
today would have to do, rather than hand-pin dozens of transitive crates to a
version set upstream never recorded:

- `FROM rust:1.94-slim-bookworm` builder. **The `-bookworm` pin matters:** it
  keeps the builder on the same Debian release as upstream's
  (glibc 2.36), so the binary still loads on the `distroless/cc-debian12`
  runtime. A trixie `rust:1.94-slim` would build a `GLIBC_2.39` binary the
  debian12 runtime cannot load [INFERRED from Debian release glibc versions —
  bookworm 2.36, trixie 2.39; distroless/cc-debian12 provides 2.36; not a
  committed run].
- Runtime `gcr.io/distroless/cc-debian12`; binaries `s3-fast-list` + `ks-tool`
  copied to `/usr/bin/`. **Entrypoint is null**, so the wrapper argv starts with
  `/usr/bin/s3-fast-list`. Nothing else changed.
- Build command (in the pinned checkout): `DOCKER_BUILDKIT=1 docker build -f
  tools/s3-fast-list/build/Dockerfile -t s3-fast-list:groundwork-6c72f59 .`
  [RUN `../receipts/smoke/_build/image.txt`].
- Built image (local, never pushed):
  `s3-fast-list@sha256:6246ee511116608864fab260aec1198c2761e42203316178a89ac1031664f2cc`,
  arch **arm64**. The built image's digest is the run's identity; the Dockerfile
  is the best-effort recipe. Build logs: `../receipts/smoke/_build/`.

## Smoked and blocked modes

Bucket `noaa-normals-pds` (us-east-1), manifest snapshot 2026-07-17 (sha256
`c78a…2adb`, 148,917 keys). Every run **anonymous** (`--no-sign-request` under
the wrapper's credential-starved env). Image `s3-fast-list@sha256:6246ee51…`,
arch arm64 **native** (`Emulated: no`). All runs are the plain `list` mode
(source-supported as serial). A separate `RUST_LOG=s3_fast_list=debug` capture
records 1 flat-list task and 1 `Sending S3 request` [OBS
`../receipts/smoke/_capability/debug-requestshape.stderr.txt`], but it is not
bound to an independently receipted invocation, image, or exit status.

| Scope | Exit | Wall | peak_rss | Verifier | [OBS] manifest-diff | Receipt |
| --- | --- | --- | --- | --- | --- | --- |
| full bucket | 0 | 20.06 s | 65.1 MB | **BLOCKED** (harness capture) | 148,917 = 148,917 — 0 missing/extra/field-mismatch/dup | `../receipts/smoke/list/full/` |
| `normals-hourly/` | 0 | 5.06 s | 19.1 MB | **BLOCKED** | 2,549 = 2,549 — clean | `../receipts/smoke/list/hourly/` |
| `normals-monthly/1991-2020/` | 0 | 5.06 s | 28.0 MB | **BLOCKED** | 15,625 = 15,625 — clean | `../receipts/smoke/list/monthly-1991-2020/` |
| `normals-annualseasonal/1981-2010/access/` | 0 | 5.06 s | 23.8 MB | **BLOCKED** | 9,839 = 9,839 — clean | `../receipts/smoke/list/annualseasonal-1981-2010-access/` |

Exit, wall-clock, and peak RSS are facts from the harness runs. The manifest
diffs used later direct captures and are grouped here only by intended scope;
the two execution paths are not evidentially bound to one another.

Full-bucket argv (via `../adapter/run.sh`, appended to the null entrypoint):
`s3-fast-list --no-sign-request --output-parquet-file /dev/stdout
--output-ks-file /dev/null list --region us-east-1 --bucket noaa-normals-pds`.
Scoped runs add `--prefix <scope>`.

**Not smoked (stay `unverified`):**

- `list -k` **hinted/parallel path** — the tool's whole value proposition. Needs
  an **input hints file mounted into the container**; the wrapper mounts nothing
  (argv + observability env only). Mechanism is source-established; the
  boundary-omission hypothesis (claim `hint-boundary-key-can-be-omitted`)
  applies and must be settled before any hinted correctness claim.
- `diff` mode — needs a second bucket; against one bucket every key is `Equal`
  and `Equal` rows are never exported → empty output. `CREDS=none`, no second
  bucket.
- `ks-tool split` / `inventory`; every scale / throughput / OOM / cancellation /
  panic / throttling / endpoint hypothesis.
- **Edge-case fidelity** — `EDGE_BUCKET=none`. Unicode / weird-key / multipart
  fidelity recorded **deferred**, not run. The NOAA keys are plain ASCII.

## The harness capture incompatibility, and the direct-capture procedure

**The standard verifier returned no verdict (BLOCKED) for every run.** The
wrapper captures container stdout via `docker logs` (json-file driver), which is
**not binary-safe**; this tool emits **binary Parquet** on stdout (routed there
by `--output-parquet-file /dev/stdout`), so non-UTF-8 bytes become U+FFFD and
the stored payload is an unparseable Parquet. `verify-listing.sh`/duckdb cannot
issue a verdict, and the verifier refuses a substituted `--input` whose sha256
does not match the receipt. Universal across payload sizes (127 KB and 8 MB both
corrupted). Run-facts (exit code, wall, peak_rss, cgroup, anonymous access) are
still valid. Full detail:
`../receipts/smoke/_capability/HARNESS-INCOMPATIBILITY.txt`.

**What was done instead — [OBS] manifest-diff, not a verifier PASS.** Listing
correctness rests on a `docker run > file` **direct capture**, normalized by
`../adapter/normalize.sh`, diffed against the registry manifest with the verifier's own
canonicalization (`LC_ALL=C`, `tolower(etag)`, mtime tz-canonical). All four
scopes matched exactly (counts above). This is a manifest-diff, **not** a
certified verifier verdict: the match is recorded as the `supported`
observation claim `limited-direct-capture-manifest-match`, and no correctness
claim is promoted to `confirmed`.

**Direct-capture provenance.** The direct-capture payloads are
bound by evidence — sha256 (matching `direct-capture.sha256`), valid Parquet
parse, row counts equal to the manifest, byte-size cross-check, file mtimes
~12:08 UTC. What is **not recorded** is stated, not reconstructed: the exact
direct `docker run` command line (the shell history predates the captures), the
direct-run exit codes, a per-run image-digest binding, and a verification
transcript. "Identical invocation to the wrapper argv" is the *intent*, not an
independently logged fact. Full record:
`../receipts/smoke/_capability/direct-capture.provenance.md`.

**normalize.sh caveat.** Claim `adapter-tab-newline-key-loss`.
`../adapter/normalize.sh` reads the Parquet on stdin
(spooled to a temp file — Parquet needs random access) and emits
`key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class` via DuckDB `-list` with a
tab separator and **no quoting**, so a valid `Key` containing a literal TAB or
newline would break the five-column / one-record contract. The NOAA keys have
neither byte, so no run was affected; the deferred edge-key bucket cannot be
verified through this adapter until it gains binary-safe framing.

## Harness carry-forwards (before the benchmark phase)

Both are prerequisites for benchmarking the parallel mode and are routed to the
orchestrator (harness gaps, not tool findings):

1. **A binary-safe output capture** (bind-mount / `docker cp` / `docker attach`
   instead of `docker logs`) so Parquet survives capture and the standard
   verifier can issue a real verdict.
2. **An input-file mount** so a `-k` hints file can reach the container — without
   it the tool's whole parallel value proposition cannot be exercised.

Detail: `../receipts/smoke/_capability/HARNESS-INCOMPATIBILITY.txt`.

## Reproduction

Every **smoke-run** receipt (the four `list/` rows above) was produced by the
shared harness — `../adapter/run.sh` only prints the argv (NUL-delimited; see its header for
the contract), and `harness/smoke-run.sh` owns `docker run`, credential starving,
timeout, measurement, and receipt-writing. The `_build/` receipts (the `docker
build` above) and the `_capability/` direct captures were produced **out of band**
— outside the smoke harness — and are labelled as such. Rebuilding the image
first requires the pinned checkout at
`6c72f59` and the `docker build` above. `../receipts/` and `../research/` are
immutable evidence — never edited to "fix" a rerun; a new receipt is added
instead.

## Architecture matrix

| Channel | amd64 | arm64 | Note |
| --- | --- | --- | --- |
| Upstream published image | — | — | none exists |
| Prebuilt binaries | — | — | none released |
| Source build (this Dockerfile) | not built | **built + ran [OBS]** | arm64 build/run receipted; amd64 [INFERRED] buildable (pure-Rust deps, multi-arch base) but **not run** |

Only **arm64** has build+run evidence (the runner box). amd64 is **inferred**
buildable — the base images are multi-arch and the deps are pure-Rust — but a
multi-arch base does not show that the whole project builds and runs on amd64; no
amd64 build or run was performed. The benchmark's single common
arch is expected to be **amd64**, which must be confirmed by an actual amd64
build before it is relied on. Smoke produces no comparative numbers, so the arch
choice here is immaterial to anything in this tool page.
