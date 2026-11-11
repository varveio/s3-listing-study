# Swath (Go port): building and running it in this study

What is selected, how the harness runs it, which plans would run it, what the
harness cannot do yet, and how the adapter was checked. Claim IDs resolve in
[`../data/claims.json`](../data/claims.json), with the notation defined in
[`mechanism.md`](mechanism.md). The canonical tested identity is
[`../data/tool.json`](../data/tool.json).

**Nothing at this pin has run.** No harness attempt, no capsule smoke, no run
against S3 and no verifier verdict exist for commit `8c8f870`; the rows run on
2026-11-07 ran from the earlier `7b05605` bundle and have no committed
receipt. Every runtime statement about the port is `unverified`.

## Build

The subject is a commit, not a release, so
[`../build/Dockerfile`](../build/Dockerfile) builds it from source: GitHub's
archive of commit `8c8f870801c700f22413581f35683096782329a0`, checksum-pinned
(SHA-256 `8aa439ac53df3f8a704851f9c9e1eaee49b52f51a3c2dfa0d958160ceb7e5f4d`),
compiled in a digest-pinned `golang:1.27.4-bookworm` stage with upstream's own
build line from `go/build.sh` (`CGO_ENABLED=0`, `-trimpath`,
`-ldflags="-s -w -X github.com/varveio/swath/go/internal/buildinfo.Version= -X github.com/varveio/swath/go/internal/buildinfo.Revision=<commit>"`,
the version unset as for any build that is not a release), the committed
`go.sum` (`-mod=readonly`; the `seekparquet` module resolves from the public
module proxy and the in-tree `reader` module through go.mod's `replace`) and
no toolchain download (`GOTOOLCHAIN=local`). Go 1.27.4 is go.mod's `toolchain`
line, the release upstream's CI and images compile with; go.mod's `go 1.26.0`
directive is the minimum, and a 1.26.0 build of the same tree also compiles,
but would not be the toolchain upstream ships. The toolbox recipe
[`benchmark/build/Dockerfile`](../../../benchmark/build/Dockerfile) carries
the same stage as `swath_go_build` and copies the binary to
`/opt/swath-go/swath` — claim `static-cgo-free-binary`.

- **The pin is a main commit.** `8c8f870` is upstream's main on 2026-11-11, an
  unreleased commit newer than the port's first release candidate,
  `v0.4.0-rc.1` — claim `subject-is-an-unreleased-main-commit`. `tool_version`
  in [`../build/image.json`](../build/image.json) says so as `main-8c8f870`,
  the only free token that file's closed field set has; the archive locator
  and checksum carry the commit. The pin moved there on 2026-11-11 from
  `a2efe16` (the merge of varveio/swath#255), which had replaced that pull
  request's head, `7b05605`, on 2026-11-07; the twenty replay rows and three
  live S3 rows run on 2026-11-07 ran from the `7b05605` bundle, and their
  receipts name it. Unlike the previous move, this one changed the command
  line and both products
  ([`../research/derivation.md`](../research/derivation.md#subject-and-pin)),
  so the adapter, the normalizer, the ledger and the adapter fixtures all
  moved with it.
- **The binary names its revision.** The build passes the pinned commit at
  link time, so `--version` prints
  `swath devel (8c8f870801c700f22413581f35683096782329a0)` and every run
  report's `build` is `{"tool": "swath", "version": "devel", "revision": …}`
  with that commit (the earlier pins' reports said `swath-go`) — claim
  `build-stamps-the-pinned-revision`.
- **Image identity.** `image.json` records what the build-selection loader
  computes: the source-archive checksum, `tool_build_sha256` over the recipe
  and that artifact, and `adapter_bundle_sha256` over the two adapter files.
  The built image's digest is bound by the campaign image set, and none is
  recorded — claim `image-builds-from-the-pinned-recipe`. A local toolbox
  build of this tree on 2026-11-11 completed and its `swath-go --version`
  printed the pinned revision; that build was not pushed and is not a record.

## Adapter and harness contract

[`../adapter/command.py`](../adapter/command.py) declares slug `swath-go`,
executable `/opt/swath-go/swath`, and four modes, all with the five contract
fields and the run report at `SINK/swath-go-report.json`:

| Mode | Product, channel | argv after the executable |
| --- | --- | --- |
| `recursive-parquet-sorted` | `parquet-sorted`, `dataset` | `list s3://BUCKET[/PREFIX] -o SINK/listing --report SINK/swath-go-report.json --s3-region REGION [--anonymous] --workers N [--endpoint-url URL]` plus any stated knob |
| `resume-parquet-sorted` | `parquet-sorted`, `dataset` | `resume SINK/listing --report SINK/swath-go-report.json --workers N --anonymous\|--no-anonymous` plus any stated runtime or operational knob |
| `recursive-tsv` | `text`, `file` (gzipped for upload) | as the sorted mode with `-o SINK/listing.tsv` |
| `recursive-tsv-zstd` | `text`, `file`, `product_compress=False` | as the sorted mode with `-o SINK/listing.tsv.zst` |

- **One file, not a dataset.** The Go TSV modes write one file, named so the
  port picks the format and compression from it — claim
  `text-output-is-one-file-of-the-dataset-columns`. The Java capsule's
  `recursive-tsv` printed on stdout and its `recursive-tsv-zstd` wrote a
  dataset directory of text parts; a comparison of the two tools' TSV modes
  compares different product shapes.
- **Concurrency** is the reserved axis, rendered as `--workers`, declared
  `Ceiling(64, "source@8c8f870")` with its source lines in the adapter's
  docstring — claim `workers-default-64-is-the-limiter-maximum`. The flag has
  no upper bound.
- **Signing.** An unsigned case renders `--anonymous`; a signed one drops it
  and leaves the AWS SDK chain to find the credential — claims `s3-flag-surface`,
  `signed-path-unexercised`.
- **Endpoint.** A replay endpoint renders `--endpoint-url URL` only. The port
  addresses a custom endpoint path-style by default, which is what the Java
  capsule's `--endpoint-url` gets; the replay server's URL is plain HTTP, so
  no TLS option is involved. An unstated endpoint falls back to
  `AWS_ENDPOINT_URL_S3`, else `AWS_ENDPOINT_URL`, so a live row relies on the
  worker environment setting neither — claim `s3-flag-surface`.
- **Region** renders `--s3-region REGION` (the earlier pins' `--region` is no
  longer accepted).
- **Config keys** a plan may state: `adaptive_limit` (a boolean: `false`
  renders `--no-adaptive-limit`, `true` its undo spelling `--adaptive-limit` —
  claim `limiter-is-aimd-on-throttle-answers`), `seed` (a boolean: `false`
  runs `swath diag list --no-seed`, `true` renders nothing, since `swath
  list` always seeds — claim `seed-is-on-by-default`), `structure` (`auto`,
  `on` or `off`; stating it runs `swath diag list`, the only command that has
  the flag — claim `structure-is-a-diagnostic-flag`), `sort_buffer` and
  `spill_budget` (sizes; state binary units, since `kb`, `mb` and `gb` are
  decimal at this pin — claim
  `sort-buffer-and-spill-budget-bound-the-reorder-stage`), `encoders` (a
  positive integer rendered as `--encoders N` in every mode, resume included:
  the Parquet encoders, a `.zst` file's compression jobs, a TSV file's
  formatters; unset, the least of GOMAXPROCS and what the sort buffer admits
  — claim `encoders-bound-the-output-workers`), `cpu_profile` (`on` or `off`, see
  below), `max_duration` (a
  duration), and, for the sorted mode only, `part_size` and `part_interval`.
  Absent keys render nothing, so the tool's own default applies. Anything else
  is refused, the earlier pins' `limiter`, `row_budget` and `spill_max`
  included, and a TSV mode refuses the two part controls. The release-mirroring
  plans (`swath-go-replay`, `swath-go-real-s3`) state none of them but
  `max_duration`, so those rows run at the port's own defaults. Two of those changed since the earlier pins and move with the
  version in any comparison with the 2026-11-07 rows:
  - the sort buffer is sized from the box's memory: 512 MiB on the four replay plans with 8 GiB subject containers
    (aws-public-blockchain, nara-1950-census, noaa-nbm-grib2-pds,
    real-changesets), 1 GiB on the two with 16 GiB (idc-open-data,
    noaa-nws-fourcastnetgfs-pds) and on the live plans' uncapped boxes, where
    the 2026-11-07 rows at the earlier pin had 128 MiB — claim
    `sort-buffer-and-spill-budget-bound-the-reorder-stage`;
  - the seed sends the scope's first data page beside its first delimiter
    wave (a scope that fits in one page is then listed with no tiles; a
    truncated first page is discarded, one request more) and stops following
    a level's pages once a page holds no folder — claim `seed-is-on-by-default`.
  The limiter's cut rule changed too (claim
  `limiter-is-aimd-on-throttle-answers`), though it only acts under throttling.
- **`max_duration`** is stated by every plan row 30 s below the row's
  `timeout_s`, so the port stops itself, resumably, before the worker's kill
  deadline, with the 20 s upstream asks for to spare — claim
  `hosted-runner-needs-a-finite-max-duration`. The adapter cannot see
  `timeout_s`, so the plan keeps that relation.
- **No count-only mode.** `swath diag count` exists (the earlier pins'
  `swath list --count-only` is gone), but it publishes no
  product and the worker settles a clean exit with nothing at the declared path
  as unusable.
- **The report** goes beside the product, not inside it, as upstream requires —
  claim `report-is-written-on-failure-too`. `RUN_REPORT_NAMES` in
  `benchmark/src/benchmark/measure.py` lists `swath-go-report.json`, so the
  worker keeps it on every attempt, retained products or not.
- **CPU profile.** The pin has no profiling flag; its one hook is the
  environment variable `SWATH_CPUPROFILE=FILE` (`go/cmd/swath/main.go:1154-1167`,
  `go/docs/commands/swath.md:213` @8c8f870). `cpu_profile: on` has the
  capsule's `build_env` set it to `SINK/swath-go-cpu.pprof`, beside the report;
  the argv is unchanged. The port checks the path as it checks `--report`
  (`go/cmd/swath/sidepaths.go:28-57`), starts the profile before the listing
  and stops it when `listing.Run` returns, before the report is written
  (`main.go:849-852,913-917`) — claim `cpu-profile-is-an-environment-hook`.
  `PROFILE_NAMES` in `measure.py` keeps the file on every attempt, retained
  products or not, as optional evidence: one that is over `PROFILE_MAX_BYTES`
  (512 MiB), not a regular readable file, hit by the secret scan or not
  uploaded is removed, left out of the manifest and named in `result.json`'s
  `profile_omissions`, and the attempt keeps its record. The profiler's cost on
  the wall is unmeasured: a profiled row is diagnostic, not a timing row. The
  heap profile (`SWATH_MEMPROFILE`) is not exposed.
- **Resume** replays the recorded command. The adapter restates what `swath
  resume` accepts again and the case states: `--workers`, the signing choice
  (`--anonymous`, or `--no-anonymous` to undo a recorded one), and any
  stated `adaptive_limit`, `encoders`, `sort_buffer`, `spill_budget`,
  `max_duration` or `part_interval`; it refuses `seed`, `structure` and `part_size`, which the
  journal freezes — claim `resume-replays-the-recorded-command`. It writes the report to the same path
  as the list step: the killed step's report has to be copied out before the
  resume runs, or a resume that dies before writing its own leaves the earlier
  one looking current. Run alone on an empty sink it is refused (exit 2), so
  its purpose ceiling is `diagnostic`. Text runs have no resume — claim
  `text-runs-keep-no-journal`.
- **Spill location.** A dataset run spills under `SINK/listing/.swath/spill/`,
  on the attempt's disk. A text run spills to a scratch directory beside its
  file, `SINK/.listing.tsv.swath-ATTEMPT-RANDOM/spill/`, on the same disk
  (the earlier pins spilled a text run to the container's temporary
  directory) — claim `text-runs-keep-no-journal`.
- **The report's argv** is the command in its canonical spelling (each flag
  `--name=value`, operands last), not the argv as given; the fixture test
  compares the two through that rule — claim `report-is-written-on-failure-too`.

[`../adapter/normalize.py`](../adapter/normalize.py) reads each product the
way upstream specifies:

- **The dataset**: `manifest.json` first and required (it is the commit),
  format `seekparquet` version 0 draft revision 1, schema
  `swath/s3-objects/v1`, key column `key`, and `extensions.swath` version 1
  with source `s3`, coverage complete with `stop_reason` `completed`, no
  skipped entries and no filters, and a scope equal to the case's prefix
  (`whole` for an empty one); a `swath-dataset` format-2 manifest from the
  earlier pins is refused. It then reads the manifest's `parts` in order with
  DuckDB by column name (`union_by_name`), after checking each is a relative
  path under `data/` whose size matches the manifest, and `count_rows`
  requires the count to equal the manifest's rows — claims
  `output-is-a-seekparquet-sorted-dataset`, `manifest-is-the-commit`,
  `coverage-states-completeness`. It does not check part digests, key bounds
  or footer stamps. It accepts the sink or the dataset directory, because
  `measure` passes the one and `verify` the other.
- **The TSV file** (decoded from zstd for the compressed mode): exactly the
  schema's nine-column header, nine fields per row, `\N` read as null, every
  field unescaped by the port's TSV grammar with any other backslash refused,
  a null key refused, `last_modified` cut to whole seconds with `Z`, and the
  ETag taken as written, a quoted one refused — claims
  `text-output-is-one-file-of-the-dataset-columns`,
  `text-fields-use-reversible-tsv-escapes`. `count_rows` counts data rows
  with DuckDB, which reads the zstd stream directly, and refuses a foreign
  header or a key whose escapes `normalize` would refuse, so the two never
  disagree about whether a listing is usable. A zero-byte or newline-only
  input is the harness's empty listing and normalizes to nothing; the port
  itself always writes the header.

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
rather than hand-made imitations: `seekparquet-dataset/` (the sorted dataset,
`.swath/` workspace included), `tsv-file/` and `tsv-zstd-file/`. They were
re-made on 2026-11-11 for this pin, outside the harness, like this (the
earlier pins' fixtures, written by the `7b05605` binary, were a `swath-dataset`
format-2 dataset and S3-API-shaped TSV files, and no longer match the port's
products):

1. The pinned archive (SHA-256 as in `build/image.json`) was built with the
   Dockerfile's `go build` line in the same digest-pinned
   `golang:1.27.4-bookworm` image, giving a static binary with SHA-256
   `e15b1436ce7bd62237a9c5dabcfb604dd4e273baf7bd288af248261c516e46ce` whose
   `--version` printed `swath devel (8c8f870801c700f22413581f35683096782329a0)`.
2. A local moto 5.1.4 S3 server held a synthetic bucket, `fixture-bucket`, with
   ten keys under `a/` (including a space, a `+`, a backslash, two non-ASCII
   letters, an empty object and one `STANDARD_IA` object) and one key outside
   it, `b/outside`. Each object's body is the first SIZE bytes of its key's
   UTF-8 repeated, at the sizes the test module's records carry (22, 64, 23, 0,
   4, 5, 6, 8, 13 and 5 bytes under `a/`, 7 for `b/outside`), so its ETag is
   reproducible from this paragraph.
3. The adapter compiled the requests recorded in the test module as
   `FIXTURE_REQUESTS` (prefix `a/`, unsigned, `concurrency` 4, `max_duration`
   `60s`, endpoint `http://127.0.0.1:5055`; the dataset also `part_size` `30`
   so it has six parts and `part_interval` `0s`), and the binary ran each argv
   once, with no `AWS_*` variable in its environment. Each exited 0.

The directories are committed exactly as the binary left them (the empty
`.swath/parts/` directory aside, which git does not keep); each
`swath-go-report.json` records the argv in its canonical spelling, and a test
asserts that it is the canonical spelling of what the adapter compiles. The
three products normalize to the same ten records, which is cross-mode
agreement and not completeness. They are adapter test input, not receipts or
observations: they show the argv parses and the normalizer reads this binary's
output, and nothing about behaviour against S3.

## Reproducing the checks

```sh
uv run pytest tools/swath-go          # argv, build facts, normalizer (offline)
uv run s3-listing-study validate-capsule --tool swath-go
uv run s3-listing-study check-source-anchors --tool swath-go --markdown tools/swath-go/ \
    --require-checked --source-root https://github.com/varveio/swath=<checkout at 8c8f870>
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
