# Swath (Go port): groundwork derivation

What was read, at which revision, what was executed, and how this derivation
departs from the study's usual method. The current explanation is in
[`../docs/`](../docs/); this page is the derivation behind it. Other capsules
name this record `research/report.md`; it is named differently here only
because of how it was written, and may be renamed in review.

## Subject and pin

- Repository: <https://github.com/varveio/swath>, public, Apache-2.0.
- Revision: `63a42568dbcfb2d3cdc18e3ccf7abfd69792ad77`, the head of the unmerged
  branch `spike/go-local-lister` when it was fetched on 2026-11-06 (commit date
  2026-11-06T15:59:27Z). The port has no tag, release, published binary or
  image, so this commit is the whole identity.
- **Provisional.** The pin moves to the merge commit when the branch merges.
  Moving it means re-reading the files below at the new commit, re-running the
  anchor check, refreshing the archive checksum in
  [`../build/Dockerfile`](../build/Dockerfile) and
  [`../build/image.json`](../build/image.json), and moving every `source@`
  provenance in [`../adapter/command.py`](../adapter/command.py).
- Source archive: `https://github.com/varveio/swath/archive/63a42568dbcfb2d3cdc18e3ccf7abfd69792ad77.tar.gz`,
  SHA-256 `22cf5a7696bc23c2ee9582d9eb8d4f6ade139fe44221f428292ee2811aa9b9e5`,
  identical across two fetches on 2026-11-06.

## What was read

Only public files at the pinned revision. No Swath-internal material, private
notes or benchmark history was used, and the port's own measurement write-up
(`go/REPORT.md`) was neither read nor cited.

| File | Used for |
| --- | --- |
| `go/README.md` | identity, separation from the Java tool |
| `go/docs/cli.md` | commands, flag set (generated from the flag set and test-gated upstream), exit codes, resume, report, hosted-runner guidance |
| `go/docs/upgrade.md` | format 2: manifest, coverage, acceptance rule, schema changes |
| `go/docs/internals/parquet-output.md` | writer choices, publication order, the manifest as commit, the S3 schema |
| `go/docs/architecture.md` | pipeline and package map |
| `go/docs/internals/providers.md` | path-style addressing for IP endpoints |
| `.github/workflows/ci.yml` | the static build line and cross-build targets |
| `go/bench/diffds.py` | how upstream reads format-2 parts with DuckDB (manifest `parts` in order) |
| `go/cmd/swath/main.go`, `go/internal/listing/listing.go`, `go/internal/source/objhttp/limiter.go`, `go/internal/source/keyrange/flags.go`, `go/internal/source/s3/register.go`, `go/internal/listing/report.go` | the anchors in [`../data/claims.json`](../data/claims.json) and the adapter's provenance |

Every source anchor in the ledger was checked against a clean checkout of the
pinned commit with `s3-listing-study check-source-anchors --tool swath/go
--require-checked`: 17 anchors, 17 checked, 0 skipped, 0 failed.

## What was executed

Nothing was run against S3, and no capsule smoke was run. Two things were
executed, both outside the harness and outside Docker, on the author's
workstation (Linux amd64):

1. **A scratch build.** The pinned archive was extracted and built with the
   Dockerfile's own line (`GOFLAGS=-mod=readonly CGO_ENABLED=0 go build
   -trimpath -ldflags='-s -w' ./cmd/swath`, Go 1.26.0). It produced a static
   binary, SHA-256 `b287ef710916cd851f2a1d24d0192f5f77e1cd4469d27c42776bf0602c090d7c`,
   byte-identical to a build of `git archive` of the same commit, reporting
   `swath-go devel` (claim `source-archive-build-has-no-revision`). This is not
   the recipe's build: the Dockerfile itself was not built.
2. **The adapter fixture.** That binary ran the adapter's compiled argv once
   against a synthetic bucket in a local moto 5.1.4 S3 server, to produce the
   format-2 dataset the normalizer tests read. How, exactly, is in
   [`../docs/running.md`](../docs/running.md#adapter-fixture). The fixture is
   test input. It shows that the argv parses and that the normalizer reads what
   this binary writes; it says nothing about how the tool behaves against S3,
   and no claim cites it.

## Departures from the usual method

- **Not blind.** The author read the Java Swath capsule before writing this
  one, to follow its conventions. The authoring guide treats such a researcher
  as contaminated for derivation. The ledger here is small, restricted to what
  the cited public files state, and every runtime proposition is `unverified`;
  the departure is stated so a reviewer weighs it.
- **One writer, no independent review yet.** The repository requires a
  different reviewer before the owner's review; that review has not happened.
- **Nested location.** The capsule lives at `tools/swath/go/` under slug
  `swath-go` rather than at `tools/swath-go/`. The harness and the capsule
  validator assume one capsule per `tools/<slug>/`, so this location is not
  yet runnable or validatable as a unit; see
  [`../docs/running.md`](../docs/running.md#what-the-harness-cannot-do-yet).
