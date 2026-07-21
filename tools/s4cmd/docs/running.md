# s4cmd — running it

How the smoke image was built, the blocked smoke state and its capability
receipts, how the run scripts are guarded, what a credentialed run would need,
and the architecture matrix. Evidence labels and claim references are as in
[`mechanism.md`](mechanism.md); canonical tested identity (pinned SHAs, version)
lives once in [`../data/tool.json`](../data/tool.json), and this page assumes it.

## Image

Upstream (bloomreach/s4cmd) ships **no published image and no Dockerfile** — the
repo at the pinned commit contains neither [SRC — repo file listing @ 80059bf],
and PyPI/GitHub/Docker Hub show only community images, not a bloomreach one
(`victorlap/s4cmd`, `poldracklab/s4cmd`, `graymic/s4cmd`) [3P — Docker Hub URLs
in [`../research/report.md`](../research/report.md) §11]. So the study wrote
[`../build/Dockerfile`](../build/Dockerfile):

```
FROM python@sha256:b53f496ca43e5af6994f8e316cf03af31050bf7944e0e4a308ad86c001cf028b  # python:3.7-slim
# ... boto3==1.9.253 botocore==1.12.253 pytz==2018.5, then s4cmd @ 80059bf --no-deps
ENTRYPOINT ["s4cmd"]
```

- **Base** `python:3.7-slim`, pinned by digest
  `sha256:b53f496ca43e5af6994f8e316cf03af31050bf7944e0e4a308ad86c001cf028b`.
- **s4cmd** installed from the exact pinned commit
  (`80059bfa4451f513a8f314fb6300e5ecc51587b2`, tag `2.1.0`) with `--no-deps`.
- **Built content digest**
  `sha256:d458ef5096180e517840712e29b0b8705ec97cebf48f717cad2fea3805105813`,
  arch **arm64** (native on the arm64 runner; no emulation).
- `--version` → `s4cmd version 2.1.0`, exit 0; `--help` shows `-c/--num-threads`
  and `--endpoint-url` but **no** unsigned flag and **no** page-size flag
  [RUN `../receipts/smoke/_build/build.md`].

**The boto3/botocore pin is reproducibility-only, NOT a compatibility need**
(claim `installs-imports-runs-under-current-boto3`). An earlier study draft
asserted s4cmd 2.1.0 "won't import under a current boto3" because `s4cmd.py:274`
references `botocore.vendored.requests` (the vendored requests *library* was
removed in botocore 1.13.0, 2019). That statement was wrong and is corrected: on
test, `pip install s4cmd` imports and runs `s4cmd --version` cleanly under
**botocore 1.33.13** (Py 3.7) and the **latest botocore 1.43.50** (Py 3.12) — the
attribute path still resolves
[RUN `../receipts/smoke/_build/modern-boto3-import/{transcript,transcript-py312}.txt`].
The `boto3==1.9.253`/`botocore==1.12.253` pin is therefore a **reproducibility
choice** that fixes the smoke run to a 2018-era boto3; the benchmark phase should
reconsider it and run a current boto3 (differing retry/pooling behavior).

**Digest-pinned ref and its local-registry dependency (note for reproducers).**
The harness wrapper takes a digest-pinned ref, produced via a local registry:

```
localhost:5000/s4cmd-study@sha256:d458ef5096180e517840712e29b0b8705ec97cebf48f717cad2fea3805105813
```

This ref resolves **only** while the `localhost:5000` registry container that the
build pushed to is running [RUN `../receipts/smoke/_build/build.md`]. It is not a
public registry path: a reproducer must rebuild the image from the pinned
`../build/Dockerfile`, push it to a local `registry:2`, and re-derive the digest
(the build receipt gives the exact commands). The content digest itself is stable
across rebuilds of the pinned inputs; the `localhost:5000/...` prefix is a local
convenience, not a fetchable location.

## Smoke state — every listing mode is blocked, not skipped

s4cmd has **no** unsigned/anonymous mechanism and `CREDS=none`, so no listing
mode can run against the anonymous smoke bucket. This is a capability finding,
demonstrated under the harness wrapper, with a source cause
([`mechanism.md`](mechanism.md) § "No unsigned path"; claim
`no-unsigned-request-support`). Scope: s4cmd **2.1.0**, image `sha256:d458…813`,
against `noaa-normals-pds` (148,917 keys, 2026-07-17 snapshot).

**Pre-flight (the bucket is not at fault).** An anonymous scoped list of
`normals-hourly/` with the pinned harness client (`--no-sign-request`) returns
keys, exit 0 [RUN `../receipts/smoke/_capability/preflight-anon/` — `meta.md` +
`stdout.txt`]. The bucket is live and anonymously listable; the failure below is
s4cmd's.

**Capability probe (canonical receipt)** —
`../receipts/smoke/_capability/anon-nocredentials/` (claim
`recursive-blocked-without-credentials`):

| | |
| --- | --- |
| Invocation | `s4cmd ls -r -c 4 s3://noaa-normals-pds/normals-hourly/` (via `run.sh recursive`) |
| Auth | `anonymous` (credential-starved, wrapper-enforced) |
| Exit | **1** |
| Wall-clock | 0.212 s |
| Verifier | not run — capability probe, s4cmd produced **no** keys |
| Failure | `botocore.exceptions.InvalidConfigError` at `BotoClient.__init__` (`s4cmd.py:386`), i.e. **before any S3 request** |

The exact exception text ("assume role with web identity but has no role ARN
configured") is an interaction with the wrapper's credential neutralization
(`AWS_WEB_IDENTITY_TOKEN_FILE=/nonexistent-by-harness`, empty `AWS_ROLE_ARN`,
activating botocore's web-identity provider). The **root cause is s4cmd's**: with
no `signature_version=UNSIGNED`, boto3 resolves credentials eagerly at client
construction and s4cmd cannot even build a client credential-starved.

**General bare-env case** (`[OBS]`, non-wrapper) —
`../receipts/smoke/_capability/direct-bare-env.stderr.txt`, same image, only
`AWS_EC2_METADATA_DISABLED=true` set (claim `bare-env-fails-at-first-list-call`).
The client constructs, then the `list_objects` call inside the s3walk **worker
thread** fails with `Unable to locate credentials` (`NoCredentialsError`).
Because it is raised on the worker thread it is caught by the worker's generic
`except Exception` (`s4cmd.py:540`) and surfaced as `[Thread Failure]`
(`s4cmd.py:469`) — it does **not** reach the main-thread
`except BotoClient.NoCredentialsError` at line 1933
[SRC `s4cmd.py:540,469,1933 @ 80059bf`]. Bound in
`../receipts/smoke/_capability/OBS-probes.md`.

**Per-mode status.** `recursive` is **settled by run** (the committed capability
receipt). `shallow` / `show-directory` / `du` are **blocked by source
inference**: each `*_handler` builds the same credential-less `BotoClient` via
the shared `s3handler()`/`S3Handler.connect()` path
(`s4cmd.py:1557,1563,674-688`), so the same construction failure applies — one
inference, not four independent receipts (claim
`other-modes-share-blocked-constructor-path`).

**Multi-prefix rejection** (`[OBS]`) —
`../receipts/smoke/_capability/obs-multiprefix.stderr.txt`: `s4cmd ls` with two
path arguments returns `[Invalid Argument] Invalid number of parameters`, exit 1,
confirming `ls` takes exactly one path (claim `ls-accepts-exactly-one-path`).

**Edge-case checks:** `EDGE_BUCKET=none` → deferred (unicode / weird-key /
multipart-ETag; claim `edge-key-fidelity-deferred`). **Request-behavior
observations:** none capturable — s4cmd issued **zero** S3 API calls (failed
before the first request).

## Run scripts and the concurrency-cap guard

[`../adapter/run.sh`](../adapter/run.sh) prints only the s4cmd argv
(NUL-delimited) for a mode; the wrapper owns `docker run`, mounts, credential
injection/starving, timeout, and measurement. The argv starts at the
**subcommand** because the image entrypoint is `["s4cmd"]`.

Because s4cmd's default thread count is `cpu_count*4` (`s4cmd.py:120-121`) — 32 on
the runner, 4× the `CONCURRENCY_CAP=8` — every mode pins `-c` (claim
`num-threads-default-is-cpu-count-times-four`). The default is 4, overridable via
`S4CMD_SMOKE_THREADS`, and the guard is **enforced in the script, not just in
prose**: a value outside `1..8` (or a non-integer) exits 2 rather than emitting a
stray `-c 99` [`../adapter/run.sh:32-35`]. `run.sh`'s `case` covers `recursive`,
`shallow`, `show-directory`, `du`.

The `region` argument is **accepted but intentionally unused**: s4cmd 2.1.0
exposes no region flag, so boto3 resolves region itself. It is taken for
interface uniformity and recorded so it is not mistaken for a dropped knob
[`../adapter/run.sh` header].

## What a credentialed run would need

Benchmarking s4cmd requires credentials — it cannot list anonymously at all
(claim `no-unsigned-request-support`). A credentialed run would supply
`--access-key/--secret-key`, or `S3_ACCESS_KEY`/`S3_SECRET_KEY` env, or `~/.s3cfg`,
or an EC2 IAM role (resolution order per `s4cmd.py:664-668,624-659,385-386`), then
exercise the four modes and sweep `-c` within the aggregate cap. This is routed
to the owner, the same class as other signed-only subjects. The open questions
such a run would settle — memory ceiling, delimiter-recursion scaling and true
LIST-page counts, throttling behavior, client-CPU cost, and current-S3 v1
compatibility — are the `unverified` claims in
[`../data/claims.json`](../data/claims.json) and are listed verbatim in
[`../research/tool-page.md`](../research/tool-page.md) § "Open hypotheses for the
benchmark".

## Reproduction via `harness/smoke-run.sh`

Every capability receipt was produced by the shared wrapper, never a bare `docker
run`. To reproduce the canonical capability probe:

```sh
harness/smoke-run.sh \
  --tool s4cmd --mode recursive \
  --image 'localhost:5000/s4cmd-study@sha256:d458ef5096180e517840712e29b0b8705ec97cebf48f717cad2fea3805105813' \
  --run-script tools/s4cmd/adapter/run.sh \
  --bucket noaa-normals-pds --region us-east-1 --prefix normals-hourly/ \
  --auth anonymous \
  --out tools/s4cmd/receipts/smoke/_capability/anon-nocredentials
```

Swap `--mode` for any row in `run.sh`'s `case` (`recursive`, `shallow`,
`show-directory`, `du`); under `CREDS=none` every mode blocks identically at
client construction. The image ref requires the local `registry:2` to be running
(see Image above). The adapter scripts and everything under
[`../research/`](../research/) and [`../receipts/`](../receipts/) are immutable
inputs to this page — a new receipt is added rather than editing an existing one.

## Architecture matrix

| Channel | amd64 | arm64 | Notes |
| --- | --- | --- | --- |
| Upstream image | — | — | none published |
| Upstream Dockerfile | — | — | none |
| Prebuilt binary | — | — | none (pure Python) |
| Source (pip) | native | native | arch-independent; rides the multi-arch Python base image |

s4cmd is pure Python, so it is architecture-neutral: it runs natively on whichever
arch the Python base supports (claim `pure-python-architecture-neutral`). **Smoke
ran on arm64, native, no emulation** (the runner is aarch64). amd64 is the
expected benchmark common denominator and s4cmd supports it natively — flagged in
the open questions only for consistency, with no s4cmd-specific arch risk
expected, though no amd64 build or run was performed.
