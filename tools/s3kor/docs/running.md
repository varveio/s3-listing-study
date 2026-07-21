# s3kor — running

How the pinned image was built, the blocked smoke state with both capability
receipts, what a credentialed run would need, and the supported-architecture
matrix. Canonical tested identity (pinned SHAs, versions, dates) lives in
[`../data/tool.json`](../data/tool.json); the root
[`../README.md`](../README.md) summarises it and this page assumes it. Evidence
labels, the canonical status vocabulary, and claim `some-id` references are as
in [`mechanism.md`](mechanism.md).

## Build — from source at the pinned tag

**Upstream ships neither a published image nor a Dockerfile** — distribution is
a Homebrew tap and goreleaser GitHub-release binaries [DOC `README.md:4-27`;
SRC `.goreleaser.yml`, `Makefile` @ 844fe3d; INFERRED from a Docker Hub + GHCR
search returning no `sethkor/s3kor` image on 2026-07-17 — absence of a found
result, not a cited document]. Per the study's Stage B ladder that puts s3kor
in the "neither image nor Dockerfile" case (claim
`no-upstream-image-or-dockerfile`), so
[`../build/Dockerfile`](../build/Dockerfile) is a best-effort recipe and the
**built image digest is the run's identity**.

Multi-stage build (`go install <module>@<pinned-commit>`, CGO disabled to match
upstream's `.goreleaser.yml`):

```sh
docker build -t s3kor:v0.0.37-study -f tools/s3kor/build/Dockerfile .
# build base:   golang@sha256:77f25981bd57e60a510165f3be89c901aec90453fd0f1c5a45691f6cb1528807 (golang:1.18-alpine)
# runtime base: alpine@sha256:6baf43584bcb78f2e5847d1de515f23499913ac9f12bdf834811a3145eb11ca1 (alpine:3.19)
# tool:         go install github.com/sethkor/s3kor@844fe3d7931fcca415c8b8a4e22f048886e6b82b
```

Built image digest (local — never pushed):
**`s3kor@sha256:b021869dfa78b7af85506a5d566ec6c7e7ed49d940b20d9e110a04fa5006f37c`**,
arch **arm64**.

**Version self-report.** The binary self-reports `dev-local-version none
unknown` because `go install` omits goreleaser's `-ldflags`
version/commit/date; identity is the pinned commit, and receipts carry
`--tool-version v0.0.37` explicitly (claims `reviewed-version-is-v0037` and
`reviewed-subject-pinned-commit`) [OBS
`--version`, captured `../receipts/smoke/_build/first-exec.txt`; direct run, not
wrapper-recorded]. The run.meta records `tool_version=v0.0.37`,
`tool_version_source=caller-supplied`.

**Live `--help` vs docs (Stage B first execution).** The container's `--help`
confirms the `ls` flag set is `--all-versions` only, and the global region flag
is **`--detect-region`** — the README's `--auto-region` is stale (claim
`region-flag-doc-drift`; see [`mechanism.md`](mechanism.md) § Doc-drift).
Nothing in live help contradicts the source read beyond that drift [OBS live
`--help`/`ls --help`, captured `../receipts/smoke/_build/first-exec.txt`; direct
run, not wrapper-recorded].

## Smoke — blocked, not skipped

**Auth: anonymous, credential-starved (enforced by the wrapper), `CREDS=none`.**
Both listing modes require signed requests — s3kor has no unsigned listing path
(claim `no-unsigned-listing-path`; see [`mechanism.md`](mechanism.md) § "No
unsigned path for listing"), so both are **blocked, not skipped** (claim
`credential-starved-listing-blocked`) — recorded with the failing invocation as
the receipt, per the study's auth protocol. No verifier verdict is possible (the
tool produced no listing to verify — `n/a — capability probe`).

| Mode | Invocation (argv appended to the image entrypoint `s3kor`) | Auth | Exit | Wall | Result | Receipt |
| --- | --- | --- | --- | --- | --- | --- |
| `list` | `ls --region us-east-1 s3://noaa-normals-pds` | anonymous | `2` | 0.058s | **BLOCKED** — panic at session build, 0 S3 requests | `../receipts/smoke/_capability/list/receipt.md` |
| `list-versions` | `ls --all-versions --region us-east-1 s3://noaa-normals-pds` | anonymous | `2` | 0.059s | **BLOCKED** — identical panic | `../receipts/smoke/_capability/list-versions/receipt.md` |

Both receipts: image
`s3kor@sha256:b021869dfa78b7af85506a5d566ec6c7e7ed49d940b20d9e110a04fa5006f37c`,
bucket `noaa-normals-pds` (us-east-1), manifest snapshot 2026-07-17 (sha256
`c78a…2adb`, 148,917 keys), arch arm64 native (not emulated). Memory
(`peak_rss`, `cgroup_peak_mem`) is `unavailable` — 0 successful samples, the
process panicked at startup; this is not zero and not a finding about the tool.

Observed panic (both modes, verbatim from `stderr.txt`, 456 bytes) [RUN]:

```
panic: WebIdentityErr: role ARN is not set

goroutine 1 [running]:
github.com/aws/aws-sdk-go/aws/session.Must(...)
	/go/pkg/mod/github.com/aws/aws-sdk-go@v1.30.16/aws/session/session.go:326
main.getAwsSession()
	/go/pkg/mod/github.com/sethkor/s3kor@v0.0.37/s3kor.go:190 +0x1b4
main.switchCommand({0x5fbf44, 0x2})
	/go/pkg/mod/github.com/sethkor/s3kor@v0.0.37/s3kor.go:204 +0x78
main.main()
	/go/pkg/mod/github.com/sethkor/s3kor@v0.0.37/s3kor.go:269 +0x16c
```

**Why this exact error.** The wrapper's credential-starved env empties the AWS
key/secret/token/role variables and points `AWS_WEB_IDENTITY_TOKEN_FILE` (and
the shared-credentials/config files) at a nonexistent in-container path.
aws-sdk-go v1.30.16 with `SharedConfigEnable` sees a web-identity **token-file**
set while `AWS_ROLE_ARN` is empty and returns `WebIdentityErr: role ARN is not
set`; `session.Must` turns that into a panic (claim `session-build-panic`) [SRC
`s3kor.go:190` @ 844fe3d]. The
panic occurs in `getAwsSession()`, *before* command dispatch, so it is
identical for both modes; the count of S3 LIST requests issued is **0**. The
**root** finding does not depend on this string: s3kor has no unsigned path for
the listing client, so under *any* credential-starved condition it cannot list
— this
environment simply makes it panic at startup rather than fail at request time.
(The two `AnonymousCredentials` paths that do exist — region detection and the
S3-to-S3 copy download — are not the listing client; see
[`mechanism.md`](mechanism.md) § "No unsigned path for listing".)
(the session-build-vs-request-time scope note is carried in
[`../README.md`](../README.md) and [`mechanism.md`](mechanism.md)).

**Verifier:** not run — there is no output to verify (0-byte stdout). Recursive
full-bucket and the three registry scoped-prefix checks (`normals-hourly/`,
`normals-monthly/1991-2020/`, `normals-annualseasonal/1981-2010/access/`) are
all blocked for the same reason.

**Adapter validation (non-mode evidence).** `normalize.sh` was self-tested on
synthetic fixtures for both modes, including keys containing spaces, confirming
the 5-field `key/-/-/-/-` contract (only the key column is populated; for
`list-versions` the leading version-id token is stripped; the leading-token
strip is manifest-comparable only on an unversioned bucket, claim
`list-versions-manifest-comparable-only-unversioned`). `run.sh` argv was
verified NUL-delimited and parameterized on bucket/region/prefix (no hardcoded
bucket) [OBS host self-tests, `../receipts/smoke/_adapter/self-test.txt`;
adapters never run on the measurement clock].

**Edge-case fidelity:** `EDGE_BUCKET=none` → deferred (unicode/weird-key/
size+ETag/multipart-ETag assertions), per registry.

## What a credentialed run would need

s3kor cannot participate in an anonymous-only (`CREDS=none`) benchmark — the
capability finding is a hard blocker, not a tuning gap. To list at all it needs
**resolvable AWS credentials** via the standard SDK-for-Go v1 chain (env vars →
shared `~/.aws/credentials`/`config` → container/instance role), selectable
with `--profile` [SRC `s3kor.go:179-197`]. A scoped, **list-only** credential
against the smoke bucket would be the minimal grant. With credentials present,
the quickstart is:

```sh
# Recursive list of a whole bucket:
s3kor --region us-east-1 ls s3://my-bucket
# List under a prefix:
s3kor --region us-east-1 ls s3://my-bucket/some/prefix/
# List all object versions + delete markers:
s3kor --region us-east-1 ls --all-versions s3://my-bucket
# Let s3kor discover the region itself (extra round-trips):
s3kor --detect-region ls s3://my-bucket
```

There is no listing concurrency, page-size, or hint knob to tune — the only
lever is supplying `--region` to avoid the pre-list region-detection
round-trips (see [`mechanism.md`](mechanism.md) § "No listing tunables").
Routing this decision to the owner is carried in [`../README.md`](../README.md)
§ "Limitations and open questions" (benchmark eligibility is `conditional` in
[`../data/tool.json`](../data/tool.json)).

## Reproduction via `harness/smoke-run.sh`

Both receipts were produced by the shared wrapper, never a bare `docker run`.
`run.sh` only *prints* the argv (NUL-delimited) that the wrapper appends to the
pinned image's entrypoint (`["/usr/local/bin/s3kor"]`); the wrapper owns
`docker run`, mounts, credential injection/starving, the timeout, and
measurement. To reproduce either row:

```sh
harness/smoke-run.sh \
  --tool s3kor --mode list \
  --image 's3kor@sha256:b021869dfa78b7af85506a5d566ec6c7e7ed49d940b20d9e110a04fa5006f37c' \
  --run-script tools/s3kor/adapter/run.sh \
  --bucket noaa-normals-pds --region us-east-1 \
  --auth anonymous \
  --out tools/s3kor/receipts/smoke/_capability/list
```

Swap `--mode list-versions` (and `--out …/list-versions`) for the other
receipt. Rebuilding the image first requires the pinned checkout at
`844fe3d7931fcca415c8b8a4e22f048886e6b82b` and the `docker build` above.
`adapter/run.sh`/`adapter/normalize.sh` and everything under `../research/` and
`../receipts/` are immutable inputs to this page; a rerun adds a new receipt
rather than editing an existing one.

## Architecture matrix

| Channel | amd64 | arm64 | Notes |
| --- | --- | --- | --- |
| Upstream published image | — | — | none exists |
| Prebuilt release binaries (goreleaser) | native | native | also 386, arm (v6/v7); darwin amd64+arm64. Windows arch is **ambiguous** — README lists amd64 only [DOC `README.md:13-21`] while `.goreleaser.yml` includes `arm` globally and ignores only windows arm64/386 [SRC `.goreleaser.yml` @ 844fe3d]; not settled here (the benchmark denominator is linux amd64/arm64, both native) |
| Source build (Go) | native | native | pure-Go, CGO off — cross-compiles to any Go target |

**What smoke ran on:** natively on the runner's **arm64** (aarch64), image arch
arm64, **not emulated** (claim `smoke-ran-arm64-native`) [RUN `run.meta`:
`image_arch=arm64 host_arch=arm64`]. For the benchmark's common-denominator
arch, **amd64** is the expected choice and s3kor supports it natively on every
channel (claim `amd64-native-support`) — flagged in
[`../README.md`](../README.md) § "Limitations and open questions".
