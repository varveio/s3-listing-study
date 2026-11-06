# Swath (Go port)

The [Go port of Swath](https://github.com/varveio/swath/tree/63a42568dbcfb2d3cdc18e3ccf7abfd69792ad77/go)
lists an S3 bucket and writes the listing as a globally key-sorted Parquet
dataset, by expanding a tree of key ranges in parallel and walking it in key
order, so rows arrive sorted with no sort afterwards. It is a separate tool
from the Java [Swath](../README.md) beside it, with its own command line and
output format, and it has no release yet: the subject here is a pinned head of
an unmerged upstream branch. Swath is built by Varve, which also maintains this
study; see [Varve and Swath](#varve-and-swath).

> **Study status (2026-09-scale-diagnostics).** This tool's standing in the current release:
> Not in the release: the Go port has never run in this study, so it has no row.
> The release is diagnostic: no attempt in it carries `purpose = measurement`, so
> nothing here is a calibrated benchmark or a ranking. Report and data:
> [`results/2026-09-scale-diagnostics/REPORT.md`](../../../results/2026-09-scale-diagnostics/REPORT.md).

## In the current release

None. The release `2026-09-scale-diagnostics` has no row for the Go port, no
fixture was attempted, and no larger one was scheduled because the port cannot
yet be selected by the harness
([what is missing](docs/running.md#what-the-harness-cannot-do-yet)). The
Swath rows in that release are the Java tool's. Everything below is the
groundwork layer.

## At a glance

| Question | Current answer |
| --- | --- |
| Tested subject | Upstream source at commit `63a4256` of the unmerged `spike/go-local-lister` branch, built from GitHub's checksum-pinned archive of that commit with upstream CI's static build line; no fork, no patch. The pin is **provisional** and moves to the merge commit when the branch merges. The binary reports `swath-go devel` with no revision, because an archive build carries none. Canonical identity: [`data/tool.json`](data/tool.json). |
| How it would run | `swath list s3://… -o <sink>/listing --report <sink>/swath-go-report.json --workers N`, and `swath resume <sink>/listing` for a killed run ([`docs/running.md`](docs/running.md#adapter-and-harness-contract)). The image has **not been built**. |
| Exercised coverage | None. No run against S3, no smoke, no harness attempt. |
| Correctness and verifier state | No verifier verdict and no completeness check exist. |
| Results | None. Nothing here is a benchmark result or a comparison. |

## How it works

Workers expand key ranges into rows that are already ascending plus child
ranges that sort between them; one drainer walks that tree depth-first, so
rows stream to the Parquet writer in global key order. `--workers` (default
64) is both the goroutine count and the maximum of an AIMD request limiter
that starts at that maximum and backs off on throttle answers, so it is a
ceiling, not a setpoint. Rows beyond a memory budget spill to disk. Parts are
published as they close, and `manifest.json`, written last, is the commit; a
journal lets `swath resume` continue an interrupted run. Full account:
[`docs/mechanism.md`](docs/mechanism.md).

## Modes and study coverage

| Mode | Upstream purpose | What this study exercised |
| --- | --- | --- |
| `swath list s3://…` (adapter mode `recursive-parquet-sorted`) | List a bucket or prefix into a format-2 sorted dataset. | Not run against S3 in either layer. |
| `swath resume DIR` (adapter mode `resume-parquet-sorted`) | Continue an interrupted S3 run from its journal. | Not run in either layer. |
| `swath list` on a local directory or `gs://` | List a filesystem tree or a GCS bucket. | Out of scope: the study lists S3. |
| `swath show`, `swath diag list` | Inspect a dataset; diagnostic comparisons. | Not used. |

## What we learned

Claim IDs resolve in [`data/claims.json`](data/claims.json). These are
groundwork findings from public source and documentation; none is a run
observation.

- **`--workers` is a ceiling, not a setpoint, and the port does not start where
  Java does.** The limit starts at `--workers` rather than Java's `min(4, N)`
  and falls by x0.7 on each throttle answer, so a run's achieved width is a fact
  about the run.
  [`Concurrency and the request limiter`](docs/mechanism.md#concurrency-and-the-request-limiter)
  · `workers-default-64-is-the-limiter-maximum`, `limiter-is-aimd-on-throttle-answers`
- **The manifest is the commit, and its coverage block is the tool's own word on
  completeness.** A directory without `manifest.json` is not a dataset even with
  parts in it, and the normalizer refuses it; coverage is upstream's statement,
  not a verification.
  [`Output`](docs/mechanism.md#output)
  · `manifest-is-the-commit`, `coverage-states-completeness`
- **Format 2 is a different product from the Java tool's Parquet.** No
  `_SUCCESS`, no `row_type`, and a format-1 reader refuses it, so the two
  Swath tools need separate readers.
  [`Output`](docs/mechanism.md#output)
  · `output-is-a-format-2-sorted-dataset`, `s3-schema-is-current-object-nine-columns`
- **A source build does not know its own revision.** The port reads its
  revision only from Go's VCS build info, so this study's build reports
  `devel`; the pinned archive is the identity.
  [`Build`](docs/running.md#build)
  · `source-archive-build-has-no-revision`

## Limitations and open questions

### Coverage gaps

- Nothing has run: every runtime proposition is `unverified` — claims
  `lists-a-bucket-completely`, `resume-after-kill-completes`,
  `throughput-and-memory-at-scale`, `signed-path-unexercised`.

### Harness and verifier blockers

- The image is unbuilt and the harness cannot yet select this subject — claims
  `image-builds-from-the-pinned-recipe`, `adapter-runs-under-the-harness`. What
  is missing (registration, run-report retention, resume wiring) is listed in
  [`docs/running.md`](docs/running.md#what-the-harness-cannot-do-yet).

### Tool risks

- The subject is an unmerged branch head and can move — claim
  `subject-is-an-unmerged-branch-head`.
- Upstream asks a hosted runner for a finite `--max-duration`, which the default
  does not provide — claim `hosted-runner-needs-a-finite-max-duration`.

### Benchmark questions

- How the limiter behaves under real throttling, and whether the Go and Java
  tools agree on the same bucket — claims `limiter-under-real-throttling`,
  `go-and-java-datasets-agree`. Comparisons with other listers live in
  [`docs/open-questions.md`](../../../docs/open-questions.md).

## Navigate this directory

| If you want to… | Go to… |
| --- | --- |
| Understand ranges, the limiter, output and resume | [`docs/mechanism.md`](docs/mechanism.md) |
| See the build, the harness contract, what is missing and how the adapter was checked | [`docs/running.md`](docs/running.md) |
| Inspect canonical identity and the claim ledger | [`data/tool.json`](data/tool.json) and [`data/claims.json`](data/claims.json) |
| Integrate the subject with the shared harness | [`adapter/`](adapter/) |
| See the pinned build recipe | [`build/`](build/) |
| See what was read and executed, and how this derivation departs from the usual method | [`research/derivation.md`](research/derivation.md) |
| Find run records | [`receipts/`](receipts/) (empty: nothing has run) |

## Provenance

**Firsthand, from public material only.** Every claim resolves to a public
source line or document at commit `63a4256`, checked mechanically; no
Swath-internal notes or benchmark history were used. The derivation was not
blind (its author had read the Java Swath pages) and has not yet had an
independent review; [`research/derivation.md`](research/derivation.md) says so
in full. The one execution, a local build used to make the adapter's test
fixture over a synthetic store, is described in
[`docs/running.md`](docs/running.md#adapter-fixture); it is not a run record.

## Evidence boundary

Source and documentation establish mechanisms and risks, never observed
behaviour. This page has no receipt, no smoke observation and no benchmark: no
claim is `confirmed`, and every runtime claim is `unverified` because nobody has
run the port against S3.

## Varve and Swath

Varve builds Swath, Java and Go, and maintains this study, which makes us
participants in the space we study. The Go port gets exactly the rules every
other tool gets: the same harness, run records and evidence vocabulary, and no
number from Varve's own development of the port is used here. The port is
added before it is released, which is why this page says plainly that it has
not run, that its pin is provisional and that its image is unbuilt — claim
`first-party-source-basis`.
