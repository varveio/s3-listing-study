# pS3

[pS3](https://github.com/jboothomas/ps3) ("parallel S3") is a Go command-line tool that lists an entire S3 bucket by discovering key prefixes through a brute-force character walk and then paginating them in parallel, printing each object as a plain text line.
It is an unmaintained single-author project that cuts no releases or tags and is licensed GPL-3.0; this study reviewed and ran the upstream project itself at pinned default-branch HEAD `9428492`, not a fork.
This study's groundwork is complete; no benchmark comparison has been run.

## At a glance

| Question | Current answer |
| --- | --- |
| Tested subject | pS3 v0.1.16 at pinned upstream HEAD `9428492` (no fork). The source does not compile, so the study runs upstream's committed prebuilt amd64 binary `pS3.0-1-16` inside a study container, under qemu emulation on an arm64 runner. Full canonical identity is in [`data/tool.json`](data/tool.json). |
| Exercised coverage | No listing was produced. Only capability and build probes ran: `--version`/`--help`, one anonymous `list-objects-v2` attempt, and a native source-compile attempt. `list-object-versions` and `head-objects` were not run. |
| Correctness / verifier | Blocked. pS3 has no unsigned request path, so under `CREDS=none` every listing mode is blocked and the verifier had no output to check. |
| Results | No benchmark or comparative result exists. |
| Smoke observation | The one anonymous `list-objects-v2` attempt exited 1 with no listing; separately, an unpromoted observation recorded a bare no-credentials environment returning exit 0 with empty output. These are single-run groundwork facts, not benchmark results — both are claimed and cited under [What we learned](#what-we-learned). |

## How it works

Starting from the empty prefix, pS3 issues `ListObjectsV2` for each character in a
fixed 81-character alphabet and, when a prefix returns a full page (more than 999
keys), recurses by appending the next character, launching each descent in its own
goroutine. The accumulated large prefixes are paginated in parallel under a package
`var maxSemaphore = 256`, while the discovery goroutines themselves are unbounded;
objects stream through a channel to 256 printer workers. There is no delimiter,
prefix, or max-keys flag, so it lists whole buckets only, and it has no unsigned
request path. Full detail: [`docs/mechanism.md`](docs/mechanism.md).

## Modes and study coverage

The upstream subcommand surface and this study's actual coverage are shown
separately. Every mode is blocked under `CREDS=none` because pS3 cannot list
anonymously.

| Mode | Upstream purpose | What this study exercised |
| --- | --- | --- |
| `list-objects-v2` | Brute-force prefix fan-out over ListObjectsV2, recursive, full bucket only. | One anonymous attempt, exit 1, no listing; source read. |
| `list-object-versions` | List object versions through the ListObjectVersions API. | Not run; blocked by the same auth wall. Command source is absent from the checkout. |
| `head-objects` | List and HEAD every object (inferred from the name and a helper). | Not run; command source is absent from the checkout. |
| `list-test` | Unmodified cobra scaffold; a development placeholder. | Not run. |

## What we learned

Each finding links its owning explanation and its canonical claim ID; claim IDs
resolve in [`data/claims.json`](data/claims.json).

- **pS3 has no unsigned or anonymous request path.** It builds its session with
  the default shared-config credential chain only, so under `CREDS=none` every
  listing mode is blocked; the one committed receipt settles narrowly that the
  anonymous `list-objects-v2` attempt exited 1 with no listing.
  [`Failure surface`](docs/running.md#capability-receipts)
  · `no-unsigned-request-path`, `list-anon-exit-1-narrow`

- **With bare no-credentials it can exit 0 with empty output — a false
  success.** An observation (not a committed receipt) recorded pS3 exiting 0 with
  zero objects and no error when the environment lacks a config-file redirect, a
  result a caller cannot distinguish from an empty bucket.
  [`Failure surface`](docs/mechanism.md#failure-surface)
  · `silent-exit-0-on-bare-no-creds`

- **The pinned source does not compile and the shipped binary is not
  reproducible from it.** A native build attempt failed on missing imports and a
  selector error, and the committed binary exposes three subcommands whose source
  is absent from the checkout.
  [`Source-build failure`](docs/running.md#source-build-failure)
  · `source-does-not-compile`, `binary-not-reproducible-from-source`

- **Listing concurrency cannot be capped.** The pager is a package `var` of 256
  and prefix discovery spawns unbounded goroutines, with no flag for either, so
  pS3 cannot run inside the shared `CONCURRENCY_CAP` of 8.
  [`Concurrency`](docs/mechanism.md#concurrency-a-256-pager-var-unbounded-discovery)
  · `discovery-goroutines-unbounded`, `no-concurrency-flag-uncappable`

- **The fixed 81-character alphabet silently drops out-of-alphabet keys.** Because
  discovery only extends prefixes with bytes from that set, any key whose next
  distinguishing byte falls outside it is never discovered — a source-level
  correctness gap not yet demonstrated at runtime.
  [`Keyspace division`](docs/mechanism.md#keyspace-division--a-brute-force-character-walk)
  · `alphabet-is-fixed-81-char-var`, `out-of-alphabet-keys-dropped`

## Limitations and open questions

### Coverage gaps

- No listing ran; `list-object-versions` and `head-objects` were never exercised
  and their command source is absent from the checkout.
- Out-of-alphabet key handling needs an edge fixture (`EDGE_BUCKET=none`,
  deferred) and credentials to demonstrate at runtime.

### Harness and verifier blockers

- pS3 has no unsigned path, so under `CREDS=none` it produces no listing and the
  shared verifier has nothing to check.
- Listing concurrency is uncappable, so pS3 cannot run inside the shared
  `CONCURRENCY_CAP`; participation needs a solo window or a package-var patch.
- The tested binary is amd64-only and ran under qemu emulation; benchmark timing
  needs a natively common architecture.

### Benchmark questions

- Do the author's blog figures (about 94,000 objects per second, roughly 7x versus
  aws s3api and 5x versus s5cmd, on local non-AWS S3) reproduce? They are
  internally checkable because aws-cli and s5cmd are study subjects.
- What is the discovery-LIST tax at scale on a sparse deep-shared-prefix keyspace,
  and how does `--prefix-count` trade discovery overhead against parallelism?
- How do memory and throughput behave with the in-memory prefix list and 256
  workers at millions of keys?

### Tool risks to test

- Reproduce the silent exit-0 false success and the out-of-alphabet key drop.
- Exercise the source-located region-selection bug, the error-swallowing
  nil-dereference in the discovery helper, and the `--debug`/`--trace` output
  suppression.

## Navigate this directory

| If you want to… | Go to… |
| --- | --- |
| Understand the discovery, concurrency, memory, output, and failure model | [`docs/mechanism.md`](docs/mechanism.md) |
| See how the container was built and why listing runs were blocked | [`docs/running.md`](docs/running.md) |
| Inspect canonical identity, tested subject, study states, and claim status | [`data/tool.json`](data/tool.json) and [`data/claims.json`](data/claims.json) |
| Integrate the subject with the shared harness | [`adapter/`](adapter/) |
| Read the study container recipe | [`build/Dockerfile`](build/Dockerfile) |
| Audit how every old ledger row and status-bearing prose claim became atomic current claims | [`research/claims-migration.md`](research/claims-migration.md) and the preserved reconciliation in [`research/`](research/) |
| Read the historical pre-restructure landing page | [`research/tool-page.md`](research/tool-page.md) — frozen historical research, not the current entry point |
| Inspect the observations and immutable run records | [`receipts/`](receipts/) |

## Provenance

**Mixed provenance.** This page combines firsthand source-and-run groundwork — an
independent clone and full source read of the pinned checkout, committed
capability and build receipts, and labeled observations — with inherited
secondhand material: the mechanism and weakness-hypothesis narrative and the
third-party blog numbers. The seed was source-read only and is **not a run record**.
Groundwork corroborated the mechanism against source but promoted no
behavior on that basis alone. See [`research/tool-page.md`](research/tool-page.md)
and [`research/reconciliation.md`](research/reconciliation.md).

## Evidence boundary

Source and documentation explain mechanisms and risks; only a committed receipt
confirms run-dependent behavior, and here that is limited to the anonymous
exit-1 attempt, the version self-report, and the source-compile failure.
Observations, including the bare-environment exit-0 note, are not receipts, and no
benchmark result exists.
