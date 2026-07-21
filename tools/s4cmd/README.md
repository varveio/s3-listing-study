# s4cmd

[s4cmd](https://github.com/bloomreach/s4cmd) is a single-file Python "super S3 CLI" that lists a bucket by walking its pseudo-directory tree and printing one plain-text `ls` line per object, parallelising the walk through client-side delimiter recursion rather than a caller-supplied shard list.
It is the canonical bloomreach project rather than a fork, and its last release (`2.1.0`) is dormant — the tag is 2018-era — though it still installs and runs under a current boto3.
This study's groundwork is complete; no benchmark comparison has been run.

## At a glance

| Question | Current answer |
| --- | --- |
| Tested subject | Upstream `bloomreach/s4cmd` at pinned commit `80059bf` (release tag `2.1.0`), built into a local `python:3.7-slim` image with a 2018-era boto3 pin and run under the shared harness. Full canonical identity is in [`data/tool.json`](data/tool.json). |
| Exercised coverage | **None of the four listing modes ran.** s4cmd has no unsigned/anonymous access, and this campaign was credential-less (`CREDS=none`), so every mode is *blocked, not skipped*. Source was read at the pinned commit. |
| Correctness | Not attempted — no listing mode produced keys, so there was nothing for the verifier to check. |
| Capability finding | A receipted harness probe of `recursive` exited 1 at client construction, before any S3 request; canonical claim `recursive-blocked-without-credentials`. This is the block itself, not a listing run. |
| Results | No benchmark or comparative result exists, and none can be produced anonymously; s4cmd requires credentials to list. |

## How it works

s4cmd always sends `Delimiter='/'` and recurses client-side: every discovered
pseudo-directory (`CommonPrefix`) is re-queued as a new thread-pool task, so a
recursive listing performs one paginated legacy `list_objects` (v1) traversal (a
continuation chain) per pseudo-directory and parallelises across the tree's branching — not across a
caller-supplied shard list, and not automatically for a flat delimiter-free
prefix. Every matching object is accumulated in memory, then the whole result is
sorted client-side and printed at the end, so peak memory scales with key count
and output is not in S3 return order. Full detail:
[`docs/mechanism.md`](docs/mechanism.md).

## Modes and study coverage

The [upstream](https://github.com/bloomreach/s4cmd) mode surface and this study's
actual coverage are shown in separate columns. Every mode rides the same
`s3walk`/`list_objects` engine and was blocked for the same reason.

| Mode | Upstream purpose | What this study exercised |
| --- | --- | --- |
| `ls -r` (recursive) | Walk the full subtree with per-directory fan-out; one line per object. | Blocked: a receipted probe failed at client construction (no credentials). Mechanism read from source. |
| `ls` (shallow) | List one level: immediate objects plus subdirectories as `DIR`. | Blocked by source inference — shares the same credential-less constructor path. Not run. |
| `ls -d` (show-directory) | Show the directory entry itself instead of its contents. | Blocked by the same shared constructor path. Not run. |
| `du -r` | Same recursive walk; output is aggregate size, not per-key. | Blocked by the same shared constructor path. Not run. |

The upstream tool also exposes a thread-count knob (`-c/--num-threads`, default
`cpu_count*4`), retry controls, and an endpoint override; their presence does not
mean the study exercised them. Detailed mode and source coverage is in
[`docs/mechanism.md`](docs/mechanism.md#modes-and-tunables); the blocked-smoke
state and reproduction are in
[`docs/running.md`](docs/running.md#smoke-state--every-listing-mode-is-blocked-not-skipped).

## What we learned

Each finding links its owning explanation and its canonical claim ID; claim IDs
resolve in [`data/claims.json`](data/claims.json).

- **Parallelism is client-side delimiter recursion, not prefix sharding.** s4cmd
  re-queues each discovered pseudo-directory onto the thread pool, so a single
  prefix with `/`-substructure parallelizes with no caller sharding, while a flat
  delimiter-free prefix collapses to one serial scan — correcting the inherited
  "threadpool across CLI-supplied prefixes" model.
  [`Listing is parallel`](docs/mechanism.md#listing-is-parallel--but-by-client-side-delimiter-recursion-not-sharding)
  · `parallelism-unit-is-delimiter-recursion`

- **No unsigned access — every listing mode is blocked, not skipped.** s4cmd
  builds its boto3 client without `signature_version=UNSIGNED`, so a
  credential-less run cannot list; a receipted `recursive` probe fails at client
  construction before any S3 request.
  [`Smoke state`](docs/running.md#smoke-state--every-listing-mode-is-blocked-not-skipped)
  · `no-unsigned-request-support`, `recursive-blocked-without-credentials`

- **Memory is the limit: accumulate-then-sort-then-dump.** Nothing is streamed —
  every object is held in memory and the whole list is sorted client-side before
  printing — so peak memory scales with key count and the OOM ceiling is an open
  benchmark question.
  [`Memory model`](docs/mechanism.md#memory-model--accumulate-then-sort-then-dump)
  · `accumulate-then-sort-then-dump`, `memory-ceiling-oom-unverified`

- **The "won't import under modern boto3" fear was wrong.** s4cmd 2.1.0 installs,
  imports, and runs under the latest botocore, so the image's 2018-era boto3 pin
  is a reproducibility choice, not a compatibility necessity.
  [`Image`](docs/running.md#image)
  · `installs-imports-runs-under-current-boto3`

- **Retry can silently duplicate keys (hypothesis).** A retryable error re-queues
  the whole directory from page one without rolling back already-appended objects
  or queued child directories, so a successful retry can emit duplicate keys — a
  source-derived risk that would surface as a verifier `FAIL` at scale, not
  observed at smoke.
  [`Retry`](docs/mechanism.md#retry--backoff--timeout)
  · `retry-can-duplicate-keys`

## Limitations and open questions

### Coverage gaps

- Every listing mode is blocked without credentials; a credentialed run is
  required to exercise `recursive`, `shallow`, `show-directory`, and `du` and to
  sweep the `-c` thread count within the aggregate concurrency cap.
- Edge-key fidelity is deferred (`EDGE_BUCKET=none`): unicode, weird-key, and
  multipart-ETag behavior were not exercised.

### Verifier and capability blocker

- s4cmd cannot make unsigned requests, so no anonymous listing produced output
  and the standard verifier was never engaged.
- s4cmd `rstrip()`s each output line, so a key with a trailing space or a newline
  cannot be represented faithfully — a tool-side output limit, canonical claim
  `key-byte-fidelity-tool-side-loss`. The `normalize.sh` adapter was exercised
  only on synthetic fixtures, not real tool output.

### Benchmark questions

- How does delimiter-recursion parallelism scale with `-c` and tree shape, and
  how many LIST pages does `ls -r` issue versus a flat scan?
- At what key count does accumulate-then-sort exhaust memory, and how does peak
  RSS scale with N?
- How does s4cmd behave under throttling (503 SlowDown is not in its own
  retryable set) and what is the client-CPU cost of per-line formatting plus a
  full sort at large N?

### Tool risks to test

- Reproduce or falsify retry-induced key duplication at benchmark scale.
- Confirm whether a single sub-delimiter-free prefix collapses to one serial
  thread regardless of `-c`.
- Determine whether s4cmd's legacy `list_objects` v1 path still lists correctly
  against live current S3; canonical claim `v1-current-s3-compatibility-unverified`.

## Navigate this directory

| If you want to… | Go to… |
| --- | --- |
| Understand the listing, parallelism, memory, retry, and output model | [`docs/mechanism.md`](docs/mechanism.md) |
| See how the image was built and exactly why every mode was blocked | [`docs/running.md`](docs/running.md) |
| Inspect canonical identity, tested-subject, eligibility, and claim status data | [`data/tool.json`](data/tool.json) and [`data/claims.json`](data/claims.json) |
| Integrate the subject with the shared harness | [`adapter/`](adapter/), whose synthetic parser checks live in [`adapter/fixtures/`](adapter/fixtures/) |
| Build the local subject image | [`build/Dockerfile`](build/Dockerfile) |
| Audit how every old ledger row and status-bearing prose claim became atomic current claims | [`research/claims-migration.md`](research/claims-migration.md) and the preserved reconciliation in [`research/`](research/) |
| Read the historical pre-restructure landing page | [`research/tool-page.md`](research/tool-page.md) — frozen historical research, not the current entry point |
| Inspect the observations and immutable capability records | [`receipts/`](receipts/) |

## Provenance

**Mixed provenance.** This page combines firsthand source, build, and capability
work with inherited secondhand notes compiled from a single thin prior-art
summary; that seed was **not a run record**. The **Language** and **License**
facts were read directly from the tool's public repository rather than the seed.
See [`research/tool-page.md`](research/tool-page.md) and
[`research/reconciliation.md`](research/reconciliation.md).

## Evidence boundary

Source and documentation explain mechanisms and risks; only a committed receipt
confirms run-dependent behavior. The receipt-backed `confirmed` facts here — the
`recursive` capability block (exit 1 before any request), the `-c/--num-threads`
flag's presence, s4cmd's install/import/run under a current boto3, and the
corrected `2.1.0` version self-report — are all build, startup, and capability
facts; none confirms listing correctness. No smoke listing observation and no
benchmark result exist.
