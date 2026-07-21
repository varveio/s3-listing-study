# s3p — running it

How the tool was imaged and invoked for every capability receipt, why the smoke
state is **blocked** (not skipped), and what a credentialed run would need.
Evidence labels and the claim `some-id` reference notation are as in
[`mechanism.md`](mechanism.md); claim IDs resolve in
[`../data/claims.json`](../data/claims.json). Canonical tested identity (pinned
SHAs, versions, study states) lives in [`../data/tool.json`](../data/tool.json);
this page supplies the operational detail. The blocked, auth-limited coverage
described here is this page's owned caveat.

## Image (study-authored)

**No upstream Docker image and no upstream Dockerfile exist** for s3p (v3.6.0 or
master); distribution is npm-only (`npx s3p` / `npm i -g s3p`) [SRC repo tree @
5a23b22e]. Per the brief's Stage B (neither image nor Dockerfile upstream), the
image is study-authored — [`../build/Dockerfile`](../build/Dockerfile):

- Base **`node:20-bookworm-slim`**, pinned by digest
  `node@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0`
  (multi-arch manifest-list digest, resolved 2026-07-17).
- `RUN npm install -g --ignore-scripts s3p@3.7.2 && npm cache clean --force`
  (`--ignore-scripts`: s3p ships a prebuilt `build/`, no build step).
- `ENTRYPOINT ["s3p"]` — so every `run.sh` argv starts at the **subcommand**.
- Built image digest
  `s3p@sha256:622d7ec0e110f49e8cddf1b65b8bae98f641690b0d6db317df6f21e573894b91`
  (arm64), `s3p@3.7.2`. [OBS `../receipts/smoke/_build/build-notes.md` — a build
  note, not a wrapper `smoke-run.sh` receipt]

**Version choice — source pin vs smoked version.** `[SRC]` anchors need a git
checkout, and the latest git *tag* is v3.6.0 → source is pinned there. But
`npm i -g s3p` installs **3.7.2** (npm `latest`), and the published **3.6.0**
artifact **cannot start** (missing `colors` dep; claim `v3-6-0-cannot-start`).
Smoking 3.7.2 tests what users actually get; its listing architecture and its
absence of any anonymous path are identical to v3.6.0 (verified against the
installed 3.7.2 build). The in-container `tool_version` field was
**caller-supplied**, but the build note independently captured the tool's own
`version` self-report of 3.7.2 (claim `tested-version-is-3-7-2`). The benchmark
phase should pin one coherent version (recommend **3.7.2**, or npm `latest` at
that time) and record that git tags lag npm (claim `git-tags-lag-npm`).

**Closure-pinning disagreement (recorded in [`../research/`](../research/)).** The
Dockerfile pins only `s3p@3.7.2`; a global `npm install` resolves s3p's ranged
dependencies at build time without a lockfile or tarball integrity, so a later
rebuild (or the proposed amd64 image) could resolve a different SDK/auth/retry
closure. Groundwork's recorded position: the **image digest is the run's
identity** per the brief's Stage B ("pin what can be pinned … the digest is the
run's identity; the Dockerfile is the best-effort recipe"); full npm-closure
pinning is not attainable from the published tarball and is out of scope for
smoke. Recorded as a benchmark-phase improvement (generate a lockfile / `npm
ci`) in report §7/§10 — a methodology point settled by the brief, not a defect.

## The blocked smoke state — every capability receipt

**Auth blocker: all listing modes blocked, not skipped.** s3p has no
anonymous/unsigned request path ([`mechanism.md`](mechanism.md) § Retry model;
claim `no-anonymous-access-path`). Under the wrapper's credential-starved
anonymous mode (`auth=anonymous`: `AWS_EC2_METADATA_DISABLED=true`, credential
values emptied, credential-file sources pointed at a nonexistent in-container
path), every listing mode fails identically at AWS-SDK credential resolution
**before any LIST completes**. This is the designed finalize-early/blocked path
for "a signed-requests-only tool with `CREDS=none`". `CREDS=none` on this subject
card → the modes are **recorded as untested-for-this-reason**, per the brief's
auth protocol (claim `anonymous-listing-blocked-at-auth`).

All receipts: s3p 3.7.2 image
`s3p@sha256:622d7ec0e110…573894b91` (arm64, native — image arm64 on host arm64,
not emulated), bucket `noaa-normals-pds` (us-east-1) @ its 2026-07-17 snapshot
(manifest sha256 `c78a827…992adb`, 148,917 keys). Concurrency pinned
`--list-concurrency 8` (≤ this subject's `CONCURRENCY_CAP=8`; the tool's own
default 100 exceeds the cap but is configurable, so the mode is not blocked *on
concurrency* — it is blocked on auth).

| Probe | Invocation (argv appended to the `s3p` entrypoint) | Exit | Wall | Result | Receipt |
| --- | --- | --- | --- | --- | --- |
| `ls`, anon | `ls --bucket noaa-normals-pds --region us-east-1 --list-concurrency 8 --prefix normals-hourly/` | 1 | 0.221s | `CredentialsProviderError: Could not load credentials from any providers` | `../receipts/smoke/_capability/anon-ls/` |
| `ls --raw`, anon | `ls --raw --bucket noaa-normals-pds --region us-east-1 --list-concurrency 8 --prefix normals-hourly/` | 1 | 0.213s | same error | `../receipts/smoke/_capability/anon-ls-raw/` |
| `summarize`, anon | `summarize --bucket noaa-normals-pds --region us-east-1 --list-concurrency 8 --prefix normals-hourly/` | 1 | 0.224s | same error | `../receipts/smoke/_capability/anon-summarize/` |

All three errors originate in `@aws-sdk/credential-provider-node` [RUN
`../receipts/smoke/_capability/anon-ls/stderr.txt`]. The probes cover **two
genuinely different subcommands** (`ls`, incl. its `--raw` output variant, and
`summarize`) and fail identically, confirming the block is
**command-independent** (shared credential-less `S3Client`). `ls --long`
traverses the identical path — marked **blocked-by-inheritance** from these
receipts, not re-run. The `ls`/`ls-raw` stdout/stderr payloads are byte-identical
(the failure precedes any mode-specific output), but the runs are distinct:
`run.meta` shows `mode=ls` vs `mode=ls-raw`, different `utc_start` (12:01:18Z vs
12:02:29Z), and different wall/RSS/cgroup samples; the `summarize` probe differs
in `mode`/timestamps as well.

**Request behaviour observed.** Even while failing, the `ls` probe **scheduled
two** `listObjectsV2` attempts with different `StartAfter` (`normals-hourly/` =
left, `normals-hourly/O` = the computed midpoint = right) — evidence the
bisection *logic* fans out; it does **not** show simultaneous wire execution
(both aborted at credential resolution before any HTTP LIST; no timestamps) (claim
`probe-scheduled-two-lists`). No counter value exists to record: the run aborted
before any heartbeat or final-stats line.

**No `verify-listing.sh` verdict.** No mode produced a listing to verify, so the
manifest pre-flight/verification path was not exercised (nothing to compare
against). This is why `tool.json` records `verification_status: blocked`.

## Adapter validation (no live data)

`normalize.sh` `ls-raw`/`ls`/`ls-long` and `summarize`/empty paths are validated
against **synthetic fixtures** under [`../adapter/fixtures/`](../adapter/fixtures/)
(committed `*.expected.tsv` + `check.sh` → all PASS) — ETag unquoted,
`LastModified` millis stripped to whole-second `…Z`, key-only modes correct,
`summarize` empty [the fixture check `check.sh`, not a live wrapper `smoke-run.sh`
receipt] (claim `normalize-validated-against-synthetic-fixtures`). s3p prints
**full keys** in every mode (never path-relative), so the `prefix` argument is
accepted but unused for key reconstruction. Because containers run `TZ=UTC`, any
local-time output is UTC by construction. `ls --long` is a **lossy,
non-verification mode** (human-rounded size; key recoverable only if it has no
spaces) (claim `ls-long-is-lossy`). `ls-raw`'s adapter uses raw `jq -j` (not
`@tsv`, which would escape backslash — a legal character in the 95-char
alphabet).

**Edge-case fidelity is deferred.** `EDGE_BUCKET=none`: unicode/weird-key and
size+ETag checks — the ones that would exercise the character-set boundary
(claims `non-ascii-runtime-behavior`, `utf16-ordering-runtime-behavior` in
[`mechanism.md`](mechanism.md)) — were not run. They matter more than usual for
this tool.

## What a credentialed run would need

s3p cannot benchmark against a public bucket without real credentials
(`report.md` § 10, the dominant open question). A credentialed run needs:

- **List-scoped, owner-supplied credentials** via the AWS SDK default credential
  chain (env / shared config) — s3p "uses the same credentials aws-cli uses" and
  that is the **only** option; there is no unsigned mode [SRC S3.caf:26-29 @
  5a23b22e; OBS live help @ 3.7.2]. Region via `--region` or `AWS_REGION` [SRC
  S3.caf:27-29; S3PCli.caf:25]. 3.7.x adds `--endpoint`/`S3_ENDPOINT` (claim
  `v3-7-x-adds-flags`).
- **`--list-concurrency` capped** to the subject's `CONCURRENCY_CAP=8` for smoke
  (the default 100 exceeds it); `--max-sockets` follows list-concurrency for
  list-only commands. For the benchmark, `--list-concurrency` is the primary
  sweep knob `{1,8,25,50,100,200,400}` paired with `--max-sockets`, and
  `--max-list-requests` caps cost (a soft cap — see
  [`mechanism.md`](mechanism.md) § Observability; claim
  `max-list-requests-is-soft-cap`).
- **Node ≥22** recommended for the benchmark image (SDK v3 deprecates node 20
  after Jan 2027; smoke ran node 20, which works today).

## Architecture matrix

s3p is pure interpreted JS (CaffeineScript compiled to JS; no native addons), so
arch = the node base image's arch:

| Channel | amd64 | arm64 | Notes |
| --- | --- | --- | --- |
| Upstream Docker image | — | — | none published |
| Upstream Dockerfile | — | — | none in repo |
| npm package (used here) | native | native | interpreted JS; arch = node base image's arch |
| Prebuilt binaries | — | — | none; distribution is npm-only |

Smoke ran **native arm64** (image arm64 on host arm64, not emulated) [RUN
`run.meta` `image_arch=arm64 host_arch=arm64`]. amd64 (the expected campaign
common denominator) is equally native — pick the node base arch = campaign arch.
Smoke produces no comparative numbers, so the arch choice here is immaterial to
anything in this tool page.

## Reproduction

Every receipt above was produced by the shared wrapper, never a bare `docker
run`. `run.sh` only *prints* the argv (NUL-delimited) that the wrapper appends to
the pinned image's entrypoint; `harness/smoke-run.sh` owns `docker run`, mounts,
credential injection/starving, the timeout, and measurement. To reproduce a
capability probe:

```sh
harness/smoke-run.sh \
  --tool s3p --mode ls \
  --image s3p@sha256:622d7ec0e110f49e8cddf1b65b8bae98f641690b0d6db317df6f21e573894b91 \
  --run-script tools/s3p/adapter/run.sh \
  --bucket noaa-normals-pds --region us-east-1 --prefix normals-hourly/ \
  --auth anonymous \
  --out tools/s3p/receipts/smoke/_capability/anon-ls
```

Swap `--mode` for `ls-raw` or `summarize` for the other two probes. The v3.6.0
`colors` unstartable finding was a direct `docker run` of an `s3p@3.6.0` image,
documented in `../receipts/smoke/_build/build-notes.md` — an `[OBS]` build note,
**not** a `smoke-run.sh` receipt (claim `v3-6-0-cannot-start`).

The adapter scripts under [`../adapter/`](../adapter/) and everything under
[`../research/`](../research/) and [`../receipts/`](../receipts/) are immutable
inputs to this page — they were not modified for behavior during this
consolidation.
