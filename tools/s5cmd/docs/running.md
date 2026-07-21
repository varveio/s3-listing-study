# s5cmd — running it

How the tool was invoked for every smoke receipt, and how to reproduce any of
them. Canonical tested identity is in [`../data/tool.json`](../data/tool.json);
this page supplies the operational detail. Evidence labels and claim references
are as in [`mechanism.md`](mechanism.md): references of the form claim `some-id`
resolve in [`../data/claims.json`](../data/claims.json).

## Image

Upstream `peakcom/s5cmd:v2.3.0`, pinned by digest:

```
peakcom/s5cmd@sha256:2ff939e2ee3c76adcadd78dbfc3e2569b18a3743ed9dcfccb1ec589af7fb9903
```

This is upstream's own published image (README § Docker → `docker pull
peakcom/s5cmd`) [DOC README.md:116-124], matching the normal packaged setup,
so no self-built Dockerfile is staged. Entrypoint is
`["/s5cmd"]`, so every `run.sh` argv below starts at the s5cmd global
flags/subcommand, never at the binary [RUN `../receipts/smoke/recursive`].

**Tool version:** `v2.3.0-991c9fb` — self-reported by `s5cmd version` in the
pinned image; the version string embeds the pinned commit `991c9fb`, linking
the image bytes to the checkout [RUN
`../receipts/smoke/_capability/observability/version.stdout.txt`]. This is the
receipt behind the `version` and `revision` provenance in
[`../data/tool.json`](../data/tool.json); the `tool_version` field in each
`run.meta` is caller-supplied metadata and is not used as the provenance source.

**Architecture:** native on both amd64 and arm64 — no common-denominator
problem for the benchmark phase [OBS `docker manifest inspect
peakcom/s5cmd:v2.3.0`; `.goreleaser.yml` for the prebuilt-binary targets,
SRC]. Smoke ran on **arm64** (image arm64 on host aarch64, not emulated) [RUN
`../receipts/smoke/*`].

## Every smoked mode

All modes ran **unsigned** (`--no-sign-request`, `auth=anonymous`) against
`noaa-normals-pds` (us-east-1) at its 2026-07-17 snapshot (148,917 keys,
manifest sha256 `c78a827…992adb`) and passed the verifier (claim
`all-smoked-modes-passed-anonymous`). `CREDS=none`, so no credentialed pass ran.
`EDGE_BUCKET=none`, so unicode/weird-key/multipart-ETag fidelity checks are
deferred (see `mechanism.md` § Scoped caveats).

| Mode | Invocation (argv after entrypoint) | Scope | Exit | Wall | Verdict | Receipt |
| --- | --- | --- | --- | --- | --- | --- |
| recursive | `--no-sign-request ls -e -s s3://b/*` | full | 0 | 16.96s | PASS 148917 | `../receipts/smoke/recursive` |
| recursive | `… ls -e -s s3://b/normals-hourly/*` | prefix | 0 | 0.54s | PASS 2549 | `../receipts/smoke/recursive-hourly` |
| recursive | `… ls -e -s s3://b/normals-monthly/1991-2020/*` | prefix | 0 | 2.25s | PASS 15625 | `../receipts/smoke/recursive-monthly` |
| recursive | `… ls -e -s s3://b/normals-annualseasonal/1981-2010/access/*` | prefix | 0 | 1.51s | PASS 9839 | `../receipts/smoke/recursive-annual` |
| delimiter | `--no-sign-request ls -e -s s3://b/` | delimiter `/` | 0 | 0.16s | PASS 5 | `../receipts/smoke/delimiter` |
| json | `--json --no-sign-request ls s3://b/*` | full | 0 | 15.77s | PASS 148917 | `../receipts/smoke/json` |
| listv1 | `--no-sign-request --use-list-objects-v1 ls -e -s s3://b/*` | full | 0 | 70.27s | PASS 148917 | `../receipts/smoke/listv1` |
| allversions | `--no-sign-request ls --all-versions -e -s s3://b/*` | full | 0 | 86.99s | PASS 148917 | `../receipts/smoke/allversions` |
| fullpath | `--no-sign-request ls --show-fullpath s3://b/*` | full | 0 | 17.10s | PASS 148917 (keys only) | `../receipts/smoke/fullpath` |
| fanout | 4 prefix shards (`ls s3://b/normals-{monthly,daily,annualseasonal,hourly}/*`) + `rootkeys` remainder | union | 0 | 0.16–5.58s each | PASS 148917 | `../receipts/smoke/fanout/` (`union/union-verify.md`) |

Every verifier verdict: `dups=0 missing=0 extra=0 fields=0` (all five contract
fields asserted where the mode exposed them). Delimiter returned exactly the
4 CommonPrefixes + `index.html` expected at bucket root. Wall-clock figures are
facts about single runs, not comparative results; the `recursive` baseline is
recorded as claim `recursive-ls-runs-as-baseline`, and the streaming memory
figures as claim `ls-streaming-memory-at-smoke`.

**Pre-flight (drift check).** Before smoke, the bucket was re-listed
anonymously with the pinned harness client and diffed against the registry
manifest as sorted sets — both hash to `8b5b584…6983aac`, 0 lines of diff, no
drift [RUN `../receipts/smoke/_capability/preflight`].

**Known cosmetic defect in the committed receipts (fixed harness-side,
014f74a).** Every prefix-scoped `receipt.md`'s "Prefix scope" cell renders the
prefix twice (e.g. `` `normals-annualseasonal/`normals-annualseasonal/ ``) —
the wrapper's `${PREFIX:+...}${PREFIX:-...}` template is not an if/else (`:-`
substitutes its value whenever the variable is *set*, not just when empty).
This was found independently by both the aws-cli and s5cmd groundwork agents
and fixed in `harness/smoke-run.sh` (commit `014f74a`); it is recorded as claim
`receipt-double-prefix-cosmetic-defect`. The already-committed receipts under
`../receipts/smoke/` are left with the malformed cell **as a direct record of
what the wrapper actually rendered** — receipts record wrapper output, not a
hand-patched correction. No verdict was ever affected: `run.meta`, the
machine-readable file the verifier actually reads, was always correct; only the
human-rendered Markdown cell was cosmetically wrong.

## The mandatory hand-rolled per-prefix fan-out procedure

s5cmd has no native keyspace-splitting listing mode (`mechanism.md`). The
inherited page requires this workaround be smoked, not skipped, so it isn't
tested only in its worst case. Procedure, exactly as smoked (claim
`fanout-completeness-verified`; the flag correction is claim
`run-takes-file-positionally-no-f-flag`):

1. **Partition the keyspace by top-level prefix.** For `noaa-normals-pds`:
   `normals-monthly/`, `normals-daily/`, `normals-annualseasonal/`,
   `normals-hourly/` — plus the **unprefixed remainder** (`index.html` sits at
   bucket root, under no prefix; a prefix-only partition silently drops it).
2. **List each shard independently.** Either as N separate `s5cmd ls`
   invocations (one per prefix, `s5cmd --no-sign-request ls -e -s
   s3://bucket/<prefix>*`), or as one `s5cmd run <file>` batch where each line
   is one of those `ls` commands. **v2.3.0 has no `run -f <file>` flag** — the
   file is positional or read from stdin: `s5cmd --no-sign-request run
   /work/cmds.txt` [OBS `../receipts/smoke/_capability/run-fanout`]. `run --help`
   documents that it executes the listed commands "in parallel", using the
   same `--numworkers` pool (default 256) that governs transfer concurrency
   (claim `numworkers-sizes-run-fanout-concurrency`) [SRC `command/run.go:76`].
3. **List the remainder separately.** A root delimiter listing
   (`s5cmd ls -e -s s3://bucket/`) whose object rows (not the CommonPrefix
   `DIR` rows) are the unprefixed keys.
4. **Union and verify.** `--scope union` over all shards plus the remainder:
   **PASS**, 148,917 keys, 0 cross-shard duplicates, 0 missing, 0 extra,
   structurally complete [RUN
   `../receipts/smoke/fanout/union/union-verify.md`]. The in-process `s5cmd run
   <file>` orchestration of the identical partition also ran clean (148,917
   distinct keys, exit 0, 0 bytes stderr) [OBS
   `../receipts/smoke/_capability/run-fanout`].

This settles **completeness** of the workaround against the smoke bucket.
Its *speed* relative to a native parallel lister — the number most likely to
be quoted about this tool — is unmeasured and stays `unverified` (claim
`fanout-speed-vs-native-unverified`), a benchmark-phase question (see
`mechanism.md` § Deferred).

## Auth setup

Unsigned access is the global `--no-sign-request` flag, which wires
`credentials.AnonymousCredentials` into the client [SRC
`storage/s3.go:1242-1244` @ 991c9fb]; it is mutually exclusive with
`--profile`/`--credentials-file` [SRC `command/app.go:110-117` @ 991c9fb].
`ls`/`du` have **no region flag** — region is auto-detected via
`s3manager.GetBucketRegion`, defaulting to `us-east-1` when undetectable [SRC
`storage/s3.go:1320,1344` @ 991c9fb]. This worked unsigned against the
us-east-1 smoke bucket [RUN `../receipts/smoke/*`].

## Reproduction via `harness/smoke-run.sh`

Every receipt above was produced by the shared wrapper, never a bare `docker
run`. `../adapter/run.sh` only *prints* the argv (NUL-delimited) that the wrapper
appends to the pinned image's entrypoint; the wrapper owns `docker run`, mounts,
credential injection/starving, the timeout, and measurement.

```sh
harness/smoke-run.sh \
  --tool s5cmd --mode recursive \
  --image peakcom/s5cmd@sha256:2ff939e2ee3c76adcadd78dbfc3e2569b18a3743ed9dcfccb1ec589af7fb9903 \
  --run-script tools/s5cmd/adapter/run.sh \
  --bucket noaa-normals-pds --region us-east-1 \
  --auth anonymous \
  --out tools/s5cmd/receipts/smoke/recursive
```

Swap `--mode` for any row in `run.sh`'s case statement (`recursive`,
`delimiter`, `rootkeys`, `json`, `listv1`, `allversions`, `fullpath`) and add
`--prefix <p>` for a scoped listing (e.g. `normals-hourly/`). The fan-out mode
is not a single wrapper invocation — it is the wrapper run once per shard
(four `recursive`-mode calls with different `--prefix` values, plus one
`rootkeys`-mode call for the remainder), followed by
`harness/verify-listing.sh --scope union` over the resulting receipts to
produce `union-verify.md`. The in-process `s5cmd run <file>` capability probe
in `../receipts/smoke/_capability/run-fanout/` is reproduced by mounting a
commands file (one `ls` line per shard) read-only into the container and
invoking `s5cmd --no-sign-request run /work/cmds.txt` directly — it is not a
`smoke-run.sh` receipt because `run` needs a file mounted into the container,
which the wrapper does not provide for this tool.

`../adapter/run.sh`/`../adapter/normalize.sh` and everything under
`../research/` and `../receipts/` are immutable inputs to this page — they were
not modified for this consolidation beyond the migration's link and path
repairs.
