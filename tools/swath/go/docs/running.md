# Swath (Go port): building and running it in this study

What is selected, how the harness would run it, what it cannot run yet, and how
the adapter was checked. Claim IDs resolve in
[`../data/claims.json`](../data/claims.json), with the notation defined in
[`mechanism.md`](mechanism.md). The canonical tested identity is
[`../data/tool.json`](../data/tool.json).

**Nothing has run.** No harness attempt, no capsule smoke, no run against S3
and no verifier verdict exist for this subject, and the image has never been
built. Every runtime statement about the port is `unverified`.

## Build

The port has no release, so [`../build/Dockerfile`](../build/Dockerfile)
builds it from source: GitHub's archive of commit
`63a42568dbcfb2d3cdc18e3ccf7abfd69792ad77`, checksum-pinned, compiled in a
digest-pinned `golang:1.26.0-bookworm` stage with upstream CI's static build line
(`CGO_ENABLED=0`, `-trimpath -ldflags='-s -w'`), the committed `go.sum`
(`-mod=readonly`) and no toolchain download (`GOTOOLCHAIN=local`). The binary
lands at `/opt/swath-go/swath` on the study's shared base — claim
`static-cgo-free-binary`.

- **The pin is provisional.** It is the head of the unmerged
  `spike/go-local-lister` branch and moves to the merge commit when the branch
  merges — claim `subject-is-an-unmerged-branch-head`. `tool_version` in
  [`../build/image.json`](../build/image.json) says so as
  `provisional-63a4256`, the only free token that file's closed field set has;
  the archive locator and checksum carry the commit.
- **The image is unbuilt.** `image.json` records what this repository's
  build-selection loader computes before any build: the source-archive
  checksum, `tool_build_sha256` over the recipe and that artifact, and
  `adapter_bundle_sha256` over the two adapter files. It has no image-digest
  field; the built image's digest is bound later, by the campaign image set,
  and none exists — claim `image-builds-from-the-pinned-recipe`.
- **The binary does not know its revision.** A source archive carries no VCS
  metadata, so `--version` and the run report say `swath-go devel` with no
  revision — claim `source-archive-build-has-no-revision`. The pinned archive is
  the identity.

## Adapter and harness contract

[`../adapter/command.py`](../adapter/command.py) declares slug `swath-go`,
executable `/opt/swath-go/swath`, and two modes. Both write a directory dataset
into the engine's sink (`product_channel = "dataset"`, product
`parquet-sorted`, all five contract fields):

| Mode | argv after the executable |
| --- | --- |
| `recursive-parquet-sorted` | `list s3://BUCKET[/PREFIX] -o SINK/listing --report SINK/swath-go-report.json --region REGION [--anonymous] --workers N [--endpoint URL]` plus any stated knob below |
| `resume-parquet-sorted` | `resume SINK/listing --report SINK/swath-go-report.json` |

- **Concurrency** is the reserved axis, rendered as `--workers`, declared
  `Ceiling(64, "source@63a4256")` with its source lines in the adapter's
  docstring — claim `workers-default-64-is-the-limiter-maximum`.
- **Signing.** An unsigned case renders `--anonymous`; a signed one drops it
  and leaves the AWS SDK chain to find the credential — claims `s3-flag-surface`,
  `signed-path-unexercised`.
- **Endpoint.** A replay endpoint renders `--endpoint URL` only: the port uses
  path-style addressing automatically for an IP endpoint.
- **Config keys** a plan may state: `limiter` and `seed` and `request_payer`
  (booleans, rendered as `--flag=true|false`), `part_interval` and
  `max_duration` (Go durations), `part_size` (a size such as `64MiB`). Absent
  keys render nothing, so the tool's own default applies. Anything else is
  refused. A plan should state `max_duration` below the row's `timeout_s` by
  more than 20 s — claim `hosted-runner-needs-a-finite-max-duration`; the
  adapter cannot see the timeout to enforce it.
- **The report** goes beside the dataset, not inside it, as upstream asks —
  claim `report-is-written-on-failure-too`.
- **Resume** replays the recorded command, so it restates nothing but the
  report path — claim `resume-replays-the-recorded-command`. It writes the
  report to the same path as the list step: the killed step's report has to be
  copied out before the resume runs, or a resume that dies before writing its
  own leaves the earlier one looking current. Run alone on an empty sink it is
  refused (exit 2), so its purpose ceiling is `diagnostic`.

[`../adapter/normalize.py`](../adapter/normalize.py) reads a committed format-2
dataset the way upstream specifies a reader should: `manifest.json` first and
required (it is the commit), format `swath-dataset` version 2, schema
`swath/s3-objects/v1`, source `s3`, coverage complete with `stop_reason`
`completed`, no skipped entries and no filters, and a scope equal to the case's
prefix (`whole` for an empty one). It then reads the manifest's `parts` in
order with DuckDB, after checking each is a relative path under `data/` whose
size matches the manifest, and `count_rows` requires the count to equal the
manifest's rows — claims `manifest-is-the-commit`,
`coverage-states-completeness`. It does not check part checksums, key bounds
or footer stamps. It accepts the sink or the dataset directory, because
`measure` passes the one and `verify` the other.

## What the harness cannot do yet

The capsule is complete as a declaration, but the harness on `main` cannot
select it. Each gap is outside `tools/swath/` and is a follow-up, not something
this capsule decides:

- **Registration.** The toolbox roster (`TOOLBOX_TOOLS` in
  `benchmark/src/benchmark/contract.py`), the consolidated toolbox
  [`Dockerfile`](../../../../benchmark/build/Dockerfile) and every loader assume
  one capsule at `tools/<slug>/`. This one is at `tools/swath/go/` under slug
  `swath-go`, so either the harness learns a nested capsule root or the
  directory moves to `tools/swath-go/`. Moving it changes
  `tool_build_sha256`, which hashes repository-relative paths.
- **The capsule validator** rejects any extra entry at a capsule root, so
  `validate-capsule --tool swath` reports `go` as unexpected until it learns
  this layout; and its status-block pattern hard-codes a two-level link depth
  that a page one level deeper cannot meet with a working link. Schema
  validation and the source-anchor check were run directly instead.
- **Run reports.** The worker keeps only reports named in `RUN_REPORT_NAMES`
  (today Java's `_swath_summary.json`); `swath-go-report.json` needs adding
  there to survive a non-retained attempt.
- **Kill and resume.** The harness's `Mode` has no field naming a resume mode on
  `main`. When it does, the list mode names `resume-parquet-sorted`, and its
  resume evidence should include `swath-go-report.json`.
- **Plans.** No plan row names `swath-go`; one cannot until it is registered.

## Mode coverage

| Mode | Groundwork | Release |
| --- | --- | --- |
| `recursive-parquet-sorted` | not run against S3 | not in any release |
| `resume-parquet-sorted` | not run | not in any release |

## Adapter fixture

The normalizer tests read
[`../adapter/fixtures/format2-dataset/`](../adapter/fixtures/format2-dataset/),
a real format-2 dataset, so that the normalizer is tested on what the binary
actually writes rather than on a hand-made imitation. It was made on
2026-11-06, outside the harness and outside Docker, like this:

1. The pinned archive was built with the Dockerfile's `go build` line under Go
   1.26.0, giving a static binary with SHA-256
   `b287ef710916cd851f2a1d24d0192f5f77e1cd4469d27c42776bf0602c090d7c`.
2. A local moto 5.1.4 S3 server held a synthetic bucket, `fixture-bucket`, with
   nine keys under `a/` (including a space, a `+`, two non-ASCII letters, an
   empty object and one `STANDARD_IA` object) and one key outside it.
3. The adapter compiled the request recorded in the test module as
   `FIXTURE_REQUEST` (prefix `a/`, unsigned, `concurrency` 4, `part_size` `30`
   so the dataset has five parts, `part_interval` `0s`, `max_duration` `60s`,
   endpoint `http://127.0.0.1:5055`, sink `format2-dataset`), and the binary ran
   that argv once. It exited 0.

The directory is committed exactly as the binary left it, `.swath/` workspace
included; its `swath-go-report.json` records the argv, and a test asserts that
argv still equals what the adapter compiles. It is adapter test input, not a
receipt or an observation: it shows the argv parses and the normalizer reads
this binary's output, and nothing about behaviour against S3.

## Reproducing the checks

```sh
uv run pytest tools/swath/go          # argv, build facts, normalizer (offline)
uv run s3-listing-study check-source-anchors --tool swath/go --markdown tools/swath/go/ \
    --require-checked --source-root https://github.com/varveio/swath=<checkout at 63a4256>
uv run s3-listing-study check-links
```

The repository's default `uv run pytest` collects `benchmark/tests` and `tests`
only, so the capsule's tests run when their path is given.

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
