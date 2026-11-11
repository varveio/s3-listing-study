# Swath (Go port): groundwork derivation

What was read, at which revision, what was executed, and how this derivation
departs from the study's usual method. The current explanation is in
[`../docs/`](../docs/); this page is the derivation behind it. Other capsules
name this record `research/report.md`; it is named differently here only
because of how it was written, and may be renamed in review.

## Subject and pin

- Repository: <https://github.com/varveio/swath>, public, Apache-2.0.
- Revision: `8c8f870801c700f22413581f35683096782329a0`, upstream's main when it
  was read on 2026-11-11 (commit subject "replay: compare the Go and Java
  servers on converted fixtures (#452)"). The port's first release candidate,
  `v0.4.0-rc.1`, a GitHub pre-release of 2026-11-10 with binaries and images,
  is tagged on `5220c636`, an ancestor 29 commits earlier (24 of them touch
  `go/`); the README at the pin still calls the Go implementation unreleased,
  and the capsule pins the main commit, not the candidate.
- Source archive: `https://github.com/varveio/swath/archive/8c8f870801c700f22413581f35683096782329a0.tar.gz`,
  SHA-256 `8aa439ac53df3f8a704851f9c9e1eaee49b52f51a3c2dfa0d958160ceb7e5f4d`,
  identical across two anonymous fetches on 2026-11-11.
- Builder: `golang:1.27.4-bookworm@sha256:4fa7c968ba93f1635670522dbb01a2c78150443e1385746924566079f36570ea`
  (the index digest of that tag on 2026-11-11). go.mod gained `toolchain
  go1.27.4` between the pins, and upstream's `Dockerfile.go` and CI compile
  with that release; the tree also compiles under the earlier
  `golang:1.26.0-bookworm` (go.mod's `go 1.26.0` is the minimum), but the
  capsule takes the toolchain upstream ships with.
- Previous pin, `a2efe16e45d66088f6447e9723a119e5aebd4a91` (the squash merge of
  varveio/swath#255, archive SHA-256
  `7f83c4e8845e14f68fe173f06a9525bfd28e513c38b408c43edc250a4a2770f4`), held
  from 2026-11-07. 114 commits between it and this pin touched `go/`, and
  every file the ledger cited changed; `go/docs/cli.md` and
  `go/docs/architecture.md` were removed (their content moved to
  `go/docs/commands/`, `go/docs/reference/`, `go/docs/output.md`,
  `go/docs/dataset.md` and `go/docs/internals/`). What the capsule depends on
  and changed:
  - **Command line.** The flag set moved to a pflag table with renamed flags
    and no aliases: `--region` is `--s3-region`, `--endpoint` is
    `--endpoint-url` (which the earlier pin accepted as an alias), `--limiter`
    is `--no-adaptive-limit`, `--seed` is the diagnostic `--no-seed` on
    `swath diag list` only, `--row-budget` is `--sort-buffer` and
    `--spill-max` is `--spill-budget`; `--anonymous=false` on resume is
    `--no-anonymous`. `-o`, `--report`, `--anonymous`, `--workers` (which now
    refuses 0), `--max-duration`, `--part-size`, `--part-interval` and
    `--structure` keep their names and meaning.
  - **Meaning.** `--no-adaptive-limit` removes the limiter rather than holding
    a fixed `--workers` in flight; the limiter cuts on a throttle share of a
    window rather than on every throttle answer; the sort buffer's default is
    1/16 of the visible memory, at most 1 GiB, rather than a fixed 128 MiB
    (512 MiB in an 8 GiB container, 1 GiB in a 16 GiB one); the seed's first
    wave also fetches the first data page (listing a one-page scope with no
    tiles, otherwise discarding it at one request more) and stops following
    folderless pages, neither of which the earlier pin did;
    `kb`, `mb`, `gb` and `tb` are decimal sizes rather than binary; durations
    accept `d` and `w`; a text run spills beside its file rather than in the
    temporary directory; page fetches go through the AWS SDK's S3 client.
  - **Products.** The dataset manifest is SeekParquet (`format` `seekparquet`,
    `format_version` 0, `draft_revision` 1) with source, scope and coverage
    under `extensions.swath` and a SHA-256 per part, where it was
    `swath-dataset` format 2 with those members at the top level and an md5.
    The TSV file has the schema's column names, bare ETags, `.ffffffZ` times,
    `\N` nulls and `\xHH` escapes, where it had the S3 API's names, quoted
    ETags, `+00:00` times and JSON escapes. The run report's `build.tool` is
    `swath` (was `swath-go`) and its `invocation.argv` is canonical.
  So the adapter, the normalizer, the ledger and the adapter fixtures all
  moved; the change list per claim is in the pull request's "Pin moved to
  8c8f8708" section.
- Pull-request head: before `a2efe16`, the capsule was pinned to
  `7b05605444766de4e7a736db0bc410409af47429` (the head of #255, branch
  `go/engine-campaign`, archive SHA-256
  `ffa2a7493cfad80fcc680eddbe2389449375c429aa379bbcdf4046c1c5cdeb69`). Between
  those two commits no command-line or output-format file changed, so that
  move kept the anchors' text and the fixtures. The twenty replay rows and
  three live S3 rows of 2026-11-07 ran from the `7b05605` bundle.
- Earlier pin: the capsule was first drafted at `63a4256`, the head of the
  unmerged `spike/go-local-lister` branch, and never ran there. Re-anchoring
  at `7b05605` changed three facts the draft stated: the seed is now on by
  default (claim `seed-is-on-by-default`), the row budget is `--row-budget`
  rather than `--memory` (claim `row-budget-and-spill-bound-the-reorder-stage`),
  and the build stamps its revision (claim `build-stamps-the-pinned-revision`),
  where the draft's archive build reported none. It also added the TSV file
  output the draft did not adapt.

## What was read

Only public files at the pinned revision. No Swath-internal material, private
notes or benchmark history was used, and the port's own measurement pages
(`go/docs/internals/measurements.md`, `go/bench/`) were neither read for claims
nor cited.

| File | Used for |
| --- | --- |
| `go/README.md` | identity, separation from the Java tool, release status |
| `go/docs/commands/swath-list.md`, `go/docs/commands/swath-resume.md` (generated from the flag table and test-gated upstream), `go/docs/internals/diagnostics.md` | commands and flag sets, resume, the diagnostic flags |
| `go/docs/dataset.md` | the swath dataset profile: SeekParquet manifest, `extensions.swath`, coverage, the S3 schema, reading the parts |
| `go/docs/output.md` | text output: columns, values, the TSV escape grammar |
| `go/docs/reference/exit-codes.md`, `go/docs/reference/run-report.md`, `go/docs/production.md`, `go/docs/running.md` | exit codes, the run report, hosted-runner guidance, sizing defaults |
| `go/docs/internals/parquet-output.md`, `go/docs/internals/architecture.md`, `go/docs/internals/range-engine.md`, `go/docs/internals/clients.md` | publication order and the commit, the pipeline, the seed, the list clients |
| `go/build.sh`, `go/go.mod`, `Dockerfile.go`, `.github/workflows/ci.yml` | the static build line, the version and revision stamp, the toolchain, cross-build targets |
| `go/cmd/swath/main.go`, `go/cmd/swath/flags.go`, `go/cmd/swath/commands.go`, `go/cmd/swath/output.go`, `go/internal/buildinfo/buildinfo.go`, `go/internal/listing/listing.go`, `go/internal/source/flags.go`, `go/internal/source/objhttp/limiter.go`, `go/internal/source/objhttp/admission.go`, `go/internal/source/keyrange/flags.go`, `go/internal/source/s3/register.go`, `go/internal/textout/textout.go`, `go/internal/filter/filter.go` | the anchors in [`../data/claims.json`](../data/claims.json) and the adapter's provenance |

Every source anchor in the ledger was checked against a clean checkout of the
pinned commit with `s3-listing-study check-source-anchors --tool swath-go
--require-checked`.

## What was executed

Nothing was run against S3, and no capsule smoke was run. For this pin, four
things were executed on 2026-11-11, outside the harness, on the author's
workstation (Linux amd64):

1. **A scratch build.** The pinned archive was built with the Dockerfile's own
   line in the digest-pinned Go image (claim
   `build-stamps-the-pinned-revision`); the binary's digest is in
   [`../docs/running.md`](../docs/running.md#adapter-fixtures). This is not
   the recipe's build. The same tree also compiled under the earlier
   `golang:1.26.0-bookworm` image, which is why the toolchain choice is
   stated rather than forced.
2. **Command-line probes.** That binary's `list --help`, `resume --help` and
   `diag list --help`, and each flag the adapter renders, given against a
   closed port on 127.0.0.1 so that parsing is all that is tested: the
   earlier pin's `--region`, `--endpoint`, `--limiter`, `--seed`,
   `--row-budget` and `--spill-max` are unknown flags (exit 2), and every
   flag the adapter now renders parses. The help text agrees with the
   generated pages the ledger cites.
3. **The adapter fixtures.** That binary ran the adapter's compiled argv for
   the sorted mode and both TSV modes against a synthetic bucket in a local
   moto 5.1.4 S3 server, to produce the products the normalizer tests read.
   How, exactly, is in
   [`../docs/running.md`](../docs/running.md#adapter-fixtures). The fixtures
   are test input; no claim cites them.
4. **A local toolbox build** of the branch, to check the consolidated recipe
   builds and the binary runs in the image. It was not pushed and is not a
   record (claim `image-builds-from-the-pinned-recipe`).

## Departures from the usual method

- **Not blind.** The author read the Java Swath capsule before writing this
  one, to follow its conventions. The authoring guide treats such a researcher
  as contaminated for derivation. The ledger here is restricted to what the
  cited public files state, and every runtime proposition is `unverified`; the
  departure is stated so a reviewer weighs it.
- **One writer, one independent review so far.** The repository requires a
  different reviewer before the owner's review. One independent review was
  made on 2026-11-07 of the `a2efe16` capsule; its findings (normalizer
  refusals, resume overrides, test discovery, plan provenance notes, this
  claim's anchors) are addressed in the same branch. The move to `8c8f870`
  rewrote the adapter, the normalizer and most of the ledger and has not had
  its own independent review yet. The owner's review is pending.
