# s3p

[s3p](https://github.com/generalui/s3p) ("S3 Parallel") lists an S3 bucket and prints one object key per line, discovering the keyspace by recursive bisection — synthetic midpoint keys and two concurrent ListObjectsV2 calls per range node — instead of the serial continuation-token page loop used by the other tools in this survey. By this study's reading it is the only keyspace bisector among the tools surveyed here — a comparative claim this study has not audited.
It is an upstream tool published by GenUI; this study tested the npm distribution unmodified rather than a fork.
This study's groundwork is complete; no benchmark comparison has been run.

## At a glance

| Question | Current answer |
| --- | --- |
| Tested subject | The upstream npm distribution `s3p@3.7.2` (the tool's own `version` self-report, captured in the build note), while `[SRC]` anchors use git tag v3.6.0 (`5a23b22e`). Full canonical identity is in [`data/tool.json`](data/tool.json). |
| Exercised coverage | None completed. `ls`, `ls --raw`, and `summarize` were attempted as capability probes and all blocked at authentication; `ls --long` shares the code path and is blocked by inheritance. |
| Correctness | No verifier verdict exists because no mode produced a listing to verify; verification is recorded as blocked. |
| Smoke observation | Blocked, not skipped. The `ls`, `ls --raw`, and `summarize` probes each failed at AWS-SDK credential resolution with exit 1 before any LIST completed — s3p has no anonymous access path. `ls --long` and the other modes share that code path and are blocked by inheritance rather than re-run, so the block is command-independent across the modes probed. No listing was produced. See [`Running details`](docs/running.md#the-blocked-smoke-state--every-capability-receipt). |
| Results | No benchmark or comparative result exists. s3p is benchmark-eligible only if list-scoped credentials are supplied. |

## How it works

Every read command funnels into one recursive engine that discovers an unknown
keyspace by bisection rather than continuation-token paging. For each range it
computes a synthetic midpoint key from a fixed 95-character alphabet — without
sampling any real keys — and issues two concurrent ListObjectsV2 calls, one from
the range start and one from the midpoint, recursing a half only when its page
came back full. A LIFO worker pool (default `--list-concurrency 100`) caps how
many ranges are in flight. The CLI `ls` path streams keys as they arrive, while
the library API accumulates them. Full detail:
[`docs/mechanism.md`](docs/mechanism.md).

## Modes and study coverage

The [upstream](https://github.com/generalui/s3p) listing surface and this study's
actual coverage are shown in separate columns.

| Mode | Upstream purpose | What this study exercised |
| --- | --- | --- |
| `ls` | List a bucket and print one key per line via bisection. | Attempted as a capability probe; blocked at AWS-SDK credential resolution before any LIST. |
| `ls --raw` | Print one JSON `listObjectsV2` Contents element per line. | Attempted as a capability probe; blocked at auth. Its normalizer is validated only against synthetic fixtures. |
| `ls --long` | Print a human date, human-rounded size, and key per line (lossy). | Not re-run; blocked by inheritance from the `ls` receipts. Lossy, so not a verification mode. |
| `summarize` | Emit an aggregate report with no per-object records. | Attempted as a capability probe on a genuinely different subcommand; blocked at auth. |
| `compare` / `each` / `map` | Two-bucket diff and the library primitives underlying `ls`. | Not run; out of scope for a single anonymous smoke bucket. |

`cp`, `sync`, and `delete` are mutating and excluded by the study guardrails.
Detailed mode and source coverage is in
[`docs/mechanism.md`](docs/mechanism.md#modes-and-output-contracts), while the
blocked smoke coverage is in
[`docs/running.md`](docs/running.md#the-blocked-smoke-state--every-capability-receipt).

## What we learned

Each finding links its owning explanation and its canonical claim ID; claim IDs
resolve in [`data/claims.json`](data/claims.json).

- **s3p cannot make anonymous requests, and that blocks every listing mode.** The
  `S3Client` is built without any credentials or unsigned hook, so under
  credential-starved smoke the `ls`, `ls --raw`, and `summarize` probes all failed
  at credential resolution with exit 1 before any LIST. Timing s3p against a public
  bucket therefore needs supplied credentials.
  [`The blocked smoke state`](docs/running.md#the-blocked-smoke-state--every-capability-receipt)
  · `no-anonymous-access-path`, `anonymous-listing-blocked-at-auth`

- **Listing is a first-class, isolable operation.** `ls` and `summarize` are
  standalone, non-mutating, list-only subcommands, and the `ls` probe issued real
  `listObjectsV2` calls before the auth failure. This corrects the inherited page's
  worry that listing might not be separately invokable.
  [`The core loop`](docs/mechanism.md#the-core-loop--s3comprehensionseach--eachrecursive)
  · `listing-is-isolable`

- **Parallelism is bought with a hard 95-character-set restriction.** Bisection
  needs a known alphabet, so a key with any character outside space `0x20` through
  `~` `0x7E` makes `getBisectKey` throw. This is a source-derived correctness
  boundary that contradicts an earlier secondhand "works on any keyspace" claim; it
  has not been exercised against a live edge-case bucket.
  [`Keyspace division`](docs/mechanism.md#keyspace-division--arithmetic-bisection-over-a-fixed-95-char-alphabet)
  · `non-ascii-key-throws`, `non-ascii-runtime-behavior`

- **The published throughput numbers are all author self-reports.** The ~20K and
  ~35K items/s figures and the conflicting 5-to-50-times and 15-to-100-times
  multipliers trace to the author with no third-party reproduction, and nothing has
  been benchmarked here.
  [`Concurrency`](docs/mechanism.md#concurrency--a-lifo-worker-pool-default-100)
  · `throughput-numbers-are-author-self-reported`, `throughput-numbers-reproduced`

- **The published v3.6.0 artifact cannot start, and git tags lag npm.** A clean
  install of the tagged v3.6.0 throws `Cannot find module 'colors'`; npm `latest`
  is 3.7.2, which has no corresponding git commit, so a reader trusting GitHub
  releases would pin a broken version.
  [`Version choice`](docs/running.md#image-study-authored)
  · `v3-6-0-cannot-start`, `git-tags-lag-npm`

## Limitations and open questions

### Coverage gaps

- Supply list-scoped credentials (or exclude s3p from anonymous-bucket runs and
  note why) so a real listing can be timed and verified.
- Exercise the character-set boundary and UTF-16 ordering divergence with an
  edge-case bucket; `EDGE_BUCKET` was not configured.
- Exercise `compare`, `each`/`map`, and the library API memory path, which the CLI
  harness cannot reach.

### Harness and verifier blockers

- No mode produced a listing, so the manifest verifier was never exercised;
  verification is blocked, not passed.
- The `normalize.sh` mode contracts are source-derived and validated only against
  synthetic adapter fixtures; canonical claim
  `normalize-validated-against-synthetic-fixtures` in [`data/claims.json`](data/claims.json).
- `ls --long` is lossy and cannot serve verification; use `ls --raw`.

### Benchmark questions

- Where does throughput plateau as `--list-concurrency` sweeps, given the single-core
  Node event loop?
- How much LIST work is wasted per unique key, counted as HTTP requests rather than
  the logical `listRequests` counter?
- Does the reported ~100M-object OOM reproduce, and on which code path?
- Does s3p survive sustained throttling given it relies on the AWS SDK's default
  retries?

## Navigate this directory

| If you want to… | Go to… |
| --- | --- |
| Understand the bisection, concurrency, memory, output, and failure model | [`docs/mechanism.md`](docs/mechanism.md) |
| See how the image was built and exactly why every mode is blocked | [`docs/running.md`](docs/running.md) |
| Inspect canonical identity, tested-subject, eligibility, and claim status data | [`data/tool.json`](data/tool.json) and [`data/claims.json`](data/claims.json) |
| Integrate the subject with the shared harness, or read the synthetic QA fixtures | [`adapter/`](adapter/) and [`adapter/fixtures/`](adapter/fixtures/) |
| Build the local subject image | [`build/Dockerfile`](build/Dockerfile) |
| Audit how every old ledger row and status-bearing prose claim became atomic current claims | [`research/claims-migration.md`](research/claims-migration.md) and the preserved reconciliation in [`research/`](research/) |
| Read the historical pre-restructure landing page | [`research/tool-page.md`](research/tool-page.md) — frozen historical research, not the current entry point |
| Inspect the observations and immutable run records | [`receipts/`](receipts/) |

## Provenance

**Mixed provenance.** This page combines firsthand source-and-run groundwork —
the study-authored image, the three capability receipts, the corrected
license/alphabet/code-anchor facts, and the additive auth, version-channel, and
CLI-surface findings — with inherited secondhand notes compiled from blog posts,
GitHub issues, and source reading. The seed was **not a run record**. See the
frozen [`research/tool-page.md`](research/tool-page.md) and the row-by-row
[`research/reconciliation.md`](research/reconciliation.md).

## Evidence boundary

Source and documentation explain mechanisms and risks; only a committed receipt
confirms run-dependent study behavior. The one receipt-confirmed runtime fact
here is that the anonymous probes failed at credential resolution — not any
listing behavior. Smoke observations are not benchmark results, and no benchmark
has been run.
