# Swath (Go port)

The [Go port of Swath](https://github.com/varveio/swath/tree/8c8f870801c700f22413581f35683096782329a0/go)
lists an S3 bucket and writes the listing as a globally key-sorted Parquet
dataset or as one TSV file, by expanding a tree of key ranges in parallel and
walking it in key order, so rows arrive sorted with no sort afterwards. It is a
separate tool from the Java [Swath](../swath/README.md), with its own command
line and output format. Upstream means it to replace the Java tool at release; it
has a release candidate but no release, and the subject here is a pinned
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
| Tested subject | Upstream source at commit `8c8f870` on main (2026-11-11), built from GitHub's checksum-pinned archive of that commit with upstream's own static build line and Go toolchain, the revision stamped in; no fork, no patch. It is newer than the port's first release candidate, `v0.4.0-rc.1`, which the capsule does not select. Rows run at the capsule's earlier pins name `7b05605` (the head of pull request varveio/swath#255) in their receipts. Canonical identity: [`data/tool.json`](data/tool.json). |
| How it runs | `swath list s3://… -o <sink>/listing` (the sorted dataset), `-o <sink>/listing.tsv` or `-o <sink>/listing.tsv.zst` (one TSV file), each with `--report <sink>/swath-go-report.json --s3-region R --workers N`, and `swath resume <sink>/listing` for a killed dataset run ([`docs/running.md`](docs/running.md#adapter-and-harness-contract)). The port is registered in the toolbox. |
| Exercised coverage | 111 harness attempts in seven diagnostic receipts under [`benchmark/receipts/`](../../benchmark/receipts/swath-go-main-8c8f870-cloud-20261111/README.md) (`swath-go-*`): 23 on 2026-11-07 from the earlier `7b05605` bundle, whose command line and products this pin no longer shares (one live S3 row on real-changesets; two live sentinel-cogs attempts cancelled with no result); 21 at this pin beside Java 0.3.2 on the release Java replay; and 67 on the qualified Go replay instrument (setting exploration, CPU profiles, a content check and 36 seeded confirmation attempts, 15 of them a later Swath-Go build, `c54179e`), run from unmerged scratch harness revisions that each receipt names verbatim. The claim ledger has not been re-evaluated against them (see [Coverage gaps](#coverage-gaps)). |
| Correctness and verifier state | No verifier verdict and no completeness check exist. Two content checks against Java 0.3.2 exist: an exact nine-column comparison of one FourCast pair, and on blockchain a probabilistic fingerprint plus key-order check of four retained products (not a row-by-row comparison, not all 36 attempts). |
| Results | Diagnostic only: per-attempt wall clock, subject CPU, peak RSS (GiB) and the replay instrument's timing grade, and Go/Java ratios computed from them for the configurations run. `TIMING_VALID` is the instrument's evidence rule, not calibration; nothing here is a calibrated benchmark, a ranking, or a claim that either tool is fully tuned. Each receipt's README states what it supports. |

## How it works

Workers expand key ranges into rows that are already ascending plus child
ranges that sort between them; the order stage walks that tree depth-first, so
rows stream to the writer in global key order. `--workers` (default 64) is both
the worker count and the maximum of an adaptive request limiter that starts at
that maximum and backs off when throttle answers pass a share of recent
answers, so it is a ceiling, not a setpoint. Rows beyond `--sort-buffer` spill
to disk. A dataset is a SeekParquet dataset: its parts are published as they
close and `manifest.json`, written last, is the commit; a journal lets `swath
resume` continue an interrupted dataset run. A TSV run writes one file of the
dataset's columns and keeps no journal. Full account:
[`docs/mechanism.md`](docs/mechanism.md).

## Modes and study coverage

| Mode | Upstream purpose | What this study exercised |
| --- | --- | --- |
| `swath list s3://… -o DIR` (adapter mode `recursive-parquet-sorted`) | List a bucket or prefix into a sorted swath (SeekParquet) dataset. | Not run against S3 in either layer at this pin. |
| `swath resume DIR` (adapter mode `resume-parquet-sorted`) | Continue an interrupted object-store dataset run from its journal. | Not run in either layer. |
| `swath list s3://… -o FILE.tsv[.zst]` (adapter modes `recursive-tsv`, `recursive-tsv-zstd`) | List into one TSV file of the dataset's columns, optionally zstd-compressed. | Not run against S3 in either layer at this pin. |
| `swath diag count`, `swath du`, stdout, JSONL, the table | Count only; totals; other text renderings. | Not adapted: the worker refuses a run that publishes no product, and the TSV file carries every field the others do. |
| `swath list` on a local directory, `gs://` or `az://`; `--all-versions` | List a filesystem tree, a GCS bucket or an Azure container; S3 versions. | Out of scope: the study lists S3 current objects. |
| `swath show`, `swath diag list` | Inspect a dataset; diagnostic comparisons. | `diag list` only when a plan states `structure` or `seed: false`; never run. |

## What we learned

Claim IDs resolve in [`data/claims.json`](data/claims.json). These are
groundwork findings from public source and documentation; none is a run
observation.

- **`--workers` is a ceiling, not a setpoint, and the port does not start where
  Java does.** The limit starts at `--workers` rather than Java's `min(4, N)`
  and falls by x0.7 when throttle answers reach 15 % of a recent window, so a
  run's achieved width is a fact about the run.
  [`Concurrency and the request limiter`](docs/mechanism.md#concurrency-and-the-request-limiter)
  · `workers-default-64-is-the-limiter-maximum`, `limiter-is-aimd-on-throttle-answers`
- **The manifest is the commit, and its coverage block is the tool's own word on
  completeness.** A directory without `manifest.json` is not a dataset even with
  parts in it, and the normalizer refuses it; coverage is upstream's statement,
  not a verification.
  [`Output`](docs/mechanism.md#output)
  · `manifest-is-the-commit`, `coverage-states-completeness`
- **The port's products are not the Java tool's, nor its own earlier pins'.**
  The dataset is a SeekParquet dataset with no `_SUCCESS` and no `row_type`, and
  the TSV output is one file of the dataset's columns where Java wrote a
  directory of parts, so each needs its own reader. Both changed shape between
  the capsule's `a2efe16` pin and this one.
  [`Output`](docs/mechanism.md#output)
  · `output-is-a-seekparquet-sorted-dataset`, `text-output-is-one-file-of-the-dataset-columns`
- **TSV fields are escaped, reversibly.** Control bytes, backslashes and bytes
  outside UTF-8 are written in the port's TSV escapes and a null as `\N`, which
  the normalizer reverses exactly and refuses anything else.
  [`Output`](docs/mechanism.md#output) · `text-fields-use-reversible-tsv-escapes`
- **The sort buffer now follows the box.** Its default is 1/16 of the memory
  the process can see, at most 1 GiB, where the earlier pins' was a fixed
  128 MiB: 512 MiB in four replay plans' 8 GiB containers, 1 GiB in the
  16 GiB ones (idc-open-data, noaa-nws-fourcastnetgfs-pds) and on live boxes.
  [`Memory and spill`](docs/mechanism.md#memory-and-spill)
  · `sort-buffer-and-spill-budget-bound-the-reorder-stage`
- **The seed starts differently.** Its first wave now also fetches the scope's
  first data page (a scope of one page is listed with no tiles; otherwise the
  page is discarded, one request more), and it stops following folderless
  pages, so startup and request counts differ from the earlier pins'.
  [`Listing, ranges and order`](docs/mechanism.md#listing-ranges-and-order)
  · `seed-is-on-by-default`

## Limitations and open questions

### Coverage gaps

- The runtime claims stay `unverified` although harness attempts exist: the
  receipts sit under `benchmark/receipts/`, while the claim schema takes run
  evidence only from this capsule's `receipts/`, and the ledger has not been
  re-evaluated against them — claims `lists-a-bucket-completely`,
  `throughput-and-memory-at-scale`, `go-and-java-datasets-agree`. Kill and
  resume and the signed path have not run at all — claims
  `resume-after-kill-completes`, `signed-path-unexercised`.

### Harness and verifier blockers

- Toolbox images built from this recipe at `8c8f870` ran harness attempts
  whose digests the receipts bind; the two claims are not yet re-evaluated
  against them — claims `image-builds-from-the-pinned-recipe`,
  `adapter-runs-under-the-harness`. Kill-and-resume needs a harness field that is not on `main`
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
| Find run records | [`benchmark/receipts/`](../../benchmark/receipts/swath-go-main-8c8f870-cloud-20261111/README.md), the `swath-go-*` directories; this capsule's [`receipts/`](receipts/) is empty |

## Provenance

**Firsthand, from public material only.** Every claim resolves to a public
source line or document at commit `8c8f870`, checked mechanically; no
Swath-internal notes or benchmark history were used. The derivation was not
blind (its author had read the Java Swath pages); the `a2efe16` capsule had one
independent review, whose findings are addressed, the move to `8c8f870`
awaits its own, and both await the owner's review;
[`research/derivation.md`](research/derivation.md) says so in full. The executions, a local build used to make the adapter's test
fixtures over a synthetic store and a local toolbox build, are described in
[`docs/running.md`](docs/running.md#adapter-fixtures); each is not a run record.

## Evidence boundary

Source and documentation establish mechanisms and risks, never observed
behaviour. The run records are the diagnostic receipts above; there is no
capsule smoke observation and no benchmark, no claim is `confirmed`, and the
runtime claims stay `unverified` until the ledger is re-evaluated against
those receipts.

## Varve and Swath

Varve builds Swath, Java and Go, and maintains this study, which makes us
participants in the space we study. The Go port gets exactly the rules every
other tool gets: the same harness, run records and evidence vocabulary, and no
number from Varve's own development of the port is used here. The port is
added before it is released, which is why this page says plainly that it has
diagnostic run records only and that its pin is an unreleased commit — claim `first-party-source-basis`.
