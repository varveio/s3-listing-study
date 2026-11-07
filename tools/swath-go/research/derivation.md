# Swath (Go port): groundwork derivation

What was read, at which revision, what was executed, and how this derivation
departs from the study's usual method. The current explanation is in
[`../docs/`](../docs/); this page is the derivation behind it. Other capsules
name this record `research/report.md`; it is named differently here only
because of how it was written, and may be renamed in review.

## Subject and pin

- Repository: <https://github.com/varveio/swath>, public, Apache-2.0.
- Revision: `a2efe16e45d66088f6447e9723a119e5aebd4a91`, the squash merge of upstream
  pull request varveio/swath#255 into main, read on 2026-11-07. The port has
  no tag, release, published binary or image, so this commit is the whole
  identity.
- Source archive: `https://github.com/varveio/swath/archive/a2efe16e45d66088f6447e9723a119e5aebd4a91.tar.gz`,
  SHA-256 `7f83c4e8845e14f68fe173f06a9525bfd28e513c38b408c43edc250a4a2770f4`,
  identical across two fetches on 2026-11-07.
- Pull-request head: the capsule was pinned to `7b05605444766de4e7a736db0bc410409af47429`
  (the head of #255, branch `go/engine-campaign`, archive SHA-256
  `ffa2a7493cfad80fcc680eddbe2389449375c429aa379bbcdf4046c1c5cdeb69`) until the merge. Between the two commits only
  `go/` changed: tests, `go/docs` prose (`measurements.md`, `operating.md`,
  `testing.md`, `internals/counters.md`, `internals/ordering.md`, one line of
  `upgrade.md` outside every cited range) and `go/internal/order/order.go` and
  `go/internal/source/objhttp/objhttp.go`, neither of which a claim cites. No
  command-line or output-format file changed, so every anchor resolves to the
  same text at the merge commit and the adapter fixtures, made with the
  pull-request-head binary, were kept.
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
(`go/docs/measurements.md`, `go/bench/`) were neither read for claims nor cited.

| File | Used for |
| --- | --- |
| `go/README.md` | identity, separation from the Java tool |
| `go/docs/cli.md` | commands, flag set (generated from the flag set and test-gated upstream), text output, exit codes, resume, report, hosted-runner guidance |
| `go/docs/upgrade.md` | format 2: manifest, coverage, acceptance rule, schema changes |
| `go/docs/internals/parquet-output.md` | publication order, the manifest as commit, the S3 schema |
| `go/docs/architecture.md` | pipeline and package map |
| `go/docs/internals/providers.md` | endpoint addressing |
| `go/build.sh`, `.github/workflows/ci.yml` | the static build line, the revision stamp, cross-build targets |
| `go/cmd/swath/main.go`, `go/cmd/swath/output.go`, `go/internal/listing/listing.go`, `go/internal/listing/report.go`, `go/internal/source/objhttp/limiter.go`, `go/internal/source/keyrange/flags.go`, `go/internal/source/s3/register.go`, `go/internal/textout/textout.go`, `go/internal/textout/sink.go`, `go/internal/filter/filter.go` | the anchors in [`../data/claims.json`](../data/claims.json) and the adapter's provenance |

Every source anchor in the ledger was checked against a clean checkout of the
pinned commit with `s3-listing-study check-source-anchors --tool swath-go
--require-checked`.

## What was executed

Nothing was run against S3, and no capsule smoke was run. Three things were
executed, outside the harness, on the author's workstation (Linux amd64):

1. **A scratch build.** The pinned archive was built with the Dockerfile's own
   line in the digest-pinned Go image (claim
   `build-stamps-the-pinned-revision`); the binary's digest is in
   [`../docs/running.md`](../docs/running.md#adapter-fixtures). This is not
   the recipe's build.
2. **The adapter fixtures.** That binary ran the adapter's compiled argv for
   the sorted mode and both TSV modes against a synthetic bucket in a local
   moto 5.1.4 S3 server, to produce the products the normalizer tests read.
   How, exactly, is in
   [`../docs/running.md`](../docs/running.md#adapter-fixtures). The fixtures
   are test input; no claim cites them.
3. **A local toolbox build** of the branch, to check the consolidated recipe
   builds and the binary runs in the image. It was not pushed and is not a
   record (claim `image-builds-from-the-pinned-recipe`).

## Departures from the usual method

- **Not blind.** The author read the Java Swath capsule before writing this
  one, to follow its conventions. The authoring guide treats such a researcher
  as contaminated for derivation. The ledger here is restricted to what the
  cited public files state, and every runtime proposition is `unverified`; the
  departure is stated so a reviewer weighs it.
- **One writer, one independent review.** The repository requires a
  different reviewer before the owner's review. One independent review was
  made on 2026-11-07; its findings (normalizer refusals, resume overrides, test
  discovery, plan provenance notes, this claim's anchors) are addressed in the
  same branch. The owner's review is pending.
