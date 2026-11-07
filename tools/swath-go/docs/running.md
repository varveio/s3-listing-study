# Swath (Go port): building and running it in this study

What is selected, how the harness runs it, which plans would run it, what the
harness cannot do yet, and how the adapter was checked. Claim IDs resolve in
[`../data/claims.json`](../data/claims.json), with the notation defined in
[`mechanism.md`](mechanism.md). The canonical tested identity is
[`../data/tool.json`](../data/tool.json).

**Nothing has run.** No harness attempt, no capsule smoke, no run against S3
and no verifier verdict exist for this subject. Every runtime statement about
the port is `unverified`.

## Build

The port has no release, so [`../build/Dockerfile`](../build/Dockerfile)
builds it from source: GitHub's archive of commit
`a2efe16e45d66088f6447e9723a119e5aebd4a91`, checksum-pinned, compiled in a
digest-pinned `golang:1.26.0-bookworm` stage with upstream's own build line
from `go/build.sh` (`CGO_ENABLED=0`, `-trimpath`,
`-ldflags="-s -w -X github.com/varveio/swath/go/internal/listing.revision=<commit>"`),
the committed `go.sum` (`-mod=readonly`) and no toolchain download
(`GOTOOLCHAIN=local`). The toolbox recipe
[`benchmark/build/Dockerfile`](../../../benchmark/build/Dockerfile) carries
the same stage as `swath_go_build` and copies the binary to
`/opt/swath-go/swath` — claim `static-cgo-free-binary`.

- **The pin is the merge commit.** It is the squash merge of upstream pull
  request varveio/swath#255 into main, an unreleased commit — claim
  `subject-is-an-unreleased-main-commit`. `tool_version` in
  [`../build/image.json`](../build/image.json) says so as `main-a2efe16`, the
  only free token that file's closed field set has; the archive locator and
  checksum carry the commit. The pin moved there on 2026-11-07 from the pull
  request's head, `7b05605`; the twenty replay rows and three live S3 rows
  run that day ran from the `7b05605` bundle, and their receipts name it. The
  two trees differ in no command-line or output-format file
  ([`../research/derivation.md`](../research/derivation.md#subject-and-pin)),
  so the ledger's anchors were re-checked rather than moved and the adapter
  fixtures were kept.
- **The binary names its revision.** The build passes the pinned commit at
  link time, so `--version` prints `swath-go devel (a2efe16…)` and every run
  report's `build.revision` carries it — claim
  `build-stamps-the-pinned-revision`.
- **Image identity.** `image.json` records what the build-selection loader
  computes: the source-archive checksum, `tool_build_sha256` over the recipe
  and that artifact, and `adapter_bundle_sha256` over the two adapter files.
  The built image's digest is bound by the campaign image set, and none is
  recorded — claim `image-builds-from-the-pinned-recipe`. A local toolbox
  build of this tree on 2026-11-07 completed and its `swath-go --version`
  printed the pinned revision; that build was not pushed and is not a record.

## Adapter and harness contract

[`../adapter/command.py`](../adapter/command.py) declares slug `swath-go`,
executable `/opt/swath-go/swath`, and four modes, all with the five contract
fields and the run report at `SINK/swath-go-report.json`:

| Mode | Product, channel | argv after the executable |
| --- | --- | --- |
| `recursive-parquet-sorted` | `parquet-sorted`, `dataset` | `list s3://BUCKET[/PREFIX] -o SINK/listing --report SINK/swath-go-report.json --region REGION [--anonymous] --workers N [--endpoint URL]` plus any stated knob |
| `resume-parquet-sorted` | `parquet-sorted`, `dataset` | `resume SINK/listing --report SINK/swath-go-report.json --workers N --anonymous[=false]` plus any stated runtime or operational knob |
| `recursive-tsv` | `text`, `file` (gzipped for upload) | as the sorted mode with `-o SINK/listing.tsv` |
| `recursive-tsv-zstd` | `text`, `file`, `product_compress=False` | as the sorted mode with `-o SINK/listing.tsv.zst` |

- **One file, not a dataset.** The Go TSV modes write one file, named so the
  port picks the format and compression from it — claim
  `text-output-is-one-file-shaped-as-the-s3-api`. The Java capsule's
  `recursive-tsv` printed on stdout and its `recursive-tsv-zstd` wrote a
  dataset directory of text parts; a comparison of the two tools' TSV modes
  compares different product shapes.
- **Concurrency** is the reserved axis, rendered as `--workers`, declared
  `Ceiling(64, "source@a2efe16")` with its source lines in the adapter's
  docstring — claim `workers-default-64-is-the-limiter-maximum`. The flag has
  no upper bound.
- **Signing.** An unsigned case renders `--anonymous`; a signed one drops it
  and leaves the AWS SDK chain to find the credential — claims `s3-flag-surface`,
  `signed-path-unexercised`.
- **Endpoint.** A replay endpoint renders `--endpoint URL` only. The port
  addresses a custom endpoint path-style by default, which is what the Java
  capsule's `--endpoint-url` gets; the replay server's URL is plain HTTP, so
  no TLS option is involved — claim `s3-flag-surface`.
- **Config keys** a plan may state: `limiter` and `seed` (booleans, rendered as
  `--flag=true|false`), `structure` (`auto`, `on` or `off`; stating it runs
  `swath diag list`, the only command that has the flag — claim
  `structure-is-a-diagnostic-flag`), `row_budget` and `spill_max` (sizes —
  claim `row-budget-and-spill-bound-the-reorder-stage`), `max_duration` (a Go
  duration), and, for the sorted mode only, `part_size` and `part_interval`.
  Absent keys render nothing, so the tool's own default applies. Anything else
  is refused, and a TSV mode refuses the two part controls.
- **`max_duration`** is stated by every plan row 30 s below the row's
  `timeout_s`, so the port stops itself, resumably, before the worker's kill
  deadline, with the 20 s upstream asks for to spare — claim
  `hosted-runner-needs-a-finite-max-duration`. The adapter cannot see
  `timeout_s`, so the plan keeps that relation.
- **No count-only mode.** `swath list --count-only` exists, but it publishes no
  product and the worker settles a clean exit with nothing at the declared path
  as unusable.
- **The report** goes beside the product, not inside it, as upstream requires —
  claim `report-is-written-on-failure-too`. `RUN_REPORT_NAMES` in
  `benchmark/src/benchmark/measure.py` lists `swath-go-report.json`, so the
  worker keeps it on every attempt, retained products or not.
- **Resume** replays the recorded command. The adapter restates what `swath
  resume` accepts again and the case states: `--workers`, the signing choice
  (`--anonymous`, or `--anonymous=false` to undo a recorded one), and any
  stated `limiter`, `row_budget`, `spill_max`, `max_duration` or
  `part_interval`; it refuses `seed`, `structure` and `part_size`, which the
  journal freezes — claim `resume-replays-the-recorded-command`. It writes the report to the same path
  as the list step: the killed step's report has to be copied out before the
  resume runs, or a resume that dies before writing its own leaves the earlier
  one looking current. Run alone on an empty sink it is refused (exit 2), so
  its purpose ceiling is `diagnostic`. Text runs have no resume — claim
  `text-runs-keep-no-journal`.
- **Spill location.** A dataset run spills under `SINK/listing/.swath/spill/`,
  on the attempt's disk. A text run spills to the process's temporary
  directory, which in the toolbox is the container's own writable layer —
  claim `text-runs-keep-no-journal`.

[`../adapter/normalize.py`](../adapter/normalize.py) reads each product the
way upstream specifies:

- **The dataset**: `manifest.json` first and required (it is the commit),
  format `swath-dataset` version 2, schema `swath/s3-objects/v1`, source `s3`,
  coverage complete with `stop_reason` `completed`, no skipped entries and no
  filters, and a scope equal to the case's prefix (`whole` for an empty one).
  It then reads the manifest's `parts` in order with DuckDB, after checking
  each is a relative path under `data/` whose size matches the manifest, and
  `count_rows` requires the count to equal the manifest's rows — claims
  `manifest-is-the-commit`, `coverage-states-completeness`. It does not check
  part checksums, key bounds or footer stamps. It accepts the sink or the
  dataset directory, because `measure` passes the one and `verify` the other.
- **The TSV file** (decoded from zstd for the compressed mode): exactly the
  port's nine-column header, nine fields per row, the key unescaped from the
  port's JSON escapes with any other backslash refused, `LastModified` cut to
  whole seconds with `Z`, the ETag's one pair of quotes removed and an
  unquoted one refused — claim `text-keys-use-reversible-json-escapes`.
  `count_rows` counts data rows with DuckDB, which reads the zstd stream
  directly, and refuses a foreign header or a key whose escapes `normalize`
  would refuse, so the two never disagree about whether a listing is usable.
  A zero-byte or newline-only input is the harness's empty listing and
  normalizes to nothing; the port itself always writes the header.

## Plans

Two campaign directories name the port; every other plan lists it as an
exclusion. `plan_cli` resolves each offline, and each submission was
dry-run against the private campaign ledger; nothing was submitted.

- [`benchmark/plans/campaigns/swath-go-replay/`](../../../benchmark/plans/campaigns/swath-go-replay/):
  one plan per release fixture (aws-public-blockchain, idc-open-data,
  FourCast, NARA, real-changesets, NBM), each copying its release plan's
  replay image, fixture, latency deadlines, warm-up, machine, allocation and
  timeout. The Go rows are `recursive-parquet-sorted` at 64 (and at 256 on
  blockchain), `recursive-tsv-zstd` at 256 on blockchain, and `recursive-tsv`
  at 64 on FourCast, NARA, real-changesets and NBM. Each plan also runs the
  Java capsule beside them, `recursive-parquet-sorted` at the Go rows'
  concurrency and, on blockchain, `recursive-tsv-zstd` at 256, so the engine
  and the output path can be told apart on one instrument. The Java rows keep
  the release replay rows' `jvm_max_heap: 4g` and text-writer settings; the
  Java sorted mode, which never ran on replay in the release, takes the
  release's live sorted rows' `sort_merge_parallelism: 8` with that 4g heap.
- [`benchmark/plans/campaigns/swath-go-real-s3/`](../../../benchmark/plans/campaigns/swath-go-real-s3/):
  live S3, unsigned, no replay, n4-highcpu-32: `sentinel-cogs` sorted at 64 and
  256 (submit with `--retain-products --boot-disk-gb 200`), and
  `real-changesets` sorted at 64 as a small live check.

## What the harness cannot do yet

- **Kill and resume.** The harness's `Mode` has no field naming a resume mode on
  `main`, and the kill-then-resume schedule is not merged. When both land, the
  list mode names `resume-parquet-sorted`, and the resume evidence should
  include the killed step's `swath-go-report.json`.

## Mode coverage

| Mode | Groundwork | Release |
| --- | --- | --- |
| `recursive-parquet-sorted` | not run against S3 | not in any release |
| `resume-parquet-sorted` | not run | not in any release |
| `recursive-tsv` | not run against S3 | not in any release |
| `recursive-tsv-zstd` | not run against S3 | not in any release |

## Adapter fixtures

The normalizer tests read three directories under
[`../adapter/fixtures/`](../adapter/fixtures/), real products of the binary
rather than hand-made imitations: `format2-dataset/` (the sorted dataset,
`.swath/` workspace included), `tsv-file/` and `tsv-zstd-file/`. They were made
on 2026-11-07, outside the harness, like this:

1. The pull request's head archive (`7b05605`, the pin before the merge) was
   built with the Dockerfile's `go build` line in the
   same digest-pinned `golang:1.26.0-bookworm` image, giving a static binary
   with SHA-256
   `926c000bef5d05d17c11567a2067796a69daf9efa87b8bbe8b4f1fc868c604b8` whose
   `--version` printed `swath-go devel (7b05605444766de4e7a736db0bc410409af47429)`.
2. A local moto 5.1.4 S3 server held a synthetic bucket, `fixture-bucket`, with
   ten keys under `a/` (including a space, a `+`, a backslash, two non-ASCII
   letters, an empty object and one `STANDARD_IA` object) and one key outside
   it.
3. The adapter compiled the requests recorded in the test module as
   `FIXTURE_REQUESTS` (prefix `a/`, unsigned, `concurrency` 4, `max_duration`
   `60s`, endpoint `http://127.0.0.1:5055`; the dataset also `part_size` `30`
   so it has six parts and `part_interval` `0s`), and the binary ran each argv
   once. Each exited 0.

The directories are committed exactly as the binary left them; each
`swath-go-report.json` records the argv, and a test asserts that argv still
equals what the adapter compiles. The three products normalize to the same
ten records, which is cross-mode agreement and not completeness. They are
adapter test input, not receipts or observations: they show the argv parses
and the normalizer reads this binary's output, and nothing about behaviour
against S3.

## Reproducing the checks

```sh
uv run pytest tools/swath-go          # argv, build facts, normalizer (offline)
uv run s3-listing-study validate-capsule --tool swath-go
uv run s3-listing-study check-source-anchors --tool swath-go --markdown tools/swath-go/ \
    --require-checked --source-root https://github.com/varveio/swath=<checkout at a2efe16>
uv run s3-listing-study check-links
uv run python -m benchmark.plan_cli --path benchmark/plans/campaigns/swath-go-replay/<bucket>.yaml
```

The capsule's tests are in the repository's configured `testpaths`, so the
default `uv run pytest`, which CI runs, collects them.

## Deferred coverage

All `unverified`, each with its reason in the ledger: the image build
(`image-builds-from-the-pinned-recipe`), a harness attempt
(`adapter-runs-under-the-harness`), completeness against S3
(`lists-a-bucket-completely`), kill and resume
(`resume-after-kill-completes`), the limiter under real throttling
(`limiter-under-real-throttling`), time and memory at scale
(`throughput-and-memory-at-scale`), the signed path
(`signed-path-unexercised`) and agreement with the Java capsule's sorted
dataset (`go-and-java-datasets-agree`).
