# Swath (Go port)

The [Go port of Swath](https://github.com/varveio/swath/tree/a2efe16e45d66088f6447e9723a119e5aebd4a91/go)
lists an S3 bucket and writes the listing as a globally key-sorted Parquet
dataset or as one TSV file, by expanding a tree of key ranges in parallel and
walking it in key order, so rows arrive sorted with no sort afterwards. It is a
separate tool from the Java [Swath](../swath/README.md), with its own command
line and output format, and it has no release yet: the subject here is a pinned
unreleased commit on upstream's main. Swath is built by Varve, which also
maintains this study; see [Varve and Swath](#varve-and-swath).

> **Study status (2026-09-scale-diagnostics).** This tool's standing in the current release:
> Not in the release: the Go port has never run in this study, so it has no row.
> The release is diagnostic: no attempt in it carries `purpose = measurement`, so
> nothing here is a calibrated benchmark or a ranking. Report and data:
> [`results/2026-09-scale-diagnostics/REPORT.md`](../../results/2026-09-scale-diagnostics/REPORT.md).

## In the current release

None. The release `2026-09-scale-diagnostics` has no row for the Go port and
no fixture was attempted: the port was registered with the harness after the
release was cut. The Swath rows in that release are the Java tool's. The
campaign plans that would run it are listed in
[`docs/running.md`](docs/running.md#plans). Everything below is the groundwork
layer.

## At a glance

| Question | Current answer |
| --- | --- |
| Tested subject | Upstream source at commit `a2efe16` (the merge of pull request varveio/swath#255 into main), built from GitHub's checksum-pinned archive of that commit with upstream's own static build line, the revision stamped in; no fork, no patch. Rows run before the pin moved name the pull request's head, `7b05605`, in their receipts. Canonical identity: [`data/tool.json`](data/tool.json). |
| How it runs | `swath list s3://… -o <sink>/listing` (the sorted dataset), `-o <sink>/listing.tsv` or `-o <sink>/listing.tsv.zst` (one TSV file), each with `--report <sink>/swath-go-report.json --workers N`, and `swath resume <sink>/listing` for a killed dataset run ([`docs/running.md`](docs/running.md#adapter-and-harness-contract)). The port is registered in the toolbox. |
| Exercised coverage | No committed run record. Twenty replay rows and three live S3 rows were run through the harness on 2026-11-07 from the earlier `7b05605` bundle (campaign ledger); until their receipts are committed, nothing on this page rests on them. |
| Correctness and verifier state | No verifier verdict and no completeness check exist. |
| Results | None. Nothing here is a benchmark result or a comparison. |

## How it works

Workers expand key ranges into rows that are already ascending plus child
ranges that sort between them; the order stage walks that tree depth-first, so
rows stream to the writer in global key order. `--workers` (default 64) is both
the goroutine count and the maximum of an AIMD request limiter that starts at
that maximum and backs off on throttle answers, so it is a ceiling, not a
setpoint. Rows beyond `--row-budget` spill to disk. A dataset's parts are
published as they close and `manifest.json`, written last, is the commit; a
journal lets `swath resume` continue an interrupted dataset run. A TSV run
writes one file and keeps no journal. Full account:
[`docs/mechanism.md`](docs/mechanism.md).

## Modes and study coverage

| Mode | Upstream purpose | What this study exercised |
| --- | --- | --- |
| `swath list s3://… -o DIR` (adapter mode `recursive-parquet-sorted`) | List a bucket or prefix into a format-2 sorted dataset. | Not run against S3 in either layer. |
| `swath resume DIR` (adapter mode `resume-parquet-sorted`) | Continue an interrupted S3 dataset run from its journal. | Not run in either layer. |
| `swath list s3://… -o FILE.tsv[.zst]` (adapter modes `recursive-tsv`, `recursive-tsv-zstd`) | List into one TSV file shaped as the S3 API's object, optionally zstd-compressed. | Not run against S3 in either layer. |
| `swath list --count-only`, stdout, JSONL, the table | Count only; other text renderings. | Not adapted: the worker refuses a run that publishes no product, and the TSV file carries every field the others do. |
| `swath list` on a local directory or `gs://` | List a filesystem tree or a GCS bucket. | Out of scope: the study lists S3. |
| `swath show`, `swath diag list` | Inspect a dataset; diagnostic comparisons. | `diag list` only when a plan states `structure`; never run. |

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
- **The port's products are not the Java tool's.** Format 2 has no `_SUCCESS`
  and no `row_type`, and the TSV output is one file in the S3 API's shape where
  Java wrote a directory of parts, so each needs its own reader.
  [`Output`](docs/mechanism.md#output)
  · `output-is-a-format-2-sorted-dataset`, `text-output-is-one-file-shaped-as-the-s3-api`
- **TSV keys are escaped, reversibly.** Control bytes and backslashes are
  written as JSON string escapes, which the normalizer reverses exactly and
  refuses anything else.
  [`Output`](docs/mechanism.md#output) · `text-keys-use-reversible-json-escapes`

## Limitations and open questions

### Coverage gaps

- Nothing has run: every runtime proposition is `unverified` — claims
  `lists-a-bucket-completely`, `resume-after-kill-completes`,
  `throughput-and-memory-at-scale`, `signed-path-unexercised`.

### Harness and verifier blockers

- No image built from this recipe is recorded, and no harness attempt exists —
  claims `image-builds-from-the-pinned-recipe`, `adapter-runs-under-the-harness`.
  Kill-and-resume needs a harness field that is not on `main`
  ([`docs/running.md`](docs/running.md#what-the-harness-cannot-do-yet)).

### Tool risks

- The subject is an unreleased commit, not a release — claim
  `subject-is-an-unreleased-main-commit`.
- Upstream asks a hosted runner for a finite `--max-duration`, which the default
  does not provide — claim `hosted-runner-needs-a-finite-max-duration`.

### Benchmark questions

- How the limiter behaves under real throttling, and whether the Go and Java
  tools agree on the same bucket — claims `limiter-under-real-throttling`,
  `go-and-java-datasets-agree`. Comparisons with other listers live in
  [`docs/open-questions.md`](../../docs/open-questions.md).

## Navigate this directory

| If you want to… | Go to… |
| --- | --- |
| Understand ranges, the limiter, output and resume | [`docs/mechanism.md`](docs/mechanism.md) |
| See the build, the harness contract, the plans and how the adapter was checked | [`docs/running.md`](docs/running.md) |
| Inspect canonical identity and the claim ledger | [`data/tool.json`](data/tool.json) and [`data/claims.json`](data/claims.json) |
| Integrate the subject with the shared harness | [`adapter/`](adapter/) |
| See the pinned build recipe | [`build/`](build/) |
| See what was read and executed, and how this derivation departs from the usual method | [`research/derivation.md`](research/derivation.md) |
| Find run records | [`receipts/`](receipts/) (empty: no receipt committed yet) |

## Provenance

**Firsthand, from public material only.** Every claim resolves to a public
source line or document at commit `a2efe16`, checked mechanically; no
Swath-internal notes or benchmark history were used. The derivation was not
blind (its author had read the Java Swath pages); it has had one independent
review, whose findings are addressed, and awaits the owner's review;
[`research/derivation.md`](research/derivation.md) says so in full. The executions, a local build used to make the adapter's test
fixtures over a synthetic store and a local toolbox build, are described in
[`docs/running.md`](docs/running.md#adapter-fixtures); each is not a run record.

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
no committed run record and that its pin is an unreleased commit — claim `first-party-source-basis`.
