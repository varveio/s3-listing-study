# pS3 — running it

How the tool was containerized and smoke-attempted, why every run was blocked,
and what participation in the benchmark would require. Evidence labels as in
`mechanism.md`. **Every run of the amd64 binary below (list-anon, help,
silent-empty) is the amd64 image under qemu emulation on an arm64 runner
(`emulated=yes` in every such receipt) — that caveat rides every binary-run
statement here.** The one exception is the source-build attempt (§ Source-build
failure), which compiled Go source in a `golang:1.20.3` builder **natively on
arm64** — no emulation. No listing was produced: pS3 is blocked for the three
reasons in `../README.md` § Limitations and open questions. Evidence labels and the
claim `some-id` reference notation are as defined in
[`mechanism.md`](mechanism.md); statuses resolve in
[`../data/claims.json`](../data/claims.json).

## Image

Upstream ships **neither a published image nor a Dockerfile**, and the source at
the pinned commit **does not compile** (§ Source-build failure), so the study
writes its own Dockerfile (`tools/ps3/build/Dockerfile`) that installs upstream's
**committed prebuilt binary** — the only artifact the project actually ships
that works — fetched by content hash from the pinned commit:

- Base `debian@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818`
  (pinned by digest) + `ca-certificates`, `tzdata`, `ENV TZ=UTC`.
- Binary `pS3.0-1-16` fetched via
  `ADD --checksum=sha256:3bc7bbbb0d45d6f96b0130f98859ea7cfe693b13a8438461240423511322a9c2`
  from `raw.githubusercontent.com/jboothomas/ps3/9428492…/pS3.0-1-16` →
  `/usr/local/bin/pS3` (ELF x86-64, static). **Reproducibility caveat:** this
  binary is *not* reproducible from the repo — the source does not build and the
  binary exposes three subcommands whose source is absent (§ Source-build
  failure). It is pinned by content hash precisely because the repo cannot
  regenerate it.
- `ENTRYPOINT ["/usr/local/bin/pS3"]`, so `run.sh` argv starts at the
  subcommand.

Built image:
`ps3-study@sha256:c0d7b655163832bf769af0dd5da037c17f6b7b1b519724b8291297b5ae539663`,
arch **amd64**. Build:
`docker build --platform linux/amd64 -t ps3-study:0.1.16 tools/ps3/build/`.
In-image version `pS3 version 0.1.16` [RUN `receipts/smoke/_capability/help/help.txt`].

## qemu / amd64 emulation posture

pS3's only runnable *binary* artifact is amd64-only (ELF x86-64); this runner is
arm64 (aarch64). Every smoke probe that ran the binary therefore ran the amd64
image **under qemu emulation** (`emulated=yes`). (The source-build attempt is
separate — it compiled Go source natively on arm64, § Source-build failure.)
amd64 binfmt/qemu was not pre-registered on the box; it was enabled via
`tonistiigi/binfmt --install amd64` before smoke. **Emulation was acceptable for
the smoke probes but must not carry into the benchmark phase**, which needs one
natively-common architecture — pS3 supports only amd64 natively, a constraint
flagged for the cross-tool architecture decision (`../README.md` § Limitations and open questions).

## Blocked smoke state

Pre-flight against the drift-checked manifest was **not run**: pS3 cannot
authenticate anonymously, so it produces no listing to diff. Manifest digest
(`c78a8273…2adb`) is cited in the receipt regardless.

| Mode | Auth | Invocation (argv after entrypoint) | Exit | Wall | Verifier | Receipt |
| --- | --- | --- | --- | --- | --- | --- |
| `list` | anonymous (credential-starved) | `list-objects-v2 --bucket noaa-normals-pds --region us-east-1` | **1** | 0.271s | n/a — **BLOCKED** (no output) | `receipts/smoke/_capability/list-anon/` |
| `list-object-versions` | anonymous | — | — | — | **BLOCKED** (same auth wall; [SRC] shared session path) | not run |
| `head-objects` | anonymous | — | — | — | **BLOCKED** (same auth wall) | not run |

### Capability receipts

- **`_capability/list-anon/`** `[RUN]` — under the harness's credential
  starvation (credential values emptied; `AWS_SHARED_CREDENTIALS_FILE`/
  `AWS_CONFIG_FILE` → `/nonexistent-by-harness`; `AWS_EC2_METADATA_DISABLED=true`)
  the primary `list` mode exits **1** with stderr `error: S3 session creation
  failed` and **zero** stdout — it fails at S3 session creation **before**
  issuing a single S3 API call ([INFERRED] from the error message +
  `session.NewSessionWithOptions` failing on the redirected config path [SRC
  `listObjectsV2.go:90-103`]; no API observer was attached, so "0 S3 API calls"
  is [INFERRED], not measured). **CONFIRMED, scoped to exactly that:** for
  v0.1.16, this one `list-objects-v2` invocation, under the harness anonymous
  env, exit 1, no listing. The wrapper receipt is **one** run [RUN];
  determinism across 5/5 non-trace + 3/3 `--trace` repeats is [OBS] from manual
  emulated re-runs. Measured process `pS3` (peak_rss 30.9 MB is the tool's, not
  a shell's). That pS3 has *no unsigned path at all* is [SRC]+[OBS], **not
  settled by this single receipt**; the two other modes are unrun (blocked by
  the shared session path, by inference).
- **`_capability/help/help.txt`** `[RUN]` — `pS3 --version` + every `--help`
  (root and all four subcommands). The raw artifact behind the subcommand/
  flag-surface claims, including the three subcommands (`head-objects`,
  `list-object-versions`, `list-test`) absent from the checkout source.
- **`_capability/silent-empty/` + `silent-empty-obs.md`** `[OBS]` — with a
  *bare* no-credentials env (only `AWS_EC2_METADATA_DISABLED=true`, no file
  redirect) pS3 instead exits **0 with empty output and no error** (false
  success; raw capture: exit 0, 0-byte stdout, 0-byte stderr). **Not a wrapper
  receipt** (env differs from the wrapper's full starvation); `[OBS]` is never a
  receipt. Recorded as a direct observation, with both environments documented; only
  the exit-1 half above is receipt-backed.
- **`_adapter/`** — `list-sample.txt` fixture; `normalize.sh` validated
  synthetically because no live pS3 output was obtainable (blocked).

**Edge-case fidelity checks: DEFERRED** (`EDGE_BUCKET=none`). The alphabet
coverage-gap and the embedded-newline adapter gap (`mechanism.md`) are exactly
what an edge fixture would exercise.

## Source-build failure

`_build/` `[RUN]` records a build attempt (no verifier verdict — not a listing).
Attempted: compile the pinned checkout (HEAD `9428492`) from source. Upstream
ships no `go.mod`, so the attempt synthesizes one and pins the exact dependency
versions the shipped binary was built against (read from binary build metadata:
`aws-sdk-go v1.44.249`, `cobra v1.7.0`, `viper v1.15.0`, Go 1.20.3). Builder
image `golang:1.20.3` (`golang@sha256:403f4863…ff5e`), native arm64. Result
**BUILD_EXIT=1** — compile errors:

```
cmd/listObjectsV2.go:9:2:   "os" imported and not used
cmd/listObjectsV2.go:66:3:  undefined: log
cmd/listObjectsV2.go:102:3: undefined: log
cmd/listObjectsV2.go:130:3: undefined: log
cmd/listObjectsV2.go:165:5: undefined: atomic
cmd/listObjectsV2.go:186:37: "debug: item count=".atomic undefined (type untyped string has no field or method atomic)
cmd/listObjectsV2.go:218:5: undefined: log
cmd/listObjectsV2.go:316:5: undefined: log
```

The source at HEAD uses `log.*` and `atomic.*` without importing
`log`/`sync/atomic`, imports `os` without using it, and at
`listObjectsV2.go:186` has a `.`-for-`,` typo that makes `"debug: item
count=".atomic` **parse as a selector and fail type-checking — a compile error,
not strictly a syntax error**. All of this is
**scoped to the pinned HEAD `9428492`**. Independently, the shipped binary
exposes subcommands whose source is absent from the checkout [RUN help], so the
repo cannot reproduce its own shipped binary even after the compile errors are
fixed. The study therefore runs the upstream-committed prebuilt binary.

## What participation would require

pS3 cannot enter the benchmark as it stands. Participation would require **all**
of:

1. **Credentials.** There is no unsigned path (`mechanism.md`), so a credentialed
   AWS identity with `s3:ListBucket` (and `s3:ListBucketVersions` for the
   versions mode) is a precondition. The harness's `--auth credentialed` is
   deliberately unimplemented for this campaign (`CREDS=none`).
2. **A solo window + owner sign-off.** Listing concurrency is uncappable (256
   pager `var` + unbounded discovery goroutines, no flag), so pS3 cannot run
   inside the shared `CONCURRENCY_CAP` alongside the ≤8-capped tools. It must be
   run out-of-band in a solo window, or the benchmark must patch the package
   vars — either way an explicit owner decision, not a default.
3. **A native amd64 host** (or an accepted-and-labeled emulation posture) — pS3
   is amd64-only natively; timing under qemu is disqualifying.

## Architecture matrix

| Channel | Ships? | amd64 | arm64 |
| --- | --- | --- | --- |
| Upstream published image | **No** | — | — |
| Upstream Dockerfile | **No** | — | — |
| Prebuilt binary (`pS3.0-1-16`, committed) | **Yes** | **native** (ELF x86-64, static) | **no** |
| Source build | ships source but **does not compile** (§ Source-build failure) and does not match the shipped binary | n/a | n/a |
| Study Dockerfile (this repo) | written for the study | installs the committed amd64 binary; run under qemu on arm64 | via qemu only |

## Reproduction

The `list-anon` receipt was produced by the shared wrapper, never a bare `docker
run`: `run.sh` only *prints* the argv (NUL-delimited) that the wrapper appends
to the image entrypoint; `harness/smoke-run.sh` owns `docker run`, mounts,
credential starving, timeout, and measurement.

```sh
harness/smoke-run.sh \
  --tool ps3 --mode list \
  --image ps3-study@sha256:c0d7b655163832bf769af0dd5da037c17f6b7b1b519724b8291297b5ae539663 \
  --run-script tools/ps3/adapter/run.sh \
  --bucket noaa-normals-pds --region us-east-1 \
  --auth anonymous \
  --out tools/ps3/receipts/smoke/_capability/list-anon
```

`run.sh` maps `list` → `list-objects-v2`, `list-versions` →
`list-object-versions`, `head` → `head-objects`. It **refuses a prefix
argument** (exit 3): pS3 has no `--prefix`/delimiter flag, so it cannot scope a
listing, and silently listing the whole bucket under a "scoped" label would
verify against the wrong expected set. The `_build/`, `help/`, and
`silent-empty/` captures are not `smoke-run.sh` receipts — they are help/version,
a source-compile attempt, and a bare-env observation respectively, captured
directly. `run.sh`/`normalize.sh` and everything under `research/` and
`receipts/` are immutable inputs to this page — not modified for this
consolidation.
